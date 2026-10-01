#!/usr/bin/env python3
"""Nimble 2 wiring at every stock + crypto entry point. Fakes only: no network, no keys.

For each point: shadow + a fake 'skip' leaves the decision unchanged (only a stamp is
added); veto + skip → SKIP_NIMBLE and no cb_buy / alp_buy / paper fill; veto + buy at or
above min_conf proceeds; engine down in veto blocks; sells are never blocked; and with
VALEDIA_SYSTEMONE unset there is no POST and the behavior is identical.

Run: python3 test_nimble_wiring_spot.py
"""
import json
import os
import sys
import tempfile
import types
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
os.environ["OPENCLAW_SKIP_ENV"] = "1"
for _k in [k for k in os.environ if k.startswith(("VALEDIA_NIMBLE", "VALEDIA_SYSTEMONE"))]:
    os.environ.pop(_k)
LOG = os.path.join(tempfile.mkdtemp(prefix="nimwire_"), "decisions.jsonl")

import numpy as np  # noqa: E402

import all_knowing_movers_scan as ak  # noqa: E402
import crypto_paper_run as cpr  # noqa: E402
import crypto_paper_safety as safety  # noqa: E402
import daviddtech_paper as dav  # noqa: E402
import dca_module  # noqa: E402
import etoro_copy_paper as etoro  # noqa: E402
import nimble_decide as nd  # noqa: E402
import nimble_wiring_spot as nws  # noqa: E402
import systemone_hotpath_client as s1  # noqa: E402
import tc_auto_paper as tc  # noqa: E402
import unified_brain as ub  # noqa: E402
import vibe_shadow_paper as vibe  # noqa: E402
from systemone_test_fakes import Scoped  # noqa: E402

_S = Scoped(s1, LOG)
_scoped_fakes = _S.fixture()
RULES = os.path.join(ROOT, "data", "crypto-paper", "rules.json")
BARS = os.path.join(ROOT, "data", "crypto-paper", "bars_safety_pass.json")


class Nim:
    """Fake Nimble transport: records every POST; answers with a fixed verdict."""

    def __init__(self, choice="skip", conf=0.9, risk="4", exc=None):
        self.choice, self.conf, self.risk, self.exc, self.all = choice, conf, risk, exc, []

    calls = property(lambda self: [c for c in self.all if c["state"].get("symbol") != "warmup"])

    def __call__(self, url, payload, timeout):
        self.all.append(payload)
        if self.exc:
            raise self.exc
        return {"model": payload["model"], "answers": {
            "action": {"choice": self.choice, "confidence": self.conf,
                       "probabilities": {"buy": 0.5, "hold": 0.25, "skip": 0.25}},
            "risk": {"choice": self.risk}}}

def setup(mode=None, on=True, post=None):
    for k in [k for k in os.environ if k.startswith("VALEDIA_NIMBLE")]:
        os.environ.pop(k)
    os.environ.pop("VALEDIA_SYSTEMONE", None)
    if on:
        os.environ["VALEDIA_SYSTEMONE"] = "1"
    if mode:
        os.environ["VALEDIA_NIMBLE_MODE"] = mode
    s1._reset_digest()
    nd._WARMED = True
    nws._HOOKS["post"], nws._HOOKS["get"] = post, None
    return post

def test_gate_modes_and_never_raises():
    post = setup("shadow", post=Nim("skip"))
    v = nws.gate("crypto", "BTC-USD", {"fused": 80.0}, source="t")
    assert v["allow"] is True and v["action"] == "skip" and v["mode"] == "shadow"
    assert not nws.blocked(v) and len(post.calls) == 1
    st = post.calls[0]["state"]
    assert st["asset_class"] == "crypto" and st["create_order"] is False
    assert st["facts"]["fused_strong"] is True and st["facts"]["already_held"] is None
    # exits are never asked and never blocked, in any mode
    for mode in ("shadow", "veto"):
        post = setup(mode, post=Nim("skip"))
        assert nws.gate("crypto", "BTC-USD", {}, source="t", side="sell") is None
        assert post.all == []
    # veto: buy at/above min_conf proceeds; low conf, skip, engine error all block
    post = setup("veto", post=Nim("buy", 0.9))
    assert nws.blocked(nws.gate("stock", "SPY", {}, source="t")) is False
    post = setup("veto", post=Nim("buy", 0.3))
    assert nws.blocked(nws.gate("stock", "SPY", {}, source="t"))
    post = setup("veto", post=Nim("skip"))
    assert nws.blocked(nws.gate("stock", "SPY", {}, source="t"))
    post = setup("veto", post=Nim(exc=ConnectionRefusedError("down")))
    v = nws.gate("stock", "SPY", {}, source="t")
    assert nws.blocked(v) and v["reason"] == "veto_engine_error"
    # engine off: shadow is a pure no-op (None, no POST); veto fails closed without a POST
    post = setup("shadow", on=False, post=Nim("skip"))
    assert nws.gate("stock", "SPY", {}, source="t") is None and post.all == []
    post = setup("veto", on=False, post=Nim("buy"))
    assert nws.blocked(nws.gate("stock", "SPY", {}, source="t")) and post.all == []
    # mode off and non-spot names: nothing
    post = setup("off", post=Nim("skip"))
    assert nws.gate("stock", "SPY", {}, source="t") is None and post.all == []
    post = setup("veto", post=Nim("skip"))
    assert nws.gate("forex", "EURUSD", {}, source="t") is None
    assert nws.gate(None, "ES1!", {}, source="t") is None and post.all == []
    # an unexpected error: shadow allows, veto blocks, never raises
    real = nd.decide_candidate
    nd.decide_candidate = lambda *a, **k: 1 / 0
    try:
        setup("shadow", post=Nim())
        v = nws.gate("stock", "SPY", {}, source="t")
        assert v["allow"] is True and not nws.blocked(v) and v["error"] == "ZeroDivisionError"
        setup("veto", post=Nim())
        v = nws.gate("stock", "SPY", {}, source="t")
        assert nws.blocked(v) and v["reason"].startswith("wiring_")
    finally:
        nd.decide_candidate = real
    assert nws.classify("COINBASE:BTCUSD") == "crypto" and nws.classify("SPY") == "stock"
    assert nws.classify("BTCUSDT") == "crypto" and nws.classify("CL1!") is None
    assert nws.classify("x", "etf") == "stock" and nws.classify("x", "oil") is None

def run_brain(universe, *, fused=80.0, has_pos=False, dca_due=False):
    calls = {k: [] for k in ("cb_buy", "alp_buy", "cb_sell", "alp_sell")}
    tmp = tempfile.mkdtemp(prefix="nimub_")
    state_path = os.path.join(tmp, "dca_state.json")
    json.dump({"last_buy": None if dca_due else datetime.now(timezone.utc).isoformat(),
               "total_invested": 0, "buys": []}, open(state_path, "w"))
    prices = types.SimpleNamespace(values=np.linspace(100.0, 120.0, 120))
    data = type("D", (), {"empty": False, "__len__": lambda s: 120, "__getitem__": lambda s, k: prices})()
    fake_yf = types.ModuleType("yfinance")
    fake_yf.Ticker = lambda t: types.SimpleNamespace(history=lambda **k: data)
    q = {"passes_filter": True, "p5": 0.03, "p3": 0.02, "p10": 0.04, "t5": True, "dom": "MOM",
         "votes": 9, "boost_labels": [], "pos_multiplier": 1.0, "is_mon_tue": False, "score": 80}
    bal = {"USD": {"balance": 1000.0}}
    if has_pos:
        bal["BTC"] = {"balance": 0.5}
    alpaca = (lambda: {"equity": 1000.0, "cash": 1000.0},
              lambda t, notional=0: calls["alp_buy"].append(t) or {"id": "a"},
              lambda t: calls["alp_sell"].append(t) or {"id": "s"},
              lambda t: {"unrealized_pl": 1.0} if has_pos else None, False)
    cb = (lambda: bal, lambda t, amt: calls["cb_buy"].append((t, amt)) or {"success": True},
          lambda t, b: calls["cb_sell"].append(t) or {"success": True}, lambda t: 100.0)
    import strategy_modules as sm
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
        "ledger_spot_buy": lambda *a, **k: None, "ledger_spot_sell": lambda *a, **k: None,
        "_shadow_coinbase_write": lambda *a, **k: None, "MEMORY_AVAILABLE": False,
        "_ftl": None, "SYS": {**ub.SYS, "models_dir": tmp},
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

def _body(report):
    return [ln for ln in report.splitlines() if "nimble" not in ln and "UTC" not in ln and not ln.startswith("ML:")]

def test_unified_brain_buy_points():
    post = setup(on=False, post=Nim("skip"))
    base_rpt, base = run_brain(BOTH)
    assert base["cb_buy"] and base["alp_buy"] == ["SPY"] and post.all == []   # unset: no POST
    assert "nimble" not in base_rpt.lower() and "SKIP_NIMBLE" not in base_rpt
    # shadow + skip: identical orders and report, plus a stamp; POST per candidate
    post = setup("shadow", post=Nim("skip"))
    rpt, got = run_brain(BOTH)
    assert got == base and _body(rpt) == _body(base_rpt)
    assert "nimble skip" in rpt and len(post.calls) == 2 and "SKIP_NIMBLE" not in rpt
    by = {c["state"]["symbol"]: c["state"] for c in post.calls}
    assert by["BTC-USD"]["asset_class"] == "crypto" and by["SPY"]["asset_class"] == "stock"
    f = by["BTC-USD"]["facts"]
    assert (f["fused_strong"], f["filter_passed"], f["fee_ok"], f["already_held"],
            f["trend_up_5d"]) == (True, True, True, False, True)
    assert by["BTC-USD"]["create_order"] is False and by["BTC-USD"]["side"] == "buy"
    # veto + skip: SKIP_NIMBLE and NO cb_buy / alp_buy
    post = setup("veto", post=Nim("skip"))
    rpt, got = run_brain(BOTH)
    assert got["cb_buy"] == [] and got["alp_buy"] == [] and rpt.count("SKIP_NIMBLE") == 2
    assert "NO NEW TRADES" in rpt
    # veto + buy at/above min_conf proceeds with the same orders; below blocks
    setup("veto", post=Nim("buy", 0.9))
    rpt, got = run_brain(BOTH)
    assert got == base and "SKIP_NIMBLE" not in rpt
    setup("veto", post=Nim("buy", 0.3))
    assert run_brain(BOTH)[1]["cb_buy"] == []
    # veto: engine down (post raises) and engine unset both block, no order
    setup("veto", post=Nim(exc=ConnectionRefusedError("down")))
    assert run_brain(BOTH)[1]["alp_buy"] == []
    post = setup("veto", on=False, post=Nim("buy"))
    assert run_brain(BOTH)[1]["cb_buy"] == [] and post.all == []
    # per-class mode: crypto veto, stock shadow
    post = setup("shadow", post=Nim("skip"))
    os.environ["VALEDIA_NIMBLE_MODE_CRYPTO"] = "veto"
    got = run_brain(BOTH)[1]
    assert got["cb_buy"] == [] and got["alp_buy"] == ["SPY"]

def test_unified_brain_sells_never_blocked():
    post = setup("veto", post=Nim("skip"))
    rpt, got = run_brain(BOTH, fused=30.0, has_pos=True)
    assert got["cb_sell"] == ["BTC-USD"] and got["alp_sell"] == ["SPY"]
    assert got["cb_buy"] == [] and got["alp_buy"] == []
    assert post.calls == [] and "SKIP_NIMBLE" not in rpt

def test_unified_brain_dca():
    post = setup(on=False, post=Nim("skip"))
    base = run_brain({}, dca_due=True)[1]
    assert [t for t, _ in base["cb_buy"]] == ["BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD"]
    assert post.all == []
    post = setup("shadow", post=Nim("skip"))
    assert run_brain({}, dca_due=True)[1] == base and len(post.calls) == 4
    post = setup("veto", post=Nim("skip"))
    rpt, got = run_brain({}, dca_due=True)
    assert got["cb_buy"] == [] and rpt.count("SKIP_NIMBLE") == 4 and "NO NEW TRADES" in rpt
    setup("veto", post=Nim("buy", 0.9))
    assert run_brain({}, dca_due=True)[1] == base
    setup("veto", post=Nim(exc=OSError("down")))
    assert run_brain({}, dca_due=True)[1]["cb_buy"] == []

def run_dca():
    calls = {"cb_buy": [], "alp_buy": []}
    tmp = tempfile.mkdtemp(prefix="nimdca_")
    cfg = os.path.join(tmp, "cfg.json")
    json.dump({"enabled": True, "amount_per_cycle": 10.0, "max_assets": 2, "min_score": 50,
               "crypto_tickers": ["BTC-USD"], "equity_tickers": ["SPY"]}, open(cfg, "w"))
    prices = types.SimpleNamespace(values=np.linspace(100.0, 120.0, 120))
    data = type("D", (), {"__len__": lambda s: 120, "__getitem__": lambda s, k: prices})()
    mods = {
        "yfinance": types.SimpleNamespace(Ticker=lambda t: types.SimpleNamespace(history=lambda **k: data)),
        "kernel_predictor_v16e": types.SimpleNamespace(KernelPredictor=lambda c: types.SimpleNamespace(
            predict=lambda p: {"score": 70.0})),
        "coinbase_trader": types.SimpleNamespace(
            get_balances=lambda: {"USD": {"balance": 100.0}},
            market_buy=lambda t, a: calls["cb_buy"].append(t) or {"success": True}),
        "alpaca_executor": types.SimpleNamespace(
            get_account=lambda: {"cash": 100.0}, alpaca_is_live=lambda: False,
            market_buy=lambda t, notional=0: calls["alp_buy"].append(t) or {"id": "a"}),
        # fake ledger: every function spot_ledger.book_buy calls, all inert (no writes)
        "forward_test_ledger": types.SimpleNamespace(
            book_spot_fill=lambda *a, **k: None, spot_ack_ok=lambda *a, **k: True,
            record_unreconciled=lambda *a, **k: None,
            extract_spot_fill=lambda *a, **k: {"complete": False, "price": 0.0, "qty": 0.0,
                                               "fee": None, "order_id": None}),
    }
    saved = {k: sys.modules.get(k) for k in mods}
    saved_paths = (dca_module.DCA_CONFIG, dca_module.DCA_LOG)
    sys.modules.update(mods)
    dca_module.DCA_CONFIG, dca_module.DCA_LOG = cfg, os.path.join(tmp, "log.json")
    try:
        out = dca_module.run_dca()
    finally:
        dca_module.DCA_CONFIG, dca_module.DCA_LOG = saved_paths
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v
    return out, calls

def test_dca_module():
    post = setup(on=False, post=Nim("skip"))
    out, base = run_dca()
    assert base == {"cb_buy": ["BTC-USD"], "alp_buy": ["SPY"]} and post.all == []
    post = setup("shadow", post=Nim("skip"))
    out, got = run_dca()
    assert got == base and "nimble skip" in out and len(post.calls) == 2
    post = setup("veto", post=Nim("skip"))
    out, got = run_dca()
    assert got == {"cb_buy": [], "alp_buy": []} and out.count("SKIP_NIMBLE") == 2
    setup("veto", post=Nim("buy", 0.9))
    assert run_dca()[1] == base
    setup("veto", post=Nim(exc=OSError("down")))
    assert run_dca()[1] == {"cb_buy": [], "alp_buy": []}

def test_dca_and_movers_facts_mapping():
    # DCA: facts come from what DCA knows; the rest stay None (never invented)
    post = setup("shadow", post=Nim("skip"))
    run_dca()
    by = {c["state"]["symbol"]: c["state"]["facts"] for c in post.calls}
    f = by["BTC-USD"]
    assert (f["fused_strong"], f["fee_ok"], f["trend_up_5d"]) == (True, True, True)
    assert f["already_held"] is None  # DCA re-buys held names: never sent as a red flag
    assert f["filter_passed"] is None and f["momentum_dominant"] is None and f["regime_risk_off"] is None
    assert by["SPY"]["fee_ok"] is True and by["SPY"]["already_held"] is None
    # unified_brain DCA: no kernel score or price history there -> fused_strong / trend None
    post = setup("shadow", post=Nim("skip"))
    run_brain({}, dca_due=True)
    f = post.calls[0]["state"]["facts"]
    assert f["fee_ok"] is True and f["already_held"] is None
    assert f["fused_strong"] is None and f["trend_up_5d"] is None
    # dca_row units: score band uses the nd threshold; fee ceiling; short history
    assert nd._spot_facts(nws.dca_row("coinbase", 5, score=nd.FUSED_STRONG))["fused_strong"] is True
    assert nd._spot_facts(nws.dca_row("coinbase", 5, score=nd.FUSED_STRONG - 1))["fused_strong"] is False
    assert nws.dca_row("coinbase", 5, prices=[1, 2, 3]).get("q") is None
    assert nd._spot_facts(nws.dca_row("coinbase", 5, prices=[5, 4, 3, 3, 3, 3, 3]))["trend_up_5d"] is False
    real = nws.DCA_FEE_MAX_RT_PCT
    nws.DCA_FEE_MAX_RT_PCT = 1.0
    try:
        assert nd._spot_facts(nws.dca_row("coinbase", 5))["fee_ok"] is False  # static 2.2% > 1.0
        assert nd._spot_facts(nws.dca_row("alpaca", 5))["fee_ok"] is True
    finally:
        nws.DCA_FEE_MAX_RT_PCT = real
    assert nd._spot_facts(nws.dca_row("coinbase", 5))["already_held"] is None
    try:
        nws.dca_row("coinbase", 5, has_pos=True)
        raise AssertionError("dca_row must not accept has_pos")
    except TypeError:
        pass
    # the brain BUY path still sends already_held (see test_unified_brain_buy_points)
    # movers: s1.facts names mapped to the spot names the question describes
    row = {"chg_24h": 20.0, "edge": 0.4, "quote_volume_usd": 5e9}
    m = nws.movers_facts(row, ak.LIMITS)
    assert m["fee_ok"] is True and m["filter_passed"] is True and m["edge_positive"] is True
    m = nws.movers_facts({"chg_24h": 0.1, "edge": -0.2}, ak.LIMITS)
    assert m["fee_ok"] is False and m["filter_passed"] is False
    m = nws.movers_facts({"edge": 0.1}, ak.LIMITS)  # no print: bar comparison unknown stays None
    assert m["filter_passed"] is None and m["fee_ok"] is True
    for k in ("fused_strong", "already_held", "trend_up_5d", "momentum_dominant", "regime_risk_off"):
        assert k not in m  # absent, never defaulted
    post = setup("shadow", post=Nim("skip"))
    doc = _scan()
    f = post.calls[0]["state"]["facts"]
    assert "fee_ok" in f and "filter_passed" in f and f["has_print"] is True

def _echo(payload):
    text = payload["messages"][0]["content"]
    blob = json.loads(text[text.rfind("\n") + 1:])
    ev = {c["id"]: {"pass": True, "value": c["actual"]} for c in blob["conditions"]}
    return json.dumps({"pass": True, "bias": blob["bias"], "failed_conditions": [],
                       "evidence": ev, "model": "ornith-1.5-k-m"})

def _safety(**kw):
    d = tempfile.mkdtemp(prefix="nimcp_")
    out = os.path.join(d, "equity.json")
    dec = safety.run_safety(fixture_path=BARS, rules_path=RULES, log_path=os.path.join(d, "log.json"),
                            out_path=out, post_fn=kw.get("post_fn", _echo))
    return dec, out

def test_crypto_paper_safety():
    post = setup(on=False, post=Nim("skip"))
    base, out = _safety()
    assert base["pass"] is True and base["would_have_fill"] and "nimble" not in base and post.all == []
    post = setup("shadow", post=Nim("skip"))
    dec, out = _safety()
    assert dec["pass"] is True and dec["reason"] == "pass" and dec["would_have_fill"]
    assert dec["nimble"]["action"] == "skip" and dec["nimble"]["allow"] is True
    assert post.calls[0]["state"]["asset_class"] == "crypto" and len(post.calls) == 1
    post = setup("veto", post=Nim("skip"))
    dec, out = _safety()
    assert dec["pass"] is False and dec["reason"] == "SKIP_NIMBLE" and "SKIP_NIMBLE" in dec["failed_conditions"]
    assert dec["would_have_fill"] is None and not os.path.exists(out)
    assert dec["create_order"] is False and dec["orderPlaced"] is False
    setup("veto", post=Nim("buy", 0.9))
    dec, out = _safety()
    assert dec["pass"] is True and dec["would_have_fill"] and os.path.exists(out)
    setup("veto", post=Nim(exc=OSError("down")))
    assert _safety()[0]["would_have_fill"] is None
    # Ornith refuses: Ornith stays the gate, Nimble is not asked
    post = setup("veto", post=Nim("buy", 0.9))
    dec, _ = _safety(post_fn=lambda p: (_ for _ in ()).throw(OSError("ornith down")))
    assert dec["pass"] is False and dec["reason"] == "ornith_down" and post.all == []

def test_crypto_paper_tick_row():
    def tick(d):
        return cpr.run_tick(
            fixture_path=BARS, rules_path=RULES, log_path=os.path.join(d, "l.json"),
            out_path=os.path.join(d, "e.json"), post_fn=_echo, try_qwen=False,
            decision_path=os.path.join(d, "decisions.jsonl"))

    def last(d):
        return [json.loads(x) for x in open(os.path.join(d, "decisions.jsonl")) if x.strip()][-1]

    post = setup("veto", post=Nim("skip"))
    d = tempfile.mkdtemp(prefix="nimtick_")
    dec = tick(d)
    row = last(d)
    assert dec["reason"] == "SKIP_NIMBLE" and row["reason"] == "SKIP_NIMBLE" and row["filled"] is False
    assert row["nimble"]["allow"] is False and row["pass"] is False
    post = setup("shadow", post=Nim("skip"))
    d = tempfile.mkdtemp(prefix="nimtick_")
    dec = tick(d)
    row = last(d)
    assert dec["pass"] is True and row["filled"] is True and row["nimble"]["action"] == "skip"
    setup(on=False, post=Nim("skip"))
    d = tempfile.mkdtemp(prefix="nimtick_")
    tick(d)
    assert "nimble" not in last(d)

def _scan():
    return ak.build(fixture_path=ak.DEFAULT_FIXTURE, annotate=lambda d: d, systemone_post=nws._HOOKS["post"])

def test_movers_scan_label_size_rank_code_owned():
    setup(on=False, post=Nim("skip"))
    base = _scan()
    assert all("nimble" not in r for r in base["ranked"])
    order = lambda d: [(r["symbol"], r["label"], r["would_have"]["sized"]) for r in d["ranked"]]
    for mode in ("shadow", "veto"):  # veto never changes what the scan sizes or ranks
        post = setup(mode, post=Nim("skip"))
        doc = _scan()
        assert order(doc) == order(base)
        rows = {r["symbol"]: r for r in doc["ranked"]}
        spot = {k for k, r in rows.items() if r["class"] in ("crypto", "equity", "etf")}
        assert {k for k, r in rows.items() if "nimble" in r} == spot
        assert "nimble" not in rows["USO"] and "nimble" not in rows["KXHIGHNY"]
        assert all(rows[k]["nimble"]["allow"] is (mode == "shadow") and rows[k]["nimble"]["enforced"] is False
                   for k in spot)
        assert rows["PUMP"]["nimble"]["action"] == "skip" and rows["PUMP"]["label"] == "would_buy"
        assert len(post.calls) == len(spot) and doc["systemone"]["n_ok"] == len(spot)
        assert post.calls[0]["state"]["facts"]["has_print"] is True
    # wall-clock cap: past the cap no further calls, mode default, rows still stamped
    post = setup("shadow", post=Nim("skip"))
    os.environ["VALEDIA_NIMBLE_MANY_CAP_S"] = "0.1"
    ticks, ranked = iter(range(0, 1000, 5)), [dict(r) for r in base["ranked"]]
    nws.scan_rows(ranked, ak.LIMITS, post=post, clock=lambda: float(next(ticks)))
    stamped = [r["nimble"]["reason"] for r in ranked if "nimble" in r]
    assert stamped and set(stamped) == {"cap_reached"} and post.calls == []
    os.environ.pop("VALEDIA_NIMBLE_MANY_CAP_S", None)


def _tc(post_mode, post, action="buy", book=None):
    setup(post_mode, on=post_mode is not None, post=post)
    book = book if book is not None else tc.fresh_book()
    body = {"passphrase": "p", "action": action, "ticker": "COINBASE:BTCUSD", "price": "83360.03",
            "strategy": "supertrend_paper"}
    env = {"VALEDIA_TV_WEBHOOK_PASSPHRASE": "p", "VALEDIA_TV_LIVE": "0", "TC_AUTO_LIVE": "0",
           "VALEDIA_TRADING_HALT": "0"}
    code, resp = tc.decide(body, book, env)
    return code, resp, book

def test_tc_auto_paper():
    code, base, book0 = _tc(None, Nim("skip"))
    assert code == 200 and base["filled"] and book0["_journal"][-1].get("nimble") is None
    post = Nim("skip")
    code, resp, book = _tc("shadow", post)
    assert code == 200 and resp["filled"] and book["cash"] == book0["cash"]
    assert book["_journal"][-1]["nimble"]["action"] == "skip" and len(post.calls) == 1
    assert post.calls[0]["state"]["asset_class"] == "crypto" and post.calls[0]["state"]["symbol"] == "BTC-USD"
    post = Nim("skip")
    code, resp, book = _tc("veto", post)
    assert code == 200 and resp["filled"] is False and resp["decision"]["reason"] == "SKIP_NIMBLE"
    assert book["cash"] == tc.PAPER_BANKROLL and not book["positions"] and "_fills" not in book
    assert resp["create_order"] is False
    code, resp, book = _tc("veto", Nim("buy", 0.9))
    assert resp["filled"] is True and book["positions"]
    code, resp, book = _tc("veto", Nim(exc=OSError("down")))
    assert resp["filled"] is False and resp["decision"]["reason"] == "SKIP_NIMBLE"
    # a sell is never asked and never blocked, even in veto with a skip engine
    _c, _r, held = _tc(None, Nim())
    post = Nim("skip")
    code, resp, held = _tc("veto", post, action="sell", book=held)
    assert code == 200 and resp["filled"] is True and post.all == []

def _dav(action="entry", ticker="BTCUSDT"):
    body = {"passphrase": "x", "timestamp": "2026-10-01T00:00:00Z", "strategy_id": "Waddah_1H_Anchor",
            "ticker": ticker, "action": action, "entry_price": 65000.0, "volume": 0.01}
    return dav.to_decision(body, env={})

def test_daviddtech_paper():
    setup(on=False, post=Nim("skip"))
    base = _dav()
    assert base["band"] == "allowlist" and "nimble" not in base
    post = setup("shadow", post=Nim("skip"))
    row = _dav()
    assert row["band"] == "allowlist" and row["reason"] == base["reason"] and row["nimble"]["action"] == "skip"
    assert len(post.calls) == 1 and post.calls[0]["state"]["asset_class"] == "crypto"
    post = setup("veto", post=Nim("skip"))
    row = _dav()
    assert row["band"] == "review_only" and row["reason"] == "SKIP_NIMBLE"
    assert row["filled"] is False and row["create_order"] is False
    setup("veto", post=Nim("buy", 0.9))
    assert _dav()["band"] == "allowlist"
    setup("veto", post=Nim(exc=OSError("down")))
    assert _dav()["reason"] == "SKIP_NIMBLE"
    post = setup("veto", post=Nim("skip"))
    for act in ("exit", "tp1", "tp2", "breakeven"):  # exits never asked, never blocked
        assert _dav(act)["band"] == "allowlist"
    assert post.all == []
    assert _dav(ticker="ES1!")["band"] == "allowlist" and post.all == []  # not stock/crypto


TRADE = {"symbol": "BTC-USD", "prior_5d_return": -0.10, "gross_pnl": 40.0, "fee_usd": 2.0}

def test_vibe_shadow_paper():
    st = {"exposure_today_usd": 0.0, "kill_switch": False}
    setup(on=False, post=Nim("skip"))
    base = vibe.decide(TRADE, st)
    assert base["reason"] == "shadow_log" and "nimble" not in base
    post = setup("shadow", post=Nim("skip"))
    row = vibe.decide(TRADE, st)
    assert row["reason"] == "shadow_log" and row["size_usd"] == base["size_usd"]
    assert row["nimble"]["action"] == "skip" and len(post.calls) == 1
    assert post.calls[0]["state"]["facts"]["trend_up_5d"] is False
    post = setup("veto", post=Nim("skip"))
    row = vibe.decide(TRADE, st)
    assert row["reason"] == "SKIP_NIMBLE" and row["size_usd"] == 0 and row["would_shadow"] is False
    assert row["create_order"] is False
    setup("veto", post=Nim("buy", 0.9))
    assert vibe.decide(TRADE, st)["reason"] == "shadow_log"
    setup("veto", post=Nim(exc=OSError("down")))
    assert vibe.decide(TRADE, st)["reason"] == "SKIP_NIMBLE"
    # a trade the desk already refuses never reaches Nimble
    post = setup("veto", post=Nim("buy", 0.9))
    assert vibe.decide(dict(TRADE, prior_5d_return=0.04), st)["reason"] == "review_only" and post.all == []

def test_etoro_copy_paper_not_wired_by_design():
    """eToro candidates are copy-traders, not stock/crypto instruments: no Nimble call."""
    cand = etoro.score({"username": "x", "gain": 0.3, "risk_score": 4, "copiers": 500,
                        "peak_to_valley": 0.1, "win_ratio": 70.0})
    for mode in ("shadow", "veto"):
        post = setup(mode, post=Nim("skip"))
        assert etoro.decide(cand, None)["reason"] == "would_copy" and post.all == []

def main():
    _S.setup()
    try:
        for name, fn in sorted(globals().items()):
            if name.startswith("test_") and callable(fn):
                fn()
                print("ok", name)
    finally:
        setup(on=False)
        _S.teardown()
    print("PASS test_nimble_wiring_spot")


if __name__ == "__main__":
    main()
