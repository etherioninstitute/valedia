"""Sportsbook lines against Kalshi's price for the same game: research code for the locked prediction in
docs/research/2026-11-13-sports-lines-prediction.md (with its six clarifications). It buys history, pairs games and
scores them. It has no way to trade: nothing here sends anything to an exchange or a broker.

The steps (each a command of `main`, run in this order; every store lives in one folder, `--store`):

1. `kalshi-markets`: Kalshi's settled game markets of the five series for a period (keyless GETs,
   `venues.kalshi_history`). They also say which league-days have a game.
2. `plan`: no paid call. Per league and US-Eastern day with a Kalshi game: one Odds API snapshot at 16:00 UTC, then
   one 30 minutes before each of the (at most three) biggest waves of starts (`select_waves`). A day whose 16:00
   snapshot is stored is planned exactly; any other day at its upper bound, 1 + 3 snapshots. The test period is always
   reserved in full, for all five leagues whatever `--leagues` names (`reserved_for_test`); if the budget (the cap
   less what the ledger has spent, and no more than this month's remaining credits) would be passed, whole days are
   dropped from the end of the development period, and the plan says so (`left_for_dev`).
3. `fetch-odds`: buys the plan, day by day from the start, every call through the credit ledger (`CreditLedger`,
   its own sqlite file, one process at a time). It begins with the free sports call, which refreshes the month's
   remaining figure and confirms what calls left pending by a crash really used. Each HTTP attempt is the ledger's
   own: both caps checked before it, a pending row written before it and completed after it with that attempt's
   headers, at most 3 attempts a snapshot. The run stops (exit code 3) after an answer without usage headers, or when
   a call costs other than expected (the book list is not cut; the lead decides). A snapshot already stored, or whose
   answer the ledger already holds, is never bought again. Test-period snapshots are refused until a choice is stored.
4. `pair`: a Kalshi event and an Odds API game are one game only when (a) both team names match by `names_match`
   (one strict rule that honours qualifying words in both directions) and each Kalshi name matches only one team of
   the league in the odds data, (b) the game starts within six hours before Kalshi's expected end of the game and not
   after it, on the same US-Eastern date, and (c) no other game satisfies (a) and (b) for the event and no other
   event satisfies them for the game. Anything else is left out as `ambiguous` or `no_match`. What code leaves out is
   offered to the injected resolver (by default the local model, `LocalModelResolver`); a proposal is kept only if it
   passes the same three checks, so it can never pair what the checks refuse. Every proposal is logged.
5. `kalshi-candles`: one-minute candles of both contracts of each paired game, only for the hour that ends at the
   minute of the game's wave snapshot. A window that came back empty is asked once more on a later run.
6. `evaluate --period dev`: both questions of the prediction for each margin and each version of the books'
   probability. `choose` stores the one choice; `evaluate --period test` then runs once with it, only when every
   planned test snapshot is stored, pairing has run on them and candles are stored for every paired game.

The choice and the test result are also appended, with the sha256 of the three store files, to `scoring.log` beside
the stores; `choose` and `evaluate --period test` refuse when that file already holds a choice or a test result,
whatever the stores say. This guards against a slip, not against a person: someone who deletes `scoring.log` (or
copies the stores without it) can score again. The outside check is the lead's commit of the choice under the
prediction before any test-period snapshot is bought.

At the moment of a game (its wave snapshot's own reported time): start times, which form the waves, come from the
day's 16:00 UTC snapshot, also for a wave whose snapshot falls before 16:00 UTC; the books' probability uses only the
wave's own snapshot; Kalshi's bid and ask come from the last candle that ends at or before that time, within the hour
before it, and candles after it, or after the market's close, are never read. A wave snapshot reported more than ten
minutes before the time asked for is not used: its games are left out as `stale_snapshot`.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import unicodedata
import urllib.request
import zlib
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from statistics import median
from typing import Any, Callable, Iterable, Sequence
from zoneinfo import ZoneInfo

import numpy as np

from valedia_trader.research_loop import REPO
from valedia_trader.venues import kalshi_history as kh
from valedia_trader.venues import odds_api as oa

__all__ = ["LEAGUES", "BOOKS", "PERIODS", "MARGINS", "VERSIONS", "CreditLedger", "CreditCapError", "LedgerStop",
           "LedgerBusy", "Refused", "LinesStore", "select_waves", "all_waves", "eastern_date", "first_snapshot_time",
           "paid_snapshot", "refresh_usage", "buy", "make_plan", "fetch_odds", "names_match", "pair_games",
           "LocalModelResolver", "kalshi_fee", "book_probabilities", "books_p", "quote_at", "collect_games",
           "evaluate_games", "test_readiness", "main"]

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
STALE = timedelta(minutes=10)          # clarification 5
PAIR_WINDOW = timedelta(hours=6)       # clarification 6
HISTORY_CAP = 9000
MONTHLY_CAP = 20000
FREE_MONTHLY_CAP = 500
MAX_ATTEMPTS = 3                       # HTTP attempts for one snapshot in one run
RETRY_STATUS = (429, 500, 502, 503, 504)
SNAPSHOT_RULE = "historical_odds:h2h:%d_books" % len(BOOKS)
SEED = 20261113
REPS = 2000
RESOLVER_URL = "http://127.0.0.1:1235/v1"
RESOLVER_MODEL = "qwen/qwen3.8-27b"
SCORING_LOG = "scoring.log"
STORE_FILES = ("lines.sqlite", "kalshi.sqlite", "credits.sqlite")


class CreditCapError(RuntimeError):
    """A paid call refused before it was made: it would pass the cap or the month's credits."""


class LedgerStop(RuntimeError):
    """The run stops: the ledger saw a charge it cannot count safely (an answer without usage headers, or a cost
    other than the one expected). The ledger row of that call says what was seen; the lead decides what to do."""


class LedgerBusy(RuntimeError):
    """Another process holds the credit ledger."""


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

_UNCERTAIN = "('pending', 'unanswered', 'no_headers')"
_SPENT = ("COALESCE(SUM(CASE WHEN status = 'not_sent' THEN 0 WHEN charged IS NULL AND status IN %s THEN expected "
          "ELSE COALESCE(charged, 0) END), 0)" % _UNCERTAIN)


class CreditLedger:
    """Every Odds API attempt, before and after. Before: its cost (by the documented rule, or the cost last charged
    for that rule if the two differed) is checked against the cap of the run (everything this ledger has spent with
    that key) and against the month (the monthly cap less the ledger's spend this UTC month, and no more than the last
    `x-requests-remaining` header said, less what is still uncertain since), and a `pending` row is written with that
    cost. After: the row is completed with that attempt's three usage headers and its status:

    - `done` (an answer with headers; the answer itself is stored in the row, so it is never bought twice),
    - `http_error` (an error answer with headers), `not_sent` (nothing was sent: no charge),
    - `unanswered` (sent, no answer came back) and `no_headers` (an answer without usage headers; the run stops),
      both counted at their expected cost until `reconcile` confirms the real use from a later call's headers,
    - `confirmed` (an uncertain row settled by `reconcile`).

    A row left `pending` by a crash is counted at its expected cost in the same way. Parameters are stored without
    the key. Rows written before rev 2 (no status) count what they charged."""

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
                last INTEGER, ok INTEGER, error TEXT, status TEXT, request_key TEXT, body BLOB);
            CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, kind TEXT, detail TEXT);
            CREATE TABLE IF NOT EXISTS costs (id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, rule TEXT, cost INTEGER);
        """)
        have = {r[1] for r in self.db.execute("PRAGMA table_info(calls)")}
        for col, kind in (("status", "TEXT"), ("request_key", "TEXT"), ("body", "BLOB")):
            if col not in have:                       # a ledger written before rev 2
                self.db.execute("ALTER TABLE calls ADD COLUMN %s %s" % (col, kind))
        self.db.commit()

    # what has been spent
    def expected_cost(self, rule: str, rule_cost: int) -> int:
        row = self.db.execute("SELECT cost FROM costs WHERE rule = ? ORDER BY id DESC LIMIT 1", (rule,)).fetchone()
        return int(row["cost"]) if row else int(rule_cost)

    def spent(self, key: str = "paid") -> int:
        return int(self.db.execute("SELECT %s FROM calls WHERE key = ?" % _SPENT, (key,)).fetchone()[0])

    def month_spent(self, key: str = "paid") -> int:
        month = self.clock().astimezone(UTC).strftime("%Y-%m")
        return int(self.db.execute("SELECT %s FROM calls WHERE key = ? AND substr(at, 1, 7) = ?" % _SPENT,
                                   (key, month)).fetchone()[0])

    def _last_remaining_row(self, key: str):
        return self.db.execute("SELECT id, remaining FROM calls WHERE key = ? AND remaining IS NOT NULL ORDER BY id "
                               "DESC LIMIT 1", (key,)).fetchone()

    def last_remaining(self, key: str = "paid") -> int | None:
        row = self._last_remaining_row(key)
        return None if row is None else int(row["remaining"])

    def _uncertain_after(self, key: str, row_id: int) -> int:
        return int(self.db.execute("SELECT COALESCE(SUM(expected), 0) FROM calls WHERE key = ? AND id > ? AND "
                                   "charged IS NULL AND status IN %s" % _UNCERTAIN, (key, row_id)).fetchone()[0])

    def month_remaining(self, key: str = "paid") -> int:
        left = self.caps[key] - self.month_spent(key)
        row = self._last_remaining_row(key)
        return left if row is None else min(left, int(row["remaining"]) - self._uncertain_after(key, row["id"]))

    def check(self, key: str, cost: int, cap: int) -> None:
        if cost <= 0:
            return
        spent = self.spent(key)
        if spent + cost > cap:
            raise CreditCapError("refused: %d spent + %d would pass the cap of %d" % (spent, cost, cap))
        month = self.month_spent(key)
        if month + cost > self.caps[key]:
            raise CreditCapError("refused: %d used this month by the ledger + %d would pass the monthly cap of %d"
                                 % (month, cost, self.caps[key]))
        row = self._last_remaining_row(key)
        if row is not None:
            open_ = self._uncertain_after(key, row["id"])
            if cost > int(row["remaining"]) - open_:
                raise CreditCapError("refused: %d credits needed, the last x-requests-remaining said %d%s"
                                     % (cost, row["remaining"], " (%d of them uncertain since)" % open_ if open_ else ""))

    # one attempt
    def attempt(self, key: str, rule: str, rule_cost: int, cap: int, path: str, params: dict, send: Callable[[], Any],
                client: oa.OddsApiClient, request_key: str | None = None,
                empty: Callable[[Any], bool] = lambda body: False, keep: Callable[[Any], Any] | None = None) -> Any:
        """One HTTP attempt (`send` must not retry): caps checked, a pending row written, the attempt made, the row
        completed. Raises the client's error after recording it, and `LedgerStop` (after `keep(answer)`) when the
        answer has no usage headers or costs other than expected."""
        expected = self.expected_cost(rule, rule_cost)
        self.check(key, expected, cap)
        clean = {k: v for k, v in params.items() if k.lower() not in ("apikey", "api_key", "x-api-key")}
        cur = self.db.execute("INSERT INTO calls (at, key, rule, path, params, expected, ok, status, request_key) "
                              "VALUES (?, ?, ?, ?, ?, ?, 0, 'pending', ?)",
                              (_iso(self.clock()), key, rule, path, _canon(clean), expected, request_key))
        rid = cur.lastrowid
        self.db.commit()
        try:
            body = send()
        except oa.OddsApiError as exc:
            usage = dict(client.last_usage)
            if not getattr(client, "attempts_sent", 1):
                self._finish(rid, "not_sent", 0, usage, error=str(exc))
            elif exc.status is None:
                self._finish(rid, "unanswered", None, usage, error=str(exc))
            elif usage["last"] is None:
                self._finish(rid, "no_headers", None, usage, error="HTTP %s" % exc.status)
                raise LedgerStop("an answer (HTTP %s) to %s came without usage headers, so its cost cannot be seen: "
                                 "the run stops. Row %d of the ledger says so and counts %d credits until a later "
                                 "run's free call confirms the use" % (exc.status, path, rid, expected)) from exc
            else:
                self._finish(rid, "http_error", usage["last"], usage, error="HTTP %s" % exc.status)
            raise
        usage = dict(client.last_usage)
        blob = zlib.compress(_canon(body).encode())
        if usage["last"] is None:
            self._finish(rid, "no_headers", None, usage, body=blob, ok=True)
            if keep is not None:
                keep(body)
            raise LedgerStop("the answer to %s came without usage headers, so its cost cannot be seen: the run "
                             "stops. Row %d of the ledger says so and counts %d credits until a later run's free call "
                             "confirms the use" % (path, rid, expected))
        charged = int(usage["last"])
        self._finish(rid, "done", charged, usage, body=blob, ok=True)
        if charged != expected and not (charged == 0 and empty(body)):
            now = _iso(self.clock())
            self.db.execute("INSERT INTO events (at, kind, detail) VALUES (?, ?, ?)",
                            (now, "cost_differs", _canon({"rule": rule, "expected": expected, "charged": charged})))
            self.db.execute("INSERT INTO costs (at, rule, cost) VALUES (?, ?, ?)", (now, rule, charged))
            self.db.commit()
            if keep is not None:
                keep(body)
            raise LedgerStop("a call by the rule %s cost %d credits, not the %d expected (headers: remaining %s, used "
                             "%s, last %s): the run stops before the next call. The book list is not cut; the lead "
                             "decides. The ledger now expects %d a call, so `plan` shows the new cost and running "
                             "fetch-odds again buys at it" % (rule, charged, expected, usage["remaining"],
                                                              usage["used"], usage["last"], charged))
        return body

    def _finish(self, rid: int, status: str, charged: int | None, usage: dict, body: bytes | None = None,
                ok: bool = False, error: str | None = None) -> None:
        self.db.execute("UPDATE calls SET status = ?, charged = ?, remaining = ?, used = ?, last = ?, ok = ?, "
                        "error = ?, body = ? WHERE id = ?",
                        (status, charged, usage.get("remaining"), usage.get("used"), usage.get("last"), 1 if ok else 0,
                         error, body, rid))
        self.db.commit()

    def stored_answer(self, request_key: str) -> Any:
        """The answer of an earlier call with the same request, if the ledger holds one."""
        row = self.db.execute("SELECT body FROM calls WHERE request_key = ? AND body IS NOT NULL ORDER BY id DESC "
                              "LIMIT 1", (request_key,)).fetchone()
        return None if row is None else json.loads(zlib.decompress(row["body"]))

    def reconcile(self, key: str = "paid") -> dict:
        """Settle uncertain rows (pending, unanswered, no headers) from `x-requests-used`: what the account used
        between the last header before them and the latest header after them, less what the calls with headers in
        between were charged, is what they cost. Set in row order, each at most its expected cost, any rest on the
        last (an event says so). Nothing is settled across a monthly reset or without a header on both sides."""
        rows = self.db.execute("SELECT id, expected FROM calls WHERE key = ? AND charged IS NULL AND status IN %s "
                               "ORDER BY id" % _UNCERTAIN, (key,)).fetchall()
        if not rows:
            return {"confirmed": 0}
        before = self.db.execute("SELECT id, used, at FROM calls WHERE key = ? AND used IS NOT NULL AND id < ? "
                                 "ORDER BY id DESC LIMIT 1", (key, rows[0]["id"])).fetchone()
        after = self.db.execute("SELECT id, used, at FROM calls WHERE key = ? AND used IS NOT NULL AND id > ? "
                                "ORDER BY id DESC LIMIT 1", (key, rows[0]["id"])).fetchone()
        if before is None or after is None:
            return {"confirmed": 0, "open": len(rows), "note": "no usage header on both sides of the open rows"}
        if before["at"][:7] != after["at"][:7] or after["used"] < before["used"]:
            return {"confirmed": 0, "open": len(rows), "note": "a monthly reset lies between the headers"}
        known = int(self.db.execute("SELECT COALESCE(SUM(last), 0) FROM calls WHERE key = ? AND id > ? AND id <= ? "
                                    "AND last IS NOT NULL", (key, before["id"], after["id"])).fetchone()[0])
        rest = int(after["used"]) - int(before["used"]) - known
        todo = [r for r in rows if r["id"] < after["id"]]
        now = _iso(self.clock())
        if rest < 0 or not todo:
            return {"confirmed": 0, "open": len(rows), "note": "the headers do not settle the open rows"}
        for i, r in enumerate(todo):
            cost = min(int(r["expected"]), rest) if i < len(todo) - 1 else rest
            rest -= cost
            self.db.execute("UPDATE calls SET status = 'confirmed', charged = ?, error = COALESCE(error || '; ', '') "
                            "|| 'confirmed from x-requests-used' WHERE id = ?", (cost, r["id"]))
        self.db.execute("INSERT INTO events (at, kind, detail) VALUES (?, ?, ?)",
                        (now, "confirmed", _canon({"rows": [r["id"] for r in todo], "from_used": [before["used"],
                                                                                                 after["used"]]})))
        self.db.commit()
        return {"confirmed": len(todo), "open": len(rows) - len(todo)}

    @contextlib.contextmanager
    def lock(self):
        """One process at a time per ledger (an exclusive lock on `<ledger>.lock`)."""
        f = open(self.path.with_name(self.path.name + ".lock"), "a")
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            f.close()
            raise LedgerBusy("another process holds the credit ledger %s; this run stops without a call"
                             % self.path) from None
        try:
            yield self
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            f.close()

    def calls(self) -> list[dict]:
        return [dict({k: r[k] for k in r.keys() if k != "body"}, params=json.loads(r["params"]))
                for r in self.db.execute("SELECT * FROM calls ORDER BY id")]

    def events(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM events ORDER BY id")]


def _snapshot_request(sport: str, when: str) -> tuple[str, dict]:
    return "/v4/historical/sports/%s/odds" % sport, {"date": when, "markets": "h2h", "bookmakers": ",".join(BOOKS)}


def _request_key(path: str, params: dict) -> str:
    return _canon({"path": path, "params": params})


def paid_snapshot(client: oa.OddsApiClient, ledger: CreditLedger, sport: str, when: str, cap: int,
                  keep: Callable[[Any], Any] | None = None) -> dict:
    """One historical h2h snapshot of the ten books, with the paid key, through the ledger: at most 3 attempts, each
    its own ledger row with the caps checked before it; a retry only after a 429 or 5xx answer or no answer."""
    path, params = _snapshot_request(sport, when)
    sleep = getattr(client, "_sleep", lambda s: None)
    for attempt in range(MAX_ATTEMPTS):
        try:
            return ledger.attempt("paid", SNAPSHOT_RULE, oa.historical_cost("h2h", BOOKS), cap, path, params,
                                  lambda: client.historical_odds(sport, when, BOOKS, markets="h2h", key="paid",
                                                                 retries=0),
                                  client, request_key=_request_key(path, params),
                                  empty=lambda body: not (isinstance(body, dict) and body.get("data")), keep=keep)
        except oa.OddsApiError as exc:
            again = getattr(client, "attempts_sent", 0) > 0 and (exc.status is None or exc.status in RETRY_STATUS)
            if not again or attempt == MAX_ATTEMPTS - 1:
                raise
            sleep(getattr(client, "backoff", 1.0) * 2 ** attempt)
    raise AssertionError("unreachable")


def refresh_usage(client: oa.OddsApiClient, ledger: CreditLedger, key: str = "paid") -> dict:
    """The free sports call (`/v4/sports`, no credit) through the ledger: its headers give the month's remaining
    figure afresh and settle what calls left pending or unanswered really used (`CreditLedger.reconcile`)."""
    ledger.attempt(key, "sports", 0, 0, "/v4/sports", {}, lambda: client.sports_with(key=key, retries=0), client)
    return ledger.reconcile(key)


# ---------------------------------------------------------------- the store

class LinesStore:
    """sqlite: snapshots (keyed by league and the time asked), pairs, the pairing log and runs, the choice and the
    test result. Nothing fetched or decided is overwritten; a different copy of a snapshot goes to `differences`."""

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
            CREATE TABLE IF NOT EXISTS pair_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, league TEXT, period TEXT,
                snapshots_seen INTEGER, at TEXT);
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

    def count_snapshots(self, league: str, start: date, end: date) -> int:
        return int(self.db.execute("SELECT COUNT(*) FROM snapshots WHERE league = ? AND day >= ? AND day <= ?",
                                   (league, start.isoformat(), end.isoformat())).fetchone()[0])

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

    def put_pair_run(self, league: str, period: str, snapshots_seen: int, at: str) -> None:
        self.db.execute("INSERT INTO pair_runs (league, period, snapshots_seen, at) VALUES (?, ?, ?, ?)",
                        (league, period, snapshots_seen, at))

    def pair_run(self, league: str, period: str) -> dict | None:
        r = self.db.execute("SELECT * FROM pair_runs WHERE league = ? AND period = ? ORDER BY id DESC LIMIT 1",
                            (league, period)).fetchone()
        return None if r is None else dict(r)

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


# ---------------------------------------------------------------- scoring.log beside the stores

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def scoring_entries(root: Path) -> list[dict]:
    """The lines of `scoring.log` (none when the file is missing). A line that is not JSON refuses everything that
    reads the log: the file is only ever appended to."""
    path = Path(root) / SCORING_LOG
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            raise Refused("%s has a line that is not JSON; it is never rewritten, look at it" % path) from None
    return out


def append_scoring(root: Path, kind: str, payload: dict, clock: Callable[[], datetime]) -> None:
    root = Path(root)
    entry = {"kind": kind, "at": _iso(clock()),
             "sha256": {name: _sha256(root / name) for name in STORE_FILES if (root / name).exists()}, **payload}
    with open(root / SCORING_LOG, "a") as f:
        f.write(json.dumps(entry, sort_keys=True, default=str) + "\n")
        f.flush()
        os.fsync(f.fileno())


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


def _period_leagues(name: str, leagues: Sequence[str]) -> tuple[str, ...]:
    """The test period is always all five leagues; the development period the leagues named."""
    return tuple(LEAGUES) if name == "test" else tuple(leagues)


def make_plan(lstore: LinesStore, kstore: kh.HistoryStore, ledger: CreditLedger, cap: int,
              leagues: Sequence[str] = tuple(LEAGUES), periods: dict | None = None,
              days_map: dict | None = None) -> dict:
    """What the rule would buy for each period, and its cost, without any call. See the module text for the drop.
    The test period is planned (and reserved) for all five leagues whatever `leagues` names. `days_map`
    ({(period, league): {day: events}}) saves re-reading the Kalshi store."""
    periods = periods or PERIODS
    days_map = dict(days_map or {})
    for n in periods:
        for lg in _period_leagues(n, leagues):
            if (n, lg) not in days_map:
                days_map[(n, lg)] = league_days(kstore, lg, *periods[n])
    unit = ledger.expected_cost(SNAPSHOT_RULE, oa.historical_cost("h2h", BOOKS))
    out: dict[str, Any] = {}
    by_date: dict[str, dict[date, int]] = {}
    for name, (start, end) in periods.items():
        snaps, dates = [], {}
        for league in _period_leagues(name, leagues):
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
    unlisted = ["%s:%s" % (n, lg) for n in periods for lg in _period_leagues(n, leagues)
                if not kstore.listed_any(LEAGUES[lg][1], *periods[n])]
    if unlisted:
        notes.append("Kalshi markets not listed for %s: run `kalshi-markets --period all` first, or this plan is "
                     "short" % ", ".join(unlisted))
    out.update({"cap": cap, "spent": spent, "month_remaining": month, "budget": budget, "unit_cost": unit,
                "reserved_for_test": reserved, "left_for_dev": budget - reserved,
                "total_cost": total, "fits": fits, "unlisted": unlisted,
                "note": "; ".join(notes) or "everything fits"})
    return out


def buy(client: oa.OddsApiClient, ledger: CreditLedger, lstore: LinesStore, league: str, when: datetime, kind: str,
        day: date, cap: int, clock: Callable[[], datetime]) -> dict | None:
    """One snapshot of a league at `when` (stored if new; never bought again, also when only the ledger holds its
    answer). None for a time not yet past."""
    at = _iso(when)
    have = lstore.snapshot(league, at)
    if have is not None:
        return have
    if when > clock():
        return None
    sport = LEAGUES[league][0]

    def keep(body):
        if isinstance(body, dict) and "timestamp" in body:
            lstore.put_snapshot(league, at, kind, day, body)

    body = ledger.stored_answer(_request_key(*_snapshot_request(sport, at)))
    if body is None:
        body = paid_snapshot(client, ledger, sport, at, cap, keep=keep)
    if not isinstance(body, dict) or "timestamp" not in body:
        raise oa.OddsApiError("historical odds answer without a timestamp for %s at %s" % (league, at))
    lstore.put_snapshot(league, at, kind, day, body)
    return lstore.snapshot(league, at)


def fetch_odds(period: str, client: oa.OddsApiClient, ledger: CreditLedger, lstore: LinesStore,
               kstore: kh.HistoryStore, cap: int, clock: Callable[[], datetime], leagues: Sequence[str] = tuple(LEAGUES),
               periods: dict | None = None, progress: Callable[[str], Any] = lambda s: None) -> dict:
    """Buy the plan of one period, day by day from its start, holding the ledger's lock. It begins with the free
    sports call. Development days are re-planned before each day and the run stops at the first dropped day; a cap
    refusal stops it too, and so do a ledger stop and a failed call (`stopped_by` says which)."""
    periods = periods or PERIODS
    if period == "test" and lstore.choice() is None:
        raise Refused("test-period snapshots are refused until a choice is stored (`choose`)")
    with ledger.lock():
        if period == "dev" and "test" in periods:
            missing = [lg for lg in LEAGUES if not kstore.listed_any(LEAGUES[lg][1], *periods["test"])]
            if missing:
                raise Refused("the test period's Kalshi markets are not listed for %s, so its credits cannot be "
                              "reserved: run `kalshi-markets --period all` first" % ", ".join(missing))
        days_map = {(n, lg): league_days(kstore, lg, *periods[n]) for n in periods
                    for lg in set(_period_leagues(n, leagues)) | set(leagues)}
        days = sorted({d for league in leagues for d in days_map[(period, league)]})
        out: dict[str, Any] = {"period": period, "days": len(days), "bought": 0, "already_stored": 0, "not_yet": 0,
                               "stopped": None, "stopped_by": None}
        try:
            out["usage"] = refresh_usage(client, ledger)
        except (LedgerStop, oa.OddsApiError, CreditCapError) as exc:
            out.update(stopped="the free sports call: %s" % exc, stopped_by="ledger" if isinstance(exc, LedgerStop)
                       else "error", spent_before=ledger.spent("paid"), spent_after=ledger.spent("paid"))
            return out
        out["spent_before"] = ledger.spent("paid")
        for day in days:
            if period == "dev":
                plan = make_plan(lstore, kstore, ledger, cap, leagues, periods, days_map)
                if day.isoformat() in plan["dev"]["dropped_dates"]:
                    out.update(stopped="day %s and later dropped by the plan: %s" % (day, plan["note"]),
                               stopped_by="plan")
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
                out.update(stopped=str(exc), stopped_by="cap")
                break
            except LedgerStop as exc:
                out.update(stopped=str(exc), stopped_by="ledger")
                break
            except oa.OddsApiError as exc:
                out.update(stopped="a call failed after its attempts: %s" % exc, stopped_by="error")
                break
            progress("%s: %s done, %d bought so far" % (period, day, out["bought"]))
        out["spent_after"] = ledger.spent("paid")
    return out


# ---------------------------------------------------------------- team names

_TOKEN_MAP = {"state": "st", "saint": "st", "university": "u", "univ": "u", "mt": "mount", "ft": "fort", "and": "",
              "the": "", "of": ""}
QUALIFIERS = frozenset({
    "st", "tech", "am", "at", "atlantic", "valley", "city", "international", "intl", "college", "u", "central",
    "eastern", "western", "northern", "southern", "north", "south", "east", "west", "northeast", "northwest",
    "southeast", "southwest", "northeastern", "northwestern", "southeastern", "southwestern", "upstate", "gulf",
    "coast", "christian", "baptist", "poly", "wesleyan", "mount", "fort", "pacific", "commonwealth", "military",
    "methodist", "lutheran"})                  # bracketed parts ("(OH)") are compared on their own


def _tokens(text: str) -> list[str]:
    s = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().lower()
    s = s.replace("&", "").replace("'", "")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return [t for t in (_TOKEN_MAP.get(t, t) for t in s.split()) if t]


def _parts(name: str) -> tuple[list[tuple[str, bool]], list[str]]:
    """The tokens outside brackets, each with a flag for a qualifying word (one of QUALIFIERS, or the first word after
    a hyphen: "Texas A&M-Corpus Christi"), and the bracketed tokens, sorted: "Miami (OH) RedHawks" ->
    ([("miami", False), ("redhawks", False)], ["oh"])."""
    inner = " ".join(re.findall(r"\(([^)]*)\)", name or ""))
    outer = re.sub(r"\([^)]*\)", " ", name or "")
    head, *tail = re.split(r"[-–]", outer, maxsplit=1)
    out = [(t, t in QUALIFIERS) for t in _tokens(head)]
    out += [(t, i == 0 or t in QUALIFIERS) for i, t in enumerate(_tokens(tail[0]) if tail else [])]
    return out, sorted(_tokens(inner))


@lru_cache(maxsize=None)
def names_match(kalshi_name: str, odds_name: str) -> bool:
    """Code's one team-name rule. Kalshi's short name must account for the start of the Odds API's full name, word by
    word (the same word; an acronym of consecutive words such as NY; a single letter as the initial of an ordinary
    word, "Los Angeles L"), and what is left of the full name (the nickname) must hold no qualifying word. A
    qualifying word ("State", "Tech", "A&M", a compass word, "Atlantic", "Valley", "City", a word after a hyphen...)
    on Kalshi's side must appear as itself in the full name, and bracketed parts must be the same on both sides. So a
    name with a qualifying word never matches the same name without it, in either direction: "Florida" is neither
    "Florida State" nor "Florida Atlantic", "Kansas St." is not "Kansas", "Michigan" is not "Central Michigan"."""
    k, kin = _parts(kalshi_name)
    o, oin = _parts(odds_name)
    if not k or not o or kin != oin:
        return False
    words = [t for t, _ in o]
    qual = [q for _, q in o]
    used: set[int] = set()
    rest: list[str] = []
    for t, q in k:
        i = next((i for i, x in enumerate(words) if x == t and i not in used), None)
        if i is not None:
            used.add(i)
        elif q:
            return False
        else:
            rest.append(t)
    for t in rest:
        hit: set[int] | None = None
        if len(t) >= 2:
            for i in range(len(words)):
                for n in range(2, len(words) - i + 1):
                    span = set(range(i, i + n))
                    if not span & used and "".join(words[j][0] for j in range(i, i + n)) == t:
                        hit = span
                        break
                if hit:
                    break
        else:
            i = next((i for i, x in enumerate(words) if x.startswith(t) and i not in used and not qual[i]), None)
            hit = None if i is None else {i}
        if not hit:
            return False
        used |= hit
    n = len(used)
    return used == set(range(n)) and not any(qual[n:])


def _name_key(name: str) -> tuple:
    k, kin = _parts(name)
    return tuple(t for t, _ in k), tuple(kin)


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


def _in_window(k: dict, g: dict) -> bool:
    """(b): the game starts within six hours before Kalshi's expected end of the game, and not after it."""
    if not k.get("expected") or not g.get("commence"):
        return False
    try:
        end, start = _parse(k["expected"]), _parse(g["commence"])
    except (TypeError, ValueError):
        return False
    return end - PAIR_WINDOW <= start <= end


def _ways(k: dict, g: dict, ok: Callable[[str, str], bool]) -> list[tuple[dict, dict]]:
    """Every way to put the two Kalshi teams on the game's home and away team by `ok(kalshi name, full name)`."""
    teams = k.get("teams") or []
    if len(teams) != 2:
        return []
    return [(h, a) for h, a in ((teams[0], teams[1]), (teams[1], teams[0]))
            if ok(h["name"], g["home"] or "") and ok(a["name"], g["away"] or "")]


def _pair(kevent: dict, game: dict, home: dict, away: dict, source: str) -> dict:
    return {"kalshi_event": kevent["event"], "odds_event_id": game["id"], "league": kevent["league"],
            "day": kevent["day"], "home_ticker": home["ticker"], "away_ticker": away["ticker"],
            "home_team": game["home"], "away_team": game["away"], "source": source}


def pair_games(kevents: Sequence[dict], games: Sequence[dict], resolver: Callable | None = None,
               taken_odds: Iterable[str] = (), taken_kalshi: Iterable[str] = (),
               reasons: dict | None = None) -> tuple[list[dict], dict, list]:
    """Pairs: (pairs, unpaired Kalshi events counted by reason, the resolver's proposals). The three checks of the
    module text decide; `games` is all the odds data of the period (its team names form each league's name list for
    check (a)). An event or game already paired (`taken_*`) is never paired again, and still counts in check (c).
    `reasons`, when given, receives each unpaired event's reason (`ambiguous` or `no_match`)."""
    taken_o, taken_k = set(taken_odds), set(taken_kalshi)
    by_day: dict[tuple, list[dict]] = {}
    league_names: dict[str, dict[tuple, str]] = {}
    for g in games:
        by_day.setdefault((g["league"], g["day"]), []).append(g)
        for full in (g["home"], g["away"]):
            if full:
                league_names.setdefault(g["league"], {}).setdefault(_name_key(full), full)
    hits: dict[tuple, int] = {}

    def unique(league: str, kname: str) -> bool:
        if (league, kname) not in hits:
            hits[(league, kname)] = sum(names_match(kname, full) for full in league_names.get(league, {}).values())
        return hits[(league, kname)] == 1

    fits: dict[str, list[tuple[dict, list]]] = {}          # event -> [(game, ways)] passing (a) and (b)
    claims: dict[str, list[str]] = {}                      # game id -> events passing (a) and (b) for it
    near_misses: set[str] = set()                          # events whose names match a game but not uniquely
    for k in kevents:
        for g in by_day.get((k["league"], k["day"]), []):
            if not _in_window(k, g):
                continue
            if _ways(k, g, names_match):
                near_misses.add(k["event"])
            ways = _ways(k, g, lambda kn, full, lg=k["league"]: names_match(kn, full) and unique(lg, kn))
            if ways:
                fits.setdefault(k["event"], []).append((g, ways))
                claims.setdefault(g["id"], []).append(k["event"])

    def verdict(k: dict, g: dict) -> tuple[str | None, tuple | None]:
        """None and (home, away) when the pair passes (a), (b) and (c) and neither side is taken; else why not."""
        mine = [w for x, w in fits.get(k["event"], []) if x["id"] == g["id"]]
        if not mine:
            return "the team names do not match by the rule", None
        if len(fits[k["event"]]) > 1:
            return "another game that day also matches", None
        if len(mine[0]) != 1:
            return "the teams fit the game both ways", None
        if claims.get(g["id"]) != [k["event"]]:
            return "another Kalshi event also matches this game", None
        if g["id"] in taken_o or k["event"] in taken_k:
            return "already paired", None
        return None, mine[0][0]

    def check_proposal(k: dict, g: dict | None, proposal: dict) -> tuple[str | None, tuple | None]:
        if g is None or g["id"] in taken_o:
            return "unknown or already paired game", None
        if g["league"] != k["league"]:
            return "league differs", None
        if g["day"] != k["day"]:
            return "date differs (%s against %s)" % (g["day"], k["day"]), None
        if not _in_window(k, g):
            return ("start %s is not within six hours before Kalshi's expected end %s"
                    % (g["commence"], k.get("expected") or "(none)")), None
        teams = proposal.get("teams")
        knames = {t["name"]: t for t in k["teams"]}
        if not isinstance(teams, dict) or set(teams) != set(knames) or \
                sorted(str(v) for v in teams.values()) != sorted([g["home"], g["away"]]):
            return "teams do not agree with the game", None
        for kname, full in sorted(teams.items()):
            if not names_match(kname, full):
                return "team name %r does not match %r by the rule" % (kname, full), None
            if not unique(k["league"], kname):
                return "team name %r matches more than one team of the league" % kname, None
        why, hw = verdict(k, g)
        if why is None and {hw[0]["name"]: g["home"], hw[1]["name"]: g["away"]} != teams:
            return "teams do not agree with the game", None
        return why, hw

    pairs: list[dict] = []
    left: dict[str, str] = {}
    for k in kevents:
        if k["event"] in taken_k:
            continue
        cands = fits.get(k["event"], [])
        if len(cands) == 1:
            why, hw = verdict(k, cands[0][0])
            if why is None:
                pairs.append(_pair(k, cands[0][0], hw[0], hw[1], "code"))
                taken_o.add(cands[0][0]["id"])
                taken_k.add(k["event"])
                continue
        left[k["event"]] = "ambiguous" if cands or k["event"] in near_misses else "no_match"

    log: list[dict] = []
    if resolver is not None:
        source = "resolver:%s" % getattr(resolver, "source", getattr(resolver, "__name__", "resolver"))
        for k in kevents:
            if k["event"] not in left:
                continue
            near = [g for g in games if g["league"] == k["league"] and g["id"] not in taken_o
                    and abs((g["day"] - k["day"]).days) <= 1]
            if not near:
                continue
            try:
                proposal = resolver(k, near)
            except Exception as exc:                              # a broken resolver leaves the game out
                log.append({"kalshi_event": k["event"], "proposal": {"error": type(exc).__name__}, "accepted": False,
                            "reason": "resolver error", "source": source})
                continue
            if not isinstance(proposal, dict) or not proposal.get("odds_event_id"):
                log.append({"kalshi_event": k["event"], "proposal": proposal, "accepted": False,
                            "reason": "no proposal", "source": source})
                continue
            g = next((x for x in games if x["id"] == proposal.get("odds_event_id")), None)
            why, hw = check_proposal(k, g, proposal)
            log.append({"kalshi_event": k["event"], "proposal": proposal, "accepted": why is None,
                        "reason": why or "accepted", "source": source})
            if why is None:
                pairs.append(_pair(k, g, hw[0], hw[1], source))
                taken_o.add(g["id"])
                taken_k.add(k["event"])
                del left[k["event"]]
    counts: dict[str, int] = {}
    for reason in left.values():
        counts[reason] = counts.get(reason, 0) + 1
    if reasons is not None:
        reasons.update(left)
    _check_unique(pairs)
    return pairs, counts, log


def _check_unique(pairs: list[dict]) -> None:
    if len({p["odds_event_id"] for p in pairs}) != len(pairs) or len({p["kalshi_event"] for p in pairs}) != len(pairs):
        raise AssertionError("a game was paired twice")


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
                leagues: Sequence[str], clock: Callable[[], datetime], period: str | None = None) -> dict:
    """Pair each league of the period and record the run (with how many of the period's snapshots it saw)."""
    at = _iso(clock())
    out: dict[str, Any] = {}
    for league in leagues:
        have = lstore.pairs(league)
        kev = kalshi_events(kstore, league, start, end)
        games = odds_games(lstore, league, start, end)
        reasons: dict[str, str] = {}
        pairs, left, log = pair_games(kev, games, resolver, taken_odds={p["odds_event_id"] for p in have},
                                      taken_kalshi={p["kalshi_event"] for p in have}, reasons=reasons)
        for p in pairs:
            lstore.put_pair(p, at)
        for rec in log:
            lstore.log_proposal(rec, at)
        days = {k["event"]: k["day"] for k in kev}
        for event, reason in sorted(reasons.items()):
            lstore.put_unpaired(event, league, days[event], reason, at)
        if period is not None:
            lstore.put_pair_run(league, period, lstore.count_snapshots(league, start, end), at)
        lstore.db.commit()
        out[league] = {"kalshi_events": len(kev), "odds_games": len(games), "paired_before": len(have),
                       "paired_now": len(pairs), "left_out": left,
                       "by_resolver": sum(p["source"] != "code" for p in pairs),
                       "proposals_refused": sum(not r["accepted"] for r in log)}
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
    """One paired game at its moment. Start times (and so the wave) come from the day's 16:00 UTC snapshot; the
    books' prices only from the wave's own snapshot; Kalshi's quotes only from candles at or before its time."""
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
    if not wave.get("timestamp") or _parse(wave["timestamp"]) < wave_at - STALE:
        g["reason"] = "stale_snapshot"                     # clarification 5
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

def _moment_window(moment: str) -> tuple[int, int]:
    end = int(_parse(moment).timestamp()) // 60 * 60
    return end - QUOTE_HOUR, end


def candle_moments(lstore: LinesStore, kstore: kh.HistoryStore, start: date, end: date,
                   leagues: Sequence[str]) -> dict[str, list[float]]:
    """For each contract of each paired game with a usable wave snapshot: the moment its quote is needed."""
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
    keys = ("windows_fetched", "windows_cached", "windows_empty", "candles_stored")
    moments = candle_moments(lstore, kstore, start, end, leagues)
    total = dict({"markets": len(moments)}, **{k: 0 for k in keys})
    if not moments:
        return total
    cutoff = kh.cutoff_ts(client, kstore)
    rows = {m["ticker"]: m for m in kstore.markets()}
    for ticker, ms in sorted(moments.items()):
        got = kh.fetch_candles(client, kstore, rows[ticker], ms, cutoff)
        for k in keys:
            total[k] += got.get(k, 0)
    return total


# ---------------------------------------------------------------- the test period's guards

def test_readiness(lstore: LinesStore, kstore: kh.HistoryStore, ledger: CreditLedger, periods: dict) -> list[str]:
    """What is missing before the test period may be scored (empty when nothing is): every planned test snapshot of
    the five leagues stored, pairing run on all of them, and candles stored for both contracts of every paired game
    with a moment (a window that came back empty counts only once asked twice)."""
    start, end = periods["test"]
    plan = make_plan(lstore, kstore, ledger, 10 ** 9, tuple(LEAGUES), {"test": periods["test"]})
    out = []
    if plan["unlisted"]:
        out.append("Kalshi markets are not listed for %s (`kalshi-markets --period test`)" % ", ".join(plan["unlisted"]))
    need = [s for s in plan["test"]["snapshots"] if s["cost"] > 0]
    if need:
        out.append("%d planned test snapshots are not stored, the first %s %s %s (`fetch-odds --period test`)"
                   % (len(need), need[0]["league"], need[0]["day"], need[0]["at"] or "(a wave, time not yet known)"))
    if out:
        return out
    stale = [lg for lg in LEAGUES if (run := lstore.pair_run(lg, "test")) is None
             or run["snapshots_seen"] != lstore.count_snapshots(lg, start, end)]
    if stale:
        return ["pairing has not run on every stored test snapshot of %s (`pair --period test`)" % ", ".join(stale)]
    games, _ = collect_games(lstore, kstore, start, end, tuple(LEAGUES))
    pairs = {p["kalshi_event"]: p for p in lstore.pairs()}
    lacking = []
    for g in games:
        if g["moment"] is None:
            continue
        s, e = _moment_window(g["moment"])
        p = pairs[g["kalshi_event"]]
        lacking += ["%s at %s" % (t, g["moment"]) for t in (p["home_ticker"], p["away_ticker"])
                    if not kstore.covered(t, s, e)]
    if lacking:
        out.append("candles are not stored for %d contracts of paired games (`kalshi-candles --period test`): %s%s"
                   % (len(lacking), ", ".join(lacking[:10]), " ..." if len(lacking) > 10 else ""))
    return out


# ---------------------------------------------------------------- the commands

def _stores(root: Path, clock) -> tuple[LinesStore, kh.HistoryStore, CreditLedger]:
    return (LinesStore(root / "lines.sqlite"), kh.HistoryStore(root / "kalshi.sqlite"),
            CreditLedger(root / "credits.sqlite", clock=clock))


def _period_dates(name: str, periods: dict) -> list[tuple[str, date, date]]:
    names = list(periods) if name == "all" else [name]
    return [(n, *periods[n]) for n in names]


def _evaluate(name: str, root: Path, lstore: LinesStore, kstore: kh.HistoryStore, ledger: CreditLedger,
              periods: dict, leagues, clock) -> dict:
    start, end = periods[name]
    if name == "test":
        if any(e.get("kind") == "test_result" for e in scoring_entries(root)):
            raise Refused("%s already holds a test result: the test period is scored once, whatever the stores say"
                          % (root / SCORING_LOG))
        choice = lstore.choice()
        if choice is None:
            raise Refused("the test period is scored only with a stored choice (`choose` first)")
        if lstore.result("test") is not None:
            raise Refused("the test period has already been scored once; its stored result stands")
        missing = test_readiness(lstore, kstore, ledger, periods)
        if missing:
            raise Refused("the test period is not ready to be scored: " + "; ".join(missing))
        margins, versions, leagues = (choice["margin"],), (choice["version"],), tuple(LEAGUES)
    else:
        choice, margins, versions = None, MARGINS, VERSIONS
    games, pairing = collect_games(lstore, kstore, start, end, leagues)
    out = {"period": name, "start": start.isoformat(), "end": end.isoformat(), "choice": choice,
           "pairing": pairing, "seed": SEED, "reps": REPS,
           "results": evaluate_games(games, margins, versions, SEED, REPS)}
    if name == "test":
        result = json.loads(json.dumps(out, default=str))
        append_scoring(root, "test_result", {"result": result}, clock)
        lstore.put_result("test", result, _iso(clock()))
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
    p = with_leagues(sub.add_parser("plan", help="what the rule would buy, no paid call; the test period is always "
                                                 "reserved for all five leagues"))
    p.add_argument("--period", choices=["dev", "test", "all"], default="all",
                   help="whose snapshots are listed (both periods are always planned)")
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
    p = with_leagues(sub.add_parser("evaluate", help="the two questions (dev: every choice; test: once, the choice, "
                                                     "all five leagues)"))
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
    rc = 0
    out: Any
    if args.cmd == "kalshi-markets":
        client = kalshi_client or kh.HistoryClient()
        cutoff = kh.cutoff_ts(client, kstore)
        out = {}
        for name, start, end in _period_dates(args.period, periods):
            out[name] = {lg: kh.fetch_settled(client, kstore, LEAGUES[lg][1], start, end, cutoff) for lg in leagues}
        out["requests"] = client.requests
    elif args.cmd == "plan":
        out = make_plan(lstore, kstore, ledger, args.cap, leagues, periods)
        for n in periods:
            if args.period not in ("all", n):
                out[n]["snapshots_not_listed"] = len(out[n]["snapshots"])
                out[n]["snapshots"] = []
    elif args.cmd == "fetch-odds":
        if args.period == "test" and lstore.choice() is None:
            raise Refused("test-period snapshots are refused until a choice is stored (`choose`)")
        client = odds_client or oa.OddsApiClient()
        out = fetch_odds(args.period, client, ledger, lstore, kstore, args.cap, clock, leagues, periods,
                         progress=lambda s: print(s, file=sys.stderr, flush=True))
        rc = 3 if out["stopped_by"] in ("ledger", "error") else 0
    elif args.cmd == "pair":
        start, end = periods[args.period]
        res = None if args.no_resolver else (resolver or LocalModelResolver())
        out = run_pairing(lstore, kstore, start, end, res, leagues, clock, period=args.period)
    elif args.cmd == "kalshi-candles":
        start, end = periods[args.period]
        out = fetch_kalshi_candles(kalshi_client or kh.HistoryClient(), lstore, kstore, start, end, leagues)
    elif args.cmd == "evaluate":
        out = _evaluate(args.period, root, lstore, kstore, ledger, periods, leagues, clock)
    elif args.cmd == "choose":
        if not any(abs(args.margin - m) < 1e-9 for m in MARGINS):
            raise ValueError("the margin must be one of %s" % (MARGINS,))
        if any(e.get("kind") == "choice" for e in scoring_entries(root)):
            raise Refused("%s already holds a choice: it is made once, whatever the stores say" % (root / SCORING_LOG))
        lstore.put_choice(float(args.margin), args.version, _iso(clock()))
        append_scoring(root, "choice", {"choice": lstore.choice()}, clock)
        out = {"choice": lstore.choice()}
    else:
        out = lstore.accepted_proposals()
    print(json.dumps(out, indent=1, default=str))
    return rc


def _cli(argv=None, **kwargs) -> int:
    try:
        return main(argv, **kwargs)
    except LedgerBusy as exc:
        print("stopped: %s" % exc, file=sys.stderr)
        return 2
    except (Refused, CreditCapError, ValueError) as exc:
        print("refused: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
