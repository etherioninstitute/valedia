#!/usr/bin/env python3
"""Slice 13 — MiroFish accuracy closed loop (fee-true, weight capped).

No network, no keys, no --live, no nautilus_trader.
Run: python3 test_slice13.py
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import intel_opportunity_gatherer as iog
import mirofish_accuracy_ledger as mal
import unified_brain as ub
import valedia_config as vc

NOW = datetime(2026, 9, 17, 19, 0, tzinfo=timezone.utc)
HOME_MODELS = os.path.expanduser("~/.openclaw/workspace/models")


def _restore():
    vc.NAUTILUS_ENABLED = False
    vc.NAUTILUS_LIVE = False
    vc.NAUTILUS_SHADOW_COINBASE = True
    vc.INTEL_SHADOW_INTENTS = True
    vc.VENUE_WRITER["coinbase"] = "valedia"
    vc.VENUE_WRITER["kalshi"] = "valedia"
    vc.VENUE_WRITER["alpaca"] = "valedia"
    vc.VENUE_WRITER["hyperliquid"] = "none"
    vc.VENUE_WRITER["polymarket"] = "none"
    vc.MIROFISH_WEIGHT = 0.10
    vc.FUSION_WEIGHTS["mirofish"] = vc.MIROFISH_WEIGHT
    vc.MIROFISH_UNPROVEN_CAP = 0.10
    vc.MIROFISH_PROVEN_CAP = 0.15
    vc.MIROFISH_ACCURACY_BAR = 0.55
    vc.MIROFISH_MIN_SCORED = 20
    vc.MIROFISH_AUTO_RAISE = False
    vc.VALEDIA_SELF_ADJUST = False
    vc.MIROFISH_BAND_FLOOR = 0.05
    vc.MIROFISH_BAND_TOP = 0.25
    vc.MIROFISH_HARD_CEILING = 0.30
    vc.MIROFISH_ENRICH_BONUS = 0.02
    os.environ.pop("SPOT_RECONCILE_HTTP", None)
    os.environ.pop("REBALANCER_AUTOEXEC", None)
    os.environ.pop("MIROFISH_PRICE_HTTP", None)
    os.environ.pop("VALEDIA_SELF_ADJUST", None)
    mal.set_paths(models_dir=HOME_MODELS)


def _flags():
    return {
        "NAUTILUS_ENABLED": vc.NAUTILUS_ENABLED,
        "NAUTILUS_LIVE": vc.NAUTILUS_LIVE,
        "SPOT_RECONCILE_HTTP": vc.spot_reconcile_http_enabled(),
        "REBALANCER_AUTOEXEC": vc.rebalancer_autoexec_enabled(),
        "VENUE_WRITER": dict(vc.VENUE_WRITER),
        "MIROFISH_AUTO_RAISE": vc.MIROFISH_AUTO_RAISE,
    }


def _tmpdir():
    return tempfile.mkdtemp(prefix="slice13_")


def _write(path, obj):
    with open(path, "w") as f:
        json.dump(obj, f)


def _jsonl(path, rows):
    with open(path, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


def _iso(dt):
    return dt.isoformat()


def _bind(md):
    mal.set_paths(models_dir=md)


def _pred(pid, asset, direction, ts, due, score=70):
    return {
        "type": "prediction", "pred_id": pid, "ts": ts, "source": "mirofish_swarm",
        "asset": asset, "predicted_direction": direction, "score": score,
        "horizon_days": 5, "score_after": due,
    }


def _ftl_entry(eid, ticker, side, source="live", ts=None):
    return {
        "type": "entry", "id": eid, "ticker": ticker, "side": side,
        "source": source, "venue": "coinbase", "entry_price": 100.0,
        "contracts": 1.0, "entry_fee": 1.1, "ts": ts or _iso(NOW),
        "fill_status": "FILLED",
    }


def _ftl_sett(eid, ticker, net, ts=None, source="live"):
    return {
        "type": "settlement", "entry_id": eid, "ticker": ticker,
        "realized_pnl_net": net, "fee": 1.1, "won": net > 0,
        "source": source, "ts": ts or _iso(NOW + timedelta(days=1)),
        "logged_at": ts or _iso(NOW + timedelta(days=1)),
    }


def _fee_true_rows(n, hits, asset="BTC-USD"):
    """n scored fee-true results with `hits` hits. Unproven unless n/bar met."""
    rows = []
    ts = _iso(NOW - timedelta(days=10))
    due = _iso(NOW - timedelta(days=1))
    for i in range(n):
        pid = f"mf_seed_{i}_{asset}"
        hit = i < hits
        rows.append(_pred(pid, asset, "BUY", ts, due))
        rows.append({
            "type": "result", "pred_id": pid, "asset": asset,
            "predicted_direction": "BUY", "realized": "UP" if hit else "DOWN",
            "hit": hit, "fee_true": True, "realized_pnl_net": 1.0 if hit else -1.0,
            "source": "forward_test_ledger", "scored_at": _iso(NOW),
        })
    return rows


def test_fence_and_hygiene():
    _restore()
    before = _flags()
    src = open(os.path.join(HERE, "mirofish_accuracy_ledger.py")).read()
    assert "import yfinance" not in src
    assert "import requests" not in src
    assert "--live" not in src
    assert "VENUE_WRITER" not in src
    assert "REBALANCER_AUTOEXEC" not in src
    iog_src = open(os.path.join(HERE, "intel_opportunity_gatherer.py")).read()
    assert "effective_mirofish_weight" in iog_src or "_mf_weight" in iog_src
    vc_src = open(os.path.join(HERE, "valedia_config.py")).read()
    assert "effective_mirofish_weight" in vc_src
    assert "MIROFISH_AUTO_RAISE" in vc_src
    assert vc.MIROFISH_AUTO_RAISE is False
    assert vc.MIROFISH_UNPROVEN_CAP == 0.10
    assert vc.MIROFISH_PROVEN_CAP == 0.15
    assert vc.MIROFISH_ACCURACY_BAR == 0.55
    assert vc.SPOT_RECONCILE_HTTP is False
    assert vc.REBALANCER_AUTOEXEC is False
    assert _flags() == before


def test_record_skips_lightweight_and_hold():
    _restore()
    md = _tmpdir()
    _bind(md)
    _write(os.path.join(md, "mirofish_signal.json"), {
        "source": "mirofish_lightweight", "ts": _iso(NOW),
        "signals": {"BTC-USD": {"direction": "BUY", "score": 80}},
    })
    assert mal.record() == 2
    assert mal._read() == []

    _write(os.path.join(md, "mirofish_signal.json"), {
        "source": "mirofish_swarm", "ts": _iso(NOW),
        "signals": {
            "BTC-USD": {"direction": "BUY", "score": 80},
            "ETH-USD": {"direction": "HOLD", "score": 51},
            "SOL-USD": {"direction": "SELL", "score": 20},
        },
    })
    assert mal.record() == 0
    preds = [r for r in mal._read() if r["type"] == "prediction"]
    assets = {p["asset"] for p in preds}
    assert assets == {"BTC-USD", "SOL-USD"}
    _restore()


def test_score_fee_true_from_ftl_excludes_paper():
    _restore()
    md = _tmpdir()
    _bind(md)
    ts = _iso(NOW - timedelta(days=6))
    due = _iso(NOW - timedelta(days=1))
    later = _iso(NOW - timedelta(days=2))  # inside [pred_ts, score_after]
    _jsonl(os.path.join(md, "mirofish_accuracy_ledger.jsonl"), [
        _pred("mf_btc", "BTC-USD", "BUY", ts, due),
        _pred("mf_eth", "ETH-USD", "BUY", ts, due),
        _pred("mf_sol", "SOL", "SELL", ts, due),
        _pred("mf_future", "NVDA", "BUY", ts, _iso(NOW + timedelta(days=5))),
    ])
    _jsonl(os.path.join(md, "forward_test_ledger.jsonl"), [
        _ftl_entry("e1", "BTC-USD", "buy", "live", later),
        _ftl_sett("e1", "BTC-USD", 2.5, later),  # buy +net => UP, hit
        _ftl_entry("e2", "ETH-USD", "buy", "paper", later),
        _ftl_sett("e2", "ETH-USD", 9.9, later, source="paper"),  # excluded
        _ftl_entry("e3", "SOL-USD", "sell", "live", later),
        _ftl_sett("e3", "SOL-USD", 1.2, later),  # sell +net => DOWN, SELL hit
    ])
    mal.score(now=NOW)
    results = [r for r in mal._read() if r["type"] == "result"]
    by = {r["pred_id"]: r for r in results}
    assert by["mf_btc"]["hit"] is True and by["mf_btc"]["fee_true"] is True
    assert by["mf_btc"]["realized"] == "UP"
    assert "mf_eth" not in by  # paper
    assert by["mf_sol"]["hit"] is True and by["mf_sol"]["realized"] == "DOWN"
    assert "mf_future" not in by  # horizon not reached
    _restore()


def test_buy_negative_net_is_miss():
    _restore()
    md = _tmpdir()
    _bind(md)
    ts = _iso(NOW - timedelta(days=6))
    due = _iso(NOW - timedelta(days=1))
    later = _iso(NOW - timedelta(days=2))
    _jsonl(os.path.join(md, "mirofish_accuracy_ledger.jsonl"), [
        _pred("mf_btc", "BTC-USD", "UP", ts, due),
    ])
    _jsonl(os.path.join(md, "forward_test_ledger.jsonl"), [
        _ftl_entry("e1", "BTC-USD", "buy", "live", later),
        _ftl_sett("e1", "BTC-USD", -0.40, later),  # fee-true loss => DOWN
    ])
    mal.score(now=NOW)
    r = [x for x in mal._read() if x["type"] == "result"][0]
    assert r["hit"] is False and r["realized"] == "DOWN"
    assert r["realized_pnl_net"] == -0.40
    _restore()


def test_empty_and_thin_not_proven_weight_capped():
    _restore()
    md = _tmpdir()
    _bind(md)
    st = mal.accuracy_stats([])
    assert st["proven"] is False
    assert st["n_scored"] == 0
    assert st["weight"] == 0.10
    assert mal.effective_weight(st) == 0.10

    _jsonl(os.path.join(md, "mirofish_accuracy_ledger.jsonl"),
           _fee_true_rows(5, 5))  # 100% but n=5
    st = mal.accuracy_stats()
    assert st["directional_accuracy"] == 1.0
    assert st["proven"] is False
    vc.MIROFISH_WEIGHT = 0.20
    assert mal.effective_weight() == 0.10  # unproven cap
    assert vc.effective_mirofish_weight() == 0.10
    _restore()


def test_legacy_yfinance_rows_do_not_prove():
    _restore()
    md = _tmpdir()
    _bind(md)
    rows = []
    for i in range(25):
        rows.append(_pred(f"leg_{i}", "BTC-USD", "BUY", _iso(NOW), _iso(NOW)))
        rows.append({
            "type": "result", "pred_id": f"leg_{i}", "hit": True,
            "realized": "UP",  # no fee_true
        })
    _jsonl(os.path.join(md, "mirofish_accuracy_ledger.jsonl"), rows)
    st = mal.accuracy_stats()
    assert st["n_scored"] == 0
    assert st["legacy_n"] == 25
    assert st["proven"] is False
    assert st["weight"] == 0.10
    _restore()


def test_proven_does_not_auto_raise():
    _restore()
    md = _tmpdir()
    _bind(md)
    _jsonl(os.path.join(md, "mirofish_accuracy_ledger.jsonl"),
           _fee_true_rows(20, 12))  # 60% > 55%, n=20
    st = mal.accuracy_stats()
    assert st["n_scored"] == 20
    assert st["hits"] == 12
    assert st["directional_accuracy"] == 0.6
    assert st["proven"] is True
    vc.MIROFISH_WEIGHT = 0.20
    vc.MIROFISH_AUTO_RAISE = False
    assert mal.effective_weight() == 0.10
    assert vc.fusion_weights_for("equity")["mirofish"] == 0.10

    vc.MIROFISH_AUTO_RAISE = True
    w = mal.effective_weight()
    assert w == 0.15  # proven cap, not the 0.20 configured
    fw = vc.fusion_weights_for("crypto")
    assert fw["mirofish"] == 0.15
    s = fw["kernel"] + fw["mirofish"] + fw["ml"] + fw["intel"] + fw["remainder"]
    assert abs(s - 1.0) < 1e-9
    _restore()


def test_bar_is_strict_gt_and_burton_configurable():
    _restore()
    md = _tmpdir()
    _bind(md)
    _jsonl(os.path.join(md, "mirofish_accuracy_ledger.jsonl"),
           _fee_true_rows(20, 11))  # 55% exactly
    st = mal.accuracy_stats()
    assert st["directional_accuracy"] == 0.55
    assert st["proven"] is False  # strict >

    vc.MIROFISH_ACCURACY_BAR = 0.50
    st = mal.accuracy_stats()
    assert st["proven"] is True
    _restore()


def test_fusion_stage_unchanged_at_default_cap():
    _restore()
    ere = {"multiplier": 1.0}
    intel = {"fg_signal": 0}
    hi = ub.stage_fusion({"score": 100}, ere, intel, 50, 50, "equity")
    assert hi == 67.5, hi
    we = vc.fusion_weights_for("equity")
    assert we["mirofish"] == 0.10
    assert abs(sum(we.values()) - 1.0) < 1e-9


def test_intel_blend_uses_capped_weight_and_reads_snapshot():
    _restore()
    md = _tmpdir()
    _bind(md)
    vc.MIROFISH_WEIGHT = 0.20  # would drift blend if uncapped
    snap = {
        "n_predictions": 20, "n_scored": 20, "hits": 12,
        "directional_accuracy": 0.6, "proven": True,
        "bar": 0.55, "min_scored": 20, "weight": 0.10,
        "auto_raise": False, "source": "forward_test_ledger",
        "fee_true_net": 4.0, "legacy_n": 0,
    }
    _write(os.path.join(md, "mirofish_accuracy_summary.json"), snap)
    _write(os.path.join(md, "mirofish_signal.json"), {
        "source": "mirofish_swarm", "status": "active",
        "signals": {"BTC-USD": {"score": 100.0, "direction": "BUY"}},
    })
    _write(os.path.join(md, "latest_fusion.json"), {
        "results": [{"ticker": "BTC-USD", "composite_score": 80.0, "signal": "BUY"}],
    })
    _write(os.path.join(md, "latest_etherion_risk.json"), {"risk_level": "NORMAL"})

    loaded = mal.read_summary(md)
    assert loaded["proven"] is True
    assert loaded["directional_accuracy"] == 0.6

    opps = iog.collect_mirofish(
        {"signals": {"BTC-USD": {"score": 100.0, "direction": "BUY"}}},
        accuracy=loaded,
    )
    assert opps[0]["raw"]["mirofish_proven"] is True
    assert opps[0]["raw"]["mirofish_accuracy"] == 0.6
    assert opps[0]["raw"]["mirofish_weight"] == 0.10  # AUTO_RAISE false

    merged = iog.merge_opps(
        iog.collect_fusion({"results": [
            {"ticker": "BTC-USD", "composite_score": 80.0, "signal": "BUY"}]})
        + opps
    )
    btc = [o for o in merged if o["instrument"] == "BTC-USD"][0]
    expected = round(80.0 * 0.90 + 100.0 * 0.10, 2)
    assert btc["score"] == expected, (btc["score"], expected)
    assert btc["source_feed"] == "fusion+mirofish"

    gathered = iog.gather(models_dir=md)
    gbtc = [o for o in gathered if o["instrument"] == "BTC-USD"][0]
    assert gbtc["source_feed"] == "fusion+mirofish"
    _restore()


def test_isolated_models_dir_does_not_read_home_ledger():
    _restore()
    md = _tmpdir()
    st = mal.read_summary(md)
    assert st["n_scored"] == 0
    assert st["proven"] is False
    assert st["weight"] == 0.10
    _restore()


def test_score_does_not_flip_live_flags():
    _restore()
    before = _flags()
    md = _tmpdir()
    _bind(md)
    mal.score(now=NOW)
    mal.record({"source": "mirofish_swarm", "ts": _iso(NOW), "signals": {}})
    mal.summary()
    assert _flags() == before
    assert vc.venue_writer("coinbase") == "valedia"
    _restore()


def run():
    _restore()
    try:
        test_fence_and_hygiene()
        test_record_skips_lightweight_and_hold()
        test_score_fee_true_from_ftl_excludes_paper()
        test_buy_negative_net_is_miss()
        test_empty_and_thin_not_proven_weight_capped()
        test_legacy_yfinance_rows_do_not_prove()
        test_proven_does_not_auto_raise()
        test_bar_is_strict_gt_and_burton_configurable()
        test_fusion_stage_unchanged_at_default_cap()
        test_intel_blend_uses_capped_weight_and_reads_snapshot()
        test_isolated_models_dir_does_not_read_home_ledger()
        test_score_does_not_flip_live_flags()
    finally:
        _restore()
    print("slice13 checks: PASS")


if __name__ == "__main__":
    run()
