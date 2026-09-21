#!/usr/bin/env python3
"""KALSHI-FEE-MAP — formula vs PDF Jul 7 2026, map join, gate, refresh. No network.

Run: python3 test_kalshi_fee_map.py
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import kalshi_auth as ka
import kalshi_fee_map as kfm
import valedia_config as vc


def test_pdf_taker_table():
    # General Trading Fees Table, 100 contracts, M=1 (PDF Jul 7 2026).
    assert kfm.fee(0.50, 100, 1, "taker") == 1.75
    assert kfm.fee(0.10, 100, 1, "taker") == 0.63
    assert kfm.fee(0.90, 100, 1, "taker") == 0.63
    assert kfm.fee(0.01, 100, 1, "taker") == 0.07
    assert kfm.fee(0.99, 100, 1, "taker") == 0.07
    assert kfm.fee(0.20, 100, 1, "taker") == 1.12
    assert kfm.fee(0.50, 1, 1, "taker") == 0.02
    assert kfm.fee(0.01, 1, 1, "taker") == 0.01


def test_pdf_maker_formula():
    assert kfm.fee(0.50, 100, 0, "maker") == 0.0
    assert kfm.fee(0.50, 100, 1, "maker") == 0.44  # 0.4375 ceil-cent
    assert kfm.fee(0.50, 1, 1, "maker") == 0.01    # 0.0175 ceil-cent


def test_p_exec_not_last_trade():
    assert kfm.p_exec(0.40, 0.46, "buy", slip=0.01) == 0.47
    assert kfm.p_exec(0.40, 0.46, "sell", slip=0.01) == 0.39
    assert kfm.fee(kfm.p_exec(0.49, 0.51, "buy"), 100, 1) == kfm.fee(0.51, 100, 1)


def test_pdf_join_and_api_overlay():
    m = kfm.build_map()
    cpi = m["KXCPI"]
    assert cpi["maker_multiplier"] == 1.0 and cpi["taker_multiplier"] == 1.0
    assert cpi["maker_viable"] is False and cpi["taker_taxed"] is False
    assert cpi["status"] == "ok" and cpi["as_of"] == "2026-07-07"
    btc = m["KXBTCY"]
    assert btc["maker_viable"] is True and btc["taker_taxed"] is False
    # API quadratic_with_maker_fees + multiplier 2 overlays PDF.
    fc = {"series_fee_change_arr": [{
        "series_ticker": "KXCPI",
        "fee_type": "quadratic_with_maker_fees",
        "fee_multiplier": 2,
        "scheduled_ts": "2026-07-07T00:00:00Z",
    }, {
        "series_ticker": "KXNEW",
        "fee_type": "quadratic",
        "fee_multiplier": 3,
        "scheduled_ts": "2026-07-07T00:00:00Z",
    }, {
        "series_ticker": "KXFUTURE",
        "fee_type": "quadratic",
        "fee_multiplier": 9,
        "scheduled_ts": "2099-01-01T00:00:00Z",
    }]}
    m2 = kfm.build_map(fc)
    assert m2["KXCPI"]["maker_multiplier"] == 2.0
    assert m2["KXCPI"]["taker_multiplier"] == 2.0
    assert m2["KXCPI"]["taker_taxed"] is True
    assert m2["KXCPI"]["source"] == "api"
    assert m2["KXNEW"]["taker_multiplier"] == 3.0 and m2["KXNEW"]["maker_viable"] is True
    assert "KXFUTURE" not in m2  # not yet effective


def test_consult_unknown_fail_closed():
    row = kfm.consult("KXNEVERHEARD-99", fmap={})
    assert row["status"] == "unknown"
    assert row["maker_viable"] is False and row["taker_taxed"] is True


def test_client_paths_mocked():
    hits = []
    orig = ka.get
    ka.get = lambda ep: hits.append(ep) or {"ok": True}
    try:
        ka.get_series_fee_changes("KXCPI", show_historical=True)
        ka.get_event_fee_changes("CPI-26", limit=50)
        ka.get_series("KXCPI")
    finally:
        ka.get = orig
    assert hits[0] == "/series/fee_changes?series_ticker=KXCPI&show_historical=true"
    assert hits[1] == "/events/fee_changes?limit=50&event_ticker=CPI-26"
    assert hits[2] == "/series/KXCPI"


def test_gate_default_off_annotates():
    assert vc.KALSHI_FEE_MAP_GATE is False
    assert vc.kalshi_fee_map_gate_enabled() is False
    fmap = kfm.build_map({"series_fee_change_arr": [{
        "series_ticker": "KXTAX", "fee_type": "quadratic",
        "fee_multiplier": 5, "scheduled_ts": "2026-07-07T00:00:00Z",
    }]})
    ok, info = kfm.allow("KXTAX-1", fmap=fmap)
    assert ok is True
    assert info["taker_taxed"] is True


def test_gate_on_drops_unknown_and_taxed():
    prev = os.environ.get("KALSHI_FEE_MAP_GATE")
    os.environ["KALSHI_FEE_MAP_GATE"] = "1"
    try:
        assert vc.kalshi_fee_map_gate_enabled() is True
        fmap = {"KXBTCY": kfm.build_map()["KXBTCY"]}
        ok_u, u = kfm.allow("KXNOPE", fmap=fmap)
        assert ok_u is False and u["status"] == "unknown"
        fmap2 = kfm.build_map({"series_fee_change_arr": [{
            "series_ticker": "KXTAX", "fee_type": "quadratic",
            "fee_multiplier": 5, "scheduled_ts": "2026-07-07T00:00:00Z",
        }]})
        ok_t, t = kfm.allow("KXTAX", fmap=fmap2)
        assert ok_t is False and t["taker_taxed"] is True
        ok_ok, r = kfm.allow("KXBTCY", fmap=fmap)
        assert ok_ok is True and r["maker_viable"] is True
    finally:
        if prev is None:
            os.environ.pop("KALSHI_FEE_MAP_GATE", None)
        else:
            os.environ["KALSHI_FEE_MAP_GATE"] = prev


def test_allocator_and_score_consult():
    import master_allocator as ma
    src_a = open(os.path.join(HERE, "master_allocator.py")).read()
    src_e = open(os.path.join(HERE, "kalshi_executor.py")).read()
    src_s = open(os.path.join(HERE, "sports_engine.py")).read()
    assert "kalshi_fee_map" in src_a and "kalshi_fee_map" in src_e
    assert "kalshi_fee_map" in src_s
    assert "KALSHI_FEE_MAP_GATE" in open(os.path.join(HERE, "valedia_config.py")).read()
    # allocate skip when gate on + taxed
    prev = os.environ.get("KALSHI_FEE_MAP_GATE")
    os.environ["KALSHI_FEE_MAP_GATE"] = "1"
    kfm.reset_map()
    kfm._MAP = kfm.build_map({"series_fee_change_arr": [{
        "series_ticker": "KXTAX", "fee_type": "quadratic",
        "fee_multiplier": 5, "scheduled_ts": "2026-07-07T00:00:00Z",
    }]})
    try:
        opps = [{
            "platform": "kalshi", "type": "NO_HARVEST", "ticker": "KXTAX-1",
            "title": "taxed", "side": "no", "price": 0.10,
            "ev_per_dollar": 0.5, "ev_per_day": 0.5, "composite_score": 9,
            "hours_to_resolve": 1, "confidence": 0.8, "max_loss_pct": 100,
            "raw": {"series": "KXTAX"},
        }]
        allocs, _ = ma.allocate(opps, {"deployable": 20.0})
        assert allocs == []
    finally:
        kfm.reset_map()
        if prev is None:
            os.environ.pop("KALSHI_FEE_MAP_GATE", None)
        else:
            os.environ["KALSHI_FEE_MAP_GATE"] = prev


def test_hygiene():
    assert vc.VALEDIA_VERSION == "0.53.0", vc.VALEDIA_VERSION
    assert vc.KALSHI_FEE_MAP_GATE is False
    assert vc.WEATHER_LIVE is False
    assert vc.MIROFISH_AUTO_RAISE is False
    assert os.environ.get("POLYMARKET_US_LIVE") in (None, "0", "false", "")
    src = open(os.path.join(HERE, "kalshi_fee_map.py")).read()
    assert "create_order" not in src
    assert "VALEDIA_TRADING_HALT" not in src
    assert "KALSHI_FEE_MAP_GATE=True" not in open(os.path.join(HERE, "valedia_config.py")).read()
    cfg = open(os.path.join(HERE, "valedia_config.py")).read()
    assert "KALSHI_FEE_MAP_GATE = False" in cfg


def test_refresh_dry_persists_counts():
    kfm.reset_map()
    td = tempfile.mkdtemp()
    out = os.path.join(td, "kalshi-fee-map.json")
    hits = []
    res = kfm.run_refresh(
        dry=True, out_path=out,
        series_tickers=["KXBTCY", "KXNBAGAME", "KXNOPE"],
        get_series_fee_changes=lambda *a, **k: hits.append("fc") or {},
        get_series=lambda *a, **k: hits.append("gs") or {},
    )
    assert hits == []  # dry ignores getters
    assert res["mode"] == "dry"
    c = res["counts"]
    assert c["maker_viable"] == 1  # KXBTCY
    assert c["taker_taxed"] == 0
    assert c["unknown"] == 2  # KXNBAGAME + KXNOPE
    assert c["sports_unknown"] == 4  # none of the 4 sports in PDF
    doc = json.load(open(out))
    assert doc["as_of"]
    assert doc["mode"] == "dry"
    assert doc["kalshi_fee_map_gate_constant"] is False
    assert "KXBTCY" in doc["series"]
    assert vc.KALSHI_FEE_MAP_GATE is False


def test_refresh_get_mocked_fills_sports():
    kfm.reset_map()
    td = tempfile.mkdtemp()
    out = os.path.join(td, "kalshi-fee-map.json")
    gets = []

    def fc(_ticker=None, _hist=True):
        gets.append("fc")
        return {"series_fee_change_arr": [{
            "series_ticker": "KXTAX", "fee_type": "quadratic",
            "fee_multiplier": 5, "scheduled_ts": "2026-07-07T00:00:00Z",
        }]}

    def gs(ticker):
        gets.append(ticker)
        return {"series": {"ticker": ticker, "fee_type": "quadratic", "fee_multiplier": 1}}

    res = kfm.run_refresh(
        dry=False, out_path=out, series_tickers=["KXNBAGAME", "KXTAX", "KXBTCY"],
        get_series_fee_changes=fc, get_series=gs,
    )
    assert res["mode"] == "get"
    assert "fc" in gets and "KXNBAGAME" in gets
    assert "KXTAX" not in gets  # already in fee_changes overlay
    c = res["counts"]
    assert c["unknown"] == 0
    assert c["taker_taxed"] == 1  # KXTAX M=5
    assert c["maker_viable"] == 3  # all three quadratic maker 0 (incl. taxed KXTAX)
    doc = json.load(open(out))
    assert doc["series"]["KXNBAGAME"]["source"] == "api"
    assert doc["series"]["KXNBAGAME"]["maker_viable"] is True
    assert doc["kalshi_fee_map_gate_constant"] is False


def test_target_series_includes_sports_and_executor():
    ts = kfm.target_series()
    for s in kfm.SPORTS_SERIES:
        assert s in ts
    assert "KXCPI" in ts and "KXBTC" in ts
    assert "KXGREENLAND" in ts  # continuation line of SERIES_POLITICS


if __name__ == "__main__":
    test_pdf_taker_table()
    test_pdf_maker_formula()
    test_p_exec_not_last_trade()
    test_pdf_join_and_api_overlay()
    test_consult_unknown_fail_closed()
    test_client_paths_mocked()
    test_gate_default_off_annotates()
    test_gate_on_drops_unknown_and_taxed()
    test_allocator_and_score_consult()
    test_hygiene()
    test_refresh_dry_persists_counts()
    test_refresh_get_mocked_fills_sports()
    test_target_series_includes_sports_and_executor()
    print("KALSHI-FEE-MAP: PASS")
