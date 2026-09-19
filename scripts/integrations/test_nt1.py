#!/usr/bin/env python3
"""NT-1 — neural-trader regime/signal → MiroFish JSON only.

No fusion weight change, no Intents, no AUTO_RAISE, no network orders.
Run: python3 test_nt1.py
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import mirofish_neural_enrich as mne
import neural_trader_fence as ntf
import test_slice13 as s13
import valedia_config as vc


MIRO = {
    "ts": "2026-09-18T00:00:00+00:00",
    "status": "active",
    "signals": {
        "BTC-USD": {"score": 42.5, "direction": "SELL", "confidence": 0.36},
        "SOL-USD": {"score": 50.0, "direction": "HOLD", "confidence": 0.1},
    },
    "source": "mirofish_swarm",
}

REGIME = {
    "command": "regime",
    "symbols": {
        "BTC-USD": {"regime": "ranging", "confidence": 0.62},
        "SOL-USD": {"label": "bull", "confidence": 0.71},
    },
}

SIGNAL = [
    {"symbol": "BTC-USD", "direction": "SELL", "quality": 0.41, "score": 0.41},
    {"symbol": "SOL-USD", "side": "HOLD", "confidence": 0.12},
]


def test_hygiene():
    src = open(os.path.join(HERE, "mirofish_neural_enrich.py")).read()
    assert "VENUE_WRITER" not in src
    assert "create_order" not in src
    assert "place_market_order" not in src
    assert "MIROFISH_AUTO_RAISE =" not in src
    assert "effective_mirofish_weight" not in src
    assert "unified_brain" not in src
    assert "import requests" not in src
    assert vc.MIROFISH_AUTO_RAISE is False
    assert vc.NAUTILUS_LIVE is False
    assert vc.SPOT_RECONCILE_HTTP is False


def test_build_maps_regime_and_signal():
    obj = mne.build_enrichment(MIRO, REGIME, SIGNAL)
    assert obj["version"] == "nt-1"
    assert obj["source"] == "neural-trader"
    assert obj["fusion_touched"] is False
    assert obj["mirofish_weight_unchanged"] is True
    assert obj["intents"] == []
    assert obj["auto_raise"] is False
    btc = obj["symbols"]["BTC-USD"]
    assert btc["regime"]["label"] == "ranging"
    assert btc["regime"]["confidence"] == 0.62
    assert btc["signal"]["direction"] == "SELL"
    assert btc["signal"]["quality"] == 0.41
    sol = obj["symbols"]["SOL-USD"]
    assert sol["regime"]["label"] == "bull"
    assert sol["signal"]["direction"] == "HOLD"


def test_write_does_not_touch_fusion():
    s13._restore()
    before_w = vc.MIROFISH_WEIGHT
    before_fw = dict(vc.FUSION_WEIGHTS)
    before_auto = vc.MIROFISH_AUTO_RAISE
    before_flags = s13._flags()
    md = tempfile.mkdtemp(prefix="nt1_")
    mne.set_paths(models_dir=md)
    with open(os.path.join(md, "mirofish_signal.json"), "w") as f:
        json.dump(MIRO, f)
    path, obj = mne.enrich(regime=REGIME, signal=SIGNAL)
    assert os.path.isfile(path)
    saved = json.load(open(path))
    assert saved["symbols"]["BTC-USD"]["regime"]["label"] == "ranging"
    assert saved["fusion_touched"] is False
    assert vc.MIROFISH_WEIGHT == before_w
    assert dict(vc.FUSION_WEIGHTS) == before_fw
    assert vc.MIROFISH_AUTO_RAISE is before_auto
    assert s13._flags() == before_flags
    assert obj["valedia_version"] == vc.VALEDIA_VERSION
    s13._restore()
    mne.set_paths(models_dir=os.path.join(mne.ROOT, "models"))


def test_cli_still_denies_live():
    try:
        ntf.assert_research_argv(["npx", "neural-trader", "--regime", "--live"])
    except ntf.FenceError:
        pass
    else:
        raise AssertionError("regime + --live must deny")
    # enricher main argv parser never takes --live/--broker
    src = open(os.path.join(HERE, "mirofish_neural_enrich.py")).read()
    assert "--broker" not in src
    assert "trader live" not in src


def test_version_contract():
    assert vc.VALEDIA_VERSION == "0.42.0", vc.VALEDIA_VERSION
    vc._STAMP_CACHE = None
    stamp = vc.version_stamp()
    assert stamp.startswith("v0.42.0 ")


if __name__ == "__main__":
    test_hygiene()
    test_build_maps_regime_and_signal()
    test_write_does_not_touch_fusion()
    test_cli_still_denies_live()
    test_version_contract()
    print("nt1 mirofish enrichment: PASS")
