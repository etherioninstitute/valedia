#!/usr/bin/env python3
"""Nimble 2 wiring at the five prediction-market decision points. Fakes only.

Points: master_allocator.execute_allocations (live Kalshi), sports_evidence._admit
(live sports), weather_live.admit (live weather), poly_loop.evaluate_one (paper),
vaughan_arb_paper.decide (paper cross-venue). kalshi_executor is legacy/inert and
is not wired. No network (s1._post / s1._get are fakes), no keys, no --live.
Run: python3 test_nimble_wiring_prediction.py
"""
import json
import os
import re
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["OPENCLAW_SKIP_ENV"] = "1"
for _k in list(os.environ):
    if _k.startswith("VALEDIA_NIMBLE") or _k.startswith("VALEDIA_SYSTEMONE"):
        os.environ.pop(_k)
LOG = os.path.join(tempfile.mkdtemp(prefix="nimwire_"), "decisions.jsonl")
os.environ["VALEDIA_SYSTEMONE_LOG"] = LOG

import calibration_ledger as cl  # noqa: E402
import forward_test_ledger as ftl  # noqa: E402
import informed_before_create as ibc  # noqa: E402
import kalshi_auth  # noqa: E402
import master_allocator as ma  # noqa: E402
import nimble_decide as nd  # noqa: E402
import nimble_wiring as nw  # noqa: E402
import poly_loop  # noqa: E402
import sports_engine as se  # noqa: E402
import systemone_hotpath_client as s1  # noqa: E402
import valedia_config as vc  # noqa: E402
import vaughan_arb_paper as va  # noqa: E402
import weather_live  # noqa: E402
import weather_shadow as ws  # noqa: E402
import test_h3 as h3  # noqa: E402
import test_poly_loop as tpl  # noqa: E402
import test_sports_evidence as tse  # noqa: E402
import test_vaughan_arb_paper as tva  # noqa: E402
import test_weather_informed_admit as twx  # noqa: E402
from test_poly_us import _restore  # noqa: E402

vc._MODELS = tempfile.mkdtemp(prefix="nimwire_models_")  # no Etherion file: phi 1.0
cl._reset_cache(items=[])  # no record: calibration is identity

PIN = "24e550a16a70"
VOLATILE = ("nimble", "ts")


def _get(url, timeout):
    if url.endswith("/api/version"):
        return {"version": "0.35.0"}
    if url.endswith("/api/tags"):
        return {"models": [{"name": "nimble:latest", "digest": PIN + "9" * 52}]}
    raise AssertionError(url)


class Nim:
    """Env + fake engine. choice None → engine down (the POST raises)."""

    def __init__(self, mode="shadow", enabled=True, choice="skip", conf=0.9, p_buy=0.9):
        self.mode, self.enabled, self.choice, self.conf = mode, enabled, choice, conf
        self.p_buy = p_buy
        self.posts = []

    def _post(self, url, payload, timeout):
        self.posts.append(payload)
        if self.choice is None:
            raise OSError("engine down")
        return {"model": payload["model"], "answers": {
            "action": {"choice": self.choice, "confidence": self.conf,
                       "probabilities": {"buy": self.p_buy, "hold": (1 - self.p_buy) / 2,
                                         "skip": (1 - self.p_buy) / 2}},
            "risk": {"choice": "4"}}}

    def __enter__(self):
        for k in list(os.environ):
            if k.startswith("VALEDIA_NIMBLE"):
                os.environ.pop(k)
        os.environ["VALEDIA_NIMBLE_MODE_PREDICTION"] = self.mode
        if self.enabled:
            os.environ["VALEDIA_SYSTEMONE"] = "1"
        else:
            os.environ.pop("VALEDIA_SYSTEMONE", None)
        self._orig = (s1._post, s1._get)
        s1._post, s1._get = self._post, _get
        s1._reset_digest()
        nd._WARMED = True  # warmup has its own test below
        return self

    def __exit__(self, *exc):
        s1._post, s1._get = self._orig
        os.environ.pop("VALEDIA_SYSTEMONE", None)
        os.environ.pop("VALEDIA_NIMBLE_MODE_PREDICTION", None)
        s1._reset_digest()


_ISO = re.compile(r"^\d{4}-\d\d-\d\dT")


def strip(d):
    """Drop the nimble stamp and wall-clock noise (fixtures use now-relative times)."""
    if isinstance(d, dict):
        return {k: strip(v) for k, v in d.items() if k not in VOLATILE}
    if isinstance(d, list):
        return [strip(v) for v in d]
    return "<iso>" if isinstance(d, str) and _ISO.match(d) else d


# ---------------------------------------------------------------- the five points
# Each runner returns (row_or_receipt, n_orders_placed_or_filled). Orders are counted
# at the venue fake, so "no create_order" is observed, not inferred.

def run_allocator():
    orig = (ftl.LEDGER, ma.build_execution_plan, ma.MakerOrderManager, ma._is_kalshi_blacklisted,
            ma._clear_kalshi_failure, kalshi_auth.create_order, kalshi_auth.get_positions,
            kalshi_auth.get_fills, sys.modules.get("intel_layer"))
    fd, ftl.LEDGER = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    fake = types.ModuleType("intel_layer")

    class _I:
        def get_kalshi_conviction(self, *a, **k):
            return {"recommended_side": "yes", "confidence": 0.2, "conviction_yes": 50}
    fake.get_intel = lambda: _I()
    sys.modules["intel_layer"] = fake
    orders = []
    ma.build_execution_plan = ma.MakerOrderManager = None
    ma._is_kalshi_blacklisted = lambda t: False
    ma._clear_kalshi_failure = lambda t: None
    kalshi_auth.get_positions = lambda: {"market_positions": []}
    kalshi_auth.create_order = lambda *a, **k: orders.append(a) or h3._ack("nw-alloc")
    kalshi_auth.get_fills = lambda: h3._fills("nw-alloc", 5, 80)
    try:
        alloc = {"platform": "kalshi", "type": "SPORTS", "ticker": "ALLOC-NW", "title": "t",
                 "side": "yes", "price": 0.80, "allocation": 4.0, "expected_profit": 0.5,
                 "hours_to_resolve": 6, "raw": {}, **h3._admitted_sports_opp()}
        out = ma.execute_allocations([alloc], dry_run=False)
        return out[0], len(orders)
    finally:
        (ftl.LEDGER, ma.build_execution_plan, ma.MakerOrderManager, ma._is_kalshi_blacklisted,
         ma._clear_kalshi_failure, kalshi_auth.create_order, kalshi_auth.get_positions,
         kalshi_auth.get_fills, old_intel) = orig
        if old_intel is None:
            sys.modules.pop("intel_layer", None)
        else:
            sys.modules["intel_layer"] = old_intel


def run_sports():
    with tse.admitted_sports_env() as env, tse.Venue() as v:
        res = se.execute_bets([tse.sports_bet()], dry_run=False, **tse._kw(env))
    return res[0], len(v.orders)


def run_weather():
    twx._quiet()
    prev, old_live, old_alert = vc.weather_live_enabled, ws.LIVE_LEDGER_PATH, ws.ALERT_MD
    tmp = tempfile.mkdtemp(prefix="nw-wx-")
    ws.LIVE_LEDGER_PATH = os.path.join(tmp, "weather_live_ledger.jsonl")
    ws.ALERT_MD = os.path.join(tmp, "alert.md")
    vc.weather_live_enabled = lambda: True
    weather_live._creates = 0
    posted = []
    try:
        ack = weather_live.admit(twx._order(twx.DEN), post=lambda *a: posted.append(a))
        return ack, len(posted)
    finally:
        vc.weather_live_enabled, ws.LIVE_LEDGER_PATH, ws.ALERT_MD = prev, old_live, old_alert
        weather_live._creates = 0


def run_poly():
    _restore()
    book = tpl._book()
    rec = poly_loop.evaluate_one(tpl._mkt(fv=0.70), book=book, bbo=book, now=tpl.NOW, bankroll=100)
    return rec, 0


def run_vaughan():
    return va.decide(tva._fat(), va.fresh_state(1000), env=dict(tva.PAPER), post_fn=tva._pass), 0


def ok_alloc(r):
    return r["status"] == "EXECUTED"


def ok_sports(r):
    return r["status"] == "EXECUTED"


def ok_weather(r):
    return r["create_order"] is True


def ok_poly(r):
    return r["status"] == poly_loop.STATUS_DRY


def ok_vaughan(r):
    return r["filled"] is True and r["reason"] == "pass"


def blocked_status(name, r):
    if name == "weather":
        return r.get("status") == "SKIP_NIMBLE" and r["create_order"] is False \
            and r["reasons"][0].startswith("SKIP_NIMBLE")
    if name == "vaughan":
        return r["reason"] == "SKIP_NIMBLE" and r["filled"] is False and r["pass"] is False
    return r["status"] == "SKIP_NIMBLE"


POINTS = (("allocator", run_allocator, ok_alloc, True),
          ("sports", run_sports, ok_sports, True),
          ("weather", run_weather, ok_weather, True),
          ("poly", run_poly, ok_poly, False),
          ("vaughan", run_vaughan, ok_vaughan, False))


def _base(run):
    with Nim(enabled=False) as n:
        r, orders = run()
    assert n.posts == [], "no POST when VALEDIA_SYSTEMONE is unset"
    return r, orders


def test_point(name, run, ok, live):
    base, base_orders = _base(run)
    assert ok(base), (name, base)
    assert not live or base_orders == 1, (name, base_orders)
    # shadow, VALEDIA_SYSTEMONE unset: zero POSTs, identical to a run with no wiring effect
    stamp = base["nimble"]
    assert stamp["mode"] == "shadow" and stamp["allow"] is True, (name, stamp)
    assert stamp["reason"] == "engine_disabled" and stamp["latency_ms"] is None, (name, stamp)
    # shadow + a fake Nimble saying skip: the code decision stands, one POST, verdict stamped
    with Nim("shadow", choice="skip") as n:
        r, orders = run()
    assert len(n.posts) == 1, (name, len(n.posts))
    assert strip(r) == strip(base) and orders == base_orders, (name, r, base)
    v = r["nimble"]
    assert v["mode"] == "shadow" and v["allow"] is True and v["action"] == "skip", (name, v)
    assert v["reason"] == "shadow_logged", (name, v)
    # the question state carries booleans and identifiers only (no raw numbers)
    st = n.posts[0]["state"]
    assert st["create_order"] is False and st["asset_class"] == "prediction", (name, st)
    assert all(x is None or isinstance(x, bool) for x in st["facts"].values()), (name, st["facts"])
    assert all(isinstance(x, bool) for x in st["informed"].values()), (name, st["informed"])
    # shadow with the engine down: still allowed, error logged on the stamp
    with Nim("shadow", choice=None) as n:
        r, orders = run()
    assert strip(r) == strip(base) and orders == base_orders, (name, r)
    assert r["nimble"]["allow"] is True and r["nimble"]["error"], (name, r["nimble"])
    # veto + skip: SKIP_NIMBLE, no order
    with Nim("veto", choice="skip") as n:
        r, orders = run()
    assert len(n.posts) == 1 and orders == 0, (name, len(n.posts), orders)
    assert blocked_status(name, r), (name, r)
    assert r["nimble"]["allow"] is False and r["nimble"]["reason"] == "veto_model_skip", (name, r["nimble"])
    # veto + hold, buy under the sharpness floor, buy with p_buy under the floor: blocked
    for choice, conf, pb, why in (("hold", 0.9, 0.9, "veto_model_hold"),
                                  ("buy", 0.01, 0.9, "veto_not_sharp"),
                                  ("buy", 0.9, 0.55, "veto_p_below_required")):
        with Nim("veto", choice=choice, conf=conf, p_buy=pb):
            r, orders = run()
        assert orders == 0 and blocked_status(name, r), (name, choice, r)
        assert r["nimble"]["reason"] == why, (name, r["nimble"])
    # veto + buy, sharp, p_buy over required: proceeds exactly as the code decided
    with Nim("veto", choice="buy", conf=0.9) as n:
        r, orders = run()
    assert ok(r) and orders == base_orders and strip(r) == strip(base), (name, r)
    assert r["nimble"]["allow"] is True and r["nimble"]["reason"] == "veto_model_buy", (name, r["nimble"])
    assert r["nimble"]["calibration"] == "identity" and r["nimble"]["phi_multiplier"] == 1.0
    assert r["nimble"]["required_p"] >= 0.60, (name, r["nimble"])
    blob = json.dumps(n.posts[0])  # economics are code-only: never in the posted state
    assert "break_even" not in blob and "fee_per" not in blob and "price" not in blob, (name, blob)
    with Nim("veto", choice="buy", conf=0.05):  # exactly the sharpness floor allows
        r, orders = run()
    assert ok(r) and orders == base_orders, (name, r)
    # veto + engine down: blocked (fail closed); veto + VALEDIA_SYSTEMONE unset: blocked, no POST
    with Nim("veto", choice=None):
        r, orders = run()
    assert orders == 0 and blocked_status(name, r), (name, r)
    assert r["nimble"]["reason"] == "veto_engine_error", (name, r["nimble"])
    with Nim("veto", enabled=False) as n:
        r, orders = run()
    assert n.posts == [] and orders == 0 and blocked_status(name, r), (name, r)
    assert r["nimble"]["reason"] == "veto_engine_disabled", (name, r["nimble"])
    # off: no call, no change
    with Nim("off") as n:
        r, orders = run()
    assert n.posts == [] and ok(r) and orders == base_orders, (name, r)
    assert r["nimble"]["reason"] == "mode_off", (name, r["nimble"])
    print("ok", name)


def test_economics_for():
    opp = {"platform": "kalshi", "side": "yes", "price": 0.8, "yes_ask": 81,
           "ev_preflight": {"fee_per": 0.012, "half_spread": 0.01, "slip": 0.002}}
    e = nw.economics_for(opp, "yes")
    assert e["price"] == 0.81 and e["fee_per_contract"] == 0.012 and e["half_spread"] is None
    assert abs(nd.break_even_p("prediction", e) - 0.824) < 1e-9, e
    e = nw.economics_for({"platform": "kalshi", "price": 0.3, "raw": {"no_ask": 0.31}}, "no")
    assert e["side"] == "no" and e["price"] == 0.31  # NO buy uses the NO price
    assert abs(e["fee_per_contract"] - 0.07 * 0.31 * 0.69) < 1e-12, e
    e = nw.economics_for({"platform": "kalshi", "price": 0.4,
                          "ev_preflight": {"fee_per": 0.01, "half_spread": 0.02}}, "yes")
    assert e["half_spread"] == 0.02 and abs(nd.break_even_p("prediction", e) - 0.43) < 1e-9
    assert nw.economics_for({"price": 0.4}, None) is None  # no yes/no side: floor applies
    assert nw.economics_for({"platform": "kalshi"}, "yes") is None
    print("ok economics_for")


def test_poly_run_level_veto():
    _restore()
    book = tpl._book()
    with Nim("veto", choice="skip"):
        doc, d = tpl._run([tpl._mkt(fv=0.70)], {"edge-mkt": book})
    assert doc["n"] == 0 and doc["create_order"] is False
    assert [b["status"] for b in doc["blocked"]] == ["SKIP_NIMBLE"], doc["blocked"]
    assert doc["skip_reasons"] == {"nimble:veto_model_skip": 1}, doc["skip_reasons"]
    row = json.loads(open(poly_loop.ledger_path(d)).read().strip().splitlines()[0])
    assert row["status"] == "SKIP_NIMBLE" and row["reason"] == "nimble:veto_model_skip"
    with Nim("shadow", choice="skip"):
        doc, d = tpl._run([tpl._mkt(fv=0.70)], {"edge-mkt": book})
    assert doc["n"] == 1 and doc["blocked"] == [] and doc["proposals"][0]["nimble"]["action"] == "skip"
    print("ok poly run-level")


def test_nimble_asked_only_after_code_gates():
    """A candidate the code already refused never reaches Nimble (fewest calls)."""
    _restore()
    book = tpl._book()
    with Nim("veto", choice="buy") as n:
        assert poly_loop.evaluate_one(tpl._mkt(fv=0.55), book=book, bbo=book, now=tpl.NOW) is None
        r = poly_loop.evaluate_one(tpl._mkt(), book=tpl._thin(), bbo=tpl._thin(), now=tpl.NOW,
                                   bankroll=100)
        assert r["status"] == poly_loop.STATUS_BLOCKED and "nimble" not in r
        va_row = va.decide(tva._fat(), va.fresh_state(1000), env=dict(tva.PAPER), post_fn=tva._refuse)
        assert va_row["reason"] == "ornith_refuse" and "nimble" not in va_row
        heur = dict(tva._fat(), source="heuristic", requires_review=True)
        assert va.decide(heur, va.fresh_state(1000), env=dict(tva.PAPER), post_fn=tva._bomb)["reason"] \
            == "review_only"
    assert n.posts == [], "no Nimble call for code-refused candidates"
    print("ok asked-last")


def test_never_raises_and_modes():
    # wiring error: shadow allows (error stamped), veto blocks
    orig = nd.prediction_facts
    nd.prediction_facts = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        with Nim("shadow"):
            v = nw.gate({"ticker": "T", "side": "yes"}, {}, source="t")
        assert v["allow"] is True and v["error"] == "wiring_RuntimeError", v
        with Nim("veto"):
            v = nw.gate({"ticker": "T", "side": "yes"}, {}, source="t")
        assert v["allow"] is False and v["reason"] == "veto_wiring_RuntimeError", v
    finally:
        nd.prediction_facts = orig
    with Nim("veto", choice="buy"):
        for junk in (None, 5, "x", [], {"side": object()}):
            v = nw.gate(junk, junk, source="t")
            assert isinstance(v, dict) and "allow" in v
        assert nw.poly_gate(None)["allow"] in (True, False)
        assert nw.vaughan_gate(None, None)["allow"] in (True, False)
    # exits are never blocked, even in veto with the engine down
    with Nim("veto", choice=None):
        v = nw.gate({"ticker": "T", "side": "sell"}, {}, source="t")
    assert v["allow"] is True and v["reason"] == "exit_never_blocked", v
    # direction rule: only an explicit up/down is used
    assert nw.direction_for({"direction": "up"}, "yes") == "up"
    assert nw.direction_for({"underlying_direction": "DOWN"}, "no") == "down"
    assert nw.direction_for({}, "yes") is None and nw.direction_for({"direction": "buy"}, "no") is None
    assert nw.why({"reason": "veto_model_skip"}) == "nimble:veto_model_skip"
    print("ok never-raises")


def test_warm_once_per_run():
    with Nim("shadow") as n:
        nd._WARMED, s1._WARM = False, False
        assert nw.warm_for_run([], False) is None and nw.warm_for_run([{"a": 1}], True) is None
        assert n.posts == []
        nw.warm_for_run([{"a": 1}], False)
        assert len(n.posts) == 1, n.posts
        nw.warm_for_run([{"a": 1}], False)
        assert len(n.posts) == 1  # once per process
    with Nim("shadow", enabled=False) as n:
        nd._WARMED, s1._WARM = False, False
        nw.warm_for_run([{"a": 1}], False)
        assert n.posts == []
    nd._WARMED = True
    print("ok warm")


def test_kalshi_executor_not_wired():
    for mod in ("kalshi_executor.py",):
        assert "nimble" not in open(os.path.join(HERE, mod)).read().lower(), mod
    print("ok executor untouched")


def main():
    for name, run, ok, live in POINTS:
        test_point(name, run, ok, live)
    test_economics_for()
    test_poly_run_level_veto()
    test_nimble_asked_only_after_code_gates()
    test_never_raises_and_modes()
    test_warm_once_per_run()
    test_kalshi_executor_not_wired()
    print("nimble wiring (prediction): PASS")


if __name__ == "__main__":
    main()
