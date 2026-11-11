#!/usr/bin/env python3
"""PHASE-3 weather RESEARCH/SHADOW. No network. No orders.

Run: python3 test_weather_shadow.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Unit tests assert git-default OFF without Burton's ~/.openclaw/.env flips.
os.environ["OPENCLAW_SKIP_ENV"] = "1"

import informed_before_create as ibc
import session_clocks as sc
import valedia_config as vc
import weather_shadow as ws

ET = (
    "Last Trading Time is 11:59 PM ET on September 21, 2026. "
    "The observation day is September 22, 2026."
)
NO_TZ = (
    "Trading closes at 10:00 AM on the observation day. "
    "Observation day is September 22, 2026."
)


def test_observe_day_and_missing_tz():
    parsed = ws.parse_clock({"title": ET, "ticker": "KXHIGHNY-26SEP22-T75"}, "KXHIGHNY-26SEP22-T75")
    assert parsed["observe_date"] == "2026-09-22"
    assert parsed["tz"] == "America/New_York"
    assert parsed["tz_source"] == "text"
    assert parsed["tz_gaps"] == []
    assert parsed["last_trading_at"].startswith("2026-09-22T03:59:00")
    bare = ws.parse_clock({"rules_primary": NO_TZ, "ticker": "KXHIGHZZ-26SEP22-T70"}, "KXHIGHZZ-26SEP22-T70")
    assert bare["observe_date"] == "2026-09-22"
    assert bare["last_trading_at"] is None
    assert "city_tz_unmapped" in bare["tz_gaps"]
    assert "ltt_tz_missing" in bare["tz_gaps"]
    city = ws.parse_clock({"rules_primary": NO_TZ, "ticker": "KXHIGHNY-26SEP22-T70"}, "KXHIGHNY-26SEP22-T70")
    assert city["tz_source"] == "city_map"
    assert city["last_trading_at"].startswith("2026-09-22T14:00:00")
    assert "ltt_tz_missing" not in city["tz_gaps"]
    title = ws.parse_clock({
        "title": "Will the high temp in NYC be >72° on Mar 26, 2026?",
        "ticker": "KXHIGHNY",
    }, "KXHIGHNY")
    assert title["observe_date"] == "2026-03-26"
    assert "ltt_missing" in title["tz_gaps"]
    assert title["city_tz"] == "America/New_York"


def test_fence_stays_and_create_refuses():
    assert vc.WEATHER_LIVE is False and vc.weather_live_enabled() is False
    assert vc.POLYMARKET_US_LIVE is False
    wx = sc.fence_gate("WEATHER_ARB", ticker="KXHIGHNY-1")
    oil = sc.fence_gate("oil", ticker="KXWTI-1")
    assert wx["mode"] == "RESEARCH/SHADOW" and wx["live_exec"] is False
    assert wx["dispatchable"] == 0 and wx["reason"] == "LIVE_EXEC_FENCED"
    assert oil["mode"] == "RESEARCH/SHADOW" and oil["dispatchable"] == 0
    assert oil["reason"] == "LIVE_EXEC_FENCED" and oil["live_exec"] is False
    market = {"title": ET, "status": "active",
              "open_time": "2026-09-21T14:00:00+00:00",
              "close_time": "2026-09-22T03:59:00+00:00"}
    sess = sc.session_clock("KXHIGHNY-26SEP22-T75", market=market, now="2026-09-21T18:00:00+00:00")
    assert sess["active"] is False and sess["reason"] == "LIVE_EXEC_FENCED"
    assert sess["dispatchable"] == 0
    assert sess["parsed"]["observe_date"] == "2026-09-22"
    res = sc.resolve_clock("weather", ticker="KXHIGHNY-26SEP22-T75", market=market)
    assert res["known"] is False
    assert res["reason"] == "observe_date_not_instant"
    assert sc.before_create({"ticker": "KXHIGHNY-26SEP22-T75", "type": "MODEL"})[0] == "SKIP_LIVE_FENCE"
    assert sc.before_create({"ticker": "KXWTI-1", "type": "NO_HARVEST"})[0] == "SKIP_LIVE_FENCE"
    reasons = ibc.kalshi_reasons(
        "KXHIGHNY-26SEP22-T75", "yes", 1, 40, informed={"strategy": "MODEL"})
    assert "live_fence" in reasons
    src = open(os.path.join(HERE, "weather_shadow.py")).read()
    assert "create_order(" not in src
    assert "kalshi_auth" not in src
    snap = {r["name"]: r for r in vc.controls_snapshot()["items"]}
    row = snap["WEATHER_SHADOW"]
    assert row["source"] == "const"
    assert "RESEARCH/SHADOW" in row["value"] and "LIVE_EXEC_FENCED" in row["value"]
    assert "dispatchable=0" in row["value"] and "live_exec=false" in row["value"]
    assert "live-eligible" not in row["value"].lower()
    oil_row = snap["OIL_SHADOW"]
    assert oil_row["source"] == "const"
    assert "RESEARCH/SHADOW" in oil_row["value"] and "dispatchable=0" in oil_row["value"]
    assert "live_exec=false" in oil_row["value"]
    assert snap["WEATHER_LIVE"]["value"] is False


def test_shadow_score_fee_true_and_not_create_fuel():
    fmap = {"KXHIGHNY": {
        "series_ticker": "KXHIGHNY", "status": "ok",
        "maker_multiplier": 0.0, "taker_multiplier": 1.0, "maker_viable": True,
    }}
    official = ws.score({
        "ticker": "KXHIGHNY-26SEP22-T72",
        "title": "high temp >72°",
        "fv_source": "nws",
        "forecast_temp": 80,
        "price": 0.40,
        "side": "yes",
        "fixture": True,
        "settlement": "yes",
        "rules_primary": ET,
    }, fmap=fmap, bankroll=100.0)
    assert official["tag"] == "official"
    assert official["dispatchable"] == 0 and official["writer"] is None
    assert official["would_have_size"] == 20
    assert official["fee"] == 0.0
    assert abs(official["fee_true_ev"] - 11.8) < 0.05
    assert official["would_have_pnl"] == 12.0
    lost = ws.score({
        "ticker": "KXHIGHNY-26SEP22-T72", "title": "high temp >72°",
        "fv_source": "nws", "forecast_temp": 80, "price": 0.40,
        "settlement": "no", "fixture": True, "rules_primary": ET,
    }, fmap=fmap, bankroll=100.0)
    assert lost["would_have_pnl"] == -8.0
    default = ws.score({
        "ticker": "KXHIGHNY-26SEP22-T72", "fv": 0.5, "fv_source": "default",
        "price": 0.40, "dispatchable": 1,
    }, fmap=fmap)
    assert default["tag"] == "refuse_default_50"
    assert default["would_have_size"] == 0 and default["dispatchable"] == 0
    assert default["fee_true_ev"] is None
    season = ws.score({
        "ticker": "KXHIGHNY-26SEP22-T72", "mirofish_season": "warm",
        "fv": 0.8, "price": 0.40,
    })
    assert season["tag"] == "season_only"
    assert season["fv"] is None and season["would_have_size"] == 0
    assert season["dispatchable"] == 0
    missing = ws.score({"ticker": "KXTEMP", "title": "temp"})
    assert missing["tag"] == "no_fv" and missing["would_have_size"] == 0
    assert "city_absent" in missing["parsed"]["tz_gaps"]
    neutral = ws.score({
        "ticker": "KXHIGHNY-26SEP22-T80", "title": "high temp >80°",
        "fv_source": "nws", "forecast_temp": 80, "price": 0.40, "fixture": True,
    }, fmap=fmap)
    assert neutral["tag"] == "near_neutral" and neutral["would_have_size"] == 0
    assert neutral["fv"] is None and neutral["dispatchable"] == 0
    assert official["ftl_shaped"]["source"] == "shadow"
    assert official["ftl_shaped"]["reconcile"] is False
    assert official["ftl_shaped"]["live_fill"] is False
    assert official["create_order"] is False


def test_nws_observe_day_not_march_cache():
    march = {
        "ticker": "KXHIGHNY",
        "title": "Will the high temp in NYC be >72° on Mar 26, 2026?",
        "fv_source": "nws",
        "forecast_temp": 80,
        "price": 0.40,
        "nws": {"periods": [{
            "temperature": 70, "temperatureUnit": "F", "isDaytime": True,
            "startTime": "2026-09-23T08:00:00-04:00",
        }]},
    }
    stale = ws.score(march)
    assert stale["tag"] == "stale_observe"
    assert stale["fv"] is None and stale["would_have_size"] == 0
    assert stale["dispatchable"] == 0
    fresh = ws.score({
        "ticker": "KXHIGHNY-26SEP23-T72",
        "title": "high temp >72°",
        "rules_primary": "The observation day is September 23, 2026.",
        "fv_source": "nws",
        "forecast_temp": 40,
        "price": 0.40,
        "nws": {"periods": [{
            "temperature": 80, "temperatureUnit": "F", "isDaytime": True,
            "startTime": "2026-09-23T08:00:00-04:00",
        }]},
        "fixture": True,
    }, fmap={"KXHIGHNY": {
        "status": "ok", "maker_viable": True, "maker_multiplier": 0.0,
        "taker_multiplier": 1.0,
    }})
    assert fresh["tag"] == "official" and fresh["fv"] is not None
    assert fresh["would_have_size"] == 20
    assert fresh["dispatchable"] == 0 and fresh["create_order"] is False


def test_universe_ledger_is_not_a_writer(tmp=None):
    import tempfile
    fee = {"KXTEMP": {"status": "ok", "maker_viable": True, "notes": "fee_type=quadratic"},
           "KXBTCY": {"status": "ok", "maker_viable": True}}
    cache = {"ts": "2026-03-26T00:00:00+00:00", "series": {
        "KXHIGHNY": {"code": "NY", "title": "Will the high temp in NYC be >72° on Mar 26, 2026?"},
    }}
    fd, path = tempfile.mkstemp(prefix="wx_shadow_", suffix=".json")
    os.close(fd)
    try:
        doc = ws.write_ledger(path, fmap=fee, city_cache=cache, fixtures=[], bankroll=100)
        assert doc["dispatchable"] == 0 and doc["create_order"] is False
        assert doc["mode"] == "RESEARCH/SHADOW"
        tickers = {r["ticker"] for r in doc["universe"]}
        assert tickers == {"KXTEMP", "KXHIGHNY"}
        assert all(r["dispatchable"] == 0 and r["would_have_size"] == 0 for r in doc["universe"])
        assert all(r["book"] == "absent" for r in doc["universe"])
        saved = open(path).read()
        assert '"dispatchable": 0' in saved
        assert "create_order" in saved  # the false flag, not a call
    finally:
        os.remove(path)


if __name__ == "__main__":
    test_observe_day_and_missing_tz()
    test_fence_stays_and_create_refuses()
    test_shadow_score_fee_true_and_not_create_fuel()
    test_nws_observe_day_not_march_cache()
    test_universe_ledger_is_not_a_writer()
    print("WEATHER-SHADOW: PASS")
