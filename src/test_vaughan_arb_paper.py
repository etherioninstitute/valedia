#!/usr/bin/env python3
"""VAUGHAN-SUBSUME-1 — Poly↔Kalshi paper/shadow. No network. No orders.

Run: python3 test_vaughan_arb_paper.py
"""
import json
import os
import sys
import tempfile
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Unit tests assert git-default OFF without Burton's ~/.openclaw/.env flips.
os.environ["OPENCLAW_SKIP_ENV"] = "1"

import forward_test_ledger as ftl
import vaughan_arb_paper as va
import valedia_config as vc

PAPER = {"TRADING_MODE": "paper", "LIVE_VENUES": "", "EXECUTION_DRY_RUN": "true"}


def _src():
    return open(os.path.join(HERE, "vaughan_arb_paper.py")).read()


def _pass(payload):
    text = payload["messages"][0]["content"]
    blob = json.loads(text[text.rfind("\n") + 1:])
    ev = {c["id"]: {"pass": True, "value": c["actual"]} for c in blob["conditions"]}
    return {
        "pass": True, "bias": "neutral", "failed_conditions": [],
        "evidence": ev, "model": "ornith-1.5-k-m",
    }


def _refuse(payload):
    text = payload["messages"][0]["content"]
    blob = json.loads(text[text.rfind("\n") + 1:])
    ev = {c["id"]: {"pass": False, "value": c["actual"]} for c in blob["conditions"]}
    return {
        "pass": False, "bias": "neutral",
        "failed_conditions": [c["id"] for c in blob["conditions"]],
        "evidence": ev, "model": "ornith-1.5-k-m",
    }


def _down(_payload):
    raise urllib.error.URLError("ornith down")


def _bomb(_payload):
    raise AssertionError("ornith must not be called")


def _fat():
    return {
        "source": "allowlist", "requires_review": False, "similarity": 1.0,
        "poly_slug": "will-judy-shelton-be-confirmed-as-fed-chair",
        "kalshi_ticker": "KXFEDCHAIRCONFIRM-JSHE",
        "raw_poly": 0.30, "raw_kal": 0.40, "size_usd": 25.0,
    }


def _scan(post_fn, limits=None, state_kill=False, realized=0.0):
    poly, kalshi = va.fixture_books()
    path = tempfile.mktemp(suffix=".jsonl")
    try:
        rows = va.run_scan(
            poly, kalshi, path=path, post_fn=post_fn, env=dict(PAPER),
            equity=1000.0, limits=limits, kill_switch=state_kill,
        )
        logged = va.read_decisions(path)
        return rows, logged
    finally:
        for p in (path, path + ".write.lock"):
            if os.path.exists(p):
                os.remove(p)


def test_allowlist_pass_would_have():
    rows, logged = _scan(_pass)
    hit = [r for r in rows if r["ticker"].endswith("KXFEDCHAIRCONFIRM-JSHE")]
    assert len(hit) == 1, hit
    row = hit[0]
    assert row["pass"] is True and row["filled"] is True
    assert row["reason"] == "pass"
    assert row["create_order"] is False and row["live"] is False
    assert row["counts_toward_kelly"] is False
    assert row["would_have_pnl"] > 0
    assert row["edge_pct"] > va.MIN_EDGE_PCT
    assert row["entry_fee"] > 0  # Kalshi taker fee is on the quote
    assert row["fee_table"] == va.FEE_TABLE
    assert row["source"] == "vaughan_arb_paper"
    assert logged[0]["create_order"] is False
    assert ftl._is_non_real_source(row["source"]) is True


def test_review_only_never_fills():
    rows, _logged = _scan(_pass)
    rev = [r for r in rows if r["requires_review"]]
    assert rev, rows
    for r in rev:
        assert r["filled"] is False and r["pass"] is False
        assert r["reason"] == "review_only"
        assert r["create_order"] is False
        assert r["would_have_pnl"] is None
    slugs = [r["ticker"] for r in rows]
    assert not any(s.startswith("will-bob-ng") for s in slugs)
    heur = {
        "source": "heuristic", "requires_review": True,
        "poly_slug": "will-alice-chen-win-the-2028-nomination",
        "kalshi_ticker": "KXNOTONLIST-AC",
        "raw_poly": 0.20, "raw_kal": 0.32, "size_usd": 25.0,
    }
    row = va.decide(heur, va.fresh_state(1000), env=dict(PAPER), post_fn=_bomb)
    assert row["reason"] == "review_only" and row["filled"] is False


def test_kill_switch_and_daily_loss():
    st = va.fresh_state(1000)
    st["kill_switch"] = True
    row = va.decide(_fat(), st, env=dict(PAPER), post_fn=_bomb)
    assert row["reason"] == "kill_switch" and row["filled"] is False
    st = va.fresh_state(1000)
    st["realized_pnl_today_usd"] = -100.0  # 10% >= 5% cap
    row = va.decide(_fat(), st, env=dict(PAPER), post_fn=_bomb)
    assert row["reason"] == "daily_loss" and row["filled"] is False and row["create_order"] is False


def test_ornith_fail_closed():
    st = va.fresh_state(1000)
    refused = va.decide(_fat(), st, env=dict(PAPER), post_fn=_refuse)
    assert refused["reason"] == "ornith_refuse" and refused["filled"] is False
    down = va.decide(_fat(), st, env=dict(PAPER), post_fn=_down)
    assert down["reason"] == "ornith_down" and down["model"] == "down" and down["filled"] is False
    bad = va.decide(_fat(), st, env=dict(PAPER), post_fn=lambda _p: "nope")
    assert bad["reason"] == "malformed" and bad["filled"] is False
    qwen = va.decide(_fat(), st, env=dict(PAPER), post_fn=lambda _p: {
        "pass": True, "failed_conditions": [], "evidence": {},
        "model": "qwen3",
    })
    assert qwen["filled"] is False and qwen["reason"] in ("model_rejected", "malformed", "missing_condition")


def test_fee_edge_below_min():
    cand = dict(_fat())
    cand["raw_poly"], cand["raw_kal"] = 0.49, 0.49
    row = va.decide(cand, va.fresh_state(1000), env=dict(PAPER), post_fn=_bomb)
    assert row["reason"] == "fee_edge"
    assert row["filled"] is False and row["pass"] is False
    assert row["would_have_pnl"] is None
    assert row["edge_pct"] < va.MIN_EDGE_PCT


def test_no_create_order_and_no_live_flip():
    src = _src()
    for banned in ("create_order(", "cancel_order(", "import requests", "from ccxt",
                   "POLYMARKET_US_LIVE = True", "WEATHER_LIVE = True",
                   "add_argument(\"--live\")", "forward_test_ledger"):
        assert banned not in src, banned
    assert vc.POLYMARKET_US_LIVE is False
    assert vc.WEATHER_LIVE is False
    assert "OIL_BRACKET" in vc.LIVE_EXEC_FENCED
    assert "WEATHER_ARB" in vc.LIVE_EXEC_FENCED
    env = dict(PAPER)
    env["POLYMARKET_US_LIVE"] = "1"
    row = va.decide(_fat(), va.fresh_state(1000), env=env, post_fn=_bomb)
    assert row["reason"] == "poly_live" and row["filled"] is False
    assert vc.POLYMARKET_US_LIVE is False
    if vc.polymarket_us_live_enabled():
        assert va.triple_gate() == "poly_live"
    half = dict(PAPER)
    half["LIVE_VENUES"] = "polymarket"
    assert va.decide(_fat(), va.fresh_state(1000), env=half, post_fn=_bomb)["reason"] == "half_live"
    live = dict(PAPER)
    live["TRADING_MODE"] = "live"
    live["LIVE_VENUES"] = ""
    assert va.decide(_fat(), va.fresh_state(1000), env=live, post_fn=_bomb)["reason"] == "not_paper"
    dry = dict(PAPER)
    dry["EXECUTION_DRY_RUN"] = "false"
    assert va.decide(_fat(), va.fresh_state(1000), env=dry, post_fn=_bomb)["reason"] == "dry_run_off"


def test_fenced_oil_series_and_parsers():
    cand = dict(_fat())
    cand["kalshi_ticker"] = "KXWTI-26SEP"
    row = va.decide(cand, va.fresh_state(1000), env=dict(PAPER), post_fn=_bomb)
    assert row["reason"] == "fenced_series" and row["filled"] is False
    assert va.poly_from_gamma({"slug": "s", "question": "q", "bestAsk": 0.4}) is None
    k = va.kalshi_from_api({
        "ticker": "KXFEDCHAIRCONFIRM-JSHE", "event_ticker": "KXFEDCHAIRCONFIRM",
        "title": "Judy Shelton", "yes_ask": 62, "no_ask": 40, "volume_24h": 10,
    })
    assert k["yes_ask"] == 0.62 and k["no_ask"] == 0.40
    assert va.MODE == "PAPER/SHADOW"
    assert vc.VALEDIA_VERSION == "0.75.0"


def test_retention_tail():
    path = tempfile.mktemp(suffix=".jsonl")
    try:
        for i in range(va.DECISION_KEEP + 5):
            va.append_decision({"n": i, "reason": "keep", "fee_table": va.FEE_TABLE}, path)
        rows = va.read_decisions(path)
        assert len(rows) == va.DECISION_KEEP
        assert rows[-1]["n"] == va.DECISION_KEEP + 4
        assert all(r["create_order"] is False and r["source"] == "vaughan_arb_paper" for r in rows)
    finally:
        for p in (path, path + ".write.lock"):
            if os.path.exists(p):
                os.remove(p)


def main():
    test_allowlist_pass_would_have()
    test_review_only_never_fills()
    test_kill_switch_and_daily_loss()
    test_ornith_fail_closed()
    test_fee_edge_below_min()
    test_no_create_order_and_no_live_flip()
    test_fenced_oil_series_and_parsers()
    test_retention_tail()
    print("test_vaughan_arb_paper PASS")


if __name__ == "__main__":
    main()
