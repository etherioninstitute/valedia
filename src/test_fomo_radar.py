#!/usr/bin/env python3
"""FOMO-RADAR enrich — offline fixtures. No network, no orders.

Run: python3 test_fomo_radar.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)

# Unit tests assert git-default OFF without Burton's ~/.openclaw/.env flips.
os.environ["OPENCLAW_SKIP_ENV"] = "1"

import fomo_radar_enrich as fr
import valedia_config as vc


def _banned(src):
    for s in (
        "create_order(", "cancel_order(", "execute_allocations(",
        "import ccxt", "fomoapi", "FOMOAPI", "urllib", "requests",
        "socket", "http.client", "web3", "privateKey", "eth_account",
        "POLYMARKET_US_LIVE", "KALSHI", "VALEDIA_TRADING_HALT",
        "import fomo", "from fomo",
    ):
        assert s not in src, s


def test_hygiene_and_fence():
    src = open(os.path.join(HERE, "fomo_radar_enrich.py")).read()
    _banned(src)
    assert vc.VALEDIA_VERSION == "0.75.0", vc.VALEDIA_VERSION
    assert vc.POLYMARKET_US_LIVE is False
    assert vc.VALEDIA_TRADING_HALT is False
    assert vc.VALEDIA_TREASURY_AUTO is False
    assert os.environ.get("POLYMARKET_US_LIVE") in (None, "0", "false", "")
    cron = open(os.path.join(ROOT, "scripts", "install_cron.sh")).read()
    assert "fomo_radar" not in cron


def test_fixture_tags():
    doc = fr.enrich_path()
    assert doc["create_order"] is False
    assert doc["sizes_money"] is False
    assert doc["annotate_only"] is True
    btc = doc["pairs"]["BTC-USDT"]
    assert btc["provenance_ok"] is True
    assert btc["seeded"] is False
    assert btc["wallet_conviction"] == 1.92  # 3 * 0.64
    assert btc["burst"] == "burst"
    assert btc["exit"] is None
    assert btc["tags"] == ["burst"]
    assert btc["judgement_rank"] == 1
    assert btc["sizes_money"] is False
    eth = doc["symbols"]["ETH"]
    assert eth["provenance_ok"] is False
    assert eth["seeded"] is True
    assert eth["wallet_conviction"] == 0.0
    assert eth["burst"] is None and eth["exit"] is None
    sol = doc["symbols"]["SOL-USD"]
    assert sol["provenance_ok"] is True
    assert sol["wallet_conviction"] == 1.28  # two trusted 80s; score 40 ignored
    assert sol["burst"] is None
    assert sol["exit"] == "exit"
    assert sol["tags"] == ["exit"]


def test_rules():
    dust = fr.tag_token({
        "symbol": "X", "pair": "X-USDT",
        "holders": [{"score": 100, "kind": "trade"}, {"score": 100, "kind": "dust"}],
    })
    assert dust["provenance_ok"] is True
    assert dust["wallet_conviction"] == 1.0
    short = fr.tag_token({
        "symbol": "Y", "pair": "Y-USDT",
        "holders": [{"score": 90, "kind": "trade"}],
        "burst": {"conviction": 5, "wallets": 4, "span_s": 100},
        "exit": {"state": "trimmed", "exit_pct": 0.2, "sellers": 3},
    })
    assert short["provenance_ok"] is True
    assert short["burst"] is None  # span under 180s
    assert short["exit"] is None  # exit_pct under 0.5
    lone = fr.tag_token({
        "symbol": "Z",
        "holders": [{"score": 90, "kind": "trade"}],
        "exit": {"state": "closed", "exit_pct": 1.0, "sellers": 1},
    })
    assert lone["exit"] is None  # sellers under 2


def test_stamps_do_not_size():
    enr = fr.enrich_path()
    paper = {
        "pair": "BTC-USDT", "cash": 100.0, "would_have_pnl": 1.5,
        "fills": [{"pair": "BTC-USDT", "qty": 0.1, "px": 10.0, "side": "buy", "fee": 0.01}],
    }
    stamped = fr.stamp_paper(paper, enr)
    assert stamped["cash"] == 100.0
    assert stamped["would_have_pnl"] == 1.5
    assert stamped["fills"][0]["qty"] == 0.1
    assert stamped["fills"][0]["px"] == 10.0
    assert stamped["fills"][0]["side"] == "buy"
    assert stamped["fomo_radar"]["burst"] == "burst"
    assert stamped["fomo_radar"]["sizes_money"] is False
    assert paper["fills"][0].get("fomo_radar") is None
    sig = {"signals": {"BTC-USD": {"score": 60.0, "direction": "BUY", "confidence": 0.4}},
           "weight": 0.10}
    out = fr.stamp_signal(sig, enr)
    assert out["signals"]["BTC-USD"]["score"] == 60.0
    assert out["signals"]["BTC-USD"]["direction"] == "BUY"
    assert out["signals"]["BTC-USD"]["confidence"] == 0.4
    assert out["weight"] == 0.10
    assert out["fomo_radar_enriched"] is True
    assert out["signals"]["BTC-USD"]["fomo_radar"]["wallet_conviction"] == 1.92
    assert sig["signals"]["BTC-USD"].get("fomo_radar") is None
    eth = fr.stamp_signal({"signals": {"ETH-USD": {"score": 55.0, "direction": "HOLD"}}}, enr)
    assert eth["signals"]["ETH-USD"]["score"] == 55.0
    assert eth["signals"]["ETH-USD"]["fomo_radar"]["provenance_ok"] is False
    assert eth["signals"]["ETH-USD"]["fomo_radar"]["wallet_conviction"] == 0.0


if __name__ == "__main__":
    test_hygiene_and_fence()
    test_fixture_tags()
    test_rules()
    test_stamps_do_not_size()
    print("FOMO-RADAR enrich: PASS")
