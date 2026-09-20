#!/usr/bin/env python3
"""OPENTERMINAL-VALEDIA — read-only glance UI. No OMS.

No network to venues, no keys, no --live. Injected temp models/ledger.
Run: python3 test_openterminal_valedia.py
"""
import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import valedia_cockpit as cockpit
import valedia_config as vc


def _tmp():
    d = tempfile.mkdtemp(prefix="otv_")
    ledger = os.path.join(d, "ftl.jsonl")
    open(ledger, "w").close()
    q = os.path.join(d, "nautilus_intent_queue.jsonl")
    with open(q, "w") as f:
        f.write(json.dumps({"intent_id": "s1", "dispatchable": False}) + "\n")
    with open(os.path.join(d, "pnl_tracker.json"), "w") as f:
        json.dump({"summary": {
            "wins": 10, "losses": 28, "net_pnl": -12.62,
            "current_balance": 5.0, "current_deployed": 48.79,
            "last_updated": "2026-09-19T00:00:00+00:00",
        }}, f)
    with open(os.path.join(d, "treasury_status.json"), "w") as f:
        json.dump({"ts": "2026-09-19T00:00:00+00:00", "venues": [
            {"venue": "kalshi", "cash": 5.0, "status": "WARN"},
            {"venue": "polymarket_us", "cash": 30.55, "status": "OK"},
        ]}, f)
    with open(os.path.join(d, "mirofish_signal.json"), "w") as f:
        json.dump({"ts": "2026-09-19T00:00:00+00:00", "overall_bias": 49.1}, f)
    with open(os.path.join(d, "mirofish_neural_enrichment.json"), "w") as f:
        json.dump({"symbols": {}, "fusion_touched": False}, f)
    with open(os.path.join(d, "persona_lens_scores.json"), "w") as f:
        json.dump({"fusion_touched": False}, f)
    return d, ledger, q


def _use(d, ledger, q):
    cockpit.set_paths(models_dir=d, ledger=ledger, intent_queue=q)


def _restore():
    cockpit.set_paths(models_dir=cockpit.MODELS, ledger=None, intent_queue=None)
    os.environ.pop("VALEDIA_TRADING_HALT", None)
    vc.VALEDIA_TRADING_HALT = False


def test_panel_broker_vs_ftl():
    d, ledger, q = _tmp()
    _use(d, ledger, q)
    try:
        p = cockpit.panel()
        assert p["mode"] == "FENCED"
        assert p["get_only"] is True
        assert p["no_orders"] is True
        assert p["broker"]["label"].startswith("broker")
        assert "≠ FTL" in p["broker"]["label"] or "not FTL" in p["broker"]["label"]
        assert p["broker"]["net"] == -12.62
        assert p["broker"]["wins"] == 10
        assert p["cash"] == 5.0
        assert p["deployed"] == 48.79
        assert p["ftl"]["n"] == 0
        assert p["ftl"]["empty"] is True
        assert "n=0" in p["ftl"]["note"]
        assert p["ftl"]["label"].startswith("fee-true")
        assert p["venues"]["kalshi"]["cash"] == 5.0
        assert p["venues"]["polymarket_us"]["cash"] == 30.55
        assert p["mirofish"]["bias"] == 49.1
        assert p["mirofish"]["ts"]
        assert p["mirofish"]["ears_on"] is True
        assert p["ears"]["gathering"] is True
        assert p["trading_halt"] is False
        assert p["shadow"]["dispatchable"] == 0
        assert p["valedia_version"] == "0.45.0"
    finally:
        _restore()


def test_halt_does_not_stop_ears():
    d, ledger, q = _tmp()
    _use(d, ledger, q)
    os.environ["VALEDIA_TRADING_HALT"] = "1"
    try:
        p = cockpit.panel()
        assert p["trading_halt"] is True
        assert p["ears"]["gathering"] is True
        assert p["mirofish"]["ears_on"] is True
        assert "orders" in p["ears"]["note"].lower()
    finally:
        _restore()


def test_unread_treasury():
    d, ledger, q = _tmp()
    os.remove(os.path.join(d, "treasury_status.json"))
    _use(d, ledger, q)
    try:
        p = cockpit.panel()
        assert p["venues"]["kalshi"]["status"] == "BROKER"
        assert p["venues"]["kalshi"]["cash"] == 5.0
        assert p["venues"]["polymarket_us"]["cash"] is None
        assert p["venues"]["polymarket_us"]["status"] == "UNREAD"
    finally:
        _restore()


def test_handle_ui_and_json():
    d, ledger, q = _tmp()
    _use(d, ledger, q)
    try:
        code, html = cockpit.handle("GET", "/")
        assert code == 200
        assert isinstance(html, str)
        assert "<title>Valedia</title>" in html
        low = html.lower()
        assert "type=\"submit\"" not in low
        assert "create_order" not in low
        assert "place_order" not in low
        assert "place_market_order" not in low
        assert 'id="refresh"' in html
        for verb in ("send", "buy", "sell"):
            assert f">{verb}<" not in low
            assert f'value="{verb}"' not in low
        code, body = cockpit.handle("GET", "/status")
        assert code == 200 and isinstance(body, dict)
        assert body["no_orders"] is True
        assert cockpit.handle("GET", "/ui")[0] == 200
        assert cockpit.handle("POST", "/")[0] == 405
        assert cockpit.handle("POST", "/status")[0] == 405
        assert cockpit.handle("GET", "/order")[0] == 404
    finally:
        _restore()


def test_html_hygiene_file():
    html = open(os.path.join(HERE, "valedia_cockpit.html"), encoding="utf-8").read().lower()
    src = open(os.path.join(HERE, "valedia_cockpit.py"), encoding="utf-8").read()
    for bad in ("create_order", "place_order", "place_market_order", "venue_writer"):
        assert bad not in html
        assert bad not in src.lower()
    assert "button" in html
    assert html.count("<button") == 1
    assert "refresh" in html
    assert "--live" not in src.replace("--listen", "")
    assert "import requests" not in src
    assert vc.VALEDIA_VERSION == "0.45.0"
    assert cockpit.DEFAULT_PORT == 8765
    assert cockpit.DEFAULT_HOST == "127.0.0.1"
    assert cockpit.DEFAULT_PORT != 3000
    assert cockpit.DEFAULT_PORT != 4010
    assert cockpit.DEFAULT_PORT != 4000


def test_mode_dry():
    class _Dry:
        NAUTILUS_LIVE = False
        SCALPER_LIVE = False
        WEATHER_LIVE = False
        REBALANCER_AUTOEXEC = False
        EXEC_DRY_RUN = True
    assert cockpit.cockpit_mode(_Dry) == "DRY"
    assert cockpit.cockpit_mode() == "FENCED"


def test_http_smoke():
    d, ledger, q = _tmp()
    _use(d, ledger, q)
    httpd = HTTPServer(("127.0.0.1", 0), cockpit._Handler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        ui = urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2)
        html = ui.read().decode()
        assert ui.status == 200
        assert "Valedia" in html
        assert "create_order" not in html
        st = json.load(urllib.request.urlopen(
            f"http://127.0.0.1:{port}/status", timeout=2))
        assert st["get_only"] is True
        assert st["ftl"]["empty"] is True
        assert st["broker"]["net"] == -12.62
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/status", method="POST", data=b"{}")
        try:
            urllib.request.urlopen(req, timeout=2)
            raise AssertionError("POST should 405")
        except urllib.error.HTTPError as e:
            assert e.code == 405
    finally:
        httpd.shutdown()
        _restore()


if __name__ == "__main__":
    test_panel_broker_vs_ftl()
    test_halt_does_not_stop_ears()
    test_unread_treasury()
    test_handle_ui_and_json()
    test_html_hygiene_file()
    test_mode_dry()
    test_http_smoke()
    print("OPENTERMINAL-VALEDIA: PASS")
