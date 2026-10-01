#!/usr/bin/env python3
"""allocator_paths: scanner + spot execute routing behind default-OFF flags.

Flags off → SKIP_SCANNER_DISABLED / SKIP_ALLOCATOR_SPOT_DISABLED, no orders.
Flags on with fakes → the full Kalshi gate chain runs for kalshi_scanner, and
the spot path calls the real writer names (coinbase_trader.market_buy,
alpaca_executor.market_buy) after venue_writer + halt, with fill-truth.
No network (socket connect raises), no keys, no --live. Temp ledger and halt file.
Run: python3 test_allocator_paths.py
"""
import contextlib
import os
import socket
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["OPENCLAW_SKIP_ENV"] = "1"
for _k in ("VALEDIA_SCANNER_EXECUTE", "VALEDIA_ALLOCATOR_SPOT_EXECUTE", "VALEDIA_TRADING_HALT",
           "ALPACA_PAPER", "VALEDIA_SYSTEMONE"):
    os.environ.pop(_k, None)
_TMP = tempfile.mkdtemp(prefix="allocpaths_")
os.environ["VALEDIA_TRADING_HALT_FILE"] = os.path.join(_TMP, "no_halt.json")


def _no_net(*a, **k):
    raise AssertionError("network call from test_allocator_paths")


socket.socket.connect = _no_net

import alpaca_executor as ae  # noqa: E402
import allocator_paths as ap  # noqa: E402
import coinbase_trader as ct  # noqa: E402
import forward_test_ledger as ftl  # noqa: E402
import informed_before_create as ibc  # noqa: E402
import kalshi_auth  # noqa: E402
import kalshi_profit as kp  # noqa: E402
import master_allocator as ma  # noqa: E402
import nautilus_shadow_coinbase as nsc  # noqa: E402
import nimble_wiring as nw  # noqa: E402
import session_clocks as sc  # noqa: E402
import valedia_config as vc  # noqa: E402
from test_kalshi_fill_ledger import _admitted_sports_opp, _fill  # noqa: E402

FV_KEYS = ("fv", "fv_source", "fv_confidence")


@contextlib.contextmanager
def patched(*triples):
    orig = [(o, n, getattr(o, n)) for o, n, _ in triples]
    for o, n, v in triples:
        setattr(o, n, v)
    try:
        yield
    finally:
        for o, n, v in reversed(orig):
            setattr(o, n, v)


@contextlib.contextmanager
def env(**kv):
    prev = {k: os.environ.get(k) for k in kv}
    for k, v in kv.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        yield
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class World:
    """Fake venue + spies on every allocator gate. Orders counted at the fakes."""

    def __init__(self, kalshi_resp=None, cb_resp=None, alp_resp=None):
        self.calls = []
        self.orders = []
        self.books = []
        self.kalshi_resp = kalshi_resp or _fill("scan-fill", 5, 80)
        self.cb_resp = cb_resp or {"success": True, "order_id": "cb-1", "filled_size": "0.0001",
                                   "average_filled_price": "40000", "total_fees": "0.02"}
        self.alp_resp = alp_resp or {"id": "alp-1", "status": "filled", "filled_qty": "0.5",
                                     "filled_avg_price": "8.0"}

    def spy(self, name, fn):
        def w(*a, **k):
            self.calls.append(name)
            return fn(*a, **k)
        return w

    def _intel(self):
        world = self

        class _I:
            def get_kalshi_conviction(self, *a, **k):
                world.calls.append("intel")
                return {"recommended_side": "yes", "confidence": 0.2, "conviction_yes": 50}
        mod = types.ModuleType("intel_layer")
        mod.get_intel = lambda: _I()
        return mod

    def _create(self, *a, **k):
        self.orders.append(("kalshi", a, k))
        return self.kalshi_resp

    def _cb_buy(self, pair, usd):
        self.calls.append("writer_buy")
        self.orders.append(("coinbase", pair, usd))
        return dict(self.cb_resp)

    def _alp_buy(self, symbol, notional=None, qty=None):
        self.orders.append(("alpaca", symbol, notional))
        return dict(self.alp_resp)

    def _book(self, *a, **k):
        self.books.append((a, k))
        return "ledger-id"

    @contextlib.contextmanager
    def on(self):
        fd, ledger = tempfile.mkstemp(suffix=".jsonl", dir=_TMP)
        os.close(fd)
        old_intel = sys.modules.get("intel_layer")
        sys.modules["intel_layer"] = self._intel()
        try:
            with patched(
                    (ftl, "LEDGER", ledger),
                    (ma, "build_execution_plan", None), (ma, "MakerOrderManager", None),
                    (ma, "_is_strategy_paused", self.spy("pause", lambda n: False)),
                    (ma, "_is_kalshi_blacklisted", self.spy("blacklist", lambda t: False)),
                    (ma, "_clear_kalshi_failure", lambda t: None),
                    (ma, "_record_kalshi_failure", lambda t, e: None),
                    (vc, "require_not_halted", self.spy("halt", vc.require_not_halted)),
                    (vc, "live_exec_fenced", self.spy("live_fence", vc.live_exec_fenced)),
                    (vc, "venue_writer", self.spy("venue_writer", vc.venue_writer)),
                    (ftl, "should_trade_category", self.spy("category", ftl.should_trade_category)),
                    (ftl, "book_spot_fill", self.spy("book_spot_fill", self._book)),
                    (kp, "annotate", self.spy("ev_preflight", kp.annotate)),
                    (sc, "before_create", self.spy("session_clock", sc.before_create)),
                    (ibc, "create_preflight", self.spy("informed", ibc.create_preflight)),
                    (nw, "gate", self.spy("nimble", nw.gate)),
                    (kalshi_auth, "get_positions",
                     self.spy("held", lambda: {"market_positions": []})),
                    (kalshi_auth, "get_fills", lambda: {"fills": []}),
                    (kalshi_auth, "create_order", self._create),
                    (ct, "market_buy", self._cb_buy),
                    (ae, "market_buy", self._alp_buy),
                    (nsc, "emit_allocator_shadow",
                     lambda a, **k: {"intent_id": "shadow-1", "dispatchable": False})):
                yield self
        finally:
            if old_intel is None:
                sys.modules.pop("intel_layer", None)
            else:
                sys.modules["intel_layer"] = old_intel


def scanner_alloc(ticker="ALLOC-SCAN", with_fv=True):
    """A kalshi_scanner allocation. with_fv=False is what the real scanner yields."""
    a = {"platform": "kalshi_scanner", "type": "SCANNER_MACRO", "ticker": ticker,
         "title": "macro", "side": "yes", "price": 0.80, "allocation": 4.0,
         "expected_profit": 0.5, "hours_to_resolve": 6, "raw": {"edge_score": 80}}
    a.update(_admitted_sports_opp())
    # taker on a free-maker (maker_viable) series is now SKIP_TAKER_HURDLE: taker-priced series
    a["fmap"] = {"ALLOC": __import__("kalshi_fee_map")._row("ALLOC", 1.0, 1.0, "sports")}
    if not with_fv:
        for k in FV_KEYS:
            a.pop(k, None)
    return a


def spot_alloc(venue, ticker, side="buy"):
    return {"platform": venue, "type": "CRYPTO_BUY" if venue == "coinbase" else "STOCK_BUY",
            "ticker": ticker, "title": "t", "side": side, "price": 1.0, "allocation": 6.0,
            "expected_profit": 0.3, "hours_to_resolve": 120}


KALSHI_CHAIN = ("halt", "pause", "live_fence", "blacklist", "category", "intel", "held",
                "ev_preflight", "session_clock", "informed", "nimble")


def test_flags_default_off():
    assert not ap.scanner_execute_on() and not ap.spot_execute_on()
    assert ap.scanner_execute_on({"VALEDIA_SCANNER_EXECUTE": "1"})
    assert not ap.scanner_execute_on({"VALEDIA_SCANNER_EXECUTE": "0"})
    assert ap.kalshi_path("kalshi") and ap.kalshi_path("kalshi_weather")
    assert not ap.kalshi_path("kalshi_scanner")
    assert ap.kalshi_path("kalshi_scanner", {"VALEDIA_SCANNER_EXECUTE": "true"})
    assert not ap.kalshi_path("coinbase", {"VALEDIA_SCANNER_EXECUTE": "true"})
    print("ok flags default off")


def test_flags_off_skip_reasons_no_orders():
    w = World()
    allocs = [scanner_alloc(), spot_alloc("coinbase", "BTC-USD"), spot_alloc("alpaca", "SPY"),
              dict(spot_alloc("alpaca", "X"), platform="mystery")]
    with w.on():
        out = ma.execute_allocations(allocs, dry_run=False)
    st = [r["status"] for r in out]
    assert st == ["SKIP_SCANNER_DISABLED", "SKIP_ALLOCATOR_SPOT_DISABLED",
                  "SKIP_ALLOCATOR_SPOT_DISABLED", "SKIP"], out
    assert "VALEDIA_SCANNER_EXECUTE" in out[0]["error"]
    assert "VALEDIA_ALLOCATOR_SPOT_EXECUTE" in out[1]["error"]
    assert out[3]["error"] == "Unknown platform: mystery"
    assert out[1]["shadow_intent"] == "shadow-1"  # shadow Intent unchanged
    assert w.orders == [] and w.books == [], (w.orders, w.books)
    # flag off: scanner never reaches Kalshi-only gates or the venue
    for g in ("blacklist", "category", "intel", "held", "ev_preflight", "informed", "nimble"):
        assert g not in w.calls, (g, w.calls)
    # dry run is unchanged
    with w.on():
        out = ma.execute_allocations([scanner_alloc()], dry_run=True)
    assert out[0]["status"] == "DRY_RUN" and w.orders == []
    print("ok flags off")


def test_scanner_on_full_chain():
    w = World()
    with env(VALEDIA_SCANNER_EXECUTE="1"), w.on():
        out = ma.execute_allocations([scanner_alloc()], dry_run=False)
    r = out[0]
    assert r["status"] == "EXECUTED", r
    for g in KALSHI_CHAIN:
        assert g in w.calls, (g, w.calls)
    order = [w.calls.index(g) for g in KALSHI_CHAIN]
    assert order == sorted(order), w.calls
    assert len(w.orders) == 1 and w.orders[0][0] == "kalshi"
    (_, args, kw) = w.orders[0]
    assert args[0] == "ALLOC-SCAN" and kw["informed"]["fv_source"] == "model"
    assert r["nimble"]["allow"] is True
    print("ok scanner on: full chain")


def test_scanner_on_halt_and_gates_still_refuse():
    w = World()
    with env(VALEDIA_SCANNER_EXECUTE="1", VALEDIA_TRADING_HALT="1"), w.on():
        out = ma.execute_allocations([scanner_alloc()], dry_run=False)
    assert out[0]["status"] == "HALTED" and w.orders == [], out
    w = World()
    with env(VALEDIA_SCANNER_EXECUTE="1"), w.on(), \
            patched((ma, "_is_kalshi_blacklisted", lambda t: True)):
        out = ma.execute_allocations([scanner_alloc()], dry_run=False)
    assert out[0]["status"] == "SKIP_BLACKLISTED" and w.orders == [], out
    w = World()
    with env(VALEDIA_SCANNER_EXECUTE="1"), w.on(), \
            patched((kalshi_auth, "get_positions", lambda: {"market_positions": [
                {"ticker": "ALLOC-SCAN", "position_fp": "3"}]})):
        out = ma.execute_allocations([scanner_alloc()], dry_run=False)
    assert out[0]["status"] == "SKIP_HELD" and w.orders == [], out
    print("ok scanner on: halt/blacklist/held still refuse")


def test_scanner_without_fv_refused_by_informed_gate():
    w = World()
    with env(VALEDIA_SCANNER_EXECUTE="1"), w.on():
        out = ma.execute_allocations([scanner_alloc("ALLOC-NOFV", with_fv=False)], dry_run=False)
    r = out[0]
    assert r["status"] == "SKIP_INFORMED", r
    reasons = r["error"].split(",")
    assert "fv_source" in reasons and "fv_missing" in reasons, reasons
    assert w.orders == [] and "nimble" not in w.calls, (w.orders, w.calls)
    # The real gather shape (only the allocate() keys) never creates either.
    raw_shape = {k: v for k, v in scanner_alloc("ALLOC-RAW", with_fv=False).items()
                 if k in ("platform", "type", "ticker", "title", "side", "price", "allocation",
                          "expected_profit", "hours_to_resolve", "raw")}
    w = World()
    with env(VALEDIA_SCANNER_EXECUTE="1"), w.on():
        out = ma.execute_allocations([raw_shape], dry_run=False)
    assert out[0]["status"].startswith("SKIP") and w.orders == [], out
    print("ok scanner without fv refused")


def test_spot_on_coinbase_real_writer_fill_truth():
    w = World()
    with env(VALEDIA_ALLOCATOR_SPOT_EXECUTE="1"), w.on():
        out = ma.execute_allocations([spot_alloc("coinbase", "BTC-USD")], dry_run=False)
    r = out[0]
    assert r["status"] == "EXECUTED" and r["intended_source"] == "live", r
    assert w.orders == [("coinbase", "BTC-USD", 6.0)], w.orders
    assert "venue_writer" in w.calls and w.calls.count("halt") >= 2, w.calls  # loop + spot
    buy_at = w.calls.index("writer_buy")
    halts = [i for i, c in enumerate(w.calls) if c == "halt"]
    assert w.calls.index("venue_writer") < buy_at and len([i for i in halts if i < buy_at]) >= 2
    assert w.calls.index("book_spot_fill") > buy_at, w.calls
    (a, k), = w.books
    assert a[:4] == ("CRYPTO_BUY", "BTC-USD", "buy", "coinbase") and k["intended_source"] == "live"
    # bare POST-ack → UNRECONCILED, still booked (as UNRECONCILED by the ledger)
    w = World(cb_resp={"success": True, "order_id": "cb-ack"})
    with env(VALEDIA_ALLOCATOR_SPOT_EXECUTE="1"), w.on():
        out = ma.execute_allocations([spot_alloc("coinbase", "ETH")], dry_run=False)
    assert out[0]["status"] == "UNRECONCILED" and w.orders[0][1] == "ETH-USD", (out, w.orders)
    assert len(w.books) == 1
    # venue rejection → FAILED, nothing booked
    w = World(cb_resp={"success": False, "error": "insufficient funds"})
    with env(VALEDIA_ALLOCATOR_SPOT_EXECUTE="1"), w.on():
        out = ma.execute_allocations([spot_alloc("coinbase", "BTC-USD")], dry_run=False)
    assert out[0]["status"] == "FAILED" and w.books == [], out
    print("ok spot on: coinbase")


def test_spot_on_alpaca_paper_semantics():
    w = World()
    with env(VALEDIA_ALLOCATOR_SPOT_EXECUTE="1", ALPACA_PAPER="true"), w.on():
        out = ma.execute_allocations([spot_alloc("alpaca", "SPY")], dry_run=False)
    r = out[0]
    assert r["status"] == "PAPER_EXECUTED" and r["intended_source"] == "paper", r
    assert w.orders == [("alpaca", "SPY", 6.0)]
    assert w.books[0][1]["intended_source"] == "paper"
    w = World(alp_resp={"id": "alp-ack", "status": "accepted"})
    with env(VALEDIA_ALLOCATOR_SPOT_EXECUTE="1", ALPACA_PAPER=None), w.on():
        out = ma.execute_allocations([spot_alloc("alpaca", "SPY")], dry_run=False)
    assert out[0]["status"] == "UNRECONCILED" and out[0]["intended_source"] == "live", out
    assert w.books[0][1]["intended_source"] == "live"
    w = World(alp_resp={"code": 40310000, "message": "insufficient buying power"})
    with env(VALEDIA_ALLOCATOR_SPOT_EXECUTE="1", ALPACA_PAPER="true"), w.on():
        out = ma.execute_allocations([spot_alloc("alpaca", "SPY")], dry_run=False)
    assert out[0]["status"] == "FAILED" and w.books == [], out
    print("ok spot on: alpaca paper/live")


def test_spot_on_writer_halt_side_refuse():
    w = World()
    with env(VALEDIA_ALLOCATOR_SPOT_EXECUTE="1"), w.on(), \
            patched((vc, "venue_writer", lambda v: "none")):
        out = ma.execute_allocations([spot_alloc("coinbase", "BTC-USD"),
                                      spot_alloc("alpaca", "SPY")], dry_run=False)
    assert [r["status"] for r in out] == ["SKIP_WRITER", "SKIP_WRITER"] and w.orders == [], out
    # halt inside the spot helper (direct call: the loop-level halt is separate)
    r = {}
    with env(VALEDIA_ALLOCATOR_SPOT_EXECUTE="1", VALEDIA_TRADING_HALT="1"), w.on():
        ap.spot_execute(spot_alloc("coinbase", "BTC-USD"), "coinbase", r, cfg=vc,
                        fill_status=ma.spot_fill_status, book=ftl.book_spot_fill)
    assert r["status"] == "HALTED" and w.orders == [], r
    r = {}
    with env(VALEDIA_ALLOCATOR_SPOT_EXECUTE="1"), w.on():
        ap.spot_execute(spot_alloc("coinbase", "BTC-USD"), "coinbase", r, cfg=None,
                        fill_status=ma.spot_fill_status)
    assert r["status"] == "SKIP_WRITER" and w.orders == [], r
    w = World()
    with env(VALEDIA_ALLOCATOR_SPOT_EXECUTE="1"), w.on():
        out = ma.execute_allocations([spot_alloc("coinbase", "BTC-USD", side="sell")], dry_run=False)
    assert out[0]["status"] == "SKIP_SPOT_SIDE" and w.orders == [], out
    print("ok spot on: writer/halt/side refuse")


def test_report_icons():
    """Run report: PAPER_* gets the neutral paper icon, never ❌ (source of ma.run)."""
    import inspect
    src = inspect.getsource(ma.run)
    i = src.index('icon = (')
    expr = src[i + len("icon = "): src.index('else "❌")', i) + len('else "❌")')]
    icon = lambda st: eval(expr, {}, {"st": st})  # noqa: E731  the exact report expression
    assert icon("PAPER_EXECUTED") == "📝" and icon("PAPER_UNRECONCILED") == "📝"
    assert icon("EXECUTED") == "✅" and icon("UNRECONCILED") == "⏳" and icon("DRY_RUN") == "🔶"
    assert icon("SKIP_ALLOCATOR_SPOT_DISABLED") == "⏭" and icon("FAILED") == "❌"
    print("ok report icons")


def test_source_hygiene():
    src = open(os.path.join(HERE, "master_allocator.py")).read()
    assert "place_market_order" not in src and "import place_order" not in src
    assert "def place_market_order" not in open(os.path.join(HERE, "coinbase_trader.py")).read()
    assert "def place_order" not in open(os.path.join(HERE, "alpaca_executor.py")).read()
    apsrc = open(os.path.join(HERE, "allocator_paths.py")).read()
    assert "ct.market_buy" in apsrc and "ae.market_buy" in apsrc and "alpaca_is_live" in apsrc
    for bad in ("EXEC_DRY_RUN =", "ALLOWED_FV =", "VENUE_WRITER[", "--live"):
        assert bad not in apsrc, bad
    print("ok hygiene")


if __name__ == "__main__":
    test_flags_default_off()
    test_flags_off_skip_reasons_no_orders()
    test_scanner_on_full_chain()
    test_scanner_on_halt_and_gates_still_refuse()
    test_scanner_without_fv_refused_by_informed_gate()
    test_spot_on_coinbase_real_writer_fill_truth()
    test_spot_on_alpaca_paper_semantics()
    test_spot_on_writer_halt_side_refuse()
    test_report_icons()
    test_source_hygiene()
    print("allocator paths: PASS")
