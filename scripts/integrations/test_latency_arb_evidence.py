#!/usr/bin/env python3
"""latency_arb live path runs the allocator's pre-create chain with honest evidence.

EV preflight -> session_clocks.before_create -> informed create_preflight ->
create_order(informed=...). dry-run never reaches any of it. Any exception or
missing evidence -> SKIP_* with reasons and no order. Gates are never loosened:
the only FV latency_arb has is a strike-distance heuristic, labelled honestly
(fv_source "distance_heuristic", not in ALLOWED_FV), so the real gate refuses.
No network (all Kalshi calls are fakes), no keys, no --live.
Run: python3 test_latency_arb_evidence.py
"""
import contextlib
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["OPENCLAW_SKIP_ENV"] = "1"
for _k in list(os.environ):
    if _k.startswith("VALEDIA_NIMBLE") or _k.startswith("VALEDIA_SYSTEMONE"):
        os.environ.pop(_k)
os.environ["VALEDIA_SYSTEMONE_LOG"] = os.path.join(tempfile.mkdtemp(prefix="arbnim_"), "d.jsonl")

import latency_arb as la
import latency_arb_evidence as lae
import nimble_wiring as nw
import kalshi_auth
import kalshi_profit as kp
import session_clocks as sc
import informed_before_create as ibc

TICKER = "KXBTCD-26OCT0117-T66999.99"


def signal(direction="UP"):
    return {"pair": "BTC-USD", "direction": direction, "delta_pct": 0.21, "spot_price": 67000.0,
            "window_start": 66860.0, "timestamp": "2026-10-01T00:00:00+00:00",
            "action": "BUY", "confidence": 0.7}


def contract(side="yes", ya=0.60, yb=0.58, dist=0.5, **over):
    c = {"ticker": TICKER, "title": "BTC above", "yes_ask": ya, "yes_bid": yb,
         "spread": round(ya - yb, 3), "strike": 66999.99, "strike_type": "T",
         "distance_pct": dist, "side": side, "edge_cents": 30,
         "fair_value": round(la.heuristic_fair_value(dist), 4), "signal": signal(), "exp": "2026-10-01T17"}
    c.update(over)
    return c


@contextlib.contextmanager
def sandbox(create=None, pre=None, before=None):
    """Temp state/log paths + recorded fakes. Real gate modules unless overridden."""
    d = tempfile.mkdtemp()
    saved = (la.ARB_LOG, la.ARB_STATE, la.create_order, ibc.create_preflight, sc.before_create)
    la.ARB_LOG, la.ARB_STATE = os.path.join(d, "log.json"), os.path.join(d, "state.json")
    orders = []

    def fake_create(ticker, side, count, price_cents, order_type="limit", informed=None):
        orders.append({"ticker": ticker, "side": side, "count": count,
                       "price_cents": price_cents, "informed": informed})
        if create is not None:
            return create(ticker, side, count, price_cents, informed)
        return {"order": {"order_id": "t1", "status": "executed", "fill_count": count,
                          "count": count, "yes_price": price_cents}}

    la.create_order = fake_create
    if pre is not None:
        ibc.create_preflight = pre
    if before is not None:
        sc.before_create = before
    try:
        yield orders, d
    finally:
        la.ARB_LOG, la.ARB_STATE, la.create_order, ibc.create_preflight, sc.before_create = saved


def admit_pre(opp, count, price_cents, order_type="limit", action="buy", post=None):
    info = ibc.informed_from_opp(opp)
    return {"ok": True, "reasons": [], "informed": info}


def state_of(d):
    p = os.path.join(d, "state.json")
    return json.load(open(p)) if os.path.exists(p) else None


def test_evidence_is_code_computed_and_honest():
    c = contract("yes", ya=0.60, yb=0.58, dist=0.5)
    opp, ev = la.build_arb_evidence(c, 10, 60)
    assert ev["fv"] == 0.9 and abs(ev["fv"] - la.heuristic_fair_value(0.5)) < 1e-9
    assert ev["edge"] == 0.3 and opp["edge"] == 0.3          # fv 0.90 - price 0.60
    assert ev["edge_cents"] == 30
    assert ev["fv_source"] == "distance_heuristic" == opp["fv_source"]
    assert ev["fv_source"] not in ibc.ALLOWED_FV              # honest: never relabelled to "model"
    assert ev["fv_confidence"] is None and "fv_confidence" not in opp
    assert opp["bid"] == 0.58 and opp["ask"] == 0.60 and opp["slip"] == 0.0
    assert opp["desk"] == "latency_arb" and opp["strategy"] == "LATENCY_ARB"
    assert set(ev["gaps"]) == {"fv_confidence", "depth", "bankroll"}
    assert ev["signal"]["delta_pct"] == 0.21 and ev["signal"]["direction"] == "UP"
    json.dumps(ev)  # log-safe
    # NO leg: book is the NO book (bid = 1 - yes_ask, ask = 1 - yes_bid)
    opp_n, ev_n = la.build_arb_evidence(contract("no", ya=0.60, yb=0.58, dist=0.5), 10, 42)
    assert ev_n["bid"] == 0.4 and ev_n["ask"] == 0.42
    assert opp_n["no_ask"] == 0.42 and opp_n["no_bid"] == 0.4 and opp_n["edge"] == 0.48
    # no fv available -> edge stays absent, not faked
    bare = contract(fair_value=None, distance_pct=None)
    bare.pop("fair_value"), bare.pop("distance_pct")
    opp_b, ev_b = la.build_arb_evidence(bare, 5, 60)
    assert ev_b["fv"] is None and ev_b["edge"] is None and "fv" not in opp_b and "edge" not in opp_b


def test_finder_carries_fair_value():
    orig = la.get
    la.get = lambda path: {"markets": [{
        "ticker": TICKER, "title": "t", "yes_ask_dollars": "0.60", "yes_bid_dollars": "0.58",
        "expected_expiration_time": "2026-10-01T17:00:00Z"}]}
    try:
        best = la.find_kalshi_contract(
            {"pair": "BTC-USD", "direction": "UP", "spot_price": 67000.0, "delta_pct": 0.2})
    finally:
        la.get = orig
    assert best and best["side"] == "yes"
    assert best["fair_value"] == round(la.heuristic_fair_value(best["distance_pct"]), 4)
    assert abs(best["edge_cents"] - (best["fair_value"] - 0.60) * 100) <= 1.0 + 1e-9  # int() truncation


def test_dry_run_never_orders_or_preflights():
    def boom(*a, **k):
        raise AssertionError("dry-run must not reach pre-create or create_order")
    with sandbox(create=boom, pre=boom, before=boom) as (orders, d):
        r = la.execute_arb(contract(), dry_run=True)
    assert r["status"] == "DRY_RUN" and orders == []
    assert "evidence" not in r


def test_live_real_gates_refuse_honestly():
    with sandbox() as (orders, d):
        r = la.execute_arb(contract(), dry_run=False)
        assert orders == [], "refused path must not create_order"
        assert r["status"].startswith("SKIP_"), r["status"]
        assert r["evidence"]["fv_source"] == "distance_heuristic"
        st = state_of(d)
        assert st is None or st.get("daily_trades", 0) == 0, "skip must not burn cooldown/daily state"
        logged = json.load(open(la.ARB_LOG))["trades"]
        assert logged and logged[-1]["status"] == r["status"]
    # With before_create out of the way the informed gate itself names the honest reasons
    with sandbox(before=lambda opp, now=None: None) as (orders, d):
        r = la.execute_arb(contract(), dry_run=False)
        assert r["status"] == "SKIP_INFORMED" and orders == []
        for need in ("fv_source", "fv_confidence", "clock_stub", "depth"):
            assert need in r["reasons"], (need, r["reasons"])
        assert "fv_missing" not in r["reasons"] and "edge_missing" not in r["reasons"]
    # The stub clock alone refuses the real session_clocks path
    opp, _ = la.build_arb_evidence(contract(), 10, 60)
    assert sc.before_create(opp)[0] == "SKIP_CLOCK_STUB"


def test_bare_create_order_still_refused():
    """latency_arb's evidence is refused by kalshi_auth.create_order itself (nothing POSTed)."""
    posted = []
    orig_post = kalshi_auth.post
    kalshi_auth.post = lambda *a, **k: posted.append(a) or {}
    try:
        opp, _ = la.build_arb_evidence(contract(), 10, 60)
        info = ibc.informed_from_opp(opp)
        for informed in (None, info):
            try:
                kalshi_auth.create_order(TICKER, "yes", 10, 60, informed=informed)
                raise AssertionError("create_order must refuse")
            except ibc.InformedCreateRefused as e:
                assert "fv_source" in e.reasons
    finally:
        kalshi_auth.post = orig_post
    assert posted == []


def test_admitted_path_passes_informed_to_create_order():
    seen = []

    def pre(opp, count, price_cents, order_type="limit", action="buy", post=None):
        seen.append((opp, count, price_cents))
        return admit_pre(opp, count, price_cents)

    with sandbox(pre=pre, before=lambda opp, now=None: None) as (orders, d):
        r = la.execute_arb(contract(), dry_run=False)
        assert r["status"] == "EXECUTED", r
        assert len(orders) == 1
        o = orders[0]
        assert o["informed"] is not None and o["informed"]["fv_source"] == "distance_heuristic"
        assert o["informed"]["fv"] == 0.9 and o["informed"]["edge"] == 0.3
        assert (o["ticker"], o["side"], o["price_cents"]) == (TICKER, "yes", 60)
        assert seen and seen[0][1] == o["count"] and seen[0][2] == 60
        assert r["evidence"]["order_price_cents"] == 60 and r["ev_preflight"] is not None
        assert state_of(d)["daily_trades"] == 1
        # unreconciled ack stays UNRECONCILED (fill-true habit unchanged)
    with sandbox(pre=admit_pre, before=lambda opp, now=None: None,
                 create=lambda *a: {"order": {"order_id": "x", "status": "resting"}}) as (orders, d):
        assert la.execute_arb(contract(), dry_run=False)["status"] == "UNRECONCILED"


def test_each_stage_failure_is_a_skip_not_an_order():
    # EV preflight raises -> fail closed
    orig = kp.annotate
    kp.annotate = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("fee map down"))
    try:
        with sandbox() as (orders, d):
            r = la.execute_arb(contract(), dry_run=False)
            assert r["status"] == "SKIP_EV_PREFLIGHT" and orders == []
    finally:
        kp.annotate = orig
    # EV preflight says no
    kp.annotate = lambda opp, contracts=1, **k: (False, dict(opp, ev_preflight={"ok": False}))
    try:
        with sandbox() as (orders, d):
            assert la.execute_arb(contract(), dry_run=False)["status"] == "SKIP_EV_PREFLIGHT"
            assert orders == []
    finally:
        kp.annotate = orig
    # clock blocks
    with sandbox(before=lambda opp, now=None: ("SKIP_SESSION", "closed")) as (orders, d):
        r = la.execute_arb(contract(), dry_run=False)
        assert r["status"] == "SKIP_SESSION" and r["reasons"] == ["closed"] and orders == []
    # informed refuses
    refuse = lambda *a, **k: {"ok": False, "reasons": ["fv_source", "depth"], "informed": {}}
    with sandbox(pre=refuse, before=lambda opp, now=None: None) as (orders, d):
        r = la.execute_arb(contract(), dry_run=False)
        assert r["status"] == "SKIP_INFORMED" and r["error"] == "fv_source,depth" and orders == []
    # any exception in the sequence -> SKIP_PRECREATE_ERROR
    def boom(*a, **k):
        raise ValueError("nope")
    with sandbox(pre=boom, before=lambda opp, now=None: None) as (orders, d):
        r = la.execute_arb(contract(), dry_run=False)
        assert r["status"] == "SKIP_PRECREATE_ERROR" and "ValueError" in r["error"] and orders == []
    with sandbox(before=boom) as (orders, d):
        assert la.execute_arb(contract(), dry_run=False)["status"] == "SKIP_PRECREATE_ERROR"
    # malformed contract (evidence cannot be built) -> SKIP_EVIDENCE
    bad = contract()
    del bad["yes_bid"]  # sizing for a YES leg does not read it; evidence does
    with sandbox() as (orders, d):
        assert la.execute_arb(bad, dry_run=False)["status"] == "SKIP_EVIDENCE" and orders == []


def test_nimble_shadow_stamps_and_allows():
    with sandbox(pre=admit_pre, before=lambda opp, now=None: None) as (orders, d):
        r = la.execute_arb(contract(), dry_run=False)
        assert r["status"] == "EXECUTED" and len(orders) == 1
        nv = r["nimble"]
        assert nv["mode"] == "shadow" and nv["allow"] is True, nv  # stamp only, never blocks


def test_nimble_veto_skips_and_is_last_gate_only_for_admitted():
    calls = []
    orig = nw.gate

    def veto(opp, informed=None, **kw):
        calls.append((opp, informed, kw))
        return {"allow": False, "mode": "veto", "reason": "veto_model_skip"}

    nw.gate = veto
    try:
        with sandbox(pre=admit_pre, before=lambda opp, now=None: None) as (orders, d):
            r = la.execute_arb(contract(), dry_run=False)
            assert r["status"] == "SKIP_NIMBLE" and r["error"] == "nimble:veto_model_skip"
            assert orders == [] and r["nimble"]["allow"] is False
            assert len(calls) == 1 and calls[0][2]["source"] == "latency_arb"
            assert calls[0][1]["fv_source"] == "distance_heuristic"  # informed dict from preflight
            st = state_of(d)
            assert st is None or st.get("daily_trades", 0) == 0
        # never asked for a candidate the code gates refused (real gates refuse this one)
        calls.clear()
        with sandbox() as (orders, d):
            assert la.execute_arb(contract(), dry_run=False)["status"].startswith("SKIP_")
            assert calls == [] and orders == []
        # nor in dry-run
        with sandbox() as (orders, d):
            assert la.execute_arb(contract(), dry_run=True)["status"] == "DRY_RUN"
            assert calls == []
    finally:
        nw.gate = orig


def test_hygiene():
    src = open(os.path.join(HERE, "latency_arb.py")).read()
    ev = open(os.path.join(HERE, "latency_arb_evidence.py")).read()
    assert len(src.splitlines()) <= 620
    assert "informed=pre[\"informed\"]" in src
    assert la.build_arb_evidence is lae.build_arb_evidence and la.arb_pre_create is lae.arb_pre_create
    assert "ALLOWED_FV" not in ev.split("def build_arb_evidence")[1].split("def arb_pre_create")[0]
    assert 'fv_source": "model"' not in ev and "fv_source='model'" not in ev
    # the Nimble gate is the last step in the chain, after create_preflight
    chain = ev.split("def arb_pre_create")[1]
    assert chain.index("create_preflight") < chain.index("nimble_wiring")
    arb_run = open(os.path.join(HERE, "arb_run.py")).read()
    assert "dry-run" in arb_run and "execute" not in arb_run.replace("Switch to --mode execute", "")


if __name__ == "__main__":
    n = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            n += 1
    print(f"PASS test_latency_arb_evidence ({n} tests)")
