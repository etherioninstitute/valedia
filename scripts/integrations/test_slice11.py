#!/usr/bin/env python3
"""Slice 11 — HL shorts + Polymarket arb never-dispatchable shadow Intents.

No network, no keys, no --live, no nautilus_trader.
Run: python3 test_slice11.py
"""
import json
import os
import tempfile

import hyperliquid_order_body as hlob
import nautilus_intent as ni
import nautilus_shadow_venues as sv
import polymarket_order_body as pmob
import valedia_config as vc

HERE = os.path.dirname(os.path.abspath(__file__))


def _restore():
    vc.NAUTILUS_ENABLED = False
    vc.NAUTILUS_LIVE = False
    vc.NAUTILUS_SHADOW_VENUES = True
    vc.VENUE_WRITER["coinbase"] = "valedia"
    vc.VENUE_WRITER["kalshi"] = "valedia"
    vc.VENUE_WRITER["alpaca"] = "valedia"
    vc.VENUE_WRITER["hyperliquid"] = "none"
    vc.VENUE_WRITER["polymarket"] = "valedia"
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


def _hl(**over):
    c = {"coin": "BTC", "side": "sell", "qty": 0.001, "bound_px": 100000.0,
         "reduce_only": False, "strategy": "HL_SHORT"}
    c.update(over)
    return c


def _pm(**over):
    c = {"token_id": "tok-yes-1", "side": "buy", "price": 0.40, "notional": 5.0,
         "kalshi_ticker": "KXBTCD-ARB", "strategy": "POLYMARKET_ARB"}
    c.update(over)
    return c


def test_fence_and_cutover_order():
    _restore()
    assert vc.NAUTILUS_ENABLED is False
    assert vc.NAUTILUS_LIVE is False
    assert vc.SPOT_RECONCILE_HTTP is False
    assert vc.REBALANCER_AUTOEXEC is False
    assert vc.NAUTILUS_SHADOW_VENUES is True
    assert vc.VENUE_WRITER["coinbase"] == "valedia"
    assert vc.VENUE_WRITER["kalshi"] == "valedia"
    assert vc.VENUE_WRITER["alpaca"] == "valedia"
    assert vc.VENUE_WRITER["hyperliquid"] == "none"
    assert vc.VENUE_WRITER["polymarket"] == "valedia"
    assert vc.venue_writer("hyperliquid") != "nautilus"
    assert vc.venue_writer("polymarket") != "nautilus"
    assert vc.nautilus_may_dispatch("hyperliquid") is False
    assert vc.nautilus_may_dispatch("polymarket") is False
    assert vc.VENUE_NT_CUTOVER_ORDER == ("coinbase", "hyperliquid", "polymarket", "kalshi")
    assert vc.VENUE_NT_CUTOVER_ORDER[0] == "coinbase"
    assert "kraken" not in vc.VENUE_NT_CUTOVER_ORDER
    assert "mercury" not in vc.VENUE_NT_CUTOVER_ORDER
    try:
        vc.assert_exclusive_writer("hyperliquid", "nautilus")
        raise AssertionError("nautilus must not own hyperliquid")
    except vc.ExclusiveWriterError:
        pass
    try:
        vc.assert_exclusive_writer("polymarket", "nautilus")
        raise AssertionError("nautilus must not own polymarket")
    except vc.ExclusiveWriterError:
        pass

    for name in ("hyperliquid_order_body.py", "polymarket_order_body.py",
                 "nautilus_shadow_venues.py"):
        src = open(os.path.join(HERE, name)).read()
        assert "import requests" not in src
        assert "import nautilus_trader" not in src
        assert "py_clob_client" not in src
        assert "hyperliquid.exchange" not in src
        assert "--live" not in src
        assert "VENUE_WRITER[" not in src
        assert "SPOT_RECONCILE_HTTP" not in src
        assert "REBALANCER_AUTOEXEC" not in src
    assert hlob.LIVE_CLIENT_HOOK is None
    assert pmob.LIVE_CLIENT_HOOK is None
    assert hlob.send_via_live_hook({}) == {"sent": False, "reason": "no live client (stub)"}
    assert pmob.send_via_live_hook({}) == {"sent": False, "reason": "no live client (stub)"}

    ma = open(os.path.join(HERE, "master_allocator.py")).read()
    assert "from nautilus_shadow_venues import emit_venue_expansion_shadows" in ma
    gather_src = ma.split("def gather_all_opportunities", 1)[1].split("def rank_opportunities", 1)[0]
    assert "emit_venue_expansion_shadows" not in gather_src
    archive = open(os.path.join(HERE, "archive", "hyperliquid_client.py")).read()
    assert "def market_overview" in archive
    assert "exchange.order" not in archive


def test_hl_short_shadow_never_dispatchable():
    _restore()
    qpath = _queue()
    try:
        rec = sv.emit_hl_short_shadow(_hl(), queue_path=qpath, gates=_gates())
        assert rec["shadow"] is True, rec
        assert rec["dispatchable"] is False, rec
        assert rec["dispatch_requested"] is False, rec
        assert rec["status"] != "dispatchable", rec
        assert rec["venue"] == "hyperliquid"
        assert rec["instrument"] == "BTC"
        assert rec["side"] == "sell"
        assert rec["order_type"] == "market"
        assert rec["tif"] == "IOC"
        assert rec["ledger_source"] != "live"
        assert rec["gates"]["live_flag"] is False
        assert rec.get("qty") == 0.001
        assert rec.get("kind") == "hl_short"
        assert rec.get("venue_writer") == "none"
        assert rec.get("leverage") == 0
        assert rec.get("live_client_hook") is None
        assert rec.get("hl_send", {}).get("sent") is False
        assert rec.get("compare", {}).get("ok") is True, rec.get("compare")
        post = rec.get("canonical_post")
        assert post["path"] == "POST /exchange"
        assert post["signed"] is False
        assert post["leverage"] == 0
        order = post["action"]["orders"][0]
        assert order["b"] is False
        assert order["a"] == "BTC"
        assert order["s"] == "0.001"
        assert order["r"] is False
        assert order["t"] == {"limit": {"tif": "Ioc"}}
        assert order["c"].startswith("0x") and len(order["c"]) == 34
        with open(qpath) as f:
            line = f.read().strip()
        assert '"shadow": true' in line.lower().replace("True", "true")
        assert '"dispatchable": true' not in line.lower().replace("True", "true")
    finally:
        os.remove(qpath)


def test_hl_post_diff_and_flatten():
    _restore()
    intent = sv.intent_from_hl_short(_hl(), gates=_gates())
    post = sv.post_body_from_hl_intent(intent, 100000.0, reduce_only=False)
    assert sv.compare_intent_to_hl_post(intent, post, reduce_only=False)["ok"] is True

    bad = dict(post)
    bad["action"] = {"type": "order", "orders": [dict(post["action"]["orders"][0], b=True)],
                     "grouping": "na"}
    cmp_bad = sv.compare_intent_to_hl_post(intent, bad, reduce_only=False)
    assert cmp_bad["ok"] is False
    assert "side/is_buy" in {m["field"] for m in cmp_bad["mismatches"]}

    usd = sv.intent_from_hl_short(_hl(coin="BTC-USD"), gates=_gates())
    assert usd.instrument == "BTC"

    qpath = _queue()
    try:
        rec = sv.emit_hl_short_shadow(
            _hl(side="buy", reduce_only=True, qty=0.002),
            queue_path=qpath, gates=_gates())
        assert rec["dispatchable"] is False
        assert rec["side"] == "buy"
        assert rec.get("reduce_only") is True
        assert rec["canonical_post"]["action"]["orders"][0]["r"] is True
        assert rec["canonical_post"]["action"]["orders"][0]["b"] is True
        assert rec.get("compare", {}).get("ok") is True, rec.get("compare")
    finally:
        os.remove(qpath)

    skipped = sv.emit_hl_short_shadow({"coin": "ETH", "side": "sell", "qty": 0.01})
    assert skipped.get("skipped") is True and skipped["dispatchable"] is False
    assert sv.emit_hl_short_shadow({"coin": "ETH", "bound_px": 1}).get("skipped") is True
    assert sv.emit_hl_short_shadow({"qty": 1, "bound_px": 1}).get("skipped") is True


def test_pm_arb_shadow_never_dispatchable():
    _restore()
    qpath = _queue()
    try:
        rec = sv.emit_polymarket_arb_shadow(_pm(), queue_path=qpath, gates=_gates())
        assert rec["shadow"] is True, rec
        assert rec["dispatchable"] is False, rec
        assert rec["dispatch_requested"] is False, rec
        assert rec["status"] != "dispatchable", rec
        assert rec["venue"] == "polymarket"
        assert rec["instrument"] == "tok-yes-1"
        assert rec["side"] == "buy"
        assert rec["tif"] == "FOK"
        assert rec["ledger_source"] != "live"
        assert rec["gates"]["live_flag"] is False
        assert rec.get("arb_candidate") is True
        assert rec.get("kind") == "polymarket_arb"
        assert rec.get("venue_writer") == "valedia"
        assert rec.get("live_client_hook") is None
        assert rec.get("pm_send", {}).get("sent") is False
        assert rec.get("kalshi_ticker") == "KXBTCD-ARB"
        assert rec.get("compare", {}).get("ok") is True, rec.get("compare")
        post = rec.get("canonical_post")
        assert post["path"] == "POST /order"
        assert post["signed"] is False
        assert post["tokenID"] == "tok-yes-1"
        assert post["side"] == "BUY"
        assert post["orderType"] == "FOK"
        assert abs(float(post["size"]) - (5.0 / 0.40)) < 1e-12
        assert post["price"] == "0.4000"
        with open(qpath) as f:
            line = f.read().strip()
        assert '"shadow": true' in line.lower().replace("True", "true")
        assert '"dispatchable": true' not in line.lower().replace("True", "true")
        assert '"arb_candidate": true' in line.lower().replace("True", "true")
    finally:
        os.remove(qpath)


def test_pm_post_diff_and_skips():
    _restore()
    intent = sv.intent_from_pm_arb(_pm(size=12.5, notional=None), gates=_gates())
    post = sv.post_body_from_pm_intent(intent)
    assert sv.compare_intent_to_pm_post(intent, post)["ok"] is True
    bad = dict(post, tokenID="other")
    cmp_bad = sv.compare_intent_to_pm_post(intent, bad)
    assert cmp_bad["ok"] is False
    assert "instrument/tokenID" in {m["field"] for m in cmp_bad["mismatches"]}

    assert sv.emit_polymarket_arb_shadow({"side": "buy", "price": 0.4, "notional": 5}
                                         ).get("skipped") is True
    assert sv.emit_polymarket_arb_shadow(_pm(price=0)).get("skipped") is True
    assert sv.emit_polymarket_arb_shadow(
        {"token_id": "t", "side": "buy", "price": 0.5}).get("skipped") is True

    from_log = sv.polymarket_candidates_from_arb_log({
        "trades": [
            {"ticker": "KXBTCD-ARB", "side": "yes", "price_cents": 40},
            {"ticker": "KXBTCD-ARB", "polymarket_token_id": "tok-2",
             "poly_price": 0.41, "poly_side": "sell", "notional": 4.1},
            {"token_id": "KXOTHER", "price": 0.5, "notional": 1},
        ]
    })
    assert len(from_log) == 1
    assert from_log[0]["token_id"] == "tok-2"
    assert from_log[0]["side"] == "sell"


def test_cannot_force_dispatch_even_with_nt_flags():
    _restore()
    qpath = _queue()
    try:
        intent = sv.intent_from_hl_short(_hl(), gates=_gates())
        forced = ni.enqueue_intent(
            intent, dispatch=True, queue_path=qpath,
            extra={"shadow": True, "dispatchable": True, "status": "dispatchable"},
        )
        assert forced["dispatchable"] is False
        assert forced["status"] != "dispatchable"
    finally:
        os.remove(qpath)

    vc.NAUTILUS_ENABLED = True
    vc.NAUTILUS_LIVE = True
    vc.VENUE_WRITER["hyperliquid"] = "nautilus"
    vc.VENUE_WRITER["polymarket"] = "nautilus"
    qpath = _queue()
    try:
        rec_hl = sv.emit_hl_short_shadow(_hl(), queue_path=qpath, gates=_gates())
        rec_pm = sv.emit_polymarket_arb_shadow(_pm(), queue_path=qpath, gates=_gates())
        assert rec_hl["dispatchable"] is False, rec_hl
        assert rec_pm["dispatchable"] is False, rec_pm
        assert rec_hl["status"] != "dispatchable"
        assert rec_pm["status"] != "dispatchable"
        assert rec_hl["gates"]["live_flag"] is False
        assert rec_pm["gates"]["live_flag"] is False
    finally:
        os.remove(qpath)
        _restore()
    assert vc.VENUE_WRITER["hyperliquid"] == "none"
    assert vc.VENUE_WRITER["polymarket"] == "valedia"
    assert vc.VENUE_WRITER["coinbase"] == "valedia"
    assert vc.nautilus_may_dispatch("hyperliquid") is False


def test_ere_halt_refuses_and_batch():
    _restore()
    qpath = _queue()
    try:
        rec = sv.emit_hl_short_shadow(_hl(), queue_path=qpath, gates=_gates(ere_level="HALT"))
        assert rec["dispatchable"] is False
        assert rec["status"] == "refused"
        assert "ere" in rec["validate_reason"].lower()
        rec2 = sv.emit_polymarket_arb_shadow(
            _pm(), queue_path=qpath, gates=_gates(ere_level="DANGER"))
        assert rec2["status"] == "refused"
        assert rec2["dispatchable"] is False
    finally:
        os.remove(qpath)

    md = tempfile.mkdtemp(prefix="slice11_")
    with open(os.path.join(md, "hl_short_candidates.json"), "w") as f:
        json.dump({"candidates": [_hl(coin="ETH", qty=0.02, bound_px=4000)]}, f)
    with open(os.path.join(md, "latency_arb_log.json"), "w") as f:
        json.dump({"trades": [
            {"ticker": "KX-1", "polymarket_token_id": "tok-batch",
             "poly_price": 0.55, "notional": 5.5, "poly_side": "buy"},
        ]}, f)
    qpath = _queue()
    try:
        recs = sv.emit_venue_expansion_shadows(models_dir=md, queue_path=qpath, gates=_gates())
        kinds = {r.get("kind") for r in recs if not r.get("skipped")}
        assert "hl_short" in kinds and "polymarket_arb" in kinds
        assert all(r["dispatchable"] is False for r in recs)
        assert all(r.get("shadow") is True or r.get("skipped") for r in recs)
    finally:
        os.remove(qpath)

    vc.NAUTILUS_SHADOW_VENUES = False
    skipped = sv.emit_hl_short_shadow(_hl())
    assert skipped.get("skipped") is True
    assert sv.emit_venue_expansion_shadows(hl_candidates=[_hl()], pm_candidates=[_pm()]) == []
    _restore()


def run():
    _restore()
    try:
        test_fence_and_cutover_order()
        test_hl_short_shadow_never_dispatchable()
        test_hl_post_diff_and_flatten()
        test_pm_arb_shadow_never_dispatchable()
        test_pm_post_diff_and_skips()
        test_cannot_force_dispatch_even_with_nt_flags()
        test_ere_halt_refuses_and_batch()
    finally:
        _restore()
    print("slice11 checks: PASS")


if __name__ == "__main__":
    run()
