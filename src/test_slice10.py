#!/usr/bin/env python3
"""Slice 10 — MiroFish/intel → opportunities → gated never-dispatchable Intents.

No network, no keys, no --live, no nautilus_trader.
Run: python3 test_slice10.py
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
import nautilus_intent as ni
import pnl_tracker as pt
import valedia_config as vc
# ERE v3: hermetic — valid gate state in a temp models dir (never the live one)
import ere_test_fixture
vc._MODELS = tempfile.mkdtemp(prefix="slice10_ere_")
ere_test_fixture.write_state(vc._MODELS)

NOW = datetime(2026, 9, 17, 18, 0, tzinfo=timezone.utc)


def _restore_flags():
    vc.NAUTILUS_ENABLED = False
    vc.NAUTILUS_LIVE = False
    vc.VENUE_WRITER["coinbase"] = "valedia"
    vc.VENUE_WRITER["kalshi"] = "valedia"
    vc.VENUE_WRITER["alpaca"] = "valedia"
    vc.INTEL_SHADOW_INTENTS = True
    os.environ.pop("SPOT_RECONCILE_HTTP", None)
    os.environ.pop("REBALANCER_AUTOEXEC", None)


def _tmpdir():
    return tempfile.mkdtemp(prefix="slice10_")


def _write(md, name, obj):
    path = os.path.join(md, name)
    with open(path, "w") as f:
        json.dump(obj, f)
    return path


def _fresh_ts():
    return datetime.now(timezone.utc).isoformat()


def _models(md, files=None):
    for name, obj in (files or {}).items():
        _write(md, name, obj)
    # ERE v3: canonical gate state so _ere_level reads a healthy book
    import ere_test_fixture
    ere_test_fixture.write_state(md)
    return md


def test_fence_and_hygiene():
    _restore_flags()
    assert vc.INTEL_SHADOW_INTENTS is True
    assert vc.NAUTILUS_ENABLED is False
    assert vc.NAUTILUS_LIVE is False
    assert vc.SPOT_RECONCILE_HTTP is False
    assert vc.REBALANCER_AUTOEXEC is False
    assert vc.VENUE_WRITER["coinbase"] == "valedia"
    assert vc.VENUE_WRITER["kalshi"] == "valedia"
    assert vc.venue_writer("coinbase") != "nautilus"
    src = open(os.path.join(HERE, "intel_opportunity_gatherer.py")).read()
    assert "import requests" not in src
    assert "record_entry(" not in src
    assert "--live" not in src
    assert "SPOT_RECONCILE_HTTP" not in src
    assert "VENUE_WRITER" not in src
    assert "REBALANCER_AUTOEXEC" not in src
    ma = open(os.path.join(HERE, "master_allocator.py")).read()
    assert "from intel_opportunity_gatherer import emit_shadow_intents" in ma
    assert "never-dispatchable" in ma
    # Intel opps are not mixed into the live gather list.
    gather_src = ma.split("def gather_all_opportunities", 1)[1].split("def rank_opportunities", 1)[0]
    assert "emit_shadow_intents" not in gather_src


def test_mirofish_listed_and_new_listing_and_stock():
    mf = {
        "source": "mirofish_swarm",
        "signals": {
            "BTC-USD": {"direction": "BUY", "score": 72.0, "confidence": 0.6},
            "ETH-USD": {"direction": "HOLD", "score": 51.0},
            "ZEC": {"direction": "BUY", "score": 80.0},
            "NVDA": {"direction": "BUY", "score": 70.0},
            "SOL-USD": {"direction": "SELL", "score": 20.0},
        },
    }
    opps = iog.collect_mirofish(mf)
    kinds = {(o["instrument"], o["kind"], o["side"]) for o in opps}
    assert ("BTC-USD", "crypto_listed", "buy") in kinds
    assert ("ZEC-USD", "crypto_new_listing", "buy") in kinds
    assert ("NVDA", "stock", "buy") in kinds
    assert ("SOL-USD", "crypto_listed", "sell") in kinds
    assert not any(o["instrument"] == "ETH-USD" for o in opps)
    assert all(o["source_feed"] == "mirofish" for o in opps)
    assert all(o["venue"] == "coinbase" or o["venue"] == "alpaca" for o in opps)


def test_fusion_both_shapes_and_mirofish_weight_blend():
    fusion_list = {"results": [
        {"ticker": "BTC-USD", "composite_score": 80.0, "signal": "BUY"},
        {"ticker": "AAPL", "composite_score": 70.0, "signal": "BUY"},
    ]}
    fusion_map = {"signals": {"ETH-USD": {"fused_score": 20.0}}}
    a = iog.collect_fusion(fusion_list)
    b = iog.collect_fusion(fusion_map)
    assert any(o["instrument"] == "BTC-USD" and o["kind"] == "crypto_listed" for o in a)
    assert any(o["instrument"] == "AAPL" and o["kind"] == "stock" for o in a)
    assert any(o["instrument"] == "ETH-USD" and o["side"] == "sell" for o in b)

    merged = iog.merge_opps(
        iog.collect_fusion(fusion_list) + iog.collect_mirofish({
            "signals": {"BTC-USD": {"score": 100.0, "direction": "BUY"}}
        })
    )
    btc = [o for o in merged if o["instrument"] == "BTC-USD"][0]
    w = vc.MIROFISH_WEIGHT
    expected = round(80.0 * (1 - w) + 100.0 * w, 2)
    assert btc["score"] == expected, (btc["score"], expected)
    assert btc["source_feed"] == "fusion+mirofish"
    assert w == 0.10


def test_hot_list_new_listing_and_hedge_and_scanner_and_arb():
    hot = {"assets": {
        "NEAR": {"ticker": "NEAR", "class": "crypto", "platform": "coinbase",
                 "buzz_score": 114, "cg_id": "near"},
        "BTC": {"ticker": "BTC-USD", "class": "crypto", "buzz_score": 90},
        "quiet": {"ticker": "XYZ", "buzz_score": 10},
    }}
    hot_opps = iog.collect_hot_list(hot)
    assert any(o["instrument"] == "NEAR-USD" and o["kind"] == "crypto_new_listing"
               for o in hot_opps)
    assert not any(o["instrument"] == "BTC-USD" for o in hot_opps)

    hedge = {
        "short_squeeze": [{"ticker": "MARA", "short_pct": 32.6}],
        "sentiment_divergence": [{"ticker": "PYPL", "change52w": -21.5, "pe": 9.1}],
    }
    hopps = iog.collect_hedge(hedge)
    tickers = {o["instrument"] for o in hopps}
    assert "MARA" in tickers and "PYPL" in tickers
    assert all(o["kind"] == "stock" and o["venue"] == "alpaca" for o in hopps)

    stale = {"ts": "2020-01-01T00:00:00+00:00", "macro": [
        {"ticker": "KXOLD", "edge_score": 90, "yes_ask": 40, "mid": 60, "title": "x"}
    ]}
    assert iog.collect_scanner(stale) == []
    fresh = {"ts": _fresh_ts(), "macro": [
        {"ticker": "KXTEST-YES", "title": "Will X", "edge_score": 72,
         "yes_ask": 55, "mid": 54},
        {"ticker": "KXLOW", "title": "nope", "edge_score": 10, "yes_ask": 50, "mid": 50},
    ]}
    scan = iog.collect_scanner(fresh)
    assert len(scan) == 1 and scan[0]["kind"] == "kalshi"
    assert scan[0]["venue"] == "kalshi" and scan[0]["instrument"] == "KXTEST-YES"

    old_arb = {"trades": [{"ticker": "KXBTCD-OLD", "side": "no", "price_cents": 51,
                           "edge_cents": 48, "ts": "2026-04-12T01:43:30+00:00"}]}
    assert iog.collect_arb(old_arb) == []
    arb = {"trades": [{"ticker": "KXBTCD-NOW", "side": "no", "price_cents": 51,
                       "edge_cents": 8, "ts": _fresh_ts(), "status": "DRY_RUN"}]}
    aopps = iog.collect_arb(arb)
    assert len(aopps) == 1 and aopps[0]["kind"] == "arb"
    assert aopps[0]["strategy"] == "LATENCY_ARB"


def test_fee_category_pause_gates_and_never_dispatchable():
    _restore_flags()
    md = _tmpdir()
    _models(md)
    qpath = os.path.join(md, "q.jsonl")
    bl = os.path.join(md, "bl.json")
    orig_bl = pt.STRATEGY_BLACKLIST_PATH
    orig_pause = ftl._learning_pause_until
    orig_stats = ftl.category_stats
    orig_ov = ftl._overrides
    pt.STRATEGY_BLACKLIST_PATH = bl
    json.dump({"paused": [{"strategy": "NEW_LISTING",
                           "until": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
                           "reason": "net"}]}, open(bl, "w"))
    ftl._overrides = lambda: {}
    ftl.category_stats = lambda lookback_days=None: {
        "CRYPTO_BRACKET": {"n": 20, "wins": 4, "net": -12.0},
    }
    try:
        opps = [
            {"kind": "crypto_listed", "strategy": "CRYPTO_BRACKET", "venue": "coinbase",
             "instrument": "BTC-USD", "side": "buy", "score": 80, "edge_pct": 8.0,
             "price": None, "notional": 5.0, "source_feed": "mirofish", "raw": {}},
            {"kind": "crypto_new_listing", "strategy": "NEW_LISTING", "venue": "coinbase",
             "instrument": "ZEC-USD", "side": "buy", "score": 80, "edge_pct": 8.0,
             "price": None, "notional": 5.0, "source_feed": "hot_list", "raw": {}},
            {"kind": "crypto_listed", "strategy": "CRYPTO_BRACKET", "venue": "coinbase",
             "instrument": "ETH-USD", "side": "buy", "score": 80, "edge_pct": 0.2,
             "price": None, "notional": 5.0, "source_feed": "fusion", "raw": {}},
            {"kind": "arb", "strategy": "LATENCY_ARB", "venue": "kalshi",
             "instrument": "KXBTCD-NOW", "side": "sell", "score": 70, "edge_pct": 10.0,
             "price": 0.51, "notional": 5.0, "source_feed": "arb", "raw": {}},
            {"kind": "stock", "strategy": "STOCK_BUY", "venue": "alpaca",
             "instrument": "NVDA", "side": "buy", "score": 70, "edge_pct": 3.0,
             "price": None, "notional": 5.0, "source_feed": "mirofish", "raw": {}},
            {"kind": "kalshi", "strategy": "SCANNER_MACRO", "venue": "kalshi",
             "instrument": "KXTEST-YES", "side": "buy", "score": 72, "edge_pct": 7.2,
             "price": 0.55, "notional": 5.0, "source_feed": "scanner", "raw": {}},
        ]
        recs = iog.emit_shadow_intents(models_dir=md, queue_path=qpath, opps=opps)
        assert len(recs) == 6
        by_inst = {r["instrument"]: r for r in recs}
        # Category net-loss → refused (fee-true gate).
        assert by_inst["BTC-USD"]["status"] == "refused"
        assert "category" in by_inst["BTC-USD"]["validate_reason"] or \
               "gate" in by_inst["BTC-USD"]["validate_reason"]
        # Learning pause → refused.
        assert by_inst["ZEC-USD"]["status"] == "refused"
        # Tiny edge vs Coinbase round-trip (~2.2%) → fee_ok false → refused.
        assert by_inst["ETH-USD"]["status"] == "refused"
        assert "fee" in by_inst["ETH-USD"]["validate_reason"]
        # Passing stock/kalshi/arb: dry_run (exec_dry_run) + shadow, never dispatchable.
        for inst in ("NVDA", "KXTEST-YES", "KXBTCD-NOW"):
            r = by_inst[inst]
            assert r["dispatchable"] is False, r
            assert r["status"] in ("dry_run", "held", "refused")
            assert r["ledger_source"] == "paper"
            assert r.get("shadow") is True
            assert r.get("live_flag") is not True
            assert r["gates"]["live_flag"] is False
            assert r["gates"]["exec_dry_run"] is True
        assert by_inst["KXBTCD-NOW"].get("arb_candidate") is True
        assert all(r["dispatchable"] is False for r in recs)

        # Even if NT flags flip, these Intents stay non-dispatchable.
        vc.NAUTILUS_ENABLED = True
        vc.NAUTILUS_LIVE = True
        vc.VENUE_WRITER["alpaca"] = "nautilus"
        rec2 = iog.emit_shadow_intents(
            models_dir=md, queue_path=qpath,
            opps=[o for o in opps if o["instrument"] == "NVDA"])
        assert rec2 and rec2[0]["dispatchable"] is False
        d = ni.validate_intent(iog.opp_to_intent(opps[4], rec2[0]["gates"]), dispatch=True)
        assert d.dispatchable is False
        assert d.status == "dry_run"
    finally:
        pt.STRATEGY_BLACKLIST_PATH = orig_bl
        ftl._learning_pause_until = orig_pause
        ftl.category_stats = orig_stats
        ftl._overrides = orig_ov
        _restore_flags()


def test_ere_halt_refuses():
    _restore_flags()
    md = _tmpdir()
    _models(md)
    # ERE v3 HALT: valid state, multiplier 0 (canonical gate; legacy file retired)
    import ere_test_fixture
    ere_test_fixture.write_state(md, multiplier=0.0)
    qpath = os.path.join(md, "q.jsonl")
    orig_stats = ftl.category_stats
    orig_ov = ftl._overrides
    orig_pause = ftl._learning_pause_until
    ftl.category_stats = lambda lookback_days=None: {}
    ftl._overrides = lambda: {}
    ftl._learning_pause_until = lambda *a, **k: None
    try:
        recs = iog.emit_shadow_intents(models_dir=md, queue_path=qpath, opps=[{
            "kind": "stock", "strategy": "STOCK_BUY", "venue": "alpaca",
            "instrument": "SPY", "side": "buy", "score": 80, "edge_pct": 5.0,
            "price": None, "notional": 5.0, "source_feed": "mirofish", "raw": {},
        }])
        assert recs[0]["dispatchable"] is False
        assert recs[0]["status"] == "refused"
        assert "ere" in recs[0]["validate_reason"].lower()
    finally:
        ftl.category_stats = orig_stats
        ftl._overrides = orig_ov
        ftl._learning_pause_until = orig_pause


def test_paper_sandbox_backfill_shadow_cannot_lift_kelly():
    """Intel/paper/sandbox/backfill/shadow settlements must not open the proven gate."""
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    orig = ftl.LEDGER
    ftl.LEDGER = path
    try:
        for src in ("paper", "sandbox", "nautilus_sandbox", "backfill:pnl_tracker",
                    "intel_shadow", "shadow"):
            eid = ftl.record_entry(
                "CRYPTO_BRACKET", f"T-{src}", "buy", 100.0, 1.0,
                venue="coinbase", source=src, fill_status="FILLED")
            ftl._append({
                "type": "settlement", "entry_id": eid, "ticker": f"T-{src}",
                "strategy": "CRYPTO_BRACKET", "result": "WON",
                "filled_contracts": 1, "realized_pnl_net": 50.0, "won": True,
                "source": src,
            })
        tr = ftl.real_track_record()
        assert tr["n"] == 0, tr
        assert tr["net"] == 0.0
        stats = ftl.category_stats()
        assert "CRYPTO_BRACKET" not in stats or stats.get("CRYPTO_BRACKET", {}).get("n", 0) == 0
        assert vc.category_track_record_mult("CRYPTO_BRACKET") == vc.KELLY_UNPROVEN_MULT
        assert vc._track_record_mult() == vc.KELLY_UNPROVEN_MULT
        assert ftl._is_non_real_source("paper")
        assert ftl._is_non_real_source("intel_shadow")
        assert ftl._is_non_real_source("shadow")
        assert ftl._is_non_real_source("backfill:pnl_tracker")
        assert not ftl._is_non_real_source("live")
    finally:
        ftl.LEDGER = orig
        os.remove(path)


def test_gather_end_to_end_from_files_no_live():
    _restore_flags()
    md = _tmpdir()
    _models(md, {
        "mirofish_signal.json": {
            "signals": {
                "BTC-USD": {"direction": "BUY", "score": 78.0},
                "NVDA": {"direction": "BUY", "score": 71.0},
            },
            "source": "test",
            "ts": _fresh_ts(),
        },
        "latest_fusion.json": {"results": [
            {"ticker": "ETH-USD", "composite_score": 22.0, "signal": "SELL"},
        ]},
        "hot_list.json": {"assets": {
            "ZEC": {"ticker": "ZEC-USD", "class": "crypto", "buzz_score": 102, "cg_id": "zcash"},
        }},
        "hedge_fund_intel.json": {"short_squeeze": [{"ticker": "MARA", "short_pct": 32.6}]},
        "scanner_results.json": {"ts": _fresh_ts(), "macro": [
            {"ticker": "KXMACRO-1", "title": "edge", "edge_score": 80,
             "yes_ask": 40, "mid": 42},
        ]},
        "latency_arb_log.json": {"trades": [
            {"ticker": "KXBTCD-ARB", "side": "yes", "price_cents": 40,
             "edge_cents": 6, "ts": _fresh_ts(), "status": "DRY_RUN"},
        ]},
    })
    orig_stats = ftl.category_stats
    orig_ov = ftl._overrides
    orig_pause = ftl._learning_pause_until
    ftl.category_stats = lambda lookback_days=None: {}
    ftl._overrides = lambda: {}
    ftl._learning_pause_until = lambda *a, **k: None
    qpath = os.path.join(md, "q.jsonl")
    try:
        opps = iog.gather(models_dir=md)
        kinds = {o["kind"] for o in opps}
        assert "crypto_listed" in kinds
        assert "crypto_new_listing" in kinds
        assert "stock" in kinds
        assert "kalshi" in kinds
        assert "arb" in kinds
        recs = iog.emit_shadow_intents(models_dir=md, queue_path=qpath)
        assert recs
        assert all(r["dispatchable"] is False for r in recs)
        assert all(r["ledger_source"] == "paper" for r in recs)
        assert all(r.get("shadow") is True for r in recs)
        with open(qpath) as f:
            lines = [json.loads(l) for l in f if l.strip()]
        assert lines and all(l.get("dispatchable") is False for l in lines)
        assert any(l.get("kind") == "arb" and l.get("arb_candidate") for l in lines)
        # No ledger writes from the gatherer.
        assert "record_entry(" not in open(os.path.join(HERE, "intel_opportunity_gatherer.py")).read()
    finally:
        ftl.category_stats = orig_stats
        ftl._overrides = orig_ov
        ftl._learning_pause_until = orig_pause
        _restore_flags()


def run():
    test_fence_and_hygiene()
    test_mirofish_listed_and_new_listing_and_stock()
    test_fusion_both_shapes_and_mirofish_weight_blend()
    test_hot_list_new_listing_and_hedge_and_scanner_and_arb()
    test_fee_category_pause_gates_and_never_dispatchable()
    test_ere_halt_refuses()
    test_paper_sandbox_backfill_shadow_cannot_lift_kelly()
    test_gather_end_to_end_from_files_no_live()
    print("slice10 checks: PASS")


if __name__ == "__main__":
    run()

