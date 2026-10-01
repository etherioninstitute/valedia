#!/usr/bin/env python3
"""WEATHER-JEV-MORNING-1. Fixtures only. No network. No orders.

Run: python3 test_weather_jev_morning.py
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Unit tests assert git-default OFF without Burton's ~/.openclaw/.env flips.
os.environ["OPENCLAW_SKIP_ENV"] = "1"

FIX = os.path.join(HERE, "fixtures", "weather")

import informed_before_create as ibc
import jev_shadow as jev
import valedia_config as vc
import weather_live
import weather_lookup as wl
import weather_shadow as ws
from weather_sources import nws_cli, weather_com

DAY = "2026-09-23"
NWS_RULES = "The observation day is September 23, 2026. Daily high settles on the NWS climate report."
TWC_RULES = "The observation day is September 23, 2026. Settles on The Weather Company CLIMIA daily high."


def _read(name):
    return open(os.path.join(FIX, name)).read()


def test_twc_daily_and_nws_still_nws():
    fam, met, why = wl.resolve_source(
        "kalshi", "KXHIGHMIA-26SEP24", "settles on The Weather Company CLIMIA daily high")
    assert fam == "weather_com" and met == "maximum" and why is None
    fam, met, why = wl.resolve_source(
        "kalshi", "KXLOWTLV-26SEP24", "The Weather Channel CLILAS daily low")
    assert fam == "weather_com" and met == "minimum" and why is None
    fam, met, why = wl.resolve_source(
        "kalshi", "KXHIGHNY-26SEP24", "weather.com / Weather Company observation")
    assert fam == "weather_com" and met == "maximum" and why is None
    fam, met, why = wl.resolve_source("kalshi", "KXHIGHMIA", "daily high", "twc")
    assert fam == "weather_com" and met == "maximum" and why is None
    fam, met, why = wl.resolve_source("kalshi", "KXHIGHNY-26SEP23-T72", NWS_RULES)
    assert fam == "nws_cli" and met == "maximum" and why is None
    fam, met, why = wl.resolve_source("kalshi", "KXHURRICANE-26SEP", "named storm")
    assert fam is None and why == "need_fv_nhc"
    fam, met, why = wl.resolve_source(
        "kalshi", "KXRAINNY-26SEP23", "The Weather Company precipitation total")
    assert why == "need_fv_precip" and fam is None


def test_daily_observation_and_cross_check():
    payload = {
        "observation": True,
        "validTimeLocal": ["2026-09-23T19:00:00-0400"],
        "calendarDayTemperatureMax": [88],
        "calendarDayTemperatureMin": [76],
    }
    hi = weather_com.parse(payload, DAY, metric="maximum", geocode="25.791,-80.316")
    assert hi["settlement_fv"]["value"] == 88
    assert hi["settlement_fv"]["metric"] == "maximum"
    assert hi["settlement_fv"]["confidence"] == "observation"
    assert hi["settlement_fv"]["source"] == "weather_com"
    lo = weather_com.parse(payload, DAY, metric="minimum")
    assert lo["settlement_fv"]["value"] == 76 and lo["settlement_fv"]["metric"] == "minimum"
    forecast = weather_com.parse(_json_daily(), DAY)
    assert forecast["settlement_fv"] is None
    assert forecast["research_tag"]["forecast_or_observation"] == "forecast"
    got = wl.lookup(
        "kalshi", "KXHIGHNY-26SEP23-T72", observe_date=DAY, rules=TWC_RULES,
        fixtures={
            "wx_json": payload,
            "cli_html": _read("nws_cli_nyc_2026-09-23.html"),
            "raw_fixture_ref": "nws_cli_nyc_2026-09-23.html",
        })
    assert got["settlement_fv"]["source"] == "weather_com"
    assert got["settlement_fv"]["value"] == 88
    assert got["informed_pass"] is False and got["create_order"] is False
    cross = [t for t in got["research_tags"] if t["source"] == "nws_cli"]
    assert cross and cross[0]["provenance"] == "cross_check"
    assert cross[0]["value_summary"].startswith("67")


def _json_daily():
    return json.loads(_read("weather_com_daily_nyc.json"))


def test_stations_tz_and_ltt_will_be():
    mia = nws_cli.station_for(city="MIA")
    las = nws_cli.station_for(city="LAS")
    tlv = nws_cli.station_for(city="TLV")
    assert mia["wfo"] == "MFL" and mia["issuedby"] == "MIA" and mia["cli_product"] == "CLIMIA"
    assert las["wfo"] == "VEF" and las["issuedby"] == "LAS"
    assert tlv["issuedby"] == "LAS" and tlv["cli_product"] == "CLILAS"
    assert nws_cli.station_for(city="CHI") is None
    assert ws.CITY_TZ["LAS"] == "America/Los_Angeles"
    assert ws.CITY_TZ["TLV"] == "America/Los_Angeles"
    assert ws.CITY_TZ["MIA"] == "America/New_York"
    text = (
        "Last Trading Time will be 11:59 PM ET on September 22, 2026. "
        "The observation day is September 22, 2026."
    )
    parsed = ws.parse_clock({"rules_primary": text, "ticker": "KXLOWTLV-26SEP22-B71.5"}, "KXLOWTLV-26SEP22-B71.5")
    assert parsed["city"] == "TLV"
    assert parsed["city_tz"] == "America/Los_Angeles"
    assert parsed["tz"] == "America/New_York"
    assert parsed["last_trading_at"].startswith("2026-09-23T03:59:00")
    assert "ltt_missing" not in parsed["tz_gaps"]


def test_lottery_and_late_on_create_only():
    tail = ibc.kalshi_reasons(
        "KXHIGHMIA-26SEP24-T80", "yes", 1, 4, informed={"strategy": "WEATHER_ARB"})
    assert "SKIP_LOTTERY" in tail and "live_fence" in tail
    late = ibc.kalshi_reasons(
        "KXHIGHMIA-26SEP24-T80", "yes", 1, 95, informed={"strategy": "WEATHER_ARB"})
    assert "SKIP_LATE_COLLAPSE" in late
    mid = ibc.kalshi_reasons(
        "KXHIGHMIA-26SEP24-T80", "yes", 1, 40, informed={"strategy": "WEATHER_ARB"})
    assert "SKIP_LOTTERY" not in mid and "SKIP_LATE_COLLAPSE" not in mid
    early = ibc.kalshi_reasons(
        "KXHIGHMIA-26SEP24-T80", "yes", 1, 95,
        informed={"strategy": "WEATHER_ARB", "late_observe": False})
    assert "SKIP_LATE_COLLAPSE" not in early
    soft = ibc.kalshi_reasons(
        "KXHIGHMIA-26SEP24-T80", "yes", 1, 95,
        informed={"strategy": "WEATHER_ARB", "late_observe": True, "early_price": 0.40})
    assert "SKIP_LATE_COLLAPSE" not in soft
    roi = ibc.kalshi_reasons(
        "KXHIGHNY-1", "yes", 1, 8,
        informed={"strategy": "CONVICTION", "roi_if_right": 25})
    assert "SKIP_LOTTERY" in roi
    oil = ibc.kalshi_reasons(
        "KXWTI-1", "yes", 1, 4, informed={"strategy": "OIL_BRACKET"})
    assert "SKIP_LOTTERY" not in oil and "live_fence" in oil
    other = ibc.kalshi_reasons(
        "KXBTCY-1", "yes", 1, 4, informed={"strategy": "CONVICTION", "roi_if_right": 40})
    assert "SKIP_LOTTERY" not in other and "SKIP_LATE_COLLAPSE" not in other


def test_jev_cannot_pass_and_morning_alert(tmp):
    assert jev.annotate_weather({"ticker": "KXHIGHNY", "create_order": False, "fv": 0.2})["fv"] == 0.2
    prev = os.environ.get("VALEDIA_JEV")
    os.environ["VALEDIA_JEV"] = "1"
    try:
        def boom(*_a, **_k):
            raise TimeoutError("slow")

        failed = jev.annotate_weather(
            {"ticker": "KXHIGHNY", "create_order": False, "fv": 0.2, "informed_pass": False},
            post=boom)
        assert failed["jev"]["fail_open"] is True and failed["jev"]["oms"] is False
        assert failed["create_order"] is False and failed["fv"] == 0.2

        def ok_post(*_a, **_k):
            return {"model": "jev-test", "answers": {
                "agree": {"choice": "agree", "confidence": 0.4},
                "confidence": {"noul": 0.4},
                "status_honesty": {"choice": "honest"},
            }}

        agreed = jev.annotate_weather(
            {"ticker": "KXHIGHNY", "create_order": False, "informed_pass": False, "fv": 0.62},
            post=ok_post)
        assert agreed["jev"]["agree"] == "agree" and agreed["jev"]["oms"] is False
        assert agreed["create_order"] is False and agreed["informed_pass"] is False
        assert agreed["fv"] == 0.62
    finally:
        if prev is None:
            os.environ.pop("VALEDIA_JEV", None)
        else:
            os.environ["VALEDIA_JEV"] = prev
    fmap = {"KXHIGHNY": {
        "series_ticker": "KXHIGHNY", "status": "ok",
        "maker_multiplier": 0.0, "taker_multiplier": 1.0, "maker_viable": True,
    }}
    rules = (
        "Last Trading Time is 11:59 PM ET on September 21, 2026. "
        "The observation day is September 22, 2026."
    )
    alert = os.path.join(tmp, "WEATHER-MORNING-ALERT.md")
    ledger = os.path.join(tmp, "shadow.json")
    good = {
        "ticker": "KXHIGHNY-26SEP22-T72",
        "title": "high temp >72°",
        "fv_source": "nws",
        "forecast_temp": 80,
        "price": 0.40,
        "side": "yes",
        "fixture": True,
        "rules_primary": rules,
    }
    body = ws.morning_scan([good], fmap=fmap, alert_path=alert, ledger_path=ledger)
    assert body["create_order"] is False and body["dispatchable"] == 0
    assert body["n_alert"] == 1
    assert body["rows"][0]["create_order"] is False
    text = open(alert).read()
    assert text.startswith("# WEATHER-MORNING-ALERT")
    assert "create_order=false" in text
    tail = dict(good)
    tail["price"] = 0.04
    quiet = os.path.join(tmp, "quiet.md")
    none = ws.morning_scan([tail], fmap=fmap, alert_path=quiet, ledger_path=False)
    assert none["n_alert"] == 0 and not os.path.exists(quiet)


def test_live_stays_off_and_oil_fenced():
    assert vc.WEATHER_LIVE is False and vc.weather_live_enabled() is False
    assert "OIL_BRACKET" in vc.LIVE_EXEC_FENCED and "WEATHER_ARB" in vc.LIVE_EXEC_FENCED
    assert "KXWTI" in vc.LIVE_EXEC_FENCED_SERIES and "KXOIL" in vc.LIVE_EXEC_FENCED_SERIES
    called = []
    off = weather_live.admit(
        {"ticker": "KXHIGHNY-26SEP24-T72", "side": "yes", "count": 1, "price_cents": 40},
        post=lambda *a, **k: called.append(1))
    assert off["create_order"] is False and off["reason"] == "WEATHER_LIVE_OFF" and called == []
    oil = weather_live.admit(
        {"ticker": "KXWTI-1", "strategy": "OIL_BRACKET", "side": "yes", "count": 1, "price_cents": 40},
        post=lambda *a, **k: called.append(1))
    assert oil["create_order"] is False and oil["reason"] == "oil_fenced" and called == []
    src = open(os.path.join(HERE, "weather_shadow.py")).read()
    assert "create_order(" not in src and "kalshi_auth" not in src
    assert "WEATHER-MORNING-ALERT" in src and "weather_live_ledger" in src
    informed = open(os.path.join(HERE, "informed_before_create.py")).read()
    assert "SKIP_LOTTERY" in informed and "SKIP_LATE_COLLAPSE" in informed
    assert "create_order(" not in open(os.path.join(HERE, "weather_arb.py")).read()


def main():
    test_twc_daily_and_nws_still_nws()
    test_daily_observation_and_cross_check()
    test_stations_tz_and_ltt_will_be()
    test_lottery_and_late_on_create_only()
    tmp = tempfile.mkdtemp(prefix="weather-jev-")
    try:
        test_jev_cannot_pass_and_morning_alert(tmp)
    finally:
        pass
    test_live_stays_off_and_oil_fenced()
    print("WEATHER-JEV-MORNING: PASS")


if __name__ == "__main__":
    main()
