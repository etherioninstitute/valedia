#!/usr/bin/env python3
"""H4 — OpenTerminal GET panel. Shadow/GET/status only. No OMS.

No network, no keys, no --live. Injected temp models/ledger.
Run: python3 test_h4.py
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import forward_test_ledger as ftl
import valedia_cockpit as cockpit
import valedia_config as vc


NOW = datetime(2026, 9, 18, 21, 0, 0, tzinfo=timezone.utc)


def _tmp():
    d = tempfile.mkdtemp(prefix="h4_cockpit_")
    ledger = os.path.join(d, "ftl.jsonl")
    open(ledger, "w").close()
    q = os.path.join(d, "nautilus_intent_queue.jsonl")
    with open(q, "w") as f:
        f.write(json.dumps({"intent_id": "s1", "dispatchable": False, "live_flag": False}) + "\n")
        f.write(json.dumps({"intent_id": "s2", "dispatchable": False}) + "\n")
    with open(os.path.join(d, "mirofish_neural_enrichment.json"), "w") as f:
        json.dump({"symbols": {"BTC-USD": {"regime": {"label": "bear"}}},
                   "fusion_touched": False}, f)
    with open(os.path.join(d, "persona_lens_scores.json"), "w") as f:
        json.dump({"fusion_touched": False, "auto_raise": False}, f)
    return d, ledger, q


def test_panel_get_only():
    d, ledger, q = _tmp()
    orig = ftl.LEDGER
    ftl.LEDGER = ledger
    cockpit.set_paths(models_dir=d, ledger=ledger, intent_queue=q)
    try:
        ftl.record_unreconciled("SPORTS", "H4-U", "yes", 0.80, 2, "kalshi",
                                intended_source="live", order_id="ack-h4")
        ftl.book_kalshi_fill(
            "SPORTS", "H4-F", "yes",
            {"order": {"order_id": "fill-h4", "status": "executed", "fill_count": 2,
                       "remaining_count": 0, "yes_price": 80, "count": 2}},
            intended_source="live")
        code, body = cockpit.handle("GET", "/status")
        assert code == 200, body
        assert body["mode"] == "FENCED"
        assert body["get_only"] is True
        assert body["no_orders"] is True
        assert body["auto_raise"] is False
        assert body["valedia_version"] == "0.32.0"
        assert body["ftl"]["profit_is_fee_true"] is True
        assert body["ftl"]["unreconciled"] >= 1
        assert body["shadow"]["intents"] == 2
        assert body["shadow"]["dispatchable"] == 0
        assert body["neural"]["fed"] is True
        assert body["neural"]["fusion_touched"] is False
        assert body["personas"]["present"] is True
        assert body["personas"]["fusion_touched"] is False
        assert body["mirofish"]["auto_raise"] is False
        bad = cockpit.handle("POST", "/status")
        assert bad[0] == 405
        assert cockpit.handle("PUT", "/api/valedia/status")[0] == 405
        assert cockpit.handle("GET", "/order")[0] == 404
        assert cockpit.handle("GET", "/api/valedia/status")[0] == 200
    finally:
        ftl.LEDGER = orig
        cockpit.set_paths(models_dir=cockpit.MODELS, ledger=None, intent_queue=None)


def test_mode_live_only_when_flag():
    class _Live:
        NAUTILUS_LIVE = True
        SCALPER_LIVE = False
        WEATHER_LIVE = False
        REBALANCER_AUTOEXEC = False
    assert cockpit.cockpit_mode(_Live) == "LIVE"
    assert cockpit.cockpit_mode() == "FENCED"
    assert vc.NAUTILUS_LIVE is False
    assert vc.SCALPER_LIVE is False
    assert vc.WEATHER_LIVE is False
    assert vc.REBALANCER_AUTOEXEC is False
    assert vc.MIROFISH_AUTO_RAISE is False


def test_hygiene_and_version():
    src = open(os.path.join(HERE, "valedia_cockpit.py")).read()
    assert "create_order" not in src
    assert "place_market_order" not in src
    assert "place_order" not in src
    assert "VENUE_WRITER" not in src
    assert "MIROFISH_AUTO_RAISE =" not in src
    assert "--live" not in src
    assert "import requests" not in src
    assert "do_POST" in src
    assert vc.VALEDIA_VERSION == "0.32.0", vc.VALEDIA_VERSION
    vc._STAMP_CACHE = None
    assert vc.version_stamp().startswith("v0.32.0 ")
    assert src.count("serve_forever") == 1  # optional loopback only, not cron


if __name__ == "__main__":
    test_panel_get_only()
    test_mode_live_only_when_flag()
    test_hygiene_and_version()
    print("H4 OpenTerminal GET panel: PASS")
