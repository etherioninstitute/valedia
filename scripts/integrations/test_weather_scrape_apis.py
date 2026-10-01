#!/usr/bin/env python3
"""WEATHER-SCRAPE-APIS-1. Fixtures only. No network. No orders.

Run: python3 test_weather_scrape_apis.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Unit tests assert git-default OFF without Burton's ~/.openclaw/.env flips.
os.environ["OPENCLAW_SKIP_ENV"] = "1"

FIX = os.path.join(HERE, "fixtures", "weather")

import informed_before_create as ibc
import valedia_config as vc
import weather_lookup as wl
import weather_shadow as ws
from weather_sources import accuweather, ecmwf, nws_cli, weather_com

DAY = "2026-09-23"
RULES = "The observation day is September 23, 2026. Daily high settles on the NWS climate report."


def _read(name):
    return open(os.path.join(FIX, name)).read()


def _json(name):
    return json.loads(_read(name))


def test_nws_cli_nyc_pre():
    html = _read("nws_cli_nyc_2026-09-23.html")
    station = nws_cli.station_for(city="NY")
    assert station["wfo"] == "OKX" and station["issuedby"] == "NYC"
    assert nws_cli.station_for(city="CHI") is None
    assert nws_cli.station_for(city="Chicago") is None
    parsed = nws_cli.parse(
        html, DAY, "maximum", station,
        source_url=nws_cli.product_url("OKX", "NYC"),
        raw_fixture_ref="nws_cli_nyc_2026-09-23.html")
    sf = parsed["settlement_fv"]
    assert parsed["ok"] is True
    assert sf["value"] == 67 and sf["unit"] == "F" and sf["metric"] == "maximum"
    assert sf["observe_date"] == DAY and sf["source"] == "nws_cli"
    assert sf["station_or_geocode"] == "OKX/NYC" and sf["confidence"] == "final"
    assert "glossary=0" in sf["source_url"] and "apiKey" not in sf["source_url"]
    lo = nws_cli.parse(html, DAY, "minimum", station)
    assert lo["settlement_fv"]["value"] == 55
    rain = nws_cli.parse(html, DAY, "precip_today", station)
    assert rain["settlement_fv"]["value"] == 0 and rain["settlement_fv"]["unit"] == "in"
    wrong = nws_cli.parse(html, "2026-09-22", "maximum", station)
    assert wrong["ok"] is False and wrong["reason"] == "wrong_date"
    assert "settlement_fv" not in wrong
    prelim = nws_cli.parse(html.replace("CLIMATE REPORT", "CLIMATE REPORT\nPRELIMINARY", 1), DAY, "maximum", station)
    assert prelim["ok"] is False and prelim["reason"] == "preliminary"
    assert "value" not in prelim


def test_weather_com_aligned_and_not_settlement():
    daily = _json("weather_com_daily_nyc.json")
    parsed = weather_com.parse(daily, DAY, geocode="40.713,-74.006", raw_fixture_ref="weather_com_daily_nyc.json")
    assert parsed["settlement_fv"] is None
    tag = parsed["research_tag"]
    assert tag["forecast_or_observation"] == "forecast"
    assert tag["value_summary"] == "max 68 F min 56 F"
    assert tag["valid_time"].startswith(DAY)
    assert "{redacted}" in parsed["source_url"] and "apiKey=" in weather_com.redact("https://x?apiKey=SECRET")
    assert "SECRET" not in weather_com.redact("https://x?apiKey=SECRET")
    shifted = dict(daily)
    shifted["calendarDayTemperatureMax"] = [68, 71]
    bad = weather_com.parse(shifted, DAY)
    assert bad["reason"] == "misaligned" and bad["settlement_fv"] is None
    hourly = weather_com.parse(_json("weather_com_hourly_forecast.json"), DAY)
    assert hourly["settlement_fv"] is None and hourly["reason"] == "forecast_not_final"
    assert hourly["research_tag"]["value_summary"] == "62 F"
    obs = weather_com.parse(_json("weather_com_hourly_obs.json"), DAY, geocode="40.713,-74.006")
    assert obs["settlement_fv"]["value"] == 64
    assert obs["settlement_fv"]["confidence"] == "observation"
    assert obs["settlement_fv"]["source"] == "weather_com"


def test_lookup_nyc_and_research_cannot_pass():
    html = _read("nws_cli_nyc_2026-09-23.html")
    daily = _json("weather_com_daily_nyc.json")
    acc = _read("accu_nyc_10day.html")
    got = wl.lookup(
        "kalshi", "KXHIGHNY-26SEP23-T72", observe_date=DAY, rules=RULES,
        fixtures={
            "cli_html": html, "wx_json": daily, "accu_html": acc,
            "ecmwf_products": ["2 m temperature"],
            "raw_fixture_ref": "nws_cli_nyc_2026-09-23.html",
            "wx_ref": "weather_com_daily_nyc.json",
            "accu_ref": "accu_nyc_10day.html",
        })
    assert got["ok"] is True and got["create_order"] is False and got["dispatchable"] == 0
    assert got["informed_pass"] is False
    assert got["settlement_fv"]["value"] == 67 and got["settlement_fv"]["source"] == "nws_cli"
    sources = {t["source"] for t in got["research_tags"]}
    assert sources == {"weather_com", "accuweather", "ecmwf"}
    assert all(t.get("value_summary") != 67 for t in got["research_tags"])
    ecm = [t for t in got["research_tags"] if t["source"] == "ecmwf"][0]
    assert ecm["value_summary"] is None and "2 m temperature" in ecm["products"]
    view = wl.informed_view(got)
    assert view["fv"] is None and view["informed_pass"] is False and view["dispatchable"] == 0
    reasons = ibc.kalshi_reasons("KXHIGHNY-26SEP23-T72", "yes", 1, 40, informed=view)
    assert reasons and "live_fence" in reasons
    research_only = wl.lookup(
        "kalshi", "KXHIGHNY-26SEP23-T72", observe_date=DAY, rules=RULES,
        fixtures={"wx_json": daily, "accu_html": acc, "wx_ref": "weather_com_daily_nyc.json"})
    assert research_only["settlement_fv"] is None
    assert research_only["unresolved"] == "missing_settlement_fv"
    assert research_only["research_tags"] and research_only["informed_pass"] is False
    rview = wl.informed_view(research_only)
    assert rview["fv"] is None and rview["fv_source"] == "research"
    rreasons = ibc.kalshi_reasons("KXHIGHNY-26SEP23-T72", "yes", 1, 40, informed=rview)
    assert "fv_source" in rreasons and "fv_missing" in rreasons and "live_fence" in rreasons
    assert "research" not in ibc.ALLOWED_FV


def test_shadow_uses_lookup_and_refuses_without_settlement():
    html = _read("nws_cli_nyc_2026-09-23.html")
    daily = _json("weather_com_daily_nyc.json")
    row = ws.score({
        "ticker": "KXHIGHNY-26SEP23-T72",
        "title": "high temp >72°",
        "use_lookup": True,
        "cli_html": html,
        "wx_json": daily,
        "raw_fixture_ref": "nws_cli_nyc_2026-09-23.html",
        "wx_ref": "weather_com_daily_nyc.json",
        "rules_primary": RULES,
        "price": 0.40,
        "fixture": True,
    })
    assert row["tag"] == "official" and row["settlement_fv"]["value"] == 67
    assert row["fv_source"] == "nws_cli"
    assert row["model_fv"] == round(ws.bracket_prob(67, "above", 72, None), 6)
    assert row["dispatchable"] == 0 and row["create_order"] is False
    assert row["would_have_size"] == 0
    assert row["research_tags"] and row["research_tags"][0]["source"] == "weather_com"
    sneaky = ws.score({
        "ticker": "KXHIGHNY-26SEP23-T72",
        "title": "high temp >72°",
        "use_lookup": True,
        "forecast_temp": 80,
        "fv": 0.9,
        "fv_source": "nws",
        "wx_json": daily,
        "rules_primary": RULES,
        "price": 0.40,
    })
    assert sneaky["settlement_fv"] is None and sneaky["fv"] is None
    assert sneaky["tag"] == "missing_settlement_fv"
    assert sneaky["would_have_size"] == 0 and sneaky["dispatchable"] == 0
    assert sneaky["create_order"] is False and sneaky["research_tags"]
    hourly_rules = (
        "The observation day is September 23, 2026. "
        "Hourly temperature uses The Weather Company's reported value as official."
    )
    forecast = ws.score({
        "ticker": "KXTEMP-26SEP23-H15",
        "use_lookup": True,
        "wx_json": _json("weather_com_hourly_forecast.json"),
        "rules_primary": hourly_rules,
        "geocode": "40.713,-74.006",
        "price": 0.40,
        "forecast_temp": 90,
    })
    assert forecast["tag"] == "forecast_not_final"
    assert forecast["fv"] is None and forecast["dispatchable"] == 0
    observed = ws.score({
        "ticker": "KXTEMP-26SEP23-H15",
        "title": "high temp >60°",
        "use_lookup": True,
        "wx_json": _json("weather_com_hourly_obs.json"),
        "rules_primary": hourly_rules,
        "geocode": "40.713,-74.006",
        "price": 0.20,
        "fixture": True,
    })
    assert observed["settlement_fv"]["source"] == "weather_com"
    assert observed["settlement_fv"]["value"] == 64
    assert observed["dispatchable"] == 0 and observed["create_order"] is False
    assert observed["tag"] == "official"


def test_httpx_is_preferred_and_unknown_city_refuses():
    import sys
    import types
    from weather_sources import _get
    fake = types.ModuleType("httpx")
    seen = {}

    class Resp:
        text = "ok"
        def raise_for_status(self):
            return None

    def get(url, headers=None, timeout=None, follow_redirects=None):
        seen["url"] = url
        return Resp()

    fake.get = get
    sys.modules["httpx"] = fake
    try:
        body, client = _get.get_text("https://forecast.weather.gov/product.php?site=OKX&issuedby=NYC")
    finally:
        sys.modules.pop("httpx", None)
    assert client == "httpx" and body == "ok"
    assert seen["url"].startswith("https://forecast.weather.gov/")
    html = _read("nws_cli_nyc_2026-09-23.html")
    chi = wl.lookup(
        "kalshi", "KXHIGHCHI-26SEP23-T72", observe_date=DAY, rules=RULES,
        fixtures={"cli_html": html})
    assert chi["unresolved"] == "station_unconfigured" and chi["settlement_fv"] is None
    rain = wl.lookup(
        "kalshi", "KXRAINNY-26SEP23", observe_date=DAY,
        rules="The observation day is September 23, 2026. Precipitation settles on the NWS climate report.",
        fixtures={"cli_html": html, "raw_fixture_ref": "nws_cli_nyc_2026-09-23.html"})
    assert rain["settlement_fv"]["metric"] == "precip_today"
    assert rain["settlement_fv"]["value"] == 0 and rain["dispatchable"] == 0
    scored = ws.score({
        "ticker": "KXRAINNY-26SEP23",
        "use_lookup": True,
        "cli_html": html,
        "rules_primary": "The observation day is September 23, 2026. Precipitation settles on the NWS climate report.",
        "price": 0.40,
    })
    assert scored["tag"] == "no_bracket" and scored["fv"] is None
    assert scored["would_have_size"] == 0 and scored["dispatchable"] == 0
    assert scored["settlement_fv"]["value"] == 0


def test_fence_and_clients_stay_closed():
    assert vc.WEATHER_LIVE is False and vc.weather_live_enabled() is False
    assert vc.LIVE_EXEC_FENCED == frozenset({"WEATHER_ARB", "OIL_BRACKET"})
    assert vc.LIVE_EXEC_FENCED_SERIES == frozenset({"KXWTI", "KXOIL"})
    assert ecmwf.CPC_BLOCKED["http"] == 403 and ecmwf.CPC_BLOCKED["client"] is None
    assert accuweather.parse("<html>no island</html>", DAY)["reason"] == "embedded_unstable"
    acc = accuweather.parse(_read("accu_nyc_10day.html"), DAY, raw_fixture_ref="accu_nyc_10day.html")
    assert acc["research_tag"]["value_summary"] == "high 70 F low 57 F"
    assert acc["settlement_fv"] is None
    named = wl.lookup("poly", "nyc-high", city="NY", settle_source="accuweather",
                      observe_date=DAY, rules="official source is AccuWeather")
    assert named["settlement_fv"] is None and named["unresolved"] == "research_not_settlement"
    bare = wl.lookup("poly", "some-slug", observe_date=DAY, rules="will it rain")
    assert bare["unresolved"] == "settle_source_unresolved" and bare["settlement_fv"] is None
    for name in ("weather_lookup.py", "weather_shadow.py", "weather_sources/nws_cli.py",
                 "weather_sources/weather_com.py", "weather_sources/accuweather.py",
                 "weather_sources/ecmwf.py"):
        src = open(os.path.join(HERE, name)).read()
        assert "create_order(" not in src
        assert "WEATHER_LIVE = True" not in src
        assert "apiKey=SECRET" not in src
    ecmwf_src = open(os.path.join(HERE, "weather_sources/ecmwf.py")).read()
    assert "urlopen" not in ecmwf_src and "httpx" not in ecmwf_src and "get_text" not in ecmwf_src


if __name__ == "__main__":
    test_nws_cli_nyc_pre()
    test_weather_com_aligned_and_not_settlement()
    test_lookup_nyc_and_research_cannot_pass()
    test_shadow_uses_lookup_and_refuses_without_settlement()
    test_httpx_is_preferred_and_unknown_city_refuses()
    test_fence_and_clients_stay_closed()
    print("WEATHER-SCRAPE-APIS: PASS")
