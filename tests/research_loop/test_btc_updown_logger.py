"""BTC 15-minute Up/Down forward logger: log-only, disabled until 2026-12-04 00:00 America/Los_Angeles.

Every fixture here is synthetic (placeholder exchange names, placeholder tickers). Nothing in this file touches the
network, a real store or a model. The module under test has no fetch of its own: quotes, the book and the tape arrive
through caller-supplied reader callables, and these tests pass fakes.
"""
from __future__ import annotations

import ast
import json
import math
import os
import socket
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from valedia_trader.research_loop import btc_updown_logger as L
from valedia_trader.research_loop.btc_updown_logger import (
    ENABLED_FROM, ExchangeQuote, Fixture, LateWindow, NaiveClock, NotYetEnabled, TapeBudget,
    TapeBudgetExceeded, Window, check_enabled, composite, default_trim, dry_run, log_tape, log_window,
    responding)

MODULE_PATH = Path(L.__file__)
MODULE_SRC = MODULE_PATH.read_text()
REPO = Path(__file__).resolve().parents[2]

SUBSET = ("exa", "exb", "exc", "exd")
BOOK = {"yes": [(0.49, 30.0), (0.48, 10.0)], "no": [(0.50, 25.0), (0.49, 5.0)]}


def at(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def window(i: int = 0, *, open_ts: float | None = None) -> Window:
    """A synthetic window that opens so that t* = ENABLED_FROM + i * 900 s (window 0: t* is the enabling instant)."""
    base = ENABLED_FROM.timestamp() - 300.0 + 900.0 * i if open_ts is None else open_ts
    return Window(window_ticker=f"KXBTC15M-TEST{i:03d}", open_ts=base, close_ts=base + 900.0,
                  floor_strike=100000.0 + i)


def quote(name: str, price: float | None = 100000.0, *, ok: bool = True, ts: float | None = 1.0,
          closes=(99990.0, 100000.0), error: str | None = None) -> ExchangeQuote:
    return ExchangeQuote(exchange=name, ok=ok, price=price, quote_ts=ts, closes_1m=tuple(closes), error=error)


def four_quotes(base: float = 100000.0) -> list[ExchangeQuote]:
    return [quote(n, base + k) for k, n in enumerate(SUBSET)]


class Spy:
    """A reader that records its calls and returns a fixed value."""
    def __init__(self, value):
        self.value, self.calls = value, []

    def __call__(self, *args):
        self.calls.append(args)
        return self.value


def run(folder, w, *, clock=None, quotes=None, book=BOOK, subset=SUBSET, **kw):
    q = Spy(four_quotes() if quotes is None else quotes)
    b = Spy(book)
    clk = clock if clock is not None else (lambda: at(w.open_ts + 300.0))
    rec = log_window(folder, w, clock=clk, read_quotes=q, read_book=b, frozen_subset=subset, **kw)
    return rec, q, b


def lines(folder: Path) -> list[dict]:
    p = folder / L.LOG_NAME
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


# ---------------------------------------------------------------- 1 and 2: the date gate

def test_enabled_from_is_the_ruled_instant():
    assert ENABLED_FROM.tzinfo is not None
    assert ENABLED_FROM == datetime(2026, 12, 4, 8, 0, 0, tzinfo=timezone.utc)
    assert ENABLED_FROM == datetime(2026, 12, 4, 0, 0, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
    # the opposite reading (UTC midnight, eight hours earlier) is not the constant
    assert ENABLED_FROM != datetime(2026, 12, 4, 0, 0, 0, tzinfo=timezone.utc)


def test_check_enabled_one_second_before_refused_and_at_instant_allowed():
    with pytest.raises(NotYetEnabled):
        check_enabled(lambda: ENABLED_FROM - timedelta(seconds=1))
    assert check_enabled(lambda: ENABLED_FROM) is None
    assert check_enabled(lambda: ENABLED_FROM + timedelta(seconds=1)) is None


def test_gate_compares_instants_not_wall_clock_digits():
    # 2026-12-03 23:59:59 in Los Angeles is one second before the instant, whatever the digits say
    la = ZoneInfo("America/Los_Angeles")
    with pytest.raises(NotYetEnabled):
        check_enabled(lambda: datetime(2026, 12, 3, 23, 59, 59, tzinfo=la))
    assert check_enabled(lambda: datetime(2026, 12, 4, 0, 0, 0, tzinfo=la)) is None
    # a +09:00 clock reading 17:00 on 12-04 is 08:00 UTC: exactly the instant
    assert check_enabled(lambda: datetime(2026, 12, 4, 17, 0, 0, tzinfo=timezone(timedelta(hours=9)))) is None


def test_naive_clock_refused_both_before_and_after_the_date():
    for naive in (datetime(2026, 12, 3, 0, 0, 0), datetime(2027, 1, 1, 0, 0, 0)):
        with pytest.raises(NaiveClock):
            check_enabled(lambda n=naive: n)


def test_log_window_before_the_instant_makes_no_read_and_no_file(tmp_path):
    w = window(0)
    folder = tmp_path / "store"
    q, b = Spy(four_quotes()), Spy(BOOK)
    with pytest.raises(NotYetEnabled):
        log_window(folder, w, clock=lambda: ENABLED_FROM - timedelta(seconds=1), read_quotes=q, read_book=b,
                   frozen_subset=SUBSET)
    assert q.calls == [] and b.calls == []
    assert not folder.exists()
    assert list(tmp_path.iterdir()) == []


def test_log_window_at_the_instant_is_allowed(tmp_path):
    w = window(0)
    q, b = Spy(four_quotes()), Spy(BOOK)
    rec = log_window(tmp_path, w, clock=lambda: ENABLED_FROM, read_quotes=q, read_book=b, frozen_subset=SUBSET)
    assert len(q.calls) == 1 and len(b.calls) == 1
    assert rec["status"] == "OK"
    assert len(lines(tmp_path)) == 1


def test_gate_comes_before_argument_checks(tmp_path):
    # before the instant, whatever the arguments: the gate answers first (empty subset, closed window)
    early = lambda: ENABLED_FROM - timedelta(seconds=1)
    q, b = Spy([]), Spy(None)
    with pytest.raises(NotYetEnabled):
        log_window(tmp_path, window(0), clock=early, read_quotes=q, read_book=b, frozen_subset=())
    closed = Window("KXBTC15M-TEST", 0.0, 900.0, 1.0)
    with pytest.raises(NotYetEnabled):
        log_window(tmp_path, closed, clock=early, read_quotes=q, read_book=b, frozen_subset=SUBSET)
    assert q.calls == [] and b.calls == [] and list(tmp_path.iterdir()) == []


def test_log_tape_gate_both_ways(tmp_path):
    w = window(0)
    t_star = w.open_ts + 300.0
    spy = Spy([])
    with pytest.raises(NotYetEnabled):
        log_tape(tmp_path, w, posted=True, read_tape=spy, clock=lambda: ENABLED_FROM - timedelta(seconds=1),
                 budget=TapeBudget(5))
    assert spy.calls == [] and list(tmp_path.iterdir()) == []
    out = log_tape(tmp_path, w, posted=True, read_tape=spy, clock=lambda: ENABLED_FROM, budget=TapeBudget(5))
    assert len(spy.calls) == 1 and out is not None
    assert t_star == ENABLED_FROM.timestamp()


def test_dry_run_gate_both_ways(tmp_path):
    fx = [Fixture(window(0), four_quotes(), BOOK)]
    with pytest.raises(NotYetEnabled):
        dry_run(tmp_path / "a", lambda: ENABLED_FROM - timedelta(seconds=1), fx, SUBSET)
    assert not (tmp_path / "a").exists()
    assert len(dry_run(tmp_path / "b", lambda: ENABLED_FROM, fx, SUBSET)) == 1


# ---------------------------------------------------------------- 3: no network, interfaces only

def test_whole_dry_run_passes_with_sockets_forbidden(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("a socket was opened")
    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    now = ENABLED_FROM + timedelta(hours=1)
    base = now.timestamp() - 300.0
    fx = [Fixture(window(i, open_ts=base + 900.0 * i), four_quotes(), BOOK) for i in range(5)]
    recs = dry_run(tmp_path, lambda: now, fx, SUBSET)
    assert len(recs) == 5 and len(lines(tmp_path)) == 5


def test_no_default_reader_folder_or_subset():
    import inspect
    for fn in (log_window, log_tape, dry_run):
        params = inspect.signature(fn).parameters
        assert params["folder"].default is inspect.Parameter.empty
    assert inspect.signature(log_window).parameters["frozen_subset"].default is inspect.Parameter.empty
    assert inspect.signature(log_window).parameters["read_quotes"].default is inspect.Parameter.empty
    assert inspect.signature(log_window).parameters["read_book"].default is inspect.Parameter.empty
    assert inspect.signature(log_tape).parameters["read_tape"].default is inspect.Parameter.empty
    # opposite: the trim IS defaulted (the parameter has a default rule, ruling 3)
    assert inspect.signature(log_window).parameters["trim"].default is default_trim


def test_missing_reader_is_refused_not_defaulted(tmp_path):
    w = window(0)
    with pytest.raises(TypeError):
        log_window(tmp_path, w, clock=lambda: at(w.open_ts + 300.0), read_book=Spy(BOOK), frozen_subset=SUBSET)
    with pytest.raises(ValueError):
        log_window(tmp_path, w, clock=lambda: at(w.open_ts + 300.0), read_quotes=None, read_book=Spy(BOOK),
                   frozen_subset=SUBSET)
    assert lines(tmp_path) == []


def test_readers_called_exactly_once_each_with_the_window(tmp_path):
    w = window(3)
    rec, q, b = run(tmp_path, w)
    assert q.calls == [(w, w.open_ts + 300.0)]
    assert b.calls == [(w.window_ticker,)]


# ---------------------------------------------------------------- 5: own store

def test_record_lands_in_the_given_folder_only(tmp_path):
    folder = tmp_path / "given"
    rec, _, _ = run(folder, window(0))
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


# ---------------------------------------------------------------- 6: one record per window, skips included

def test_record_fields_for_an_ok_window(tmp_path):
    w = window(2)
    rec, _, _ = run(tmp_path, w)
    saved = lines(tmp_path)[0]
    assert saved == json.loads(json.dumps(rec))
    assert saved["kind"] == "window" and saved["schema_version"] == L.SCHEMA_VERSION
    assert saved["window_ticker"] == w.window_ticker
    assert saved["open_ts"] == w.open_ts and saved["close_ts"] == w.close_ts
    assert saved["t_star"] == w.open_ts + 300.0
    assert saved["floor_strike"] == w.floor_strike
    assert saved["written_at"] == w.open_ts + 300.0 and saved["written_at"] < saved["close_ts"]
    assert saved["status"] == "OK" and saved["skip_reasons"] == []
    assert saved["n_responding"] == 4 and saved["price"] == 100001.5
    assert [e["exchange"] for e in saved["exchanges"]] == list(SUBSET)
    assert all(e["ok"] and e["counted"] and e["error"] is None for e in saved["exchanges"])
    assert saved["exchanges"][0]["closes_1m"] == [99990.0, 100000.0]
    book = saved["book"]
    assert book["yes"] == [[0.49, 30.0], [0.48, 10.0]] and book["no"] == [[0.5, 25.0], [0.49, 5.0]]
    assert book["best_bid"] == 0.49 and book["best_bid_size"] == 30.0
    assert book["best_ask"] == 0.5 and book["best_ask_size"] == 25.0 and book["spread"] == 0.01


def test_record_holds_no_decision_fields(tmp_path):
    rec, _, _ = run(tmp_path, window(0))
    blob = json.dumps(rec).lower()
    for word in ("roc3", "range5", "trigger", "fill", "eligible", "gate", "q_hat", "edge"):
        assert word not in blob, word
    for word in ("roc3", "range5", "def trigger", "def fill", "gates"):
        assert word not in MODULE_SRC.lower(), word


def test_twenty_windows_one_record_each_skips_included(tmp_path):
    base = (ENABLED_FROM + timedelta(hours=3)).timestamp()
    expect = {}
    for i in range(20):
        w = window(i, open_ts=base + 900.0 * i)
        quotes, book = four_quotes(), BOOK
        if i == 5:
            quotes = [quote("exa"), quote("exb")]                 # only 2 respond
            expect[i] = "SKIP_FEED"
        elif i == 9:
            book = None                                           # no book
            expect[i] = "SKIP_NO_BOOK"
        else:
            expect[i] = "OK"
        run(tmp_path, w, quotes=quotes, book=book)
    got = lines(tmp_path)
    assert len(got) == 20
    assert [r["window_ticker"] for r in got] == [f"KXBTC15M-TEST{i:03d}" for i in range(20)]
    assert [r["status"] for r in got] == [expect[i] for i in range(20)]
    assert all(r["written_at"] < r["close_ts"] for r in got)
    assert got[5]["price"] is None and got[5]["n_responding"] == 2
    assert got[9]["book"] is None and got[9]["price"] is not None


def test_skip_feed_and_missing_book_together_name_both_reasons(tmp_path):
    rec, _, _ = run(tmp_path, window(0), quotes=[quote("exa")], book=None)
    assert rec["status"] == "SKIP_FEED"
    assert rec["skip_reasons"] == ["SKIP_FEED", "SKIP_NO_BOOK"]
    assert len(lines(tmp_path)) == 1


def test_late_window_is_refused_before_any_read(tmp_path):
    w = window(0)
    q, b = Spy(four_quotes()), Spy(BOOK)
    with pytest.raises(LateWindow):
        log_window(tmp_path, w, clock=lambda: at(w.close_ts), read_quotes=q, read_book=b, frozen_subset=SUBSET)
    assert q.calls == [] and b.calls == [] and lines(tmp_path) == []


def test_window_that_closes_during_the_reads_is_refused_unwritten(tmp_path):
    w = window(0)
    now = [w.open_ts + 300.0]

    def slow_quotes(win, t_star):
        now[0] = w.close_ts + 1.0          # the reads ran past the close
        return four_quotes()

    with pytest.raises(LateWindow):
        log_window(tmp_path, w, clock=lambda: at(now[0]), read_quotes=slow_quotes, read_book=Spy(BOOK),
                   frozen_subset=SUBSET)
    assert lines(tmp_path) == []
    # opposite: the same reads finishing one second before the close are written
    now[0] = w.open_ts + 300.0

    def quick_quotes(win, t_star):
        now[0] = w.close_ts - 1.0
        return four_quotes()

    rec = log_window(tmp_path, w, clock=lambda: at(now[0]), read_quotes=quick_quotes, read_book=Spy(BOOK),
                     frozen_subset=SUBSET)
    assert rec["written_at"] == w.close_ts - 1.0 and len(lines(tmp_path)) == 1


def test_empty_frozen_subset_refused_after_the_date(tmp_path):
    w = window(0)
    q, b = Spy([]), Spy(BOOK)
    with pytest.raises(ValueError):
        log_window(tmp_path, w, clock=lambda: at(w.open_ts + 300.0), read_quotes=q, read_book=b, frozen_subset=())
    assert q.calls == [] and b.calls == [] and lines(tmp_path) == []
    with pytest.raises(ValueError):   # opposite shape of the same mistake: a bare string is not a subset
        log_window(tmp_path, w, clock=lambda: at(w.open_ts + 300.0), read_quotes=q, read_book=b,
                   frozen_subset="exa")
    rec, _, _ = run(tmp_path, w, subset=("exa",), quotes=[quote("exa")])
    assert rec["status"] == "SKIP_FEED"   # a subset of one runs, and every window is a skip


def test_reader_errors_are_logged_not_lost(tmp_path):
    w = window(0)

    def bad_quotes(win, t):
        raise RuntimeError("feed down")

    def bad_book(ticker):
        raise RuntimeError("book down")

    rec = log_window(tmp_path, w, clock=lambda: at(w.open_ts + 300.0), read_quotes=bad_quotes, read_book=bad_book,
                     frozen_subset=SUBSET)
    assert rec["status"] == "SKIP_FEED" and rec["skip_reasons"] == ["SKIP_FEED", "SKIP_NO_BOOK"]
    assert "feed down" in rec["feed_error"] and "book down" in rec["book_error"]
    assert len(lines(tmp_path)) == 1
    ok, _, _ = run(tmp_path, window(1))
    assert ok["feed_error"] is None and ok["book_error"] is None


def test_non_finite_values_never_reach_the_file(tmp_path):
    qs = [quote("exa", float("nan")), quote("exb", float("inf")), quote("exc", 100000.0, closes=(float("nan"), 1.0)),
          quote("exd", 100001.0, ts=float("nan"))]
    rec, _, _ = run(tmp_path, window(0), quotes=qs)
    text = (tmp_path / L.LOG_NAME).read_text()
    assert "NaN" not in text and "Infinity" not in text
    json.loads(text)
    assert rec["n_responding"] == 2 and rec["status"] == "SKIP_FEED"
    by = {e["exchange"]: e for e in rec["exchanges"]}
    assert by["exa"]["price"] is None and by["exa"]["counted"] is False and by["exa"]["error"] == "invalid_price"
    assert by["exc"]["closes_1m"] == [None, 1.0] and by["exd"]["quote_ts"] is None


def test_missing_and_outside_exchange_entries(tmp_path):
    qs = [quote("exa"), quote("exb"), quote("exc"), quote("zzz-outside", 1.0)]
    rec, _, _ = run(tmp_path, window(0), quotes=qs)
    assert [e["exchange"] for e in rec["exchanges"]] == list(SUBSET)       # outside one is not logged as a member
    missing = rec["exchanges"][3]
    assert missing["ok"] is False and missing["counted"] is False and missing["error"] == "no_quote"
    assert rec["n_responding"] == 3 and rec["status"] == "OK"


def test_book_with_one_empty_side(tmp_path):
    rec, _, _ = run(tmp_path, window(0), book={"yes": [(0.30, 4.0)], "no": []})
    b = rec["book"]
    assert b["best_bid"] == 0.3 and b["best_ask"] is None and b["spread"] is None and b["best_ask_size"] is None
    assert rec["status"] == "OK"        # an empty side is still a book; only a missing book is SKIP_NO_BOOK
    rec2, _, _ = run(tmp_path, window(1), book={"yes": [], "no": []})
    assert rec2["book"]["best_bid"] is None and rec2["status"] == "OK"


def test_book_order_does_not_matter(tmp_path):
    shuffled = {"yes": [(0.48, 10.0), (0.49, 30.0)], "no": [(0.49, 5.0), (0.50, 25.0)]}
    a, _, _ = run(tmp_path, window(0), book=BOOK)
    b, _, _ = run(tmp_path, window(0), book=shuffled)
    assert a["book"]["best_bid"] == b["book"]["best_bid"] == 0.49
    assert a["book"]["best_ask"] == b["book"]["best_ask"] == 0.5
    assert a["book"]["best_ask_size"] == b["book"]["best_ask_size"] == 25.0


# ---------------------------------------------------------------- 7: composite

def test_composite_three_and_four_responders_use_the_plain_median():
    assert composite([quote("exa", 100.0), quote("exb", 101.0), quote("exc", 200.0)], SUBSET) == 101.0
    four = [quote("exa", 100.0), quote("exb", 101.0), quote("exc", 102.0), quote("exd", 300.0)]
    assert composite(four, SUBSET) == 101.5


def test_composite_five_or_more_drops_highest_and_lowest_then_median():
    five = [quote(n, p) for n, p in zip(("a", "b", "c", "d", "e"), (10.0, 100.0, 101.0, 102.0, 1000.0))]
    sub = ("a", "b", "c", "d", "e", "f")
    assert default_trim([10.0, 100.0, 101.0, 102.0, 1000.0]) == [100.0, 101.0, 102.0]
    assert composite(five, sub) == 101.0
    six = five + [quote("f", 103.0)]
    assert default_trim([10.0, 100.0, 101.0, 102.0, 103.0, 1000.0]) == [100.0, 101.0, 102.0, 103.0]
    assert composite(six, sub) == 101.5


def test_default_trim_leaves_three_and_four_untouched():
    assert default_trim([3.0, 1.0, 2.0]) == [1.0, 2.0, 3.0]
    assert default_trim([4.0, 1.0, 3.0, 2.0]) == [1.0, 2.0, 3.0, 4.0]


def test_trim_is_a_parameter():
    five = [quote(n, p) for n, p in zip("abcde", (100.0, 101.0, 102.0, 103.0, 104.0))]
    sub = tuple("abcde")
    drop_high_only = lambda vals: sorted(vals)[:-1]
    assert composite(five, sub) == 102.0                       # default
    assert composite(five, sub, trim=drop_high_only) == 101.5  # another rule changes the answer
    assert composite(five, sub, trim=lambda v: sorted(v)) == 102.0   # no trim at all, here equal to the default


def test_two_responders_is_none_and_skip_feed(tmp_path):
    two = [quote("exa", 100.0), quote("exb", 101.0)]
    assert composite(two, SUBSET) is None
    assert composite([], SUBSET) is None
    rec, _, _ = run(tmp_path, window(0), quotes=two)
    assert rec["status"] == "SKIP_FEED" and rec["price"] is None and rec["n_responding"] == 2
    # opposite: three responders is a price
    assert composite(two + [quote("exc", 102.0)], SUBSET) == 101.0


def test_exchange_outside_the_frozen_subset_is_ignored():
    three_in = [quote("exa", 100.0), quote("exb", 101.0), quote("exc", 102.0)]
    assert composite(three_in + [quote("zzz", 9999.0)], SUBSET) == 101.0          # extra outsider changes nothing
    two_in_one_out = [quote("exa", 100.0), quote("exb", 101.0), quote("zzz", 102.0)]
    assert composite(two_in_one_out, SUBSET) is None                              # the outsider is not a third


@pytest.mark.parametrize("bad", [float("nan"), 0.0, -5.0, float("inf"), float("-inf"), None])
def test_non_response_prices_do_not_count(bad):
    qs = [quote("exa", 100.0), quote("exb", 101.0), quote("exc", bad)]
    assert composite(qs, SUBSET) is None
    assert len(responding(qs, SUBSET)) == 2
    # opposite: a small positive finite price counts
    assert len(responding([quote("exa", 100.0), quote("exb", 101.0), quote("exc", 0.01)], SUBSET)) == 3


def test_not_ok_with_a_price_does_not_count_and_ok_without_a_price_does_not_count():
    qs = [quote("exa", 100.0), quote("exb", 101.0), quote("exc", 102.0, ok=False, error="timeout")]
    assert composite(qs, SUBSET) is None
    qs2 = [quote("exa", 100.0), quote("exb", 101.0), quote("exc", None)]
    assert composite(qs2, SUBSET) is None


def test_boolean_price_is_not_a_price():
    qs = [quote("exa", 100.0), quote("exb", 101.0), quote("exc", True)]
    assert composite(qs, SUBSET) is None


def test_duplicate_quote_for_one_exchange_counts_once():
    qs = [quote("exa", 100.0), quote("exa", 500.0), quote("exb", 101.0)]
    assert len(responding(qs, SUBSET)) == 2 and composite(qs, SUBSET) is None
    assert composite(qs + [quote("exc", 102.0)], SUBSET) == 101.0     # first quote for exa kept: 100, 101, 102


def test_outlier_and_no_outlier_cases():
    clean = [quote(n, p) for n, p in zip("abcde", (100.0, 100.5, 101.0, 101.5, 102.0))]
    spiked = [quote(n, p) for n, p in zip("abcde", (100.0, 100.5, 101.0, 101.5, 9999.0))]
    sub = tuple("abcde")
    assert composite(clean, sub) == 101.0
    assert composite(spiked, sub) == 101.0       # the spike does not move the price
    assert math.isfinite(composite(spiked, sub))


# ---------------------------------------------------------------- 8: imports, no LLM

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
    assert roots <= {"__future__", "collections", "dataclasses", "datetime", "json", "math", "os", "pathlib",
                     "statistics", "typing"}, roots
    forbidden = {"urllib", "http", "socket", "requests", "subprocess", "ssl", "asyncio", "valedia_trader",
                 "anthropic", "openai"}
    assert not (roots & forbidden)
    for name in ("orderbook_loop", "second_quote", "execution_and_risk", "kalshi_data", "webull", "decision_model",
                 "hermes", "agents", "legacy"):
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
            "('orderbook_loop','second_quote','kalshi_data','webull','execution_and_risk','agents','urllib'))]\n"
            "bad += [m for m in ('urllib.request','http.client','requests') if m in sys.modules]\n"
            "print(sorted(bad))\n")
    env = {"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(REPO)}
    out = subprocess.run([sys.executable, "-c", code], cwd=str(REPO), env=env, capture_output=True, text=True,
                         timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]", out.stdout


# ---------------------------------------------------------------- 9: tape

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
    assert saved[0]["t_from"] == w.open_ts + 300.0 and saved[0]["t_to"] == w.open_ts + 360.0
    assert saved[0]["written_at"] == w.open_ts + 400.0 and saved[0]["written_at"] < w.close_ts
    assert saved[0]["n_rows"] == 3 and [p.name for p in tmp_path.iterdir()] == [L.LOG_NAME]


def test_tape_not_posted_makes_no_read_and_spends_no_budget(tmp_path):
    w = window(0)
    spy = Spy(_rows(w))
    budget = TapeBudget(1)
    assert log_tape(tmp_path, w, posted=False, read_tape=spy, clock=lambda: at(w.open_ts + 400.0),
                    budget=budget) is None
    assert spy.calls == [] and budget.used == 0 and lines(tmp_path) == []
    # opposite: the posted window right after it still has its one read
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


# ---------------------------------------------------------------- 10: dry run

def _fixtures(now: datetime, n: int = 20):
    base = now.timestamp() - 300.0
    out = []
    for i in range(n):
        w = window(i, open_ts=base + 900.0 * i)
        if i == 4:
            out.append(Fixture(w, [quote("exa"), quote("exb")], BOOK))
        elif i == 7:
            out.append(Fixture(w, four_quotes(100000.0 + i), None))
        else:
            out.append(Fixture(w, four_quotes(100000.0 + i), BOOK))
    return out


def test_dry_run_one_record_per_window_no_sockets_no_extra_files(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(socket, "socket", lambda *a, **k: opened.append(a) or (_ for _ in ()).throw(OSError("no")))
    now = ENABLED_FROM + timedelta(days=1)
    recs = dry_run(tmp_path, lambda: now, _fixtures(now), SUBSET)
    assert len(recs) == 20 and len(lines(tmp_path)) == 20
    assert opened == []
    assert [r["status"] for r in recs].count("SKIP_FEED") == 1 and [r["status"] for r in recs].count("SKIP_NO_BOOK") == 1
    assert [p.name for p in tmp_path.iterdir()] == [L.LOG_NAME]


def test_dry_run_is_deterministic_byte_for_byte(tmp_path):
    now = ENABLED_FROM + timedelta(days=1)
    dry_run(tmp_path / "one", lambda: now, _fixtures(now), SUBSET)
    dry_run(tmp_path / "two", lambda: now, _fixtures(now), SUBSET)
    a = (tmp_path / "one" / L.LOG_NAME).read_bytes()
    b = (tmp_path / "two" / L.LOG_NAME).read_bytes()
    assert a == b and len(a) > 0
    # opposite: different fixtures give different bytes
    other = _fixtures(now)
    other[0] = Fixture(other[0].window, four_quotes(5.0), BOOK)
    dry_run(tmp_path / "three", lambda: now, other, SUBSET)
    assert (tmp_path / "three" / L.LOG_NAME).read_bytes() != a


def test_dry_run_takes_no_url_or_fetch():
    import inspect
    names = set(inspect.signature(dry_run).parameters)
    assert not (names & {"url", "fetch", "base_url", "headers", "timeout"})


def test_dry_run_into_a_folder_with_old_records_is_refused(tmp_path):
    now = ENABLED_FROM + timedelta(days=1)
    dry_run(tmp_path, lambda: now, _fixtures(now, 2), SUBSET)
    with pytest.raises(ValueError):
        dry_run(tmp_path, lambda: now, _fixtures(now, 2), SUBSET)
    assert len(lines(tmp_path)) == 2


# ---------------------------------------------------------------- 11: no schedule

def test_no_schedule_or_daemon_words_in_the_module():
    low = MODULE_SRC.lower()
    for word in (".plist", "launchctl", "crontab", "schedule", "__main__", "while true", "sleep(", "daemon",
                 "argparse", "threading"):
        assert word not in low, word


def test_no_cli_registration():
    cli = (REPO / "valedia_trader" / "research_loop" / "cli.py").read_text()
    assert "btc_updown" not in cli


# ---------------------------------------------------------------- round 1 repairs

class SeqClock:
    """A clock that returns the given instants in order (the last one forever) and counts its readings."""
    def __init__(self, instants):
        self.instants, self.reads = list(instants), 0

    def __call__(self):
        i = min(self.reads, len(self.instants) - 1)
        self.reads += 1
        return self.instants[i]


EF = ENABLED_FROM
ONE_S = timedelta(seconds=1)


def test_log_window_stamps_written_at_from_the_reading_it_gated_on(tmp_path):
    # a clock that steps back after the gate passed must not give a record stamped before the instant
    clk = SeqClock([EF, EF, EF, EF - ONE_S])
    rec = log_window(tmp_path, window(0), clock=clk, read_quotes=Spy(four_quotes()), read_book=Spy(BOOK),
                     frozen_subset=SUBSET)
    assert rec["written_at"] == EF.timestamp()
    assert lines(tmp_path)[0]["written_at"] >= EF.timestamp()
    # opposite: a clock that never steps back writes the reading it gated on
    clk2 = SeqClock([EF, EF + ONE_S])
    rec2 = log_window(tmp_path, window(1), clock=clk2, read_quotes=Spy(four_quotes()), read_book=Spy(BOOK),
                      frozen_subset=SUBSET)
    assert rec2["written_at"] == (EF + ONE_S).timestamp()


def test_log_window_refuses_when_the_clock_steps_back_before_the_instant_at_write_time(tmp_path):
    clk = SeqClock([EF, EF - ONE_S, EF, EF])
    with pytest.raises(NotYetEnabled):
        log_window(tmp_path, window(0), clock=clk, read_quotes=Spy(four_quotes()), read_book=Spy(BOOK),
                   frozen_subset=SUBSET)
    assert lines(tmp_path) == []


def test_log_window_reads_the_clock_exactly_twice(tmp_path):
    clk = SeqClock([EF, EF])
    log_window(tmp_path, window(0), clock=clk, read_quotes=Spy(four_quotes()), read_book=Spy(BOOK),
               frozen_subset=SUBSET)
    assert clk.reads == 2          # one before the readers (gate and close check), one after (gate, close, stamp)


def test_log_tape_stamps_written_at_from_the_reading_it_gated_on(tmp_path):
    w = window(0)
    clk = SeqClock([EF, EF, EF, EF - ONE_S])
    out = log_tape(tmp_path, w, posted=True, read_tape=Spy([]), clock=clk, budget=TapeBudget(2))
    assert out["written_at"] == EF.timestamp() and lines(tmp_path)[0]["written_at"] == EF.timestamp()
    clk2 = SeqClock([EF, EF - ONE_S, EF, EF])
    with pytest.raises(NotYetEnabled):
        log_tape(tmp_path / "b", w, posted=True, read_tape=Spy([]), clock=clk2, budget=TapeBudget(2))
    assert lines(tmp_path / "b") == []
    clk3 = SeqClock([EF, EF])
    log_tape(tmp_path / "c", w, posted=True, read_tape=Spy([]), clock=clk3, budget=TapeBudget(2))
    assert clk3.reads == 2


def test_best_ask_is_one_minus_the_best_no_bid_on_an_asymmetric_book(tmp_path):
    rec, _, _ = run(tmp_path, window(0), book={"yes": [(0.55, 3.0)], "no": [(0.40, 5.0), (0.30, 9.0)]})
    b = rec["book"]
    assert b["best_bid"] == 0.55 and b["best_ask"] == 0.6 and b["best_ask_size"] == 5.0 and b["spread"] == 0.05
    # opposite: the ask is not the best no bid, and a different best no bid gives a different ask
    assert b["best_ask"] != 0.40
    rec2, _, _ = run(tmp_path, window(1), book={"yes": [(0.55, 3.0)], "no": [(0.45, 5.0)]})
    assert rec2["book"]["best_ask"] == 0.55 and rec2["book"]["spread"] == 0.0


def test_window_finishing_at_exactly_close_is_refused_unwritten(tmp_path):
    w = window(0)
    clk = SeqClock([at(w.open_ts + 300.0), at(w.close_ts)])
    with pytest.raises(LateWindow):
        log_window(tmp_path, w, clock=clk, read_quotes=Spy(four_quotes()), read_book=Spy(BOOK), frozen_subset=SUBSET)
    assert lines(tmp_path) == []
    # opposite: one second earlier is written
    clk2 = SeqClock([at(w.open_ts + 300.0), at(w.close_ts - 1.0)])
    rec = log_window(tmp_path, w, clock=clk2, read_quotes=Spy(four_quotes()), read_book=Spy(BOOK),
                     frozen_subset=SUBSET)
    assert rec["written_at"] == w.close_ts - 1.0


def test_tape_finishing_at_exactly_close_is_refused_unwritten(tmp_path):
    w = window(0)
    clk = SeqClock([at(w.open_ts + 400.0), at(w.close_ts)])
    with pytest.raises(LateWindow):
        log_tape(tmp_path, w, posted=True, read_tape=Spy([]), clock=clk, budget=TapeBudget(2))
    assert lines(tmp_path) == []
    clk2 = SeqClock([at(w.open_ts + 400.0), at(w.close_ts - 1.0)])
    assert log_tape(tmp_path, w, posted=True, read_tape=Spy([]), clock=clk2, budget=TapeBudget(2)) is not None


@pytest.mark.parametrize("bad", ["", ".", Path(""), Path(".")])
def test_empty_or_current_directory_folder_is_refused_after_the_date(tmp_path, monkeypatch, bad):
    monkeypatch.chdir(tmp_path)
    w = window(0)
    q, b = Spy(four_quotes()), Spy(BOOK)
    with pytest.raises(ValueError):
        log_window(bad, w, clock=lambda: at(w.open_ts + 300.0), read_quotes=q, read_book=b, frozen_subset=SUBSET)
    with pytest.raises(ValueError):
        log_tape(bad, w, posted=True, read_tape=Spy([]), clock=lambda: at(w.open_ts + 400.0), budget=TapeBudget(2))
    with pytest.raises(ValueError):
        dry_run(bad, lambda: EF, [Fixture(w, four_quotes(), BOOK)], SUBSET)
    assert q.calls == [] and b.calls == [] and list(tmp_path.iterdir()) == []
    # the date gate still answers first, whatever the folder
    with pytest.raises(NotYetEnabled):
        log_window(bad, w, clock=lambda: EF - ONE_S, read_quotes=q, read_book=b, frozen_subset=SUBSET)
    # opposite: a named folder works
    log_window(tmp_path / "named", w, clock=lambda: at(w.open_ts + 300.0), read_quotes=q, read_book=b,
               frozen_subset=SUBSET)
    assert len(lines(tmp_path / "named")) == 1


class _Odd:
    """A quote-like object missing fields (and with a non-string error): a reader's mistake, not a lost window."""
    def __init__(self, **kw):
        self.__dict__.update(kw)


@pytest.mark.parametrize("odd", [
    _Odd(exchange="exa"),
    _Odd(exchange="exa", price=100000.0),
    _Odd(exchange="exa", ok=True),
    _Odd(exchange="exa", ok=True, price="abc", quote_ts="x", closes_1m=5, error=ValueError("boom")),
])
def test_odd_quote_object_is_logged_not_lost(tmp_path, odd):
    qs = [odd, quote("exb"), quote("exc"), quote("exd")]
    rec, _, _ = run(tmp_path, window(0), quotes=qs)
    saved = lines(tmp_path)
    assert len(saved) == 1
    first = saved[0]["exchanges"][0]
    assert first["exchange"] == "exa" and first["counted"] is False
    assert saved[0]["n_responding"] == 3 and saved[0]["status"] == "OK"
    assert first["error"] is None or isinstance(first["error"], str)
    # opposite: a well-formed quote for the same exchange counts
    rec2, _, _ = run(tmp_path, window(1), quotes=four_quotes())
    assert rec2["n_responding"] == 4
