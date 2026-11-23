"""Kalshi's public history of single-game markets, read only and without a key, stored once in sqlite.

What is fetched (for the sports-lines prediction, docs/research/2026-11-13-sports-lines-prediction.md):

- every settled market of the five game series (`SERIES`), with its result, for the games of a period. Kalshi keeps
  markets settled before its "historical cutoff" (GET /historical/cutoff, `market_settled_ts`) under /historical and
  later ones under the live paths, so a period wholly before the cutoff is read from /historical/markets, one wholly
  after it from /markets?status=settled, and one that straddles it from both;
- one-minute candles with the yes bid and ask, only around the moments the evaluation uses: for each moment, the
  minute that ends at or before it and the hour before (`windows`). A market settled before the cutoff is asked on
  the historical path, a later one on the live path, and a 404 there is tried on the other tier (the cutoff moves).

Transport and pacing are reused from `kalshi_data` (`urllib_get`, `HttpError`, `Pacer`): plain GETs, nothing else
can be sent. At most 5 requests a second; a 429, a server error or a network error is retried after a wait
(Retry-After if sent, else 1, 2, 4 s), at most 4 tries in all. `max_requests` caps a run.

The store never overwrites a fetched fact: the same thing fetched again and found different is written to
`differences`, and the first copy is kept. A listing already fetched is not asked again; a candle window that
brought candles is not asked again, and one that came back empty is recorded as empty and asked once more on a
later run (then no more).
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from valedia_trader.venues import kalshi_data as kd

__all__ = ["SERIES", "HistoryClient", "HistoryStore", "KalshiHistoryError", "event_date", "team_code", "iso_ts",
           "cutoff_ts", "fetch_settled", "fetch_candles", "windows"]

UTC = timezone.utc
SERIES = {"NFL": "KXNFLGAME", "NBA": "KXNBAGAME", "NHL": "KXNHLGAME", "NCAAF": "KXNCAAFGAME",
          "NCAAMB": "KXNCAAMBGAME"}
MONTHS = {m: i + 1 for i, m in enumerate(("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT",
                                          "NOV", "DEC"))}
_DATE = re.compile(r"^[A-Z0-9]+-(\d{2})([A-Z]{3})(\d{2})")
SETTLE_DAYS = 3            # a game's markets settle within this many days of its date (close_time placeholder: 2 d)


class KalshiHistoryError(RuntimeError):
    """Retries ran out, the request budget is spent, or an answer was not the expected shape."""


def event_date(ticker: str) -> date | None:
    """The game date written in an event or market ticker (KXNBAGAME-26NOV12TORPHI -> 2026-11-12). Kalshi writes the
    local (US-Eastern) date of the game, not a UTC date."""
    m = _DATE.match(ticker or "")
    if not m or m.group(2) not in MONTHS:
        return None
    try:
        return date(2000 + int(m.group(1)), MONTHS[m.group(2)], int(m.group(3)))
    except ValueError:
        return None


def team_code(market_ticker: str) -> str:
    """The code of the team a market pays on (the last dash-separated part of its ticker)."""
    return market_ticker.rsplit("-", 1)[-1]


def iso_ts(text: str) -> float:
    """A UTC ISO time ("2026-09-13T00:00:00Z") in epoch seconds."""
    return datetime.fromisoformat(str(text).replace("Z", "+00:00")).timestamp()


class HistoryClient:
    """Keyless GET client: `fetch(url) -> bytes` (urllib by default, a fake in tests)."""

    def __init__(self, fetch: Callable[[str], bytes] = kd.urllib_get, *, base_url: str = kd.BASE_URL,
                 max_per_second: float = 5.0, tries: int = 4, sleep: Callable[[float], Any] = time.sleep,
                 clock: Callable[[], float] = time.monotonic, max_requests: int | None = None,
                 pacer: Any = None):
        self.fetch, self.base_url, self.tries, self.sleep = fetch, base_url.rstrip("/"), tries, sleep
        self.pacer = pacer if pacer is not None else kd.Pacer(1.0 / max_per_second + 0.001, sleep, clock)
        self.max_requests, self.requests = max_requests, 0

    def get_json(self, path: str, params: dict | None = None) -> dict:
        """GET base + path with the query `params`, paced and retried (see the module text)."""
        import urllib.parse

        url = self.base_url + path + ("?" + urllib.parse.urlencode(params) if params else "")
        last = "no attempt"
        retry_after = None
        for attempt in range(self.tries):
            if attempt:
                self.sleep(min(retry_after if retry_after else float(2 ** (attempt - 1)), 60.0))
            if self.max_requests is not None and self.requests >= self.max_requests:
                raise KalshiHistoryError("request budget of %d spent before %s" % (self.max_requests, path))
            self.pacer.wait()
            self.requests += 1
            retry_after = None
            try:
                body = self.fetch(url)
            except kd.HttpError as e:
                if e.status == 429 or e.status >= 500:
                    last, retry_after = "HTTP %s" % e.status, e.retry_after
                    continue
                raise
            except OSError as e:                       # URLError, timeouts, refused or reset connections
                last = "network error (%s)" % type(e).__name__
                continue
            try:
                return json.loads(body)
            except ValueError as e:
                raise KalshiHistoryError("not JSON from %s: %s" % (path, e)) from None
        raise KalshiHistoryError("gave up after %d tries: %s at %s" % (self.tries, last, path))

    def pages(self, path: str, params: dict, key: str) -> list[dict]:
        out, cursor, seen = [], None, set()
        while True:
            page = self.get_json(path, dict(params, cursor=cursor) if cursor else dict(params))
            if key not in page:
                raise KalshiHistoryError("no %r in the answer from %s (keys %s)" % (key, path, sorted(page)))
            out += page[key] or []
            cursor = page.get("cursor")
            if not cursor or not page[key] or cursor in seen:
                return out
            seen.add(cursor)


def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _now() -> str:
    return datetime.now(UTC).isoformat()


class HistoryStore:
    """sqlite store of markets, candles, the windows and listings already fetched, cutoffs and differences."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS markets (ticker TEXT PRIMARY KEY, series TEXT, event_ticker TEXT, tier TEXT,
                raw TEXT NOT NULL, fetched_at TEXT);
            CREATE TABLE IF NOT EXISTS candles (ticker TEXT, end_ts INTEGER, tier TEXT, raw TEXT NOT NULL,
                fetched_at TEXT, PRIMARY KEY (ticker, end_ts));
            CREATE TABLE IF NOT EXISTS windows (ticker TEXT, start_ts INTEGER, end_ts INTEGER, tier TEXT, n INTEGER,
                fetched_at TEXT, PRIMARY KEY (ticker, start_ts, end_ts));
            CREATE TABLE IF NOT EXISTS candle_asks (id INTEGER PRIMARY KEY AUTOINCREMENT, ticker TEXT,
                start_ts INTEGER, end_ts INTEGER, tier TEXT, n INTEGER, fetched_at TEXT);
            CREATE TABLE IF NOT EXISTS listings (series TEXT, tier TEXT, start TEXT, end TEXT, n INTEGER,
                fetched_at TEXT, PRIMARY KEY (series, tier, start, end));
            CREATE TABLE IF NOT EXISTS cutoffs (fetched_at TEXT, raw TEXT);
            CREATE TABLE IF NOT EXISTS differences (id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, key TEXT,
                old TEXT, new TEXT, fetched_at TEXT);
        """)
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def _put(self, kind: str, key: str, select: str, args: tuple, insert: str, values: tuple, raw: str) -> str:
        row = self.db.execute(select, args).fetchone()
        if row is None:
            self.db.execute(insert, values)
            self.db.commit()
            return "new"
        if row["raw"] == raw:
            return "same"
        self.db.execute("INSERT INTO differences (kind, key, old, new, fetched_at) VALUES (?, ?, ?, ?, ?)",
                        (kind, key, row["raw"], raw, _now()))
        self.db.commit()
        return "different"

    def put_market(self, row: dict, tier: str, series: str) -> str:
        raw = _canon(row)
        return self._put("market", row["ticker"], "SELECT raw FROM markets WHERE ticker = ?", (row["ticker"],),
                         "INSERT INTO markets VALUES (?, ?, ?, ?, ?, ?)",
                         (row["ticker"], series, row.get("event_ticker"), tier, raw, _now()), raw)

    def put_candle(self, ticker: str, row: dict, tier: str) -> str:
        raw = _canon(row)
        end = int(row["end_period_ts"])
        return self._put("candle", "%s@%d" % (ticker, end), "SELECT raw FROM candles WHERE ticker = ? AND end_ts = ?",
                         (ticker, end), "INSERT INTO candles VALUES (?, ?, ?, ?, ?)", (ticker, end, tier, raw, _now()),
                         raw)

    def markets(self, series: str | None = None) -> list[dict]:
        """Stored markets as Kalshi sent them, each with `tier` and `series` added."""
        q = "SELECT * FROM markets" + (" WHERE series = ?" if series else "") + " ORDER BY ticker"
        out = []
        for r in self.db.execute(q, (series,) if series else ()):
            out.append(dict(json.loads(r["raw"]), tier=r["tier"], series=r["series"]))
        return out

    def candles(self, ticker: str) -> list[dict]:
        """Stored candles of one market in the one shape of `kalshi_data.normalize_candle`, oldest first."""
        rows = self.db.execute("SELECT raw FROM candles WHERE ticker = ? ORDER BY end_ts", (ticker,))
        return [kd.normalize_candle(json.loads(r["raw"])) for r in rows]

    def window_state(self, ticker: str, start: int, end: int) -> str:
        """What is known of the candles of one market in [start, end]: "filled" (an answer with candles covers it),
        "empty_once" (asked once, nothing came: ask again on a later run), "empty_final" (asked twice, nothing came)
        or "missing". Rows of `windows` (the table before rev 2) are read as asks too."""
        rows = []
        for table in ("windows", "candle_asks"):
            rows += self.db.execute("SELECT n FROM %s WHERE ticker = ? AND start_ts <= ? AND end_ts >= ?" % table,
                                    (ticker, start, end)).fetchall()
        if any((r[0] or 0) > 0 for r in rows):
            return "filled"
        return {0: "missing", 1: "empty_once"}.get(len(rows), "empty_final")

    def covered(self, ticker: str, start: int, end: int) -> bool:
        """Nothing more to ask: candles came, or the window came back empty twice."""
        return self.window_state(ticker, start, end) in ("filled", "empty_final")

    def put_window(self, ticker: str, start: int, end: int, tier: str, n: int) -> None:
        """Record one ask of a window and how many candles came (0 for an empty answer)."""
        self.db.execute("INSERT INTO candle_asks (ticker, start_ts, end_ts, tier, n, fetched_at) VALUES (?, ?, ?, ?, ?, ?)",
                        (ticker, start, end, tier, n, _now()))
        self.db.commit()

    def listed(self, series: str, tier: str, start: date, end: date) -> bool:
        return self.db.execute("SELECT 1 FROM listings WHERE series = ? AND tier = ? AND start = ? AND end = ?",
                               (series, tier, start.isoformat(), end.isoformat())).fetchone() is not None

    def listed_any(self, series: str, start: date, end: date) -> bool:
        return self.db.execute("SELECT 1 FROM listings WHERE series = ? AND start = ? AND end = ?",
                               (series, start.isoformat(), end.isoformat())).fetchone() is not None

    def put_listing(self, series: str, tier: str, start: date, end: date, n: int) -> None:
        self.db.execute("INSERT OR IGNORE INTO listings VALUES (?, ?, ?, ?, ?, ?)",
                        (series, tier, start.isoformat(), end.isoformat(), n, _now()))
        self.db.commit()

    def put_cutoff(self, raw: dict) -> None:
        self.db.execute("INSERT INTO cutoffs VALUES (?, ?)", (_now(), _canon(raw)))
        self.db.commit()

    def differences(self) -> list[dict]:
        return [dict(r) for r in self.db.execute("SELECT * FROM differences ORDER BY id")]


def cutoff_ts(client: HistoryClient, store: HistoryStore | None = None) -> float:
    """Kalshi's historical cutoff for settled markets, in epoch seconds."""
    raw = client.get_json("/historical/cutoff")
    if "market_settled_ts" not in raw:
        raise KalshiHistoryError("no market_settled_ts in /historical/cutoff (keys %s)" % sorted(raw))
    if store is not None:
        store.put_cutoff(raw)
    return iso_ts(raw["market_settled_ts"])


def _day_start(d: date) -> float:
    return datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp()


def fetch_settled(client: HistoryClient, store: HistoryStore, series: str, start: date, end: date,
                  cutoff: float | None = None, refresh: bool = False) -> dict:
    """Store every settled market of `series` whose game date (from the ticker) is in [start, end]. The historical
    tier is read when the period starts before the cutoff, the live tier when its games can settle after it. A
    period already listed is not asked again (not even the cutoff) unless `refresh`."""
    if not refresh and store.listed_any(series, start, end):
        return {"series": series, "tiers": [], "listed": 0, "in_period": 0, "stored": 0, "same": 0, "different": 0,
                "cached": True}
    cutoff = cutoff_ts(client, store) if cutoff is None else cutoff
    tiers = []
    if _day_start(start) < cutoff:
        tiers.append(("historical", "/historical/markets", {}))
    if _day_start(end + timedelta(days=SETTLE_DAYS)) > cutoff:
        lo, hi = int(_day_start(start)), int(_day_start(end + timedelta(days=SETTLE_DAYS + 1)))
        tiers.append(("live", "/markets", {"status": "settled", "min_close_ts": lo, "max_close_ts": hi}))
    counts = {"series": series, "tiers": [t for t, _, _ in tiers], "listed": 0, "in_period": 0, "stored": 0,
              "same": 0, "different": 0, "cached": False}
    cached, done = 0, []
    for tier, path, extra in tiers:
        if not refresh and store.listed(series, tier, start, end):
            cached += 1
            continue
        rows = client.pages(path, {"series_ticker": series, **extra, "limit": 1000}, "markets")
        counts["listed"] += len(rows)
        n = 0
        for row in rows:
            d = event_date(row.get("ticker", ""))
            if d is None or not (start <= d <= end) or row.get("status") not in ("finalized", "settled"):
                continue
            n += 1
            got = store.put_market(row, tier, series)
            counts[{"new": "stored", "same": "same", "different": "different"}[got]] += 1
        counts["in_period"] += n
        done.append((tier, n))
    for tier, n in done:                     # only once every tier answered, so a broken run is listed again
        store.put_listing(series, tier, start, end, n)
    counts["cached"] = bool(tiers) and cached == len(tiers)
    return counts


def windows(moments: Iterable[float], before: int = 3600) -> list[tuple[int, int]]:
    """For each moment, the window from `before` seconds before its minute to the end of the minute that ends at or
    before it; overlapping windows merged, in time order."""
    spans = sorted((int(m) // 60 * 60 - before, int(m) // 60 * 60) for m in moments)
    out: list[tuple[int, int]] = []
    for s, e in spans:
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def fetch_candles(client: HistoryClient, store: HistoryStore, market: dict, moments: Iterable[float],
                  cutoff: float) -> dict:
    """One-minute candles of one market around each moment (see `windows`), each window asked once."""
    ticker = market["ticker"]
    series = market.get("series") or ticker.split("-", 1)[0]
    settled = market.get("settlement_ts") or market.get("close_time")
    first = "historical" if settled and iso_ts(settled) < cutoff else "live"
    paths = {"historical": "/historical/markets/%s/candlesticks" % ticker,
             "live": "/series/%s/markets/%s/candlesticks" % (series, ticker)}
    out = {"windows_fetched": 0, "windows_cached": 0, "windows_empty": 0, "candles_stored": 0}
    for start, end in windows(moments):
        if store.covered(ticker, start, end):
            out["windows_cached"] += 1
            continue
        params = {"start_ts": start, "end_ts": end, "period_interval": 1}
        rows, used = [], first
        for tier in (first, "live" if first == "historical" else "historical"):
            try:
                rows = client.get_json(paths[tier], params).get("candlesticks") or []
            except kd.HttpError as e:
                if e.status == 404:
                    continue
                raise
            used = tier
            if rows:
                break
        for r in rows:
            if store.put_candle(ticker, r, used) == "new":
                out["candles_stored"] += 1
        store.put_window(ticker, start, end, used, len(rows))
        out["windows_fetched"] += 1
        out["windows_empty"] += 0 if rows else 1
    return out
