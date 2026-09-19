#!/usr/bin/env python3
"""H2 — Buffett/Lynch/Hayes lenses on MiroFish. No weight, no Kelly, no orders.

Run: python3 test_h2.py
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import persona_lenses as pl
from mirofish_signal_extractor import process_mirofish_report
import valedia_config as vc


SIG = {
    "ts": "2026-09-18T00:00:00+00:00",
    "status": "active",
    "overall_bias": 32.0,
    "macro_bias": 40.0,
    "source": "mirofish_swarm",
    "events": [{"type": "regulation", "direction": "RISK_ON"}],
    "signals": {
        "BTC-USD": {"score": 42.5, "direction": "SELL", "confidence": 0.36},
        "AAPL": {"score": 61.0, "direction": "BUY", "confidence": 0.4},
        "SOL-USD": {"score": 50.0, "direction": "HOLD", "confidence": 0.1},
    },
}


def test_hygiene():
    src = open(os.path.join(HERE, "persona_lenses.py")).read()
    assert "VENUE_WRITER" not in src
    assert "create_order" not in src
    assert "place_market_order" not in src
    assert "KellySizer" not in src
    assert "MIROFISH_AUTO_RAISE =" not in src
    assert "effective_mirofish_weight" not in src
    assert "import requests" not in src
    ext = open(os.path.join(HERE, "mirofish_signal_extractor.py")).read()
    assert "persona_lenses" in ext
    assert "MIROFISH_AUTO_RAISE =" not in ext
    seed = open(os.path.join(HERE, "mirofish_seed_accurate.py")).read()
    assert "persona_lenses" in seed
    assert vc.MIROFISH_AUTO_RAISE is False
    assert vc.MIROFISH_UNPROVEN_CAP == 0.10
    assert vc.MIROFISH_PROVEN_CAP == 0.15
    assert vc.NAUTILUS_LIVE is False
    assert vc.SPOT_RECONCILE_HTTP is False
    assert vc.REBALANCER_AUTOEXEC is False
    assert vc.VALEDIA_VERSION == "0.33.0"


def test_buffett_crypto_abstains_equity_fear():
    btc = pl.buffett("BTC-USD", SIG["signals"]["BTC-USD"])
    assert btc["verdict"] == "OUTSIDE_CIRCLE"
    assert btc["abstain"] is True
    assert btc["score"] == 50
    aapl = pl.buffett("AAPL", {**SIG["signals"]["AAPL"], "overall_bias": 32.0})
    assert aapl["verdict"] == "GREEDY_WHEN_FEARFUL"
    assert aapl["score"] == 58
    greedy = pl.buffett("AAPL", {"overall_bias": 81})
    assert greedy["verdict"] == "FEARFUL_WHEN_GREEDY"


def test_lynch_hayes_asset_class():
    assert pl.lynch("BTC-USD", {})["verdict"] == "NOT_GARP"
    assert pl.lynch("AAPL", {})["abstain"] is True
    assert pl.hayes("AAPL", {})["verdict"] == "OUTSIDE_CIRCLE"
    h = pl.hayes("BTC-USD", {
        "overall_bias": 80, "events": [{"direction": "RISK_ON"}],
    })
    assert h["verdict"] == "STRUCTURAL_LONG"
    assert h["score"] > 50
    assert h["abstain"] is False


def test_attach_does_not_change_scores_or_weight():
    before_w = vc.MIROFISH_WEIGHT
    before_auto = vc.MIROFISH_AUTO_RAISE
    out = pl.attach_to_signal(SIG)
    assert out["persona_lenses"] is True
    assert out["signals"]["BTC-USD"]["score"] == 42.5
    assert out["signals"]["BTC-USD"]["direction"] == "SELL"
    assert out["overall_bias"] == 32.0
    assert out["signals"]["BTC-USD"]["persona"]["buffett"]["verdict"] == "OUTSIDE_CIRCLE"
    assert out["signals"]["AAPL"]["persona"]["lynch"]["id"] == "lynch"
    pack = pl.score_document(SIG)
    assert pack["fusion_touched"] is False
    assert pack["auto_raise"] is False
    assert pack["intents"] == []
    assert pack["mirofish_weight_unchanged"] is True
    assert vc.MIROFISH_WEIGHT == before_w
    assert vc.MIROFISH_AUTO_RAISE is before_auto


def test_extractor_wires_and_writes(tmp_path=None):
    d = tempfile.mkdtemp()
    dest = os.path.join(d, "mirofish_signal.json")
    report = {
        "outline": {"summary": "Bitcoin likely to recover from extreme fear"},
        "markdown_content": (
            "Bitcoin rally surge accumulate buying pressure near support. "
            "Institutional traders executed a BUY trade for BTC-USD. "
            "Apple looks cheap after the panic. AAPL buy."
        ),
    }
    out = process_mirofish_report(report, out_path=dest)
    assert "persona_lenses" in out
    raw = json.load(open(dest))
    assert raw.get("persona_lenses") in (True, False)
    sibling = os.path.join(d, "persona_lens_scores.json")
    if out.get("persona_lenses"):
        assert os.path.isfile(sibling)
        pack = json.load(open(sibling))
        assert pack["fusion_touched"] is False
        assert pack["auto_raise"] is False
        assert "create_order" not in json.dumps(pack)


def test_seed_section_advisory():
    text = pl.apply_seed_text("# seed\n", signal_doc=SIG)
    assert "Persona lenses" in text
    assert "not a live order" in text.lower() or "Not a live order" in text
    assert "BTC-USD" in text


def main():
    test_hygiene()
    test_buffett_crypto_abstains_equity_fear()
    test_lynch_hayes_asset_class()
    test_attach_does_not_change_scores_or_weight()
    test_extractor_wires_and_writes()
    test_seed_section_advisory()
    print("test_h2.py PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
