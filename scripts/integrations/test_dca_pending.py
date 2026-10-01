#!/usr/bin/env python3
"""DCAEngine ambiguous-POST guard: never double-buy after a timeout. Fakes only, no network.

Run: python3 test_dca_pending.py
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["OPENCLAW_SKIP_ENV"] = "1"
os.environ["VALEDIA_TRADING_HALT"] = "0"

import alpaca_executor as ae  # noqa: E402
import strategy_modules as sm  # noqa: E402

PAIRS = {"BTC-USD": 0.5, "ETH-USD": 0.5}


def _engine(path):
    sm.DCAEngine.STATE_FILE = path
    return sm.DCAEngine(weekly_budget=10, assets=dict(PAIRS))


def _fresh():
    path = os.path.join(tempfile.mkdtemp(prefix="dcapend_"), "s.json")
    json.dump({"last_buy": None, "total_invested": 0, "buys": []}, open(path, "w"))
    return path


class Venue:
    def __init__(self, timeout_pairs=()):
        self.calls, self.timeout_pairs, self.booked = [], set(timeout_pairs), []

    def buy(self, pair, amount):
        self.calls.append(pair)
        if pair in self.timeout_pairs:
            raise TimeoutError("read timed out")  # the POST may or may not have landed
        return {"success": True, "order_id": "ok-" + pair}

    def book(self, pair, result):
        self.booked.append((pair, result.get("order_id")))


def test_timeout_then_found_at_venue_is_booked_not_rebought():
    saved = sm.DCAEngine.STATE_FILE
    try:
        path = _fresh()
        v = Venue(timeout_pairs={"BTC-USD", "ETH-USD"})
        acts = _engine(path).execute(v.buy, lambda: 100.0, book=v.book, lookup=lambda p, t: [])
        st = _engine(path).state
        assert set(st["pending"]) == set(PAIRS) and st["last_buy"] is None, (st, acts)
        assert any("ambiguous" in a for a in acts) and v.booked == []
        # next run: BTC's order landed (venue shows it), ETH's did not
        seen = []

        def lookup(pair, since):
            seen.append((pair, since))
            return [{"order_id": "cb-1", "filled_size": "0.0001",
                     "average_filled_price": "50000", "total_fees": "0.03"}] if pair == "BTC-USD" else []
        v2 = Venue()
        acts = _engine(path).execute(v2.buy, lambda: 100.0, book=v2.book, lookup=lookup)
        st = _engine(path).state
        # BTC found → booked, NOT re-bought; ETH (venue says none) retried in the same run
        assert v2.calls == ["ETH-USD"], (v2.calls, acts)
        assert v2.booked == [("BTC-USD", "cb-1"), ("ETH-USD", "ok-ETH-USD")], v2.booked
        assert st["last_buy"] is not None and st["pending"] == {}, st   # found counts as an ack
        assert [b["pair"] for b in st["buys"]] == ["BTC-USD", "ETH-USD"]
        assert all(t.endswith("Z") for _p, t in seen) and len(seen) == 2
    finally:
        sm.DCAEngine.STATE_FILE = saved


def test_venue_says_none_then_retry_and_unknown_skips():
    saved = sm.DCAEngine.STATE_FILE
    try:
        path = _fresh()
        v = Venue(timeout_pairs={"BTC-USD"})
        _engine(path).execute(v.buy, lambda: 100.0, book=v.book, lookup=lambda p, t: [])
        st = _engine(path).state
        assert list(st["pending"]) == ["BTC-USD"] and st["last_buy"] is not None  # ETH acked
        # force the week due again; BTC's venue lookup FAILS → BTC never re-bought, ETH buys
        st["last_buy"] = None
        json.dump(st, open(path, "w"))
        v2 = Venue()
        acts = _engine(path).execute(v2.buy, lambda: 100.0, book=v2.book, lookup=lambda p, t: None)
        assert v2.calls == ["ETH-USD"] and any("unresolved" in a for a in acts), (v2.calls, acts)
        assert list(_engine(path).state["pending"]) == ["BTC-USD"]
        # venue says none → marker cleared and BTC retried
        st = _engine(path).state
        st["last_buy"] = None
        json.dump(st, open(path, "w"))
        v3 = Venue()
        _engine(path).execute(v3.buy, lambda: 100.0, book=v3.book, lookup=lambda p, t: [])
        assert v3.calls == ["BTC-USD", "ETH-USD"] and _engine(path).state["pending"] == {}
    finally:
        sm.DCAEngine.STATE_FILE = saved


def test_not_placed_errors_and_unpersistable_marker():
    import nimble_wiring_spot as nws
    saved = sm.DCAEngine.STATE_FILE
    try:
        path = _fresh()

        def vetoed(p, a):
            raise nws.NimbleSkip("SKIP_NIMBLE veto")
        _engine(path).execute(vetoed, lambda: 100.0, book=lambda p, r: None)
        assert _engine(path).state.get("pending") == {}, "a pre-POST refusal leaves no marker"
        placed = []
        bad = os.path.join(tempfile.mkdtemp(), "missing-dir", "s.json")
        acts = _engine(bad).execute(lambda p, a: placed.append(p) or {"success": True},
                                    lambda: 100.0, book=lambda p, r: None)
        assert placed == [] and all("marker not persisted" in a for a in acts[:2]), acts
    finally:
        sm.DCAEngine.STATE_FILE = saved


def test_5xx_then_found_not_rebought():
    import dca_pending as dp
    saved = sm.DCAEngine.STATE_FILE
    try:
        path = _fresh()
        bad = {"success": False, "error": "<html>502 Bad Gateway</html>", "http_status": 502,
               "ambiguous": True}
        calls = []
        _engine(path).execute(lambda p, a: calls.append(p) or dict(bad), lambda: 100.0,
                              book=lambda p, r: None, lookup=lambda p, t: [])
        st = _engine(path).state
        assert set(st["pending"]) == set(PAIRS) and st["last_buy"] is None, st  # marker kept
        # next run: the 502 POSTs had landed → booked from the venue, never bought twice
        booked, calls2 = [], []
        acts = _engine(path).execute(
            lambda p, a: calls2.append(p) or {"success": True}, lambda: 100.0,
            book=lambda p, r: booked.append((p, r.get("order_id"))),
            lookup=lambda p, t: [{"order_id": "landed-" + p}])
        assert calls2 == [] and sorted(booked) == [("BTC-USD", "landed-BTC-USD"),
                                                   ("ETH-USD", "landed-ETH-USD")], (calls2, acts)
        assert _engine(path).state["last_buy"] is not None
        # classification table: only explicit rejections are definitive
        amb = ({"success": False, "error": "504 Gateway Time-out"}, {"success": False},
               {"success": False, "http_status": 503, "error": "invalid"}, None, "garbage",
               {"success": False, "http_status": 408, "error": "timeout"})
        ref = ({"success": False, "rejected": True}, {"success": False, "error": "rejected"},
               {"http_status": 400, "message": "INVALID_PRODUCT_ID"},
               {"success": False, "error": "INSUFFICIENT_FUND: insufficient balance"})
        assert all(dp.outcome(r) == "ambiguous" for r in amb), [dp.outcome(r) for r in amb]
        assert all(dp.outcome(r) == "refused" for r in ref), [dp.outcome(r) for r in ref]
        assert dp.outcome(exc=TimeoutError("x")) == "ambiguous"
        assert dp.outcome(exc=ConnectionError("x")) == "ambiguous"
    finally:
        sm.DCAEngine.STATE_FILE = saved


def test_venue_wrappers_classify_5xx_as_ambiguous():
    import types
    ct = __import__("coinbase_trader")
    orig = ct._request
    try:
        ct._request = lambda m, p, b=None: types.SimpleNamespace(ok=False, status_code=502,
                                                                  text="bad gateway")
        r = ct.market_buy("BTC-USD", 5)
        assert r["ambiguous"] is True and r["http_status"] == 502, r
        ct._request = lambda m, p, b=None: types.SimpleNamespace(
            ok=True, status_code=200, json=lambda: {"success": False, "error_response": {
                "error": "INSUFFICIENT_FUND"}})
        r = ct.market_buy("BTC-USD", 5)
        assert r["rejected"] is True and r["reject_reason"] == "INSUFFICIENT_FUND", r
    finally:
        ct._request = orig
    import requests
    orig_post = requests.post
    try:
        requests.post = lambda *a, **k: types.SimpleNamespace(ok=False, status_code=503,
                                                               text="unavailable")
        assert ae.market_buy("SPY", notional=5)["ambiguous"] is True
        requests.post = lambda *a, **k: types.SimpleNamespace(
            ok=False, status_code=403, json=lambda: {"code": 40310000, "message": "insufficient buying power"})
        r = ae.market_buy("SPY", notional=5)
        assert r["rejected"] is True and r["http_status"] == 403, r
    finally:
        requests.post = orig_post


def test_dca_module_ambiguous_stub_then_resolve():
    """dca_module.run_dca: a 5xx buy leaves an UNRECONCILED stub (no order id); the next run
    looks the ticker up before buying: found → booked + stub closed, no re-buy; none → stub
    closed + bought; lookup failed → skipped. Crypto leg only (SPY uses Alpaca paper here)."""
    import forward_test_ledger as ftl
    import test_spot_ledger_booking as tsb
    bad = {"success": False, "error": "<html>502</html>", "http_status": 502, "ambiguous": True}
    paper = {"id": "a", "status": "accepted"}

    def stubs():
        done = ftl._settled_ids()
        return [r for r in ftl._read() if r.get("reason") == "ambiguous_5xx"
                and r["id"] not in done]
    with tsb.ledger():
        out, calls = tsb.run_dca(bad, paper, False, lookup=lambda t, s: [])
        st = stubs()
        assert calls["cb_buy"] == ["BTC-USD"] and len(st) == 1, (calls, ftl._read())
        assert st[0]["fill_status"] == "UNRECONCILED" and "order_id" not in st[0]
        assert st[0]["intended_notional"] == 5.0 and st[0]["venue"] == "coinbase"
        # lookup fails → BTC skipped (stub stays), no second POST
        out, calls = tsb.run_dca(tsb.cb_fill(0.0001, 50000, oid="x"), paper, False,
                                 lookup=lambda t, s: None)
        assert calls["cb_buy"] == [] and len(stubs()) == 1 and "unresolved" in out, out
        # found at the venue → booked as the fill, stub closed, NOT re-bought
        landed = [{"order_id": "landed-1", "filled_size": "0.0001",
                   "average_filled_price": "50000", "total_fees": "0.03"}]
        out, calls = tsb.run_dca(tsb.cb_fill(0.0001, 50000, oid="y"), paper, False,
                                 lookup=lambda t, s: landed)
        assert calls["cb_buy"] == [] and stubs() == [], (calls, out)
        filled = [r for r in ftl._read() if r.get("order_id") == "landed-1"]
        assert filled and filled[0]["fill_status"] == "FILLED" and filled[0]["entry_fee"] == 0.03
        # the booked order id is never matched again; a fresh 5xx then "venue says none" → buy
        tsb.run_dca(bad, paper, False, lookup=lambda t, s: [])
        assert len(stubs()) == 1
        out, calls = tsb.run_dca(tsb.cb_fill(0.0001, 50000, oid="z"), paper, False,
                                 lookup=lambda t, s: landed)  # only the already-booked id
        assert calls["cb_buy"] == ["BTC-USD"] and stubs() == [], (calls, out)
        assert [r for r in ftl._read() if r.get("order_id") == "z"]


def test_dca_module_timeout_exception_is_stubbed():
    import dca_module
    import forward_test_ledger as ftl
    import test_spot_ledger_booking as tsb

    def timeout():
        raise TimeoutError("read timed out")

    def halted():
        raise type("HaltedError", (RuntimeError,), {})("halt")
    with tsb.ledger():
        r = dca_module._buy_or_stub("coinbase", "BTC-USD", 5.0, timeout)
        rows = [x for x in ftl._read() if x.get("reason") == "ambiguous_5xx"]
        assert r["ambiguous"] is True and len(rows) == 1 and "timed out" in rows[0]["error"], rows
        try:  # a pre-POST refusal is definitive: re-raised, no stub
            dca_module._buy_or_stub("coinbase", "ETH-USD", 5.0, halted)
            raise AssertionError("must re-raise")
        except RuntimeError as e:
            assert str(e) == "halt"
        assert len([x for x in ftl._read() if x.get("reason") == "ambiguous_5xx"]) == 1


class _R:
    def __init__(self, ok, body):
        self.ok, self._b = ok, body

    def json(self):
        return self._b


def test_venue_lookup_helpers():
    orders = {"orders": [
        {"order_id": "a", "product_id": "BTC-USD", "side": "BUY", "status": "FILLED", "filled_size": "1"},
        {"order_id": "b", "product_id": "BTC-USD", "side": "BUY", "status": "FAILED"},
        {"order_id": "c", "product_id": "BTC-USD", "side": "BUY", "status": "CANCELLED", "filled_size": "0"},
        {"order_id": "d", "product_id": "BTC-USD", "side": "SELL", "status": "FILLED"},
        {"order_id": "e", "product_id": "ETH-USD", "side": "BUY", "status": "OPEN"}]}
    try:
        import coinbase_trader as ct
    except Exception:  # jwt/cryptography missing in this sandbox: helper is still covered below
        ct = None
    if ct is not None:
        got = ct.recent_buy_orders("BTC-USD", "2026-10-01T00:00:00Z",
                                   request=lambda m, p: _R(True, orders))
        assert [o["order_id"] for o in got] == ["a"], got
        assert ct.recent_buy_orders("BTC-USD", "x", request=lambda m, p: _R(False, {})) is None
        assert ct.recent_buy_orders("BTC-USD", "x", request=lambda *a: 1 / 0) is None
    al = [{"id": "1", "symbol": "SPY", "side": "buy", "status": "filled", "filled_qty": "2"},
          {"id": "2", "symbol": "SPY", "side": "buy", "status": "rejected"},
          {"id": "3", "symbol": "SPY", "side": "buy", "status": "canceled", "filled_qty": "0"},
          {"id": "4", "symbol": "SPY", "side": "buy", "status": "new"}]
    got = ae.recent_buy_orders("SPY", "2026-10-01T00:00:00Z", get=lambda *a, **k: _R(True, al))
    assert [o["order_id"] for o in got] == ["1", "4"], got
    assert ae.recent_buy_orders("SPY", "x", get=lambda *a, **k: _R(False, [])) is None
    assert ae.recent_buy_orders("SPY", "x", get=lambda *a, **k: 1 / 0) is None


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
    print("test_dca_pending PASS")
