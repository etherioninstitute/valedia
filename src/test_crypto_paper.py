#!/usr/bin/env python3
"""CRYPTO-PAPER-1 — paper simulation ledger. Fixture/mocked public. No orders.

Run: python3 test_crypto_paper.py
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

import crypto_paper as cp
import crypto_paper_run as cpr
import valedia_config as vc


def _banned(src):
    for s in (
        "create_order(", "cancel_order(", "withdraw(",
        "import ccxt", "from ccxt", "privateKey", "api_secret",
        "eth_account", "web3", "sign_transaction", "broadcast",
        "POLYMARKET_US_LIVE", "KALSHI_FEE_MAP_GATE",
        "VALEDIA_TRADING_HALT = True", "VENUE_WRITER[",
        "import requests",
    ):
        assert s not in src, s
    assert "--live" not in src.replace("--live-data", "")


def test_hygiene_and_fence():
    for name in ("crypto_paper.py", "crypto_paper_run.py"):
        _banned(open(os.path.join(HERE, name)).read())
    src = open(os.path.join(HERE, "crypto_paper.py")).read()
    assert "api.binance.us/api/v3/klines" in src
    assert "/api/v3/order" not in src
    assert vc.KALSHI_FEE_MAP_GATE is False
    assert vc.VALEDIA_TRADING_HALT is False
    assert vc.VALEDIA_TREASURY_AUTO is False
    assert os.environ.get("POLYMARKET_US_LIVE") in (None, "0", "false", "")
    assert vc.VALEDIA_VERSION == "0.74.1", vc.VALEDIA_VERSION
    cron = open(os.path.join(ROOT, "scripts", "install_cron.sh")).read()
    assert "crypto_paper" not in cron
    doc = open(os.path.join(ROOT, "recommendations", "CRYPTO-PAPER-DESK.md")).read()
    assert "would-have" in doc.lower() or "would-have" in doc
    assert "create_order" in doc
    inv = open(os.path.join(ROOT, "recommendations",
                            "PROFIT-PATHS-INVENTORY.md")).read()
    assert "crypto paper" in inv.lower() or "Crypto paper" in inv


def test_fee_fill_mtm():
    st = cp.book(1000.0)
    rec = cp.apply_fill(
        st, "BTC-USDT", "buy", 1.0, 100.0,
        slip=0.001, maker=0.001, taker=0.002, role="taker", ts="t1",
    )
    assert rec["px"] == 100.1
    assert rec["fee"] == 0.2002
    assert rec["source"] == "paper"
    assert abs(st["cash"] - (1000.0 - 100.1 - 0.2002)) < 1e-9
    assert st["positions"]["BTC-USDT"] == 1.0
    eq = cp.mark_to_market(st, {"BTC-USDT": 110.0})
    assert abs(eq - (st["cash"] + 110.0)) < 1e-9
    maker_fee = cp.fee_usd(100.0, "maker", 0.001, 0.002)
    taker_fee = cp.fee_usd(100.0, "taker", 0.001, 0.002)
    assert maker_fee == 0.1 and taker_fee == 0.2
    sold = cp.apply_fill(
        st, "BTC-USDT", "sell", 1.0, 110.0,
        slip=0.001, maker=0.001, taker=0.002, role="maker", ts="t2",
    )
    assert sold["px"] == 109.89
    assert "BTC-USDT" not in st["positions"]
    assert st["fees_paid"] > 0


def test_sma_fixture_run():
    bars = cp.synthetic_bars()
    intents = cp.sma_intents(bars, "BTC-USDT")
    sides = [i["side"] for i in intents]
    assert "buy" in sides and "sell" in sides
    d = tempfile.mkdtemp(prefix="crypto_paper_")
    fix = os.path.join(d, "bars.json")
    json.dump({"pair": "BTC-USDT", "bars": bars}, open(fix, "w"))
    out = os.path.join(d, "equity.json")
    doc = cp.run(fixture_path=fix, out_path=out, cash=10000.0)
    assert doc["status"] == "PAPER/SHADOW"
    assert doc["create_order"] is False
    assert doc["live"] is False
    assert doc["source"] == "fixture"
    assert doc["n_fills"] >= 2
    assert doc["fees_paid"] > 0
    assert len(doc["equity_curve"]) == len(bars)
    assert len(doc["daily_pnl"]) >= 1
    saved = json.load(open(out))
    assert saved["mode"] == "PAPER/SHADOW"
    assert abs(saved["would_have_pnl"] - (saved["ending_equity"] - saved["starting_equity"])) < 1e-6


def test_live_data_injected_no_http():
    bars = cp.synthetic_bars()[0:40]
    raw = []
    for b in bars:
        ts_ms = int(datetime_to_ms(b["ts"]))
        raw.append([ts_ms, b["open"], b["high"], b["low"], b["close"], b["volume"]])
    calls = []

    def get_fn(url):
        calls.append(url)
        assert "klines" in url
        assert "order" not in url
        assert "BTCUSDT" in url
        return raw

    d = tempfile.mkdtemp(prefix="crypto_paper_pub_")
    out = os.path.join(d, "equity.json")
    doc = cp.run(live_data=True, pair="BTC-USDT", get_fn=get_fn, out_path=out, cash=10000.0)
    assert calls and "klines" in calls[0]
    assert doc["source"] == "public"
    assert doc["live"] is False
    assert doc["create_order"] is False
    try:
        cp.fetch_public("DOGE-USDT", get_fn=get_fn)
        raise AssertionError("allowlist must reject")
    except ValueError:
        pass


def datetime_to_ms(ts):
    from datetime import datetime
    return datetime.fromisoformat(ts).timestamp() * 1000


def test_cli_dry_default():
    d = tempfile.mkdtemp(prefix="crypto_paper_cli_")
    fix = os.path.join(ROOT, "data", "crypto-paper", "bars_fixture.json")
    out = os.path.join(d, "equity.json")
    rc = cpr.main(["--fixture", fix, "--out", out, "--cash", "10000"])
    assert rc == 0
    saved = json.load(open(out))
    assert saved["source"] == "fixture"
    assert saved["live_data"] is False
    assert saved["n_fills"] >= 1
    help_src = open(os.path.join(HERE, "crypto_paper_run.py")).read()
    assert "--live-data" in help_src
    assert "add_argument(\"--live\")" not in help_src


if __name__ == "__main__":
    test_hygiene_and_fence()
    test_fee_fill_mtm()
    test_sma_fixture_run()
    test_live_data_injected_no_http()
    test_cli_dry_default()
    print("CRYPTO-PAPER-1 paper ledger: PASS")
