#!/usr/bin/env python3
"""TREASURY-1 — report-only floors. Mocked balances. Auto path refused.

No network, no keys, no ACH/wire, no --live, no writer flips.
Run: python3 test_treasury.py
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)

import rebalancer
import treasury_status as ts
import valedia_config as vc


def _restore():
    vc.VALEDIA_TREASURY_AUTO = False
    vc.REBALANCER_AUTOEXEC = False
    vc.NAUTILUS_LIVE = False
    vc.SPOT_RECONCILE_HTTP = False
    vc.MIROFISH_AUTO_RAISE = False
    vc.VALEDIA_SELF_ADJUST = False
    vc.VENUE_WRITER["kalshi"] = "valedia"
    vc.VENUE_WRITER["coinbase"] = "valedia"
    vc.VENUE_WRITER["alpaca"] = "valedia"
    vc.VENUE_WRITER["polymarket"] = "none"
    vc.VENUE_WRITER["hyperliquid"] = "none"
    vc.VENUE_WRITER["kraken"] = "none"
    os.environ.pop("VALEDIA_TREASURY_AUTO", None)
    os.environ.pop("REBALANCER_AUTOEXEC", None)
    os.environ.pop("POLYMARKET_US_LIVE", None)
    os.environ.pop("TREASURY_ALERT_WEBHOOK", None)
    vc._STAMP_CACHE = None


def test_hygiene_and_fence():
    _restore()
    src = open(os.path.join(HERE, "treasury_status.py")).read()
    for banned in (
        "import requests", "market_buy", "market_sell", "create_order",
        "initiate_transfer", "def withdraw", "--live", "VENUE_WRITER[",
        "POLYMARKET_US_LIVE = True", "REBALANCER_AUTOEXEC = True",
        "VALEDIA_TREASURY_AUTO = True", "NAUTILUS_LIVE = True",
    ):
        assert banned not in src, banned
    assert "NEVER moves money" in src or "never moves money" in src.lower()
    assert vc.VALEDIA_TREASURY_AUTO is False
    assert vc.treasury_auto_enabled() is False
    assert vc.REBALANCER_AUTOEXEC is False
    assert vc.rebalancer_autoexec_enabled() is False
    assert vc.TREASURY_FLOOR_POLYMARKET_US == 25.0
    assert vc.TREASURY_FLOOR_KALSHI == 25.0
    assert vc.TREASURY_FLOORS["polymarket_us"] == 25.0
    assert vc.TREASURY_FLOORS["hyperliquid"] is None
    assert vc.TREASURY_ALERT_WEBHOOK == ""
    assert vc.treasury_alert_webhook() == ""
    assert vc.VALEDIA_VERSION == "0.36.0", vc.VALEDIA_VERSION
    assert vc.VENUE_WRITER["polymarket"] == "none"
    assert vc.SPOT_RECONCILE_HTTP is False
    assert vc.NAUTILUS_LIVE is False
    reb = open(os.path.join(HERE, "rebalancer.py")).read()
    assert "TREASURY-1" in reb
    assert "REBALANCER_AUTOEXEC=False" in reb
    assert rebalancer.rebalancer_may_exec({"live_flag": True}) is False
    guide = open(os.path.join(ROOT, "recommendations",
                              "VALEDIA-TREASURY-FUNDING.md")).read()
    assert "first human fund" in guide.lower()
    assert "VALEDIA_TREASURY_AUTO" in guide
    assert "docs.polymarket.us" in guide
    assert "compound" in guide.lower()
    cron = open(os.path.join(ROOT, "scripts", "install_cron.sh")).read()
    assert "treasury_status" not in cron


def test_parsers_and_floors():
    _restore()
    assert ts.cash_from_kalshi({"balance": 2500}) == 25.0
    assert ts.cash_from_polymarket({
        "balances": [{"currency": "USD", "buyingPower": 30.55,
                      "currentBalance": 30.55}],
    }) == 30.55
    assert ts.cash_from_coinbase({"USD": {"balance": 81.0}}) == 81.0
    alp = ts.cash_from_alpaca({"cash": "28.00", "buying_power": "28.00"})
    assert alp["cash"] == 28.0
    assert alp["buying_power"] == 28.0


def test_mocked_status_warn_ok_unread():
    _restore()
    readers = {
        "kalshi": {"kind": "kalshi", "raw": {"balance": 500}},  # $5 < $25
        "polymarket_us": {"kind": "polymarket_us", "raw": {
            "balances": [{"currency": "USD", "buyingPower": 30.55}],
        }},
        "coinbase": {"kind": "coinbase", "raw": {"USD": {"balance": 81}}},
        "alpaca": {"kind": "alpaca", "raw": {"cash": 28, "buying_power": 28}},
    }
    report = ts.collect_status(readers=readers, live_read=False)
    by = {v["venue"]: v for v in report["venues"]}
    assert by["kalshi"]["status"] == "WARN"
    assert by["kalshi"]["cash"] == 5.0
    assert "kalshi below floor" in by["kalshi"]["alert"]
    assert by["polymarket_us"]["status"] == "OK"
    assert by["polymarket_us"]["cash"] == 30.55
    assert by["coinbase"]["status"] == "OK"
    assert by["alpaca"]["status"] == "OK"
    assert by["hyperliquid"]["status"] == "DOCUMENT"
    assert by["hyperliquid"]["readable"] is False
    assert by["kraken"]["status"] == "DOCUMENT"
    assert report["moves_money"] is False
    assert report["auto"]["executed"] is False
    assert report["auto"]["refused"] is True
    assert report["alerts"] == [by["kalshi"]["alert"]]
    assert report["rebalancer_autoexec"] is False
    assert report["webhook"]["posted"] is False
    unread = ts.collect_status(readers={}, live_read=False)
    ub = {v["venue"]: v for v in unread["venues"]}
    assert ub["kalshi"]["status"] == "UNREAD"
    assert ub["polymarket_us"]["status"] == "UNREAD"
    assert not unread["alerts"]


def test_auto_path_refused():
    _restore()
    os.environ["VALEDIA_TREASURY_AUTO"] = "1"
    assert vc.treasury_auto_enabled() is True
    raised = False
    try:
        ts.attempt_auto_topup(from_venue="mercury", to_venue="kalshi", usd=25)
    except ts.TreasuryAutoRefused:
        raised = True
    assert raised
    report = ts.collect_status(readers={
        "kalshi": {"cash": 5.0},
    }, live_read=False)
    assert report["auto"]["flag"] is True
    assert report["auto"]["executed"] is False
    assert report["auto"]["refused"] is True
    assert report["moves_money"] is False
    hook = ts.post_alert_webhook("https://example.invalid/hook", {"x": 1})
    assert hook["posted"] is False
    os.environ.pop("VALEDIA_TREASURY_AUTO", None)
    assert vc.treasury_auto_enabled() is False


def test_emit_json():
    _restore()
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "treasury_status.json")
        report = ts.emit_status(path=path, readers={
            "polymarket_us": {"cash": 10.0},
        }, live_read=False)
        assert os.path.isfile(path)
        saved = json.load(open(path))
        assert saved["valedia_version"] == "0.36.0"
        assert saved["moves_money"] is False
        by = {v["venue"]: v for v in saved["venues"]}
        assert by["polymarket_us"]["status"] == "WARN"
        assert report["path"] == path


if __name__ == "__main__":
    test_hygiene_and_fence()
    test_parsers_and_floors()
    test_mocked_status_warn_ok_unread()
    test_auto_path_refused()
    test_emit_json()
    print("treasury: PASS")
