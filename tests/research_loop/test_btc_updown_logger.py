"""BTC 15-minute Up/Down forward logger: log-only, disabled until 2026-12-04 00:00 America/Los_Angeles.

Every fixture here is synthetic (placeholder tickers, placeholder tick values). Nothing in this file touches the
network, a real store or a model. The module under test has no fetch of its own: BRTI ticks, the book and the tape
arrive through caller-supplied reader callables (or the one file adapter, pointed at a temporary folder), and these
tests pass fakes.
"""
from __future__ import annotations

import ast
import json
import os
import socket
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from valedia_trader.research_loop import btc_updown_logger as L
from valedia_trader.research_loop.btc_updown_logger import (
    ENABLED_FROM, CloseNotReached, Fixture, LateWindow, NaiveClock, NotYetEnabled, TapeBudget, TapeBudgetExceeded,
    Window, check_enabled, dry_run, log_close, log_tape, log_window, read_tick_files, tick_file_reader)

MODULE_PATH = Path(L.__file__)
MODULE_SRC = MODULE_PATH.read_text()
REPO = Path(__file__).resolve().parents[2]

BOOK = {"yes": [(0.49, 30.0), (0.48, 10.0)], "no": [(0.50, 25.0), (0.49, 5.0)]}
EF = ENABLED_FROM
ONE_S = timedelta(seconds=1)
UTC = timezone.utc


def at(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=UTC)


def D(x: float) -> str:
    """How the logger writes a float: its shortest repr as a decimal string."""
    return format(Decimal(repr(x)), "f")


def window(i: int = 0, *, open_ts: float | None = None) -> Window:
    """A synthetic window that opens so that t* = ENABLED_FROM + i * 900 s (window 0: t* is the enabling instant)."""
    base = EF.timestamp() - 300.0 + 900.0 * i if open_ts is None else open_ts
    return Window(window_ticker=f"KXBTC15M-TEST{i:03d}", open_ts=base, close_ts=base + 900.0,
                  floor_strike=100000.0 + i)


def tstar_ms(w: Window) -> int:
    return int(round((w.open_ts + 300.0) * 1000))


def tick_row(src_ms: int, value: str = "100000.00", *, seq: int = 1, recv: int | None = None, avg: str = "99999.99",
             last15: dict | None = None, channel: str = "cfbenchmarks_value", index_id: str = "BRTI",
             row_value: str | None = None) -> dict:
    """One row in the index client's JSONL shape (`IndexTick.as_json(channel)`)."""
    recv = src_ms + 20 if recv is None else recv
    return {"index_id": index_id, "value": value if row_value is None else row_value, "source_time_ms": src_ms,
            "received_at_ms": recv, "sending_ts_ms": recv + 5, "sid": 1, "seq": seq,
            "avg_60s": {"value": avg, "window_size": 60, "window_start_ts_ms": recv - 60000,
                        "window_end_ts_exclusive": recv},
            "last_15min_avg": last15,
            "data": json.dumps({"type": "value", "id": "BRTI", "time": src_ms, "value": value}),
            "channel": channel}


def series(end_ms: int, n: int = 301, step_ms: int = 1000, *, base: str = "100000.00") -> list[dict]:
    """n ticks, step_ms apart, the last at end_ms; values rise by one cent per tick."""
    start = end_ms - (n - 1) * step_ms
    return [tick_row(start + k * step_ms, str(Decimal(base) + Decimal("0.01") * k), seq=k + 1) for k in range(n)]


class Spy:
    """A reader that records its calls and returns a fixed value."""
    def __init__(self, value):
        self.value, self.calls = value, []

    def __call__(self, *args):
        self.calls.append(args)
        return self.value


def run(folder, w, *, clock=None, ticks=None, book=BOOK):
    t = Spy(series(tstar_ms(w)) if ticks is None else ticks)
    b = Spy(book)
    clk = clock if clock is not None else (lambda: at(w.open_ts + 300.0))
    rec = log_window(folder, w, clock=clk, read_ticks=t, read_book=b)
    return rec, t, b


def lines(folder: Path) -> list[dict]:
    p = folder / L.LOG_NAME
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


def no_float_lines(folder: Path) -> list[dict]:
    """Parse every written line, failing on any JSON float."""
    def refuse(s):
        raise AssertionError(f"a float was written: {s}")
    return [json.loads(x, parse_float=refuse) for x in (folder / L.LOG_NAME).read_text().splitlines()]


# ---------------------------------------------------------------- the date gate

def test_enabled_from_is_the_ruled_instant():
    assert ENABLED_FROM.tzinfo is not None
    assert ENABLED_FROM == datetime(2026, 12, 4, 8, 0, 0, tzinfo=UTC)
    assert ENABLED_FROM == datetime(2026, 12, 4, 0, 0, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    # the opposite reading (UTC midnight, eight hours earlier) is not the constant
    assert ENABLED_FROM != datetime(2026, 12, 4, 0, 0, 0, tzinfo=UTC)


def test_check_enabled_one_second_before_refused_and_at_instant_allowed():
    with pytest.raises(NotYetEnabled):
        check_enabled(lambda: EF - ONE_S)
    assert check_enabled(lambda: EF) is None
    assert check_enabled(lambda: EF + ONE_S) is None


def test_gate_compares_instants_not_wall_clock_digits():
    la = ZoneInfo("America/Los_Angeles")
    with pytest.raises(NotYetEnabled):
        check_enabled(lambda: datetime(2026, 12, 3, 23, 59, 59, tzinfo=la))
    assert check_enabled(lambda: datetime(2026, 12, 4, 0, 0, 0, tzinfo=la)) is None
    assert check_enabled(lambda: datetime(2026, 12, 4, 17, 0, 0, tzinfo=timezone(timedelta(hours=9)))) is None


def test_naive_clock_refused_both_before_and_after_the_date():
    for naive in (datetime(2026, 12, 3, 0, 0, 0), datetime(2027, 1, 1, 0, 0, 0)):
        with pytest.raises(NaiveClock):
            check_enabled(lambda n=naive: n)


def test_log_window_before_the_instant_makes_no_read_and_no_file(tmp_path):
    w = window(0)
    folder = tmp_path / "store"
    t, b = Spy(series(tstar_ms(w))), Spy(BOOK)
    with pytest.raises(NotYetEnabled):
        log_window(folder, w, clock=lambda: EF - ONE_S, read_ticks=t, read_book=b)
    assert t.calls == [] and b.calls == []
    assert not folder.exists() and list(tmp_path.iterdir()) == []


def test_log_window_at_the_instant_is_allowed(tmp_path):
    w = window(0)
    t, b = Spy(series(tstar_ms(w))), Spy(BOOK)
    rec = log_window(tmp_path, w, clock=lambda: EF, read_ticks=t, read_book=b)
    assert len(t.calls) == 1 and len(b.calls) == 1
    assert rec["status"] == "OK" and len(lines(tmp_path)) == 1


def test_gate_comes_before_argument_checks(tmp_path):
    early = lambda: EF - ONE_S
    t, b = Spy([]), Spy(None)
    closed = Window("KXBTC15M-TEST", 0.0, 900.0, 1.0)
    with pytest.raises(NotYetEnabled):
        log_window(tmp_path, closed, clock=early, read_ticks=t, read_book=b)
    with pytest.raises(NotYetEnabled):
        log_window(tmp_path, window(0), clock=early, read_ticks=None, read_book=None)
    with pytest.raises(NotYetEnabled):
        log_close(tmp_path, closed, clock=early, read_ticks=None)
    assert t.calls == [] and b.calls == [] and list(tmp_path.iterdir()) == []


def test_every_public_reader_refuses_before_the_instant_with_zero_reader_calls(tmp_path, monkeypatch):
    opened = []
    real_open = open
    monkeypatch.setattr(L, "open", lambda *a, **k: (opened.append(a), real_open(*a, **k))[1], raising=False)
    w = window(0)
    tick_dir = tmp_path / "ticks"
    tick_dir.mkdir()
    (tick_dir / "kalshi_index_ticks-2026-12-04.jsonl").write_text(json.dumps(tick_row(tstar_ms(w))) + "\n")
    early = lambda: EF - ONE_S
    t = Spy(series(tstar_ms(w)))
    with pytest.raises(NotYetEnabled):
        log_close(tmp_path / "c", w, clock=early, read_ticks=t)
    with pytest.raises(NotYetEnabled):
        read_tick_files(tick_dir, tstar_ms(w) - 1000, tstar_ms(w), clock=early)
    with pytest.raises(NotYetEnabled):
        tick_file_reader(tick_dir, clock=early)
    assert t.calls == [] and opened == [] and not (tmp_path / "c").exists()
    # a reader made after the instant still refuses when the clock later reads before it
    clocks = iter([EF, EF - ONE_S])
    reader = tick_file_reader(tick_dir, clock=lambda: next(clocks))
    with pytest.raises(NotYetEnabled):
        reader(w, tstar_ms(w) - 1000, tstar_ms(w))
    assert opened == []
    # opposite: at the instant the adapter reads the file
    assert len(read_tick_files(tick_dir, tstar_ms(w) - 1000, tstar_ms(w), clock=lambda: EF)) == 1
    assert len(opened) == 1


def test_log_tape_gate_both_ways(tmp_path):
    w = window(0)
    spy = Spy([])
    with pytest.raises(NotYetEnabled):
        log_tape(tmp_path, w, posted=True, read_tape=spy, clock=lambda: EF - ONE_S, budget=TapeBudget(5))
    assert spy.calls == [] and list(tmp_path.iterdir()) == []
    out = log_tape(tmp_path, w, posted=True, read_tape=spy, clock=lambda: EF, budget=TapeBudget(5))
    assert len(spy.calls) == 1 and out is not None
    assert w.open_ts + 300.0 == EF.timestamp()


def test_dry_run_gate_both_ways(tmp_path):
    w = window(0)
    fx = [Fixture(w, series(tstar_ms(w)), BOOK)]
    with pytest.raises(NotYetEnabled):
        dry_run(tmp_path / "a", lambda: EF - ONE_S, fx)
    assert not (tmp_path / "a").exists()
    assert len(dry_run(tmp_path / "b", lambda: EF, fx)) == 1


# ---------------------------------------------------------------- no network, interfaces only

def test_whole_dry_run_passes_with_sockets_forbidden(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("a socket was opened")
    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    now = EF + timedelta(hours=1)
    base = now.timestamp() - 300.0
    fx = []
    for i in range(5):
        w = window(i, open_ts=base + 900.0 * i)
        fx.append(Fixture(w, series(tstar_ms(w)), BOOK))
    recs = dry_run(tmp_path, lambda: now, fx)
    assert len(recs) == 5 and len(lines(tmp_path)) == 5


def test_no_default_reader_or_folder():
    import inspect
    for fn in (log_window, log_tape, log_close, dry_run):
        assert inspect.signature(fn).parameters["folder"].default is inspect.Parameter.empty
    for fn in (log_window, log_close):
        assert inspect.signature(fn).parameters["read_ticks"].default is inspect.Parameter.empty
    assert inspect.signature(log_window).parameters["read_book"].default is inspect.Parameter.empty
    assert inspect.signature(log_tape).parameters["read_tape"].default is inspect.Parameter.empty
    for fn in (read_tick_files, tick_file_reader):
        assert inspect.signature(fn).parameters["tick_folder"].default is inspect.Parameter.empty
    # the composite's parameters are gone
    for fn in (log_window, dry_run):
        names = set(inspect.signature(fn).parameters)
        assert not (names & {"frozen_subset", "trim", "read_quotes"})


def test_composite_names_are_gone_from_the_module():
    for name in ("composite", "default_trim", "responding", "ExchangeQuote", "MIN_RESPONDING", "TRIM_FROM",
                 "frozen_subset", "SKIP_FEED", "statistics", "LMAX", "lmax"):
        assert name not in MODULE_SRC, name
        assert not hasattr(L, name), name


def test_missing_reader_is_refused_not_defaulted(tmp_path):
    w = window(0)
    with pytest.raises(TypeError):
        log_window(tmp_path, w, clock=lambda: at(w.open_ts + 300.0), read_book=Spy(BOOK))
    with pytest.raises(ValueError):
        log_window(tmp_path, w, clock=lambda: at(w.open_ts + 300.0), read_ticks=None, read_book=Spy(BOOK))
    with pytest.raises(ValueError):
        log_close(tmp_path, w, clock=lambda: at(w.close_ts + 60.0), read_ticks=None)
    assert lines(tmp_path) == []


def test_readers_called_exactly_once_each_with_the_window(tmp_path):
    w = window(3)
    rec, t, b = run(tmp_path, w)
    assert t.calls == [(w, tstar_ms(w) - 300_000, tstar_ms(w))]
    assert b.calls == [(w.window_ticker,)]


# ---------------------------------------------------------------- own store

def test_record_lands_in_the_given_folder_only(tmp_path):
    folder = tmp_path / "given"
    run(folder, window(0))
    assert [p.name for p in folder.iterdir()] == ["btc_updown_log.jsonl"]
    assert [p.name for p in tmp_path.iterdir()] == ["given"]
    got = lines(folder)
    assert len(got) == 1 and got[0]["window_ticker"] == "KXBTC15M-TEST000"


def test_append_only_and_fsync_per_record(tmp_path, monkeypatch):
    synced = []
    real = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (synced.append(fd), real(fd))[1])
    run(tmp_path, window(0))
    first = (tmp_path / L.LOG_NAME).read_bytes()
    run(tmp_path, window(1))
    both = (tmp_path / L.LOG_NAME).read_bytes()
    assert both.startswith(first) and len(both) > len(first)
    assert len(synced) == 2
    assert both.endswith(b"\n") and both.count(b"\n") == 2


def test_module_names_no_real_store_and_no_default_path():
    for forbidden in ("orderbook_loop.sqlite", "valedia-data", "memory/", "artifacts/", "state/", "journal/",
                      "config/", ".env", "Path.home", "expanduser", "~/"):
        assert forbidden not in MODULE_SRC, forbidden


# ---------------------------------------------------------------- the window record at t*

def test_record_fields_at_t_star_with_decimals_as_strings(tmp_path):
    w = window(2)
    T = tstar_ms(w)
    rec, _, _ = run(tmp_path, w)
    saved = no_float_lines(tmp_path)[0]
    assert saved == json.loads(json.dumps(rec))
    assert saved["kind"] == "window" and saved["schema_version"] == 2 == L.SCHEMA_VERSION
    assert saved["window_ticker"] == w.window_ticker
    assert saved["open_ts"] == D(w.open_ts) and saved["close_ts"] == D(w.close_ts)
    assert saved["t_star"] == D(w.open_ts + 300.0) and saved["t_star_ms"] == T
    assert saved["floor_strike"] == "100002.0" and saved["written_at"] == D(w.open_ts + 300.0)
    assert saved["index_id"] == "BRTI" and saved["channel"] == "cfbenchmarks_value"
    assert saved["status"] == "OK" and saved["skip_reasons"] == []
    used = saved["tick_used"]
    assert used == {"local_receipt_ms": None, "local_receipt_note": "not carried by the tick store row",
                    "received_at": T + 20, "sending_ts_ms": T + 25, "seq": 301, "sid": 1,
                    "cf_source_ts_ms": T, "brti_raw": "100003.00",
                    "avg_60s_data": {"value": "99999.99", "window_size": 60, "window_start_ts_ms": T + 20 - 60000,
                                     "window_end_ts_exclusive": T + 20}}
    assert isinstance(used["brti_raw"], str) and isinstance(used["avg_60s_data"]["value"], str)
    assert saved["index_lag_ms"] == 0
    assert saved["series_from_ms"] == T - 300_000 and saved["series_to_ms"] == T
    assert len(saved["series"]) == 301
    assert saved["series"][0] == {"cf_source_ts_ms": T - 300_000, "brti_raw": "100000.00"}
    assert saved["series"][-1] == {"cf_source_ts_ms": T, "brti_raw": "100003.00"}
    assert all(isinstance(p["brti_raw"], str) for p in saved["series"])
    ch = saved["series_checks"]
    assert ch["n_ticks"] == 301 and ch["count_ok"] and ch["anchor_ok"] and ch["gap_ok"] and ch["warm"]
    assert ch["anchor_ts_ms"] == T - 180_000 and ch["anchor_tick_ms"] == T - 180_000 and ch["max_gap_ms"] == 1000
    assert saved["n_rows_read"] == 301 and saved["n_rows_malformed"] == 0 and saved["n_duplicates_dropped"] == 0
    book = saved["book"]
    assert book["yes"] == [["0.49", "30.0"], ["0.48", "10.0"]] and book["no"] == [["0.5", "25.0"], ["0.49", "5.0"]]
    assert book["best_bid"] == "0.49" and book["best_bid_size"] == "30.0"
    assert book["best_ask"] == "0.5" and book["best_ask_size"] == "25.0" and book["spread"] == "0.01"


def test_the_tick_used_is_the_latest_at_or_before_t_star(tmp_path):
    w = window(0)
    T = tstar_ms(w)
    ticks = series(T - 500, n=300) + [tick_row(T + 400, "999999.00", seq=999)]     # one tick after t*: never used
    rec, _, _ = run(tmp_path, w, ticks=ticks)
    assert rec["tick_used"]["cf_source_ts_ms"] == T - 500 and rec["index_lag_ms"] == 500
    assert rec["n_rows_outside_span"] == 1 and all(p["brti_raw"] != "999999.00" for p in rec["series"])
    assert rec["status"] == "OK"


def test_avg_60s_is_never_used_as_the_price(tmp_path):
    w = window(0)
    T = tstar_ms(w)
    ticks = [tick_row(T - k * 1000, "100000.50", seq=k, avg="1.00", row_value="5") for k in range(300, -1, -1)]
    rec, _, _ = run(tmp_path, w, ticks=ticks)
    assert rec["tick_used"]["brti_raw"] == "100000.50"           # the raw data frame's value, not avg_60s, not the row
    assert rec["tick_used"]["avg_60s_data"]["value"] == "1.00"   # carried as metadata only
    assert {p["brti_raw"] for p in rec["series"]} == {"100000.50"}
    # opposite: a different data value changes brti_raw while avg_60s stays the same
    rec2, _, _ = run(tmp_path, window(1), ticks=series(tstar_ms(window(1)), base="200.00"))
    assert rec2["tick_used"]["brti_raw"] == "203.00" and rec2["tick_used"]["avg_60s_data"]["value"] == "99999.99"


def test_raw_data_value_keeps_every_digit_as_a_string(tmp_path):
    w = window(0)
    T = tstar_ms(w)
    ticks = series(T - 1000)
    ticks.append(dict(tick_row(T), data=json.dumps({"time": T, "value": "100003.123456789"})))
    rec, _, _ = run(tmp_path, w, ticks=ticks)
    assert rec["tick_used"]["brti_raw"] == "100003.123456789"


def test_record_holds_no_decision_fields(tmp_path):
    rec, _, _ = run(tmp_path, window(0))
    blob = json.dumps(rec).lower()
    for word in ("roc3", "range5", "trigger", "fill", "eligible", "gate", "q_hat", "edge", "z_dist"):
        assert word not in blob, word
    for word in ("roc3", "range5", "def trigger", "def fill", "gates"):
        assert word not in MODULE_SRC.lower(), word


# ---------------------------------------------------------------- SKIP_STALE_INDEX

def _stale_ticks(T: int, lag_ms: int) -> list[dict]:
    """A dense, warm series whose last tick is lag_ms before t*."""
    return series(T - lag_ms, n=297)


def test_stale_at_3001_ms_and_ok_at_exactly_3000(tmp_path):
    w = window(0)
    T = tstar_ms(w)
    rec, _, _ = run(tmp_path, w, ticks=_stale_ticks(T, 3001))
    assert rec["status"] == "SKIP_STALE_INDEX" and rec["skip_reasons"] == ["SKIP_STALE_INDEX"]
    assert rec["index_lag_ms"] == 3001 and rec["series_checks"]["warm"] is True
    ok, _, _ = run(tmp_path, window(0), ticks=_stale_ticks(T, 3000))
    assert ok["status"] == "OK" and ok["index_lag_ms"] == 3000


def test_stale_when_the_only_recent_tick_has_a_source_time_after_t_star(tmp_path):
    w = window(0)
    T = tstar_ms(w)
    ticks = _stale_ticks(T, 3500) + [tick_row(T + 200, seq=5000)]
    rec, _, _ = run(tmp_path, w, ticks=ticks)
    assert rec["status"] == "SKIP_STALE_INDEX"
    assert rec["tick_used"]["cf_source_ts_ms"] == T - 3500 and rec["n_rows_outside_span"] == 1
    # every tick after t*: no tick to use, stale
    rec2, _, _ = run(tmp_path, window(0), ticks=[tick_row(T + 1000 * k, seq=k) for k in range(1, 5)])
    assert rec2["tick_used"] is None and rec2["index_lag_ms"] is None
    assert rec2["skip_reasons"][0] == "SKIP_STALE_INDEX"


def test_stale_rule_refuses_a_negative_lag():
    T = 1_000_000_000
    after = L._parse_tick_row(tick_row(T + 1))
    exact = L._parse_tick_row(tick_row(T))
    warm = {"warm": True}
    assert L._status(after, T, warm, BOOK) == (-1, ["SKIP_STALE_INDEX"])
    assert L._status(exact, T, warm, BOOK) == (0, [])
    assert L._status(None, T, warm, BOOK) == (None, ["SKIP_STALE_INDEX"])


# ---------------------------------------------------------------- SKIP_WARMUP_OR_GAP

def test_warmup_269_ticks_skips_and_270_is_ok(tmp_path):
    w = window(0)
    T = tstar_ms(w)
    rec, _, _ = run(tmp_path, w, ticks=series(T, n=269))
    assert rec["status"] == "SKIP_WARMUP_OR_GAP" and rec["skip_reasons"] == ["SKIP_WARMUP_OR_GAP"]
    assert rec["series_checks"]["n_ticks"] == 269 and rec["series_checks"]["count_ok"] is False
    assert rec["series_checks"]["anchor_ok"] and rec["series_checks"]["gap_ok"]
    ok, _, _ = run(tmp_path, window(0), ticks=series(T, n=270))
    assert ok["status"] == "OK" and ok["series_checks"]["n_ticks"] == 270


def test_warmup_counts_a_repeated_source_time_once(tmp_path):
    w = window(0)
    T = tstar_ms(w)
    ticks = series(T, n=269)
    ticks.append(dict(ticks[-1]))                                # a duplicate row is not a 270th tick
    rec, _, _ = run(tmp_path, w, ticks=ticks)
    assert rec["status"] == "SKIP_WARMUP_OR_GAP" and rec["n_duplicates_dropped"] == 1


def test_no_tick_near_t_star_minus_180_skips_and_exactly_3_s_is_ok(tmp_path):
    w = window(0)
    T = tstar_ms(w)
    # half-second ticks starting 170 s before t*: plenty of ticks, no gap, none near t* - 180 s
    late_start = series(T, n=341, step_ms=500)
    rec, _, _ = run(tmp_path, w, ticks=late_start)
    ch = rec["series_checks"]
    assert ch["count_ok"] and ch["gap_ok"] and ch["anchor_ok"] is False
    assert rec["status"] == "SKIP_WARMUP_OR_GAP" and rec["skip_reasons"] == ["SKIP_WARMUP_OR_GAP"]
    # the first tick exactly 3 s after t* - 180 s: ok
    at_3s = series(T, n=355, step_ms=500)
    assert int(at_3s[0]["source_time_ms"]) == T - 177_000
    ok, _, _ = run(tmp_path, window(0), ticks=at_3s)
    assert ok["series_checks"]["anchor_tick_ms"] == T - 177_000 and ok["status"] == "OK"
    # 3.001 s: refused
    at_3001 = [r for r in series(T, n=355, step_ms=500) if r["source_time_ms"] != T - 177_000]
    at_3001.insert(0, tick_row(T - 176_999, seq=0))
    rec3, _, _ = run(tmp_path, window(0), ticks=at_3001)
    assert rec3["series_checks"]["anchor_ok"] is False and rec3["status"] == "SKIP_WARMUP_OR_GAP"


def _with_gap(T: int, gap_ms: int) -> list[dict]:
    """Full 1 Hz series with one gap of gap_ms, starting 100 s before t*."""
    rows = [r for r in series(T) if not (T - 100_000 < r["source_time_ms"] <= T - 100_000 + gap_ms)]
    rows.append(tick_row(T - 100_000 + gap_ms, seq=9999))
    return sorted(rows, key=lambda r: r["source_time_ms"])


def test_gap_of_5001_ms_skips_and_5000_is_ok(tmp_path):
    w = window(0)
    T = tstar_ms(w)
    rec, _, _ = run(tmp_path, w, ticks=_with_gap(T, 5001))
    assert rec["series_checks"]["max_gap_ms"] == 5001 and rec["series_checks"]["gap_ok"] is False
    assert rec["series_checks"]["count_ok"] and rec["series_checks"]["anchor_ok"]
    assert rec["status"] == "SKIP_WARMUP_OR_GAP" and rec["skip_reasons"] == ["SKIP_WARMUP_OR_GAP"]
    ok, _, _ = run(tmp_path, window(0), ticks=_with_gap(T, 5000))
    assert ok["series_checks"]["max_gap_ms"] == 5000 and ok["status"] == "OK"


# ---------------------------------------------------------------- the status order

def test_status_order_when_two_checks_fail(tmp_path):
    w = window(0)
    T = tstar_ms(w)
    stale_and_cold = series(T - 4000, n=100)
    rec, _, _ = run(tmp_path, w, ticks=stale_and_cold)
    assert rec["status"] == "SKIP_STALE_INDEX"
    assert rec["skip_reasons"] == ["SKIP_STALE_INDEX", "SKIP_WARMUP_OR_GAP"]
    rec2, _, _ = run(tmp_path, window(0), ticks=stale_and_cold, book=None)
    assert rec2["skip_reasons"] == ["SKIP_STALE_INDEX", "SKIP_WARMUP_OR_GAP", "SKIP_NO_BOOK"]
    rec3, _, _ = run(tmp_path, window(0), ticks=series(T, n=100), book=None)
    assert rec3["status"] == "SKIP_WARMUP_OR_GAP" and rec3["skip_reasons"] == ["SKIP_WARMUP_OR_GAP", "SKIP_NO_BOOK"]


def test_twenty_windows_one_record_each_skips_included(tmp_path):
    base = (EF + timedelta(hours=3)).timestamp()
    expect = {}
    for i in range(20):
        w = window(i, open_ts=base + 900.0 * i)
        T = tstar_ms(w)
        ticks, book = series(T), BOOK
        if i == 5:
            ticks = _stale_ticks(T, 4000)
            expect[i] = "SKIP_STALE_INDEX"
        elif i == 7:
            ticks = series(T, n=50)
            expect[i] = "SKIP_WARMUP_OR_GAP"
        elif i == 9:
            book = None
            expect[i] = "SKIP_NO_BOOK"
        else:
            expect[i] = "OK"
        run(tmp_path, w, ticks=ticks, book=book)
    got = lines(tmp_path)
    assert len(got) == 20
    assert [r["window_ticker"] for r in got] == [f"KXBTC15M-TEST{i:03d}" for i in range(20)]
    assert [r["status"] for r in got] == [expect[i] for i in range(20)]
    assert all(Decimal(r["written_at"]) < Decimal(r["close_ts"]) for r in got)
    assert got[9]["book"] is None and got[9]["tick_used"] is not None


def test_late_window_is_refused_before_any_read(tmp_path):
    w = window(0)
    t, b = Spy(series(tstar_ms(w))), Spy(BOOK)
    with pytest.raises(LateWindow):
        log_window(tmp_path, w, clock=lambda: at(w.close_ts), read_ticks=t, read_book=b)
    assert t.calls == [] and b.calls == [] and lines(tmp_path) == []


def test_window_that_closes_during_the_reads_is_refused_unwritten(tmp_path):
    w = window(0)
    now = [w.open_ts + 300.0]

    def slow_ticks(win, a, b):
        now[0] = w.close_ts + 1.0
        return series(tstar_ms(w))

    with pytest.raises(LateWindow):
        log_window(tmp_path, w, clock=lambda: at(now[0]), read_ticks=slow_ticks, read_book=Spy(BOOK))
    assert lines(tmp_path) == []
    now[0] = w.open_ts + 300.0

    def quick_ticks(win, a, b):
        now[0] = w.close_ts - 1.0
        return series(tstar_ms(w))

    rec = log_window(tmp_path, w, clock=lambda: at(now[0]), read_ticks=quick_ticks, read_book=Spy(BOOK))
    assert rec["written_at"] == D(w.close_ts - 1.0) and len(lines(tmp_path)) == 1


def test_reader_errors_are_logged_not_lost(tmp_path):
    w = window(0)

    def bad_ticks(win, a, b):
        raise RuntimeError("ticks down")

    def bad_book(ticker):
        raise RuntimeError("book down")

    rec = log_window(tmp_path, w, clock=lambda: at(w.open_ts + 300.0), read_ticks=bad_ticks, read_book=bad_book)
    assert rec["status"] == "SKIP_STALE_INDEX"
    assert rec["skip_reasons"] == ["SKIP_STALE_INDEX", "SKIP_WARMUP_OR_GAP", "SKIP_NO_BOOK"]
    assert "ticks down" in rec["tick_error"] and "book down" in rec["book_error"]
    assert len(lines(tmp_path)) == 1
    ok, _, _ = run(tmp_path, window(1))
    assert ok["tick_error"] is None and ok["book_error"] is None


def test_non_finite_values_never_reach_the_file(tmp_path):
    w = Window("KXBTC15M-TESTNAN", window(0).open_ts, window(0).close_ts, float("nan"))
    T = tstar_ms(w)
    ticks = series(T)
    ticks[-1] = dict(ticks[-1], data=json.dumps({"time": T, "value": "NaN"}))
    ticks[-2] = dict(ticks[-2], data=json.dumps({"time": T - 1000, "value": "Infinity"}))
    rec, _, _ = run(tmp_path, w, ticks=ticks, book={"yes": [(float("nan"), 1.0)], "no": []})
    text = (tmp_path / L.LOG_NAME).read_text()
    assert "NaN" not in text and "Infinity" not in text
    json.loads(text)
    assert rec["floor_strike"] is None and rec["n_rows_malformed"] == 2
    assert rec["tick_used"]["cf_source_ts_ms"] == T - 2000
    assert rec["book"] is None and rec["book_error"] is not None


@pytest.mark.parametrize("odd", [
    "not a row", None, [], {}, {"index_id": "BRTI", "channel": "cfbenchmarks_value"},
    "other_index", "fivehz", "data_not_json", "data_list", "float_time", "bool_seq", "zero_value",
    "float_received", "bad_avg",
])
def test_odd_tick_row_is_counted_not_lost(tmp_path, odd):
    w = window(0)
    T = tstar_ms(w)
    good = tick_row(T)
    bad = {
        "other_index": tick_row(T, index_id="ETHUSD_RTI"),
        "fivehz": tick_row(T, channel="cfbenchmarks_value_5hz"),
        "data_not_json": dict(good, data="{nope"),
        "data_list": dict(good, data="[1, 2]"),
        "float_time": dict(good, data='{"time": 1.5, "value": "1"}'),
        "bool_seq": dict(good, seq=True),
        "zero_value": dict(good, data=json.dumps({"time": T, "value": "0"})),
        "float_received": dict(good, received_at_ms=float(T)),
        "bad_avg": dict(good, avg_60s={"value": 1.5, "window_size": 60, "window_start_ts_ms": 1,
                                       "window_end_ts_exclusive": 2}),
    }.get(odd, odd) if isinstance(odd, str) else odd
    rec, _, _ = run(tmp_path, w, ticks=series(T - 1000) + [bad])
    saved = lines(tmp_path)
    assert len(saved) == 1 and saved[0]["n_rows_malformed"] == 1
    assert saved[0]["tick_used"]["cf_source_ts_ms"] == T - 1000 and saved[0]["status"] == "OK"
    # opposite: the well-formed row for the same instant is used
    rec2, _, _ = run(tmp_path, window(0), ticks=series(T - 1000) + [good])
    assert rec2["tick_used"]["cf_source_ts_ms"] == T and rec2["n_rows_malformed"] == 0


def test_local_receipt_time_is_logged_when_the_row_carries_one(tmp_path):
    w = window(0)
    T = tstar_ms(w)
    ticks = series(T - 1000) + [dict(tick_row(T), local_receipt_ms=T + 77)]
    rec, _, _ = run(tmp_path, w, ticks=ticks)
    assert rec["tick_used"]["local_receipt_ms"] == T + 77 and rec["tick_used"]["local_receipt_note"] is None


def test_book_with_one_empty_side(tmp_path):
    rec, _, _ = run(tmp_path, window(0), book={"yes": [(0.30, 4.0)], "no": []})
    b = rec["book"]
    assert b["best_bid"] == "0.3" and b["best_ask"] is None and b["spread"] is None and b["best_ask_size"] is None
    assert rec["status"] == "OK"
    rec2, _, _ = run(tmp_path, window(1), book={"yes": [], "no": []})
    assert rec2["book"]["best_bid"] is None and rec2["status"] == "OK"


def test_book_order_does_not_matter(tmp_path):
    shuffled = {"yes": [(0.48, 10.0), (0.49, 30.0)], "no": [(0.49, 5.0), (0.50, 25.0)]}
    a, _, _ = run(tmp_path, window(0), book=BOOK)
    b, _, _ = run(tmp_path, window(0), book=shuffled)
    assert a["book"]["best_bid"] == b["book"]["best_bid"] == "0.49"
    assert a["book"]["best_ask"] == b["book"]["best_ask"] == "0.5"
    assert a["book"]["best_ask_size"] == b["book"]["best_ask_size"] == "25.0"


def test_best_ask_is_one_minus_the_best_no_bid_on_an_asymmetric_book(tmp_path):
    rec, _, _ = run(tmp_path, window(0), book={"yes": [(0.55, 3.0)], "no": [(0.40, 5.0), (0.30, 9.0)]})
    b = rec["book"]
    assert b["best_bid"] == "0.55" and b["best_ask"] == "0.6" and b["best_ask_size"] == "5.0" and b["spread"] == "0.05"
    assert b["best_ask"] != "0.4"
    rec2, _, _ = run(tmp_path, window(1), book={"yes": [(0.55, 3.0)], "no": [(0.45, 5.0)]})
    assert rec2["book"]["best_ask"] == "0.55" and rec2["book"]["spread"] == "0.00"


# ---------------------------------------------------------------- the close record

def _close_rows(w: Window, value: str | None, *, size: int = 60) -> list[dict]:
    """Ticks around the close; the ones from the close on carry the 15-minute windowed average."""
    C = int(round(w.close_ts * 1000))
    rows = []
    for k in range(-30, 6):
        src = C + 1000 * k
        last15 = None
        if value is not None and k >= 0:
            last15 = {"value": value if k == 0 else "1.00", "window_size": size,
                      "window_start_ts_ms": C - 60000, "window_end_ts_exclusive": C}
        rows.append(tick_row(src, seq=k + 100, last15=last15))
    return rows


def test_close_estimate_rounds_half_even_on_a_half_cent(tmp_path):
    w = window(0)
    after = lambda: at(w.close_ts + 15.0)
    for i, (raw, want) in enumerate((("100000.125", "100000.12"), ("100000.135", "100000.14"),
                                     ("100000.1251", "100000.13"), ("99999.995", "100000.00"))):
        rec = log_close(tmp_path / str(i), w, clock=after, read_ticks=Spy(_close_rows(w, raw)))
        assert rec["settlement_estimate"] == want, raw
        assert rec["settlement_estimate_reason"] is None and rec["rounding"] == "2 dp, ROUND_HALF_EVEN"
        assert rec["close_tick"]["last_60s_windowed_average_15min"]["value"] == raw
        assert rec["close_tick"]["cf_source_ts_ms"] == int(w.close_ts * 1000)     # the first tick with size 60
    saved = no_float_lines(tmp_path / "0")[0]
    assert saved["kind"] == "close" and saved["schema_version"] == 2 and saved["settlement_estimate"] == "100000.12"
    assert saved["n_close_candidates"] == 6 and saved["close_candidates_agree"] is False
    assert "Kalshi settled market" in saved["outcome_source"]


def test_close_estimate_absent_gives_none_and_a_reason(tmp_path):
    w = window(0)
    after = lambda: at(w.close_ts + 15.0)
    none_at_all = log_close(tmp_path / "a", w, clock=after, read_ticks=Spy(_close_rows(w, None)))
    assert none_at_all["settlement_estimate"] is None and none_at_all["settlement_estimate_reason"] == "no_close_tick"
    assert none_at_all["close_tick"] is None
    short = log_close(tmp_path / "b", w, clock=after, read_ticks=Spy(_close_rows(w, "100000.125", size=59)))
    assert short["settlement_estimate"] is None and short["settlement_estimate_reason"] == "no_close_tick"

    def broken(win, a, b):
        raise RuntimeError("store unreadable")
    err = log_close(tmp_path / "c", w, clock=after, read_ticks=broken)
    assert err["settlement_estimate"] is None and err["settlement_estimate_reason"] == "tick_reader_error"
    assert "store unreadable" in err["tick_error"]
    assert len(lines(tmp_path / "a")) == len(lines(tmp_path / "b")) == len(lines(tmp_path / "c")) == 1


def test_close_record_asks_for_its_span_and_waits_for_it(tmp_path):
    w = window(0)
    C = int(round(w.close_ts * 1000))
    spy = Spy(_close_rows(w, "100.00"))
    with pytest.raises(CloseNotReached):
        log_close(tmp_path, w, clock=lambda: at(w.close_ts + 9.999), read_ticks=spy)
    assert spy.calls == [] and lines(tmp_path) == []
    log_close(tmp_path, w, clock=lambda: at(w.close_ts + 10.0), read_ticks=spy)
    assert spy.calls == [(w, C - 60_000, C + 10_000)]


# ---------------------------------------------------------------- the tick-file adapter

def _write(folder: Path, day: str, items: list) -> None:
    with open(folder / f"kalshi_index_ticks-{day}.jsonl", "a", encoding="utf-8") as fh:
        for it in items:
            fh.write((it if isinstance(it, str) else json.dumps(it, separators=(",", ":"))) + "\n")


def test_adapter_reads_across_a_utc_midnight_file_boundary(tmp_path):
    M = int(datetime(2026, 12, 5, 0, 0, 0, tzinfo=UTC).timestamp() * 1000)
    before = [tick_row(M - 3000 + 1000 * k, seq=k) for k in range(3)]                     # received on 12-04
    late = tick_row(M - 100, seq=3, recv=M + 50)                                           # sourced 12-04, filed 12-05
    after = [tick_row(M + 1000 * k, seq=4 + k) for k in range(3)]                          # received on 12-05
    _write(tmp_path, "2026-12-04", before)
    _write(tmp_path, "2026-12-05", [late] + after)
    _write(tmp_path, "2026-12-06", [tick_row(M + 86_400_000, seq=99)])                     # outside the span
    clk = lambda: at(M / 1000 + 3600)
    rows = read_tick_files(tmp_path, M - 3000, M + 2000, clock=clk)
    assert [r["seq"] for r in rows] == [0, 1, 2, 3, 4, 5, 6]
    assert read_tick_files(tmp_path, M - 3000, M - 1001, clock=clk) == before[:2]
    # the reader form hands the same rows to log_window's signature
    reader = tick_file_reader(tmp_path, clock=clk)
    assert reader(window(0), M - 3000, M + 2000) == rows


def test_adapter_skips_malformed_lines(tmp_path):
    M = int(datetime(2026, 12, 5, 0, 0, 0, tzinfo=UTC).timestamp() * 1000)
    good = [tick_row(M + 1000 * k, seq=k) for k in range(3)]
    _write(tmp_path, "2026-12-05", [good[0], "{truncated", "", "[1,2]", "null", good[1],
                                    dict(good[1], received_at_ms=1.5e12), dict(good[1], data="nope"),
                                    "\x00\x01 binary", good[2]])
    rows = read_tick_files(tmp_path, M, M + 5000, clock=lambda: at(M / 1000 + 60))
    assert [r["seq"] for r in rows] == [0, 1, 2]
    assert read_tick_files(tmp_path / "missing", M, M + 5000, clock=lambda: at(M / 1000 + 60)) == []


def test_adapter_refuses_a_bad_span_and_a_relative_folder(tmp_path):
    clk = lambda: EF
    for a, b in ((5, 4), (1.0, 2), ("1", "2"), (True, 2)):
        with pytest.raises(ValueError):
            read_tick_files(tmp_path, a, b, clock=clk)
    for bad in ("ticks", "", REPO):
        with pytest.raises(ValueError):
            read_tick_files(bad, 1, 2, clock=clk)
        with pytest.raises(ValueError):
            tick_file_reader(bad, clock=clk)


def test_adapter_reads_what_the_index_client_writes(tmp_path):
    kis = pytest.importorskip("valedia_trader.venues.kalshi_index_ws")
    assert L.TICK_FILE_PREFIX == kis.STORE_PREFIX and L.INDEX_CHANNEL == kis.CHANNEL_VALUE
    w = window(0)
    T = tstar_ms(w)
    store = kis.TickStore(tmp_path)
    for k in range(300, -1, -1):
        src = T - 1000 * k
        frame = {"type": "cfbenchmarks_value", "sid": 1, "seq": 301 - k, "sending_ts_ms": src + 30,
                 "msg": {"index_id": "BRTI", "received_at": src + 25,
                         "data": json.dumps({"type": "value", "id": "BRTI", "time": src,
                                             "value": str(Decimal("100000.00") + Decimal(k))}),
                         "avg_60s_data": {"value": "1.00", "window_size": 60,
                                          "window_start_ts_ms": src - 60000, "window_end_ts_exclusive": src}}}
        store.append(kis.parse_value_message(frame), "cfbenchmarks_value")
    out = tmp_path / "log"
    clk = lambda: at(w.open_ts + 301.0)
    rec = log_window(out, w, clock=clk, read_ticks=tick_file_reader(tmp_path, clock=clk), read_book=Spy(BOOK))
    assert rec["status"] == "OK" and rec["series_checks"]["n_ticks"] == 301
    assert rec["tick_used"]["brti_raw"] == "100000.00" and rec["tick_used"]["received_at"] == T + 25
    assert rec["tick_used"]["sending_ts_ms"] == T + 30 and rec["tick_used"]["seq"] == 301
    no_float_lines(out)


# ---------------------------------------------------------------- no float anywhere

def test_no_float_in_any_written_record(tmp_path):
    w = window(0)
    folder = tmp_path / "all"
    run(folder, w)
    run(folder, window(1), ticks=[], book=None)
    log_close(folder, w, clock=lambda: at(w.close_ts + 15.0), read_ticks=Spy(_close_rows(w, "100000.125")))
    log_close(folder, w, clock=lambda: at(w.close_ts + 15.0), read_ticks=Spy([]))
    tape = [{"trade_id": "t1", "created_ts": w.open_ts + 301.5, "yes_price": 0.51, "count": 3.0}]
    log_tape(folder, w, posted=True, read_tape=Spy(tape), clock=lambda: at(w.open_ts + 400.0), budget=TapeBudget(1))
    recs = no_float_lines(folder)
    assert [r["kind"] for r in recs] == ["window", "window", "close", "close", "tape"]
    assert recs[4]["rows"][0]["yes_price"] == "0.51" and recs[4]["rows"][0]["created_ts"] == D(w.open_ts + 301.5)


# ---------------------------------------------------------------- imports, no model

def _imports(src: str) -> set[str]:
    out = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            out.add(("." * node.level) + (node.module or ""))
    return out


def test_import_discipline():
    imports = _imports(MODULE_SRC)
    roots = {i.split(".")[0] for i in imports}
    assert roots <= {"__future__", "collections", "dataclasses", "datetime", "decimal", "json", "math", "os",
                     "pathlib", "typing"}, roots
    forbidden = {"urllib", "http", "socket", "requests", "subprocess", "ssl", "asyncio", "valedia_trader",
                 "anthropic", "openai", "websockets"}
    assert not (roots & forbidden)
    for name in ("orderbook_loop", "second_quote", "execution_and_risk", "kalshi_data", "kalshi_index_ws", "webull",
                 "decision_model", "hermes", "agents", "legacy"):
        assert not any(name in i for i in imports), name


def test_the_checker_catches_a_forbidden_import():
    assert "urllib.request" in _imports("import urllib.request\n")
    assert "valedia_trader.research_loop.orderbook_loop" in _imports(
        "from valedia_trader.research_loop import orderbook_loop\nfrom valedia_trader.research_loop.orderbook_loop import x\n")
    assert _imports("import json\n") == {"json"}


def test_no_model_words_in_the_module():
    low = MODULE_SRC.lower()
    for word in ("anthropic", "openai", "hermes", "decision_model", "llm", "prompt", "gateway"):
        assert word not in low, word


def test_pinned_package_init_does_not_mention_the_module_and_no_pin_imports_it():
    init = (REPO / "valedia_trader" / "research_loop" / "__init__.py").read_text()
    assert "btc_updown_logger" not in init
    top = (REPO / "valedia_trader" / "__init__.py").read_text()
    assert "btc_updown_logger" not in top
    cli = (REPO / "valedia_trader" / "research_loop" / "cli.py").read_text()
    assert "btc_updown_logger" not in cli


def test_importing_the_module_loads_no_collector_or_network_module():
    code = ("import sys\n"
            "import valedia_trader.research_loop.btc_updown_logger\n"
            "bad = [m for m in sys.modules if m.startswith('valedia_trader.') and any(k in m for k in "
            "('orderbook_loop','second_quote','kalshi_data','kalshi_index_ws','webull','execution_and_risk',"
            "'agents','urllib'))]\n"
            "bad += [m for m in ('urllib.request','http.client','requests','websockets') if m in sys.modules]\n"
            "print(sorted(bad))\n")
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(REPO)}
    out = subprocess.run([sys.executable, "-c", code], cwd=str(REPO), env=env, capture_output=True, text=True,
                         timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]", out.stdout


# ---------------------------------------------------------------- tape

def _rows(w: Window):
    t = w.open_ts + 300.0

    def row(i, ts):
        return {"trade_id": f"t{i}", "ticker": w.window_ticker, "created_time": "x", "created_ts": ts,
                "yes_price": 0.5, "no_price": 0.5, "count": 3.0, "taker_side": "yes", "is_block_trade": False}
    return [row(0, t - 1.0), row(1, t), row(2, t + 30.0), row(3, t + 60.0), row(4, t + 60.5)]


def test_tape_rows_kept_only_inside_the_sixty_seconds(tmp_path):
    w = window(0)
    spy = Spy(_rows(w))
    out = log_tape(tmp_path, w, posted=True, read_tape=spy, clock=lambda: at(w.open_ts + 400.0), budget=TapeBudget(3))
    assert spy.calls == [(w, w.open_ts + 300.0, w.open_ts + 360.0)]
    assert [r["trade_id"] for r in out["rows"]] == ["t1", "t2", "t3"]
    saved = lines(tmp_path)
    assert len(saved) == 1 and saved[0]["kind"] == "tape" and saved[0]["window_ticker"] == w.window_ticker
    assert saved[0]["t_from"] == D(w.open_ts + 300.0) and saved[0]["t_to"] == D(w.open_ts + 360.0)
    assert saved[0]["written_at"] == D(w.open_ts + 400.0)
    assert saved[0]["n_rows"] == 3 and [p.name for p in tmp_path.iterdir()] == [L.LOG_NAME]


def test_tape_not_posted_makes_no_read_and_spends_no_budget(tmp_path):
    w = window(0)
    spy = Spy(_rows(w))
    budget = TapeBudget(1)
    assert log_tape(tmp_path, w, posted=False, read_tape=spy, clock=lambda: at(w.open_ts + 400.0),
                    budget=budget) is None
    assert spy.calls == [] and budget.used == 0 and lines(tmp_path) == []
    assert log_tape(tmp_path, w, posted=True, read_tape=spy, clock=lambda: at(w.open_ts + 400.0),
                    budget=budget) is not None
    assert len(spy.calls) == 1 and budget.used == 1


def test_tape_budget_refuses_the_next_read(tmp_path):
    spy = Spy([])
    budget = TapeBudget(2)
    for i in range(2):
        w = window(i)
        log_tape(tmp_path, w, posted=True, read_tape=spy, clock=lambda w=w: at(w.open_ts + 400.0), budget=budget)
    assert len(spy.calls) == 2 and budget.used == 2
    w = window(2)
    with pytest.raises(TapeBudgetExceeded):
        log_tape(tmp_path, w, posted=True, read_tape=spy, clock=lambda: at(w.open_ts + 400.0), budget=budget)
    assert len(spy.calls) == 2 and len(lines(tmp_path)) == 2


def test_tape_budget_zero_refuses_the_first_read(tmp_path):
    spy = Spy([])
    w = window(0)
    with pytest.raises(TapeBudgetExceeded):
        log_tape(tmp_path, w, posted=True, read_tape=spy, clock=lambda: at(w.open_ts + 400.0), budget=TapeBudget(0))
    assert spy.calls == []
    with pytest.raises(ValueError):
        TapeBudget(-1)


def test_tape_after_close_is_refused_unread(tmp_path):
    w = window(0)
    spy = Spy([])
    budget = TapeBudget(5)
    with pytest.raises(LateWindow):
        log_tape(tmp_path, w, posted=True, read_tape=spy, clock=lambda: at(w.close_ts), budget=budget)
    assert spy.calls == [] and budget.used == 0 and lines(tmp_path) == []


def test_tape_read_that_ends_after_close_is_not_written(tmp_path):
    w = window(0)
    now = [w.open_ts + 400.0]

    def slow(win, a, b):
        now[0] = w.close_ts + 5.0
        return []

    with pytest.raises(LateWindow):
        log_tape(tmp_path, w, posted=True, read_tape=slow, clock=lambda: at(now[0]), budget=TapeBudget(2))
    assert lines(tmp_path) == []


# ---------------------------------------------------------------- dry run (fixtures of ticks)

def _fixtures(now: datetime, n: int = 20):
    base = now.timestamp() - 300.0
    out = []
    for i in range(n):
        w = window(i, open_ts=base + 900.0 * i)
        T = tstar_ms(w)
        if i == 4:
            out.append(Fixture(w, _stale_ticks(T, 3500), BOOK))
        elif i == 7:
            out.append(Fixture(w, series(T, base=str(100000 + i)), None))
        else:
            out.append(Fixture(w, series(T, base=str(100000 + i)), BOOK))
    return out


def test_dry_run_one_record_per_window_no_sockets_no_extra_files(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(socket, "socket", lambda *a, **k: opened.append(a) or (_ for _ in ()).throw(OSError("no")))
    now = EF + timedelta(days=1)
    recs = dry_run(tmp_path, lambda: now, _fixtures(now))
    assert len(recs) == 20 and len(lines(tmp_path)) == 20
    assert opened == []
    statuses = [r["status"] for r in recs]
    assert statuses.count("SKIP_STALE_INDEX") == 1 and statuses.count("SKIP_NO_BOOK") == 1
    assert statuses.count("OK") == 18
    assert [p.name for p in tmp_path.iterdir()] == [L.LOG_NAME]
    no_float_lines(tmp_path)


def test_dry_run_is_deterministic_byte_for_byte(tmp_path):
    now = EF + timedelta(days=1)
    dry_run(tmp_path / "one", lambda: now, _fixtures(now))
    dry_run(tmp_path / "two", lambda: now, _fixtures(now))
    a = (tmp_path / "one" / L.LOG_NAME).read_bytes()
    b = (tmp_path / "two" / L.LOG_NAME).read_bytes()
    assert a == b and len(a) > 0
    other = _fixtures(now)
    other[0] = Fixture(other[0].window, series(tstar_ms(other[0].window), base="5.00"), BOOK)
    dry_run(tmp_path / "three", lambda: now, other)
    assert (tmp_path / "three" / L.LOG_NAME).read_bytes() != a


def test_dry_run_takes_no_url_or_fetch():
    import inspect
    names = set(inspect.signature(dry_run).parameters)
    assert not (names & {"url", "fetch", "base_url", "headers", "timeout"})


def test_dry_run_into_a_folder_with_old_records_is_refused(tmp_path):
    now = EF + timedelta(days=1)
    dry_run(tmp_path, lambda: now, _fixtures(now, 2))
    with pytest.raises(ValueError):
        dry_run(tmp_path, lambda: now, _fixtures(now, 2))
    assert len(lines(tmp_path)) == 2


# ---------------------------------------------------------------- no schedule

def test_no_schedule_or_daemon_words_in_the_module():
    low = MODULE_SRC.lower()
    for word in (".plist", "launchctl", "crontab", "schedule", "__main__", "while true", "sleep(", "daemon",
                 "argparse", "threading"):
        assert word not in low, word


def test_no_cli_registration():
    cli = (REPO / "valedia_trader" / "research_loop" / "cli.py").read_text()
    assert "btc_updown" not in cli


# ---------------------------------------------------------------- clock readings

class SeqClock:
    """A clock that returns the given instants in order (the last one forever) and counts its readings."""
    def __init__(self, instants):
        self.instants, self.reads = list(instants), 0

    def __call__(self):
        i = min(self.reads, len(self.instants) - 1)
        self.reads += 1
        return self.instants[i]


def _lw(folder, w, clk):
    return log_window(folder, w, clock=clk, read_ticks=Spy(series(tstar_ms(w))), read_book=Spy(BOOK))


def test_log_window_stamps_written_at_from_the_reading_it_gated_on(tmp_path):
    clk = SeqClock([EF, EF, EF, EF - ONE_S])
    rec = _lw(tmp_path, window(0), clk)
    assert rec["written_at"] == D(EF.timestamp())
    clk2 = SeqClock([EF, EF + ONE_S])
    rec2 = _lw(tmp_path, window(1), clk2)
    assert rec2["written_at"] == D((EF + ONE_S).timestamp())


def test_log_window_refuses_when_the_clock_steps_back_before_the_instant_at_write_time(tmp_path):
    with pytest.raises(NotYetEnabled):
        _lw(tmp_path, window(0), SeqClock([EF, EF - ONE_S, EF, EF]))
    assert lines(tmp_path) == []


def test_log_window_reads_the_clock_exactly_twice(tmp_path):
    clk = SeqClock([EF, EF])
    _lw(tmp_path, window(0), clk)
    assert clk.reads == 2


def test_log_close_refuses_when_the_clock_steps_back_before_the_instant_at_write_time(tmp_path):
    w = window(0)
    after = at(w.close_ts + 15.0)
    with pytest.raises(NotYetEnabled):
        log_close(tmp_path, w, clock=SeqClock([after, EF - ONE_S]), read_ticks=Spy(_close_rows(w, "1.00")))
    assert lines(tmp_path) == []
    clk = SeqClock([after, after])
    log_close(tmp_path, w, clock=clk, read_ticks=Spy(_close_rows(w, "1.00")))
    assert clk.reads == 2 and len(lines(tmp_path)) == 1


def test_log_tape_stamps_written_at_from_the_reading_it_gated_on(tmp_path):
    w = window(0)
    clk = SeqClock([EF, EF, EF, EF - ONE_S])
    out = log_tape(tmp_path, w, posted=True, read_tape=Spy([]), clock=clk, budget=TapeBudget(2))
    assert out["written_at"] == D(EF.timestamp()) and lines(tmp_path)[0]["written_at"] == D(EF.timestamp())
    clk2 = SeqClock([EF, EF - ONE_S, EF, EF])
    with pytest.raises(NotYetEnabled):
        log_tape(tmp_path / "b", w, posted=True, read_tape=Spy([]), clock=clk2, budget=TapeBudget(2))
    assert lines(tmp_path / "b") == []
    clk3 = SeqClock([EF, EF])
    log_tape(tmp_path / "c", w, posted=True, read_tape=Spy([]), clock=clk3, budget=TapeBudget(2))
    assert clk3.reads == 2


def test_window_finishing_at_exactly_close_is_refused_unwritten(tmp_path):
    w = window(0)
    with pytest.raises(LateWindow):
        _lw(tmp_path, w, SeqClock([at(w.open_ts + 300.0), at(w.close_ts)]))
    assert lines(tmp_path) == []
    rec = _lw(tmp_path, w, SeqClock([at(w.open_ts + 300.0), at(w.close_ts - 1.0)]))
    assert rec["written_at"] == D(w.close_ts - 1.0)


def test_tape_finishing_at_exactly_close_is_refused_unwritten(tmp_path):
    w = window(0)
    clk = SeqClock([at(w.open_ts + 400.0), at(w.close_ts)])
    with pytest.raises(LateWindow):
        log_tape(tmp_path, w, posted=True, read_tape=Spy([]), clock=clk, budget=TapeBudget(2))
    assert lines(tmp_path) == []
    clk2 = SeqClock([at(w.open_ts + 400.0), at(w.close_ts - 1.0)])
    assert log_tape(tmp_path, w, posted=True, read_tape=Spy([]), clock=clk2, budget=TapeBudget(2)) is not None


# ---------------------------------------------------------------- the store folder rule

@pytest.mark.parametrize("bad", ["", ".", Path(""), Path(".")])
def test_empty_or_current_directory_folder_is_refused_after_the_date(tmp_path, monkeypatch, bad):
    monkeypatch.chdir(tmp_path)
    w = window(0)
    t, b = Spy(series(tstar_ms(w))), Spy(BOOK)
    with pytest.raises(ValueError):
        log_window(bad, w, clock=lambda: at(w.open_ts + 300.0), read_ticks=t, read_book=b)
    with pytest.raises(ValueError):
        log_tape(bad, w, posted=True, read_tape=Spy([]), clock=lambda: at(w.open_ts + 400.0), budget=TapeBudget(2))
    with pytest.raises(ValueError):
        log_close(bad, w, clock=lambda: at(w.close_ts + 15.0), read_ticks=t)
    with pytest.raises(ValueError):
        dry_run(bad, lambda: EF, [Fixture(w, series(tstar_ms(w)), BOOK)])
    assert t.calls == [] and b.calls == [] and list(tmp_path.iterdir()) == []
    with pytest.raises(NotYetEnabled):
        log_window(bad, w, clock=lambda: EF - ONE_S, read_ticks=t, read_book=b)
    log_window(tmp_path / "named", w, clock=lambda: at(w.open_ts + 300.0), read_ticks=t, read_book=b)
    assert len(lines(tmp_path / "named")) == 1


class _Liar(datetime):
    """A datetime whose comparison methods lie. `lie_early` True: it claims to be earlier than anything; False: later."""
    lie_early = False

    def __lt__(self, other):
        return self.lie_early

    def __le__(self, other):
        return self.lie_early

    def __gt__(self, other):
        return not self.lie_early

    def __ge__(self, other):
        return not self.lie_early


def _liar(ts: float, *, lie_early: bool) -> datetime:
    cls = type("Liar", (_Liar,), {"lie_early": lie_early})
    return cls.fromtimestamp(ts, tz=UTC)


@pytest.fixture
def writes(monkeypatch):
    """Record every call to the module's writer and perform none, so a refused-folder test can never write."""
    seen = []
    monkeypatch.setattr(L, "_append", lambda folder, record: seen.append(Path(folder)))
    return seen


def test_liar_clock_before_the_instant_is_refused_everywhere(tmp_path):
    early = _liar(EF.timestamp() - 1.0, lie_early=False)
    assert isinstance(early, datetime) and early.timestamp() < EF.timestamp()
    w = window(0)
    t, b, tp = Spy(series(tstar_ms(w))), Spy(BOOK), Spy([])
    with pytest.raises(NotYetEnabled):
        log_window(tmp_path / "w", w, clock=lambda: early, read_ticks=t, read_book=b)
    with pytest.raises(NotYetEnabled):
        log_tape(tmp_path / "t", w, posted=True, read_tape=tp, clock=lambda: early, budget=TapeBudget(3))
    with pytest.raises(NotYetEnabled):
        log_close(tmp_path / "c", w, clock=lambda: early, read_ticks=t)
    with pytest.raises(NotYetEnabled):
        dry_run(tmp_path / "d", lambda: early, [Fixture(w, series(tstar_ms(w)), BOOK)])
    with pytest.raises(NotYetEnabled):
        check_enabled(lambda: early)
    assert t.calls == [] and b.calls == [] and tp.calls == []
    assert list(tmp_path.iterdir()) == []


def test_liar_clock_at_the_instant_is_allowed_everywhere(tmp_path):
    at_instant = _liar(EF.timestamp(), lie_early=True)
    w = window(0)
    t, b, tp = Spy(series(tstar_ms(w))), Spy(BOOK), Spy([])
    rec = log_window(tmp_path / "w", w, clock=lambda: at_instant, read_ticks=t, read_book=b)
    assert rec["status"] == "OK" and len(t.calls) == 1 and len(b.calls) == 1
    assert log_tape(tmp_path / "t", w, posted=True, read_tape=tp, clock=lambda: at_instant,
                    budget=TapeBudget(3)) is not None and len(tp.calls) == 1
    assert len(dry_run(tmp_path / "d", lambda: at_instant, [Fixture(w, series(tstar_ms(w)), BOOK)])) == 1
    assert check_enabled(lambda: at_instant) is None


def test_dry_run_before_the_instant_refuses_even_with_no_fixtures(tmp_path):
    with pytest.raises(NotYetEnabled):
        dry_run(tmp_path / "a", lambda: EF - ONE_S, [])
    with pytest.raises(NotYetEnabled):
        dry_run("", lambda: EF - ONE_S, [])
    assert list(tmp_path.iterdir()) == []
    assert dry_run(tmp_path / "c", lambda: EF, []) == []


@pytest.mark.parametrize("bad", ["", ".", "./", ".//", "logs", "../x", Path(""), Path("."), Path("logs"),
                                 Path("../x"), "~", "a/../b"])
def test_relative_folder_is_refused_in_any_spelling(tmp_path, monkeypatch, writes, bad):
    monkeypatch.chdir(tmp_path)
    _refused_everywhere(bad, writes)


def _refused_everywhere(folder, writes):
    w = window(0)
    t, b, tp = Spy(series(tstar_ms(w))), Spy(BOOK), Spy([])
    with pytest.raises(ValueError):
        log_window(folder, w, clock=lambda: at(w.open_ts + 300.0), read_ticks=t, read_book=b)
    with pytest.raises(ValueError):
        log_tape(folder, w, posted=True, read_tape=tp, clock=lambda: at(w.open_ts + 400.0), budget=TapeBudget(2))
    with pytest.raises(ValueError):
        log_close(folder, w, clock=lambda: at(w.close_ts + 15.0), read_ticks=t)
    with pytest.raises(ValueError):
        dry_run(folder, lambda: EF, [Fixture(w, series(tstar_ms(w)), BOOK)])
    with pytest.raises(ValueError):
        read_tick_files(folder, 1, 2, clock=lambda: EF)
    assert t.calls == [] and b.calls == [] and tp.calls == [] and writes == []


def test_the_repository_root_is_refused(writes):
    assert (REPO / "valedia_trader").is_dir()
    _refused_everywhere(REPO, writes)
    _refused_everywhere(str(REPO), writes)


def test_a_path_inside_the_repository_is_refused(writes):
    _refused_everywhere(REPO / "valedia_trader", writes)
    _refused_everywhere(REPO / "no-such-folder-low2" / "deeper", writes)
    _refused_everywhere(str(REPO / "docs" / "research"), writes)


def test_dot_dot_back_into_the_repository_is_refused(writes):
    _refused_everywhere(os.path.join(str(REPO.parent), "elsewhere", "..", REPO.name), writes)
    _refused_everywhere(os.path.join(str(REPO), "docs", "..", "valedia_trader", "x"), writes)


def test_a_symbolic_link_into_the_repository_is_refused(tmp_path, writes):
    link = tmp_path / "link"
    link.symlink_to(REPO / "docs", target_is_directory=True)
    _refused_everywhere(link, writes)
    _refused_everywhere(link / "store", writes)
    root_link = tmp_path / "rootlink"
    root_link.symlink_to(REPO, target_is_directory=True)
    _refused_everywhere(root_link, writes)


def test_a_different_letter_case_of_the_repository_is_refused(writes):
    twisted = str(REPO).swapcase()
    if twisted == str(REPO) or not os.path.exists(twisted):
        pytest.skip("this file system tells letter cases apart: the twisted path is a different place")
    _refused_everywhere(twisted, writes)


def test_an_absolute_folder_outside_the_repository_is_accepted(tmp_path):
    assert REPO not in tmp_path.resolve().parents and tmp_path.resolve() != REPO
    w = window(0)
    t, b, tp = Spy(series(tstar_ms(w))), Spy(BOOK), Spy([])
    log_window(tmp_path / "w", w, clock=lambda: at(w.open_ts + 300.0), read_ticks=t, read_book=b)
    assert log_tape(tmp_path / "t", w, posted=True, read_tape=tp, clock=lambda: at(w.open_ts + 400.0),
                    budget=TapeBudget(2)) is not None
    assert len(dry_run(tmp_path / "d", lambda: EF, [Fixture(w, series(tstar_ms(w)), BOOK)])) == 1
    log_close(tmp_path / "c", w, clock=lambda: at(w.close_ts + 15.0), read_ticks=Spy(_close_rows(w, "1.00")))
    assert all(len(lines(tmp_path / n)) == 1 for n in ("w", "t", "d", "c"))
    out_link = tmp_path / "outlink"
    out_link.symlink_to(tmp_path / "w", target_is_directory=True)
    w1 = window(1)
    log_window(out_link, w1, clock=lambda: at(w1.open_ts + 300.0), read_ticks=Spy(series(tstar_ms(w1))), read_book=b)
    assert len(lines(tmp_path / "w")) == 2


def test_the_date_gate_still_answers_before_the_folder_check():
    w = window(0)
    with pytest.raises(NotYetEnabled):
        log_window("logs", w, clock=lambda: EF - ONE_S, read_ticks=Spy([]), read_book=Spy(None))
    with pytest.raises(NotYetEnabled):
        log_tape(REPO, w, posted=True, read_tape=Spy([]), clock=lambda: EF - ONE_S, budget=TapeBudget(1))
    with pytest.raises(NotYetEnabled):
        log_close(REPO, w, clock=lambda: EF - ONE_S, read_ticks=Spy([]))
    with pytest.raises(NotYetEnabled):
        read_tick_files("rel", 1, 2, clock=lambda: EF - ONE_S)
    with pytest.raises(NotYetEnabled):
        dry_run("", lambda: EF - ONE_S, [])
