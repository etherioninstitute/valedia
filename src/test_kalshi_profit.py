#!/usr/bin/env python3
"""PROFIT-1 — EV preflight, maker-first, fill ledger. No network, no orders.

Run: python3 test_kalshi_profit.py
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)

# Unit tests assert git-default OFF without Burton's ~/.openclaw/.env flips.
os.environ["OPENCLAW_SKIP_ENV"] = "1"

import kalshi_fee_map as kfm
import kalshi_profit as kp
import valedia_config as vc


def _env(name, val=None):
    prev = os.environ.get(name)
    if val is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = val
    return prev


def _restore_env(name, prev):
    if prev is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = prev


def test_ev_math():
    # PDF Jul 2026: fee(0.50, 100, 1, taker) = 1.75 → 0.0175 / contract
    ev = kp.ev_preflight(0.10, 0.50, 100, 1, half_spread=0.01, slip=0.01, role="taker")
    assert ev["fee_total"] == 1.75
    assert abs(ev["fee_per"] - 0.0175) < 1e-12
    assert abs(ev["net"] - (0.10 - 0.0175 - 0.01 - 0.01)) < 1e-12
    assert ev["ok"] is True
    dead = kp.ev_preflight(0.02, 0.50, 1, 1, half_spread=0.02, slip=0.01, role="taker")
    assert dead["fee_total"] == 0.02
    assert dead["ok"] is False
    assert kp.half_spread(0.40, 0.46) == 0.03
    assert kp.half_spread(0, 0.50) == 0.0
    maker = kp.ev_preflight(0.10, 0.50, 100, 0, 0.0, 0.0, "maker")
    assert maker["fee_total"] == 0.0 and maker["net"] == 0.10


def test_gate_default_annotates_does_not_skip():
    assert vc.KALSHI_EV_PREFLIGHT_GATE is False
    assert kp.ev_gate_on() is False
    opp = {
        "ticker": "KXCPI-1", "series": "KXCPI", "side": "yes",
        "price": 0.50, "edge": 0.01, "yes_bid": 0.40, "yes_ask": 0.60,
        "strategy": "NO_HARVEST",
    }
    proceed, out = kp.annotate(opp, contracts=1, slip=0.01)
    assert proceed is True
    assert out["ev_preflight"]["ok"] is False  # 0.01 eaten by fee+spread+slip
    assert "maker_first" in out and "fill_quality" in out


def test_ev_gate_on_skips_negative():
    prev = _env("KALSHI_EV_PREFLIGHT_GATE", "1")
    try:
        assert kp.ev_gate_on() is True
        opp = {
            "ticker": "KXCPI-1", "series": "KXCPI", "side": "yes",
            "price": 0.50, "edge": 0.01, "yes_bid": 0.40, "yes_ask": 0.60,
        }
        proceed, _ = kp.annotate(opp, contracts=1, slip=0.01)
        assert proceed is False
        fat = {
            "ticker": "KXBTCY-1", "series": "KXBTCY", "side": "yes",
            "price": 0.50, "edge": 0.20, "yes_bid": 0.50, "yes_ask": 0.50,
        }
        ok, out = kp.annotate(fat, contracts=100, slip=0.0)
        assert ok is True and out["ev_preflight"]["ok"] is True
    finally:
        _restore_env("KALSHI_EV_PREFLIGHT_GATE", prev)


def test_maker_first_preference():
    fmap = kfm.build_map()
    # KXBTCY maker_viable; taker pays half-spread → prefer maker
    rt = kp.route("KXBTCY-1", "KXBTCY", edge=0.10, bid=0.49, ask=0.51,
                  C=100, slip=0.0, fmap=fmap)
    assert rt["maker_viable"] is True
    assert rt["prefer"] == "maker"
    assert rt["taker_clears"] is False
    assert rt["soft"] is True
    assert kp.should_skip_taker(rt) is False  # flag off
    prev = _env("KALSHI_MAKER_FIRST", "1")
    try:
        assert kp.maker_first_on() is True
        rt2 = kp.route("KXBTCY-1", "KXBTCY", edge=0.10, bid=0.49, ask=0.51,
                       C=100, slip=0.0, fmap=fmap)
        assert rt2["prefer"] == "maker" and rt2["soft"] is False
        assert kp.should_skip_taker(rt2) is True
        # hurdle -1 → taker clears, prefer taker
        hprev = _env("KALSHI_TAKER_HURDLE", "-1")
        try:
            rt3 = kp.route("KXBTCY-1", "KXBTCY", edge=0.10, bid=0.50, ask=0.50,
                           C=100, slip=0.0, fmap=fmap)
            assert rt3["taker_clears"] is True and rt3["prefer"] == "taker"
            assert kp.should_skip_taker(rt3) is False
        finally:
            _restore_env("KALSHI_TAKER_HURDLE", hprev)
        # KXCPI not maker_viable → taker, no skip
        rt4 = kp.route("KXCPI-1", "KXCPI", edge=0.10, bid=0.49, ask=0.51,
                       C=100, slip=0.0, fmap=fmap)
        assert rt4["maker_viable"] is False and rt4["prefer"] == "taker"
        assert kp.should_skip_taker(rt4) is False
    finally:
        _restore_env("KALSHI_MAKER_FIRST", prev)


def test_fill_ledger_update_and_demote_flag():
    td = tempfile.mkdtemp()
    path = os.path.join(td, "fill-quality.json")
    drift, doc = kp.record_fill(
        "KXNBAGAME-T", "yes", 0.50, 0.47, strategy="SPORTS",
        series="KXNBAGAME", path=path)
    assert abs(drift - 0.03) < 1e-12  # bought, mid dropped
    row = doc["series"]["KXNBAGAME"]
    assert row["n"] == 1
    assert abs(row["score"] - 0.3 * 0.03) < 1e-12
    assert kp.demote_blocks("KXNBAGAME-T", "SPORTS", path=path) is False
    n, doc = kp.ingest_fills(
        [{"ticker": "KXNBAGAME-U", "side": "yes", "fill_px": 0.50,
          "mid_after": 0.40, "strategy": "SPORTS", "series": "KXNBAGAME"}]
        * 6,
        path=path, ledger=doc)
    assert n == 6
    assert doc["series"]["KXNBAGAME"]["n"] == 7
    assert kp.demote_blocks("KXNBAGAME-T", "SPORTS", path=path) is False
    prev = _env("KALSHI_FILL_LEDGER_DEMOTE", "1")
    try:
        assert kp.fill_demote_on() is True
        assert kp.demote_blocks("KXNBAGAME-T", "SPORTS", ledger=doc) is True
    finally:
        _restore_env("KALSHI_FILL_LEDGER_DEMOTE", prev)
    # sell: mid up is adverse
    assert abs(kp.adverse_drift("no", 0.40, 0.45) - 0.05) < 1e-12
    saved = json.load(open(path))
    assert saved["kalshi_fill_ledger_demote"] is False
    assert vc.KALSHI_FILL_LEDGER_DEMOTE is False


def test_allocator_skip_when_ev_gate_on():
    import master_allocator as ma
    prev = _env("KALSHI_EV_PREFLIGHT_GATE", "1")
    kfm.reset_map()
    kfm._MAP = kfm.build_map()
    try:
        opps = [{
            "platform": "kalshi", "type": "NO_HARVEST", "ticker": "KXCPI-1",
            "title": "cpi", "side": "yes", "price": 0.50,
            "ev_per_dollar": 0.05, "ev_per_day": 0.05, "composite_score": 9,
            "hours_to_resolve": 1, "confidence": 0.8, "max_loss_pct": 100,
            "raw": {"series": "KXCPI"}, "yes_bid": 0.40, "yes_ask": 0.60,
        }]
        allocs, _ = ma.allocate(opps, {"deployable": 20.0})
        assert allocs == []
    finally:
        kfm.reset_map()
        _restore_env("KALSHI_EV_PREFLIGHT_GATE", prev)


def test_hygiene_and_call_sites():
    assert vc.VALEDIA_VERSION == "0.74.1", vc.VALEDIA_VERSION
    assert vc.KALSHI_FEE_MAP_GATE is False
    assert vc.KALSHI_EV_PREFLIGHT_GATE is False
    assert vc.KALSHI_MAKER_FIRST is False
    assert vc.KALSHI_FILL_LEDGER_DEMOTE is False
    assert vc.WEATHER_LIVE is False
    assert vc.POLYMARKET_US_LIVE is False
    src = open(os.path.join(HERE, "kalshi_profit.py")).read()
    for banned in ("create_order", "KALSHI_FEE_MAP_GATE=True",
                   "KALSHI_EV_PREFLIGHT_GATE = True",
                   "KALSHI_MAKER_FIRST = True",
                   "KALSHI_FILL_LEDGER_DEMOTE = True",
                   "VALEDIA_TRADING_HALT"):
        assert banned not in src, banned
    cfg = open(os.path.join(HERE, "valedia_config.py")).read()
    assert "KALSHI_FEE_MAP_GATE = False" in cfg
    assert "KALSHI_EV_PREFLIGHT_GATE = False" in cfg
    assert "KALSHI_MAKER_FIRST = False" in cfg
    assert "KALSHI_FILL_LEDGER_DEMOTE = False" in cfg
    assert "KALSHI_FEE_MAP_GATE = True" not in cfg
    assert "POLYMARKET_US_LIVE = False" in cfg
    assert "POLYMARKET_US_LIVE = True" not in cfg
    src_e = open(os.path.join(HERE, "kalshi_executor.py")).read()
    src_a = open(os.path.join(HERE, "master_allocator.py")).read()
    assert "kalshi_profit" in src_e and "kalshi_profit" in src_a
    assert "annotate" in src_e and "annotate" in src_a
    cron = open(os.path.join(ROOT, "scripts", "install_cron.sh")).read()
    assert "kalshi_profit" not in cron


if __name__ == "__main__":
    test_ev_math()
    test_gate_default_annotates_does_not_skip()
    test_ev_gate_on_skips_negative()
    test_maker_first_preference()
    test_fill_ledger_update_and_demote_flag()
    test_allocator_skip_when_ev_gate_on()
    test_hygiene_and_call_sites()
    print("PROFIT-1: PASS")
