#!/usr/bin/env python3
"""nimble_decide profit-consistent veto rule. Fake POST only: no server, no network, no keys.

allow iff buy and confidence >= min_sharpness and calibrate(p_buy) >= required_p,
required_p = max(p_floor, break_even_p + base_margin * phi_multiplier).
Run: python3 test_nimble_veto_rule.py
"""
import contextlib
import io
import json
import math
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["OPENCLAW_SKIP_ENV"] = "1"
for _k in list(os.environ):
    if _k.startswith("VALEDIA_NIMBLE") or _k.startswith("VALEDIA_SYSTEMONE"):
        os.environ.pop(_k)
LOG = os.path.join(tempfile.mkdtemp(prefix="nimrule_"), "decisions.jsonl")
os.environ["VALEDIA_SYSTEMONE_LOG"] = LOG

import calibration_ledger as cl  # noqa: E402
import nimble_decide as nd  # noqa: E402
import systemone_hotpath_client as s1  # noqa: E402
import valedia_config as vc  # noqa: E402

PIN = "24e550a16a70"
MODELS = tempfile.mkdtemp(prefix="nimrule_models_")
vc._MODELS = MODELS
ETH = os.path.join(MODELS, "latest_etherion_risk.json")
SECRET_PRICE = 0.7319  # a distinctive number that must never reach the model


def _get(url, timeout):
    if url.endswith("/api/version"):
        return {"version": "0.35.0"}
    if url.endswith("/api/tags"):
        return {"models": [{"name": "nimble:latest", "digest": PIN + "9" * 52}]}
    raise AssertionError(url)


def _post(choice="buy", conf=0.9, p_buy=0.9, calls=None):
    def fake(url, payload, timeout):
        if calls is not None:
            calls.append(payload)
        rest = (1.0 - p_buy) / 2
        return {"model": payload["model"], "answers": {
            "action": {"choice": choice, "confidence": conf,
                       "probabilities": {"buy": p_buy, "hold": rest, "skip": rest}},
            "risk": {"choice": "3"}}}
    return fake


def _etherion(level=None, age_h=0.0, raw=None):
    if os.path.exists(ETH):
        os.remove(ETH)
    if level is None and raw is None:
        return
    doc = raw if raw is not None else {"risk_level": level, "regime": "X"}
    if raw is None:
        from datetime import datetime, timedelta, timezone
        doc["ts"] = (datetime.now(timezone.utc) - timedelta(hours=age_h)).isoformat()
    with open(ETH, "w") as f:
        json.dump(doc, f)


def _reset(mode="veto", **env):
    for k in list(os.environ):
        if k.startswith("VALEDIA_NIMBLE"):
            os.environ.pop(k)
    os.environ["VALEDIA_SYSTEMONE"] = "1"
    os.environ["VALEDIA_NIMBLE_MODE"] = mode
    os.environ.update(env)
    s1._reset_digest()
    nd._WARMED = True
    cl._reset_cache(items=[])
    _etherion(None)


def _decide(post, cls="prediction", side="yes", economics=None, source="rule"):
    return nd.decide_candidate(cls, {"ticker": "KXTEST-1", "venue": "kalshi"}, side=side,
                               code_facts={"edge_positive": True}, informed={}, source=source,
                               post=post, get=_get, economics=economics)


def test_sample_probs_p_buy_and_confidence():
    """Verified sample: buy .4765 / skip .5235 → confidence .00159 = 1 - normalized entropy."""
    probs = {"buy": 0.4765, "skip": 0.5235}
    h = -sum(v * math.log(v) for v in probs.values()) / math.log(len(probs))
    assert abs((1 - h) - 0.00159) < 1e-5
    parsed = nd._parse({"answers": {"action": {"choice": "skip", "confidence": 0.00159,
                                               "probabilities": probs}}})
    assert parsed["p_buy"] == 0.4765 and parsed["p_action"] == 0.5235
    assert parsed["confidence"] == 0.00159  # sharpness, not P(chosen)


def test_break_even_math():
    be = nd.break_even_p
    # yes buy: YES price + fee per contract (+ half spread + slip when known)
    assert abs(be("prediction", {"side": "yes", "price": 0.40, "fee_per_contract": 0.0168}) - 0.4168) < 1e-9
    assert abs(be("prediction", {"side": "yes", "price": 40, "fee_per_contract": 0.0168,
                                 "half_spread": 0.01, "slip": 0.005}) - 0.4318) < 1e-9  # cents
    # no buy: the NO price, never the YES price
    assert abs(be("prediction", {"side": "no", "yes_price": 0.40, "no_price": 0.62,
                                 "fee_per_contract": 0.0165}) - 0.6365) < 1e-9
    assert be("prediction", {"yes_price": 0.40}, side="no") is None  # NO price unknown
    assert be("prediction", {"price": 0.3}, side="no") is None  # unmarked price: never for NO
    assert abs(be("prediction", {"no_price": 0.3}, side="no") - 0.3) < 1e-9  # side from caller
    assert abs(be("prediction", {"price": 0.3}, side="yes") - 0.3) < 1e-9
    assert be("prediction", {"price": 0.3}) is None and be("prediction", None, "yes") is None
    assert be("prediction", {"side": "yes", "price": 0}) is None
    # spot: 0.5 + roundtrip_fee_pct / 100 / 2 (documented approximation); explicit value wins
    assert abs(be("crypto", {"roundtrip_fee_pct": 2.2}) - 0.511) < 1e-9
    assert be("stock", {"roundtrip_fee_pct": 0.0}) == 0.5
    assert be("stock", {"roundtrip_fee_pct": 2.2, "break_even_p": 0.57}) == 0.57
    assert be("stock", {"roundtrip_fee_pct": -1}) is None and be("crypto", {}) is None
    assert be("forex", {"roundtrip_fee_pct": 1.0}) is None


def test_phi_multiplier_monotone_bounded_and_missing():
    _reset()
    assert nd.phi_multiplier() == (1.0, "etherion_missing")
    seen = []
    for lvl in ("NORMAL", "CAUTION", "ELEVATED", "WARNING", "HALT"):
        _etherion(lvl)
        m, src = nd.phi_multiplier()
        assert src == "etherion:" + lvl and 0.5 <= m <= 3.0, (lvl, m)
        # same quantity as the Kelly cap: rf = valedia_config._risk_factor()
        assert abs(m - (0.5 + 2.5 * (1 - vc._risk_factor()))) < 1e-9
        seen.append(m)
    assert seen == sorted(seen) and seen[0] == 0.5 and seen[-1] == 3.0, seen
    _etherion("NORMAL", age_h=48)
    assert nd.phi_multiplier() == (1.0, "etherion_stale")
    _etherion(raw={"timestamp": "2026-09-30 10:00 UTC", "signals": {}})  # alpha_risk_report shape
    assert nd.phi_multiplier() == (1.0, "etherion_no_risk_level")
    with open(ETH, "w") as f:
        f.write("{not json")
    m, src = nd.phi_multiplier()
    assert m == 1.0 and src.startswith("etherion_error_")
    # margin follows phi: calm smaller, high risk larger
    _etherion("NORMAL")
    assert nd.rule_inputs("crypto", {"roundtrip_fee_pct": 0})["margin"] == 0.01
    _etherion("HALT")
    assert nd.rule_inputs("crypto", {"roundtrip_fee_pct": 0})["margin"] == 0.06
    _etherion(None)
    assert nd.rule_inputs("crypto", {"roundtrip_fee_pct": 0})["margin"] == 0.02


def test_veto_grid():
    econ = {"side": "yes", "price": 0.70, "fee_per_contract": 0.0147}  # be .7147, req .7347
    _reset()
    req = nd.rule_inputs("prediction", econ)["required_p"]
    assert abs(req - 0.7347) < 1e-9, req
    grid = [  # (choice, conf, p_buy, allow, reason)
        ("buy", 0.9, round(req + 0.001, 4), True, "veto_model_buy"),
        ("buy", 0.9, round(req - 0.001, 4), False, "veto_p_below_required"),
        ("buy", 0.05, 0.95, True, "veto_model_buy"),       # exactly the sharpness floor
        ("buy", 0.049, 0.95, False, "veto_not_sharp"),
        ("hold", 0.9, 0.95, False, "veto_model_hold"),
        ("skip", 0.9, 0.95, False, "veto_model_skip"),
    ]
    for choice, conf, pb, allow, reason in grid:
        _reset()
        v = _decide(_post(choice, conf, pb), economics=econ)
        assert v["allow"] is allow and v["reason"] == reason, (choice, conf, pb, v)
        assert v["rule_reason"] == reason and abs(v["break_even_p"] - 0.7147) < 1e-9
        assert v["calibration"] == "identity" and v["p_cal"] == pb
        assert v["phi_multiplier"] == 1.0 and v["margin"] == 0.02
    # unknown economics → the floor alone
    _reset()
    v = _decide(_post("buy", 0.9, 0.61))
    assert v["allow"] is True and v["required_p"] == 0.60 and v["break_even_p"] is None
    _reset()
    assert _decide(_post("buy", 0.9, 0.59))["reason"] == "veto_p_below_required"
    # a cheap contract still needs the floor; an expensive one needs break-even + margin
    _reset()
    v = _decide(_post("buy", 0.9, 0.62), economics={"side": "yes", "price": 0.10})
    assert v["allow"] is True and v["required_p"] == 0.60
    # no-side buy uses the NO price
    _reset()
    v = _decide(_post("buy", 0.9, 0.83), side="no",  # .83 < .82 + .02 (YES .2 would pass)
                economics={"side": "no", "yes_price": 0.2, "no_price": 0.82})
    assert v["allow"] is False and abs(v["required_p"] - 0.84) < 1e-9
    # calibration lowers an overconfident source's p_buy below required
    _reset()
    cl._reset_cache(items=[{"source": "nimble_decide:cal", "p": 0.9, "y": i % 2}
                           for i in range(40)])
    v = _decide(_post("buy", 0.9, 0.9), source="cal")
    assert v["calibration"] == "isotonic" and v["p_cal"] == 0.5 and v["allow"] is False
    assert v["reason"] == "veto_p_below_required"
    cl._reset_cache(items=[])


def test_exits_always_allowed():
    for mode in ("veto", "shadow"):
        _reset(mode)
        calls = []
        for side in ("sell", "exit", "close"):
            v = _decide(_post("skip", 0.9, 0.1, calls), side=side,
                        economics={"side": "yes", "price": 0.99})
            assert v["allow"] is True and v["reason"] == "exit_never_blocked"
        assert calls == []


def test_economics_never_in_posted_state():
    _reset("shadow")
    calls = []
    econ = {"side": "yes", "price": SECRET_PRICE, "fee_per_contract": 0.0137,
            "half_spread": 0.0123, "slip": 0.0111}
    v = _decide(_post("buy", 0.9, 0.95, calls), economics=econ)
    assert len(calls) == 1 and v["break_even_p"] is not None
    body = json.dumps(calls[0])  # whole POST: no economics number anywhere
    for needle in ("7319", "0137", "0123", "0111", "break_even", "required_p", "phi_"):
        assert needle not in body, needle
    state = json.dumps(calls[0]["state"])  # (the question text itself mentions fees)
    for needle in ("price", "fee", "spread", "slip", "margin", "economics"):
        assert needle not in state, needle

    def walk(x):
        if isinstance(x, dict):
            for val in x.values():
                walk(val)
        else:
            assert x is None or isinstance(x, (bool, str)), x
    walk(calls[0]["state"])
    # the local log keeps the rule inputs (not posted) for review
    rec = [json.loads(x) for x in open(LOG) if x.strip()][-1]
    assert rec["label"]["rule"]["break_even_p"] == v["break_even_p"]


def test_shadow_never_blocks():
    econ = {"side": "yes", "price": 0.9, "fee_per_contract": 0.05}
    for choice, conf, pb, would in (("buy", 0.9, 0.5, "veto_p_below_required"),
                                    ("buy", 0.001, 0.99, "veto_not_sharp"),
                                    ("skip", 0.9, 0.1, "veto_model_skip"),
                                    ("buy", 0.9, 0.99, "veto_model_buy")):
        _reset("shadow")
        v = _decide(_post(choice, conf, pb), economics=econ)
        assert v["allow"] is True and v["reason"] == "shadow_logged", v
        assert v["rule_reason"] == would and v["required_p"] > 0.9, v
    os.environ.pop("VALEDIA_NIMBLE_MODE", None)
    assert nd.mode_for("prediction") == "shadow"  # default stays shadow


def test_deprecated_min_conf_ignored():
    """An old VALEDIA_NIMBLE_MIN_CONF=0.6 must not become a sharpness floor that blocks all."""
    nd._MIN_CONF_NOTED = False
    _reset(VALEDIA_NIMBLE_MIN_CONF="0.6")
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        assert nd.min_sharpness() == 0.05 and nd.min_conf() == 0.05
        assert nd.min_sharpness() == 0.05
        v = _decide(_post("buy", 0.3, 0.95))
    lines = [x for x in err.getvalue().splitlines() if x.strip()]
    assert len(lines) == 1, lines  # one-line note, once
    assert "deprecated and ignored" in lines[0] and "VALEDIA_NIMBLE_MIN_SHARPNESS" in lines[0]
    assert v["allow"] is True and v["reason"] == "veto_model_buy", v  # 0.3 >= 0.05
    _reset(VALEDIA_NIMBLE_MIN_CONF="0.6", VALEDIA_NIMBLE_MIN_SHARPNESS="0.4")
    with contextlib.redirect_stderr(io.StringIO()):
        assert nd.min_sharpness() == 0.4  # only the new name sets the floor
        assert _decide(_post("buy", 0.3, 0.95))["reason"] == "veto_not_sharp"
    _reset()
    assert nd.min_sharpness() == 0.05


if __name__ == "__main__":
    t0 = time.time()
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    _etherion(None)
    print("PASS test_nimble_veto_rule (%.2fs)" % (time.time() - t0))
