#!/usr/bin/env python3
"""ARB live promotion path (arb_live.py). Fakes only: no network, no keys, no flag flips.

Run: python3 test_arb_live.py
"""
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["OPENCLAW_SKIP_ENV"] = "1"
for _k in ("VALEDIA_SYSTEMONE", "VALEDIA_ARB_LIVE", "POLYMARKET_US_LIVE"):
    os.environ.pop(_k, None)
os.environ["VALEDIA_TRADING_HALT"] = "0"

import arb_live as al  # noqa: E402
import forward_test_ledger as ftl  # noqa: E402
import kalshi_fee_map as kfm  # noqa: E402
import valedia_config as vc  # noqa: E402
import vaughan_arb_paper as va  # noqa: E402
import test_vaughan_arb_paper as tva  # noqa: E402

TMP = tempfile.mkdtemp(prefix="arb_live_")
REPORT = os.path.join(TMP, "backtest_arb.json")


def _report(verdict="PASS", name="ARB_candidate", age_s=0):
    with open(REPORT, "w") as f:
        json.dump({"tool": "backtest_gate", "verdict": verdict, "candidate": {"name": name}}, f)
    t = time.time() - age_s
    os.utime(REPORT, (t, t))


def _env(**kw):
    env = {"VALEDIA_ARB_LIVE": "1", "POLYMARKET_US_LIVE": "1",
           "VALEDIA_ARB_BACKTEST_REPORT": REPORT,
           "VALEDIA_ARB_LIVE_LOG": os.path.join(TMP, "legs-%d.jsonl" % time.time_ns())}
    env.update(kw)
    return env


def _cand():
    c = dict(tva._fat(), kal_side="NO", poly_side="YES")
    ser = kfm.series_from_ticker(c["kalshi_ticker"])
    c["kalshi_informed"] = {"fv": 0.70, "fv_source": "model", "fv_confidence": True,
                            "bid": 0.39, "ask": 0.40, "slip": 0.0, "depth": 50,
                            "bankroll": 10000, "fmap": {ser: kfm._row(ser, 1.0, 1.0, "t")}}
    c["poly_informed"] = {"fv": 0.60, "fv_source": "model", "fv_confidence": True,
                          "fee_prob": 0.0, "mid": 0.30, "exit_ok": True, "depth": 50,
                          "bankroll": 10000,
                          "market": {"acceptingOrders": True,
                                     "umaProposalTime": datetime.now(timezone.utc).isoformat(),
                                     "challengePeriodSeconds": 7200}}
    return c


class FakeKalshi:
    def __init__(self, fill=True):
        self.orders, self.cancels, self.fill = [], [], fill

    def create_order(self, ticker, side, n, price_cents, order_type="limit", informed=None):
        self.orders.append((ticker, side, n, price_cents, order_type))
        o = {"order_id": "k1", "status": "executed" if self.fill else "canceled",
             "fill_count": n if self.fill else 0, "count": n, "no_price": price_cents}
        return {"order": o}

    def get_fills(self):
        return {"fills": []}

    def cancel_order(self, oid, ticker=None):
        self.cancels.append(oid)
        return {}


class FakePoly:
    def __init__(self, fills=True, raise_create=False):
        self.orders, self.cancels, self.fills, self.raise_create = [], [], fills, raise_create

    def create_order(self, body):
        if self.raise_create:
            raise RuntimeError("poly refused")
        self.orders.append(body)
        return {"id": "p1", "kind": "create_ack", "filled": False}

    def get_order(self, oid):
        q = self.orders[-1]["quantity"] if self.fills else 0
        st = "ORDER_STATE_FILLED" if self.fills else "ORDER_STATE_OPEN"
        return {"order": {"id": oid, "state": st, "cumQuantity": q, "avgPx": "0.30"}}

    def cancel_order(self, oid, body=None):
        self.cancels.append(oid)
        return {}


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


def _go(cand=None, env=None, k=None, p=None, **kw):
    k, p, clk = k or FakeKalshi(), p or FakePoly(), Clock()
    orig = ftl.LEDGER
    ftl.LEDGER = os.path.join(TMP, "ledger-%d.jsonl" % time.time_ns())
    try:
        row = al.promote(cand or _cand(), env=env if env is not None else _env(), kalshi=k,
                         poly=p, clock=clk, sleep=clk.sleep, **kw)
    finally:
        ftl.LEDGER = orig
    return row, k, p, clk


def test_flag_off_paper_only_zero_orders():
    assert al.enabled({}) is False and al.maybe_promote(_cand(), env={}) is None
    row, k, p, _ = _go(env={})
    assert row["status"] == "OFF" and row["orders"] == 0 and not k.orders and not p.orders
    poly, kalshi = va.fixture_books()
    path = os.path.join(TMP, "paper.jsonl")
    rows = va.run_scan(poly, kalshi, path=path, post_fn=tva._pass, env=dict(tva.PAPER))
    assert rows and all("arb_live" not in r and r["create_order"] is False for r in rows)
    assert vc.POLYMARKET_US_LIVE is False and vc.EXEC_DRY_RUN is False  # config untouched


def test_missing_or_bad_backtest_refused():
    if os.path.exists(REPORT):
        os.remove(REPORT)
    row, k, p, _ = _go()
    assert row["status"] == "REFUSED" and row["reason"] == "backtest_missing", row
    assert not k.orders and not p.orders
    for kw, why in ((dict(verdict="FAIL"), "backtest_not_pass"),
                    (dict(name="SPORTS_only"), "backtest_not_arb"),
                    (dict(age_s=8 * 86400), "backtest_stale")):
        _report(**kw)
        row, k, p, _ = _go()
        assert row["reason"] == why and not k.orders and not p.orders, (why, row)


def test_all_gates_both_legs_placed():
    _report()
    row, k, p, _ = _go()
    assert row["status"] == "BOTH_FILLED" and row["reason"] == "pass", row
    assert row["orders"] == 2 and row["both_filled"] is True
    assert len(k.orders) == 1 and k.orders[0][1] == "no" and k.orders[0][3] == 40
    assert k.orders[0][4] == "ioc", "leg 1 never rests"
    assert len(p.orders) == 1 and p.orders[0]["intent"] == "ORDER_INTENT_BUY_LONG"
    assert p.orders[0]["quantity"] == row["k_qty"] == k.orders[0][2]
    assert row["size_usd"] <= al.DEFAULT_MAX_USD and not k.cancels and not p.cancels


def test_size_cap():
    _report()
    row, k, _p, _ = _go(env=_env(VALEDIA_ARB_MAX_USD="10"))
    assert row["status"] == "BOTH_FILLED" and row["size_usd"] == 10.0, row
    assert k.orders[0][2] == int(10.0 / (0.30 + 0.40))


def test_leg2_failure_cancels_leg1_and_hedges():
    _report()
    row, k, p, clk = _go(p=FakePoly(fills=False))
    assert row["status"] == "LEG2_FAILED_LEG1_UNHEDGED" and row["alert"] is True, row
    assert p.cancels == ["p1"] and k.cancels == ["k1"], (p.cancels, k.cancels)
    assert row["leg1_cancelled"] and row["leg2_cancelled"] and row["unmatched"] == row["k_qty"]
    assert clk.t >= al.DEFAULT_LEG_TIMEOUT_S  # waited the documented N seconds, no longer
    hedged = []
    row, k, p, _ = _go(p=FakePoly(raise_create=True),
                       hedge_fn=lambda t, s, n: hedged.append((t, s, n)) or {"ok": True})
    assert row["status"] == "LEG2_FAILED_LEG1_HEDGED" and "poly refused" in row["error"], row
    assert k.cancels == ["k1"] and hedged == [(row["kalshi_ticker"], "no", row["k_qty"])]


def test_leg1_unfilled_places_nothing_else():
    _report()
    row, k, p, _ = _go(k=FakeKalshi(fill=False))
    assert row["status"] == "NONE_LEG1_UNFILLED" and row["orders"] == 1, row
    assert not p.orders and k.cancels == ["k1"]


def test_writer_halt_poly_live_allowlist_refusals():
    _report()
    saved = dict(vc.VENUE_WRITER)
    try:
        vc.VENUE_WRITER["polymarket"] = "none"
        row, k, p, _ = _go()
        assert row["reason"] == "writer_polymarket_none" and not k.orders and not p.orders, row
    finally:
        vc.VENUE_WRITER.clear()
        vc.VENUE_WRITER.update(saved)
    row, k, p, _ = _go(env=_env(POLYMARKET_US_LIVE="0"))
    assert row["reason"] == "poly_live_off" and not k.orders and not p.orders, row
    os.environ["VALEDIA_TRADING_HALT"] = "1"
    try:
        row, k, p, _ = _go()
        assert row["reason"] == "halt" and not k.orders and not p.orders, row
    finally:
        os.environ["VALEDIA_TRADING_HALT"] = "0"
    heur = dict(_cand(), source="heuristic", requires_review=True)
    row, k, p, _ = _go(cand=heur)
    assert row["reason"] == "not_allowlisted" and not k.orders, row
    off_list = dict(_cand(), kalshi_ticker="KXFEDCHAIRCONFIRM-ZZZ")
    row, k, p, _ = _go(cand=off_list)
    assert row["reason"] == "not_allowlisted" and not k.orders, row


def test_preflights_refuse_before_any_leg():
    _report()
    c = _cand()
    c["kalshi_informed"] = {}
    row, k, p, _ = _go(cand=c)
    assert row["reason"].startswith("kalshi_preflight:") and not k.orders and not p.orders, row
    c = _cand()
    c["poly_informed"] = {}
    row, k, p, _ = _go(cand=c)
    assert row["reason"].startswith("poly_preflight:") and not k.orders and not p.orders, row


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("test_arb_live PASS")
