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
# ERE v3: hermetic — valid gate state in a temp models dir (never the live one)
import ere_test_fixture
vc._MODELS = tempfile.mkdtemp(prefix="arblive_ere_")
ere_test_fixture.write_state(vc._MODELS)
import vaughan_arb_paper as va  # noqa: E402
import test_vaughan_arb_paper as tva  # noqa: E402

TMP = tempfile.mkdtemp(prefix="arb_live_")
REPORT = os.path.join(TMP, "backtest_arb.json")


def _report(verdict="PASS", category="ARB", age_s=0, generated=True, name="ARB_candidate",
            base=None, cand=None, policies=None):
    doc = {"tool": "backtest_gate", "verdict": verdict, "category": category,
           "baseline": base or {"name": "ARB_baseline", "n_taken": 40, "net": 1.0},
           "candidate": cand or {"name": name, "n_taken": 35, "net": 3.0}}
    if policies is not None:
        doc["policies"] = policies
    if generated:
        doc["generated_at"] = (datetime.now(timezone.utc) - timedelta(seconds=age_s)).isoformat()
    with open(REPORT, "w") as f:
        json.dump(doc, f)


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
    def __init__(self, fills=True, raise_create=False, partial=None, read_fails=False):
        self.orders, self.cancels, self.fills, self.raise_create = [], [], fills, raise_create
        self.partial, self.read_fails = partial, read_fails

    def create_order(self, body):
        if self.raise_create:
            raise RuntimeError("poly refused")
        self.orders.append(body)
        return {"id": "p1", "kind": "create_ack", "filled": False}

    def get_order(self, oid):
        if self.read_fails:
            raise OSError("poly GET down")
        if self.partial is not None:
            return {"order": {"id": oid, "state": "ORDER_STATE_PARTIALLY_FILLED",
                              "cumQuantity": self.partial, "avgPx": "0.30"}}
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


LAST = {}


def _go(cand=None, env=None, k=None, p=None, clk=None, **kw):
    k, p, clk = k or FakeKalshi(), p or FakePoly(), clk or Clock()
    kw.setdefault("ornith_post", tva._pass)
    orig = ftl.LEDGER
    ftl.LEDGER = LAST["ledger"] = os.path.join(TMP, "ledger-%d.jsonl" % time.time_ns())
    env = env if env is not None else _env()
    LAST["log"] = env.get("VALEDIA_ARB_LIVE_LOG")
    try:
        row = al.promote(cand or _cand(), env=env, kalshi=k,
                         poly=p, clock=clk, sleep=clk.sleep, **kw)
    finally:
        ftl.LEDGER = orig
    return row, k, p, clk


def _ledger_rows():
    try:
        return [json.loads(x) for x in open(LAST["ledger"]) if x.strip()]
    except OSError:
        return []


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
                    (dict(category=None), "backtest_not_arb"),          # name "ARB_candidate" ≠ category
                    (dict(category="ARB_SPORTS"), "backtest_not_arb"),  # substring is not a match
                    (dict(category="arb"), "backtest_not_arb"),
                    (dict(generated=False), "backtest_stale"),          # no generated_at → stale
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


def test_backtest_self_comparison_refused():
    for kw in (dict(base={"name": "same", "net": 1.0}, cand={"name": "same", "net": 2.0}),
               dict(base={"name": "b", "n_taken": 40, "net": 1.0},
                    cand={"name": "c", "n_taken": 40, "net": 1.0}),
               dict(policies={"baseline": {"name": "b", "min_edge": 0.02},
                              "candidate": {"name": "c", "min_edge": 0.02}})):
        _report(**kw)
        row, k, _p, _ = _go()
        assert row["reason"] == "backtest_self_compare" and not k.orders, (kw, row)
    _report(policies={"baseline": {"name": "b", "min_edge": 0.02},
                      "candidate": {"name": "c", "min_edge": 0.03}})
    row, _k, _p, _ = _go()
    assert row["status"] == "BOTH_FILLED", row


def test_leg1_fill_unknown_counts_exposure_and_alerts():
    _report()

    class NoFields(FakeKalshi):
        def create_order(self, ticker, side, n, price_cents, order_type="limit", informed=None):
            self.orders.append((ticker, side, n, price_cents, order_type))
            return {"order": {"order_id": "k1", "status": "resting"}}  # IOC ack, no fill fields

        def get_fills(self):
            raise OSError("fills endpoint down")
    row, k, p, _ = _go(k=NoFields())
    assert row["status"] == "LEG1_UNKNOWN" and row["filled"] is True and row["alert"] is True, row
    assert row["exposure_unknown"] and row["possible_contracts"] == k.orders[0][2]
    assert k.cancels == ["k1"] and not p.orders, "cancel attempted, no leg 2"
    assert json.loads(open(LAST["log"]).readlines()[-1])["status"] == "LEG1_UNKNOWN"


def test_poly_fill_unknown_always_alerts_even_when_hedged():
    _report()
    hedged = []
    row, k, p, _ = _go(p=FakePoly(read_fails=True),
                       hedge_fn=lambda t, s, n: hedged.append(n) or {"ok": True})
    assert row["status"] == "LEG2_FAILED_LEG1_HEDGED" and hedged == [row["k_qty"]], row
    assert row["poly_fill_unknown"] is True and row["alert"] is True, row
    row, k, p, _ = _go(p=FakePoly(fills=False), hedge_fn=lambda t, s, n: {"ok": True})
    assert row["status"] == "LEG2_FAILED_LEG1_HEDGED" and row["poly_fill_unknown"] is False
    assert row["alert"] is False, "a CONFIRMED zero Poly fill that is fully hedged needs no alert"


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


def test_backtest_freshness_ignores_mtime_and_category_configurable():
    _report(age_s=8 * 86400)
    os.utime(REPORT, None)  # fresh mtime must not rescue a stale generated_at
    row, k, _p, _ = _go()
    assert row["reason"] == "backtest_stale" and not k.orders, row
    _report(category="PREDICTION_PAIR")
    row, _k, _p, _ = _go(env=_env(VALEDIA_ARB_CATEGORY="PREDICTION_PAIR"))
    assert row["status"] == "BOTH_FILLED", row


def test_ornith_pass_required():
    _report()
    row, k, p, _ = _go(ornith_post=tva._refuse)
    assert row["reason"] == "ornith:ornith_refuse" and not k.orders and not p.orders, row
    row, k, p, _ = _go(ornith_post=tva._down)
    assert row["reason"] == "ornith:ornith_down" and not k.orders, row
    # a paper row with Ornith PASS for this exact pair is reused (Ornith not asked again)
    paper = {"ticker": "%s|%s" % (_cand()["poly_slug"], _cand()["kalshi_ticker"]), "ornith": "pass"}
    row, k, _p, _ = _go(ornith_post=tva._bomb, paper_row=paper)
    assert row["status"] == "BOTH_FILLED", row
    other = dict(paper, ticker="x|y")
    row, k, _p, _ = _go(ornith_post=tva._refuse, paper_row=other)
    assert row["reason"] == "ornith:ornith_refuse" and not k.orders, row


def test_partial_poly_fill_booked_and_unmatched_alerted():
    _report()
    row, k, p, _ = _go(p=FakePoly(partial=5))
    assert row["status"] == "LEG2_FAILED_LEG1_UNHEDGED" and row["p_qty"] == 5, row
    assert row["unmatched"] == row["k_qty"] - 5 and row["alert"] is True
    poly_rows = [r for r in _ledger_rows() if r.get("venue", "").startswith("poly")]
    assert len(poly_rows) == 1 and float(poly_rows[0]["contracts"]) == 5.0, poly_rows
    logged = [json.loads(x) for x in open(LAST["log"])]
    assert logged[-1]["status"] == row["status"] and logged[-1]["filled"] is True


def test_poly_unreadable_booked_unreconciled_with_order_id():
    _report()
    row, k, p, _ = _go(p=FakePoly(read_fails=True))
    assert row["status"] == "LEG2_FAILED_LEG1_UNHEDGED" and p.cancels == ["p1"], row
    poly_rows = [r for r in _ledger_rows() if r.get("venue", "").startswith("poly")]
    assert len(poly_rows) == 1 and poly_rows[0].get("order_id") == "p1", poly_rows
    assert poly_rows[0].get("fill_status") != "FILLED", poly_rows


def test_exceptions_after_leg1_unwind_and_log():
    _report()

    class BoomClock(Clock):
        def __call__(self):
            raise RuntimeError("clock exploded")
    row, k, p, _ = _go(clk=BoomClock())
    assert row["status"] == "POST_LEG1_ERROR_LEG1_UNHEDGED" and row["alert"] is True, row
    assert k.cancels == ["k1"] and "clock exploded" in row["error"] and row["filled"] is True
    assert json.loads(open(LAST["log"]).readlines()[-1])["status"] == row["status"]

    class BoomKalshi(FakeKalshi):
        def create_order(self, *a, **k):
            self.orders.append(a)
            raise TimeoutError("POST timed out")
    row, k, p, _ = _go(k=BoomKalshi())
    assert row["status"] == "LEG1_ERROR" and row["exposure_unknown"] and row["filled"], row
    assert not p.orders and json.loads(open(LAST["log"]).readlines()[-1])["status"] == "LEG1_ERROR"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("test_arb_live PASS")
