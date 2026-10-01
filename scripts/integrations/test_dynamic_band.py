#!/usr/bin/env python3
"""DYNAMIC-BAND — MiroFish self-adjust inside band. Defaults seatbelted.

No network, no keys, no --live, no VENUE_WRITER flip.
Run: python3 test_dynamic_band.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Unit tests assert git-default OFF without Burton's ~/.openclaw/.env flips.
os.environ["OPENCLAW_SKIP_ENV"] = "1"

import mirofish_accuracy_ledger as mal
import test_slice13 as s13
import valedia_config as vc


def _rows_enriched(n, n_enr, enr_hits, plain_hits, asset="BTC-USD"):
    """n fee-true results; first n_enr tagged neural_enriched."""
    rows = s13._fee_true_rows(n, 0, asset=asset)
    results = [r for r in rows if r.get("type") == "result"]
    for i, r in enumerate(results):
        if i < n_enr:
            r["neural_enriched"] = True
            r["hit"] = i < enr_hits
        else:
            r["neural_enriched"] = False
            r["hit"] = (i - n_enr) < plain_hits
        r["realized"] = "UP" if r["hit"] else "DOWN"
    return rows


def test_hygiene_defaults_off():
    s13._restore()
    src = open(os.path.join(HERE, "valedia_config.py")).read()
    assert "VALEDIA_SELF_ADJUST   = False" in src
    assert "MIROFISH_AUTO_RAISE   = False" in src
    assert "MIROFISH_BAND_FLOOR   = 0.05" in src
    assert "MIROFISH_BAND_TOP     = 0.25" in src
    assert "MIROFISH_HARD_CEILING = 0.30" in src
    assert vc.VALEDIA_SELF_ADJUST is False
    assert vc.self_adjust_enabled() is False
    assert vc.MIROFISH_AUTO_RAISE is False
    assert vc.MIROFISH_UNPROVEN_CAP == 0.10
    assert vc.MIROFISH_PROVEN_CAP == 0.15
    assert vc.VENUE_WRITER["hyperliquid"] == "none"
    assert vc.NAUTILUS_LIVE is False
    assert vc.SPOT_RECONCILE_HTTP is False
    led = open(os.path.join(HERE, "mirofish_accuracy_ledger.py")).read()
    assert "VENUE_WRITER" not in led
    assert "--live" not in led
    assert "import requests" not in led


def test_gate_off_keeps_10_15():
    s13._restore()
    md = s13._tmpdir()
    s13._bind(md)
    s13._jsonl(os.path.join(md, "mirofish_accuracy_ledger.jsonl"),
               s13._fee_true_rows(20, 12))
    vc.VALEDIA_SELF_ADJUST = False
    vc.MIROFISH_WEIGHT = 0.50
    assert mal.effective_weight() == 0.10
    vc.MIROFISH_AUTO_RAISE = True
    assert mal.effective_weight() == 0.15
    assert vc.VENUE_WRITER["hyperliquid"] == "none"
    s13._restore()


def test_gate_on_unproven_stays_10():
    s13._restore()
    md = s13._tmpdir()
    s13._bind(md)
    s13._jsonl(os.path.join(md, "mirofish_accuracy_ledger.jsonl"),
               s13._fee_true_rows(5, 5))
    vc.VALEDIA_SELF_ADJUST = True
    vc.MIROFISH_AUTO_RAISE = False
    assert vc.self_adjust_enabled() is True
    st = mal.accuracy_stats()
    assert st["proven"] is False
    assert mal.effective_weight(st) == 0.10
    s13._restore()
    assert vc.self_adjust_enabled() is False


def test_gate_on_proven_raises_to_band_top():
    s13._restore()
    md = s13._tmpdir()
    s13._bind(md)
    s13._jsonl(os.path.join(md, "mirofish_accuracy_ledger.jsonl"),
               s13._fee_true_rows(20, 12))  # 60% > 55%
    vc.VALEDIA_SELF_ADJUST = True
    vc.MIROFISH_AUTO_RAISE = False  # allowed by flip, constant stays False
    w = mal.effective_weight()
    assert w == 0.25, w
    assert vc.MIROFISH_AUTO_RAISE is False
    assert vc.VENUE_WRITER["hyperliquid"] == "none"
    fw = vc.fusion_weights_for("crypto")
    assert fw["mirofish"] == 0.25
    assert abs(sum(fw.values()) - 1.0) < 1e-9
    s13._restore()


def test_gate_on_below_bar_decays_to_floor():
    s13._restore()
    md = s13._tmpdir()
    s13._bind(md)
    s13._jsonl(os.path.join(md, "mirofish_accuracy_ledger.jsonl"),
               s13._fee_true_rows(20, 10))  # 50% < 55%, n=20
    vc.VALEDIA_SELF_ADJUST = True
    st = mal.accuracy_stats()
    assert st["proven"] is False
    assert st["n_scored"] == 20
    assert mal.effective_weight(st) == 0.05
    s13._restore()


def test_enrich_bonus_inside_band_and_ceiling():
    s13._restore()
    md = s13._tmpdir()
    s13._bind(md)
    # n=20, 10 hits (50% below bar). 10 enriched 8 hits, 10 plain 2 hits.
    s13._jsonl(os.path.join(md, "mirofish_accuracy_ledger.jsonl"),
               _rows_enriched(20, 10, 8, 2))
    vc.VALEDIA_SELF_ADJUST = True
    st = mal.accuracy_stats()
    assert st["n_scored_enriched"] == 10
    assert st["enriched_accuracy"] == 0.8
    assert st["plain_accuracy"] == 0.2
    assert mal.effective_weight(st) == 0.07  # floor 0.05 + 0.02
    vc.MIROFISH_BAND_TOP = 0.40
    vc.MIROFISH_HARD_CEILING = 0.30
    s13._jsonl(os.path.join(md, "mirofish_accuracy_ledger.jsonl"),
               s13._fee_true_rows(20, 20))
    assert mal.effective_weight() == 0.30  # ceiling binds
    s13._restore()


def test_flip_does_not_open_writers():
    s13._restore()
    vc.VALEDIA_SELF_ADJUST = True
    os.environ["VALEDIA_SELF_ADJUST"] = "1"
    assert vc.self_adjust_enabled() is True
    assert vc.VENUE_WRITER["hyperliquid"] == "none"
    assert vc.VENUE_WRITER["polymarket"] == "none"
    assert vc.NAUTILUS_LIVE is False
    assert vc.NAUTILUS_ENABLED is False
    assert vc.spot_reconcile_http_enabled() is False
    assert vc.rebalancer_autoexec_enabled() is False
    assert vc.SCALPER_LIVE is False
    assert vc.WEATHER_LIVE is False
    s13._restore()
    assert vc.VALEDIA_SELF_ADJUST is False
    assert vc.self_adjust_enabled() is False


if __name__ == "__main__":
    test_hygiene_defaults_off()
    test_gate_off_keeps_10_15()
    test_gate_on_unproven_stays_10()
    test_gate_on_proven_raises_to_band_top()
    test_gate_on_below_bar_decays_to_floor()
    test_enrich_bonus_inside_band_and_ceiling()
    test_flip_does_not_open_writers()
    print("DYNAMIC-BAND self-adjust: PASS")
