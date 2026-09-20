#!/usr/bin/env python3
"""POLY-E — Polymarket Oscars/entertainment path. Shadow only.

No network, no keys, no --live, no VENUE_WRITER flip, no LIVE_CLIENT_HOOK.
Run: python3 test_poly_e.py
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)

import forward_test_ledger as ftl
import intel_opportunity_gatherer as iog
import nautilus_shadow_venues as sv
import polymarket_entertainment as pe
import polymarket_order_body as pmob
import valedia_config as vc


def _restore():
    vc.NAUTILUS_ENABLED = False
    vc.NAUTILUS_LIVE = False
    vc.NAUTILUS_SHADOW_VENUES = True
    vc.INTEL_SHADOW_INTENTS = True
    vc.VENUE_WRITER["polymarket"] = "none"
    vc.VENUE_WRITER["kalshi"] = "valedia"
    vc.MIROFISH_AUTO_RAISE = False
    vc.VALEDIA_SELF_ADJUST = False
    os.environ.pop("SPOT_RECONCILE_HTTP", None)
    os.environ.pop("FORTRESS_INTEL_FALLBACK", None)
    vc._STAMP_CACHE = None


def _oscar():
    return {
        "question": "Will Anora win Best Picture at the Oscars?",
        "slug": "oscars-2026-best-picture-anora",
        "tags": [{"slug": "oscars"}, {"slug": "awards"}],
        "category": "Entertainment",
        "tokens": [
            {"token_id": "tok-oscar-yes", "outcome": "Yes", "price": 0.42},
            {"token_id": "tok-oscar-no", "outcome": "No", "price": 0.58},
        ],
        "volume24hr": 250000,
    }


def _nba():
    return {
        "question": "Lakers vs Celtics NBA winner?",
        "slug": "nba-lakers-celtics",
        "tags": [{"slug": "sports"}, {"slug": "nba"}],
        "tokens": [{"token_id": "tok-nba", "outcome": "Yes", "price": 0.55}],
    }


def _btc():
    return {
        "question": "Bitcoin above 100k by Friday?",
        "slug": "btc-100k",
        "tags": [{"slug": "crypto"}],
        "tokens": [{"token_id": "tok-btc", "outcome": "Yes", "price": 0.40}],
    }


def test_hygiene_and_fence():
    _restore()
    src = open(os.path.join(HERE, "polymarket_entertainment.py")).read()
    for banned in (
        "import requests", "py_clob_client", "VENUE_WRITER[",
        "LIVE_CLIENT_HOOK =", "NAUTILUS_LIVE =", "create_order",
        "place_market_order", "--live", "MIROFISH_AUTO_RAISE =",
    ):
        assert banned not in src, banned
    assert "import nautilus_trader" not in open(
        os.path.join(HERE, "nautilus_shadow_venues.py")).read()
    assert pmob.LIVE_CLIENT_HOOK is None
    assert vc.VENUE_WRITER["polymarket"] == "none"
    assert vc.nautilus_may_dispatch("polymarket") is False
    assert vc.NAUTILUS_LIVE is False
    assert vc.SPOT_RECONCILE_HTTP is False
    assert vc.MIROFISH_AUTO_RAISE is False
    assert vc.VALEDIA_VERSION == "0.45.0", vc.VALEDIA_VERSION
    cron = open(os.path.join(ROOT, "scripts", "install_cron.sh")).read()
    assert "polymarket_entertainment" not in cron
    cut = open(os.path.join(ROOT, "recommendations",
                            "POLYMARKET-ENTERTAINMENT-CUTOVER.md")).read()
    assert "do not flip" in cut.lower() or "Do not flip" in cut
    assert "VENUE_WRITER" in cut


def test_classify_and_category():
    _restore()
    osc = pe.classify_market(_oscar())
    assert osc["is_entertainment"] is True
    assert osc["category"] == "ENTERTAINMENT"
    assert "entertainment" in osc["tags"]
    assert "oscars" in osc["tags"]
    assert pe.classify_market(_nba())["category"] == "SPORTS"
    assert pe.classify_market(_nba())["is_entertainment"] is False
    assert pe.classify_market(_btc())["category"] == "CRYPTO"
    pol = pe.classify_market({
        "question": "Republican nominee 2028?", "tags": [{"slug": "politics"}],
        "tokens": [{"token_id": "t", "outcome": "Yes", "price": 0.33}]})
    assert pol["category"] == "POLITICS"
    wx = pe.classify_market({
        "question": "Hurricane landfall in Florida?", "tags": [{"slug": "weather"}],
        "tokens": [{"token_id": "t", "outcome": "Yes", "price": 0.22}]})
    assert wx["category"] == "WEATHER"
    assert ftl.canonical_category("POLYMARKET_ENTERTAINMENT") == "ENTERTAINMENT"
    assert ftl.canonical_category("OSCARS") == "ENTERTAINMENT"
    assert ftl.canonical_category("POLYMARKET_POLITICS") == "POLITICS"
    assert ftl.canonical_category("POLYMARKET_WEATHER") == "WEATHER"
    c = pe.to_candidate(_oscar())
    assert c["token_id"] == "tok-oscar-yes"
    assert c["kind"] == "polymarket_entertainment"
    nba_c = pe.to_candidate(_nba())
    assert nba_c["kind"] == "polymarket_sports"
    assert nba_c["category"] == "SPORTS"


def test_gatherer_and_shadow():
    _restore()
    md = tempfile.mkdtemp(prefix="poly_e_")
    pe.write_scan([_oscar(), _nba(), _btc()], models_dir=md)
    opps = iog.collect_pm_entertainment(
        iog._load(os.path.join(md, "polymarket_entertainment.json")))
    by_kind = {o["kind"]: o for o in opps}
    assert "polymarket_entertainment" in by_kind
    assert by_kind["polymarket_entertainment"]["venue"] == "polymarket"
    assert "entertainment" in by_kind["polymarket_entertainment"]["tags"]
    assert by_kind["polymarket_sports"]["strategy"] == "POLYMARKET_SPORTS"
    assert by_kind["polymarket_crypto"]["strategy"] == "POLYMARKET_CRYPTO"
    gathered = iog.gather(models_dir=md)
    assert any(o["source_feed"] == "pm_entertainment" for o in gathered)
    q = os.path.join(md, "q.jsonl")
    recs = iog.emit_shadow_intents(
        models_dir=md, queue_path=q,
        opps=[by_kind["polymarket_entertainment"]])
    assert recs
    assert recs[0]["dispatchable"] is False
    assert recs[0]["desk_stage"] == "risk"
    assert recs[0]["opportunity_tags"] == opps[0]["tags"]
    q2 = os.path.join(md, "pm.jsonl")
    rec = sv.emit_polymarket_entertainment_shadow(
        {"token_id": "tok-oscar-yes", "side": "buy", "price": 0.42, "notional": 5.0},
        queue_path=q2)
    assert rec["dispatchable"] is False
    assert rec["kind"] == "polymarket_entertainment"
    assert rec["arb_candidate"] is False
    assert rec["venue_writer"] == "none"
    assert rec["live_client_hook"] is None
    assert rec["pm_send"]["sent"] is False
    assert rec["canonical_post"]["signed"] is False
    assert rec["desk_stage"] == "risk"
    seed = pe.apply_seed_text("# brief", models_dir=md)
    assert "entertainment" in seed.lower()
    try:
        pe.fetch_markets()
        raise AssertionError("HTTP must stay off")
    except RuntimeError as e:
        assert "HTTP off" in str(e)


def test_fill_true_post_ack_not_filled():
    _restore()
    ack = {"orderID": "pm-1", "status": "matched", "takingAmount": "12.0"}
    fill, payload = ftl.resolve_polymarket_fill(ack, None)
    assert fill["complete"] is False and payload is None

    polled = []
    fill, payload = ftl.resolve_polymarket_fill(
        ack, lambda: polled.append(1) or {
            "trades": [{"id": "t1", "taker_order_id": "pm-1",
                        "size": 12.0, "price": 0.42}]})
    assert fill["complete"] is True and fill["qty"] == 12.0
    assert fill["order_id"] == "pm-1"
    assert polled == [1]

    fill, payload = ftl.resolve_polymarket_fill(
        ack, lambda: {"trades": [{"id": "t9", "taker_order_id": "other",
                                  "size": 99, "price": 0.9}]})
    assert fill["complete"] is False  # do not steal

    polled.clear()
    fill, payload = ftl.resolve_polymarket_fill(
        {"success": True}, lambda: polled.append(1) or {
            "trades": [{"id": "t1", "taker_order_id": "pm-1", "size": 9}]})
    assert fill["complete"] is False
    assert polled == []

    def boom():
        raise RuntimeError("no network")
    fill, payload = ftl.resolve_polymarket_fill(ack, boom)
    assert fill["complete"] is False and payload is None

    fd, path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    orig = ftl.LEDGER
    ftl.LEDGER = path
    try:
        eid = ftl.book_polymarket_fill(
            "POLYMARKET_ENTERTAINMENT", "tok-oscar-yes", "buy", ack,
            intended_source="live", fallback_price=0.42, fallback_size=5)
        rec = [r for r in ftl._read() if r.get("id") == eid][0]
        assert rec["fill_status"] == "UNRECONCILED"
        assert rec["source"] == "unreconciled"
        assert rec["strategy"] == "ENTERTAINMENT"
        eid2 = ftl.book_polymarket_fill(
            "POLYMARKET_ENTERTAINMENT", "tok-oscar-yes", "buy", ack,
            intended_source="live", fallback_price=0.42,
            trades_payload={"trades": [
                {"id": "t1", "taker_order_id": "pm-1", "size": 5, "price": 0.42}]})
        rec2 = [r for r in ftl._read() if r.get("id") == eid2][0]
        assert rec2["fill_status"] == "FILLED" and rec2["source"] == "live"
    finally:
        ftl.LEDGER = orig
        try:
            os.remove(path)
        except OSError:
            pass


if __name__ == "__main__":
    test_hygiene_and_fence()
    test_classify_and_category()
    test_gatherer_and_shadow()
    test_fill_true_post_ack_not_filled()
    print("POLY-E entertainment path: PASS")
