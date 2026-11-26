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
    assert saved["t_star"] == str(Decimal(T) / 1000) and saved["t_star_ms"] == T         # exact, from ms
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
    assert ch["first_gap_ms"] == 0 and ch["last_gap_ms"] == 0 and saved["n_rows_late"] == 0
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

def _thinned(T: int, n: int) -> list[dict]:
    """n ticks covering the whole span [t* - 300 s, t*]: the full 1 Hz series less every second tick from the
    middle outwards, so no gap exceeds 2 s and both edges are held."""
    rows = series(T)
    drop = 301 - n
    keep = [r for i, r in enumerate(rows) if not (i % 2 == 1 and 1 <= i < 2 * drop)]
    assert len(keep) == n
    return keep


def test_warmup_269_ticks_skips_and_270_is_ok(tmp_path):
    w = window(0)
    T = tstar_ms(w)
    rec, _, _ = run(tmp_path, w, ticks=_thinned(T, 269))
    assert rec["status"] == "SKIP_WARMUP_OR_GAP" and rec["skip_reasons"] == ["SKIP_WARMUP_OR_GAP"]
    assert rec["series_checks"]["n_ticks"] == 269 and rec["series_checks"]["count_ok"] is False
    assert rec["series_checks"]["anchor_ok"] and rec["series_checks"]["gap_ok"]
    ok, _, _ = run(tmp_path, window(0), ticks=_thinned(T, 270))
    assert ok["status"] == "OK" and ok["series_checks"]["n_ticks"] == 270


def test_270_ticks_that_start_at_t_star_minus_270_no_longer_pass(tmp_path):
    w = window(0)
    T = tstar_ms(w)
    rec, _, _ = run(tmp_path, w, ticks=series(T, n=270))        # 30 s of the span's start uncovered
    ch = rec["series_checks"]
    assert ch["count_ok"] and ch["anchor_ok"] and ch["max_gap_ms"] == 1000
    assert ch["first_gap_ms"] == 31_000 and ch["gap_ok"] is False
    assert rec["status"] == "SKIP_WARMUP_OR_GAP"


def test_span_start_edge_gap_5000_ok_and_5001_skips(tmp_path):
    w = window(0)
    T = tstar_ms(w)
    at_5000 = [r for r in series(T) if r["source_time_ms"] >= T - 295_000]
    rec, _, _ = run(tmp_path, w, ticks=at_5000)
    assert rec["series_checks"]["first_gap_ms"] == 5000 and rec["status"] == "OK"
    at_5001 = [tick_row(T - 294_999, seq=0)] + [r for r in series(T) if r["source_time_ms"] >= T - 294_000]
    rec2, _, _ = run(tmp_path, window(0), ticks=at_5001)
    assert rec2["series_checks"]["first_gap_ms"] == 5001 and rec2["series_checks"]["gap_ok"] is False
    assert rec2["status"] == "SKIP_WARMUP_OR_GAP" and rec2["skip_reasons"] == ["SKIP_WARMUP_OR_GAP"]


def test_span_end_edge_gap_is_checked_the_same_way():
    T = 1_000_000_000
    def ticks_ending(last_ms):
        return [L._parse_tick_row(tick_row(s, seq=1)) for s in range(T - 300_000, last_ms + 1, 1000)]
    assert L._series_checks(ticks_ending(T - 5000), T)["last_gap_ms"] == 5000
    assert L._series_checks(ticks_ending(T - 5000), T)["gap_ok"] is True
    late = ticks_ending(T - 6000) + [L._parse_tick_row(tick_row(T - 5001, seq=1))]
    ch = L._series_checks(late, T)
    assert ch["last_gap_ms"] == 5001 and ch["max_gap_ms"] == 1000 and ch["gap_ok"] is False
    assert L._series_checks([], T)["gap_ok"] is False and L._series_checks([], T)["first_gap_ms"] is None


def test_warmup_counts_a_repeated_source_time_once(tmp_path):
    w = window(0)
    T = tstar_ms(w)
    ticks = _thinned(T, 269)
    ticks.append(dict(ticks[-1]))                                # a duplicate row is not a 270th tick
    rec, _, _ = run(tmp_path, w, ticks=ticks)
    assert rec["status"] == "SKIP_WARMUP_OR_GAP" and rec["n_duplicates_dropped"] == 1


def test_no_tick_near_t_star_minus_180_fails_and_exactly_3_s_holds():
    # With both edges held and no gap over 5 s, a tick always lies within 2.5 s of t* - 180 s, so the anchor
    # check can only fail together with a gap check; its own boundary is tested on the check itself.
    T = 1_000_000_000
    A = T - 180_000
    def ticks(*src):
        return [L._parse_tick_row(tick_row(s, seq=1)) for s in src]
    assert L._series_checks(ticks(T - 300_000, A - 3000, T), T)["anchor_ok"] is True
    assert L._series_checks(ticks(T - 300_000, A + 3000, T), T)["anchor_ok"] is True
    assert L._series_checks(ticks(T - 300_000, A - 3001, A + 3001, T), T)["anchor_ok"] is False
    assert L._series_checks(ticks(T - 300_000, A - 3001, T), T)["anchor_tick_ms"] == A - 3001


def test_no_tick_near_t_star_minus_180_is_a_warmup_skip(tmp_path):
    w = window(0)
    T = tstar_ms(w)
    hole = [r for r in series(T) if abs(r["source_time_ms"] - (T - 180_000)) > 3000]
    rec, _, _ = run(tmp_path, w, ticks=hole)
    assert rec["series_checks"]["anchor_ok"] is False and rec["status"] == "SKIP_WARMUP_OR_GAP"


# ---------------------------------------------------------------- late ticks

def test_a_tick_late_by_61_s_is_counted_and_excluded_and_60_s_is_kept(tmp_path):
    w = window(0)
    T = tstar_ms(w)
    base = series(T - 1000, n=300)
    rec, _, _ = run(tmp_path, w, ticks=base + [tick_row(T, "777.00", seq=900, recv=T + 61_000)])
    assert rec["n_rows_late"] == 1 and rec["tick_used"]["cf_source_ts_ms"] == T - 1000
    assert all(p["brti_raw"] != "777.00" for p in rec["series"])
    ok, _, _ = run(tmp_path, window(0), ticks=base + [tick_row(T, "777.00", seq=900, recv=T + 60_000)])
    assert ok["n_rows_late"] == 0 and ok["tick_used"]["brti_raw"] == "777.00"


def test_a_negative_lag_beyond_60_s_is_excluded_too(tmp_path):
    w = window(0)
    T = tstar_ms(w)
    base = series(T - 1000, n=300)
    rec, _, _ = run(tmp_path, w, ticks=base + [tick_row(T, "777.00", seq=900, recv=T - 60_001)])
    assert rec["n_rows_late"] == 1 and rec["tick_used"]["cf_source_ts_ms"] == T - 1000
    ok, _, _ = run(tmp_path, window(0), ticks=base + [tick_row(T, "777.00", seq=900, recv=T - 60_000)])
    assert ok["n_rows_late"] == 0 and ok["tick_used"]["brti_raw"] == "777.00"


def test_late_ticks_are_excluded_from_the_close_record_too(tmp_path):
    w = window(0)
    C = int(round(w.close_ts * 1000))
    rows = _close_rows(w, "100.00")
    rows[30] = dict(rows[30], received_at_ms=rows[30]["source_time_ms"] + 61_000)     # the k = 0 candidate, late
    rec = log_close(tmp_path, w, clock=lambda: at(w.close_ts + 195.0), read_ticks=Spy(rows))
    assert rec["n_rows_late"] == 1 and rec["close_tick"]["cf_source_ts_ms"] == C + 1000


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
            last15 = {"value": value, "window_size": size,
                      "window_start_ts_ms": C - 60000, "window_end_ts_exclusive": C}
        rows.append(tick_row(src, seq=k + 100, last15=last15))
    return rows


def _avg(value: str, start: int, end: int, size: int = 60) -> dict:
    return {"value": value, "window_size": size, "window_start_ts_ms": start, "window_end_ts_exclusive": end}


def test_close_never_uses_the_previous_quarters_average(tmp_path):
    w = window(0)
    C = int(round(w.close_ts * 1000))
    rows = _close_rows(w, None)
    for i in range(0, 36):                         # every tick in the span still carries the last quarter's average
        rows[i] = dict(rows[i], last_15min_avg=_avg("555.55", C - 960_000, C - 900_000))
    rec = log_close(tmp_path / "a", w, clock=lambda: at(w.close_ts + 195.0), read_ticks=Spy(rows))
    assert rec["settlement_estimate"] is None and rec["settlement_estimate_reason"] == "no_close_tick"
    assert rec["n_close_wrong_window"] == 36 and rec["n_close_candidates"] == 0
    # opposite: this quarter's average arriving later in the span is used, the stale ones still rejected
    for i in range(31, 36):
        rows[i] = dict(rows[i], last_15min_avg=_avg("100.005", C - 60_000, C))
    rec2 = log_close(tmp_path / "b", w, clock=lambda: at(w.close_ts + 195.0), read_ticks=Spy(rows))
    assert rec2["settlement_estimate"] == "100.00" and rec2["close_tick"]["cf_source_ts_ms"] == C + 1000
    assert rec2["n_close_wrong_window"] == 31 and rec2["n_close_candidates"] == 5


@pytest.mark.parametrize("start_off,end_off", [(0, 1), (0, -1), (1, 0), (-1, 0)])
def test_close_bounds_off_by_one_are_rejected(tmp_path, start_off, end_off):
    w = window(0)
    C = int(round(w.close_ts * 1000))
    rows = _close_rows(w, None)
    rows[30] = dict(rows[30], last_15min_avg=_avg("100.00", C - 60_000 + start_off, C + end_off))
    rec = log_close(tmp_path, w, clock=lambda: at(w.close_ts + 195.0), read_ticks=Spy(rows))
    assert rec["settlement_estimate"] is None and rec["n_close_wrong_window"] == 1
    assert rec["settlement_estimate_reason"] == "no_close_tick"


def test_close_candidates_that_disagree_give_none(tmp_path):
    w = window(0)
    rows = _close_rows(w, "100.00")
    rows[33] = dict(rows[33], last_15min_avg=dict(rows[33]["last_15min_avg"], value="100.01"))
    rec = log_close(tmp_path, w, clock=lambda: at(w.close_ts + 195.0), read_ticks=Spy(rows))
    assert rec["settlement_estimate"] is None and rec["settlement_estimate_reason"] == "close_candidates_disagree"
    assert rec["close_candidates_agree"] is False and rec["n_close_candidates"] == 6
    assert rec["close_tick"] is not None                       # the first candidate is still recorded, as metadata


def test_close_estimate_rounds_half_even_on_a_half_cent(tmp_path):
    w = window(0)
    after = lambda: at(w.close_ts + 195.0)
    for i, (raw, want) in enumerate((("100000.125", "100000.12"), ("100000.135", "100000.14"),
                                     ("100000.1251", "100000.13"), ("99999.995", "100000.00"))):
        rec = log_close(tmp_path / str(i), w, clock=after, read_ticks=Spy(_close_rows(w, raw)))
        assert rec["settlement_estimate"] == want, raw
        assert rec["settlement_estimate_reason"] is None and rec["rounding"] == "2 dp, ROUND_HALF_EVEN"
        assert rec["close_tick"]["last_60s_windowed_average_15min"]["value"] == raw
        assert rec["close_tick"]["cf_source_ts_ms"] == int(w.close_ts * 1000)     # the first tick with size 60
    saved = no_float_lines(tmp_path / "0")[0]
    assert saved["kind"] == "close" and saved["schema_version"] == 2 and saved["settlement_estimate"] == "100000.12"
    assert saved["n_close_candidates"] == 6 and saved["close_candidates_agree"] is True
    assert saved["n_close_wrong_window"] == 0
    assert "Kalshi settled market" in saved["outcome_source"]


def test_close_estimate_absent_gives_none_and_a_reason(tmp_path):
    w = window(0)
    after = lambda: at(w.close_ts + 195.0)
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
        log_close(tmp_path, w, clock=lambda: at(w.close_ts + 189.999), read_ticks=spy)
    with pytest.raises(CloseNotReached):                          # exact: 1 microsecond short is refused, not rounded
        log_close(tmp_path, w, clock=lambda: at(w.close_ts + 190.0) - timedelta(microseconds=1), read_ticks=spy)
    assert spy.calls == [] and lines(tmp_path) == []
    log_close(tmp_path, w, clock=lambda: at(w.close_ts + 190.0), read_ticks=spy)
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
        store.append(kis.parse_value_message(frame), "cfbenchmarks_value", local_receipt_ms=src + 40)
    out = tmp_path / "log"
    clk = lambda: at(w.open_ts + 301.0)
    rec = log_window(out, w, clock=clk, read_ticks=tick_file_reader(tmp_path, clock=clk), read_book=Spy(BOOK))
    assert rec["status"] == "OK" and rec["series_checks"]["n_ticks"] == 301
    assert rec["tick_used"]["brti_raw"] == "100000.00" and rec["tick_used"]["received_at"] == T + 25
    assert rec["tick_used"]["sending_ts_ms"] == T + 30 and rec["tick_used"]["seq"] == 301
    assert type(rec["tick_used"]["local_receipt_ms"]) is int and rec["tick_used"]["local_receipt_ms"] == T + 40
    assert rec["tick_used"]["local_receipt_note"] is None
    no_float_lines(out)


# ---------------------------------------------------------------- the adapter reads only the span

def _full_scan(folder: Path, a: int, b: int) -> list[dict]:
    """The reference: every line of every day file in the folder, oldest day first, then the same filter."""
    out = []
    for path in sorted(folder.glob("kalshi_index_ticks-*.jsonl")):
        for line in path.read_bytes().split(b"\n"):
            try:
                row = json.loads(line.decode("utf-8", errors="replace"), parse_float=Decimal)
            except ValueError:
                continue
            tick = L._parse_tick_row(row)
            if tick is not None and a <= tick.source_ms <= b:
                out.append(row)
    return out


class _Counted:
    """Wraps a file object and counts the bytes its read() returns."""
    def __init__(self, fh, log, path):
        self.fh, self.log, self.path = fh, log, str(path)

    def read(self, *a):
        data = self.fh.read(*a)
        self.log.append((self.path, len(data)))
        return data

    def __getattr__(self, name):
        return getattr(self.fh, name)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return self.fh.__exit__(*exc)


@pytest.fixture
def counted(monkeypatch):
    log = []
    real_open = open
    monkeypatch.setattr(L, "open", lambda p, *a, **k: _Counted(real_open(p, *a, **k), log, p), raising=False)
    return log


def _midnight_store(folder: Path, M: int) -> list[dict]:
    """1 Hz ticks from M - 1800 s to M + 1800 s, filed by UTC receipt day, written out of order by up to 60 s,
    with malformed lines between them."""
    import random
    rng = random.Random(7)
    rows = [tick_row(M + 1000 * k, seq=k + 2000, recv=M + 1000 * k + rng.choice((20, 900, 1500))) for k in
            range(-1800, 1801)]
    rows.append(tick_row(M - 100, seq=1, recv=M + 50))           # sourced on one day, received on the next
    rows.append(tick_row(M - 30_500, seq=2, recv=M + 30_501))    # late by 61.001 s, across midnight: never valid
    rows.append(tick_row(M - 20_500, seq=3, recv=M + 39_500))    # late by exactly 60 s, across midnight: valid
    rows.sort(key=lambda r: r["received_at_ms"])
    shuffled = []
    for i in range(0, len(rows), 60):                            # write order differs from receipt by < 60 s
        chunk = rows[i:i + 60]
        rng.shuffle(chunk)
        shuffled += chunk
    junk = ["{truncated", "", "[1,2]", "null", "\x00\x01 binary", json.dumps({"received_at_ms": "x"})]
    for n, row in enumerate(shuffled):
        day = datetime.fromtimestamp(row["received_at_ms"] // 1000, UTC).date().isoformat()
        _write(folder, day, [row] + ([junk[n % len(junk)]] if n % 37 == 0 else []))
    return rows


SPANS = [(-300_000, 0), (-10_000, 10_000), (600_000, 900_000), (-1_800_000, 1_800_000), (60_000, 100_000),
         (-5_000_000, -4_000_000), (4_000_000, 5_000_000), (1_799_000, 1_800_000), (-1_800_000, -1_790_000)]


@pytest.mark.parametrize("block", [None, 512, 4096])
def test_backward_reader_matches_the_full_scan(tmp_path, monkeypatch, block):
    if block is not None:
        monkeypatch.setattr(L, "READ_BLOCK_BYTES", block)
    M = int(datetime(2026, 12, 5, 0, 0, 0, tzinfo=UTC).timestamp() * 1000)
    _midnight_store(tmp_path, M)
    clk = lambda: at(M / 1000 + 7200)
    for lo, hi in SPANS:
        got = read_tick_files(tmp_path, M + lo, M + hi, clock=clk)
        full = _full_scan(tmp_path, M + lo, M + hi)
        assert got == full, (lo, hi)
        assert L._ticks_in(got, M + lo, M + hi) == L._ticks_in(full, M + lo, M + hi), (lo, hi)
    got = read_tick_files(tmp_path, M - 300_000, M, clock=clk)
    ticks, counts = L._ticks_in(got, M - 300_000, M)
    assert len(got) == 304 and counts["n_rows_late"] == 1 and len(ticks) == 303      # 301 + the two valid strays
    assert M - 20_500 in {t.source_ms for t in ticks} and M - 30_500 not in {t.source_ms for t in ticks}


def test_backward_reader_stops_early_and_opens_the_day_before_only_when_needed(tmp_path, counted):
    M = int(datetime(2026, 12, 5, 0, 0, 0, tzinfo=UTC).timestamp() * 1000)
    _midnight_store(tmp_path, M)
    clk = lambda: at(M / 1000 + 7200)
    today = str(tmp_path / "kalshi_index_ticks-2026-12-05.jsonl")
    before = str(tmp_path / "kalshi_index_ticks-2026-12-04.jsonl")
    # a span far after midnight: the day before is never opened, and today's file is read only near its end
    read_tick_files(tmp_path, M + 1_500_000, M + 1_800_000, clock=clk)
    assert {p for p, _ in counted} == {today}
    assert sum(n for _, n in counted) < os.path.getsize(today) / 2
    # a span just after midnight: today's file to its start, and the day before only from its end, briefly
    counted.clear()
    read_tick_files(tmp_path, M + 60_000, M + 100_000, clock=clk)
    read_before = sum(n for p, n in counted if p == before)
    assert 0 < read_before <= 4 * L.READ_BLOCK_BYTES and read_before < os.path.getsize(before) / 4


def test_backward_reader_skips_an_oversized_line(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "READ_BLOCK_BYTES", 1024)
    monkeypatch.setattr(L, "MAX_LINE_BYTES", 4096)
    M = int(datetime(2026, 12, 5, 6, 0, 0, tzinfo=UTC).timestamp() * 1000)
    rows = [tick_row(M + 1000 * k, seq=k) for k in range(40)]
    _write(tmp_path, "2026-12-05", rows[:20] + ["x" * 20_000] + rows[20:30] + ["{" + "y" * 5000] + rows[30:])
    got = read_tick_files(tmp_path, M, M + 39_000, clock=lambda: at(M / 1000 + 60))
    assert got == rows == _full_scan(tmp_path, M, M + 39_000)


def test_backward_reader_reads_a_bounded_number_of_bytes_on_a_large_file(tmp_path, counted):
    D0 = int(datetime(2026, 12, 6, 0, 0, 10, tzinfo=UTC).timestamp() * 1000)
    n = 100_000
    path = tmp_path / "kalshi_index_ticks-2026-12-06.jsonl"
    with open(path, "w", encoding="utf-8") as fh:
        for k in range(n):                                       # 2 Hz, about 13.9 hours, one UTC day
            row = dict(tick_row(D0 + 500 * k, seq=k), avg_60s=None)
            fh.write(json.dumps(row, separators=(",", ":")) + "\n")
    end = D0 + 500 * (n - 1)
    got = read_tick_files(tmp_path, end - 300_000, end, clock=lambda: at(end / 1000 + 60))
    read = sum(b for _, b in counted)
    size = os.path.getsize(path)
    path.unlink()
    assert len(got) == 601 and got[0]["seq"] == n - 601 and got[-1]["seq"] == n - 1
    print(f"bounded read: {read} bytes of a {size}-byte file, {n} rows")
    assert read < 512 * 1024, read                               # budget: 512 KiB, against a file of about 30 MB
    assert size > 50 * read


# ---------------------------------------------------------------- no float anywhere

def test_no_float_in_any_written_record(tmp_path):
    w = window(0)
    folder = tmp_path / "all"
    run(folder, w)
    run(folder, window(1), ticks=[], book=None)
    log_close(folder, w, clock=lambda: at(w.close_ts + 195.0), read_ticks=Spy(_close_rows(w, "100000.125")))
    log_close(folder, w, clock=lambda: at(w.close_ts + 195.0), read_ticks=Spy([]))
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
    T = tstar_ms(w)
    assert saved[0]["t_from"] == str(Decimal(T) / 1000) and saved[0]["t_to"] == str(Decimal(T + 60_000) / 1000)
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
    after = at(w.close_ts + 195.0)
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
        log_close(bad, w, clock=lambda: at(w.close_ts + 195.0), read_ticks=t)
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


def _refused_as_a_subclass_everywhere(tmp_path, reading: datetime) -> None:
    w = window(0)
    t, b, tp = Spy(series(tstar_ms(w))), Spy(BOOK), Spy([])
    budget = TapeBudget(3)
    with pytest.raises(NaiveClock, match="not a subclass"):
        log_window(tmp_path / "w", w, clock=lambda: reading, read_ticks=t, read_book=b)
    with pytest.raises(NaiveClock, match="not a subclass"):
        log_tape(tmp_path / "t", w, posted=True, read_tape=tp, clock=lambda: reading, budget=budget)
    with pytest.raises(NaiveClock, match="not a subclass"):
        log_close(tmp_path / "c", w, clock=lambda: reading, read_ticks=t)
    with pytest.raises(NaiveClock, match="not a subclass"):
        dry_run(tmp_path / "d", lambda: reading, [Fixture(w, series(tstar_ms(w)), BOOK)])
    with pytest.raises(NaiveClock, match="not a subclass"):
        read_tick_files(tmp_path, 0, 1, clock=lambda: reading)
    with pytest.raises(NaiveClock, match="not a subclass"):
        check_enabled(lambda: reading)
    assert t.calls == [] and b.calls == [] and tp.calls == [] and budget.used == 0
    assert not tmp_path.exists() or list(tmp_path.iterdir()) == []


def test_a_comparison_liar_clock_is_refused_everywhere_before_or_after_the_instant(tmp_path):
    early = _liar(EF.timestamp() - 1.0, lie_early=False)
    assert isinstance(early, datetime) and early.timestamp() < EF.timestamp()
    _refused_as_a_subclass_everywhere(tmp_path / "a", early)
    _refused_as_a_subclass_everywhere(tmp_path / "b", _liar(EF.timestamp() + 900.0, lie_early=True))


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
        log_close(folder, w, clock=lambda: at(w.close_ts + 195.0), read_ticks=t)
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
    log_close(tmp_path / "c", w, clock=lambda: at(w.close_ts + 195.0), read_ticks=Spy(_close_rows(w, "1.00")))
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


# ---------------------------------------------------------------- duplicates and conflicting source times

def test_equal_duplicates_still_dedupe_to_one_tick(tmp_path):
    w = window(0)
    T = tstar_ms(w)
    ticks = series(T, n=300)
    twin = dict(ticks[-1], received_at_ms=ticks[-1]["received_at_ms"] + 7, seq=5000)   # same tick, received again
    rec, _, _ = run(tmp_path, w, ticks=ticks + [twin])
    assert rec["n_duplicates_dropped"] == 1 and rec["n_conflicting_source_times"] == 0
    assert rec["tick_used"]["cf_source_ts_ms"] == T and rec["status"] == "OK"


def test_conflicting_values_at_t_star_drop_that_source_time(tmp_path):
    w = window(0)
    T = tstar_ms(w)
    ticks = series(T, n=300)
    other = tick_row(T, "999.99", seq=5000)                     # a second, different tick at t*'s source time
    rec, _, _ = run(tmp_path, w, ticks=ticks + [other])
    assert rec["n_conflicting_source_times"] == 1 and rec["n_duplicates_dropped"] == 0
    assert rec["tick_used"]["cf_source_ts_ms"] == T - 1000 and rec["tick_used"]["brti_raw"] != "999.99"
    assert all(p["brti_raw"] != "999.99" for p in rec["series"])
    # conflicts at the last four source times: the tick before is 4 s old, so the stale rule skips
    bad = [tick_row(T - 1000 * k, "999.99", seq=6000 + k) for k in range(4)]
    rec2, _, _ = run(tmp_path, window(0), ticks=ticks + bad)
    assert rec2["n_conflicting_source_times"] == 4 and rec2["status"] == "SKIP_STALE_INDEX"


def test_a_conflict_only_in_avg_60s_at_an_ordinary_tick_is_dropped_and_counted(tmp_path):
    w = window(0)
    T = tstar_ms(w)
    ticks = series(T, n=300)
    i = 150
    other = dict(ticks[i], seq=5000, avg_60s=dict(ticks[i]["avg_60s"], value="12345.67"))
    rec, _, _ = run(tmp_path, w, ticks=ticks + [other])
    assert rec["n_conflicting_source_times"] == 1 and rec["n_duplicates_dropped"] == 0
    assert ticks[i]["source_time_ms"] not in {p["cf_source_ts_ms"] for p in rec["series"]}
    assert len(rec["series"]) == 299


def test_conflicting_close_candidates_at_one_source_time_give_none(tmp_path):
    w = window(0)
    C = int(round(w.close_ts * 1000))
    rows = _close_rows(w, "100.10")
    rows.append(dict(rows[30], seq=5000, last_15min_avg=_avg("999.99", C - 60_000, C)))   # k = 0, other value
    rec = log_close(tmp_path, w, clock=lambda: at(w.close_ts + 195.0), read_ticks=Spy(rows))
    assert rec["settlement_estimate"] is None and rec["settlement_estimate_reason"] == "close_candidates_disagree"
    assert rec["close_candidates_agree"] is False and rec["n_close_conflicting_candidates"] == 2
    assert rec["n_conflicting_source_times"] == 1


def test_a_wrong_bounds_row_first_and_a_right_candidate_second_is_a_conflict(tmp_path):
    w = window(0)
    C = int(round(w.close_ts * 1000))
    first = tick_row(C, seq=1, recv=C + 20, last15=_avg("555.55", C - 960_000, C - 900_000))
    second = tick_row(C, seq=2, recv=C + 20, last15=_avg("100.10", C - 60_000, C))
    rows = [r for r in _close_rows(w, None) if r["source_time_ms"] != C] + [first, second]
    rec = log_close(tmp_path, w, clock=lambda: at(w.close_ts + 195.0), read_ticks=Spy(rows))
    assert rec["settlement_estimate"] is None and rec["settlement_estimate_reason"] == "close_candidates_disagree"
    assert rec["n_close_conflicting_candidates"] == 1 and rec["n_close_candidates"] == 0


def _reconciles(rec: dict) -> bool:
    """The count identity, from the record's own fields only."""
    return rec["n_rows_read"] == (rec["n_rows_malformed"] + rec["n_rows_late"] + rec["n_rows_outside_span"]
                                  + rec["n_ticks_kept"] + rec["n_duplicates_dropped"] + rec["n_conflicting_rows"])


def test_the_row_counts_reconcile_over_every_kind_of_row(tmp_path):
    w = window(0)
    T = tstar_ms(w)
    ticks = series(T, n=300)
    extra = [
        {"index_id": "BRTI", "value": "x"},                                       # malformed
        tick_row(T - 2000, "777.00", seq=7001, recv=T - 2000 + 61_000),          # late
        tick_row(T + 400, seq=7002),                                              # outside the span
        dict(ticks[10], seq=7003), dict(ticks[10], seq=7004),                     # two equal duplicates
        tick_row(ticks[20]["source_time_ms"], "1.00", seq=7005),                  # with ticks[20]: a conflict of 3
        tick_row(ticks[20]["source_time_ms"], "2.00", seq=7006),
    ]
    rec, _, _ = run(tmp_path, w, ticks=ticks + extra)
    assert (rec["n_rows_malformed"], rec["n_rows_late"], rec["n_rows_outside_span"]) == (1, 1, 1)
    assert rec["n_duplicates_dropped"] == 2 and rec["n_conflicting_source_times"] == 1
    assert rec["n_conflicting_rows"] == 3 and len(rec["series"]) == 299 and rec["n_rows_read"] == 307
    assert rec["n_ticks_kept"] == len(rec["series"]) == 299 and _reconciles(rec)
    assert _reconciles(lines(tmp_path)[0])                                        # and as written


def test_the_close_record_carries_conflicting_rows_and_reconciles(tmp_path):
    w = window(0)
    C = int(round(w.close_ts * 1000))
    rows = _close_rows(w, "100.10")
    rows.append(dict(rows[30], seq=5000, last_15min_avg=_avg("999.99", C - 60_000, C)))
    rows.append(dict(rows[31], seq=5001))                                         # an equal duplicate
    rec = log_close(tmp_path, w, clock=lambda: at(w.close_ts + 195.0), read_ticks=Spy(rows))
    assert rec["n_conflicting_rows"] == 2 and rec["n_duplicates_dropped"] == 1
    assert rec["n_rows_read"] == 38 and rec["n_ticks_kept"] == 35 and _reconciles(rec)
    assert _reconciles(lines(tmp_path)[0])


class _NeverBefore(datetime):
    """A datetime subclass whose comparisons always say "not before"."""
    def __lt__(self, other): return False
    def __le__(self, other): return False
    def __gt__(self, other): return True
    def __ge__(self, other): return True


def test_a_close_wait_liar_is_refused_at_the_clock_check(tmp_path):
    w = window(0)
    spy = Spy(_close_rows(w, "100.00"))
    for ts in (w.close_ts + 100.0, w.close_ts + 190.0, w.close_ts + 500.0):
        liar = _NeverBefore.fromtimestamp(ts, tz=UTC)
        with pytest.raises(NaiveClock, match="not a subclass"):
            log_close(tmp_path, w, clock=lambda: liar, read_ticks=spy)
    assert spy.calls == [] and lines(tmp_path) == []


class _LyingFloat(float):
    def __repr__(self): return "1796379300.0"


def test_a_close_ts_that_is_not_a_plain_whole_millisecond_number_is_refused(tmp_path):
    from decimal import localcontext
    w = window(0)
    C = int(w.close_ts)
    assert C == w.close_ts

    def win(close_ts):
        return Window(window_ticker=w.window_ticker, open_ts=w.open_ts, close_ts=close_ts, floor_strike=w.floor_strike)

    for bad in (Decimal(C), True, _LyingFloat(C + 0.0004), float("nan"), float("inf"), -1, 4_102_444_801, 1e24,
                C + 0.0004):
        spy = Spy(_close_rows(w, "1.00"))
        with pytest.raises(ValueError):
            log_close(tmp_path, win(bad), clock=lambda: at(C + 195.0), read_ticks=spy)
        with pytest.raises(ValueError):
            run(tmp_path, win(bad))
        assert spy.calls == [], bad
    assert lines(tmp_path) == []
    for prec in (None, 5):
        for ok, want in ((C, C * 1000), (float(C), C * 1000), (C + 0.001, C * 1000 + 1)):
            if prec is None:
                got = L._check_window(win(ok))[1]
            else:
                with localcontext() as ctx:
                    ctx.prec = prec
                    got = L._check_window(win(ok))[1]
            assert type(got) is int and got == want, (prec, ok)
    rec = log_close(tmp_path, win(C + 0.001), clock=lambda: at(C + 195.0), read_ticks=Spy([]))
    assert rec["close_ms"] == C * 1000 + 1


def test_an_int_window_time_beyond_float_range_is_a_value_error_in_every_function(tmp_path):
    w = window(0)
    huge = 10 ** 400
    for open_ts, close_ts in ((huge, w.close_ts), (w.open_ts, huge)):
        bad = Window(window_ticker=w.window_ticker, open_ts=open_ts, close_ts=close_ts, floor_strike=w.floor_strike)
        ticks, book, tape = Spy([]), Spy(None), Spy([])
        with pytest.raises(ValueError):
            log_window(tmp_path, bad, clock=lambda: EF, read_ticks=ticks, read_book=book)
        with pytest.raises(ValueError):
            log_close(tmp_path, bad, clock=lambda: at(w.close_ts + 195.0), read_ticks=ticks)
        budget = TapeBudget(1)
        with pytest.raises(ValueError):
            log_tape(tmp_path, bad, posted=True, read_tape=tape, clock=lambda: EF, budget=budget)
        assert ticks.calls == book.calls == tape.calls == []
    assert lines(tmp_path) == []


def test_t_star_is_derived_exactly_from_a_whole_ms_open_time(tmp_path):
    open_ts = 1764000000.123 + (EF.timestamp() - 1764000000.0) // 900 * 900       # a whole-ms open, after the gate
    open_ms = L._window_ms(open_ts, "open_ts")
    w = Window(window_ticker="KXBTC15M-TESTMS", open_ts=open_ts, close_ts=open_ts + 900.0, floor_strike=1.0)
    clock = lambda: at(w.open_ts + 301.0)
    spy = Spy([])
    rec = log_window(tmp_path, w, clock=clock, read_ticks=spy, read_book=Spy(None))
    assert rec["t_star_ms"] == open_ms + 300_000 and spy.calls[0][2] == open_ms + 300_000
    assert rec["t_star"] == str(Decimal(open_ms + 300_000) / 1000) and rec["t_star"].endswith(".123")
    # the literal case: open_ts 1764000000.123 gives t* "1764000300.123"
    assert L._window_ms(1764000000.123, "open_ts") + 300_000 == 1764000300123
    assert str(L._ms_to_s(1764000300123)) == "1764000300.123"
    tape = log_tape(tmp_path, w, posted=True, read_tape=Spy([]), clock=clock, budget=TapeBudget(1))
    assert tape["t_from"] == rec["t_star"] and tape["t_to"] == str(Decimal(open_ms + 360_000) / 1000)


class _ClaimsAfter(datetime):
    """A datetime subclass whose timestamp(), epoch __sub__ and comparisons all claim a day after ENABLED_FROM."""
    def timestamp(self):
        return EF.timestamp() + 86_400.0

    def __sub__(self, other):
        if isinstance(other, datetime) and other.year == 1970:
            return (EF - other) + timedelta(days=1)
        return datetime.__sub__(self, other)

    def __lt__(self, other): return False
    def __le__(self, other): return False
    def __gt__(self, other): return True
    def __ge__(self, other): return True


def _claims_after(true_value: datetime) -> _ClaimsAfter:
    t = true_value.astimezone(UTC)
    return _ClaimsAfter(t.year, t.month, t.day, t.hour, t.minute, t.second, t.microsecond, tzinfo=UTC)


class _LyingTz(datetime):
    """A datetime subclass that lies through its tzinfo property."""
    @property
    def tzinfo(self):
        return timezone(timedelta(hours=-23))


def test_every_lying_subclass_clock_is_refused_at_the_clock_check(tmp_path):
    liar = _claims_after(EF - ONE_S)                                              # truly one second before
    assert liar.timestamp() > EF.timestamp() and liar > EF                       # it lies
    _refused_as_a_subclass_everywhere(tmp_path / "a", liar)
    _refused_as_a_subclass_everywhere(tmp_path / "b", _claims_after(EF + timedelta(days=2)))
    t = (EF - ONE_S).astimezone(UTC)
    tz_liar = _LyingTz(t.year, t.month, t.day, t.hour, t.minute, t.second, tzinfo=UTC)
    assert tz_liar.tzinfo != UTC                                                  # it lies
    _refused_as_a_subclass_everywhere(tmp_path / "c", tz_liar)


def test_every_fake_clock_reading_in_this_file_is_exactly_a_datetime():
    w = window(0)
    for reading in (EF, EF - ONE_S, at(w.close_ts + 195.0), SeqClock([EF])()):
        assert type(reading) is datetime


# ---------------------------------------------------------------- the tape filter: exact row times

def _tape_window_off_a_whole_second() -> Window:
    w = window(0)
    open_ts = w.open_ts + 0.123
    return Window(window_ticker="KXBTC15M-TESTTAPE", open_ts=open_ts, close_ts=open_ts + 900.0, floor_strike=1.0)


def test_tape_rows_at_exactly_the_requested_float_bounds_are_both_kept(tmp_path):
    w = _tape_window_off_a_whole_second()
    asked = []

    def read_tape(window, t_from, t_to):
        asked.append((t_from, t_to))
        return [{"trade_id": "lo", "created_ts": t_from}, {"trade_id": "hi", "created_ts": t_to},
                {"trade_id": "below", "created_ts": t_from - 0.001}, {"trade_id": "above", "created_ts": t_to + 0.001}]

    out = log_tape(tmp_path, w, posted=True, read_tape=read_tape, clock=lambda: at(w.open_ts + 400.0),
                   budget=TapeBudget(1))
    assert type(asked[0][0]) is float and type(asked[0][1]) is float
    assert [r["trade_id"] for r in out["rows"]] == ["lo", "hi"]


def test_a_malformed_tape_row_time_is_skipped_never_raised_on(tmp_path):
    w = window(0)
    t = w.open_ts + 310.0
    bad_times = ["x", None, True, float("nan"), float("inf"), -1, 4_102_444_801, 10 ** 400, Decimal(int(t))]
    raw = [{"trade_id": f"b{i}", "created_ts": v} for i, v in enumerate(bad_times)]
    raw += ["not a dict", {"trade_id": "no time"}, {"trade_id": "ok", "created_ts": t}]
    out = log_tape(tmp_path, w, posted=True, read_tape=Spy(raw), clock=lambda: at(w.open_ts + 400.0),
                   budget=TapeBudget(1))
    assert [r["trade_id"] for r in out["rows"]] == ["ok"] and out["n_rows"] == 1
    assert out["n_tape_rows_malformed"] == len(bad_times) + 2                     # + "not a dict" and "no time"
    saved = lines(tmp_path)[0]
    assert saved["n_rows"] == len(saved["rows"]) == 1 and saved["n_tape_rows_malformed"] == 11


def test_the_tape_is_unchanged_under_a_caller_context_that_traps_float_operations(tmp_path):
    from decimal import FloatOperation, localcontext
    w = _tape_window_off_a_whole_second()
    rows = lambda: [{"trade_id": "a", "created_ts": w.open_ts + 300.0}, {"trade_id": "b", "created_ts": w.open_ts + 330},
                    {"trade_id": "c", "created_ts": w.open_ts + 361.0}]
    clock = lambda: at(w.open_ts + 400.0)
    plain = log_tape(tmp_path / "a", w, posted=True, read_tape=lambda *a: rows(), clock=clock, budget=TapeBudget(1))
    with localcontext() as ctx:
        ctx.traps[FloatOperation] = True
        trapped = log_tape(tmp_path / "b", w, posted=True, read_tape=lambda *a: rows(), clock=clock,
                           budget=TapeBudget(1))
    assert trapped == plain and [r["trade_id"] for r in plain["rows"]] == ["a", "b"]
    assert lines(tmp_path / "a") == lines(tmp_path / "b")


def test_the_tape_budget_is_taken_only_after_every_check_before_the_read(tmp_path, monkeypatch):
    w = window(0)
    good_clock = lambda: at(w.open_ts + 400.0)
    budget, tape = TapeBudget(5), Spy([])
    bad_window = Window(window_ticker=w.window_ticker, open_ts=w.open_ts, close_ts=w.close_ts + 0.0004)
    cases = [
        (NotYetEnabled, dict(folder=tmp_path, window=w, clock=lambda: EF - ONE_S, read_tape=tape)),
        (NaiveClock, dict(folder=tmp_path, window=w, clock=lambda: datetime(2027, 1, 1), read_tape=tape)),
        (ValueError, dict(folder="", window=w, clock=good_clock, read_tape=tape)),
        (ValueError, dict(folder=tmp_path, window=w, clock=good_clock, read_tape=None)),
        (ValueError, dict(folder=tmp_path, window=bad_window, clock=good_clock, read_tape=tape)),
        (LateWindow, dict(folder=tmp_path, window=w, clock=lambda: at(w.close_ts), read_tape=tape)),
    ]
    for exc, kw in cases:
        with pytest.raises(exc):
            log_tape(kw["folder"], kw["window"], posted=True, read_tape=kw["read_tape"], clock=kw["clock"],
                     budget=budget)
        assert budget.used == 0 and tape.calls == [], exc
    log_tape(tmp_path, w, posted=True, read_tape=tape, clock=good_clock, budget=budget)
    assert budget.used == 1 and len(tape.calls) == 1


def test_num_is_none_for_an_int_beyond_float_range_never_an_overflow(tmp_path):
    assert L._num(10 ** 400) is None and L._num(-(10 ** 400)) is None and L._num(10 ** 300) == float(10 ** 300)
    w = window(0)
    huge = Window(window_ticker=w.window_ticker, open_ts=w.open_ts, close_ts=w.close_ts, floor_strike=10 ** 400)
    rec, _, _ = run(tmp_path, huge)
    assert rec["floor_strike"] is None


# ---------------------------------------------------------------- every clock reading is checked (the Nth one too)

def _nth_liar_clock(true_value, n: int, folder: Path):
    """A clock that returns an exact datetime, except a lying subclass at its nth reading; it notes how many
    records the folder held at each reading."""
    state = {"calls": 0, "held": []}

    def clock():
        state["calls"] += 1
        state["held"].append(len(lines(folder)))
        real = true_value()
        return _claims_after(real) if state["calls"] == n else real
    return clock, state


def test_a_subclass_at_any_one_reading_is_refused_and_nothing_is_written_after_it(tmp_path):
    w0, w1 = window(0), window(1)
    tick_dir = tmp_path / "ticks"
    tick_dir.mkdir()
    runs = {
        "log_window": (lambda: at(w0.open_ts + 301.0), lambda f, c: log_window(
            f, w0, clock=c, read_ticks=tick_file_reader(tick_dir, clock=c), read_book=Spy(BOOK))),
        "log_close": (lambda: at(w0.close_ts + 195.0), lambda f, c: log_close(
            f, w0, clock=c, read_ticks=tick_file_reader(tick_dir, clock=c))),
        "log_tape": (lambda: at(w0.open_ts + 400.0), lambda f, c: log_tape(
            f, w0, posted=True, read_tape=Spy([]), clock=c, budget=TapeBudget(1))),
        "dry_run": (lambda: EF, lambda f, c: dry_run(
            f, c, [Fixture(w0, series(tstar_ms(w0)), BOOK), Fixture(w1, series(tstar_ms(w1)), BOOK)])),
        "check_enabled": (lambda: EF, lambda f, c: check_enabled(c)),
    }
    for name, (true_value, call) in runs.items():
        folder = tmp_path / name / "normal"
        clock, state = _nth_liar_clock(true_value, 0, folder)
        call(folder, clock)                                   # a normal run: count its readings
        n_readings = state["calls"]
        assert n_readings >= 1, name
        for n in range(1, n_readings + 1):
            folder = tmp_path / name / f"bad{n}"
            clock, state = _nth_liar_clock(true_value, n, folder)
            with pytest.raises(NaiveClock, match="not a subclass"):
                call(folder, clock)
            assert state["calls"] == n, (name, n)               # refused at that reading, none after it
            assert len(lines(folder)) == state["held"][n - 1], (name, n)


def test_every_tape_row_is_counted_in_exactly_one_field(tmp_path):
    w = window(0)
    t = w.open_ts + 300.0
    raw = ([{"trade_id": f"k{i}", "created_ts": t + i} for i in range(3)]                   # 3 kept
           + [{"trade_id": "early", "created_ts": t - 0.5}, {"trade_id": "late", "created_ts": t + 60.5}]   # 2 out
           + ["not a dict", {"trade_id": "no time"}, {"trade_id": "nan", "created_ts": float("nan")},
              {"trade_id": "huge", "created_ts": 10 ** 400}])                                  # 4 malformed
    log_tape(tmp_path, w, posted=True, read_tape=Spy(raw), clock=lambda: at(w.open_ts + 400.0), budget=TapeBudget(1))
    rec = lines(tmp_path)[0]                                       # from the written record alone
    assert rec["n_tape_rows_read"] == 9 and rec["n_rows"] == len(rec["rows"]) == 3
    assert rec["n_tape_rows_outside_span"] == 2 and rec["n_tape_rows_malformed"] == 4
    assert rec["n_tape_rows_read"] == rec["n_rows"] + rec["n_tape_rows_outside_span"] + rec["n_tape_rows_malformed"]

    def broken(*a):
        raise OSError("tape unreadable")
    log_tape(tmp_path / "e", w, posted=True, read_tape=broken, clock=lambda: at(w.open_ts + 400.0), budget=TapeBudget(1))
    err = lines(tmp_path / "e")[0]
    assert err["n_tape_rows_read"] == err["n_rows"] == err["n_tape_rows_outside_span"] == 0 and err["tape_error"]


def test_a_non_ascii_digit_string_is_not_a_whole_number_and_the_row_is_skipped(tmp_path):
    for bad in ("²", "١٢", "-²", "１２"):
        assert L._whole(bad) is None
    assert L._whole("12") == 12 and L._whole("-12") == -12
    w = window(0)
    T = tstar_ms(w)
    tick_dir = tmp_path / "ticks"
    tick_dir.mkdir()
    good = series(T, n=300)
    bad_rows = [dict(tick_row(T - 500), received_at_ms="²"), dict(tick_row(T - 400), received_at_ms="١٢")]
    day = datetime.fromtimestamp(T / 1000, UTC).strftime("%Y-%m-%d")
    _write(tick_dir, day, good[:150] + bad_rows + good[150:])
    got = read_tick_files(tick_dir, T - 300_000, T, clock=lambda: EF)          # no exception; both skipped
    assert len(got) == 300 and all(r["received_at_ms"] not in ("²", "١٢") for r in got)
    rec = log_window(tmp_path / "log", w, clock=lambda: EF, read_ticks=tick_file_reader(tick_dir, clock=lambda: EF),
                     read_book=Spy(BOOK))
    assert rec["n_rows_read"] == 300 and rec["n_rows_malformed"] == 0 and rec["tick_error"] is None
    direct, _, _ = run(tmp_path / "direct", w, ticks=good + bad_rows)             # given to the logger directly
    assert direct["n_rows_malformed"] == 2 and direct["n_rows_read"] == 302
