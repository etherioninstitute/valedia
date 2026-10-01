#!/usr/bin/env python3
"""Slice 12 — shadow parity harness (7d mockable window, fail-closed).

No network, no keys, no --live, no nautilus_trader.
Run: python3 test_slice12.py
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Unit tests assert git-default OFF without Burton's ~/.openclaw/.env flips.
os.environ["OPENCLAW_SKIP_ENV"] = "1"

import forward_test_ledger as ftl
import intel_opportunity_gatherer as iog
import nautilus_shadow_coinbase as sh
import nautilus_shadow_parity as sp
import nautilus_shadow_venues as sv
import valedia_config as vc

NOW = datetime(2026, 9, 17, 18, 0, tzinfo=timezone.utc)


def _restore():
    vc.NAUTILUS_ENABLED = False
    vc.NAUTILUS_LIVE = False
    vc.NAUTILUS_SHADOW_COINBASE = True
    vc.NAUTILUS_SHADOW_VENUES = True
    vc.INTEL_SHADOW_INTENTS = True
    vc.VENUE_WRITER["coinbase"] = "valedia"
    vc.VENUE_WRITER["kalshi"] = "valedia"
    vc.VENUE_WRITER["alpaca"] = "valedia"
    vc.VENUE_WRITER["hyperliquid"] = "none"
    vc.VENUE_WRITER["polymarket"] = "none"
    os.environ.pop("SPOT_RECONCILE_HTTP", None)
    os.environ.pop("REBALANCER_AUTOEXEC", None)


def _queue():
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    return path


def _gates(**over):
    g = {
        "ere_level": "NORMAL",
        "fee_ok": True,
        "category_ok": True,
        "blacklist_ok": True,
        "exec_dry_run": False,
        "live_flag": False,
    }
    g.update(over)
    return g


def _iso(dt):
    return dt.isoformat()


def _stamp(rec, ts):
    rec = dict(rec)
    rec["ts"] = ts if isinstance(ts, str) else ts.isoformat()
    rec["enqueued_at"] = rec["ts"]
    return rec


def _flags():
    return {
        "NAUTILUS_ENABLED": vc.NAUTILUS_ENABLED,
        "NAUTILUS_LIVE": vc.NAUTILUS_LIVE,
        "SPOT_RECONCILE_HTTP": vc.spot_reconcile_http_enabled(),
        "REBALANCER_AUTOEXEC": vc.rebalancer_autoexec_enabled(),
        "VENUE_WRITER": dict(vc.VENUE_WRITER),
    }


def _assert_fence(before=None):
    now = _flags()
    if before is not None:
        assert now == before, (before, now)
    assert vc.NAUTILUS_ENABLED is False
    assert vc.NAUTILUS_LIVE is False
    assert vc.SPOT_RECONCILE_HTTP is False
    assert vc.REBALANCER_AUTOEXEC is False
    assert vc.VENUE_WRITER["coinbase"] == "valedia"
    assert vc.VENUE_WRITER["hyperliquid"] == "none"
    assert vc.VENUE_WRITER["polymarket"] == "none"
    assert vc.nautilus_may_dispatch("coinbase") is False


def _emit_brain(qpath, **kw):
    return sh.emit_brain_shadow("buy", "BTC-USD", usd_amount=25.0,
                                queue_path=qpath, gates=_gates(), **kw)


def _emit_hl(qpath, **kw):
    c = {"coin": "BTC", "side": "sell", "qty": 0.001, "bound_px": 100000.0,
         "reduce_only": False, "strategy": "HL_SHORT"}
    c.update(kw)
    return sv.emit_hl_short_shadow(c, queue_path=qpath, gates=_gates())


def _emit_pm(qpath, **kw):
    c = {"token_id": "tok-yes-1", "side": "buy", "price": 0.40, "notional": 5.0,
         "kalshi_ticker": "KXBTCD-ARB", "strategy": "POLYMARKET_ARB"}
    c.update(kw)
    return sv.emit_polymarket_arb_shadow(c, queue_path=qpath, gates=_gates())


def _emit_intel_cb(qpath):
    orig_stats = ftl.category_stats
    orig_ov = ftl._overrides
    orig_pause = ftl._learning_pause_until
    ftl.category_stats = lambda lookback_days=None: {}
    ftl._overrides = lambda: {}
    ftl._learning_pause_until = lambda *a, **k: None
    md = tempfile.mkdtemp(prefix="slice12_")
    with open(os.path.join(md, "latest_etherion_risk.json"), "w") as f:
        json.dump({"risk_level": "NORMAL"}, f)
    try:
        recs = iog.emit_shadow_intents(models_dir=md, queue_path=qpath, opps=[{
            "kind": "crypto_listed", "strategy": "CRYPTO_BRACKET",
            "venue": "coinbase", "instrument": "ETH-USD", "side": "buy",
            "score": 80, "edge_pct": 15.0, "price": None, "notional": 5.0,
            "source_feed": "fusion", "raw": {},
        }])
        return recs[0]
    finally:
        ftl.category_stats = orig_stats
        ftl._overrides = orig_ov
        ftl._learning_pause_until = orig_pause


def _emit_intel_alpaca(qpath):
    orig_stats = ftl.category_stats
    orig_ov = ftl._overrides
    orig_pause = ftl._learning_pause_until
    ftl.category_stats = lambda lookback_days=None: {}
    ftl._overrides = lambda: {}
    ftl._learning_pause_until = lambda *a, **k: None
    md = tempfile.mkdtemp(prefix="slice12a_")
    with open(os.path.join(md, "latest_etherion_risk.json"), "w") as f:
        json.dump({"risk_level": "NORMAL"}, f)
    try:
        recs = iog.emit_shadow_intents(models_dir=md, queue_path=qpath, opps=[{
            "kind": "stock", "strategy": "STOCK_BUY", "venue": "alpaca",
            "instrument": "SPY", "side": "buy", "score": 80, "edge_pct": 5.0,
            "price": None, "notional": 5.0, "source_feed": "mirofish", "raw": {},
        }])
        return recs[0]
    finally:
        ftl.category_stats = orig_stats
        ftl._overrides = orig_ov
        ftl._learning_pause_until = orig_pause


def test_fence_and_hygiene():
    _restore()
    src = open(os.path.join(HERE, "nautilus_shadow_parity.py")).read()
    assert "import requests" not in src
    assert "import nautilus_trader" not in src
    assert "--live" not in src
    assert "VENUE_WRITER[" not in src
    assert "SPOT_RECONCILE_HTTP =" not in src
    assert "REBALANCER_AUTOEXEC =" not in src
    assert "NAUTILUS_ENABLED =" not in src
    assert "NAUTILUS_LIVE =" not in src
    assert "LIVE_CLIENT_HOOK" not in src or "live_client_hook" in src
    assert vc.VENUE_NT_CUTOVER_ORDER[0] == "coinbase"
    win = sp.parse_window("7d")
    assert win == timedelta(days=7)
    assert sp.parse_window("7 days") == timedelta(days=7)
    assert sp.parse_window(7) == timedelta(days=7)
    try:
        sp.parse_window("7h")
        raise AssertionError("unknown window must fail closed")
    except ValueError:
        pass
    try:
        sp.parse_window("0d")
        raise AssertionError("zero window must fail closed")
    except ValueError:
        pass
    _assert_fence()


def test_window_filters_7d():
    _restore()
    qpath = _queue()
    try:
        rec = _emit_brain(qpath)
        inside = _stamp(rec, NOW - timedelta(days=6, hours=12))
        edge = _stamp(rec, NOW - timedelta(days=7))
        outside = _stamp(rec, NOW - timedelta(days=7, seconds=1))
        future = _stamp(rec, NOW + timedelta(seconds=1))
        before = _flags()
        rep = sp.report(rows=[inside, edge, outside, future], window="7d", now=NOW)
        assert rep["n_rows"] == 4
        assert rep["n_in_window"] == 2, rep
        assert rep["ok"] is True, rep["dangerous"]
        assert rep["cutover_ready"]["coinbase"] is False
        _assert_fence(before)
        assert rep["flags"]["NAUTILUS_ENABLED"] is False
        assert rep["flags"]["VENUE_WRITER"]["coinbase"] == "valedia"
    finally:
        os.remove(qpath)


def test_parity_ok_four_streams():
    _restore()
    qpath = _queue()
    try:
        brain = _stamp(_emit_brain(qpath), NOW)
        hl = _stamp(_emit_hl(qpath), NOW)
        pm = _stamp(_emit_pm(qpath), NOW)
        intel = _stamp(_emit_intel_cb(qpath), NOW)
        before = _flags()
        outp = os.path.join(tempfile.mkdtemp(prefix="s12r_"), "parity.json")
        rep = sp.report(rows=[brain, hl, pm, intel], window="7d", now=NOW, out_path=outp)
        assert rep["ok"] is True, rep["dangerous"]
        assert rep["n_in_window"] == 4
        assert rep["buckets"]["brain_coinbase"]["n"] == 1
        assert rep["buckets"]["brain_coinbase"]["compare_ok"] == 1
        assert rep["buckets"]["hl_short"]["compare_ok"] == 1
        assert rep["buckets"]["polymarket_arb"]["compare_ok"] == 1
        assert rep["buckets"]["intel_coinbase"]["compare_ok"] == 1
        assert rep["cutover_ready"] == {
            "coinbase": False, "hyperliquid": False, "polymarket": False,
        }
        disk = json.load(open(outp))
        assert disk["ok"] is True
        _assert_fence(before)
    finally:
        os.remove(qpath)


def test_allocator_ignored_not_cutover_evidence():
    _restore()
    qpath = _queue()
    try:
        alloc = sh.emit_allocator_shadow({
            "platform": "coinbase", "type": "CRYPTO_BUY", "ticker": "BTC-USD",
            "side": "buy", "price": 1.0, "allocation": 10.0, "expected_profit": 0.5,
        }, queue_path=qpath, gates=_gates())
        alloc = _stamp(alloc, NOW)
        brain = _stamp(_emit_brain(qpath), NOW)
        # Allocator canonical vs allocator_post is dirty (stripped ticker) —
        # must not fail the report. Fence still scanned.
        assert alloc.get("allocator_post_compare", {}).get("ok") is False
        before = _flags()
        rep = sp.report(rows=[alloc, brain], window="7d", now=NOW)
        assert sp.classify(alloc) == "allocator_coinbase"
        assert rep["ignored"].get("allocator_coinbase") == 1
        assert "allocator_coinbase" not in sp.EVIDENCE
        assert rep["ok"] is True, rep["dangerous"]
        assert rep["buckets"]["brain_coinbase"]["compare_ok"] == 1
        _assert_fence(before)
    finally:
        os.remove(qpath)


def test_fail_closed_instrument_and_side():
    _restore()
    qpath = _queue()
    try:
        rec = _stamp(_emit_brain(qpath), NOW)
        bad = dict(rec)
        post = dict(bad["canonical_post"])
        post["product_id"] = "ETH-USD"
        post["side"] = "SELL"
        bad["canonical_post"] = post
        before = _flags()
        rep = sp.report(rows=[bad], window="7d", now=NOW)
        assert rep["ok"] is False, rep
        cats = {d["category"] for d in rep["dangerous"]}
        assert "instrument" in cats, rep["dangerous"]
        assert "side" in cats, rep["dangerous"]
        assert rep["cutover_ready"]["coinbase"] is False
        _assert_fence(before)
    finally:
        os.remove(qpath)


def test_fail_closed_size_and_stale_compare_ok():
    _restore()
    qpath = _queue()
    try:
        rec = _stamp(_emit_brain(qpath), NOW)
        assert rec.get("compare", {}).get("ok") is True
        bad = dict(rec)
        post = dict(bad["canonical_post"])
        ioc = dict(post["order_configuration"]["market_market_ioc"])
        ioc["quote_size"] = "99.00"  # would overbet at cutover
        post["order_configuration"] = {"market_market_ioc": ioc}
        bad["canonical_post"] = post
        before = _flags()
        rep = sp.report(rows=[bad], window="7d", now=NOW)
        assert rep["ok"] is False
        cats = {d["category"] for d in rep["dangerous"]}
        assert "size" in cats, rep["dangerous"]
        assert "stale_compare_ok" in cats, rep["dangerous"]
        _assert_fence(before)
    finally:
        os.remove(qpath)


def test_fail_closed_hl_leverage_and_pm_token():
    _restore()
    qpath = _queue()
    try:
        hl = _stamp(_emit_hl(qpath), NOW)
        hl_bad = dict(hl)
        hl_bad["leverage"] = 5
        pm = _stamp(_emit_pm(qpath), NOW)
        pm_bad = dict(pm)
        post = dict(pm_bad["canonical_post"])
        post["tokenID"] = "other-token"
        pm_bad["canonical_post"] = post
        before = _flags()
        rep = sp.report(rows=[hl_bad, pm_bad], window="7d", now=NOW)
        assert rep["ok"] is False
        cats = {d["category"] for d in rep["dangerous"]}
        assert "leverage" in cats, rep["dangerous"]
        assert "instrument" in cats, rep["dangerous"]
        _assert_fence(before)
    finally:
        os.remove(qpath)


def test_fail_closed_dispatchable_live_flag_signed():
    _restore()
    qpath = _queue()
    try:
        rec = _stamp(_emit_brain(qpath), NOW)
        live = dict(rec)
        live["dispatchable"] = True
        live["gates"] = dict(live.get("gates") or {}, live_flag=True)
        post = dict(live.get("canonical_post") or {})
        post["signed"] = True
        live["canonical_post"] = post
        live["live_client_hook"] = "bound"
        before = _flags()
        rep = sp.report(rows=[live], window="7d", now=NOW)
        assert rep["ok"] is False
        cats = {d["category"] for d in rep["dangerous"]}
        assert "dispatchable" in cats
        assert "live_flag" in cats
        assert "signed" in cats
        assert "live_client_hook" in cats
        _assert_fence(before)
    finally:
        os.remove(qpath)


def test_fail_closed_queue_corrupt_and_writer_nautilus():
    _restore()
    qpath = _queue()
    try:
        rec = _stamp(_emit_hl(qpath), NOW)
        rec["venue_writer"] = "nautilus"
        bad_path = _queue()
        with open(bad_path, "w") as f:
            f.write("{not json\n")
            f.write(json.dumps(rec) + "\n")
        before = _flags()
        rep = sp.report(queue_path=bad_path, window="7d", now=NOW)
        cats = {d["category"] for d in rep["dangerous"]}
        assert "queue_corrupt" in cats, rep["dangerous"]
        assert "writer_nautilus" in cats, rep["dangerous"]
        assert rep["ok"] is False
        _assert_fence(before)
        os.remove(bad_path)
    finally:
        os.remove(qpath)


def test_intel_alpaca_not_cutover_fail():
    """Alpaca stays Valedia; IOC vs DAY is not a Coinbase/HL/PM cutover fuse."""
    _restore()
    qpath = _queue()
    try:
        alp = _stamp(_emit_intel_alpaca(qpath), NOW)
        brain = _stamp(_emit_brain(qpath), NOW)
        assert sp.classify(alp) == "intel_alpaca"
        assert "intel_alpaca" not in sp.EVIDENCE
        before = _flags()
        rep = sp.report(rows=[alp, brain], window="7d", now=NOW)
        assert rep["ok"] is True, rep["dangerous"]
        assert rep["ignored"].get("intel_alpaca") == 1
        _assert_fence(before)
    finally:
        os.remove(qpath)


def test_seven_calendar_days_ready_does_not_flip_flags():
    _restore()
    qpath = _queue()
    try:
        rec = _emit_brain(qpath)
        rows = [_stamp(rec, NOW - timedelta(days=i)) for i in range(7)]
        before = _flags()
        rep = sp.report(rows=rows, window="7d", now=NOW)
        assert rep["ok"] is True, rep["dangerous"]
        assert rep["buckets"]["brain_coinbase"]["n"] == 7
        assert rep["buckets"]["brain_coinbase"]["compare_ok"] == 7
        assert rep["buckets"]["brain_coinbase"]["days_ok"] == 7
        assert rep["cutover_ready"]["coinbase"] is True
        assert rep["cutover_ready"]["hyperliquid"] is False
        # Ready is evidence only. This lane never flips the writer.
        _assert_fence(before)
        assert vc.VENUE_WRITER["coinbase"] != "nautilus"
        assert vc.nautilus_may_dispatch("coinbase") is False
    finally:
        os.remove(qpath)


def test_report_does_not_flip_even_if_nt_flags_already_on():
    _restore()
    vc.NAUTILUS_ENABLED = True
    vc.NAUTILUS_LIVE = True
    vc.VENUE_WRITER["coinbase"] = "nautilus"
    qpath = _queue()
    try:
        rec = _stamp(_emit_brain(qpath), NOW)
        before = _flags()
        assert before["NAUTILUS_ENABLED"] is True
        rep = sp.report(rows=[rec], window="7d", now=NOW)
        after = _flags()
        assert after == before
        assert rep["flags"]["NAUTILUS_ENABLED"] is True
        assert rep["flags"]["VENUE_WRITER"]["coinbase"] == "nautilus"
        # Harness itself did not clear or raise flags.
        assert "flag_mutation" not in {d["category"] for d in rep["dangerous"]}
    finally:
        os.remove(qpath)
        _restore()
    _assert_fence()


def test_empty_window_ok_not_ready():
    _restore()
    before = _flags()
    rep = sp.report(rows=[], window="7d", now=NOW)
    assert rep["ok"] is True
    assert rep["n_in_window"] == 0
    assert rep["cutover_ready"]["coinbase"] is False
    _assert_fence(before)


def run():
    _restore()
    try:
        test_fence_and_hygiene()
        test_window_filters_7d()
        test_parity_ok_four_streams()
        test_allocator_ignored_not_cutover_evidence()
        test_fail_closed_instrument_and_side()
        test_fail_closed_size_and_stale_compare_ok()
        test_fail_closed_hl_leverage_and_pm_token()
        test_fail_closed_dispatchable_live_flag_signed()
        test_fail_closed_queue_corrupt_and_writer_nautilus()
        test_intel_alpaca_not_cutover_fail()
        test_seven_calendar_days_ready_does_not_flip_flags()
        test_report_does_not_flip_even_if_nt_flags_already_on()
        test_empty_window_ok_not_ready()
    finally:
        _restore()
    print("slice12 checks: PASS")


if __name__ == "__main__":
    run()
