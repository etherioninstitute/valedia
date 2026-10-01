#!/usr/bin/env python3
"""Maker-first routing + MakerOrderManager fill/cancel truth. Fakes only, no network.

Run: python3 test_maker_routing.py
"""
import os
import sys
import tempfile
import types
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["OPENCLAW_SKIP_ENV"] = "1"
for _k in ("VALEDIA_SYSTEMONE", "VALEDIA_MAKER_FIRST_ENFORCE", "KALSHI_MAKER_FIRST",
           "KALSHI_EV_PREFLIGHT_GATE", "KALSHI_TAKER_HURDLE"):
    os.environ.pop(_k, None)
os.environ["VALEDIA_TRADING_HALT"] = "0"

import execution_policy as ep  # noqa: E402
import forward_test_ledger as ftl  # noqa: E402
import kalshi_auth  # noqa: E402
import kalshi_fee_map as kfm  # noqa: E402
import kalshi_profit as kp  # noqa: E402
import maker_routing as mr  # noqa: E402
import master_allocator as ma  # noqa: E402
import valedia_config as vc  # noqa: E402
import test_h3 as h3  # noqa: E402

FREE = 0.0  # maker multiplier 0 → maker_viable


def _rt(viable, m_net, t_net, hurdle=0.02):
    return {"maker_viable": viable, "hurdle": hurdle,
            "maker_ev": {"net": m_net}, "taker_ev": {"net": t_net}}


# ------------------------------------------------------------------ pure rule
def test_rule_maker_when_viable_and_better():
    d = mr.decide(_rt(True, 0.10, 0.075), maker_px=46, taker_px=50, secs=8 * 3600, live=True)
    assert d["route"] == "maker" and d["why"] == "maker_viable", d
    assert d["maker_ev"] == 0.10 and d["taker_ev"] == 0.075
    # legacy wide-spread plan: maker strictly better even on a fee-paying maker series
    d = mr.decide(_rt(False, 0.09, 0.05), legacy_maker=True, maker_px=40, taker_px=47,
                  secs=8 * 3600, live=True)
    assert d["route"] == "maker" and d["why"] == "maker_better_wide_spread", d


def test_rule_taker_only_per_rule():
    # not viable, taker clears its higher hurdle → taker
    d = mr.decide(_rt(False, 0.06, 0.057), maker_px=46, taker_px=50, secs=8 * 3600, live=True)
    assert d["route"] == "taker" and "maker_not_viable" in d["why"], d
    # not viable, taker under hurdle (0.02 + dome 0.02 at 50c) → none
    d = mr.decide(_rt(False, 0.06, 0.035), maker_px=46, taker_px=50, secs=8 * 3600, live=True)
    assert d["route"] == "none", d
    # viable but closes too soon: taker only if it beats maker by the hurdle
    soon = mr.MAKER_MIN_SECONDS_TO_CLOSE - 1
    d = mr.decide(_rt(True, 0.05, 0.08), maker_px=46, taker_px=50, secs=soon, live=True)
    assert d["route"] == "taker" and "closes_in_" in d["why"], d
    d = mr.decide(_rt(True, 0.10, 0.075), maker_px=46, taker_px=50, secs=soon, live=True)
    assert d["route"] == "none", d
    # maker blocked by: no resting price, maker below its hurdle, exec off
    for kw, tag in ((dict(maker_px=None), "no_resting_price"), (dict(live=False), "maker_exec_off")):
        args = dict(maker_px=46, taker_px=50, secs=8 * 3600, live=True)
        args.update(kw)
        d = mr.decide(_rt(True, 0.10, 0.075), **args)
        assert d["route"] == "none" and tag in d["why"], (tag, d)
    d = mr.decide(_rt(True, 0.005, -0.01), maker_px=50, taker_px=51, secs=9e9, live=True)
    assert d["route"] == "none" and "maker_ev_below_hurdle" in d["why"], d
    # thresholds unchanged
    assert kp.TAKER_HURDLE == 0.02 and kp.DOME_PEAK == 0.02 and mr.MAKER_MIN_SECONDS_TO_CLOSE == 7200


def test_price_and_book_helpers():
    assert mr.cents(0.45) == 45 and mr.cents(45) == 45 and mr.cents(0) == 0 and mr.cents("x") == 0
    assert mr.maker_price_cents(45, 50) == 46       # improve inside the spread
    assert mr.maker_price_cents(49, 50) == 49       # 1c spread: join the bid
    assert mr.maker_price_cents(0, 50) is None      # no bid → cannot rest
    a = {"side": "no", "price": 0.55, "raw": {"no_bid": 0.50, "no_ask": 0.55}}
    assert mr.book_cents(a) == (50, 55)
    # kalshi_profit reads raw dollars too (allocator rows carry the book only in raw)
    opp = {"ticker": "KXBTCY-1", "series": "KXBTCY", "side": "yes", "price": 0.50, "edge": 0.10,
           "raw": {"yes_bid": 0.45, "yes_ask": 0.50}}
    _ok, out = kp.annotate(opp, contracts=10, fmap=kfm.build_map())
    assert out["maker_first"]["maker_ev"]["P"] == 0.45, out["maker_first"]["maker_ev"]
    assert out["maker_first"]["maker_price_known"] is True
    rt = kp.route("KXBTCY-1", "KXBTCY", edge=0.10, bid=0, ask=0.50, C=10, fmap=kfm.build_map())
    assert rt["maker_ev"]["P"] == rt["taker_ev"]["P"] and rt["maker_price_known"] is False


# ------------------------------------------------------------------ allocator integration
class FakeMgr:
    instances = []

    def __init__(self, *a, **k):
        self.placed, self._active = [], {}
        FakeMgr.instances.append(self)

    def check_and_manage(self):
        pass

    @property
    def active_orders(self):
        return dict(self._active)

    def place_maker_order(self, ticker, side, n, price_cents, strategy="", informed=None):
        self.placed.append((ticker, side, n, price_cents))
        return "mkr-1"


def _alloc(series="MKR", hours=8, maker_m=FREE, taker_m=FREE):
    now = datetime.now(timezone.utc)
    a = {"platform": "kalshi", "type": "SPORTS", "ticker": series + "-1", "title": "t",
         "side": "yes", "price": 0.50, "allocation": 5.0, "expected_profit": 0.5,
         "hours_to_resolve": hours,
         "raw": {"yes_bid": 0.45, "yes_ask": 0.50, "no_bid": 0.50, "no_ask": 0.55}}
    a.update(h3._admitted_sports_opp(series))
    a["fmap"] = {series: kfm._row(series, maker_m, taker_m, "test")}
    a["market"] = dict(a["market"], close_time=(now + timedelta(hours=hours)).isoformat())
    a.update(bid=0.45, ask=0.50)
    return a


def _run(alloc, mgr_cls=FakeMgr):
    orig_led, path = h3._ledger()
    saved = (ma.MakerOrderManager, ma._is_kalshi_blacklisted, ma._clear_kalshi_failure,
             kalshi_auth.create_order, kalshi_auth.get_positions, kalshi_auth.get_fills,
             sys.modules.get("intel_layer"))
    fake = types.ModuleType("intel_layer")

    class _I:
        def get_kalshi_conviction(self, *a, **k):
            return {"recommended_side": "yes", "confidence": 0.2, "conviction_yes": 50}
    fake.get_intel = lambda: _I()
    sys.modules["intel_layer"] = fake
    orders = []
    FakeMgr.instances = []
    ma.MakerOrderManager = mgr_cls
    ma._is_kalshi_blacklisted = lambda t: False
    ma._clear_kalshi_failure = lambda t: None
    kalshi_auth.get_positions = lambda: {"market_positions": []}
    kalshi_auth.create_order = lambda *a, **k: orders.append((a, k)) or h3._ack("tk-1")
    kalshi_auth.get_fills = lambda: h3._fills("tk-1", 10, 50)
    try:
        out = ma.execute_allocations([alloc], dry_run=False)
        mgr = FakeMgr.instances[0] if FakeMgr.instances else None
        return out[0], orders, mgr
    finally:
        (ma.MakerOrderManager, ma._is_kalshi_blacklisted, ma._clear_kalshi_failure,
         kalshi_auth.create_order, kalshi_auth.get_positions, kalshi_auth.get_fills,
         old) = saved
        if old is None:
            sys.modules.pop("intel_layer", None)
        else:
            sys.modules["intel_layer"] = old
        h3._restore(orig_led, path)


def test_allocator_maker_chosen_when_viable_and_better():
    assert vc.EXEC_DRY_RUN is False  # config untouched; maker can rest
    r, orders, mgr = _run(_alloc())
    assert r["status"] == "MAKER_PLACED", r
    assert orders == [] and mgr.placed == [("MKR-1", "yes", 10, 46)], (orders, mgr.placed)
    assert r["route"] == "maker" and r["route_why"] == "maker_viable", r
    assert r["maker_ev"] > r["taker_ev"], r


def test_allocator_too_soon_and_enforce():
    # closes in 1h: maker unsafe; taker does not clear on a viable series → legacy (flag OFF)
    r, orders, mgr = _run(_alloc(hours=1))
    assert r["route"] == "legacy" and "closes_in_" in r["route_why"], r
    assert r["status"] == "EXECUTED" and len(orders) == 1 and mgr.placed == [], (r, orders)
    os.environ["VALEDIA_MAKER_FIRST_ENFORCE"] = "1"
    try:
        r, orders, mgr = _run(_alloc(hours=1))
        assert r["status"] == "SKIP_NO_ROUTE" and orders == [] and mgr.placed == [], (r, orders)
    finally:
        os.environ.pop("VALEDIA_MAKER_FIRST_ENFORCE", None)


def test_allocator_taker_when_maker_not_viable():
    r, orders, mgr = _run(_alloc(series="TKR", maker_m=1.0, taker_m=1.0))
    assert r["route"] == "taker" and "maker_not_viable" in r["route_why"], r
    assert r["status"] == "EXECUTED" and len(orders) == 1 and mgr.placed == [], (r, orders)
    assert orders[0][0][3] == 50  # taker at the quoted ask


def test_allocator_never_stacks_on_resting_maker():
    class Resting(FakeMgr):
        def __init__(self, *a, **k):
            super().__init__()
            self._active = {"old": {"ticker": "MKR-1", "side": "yes"}}
    r, orders, mgr = _run(_alloc(), mgr_cls=Resting)
    assert r["status"] == "SKIP_MAKER_RESTING" and orders == [] and mgr.placed == [], r


# ------------------------------------------------------------------ MakerOrderManager truth
class Stub:
    def __init__(self):
        self.cancelled, self.placed, self._fills, self.fail_fills = [], [], [], False

    def place_order(self, t, side, n, p):
        self.placed.append((t, side, n, p))
        return {"order": {"order_id": "oid_%d" % len(self.placed)}}

    def cancel_order(self, oid, ticker=None):
        self.cancelled.append(oid)
        return {}

    def get_fills(self):
        if self.fail_fills:
            raise OSError("fills down")
        return {"fills": list(self._fills)}


def _mgr(stub, **kw):
    sf = tempfile.mktemp(suffix=".json")
    return ep.MakerOrderManager(api=stub, state_file=sf, **kw), sf


def _capture():
    rec = []
    orig = ftl.record_entry
    ftl.record_entry = lambda *a, **k: rec.append((a, k))
    return rec, orig


def test_fills_recorded_only_on_actual_fill():
    rec, orig = _capture()
    try:
        stub = Stub()
        m, sf = _mgr(stub)
        oid = m.place_maker_order("KXM-1", "yes", 7, 46, strategy="S")
        assert oid and rec == [], "placement is never a fill"
        m.check_and_manage()
        assert rec == [] and oid in m.active_orders, "no fill → nothing recorded"
        stub.fail_fills = True
        m.check_and_manage()
        assert rec == [] and oid in m.active_orders, "unknown fill state ≠ fill"
        stub.fail_fills = False
        stub._fills = [{"order_id": oid, "count_fp": "3"}]
        m.check_and_manage()
        assert [x[0][4] for x in rec] == [3.0] and oid in m.active_orders, rec   # partial: keep resting
        m.check_and_manage()
        assert len(rec) == 1, "same fill never recorded twice"
        stub._fills.append({"order_id": oid, "count": 4})
        m.check_and_manage()
        assert [x[0][4] for x in rec] == [3.0, 4.0] and not m.active_orders, rec
        os.path.exists(sf) and os.remove(sf)
    finally:
        ftl.record_entry = orig


def test_stale_maker_cancelled_and_late_fill_reconciled():
    rec, orig = _capture()
    try:
        stub = Stub()
        m, sf = _mgr(stub, max_age_seconds=0)
        oid = m.place_maker_order("KXM-2", "no", 5, 20)
        m.check_and_manage()
        assert stub.cancelled == [oid] and not m.active_orders, "stale order must be cancelled"
        assert rec == []
        # a fill that raced the cancel is still recorded next cycle (cancelled orders stay tracked)
        stub._fills = [{"order_id": oid, "count": 2}]
        m2 = ep.MakerOrderManager(api=stub, state_file=sf)
        m2.check_and_manage()
        assert [x[0][4] for x in rec] == [2.0] and stub.cancelled == [oid], (rec, stub.cancelled)
        # a failed cancel keeps the order live (never orphaned) and retries
        class Flaky(Stub):
            def cancel_order(self, oid, ticker=None):
                raise OSError("venue 503")
        fl = Flaky()
        m3, sf3 = _mgr(fl, max_age_seconds=0)
        o3 = m3.place_maker_order("KXM-3", "yes", 1, 30)
        m3.check_and_manage()
        assert o3 in m3.active_orders and m3._orders[o3]["cancel_attempts"] == 1
        for p in (sf, sf3):
            os.path.exists(p) and os.remove(p)
    finally:
        ftl.record_entry = orig


def test_reprice_reposts_only_remainder():
    rec, orig = _capture()
    try:
        stub = Stub()
        m, sf = _mgr(stub)
        oid = m.place_maker_order("KXM-4", "yes", 7, 40)
        stub._fills = [{"order_id": oid, "count": 3}]
        book = ep.MARKET_STATE.update_book("KXM-4", {"orderbook": {
            "yes": [{"price": 45, "size": 5}], "no": [{"price": 50, "size": 5}]}})
        assert m._check_reprice(oid, m._orders[oid], book) is True
        assert stub.cancelled == [oid] and stub.placed[-1] == ("KXM-4", "yes", 4, 46), stub.placed
        assert [x[0][4] for x in rec] == [3.0]
        os.path.exists(sf) and os.remove(sf)
    finally:
        ftl.record_entry = orig


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("test_maker_routing PASS")
