"""The leaders' record (T52): what ten fixed Polymarket wallets hold, read in passes, and how it changes.

    python -m valedia_trader.research_loop.leaders_monitor fix-leaders [--store PATH]
    python -m valedia_trader.research_loop.leaders_monitor pass        [--store PATH] [--max-memory-mb MB]
    python -m valedia_trader.research_loop.leaders_monitor report      [--store PATH] [--days N]

Read only and keyless, through `venues.polymarket_data` (its allow-list, pacing, retries and row checks). Nothing
here matches another venue, calls a model, reads another venue's price or makes a paper trade (later slices), and
nothing here repeats itself: `pass` reads once and ends; whether and how often it runs is the owner's decision.

* `fix-leaders`, once: the all-time PNL boards of the sports and crypto categories, BOARD_ROWS rows each. A board
  that answers fewer good rows (or fewer distinct wallets) than asked is a refusal with nothing stored (an unknown
  category answers an empty board, not an error). The claim is an INSERT of the meta key `leaders_fixed_at` inside
  BEGIN IMMEDIATE, with the leaders in the same transaction: of two runs at once exactly one fixes, and every later
  run refuses before any request. A wallet on both boards is one leader with both ranks; the list is not filled.
  Nothing ever re-ranks, replaces or drops a leader.
* `pass`: under an exclusive lock on STORE.lock taken without waiting (a second pass exits at once, one line, code
  LOCKED), each leader's open positions are read one wallet at a time (at most POSITION_PAGE_CAP pages). A good
  read is compared with that wallet's last good read: a token not held then is NEW (`flip` when its opposite token
  was held), a size moved by more than SIZE_EPS is an INCREASE or a DECREASE, a token no longer held is an EXIT;
  each change carries the read time, the previous good read's time and the gap. A wallet's first good read is a
  baseline and creates no change. A failed read (an error after the reader's retries: `read_error`; the page cap:
  `page_limit`; a page short of the first while more were promised: `rows_shrank`) records only the failure and
  infers nothing. An error of any other kind while one wallet is read or written (not a memory error, not an
  interrupt) is that wallet's failed read `internal_error`, with the error's class name only; the pass goes on and
  ends with code INTERNAL_ERROR, its summary line counting such wallets. ONE WRITE PER WALLET: the read, its holdings, its changes, its snapshot and its rejects are one
  transaction, so a kill leaves the old holding or the new one with its changes, never one without the other.
  A full snapshot is kept at the baseline and once per UTC day. Memory is read at each wallet; above
  --max-memory-mb the pass stops between wallets (code STOPPED_MEMORY). At the end, rows of changes, snapshots,
  rejects, wallet_reads and passes dated more than RETENTION_DAYS before the pass are removed, DELETE_BATCH rows a
  statement, at most DELETE_STATEMENTS statements a pass; holdings_current, leaders and each wallet's last good
  read are never aged out.
* `report`: counts per leader and per kind over a window, the largest and median gap between good reads, the
  share of failed reads, and whether the store is consistent.

No line printed here names a title, an outcome, a token, a market or a price, nor a wallet beyond its first ten
characters: what a leader holds is read from the store by whoever needs it.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sqlite3
import statistics
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from valedia_trader.research_loop.copy_leaders import rss_mb
from valedia_trader.venues import polymarket_data as pd

DEFAULT_STORE = Path(__file__).resolve().parents[2] / "artifacts" / "leaders_monitor" / "leaders.sqlite"
BOARDS = pd.BOARD_CATEGORIES                # ("sports", "crypto")
BOARD_ROWS = 5
POSITION_PAGE_CAP = 20                      # 20,000 positions a wallet
DEFAULT_MAX_MEMORY_MB = 500
RETENTION_DAYS = 365
DELETE_BATCH = 5000
DELETE_STATEMENTS = 20
SIZE_EPS = 1e-6
RAW_MAX = 2000                              # characters of a rejected row kept in the store
BUSY_SECONDS = 30.0
DAY = 86_400
CLAIM_KEY = "leaders_fixed_at"
KINDS = ("NEW", "INCREASE", "DECREASE", "EXIT")
OK, LOCKED, REFUSED, STOPPED_MEMORY, INTERNAL_ERROR = 0, 2, 3, 5, 6

HOLDING_COLS = ("condition_id", "opposite_token_id", "outcome_index", "size", "avg_price", "current_value",
                "first_entry_at", "last_event_at", "title", "outcome", "end_date", "current_price", "event_slug")
_HOLDING_DDL = """condition_id TEXT, opposite_token_id TEXT, outcome_index INTEGER, size REAL NOT NULL, avg_price REAL,
    current_value REAL, first_entry_at INTEGER, last_event_at INTEGER, title TEXT, outcome TEXT, end_date TEXT,
    current_price REAL, event_slug TEXT"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS leaders (wallet_key TEXT PRIMARY KEY, wallet TEXT NOT NULL,
    sports_rank INTEGER, sports_pnl REAL, sports_volume REAL, sports_read_ts REAL,
    crypto_rank INTEGER, crypto_pnl REAL, crypto_volume REAL, crypto_read_ts REAL, fixed_ts REAL NOT NULL);
CREATE TABLE IF NOT EXISTS passes (pass_id INTEGER PRIMARY KEY AUTOINCREMENT, started_ts REAL NOT NULL,
    finished_ts REAL, status TEXT NOT NULL, good INTEGER, failed INTEGER, removed INTEGER);
CREATE INDEX IF NOT EXISTS passes_ts ON passes (started_ts);
CREATE TABLE IF NOT EXISTS wallet_reads (read_id INTEGER PRIMARY KEY AUTOINCREMENT, pass_id INTEGER,
    wallet_key TEXT NOT NULL, good INTEGER NOT NULL, reason TEXT, detail TEXT, rows INTEGER, pages INTEGER,
    rejects TEXT, unread TEXT, baseline INTEGER, prev_read_id INTEGER, prev_read_ts REAL, gap REAL,
    n_new INTEGER, n_increase INTEGER, n_decrease INTEGER, n_exit INTEGER, read_ts REAL NOT NULL);
CREATE INDEX IF NOT EXISTS wallet_reads_wallet ON wallet_reads (wallet_key, good, read_id);
CREATE INDEX IF NOT EXISTS wallet_reads_ts ON wallet_reads (read_ts);
CREATE TABLE IF NOT EXISTS holdings_current (wallet_key TEXT NOT NULL, token_id TEXT NOT NULL,
    read_id INTEGER NOT NULL, %(h)s, PRIMARY KEY (wallet_key, token_id));
CREATE TABLE IF NOT EXISTS snapshots (read_id INTEGER NOT NULL, wallet_key TEXT NOT NULL, token_id TEXT NOT NULL,
    kind TEXT NOT NULL, utc_day TEXT NOT NULL, read_ts REAL NOT NULL, %(h)s);
CREATE INDEX IF NOT EXISTS snapshots_day ON snapshots (wallet_key, utc_day);
CREATE INDEX IF NOT EXISTS snapshots_ts ON snapshots (read_ts);
CREATE TABLE IF NOT EXISTS changes (change_id INTEGER PRIMARY KEY AUTOINCREMENT, read_id INTEGER NOT NULL,
    wallet_key TEXT NOT NULL, token_id TEXT NOT NULL, kind TEXT NOT NULL, flip INTEGER NOT NULL,
    size_before REAL NOT NULL, size_after REAL NOT NULL, first_entry_at INTEGER, read_ts REAL NOT NULL,
    prev_read_ts REAL, gap REAL);
CREATE INDEX IF NOT EXISTS changes_ts ON changes (read_ts);
CREATE INDEX IF NOT EXISTS changes_read ON changes (read_id);
CREATE TABLE IF NOT EXISTS rejects (read_id INTEGER, wallet_key TEXT, reason TEXT, raw TEXT, read_ts REAL NOT NULL);
CREATE INDEX IF NOT EXISTS rejects_ts ON rejects (read_ts);
""" % {"h": _HOLDING_DDL}


# ---------------------------------------------------------------- small helpers
def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


def _utc_day(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")


def _int(v):
    """A whole number that fits SQLite's 64-bit integer, else None."""
    return v if isinstance(v, int) and not isinstance(v, bool) and -2 ** 63 <= v < 2 ** 63 else None


def _holding(r: dict) -> tuple:
    return (r["condition_id"], r.get("opposite_token_id"), _int(r.get("outcome_index")), float(r["current_size"]),
            float(r["avg_price"]), float(r["current_value"]), _int(r.get("first_entry_at")),
            _int(r.get("last_event_at")), r.get("title"), r.get("outcome"), r.get("end_date"), r.get("current_price"),
            r.get("event_slug"))


def _raw(row) -> str:
    try:
        return json.dumps(row, sort_keys=True, default=str)[:RAW_MAX]
    except (TypeError, ValueError, RecursionError):
        return "(a row that could not be written out: %s)" % type(row).__name__


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=BUSY_SECONDS, isolation_level=None)
    conn.executescript(SCHEMA)
    return conn


def _lock(path: Path, exclusive: bool):
    """The lock file beside the store, taken without waiting; None when another run holds it."""
    fd = os.open(str(path) + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    return fd


def _leaders(conn) -> list:
    return conn.execute("SELECT wallet_key, wallet, sports_rank, crypto_rank FROM leaders ORDER BY "
                        "min(coalesce(sports_rank, 99), coalesce(crypto_rank, 99)), wallet_key").fetchall()


# ---------------------------------------------------------------- fix-leaders
def fix_leaders(conn, client, wall: Callable[[], float], say) -> int:
    done = conn.execute("SELECT value FROM meta WHERE key=?", (CLAIM_KEY,)).fetchone()
    if done:
        say("fix-leaders refused: the leaders were already fixed at %s; nothing changes" % done[0])
        return REFUSED
    boards = {}
    for cat in BOARDS:
        try:
            w = client.leaderboard("all", "PNL", rows=BOARD_ROWS, page_cap=2, category=cat)
        except (pd.PolymarketDataError, pd.HttpError) as e:
            say("fix-leaders refused: the %s board could not be read (%s); nothing stored" % (cat, type(e).__name__))
            return REFUSED
        read_ts = wall()
        rows = w.rows[:BOARD_ROWS]
        distinct = len({r["user_id"].lower() for r in rows})
        if len(rows) < BOARD_ROWS or distinct < BOARD_ROWS:
            say("fix-leaders refused: the %s board answered %d good rows (%d distinct wallets, %d rejects) of %d asked;"
                " nothing stored" % (cat, len(rows), distinct, sum(w.rejects.values()), BOARD_ROWS))
            return REFUSED
        boards[cat] = (rows, read_ts)
    fixed_ts = wall()
    leaders: dict = {}
    for cat, (rows, read_ts) in boards.items():
        for r in rows:
            d = leaders.setdefault(r["user_id"].lower(), {"wallet": r["user_id"]})
            d[cat] = (r["rank"], r["pnl"], r["volume"], read_ts)
    values = []
    for key, d in sorted(leaders.items()):
        s, c = d.get("sports", (None,) * 4), d.get("crypto", (None,) * 4)
        values.append((key, d["wallet"], *s, *c, fixed_ts))
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("INSERT INTO meta (key, value) VALUES (?, ?)", (CLAIM_KEY, _iso(fixed_ts)))
        conn.executemany("INSERT INTO leaders (wallet_key, wallet, sports_rank, sports_pnl, sports_volume, "
                         "sports_read_ts, crypto_rank, crypto_pnl, crypto_volume, crypto_read_ts, fixed_ts) "
                         "VALUES (?,?,?,?,?,?,?,?,?,?,?)", values)
        conn.execute("COMMIT")
    except sqlite3.IntegrityError:
        conn.execute("ROLLBACK")
        say("fix-leaders refused: another run fixed the leaders first; nothing changes")
        return REFUSED
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    both = sum(1 for d in leaders.values() if "sports" in d and "crypto" in d)
    say("leaders fixed at %s: %d wallets (sports %d, crypto %d, on both %d); nothing re-ranks them"
        % (_iso(fixed_ts), len(leaders), BOARD_ROWS, BOARD_ROWS, both))
    for key, wallet, srank, crank in _leaders(conn):
        say("  %s sports#%s crypto#%s" % (wallet[:10], srank or "-", crank or "-"))
    return OK


# ---------------------------------------------------------------- one wallet
def read_wallet(client, wallet: str) -> dict:
    """One wallet's open positions, at most POSITION_PAGE_CAP pages: {good, reason, detail, rows, pages, rejects
    (Counter), bad [(reason, raw)], unread (Counter)}. Identities in conflict are dropped and counted."""
    ids: dict = {}
    conflicted: set = set()
    rows, bad, sizes = [], [], []
    rejects: Counter = Counter()
    try:
        for w in client.position_pages(wallet, page_cap=POSITION_PAGE_CAP, ids=ids, conflicted=conflicted):
            sizes.append(len(w.raw))
            bad.extend((why, raw) for raw, why in w.raw if why is not None)
            rows.extend(w.rows)
            rejects.update(w.rejects)
    except pd.PageCapHit as e:
        return {"good": False, "reason": "page_limit", "detail": str(e)[:300], "pages": e.pages}
    except (pd.PolymarketDataError, pd.HttpError) as e:
        return {"good": False, "reason": "read_error", "detail": ("%s: %s" % (type(e).__name__, e))[:300],
                "pages": len(sizes)}
    if any(n < sizes[0] for n in sizes[1:-1]):
        return {"good": False, "reason": "rows_shrank", "detail": "page sizes %s" % sizes[:POSITION_PAGE_CAP],
                "pages": len(sizes)}
    if conflicted:
        kept = []
        for r in rows:
            if pd.position_identity(r) in conflicted:
                bad.append(("conflicting_row", r))
                rejects["conflicting_row"] += 1
            else:
                kept.append(r)
        rows = kept
    unread = Counter(k for r in rows for k in r["unread"])
    return {"good": True, "rows": rows, "pages": len(sizes), "rejects": rejects, "bad": bad, "unread": unread}


def _diff(prev: dict, new: dict) -> list:
    """(token, kind, flip, size_before, size_after, row) for every change from `prev` {token: size} to `new`."""
    out = []
    for tok, r in new.items():
        size = float(r["current_size"])
        if tok not in prev:
            out.append((tok, "NEW", int(r.get("opposite_token_id") in prev), 0.0, size, r))
        elif size - prev[tok] > SIZE_EPS:
            out.append((tok, "INCREASE", 0, prev[tok], size, r))
        elif prev[tok] - size > SIZE_EPS:
            out.append((tok, "DECREASE", 0, prev[tok], size, r))
    out.extend((tok, "EXIT", 0, size, 0.0, None) for tok, size in prev.items() if tok not in new)
    return sorted(out, key=lambda c: (KINDS.index(c[1]), c[0]))


def write_wallet(conn, pass_id: int, key: str, res: dict, read_ts: float) -> dict:
    """ONE transaction for a wallet's read: a failed read alone, or a good read with its holdings, changes,
    snapshot and rejects. Returns {kind: count} and whether it was a baseline."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        if not res["good"]:
            conn.execute("INSERT INTO wallet_reads (pass_id, wallet_key, good, reason, detail, pages, read_ts) "
                         "VALUES (?, ?, 0, ?, ?, ?, ?)", (pass_id, key, res["reason"], res["detail"], res["pages"],
                                                          read_ts))
            conn.execute("COMMIT")
            return {"baseline": False}
        last = conn.execute("SELECT read_id, read_ts FROM wallet_reads WHERE wallet_key=? AND good=1 "
                            "ORDER BY read_id DESC LIMIT 1", (key,)).fetchone()
        prev = dict(conn.execute("SELECT token_id, size FROM holdings_current WHERE wallet_key=?", (key,)))
        new = {r["token_id"]: r for r in res["rows"]}
        baseline = last is None
        found = [] if baseline else _diff(prev, new)
        prev_id, prev_ts = (None, None) if baseline else last
        gap = None if baseline else read_ts - prev_ts
        n = Counter(c[1] for c in found)
        cur = conn.execute(
            "INSERT INTO wallet_reads (pass_id, wallet_key, good, rows, pages, rejects, unread, baseline, prev_read_id,"
            " prev_read_ts, gap, n_new, n_increase, n_decrease, n_exit, read_ts) "
            "VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (pass_id, key, len(new), res["pages"], json.dumps(dict(res["rejects"]), sort_keys=True),
             json.dumps(dict(res["unread"]), sort_keys=True), int(baseline), prev_id, prev_ts, gap,
             n["NEW"], n["INCREASE"], n["DECREASE"], n["EXIT"], read_ts))
        read_id = cur.lastrowid
        cols = ", ".join(HOLDING_COLS)
        marks = ", ".join("?" * len(HOLDING_COLS))
        conn.execute("DELETE FROM holdings_current WHERE wallet_key=?", (key,))
        conn.executemany("INSERT INTO holdings_current (wallet_key, token_id, read_id, %s) VALUES (?, ?, ?, %s)"
                         % (cols, marks), [(key, tok, read_id, *_holding(r)) for tok, r in new.items()])
        conn.executemany("INSERT INTO changes (read_id, wallet_key, token_id, kind, flip, size_before, size_after,"
                         " first_entry_at, read_ts, prev_read_ts, gap) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         [(read_id, key, tok, kind, flip, before, after,
                           _int(r.get("first_entry_at")) if r else None, read_ts, prev_ts, gap)
                          for tok, kind, flip, before, after, r in found])
        day = _utc_day(read_ts)
        have = conn.execute("SELECT 1 FROM snapshots WHERE wallet_key=? AND utc_day=? LIMIT 1", (key, day)).fetchone()
        if baseline or not have:
            conn.executemany("INSERT INTO snapshots (read_id, wallet_key, token_id, kind, utc_day, read_ts, %s) "
                             "VALUES (?, ?, ?, ?, ?, ?, %s)" % (cols, marks),
                             [(read_id, key, tok, "baseline" if baseline else "daily", day, read_ts, *_holding(r))
                              for tok, r in new.items()])
        conn.executemany("INSERT INTO rejects (read_id, wallet_key, reason, raw, read_ts) VALUES (?, ?, ?, ?, ?)",
                         [(read_id, key, why, _raw(raw), read_ts) for why, raw in res["bad"]])
        conn.execute("COMMIT")
        return {"baseline": baseline, **n}
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


# ---------------------------------------------------------------- retention
def age_out(conn, now: float) -> int:
    """Rows dated more than RETENTION_DAYS before `now`, at most DELETE_BATCH a statement and DELETE_STATEMENTS a
    call, in this order; a wallet's last good read (and its changes) are kept whatever their age."""
    cutoff = now - RETENTION_DAYS * DAY
    keep = " AND read_id NOT IN (SELECT max(read_id) FROM wallet_reads WHERE good=1 GROUP BY wallet_key)"
    plan = (("changes", "read_ts", keep), ("snapshots", "read_ts", ""), ("rejects", "read_ts", ""),
            ("wallet_reads", "read_ts", keep), ("passes", "started_ts", ""))
    removed, left = 0, DELETE_STATEMENTS
    for table, col, extra in plan:
        while left > 0:
            cur = conn.execute("DELETE FROM %s WHERE rowid IN (SELECT rowid FROM %s WHERE %s < ?%s LIMIT %d)"
                               % (table, table, col, extra, DELETE_BATCH), (cutoff,))
            left -= 1
            removed += cur.rowcount
            if cur.rowcount < DELETE_BATCH:
                break
    return removed


# ---------------------------------------------------------------- pass
def run_pass(conn, client, wall, say, memory: Callable[[], float], max_mb: float) -> int:
    leaders = _leaders(conn)
    if not leaders:
        say("pass refused: no leaders are fixed yet; run fix-leaders first")
        return REFUSED
    started, t0 = wall(), time.monotonic()
    pass_id = conn.execute("INSERT INTO passes (started_ts, status) VALUES (?, 'running')", (started,)).lastrowid
    totals: Counter = Counter()
    good = failed = internal = 0
    status = "done"
    mem = memory()
    for i, (key, wallet, _s, _c) in enumerate(leaders):
        if mem > max_mb:
            say("pass %d stopped: memory %.0f MB is above --max-memory-mb %.0f before wallet %s; %d wallets not read"
                % (pass_id, mem, max_mb, wallet[:10], len(leaders) - i))
            status = "stopped_memory"
            break
        t = time.monotonic()
        try:
            res = read_wallet(client, wallet)
            n = write_wallet(conn, pass_id, key, res, wall())
        except MemoryError:
            raise
        except Exception as e:                  # this wallet's failed read; the class name only, never a row
            res = {"good": False, "reason": "internal_error", "detail": type(e).__name__, "pages": None}
            n = write_wallet(conn, pass_id, key, res, wall())
            internal += 1
        mem = memory()
        if res["good"]:
            good += 1
            totals.update({k: n.get(k, 0) for k in KINDS})
            say("wallet %s good rows=%d rejects=%d new=%d increase=%d decrease=%d exit=%d%s %.1fs mem=%.0fMB"
                % (wallet[:10], len(res["rows"]), sum(res["rejects"].values()), n.get("NEW", 0),
                   n.get("INCREASE", 0), n.get("DECREASE", 0), n.get("EXIT", 0),
                   " baseline" if n["baseline"] else "", time.monotonic() - t, mem))
        else:
            failed += 1
            say("wallet %s failed %s pages=%d %.1fs mem=%.0fMB" % (wallet[:10], res["reason"], res["pages"] or 0,
                                                                    time.monotonic() - t, mem))
    removed = age_out(conn, started)
    conn.execute("UPDATE passes SET finished_ts=?, status=?, good=?, failed=?, removed=? WHERE pass_id=?",
                 (wall(), status, good, failed, removed, pass_id))
    say("pass %d %s: %d good, %d failed, internal_error=%d, new=%d increase=%d decrease=%d exit=%d, removed %d old "
        "rows, %.1fs, mem=%.0fMB" % (pass_id, status, good, failed, internal, totals["NEW"], totals["INCREASE"],
                                     totals["DECREASE"], totals["EXIT"], removed, time.monotonic() - t0, mem))
    if status == "stopped_memory":
        return STOPPED_MEMORY
    return INTERNAL_ERROR if internal else OK


# ---------------------------------------------------------------- consistency and report
def consistency(conn) -> list:
    """Every way the store breaks the one-write rule: a wallet's holdings that are not exactly its last good read's
    rows, or that read's changes not all present. An empty list is a consistent store."""
    problems = []
    keys = [r[0] for r in conn.execute("SELECT wallet_key FROM leaders UNION SELECT wallet_key FROM holdings_current")]
    for key in keys:
        last = conn.execute("SELECT read_id, rows, n_new + n_increase + n_decrease + n_exit FROM wallet_reads "
                            "WHERE wallet_key=? AND good=1 ORDER BY read_id DESC LIMIT 1", (key,)).fetchone()
        held, reads, top = conn.execute("SELECT count(*), count(DISTINCT read_id), max(read_id) FROM "
                                        "holdings_current WHERE wallet_key=?", (key,)).fetchone()
        if last is None:
            if held:
                problems.append("%s: holdings without a good read" % key[:10])
            continue
        if held != last[1] or (held and (reads != 1 or top != last[0])):
            problems.append("%s: holdings are not exactly the last good read" % key[:10])
        if conn.execute("SELECT count(*) FROM changes WHERE read_id=?", (last[0],)).fetchone()[0] != last[2]:
            problems.append("%s: the last good read's changes are not all stored" % key[:10])
    return problems


def report(conn, wall, say, days: int) -> int:
    leaders = _leaders(conn)
    if not leaders:
        say("report refused: no leaders are fixed yet; run fix-leaders first")
        return REFUSED
    now = wall()
    since = now - days * DAY
    fixed = conn.execute("SELECT value FROM meta WHERE key=?", (CLAIM_KEY,)).fetchone()
    problems = consistency(conn)
    say("report over the last %d days (since %s); leaders fixed at %s; store %s"
        % (days, _iso(since), fixed[0] if fixed else "?",
           "consistent" if not problems else "INCONSISTENT (%d problems)" % len(problems)))
    for p in problems:
        say("  problem: %s" % p)
    totals: Counter = Counter()
    for key, wallet, srank, crank in leaders:
        g, f = conn.execute("SELECT coalesce(sum(good), 0), coalesce(sum(1 - good), 0) FROM wallet_reads "
                            "WHERE wallet_key=? AND read_ts>=?", (key, since)).fetchone()
        kinds = dict(conn.execute("SELECT kind, count(*) FROM changes WHERE wallet_key=? AND read_ts>=? GROUP BY kind",
                                  (key, since)))
        totals.update(kinds)
        last = conn.execute("SELECT max(read_ts) FROM wallet_reads WHERE wallet_key=? AND good=1", (key,)).fetchone()[0]
        say("%s sports#%s crypto#%s good=%d failed=%d new=%d increase=%d decrease=%d exit=%d last good read %s"
            % (wallet[:10], srank or "-", crank or "-", g, f, kinds.get("NEW", 0), kinds.get("INCREASE", 0),
               kinds.get("DECREASE", 0), kinds.get("EXIT", 0),
               "never" if last is None else "%.0fs ago" % (now - last)))
    flips = conn.execute("SELECT count(*) FROM changes WHERE flip=1 AND read_ts>=?", (since,)).fetchone()[0]
    say("changes in the window: new=%d increase=%d decrease=%d exit=%d (flip=%d)"
        % (totals["NEW"], totals["INCREASE"], totals["DECREASE"], totals["EXIT"], flips))
    gaps = [r[0] for r in conn.execute(
        "SELECT g FROM (SELECT read_ts, read_ts - lag(read_ts) OVER (PARTITION BY wallet_key ORDER BY read_id) AS g "
        "FROM wallet_reads WHERE good=1) WHERE g IS NOT NULL AND read_ts>=?", (since,))]
    if gaps:
        say("gap between good reads: largest %.0f s, median %.0f s, over %d gaps"
            % (max(gaps), statistics.median(gaps), len(gaps)))
    else:
        say("gap between good reads: none yet")
    total, nf = conn.execute("SELECT count(*), coalesce(sum(1 - good), 0) FROM wallet_reads WHERE read_ts>=?",
                             (since,)).fetchone()
    reasons = conn.execute("SELECT reason, count(*) FROM wallet_reads WHERE good=0 AND read_ts>=? GROUP BY reason "
                           "ORDER BY reason", (since,)).fetchall()
    say("failed reads %d of %d (%.1f%%)%s" % (nf, total, 100.0 * nf / total if total else 0.0,
                                               "; " + ", ".join("%s=%d" % r for r in reasons) if reasons else ""))
    return OK


# ---------------------------------------------------------------- command line
def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="leaders_monitor", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("fix-leaders", "pass", "report"):
        s = sub.add_parser(name)
        s.add_argument("--store", default=str(DEFAULT_STORE))
        if name == "pass":
            s.add_argument("--max-memory-mb", type=float, default=DEFAULT_MAX_MEMORY_MB)
        if name == "report":
            s.add_argument("--days", type=int, default=30)
    return p


def main(argv=None, *, client=None, wall: Callable[[], float] = time.time, out=None,
         memory: Callable[[], float] = rss_mb) -> int:
    args = _parser().parse_args(argv)
    out = out or sys.stdout

    def say(line: str) -> None:
        out.write(line + "\n")
        out.flush()
    store = Path(args.store)
    try:
        store.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        say("refused: the store's folder could not be made (%s); nothing done" % type(e).__name__)
        return REFUSED
    if args.cmd == "fix-leaders":
        conn = _connect(store)
        try:
            return fix_leaders(conn, client or pd.PolymarketData(), wall, say)
        finally:
            conn.close()
    try:
        fd = _lock(store, exclusive=args.cmd == "pass")
    except OSError as e:
        say("refused: the lock file %s.lock could not be opened (%s); nothing done" % (store, type(e).__name__))
        return REFUSED
    if fd is None:
        say("refused: another run holds the lock %s.lock; nothing done" % store)
        return LOCKED
    try:
        conn = _connect(store)
        try:
            if args.cmd == "pass":
                return run_pass(conn, client or pd.PolymarketData(), wall, say, memory, args.max_memory_mb)
            return report(conn, wall, say, args.days)
        finally:
            conn.close()
    finally:
        os.close(fd)


if __name__ == "__main__":
    sys.exit(main())
