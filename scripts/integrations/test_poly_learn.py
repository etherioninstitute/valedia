#!/usr/bin/env python3
"""POLY-LEARN — paper classroom. Mocked. Never create_order.

Preview still works. Create still blocked. Proposals DRY/SHADOW.
Run: python3 test_poly_learn.py
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)

import master_allocator as ma
import poly_learn
import poly_us_smoke as smoke
import polymarket_order_body as pmob
import polymarket_us_client as pmu
import valedia_config as vc
from test_poly_us import MockXport, _client, _restore


def test_hygiene_and_fence():
    _restore()
    src = open(os.path.join(HERE, "poly_learn.py")).read()
    for banned in (
        "create_order(", "cancel_order(", "VENUE_WRITER[",
        "LIVE_CLIENT_HOOK =", "NAUTILUS_LIVE =", "--live",
        "POLYMARKET_US_LIVE\"] = \"1\"", "WEATHER_LIVE = True",
        "import requests",
    ):
        assert banned not in src, banned
    client_src = open(os.path.join(HERE, "polymarket_us_client.py")).read()
    assert "def get_market" in client_src
    assert "def get_bbo" in client_src
    assert "def get_book" in client_src
    assert pmob.LIVE_CLIENT_HOOK is None
    assert vc.VENUE_WRITER["polymarket"] == "none"
    assert pmu.live_enabled() is False
    assert vc.VALEDIA_VERSION == "0.53.0", vc.VALEDIA_VERSION
    cron = open(os.path.join(ROOT, "scripts", "install_cron.sh")).read()
    assert "poly_learn" not in cron
    doc = open(os.path.join(ROOT, "recommendations", "POLY-LEARN.md")).read()
    assert "POLYMARKET_US_LIVE" in doc
    assert "do not" in doc.lower() or "Do not" in doc
    inv = open(os.path.join(ROOT, "recommendations",
                            "PROFIT-PATHS-INVENTORY.md")).read()
    assert "Kalshi" in inv and "WEATHER_ARB" in inv and "OIL_BRACKET" in inv


def test_market_bbo_book_helpers():
    _restore()
    x = MockXport()
    c = _client(x)
    m = c.get_market("btc-100k")
    assert m["slug"] == "btc-100k"
    bbo = c.get_bbo("btc-100k")
    sized = pmu.sizing_from_bbo(bbo)
    assert sized["ok"] is True
    assert sized["ask"] == 0.56
    assert sized["bid"] == 0.54
    book = c.get_book("btc-100k")
    from_book = pmu.sizing_from_bbo(book)
    assert from_book["ok"] is True
    assert from_book["ask"] == 0.56
    by_id = c.get_market_by_id("42")
    assert by_id["slug"] == "btc-100k"
    paths = [call["path"] for call in x.calls]
    assert "/v1/market/slug/btc-100k" in paths
    assert "/v1/markets/btc-100k/bbo" in paths
    assert "/v1/markets/btc-100k/book" in paths
    assert not any(call["path"] == "/v1/orders" for call in x.calls)


def test_proposals_dry_shadow_file():
    _restore()
    d = tempfile.mkdtemp(prefix="poly_learn_")
    with open(os.path.join(d, "mirofish_neural_enrichment.json"), "w") as f:
        json.dump({"regime": {"label": "sideways"}, "fusion_touched": False}, f)
    with open(os.path.join(d, "persona_lens_scores.json"), "w") as f:
        json.dump({"fusion_touched": False, "auto_raise": False}, f)
    x = MockXport()
    c = _client(x)
    doc = poly_learn.run(client=c, models_dir=d, paper_usd=25)
    assert doc["status"] == "DRY/SHADOW"
    assert doc["create_order"] is False
    assert doc["live"] is False
    assert doc["n"] >= 1
    p0 = doc["proposals"][0]
    assert p0["status"] == "DRY/SHADOW"
    assert p0["create_order"] is False
    assert p0["platform"] == "polymarket_us"
    assert p0["tags"]["mirofish"] is True
    assert p0["tags"]["neural_regime"] == "sideways"
    path = os.path.join(d, "poly_us_proposals.json")
    assert os.path.isfile(path)
    saved = json.load(open(path))
    assert saved["status"] == "DRY/SHADOW"
    assert saved["create_order"] is False
    assert "/v1/orders" not in [call["path"] for call in x.calls]


def test_allocator_execute_never_creates():
    _restore()
    x = MockXport()
    c = _client(x)
    opps = ma.gather_poly_us_opportunities(client=c, limit=3)
    assert opps and opps[0]["status"] == "DRY/SHADOW"
    allocs = [{
        "platform": "polymarket_us", "type": "POLY_US_LEARN",
        "ticker": "btc-100k", "title": "paper", "side": "buy",
        "price": 0.56, "allocation": 5.0, "expected_profit": 0,
        "mode": "DRY/SHADOW",
    }]
    results = ma.execute_allocations(allocs, dry_run=False)
    assert results[0]["status"] == "DRY/SHADOW"
    assert results[0]["create_order"] is False
    assert not any(call["path"] == "/v1/orders" for call in x.calls)
    assert pmu.live_enabled() is False


def test_preview_still_works_create_blocked():
    _restore()
    x = MockXport()
    c = _client(x, live=False)
    out = smoke.run_smoke(client=c)
    assert out["ok"] is True
    assert out["create_blocked"] is True
    assert out["preview"]["kind"] == "preview"
    assert out["live"] is False
    try:
        c.create_order({"marketSlug": "btc-100k"})
        raise AssertionError("create must stay blocked")
    except pmu.LiveWriteBlocked:
        pass
    assert vc.VENUE_WRITER["polymarket"] == "none"
    assert os.environ.get("POLYMARKET_US_LIVE") in (None, "0", "false", "")


if __name__ == "__main__":
    test_hygiene_and_fence()
    test_market_bbo_book_helpers()
    test_proposals_dry_shadow_file()
    test_allocator_execute_never_creates()
    test_preview_still_works_create_blocked()
    print("POLY-LEARN paper classroom: PASS")
