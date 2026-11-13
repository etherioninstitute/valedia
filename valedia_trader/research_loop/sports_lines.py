"""Sportsbook lines against Kalshi's price for the same game: research code for the locked prediction in
docs/research/2026-11-13-sports-lines-prediction.md. It buys history, pairs games and scores them. It has no way to
trade: nothing here sends anything to an exchange or a broker.

The steps (each a command of `main`, run in this order; every store lives in one folder, `--store`):

1. `kalshi-markets`: Kalshi's settled game markets of the five series for a period (keyless GETs,
   `venues.kalshi_history`). They also say which league-days have a game.
2. `plan`: no paid call. Per league and US-Eastern day with a Kalshi game: one Odds API snapshot at 16:00 UTC, then
   one 30 minutes before each of the (at most three) biggest waves of starts (`select_waves`). A day whose 16:00
   snapshot is stored is planned exactly; any other day at its upper bound, 1 + 3 snapshots. The test period is always
   reserved in full; if the budget (the cap less what the ledger has spent, and no more than this month's remaining
   credits) would be passed, whole days are dropped from the end of the development period, and the plan says so.
3. `fetch-odds`: buys the plan, day by day from the start, every call through the credit ledger (`CreditLedger`,
   its own sqlite file). A snapshot already stored is never bought again. Test-period snapshots are refused until a
   choice is stored (`choose`).
4. `pair`: a Kalshi event and an Odds API event are one game when the league, both teams and the US-Eastern date
   agree. Code first (`names_match`, then a code table learned from the code's own pairs); what code cannot settle is
   offered to the injected resolver (by default the local model, `LocalModelResolver`), whose proposal is kept only if
   the date and both teams then pass the checks. Every proposal is logged; accepted ones are listed by `resolved`.
5. `kalshi-candles`: one-minute candles of both contracts of each paired game, only for the minute that ends at or
   before the game's wave snapshot and the hour before it.
6. `evaluate --period dev`: both questions of the prediction for each margin and each version of the books'
   probability. `choose` stores the one choice; `evaluate --period test` then runs once with it and stores its result.

At the moment of a game (its wave snapshot's own reported time): the books' probability uses only that snapshot;
Kalshi's bid and ask come from the candle of the minute that ends at or before that time, and candles after it, or
after the market's close, are never read.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import sys
import unicodedata
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any, Callable, Iterable, Sequence
from zoneinfo import ZoneInfo

import numpy as np

from valedia_trader.research_loop import REPO
from valedia_trader.venues import kalshi_history as kh
from valedia_trader.venues import odds_api as oa

__all__ = ["LEAGUES", "BOOKS", "PERIODS", "MARGINS", "VERSIONS", "CreditLedger", "CreditCapError", "Refused",
           "LinesStore", "select_waves", "all_waves", "eastern_date", "first_snapshot_time", "paid_snapshot", "buy",
           "make_plan", "fetch_odds", "names_match", "pair_games", "LocalModelResolver", "kalshi_fee",
           "book_probabilities", "books_p", "quote_at", "collect_games", "evaluate_games", "main"]

UTC = timezone.utc
ET = ZoneInfo("America/New_York")
LEAGUES = {"NFL": ("americanfootball_nfl", "KXNFLGAME"), "NBA": ("basketball_nba", "KXNBAGAME"),
           "NHL": ("icehockey_nhl", "KXNHLGAME"), "NCAAF": ("americanfootball_ncaaf", "KXNCAAFGAME"),
           "NCAAMB": ("basketball_ncaab", "KXNCAAMBGAME")}
BOOKS = ("pinnacle", "draftkings", "fanduel", "betmgm", "williamhill_us", "betrivers", "espnbet", "fanatics", "lowvig",
         "betonlineag")
PERIODS = {"dev": (date(2026, 1, 5), date(2026, 3, 1)), "test": (date(2026, 9, 20), date(2026, 11, 8))}
MARGINS = (0.02, 0.03, 0.05)
VERSIONS = ("median", "pinnacle")
MIN_BOOKS = 3
QUOTE_HOUR = 3600          # Kalshi candles are fetched for the hour before each moment, and read only there
MAX_SPREAD_CENTS = 5
FIRST_HOUR_UTC = 16
WAVE_SPAN = timedelta(minutes=90)
WAVE_LEAD = timedelta(minutes=30)
MAX_WAVES = 3
HISTORY_CAP = 9000
MONTHLY_CAP = 20000
FREE_MONTHLY_CAP = 500
SNAPSHOT_RULE = "historical_odds:h2h:%d_books" % len(BOOKS)
SEED = 20261113
REPS = 2000
RESOLVER_URL = "http://127.0.0.1:1235/v1"
RESOLVER_MODEL = "qwen/qwen3.8-27b"


class CreditCapError(RuntimeError):
    """A paid call refused before it was made: it would pass the cap or the month's credits."""


class Refused(RuntimeError):
    """A step the locked prediction does not allow now (the test period before a choice, a second test run...)."""


def _iso(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(text: str) -> datetime:
    return datetime.fromisoformat(str(text).replace("Z", "+00:00")).astimezone(UTC)


def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _system_clock() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------- the snapshot rule

def eastern_date(when) -> date:
    """The US-Eastern calendar date of a UTC time (an ISO string or a datetime)."""
    t = _parse(when) if isinstance(when, str) else when.astimezone(UTC)
    return t.astimezone(ET).date()


def first_snapshot_time(day: date) -> datetime:
    """16:00 UTC on the day: the first snapshot of a league-day, which also gives its start times."""
    return datetime(day.year, day.month, day.day, FIRST_HOUR_UTC, tzinfo=UTC)


def all_waves(starts: Iterable[datetime]) -> list[list[datetime]]:
    """Starts grouped into waves: a wave opens at the earliest start not yet in one and takes every start within
    90 minutes of it, so any two starts of a wave are within 90 minutes of each other."""
    out: list[list[datetime]] = []
    for s in sorted(starts):
        if out and s - out[-1][0] <= WAVE_SPAN:
            out[-1].append(s)
        else:
            out.append([s])
    return out


def select_waves(starts: Iterable[datetime]) -> list[tuple[datetime, list[datetime]]]:
    """The (at most three) waves with the most games, a tie going to the earlier wave, each with its snapshot time
    (30 minutes before its first start), in time order."""
    waves = all_waves(starts)
    ranked = sorted(range(len(waves)), key=lambda i: (-len(waves[i]), i))[:MAX_WAVES]
    return [(waves[i][0] - WAVE_LEAD, waves[i]) for i in sorted(ranked)]


def day_games(first: dict, day: date) -> list[dict]:
    """The games of a league-day: the events of its 16:00 snapshot whose start is on that US-Eastern date."""
    return [e for e in first.get("data") or [] if e.get("commence_time") and eastern_date(e["commence_time"]) == day]


# ---------------------------------------------------------------- the credit ledger

class CreditLedger:
    """Every Odds API call, before and after. Before: its cost (by the documented rule, or the cost last charged for
    that rule if the two differed) is checked against the cap of the run (everything this ledger has spent with that
    key) and against the month (the monthly cap less the ledger's spend this UTC month, and no more than the last
    `x-requests-remaining` header said). After: the three usage headers are stored, and a charge different from the
    expected cost is an event and becomes the cost of that rule from then on. A call that fails without headers is
    counted at its expected cost. Parameters are stored without the key."""

    def __init__(self, path: str | Path, monthly_cap: int = MONTHLY_CAP, free_monthly_cap: int = FREE_MONTHLY_CAP,
                 clock: Callable[[], datetime] = _system_clock):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.caps = {"paid": int(monthly_cap), "free": int(free_monthly_cap)}
        self.clock = clock
        self.db = sqlite3.connect(str(self.path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS calls (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, key TEXT, rule TEXT,
                path TEXT, params TEXT, expected INTEGER, charged INTEGER, remaining INTEGER, used INTEGER,
                last INTEGER, ok INTEGER, error TEXT);
            CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, kind TEXT, detail TEXT);
            CREATE TABLE IF NOT EXISTS costs (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, rule TEXT, cost INTEGER);
        """)
        self.db.commit()

    def expected_cost(self, rule: str, rule_cost: int) -> int:
        row = self.db.execute("SELECT cost FROM costs WHERE rule = ? ORDER BY id DESC LIMIT 1", (rule,)).fetchone()
        return int(row["cost"]) if row else int(rule_cost)

    def spent(self, key: str = "paid") -> int:
        return int(self.db.execute("SELECT COALESCE(SUM(charged), 0) FROM calls WHERE key = ?", (key,)).fetchone()[0])

    def month_spent(self, key: str = "paid") -> int:
        month = self.clock().astimezone(UTC).strftime("%Y-%m")
        return int(self.db.execute("SELECT COALESCE(SUM(charged), 0) FROM calls WHERE key = ? AND substr(at, 1, 7) = ?",
                                   (key, month)).fetchone()[0])

    def last_remaining(self, key: str = "paid") -> int | None:
        row = self.db.execute("SELECT remaining FROM calls WHERE key = ? AND remaining IS NOT NULL ORDER BY id DESC "
                              "LIMIT 1", (key,)).fetchone()
        return None if row is None else int(row["remaining"])

    def month_remaining(self, key: str = "paid") -> int:
        left = self.caps[key] - self.month_spent(key)
        header = self.last_remaining(key)
        return left if header is None else min(left, header)

    def check(self, key: str, cost: int, cap: int) -> None:
        spent = self.spent(key)
        if spent + cost > cap:
            raise CreditCapError("refused: %d spent + %d would pass the cap of %d" % (spent, cost, cap))
        month = self.month_spent(key)
        if month + cost > self.caps[key]:
            raise CreditCapError("refused: %d used this month by the ledger + %d would pass the monthly cap of %d"
                                 % (month, cost, self.caps[key]))
        header = self.last_remaining(key)
        if header is not None and cost > header:
            raise CreditCapError("refused: %d credits needed, the last x-requests-remaining said %d" % (cost, header))

    def call(self, key: str, rule: str, rule_cost: int, cap: int, path: str, params: dict, do: Callable[[], Any],
             client: oa.OddsApiClient, empty: Callable[[Any], bool] = lambda body: False) -> Any:
        expected = self.expected_cost(rule, rule_cost)
        self.check(key, expected, cap)
        clean = {k: v for k, v in params.items() if k.lower() not in ("apikey", "api_key", "x-api-key")}
        try:
            body = do()
        except Exception as exc:
            usage = dict(client.last_usage)
            charged = usage["last"] if usage["last"] is not None else expected
            self._record(key, rule, path, clean, expected, charged, usage, False, type(exc).__name__)
            raise
        usage = dict(client.last_usage)
        charged = usage["last"] if usage["last"] is not None else expected
        self._record(key, rule, path, clean, expected, charged, usage, True, None)
        free_empty = charged == 0 and empty(body)
        if usage["last"] is not None and charged != expected and not free_empty:
            now = _iso(self.clock())
            self.db.execute("INSERT INTO events (at, kind, detail) VALUES (?, ?, ?)",
                            (now, "cost_differs", _canon({"rule": rule, "expected": expected, "charged": charged})))
            self.db.execute("INSERT INTO costs (at, rule, cost) VALUES (?, ?, ?)", (now, rule, charged))
            self.db.commit()
        return body

    def _record(self, key, rule, path, params, expected, charged, usage, ok, error) -> None:
        self.db.execute("INSERT INTO calls (at, key, rule, path, params, expected, charged, remaining, used, last, ok, "
                        "error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (_iso(self.clock()), key, rule, path, _canon(params), expected, charged, usage["remaining"],
                         usage["used"], usage["last"], 1 if ok else 0, error))
        self.db.commit()

    def calls(self) -> list[dict]:
        return [dict(r, params=json.loads(r["params"])) for r in self.db.execute("SELECT * FROM calls ORDER BY id")]

    def events(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM events ORDER BY id")]


def paid_snapshot(client: oa.OddsApiClient, ledger: CreditLedger, sport: str, when: str, cap: int) -> dict:
    """One historical h2h snapshot of the ten books, with the paid key, through the ledger."""
    params = {"date": when, "markets": "h2h", "bookmakers": ",".join(BOOKS)}
    return ledger.call("paid", SNAPSHOT_RULE, oa.historical_cost("h2h", BOOKS), cap,
                       "/v4/historical/sports/%s/odds" % sport, params,
                       lambda: client.historical_odds(sport, when, BOOKS, markets="h2h", key="paid"), client,
                       empty=lambda body: not (isinstance(body, dict) and body.get("data")))


# ---------------------------------------------------------------- the store

class LinesStore:
    """sqlite: snapshots (keyed by league and the time asked), pairs, the pairing log, the choice and the test result.
    Nothing fetched or decided is overwritten; a different copy of a snapshot goes to `differences`."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS snapshots (league TEXT, requested_at TEXT, kind TEXT, day TEXT, timestamp TEXT,
                previous_timestamp TEXT, next_timestamp TEXT, raw TEXT NOT NULL, fetched_at TEXT,
                PRIMARY KEY (league, requested_at));
            CREATE TABLE IF NOT EXISTS pairs (kalshi_event TEXT PRIMARY KEY, odds_event_id TEXT UNIQUE NOT NULL,
                league TEXT, day TEXT, home_ticker TEXT, away_ticker TEXT, home_team TEXT, away_team TEXT,
                source TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS resolver_log (id INTEGER PRIMARY KEY AUTOINCREMENT, kalshi_event TEXT,
                proposal TEXT, accepted INTEGER, reason TEXT, source TEXT, at TEXT);
            CREATE TABLE IF NOT EXISTS unpaired (id INTEGER PRIMARY KEY AUTOINCREMENT, kalshi_event TEXT, league TEXT,
                day TEXT, reason TEXT, at TEXT);
            CREATE TABLE IF NOT EXISTS choice (id INTEGER PRIMARY KEY CHECK (id = 1), margin REAL, version TEXT,
                at TEXT);
            CREATE TABLE IF NOT EXISTS results (period TEXT PRIMARY KEY, raw TEXT, at TEXT);
            CREATE TABLE IF NOT EXISTS differences (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, key TEXT,
                old TEXT, new TEXT, at TEXT);
        """)
        self.db.commit()

    # snapshots
    def snapshot(self, league: str, requested_at: str) -> dict | None:
        r = self.db.execute("SELECT * FROM snapshots WHERE league = ? AND requested_at = ?",
                            (league, requested_at)).fetchone()
        return None if r is None else dict(r, raw=json.loads(r["raw"]))

    def snapshots(self, league: str | None = None) -> list[dict]:
        q = "SELECT * FROM snapshots" + (" WHERE league = ?" if league else "") + " ORDER BY requested_at"
        return [dict(r, raw=json.loads(r["raw"])) for r in self.db.execute(q, (league,) if league else ())]

    def put_snapshot(self, league: str, requested_at: str, kind: str, day: date, body: dict) -> str:
        raw = _canon(body)
        old = self.db.execute("SELECT raw FROM snapshots WHERE league = ? AND requested_at = ?",
                              (league, requested_at)).fetchone()
        if old is not None:
            if old["raw"] == raw:
                return "same"
            self.db.execute("INSERT INTO differences (kind, key, old, new, at) VALUES (?, ?, ?, ?, ?)",
                            ("snapshot", "%s@%s" % (league, requested_at), old["raw"], raw, _iso(_system_clock())))
            self.db.commit()
            return "different"
        self.db.execute("INSERT INTO snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (league, requested_at, kind, day.isoformat(), body.get("timestamp"),
                         body.get("previous_timestamp"), body.get("next_timestamp"), raw, _iso(_system_clock())))
        self.db.commit()
        return "new"

    # pairs
    def pairs(self, league: str | None = None) -> list[dict]:
        q = "SELECT * FROM pairs" + (" WHERE league = ?" if league else "") + " ORDER BY day, kalshi_event"
        return [dict(r) for r in self.db.execute(q, (league,) if league else ())]

    def put_pair(self, p: dict, at: str) -> None:
        self.db.execute("INSERT INTO pairs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (p["kalshi_event"], p["odds_event_id"], p["league"], p["day"].isoformat(), p["home_ticker"],
                         p["away_ticker"], p["home_team"], p["away_team"], p["source"], at))

    def log_proposal(self, rec: dict, at: str) -> None:
        self.db.execute("INSERT INTO resolver_log (kalshi_event, proposal, accepted, reason, source, at) "
                        "VALUES (?, ?, ?, ?, ?, ?)", (rec["kalshi_event"], _canon(rec["proposal"]),
                                                      1 if rec["accepted"] else 0, rec["reason"], rec["source"], at))

    def put_unpaired(self, event: str, league: str, day: date, reason: str, at: str) -> None:
        self.db.execute("INSERT INTO unpaired (kalshi_event, league, day, reason, at) VALUES (?, ?, ?, ?, ?)",
                        (event, league, day.isoformat(), reason, at))

    def latest_unpaired(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for r in self.db.execute("SELECT kalshi_event, reason FROM unpaired ORDER BY id"):
            out[r["kalshi_event"]] = r["reason"]
        return out

    def accepted_proposals(self) -> list[dict]:
        rows = self.db.execute("SELECT p.kalshi_event, p.odds_event_id, p.source, p.home_team, p.away_team, "
                               "p.home_ticker, p.away_ticker, p.day, p.created_at FROM pairs p "
                               "WHERE p.source LIKE 'resolver:%' ORDER BY p.day, p.kalshi_event")
        return [dict(r) for r in rows]

    # the choice and the test result
    def choice(self) -> dict | None:
        r = self.db.execute("SELECT margin, version FROM choice WHERE id = 1").fetchone()
        return None if r is None else {"margin": float(r["margin"]), "version": r["version"]}

    def put_choice(self, margin: float, version: str, at: str) -> None:
        if self.choice() is not None:
            raise Refused("a choice is already stored: %s" % _canon(self.choice()))
        self.db.execute("INSERT INTO choice VALUES (1, ?, ?, ?)", (margin, version, at))
        self.db.commit()

    def result(self, period: str) -> dict | None:
        r = self.db.execute("SELECT raw FROM results WHERE period = ?", (period,)).fetchone()
        return None if r is None else json.loads(r["raw"])

    def put_result(self, period: str, result: dict, at: str) -> None:
        if self.result(period) is not None:
            raise Refused("the %s period has already been scored" % period)
        self.db.execute("INSERT INTO results VALUES (?, ?, ?)", (period, _canon(result), at))
        self.db.commit()


# ---------------------------------------------------------------- Kalshi events from the store

def kalshi_events(kstore: kh.HistoryStore, league: str, start: date, end: date) -> list[dict]:
    """Kalshi game events of a league in [start, end] (the US-Eastern date of the ticker), each with its two
    contracts: code, team name (`yes_sub_title`), ticker, result and close."""
    by: dict[str, list[dict]] = {}
    for m in kstore.markets(LEAGUES[league][1]):
        d = kh.event_date(m["ticker"])
        if d is None or not (start <= d <= end):
            continue
        by.setdefault(m.get("event_ticker") or m["ticker"].rsplit("-", 1)[0], []).append(m)
    out = []
    for event, rows in sorted(by.items()):
        rows = sorted(rows, key=lambda r: r["ticker"])
        out.append({"event": event, "league": league, "day": kh.event_date(event) or kh.event_date(rows[0]["ticker"]),
                    "expected": rows[0].get("expected_expiration_time") or rows[0].get("occurrence_datetime") or "",
                    "teams": [{"code": kh.team_code(r["ticker"]), "name": r.get("yes_sub_title") or "",
                               "ticker": r["ticker"]} for r in rows],
                    "markets": {r["ticker"]: r for r in rows}})
    return out


def league_days(kstore: kh.HistoryStore, league: str, start: date, end: date) -> dict[date, int]:
    days: dict[date, int] = {}
    for e in kalshi_events(kstore, league, start, end):
        days[e["day"]] = days.get(e["day"], 0) + 1
    return days


# ---------------------------------------------------------------- plan and purchase

def _day_snapshots(lstore: LinesStore, league: str, day: date) -> tuple[list[dict], bool]:
    """The snapshots a league-day needs: (list of {kind, at, stored}, whether the list is exact)."""
    at = _iso(first_snapshot_time(day))
    first = lstore.snapshot(league, at)
    out = [{"kind": "first", "at": at, "stored": first is not None}]
    if first is None:
        out += [{"kind": "wave", "at": None, "stored": False} for _ in range(MAX_WAVES)]
        return out, False
    starts = [_parse(e["commence_time"]) for e in day_games(first["raw"], day)]
    for when, _ in select_waves(starts):
        w = _iso(when)
        out.append({"kind": "wave", "at": w, "stored": lstore.snapshot(league, w) is not None})
    return out, True


def make_plan(lstore: LinesStore, kstore: kh.HistoryStore, ledger: CreditLedger, cap: int,
              leagues: Sequence[str] = tuple(LEAGUES), periods: dict | None = None,
              days_map: dict | None = None) -> dict:
    """What the rule would buy for each period, and its cost, without any call. See the module text for the drop.
    `days_map` ({(period, league): {day: events}}) saves re-reading the Kalshi store."""
    periods = periods or PERIODS
    days_map = days_map if days_map is not None else {
        (n, lg): league_days(kstore, lg, *periods[n]) for n in periods for lg in leagues}
    unit = ledger.expected_cost(SNAPSHOT_RULE, oa.historical_cost("h2h", BOOKS))
    out: dict[str, Any] = {}
    by_date: dict[str, dict[date, int]] = {}
    for name, (start, end) in periods.items():
        snaps, dates = [], {}
        for league in leagues:
            for day in sorted(days_map[(name, league)]):
                needed, exact = _day_snapshots(lstore, league, day)
                for s in needed:
                    cost = 0 if s["stored"] else unit
                    snaps.append({"league": league, "day": day.isoformat(), "kind": s["kind"], "at": s["at"],
                                  "known": exact or s["kind"] == "first", "cost": cost})
                    dates[day] = dates.get(day, 0) + cost
        out[name] = {"start": start.isoformat(), "end": end.isoformat(), "snapshots": snaps,
                     "cost": sum(dates.values()), "dropped_dates": []}
        by_date[name] = dates
    spent = ledger.spent("paid")
    month = ledger.month_remaining("paid")
    budget = min(cap - spent, month)
    reserved = sum(out[n]["cost"] for n in out if n != "dev")
    notes = []
    if "dev" in out:
        dev = by_date["dev"]
        kept = sorted(d for d, c in dev.items())
        dropped: list[date] = []
        while kept and sum(dev[d] for d in kept) + reserved > budget:
            d = kept.pop()
            if dev[d] > 0:
                dropped.append(d)
        dropped.sort()
        out["dev"]["dropped_dates"] = [d.isoformat() for d in dropped]
        out["dev"]["snapshots"] = [s for s in out["dev"]["snapshots"] if date.fromisoformat(s["day"]) not in dropped]
        out["dev"]["cost"] = sum(dev[d] for d in dev if d not in dropped)
        if dropped:
            notes.append("the budget would be passed: %d development days dropped from the end (%s to %s)"
                         % (len(dropped), dropped[0].isoformat(), dropped[-1].isoformat()))
    total = sum(out[n]["cost"] for n in out)
    fits = total <= budget
    if not fits:
        notes.append("the test period alone (%d credits, upper bound) does not fit the budget of %d; it is never cut"
                     % (reserved, budget))
    unlisted = ["%s:%s" % (n, lg) for n in periods for lg in leagues
                if not kstore.listed_any(LEAGUES[lg][1], *periods[n])]
    if unlisted:
        notes.append("Kalshi markets not listed for %s: run `kalshi-markets --period all` first, or this plan is "
                     "short" % ", ".join(unlisted))
    out.update({"cap": cap, "spent": spent, "month_remaining": month, "budget": budget, "unit_cost": unit,
                "total_cost": total, "fits": fits, "unlisted": unlisted,
                "note": "; ".join(notes) or "everything fits"})
    return out


def buy(client: oa.OddsApiClient, ledger: CreditLedger, lstore: LinesStore, league: str, when: datetime, kind: str,
        day: date, cap: int, clock: Callable[[], datetime]) -> dict | None:
    """One snapshot of a league at `when` (stored if new; never bought again). None for a time not yet past."""
    at = _iso(when)
    have = lstore.snapshot(league, at)
    if have is not None:
        return have
    if when > clock():
        return None
    body = paid_snapshot(client, ledger, LEAGUES[league][0], at, cap)
    if not isinstance(body, dict) or "timestamp" not in body:
        raise oa.OddsApiError("historical odds answer without a timestamp for %s at %s" % (league, at))
    lstore.put_snapshot(league, at, kind, day, body)
    return lstore.snapshot(league, at)


def fetch_odds(period: str, client: oa.OddsApiClient, ledger: CreditLedger, lstore: LinesStore,
               kstore: kh.HistoryStore, cap: int, clock: Callable[[], datetime], leagues: Sequence[str] = tuple(LEAGUES),
               periods: dict | None = None, progress: Callable[[str], Any] = lambda s: None) -> dict:
    """Buy the plan of one period, day by day from its start. Development days are re-planned before each day and the
    run stops at the first dropped day; a refusal by the ledger stops it too (and is reported)."""
    periods = periods or PERIODS
    if period == "test" and lstore.choice() is None:
        raise Refused("test-period snapshots are refused until a choice is stored (`choose`)")
    if period == "dev" and "test" in periods:
        missing = [lg for lg in leagues if not kstore.listed_any(LEAGUES[lg][1], *periods["test"])]
        if missing:
            raise Refused("the test period's Kalshi markets are not listed for %s, so its credits cannot be reserved: "
                          "run `kalshi-markets --period all` first" % ", ".join(missing))
    start, end = periods[period]
    days_map = {(n, lg): league_days(kstore, lg, *periods[n]) for n in periods for lg in leagues}
    days = sorted({d for league in leagues for d in days_map[(period, league)]})
    out = {"period": period, "days": len(days), "bought": 0, "already_stored": 0, "not_yet": 0, "stopped": None,
           "spent_before": ledger.spent("paid")}
    for day in days:
        if period == "dev":
            plan = make_plan(lstore, kstore, ledger, cap, leagues, periods, days_map)
            if day.isoformat() in plan["dev"]["dropped_dates"]:
                out["stopped"] = "day %s and later dropped by the plan: %s" % (day, plan["note"])
                break
        try:
            for league in leagues:
                if day not in days_map[(period, league)]:
                    continue
                at = first_snapshot_time(day)
                had = lstore.snapshot(league, _iso(at)) is not None
                first = buy(client, ledger, lstore, league, at, "first", day, cap, clock)
                if first is None:
                    out["not_yet"] += 1
                    continue
                out["already_stored" if had else "bought"] += 1
                starts = [_parse(e["commence_time"]) for e in day_games(first["raw"], day)]
                for when, _ in select_waves(starts):
                    had = lstore.snapshot(league, _iso(when)) is not None
                    got = buy(client, ledger, lstore, league, when, "wave", day, cap, clock)
                    if got is None:
                        out["not_yet"] += 1
                    else:
                        out["already_stored" if had else "bought"] += 1
        except CreditCapError as exc:
            out["stopped"] = str(exc)
            break
        progress("%s: %s done, %d bought so far" % (period, day, out["bought"]))
    out["spent_after"] = ledger.spent("paid")
    return out


# ---------------------------------------------------------------- team names

_TOKEN_MAP = {"state": "st", "saint": "st", "and": "", "the": "", "of": ""}
QUALIFIERS = {"st", "am", "tech", "oh", "fl", "college", "university", "u", "intl", "international", "central",
              "eastern", "western", "northern", "southern", "north", "south", "east", "west", "northeast",
              "northwest", "southeast", "southwest", "upstate"}


def _tokens(text: str) -> list[str]:
    s = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    s = s.replace("&", "").replace("'", "")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return [t for t in (_TOKEN_MAP.get(t, t) for t in s.split()) if t]


def _split(name: str) -> tuple[list[str], list[str]]:
    """Tokens outside and inside parentheses: "Miami (OH) RedHawks" -> (["miami", "redhawks"], ["oh"])."""
    inner = " ".join(re.findall(r"\(([^)]*)\)", name or ""))
    outer = re.sub(r"\([^)]*\)", " ", name or "")
    return _tokens(outer), _tokens(inner)


def _cover(k: list[str], o: list[str]) -> set[int] | None:
    """Indexes of `o` that the tokens of `k` account for (equal tokens, then acronyms of consecutive tokens, then
    single letters as initials), or None when a token of `k` is not accounted for."""
    used: set[int] = set()
    rest = []
    for t in k:
        i = next((i for i, x in enumerate(o) if x == t and i not in used), None)
        if i is None:
            rest.append(t)
        else:
            used.add(i)
    for t in rest:
        hit = None
        if len(t) >= 2:
            for i in range(len(o)):
                for n in range(2, len(o) - i + 1):
                    span = range(i, i + n)
                    if "".join(o[j][0] for j in span) == t and any(j not in used for j in span):
                        hit = set(span)
                        break
                if hit:
                    break
        else:
            i = next((i for i, x in enumerate(o) if x.startswith(t) and i not in used), None)
            hit = None if i is None else {i}
        if not hit:
            return None
        used |= hit
    return used


def names_match(kalshi_name: str, odds_name: str) -> bool:
    """Code's team-name rule. Every word of Kalshi's short name must be accounted for in the Odds API's full name
    (the same word; an acronym such as NY or NYI; a single letter as an initial, "Los Angeles L"); a bracketed part
    must agree when both names have one; and a word left over in the full name must not be one that names a
    different school ("State", "Tech", "A&M", a direction...), so "Florida" is not "Florida State"."""
    k, kp = _split(kalshi_name)
    o, op = _split(odds_name)
    if not k or not o:
        return False
    if op and kp != op:
        return False
    used = _cover(k, o)
    if used is None:
        return False
    return not any(o[i] in QUALIFIERS for i in range(len(o)) if i not in used)


def _abbreviates(t: str, word: str) -> bool:
    """A Kalshi abbreviation of at least three letters of one word: its first letter, then the rest in order."""
    if len(t) < 3 or not word or t[0] != word[0]:
        return False
    it = iter(word[1:])
    return all(c in it for c in t[1:])


def loosely_names(kalshi_name: str, odds_name: str) -> bool:
    """The resolver's check: some word of the Kalshi name is a word, an acronym, an initial or an abbreviation
    ("Phx", "Dnvr") of the Odds API name, and the strict rule's qualifier test still holds."""
    k, kp = _split(kalshi_name)
    o, op = _split(odds_name)
    if not k or not o or (op and kp and kp != op):
        return False
    for t in k:
        if t in o or (len(t) >= 2 and _cover([t], o) is not None) or any(_abbreviates(t, w) for w in o):
            return True
    return False


# ---------------------------------------------------------------- pairing

def odds_games(lstore: LinesStore, league: str, start: date, end: date) -> list[dict]:
    """Odds API games of a league from its stored 16:00 snapshots: one per event id, dated by its US-Eastern start."""
    seen: dict[str, dict] = {}
    for s in lstore.snapshots(league):
        if s["kind"] != "first":
            continue
        for e in s["raw"].get("data") or []:
            if not e.get("commence_time") or e.get("id") in seen:
                continue
            d = eastern_date(e["commence_time"])
            if start - timedelta(days=1) <= d <= end + timedelta(days=1):
                seen[e["id"]] = {"id": e["id"], "league": league, "home": e.get("home_team"),
                                 "away": e.get("away_team"), "commence": e["commence_time"], "day": d}
    return sorted(seen.values(), key=lambda g: (g["commence"], g["id"]))


def _assign(kevent: dict, game: dict, table: dict[str, str]) -> list[tuple[dict, dict]]:
    """Every way to put the two Kalshi teams on the game's home and away team by name or by the code table:
    a list of (home team, away team) pairs."""
    teams = kevent["teams"]
    if len(teams) != 2:
        return []

    def ok(t, name):
        return names_match(t["name"], name) or table.get(t["code"]) == name

    out = []
    for h, a in ((teams[0], teams[1]), (teams[1], teams[0])):
        if ok(h, game["home"]) and ok(a, game["away"]):
            out.append((h, a))
    return out


def _pair(kevent: dict, game: dict, home: dict, away: dict, source: str) -> dict:
    return {"kalshi_event": kevent["event"], "odds_event_id": game["id"], "league": kevent["league"],
            "day": kevent["day"], "home_ticker": home["ticker"], "away_ticker": away["ticker"],
            "home_team": game["home"], "away_team": game["away"], "source": source}


def pair_games(kevents: Sequence[dict], games: Sequence[dict], resolver: Callable | None = None,
               taken_odds: Iterable[str] = (), taken_kalshi: Iterable[str] = ()) -> tuple[list[dict], dict, list]:
    """Pairs of one league: (pairs, unpaired Kalshi events counted by reason, the resolver's proposals). An event or
    game already paired (`taken_*`) is never paired again."""
    taken_o, taken_k = set(taken_odds), set(taken_kalshi)
    pairs: list[dict] = []
    left: dict[str, str] = {}
    open_k = [k for k in kevents if k["event"] not in taken_k]
    for k in open_k:
        if len(k["teams"]) != 2:
            left[k["event"]] = "kalshi_event_incomplete"
    open_k = [k for k in open_k if k["event"] not in left]

    def free_games():
        return [g for g in games if g["id"] not in taken_o]

    def code_round(table: dict[str, str]) -> None:
        cands = {k["event"]: [g for g in free_games() if g["day"] == k["day"] and _assign(k, g, table)]
                 for k in open_k if k["event"] not in done}
        groups: dict[tuple, list[dict]] = {}
        for k in open_k:
            if k["event"] in cands and cands[k["event"]]:
                groups.setdefault(tuple(sorted(g["id"] for g in cands[k["event"]])), []).append(k)
        for ids, ks in groups.items():
            if len(ids) != len(ks):
                continue                                   # more games than events, or the other way: ambiguous
            gs = sorted((g for g in free_games() if g["id"] in ids), key=lambda g: (g["commence"], g["id"]))
            ks = sorted(ks, key=lambda k: (k["expected"], k["event"]))
            if len(gs) != len(ks):
                continue
            chosen = []
            for k, g in zip(ks, gs):
                ways = _assign(k, g, table)
                if len(ways) != 1:
                    break
                chosen.append((k, g, ways[0]))
            else:
                for k, g, (h, a) in chosen:
                    pairs.append(_pair(k, g, h, a, "code"))
                    taken_o.add(g["id"])
                    done.add(k["event"])

    done: set[str] = set()
    code_round({})
    learned: dict[str, set[str]] = {}
    for p in pairs:
        k = next(x for x in open_k if x["event"] == p["kalshi_event"])
        for t in k["teams"]:
            learned.setdefault(t["code"], set()).add(p["home_team"] if t["ticker"] == p["home_ticker"]
                                                     else p["away_team"])
    table = {c: next(iter(v)) for c, v in learned.items() if len(v) == 1}
    code_round(table)

    log: list[dict] = []
    for k in open_k:
        if k["event"] in done:
            continue
        near = [g for g in free_games() if abs((g["day"] - k["day"]).days) <= 1]
        exact = [g for g in near if g["day"] == k["day"] and _assign(k, g, table)]
        why = "ambiguous" if len(exact) > 1 or any(len(_assign(k, g, table)) > 1 for g in exact) else "no_match"
        if resolver is None or not near:
            left[k["event"]] = why
            continue
        source = "resolver:%s" % getattr(resolver, "source", getattr(resolver, "__name__", "resolver"))
        try:
            proposal = resolver(k, near)
        except Exception as exc:                                  # a broken resolver leaves the game out
            left[k["event"]] = "resolver_error"
            log.append({"kalshi_event": k["event"], "proposal": {"error": type(exc).__name__}, "accepted": False,
                        "reason": "resolver error", "source": source})
            continue
        if not proposal or not proposal.get("odds_event_id"):
            left[k["event"]] = why
            log.append({"kalshi_event": k["event"], "proposal": proposal, "accepted": False,
                        "reason": "no proposal", "source": source})
            continue
        verdict, assign = _check_proposal(k, proposal, free_games(), table)
        log.append({"kalshi_event": k["event"], "proposal": proposal, "accepted": verdict is None,
                    "reason": verdict or "accepted", "source": source})
        if verdict is not None:
            left[k["event"]] = "resolver_refused"
            continue
        g, h, a = assign
        pairs.append(_pair(k, g, h, a, source))
        taken_o.add(g["id"])
        done.add(k["event"])
    counts: dict[str, int] = {}
    for reason in left.values():
        counts[reason] = counts.get(reason, 0) + 1
    _check_unique(pairs)
    return pairs, counts, log


def _check_unique(pairs: list[dict]) -> None:
    if len({p["odds_event_id"] for p in pairs}) != len(pairs) or len({p["kalshi_event"] for p in pairs}) != len(pairs):
        raise AssertionError("a game was paired twice")


def _check_proposal(k: dict, proposal: dict, free: list[dict], table: dict[str, str]):
    """None and the assignment when a resolver's proposal passes the checks, else the reason it is refused."""
    g = next((x for x in free if x["id"] == proposal.get("odds_event_id")), None)
    if g is None:
        return "unknown or already paired game", None
    if g["league"] != k["league"]:
        return "league differs", None
    if g["day"] != k["day"]:
        return "date differs (%s against %s)" % (g["day"], k["day"]), None
    teams = proposal.get("teams")
    if not isinstance(teams, dict) or len(k["teams"]) != 2:
        return "teams missing", None
    names = {t["name"]: t for t in k["teams"]}
    if set(teams) != set(names) or sorted(teams.values()) != sorted([g["home"], g["away"]]):
        return "teams do not agree with the game", None
    home = next(names[n] for n, v in teams.items() if v == g["home"])
    away = next(names[n] for n, v in teams.items() if v == g["away"])
    for t, full in ((home, g["home"]), (away, g["away"])):
        known = table.get(t["code"])
        if known is not None and known != full:
            return "team %s is %s by the code table" % (t["code"], known), None
        if known is None and not loosely_names(t["name"], full):
            return "team name %r does not resemble %r" % (t["name"], full), None
    return None, (g, home, away)


class LocalModelResolver:
    """The default resolver: the local model behind an OpenAI-style endpoint, asked for JSON with reasoning off.
    It only proposes; `pair_games` decides. Anything but a JSON object comes back as None."""

    def __init__(self, url: str = RESOLVER_URL, model: str = RESOLVER_MODEL, post: Callable | None = None,
                 timeout: float = 120.0):
        self.url, self.model, self.timeout = url.rstrip("/"), model, timeout
        self.post = post or _post_json
        self.source = model

    def __call__(self, kevent: dict, candidates: Sequence[dict]) -> dict | None:
        names = [t["name"] for t in kevent.get("teams", [])]
        ask = {"kalshi_game": {"league": kevent.get("league"), "date_us_eastern": str(kevent.get("day")),
                               "teams": names, "event": kevent.get("event")},
               "candidates": [{"odds_event_id": c["id"], "home": c["home"], "away": c["away"],
                               "start_utc": c["commence"], "date_us_eastern": str(c["day"])} for c in candidates]}
        payload = {"model": self.model, "temperature": 0, "reasoning_effort": "none",
                   "messages": [
                       {"role": "system", "content": "You match sports games between two listings. Answer with one "
                        "JSON object only: {\"odds_event_id\": <id or null>, \"teams\": {<each Kalshi team name>: "
                        "<the matching team name of that candidate>}}. Propose a candidate only if it is the same "
                        "game: same league, same date, the same two teams. Otherwise answer {\"odds_event_id\": null}."},
                       {"role": "user", "content": json.dumps(ask)}]}
        answer = self.post(self.url + "/chat/completions", payload, self.timeout)
        try:
            text = answer["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            return None
        text = re.sub(r"^```(?:json)?|```$", "", text.strip()).strip()
        try:
            got = json.loads(text)
        except ValueError:
            return None
        return got if isinstance(got, dict) else None


def _post_json(url: str, payload: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def run_pairing(lstore: LinesStore, kstore: kh.HistoryStore, start: date, end: date, resolver: Callable | None,
                leagues: Sequence[str], clock: Callable[[], datetime]) -> dict:
    at = _iso(clock())
    out: dict[str, Any] = {}
    for league in leagues:
        have = lstore.pairs(league)
        kev = kalshi_events(kstore, league, start, end)
        games = odds_games(lstore, league, start, end)
        pairs, left, log = pair_games(kev, games, resolver, taken_odds={p["odds_event_id"] for p in have},
                                      taken_kalshi={p["kalshi_event"] for p in have})
        for p in pairs:
            lstore.put_pair(p, at)
        for rec in log:
            lstore.log_proposal(rec, at)
        paired_now = {p["kalshi_event"] for p in pairs} | {p["kalshi_event"] for p in have}
        reasons = {}
        if left:
            reasons = _reasons(kev, games, paired_now, pairs, log, left)
        for event, reason in reasons.items():
            day = next(k["day"] for k in kev if k["event"] == event)
            lstore.put_unpaired(event, league, day, reason, at)
        lstore.db.commit()
        out[league] = {"kalshi_events": len(kev), "odds_games": len(games), "paired_before": len(have),
                       "paired_now": len(pairs), "left_out": left,
                       "by_resolver": sum(p["source"] != "code" for p in pairs)}
    return out


def _reasons(kev, games, paired, pairs, log, counts) -> dict[str, str]:
    """The reason of each still-unpaired event (pair_games counts them; this recovers them one by one)."""
    out = {}
    refused = {r["kalshi_event"] for r in log if not r["accepted"] and r["reason"] not in ("no proposal",
                                                                                           "resolver error")}
    errored = {r["kalshi_event"] for r in log if r["reason"] == "resolver error"}
    for k in kev:
        if k["event"] in paired:
            continue
        if len(k["teams"]) != 2:
            out[k["event"]] = "kalshi_event_incomplete"
        elif k["event"] in errored:
            out[k["event"]] = "resolver_error"
        elif k["event"] in refused:
            out[k["event"]] = "resolver_refused"
        else:
            same_day = [g for g in games if g["day"] == k["day"] and _assign(k, g, {})]
            out[k["event"]] = "ambiguous" if len(same_day) > 1 else "no_match"
    return out


# ---------------------------------------------------------------- the numbers

def kalshi_fee(price: float) -> float:
    """Kalshi's taker fee a contract: 0.07 x price x (1 - price), rounded up to the cent on an order of 100."""
    c = int(round(float(price) * 100))
    cents = -(-7 * c * (100 - c) // 100)                  # ceil(7 c (100 - c) / 100) cents on 100 contracts
    return cents / 100.0 / 100.0


def book_probabilities(books: dict[str, tuple[float, float]]) -> dict[str, float]:
    """Per book with both prices: the home team's implied probability divided by the sum of both."""
    out = {}
    for b, (dh, da) in books.items():
        if dh and da and dh > 1 and da > 1:
            ih, ia = 1.0 / dh, 1.0 / da
            out[b] = ih / (ih + ia)
    return out


def books_p(books: dict[str, tuple[float, float]], version: str) -> float | None:
    """The books' probability of the home team: the median across books (at least 3), or Pinnacle alone."""
    per = book_probabilities(books)
    if version == "pinnacle":
        return per.get("pinnacle")
    if version != "median":
        raise ValueError("version must be 'median' or 'pinnacle'")
    return float(median(per.values())) if len(per) >= MIN_BOOKS else None


def quote_at(candles: Sequence[dict], moment: float, close_ts: float | None) -> dict | None:
    """The yes bid and ask (closes) of the last candle that ends at or before `moment`: the candle of that minute,
    or, when Kalshi left that minute out (it skips minutes in which nothing changed; seen in 12 of 2,440 minutes on
    2026-11-13), the last earlier one within the hour fetched. Candles after the moment or after the market's close
    are never read. None without such a candle, or without a bid above 0 and an ask below 1."""
    best = None
    for c in candles:
        end = int(c["end_ts"])
        if end > moment or end <= moment - QUOTE_HOUR or (close_ts is not None and end > close_ts):
            continue
        if best is None or end > int(best["end_ts"]):
            best = c
    if best is None:
        return None
    bid, ask = (best.get("yes_bid") or {}).get("close"), (best.get("yes_ask") or {}).get("close")
    if bid is None or ask is None or not bid > 0 or not ask < 1 or ask < bid:
        return None
    return {"bid": float(bid), "ask": float(ask)}


def _event_books(event: dict, home: str, away: str) -> dict[str, tuple[float, float]]:
    out = {}
    for b in event.get("bookmakers") or []:
        if b.get("key") not in BOOKS:
            continue
        for m in b.get("markets") or []:
            if m.get("key") != "h2h":
                continue
            prices = {o.get("name"): o.get("price") for o in m.get("outcomes") or []}
            dh, da = prices.get(home), prices.get(away)
            if isinstance(dh, (int, float)) and isinstance(da, (int, float)) and dh > 1 and da > 1:
                out[b["key"]] = (float(dh), float(da))
    return dict(sorted(out.items()))


def collect_games(lstore: LinesStore, kstore: kh.HistoryStore, start: date, end: date,
                  leagues: Sequence[str] = tuple(LEAGUES)) -> tuple[list[dict], dict]:
    """Every paired game of the period with what is known at its moment (books, both quotes) and its result, or the
    reason it cannot be scored. Also the pairing summary."""
    games: list[dict] = []
    unpaired = lstore.latest_unpaired()
    pairing = {"kalshi_events": 0, "paired": 0, "left_out": {}}
    for league in leagues:
        kev = {k["event"]: k for k in kalshi_events(kstore, league, start, end)}
        pairing["kalshi_events"] += len(kev)
        pairs = [p for p in lstore.pairs(league) if p["kalshi_event"] in kev]
        pairing["paired"] += len(pairs)
        for e in kev:
            if e not in {p["kalshi_event"] for p in pairs}:
                r = unpaired.get(e, "not_paired_yet")
                pairing["left_out"][r] = pairing["left_out"].get(r, 0) + 1
        snaps = lstore.snapshots(league)
        by_at = {s["requested_at"]: s for s in snaps}
        firsts: dict[str, dict] = {}
        for s in snaps:
            if s["kind"] != "first":
                continue
            for e in s["raw"].get("data") or []:
                if e.get("id") and e.get("commence_time") and s["day"] == eastern_date(e["commence_time"]).isoformat():
                    firsts.setdefault(e["id"], s)
        for p in pairs:
            k = kev[p["kalshi_event"]]
            games.append(_game(kstore, league, k, p, firsts.get(p["odds_event_id"]), by_at))
    games.sort(key=lambda g: (g["day"], g["league"], g["kalshi_event"]))
    return games, pairing


def _game(kstore: kh.HistoryStore, league: str, k: dict, p: dict, first: dict | None, by_at: dict) -> dict:
    g = {"league": league, "day": k["day"], "kalshi_event": k["event"], "odds_event_id": p["odds_event_id"],
         "home_team": p["home_team"], "away_team": p["away_team"], "moment": None, "books": {}, "home": None,
         "away": None, "y": None, "reason": None}
    hm, am = k["markets"][p["home_ticker"]], k["markets"][p["away_ticker"]]
    results = (hm.get("result"), am.get("result"))
    g["y"] = 1 if results == ("yes", "no") else 0 if results == ("no", "yes") else None
    if first is None:
        g["reason"] = "no_first_snapshot"
        return g
    odds_day = date.fromisoformat(first["day"])
    ev = next(e for e in first["raw"]["data"] if e.get("id") == p["odds_event_id"])
    start = _parse(ev["commence_time"])
    wave_at = next((w for w, members in select_waves(_parse(e["commence_time"])
                                                     for e in day_games(first["raw"], odds_day)) if start in members),
                   None)
    if wave_at is None:
        g["reason"] = "not_in_wave"
        return g
    wave = by_at.get(_iso(wave_at))
    if wave is None:
        g["reason"] = "no_wave_snapshot"
        return g
    wev = next((e for e in wave["raw"].get("data") or [] if e.get("id") == p["odds_event_id"]), None)
    if wev is None:
        g["reason"] = "not_in_wave_snapshot"
        return g
    g["moment"] = wave["timestamp"]
    moment = _parse(wave["timestamp"]).timestamp()
    g["books"] = _event_books(wev, p["home_team"], p["away_team"])
    for side, m in (("home", hm), ("away", am)):
        close = m.get("close_time")
        g[side] = quote_at(kstore.candles(m["ticker"]), moment, kh.iso_ts(close) if close else None)
    return g


def _week(d: date) -> tuple[int, int]:
    iso = d.isocalendar()
    return iso[0], iso[1]


def t_weekly(days: Sequence[date], values: Sequence[float]) -> tuple[float | None, int]:
    """The t statistic of the weekly means (ISO weeks, each week once) and the number of weeks; None with fewer than
    two weeks or no spread between them."""
    by: dict[tuple[int, int], list[float]] = {}
    for d, v in zip(days, values):
        by.setdefault(_week(d), []).append(float(v))
    if len(by) < 2:
        return None, len(by)
    weekly = np.array([np.mean(v) for v in by.values()])
    sd = float(weekly.std(ddof=1))
    return (float(weekly.mean() / (sd / math.sqrt(len(weekly)))) if sd > 0 else None), len(by)


def week_interval(days: Sequence[date], values: Sequence[float], seed: int, reps: int) -> list[float] | None:
    """95% interval of the mean of `values` from resampling whole weeks with replacement (a fixed seed)."""
    if not values:
        return None
    by: dict[tuple[int, int], list[float]] = {}
    for d, v in zip(days, values):
        by.setdefault(_week(d), []).append(float(v))
    weeks = [np.array(by[w]) for w in sorted(by)]
    sums = np.array([w.sum() for w in weeks])
    counts = np.array([len(w) for w in weeks])
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(weeks), size=(reps, len(weeks)))
    means = sums[idx].sum(axis=1) / counts[idx].sum(axis=1)
    return [float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))]


def _score(g: dict, version: str) -> tuple[dict | None, str | None]:
    if g.get("reason"):
        return None, g["reason"]
    p = books_p(g["books"], version)
    if p is None:
        return None, "too_few_books" if version == "median" else "no_pinnacle"
    h, a = g.get("home"), g.get("away")
    if not h or not a:
        return None, "no_bid_ask"
    for q in (h, a):
        if int(round(q["ask"] * 100)) - int(round(q["bid"] * 100)) > MAX_SPREAD_CENTS:
            return None, "wide_spread"
    if g.get("y") is None:
        return None, "no_result"
    return {"p": p, "mid": (h["bid"] + h["ask"]) / 2}, None


def _brier(rows: list[dict], seed: int, reps: int) -> dict:
    if not rows:
        return {"n": 0, "weeks": 0, "books": None, "kalshi": None, "difference": None, "ci95": None}
    b = [(r["p"] - r["y"]) ** 2 for r in rows]
    k = [(r["mid"] - r["y"]) ** 2 for r in rows]
    d = [x - y for x, y in zip(b, k)]
    days = [r["day"] for r in rows]
    return {"n": len(rows), "weeks": len({_week(x) for x in days}), "books": float(np.mean(b)),
            "kalshi": float(np.mean(k)), "difference": float(np.mean(d)),
            "ci95": week_interval(days, d, seed, reps)}


def _trade(r: dict, margin: float) -> dict | None:
    best = None
    for side, p, q, won in (("home", r["p"], r["home"], r["y"]), ("away", 1.0 - r["p"], r["away"], 1 - r["y"])):
        fee = kalshi_fee(q["ask"])
        edge = p - q["ask"] - fee
        if best is None or edge > best["edge"]:
            best = {"side": side, "p": p, "ask": q["ask"], "fee": fee, "edge": edge, "profit": won - q["ask"] - fee}
    if best["edge"] < margin - 1e-12:
        return None
    return dict(best, kalshi_event=r["kalshi_event"], league=r["league"], day=r["day"].isoformat())


def _trading(trades: list[dict]) -> dict:
    profits = [t["profit"] for t in trades]
    t, weeks = t_weekly([date.fromisoformat(x["day"]) for x in trades], profits)
    return {"trades": len(trades), "mean_profit": float(np.mean(profits)) if trades else None,
            "total_profit": float(np.sum(profits)) if trades else 0.0, "t_weekly": t, "weeks": weeks,
            "list": trades}


def evaluate_games(games: Sequence[dict], margins: Sequence[float] = MARGINS, versions: Sequence[str] = VERSIONS,
                   seed: int = SEED, reps: int = REPS) -> dict:
    """Both questions for every version and margin: {version: {"0.02": block, ...}}. A block has games counted, left
    out by reason, both Brier scores on the home team winning with their difference and its week-resampled interval,
    the trades (at most one a game, on the side with the larger p - ask - fee, when that is at least the margin),
    their mean profit a contract and the t of weekly mean profits, and the same by league."""
    out: dict[str, dict] = {}
    for version in versions:
        rows, left = [], {}
        for g in games:
            s, why = _score(g, version)
            if s is None:
                left[why] = left.get(why, 0) + 1
                continue
            rows.append(dict(g, **s))
        out[version] = {}
        for margin in margins:
            trades = [t for t in (_trade(r, margin) for r in rows) if t is not None]
            block = {"games_counted": len(rows), "left_out": dict(left), "brier": _brier(rows, seed, reps),
                     "trading": _trading(trades), "by_league": {}}
            for league in sorted({r["league"] for r in rows}):
                lr = [r for r in rows if r["league"] == league]
                block["by_league"][league] = {"games_counted": len(lr), "brier": _brier(lr, seed, reps),
                                              "trading": _trading([t for t in trades if t["league"] == league])}
            out[version]["%.2f" % margin] = block
    return out


# ---------------------------------------------------------------- Kalshi fetches driven by the pairs

def candle_moments(lstore: LinesStore, kstore: kh.HistoryStore, start: date, end: date,
                   leagues: Sequence[str]) -> dict[str, list[float]]:
    """For each contract of each paired game with a wave snapshot: the moment its quote is needed."""
    out: dict[str, list[float]] = {}
    games, _ = collect_games(lstore, kstore, start, end, leagues)
    pairs = {p["kalshi_event"]: p for p in lstore.pairs()}
    for g in games:
        if g["moment"] is None:
            continue
        p = pairs[g["kalshi_event"]]
        for ticker in (p["home_ticker"], p["away_ticker"]):
            out.setdefault(ticker, []).append(_parse(g["moment"]).timestamp())
    return out


def fetch_kalshi_candles(client: kh.HistoryClient, lstore: LinesStore, kstore: kh.HistoryStore, start: date,
                         end: date, leagues: Sequence[str]) -> dict:
    moments = candle_moments(lstore, kstore, start, end, leagues)
    if not moments:
        return {"markets": 0, "windows_fetched": 0, "windows_cached": 0, "candles_stored": 0}
    cutoff = kh.cutoff_ts(client, kstore)
    rows = {m["ticker"]: m for m in kstore.markets()}
    total = {"markets": len(moments), "windows_fetched": 0, "windows_cached": 0, "candles_stored": 0}
    for ticker, ms in sorted(moments.items()):
        got = kh.fetch_candles(client, kstore, rows[ticker], ms, cutoff)
        for k in ("windows_fetched", "windows_cached", "candles_stored"):
            total[k] += got[k]
    return total


# ---------------------------------------------------------------- the commands

def _stores(root: Path, clock) -> tuple[LinesStore, kh.HistoryStore, CreditLedger]:
    return (LinesStore(root / "lines.sqlite"), kh.HistoryStore(root / "kalshi.sqlite"),
            CreditLedger(root / "credits.sqlite", clock=clock))


def _period_dates(name: str, periods: dict) -> list[tuple[str, date, date]]:
    names = list(periods) if name == "all" else [name]
    return [(n, *periods[n]) for n in names]


def _evaluate(name: str, lstore: LinesStore, kstore: kh.HistoryStore, periods: dict, leagues, clock) -> dict:
    start, end = periods[name]
    if name == "test":
        choice = lstore.choice()
        if choice is None:
            raise Refused("the test period is scored only with a stored choice (`choose` first)")
        if lstore.result("test") is not None:
            raise Refused("the test period has already been scored once; its stored result stands")
        margins, versions = (choice["margin"],), (choice["version"],)
    else:
        choice, margins, versions = None, MARGINS, VERSIONS
    games, pairing = collect_games(lstore, kstore, start, end, leagues)
    out = {"period": name, "start": start.isoformat(), "end": end.isoformat(), "choice": choice,
           "pairing": pairing, "seed": SEED, "reps": REPS,
           "results": evaluate_games(games, margins, versions, SEED, REPS)}
    if name == "test":
        lstore.put_result("test", out, _iso(clock()))
    return out


def main(argv=None, *, odds_client: oa.OddsApiClient | None = None, kalshi_client: kh.HistoryClient | None = None,
         resolver: Callable | None = None, clock: Callable[[], datetime] | None = None,
         periods: dict | None = None) -> int:
    clock = clock or _system_clock
    periods = periods or PERIODS
    ap = argparse.ArgumentParser(prog="sports_lines", description="Sportsbook lines against Kalshi prices (research).")
    ap.add_argument("--store", default=str(REPO / "artifacts" / "sports_lines"), help="folder of the sqlite stores")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def with_leagues(p):
        p.add_argument("--leagues", nargs="+", default=list(LEAGUES), choices=list(LEAGUES))
        return p

    with_leagues(sub.add_parser("kalshi-markets", help="Kalshi's settled game markets (keyless)")).add_argument(
        "--period", choices=["dev", "test", "all"], required=True)
    p = with_leagues(sub.add_parser("plan", help="what the rule would buy, no paid call"))
    p.add_argument("--period", choices=["dev", "test", "all"], default="all")
    p.add_argument("--cap", type=int, default=HISTORY_CAP)
    p.add_argument("--monthly-cap", type=int, default=MONTHLY_CAP)
    p = with_leagues(sub.add_parser("fetch-odds", help="buy the plan's snapshots through the credit ledger"))
    p.add_argument("--period", choices=["dev", "test"], required=True)
    p.add_argument("--cap", type=int, required=True)
    p.add_argument("--monthly-cap", type=int, default=MONTHLY_CAP)
    p = with_leagues(sub.add_parser("pair", help="pair Kalshi events with Odds API games"))
    p.add_argument("--period", choices=["dev", "test"], required=True)
    p.add_argument("--no-resolver", action="store_true", help="code only; leave what code cannot pair")
    with_leagues(sub.add_parser("kalshi-candles", help="candles around each paired game's moment (keyless)")).add_argument(
        "--period", choices=["dev", "test"], required=True)
    p = with_leagues(sub.add_parser("evaluate", help="the two questions (dev: every choice; test: once, the choice)"))
    p.add_argument("--period", choices=["dev", "test"], required=True)
    p = sub.add_parser("choose", help="store the one choice made on the development period")
    p.add_argument("--margin", type=float, required=True)
    p.add_argument("--version", choices=list(VERSIONS), required=True)
    sub.add_parser("resolved", help="list the pairs the resolver proposed and the checks accepted")
    args = ap.parse_args(argv)

    root = Path(args.store)
    lstore, kstore, ledger = _stores(root, clock)
    if getattr(args, "monthly_cap", None) is not None:
        ledger.caps["paid"] = int(args.monthly_cap)
    leagues = getattr(args, "leagues", list(LEAGUES))
    out: Any
    if args.cmd == "kalshi-markets":
        client = kalshi_client or kh.HistoryClient()
        cutoff = kh.cutoff_ts(client, kstore)
        out = {}
        for name, start, end in _period_dates(args.period, periods):
            out[name] = {lg: kh.fetch_settled(client, kstore, LEAGUES[lg][1], start, end, cutoff) for lg in leagues}
        out["requests"] = client.requests
    elif args.cmd == "plan":
        names = list(periods) if args.period == "all" else [args.period]
        out = make_plan(lstore, kstore, ledger, args.cap, leagues, {n: periods[n] for n in names})
    elif args.cmd == "fetch-odds":
        if args.period == "test" and lstore.choice() is None:
            raise Refused("test-period snapshots are refused until a choice is stored (`choose`)")
        client = odds_client or oa.OddsApiClient()
        out = fetch_odds(args.period, client, ledger, lstore, kstore, args.cap, clock, leagues, periods,
                         progress=lambda s: print(s, file=sys.stderr, flush=True))
    elif args.cmd == "pair":
        start, end = periods[args.period]
        res = None if args.no_resolver else (resolver or LocalModelResolver())
        out = run_pairing(lstore, kstore, start, end, res, leagues, clock)
    elif args.cmd == "kalshi-candles":
        start, end = periods[args.period]
        out = fetch_kalshi_candles(kalshi_client or kh.HistoryClient(), lstore, kstore, start, end, leagues)
    elif args.cmd == "evaluate":
        out = _evaluate(args.period, lstore, kstore, periods, leagues, clock)
    elif args.cmd == "choose":
        if not any(abs(args.margin - m) < 1e-9 for m in MARGINS):
            raise ValueError("the margin must be one of %s" % (MARGINS,))
        lstore.put_choice(float(args.margin), args.version, _iso(clock()))
        out = {"choice": lstore.choice()}
    else:
        out = lstore.accepted_proposals()
    print(json.dumps(out, indent=1, default=str))
    return 0


def _cli() -> int:
    try:
        return main()
    except (Refused, CreditCapError, ValueError) as exc:
        print("refused: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
