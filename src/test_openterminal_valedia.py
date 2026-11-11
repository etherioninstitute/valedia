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
from datetime import datetime, timezone
from http.server import HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Unit tests assert git-default OFF without Burton's ~/.openclaw/.env flips.
os.environ["OPENCLAW_SKIP_ENV"] = "1"

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
        assert p["trading_halt"] is False
        assert p["mode"] == "LIVE"
        assert p["mode_why"] == "writer"
        assert p["mode"] == cockpit.cockpit_mode()
        assert p["identity"].startswith("v" + vc.VALEDIA_VERSION)
        assert p["branch"]
        assert "sha" in p and "dirty" in p
        assert [c["name"] for c in p["connections"]] == [
            "litellm", "bionic", "ruflo", "kalshi"]
        assert all(c["status"] == "SKIP" for c in p["connections"])
        assert "n" in p["poly_loop"] and "skip_reasons" in p["poly_loop"]
        assert "live" in p["poly_loop"]
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
        assert p["valedia_version"] == vc.VALEDIA_VERSION
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
    assert vc.VALEDIA_VERSION == "0.75.0"
    assert cockpit.DEFAULT_PORT == 8765
    assert cockpit.DEFAULT_HOST == "127.0.0.1"
    assert cockpit.DEFAULT_PORT != 3000
    assert cockpit.DEFAULT_PORT != 4010
    assert cockpit.DEFAULT_PORT != 4000


def test_mode_truth():
    class _Live:
        NAUTILUS_LIVE = True
        SCALPER_LIVE = False
        WEATHER_LIVE = False
        REBALANCER_AUTOEXEC = False
    assert cockpit.cockpit_mode(_Live) == "LIVE"

    class _Dry:
        NAUTILUS_LIVE = False
        SCALPER_LIVE = False
        WEATHER_LIVE = False
        REBALANCER_AUTOEXEC = False
        EXEC_DRY_RUN = True
    assert cockpit.cockpit_mode(_Dry) == "DRY"

    class _Shadow:
        NAUTILUS_LIVE = False
        SCALPER_LIVE = False
        WEATHER_LIVE = False
        REBALANCER_AUTOEXEC = False
        EXEC_DRY_RUN = False
        NAUTILUS_SHADOW_COINBASE = True
    assert cockpit.cockpit_mode(_Shadow) == "SHADOW"

    class _Fenced:
        NAUTILUS_LIVE = False
        SCALPER_LIVE = False
        WEATHER_LIVE = False
        REBALANCER_AUTOEXEC = False
        EXEC_DRY_RUN = False
    assert cockpit.cockpit_mode(_Fenced) == "FENCED"

    class _Halt:
        NAUTILUS_LIVE = True

        def trading_halted(self):
            return True

        def controls_snapshot(self):
            return {"items": [{"category": "writer", "value": "valedia"}]}

    assert cockpit.cockpit_mode(_Halt) == "FENCED"
    assert cockpit.cockpit_mode() == "LIVE"
    assert cockpit._mode_why() == "writer"


def test_treasury_stale_and_fresh_and_injected():
    d, ledger, q = _tmp()
    _use(d, ledger, q)
    now = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
    try:
        old = cockpit.panel(now=now)
        assert old["venues"]["kalshi"]["freshness"] == "STALE"
        assert old["venues"]["kalshi"]["status"] == "STALE"
        assert old["venues"]["kalshi"]["cash"] == 5.0
        assert old["venues"]["kalshi"]["age_s"] > cockpit.STALE_S
        assert old["venues"]["polymarket_us"]["cash"] == 30.55
        assert "STALE" in old["venues"]["source"] and "age=" in old["venues"]["source"]
        with open(os.path.join(d, "treasury_status.json"), "w") as f:
            json.dump({"ts": now.isoformat(), "venues": [
                {"venue": "kalshi", "cash": 5.0, "status": "WARN"},
                {"venue": "polymarket_us", "cash": 30.55, "status": "OK"},
            ]}, f)
        fresh = cockpit.panel(now=now)
        assert fresh["venues"]["kalshi"]["freshness"] == "CACHED"
        assert fresh["venues"]["kalshi"]["status"] == "WARN"
        assert fresh["venues"]["kalshi"]["age_s"] == 0
        cockpit.set_paths(readers={
            "kalshi": {"kind": "kalshi", "raw": {"balance": 5000}},
            "polymarket_us": {"kind": "polymarket_us", "raw": {
                "buyingPower": "12.5", "currency": "USD"}},
        })
        live = cockpit.panel(now=now)
        assert live["venues"]["kalshi"]["freshness"] == "LIVE"
        assert live["venues"]["kalshi"]["cash"] == 50.0
        assert live["venues"]["polymarket_us"]["freshness"] == "LIVE"
        assert live["venues"]["polymarket_us"]["cash"] == 12.5
        assert "live GET" in live["venues"]["source"]
    finally:
        _restore()


def test_poly_loop_and_ping_and_controls():
    d, ledger, q = _tmp()
    path = os.path.join(d, "last_run.json")
    with open(path, "w") as f:
        json.dump({
            "n": 3, "live": False, "status": "DRY/SHADOW",
            "skip_reasons": {"no_fv": 2, "edge_lt_8": 1},
            "ts": "2026-09-21T00:00:00+00:00",
        }, f)
    _use(d, ledger, q)
    cockpit.set_paths(poly_loop=path, ping=lambda host, port: host == "127.0.0.1" and port == 4000)
    try:
        p = cockpit.panel()
        assert p["poly_loop"]["present"] is True
        assert p["poly_loop"]["n"] == 3
        assert p["poly_loop"]["live"] is False
        assert p["poly_loop"]["skip_reasons"]["no_fv"] == 2
        rows = {c["name"]: c["status"] for c in p["connections"]}
        assert rows == {"litellm": "OK", "bionic": "FAIL", "ruflo": "FAIL", "kalshi": "FAIL"}
        items = vc.controls_snapshot()["items"]
        html = cockpit.render_controls_html(items)
        page = cockpit.ui_html()
        src = open(os.path.join(HERE, "valedia_cockpit.html"), encoding="utf-8").read()
        assert "it.source" in src and "forEach" in src and ".filter(" not in src
        assert 'id="conns"' in src and 'id="pl-n"' in src
        assert "does not restart" in src
        for it in items:
            needle = f'data-control="{it["name"]}"'
            assert needle in html, it["name"]
            assert needle in page, it["name"]
            assert f'data-source="{it["source"]}"' in html
            if isinstance(it["value"], bool):
                i = html.index(needle)
                chunk = html[i:i + 500]
                assert ("ON" if it["value"] else "OFF") in chunk
                assert f'badge {it["source"]}' in chunk
        for n in ("VALEDIA_SYSTEMONE", "POLY_LOOP_EDGE_PTS", "POLY_LOOP_MAX_OPEN",
                  "POLY_LOOP_DD_STOP", "POLY_LOOP_DD_STOP_ARMED", "KALSHI_FEE_MAP_GATE"):
            assert f'data-control="{n}"' in html
        short = [it for it in items if it["name"] != "VALEDIA_SYSTEMONE"]
        missing = [it["name"] for it in items
                   if f'data-control="{it["name"]}"' not in cockpit.render_controls_html(short)]
        assert missing == ["VALEDIA_SYSTEMONE"]
    finally:
        _restore()


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
    test_mode_truth()
    test_treasury_stale_and_fresh_and_injected()
    test_poly_loop_and_ping_and_controls()
    test_http_smoke()
    print("OPENTERMINAL-VALEDIA: PASS")
