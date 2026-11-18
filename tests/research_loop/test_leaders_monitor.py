"""T52: the record of what ten fixed leaders hold (addendum 1, rulings R1 to R6). Every answer here comes from a
stand-in transport: no request leaves, and any socket connection fails at once."""
import fcntl
import io
import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import textwrap
import urllib.parse
from pathlib import Path

import pytest

from valedia_trader.research_loop import leaders_monitor as lm
from valedia_trader.venues import polymarket_data as pd

WORKTREE = str(Path(__file__).resolve().parents[2])
DAY = 86_400
T0 = 1_795_000_000                    # 2026-11-19, a fixed wall clock for the tests


@pytest.fixture(autouse=True)
def no_socket_may_connect(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("a test tried to open a network connection")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


def test_no_test_can_open_a_socket():
    with pytest.raises(AssertionError, match="network"):
        socket.create_connection(("127.0.0.1", 9))
    s = socket.socket()
    try:
        with pytest.raises(AssertionError, match="network"):
            s.connect(("127.0.0.1", 9))
    finally:
        s.close()


# ------------------------------------------------------------------ a stand-in world
def wallet(i):
    return "0x%08x" % (0x1a2b3c00 + i) + "%032x" % (0xfeed0000 + i)


def token(n):
    return "7%018d" % n


def pos(w, n, size=10.0, opp=None, **kw):
    r = {"proxy_wallet": w, "token_id": token(n), "condition_id": "0xcond%04d" % n, "current_size": size,
         "avg_price": 0.123456, "entry_cost_usdc": 1.0, "total_cost_usdc": 1.0, "current_value": 1.0,
         "total_size": size, "realized_pnl": 0.0, "unrealized_pnl": 0.0, "total_pnl": 0.0, "status": "OPEN",
         "title": "SECRET-TITLE-%d" % n, "outcome": "SECRET-OUTCOME-%d" % n, "end_date": "2026-12-01",
         "current_price": 0.654321, "event_slug": "secret-event-%d" % n, "event_id": "e%d" % n,
         "opposite_token_id": opp, "outcome_index": 0, "first_entry_at": T0 - 100, "last_event_at": T0 - 50}
    r.update(kw)
    return r


def board_row(rank, user):
    return {"rank": rank, "user_id": user, "pnl": 1e6 - rank, "volume": 5e6, "user_name": "u", "verified": False}


class World:
    """Boards per category, each wallet's open positions (a list), a failure mode per wallet, a page size."""

    def __init__(self, sports=None, crypto=None, page_size=1000):
        self.boards = {"sports": [board_row(i + 1, wallet(i)) for i in range(5)] if sports is None else sports,
                       "crypto": [board_row(i + 1, wallet(i + 5)) for i in range(5)] if crypto is None else crypto}
        self.held: dict = {}
        self.fail: dict = {}
        self.page_size = page_size
        self.urls: list = []

    def __call__(self, url):
        self.urls.append(url)
        parts = urllib.parse.urlsplit(url)
        assert parts.scheme + "://" + parts.netloc == pd.BASE_URL
        q = dict(urllib.parse.parse_qsl(parts.query))
        if parts.path == "/v2/leaderboard":
            rows = self.boards.get(q.get("category"), [])[: int(q.get("limit", 100))]
            return json.dumps(page(rows)).encode()
        assert parts.path == "/v2/positions", parts.path
        w, k = q["user"], int(q.get("cursor", "p0")[1:])
        mode = self.fail.get(w)
        if mode == "http500":
            raise pd.HttpError(500, url)
        if mode == "page_limit":
            return json.dumps(page([pos(w, 900 + k)], True, "p%d" % (k + 1))).encode()
        if mode == "shrank":
            sizes = [3, 1, 0]
            rows = [pos(w, 800 + 10 * k + j) for j in range(sizes[k])]
            return json.dumps(page(rows, k < 2, "p%d" % (k + 1) if k < 2 else None)).encode()
        rows = self.held.get(w, [])
        chunk = rows[k * self.page_size:(k + 1) * self.page_size]
        more = (k + 1) * self.page_size < len(rows)
        return json.dumps(page(chunk, more, "p%d" % (k + 1) if more else None)).encode()


def page(rows, has_more=False, cursor=None):
    return {"data": rows, "pagination": {"limit": 1000, "offset": 0, "has_more": has_more, "next_cursor": cursor}}


class Wall:
    def __init__(self, t=T0):
        self.t = float(t)

    def __call__(self):
        return self.t


def make_client(world, wall):
    def sleep(s):
        wall.t += 0                                   # waits cost nothing here
    return pd.PolymarketData(world, sleep=sleep, clock=lambda: 0.0, wall=wall)


def run(argv, world, wall, store, memory=lambda: 50.0):
    out = io.StringIO()
    code = lm.main(list(argv) + ["--store", str(store)], client=make_client(world, wall), wall=wall, out=out,
                   memory=memory)
    return code, out.getvalue()


def db(store):
    c = sqlite3.connect(str(store))
    c.row_factory = sqlite3.Row
    return c


@pytest.fixture
def store(tmp_path):
    return tmp_path / "lm" / "leaders.sqlite"


def fixed(store, world=None, wall=None):
    world = world or World()
    wall = wall or Wall()
    code, out = run(["fix-leaders"], world, wall, store)
    assert code == 0, out
    return world, wall


# ------------------------------------------------------------------ R4: the leaders
def test_the_default_store_is_under_artifacts_leaders_monitor():
    assert lm.DEFAULT_STORE.parts[-3:] == ("artifacts", "leaders_monitor", "leaders.sqlite")
    assert (lm.BOARD_ROWS, lm.POSITION_PAGE_CAP, lm.DEFAULT_MAX_MEMORY_MB) == (5, 20, 500)


def test_pass_and_report_refuse_in_words_before_fix_leaders(store):
    world, wall = World(), Wall()
    for cmd in (["pass"], ["report"]):
        code, out = run(cmd, world, wall, store)
        assert code == lm.REFUSED and "fix-leaders" in out
    assert world.urls == []
    assert db(store).execute("SELECT count(*) FROM wallet_reads").fetchone()[0] == 0


def test_fix_leaders_stores_both_all_time_pnl_boards_once(store):
    world, wall = fixed(store)
    asked = [dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(u).query)) for u in world.urls]
    assert asked == [{"time_period": "all", "sort_by": "PNL", "category": "sports", "limit": "5"},
                     {"time_period": "all", "sort_by": "PNL", "category": "crypto", "limit": "5"}]
    c = db(store)
    rows = c.execute("SELECT * FROM leaders ORDER BY wallet_key").fetchall()
    assert [r["wallet"] for r in rows] == [wallet(i) for i in range(10)]
    assert {r["sports_rank"] for r in rows[:5]} == {1, 2, 3, 4, 5} and all(r["crypto_rank"] is None for r in rows[:5])
    assert rows[7]["crypto_rank"] == 3 and rows[7]["crypto_pnl"] == 1e6 - 3 and rows[7]["crypto_volume"] == 5e6
    assert all(r["fixed_ts"] == T0 and r["sports_read_ts"] in (T0, None) for r in rows)
    assert c.execute("SELECT value FROM meta WHERE key='leaders_fixed_at'").fetchone()[0]
    before = [tuple(r) for r in rows]
    n = len(world.urls)
    world.boards["sports"] = [board_row(i + 1, wallet(50 + i)) for i in range(5)]
    code, out = run(["fix-leaders"], world, Wall(T0 + DAY), store)
    assert code == lm.REFUSED and "already" in out and len(world.urls) == n          # refused before a request
    assert [tuple(r) for r in db(store).execute("SELECT * FROM leaders ORDER BY wallet_key")] == before


def test_a_wallet_on_both_boards_is_one_leader_with_both_ranks_and_the_list_is_not_filled(store):
    world = World(crypto=[board_row(1, wallet(2))] + [board_row(i + 2, wallet(i + 5)) for i in range(4)])
    fixed(store, world)
    rows = db(store).execute("SELECT * FROM leaders").fetchall()
    assert len(rows) == 9
    both = [r for r in rows if r["wallet"] == wallet(2)][0]
    assert (both["sports_rank"], both["crypto_rank"]) == (3, 1)


@pytest.mark.parametrize("case", ["unknown_category_zero_rows", "four_rows", "a_reject", "a_wallet_twice"])
def test_a_board_with_fewer_good_rows_than_asked_refuses_and_stores_nothing(store, case):
    world = World()
    if case == "unknown_category_zero_rows":
        world.boards["crypto"] = []
    elif case == "four_rows":
        world.boards["sports"] = world.boards["sports"][:4]
    elif case == "a_reject":
        world.boards["sports"][2]["pnl"] = "lots"
    else:
        world.boards["crypto"][4]["user_id"] = wallet(5)
    code, out = run(["fix-leaders"], world, Wall(), store)
    assert code == lm.REFUSED and "nothing stored" in out
    c = db(store)
    assert c.execute("SELECT count(*) FROM leaders").fetchone()[0] == 0
    assert c.execute("SELECT count(*) FROM meta WHERE key='leaders_fixed_at'").fetchone()[0] == 0


def test_a_board_read_error_refuses_fix_leaders_and_stores_nothing(store):
    world = ErrWorld()                                      # every board read answers 503, retried, then an error
    code, out = run(["fix-leaders"], world, Wall(), store)
    assert code == lm.REFUSED and "nothing stored" in out and len(world.urls) == 5
    assert db(store).execute("SELECT count(*) FROM leaders").fetchone()[0] == 0


class ErrWorld(World):
    def __call__(self, url):
        self.urls.append(url)
        raise pd.HttpError(503, url)


def test_nothing_ever_re_ranks_replaces_or_drops_a_leader(store):
    world, wall = fixed(store)
    before = [tuple(r) for r in db(store).execute("SELECT * FROM leaders ORDER BY wallet_key")]
    world.fail[wallet(3)] = "http500"                       # one never readable, the others idle (nothing held)
    for k in range(3):
        wall.t += 300
        code, out = run(["pass"], world, wall, store)
        assert code == 0, out
    assert [tuple(r) for r in db(store).execute("SELECT * FROM leaders ORDER BY wallet_key")] == before
    code, out = run(["report"], world, wall, store)
    assert code == 0 and all(wallet(i)[:10] in out for i in range(10))


# ------------------------------------------------------------------ R3: the record
def changes(store, kind=None):
    q = "SELECT * FROM changes" + (" WHERE kind=?" if kind else "") + " ORDER BY change_id"
    return db(store).execute(q, (kind,) if kind else ()).fetchall()


def test_a_wallets_first_good_read_is_a_baseline_with_no_new(store):
    world, wall = fixed(store)
    world.held[wallet(0)] = [pos(wallet(0), 1), pos(wallet(0), 2)]
    code, out = run(["pass"], world, wall, store)
    assert code == 0
    c = db(store)
    r = c.execute("SELECT * FROM wallet_reads WHERE wallet_key=?", (wallet(0),)).fetchone()
    assert (r["good"], r["baseline"], r["rows"], r["n_new"]) == (1, 1, 2, 0)
    assert changes(store) == []
    assert c.execute("SELECT count(*) FROM holdings_current WHERE wallet_key=?", (wallet(0),)).fetchone()[0] == 2
    assert {s["kind"] for s in c.execute("SELECT kind FROM snapshots WHERE wallet_key=?", (wallet(0),))} == {"baseline"}
    assert lm.consistency(c) == []


def test_new_increase_decrease_and_exit_against_the_last_good_read_with_the_gap(store):
    world, wall = fixed(store)
    w = wallet(1)
    world.held[w] = [pos(w, 1, 10.0), pos(w, 2, 10.0), pos(w, 3, 10.0), pos(w, 4, 10.0)]
    run(["pass"], world, wall, store)
    t1 = wall.t
    wall.t += 1800                                           # a missed pass or three: the gap says so
    world.held[w] = [pos(w, 1, 12.0), pos(w, 2, 7.5), pos(w, 3, 10.0 + 5e-7), pos(w, 5, 3.0)]
    code, out = run(["pass"], world, wall, store)
    assert code == 0
    got = {(r["kind"], r["token_id"]): (r["size_before"], r["size_after"]) for r in changes(store)}
    assert got == {("INCREASE", token(1)): (10.0, 12.0), ("DECREASE", token(2)): (10.0, 7.5),
                   ("EXIT", token(4)): (10.0, 0.0), ("NEW", token(5)): (0.0, 3.0)}
    for r in changes(store):
        assert (r["read_ts"], r["prev_read_ts"], r["gap"], r["flip"]) == (t1 + 1800, t1, 1800, 0)
    held = {r["token_id"]: r["size"] for r in db(store).execute(
        "SELECT token_id, size FROM holdings_current WHERE wallet_key=?", (w,))}
    assert held == {token(1): 12.0, token(2): 7.5, token(3): 10.0 + 5e-7, token(5): 3.0}
    assert lm.consistency(db(store)) == []


def test_a_new_whose_opposite_token_was_held_carries_flip(store):
    world, wall = fixed(store)
    w = wallet(2)
    world.held[w] = [pos(w, 1, opp=token(2))]
    run(["pass"], world, wall, store)
    wall.t += 300
    world.held[w] = [pos(w, 2, opp=token(1)), pos(w, 3)]
    run(["pass"], world, wall, store)
    flips = {(r["kind"], r["token_id"]): r["flip"] for r in changes(store)}
    assert flips == {("NEW", token(2)): 1, ("NEW", token(3)): 0, ("EXIT", token(1)): 0}


@pytest.mark.parametrize("mode,reason", [("http500", "read_error"), ("page_limit", "page_limit"),
                                         ("shrank", "rows_shrank")])
def test_a_failed_read_records_only_the_failure_and_the_next_good_read_compares_with_the_last_good(store, mode,
                                                                                                    reason):
    world, wall = fixed(store)
    w = wallet(4)
    world.held[w] = [pos(w, 1), pos(w, 2)]
    run(["pass"], world, wall, store)
    t1 = wall.t
    wall.t += 300
    world.fail[w] = mode
    code, out = run(["pass"], world, wall, store)
    assert code == 0 and reason in out
    c = db(store)
    last = c.execute("SELECT * FROM wallet_reads WHERE wallet_key=? ORDER BY read_id DESC", (w,)).fetchone()
    assert (last["good"], last["reason"]) == (0, reason)
    assert changes(store) == []
    assert {r[0] for r in c.execute("SELECT token_id FROM holdings_current WHERE wallet_key=?", (w,))} == \
        {token(1), token(2)}
    wall.t += 300
    del world.fail[w]
    world.held[w] = [pos(w, 2), pos(w, 3)]
    run(["pass"], world, wall, store)
    got = {(r["kind"], r["token_id"]): (r["prev_read_ts"], r["gap"]) for r in changes(store)}
    assert got == {("EXIT", token(1)): (t1, 600), ("NEW", token(3)): (t1, 600)}
    assert lm.consistency(db(store)) == []


def test_twenty_pages_are_a_good_read_and_the_twenty_first_is_a_page_limit(store):
    world, wall = fixed(store)
    world.page_size = 2
    w = wallet(0)
    world.held[w] = [pos(w, n) for n in range(40)]          # exactly 20 pages
    run(["pass"], world, wall, store)
    r = db(store).execute("SELECT good, pages, rows FROM wallet_reads WHERE wallet_key=?", (w,)).fetchone()
    assert tuple(r) == (1, 20, 40)
    wall.t += 300
    world.held[w] = [pos(w, n) for n in range(41)]          # 21 pages
    code, out = run(["pass"], world, wall, store)
    r = db(store).execute("SELECT good, reason FROM wallet_reads WHERE wallet_key=? ORDER BY read_id DESC",
                          (w,)).fetchone()
    assert tuple(r) == (0, "page_limit") and changes(store) == []


def test_rejects_and_unread_information_fields_are_stored_with_the_read_and_counted(store):
    world, wall = fixed(store)
    w = wallet(6)
    world.held[w] = [pos(w, 1), pos(w, 1), pos(w, 2, current_size="x"), pos(w, 3, title=None), pos(w, 4),
                     pos(w, 4, current_size=99.0)]
    code, out = run(["pass"], world, wall, store)
    c = db(store)
    r = c.execute("SELECT * FROM wallet_reads WHERE wallet_key=?", (w,)).fetchone()
    assert r["rows"] == 2 and json.loads(r["rejects"]) == {"repeated_row": 1, "bad_current_size": 1,
                                                           "conflicting_row": 2}
    assert json.loads(r["unread"]) == {"title": 1}
    assert c.execute("SELECT count(*) FROM rejects WHERE read_id=?", (r["read_id"],)).fetchone()[0] == 4
    assert "rejects=4" in out


def test_one_full_snapshot_per_utc_day_beside_the_baseline(store):
    world, wall = fixed(store, wall=Wall(T0 - T0 % DAY + 3600))      # 01:00 UTC
    w = wallet(0)
    world.held[w] = [pos(w, 1), pos(w, 2)]
    for k in range(3):                                       # the same UTC day
        run(["pass"], world, wall, store)
        wall.t += 3600
    kinds = [r[0] for r in db(store).execute("SELECT kind FROM snapshots WHERE wallet_key=? ORDER BY read_id", (w,))]
    assert kinds == ["baseline", "baseline"]
    wall.t += DAY                                            # the next UTC day: one daily copy
    run(["pass"], world, wall, store)
    wall.t += 60
    run(["pass"], world, wall, store)
    kinds = [r[0] for r in db(store).execute("SELECT kind FROM snapshots WHERE wallet_key=? ORDER BY read_id", (w,))]
    assert kinds == ["baseline", "baseline", "daily", "daily"]


def test_retention_removes_rows_older_than_365_days_in_bounded_statements(store, monkeypatch):
    world, wall = fixed(store)
    w = wallet(0)
    world.held[w] = [pos(w, 1)]
    run(["pass"], world, wall, store)
    c = db(store)
    old = T0 - 366 * DAY
    c.executemany("INSERT INTO changes (read_id, wallet_key, token_id, kind, flip, size_before, size_after, read_ts,"
                  " prev_read_ts, gap) VALUES (0, ?, 'x', 'NEW', 0, 0, 1, ?, ?, 1)",
                  [(w, old, old)] * 100_500)
    c.executemany("INSERT INTO changes (read_id, wallet_key, token_id, kind, flip, size_before, size_after, read_ts,"
                  " prev_read_ts, gap) VALUES (0, ?, 'y', 'NEW', 0, 0, 1, ?, ?, 1)", [(w, T0 - 364 * DAY, old)] * 7)
    c.execute("INSERT INTO rejects (read_id, wallet_key, reason, raw, read_ts) VALUES (0, ?, 'r', '{}', ?)", (w, old))
    c.commit()
    statements = []
    real_connect = sqlite3.connect

    def traced(*a, **k):
        conn = real_connect(*a, **k)
        conn.set_trace_callback(statements.append)
        return conn
    monkeypatch.setattr(lm.sqlite3, "connect", traced)
    wall.t += 300
    code, out = run(["pass"], world, wall, store)
    monkeypatch.setattr(lm.sqlite3, "connect", real_connect)
    deletes = [s for s in statements if s.lstrip().upper().startswith("DELETE") and "holdings_current" not in s]
    assert len(deletes) == lm.DELETE_STATEMENTS == 20 and all("LIMIT 5000" in s for s in deletes)
    assert db(store).execute("SELECT count(*) FROM changes WHERE token_id='x'").fetchone()[0] == 500
    assert "removed 100000" in out
    wall.t += 300
    run(["pass"], world, wall, store)
    c = db(store)
    assert c.execute("SELECT count(*) FROM changes WHERE token_id='x'").fetchone()[0] == 0
    assert c.execute("SELECT count(*) FROM changes WHERE token_id='y'").fetchone()[0] == 7
    assert c.execute("SELECT count(*) FROM rejects WHERE reason='r'").fetchone()[0] == 0


def test_holdings_leaders_and_a_wallets_last_good_read_are_never_aged_out(store):
    world, wall = fixed(store)
    w = wallet(0)
    world.held[w] = [pos(w, 1)]
    run(["pass"], world, wall, store)
    world.fail[w] = "http500"
    wall.t += 400 * DAY
    run(["pass"], world, wall, store)
    wall.t += 300
    run(["pass"], world, wall, store)
    c = db(store)
    assert c.execute("SELECT count(*) FROM leaders").fetchone()[0] == 10
    assert c.execute("SELECT count(*) FROM holdings_current WHERE wallet_key=?", (w,)).fetchone()[0] == 1
    assert c.execute("SELECT count(*) FROM wallet_reads WHERE wallet_key=? AND good=1", (w,)).fetchone()[0] == 1
    assert lm.consistency(c) == []


def test_a_second_pass_exits_at_once_with_one_line_and_a_non_zero_code(store):
    world, wall = fixed(store)
    fd = os.open(str(store) + ".lock", os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        n = len(world.urls)
        code, out = run(["pass"], world, wall, store)
        assert code == lm.LOCKED and code != 0 and len(out.strip().splitlines()) == 1 and "lock" in out
        assert len(world.urls) == n
    finally:
        os.close(fd)
    assert run(["pass"], world, wall, store)[0] == 0


def test_memory_is_checked_at_each_wallet_and_the_pass_stops_cleanly_between_wallets(store):
    world, wall = fixed(store)
    seen = iter([100.0, 499.0, 501.0, 501.0, 501.0, 501.0, 501.0, 501.0, 501.0, 501.0, 501.0])
    code, out = run(["pass"], world, wall, store, memory=lambda: next(seen))
    assert code == lm.STOPPED_MEMORY and "500" in out
    c = db(store)
    assert c.execute("SELECT count(*) FROM wallet_reads").fetchone()[0] == 2
    assert c.execute("SELECT status FROM passes").fetchone()[0] == "stopped_memory"
    code, out = run(["pass", "--max-memory-mb", "1000"], world, wall, store, memory=lambda: 600.0)
    assert code == 0


def test_pass_is_one_command_that_ends_with_no_watch_loop_or_interval(store):
    world, wall = fixed(store)
    for extra in (["--watch"], ["--loop"], ["--interval", "300"], ["--every", "5m"]):
        with pytest.raises(SystemExit) as e:
            run(["pass"] + extra, world, wall, store)
        assert e.value.code == 2
    src = Path(lm.__file__).read_text()
    for word in ("while True", "crontab", "launchd", "schedule", "time.sleep"):
        assert word not in src


# ------------------------------------------------------------------ R5: the lines
def seeded_world():
    world = World()
    for i in range(10):
        w = wallet(i)
        world.held[w] = [pos(w, 100 * i + n, opp=token(100 * i + n + 50)) for n in range(3)]
    return world


def secrets_of(world):
    out = set()
    for rows in world.held.values():
        for r in rows:
            out |= {r["title"], r["outcome"], r["token_id"], r["condition_id"], r["event_slug"], r["opposite_token_id"]}
    out |= {"0.123456", "0.654321", "0.12", "0.65"}
    out |= {wallet(i) for i in range(10)} | {wallet(i)[:11] for i in range(10)}
    return out


def test_no_line_prints_a_title_outcome_token_market_price_or_full_wallet(store):
    world, wall = seeded_world(), Wall()
    outs = [run(["fix-leaders"], world, wall, store)[1], run(["pass"], world, wall, store)[1]]
    wall.t += 300
    for i in range(10):
        world.held[wallet(i)] = world.held[wallet(i)][1:] + [pos(wallet(i), 100 * i + 9, opp=token(100 * i))]
    world.fail[wallet(3)] = "http500"
    outs += [run(["pass"], world, wall, store)[1], run(["report"], world, wall, store)[1]]
    text = "\n".join(outs)
    assert changes(store, "NEW")                            # the world did change
    for s in secrets_of(world):
        assert s not in text, s
    assert all(wallet(i)[:10] in text for i in range(10))


def test_pass_prints_one_line_per_wallet_and_one_summary_line(store):
    world, wall = seeded_world(), Wall()
    run(["fix-leaders"], world, wall, store)
    code, out = run(["pass"], world, wall, store)
    lines = out.strip().splitlines()
    assert len(lines) == 11
    for i, line in enumerate(lines[:10]):
        assert line.startswith("wallet 0x") and " good " in line and "rows=3" in line and "mem=" in line
        for k in ("new=", "increase=", "decrease=", "exit=", "rejects="):
            assert k in line
    assert lines[10].startswith("pass ") and "10 good" in lines[10] and "0 failed" in lines[10]


def test_report_counts_per_leader_and_kind_the_gaps_and_the_failed_share(store):
    world, wall = seeded_world(), Wall()
    run(["fix-leaders"], world, wall, store)
    run(["pass"], world, wall, store)
    for step in (300, 300, 900):
        wall.t += step
        world.held[wallet(0)] = world.held[wallet(0)] + [pos(wallet(0), 7000 + step + int(wall.t) % 1000)]
        world.fail[wallet(9)] = "http500" if step == 900 else None
        run(["pass"], world, wall, store)
    code, out = run(["report", "--days", "30"], world, wall, store)
    assert code == 0
    assert "largest 900" in out and "median 300" in out
    assert "failed reads 1 of 40" in out
    line0 = [l for l in out.splitlines() if l.startswith(wallet(0)[:10])][0]
    assert "new=3" in line0 and "good=4" in line0
    assert "new=3 increase=0 decrease=0 exit=0" in out


# ------------------------------------------------------------------ real kills (R3 one write per wallet, R4 claim)
KILL_SCRIPT = textwrap.dedent(r'''
    import io, json, os, signal, socket, sqlite3, sys
    def refuse(*a, **k):
        raise AssertionError("network")
    socket.socket.connect = refuse
    socket.create_connection = refuse
    sys.path.insert(0, os.environ["TEST_DIR"])
    import test_leaders_monitor as T
    from valedia_trader.research_loop import leaders_monitor as lm
    spec = json.load(open(sys.argv[1]))
    kill_at = spec["kill_at"]
    count = [0]
    class Kill(sqlite3.Connection):
        def _tick(self):
            count[0] += 1
            if count[0] == kill_at:
                os.kill(os.getpid(), signal.SIGKILL)
        def execute(self, *a, **k):
            self._tick()
            return super().execute(*a, **k)
        def executemany(self, *a, **k):
            self._tick()
            return super().executemany(*a, **k)
    real = sqlite3.connect
    lm.sqlite3.connect = lambda *a, **k: real(*a, factory=Kill, **k)
    world = T.World()
    world.held = spec["held"]
    world.boards = spec.get("boards", world.boards)
    if spec.get("barrier"):
        # both processes wait inside their last board read, so both are past the "already fixed" check before
        # either claims: only the claim itself can decide which one fixes
        import time
        answer = world.__call__
        def gated(url):
            if "category=crypto" in url:
                open(spec["barrier"] + "." + str(os.getpid()), "w").close()
                while len([f for f in os.listdir(os.path.dirname(spec["barrier"])) if f.startswith("go.")]) < 2:
                    time.sleep(0.01)
            return answer(url)
        world = gated
        world.urls = []
    wall = T.Wall(spec["t"])
    code, out = T.run([spec["cmd"]], world, wall, spec["store"])
    print("STATEMENTS %d CODE %d" % (count[0], code))
    print(out)
''')


def child(tmp_path, spec, timeout=60):
    f = tmp_path / ("spec%d.json" % len(list(tmp_path.glob("spec*.json"))))
    f.write_text(json.dumps(spec))
    script = tmp_path / "kill_child.py"
    script.write_text(KILL_SCRIPT)
    env = dict(os.environ, PYTHONPATH=WORKTREE, TEST_DIR=str(Path(__file__).parent), PYTHONDONTWRITEBYTECODE="1")
    return subprocess.Popen([sys.executable, "-B", str(script), str(f)], env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)


def holdings(store):
    c = db(store)
    return {(r[0], r[1]): r[2] for r in c.execute("SELECT wallet_key, token_id, size FROM holdings_current")}


def test_one_write_per_wallet_survives_a_real_kill_at_every_statement(tmp_path):
    base = tmp_path / "base" / "leaders.sqlite"
    world = World(sports=[board_row(i + 1, wallet(i)) for i in range(2)] + [board_row(3, wallet(2))] +
                  [board_row(4, wallet(3)), board_row(5, wallet(4))],
                  crypto=[board_row(i + 1, wallet(i)) for i in range(5)])
    wall = Wall()
    run(["fix-leaders"], world, wall, base)
    old = {wallet(i): [pos(wallet(i), 10 * i + n) for n in range(3)] for i in range(5)}
    world.held = old
    run(["pass"], world, wall, base)
    old_h = holdings(base)
    new = {w: rows[1:] + [pos(w, 999 + k, size=4.0)] for k, (w, rows) in enumerate(old.items())}
    probe = tmp_path / "probe" / "leaders.sqlite"
    probe.parent.mkdir()
    shutil.copy(base, probe)
    p = child(tmp_path, {"kill_at": 10 ** 9, "held": new, "t": T0 + 300, "cmd": "pass", "store": str(probe)})
    out, err = p.communicate(timeout=120)
    assert p.returncode == 0, err[-2000:]
    total = int(out.split()[1])
    new_h = holdings(probe)
    assert total > 20 and new_h != old_h
    killed = 0
    for n in range(1, total + 1):
        s = tmp_path / ("k%d" % n) / "leaders.sqlite"
        s.parent.mkdir()
        shutil.copy(base, s)
        p = child(tmp_path, {"kill_at": n, "held": new, "t": T0 + 300, "cmd": "pass", "store": str(s)})
        p.communicate(timeout=120)
        killed += p.returncode == -signal.SIGKILL
        c = db(s)
        assert lm.consistency(c) == [], n
        h = holdings(s)
        for w in new:
            mine = {k: v for k, v in h.items() if k[0] == w}
            was = {k: v for k, v in old_h.items() if k[0] == w}
            now = {k: v for k, v in new_h.items() if k[0] == w}
            n_changes = c.execute("SELECT count(*) FROM changes WHERE wallet_key=?", (w,)).fetchone()[0]
            assert (mine, n_changes) in ((was, 0), (now, 2)), (n, w)
        c.close()
        shutil.rmtree(s.parent)
    assert killed == total


def test_a_real_kill_inside_fix_leaders_leaves_all_or_nothing(tmp_path):
    probe = tmp_path / "probe" / "leaders.sqlite"
    p = child(tmp_path, {"kill_at": 10 ** 9, "held": {}, "t": T0, "cmd": "fix-leaders", "store": str(probe)})
    out, err = p.communicate(timeout=120)
    assert p.returncode == 0, err[-2000:]
    total = int(out.split()[1])
    for n in range(1, total + 1):
        s = tmp_path / ("f%d" % n) / "leaders.sqlite"
        p = child(tmp_path, {"kill_at": n, "held": {}, "t": T0, "cmd": "fix-leaders", "store": str(s)})
        p.communicate(timeout=120)
        assert p.returncode == -signal.SIGKILL
        if s.exists():
            c = db(s)
            try:
                n_leaders = c.execute("SELECT count(*) FROM leaders").fetchone()[0]
                n_claim = c.execute("SELECT count(*) FROM meta WHERE key='leaders_fixed_at'").fetchone()[0]
            except sqlite3.OperationalError:
                n_leaders = n_claim = 0                      # killed before the tables were made
            assert (n_leaders, n_claim) in ((0, 0), (10, 1)), n
        code, out = run(["fix-leaders"], World(), Wall(), s)       # a later run completes it, or refuses
        assert code in (0, lm.REFUSED)
        assert db(s).execute("SELECT count(*) FROM leaders").fetchone()[0] == 10


def test_two_fix_leaders_processes_at_once_exactly_one_fixes(tmp_path):
    s = tmp_path / "both" / "leaders.sqlite"
    gate = tmp_path / "gate"
    gate.mkdir()
    procs = []
    for k in range(2):
        boards = {"sports": [board_row(i + 1, wallet(20 * k + i)) for i in range(5)],
                  "crypto": [board_row(i + 1, wallet(20 * k + i + 5)) for i in range(5)]}
        procs.append(child(tmp_path, {"kill_at": 10 ** 9, "held": {}, "t": T0, "cmd": "fix-leaders",
                                      "store": str(s), "boards": boards, "barrier": str(gate / "go")}))
    outs = [p.communicate(timeout=120) for p in procs]
    codes = sorted(int(o.split("CODE")[1].split()[0]) for o, e in outs)
    assert codes == [0, lm.REFUSED], outs
    rows = db(s).execute("SELECT wallet FROM leaders").fetchall()
    assert len(rows) == 10
    firsts = {int(r[0][2:10], 16) - 0x1a2b3c00 for r in rows}
    assert firsts in ({*range(10)}, {*range(20, 30)})          # one process's boards, never a mix


# ------------------------------------------------------------------ addendum 2, item 1: no value stops a pass
def test_a_lone_surrogate_title_and_a_401_digit_price_stop_nothing(store):
    world, wall = fixed(store)
    w = wallet(0)
    world.held[w] = [pos(w, 1, title="\ud800"), pos(w, 2, current_price=10 ** 400), pos(w, 3, outcome="a\x00b")]
    code, out = run(["pass"], world, wall, store)
    assert code == 0, out
    c = db(store)
    r = c.execute("SELECT * FROM wallet_reads WHERE wallet_key=?", (w,)).fetchone()
    assert (r["good"], r["rows"]) == (1, 3)
    assert json.loads(r["unread"]) == {"title": 1, "current_price": 1, "outcome": 1}
    assert c.execute("SELECT count(*) FROM wallet_reads WHERE good=1").fetchone()[0] == 10
    assert c.execute("SELECT count(*) FROM holdings_current WHERE wallet_key=? AND title IS NULL", (w,)).fetchone()[0] == 1


class RaisingWorld(World):
    """Raises an error of no expected kind inside one wallet's read (its message holds a row's content)."""
    bad: str = ""

    def __call__(self, url):
        if "user=" + self.bad in url and self.bad:
            self.urls.append(url)
            raise ValueError("SECRET-TITLE-1 broke the reader")
        return super().__call__(url)


def test_an_error_inside_one_wallets_read_is_that_wallets_failed_read_and_the_pass_goes_on(store):
    world = RaisingWorld()
    world, wall = fixed(store, world)
    for i in range(10):
        world.held[wallet(i)] = [pos(wallet(i), 10 * i + 1)]
    run(["pass"], world, wall, store)
    wall.t += 300
    world.bad = wallet(2)
    world.held[wallet(2)] = []
    code, out = run(["pass"], world, wall, store)
    assert code == lm.INTERNAL_ERROR and code not in (lm.OK, lm.LOCKED, lm.REFUSED, lm.STOPPED_MEMORY)
    c = db(store)
    last = c.execute("SELECT * FROM wallet_reads WHERE wallet_key=? ORDER BY read_id DESC", (wallet(2),)).fetchone()
    assert (last["good"], last["reason"], last["detail"]) == (0, "internal_error", "ValueError")
    assert c.execute("SELECT count(*) FROM wallet_reads WHERE good=1").fetchone()[0] == 19
    assert changes(store) == []                              # its holding stays; nothing inferred
    assert c.execute("SELECT count(*) FROM holdings_current WHERE wallet_key=?", (wallet(2),)).fetchone()[0] == 1
    lines = out.strip().splitlines()
    mine = [l for l in lines if l.startswith("wallet " + wallet(2)[:10])]
    assert len(lines) == 11 and len(mine) == 1 and "failed internal_error" in mine[0] and "internal_error=1" in lines[-1]
    assert "1 failed" in lines[-1] and "SECRET" not in out
    assert lm.consistency(c) == []


def test_a_write_that_fails_for_one_wallet_is_that_wallets_failed_read_and_the_pass_goes_on(store, monkeypatch):
    world, wall = fixed(store)
    for i in range(10):
        world.held[wallet(i)] = [pos(wallet(i), 10 * i + 1)]
    run(["pass"], world, wall, store)
    before = holdings(store)
    wall.t += 300
    for i in range(10):
        world.held[wallet(i)] = [pos(wallet(i), 10 * i + 2)]
    real = lm._holding

    def failing(r):
        if r["proxy_wallet"] == wallet(3):
            raise sqlite3.InterfaceError("SECRET-TITLE-32 could not be bound")
        return real(r)
    monkeypatch.setattr(lm, "_holding", failing)
    code, out = run(["pass"], world, wall, store)
    assert code == lm.INTERNAL_ERROR
    c = db(store)
    last = c.execute("SELECT * FROM wallet_reads WHERE wallet_key=? ORDER BY read_id DESC", (wallet(3),)).fetchone()
    assert (last["good"], last["reason"], last["detail"]) == (0, "internal_error", "InterfaceError")
    after = holdings(store)
    assert {k: v for k, v in after.items() if k[0] == wallet(3)} == {k: v for k, v in before.items() if k[0] == wallet(3)}
    assert len(changes(store)) == 18                         # nine wallets: one EXIT and one NEW each
    assert "internal_error=1" in out and "SECRET" not in out
    assert lm.consistency(c) == []


@pytest.mark.parametrize("error", [MemoryError, KeyboardInterrupt])
def test_a_memory_error_or_an_interrupt_is_not_caught(store, monkeypatch, error):
    world, wall = fixed(store)
    world.held[wallet(0)] = [pos(wallet(0), 1)]

    def boom(r):
        raise error()
    monkeypatch.setattr(lm, "_holding", boom)
    with pytest.raises(error):
        run(["pass"], world, wall, store)
    assert lm.consistency(db(store)) == []


# ------------------------------------------------------------------ addendum 2, item 2: a malformed row is not an EXIT
def held_now(store, w):
    return {r["token_id"]: (r["size"], r["carried"], r["seen_read_id"]) for r in db(store).execute(
        "SELECT token_id, size, carried, seen_read_id FROM holdings_current WHERE wallet_key=?", (w,))}


def last_read(store, w):
    return db(store).execute("SELECT * FROM wallet_reads WHERE wallet_key=? ORDER BY read_id DESC", (w,)).fetchone()


def test_a_held_token_whose_row_is_rejected_or_missing_in_a_read_with_a_reject_is_carried_not_an_exit(store):
    world, wall = fixed(store)
    w = wallet(1)
    world.held[w] = [pos(w, 1, 10.0), pos(w, 2, 10.0), pos(w, 3, 10.0)]
    run(["pass"], world, wall, store)
    base = last_read(store, w)
    wall.t += 300
    world.held[w] = [pos(w, 1, 10.0), pos(w, 2, current_size="x")]          # t2 unreadable, t3 not served
    run(["pass"], world, wall, store)
    assert changes(store) == []                                              # no EXIT invented
    r2 = last_read(store, w)
    assert (r2["good"], r2["rows"], r2["carried"]) == (1, 1, 2)
    assert held_now(store, w) == {token(1): (10.0, 0, r2["read_id"]), token(2): (10.0, 1, base["read_id"]),
                                  token(3): (10.0, 1, base["read_id"])}
    assert lm.consistency(db(store)) == []
    wall.t += 300
    world.held[w] = [pos(w, 1, 10.0), pos(w, 2, 10.0)]                       # a clean read: t3 truly left
    run(["pass"], world, wall, store)
    got = [(r["kind"], r["token_id"], r["size_before"], r["prev_read_ts"], r["last_seen_ts"]) for r in changes(store)]
    assert got == [("EXIT", token(3), 10.0, base["read_ts"] + 300, base["read_ts"])]    # and no NEW for t2
    r3 = last_read(store, w)
    assert r3["carried"] == 0 and set(held_now(store, w)) == {token(1), token(2)}
    assert all(c == 0 for _, c, _ in held_now(store, w).values())
    assert lm.consistency(db(store)) == []


def test_a_held_token_in_conflict_is_carried_and_a_later_size_is_compared_with_the_carried_one(store):
    world, wall = fixed(store)
    w = wallet(2)
    world.held[w] = [pos(w, 1, 10.0), pos(w, 2, 10.0)]
    run(["pass"], world, wall, store)
    wall.t += 300
    world.held[w] = [pos(w, 1, 10.0), pos(w, 2, 4.0), pos(w, 2, 5.0)]       # t2 in conflict
    run(["pass"], world, wall, store)
    assert changes(store) == [] and held_now(store, w)[token(2)][:2] == (10.0, 1)
    wall.t += 300
    world.held[w] = [pos(w, 1, 10.0), pos(w, 2, 6.0)]
    run(["pass"], world, wall, store)
    assert [(r["kind"], r["token_id"], r["size_before"], r["size_after"]) for r in changes(store)] == \
        [("DECREASE", token(2), 10.0, 6.0)]


def test_new_increase_and_decrease_from_the_good_rows_of_a_read_with_a_reject_are_recorded(store):
    world, wall = fixed(store)
    w = wallet(3)
    world.held[w] = [pos(w, 1, 10.0), pos(w, 2, 10.0), pos(w, 3, 10.0)]
    run(["pass"], world, wall, store)
    wall.t += 300
    world.held[w] = [pos(w, 1, 12.0), pos(w, 2, 8.0), pos(w, 4, 1.0, opp=token(3)), {"not": "a position"}]
    run(["pass"], world, wall, store)
    got = {(r["kind"], r["token_id"]): (r["size_before"], r["size_after"], r["flip"]) for r in changes(store)}
    assert got == {("INCREASE", token(1)): (10.0, 12.0, 0), ("DECREASE", token(2)): (10.0, 8.0, 0),
                   ("NEW", token(4)): (0.0, 1.0, 1)}
    assert held_now(store, w)[token(3)][:2] == (10.0, 1)


def test_a_true_repeat_alone_does_not_stop_an_exit(store):
    world, wall = fixed(store)
    w = wallet(4)
    world.held[w] = [pos(w, 1), pos(w, 2)]
    run(["pass"], world, wall, store)
    wall.t += 300
    world.held[w] = [pos(w, 1), pos(w, 1)]                                   # every token readable: t2 left
    run(["pass"], world, wall, store)
    assert [(r["kind"], r["token_id"]) for r in changes(store)] == [("EXIT", token(2))]


def test_report_prints_the_carried_holdings_and_the_share_of_good_reads_that_carried_any(store):
    world, wall = fixed(store)
    w = wallet(0)
    world.held[w] = [pos(w, 1), pos(w, 2), pos(w, 3)]
    run(["pass"], world, wall, store)
    for k in range(3):
        wall.t += 300
        world.held[w] = [pos(w, 1)] + ([pos(w, 2, avg_price="?")] if k < 2 else [pos(w, 2), pos(w, 3)])
        run(["pass"], world, wall, store)
    wall.t += 300
    world.held[w] = [pos(w, 1), pos(w, 2, avg_price="?")]
    run(["pass"], world, wall, store)
    code, out = run(["report"], world, wall, store)
    line0 = [l for l in out.splitlines() if l.startswith(wallet(0)[:10])][0]
    assert "carried=2" in line0 and "reads carrying=60.0%" in line0           # 3 of 5 good reads carried any
    line1 = [l for l in out.splitlines() if l.startswith(wallet(1)[:10])][0]
    assert "carried=0" in line1 and "reads carrying=0.0%" in line1


import random as _random


def sizes_held(store, w):
    return dict(db(store).execute("SELECT token_id, size FROM holdings_current WHERE wallet_key=?", (w,)).fetchall())


def _render(w, truth, fresh, rng, junk):
    """The service's answer for `truth` {n: size}: a freshly bought token's row is always good; any other row
    may be rejected, in conflict or repeated; junk rows (another wallet's, not an object) may be added."""
    rows, unreadable = [], set()
    for n, size in sorted(truth.items()):
        x = rng.random()
        if n in fresh or x < 0.65:
            rows.append(pos(w, n, size))
        elif x < 0.8:
            rows.append(pos(w, n, size, current_size=rng.choice(["x", None, True, [1]])))
            unreadable.add(n)
        elif x < 0.92:
            rows += [pos(w, n, size), pos(w, n, size + 1.0)]
            unreadable.add(n)
        else:
            rows += [pos(w, n, size), pos(w, n, size)]
    if junk and rng.random() < 0.15:
        rows.append(rng.choice([pos(wallet(99), 5000), "a row", pos(w, 6000, token_id=None)]))
    rng.shuffle(rows)
    return rows, unreadable


@pytest.mark.parametrize("seed", range(12))
def test_property_no_exit_or_new_for_a_merely_unreadable_row_and_a_true_exit_at_the_first_clean_read(tmp_path,
                                                                                                     seed):
    rng = _random.Random(seed)
    store = tmp_path / ("s%d" % seed) / "leaders.sqlite"
    world, wall = fixed(store)
    ws = [wallet(i) for i in range(3)]
    truth = {w: {100 * i + k: float(rng.randint(1, 9)) for k in range(rng.randint(0, 4))} for i, w in enumerate(ws)}
    nxt = {w: 100 * i + 50 for i, w in enumerate(ws)}
    fresh = {w: set(truth[w]) for w in ws}
    prev_truth = {w: {} for w in ws}
    for p in range(10):
        for w in ws:
            world.held[w], _ = _render(w, truth[w], fresh[w], rng, junk=p > 0)
            world.fail[w] = "http500" if p and rng.random() < 0.1 else None
        n_before = len(changes(store))
        held_before = {w: sizes_held(store, w) for w in ws}
        run(["pass"], world, wall, store)
        for r in changes(store)[n_before:]:
            w, n = r["wallet_key"], int(r["token_id"][1:])
            if r["kind"] == "EXIT":
                assert n not in truth[w], (seed, p, "an EXIT for a token still held")
            if r["kind"] == "NEW":
                assert r["token_id"] not in held_before[w], (seed, p, "a NEW for a token already in the record")
                assert n in fresh[w] or n not in prev_truth[w], (seed, p, "a NEW for a merely unreadable row")
        for w in ws:
            last = last_read(store, w)
            mine = {int(t[1:]): s for t, s in sizes_held(store, w).items()}
            clean = last["good"] and last["read_id"] == db(store).execute(
                "SELECT max(read_id) FROM wallet_reads WHERE wallet_key=?", (w,)).fetchone()[0] and \
                not {k for k in json.loads(last["rejects"] or "{}") if k != "repeated_row"}
            if clean and last["pass_id"] == db(store).execute("SELECT max(pass_id) FROM passes").fetchone()[0]:
                assert mine == truth[w], (seed, p, "after a clean read the record is the truth")
            assert set(mine) >= {n for n in truth[w] if n in fresh[w]} or not last["good"]
        assert lm.consistency(db(store)) == []
        wall.t += 300
        for w in ws:                                         # the world moves on
            prev_truth[w] = dict(truth[w])
            if last_read(store, w)["good"]:                  # a bought token is fresh until a good read saw it
                fresh[w] = set()
            for n in list(truth[w]):
                x = rng.random()
                if x < 0.15:
                    del truth[w][n]
                elif x < 0.3:
                    truth[w][n] = float(rng.randint(1, 9))
            if rng.random() < 0.4:
                truth[w][nxt[w]] = float(rng.randint(1, 9))
                fresh[w].add(nxt[w])
                nxt[w] += 1


def held_state(s):
    c = db(s)
    try:
        rows = c.execute("SELECT wallet_key, token_id, size, carried FROM holdings_current").fetchall()
    except sqlite3.OperationalError:
        rows = [(*r, 0) for r in c.execute("SELECT wallet_key, token_id, size FROM holdings_current")]
    c.close()
    out = {}
    for w, t, size, carried in rows:
        out.setdefault(w, {})[t] = (size, carried)
    return out


def test_one_write_per_wallet_survives_a_real_kill_at_every_statement_with_carried_holdings(tmp_path):
    base = tmp_path / "base" / "leaders.sqlite"
    world = World(sports=[board_row(i + 1, wallet(i)) for i in range(5)],
                  crypto=[board_row(i + 1, wallet(i)) for i in range(5)])
    wall = Wall()
    run(["fix-leaders"], world, wall, base)
    old = {wallet(i): [pos(wallet(i), 10 * i + n) for n in range(3)] for i in range(5)}
    world.held = old
    run(["pass"], world, wall, base)
    old_s = held_state(base)
    new = {}
    for k, (w, rows) in enumerate(old.items()):
        if k < 3:      # a rejected row and a missing row: both carried, the good new token is NEW
            new[w] = [dict(rows[1], current_size="x"), rows[2], pos(w, 999 + k, size=4.0)]
        else:          # a clean read: one EXIT, one NEW
            new[w] = rows[1:] + [pos(w, 999 + k, size=4.0)]
    probe = tmp_path / "probe" / "leaders.sqlite"
    probe.parent.mkdir()
    shutil.copy(base, probe)
    p = child(tmp_path, {"kill_at": 10 ** 9, "held": new, "t": T0 + 300, "cmd": "pass", "store": str(probe)})
    out, err = p.communicate(timeout=120)
    assert p.returncode == 0, err[-2000:]
    total = int(out.split()[1])
    new_s = held_state(probe)
    c = db(probe)
    want_changes = {w: c.execute("SELECT count(*) FROM changes WHERE wallet_key=?", (w,)).fetchone()[0] for w in new}
    c.close()
    ws = list(new)
    assert [want_changes[w] for w in ws] == [1, 1, 1, 2, 2]                   # no EXIT for the carried ones
    assert all(sum(cr for _, cr in new_s[w].values()) == 2 and len(new_s[w]) == 4 for w in ws[:3])
    assert all(sum(cr for _, cr in new_s[w].values()) == 0 and len(new_s[w]) == 3 for w in ws[3:])
    killed = 0
    for n in range(1, total + 1):
        s = tmp_path / ("c%d" % n) / "leaders.sqlite"
        s.parent.mkdir()
        shutil.copy(base, s)
        p = child(tmp_path, {"kill_at": n, "held": new, "t": T0 + 300, "cmd": "pass", "store": str(s)})
        p.communicate(timeout=120)
        killed += p.returncode == -signal.SIGKILL
        c = db(s)
        assert lm.consistency(c) == [], n
        st = held_state(s)
        for w in ws:
            n_changes = c.execute("SELECT count(*) FROM changes WHERE wallet_key=?", (w,)).fetchone()[0]
            assert (st.get(w), n_changes) in ((old_s.get(w), 0), (new_s.get(w), want_changes[w])), (n, w)
        c.close()
        shutil.rmtree(s.parent)
    assert killed == total


# ------------------------------------------------------------------ addendum 2, item 3: one snapshot a UTC day
def test_one_snapshot_a_wallet_a_utc_day_also_when_it_holds_nothing(store):
    day0 = T0 - T0 % DAY
    world, wall = fixed(store, wall=Wall(day0 + 3600))       # 01:00 UTC
    w, e = wallet(0), wallet(1)
    world.held[w] = [pos(w, 1), pos(w, 2)]
    run(["pass"], world, wall, store)                        # w: baseline of two; e: baseline of nothing
    wall.t = day0 + 5 * 3600
    world.held[e] = [pos(e, 7)]
    run(["pass"], world, wall, store)                        # e's day-one snapshot (empty) was already taken
    wall.t = day0 + DAY + 3600
    world.held[w] = []
    run(["pass"], world, wall, store)                        # w's day-two snapshot: it holds nothing
    wall.t = day0 + DAY + 5 * 3600
    world.held[w] = [pos(w, 3)]
    run(["pass"], world, wall, store)
    c = db(store)

    def snaps(k, day):
        return c.execute("SELECT count(*) FROM snapshots WHERE wallet_key=? AND utc_day=?", (k, day)).fetchone()[0]
    d1, d2 = lm._utc_day(day0 + 1), lm._utc_day(day0 + DAY + 1)
    assert (snaps(w, d1), snaps(w, d2)) == (2, 0)            # the empty day-two snapshot is the day's one
    assert (snaps(e, d1), snaps(e, d2)) == (0, 1)
    taken = c.execute("SELECT wallet_key, snapshot_day, count(*) FROM wallet_reads WHERE snapshot_day IS NOT NULL "
                      "AND wallet_key IN (?, ?) GROUP BY wallet_key, snapshot_day ORDER BY 1, 2", (w, e)).fetchall()
    assert sorted(tuple(r) for r in taken) == sorted([(w, d1, 1), (w, d2, 1), (e, d1, 1), (e, d2, 1)])
    assert all(r[0] == lm._utc_day(r[1]) for r in c.execute(
        "SELECT snapshot_day, read_ts FROM wallet_reads WHERE snapshot_day IS NOT NULL"))
