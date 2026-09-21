#!/usr/bin/env python3
"""POLY-LOOP — paper desk. Mocked. Never create_order.

Run: python3 test_poly_loop.py
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)

import poly_loop
import valedia_config as vc
from test_poly_us import _restore


NOW = datetime(2026, 9, 20, 12, tzinfo=timezone.utc)


def _end(hours=24):
    return (NOW + timedelta(hours=hours)).isoformat()


def _mkt(slug="edge-mkt", fv=0.70, liq=10000, hours=24, src="https://official.example"):
    return {
        "id": slug, "slug": slug, "question": slug.replace("-", " "),
        "fairValue": fv, "liquidity": liq, "endDate": _end(hours),
        "resolutionSource": src, "active": True,
    }


def _book(mid=0.50, depth_usd=500.0, slip=0.01):
    bid, ask = mid - slip, mid + slip
    bqty = depth_usd / bid
    aqty = depth_usd / ask
    return {"marketData": {
        "bestBid": {"value": str(bid)},
        "bestAsk": {"value": str(ask)},
        "bids": [{"px": {"value": str(bid)}, "qty": str(bqty)}],
        "offers": [{"px": {"value": str(ask)}, "qty": str(aqty)}],
    }}


def _thin(mid=0.50):
    bid, ask = mid - 0.01, mid + 0.01
    return {"marketData": {
        "bestBid": {"value": str(bid)},
        "bestAsk": {"value": str(ask)},
        "bids": [{"px": {"value": str(bid)}, "qty": "0.01"}],
        "offers": [{"px": {"value": str(ask)}, "qty": "0.01"}],
    }}


def _run(markets, books, **kw):
    d = kw.pop("out_dir", None) or tempfile.mkdtemp(prefix="poly_loop_")
    kw.setdefault("now", NOW)
    return poly_loop.run(
        markets=markets, books=books, bbos=books, out_dir=d, **kw), d


def test_hygiene_and_fence():
    _restore()
    src = open(os.path.join(HERE, "poly_loop.py")).read()
    for banned in (
        "create_order(", "cancel_order(", "VENUE_WRITER[",
        "LIVE_CLIENT_HOOK =", "NAUTILUS_LIVE =", "--live",
        'POLYMARKET_US_LIVE"] = "1"', "WEATHER_LIVE = True",
        "SCALPER_LIVE = True", "TREASURY_AUTO = True",
        "import requests", "crontab", "ollama",
    ):
        assert banned not in src, banned
    assert "POLY-LOOP" in src
    assert vc.POLYMARKET_US_LIVE is False
    assert vc.VENUE_WRITER["polymarket"] == "none"
    assert vc.WEATHER_LIVE is False
    assert vc.SCALPER_LIVE is False
    assert vc.NAUTILUS_LIVE is False
    assert vc.VALEDIA_TREASURY_AUTO is False
    assert vc.VALEDIA_VERSION == "0.52.0", vc.VALEDIA_VERSION
    assert vc.POLY_LOOP_EDGE_PTS == 8.0
    assert vc.POLY_LOOP_MAX_OPEN == 5
    assert vc.POLY_LOOP_DD_STOP == 0.40
    assert vc.POLY_LOOP_DD_STOP_ARMED is True
    cron = open(os.path.join(ROOT, "scripts", "install_cron.sh")).read()
    assert "poly_loop" not in cron
    doc = open(os.path.join(ROOT, "recommendations", "POLY-LOOP.md")).read()
    assert "POLY-LOOP" in doc
    assert doc.lstrip().startswith("# POLY-LOOP")
    assert "POLYMARKET_US_LIVE" in doc
    snap = {r["name"]: r for r in vc.controls_snapshot()["items"]}
    for n in ("POLY_LOOP_EDGE_PTS", "POLY_LOOP_MAX_OPEN",
              "POLY_LOOP_DD_STOP", "POLY_LOOP_DD_STOP_ARMED"):
        assert n in snap, n
        assert snap[n]["source"] == "const"
    assert snap["POLY_LOOP_DD_STOP_ARMED"]["value"] is True
    assert snap["POLYMARKET_US_LIVE"]["value"] is False


def test_edge_filter():
    _restore()
    assert poly_loop.passes_edge(0.58, 0.50) is True
    assert poly_loop.passes_edge(0.42, 0.50) is True
    assert poly_loop.passes_edge(0.57, 0.50) is False
    assert poly_loop.passes_edge(0.50, 0.50) is False
    book = _book()
    miss = _mkt(fv=0.55)
    hit = _mkt(fv=0.70)
    assert poly_loop.evaluate_one(miss, book=book, bbo=book, now=NOW) is None
    rec = poly_loop.evaluate_one(hit, book=book, bbo=book, now=NOW, bankroll=100)
    assert rec is not None
    assert rec["status"] == poly_loop.STATUS_DRY
    assert rec["edge_pts"] >= 8
    assert rec["create_order"] is False
    no_src = _mkt(src="")
    no_src.pop("resolutionSource")
    assert poly_loop.estimate_fv(no_src) is None
    assert poly_loop.evaluate_one(no_src, book=book, bbo=book, now=NOW) is None
    low_liq = _mkt(liq=100)
    assert poly_loop.evaluate_one(low_liq, book=book, bbo=book, now=NOW) is None
    soon = _mkt(hours=1)
    assert poly_loop.evaluate_one(soon, book=book, bbo=book, now=NOW) is None


def test_slippage_skip():
    _restore()
    m = _mkt()
    rec = poly_loop.evaluate_one(
        m, book=_thin(), bbo=_thin(), now=NOW, bankroll=100)
    assert rec is not None
    assert rec["status"] == poly_loop.STATUS_BLOCKED
    assert rec["reason"] in ("insufficient depth", "no book depth", "slippage")
    doc, d = _run([m], {"edge-mkt": _thin()})
    assert doc["n"] == 0
    assert doc["blocked"]
    assert doc["create_order"] is False
    lines = open(poly_loop.ledger_path(d)).read().strip().splitlines()
    assert lines
    row = json.loads(lines[0])
    assert row["status"] == "BLOCKED"


def test_kelly_cap():
    _restore()
    bet, n, f, side = poly_loop.kelly_capped(0.95, 0.40, 100.0)
    assert bet <= 6.0 + 1e-9, bet
    assert bet > 0
    assert side == "buy"
    rec = poly_loop.evaluate_one(
        _mkt(fv=0.95), book=_book(depth_usd=50), bbo=_book(depth_usd=50),
        now=NOW, bankroll=100)
    assert rec["status"] == poly_loop.STATUS_DRY
    assert rec["size"] <= 6.0 + 1e-9


def test_dd_stop():
    _restore()
    assert poly_loop.drawdown_halted(59, 100) is True
    assert poly_loop.drawdown_halted(61, 100) is False
    m = _mkt()
    doc, d = _run(
        [m], {"edge-mkt": _book()},
        state={"equity": 59, "peak": 100, "open": [], "halted": False},
        bankroll=59,
    )
    assert doc["halted"] is True
    assert doc["n"] == 0
    assert doc["status"] == "BLOCKED"
    assert os.path.isfile(os.path.join(d, "post-mortem.json"))
    pm = json.load(open(os.path.join(d, "post-mortem.json")))
    assert pm["category_flip"] is False
    assert pm["slice"] == "POLY-LOOP"
    assert pm["drawdown"] >= 0.40
    assert not os.path.isfile(poly_loop.ledger_path(d)) or (
        open(poly_loop.ledger_path(d)).read().strip() == "")


def test_ledger_shape_before_propose():
    _restore()
    m = _mkt()
    book = _book()
    doc, d = _run([m], {"edge-mkt": book}, bankroll=100)
    assert doc["create_order"] is False
    assert doc["live"] is False
    assert doc["n"] == 1
    p0 = doc["proposals"][0]
    assert p0["status"] == "DRY/SHADOW"
    assert p0["create_order"] is False
    path = poly_loop.ledger_path(d)
    assert os.path.isfile(path)
    row = json.loads(open(path).readline())
    for k in poly_loop.LEDGER_KEYS:
        assert k in row, k
    assert row["status"] in ("DRY/SHADOW", "BLOCKED")
    assert row["slug"] == "edge-mkt"
    assert row["edge_pts"] >= 8
    assert row["size"] > 0
    sf = doc["self_fund"]
    assert sf["withdraw"] is False
    assert "subscription" in sf and "reserve" in sf
    assert sf["covers"] is True
    # max open
    opens = [{"slug": f"p{i}", "size": 1} for i in range(5)]
    rec = poly_loop.evaluate_one(
        m, book=book, bbo=book, now=NOW, bankroll=100, n_open=5)
    assert rec["status"] == "BLOCKED"
    assert rec["reason"] == "max_open"


def test_expired_filtered_and_aliases():
    _restore()
    stale = _mkt(slug="stale-nfl", hours=-8000)
    closed = _mkt(slug="closed-mkt")
    closed["closed"] = True
    zero = _mkt(slug="zero-liq", liq=0)
    alias = {
        "id": "alias-mkt", "slug": "alias-mkt", "active": True,
        "endDateIso": _end(24),
        "liquidityNum": {"value": 9000},
        "tags": [{"league": {"resolution": "https://official.example"}}],
        "fairValue": 0.70,
    }
    assert poly_loop.universe_skip(stale, now=NOW) == "expired"
    assert poly_loop.universe_skip(closed, now=NOW) == "expired"
    assert poly_loop.universe_skip(zero, now=NOW) == "low_liq"
    missing_liq = _mkt(slug="no-liq-field")
    missing_liq.pop("liquidity")
    assert poly_loop.universe_skip(missing_liq, now=NOW) is None
    wrapped = {"market": {"slug": "nested", "endDate": _end(24),
                          "resolutionSource": "https://official.example"}}
    assert poly_loop.hours_left(wrapped, NOW) > 2
    assert poly_loop.resolution_source(wrapped) == "https://official.example"
    assert poly_loop.hours_left(alias, NOW) > 2
    assert poly_loop.liquidity_usd(alias) == 9000
    assert poly_loop.resolution_source(alias) == "https://official.example"
    rec = poly_loop.evaluate_one(
        alias, book=_book(), bbo=_book(), now=NOW, bankroll=100)
    assert rec is not None
    assert rec["status"] == poly_loop.STATUS_DRY

    class _C:
        def __init__(self):
            self.kw = None
        def list_markets(self, **kw):
            self.kw = kw
            return {"markets": [stale, closed, zero, _mkt()]}
        def get_market(self, slug):
            return {"status": 404}

    stats = {}
    c = _C()
    kept = poly_loop.pull_markets(c, limit=20, now=NOW, stats=stats)
    assert c.kw.get("active") is True
    assert c.kw.get("closed") is False
    assert c.kw.get("archived") is False
    assert [k["slug"] for k in kept] == ["edge-mkt"]
    assert stats["dropped"].get("expired", 0) >= 2
    assert stats["dropped"].get("low_liq", 0) >= 1
    assert poly_loop.evaluate_one(
        stale, book=_book(), bbo=_book(), now=NOW) is None


def test_skip_reason_histogram():
    _restore()
    stale = _mkt(slug="old", hours=-100)
    no_src = _mkt(slug="nosrc")
    no_src.pop("resolutionSource")
    no_fv = _mkt(slug="nofv")
    no_fv.pop("fairValue")
    miss = _mkt(slug="thin-edge", fv=0.55)
    hit = _mkt()
    books = {s: _book() for s in (
        "old", "nosrc", "nofv", "thin-edge", "edge-mkt")}
    doc, d = _run([stale, no_src, no_fv, miss, hit], books)
    last = json.load(open(os.path.join(d, "last_run.json")))
    hist = last["skip_reasons"]
    assert hist.get("expired") == 1
    assert hist.get("no_resolution") == 1
    assert hist.get("no_fv") == 1
    assert hist.get("edge_lt_8") == 1
    assert last["n"] == 1
    assert last["skipped"] == 4
    assert last["create_order"] is False
    assert last["live"] is False


def test_fv_from_cache_when_fairvalue_missing():
    _restore()
    m = _mkt()
    m.pop("fairValue")
    d = tempfile.mkdtemp(prefix="poly_fv_")
    with open(os.path.join(d, "mirofish_neural_enrichment.json"), "w") as f:
        json.dump({"symbols": {"edge-mkt": {"p_yes": 0.72}}}, f)
    assert poly_loop.estimate_fv(m) is None
    assert poly_loop.estimate_fv(m, models_dir=d) == 0.72
    rec = poly_loop.evaluate_one(
        m, book=_book(), bbo=_book(), now=NOW, models_dir=d, bankroll=100)
    assert rec is not None
    assert rec["fv"] == 0.72
    assert rec["create_order"] is False
    bare = dict(m)
    bare.pop("resolutionSource")
    assert poly_loop.estimate_fv(bare, models_dir=d) is None
    assert poly_loop.skip_reason(
        bare, book=_book(), bbo=_book(), now=NOW, models_dir=d
    ) == "no_resolution"


def test_allocator_status_only_and_no_create():
    _restore()
    alloc = open(os.path.join(HERE, "master_allocator.py")).read()
    assert "poly_loop.status_line" in alloc
    assert "POLY-LOOP" in alloc
    learn = open(os.path.join(HERE, "poly_learn.py")).read()
    assert "poly_loop" in learn
    assert os.environ.get("POLYMARKET_US_LIVE") in (None, "0", "false", "")
    d = tempfile.mkdtemp(prefix="poly_loop_st_")
    line = poly_loop.status_line(d)
    assert "create=0" in line
    assert "live=0" in line


if __name__ == "__main__":
    test_hygiene_and_fence()
    test_edge_filter()
    test_slippage_skip()
    test_kelly_cap()
    test_dd_stop()
    test_ledger_shape_before_propose()
    test_expired_filtered_and_aliases()
    test_skip_reason_histogram()
    test_fv_from_cache_when_fairvalue_missing()
    test_allocator_status_only_and_no_create()
    print("POLY-LOOP paper desk: PASS")
