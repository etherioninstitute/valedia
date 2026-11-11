#!/usr/bin/env python3
"""OIL-WEATHER-SMART-1 oil shadow. No network. No orders.

Run: python3 test_oil_shadow.py
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Unit tests assert git-default OFF without Burton's ~/.openclaw/.env flips.
os.environ["OPENCLAW_SKIP_ENV"] = "1"

import forward_test_ledger as ftl
import informed_before_create as ibc
import oil_shadow as oil
import oil_weather_shadow_tick as tick
import session_clocks as sc
import shadow_common as desk
import valedia_config as vc


FMAP = {"KXWTI": {
    "series_ticker": "KXWTI", "status": "ok",
    "maker_multiplier": 0.0, "taker_multiplier": 1.0, "maker_viable": True,
}}


def test_official_print_and_refuses():
    row = oil.score({
        "ticker": "KXWTI-26SEP23-T72",
        "title": "WTI above $72",
        "fv_source": "nymex",
        "rules_cite": "nymex_cl",
        "print_kind": "settlement",
        "print_usd": 80,
        "price": 0.40,
        "side": "yes",
        "fixture": True,
        "settlement": "yes",
        "create_order": True,
        "dispatchable": 1,
    }, fmap=FMAP, bankroll=100.0)
    assert row["tag"] == "official" and row["fv"] == 0.99
    assert row["dispatchable"] == 0 and row["live_exec"] is False
    assert row["create_order"] is False and row["writer"] is None
    assert row["would_have_size"] == 20
    assert row["fee"] == 0.0
    assert abs(row["fee_true_ev"] - 11.8) < 0.05
    assert row["would_have_pnl"] == 12.0
    assert row["mode"] == "RESEARCH/SHADOW"
    assert row["fence"] == "LIVE_EXEC_FENCED"
    assert row["enter_window"]["active"] is False
    shaped = row["ftl_shaped"]
    assert shaped["source"] == "shadow" and shaped["fill_status"] == "PAPER"
    assert shaped["reconcile"] is False and shaped["live_fill"] is False
    muse = oil.score({
        "ticker": "KXWTI-26SEP23-T72", "fv_source": "muse",
        "rules_cite": "nymex_cl", "print_usd": 80, "price": 0.40,
    }, fmap=FMAP)
    assert muse["tag"] == "unofficial" and muse["would_have_size"] == 0
    season = oil.score({
        "ticker": "KXOIL-1", "mirofish_season": "tight", "fv": 0.8,
        "print_usd": 80, "rules_cite": "eia_wti", "price": 0.40,
    })
    assert season["tag"] == "season_only" and season["fv"] is None
    bare = oil.score({
        "ticker": "KXWTI-26SEP23-T72", "fv_source": "nymex", "print_usd": 80,
        "price": 0.40,
    }, fmap=FMAP)
    assert bare["tag"] == "no_fv" and bare["would_have_size"] == 0
    neutral = oil.score({
        "ticker": "KXWTI-26SEP23-T72", "title": "WTI above $72",
        "fv_source": "cme", "rules_cite": "cme_wti", "print_kind": "forecast",
        "print_usd": 72, "price": 0.40,
    }, fmap=FMAP)
    assert neutral["tag"] == "near_neutral" and neutral["would_have_size"] == 0
    other = oil.score({"ticker": "KXBTCY-1", "fv_source": "nymex", "print_usd": 80,
                       "rules_cite": "nymex_cl", "price": 0.40})
    assert other["tag"] == "not_oil_series" and other["dispatchable"] == 0


def test_fence_refuse_and_rollup_does_not_apply():
    assert vc.WEATHER_LIVE is False and vc.weather_live_enabled() is False
    assert "OIL_BRACKET" in vc.LIVE_EXEC_FENCED and "WEATHER_ARB" in vc.LIVE_EXEC_FENCED
    assert "KXWTI" in vc.LIVE_EXEC_FENCED_SERIES and "KXOIL" in vc.LIVE_EXEC_FENCED_SERIES
    gate = sc.fence_gate("OIL_BRACKET", ticker="KXWTI-1")
    assert gate["mode"] == "RESEARCH/SHADOW" and gate["dispatchable"] == 0
    assert gate["live_exec"] is False and gate["reason"] == "LIVE_EXEC_FENCED"
    assert sc.before_create({"ticker": "KXWTI-26SEP23-T72", "type": "NO_HARVEST"})[0] == "SKIP_LIVE_FENCE"
    assert sc.before_create({"ticker": "KXOIL-1", "strategy": "OIL_BRACKET"})[0] == "SKIP_LIVE_FENCE"
    assert sc.before_create({"ticker": "KXHIGHNY-1", "type": "WEATHER_ARB"})[0] == "SKIP_LIVE_FENCE"
    reasons = ibc.kalshi_reasons(
        "KXWTI-26SEP23-T72", "yes", 1, 40,
        informed={"strategy": "NO_HARVEST", "series": "KXWTI"})
    assert "live_fence" in reasons and "oil_no_harvest" in reasons
    wx = ibc.kalshi_reasons(
        "KXHIGHNY-26SEP23-T72", "yes", 1, 40, informed={"strategy": "MODEL"})
    assert "live_fence" in wx
    snap = {r["name"]: r for r in vc.controls_snapshot()["items"]}
    for name in ("OIL_SHADOW", "WEATHER_SHADOW", "CLOCK_OIL_WEATHER"):
        val = snap[name]["value"]
        assert "RESEARCH/SHADOW" in val, name
        assert "LIVE_EXEC_FENCED" in val, name
        assert "dispatchable=0" in val and "live_exec=false" in val
        assert "live-eligible" not in val.lower()
    assert snap["WEATHER_LIVE"]["value"] is False
    roll = desk.accuracy_rollup([{
        "settlement": "yes", "would_have_size": 1, "would_have_pnl": 1.0,
    }])
    hint = desk.threshold_hint(roll)
    assert hint["apply"] is False
    assert roll["admissible_for_kelly"] is False
    assert roll["admissible_for_category_stats"] is False
    assert ftl._is_non_real_source("shadow") is True
    assert ftl._is_non_real_source("paper") is True
    src = open(os.path.join(HERE, "oil_shadow.py")).read()
    assert "create_order(" not in src
    assert "kalshi_auth" not in src
    arb = open(os.path.join(HERE, "weather_arb.py")).read()
    assert "create_order(" not in arb
    assert tick.main(["--live"]) == 2


def test_ledger_file_is_not_a_writer():
    fd, path = tempfile.mkstemp(prefix="oil_shadow_", suffix=".json")
    os.close(fd)
    try:
        doc = oil.write_ledger(
            path, fmap={"KXWTI": FMAP["KXWTI"], "KXBTCY": {"status": "ok"}},
            fixtures=[], bankroll=100, mirror_live=False)
        assert doc["dispatchable"] == 0 and doc["create_order"] is False
        assert doc["mode"] == "RESEARCH/SHADOW"
        assert doc["threshold_hint"]["apply"] is False
        tickers = {r["ticker"] for r in doc["universe"]}
        assert tickers == {"KXWTI"}
        assert all(r["dispatchable"] == 0 and r["create_order"] is False for r in doc["universe"])
        saved = open(path).read()
        assert '"dispatchable": 0' in saved
    finally:
        os.remove(path)


if __name__ == "__main__":
    test_official_print_and_refuses()
    test_fence_refuse_and_rollup_does_not_apply()
    test_ledger_file_is_not_a_writer()
    print("OIL-SHADOW: PASS")
