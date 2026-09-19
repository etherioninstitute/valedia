#!/usr/bin/env python3
"""H3 — Kalshi fill-true: POST-ack is not a fill; GET /portfolio/fills may upgrade.

No network, no keys, no --live. Isolated temp ledger.
Run: python3 test_h3.py
"""
import os
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import forward_test_ledger as ftl
import sports_engine as se
import master_allocator as ma
import kalshi_auth
import kalshi_executor as ke
import valedia_config as vc


def _ack(oid="ack-1"):
    return {"order": {"order_id": oid, "status": "resting", "count": 5, "yes_price": 80}}


def _fills(oid, n=5, px=80):
    return {"fills": [{"order_id": oid, "count": n, "yes_price": px}]}


def _ledger():
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    orig = ftl.LEDGER
    ftl.LEDGER = path
    return orig, path


def _restore(orig, path):
    ftl.LEDGER = orig
    try:
        os.remove(path)
    except OSError:
        pass


def test_resolve_poll_rules():
    polled = []
    fill, payload = ftl.resolve_kalshi_fill(_ack(), None)
    assert fill["complete"] is False and payload is None

    fill, payload = ftl.resolve_kalshi_fill(_ack("m1"), lambda: polled.append(1) or _fills("m1", 3, 77))
    assert fill["complete"] is True and fill["qty"] == 3, fill
    assert payload["fills"][0]["order_id"] == "m1"
    assert polled == [1]

    fill, payload = ftl.resolve_kalshi_fill(_ack("m1"), lambda: _fills("other", 9))
    assert fill["complete"] is False, fill  # do not steal another order's fills

    def boom():
        raise RuntimeError("no network")
    fill, payload = ftl.resolve_kalshi_fill(_ack("m1"), boom)
    assert fill["complete"] is False and payload is None

    polled.clear()
    raw = {"success": True}  # no order_id
    fill, payload = ftl.resolve_kalshi_fill(raw, lambda: polled.append(1) or _fills("x", 9))
    assert fill["complete"] is False
    assert polled == []  # no steal without order_id

    # POST already complete: do not poll
    complete = {"order": {"order_id": "f1", "status": "executed", "fill_count": 2,
                          "remaining_count": 0, "yes_price": 80, "count": 2}}
    fill, payload = ftl.resolve_kalshi_fill(complete, lambda: polled.append(1) or _fills("f1"))
    assert fill["complete"] is True and polled == [] and payload is None


def test_sports_poll_upgrades_ack():
    orig, path = _ledger()
    orig_pos, orig_co, orig_gf = kalshi_auth.get_positions, se.create_order, kalshi_auth.get_fills
    kalshi_auth.get_positions = lambda: {"market_positions": []}
    se.create_order = lambda *a, **k: _ack("se-poll")
    kalshi_auth.get_fills = lambda: _fills("se-poll", 5, 80)
    try:
        o0 = ftl.open_position_count()
        bet = {'fav_ticker': 'SE-POLL', 'fav_title': 'X', 'fav_price': 0.80,
               'contracts': 5, 'cost': 4.0, 'fee': 0.02, 'profit_if_win': 0.98,
               'league': 'NBA', 'exp': '2026'}
        res = se.execute_bets([bet], dry_run=False)
        assert res[0]['status'] == 'EXECUTED', res[0]
        assert ftl.open_position_count() == o0 + 1
        rec = [r for r in ftl._read() if r.get("ticker") == "SE-POLL"][0]
        assert rec["source"] == "live" and rec["fill_status"] == "FILLED", rec
    finally:
        se.create_order = orig_co
        kalshi_auth.get_positions = orig_pos
        kalshi_auth.get_fills = orig_gf
        _restore(orig, path)


def test_allocator_and_executor_poll():
    orig, path = _ledger()
    orig_plan, orig_mom = ma.build_execution_plan, ma.MakerOrderManager
    orig_bl, orig_clr = ma._is_kalshi_blacklisted, ma._clear_kalshi_failure
    orig_intel = sys.modules.get("intel_layer")
    fake = types.ModuleType("intel_layer")

    class _I:
        def get_kalshi_conviction(self, *a, **k):
            return {"recommended_side": "yes", "confidence": 0.2, "conviction_yes": 50}

    fake.get_intel = lambda: _I()
    sys.modules["intel_layer"] = fake
    ma.build_execution_plan = None
    ma.MakerOrderManager = None
    ma._is_kalshi_blacklisted = lambda t: False
    ma._clear_kalshi_failure = lambda t: None
    orig_ka_co, orig_ka_pos, orig_ka_gf = (
        kalshi_auth.create_order, kalshi_auth.get_positions, kalshi_auth.get_fills)
    orig_ke_co, orig_ke_gf = ke.create_order, ke.get_fills
    kalshi_auth.get_positions = lambda: {"market_positions": []}
    try:
        kalshi_auth.create_order = lambda *a, **k: _ack("alloc-poll")
        kalshi_auth.get_fills = lambda: _fills("alloc-poll", 5, 80)
        alloc = {
            "platform": "kalshi", "type": "SPORTS", "ticker": "ALLOC-POLL",
            "title": "t", "side": "yes", "price": 0.80, "allocation": 4.0,
            "expected_profit": 0.5, "hours_to_resolve": 6, "raw": {},
        }
        o0 = ftl.open_position_count()
        out = ma.execute_allocations([alloc], dry_run=False)
        assert out[0]["status"] == "EXECUTED", out[0]
        assert ftl.open_position_count() == o0 + 1

        ke.create_order = lambda **k: _ack("ke-poll")
        ke.get_fills = lambda: _fills("ke-poll", 2, 80)
        state = {"max_per_trade": 10.0, "max_deployable": 10.0, "balance": 100.0}
        opp = {"ticker": "KE-POLL", "strategy": "SPORTS", "side": "yes",
               "price": 0.80, "edge": 0.05, "score": 50, "title": "t"}
        result = ke.execute_trade(opp, state, dry_run=False)
        assert result["status"] == "EXECUTED", result
        assert result.get("fills_payload")
        eid = ftl.book_kalshi_fill(
            "SPORTS", result["ticker"], result["side"], result.get("order") or {},
            intended_source="live", fallback_price=0.80, fallback_contracts=result["contracts"],
            fills_payload=result.get("fills_payload"))
        rec = [r for r in ftl._read() if r.get("id") == eid][0]
        assert rec["fill_status"] == "FILLED" and rec["source"] == "live", rec
    finally:
        ma.build_execution_plan = orig_plan
        ma.MakerOrderManager = orig_mom
        ma._is_kalshi_blacklisted = orig_bl
        ma._clear_kalshi_failure = orig_clr
        kalshi_auth.create_order = orig_ka_co
        kalshi_auth.get_positions = orig_ka_pos
        kalshi_auth.get_fills = orig_ka_gf
        ke.create_order = orig_ke_co
        ke.get_fills = orig_ke_gf
        if orig_intel is not None:
            sys.modules["intel_layer"] = orig_intel
        else:
            sys.modules.pop("intel_layer", None)
        _restore(orig, path)


def test_hygiene_and_version():
    se_src = open(os.path.join(HERE, "sports_engine.py")).read()
    ma_src = open(os.path.join(HERE, "master_allocator.py")).read()
    ke_src = open(os.path.join(HERE, "kalshi_executor.py")).read()
    ftl_src = open(os.path.join(HERE, "forward_test_ledger.py")).read()
    assert "resolve_kalshi_fill" in se_src
    assert "resolve_kalshi_fill" in ma_src
    assert "resolve_kalshi_fill" in ke_src
    assert "def resolve_kalshi_fill" in ftl_src
    assert "fills_payload=payload" in se_src
    assert "fills_payload=payload" in ma_src
    assert "fills_payload=result.get(\"fills_payload\")" in ke_src
    assert "MIROFISH_AUTO_RAISE" not in se_src
    assert "--live" not in ftl_src
    assert vc.VALEDIA_VERSION == "0.40.0", vc.VALEDIA_VERSION
    assert vc.MIROFISH_AUTO_RAISE is False
    vc._STAMP_CACHE = None
    assert vc.version_stamp().startswith("v0.40.0 ")


if __name__ == "__main__":
    test_resolve_poll_rules()
    test_sports_poll_upgrades_ack()
    test_allocator_and_executor_poll()
    test_hygiene_and_version()
    print("H3 kalshi fill-true GET poll: PASS")
