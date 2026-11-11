#!/usr/bin/env python3
"""ETORO-SUBSUME-1 — public API client + paper desk. No network. No writes.

Run: python3 test_etoro_copy_paper.py
"""
import json
import os
import sys
import tempfile
import urllib.request
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Unit tests assert git-default OFF without Burton's ~/.openclaw/.env flips.
os.environ["OPENCLAW_SKIP_ENV"] = "1"

import etoro_client as ec
import etoro_copy_paper as desk
import forward_test_ledger as ftl
import valedia_config as vc

KEYS = {"ETORO_API_KEY": "pub-test-key", "ETORO_USER_KEY": "user-test-key"}
LIVE = {"ETORO_LIVE": "1", "ETORO_UNLOCK": ec.UNLOCK, "ETORO_MODE": "live"}


def _client(transport=None, env=None, rate=None):
    return ec.EtoroClient(env=dict(env or KEYS), file_env={}, transport=transport, rate=rate)


def _boom(*_a, **_k):
    raise AssertionError("socket")


def test_headers_and_missing_keys():
    c = _client()
    h1, h2 = c.headers(), c.headers()
    assert h1["x-api-key"] == "pub-test-key"
    assert h1["x-user-key"] == "user-test-key"
    uuid.UUID(h1["x-request-id"])
    assert h1["x-request-id"] != h2["x-request-id"]
    assert "authorization" not in {k.lower() for k in h1}
    assert h1["user-agent"] == ec.USER_AGENT
    assert "python-urllib" not in h1["user-agent"].lower()
    alias = ec.EtoroClient(env={"ETORO_PUBLIC_KEY": "pub2", "ETORO_PRIVATE_KEY": "usr2"}, file_env={})
    assert alias.headers()["x-api-key"] == "pub2"
    try:
        ec.resolve_keys(env={}, file_env={})
    except ec.EtoroError as exc:
        assert exc.code == "missing_keys"
        assert "pub-test-key" not in str(exc)
    else:
        raise AssertionError("missing keys must refuse")
    path = tempfile.mktemp(suffix=".env")
    try:
        with open(path, "w") as f:
            f.write("OTHER_SECRET=nope\nETORO_API_KEY=fromfile\nETORO_USER_KEY=fromuser\n")
        found = ec.load_key_file(path)
        assert set(found) == {"ETORO_API_KEY", "ETORO_USER_KEY"}
        assert "nope" not in found.values()
    finally:
        os.remove(path)


def test_rankings_and_smart_search():
    seen = []

    def transport(method, url, headers):
        assert method == "GET"
        assert headers["x-api-key"] == "pub-test-key"
        seen.append(url)
        if "/rankings" in url:
            assert "period=OneYearAgo" in url and "sort=-copiers" in url
            return 200, {"results": [{
                "username": "steady-pi", "type": "trader", "gain": 0.22, "riskScore": 4,
                "copiers": 800, "peakToValley": 0.12, "winRatio": 68,
            }], "pagination": {"page": 1, "pageSize": 5, "hasNext": False}}
        if "type=smart-portfolio" in url and "Type=" not in url.split("?", 1)[-1]:
            raise ec.EtoroHTTP(400, "/api/v1/portfolios/search")
        if "Type=smart-portfolio" in url:
            return 200, {"results": [{
                "username": "crypto-smart", "type": "smart-portfolio", "copiers": 12,
                "profileUrl": "https://www.etoro.com/people/crypto-smart",
            }]}
        if url.endswith("/instrument-types"):
            return 200, {"instrumentTypes": [{"id": 5, "name": "Stocks"}]}
        if "/instruments/search" in url:
            return 200, {"results": [{"instrumentId": 1, "symbol": "AAPL"}]}
        return 200, {}

    c = _client(transport)
    parsed = desk.parse_rankings(c.rankings(page_size=5, popular_investor=True))
    assert parsed[0]["username"] == "steady-pi"
    assert desk.score(parsed[0])["band"] == "allowlist"
    again = desk.normalize(desk.normalize(parsed[0]))
    assert again["risk_score"] == 4 and desk.score(again)["band"] == "allowlist"
    pct = desk.normalize({"username": "dd", "type": "trader", "gain": 0.2, "riskScore": 4,
                          "copiers": 200, "peakToValley": -23.28, "winRatio": 60})
    assert abs(pct["peak_to_valley"] - 0.2328) < 1e-6
    assert desk.score(pct)["band"] == "allowlist"
    smart = desk.parse_search(c.search_portfolios("smart-portfolio", "crypto", limit=5))
    assert smart[0]["type"] == "smart-portfolio"
    assert desk.score(smart[0])["band"] == "review_only"  # search row has no gain/risk
    assert any("type=smart-portfolio" in u for u in seen)
    assert any("Type=smart-portfolio" in u for u in seen)
    types = c.instrument_types()
    assert types["instrumentTypes"][0]["name"] == "Stocks"
    assert c.search_instruments("AAPL")["results"][0]["symbol"] == "AAPL"
    c.people(["steady-pi"])
    c.copier_stats("steady-pi")
    assert any("/api/v1/user-info/people?" in u or u.endswith("/api/v1/user-info/people") for u in seen) or any("/user-info/people" in u for u in seen)
    assert any("/portfolios/steady-pi/copiers" in u for u in seen)
    try:
        c.search_portfolios("fund", "x")
    except ec.EtoroError as exc:
        assert exc.code == "bad_type"
    else:
        raise AssertionError("bad type")


def test_writes_refuse_without_socket():
    prev = urllib.request.urlopen
    urllib.request.urlopen = _boom
    try:
        off = _client(env=dict(KEYS, ETORO_LIVE="0"))
        calls = (
            (off.start_copy, ("steady-pi", 25), "copy_start"),
            (off.adjust_copy, ("steady-pi", 25), "etoro_live"),
            (off.close_copy, ("steady-pi",), "etoro_live"),
            (off.create_order, ({"instrumentId": 1},), "etoro_live"),
            (off.cancel_order, ("oid",), "etoro_live"),
            (off.money_withdraw, (), "etoro_live"),
        )
        for fn, args, code in calls:
            try:
                fn(*args)
            except ec.EtoroError as exc:
                assert exc.code == code, (fn.__name__, exc.code)
                assert "pub-test-key" not in str(exc)
            else:
                raise AssertionError(fn.__name__)
        try:
            off.request("POST", "/api/v2/trading/copy")
        except ec.EtoroError as exc:
            assert exc.code == "etoro_live"
        half = _client(env=dict(KEYS, ETORO_LIVE="1", ETORO_MODE="research"))
        try:
            half.start_copy("steady-pi", 25)
        except ec.EtoroError as exc:
            assert exc.code == "half_live"
        try:
            half.create_order({})
        except ec.EtoroError as exc:
            assert exc.code == "half_live"
        opened = _client(env=dict(KEYS, **LIVE))
        for fn, arg in ((opened.start_copy, ("steady-pi", 25)), (opened.adjust_copy, ("steady-pi", 10)),
                        (opened.close_copy, ("steady-pi",)), (opened.create_order, ({},)),
                        (opened.cancel_order, ("1",)), (opened.money_withdraw, ())):
            try:
                fn(*arg)
            except ec.EtoroError as exc:
                assert exc.code == "write_endpoint", exc.code
            else:
                raise AssertionError("unlocked live still has no sender")
        demo = _client(env=dict(KEYS, **LIVE))
        try:
            demo.start_copy("steady-pi", 25, demo=True)
        except ec.EtoroError as exc:
            assert exc.code == "write_endpoint"
        fenced = desk.guarded_write("copy_start", env={"ETORO_LIVE": "0"}, username="steady-pi")
        assert fenced["code"] == "copy_start" and fenced["start_copy"] is False and fenced["create_order"] is False
    finally:
        urllib.request.urlopen = prev


def test_decisions_jsonl_and_caps():
    path = tempfile.mktemp(suffix=".jsonl")
    try:
        rows = desk.run_scan(desk.fixture_rows(), path=path, equity=1000.0, env={})
        reasons = {r["ticker"]: r["reason"] for r in rows}
        assert reasons["steady-pi"] == "would_copy"
        assert reasons["crypto-smart"] == "would_copy"
        assert reasons["mid-book"] == "review_only"
        assert reasons["blowup"] == "refuse"
        logged = desk.read_decisions(path)
        assert len(logged) == 4
        for r in logged:
            assert "paper" in r["source"]
            assert r["create_order"] is False and r["start_copy"] is False and r["live"] is False
            assert r["filled"] is False and r["counts_toward_kelly"] is False
            assert r["would_have_pnl"] is None
        would = [r for r in logged if r["reason"] == "would_copy"]
        assert all(r["size_usd"] == 25 for r in would)
        assert ftl._is_non_real_source(desk.SOURCE)
    finally:
        for p in (path, path + ".write.lock"):
            if os.path.exists(p):
                os.remove(p)
    path = tempfile.mktemp(suffix=".jsonl")
    try:
        desk.run_scan(
            [desk.fixture_rows()[0], dict(desk.fixture_rows()[0], username="steady-two")],
            path=path, env={}, limits={"max_trade_size_usd": 25, "max_daily_notional_usd": 25, "min_size_usd": 5},
        )
        logged = desk.read_decisions(path)
        assert [r["reason"] for r in logged] == ["would_copy", "daily_notional"]
        killed = desk.run_scan(desk.fixture_rows()[:1], path=path, env={}, kill_switch=True)
        assert killed[0]["reason"] == "kill_switch"
        assert desk.intel_tags(logged)[0]["username"] == "steady-pi"
    finally:
        for p in (path, path + ".write.lock"):
            if os.path.exists(p):
                os.remove(p)


def test_rate_gate_and_version():
    clock = {"t": 0.0}
    slept = []

    def now():
        return clock["t"]

    def sleep(d):
        slept.append(d)
        clock["t"] += d

    c = _client(transport=lambda m, u, h: (200, {}), rate=ec.RateGate(limit=2, window=60, now=now, sleep=sleep))
    c.get("/api/v1/me")
    c.get("/api/v1/me")
    c.get("/api/v1/me")
    assert slept and slept[0] > 0
    assert vc.VALEDIA_VERSION == "0.74.1"


def main():
    test_headers_and_missing_keys()
    test_rankings_and_smart_search()
    test_writes_refuse_without_socket()
    test_decisions_jsonl_and_caps()
    test_rate_gate_and_version()
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
