#!/usr/bin/env python3
"""Slice 6 — Kalshi fill-before-EXECUTED, sports cap, SPORTS alias, backfill exclude.

No network, no keys, no orders. Isolated temp ledger.
Run: python3 test_kalshi_fill_ledger.py
"""
import os
import sys
import tempfile
import types

import forward_test_ledger as ftl
import sports_engine as se
import master_allocator as ma
import kalshi_auth
import kalshi_executor as ke  # noqa: F401  (load-smoke after SPORTS rename)

HERE = os.path.dirname(os.path.abspath(__file__))


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


def _ack(oid="ack-1"):
    return {"order": {"order_id": oid, "status": "resting", "count": 5, "yes_price": 80}}


def _fill(oid="fill-1", n=5, px=80):
    return {"order": {"order_id": oid, "status": "executed", "fill_count": n,
                      "remaining_count": 0, "yes_price": px, "count": n}}


def run():
    # 0. POST-ack / resting / fill_count=0 is NOT a fill. Fill-count and get_fills are.
    ext = ftl.extract_kalshi_fill({"success": True, "order_id": "abc"})
    assert ext["complete"] is False, ext
    ext = ftl.extract_kalshi_fill(_ack())
    assert ext["complete"] is False and ext["order_id"] == "ack-1", ext
    ext = ftl.extract_kalshi_fill({"order": {"order_id": "z", "status": "resting",
                                            "fill_count": 0, "count": 10}})
    assert ext["complete"] is False, ext
    ext = ftl.extract_kalshi_fill(None)
    assert ext["complete"] is False, ext
    ext = ftl.extract_kalshi_fill(_fill())
    assert ext["complete"] is True and ext["qty"] == 5 and abs(ext["price"] - 0.80) < 1e-9, ext
    ext = ftl.extract_kalshi_fill(
        {"order": {"order_id": "m1", "status": "resting"}},
        fills_payload={"fills": [{"order_id": "m1", "count": 3, "yes_price": 77}]},
    )
    assert ext["complete"] is True and ext["qty"] == 3, ext
    ext = ftl.extract_kalshi_fill(
        {"order": {"order_id": "m1", "status": "resting"}},
        fills_payload={"fills": [{"order_id": "other", "count": 9}]},
    )
    assert ext["complete"] is False, ext  # do not steal another order's fills

    orig, path = _ledger()
    try:
        # 1. Bare POST-ack → UNRECONCILED; does not lift Kelly / Thorp open count.
        eid_u = ftl.book_kalshi_fill(
            "SPORTS", "KXNBAGAME-T", "yes", _ack(),
            intended_source="live", fallback_price=0.80, fallback_contracts=5)
        rec = [r for r in ftl._read() if r.get("id") == eid_u][0]
        assert rec["source"] == "unreconciled", rec
        assert rec["fill_status"] == "UNRECONCILED"
        assert rec["order_id"] == "ack-1"
        assert rec["strategy"] == "SPORTS"
        assert ftl.open_position_count() == 0
        assert ftl.real_track_record()["n"] == 0
        ftl.settle_open("KXNBAGAME-T", "kalshi", 1.0, source="live")  # launder attempt
        assert ftl.real_track_record()["n"] == 0

        # 2. Real fill books live; settlement lifts Kelly; open count sees it until settled.
        eid = ftl.book_kalshi_fill(
            "SPORTS", "KXNHLGAME-T", "yes", _fill("fill-1", 4, 75),
            intended_source="live")
        rec = [r for r in ftl._read() if r.get("id") == eid][0]
        assert rec["source"] == "live" and rec["fill_status"] == "FILLED", rec
        assert rec["contracts"] == 4 and rec["entry_fee"] > 0
        assert ftl.open_position_count() == 1
        assert ftl.real_track_record()["n"] == 0  # not settled
        ftl._append({
            "type": "settlement", "entry_id": eid, "ticker": "KXNHLGAME-T",
            "strategy": "SPORTS", "won": True, "realized_pnl_net": 0.5, "source": "live",
        })
        tr = ftl.real_track_record()
        assert tr["n"] == 1 and tr["wins"] == 1 and tr["net"] == 0.5, tr
        assert ftl.open_position_count() == 0

        # 3. SPORTS_TONIGHT write folds to SPORTS; gate sees one bucket.
        eid_tn = ftl.book_kalshi_fill(
            "SPORTS_TONIGHT", "KXNBAGAME-U", "yes", _fill("fill-tn", 2, 80),
            intended_source="live")
        rec_tn = [r for r in ftl._read() if r.get("id") == eid_tn][0]
        assert rec_tn["strategy"] == "SPORTS", rec_tn  # canonicalized on write
        # Historical SPORTS_TONIGHT settlement still folds on read.
        eid_old = ftl.record_entry("SPORTS_TONIGHT", "OLD-T", "yes", 0.80, 2,
                                   venue="kalshi", source="live", fill_status="FILLED")
        ftl._append({
            "type": "settlement", "entry_id": eid_old, "ticker": "OLD-T",
            "strategy": "SPORTS_TONIGHT", "won": False, "realized_pnl_net": -5.0,
            "source": "live",
        })
        ftl._append({
            "type": "settlement", "entry_id": eid_tn, "ticker": "KXNBAGAME-U",
            "strategy": "SPORTS", "won": False, "realized_pnl_net": -3.0, "source": "live",
        })
        stats = ftl.category_stats(lookback_days=45)
        assert "SPORTS_TONIGHT" not in stats, stats
        assert stats["SPORTS"]["n"] >= 2, stats
        assert stats["SPORTS"]["net"] < 0, stats

        # 4. backfill:pnl_tracker must not lift Kelly even if settled as a win.
        n_before = ftl.real_track_record()["n"]
        eid_bf = ftl.record_entry("SPORTS", "BF-T", "yes", 0.80, 10,
                                  venue="kalshi", source="backfill:pnl_tracker")
        assert ftl.open_position_count() == 0  # backfill is non-real
        ftl._append({
            "type": "settlement", "entry_id": eid_bf, "ticker": "BF-T",
            "strategy": "SPORTS", "won": True, "realized_pnl_net": 99.0,
            "source": "backfill:pnl_tracker",
        })
        tr = ftl.real_track_record()
        assert tr["n"] == n_before, tr
        ftl.settle_open("BF-T", "kalshi", 1.0, source="live")  # cannot launder
        assert ftl.real_track_record()["n"] == n_before

        # 5. paper/sandbox still excluded (regression).
        ftl.book_kalshi_fill("SPORTS", "PAPER-T", "yes", _fill("p1"), intended_source="paper")
        ftl.book_kalshi_fill("SPORTS", "SB-T", "yes", _fill("s1"), intended_source="nautilus_sandbox")
        assert ftl.real_track_record()["n"] == n_before

        # 6. execute_bets: ack-without-fill does not increment Kelly/open; fill does.
        orig_pos = kalshi_auth.get_positions
        orig_co = se.create_order
        orig_gf = kalshi_auth.get_fills
        kalshi_auth.get_positions = lambda: {"market_positions": []}
        kalshi_auth.get_fills = lambda: {"fills": []}
        try:
            n0, o0 = ftl.real_track_record()["n"], ftl.open_position_count()
            se.create_order = lambda *a, **k: _ack("se-ack")
            bet = {'fav_ticker': 'SE-ACK', 'fav_title': 'X', 'fav_price': 0.80,
                   'contracts': 5, 'cost': 4.0, 'fee': 0.02, 'profit_if_win': 0.98,
                   'league': 'NBA', 'exp': '2026'}
            res = se.execute_bets([bet], dry_run=False)
            assert res[0]['status'] == 'UNRECONCILED', res[0]
            assert ftl.open_position_count() == o0
            assert ftl.real_track_record()["n"] == n0
            se.create_order = lambda *a, **k: _fill("se-fill", 5, 80)
            bet2 = dict(bet, fav_ticker='SE-FILL')
            res = se.execute_bets([bet2], dry_run=False)
            assert res[0]['status'] == 'EXECUTED', res[0]
            assert ftl.open_position_count() == o0 + 1
            assert ftl.real_track_record()["n"] == n0  # still unsettled
        finally:
            se.create_order = orig_co
            kalshi_auth.get_positions = orig_pos
            kalshi_auth.get_fills = orig_gf

        # 7. allocator Kalshi taker: same fill-true rule (mocked intel/auth, no HTTP).
        orig_plan, orig_mom = ma.build_execution_plan, ma.MakerOrderManager
        orig_bl = ma._is_kalshi_blacklisted
        orig_clr = ma._clear_kalshi_failure
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
        orig_ka_co, orig_ka_pos = kalshi_auth.create_order, kalshi_auth.get_positions
        orig_ka_gf = kalshi_auth.get_fills
        kalshi_auth.get_positions = lambda: {"market_positions": []}
        kalshi_auth.get_fills = lambda: {"fills": []}
        alloc = {
            "platform": "kalshi", "type": "SPORTS_TONIGHT", "ticker": "ALLOC-ACK",
            "title": "t", "side": "yes", "price": 0.80, "allocation": 4.0,
            "expected_profit": 0.5, "hours_to_resolve": 6, "raw": {},
        }
        try:
            n0, o0 = ftl.real_track_record()["n"], ftl.open_position_count()
            kalshi_auth.create_order = lambda *a, **k: _ack("alloc-ack")
            out = ma.execute_allocations([alloc], dry_run=False)
            assert out[0]["status"] == "UNRECONCILED", out[0]
            assert ftl.open_position_count() == o0
            assert ftl.real_track_record()["n"] == n0
            kalshi_auth.create_order = lambda *a, **k: _fill("alloc-fill", 5, 80)
            alloc2 = dict(alloc, ticker="ALLOC-FILL", type="SPORTS")
            out = ma.execute_allocations([alloc2], dry_run=False)
            assert out[0]["status"] == "EXECUTED", out[0]
            assert ftl.open_position_count() == o0 + 1
            # type SPORTS_TONIGHT was canonicalized to SPORTS on the ack stub too
            rows = [r for r in ftl._read() if r.get("ticker") == "ALLOC-ACK"]
            assert rows and rows[0]["strategy"] == "SPORTS", rows
        finally:
            ma.build_execution_plan = orig_plan
            ma.MakerOrderManager = orig_mom
            ma._is_kalshi_blacklisted = orig_bl
            ma._clear_kalshi_failure = orig_clr
            kalshi_auth.create_order = orig_ka_co
            kalshi_auth.get_positions = orig_ka_pos
            kalshi_auth.get_fills = orig_ka_gf
            if orig_intel is not None:
                sys.modules["intel_layer"] = orig_intel
            else:
                sys.modules.pop("intel_layer", None)
    finally:
        _restore(orig, path)

    # 6. Sports size: never 10%; always <= fund 4% and the injected cap.
    g = {'game_id': 'g', 'league': 'NBA', 'fav_ticker': 'KXNBAGAME-T', 'fav_title': 'X',
         'fav_price': 0.80, 'fav_bid': 0.79, 'spread': 0.01, 'dog_ticker': 'd',
         'dog_price': 0.20, 'exp': '2026', 'edge_score': 40, 'edge_reasons': []}
    bets = se.size_bets([g], bankroll=100.0, per_trade_cap=0.04)
    assert len(bets) == 1
    assert bets[0]['cost'] <= 100.0 * 0.04 + 1e-9, bets[0]
    assert bets[0]['bet_pct'] <= 4.0 + 1e-9, bets[0]
    bets_hi = se.size_bets([g], bankroll=100.0, per_trade_cap=0.15)
    assert bets_hi[0]['cost'] <= 100.0 * se.FUND_PER_TRADE_MAX + 1e-9, bets_hi[0]
    bets_lo = se.size_bets([g], bankroll=100.0, per_trade_cap=0.02)
    assert bets_lo[0]['bet_pct'] <= 2.0 + 1e-9, bets_lo[0]

    # 8. Source scan: taker paths book via book_kalshi_fill; no silent live record_entry.
    se_src = open(os.path.join(HERE, "sports_engine.py")).read()
    ma_src = open(os.path.join(HERE, "master_allocator.py")).read()
    ke_src = open(os.path.join(HERE, "kalshi_executor.py")).read()
    ftl_src = open(os.path.join(HERE, "forward_test_ledger.py")).read()
    assert "book_kalshi_fill" in se_src and "resolve_kalshi_fill" in se_src
    assert "book_kalshi_fill" in ma_src
    assert "book_kalshi_fill" in ke_src
    assert "ftl.record_entry" not in se_src
    assert "dynamic_per_trade_cap" in se_src
    assert 'win_rate", 86' not in se_src
    assert "pct = 0.10" not in se_src
    assert "LEAGUE_BONUS" not in se_src
    assert 'strategy = "SPORTS_TONIGHT"' not in ke_src
    assert 'strategy = "SPORTS"' in ke_src
    assert 'or "backfill" in s' in ftl_src
    assert 'CATEGORY_ALIASES = {"SPORTS_TONIGHT": "SPORTS"}' in ftl_src
    trader_src = open(os.path.join(HERE, "coinbase_trader.py")).read()
    assert "def place_market_order" not in trader_src
    print("kalshi-fill-ledger Slice 6 checks: PASS")


if __name__ == "__main__":
    run()
