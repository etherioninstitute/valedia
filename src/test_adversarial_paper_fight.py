#!/usr/bin/env python3
"""Adversarial paper fight — mocked, no network, no orders, no LN.

Run: python3 test_adversarial_paper_fight.py
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Unit tests assert git-default OFF without Burton's ~/.openclaw/.env flips.
os.environ["OPENCLAW_SKIP_ENV"] = "1"

import adversarial_paper_fight as apf
import valedia_config as vc

NOW = datetime(2026, 9, 18, 20, 0, 0, tzinfo=timezone.utc)

SIG = {
    "ts": "2026-09-18T00:00:00+00:00",
    "status": "active",
    "overall_bias": 49.1,
    "source": "mirofish_swarm",
    "signals": {
        "BTC-USD": {"score": 42.5, "direction": "SELL", "confidence": 0.36},
        "SOL-USD": {"score": 50.0, "direction": "HOLD", "confidence": 0.1},
        "XRP-USD": {"score": 72.0, "direction": "BUY", "confidence": 0.55},
    },
    "events": [],
}

ENR = {
    "symbols": {
        "BTC-USD": {
            "regime": {"label": "bear", "confidence": 0.62},
            "signal": {"direction": "SELL", "quality": 0.41},
        },
        "XRP-USD": {
            "regime": {"label": "bull", "confidence": 0.5},
            "signal": {"direction": "SELL", "quality": 0.7},
        },
    }
}


def _src(*names):
    return "".join(open(os.path.join(HERE, n)).read() for n in names)


def test_hygiene():
    src = _src("adversarial_paper_fight.py", "adversarial_run.py")
    for banned in (
        "create_order", "place_market_order", "VENUE_WRITER",
        "REBALANCER_AUTOEXEC", "SPOT_RECONCILE_HTTP", "NAUTILUS_LIVE",
        "LF_OPERATOR", "lightningfaucet", "lnbits", "enqueue_intent",
        "import requests", "MIROFISH_AUTO_RAISE =",
    ):
        assert banned not in src, banned
    assert "crontab" in src  # runner says not installed
    assert vc.MIROFISH_AUTO_RAISE is False
    assert vc.NAUTILUS_LIVE is False
    assert vc.SPOT_RECONCILE_HTTP is False
    assert vc.REBALANCER_AUTOEXEC is False


def test_buffett_reuses_h2_persona():
    btc = apf.lens_buffett_value(apf._opp("BTC-USD", SIG["signals"]["BTC-USD"]))
    assert btc["abstain"] is True
    assert btc["side"] == "HOLD"
    assert btc["verdict"] == "OUTSIDE_CIRCLE"
    fear = apf.lens_buffett_value(apf._opp("AAPL", {
        "score": 40, "direction": "SELL", "overall_bias": 32.0, "asset_class": "equity",
    }))
    assert fear["abstain"] is False
    assert fear["verdict"] == "GREEDY_WHEN_FEARFUL"
    assert fear["side"] == "BUY"
    empty = apf.lens_buffett_value(apf._opp("SPY", {"score": 60, "asset_class": "equity"}))
    assert empty["abstain"] is True
    assert empty["verdict"] == "INSUFFICIENT_DATA"


def test_momentum_and_mirofish():
    opp = apf._opp("BTC-USD", SIG["signals"]["BTC-USD"])
    m = apf.lens_momentum_kernel(opp)
    assert m["side"] == "SELL"
    assert m["score"] == 42.5
    f = apf.lens_mirofish_enriched(opp)
    assert f["side"] == "SELL"
    opp_n = apf._opp("BTC-USD", {**SIG["signals"]["BTC-USD"], "neural": ENR["symbols"]["BTC-USD"]})
    f2 = apf.lens_mirofish_enriched(opp_n)
    assert f2["confidence"] >= f["confidence"]
    x = apf._opp("XRP-USD", {**SIG["signals"]["XRP-USD"], "neural": ENR["symbols"]["XRP-USD"]})
    disagree = apf.lens_mirofish_enriched(x)
    plain = apf.lens_mirofish_enriched(apf._opp("XRP-USD", SIG["signals"]["XRP-USD"]))
    assert disagree["side"] == "BUY"
    assert disagree["confidence"] < plain["confidence"]


def test_risk_veto_risk_off_and_fenced():
    buy = apf._vote("momentum_kernel", 72, "BUY", 0.6, "x")
    hold = apf._vote("buffett_value", 50, "HOLD", 0.9, "x", abstain=True)
    mf = apf._vote("mirofish_enriched", 72, "BUY", 0.5, "x")
    v = apf.lens_risk_veto(
        apf._opp("XRP-USD", SIG["signals"]["XRP-USD"]),
        [hold, buy, mf],
        {"events": [{"type": "geopolitics", "direction": "RISK_OFF"}], "overall_bias": 49},
    )
    assert v["veto"] is True
    assert "RISK_OFF" in v["rationale"]
    ok = apf.lens_risk_veto(
        apf._opp("BTC-USD", SIG["signals"]["BTC-USD"]),
        [hold, apf._vote("momentum_kernel", 42.5, "SELL", 0.4, "x"),
         apf._vote("mirofish_enriched", 42.5, "SELL", 0.3, "x")],
        {"events": [], "overall_bias": 49},
    )
    assert ok["veto"] is False
    fence = apf.lens_risk_veto(
        apf._opp("WEATHER", {"score": 80, "strategy": "WEATHER_ARB"}),
        [buy], {"events": []},
    )
    assert fence["veto"] is True
    halt = apf.lens_risk_veto(
        apf._opp("SPY", {"score": 70, "asset_class": "equity"}),
        [buy], {"ere": {"safe": False, "risk_level": "CRITICAL"}},
    )
    assert halt["veto"] is True


def test_fight_records_disagreement_no_intents():
    aapl = apf._opp("AAPL", {
        "score": 38, "direction": "SELL", "asset_class": "equity",
    })
    row = apf.fight_one(aapl, {"events": [], "overall_bias": 32})
    assert row["disagreement"] is True
    assert row["mode"] == "PAPER/SHADOW"
    sides = {v["lens"]: v["side"] for v in row["lenses"]}
    assert sides["buffett_value"] == "BUY"
    assert sides["momentum_kernel"] == "SELL"
    assert row["risk_veto"] is False
    assert "intents" not in row


def test_round_persist_mocked():
    md = tempfile.mkdtemp(prefix="adv_fight_")
    apf.set_paths(models_dir=md)
    doc = apf.run_round(signal_doc=SIG, enrich=ENR, ere={}, models_dir=md, write=True, now=NOW)
    assert doc["mode"] == "PAPER/SHADOW"
    assert doc["no_orders"] is True
    assert doc["no_ln"] is True
    assert doc["auto_raise"] is False
    assert doc["fusion_touched"] is False
    assert doc["intents"] == []
    assert doc["valedia_version"] == "0.75.0"
    assert doc["n"] == 3
    by = {r["symbol"]: r for r in doc["fights"]}
    assert by["BTC-USD"]["lenses"][0]["abstain"] is True
    assert by["BTC-USD"]["consensus_side"] == "SELL"
    assert by["XRP-USD"]["consensus_side"] == "BUY"
    path = doc["path"]
    assert os.path.basename(path).startswith("adversarial_round_")
    saved = json.load(open(path))
    assert saved["no_orders"] is True
    assert saved["fights"][0]["mode"] == "PAPER/SHADOW"
    # opps list without signal
    doc2 = apf.build_round(opps=[{"symbol": "ETH-USD", "score": 30, "direction": "SELL"}])
    assert doc2["n"] == 1
    assert doc2["fights"][0]["symbol"] == "ETH-USD"


def test_veto_wins_round():
    sig = dict(SIG)
    sig["events"] = [{"direction": "RISK_OFF", "type": "geopolitics"}]
    row = None
    for r in apf.build_round(signal_doc=sig, enrich={}).get("fights"):
        if r["symbol"] == "XRP-USD":
            row = r
    assert row is not None
    assert row["risk_veto"] is True
    assert row["winner"]["lens"] == "risk_veto"
    assert row["consensus_side"] == "HOLD"


def test_cli_and_version():
    md = tempfile.mkdtemp(prefix="adv_cli_")
    sigp = os.path.join(md, "mirofish_signal.json")
    json.dump(SIG, open(sigp, "w"))
    apf.set_paths(models_dir=md, signal_path=sigp)
    rc = apf.main(["--signal", sigp, "--models", md])
    assert rc == 0
    rounds = [n for n in os.listdir(md) if n.startswith("adversarial_round_")]
    assert len(rounds) == 1
    assert vc.VALEDIA_VERSION == "0.75.0"
    assert vc.version_stamp().startswith("v0.75.0 ")
    assert vc.MIROFISH_AUTO_RAISE is False


if __name__ == "__main__":
    test_hygiene()
    test_buffett_reuses_h2_persona()
    test_momentum_and_mirofish()
    test_risk_veto_risk_off_and_fenced()
    test_fight_records_disagreement_no_intents()
    test_round_persist_mocked()
    test_veto_wins_round()
    test_cli_and_version()
    print("adversarial paper fight: PASS")
