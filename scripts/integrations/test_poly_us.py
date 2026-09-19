#!/usr/bin/env python3
"""POLY-US-2 — Polymarket US DCM client. Mocked. No live orders.

No network unless keys present (optional live-http skip if no keys).
No VENUE_WRITER flip, no LIVE_CLIENT_HOOK, no create in tests.
Run: python3 test_poly_us.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)

import forward_test_ledger as ftl
import poly_us_smoke as smoke
import polymarket_order_body as pmob
import polymarket_us_client as pmu
import valedia_config as vc


def _restore():
    vc.NAUTILUS_ENABLED = False
    vc.NAUTILUS_LIVE = False
    vc.VENUE_WRITER["polymarket"] = "none"
    vc.MIROFISH_AUTO_RAISE = False
    vc.VALEDIA_SELF_ADJUST = False
    os.environ.pop("POLYMARKET_US_LIVE", None)
    os.environ.pop("POLYMARKET_US_HTTP", None)
    os.environ.pop("SPOT_RECONCILE_HTTP", None)


class MockXport:
    def __init__(self):
        self.calls = []
        self.replies = {}

    def __call__(self, method, path, url, headers, body):
        self.calls.append({"method": method, "path": path, "url": url,
                           "headers": headers, "body": body})
        key = method + " " + path
        if key in self.replies:
            return self.replies[key]
        if path.startswith("/v2/tags/") and path.endswith("/events"):
            slug = path.split("/")[3]
            return {"events": [{"slug": slug + "-ev", "tags": [{"slug": slug}],
                                "markets": [{"slug": slug + "-mkt"}]}]}
        if path == "/v1/markets":
            return {"markets": [{"slug": "btc-100k",
                                 "tags": [{"slug": "crypto"}]}]}
        if path == "/v1/events":
            return {"events": [{"slug": "ev-1"}]}
        if path == "/v1/account/balances":
            return {"balances": [{"currency": "USD", "buyingPower": 0}]}
        if path == "/v1/whoami":
            return {"user": "mock"}
        if path == "/v1/portfolio/positions":
            return {"positions": {}}
        if path == "/v1/orders/open":
            return {"orders": []}
        if path.startswith("/v1/order/") and method == "GET":
            return {"order": {"id": path.rsplit("/", 1)[-1],
                              "state": "ORDER_STATE_NEW", "cumQuantity": 0}}
        if path == "/v1/order/preview":
            inner = body or {}
            if isinstance(inner.get("request"), dict):
                inner = inner["request"]
            return {"order": {"marketSlug": inner.get("marketSlug")}}
        if path == "/v1/orders":
            return {"id": "ord-us-1"}
        if path.endswith("/cancel"):
            return {"canceledOrderIds": [path.split("/")[3]]}
        return {"ok": True}

    def last(self):
        return self.calls[-1]


def _client(xport, live=False):
    return pmu.PolymarketUSClient(
        key_id="test-key-id", secret_key="dGVzdA==", live=live,
        http=False, transport=xport)


def test_hygiene_and_fence():
    _restore()
    src = open(os.path.join(HERE, "polymarket_us_client.py")).read()
    for banned in (
        "import requests", "py_clob_client", "from polymarket_us",
        "VENUE_WRITER[", "LIVE_CLIENT_HOOK =", "NAUTILUS_LIVE =",
        "--live", "clob.polymarket.com", "gamma-api.polymarket.com",
    ):
        assert banned not in src, banned
    assert "api.polymarket.us" in src
    assert "gateway.polymarket.us" in src
    assert pmob.LIVE_CLIENT_HOOK is None
    assert vc.VENUE_WRITER["polymarket"] == "none"
    assert vc.nautilus_may_dispatch("polymarket") is False
    assert pmu.live_enabled() is False
    assert pmu.http_enabled() is False
    clob = open(os.path.join(HERE, "polymarket_client.py")).read()
    assert "gamma-api.polymarket.com" in clob
    assert "clob.polymarket.com" in clob
    cron = open(os.path.join(ROOT, "scripts", "install_cron.sh")).read()
    assert "polymarket_us_client" not in cron
    assert "poly_us_smoke" not in cron
    cut = open(os.path.join(ROOT, "recommendations",
                            "POLYMARKET-US-CUTOVER.md")).read()
    assert "do not flip" in cut.lower()
    assert "VENUE_WRITER" in cut
    assert "POLYMARKET_KEY_ID" in cut
    matrix = open(os.path.join(ROOT, "recommendations",
                               "POLYMARKET-US-API-MATRIX.md")).read()
    assert "preview" in matrix.lower()
    assert "websocket" in matrix.lower()
    assert "whoami_retail" in matrix.lower() or "retail identity" in matrix.lower()
    assert "api.prod.polymarketexchange.com" in matrix
    assert vc.VALEDIA_VERSION == "0.38.0", vc.VALEDIA_VERSION
    assert 'self._request("GET", "/v1/whoami"' not in src
    assert "def whoami_retail" in src
    smoke_src = open(os.path.join(HERE, "poly_us_smoke.py")).read()
    assert "POLYMARKET_US_LIVE\"] = \"0\"" in smoke_src
    assert "create_order" in smoke_src
    assert "LiveWriteBlocked" in smoke_src
    assert "POLYMARKET_US_LIVE\"] = \"1\"" not in smoke_src


def test_public_reads_and_nav():
    _restore()
    x = MockXport()
    c = _client(x)
    mk = c.list_markets(limit=5, active=True)
    assert mk["markets"][0]["slug"] == "btc-100k"
    ev = c.list_events(limit=3, tag_slug="politics")
    assert ev["events"]
    last = x.last()
    assert last["url"].startswith("https://gateway.polymarket.us")
    assert "tagSlug=politics" in last["url"]
    assert "clob.polymarket.com" not in last["url"]
    seen = {}
    for cat in ("Sports", "Politics", "Crypto", "Weather", "Tech",
                "Culture", "Econ"):
        doc = c.list_nav_markets(cat)
        seen[cat] = pmu.nav_tag_slug(cat)
        assert doc["events"], cat
    assert seen["Sports"] == "sports"
    assert seen["Politics"] == "politics"
    assert seen["Crypto"] == "crypto"
    assert seen["Weather"] == "weather"
    assert seen["Tech"] == "tech"
    assert seen["Culture"] == "culture"
    assert seen["Econ"] == "econ"
    paths = [call["path"] for call in x.calls]
    assert "/v2/tags/sports/events" in paths
    assert "/v2/tags/politics/events" in paths
    assert "/v2/tags/crypto/events" in paths
    assert "/v2/tags/culture/events" in paths
    assert pmu.nav_tag_slug("entertainment") == "entertainment"


def test_auth_reads_and_write_gate():
    _restore()
    x = MockXport()
    c = _client(x, live=False)
    ident = c.whoami()
    assert ident["ok"] is True
    assert ident["source"] == "balances"
    assert ident["balances"][0]["currency"] == "USD"
    assert "pendingWithdrawals" not in ident["balances"][0]
    assert ident.get("positions_n") == 0
    assert c.balances()["balances"][0]["currency"] == "USD"
    assert c.positions()["positions"] == {}
    assert c.get_open_orders()["orders"] == []
    paths = [call["path"] for call in x.calls]
    assert "/v1/whoami" not in paths
    assert "/v1/account/balances" in paths
    auth_urls = [call["url"] for call in x.calls]
    assert any(u.startswith("https://api.polymarket.us/v1/account/balances")
               for u in auth_urls)
    prev = c.preview_order({
        "marketSlug": "btc-100k",
        "intent": "ORDER_INTENT_BUY_LONG",
        "type": "ORDER_TYPE_LIMIT",
        "price": {"value": "0.55", "currency": "USD"},
        "quantity": 1,
    })
    assert prev["kind"] == "preview"
    assert prev["filled"] is False
    posted_prev = [call for call in x.calls if call["path"] == "/v1/order/preview"]
    assert posted_prev and "request" in (posted_prev[-1]["body"] or {})
    assert posted_prev[-1]["body"]["request"]["marketSlug"] == "btc-100k"
    try:
        c.create_order({"marketSlug": "btc-100k"})
        raise AssertionError("create must be gated")
    except pmu.LiveWriteBlocked as e:
        assert "POLYMARKET_US_LIVE" in str(e)
    try:
        c.cancel_order("ord-us-1")
        raise AssertionError("cancel must be gated")
    except pmu.LiveWriteBlocked:
        pass
    assert not any(call["path"] == "/v1/orders" for call in x.calls)
    live = _client(x, live=True)
    ack = live.create_order({
        "marketSlug": "btc-100k",
        "intent": "ORDER_INTENT_BUY_LONG",
        "type": "ORDER_TYPE_LIMIT",
        "quantity": 1,
    })
    assert ack["kind"] == "create_ack"
    assert ack["id"] == "ord-us-1"
    assert ack["filled"] is False
    posted = [call for call in x.calls if call["path"] == "/v1/orders"]
    assert posted and posted[-1]["method"] == "POST"
    assert posted[-1]["url"].startswith("https://api.polymarket.us")


def test_whoami_retail_error_is_not_institutional():
    _restore()
    x = MockXport()
    x.replies["GET /v1/account/balances"] = {"status": 404, "error": "nope"}
    c = _client(x, live=False)
    ident = c.whoami_retail(include_positions=False)
    assert ident["ok"] is False
    assert ident["source"] == "balances"
    assert ident.get("status") == 404
    assert [call["path"] for call in x.calls] == ["/v1/account/balances"]


def test_mocked_preview_smoke_never_creates():
    _restore()
    x = MockXport()
    c = _client(x, live=False)
    out = smoke.run_smoke(client=c)
    assert out["ok"] is True
    assert out["live"] is False
    assert out["create_blocked"] is True
    assert out["whoami"]["source"] == "balances"
    assert out["preview"]["kind"] == "preview"
    assert out["preview"]["quantity"] == 1
    paths = [call["path"] for call in x.calls]
    assert "/v1/whoami" not in paths
    assert "/v1/order/preview" in paths
    assert "/v1/orders" not in paths
    assert pmu.live_enabled() is False


def test_http_off_without_transport():
    _restore()
    c = pmu.PolymarketUSClient(live=False, http=False, transport=None)
    try:
        c.list_markets()
        raise AssertionError("HTTP must stay off")
    except RuntimeError as e:
        assert "HTTP off" in str(e)


def test_fill_true_preview_and_ack_not_filled():
    _restore()
    prev = {"kind": "preview", "order": {"id": "should-not-fill"}}
    fill, payload = pmu.reconcile_us_fill(prev, lambda: {
        "order": {"id": "should-not-fill", "state": "ORDER_STATE_FILLED",
                  "cumQuantity": 9}})
    assert fill["complete"] is False
    assert payload is None

    ack = {"kind": "create_ack", "id": "ord-us-1"}
    fill, payload = pmu.reconcile_us_fill(ack, None)
    assert fill["complete"] is False and payload is None

    fill, payload = ftl.resolve_polymarket_fill(
        ack, lambda: {"order": {"id": "ord-us-1",
                                "state": "ORDER_STATE_FILLED",
                                "cumQuantity": 5,
                                "avgPx": {"value": "0.42", "currency": "USD"}}})
    assert fill["complete"] is True
    assert fill["qty"] == 5.0
    assert fill["price"] == 0.42
    assert fill["order_id"] == "ord-us-1"

    fill, payload = ftl.resolve_polymarket_fill(
        ack, lambda: {"executions": [{
            "tradeId": "tr-9", "order_id": "other", "lastShares": 99}]})
    assert fill["complete"] is False

    fill, payload = ftl.resolve_polymarket_fill(
        ack, lambda: {"executions": [{
            "tradeId": "tr-1", "order_id": "ord-us-1", "lastShares": "3",
            "lastPx": {"value": "0.50"}}]})
    assert fill["complete"] is True and fill["qty"] == 3.0
    assert fill["trade_id"] == "tr-1"

    fill, payload = ftl.resolve_polymarket_fill(
        {"success": True}, lambda: {"order": {"id": "ord-us-1",
                                              "state": "ORDER_STATE_FILLED",
                                              "cumQuantity": 5}})
    assert fill["complete"] is False


def test_optional_readonly_smoke():
    """Keys present → whoami/balances + preview. Never create. Skip if no keys."""
    _restore()
    pmu.load_openclaw_env()
    os.environ["POLYMARKET_US_LIVE"] = "0"
    probe = pmu.PolymarketUSClient(live=False, http=False, transport=None)
    if not probe.has_keys():
        return
    try:
        out = smoke.run_smoke()
    except (OSError, TimeoutError) as e:
        print("optional live-http smoke skipped:", type(e).__name__)
        return
    assert out["ok"] is True
    assert out["create_blocked"] is True
    assert out["live"] is False
    assert out["whoami"]["source"] == "balances"
    assert out["preview"]["kind"] == "preview"
    assert out["preview"]["filled"] is False


if __name__ == "__main__":
    test_hygiene_and_fence()
    test_public_reads_and_nav()
    test_auth_reads_and_write_gate()
    test_whoami_retail_error_is_not_institutional()
    test_mocked_preview_smoke_never_creates()
    test_http_off_without_transport()
    test_fill_true_preview_and_ack_not_filled()
    test_optional_readonly_smoke()
    print("POLY-US-2 retail whoami + preview dry-run: PASS")
