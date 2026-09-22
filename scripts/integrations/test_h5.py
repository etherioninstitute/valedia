#!/usr/bin/env python3
"""H5 — hypergrok desk labels + HL public /info. No HL live.

No network, no keys, no --live, no Exchange, no VENUE_WRITER flip.
Run: python3 test_h5.py
"""
import json
import os
import sys
import tempfile
from decimal import Decimal

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import desk_roles as dr
import hl_public_info as hpi
import intel_opportunity_gatherer as iog
import nautilus_shadow_venues as sv
import valedia_config as vc


def _restore():
    vc.NAUTILUS_ENABLED = False
    vc.NAUTILUS_LIVE = False
    vc.NAUTILUS_SHADOW_VENUES = True
    vc.INTEL_SHADOW_INTENTS = True
    vc.VENUE_WRITER["hyperliquid"] = "none"
    vc.VENUE_WRITER["coinbase"] = "valedia"
    vc.VENUE_WRITER["kalshi"] = "valedia"
    os.environ.pop("HL_PUBLIC_HTTP", None)
    os.environ.pop("SPOT_RECONCILE_HTTP", None)
    hpi.set_post_fn(None)


def _meta_book(coin="BTC", funding="0.00012", mark="100000",
               bid="99990", ask="100010"):
    meta = [
        {"universe": [{"name": coin}]},
        [{
            "markPx": mark, "oraclePx": mark, "prevDayPx": "99000",
            "funding": funding, "openInterest": "10", "dayNtlVlm": "1e9",
        }],
    ]
    book = {
        "time": 1726665600000,
        "levels": [
            [{"px": bid, "sz": "2"}, {"px": "99950", "sz": "5"}],
            [{"px": ask, "sz": "2"}, {"px": "100050", "sz": "5"}],
        ],
    }
    return meta, book


def test_hygiene():
    _restore()
    for name in ("desk_roles.py", "hl_public_info.py"):
        src = open(os.path.join(HERE, name)).read()
        assert "from hyperliquid" not in src
        assert "import Exchange" not in src
        assert "Exchange" not in src or "No Exchange" in src
        assert "create_order" not in src
        assert "place_market_order" not in src
        assert "--live" not in src
        assert "import requests" not in src
        assert "VENUE_WRITER[" not in src
        assert "NAUTILUS_LIVE =" not in src
    assert "Galleon Labs" in open(os.path.join(HERE, "hl_public_info.py")).read()
    assert "Galleon Labs" in open(os.path.join(HERE, "desk_roles.py")).read()
    assert vc.VENUE_WRITER["hyperliquid"] == "none"
    assert vc.NAUTILUS_LIVE is False
    assert vc.SPOT_RECONCILE_HTTP is False
    assert vc.MIROFISH_AUTO_RAISE is False
    assert hpi.http_enabled() is False
    assert vc.VALEDIA_VERSION == "0.55.0", vc.VALEDIA_VERSION
    vc._STAMP_CACHE = None
    assert vc.version_stamp().startswith("v0.55.0 ")


def test_desk_stages_and_ticket():
    _restore()
    assert dr.stage_from_gates({}) == "research"
    assert dr.stage_from_gates({"fee_ok": False, "ere_level": "NORMAL"}) == "risk"
    assert dr.stage_from_gates(
        {"fee_ok": True, "ere_level": "NORMAL", "ere_ok": True},
        venue="hyperliquid") == "risk"
    assert dr.stage_from_gates(
        {"fee_ok": True, "ere_level": "NORMAL", "ere_ok": True},
        venue="coinbase") == "exec"
    assert dr.stage_from_gates(
        {"fee_ok": True, "ere_level": "HALT"}, venue="coinbase") == "risk"
    rec = {"intent_id": "abcd1234-xxxx", "instrument": "BTC", "side": "sell",
           "qty": 0.001, "ts": "2026-09-18T00:00:00+00:00",
           "desk_stage": "risk"}
    t = dr.ticket_from_record(rec, extra={"source_feed": "hl_public"})
    assert t["hg_id"] == "HG-abcd1234"
    assert t["stop"] is None
    assert t["send"] is False
    assert t["sources"] == ["hl_public"]
    assert dr.parse_approve("yes") is None
    assert dr.parse_approve("approve HG-abcd1234") == "abcd1234"
    assert dr.parse_approve("please approve HG-deadbeef now") == "deadbeef"


def test_hl_snapshot_injected_no_http():
    _restore()
    meta, book = _meta_book()
    calls = []

    def boom(payload):
        calls.append(payload)
        raise AssertionError("HTTP must not run")

    hpi.set_post_fn(boom)
    snap = hpi.build_snapshot(meta, book, "BTC")
    assert calls == []
    assert snap["mode"] == "read-only"
    assert snap["coin"] == "BTC"
    assert "Galleon Labs" in snap["source"]["attribution"]
    assert "/exchange" not in json.dumps(snap).lower() or "No /exchange" in snap["safety"]
    assert Decimal(snap["book"]["spread_bps"]) > 0
    assert "5" in snap["book"]["depth"]
    assert snap["valedia_version"] == "0.55.0"
    md = tempfile.mkdtemp(prefix="h5_hl_")
    path = hpi.write_snapshot(snap, models_dir=md)
    assert os.path.basename(path) == "hl_public_info.json"
    loaded = hpi.load_snapshot(md)
    assert loaded["coin"] == "BTC"
    hpi.set_post_fn(None)
    try:
        hpi.fetch_snapshot("BTC")
        raise AssertionError("fetch must refuse HTTP-off")
    except RuntimeError as e:
        assert "HTTP off" in str(e)
    assert calls == []


def test_gatherer_hl_public_and_shadow_labels():
    _restore()
    md = tempfile.mkdtemp(prefix="h5_g_")
    meta, book = _meta_book(funding="0.0002")
    snap = hpi.build_snapshot(meta, book, "BTC")
    hpi.write_snapshot(snap, models_dir=md)
    opps = iog.collect_hl_public(hpi.load_snapshot(md))
    assert len(opps) == 1
    assert opps[0]["source_feed"] == "hl_public"
    assert opps[0]["venue"] == "hyperliquid"
    assert opps[0]["side"] == "sell"
    gathered = iog.gather(models_dir=md)
    assert any(o["source_feed"] == "hl_public" for o in gathered)
    q = os.path.join(md, "q.jsonl")
    recs = iog.emit_shadow_intents(models_dir=md, queue_path=q, opps=opps)
    assert recs
    assert recs[0]["dispatchable"] is False
    assert recs[0]["desk_stage"] == "risk"
    assert recs[0]["source_feed"] == "hl_public"
    assert vc.VENUE_WRITER["hyperliquid"] == "none"

    q2 = os.path.join(md, "hl.jsonl")
    rec = sv.emit_hl_short_shadow(
        {"coin": "BTC", "qty": 0.001, "bound_px": 100000.0}, queue_path=q2)
    assert rec["dispatchable"] is False
    assert rec["desk_stage"] == "risk"
    assert rec["venue_writer"] == "none"
    assert rec.get("leverage") == 0


if __name__ == "__main__":
    test_hygiene()
    test_desk_stages_and_ticket()
    test_hl_snapshot_injected_no_http()
    test_gatherer_hl_public_and_shadow_labels()
    print("H5 desk labels + HL public info: PASS")
