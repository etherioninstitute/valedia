#!/usr/bin/env python3
"""informed_facts FOMO-radar source + sports MiroFish evidence. Offline, temp files only.

FOMO: fomo_radar_enrich over a fixture file → four boolean facts for crypto; missing
file / unknown symbol / non-crypto → all False; a dated stale file is ignored.
Sports: sports_evidence puts MiroFish team/series facts (load_for_fv freshness) on
the evidence record and the Nimble verdict; stale MiroFish is ignored; size and the
INFORMED gate are unchanged. No network (socket connect raises), no keys, no --live.
Run: python3 test_informed_facts_fomo.py
"""
import json
import os
import shutil
import socket
import sys
import tempfile
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["OPENCLAW_SKIP_ENV"] = "1"


def _no_net(*a, **k):
    raise AssertionError("network call from test_informed_facts_fomo")


socket.socket.connect = _no_net

import fomo_radar_enrich as fre  # noqa: E402
import informed_before_create as ibc  # noqa: E402
import informed_facts as inf  # noqa: E402
import sports_engine as se  # noqa: E402
import sports_evidence as sev  # noqa: E402
import test_nimble_wiring_prediction as tnw  # noqa: E402
import test_sports_evidence as tse  # noqa: E402

TMP = tempfile.mkdtemp(prefix="inffomo_")
NOPE = os.path.join(TMP, "missing.json")
FIXTURE = os.path.join(TMP, "fomo_fixture.json")
shutil.copy(fre.DEFAULT_FIXTURE, FIXTURE)
FOMO = ("fomo_present", "fomo_wallet_conviction_high", "fomo_exit_signal", "fomo_provenance_ok")
MF = ("mirofish_fresh", "mirofish_has_symbol", "mirofish_agrees", "mirofish_conflicts")


def _write(name, doc):
    path = os.path.join(TMP, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(doc, f)
    return path


def _facts(sym, side="buy", fomo=FIXTURE, **kw):
    return inf.informed_facts(sym, side, mirofish_paths=[NOPE], ere_paths=[NOPE],
                              prediction_log_paths=[NOPE], fomo_paths=[fomo], **kw)


# ---------------------------------------------------------------- FOMO radar

def test_fomo_from_fixture():
    f = _facts("BTC-USD")["facts"]  # 3 trusted wallets at 80 → conviction 1.92, burst, no exit
    assert (f["fomo_present"], f["fomo_provenance_ok"], f["fomo_wallet_conviction_high"],
            f["fomo_exit_signal"]) == (True, True, True, False), f
    f = _facts("SOL")["facts"]  # conviction 1.28 < 1.5, closed by 2 sellers → exit
    assert (f["fomo_present"], f["fomo_provenance_ok"], f["fomo_wallet_conviction_high"],
            f["fomo_exit_signal"]) == (True, True, False, True), f
    f = _facts("ETHUSDT")["facts"]  # seeded: provenance fails, conviction zeroed, exit dropped
    assert (f["fomo_present"], f["fomo_provenance_ok"], f["fomo_wallet_conviction_high"],
            f["fomo_exit_signal"]) == (True, False, False, False), f
    r = _facts("BTC/USD")
    assert r["facts"]["fomo_present"] and r["sources"]["fomo"]["tag"]["wallet_conviction"] == 1.92
    assert r["sources"]["fomo"]["annotate_only"] is True
    assert inf.FOMO_CONVICTION_HIGH == 1.5
    print("ok fomo fixture")


def test_fomo_absent_and_non_crypto():
    for f in (_facts("BTC", fomo=NOPE)["facts"],             # no file
              _facts("DOGE")["facts"],                       # not on the radar
              _facts("btc-above-100k", "yes")["facts"],      # poly slug: no prefix match
              _facts("KXBTCD-26OCT01-T100000", "yes")["facts"],
              _facts("BTC", asset_class="stock")["facts"]):  # non-crypto class
        assert all(f[k] is False for k in FOMO), f
    assert _facts("BTC", asset_class="crypto")["facts"]["fomo_present"] is True
    assert _facts("BTC", fomo=NOPE)["sources"]["fomo"] == {"used": False, "reason": "absent"}
    # broken file → absent, never raises
    bad = _write("bad.json", None)
    with open(bad, "w") as fh:
        fh.write("{not json")
    assert all(v is False for k, v in _facts("BTC", fomo=bad)["facts"].items() if k in FOMO)
    # a dated file older than 24h is ignored
    now = datetime(2026, 10, 1, 18, 0, tzinfo=timezone.utc)
    doc = json.load(open(FIXTURE))
    doc["as_of"] = (now - timedelta(hours=30)).isoformat()
    stale = _write("stale_fomo.json", doc)
    r = _facts("BTC", fomo=stale, now=now)
    assert not r["facts"]["fomo_present"] and r["sources"]["fomo"]["reason"] == "stale", r
    doc["as_of"] = (now - timedelta(hours=2)).isoformat()
    fresh = _write("fresh_fomo.json", doc)
    assert _facts("BTC", fomo=fresh, now=now)["facts"]["fomo_present"] is True
    # facts are booleans (Nimble state carries booleans only); fomo never joins conflict/risk
    f = _facts("SOL")["facts"]
    assert all(isinstance(v, bool) for v in f.values()), f
    assert f["any_conflict"] is False and f["any_risk_off"] is False
    print("ok fomo absent / non-crypto / stale")


def test_fomo_default_path_is_the_existing_file():
    assert inf.FOMO_PATHS == (fre.DEFAULT_FIXTURE,)
    src = open(os.path.join(HERE, "informed_facts.py")).read()
    for bad in ("import requests", "urllib", "http://", "https://", "create_order"):
        assert bad not in src, bad
    print("ok fomo default path")


# ---------------------------------------------------------------- sports MiroFish

def _mf(age_h=1.0, signals=None):
    now = datetime.now(timezone.utc)
    return {"as_of": (now - timedelta(hours=age_h)).isoformat(), "source": "mirofish_swarm",
            "overall_bias": 30.0, "macro_bias": 55.0, "events": [],
            "signals": signals if signals is not None else {
                "BOS": {"score": 70.0, "direction": "BUY", "confidence": 0.6}},
            "report_id": "report_sports_test"}


def _ev(mf_path, bet=None):
    with tse.admitted_sports_env() as env:
        opp, ev = sev.build_sports_opp(bet or tse.sports_bet(), se.get, se.SPORTS_ATTESTATION_PATH,
                                       bankroll=10000.0, fv_fn=env["fv_fn"], fmap=env["fmap"],
                                       mirofish_paths=[mf_path])
    return opp, ev


def test_sports_evidence_mirofish():
    assert sev.mirofish_keys(tse.sports_bet()) == ["BOS", "KXNBAGAME"]
    agree = _write("mf/agree/mirofish_signal.json", _mf())
    opp_a, ev = _ev(agree)
    m = ev["mirofish"]
    assert m["key"] == "BOS" and m["facts"]["mirofish_fresh"] and m["facts"]["mirofish_has_symbol"]
    assert m["facts"]["mirofish_agrees"] and not m["facts"]["mirofish_conflicts"], m
    json.dumps(ev)
    conflict = _write("mf/conflict/mirofish_signal.json",
                      _mf(signals={"BOS": {"score": 20.0, "direction": "SELL"}}))
    _, ev = _ev(conflict)
    assert ev["mirofish"]["facts"]["mirofish_conflicts"] and not ev["mirofish"]["facts"]["mirofish_agrees"]
    series = _write("mf/series/mirofish_signal.json",
                    _mf(signals={"KXNBAGAME": {"score": 75.0, "direction": "BUY"}}))
    _, ev = _ev(series)
    assert ev["mirofish"]["key"] == "KXNBAGAME" and ev["mirofish"]["facts"]["mirofish_agrees"]
    # stale MiroFish (30h) is ignored: every fact False
    stale = _write("mf/stale/mirofish_signal.json", _mf(age_h=30))
    opp_s, ev = _ev(stale)
    assert all(ev["mirofish"]["facts"][k] is False for k in MF), ev["mirofish"]
    assert ev["mirofish"]["source"]["used"] is False
    _, ev = _ev(NOPE)
    assert all(ev["mirofish"]["facts"][k] is False for k in MF)
    # facts only: the order/opportunity and the INFORMED gate do not change
    assert tnw.strip(opp_a) == tnw.strip(opp_s), (opp_a, opp_s)  # fixtures are now-relative
    assert not any(k.startswith("mirofish") for k in opp_a)
    pa = ibc.create_preflight(opp_a, 5, 80)
    ps = ibc.create_preflight(opp_s, 5, 80)
    assert pa["ok"] is ps["ok"] is True, (pa, ps)
    assert tnw.strip(pa["informed"]) == tnw.strip(ps["informed"]), (pa, ps)
    print("ok sports evidence mirofish")


def _run_sports(mf_path):
    orig = inf.MIROFISH_PATHS
    inf.MIROFISH_PATHS = (mf_path,)
    try:
        return tnw.run_sports()
    finally:
        inf.MIROFISH_PATHS = orig


def test_sports_nimble_verdict_carries_mirofish():
    agree = _write("mfn/agree/mirofish_signal.json", _mf())
    stale = _write("mfn/stale/mirofish_signal.json", _mf(age_h=30))
    with tnw.Nim(enabled=False):
        base, base_orders = _run_sports(NOPE)
    with tnw.Nim("shadow", choice="skip") as n:
        r, orders = _run_sports(agree)
    assert r["status"] == "EXECUTED" and orders == base_orders == 1, (r, orders)
    assert tnw.strip(r) == tnw.strip(dict(base, evidence=r["evidence"])), "size/order unchanged"
    v = r["nimble"]
    assert v["mirofish"]["key"] == "BOS" and v["mirofish"]["mirofish_agrees"] is True, v
    assert r["evidence"]["mirofish"]["facts"]["mirofish_fresh"] is True
    st = n.posts[0]["state"]
    assert st["symbol"] == "BOS" and st["informed"]["mirofish_agrees"] is True, st
    assert st["informed"]["mirofish_fresh"] is True and st["create_order"] is False
    with tnw.Nim("shadow", choice="skip") as n:
        r, orders = _run_sports(stale)
    assert orders == 1 and r["nimble"]["mirofish"]["mirofish_fresh"] is False, r["nimble"]
    assert n.posts[0]["state"]["informed"]["mirofish_fresh"] is False
    assert all(r["evidence"]["mirofish"]["facts"][k] is False for k in MF)
    # veto still decides only on the model answer; MiroFish facts never admit by themselves
    with tnw.Nim("veto", choice="skip"):
        r, orders = _run_sports(agree)
    assert r["status"] == "SKIP_NIMBLE" and orders == 0, r
    print("ok sports nimble verdict mirofish")


def test_dead_helper_removed():
    assert not hasattr(se, "get_mirofish_bias")
    assert "get_mirofish_bias" not in open(os.path.join(HERE, "sports_engine.py")).read()
    print("ok dead helper removed")


if __name__ == "__main__":
    try:
        test_fomo_from_fixture()
        test_fomo_absent_and_non_crypto()
        test_fomo_default_path_is_the_existing_file()
        test_sports_evidence_mirofish()
        test_sports_nimble_verdict_carries_mirofish()
        test_dead_helper_removed()
    finally:
        shutil.rmtree(TMP, ignore_errors=True)
    print("informed facts fomo + sports mirofish: PASS")
