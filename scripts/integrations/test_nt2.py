#!/usr/bin/env python3
"""NT-2 — wire neural enrichment into MiroFish seed/extractor + ledger tags.

No fusion weight change, no AUTO_RAISE, no Intents, no network orders.
Run: python3 test_nt2.py
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import mirofish_accuracy_ledger as mal
import mirofish_neural_enrich as mne
from mirofish_signal_extractor import process_mirofish_report
import test_slice13 as s13
import valedia_config as vc


ENR = {
    "symbols": {
        "BTC-USD": {
            "regime": {"label": "ranging", "confidence": 0.62},
            "signal": {"direction": "SELL", "quality": 0.41},
        },
        "SOL-USD": {
            "regime": {"label": "unknown", "confidence": 0.0},
            "signal": {"direction": "HOLD", "quality": None},
        },
    }
}

SIG = {
    "ts": "2026-09-18T00:00:00+00:00",
    "status": "active",
    "overall_bias": 58.5,
    "source": "mirofish_swarm",
    "signals": {
        "BTC-USD": {"score": 42.5, "direction": "SELL", "confidence": 0.36},
        "SOL-USD": {"score": 50.0, "direction": "HOLD", "confidence": 0.1},
    },
}

REPORT = {
    "outline": {"summary": "Bitcoin likely to recover from extreme fear"},
    "markdown_content": (
        "Bitcoin rally surge accumulate buying pressure near support. "
        "Institutional traders executed a BUY trade for BTC-USD."
    ),
}


def test_hygiene():
    s13._restore()
    src = open(os.path.join(HERE, "mirofish_neural_enrich.py")).read()
    assert "VENUE_WRITER" not in src
    assert "create_order" not in src
    assert "place_market_order" not in src
    assert "MIROFISH_AUTO_RAISE =" not in src
    assert "effective_mirofish_weight" not in src
    assert "import requests" not in src
    ext = open(os.path.join(HERE, "mirofish_signal_extractor.py")).read()
    assert "attach_to_signal" in ext
    assert "MIROFISH_AUTO_RAISE =" not in ext
    led = open(os.path.join(HERE, "mirofish_accuracy_ledger.py")).read()
    assert '"neural_enriched"' in led
    assert vc.MIROFISH_AUTO_RAISE is False
    assert vc.MIROFISH_UNPROVEN_CAP == 0.10
    assert vc.MIROFISH_PROVEN_CAP == 0.15
    assert vc.NAUTILUS_LIVE is False
    assert vc.SPOT_RECONCILE_HTTP is False


def test_attach_stamps_when_present_skips_unknown():
    out = mne.attach_to_signal(SIG, ENR)
    assert out["neural_enriched"] is True
    btc = out["signals"]["BTC-USD"]
    assert btc["neural"]["present"] is True
    assert btc["neural"]["regime"]["label"] == "ranging"
    assert btc["neural"]["signal"]["quality"] == 0.41
    assert btc["score"] == 42.5
    assert btc["direction"] == "SELL"
    assert out["overall_bias"] == 58.5
    assert "neural" not in out["signals"]["SOL-USD"]
    empty = mne.attach_to_signal(SIG, {})
    assert empty["neural_enriched"] is False
    assert "neural" not in empty["signals"]["BTC-USD"]
    assert empty["signals"]["BTC-USD"]["score"] == 42.5
    md = tempfile.mkdtemp(prefix="nt2_empty_")
    mne.set_paths(models_dir=md)
    missing = mne.attach_to_signal(SIG, None)
    assert missing["neural_enriched"] is False
    mne.set_paths(models_dir=os.path.join(mne.ROOT, "models"))


def test_seed_section_advisory_only():
    assert mne.seed_section({}) == ""
    sec = mne.seed_section(ENR)
    assert "BTC-USD" in sec
    assert "ranging" in sec
    assert "SOL-USD" not in sec
    assert "not an order" in sec.lower()
    assert "fusion-weight" in sec
    text = mne.apply_seed_text("# Live brief\n\nhello", ENR)
    assert text.startswith("# Live brief")
    assert "## Neural-trader context" in text
    assert mne.apply_seed_text("hello", {}) == "hello"


def test_extractor_carries_neural_does_not_change_bias():
    md = tempfile.mkdtemp(prefix="nt2_")
    out = os.path.join(md, "mirofish_signal.json")
    plain = process_mirofish_report(REPORT, enrichment={}, out_path=out)
    enr = process_mirofish_report(REPORT, enrichment=ENR, out_path=out)
    assert "BTC-USD" in enr["signals"]
    assert enr["neural_enriched"] is True
    assert enr["signals"]["BTC-USD"]["neural"]["regime"]["label"] == "ranging"
    assert plain["neural_enriched"] is False
    assert plain["overall_bias"] == enr["overall_bias"]
    assert plain["signals"]["BTC-USD"]["score"] == enr["signals"]["BTC-USD"]["score"]
    saved = json.load(open(out))
    assert saved["neural_enriched"] is True
    assert saved["source"] == "mirofish_swarm"


def test_record_tags_per_symbol():
    s13._restore()
    md = s13._tmpdir()
    s13._bind(md)
    attached = mne.attach_to_signal(SIG, ENR)
    assert mal.record(attached) == 0
    preds = {p["asset"]: p for p in mal._read() if p["type"] == "prediction"}
    assert preds["BTC-USD"]["neural_enriched"] is True
    assert "SOL-USD" not in preds  # HOLD skipped
    s13._restore()


def test_score_copies_tag_fee_true_only():
    s13._restore()
    md = s13._tmpdir()
    s13._bind(md)
    ts = s13._iso(s13.NOW - __import__("datetime").timedelta(days=6))
    due = s13._iso(s13.NOW - __import__("datetime").timedelta(days=1))
    later = s13._iso(s13.NOW - __import__("datetime").timedelta(days=2))
    btc = s13._pred("mf_btc", "BTC-USD", "BUY", ts, due)
    btc["neural_enriched"] = True
    eth = s13._pred("mf_eth", "ETH-USD", "BUY", ts, due)
    eth["neural_enriched"] = False
    sol = s13._pred("mf_sol", "SOL-USD", "SELL", ts, due)
    sol["neural_enriched"] = True
    s13._jsonl(os.path.join(md, "mirofish_accuracy_ledger.jsonl"), [btc, eth, sol])
    s13._jsonl(os.path.join(md, "forward_test_ledger.jsonl"), [
        s13._ftl_entry("e1", "BTC-USD", "buy", "live", later),
        s13._ftl_sett("e1", "BTC-USD", 2.5, later),
        s13._ftl_entry("e2", "ETH-USD", "buy", "paper", later),
        s13._ftl_sett("e2", "ETH-USD", 9.9, later, source="paper"),
        s13._ftl_entry("e3", "SOL-USD", "sell", "live", later),
        s13._ftl_sett("e3", "SOL-USD", 1.2, later),
    ])
    mal.score(now=s13.NOW)
    results = {r["pred_id"]: r for r in mal._read() if r["type"] == "result"}
    assert results["mf_btc"]["fee_true"] is True
    assert results["mf_btc"]["neural_enriched"] is True
    assert "mf_eth" not in results
    assert results["mf_sol"]["neural_enriched"] is True
    st = mal.accuracy_stats()
    assert st["n_scored"] == 2
    assert st["n_scored_enriched"] == 2
    assert st["n_scored_plain"] == 0
    assert st["weight"] == 0.10
    s13._restore()


def test_weight_math_unchanged_with_enriched_proven():
    s13._restore()
    md = s13._tmpdir()
    s13._bind(md)
    rows = s13._fee_true_rows(20, 12)
    for r in rows:
        r["neural_enriched"] = True
    s13._jsonl(os.path.join(md, "mirofish_accuracy_ledger.jsonl"), rows)
    st = mal.accuracy_stats()
    assert st["proven"] is True
    assert st["n_scored_enriched"] == 20
    vc.MIROFISH_WEIGHT = 0.20
    assert mal.effective_weight() == 0.10
    assert vc.MIROFISH_AUTO_RAISE is False
    assert vc.MIROFISH_UNPROVEN_CAP == 0.10
    assert vc.MIROFISH_PROVEN_CAP == 0.15
    assert vc.fusion_weights_for("equity")["mirofish"] == 0.10
    s13._restore()


def test_version_contract():
    assert vc.VALEDIA_VERSION == "0.29.0", vc.VALEDIA_VERSION
    stamp = vc.version_stamp()
    assert stamp.startswith("v0.29.0 ")
    src = open(os.path.join(HERE, "valedia_config.py")).read()
    assert "VALEDIA-MIROFISH-DYNAMIC-WEIGHT.md" in src
    assert "VALEDIA_SELF_ADJUST" in src
    assert "MIROFISH_AUTO_RAISE   = False" in src


if __name__ == "__main__":
    test_hygiene()
    test_attach_stamps_when_present_skips_unknown()
    test_seed_section_advisory_only()
    test_extractor_carries_neural_does_not_change_bias()
    test_record_tags_per_symbol()
    test_score_copies_tag_fee_true_only()
    test_weight_math_unchanged_with_enriched_proven()
    test_version_contract()
    print("nt2 mirofish neural wire: PASS")
