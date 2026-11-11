#!/usr/bin/env python3
"""Live spot paths book every real fill in the fee-true ledger (spot_ledger).

unified_brain BUY/SELL, strategy_modules.DCAEngine, dca_module.run_dca. Fakes only:
no network, no keys, no orders; an isolated temp ledger.
Run: python3 test_spot_ledger_booking.py
"""
import contextlib
import io
import json
import os
import sys
import tempfile
import types
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["OPENCLAW_SKIP_ENV"] = "1"
for _k in [k for k in os.environ if k.startswith(("VALEDIA_NIMBLE", "VALEDIA_SYSTEMONE"))]:
    os.environ.pop(_k)
os.environ["VALEDIA_NIMBLE_MODE"] = "off"

import numpy as np  # noqa: E402

import dca_module  # noqa: E402
import forward_test_ledger as ftl  # noqa: E402
import spot_ledger as sl  # noqa: E402
import spot_reconcile as sr  # noqa: E402
import strategy_modules as sm  # noqa: E402
import unified_brain as ub  # noqa: E402

CB_FEE_RATE = 0.006 + 0.005  # fee_engine STATIC_FEES coinbase taker + spread


@contextlib.contextmanager
def ledger():
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    orig = ftl.LEDGER
    ftl.LEDGER = path
    try:
        yield path
    finally:
        ftl.LEDGER = orig
        os.remove(path)


def entries(strategy=None):
    return [r for r in ftl._read() if r.get("type") == "entry"
            and (strategy is None or r.get("strategy") == strategy)]


def settlements():
    return [r for r in ftl._read() if r.get("type") == "settlement"]


def cb_fill(qty, px, fee=None, oid="cb1"):
    sr_ = {"order_id": oid, "filled_size": str(qty), "average_filled_price": str(px)}
    if fee is not None:
        sr_["total_fees"] = str(fee)
    return {"success": True, "order_id": oid, "success_response": sr_}


def alp_fill(qty, px, oid="ap1"):
    return {"id": oid, "status": "filled", "filled_qty": str(qty), "filled_avg_price": str(px)}


def close(a, b):
    return abs(a - b) < 1e-6


# ── spot_ledger units ───────────────────────────────────────────────────────
def test_buy_fill_fee_and_unreconciled():
    with ledger():
        r = sl.book_buy("BRAIN_CRYPTO", "coinbase", "ETH-USD", cb_fill(0.01, 2000, fee=0.2))
        assert r["status"] == "EXECUTED", r
        e = entries()[-1]
        assert (e["strategy"], e["source"], e["fill_status"], e["venue"]) == \
            ("BRAIN_CRYPTO", "live", "FILLED", "coinbase")
        assert e["entry_price"] == 2000.0 and e["contracts"] == 0.01
        assert e["entry_fee"] == 0.2 and e["fee_status"] == "venue" and e["order_id"] == "cb1"
        # no fee in the body -> fee_engine static fee, flagged modeled
        sl.book_buy("BRAIN_CRYPTO", "coinbase", "SOL-USD", cb_fill(2, 100, oid="cb2"))
        e = entries()[-1]
        assert close(e["entry_fee"], 200 * CB_FEE_RATE) and e["fee_status"] == "modeled", e
        sl.book_buy("BRAIN_EQUITY", "alpaca", "AAPL", alp_fill(1, 50))
        e = entries()[-1]
        assert e["entry_fee"] == 0.0 and e["fee_status"] == "modeled" and e["source"] == "live"
        # POST-ack only -> UNRECONCILED with the venue order id; "?" is not an order id
        r = sl.book_buy("BRAIN_CRYPTO", "coinbase", "BTC-USD",
                        {"success": True, "order_id": "post1", "pair": "BTC-USD", "usd": 25})
        e = entries()[-1]
        assert r["status"] == "UNRECONCILED"
        assert e["fill_status"] == "UNRECONCILED" and e["source"] == "unreconciled"
        assert e["order_id"] == "post1" and e["entry_fee"] == 0.0
        sl.book_buy("BRAIN_CRYPTO", "coinbase", "BTC-USD", {"success": True, "order_id": "?"})
        assert "order_id" not in entries()[-1]
        n = len(ftl._read())
        # rejected order and paper/shadow intents write nothing
        assert sl.book_buy("BRAIN_CRYPTO", "coinbase", "X", {"success": False, "error": "no"}
                           )["status"] == "NOT_ACKED"
        for src in ("paper", "shadow", "nautilus_sandbox"):
            assert sl.book_buy("BRAIN_EQUITY", "alpaca", "MSFT", alp_fill(1, 10),
                               intended_source=src)["status"] == "SKIPPED_NON_LIVE"
        assert len(ftl._read()) == n
        assert ftl.real_track_record()["n"] == 0


def test_sell_settles_fee_true_fifo():
    with ledger():
        sl.book_buy("BRAIN_CRYPTO", "coinbase", "ETH-USD", cb_fill(0.01, 2000, fee=0.2))
        r = sl.book_sell("BRAIN_CRYPTO", "coinbase", "ETH-USD",
                         cb_fill(0.01, 2100, fee=0.21, oid="s1"))
        assert r["status"] == "EXECUTED" and close(r["realized"], 0.59), r
        s = settlements()[-1]
        assert close(s["realized_pnl_net"], 1.0 - 0.2 - 0.21) and s["won"] is True
        assert close(s["gross"], 1.0) and close(s["fee"], 0.41) and s["fee_status"] == "venue"
        assert s["source"] == "live" and s["exit_price"] == 2100 and s["order_id"] == "s1"
        tr = ftl.real_track_record()
        assert tr["n"] == 1 and close(tr["net"], 0.59), tr
        assert ftl.open_position_count() == 0
        # FIFO over two lots (DCA then brain), partial second lot re-opens the remainder
        sl.book_buy("DCA_CRYPTO", "coinbase", "SOL-USD", cb_fill(1, 10, fee=0.1, oid="b1"))
        sl.book_buy("BRAIN_CRYPTO", "coinbase", "SOL-USD", cb_fill(1, 12, fee=0.1, oid="b2"))
        r = sl.book_sell("BRAIN_CRYPTO", "coinbase", "SOL-USD",
                         cb_fill(1.5, 14, fee=0.15, oid="s2"))
        s1, s2 = r["settlements"]
        assert s1["strategy"] == "DCA_CRYPTO" and s1["exit_strategy"] == "BRAIN_CRYPTO"
        assert close(s1["realized_pnl_net"], 4 - 0.1 - 0.1)
        assert s2["strategy"] == "BRAIN_CRYPTO" and s2["filled_contracts"] == 0.5
        assert close(s2["realized_pnl_net"], 1.0 - 0.05 - 0.05)
        rem = [e for e in entries() if e["id"] == s2["remainder_id"]][0]
        assert rem["contracts"] == 0.5 and close(rem["entry_fee"], 0.05)
        assert rem["entry_price"] == 12 and rem["source"] == "live"
        assert ftl.open_position_count() == 1
        # selling more than the ledger holds: excess is a visible UNRECONCILED row
        r = sl.book_sell("BRAIN_CRYPTO", "coinbase", "SOL-USD", cb_fill(2, 13, fee=0.2, oid="s3"))
        assert close(r["excess"], 1.5) and len(r["settlements"]) == 1
        ex = [e for e in entries() if e["id"] == r["excess_id"]][0]
        assert ex["fill_status"] == "UNRECONCILED" and ex["side"] == "sell"
        assert "order_id" not in ex
        assert ftl.open_position_count() == 0


def test_sell_incomplete_then_reconcile_cron():
    with ledger():
        sl.book_buy("BRAIN_EQUITY", "alpaca", "SPY", alp_fill(2, 500, oid="buy1"))
        r = sl.book_sell("BRAIN_EQUITY", "alpaca", "SPY", {"id": "sell1", "status": "accepted"})
        assert r["status"] == "UNRECONCILED" and settlements() == []
        stub = entries()[-1]
        assert stub["side"] == "sell" and stub["order_id"] == "sell1"
        assert ftl.real_track_record()["n"] == 0
        out = sr.reconcile_unreconciled(get_responses={"sell1": alp_fill(2, 510, oid="sell1")})
        assert out["upgraded"] == 1, out
        tr = ftl.real_track_record()
        assert tr["n"] == 1 and close(tr["net"], 20.0), tr


def test_booking_exception_marks_unreconciled():
    real = ftl.book_spot_fill
    ftl.book_spot_fill = lambda *a, **k: 1 / 0
    err = io.StringIO()
    try:
        with ledger(), contextlib.redirect_stderr(err):
            r = sl.book_buy("BRAIN_CRYPTO", "coinbase", "ETH-USD", cb_fill(1, 10, oid="x1"))
            assert r["status"] == "UNRECONCILED", r
            e = entries()[-1]
            assert e["fill_status"] == "UNRECONCILED" and e["order_id"] == "x1"
    finally:
        ftl.book_spot_fill = real
    assert "[spot_ledger] book_buy BRAIN_CRYPTO ETH-USD failed" in err.getvalue()


# ── unified_brain ───────────────────────────────────────────────────────────
def run_brain(universe, *, fused=80.0, has_pos=False, dca_due=False, alpaca_live=True,
              cb_resp=None, alp_resp=None):
    calls = {k: [] for k in ("cb_buy", "alp_buy", "cb_sell", "alp_sell")}
    tmp = tempfile.mkdtemp(prefix="spotledger_")
    state_path = os.path.join(tmp, "dca_state.json")
    json.dump({"last_buy": None if dca_due else datetime.now(timezone.utc).isoformat(),
               "total_invested": 0, "buys": []}, open(state_path, "w"))
    prices = types.SimpleNamespace(values=np.linspace(100.0, 120.0, 120))
    data = type("D", (), {"empty": False, "__len__": lambda s: 120,
                          "__getitem__": lambda s, k: prices})()
    fake_yf = types.ModuleType("yfinance")
    fake_yf.Ticker = lambda t: types.SimpleNamespace(history=lambda **k: data)
    q = {"passes_filter": True, "p5": 0.03, "p3": 0.02, "p10": 0.04, "t5": True, "dom": "MOM",
         "votes": 9, "boost_labels": [], "pos_multiplier": 1.0, "is_mon_tue": False, "score": 80}
    bal = {"USD": {"balance": 1000.0}}
    if has_pos:
        bal["BTC"] = {"balance": 0.5}
    cb_resp = cb_resp or (lambda side, t, amt: cb_fill(0.1, 100, fee=0.05, oid=f"{side}-{t}"))
    alp_resp = alp_resp or (lambda side, t: alp_fill(0.1, 100, oid=f"{side}-{t}"))
    alpaca = (lambda: {"equity": 1000.0, "cash": 1000.0},
              lambda t, notional=0: calls["alp_buy"].append(t) or alp_resp("buy", t),
              lambda t: calls["alp_sell"].append(t) or alp_resp("sell", t),
              lambda t: {"unrealized_pl": 1.0} if has_pos else None, alpaca_live)
    cb = (lambda: bal,
          lambda t, amt: calls["cb_buy"].append(t) or cb_resp("buy", t, amt),
          lambda t, b: calls["cb_sell"].append(t) or cb_resp("sell", t, b), lambda t: 100.0)
    patches = {
        "get_active_universe": lambda: universe, "stage_intel": lambda: {
            "fear_greed": 50, "fg_label": "Neutral", "fg_signal": 0},
        "stage_kernel": lambda p, c: {"direction": "BUY", "score": 80, "votes": 9, "dom": "MOM"},
        "stage_ere_safety": lambda p: {"safe": True, "multiplier": 1.0, "status": "OK"},
        "compute_signal_quality": lambda p, k, c: dict(q),
        "stage_fusion": lambda *a, **k: fused, "stage_kelly": lambda *a, **k: 10.0,
        "stage_fee_gate": lambda *a, **k: {"pass": True, "net_profit": 1.0},
        "get_alpaca": lambda: alpaca, "get_coinbase": lambda: cb,
        "get_confidence_multiplier": lambda: 1.0, "log_trade": lambda *a, **k: None,
        "_shadow_coinbase_write": lambda *a, **k: None, "MEMORY_AVAILABLE": False,
        "SYS": {**ub.SYS, "models_dir": tmp},
    }
    saved = {k: getattr(ub, k) for k in patches}
    saved_yf, saved_state = sys.modules.get("yfinance"), sm.DCAEngine.STATE_FILE
    for k, v in patches.items():
        setattr(ub, k, v)
    sys.modules["yfinance"], sm.DCAEngine.STATE_FILE = fake_yf, state_path
    try:
        report = ub.run_brain()
    finally:
        for k, v in saved.items():
            setattr(ub, k, v)
        sm.DCAEngine.STATE_FILE = saved_state
        if saved_yf is None:
            sys.modules.pop("yfinance", None)
        else:
            sys.modules["yfinance"] = saved_yf
    return report, calls


BOTH = {"BTC": ("BTC-USD", "crypto", "coinbase"), "SPY": ("SPY", "equity", "alpaca")}


def test_brain_buy_books_one_entry_per_fill():
    with ledger():
        rpt, calls = run_brain(BOTH)
        assert calls["cb_buy"] == ["BTC-USD"] and calls["alp_buy"] == ["SPY"]
        cb_rows, eq_rows = entries("BRAIN_CRYPTO"), entries("BRAIN_EQUITY")
        assert len(cb_rows) == 1 and len(eq_rows) == 1 and len(entries()) == 2
        c, a = cb_rows[0], eq_rows[0]
        assert (c["ticker"], c["venue"], c["entry_price"], c["contracts"], c["entry_fee"]) == \
            ("BTC-USD", "coinbase", 100.0, 0.1, 0.05)
        assert (a["ticker"], a["venue"], a["entry_price"], a["contracts"]) == \
            ("SPY", "alpaca", 100.0, 0.1)
        assert c["source"] == a["source"] == "live" and c["fill_status"] == "FILLED"
        assert rpt.count("ledger=EXECUTED") == 2, rpt
    # POST-ack only (the usual Coinbase body) -> UNRECONCILED, order flow unchanged
    with ledger():
        rpt, calls = run_brain(BOTH, cb_resp=lambda s, t, a: {"success": True, "order_id": "p1",
                                                               "pair": t, "usd": a},
                               alp_resp=lambda s, t: {"id": "p2", "status": "accepted"})
        assert calls["cb_buy"] == ["BTC-USD"] and calls["alp_buy"] == ["SPY"]
        rows = entries()
        assert [r["fill_status"] for r in rows] == ["UNRECONCILED", "UNRECONCILED"]
        assert {r["order_id"] for r in rows} == {"p1", "p2"}
        assert rpt.count("ledger=UNRECONCILED") == 2 and "BUY $10.00 BTC-USD" in rpt


def test_brain_alpaca_paper_writes_nothing():
    with ledger():
        rpt, calls = run_brain({"SPY": BOTH["SPY"]}, alpaca_live=False)
        assert calls["alp_buy"] == ["SPY"] and ftl._read() == []
        assert "ledger=SKIPPED_NON_LIVE" in rpt
        run_brain({"SPY": BOTH["SPY"]}, fused=30.0, has_pos=True, alpaca_live=False)
        assert ftl._read() == []


def test_brain_sell_settles_fee_true():
    with ledger():
        run_brain(BOTH)  # opens BTC 0.1@100 fee .05 and SPY 0.1@100 (alpaca fee 0)
        rpt, calls = run_brain(BOTH, fused=30.0, has_pos=True,
                               cb_resp=lambda s, t, b: cb_fill(0.1, 110, fee=0.06, oid="s-cb"),
                               alp_resp=lambda s, t: alp_fill(0.1, 90, oid="s-ap"))
        assert calls["cb_sell"] == ["BTC-USD"] and calls["alp_sell"] == ["SPY"]
        by = {s["ticker"]: s for s in settlements()}
        assert len(settlements()) == 2
        assert close(by["BTC-USD"]["realized_pnl_net"], 1.0 - 0.05 - 0.06)
        assert close(by["SPY"]["realized_pnl_net"], -1.0) and by["SPY"]["won"] is False
        assert by["BTC-USD"]["strategy"] == "BRAIN_CRYPTO" and by["SPY"]["strategy"] == "BRAIN_EQUITY"
        tr = ftl.real_track_record()
        assert tr["n"] == 2 and tr["wins"] == 1 and close(tr["net"], 0.89 - 1.0), tr
        assert ftl.open_position_count() == 0 and rpt.count("ledger=EXECUTED") == 2


def test_brain_booking_failure_never_blocks():
    real = ftl.book_spot_fill
    ftl.book_spot_fill = lambda *a, **k: (_ for _ in ()).throw(OSError("disk full"))
    err = io.StringIO()
    try:
        with ledger(), contextlib.redirect_stderr(err):
            rpt, calls = run_brain(BOTH)
            assert calls["cb_buy"] == ["BTC-USD"] and calls["alp_buy"] == ["SPY"]  # once each
            assert "BUY $10.00 BTC-USD" in rpt and "BUY $10.00 SPY" in rpt
            rows = entries()
            assert len(rows) == 2 and all(r["fill_status"] == "UNRECONCILED" for r in rows)
            assert rpt.count("ledger=UNRECONCILED") == 2
    finally:
        ftl.book_spot_fill = real
    assert "disk full" in err.getvalue()
    # the ledger module itself broken: still no exception, order flow unchanged
    saved = ub.ledger_spot_buy
    ub.ledger_spot_buy = lambda *a, **k: 1 / 0
    try:
        with ledger():
            try:
                run_brain(BOTH)
            except ZeroDivisionError:
                raise AssertionError("brain must contain ledger errors per asset")
    finally:
        ub.ledger_spot_buy = saved


def test_brain_dca_books_dca_crypto():
    with ledger():
        rpt, calls = run_brain({}, dca_due=True)
        assert calls["cb_buy"] == ["BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD"]
        rows = entries("DCA_CRYPTO")
        assert len(rows) == 4 and len(entries()) == 4
        assert all(r["source"] == "live" and r["venue"] == "coinbase" for r in rows)


# ── strategy_modules.DCAEngine (direct) ─────────────────────────────────────
def _engine(tmp):
    sm.DCAEngine.STATE_FILE = os.path.join(tmp, "s.json")
    return sm.DCAEngine(weekly_budget=10, assets={"BTC-USD": 0.5, "ETH-USD": 0.5})


def test_dca_engine_default_book_and_failure():
    saved = sm.DCAEngine.STATE_FILE
    tmp = tempfile.mkdtemp(prefix="dcaeng_")
    try:
        with ledger():
            eng = _engine(tmp)
            resp = {"BTC-USD": cb_fill(0.0001, 50000, fee=0.03, oid="d1"),
                    "ETH-USD": {"success": False, "error": "rejected"}}
            acts = eng.execute(lambda p, a: resp[p], lambda: 100.0, venue="coinbase")
            rows = entries()
            assert len(rows) == 1 and rows[0]["strategy"] == "DCA_CRYPTO"
            assert rows[0]["entry_price"] == 50000 and rows[0]["entry_fee"] == 0.03
            assert any("bought $5.00 BTC-USD" in a for a in acts)
        with ledger():
            eng = _engine(tempfile.mkdtemp(prefix="dcaeng_"))
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                acts = eng.execute(lambda p, a: {"success": True, "order_id": p},
                                   lambda: 100.0, book=lambda p, r: 1 / 0)
            assert sum("DCA: bought" in a for a in acts) == 2 and eng.state["last_buy"]
            assert "[DCAEngine] ledger booking failed" in err.getvalue()
    finally:
        sm.DCAEngine.STATE_FILE = saved


# ── dca_module.run_dca ──────────────────────────────────────────────────────
def run_dca(cb_resp, alp_resp, alpaca_live, lookup=None):
    calls = {"cb_buy": [], "alp_buy": []}
    tmp = tempfile.mkdtemp(prefix="spotdca_")
    cfg = os.path.join(tmp, "cfg.json")
    json.dump({"enabled": True, "amount_per_cycle": 10.0, "max_assets": 2, "min_score": 50,
               "crypto_tickers": ["BTC-USD"], "equity_tickers": ["SPY"]}, open(cfg, "w"))
    prices = types.SimpleNamespace(values=np.linspace(100.0, 120.0, 120))
    data = type("D", (), {"__len__": lambda s: 120, "__getitem__": lambda s, k: prices})()
    mods = {
        "yfinance": types.SimpleNamespace(
            Ticker=lambda t: types.SimpleNamespace(history=lambda **k: data)),
        "kernel_predictor_v16e": types.SimpleNamespace(KernelPredictor=lambda c: types.SimpleNamespace(
            predict=lambda p: {"score": 70.0})),
        "coinbase_trader": types.SimpleNamespace(
            get_balances=lambda: {"USD": {"balance": 100.0}},
            market_buy=lambda t, a: calls["cb_buy"].append(t) or cb_resp),
        "alpaca_executor": types.SimpleNamespace(
            get_account=lambda: {"cash": 100.0}, alpaca_is_live=lambda: alpaca_live,
            market_buy=lambda t, notional=0: calls["alp_buy"].append(t) or alp_resp),
    }
    saved = {k: sys.modules.get(k) for k in mods}
    saved_paths = (dca_module.DCA_CONFIG, dca_module.DCA_LOG)
    sys.modules.update(mods)
    dca_module.DCA_CONFIG, dca_module.DCA_LOG = cfg, os.path.join(tmp, "log.json")
    try:
        out = dca_module.run_dca(lookup=lookup)
    finally:
        dca_module.DCA_CONFIG, dca_module.DCA_LOG = saved_paths
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return out, calls


def test_dca_module_books():
    with ledger():
        out, calls = run_dca(cb_fill(0.0001, 50000, fee=0.03, oid="c1"), alp_fill(0.01, 500), True)
        assert calls == {"cb_buy": ["BTC-USD"], "alp_buy": ["SPY"]}
        c, e = entries("DCA_CRYPTO"), entries("DCA_EQUITY")
        assert len(c) == 1 and len(e) == 1 and len(entries()) == 2
        assert c[0]["entry_fee"] == 0.03 and c[0]["source"] == "live"
        assert e[0]["entry_price"] == 500 and e[0]["contracts"] == 0.01 and e[0]["source"] == "live"
    with ledger():  # Alpaca paper writes nothing; Coinbase POST-ack -> UNRECONCILED
        out, calls = run_dca({"success": True, "order_id": "c2"}, alp_fill(0.01, 500), False)
        assert calls == {"cb_buy": ["BTC-USD"], "alp_buy": ["SPY"]}
        rows = entries()
        assert len(rows) == 1 and rows[0]["strategy"] == "DCA_CRYPTO"
        assert rows[0]["fill_status"] == "UNRECONCILED" and rows[0]["order_id"] == "c2"
    with ledger():  # rejected order: no fill, no row
        # (coinbase_trader now always tags a non-2xx with http_status; a bare unclear body is
        # ambiguous and leaves an UNRECONCILED stub — see test_dca_pending)
        run_dca({"success": False, "error": "nope", "http_status": 400},
                {"message": "forbidden", "code": 403}, True)
        assert ftl._read() == []


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("spot-ledger-booking: PASS")
