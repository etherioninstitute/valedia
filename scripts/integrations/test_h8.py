#!/usr/bin/env python3
"""H8 — fortress intel fallback, fail-closed. No CDP, no venues, no Kelly.

No network, no keys, no --live. Engine PARKED unless tests inject.
Run: python3 test_h8.py
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)

import intel_fetch_fallback as iff
import valedia_config as vc


def _restore():
    os.environ.pop("FORTRESS_INTEL_FALLBACK", None)
    os.environ.pop("SPOT_RECONCILE_HTTP", None)
    os.environ.pop("VALEDIA_SELF_ADJUST", None)
    os.environ.pop("MIROFISH_AUTO_RAISE", None)
    vc.FORTRESS_INTEL_FALLBACK = False
    vc.SPOT_RECONCILE_HTTP = False
    vc.MIROFISH_AUTO_RAISE = False
    vc.VALEDIA_SELF_ADJUST = False
    vc._STAMP_CACHE = None


def test_hygiene_and_version():
    _restore()
    src = open(os.path.join(HERE, "intel_fetch_fallback.py")).read()
    for banned in (
        "solve_captcha", "TILION_PROXY", "crawl_site", "2captcha",
        "playwright", "puppeteer", "residential", "import requests",
        "create_order", "place_market_order", "VENUE_WRITER[",
        "NAUTILUS_LIVE =", "LIVE_CLIENT_HOOK", "kelly_frac",
        "MIROFISH_AUTO_RAISE =", "VALEDIA_SELF_ADJUST =",
    ):
        assert banned not in src, banned
    seed = open(os.path.join(HERE, "mirofish_seed_accurate.py")).read()
    assert "intel_fetch_fallback" in seed
    cron = open(os.path.join(ROOT, "scripts", "install_cron.sh")).read()
    assert "intel_fetch_fallback" not in cron
    assert "fortress" not in cron.lower()
    assert vc.FORTRESS_INTEL_FALLBACK is False
    assert vc.fortress_intel_fallback_enabled() is False
    assert iff.engine_available() is False
    assert vc.SPOT_RECONCILE_HTTP is False
    assert vc.MIROFISH_AUTO_RAISE is False
    assert vc.VALEDIA_SELF_ADJUST is False
    assert vc.NAUTILUS_LIVE is False
    assert vc.VENUE_WRITER["polymarket"] == "none"
    assert vc.VALEDIA_VERSION == "0.42.0", vc.VALEDIA_VERSION
    assert vc.version_stamp().startswith("v0.42.0 ")


def test_classify():
    _restore()
    ok = iff.classify_fetch(200, '{"v":1}')
    assert ok["status"] == "ok" and ok["reason"] == "ok"
    blocked = iff.classify_fetch(403, "nope")
    assert blocked["status"] == "blocked"
    rate = iff.classify_fetch(429, "slow")
    assert rate["status"] == "blocked" and rate["reason"] == "rate_limited"
    empty = iff.classify_fetch(200, "  ")
    assert empty["status"] == "empty"
    waf = iff.classify_fetch(403, "<title>Just a moment...</title>",
                             {"cf-ray": "abc"})
    assert waf["status"] == "waf"
    waf200 = iff.classify_fetch(200, "Checking your browser before you continue")
    assert waf200["status"] == "waf"
    err = iff.classify_fetch(500, "boom")
    assert err["status"] == "error"  # not failure-gated


def test_flag_off_and_parked():
    _restore()
    md = tempfile.mkdtemp(prefix="h8_")
    called = []

    def engine(url):
        called.append(url)
        return ["should not run"]

    url = "https://www.coindesk.com/arc/outboundfeeds/rss/"
    r = iff.run_fallback(url, iff.classify_fetch(403, "denied"),
                         engine_fn=engine, models_dir=md)
    assert r["fallback_ran"] is False
    assert r["reason"] == "flag_off"
    assert r["engine"] == "parked"
    assert r["kelly"] is False
    assert called == []
    os.environ["FORTRESS_INTEL_FALLBACK"] = "1"
    r2 = iff.run_fallback(url, iff.classify_fetch(403, "denied"),
                          engine_fn=None, models_dir=md)
    assert r2["reason"] == "engine_parked"
    assert r2["fallback_ran"] is False
    assert called == []


def test_refuse_venue_and_inject_only_after_gate():
    _restore()
    md = tempfile.mkdtemp(prefix="h8v_")
    os.environ["FORTRESS_INTEL_FALLBACK"] = "1"
    called = []

    def engine(url):
        called.append(url)
        return ["Oscar nominee headlines " * 20, "second"]

    clob = iff.run_fallback(
        "https://clob.polymarket.com/markets",
        iff.classify_fetch(403, "denied"),
        engine_fn=engine, models_dir=md)
    assert clob["reason"] == "venue_host_refused"
    assert called == []

    kalshi = iff.run_fallback(
        "https://api.elections.kalshi.com/trade-api/v2/markets",
        iff.classify_fetch(429, "slow"),
        engine_fn=engine, models_dir=md)
    assert kalshi["reason"] == "venue_host_refused"

    # 500 is not failure-gated
    r500 = iff.run_fallback(
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        iff.classify_fetch(500, "x"),
        engine_fn=engine, models_dir=md)
    assert r500["reason"] == "not_failure_gated"
    assert called == []

    ok = iff.run_fallback(
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        iff.classify_fetch(403, "denied"),
        engine_fn=engine, models_dir=md)
    assert ok["fallback_ran"] is True
    assert ok["engine"] == "injected"
    assert ok["headlines"]
    assert all(len(h) <= iff.MAX_CHARS for h in ok["headlines"])
    assert called == ["https://www.coindesk.com/arc/outboundfeeds/rss/"]

    def boom(_url):
        raise RuntimeError("cdp down")

    fail = iff.run_fallback(
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        iff.classify_fetch(403, "denied"),
        engine_fn=boom, models_dir=md)
    assert fail["reason"] == "engine_fail_closed"
    assert fail["fallback_ran"] is False
    loaded = iff.load(md)
    assert loaded.get("kept_last_good") is True
    assert loaded.get("last_good_headlines")


def test_cap_and_observe_ok_no_write():
    _restore()
    items = [f"headline {i} " + ("word " * 80) for i in range(50)]
    capped = iff.cap_headlines(items)
    assert len(capped) <= iff.MAX_HEADLINES
    assert sum((len(h) + 3) // 4 for h in capped) <= iff.MAX_TOKENS
    md = tempfile.mkdtemp(prefix="h8o_")
    out = iff.observe("https://api.coingecko.com/api/v3/ping", 200, '{"ok":1}',
                      models_dir=md, write=True)
    assert out["status"] == "ok"
    assert iff.load(md) == {}


if __name__ == "__main__":
    test_hygiene_and_version()
    test_classify()
    test_flag_off_and_parked()
    test_refuse_venue_and_inject_only_after_gate()
    test_cap_and_observe_ok_no_write()
    print("H8 fortress intel fallback: PASS")
