#!/usr/bin/env python3
"""Slice 7 — spot GET helpers shipped OFF, per-strategy cap, cron stagger.

No network, no keys, no --live, no nautilus_trader.
Run: python3 test_slice7.py
"""
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)

# Unit tests assert git-default OFF without Burton's ~/.openclaw/.env flips.
os.environ["OPENCLAW_SKIP_ENV"] = "1"

import alpaca_executor as alp
import forward_test_ledger as ftl
import reconcile_run as rr
import spot_order_get as sog
import spot_reconcile as sr
import sports_engine as se
import valedia_config as vc

import kelly_sizing as ks  # inserts live-tree path; put v2 back first
sys.path.insert(0, HERE)
import master_allocator as ma


def _ledger():
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    orig = ftl.LEDGER
    ftl.LEDGER = path
    return orig, path


def _restore(orig, path):
    ftl.LEDGER = orig
    rr.set_spot_order_getter(None)
    os.environ.pop("SPOT_RECONCILE_HTTP", None)
    try:
        os.remove(path)
    except OSError:
        pass


def _seed(strategy, n, wins, net_win=1.0, net_loss=-1.0, source="live"):
    """n settled rows in-window. First `wins` are wins."""
    for i in range(n):
        won = i < wins
        eid = ftl.record_entry(
            strategy, f"{strategy}-{i}", "yes", 0.70, 1.0, venue="kalshi",
            source=source, fill_status="FILLED")
        ftl._append({
            "type": "settlement",
            "entry_id": eid,
            "ticker": f"{strategy}-{i}",
            "strategy": strategy,
            "result": "WON" if won else "LOST",
            "filled_contracts": 1,
            "realized_pnl_net": net_win if won else net_loss,
            "won": won,
            "source": source,
        })


def _cb_get(oid="oid-s7", size="0.01", px="2000", fee="0.22"):
    return {"order": {
        "order_id": oid, "product_id": "ETH-USD", "side": "BUY", "status": "FILLED",
        "filled_size": size, "average_filled_price": px, "total_fees": fee,
    }}


def _opp(typ, ticker, ev=0.50, conf=0.90):
    return {
        "platform": "kalshi", "type": typ, "ticker": ticker, "title": ticker,
        "side": "yes", "price": 0.70, "ev_per_dollar": ev, "ev_per_day": ev,
        "composite_score": 90, "hours_to_resolve": 2, "confidence": conf,
    }


def test_spot_get_helpers():
    assert os.path.basename(rr.__file__) == "reconcile_run.py", rr.__file__
    assert "scripts/integrations" in rr.__file__.replace("\\", "/")
    os.environ.pop("SPOT_RECONCILE_HTTP", None)
    assert vc.SPOT_RECONCILE_HTTP is False
    assert vc.spot_reconcile_http_enabled() is False

    # reconcile_run default never imports the HTTP traders.
    src = open(os.path.join(HERE, "reconcile_run.py")).read()
    assert "import coinbase_trader" not in src
    assert "from coinbase_trader" not in src
    assert "import alpaca_executor" not in src
    assert "import requests" not in src
    assert "_request(" not in src
    assert "def mock_spot_order_getter" in src

    sog_src = open(os.path.join(HERE, "spot_order_get.py")).read()
    assert "import requests" not in sog_src.split("def ")[0]  # no module-level requests
    assert "from coinbase_trader import" not in sog_src.split("def coinbase_order_get")[0]
    assert "SPOT_RECONCILE_HTTP" in sog_src

    cb_src = open(os.path.join(HERE, "coinbase_trader.py")).read()
    assert "def get_order" in cb_src
    assert "/api/v3/brokerage/orders/historical/" in cb_src
    assert "def place_market_order" not in cb_src
    alp_src = open(os.path.join(HERE, "alpaca_executor.py")).read()
    assert "def get_order" in alp_src
    assert "/v2/orders/" in alp_src

    rr.set_spot_order_getter(None)
    assert rr.get_spot_order_getter() is rr.mock_spot_order_getter
    assert rr.mock_spot_order_getter("oid", "coinbase") is None

    # Flag-off: live helper refuses HTTP even if called directly (no inject).
    http_calls = []

    def _boom_req(method, path, body=None):
        http_calls.append((method, path))
        raise RuntimeError("must not HTTP")

    assert sog.coinbase_order_get("oid-off") is None
    assert sog.alpaca_order_get("oid-off") is None
    assert sog.live_spot_order_getter("oid-off", "coinbase") is None
    assert sog.live_spot_order_getter("oid-off", "alpaca") is None
    assert http_calls == []

    # Injected transport (tests): complete Coinbase GET parses; never live.
    got = sog.coinbase_order_get("oid-s7", _request=lambda m, p: _cb_get("oid-s7"))
    assert got and got["order"]["order_id"] == "oid-s7"
    assert got["order"]["filled_size"] == "0.01"
    path_seen = []

    def _cb_req(method, path):
        path_seen.append((method, path))
        return _cb_get("abc")

    sog.coinbase_order_get("abc", _request=_cb_req)
    assert path_seen == [("GET", sr.coinbase_order_get_path("abc"))]
    assert sr.coinbase_order_get_path("abc") == "/api/v3/brokerage/orders/historical/abc"

    alp_seen = []

    def _alp_get(path):
        alp_seen.append(path)
        return {"id": "a1", "status": "filled", "filled_qty": "2", "filled_avg_price": "10"}

    ag = sog.alpaca_order_get("a1", _get=_alp_get)
    assert ag["id"] == "a1" and ag["filled_qty"] == "2"
    assert alp_seen == [sr.alpaca_order_get_path("a1")]
    assert sr.alpaca_order_get_path("a1") == "/v2/orders/a1"

    # Incomplete GET body is still a body (reconcile decides complete).
    miss = sog.coinbase_order_get("miss", _request=lambda m, p: {
        "order": {"order_id": "miss", "status": "OPEN", "filled_size": "0"},
    })
    assert miss["order"]["filled_size"] == "0"
    fill = sr.fill_from_payload("coinbase", get_response=miss)
    assert fill["complete"] is False

    # Flag-on with inject upgrades UNRECONCILED; without inject still no HTTP
    # from live_spot_order_getter if we don't call trader.
    orig, path = _ledger()
    try:
        eid = ftl.book_spot_fill(
            "CRYPTO_MOMENTUM", "ETH-USD", "buy", "coinbase",
            {"success": True, "order_id": "oid-s7", "usd": 20.0},
            intended_source="live")
        stub = [r for r in ftl._read() if r.get("id") == eid][0]
        assert stub["source"] == "unreconciled"

        rec = rr.reconcile_unreconciled_run()  # default mock
        assert rec["upgraded"] == 0, rec

        def _getter(order_id, venue):
            return sog.live_spot_order_getter(
                order_id, venue, _request=lambda m, p: _cb_get(order_id))

        rec = rr.reconcile_unreconciled_run(getter=_getter)
        assert rec["upgraded"] == 1, rec
        filled = [r for r in ftl._read()
                  if r.get("type") == "entry" and r.get("order_id") == "oid-s7"
                  and r.get("fill_status") == "FILLED"]
        assert filled and filled[0]["source"] == "live"
        assert filled[0]["entry_fee"] > 0

        # Incomplete inject stays UNRECONCILED.
        eid_m = ftl.book_spot_fill(
            "CRYPTO_MOMENTUM", "BTC-USD", "buy", "coinbase",
            {"success": True, "order_id": "oid-miss", "usd": 10.0},
            intended_source="live")

        def _incomplete(order_id, venue):
            return {"order": {"order_id": order_id, "status": "OPEN", "filled_size": "0"}}

        rec = rr.reconcile_unreconciled_run(getter=_incomplete)
        still = [r for r in ftl._read() if r.get("id") == eid_m][0]
        assert still["source"] == "unreconciled"
        assert still["fill_status"] == "UNRECONCILED"
    finally:
        _restore(orig, path)

    # Env flip is the documented single switch; restore immediately. Do not HTTP.
    os.environ["SPOT_RECONCILE_HTTP"] = "1"
    try:
        assert vc.spot_reconcile_http_enabled() is True
        g = rr.get_spot_order_getter()
        assert g is sog.live_spot_order_getter
        # live getter with flag on but we do not call it (would HTTP). Importing is OK.
    finally:
        os.environ.pop("SPOT_RECONCILE_HTTP", None)
        rr.set_spot_order_getter(None)
    assert vc.spot_reconcile_http_enabled() is False
    assert rr.get_spot_order_getter() is rr.mock_spot_order_getter

    # alpaca get_order exists and is GET, not POST.
    assert "requests.get" in alp_src
    assert "def get_order" in alp_src
    # Don't call alp.get_order (would HTTP). Source-level only.
    assert alp.get_order  # bound


def test_per_strategy_cap():
    os.environ.pop("SPOT_RECONCILE_HTTP", None)
    orig, path = _ledger()
    orig_cap = ma._cfg.dynamic_per_trade_cap if ma._cfg else None
    orig_se_cap = se.dynamic_per_trade_cap
    try:
        # Empty ledger: every category is unproven 0.5 → 4% at SIDEWAYS/risk=1.
        assert vc.category_track_record_mult("SPORTS") == vc.KELLY_UNPROVEN_MULT
        thin = vc.dynamic_per_trade_cap(regime="SIDEWAYS", risk_factor=1.0, category="SPORTS")
        assert abs(thin - vc.KELLY_BASE_CAP * vc.KELLY_UNPROVEN_MULT) < 1e-12, thin

        # Hot CRYPTO winner must NOT lift thin SPORTS.
        _seed("CRYPTO_BRACKET", n=20, wins=16, net_win=2.0, net_loss=-1.0, source="live")
        assert vc.category_track_record_mult("CRYPTO_BRACKET") == 1.0
        crypto_cap = vc.dynamic_per_trade_cap(
            regime="SIDEWAYS", risk_factor=1.0, category="CRYPTO_BRACKET")
        sports_cap = vc.dynamic_per_trade_cap(
            regime="SIDEWAYS", risk_factor=1.0, category="SPORTS")
        assert abs(crypto_cap - vc.KELLY_BASE_CAP * 1.0) < 1e-12, crypto_cap
        assert abs(sports_cap - vc.KELLY_BASE_CAP * vc.KELLY_UNPROVEN_MULT) < 1e-12, sports_cap
        assert crypto_cap > sports_cap

        # Loser category throttles to 0.3 even if CRYPTO is hot.
        _seed("WEATHER_ARB", n=20, wins=2, net_win=1.0, net_loss=-2.0, source="live")
        assert vc.category_track_record_mult("WEATHER_ARB") == 0.3
        weather_cap = vc.dynamic_per_trade_cap(
            regime="SIDEWAYS", risk_factor=1.0, category="WEATHER_ARB")
        assert abs(weather_cap - vc.KELLY_BASE_CAP * 0.3) < 1e-12, weather_cap
        assert weather_cap < sports_cap

        # Paper / backfill / unreconciled cannot lift a thin category.
        _seed("EQUITY_MOMENTUM", n=25, wins=25, net_win=5.0, source="paper")
        _seed("OIL_BRACKET", n=25, wins=25, net_win=5.0, source="backfill:pnl_tracker")
        _seed("SPORTS", n=5, wins=5, net_win=3.0, source="unreconciled")
        assert vc.category_track_record_mult("EQUITY_MOMENTUM") == vc.KELLY_UNPROVEN_MULT
        assert vc.category_track_record_mult("OIL_BRACKET") == vc.KELLY_UNPROVEN_MULT
        assert vc.category_track_record_mult("SPORTS") == vc.KELLY_UNPROVEN_MULT
        assert "EQUITY_MOMENTUM" not in ftl.category_stats()
        assert "OIL_BRACKET" not in ftl.category_stats()

        # SPORTS_TONIGHT folds into SPORTS. Thin SPORTS stays unproven.
        assert vc.category_track_record_mult("SPORTS_TONIGHT") == vc.KELLY_UNPROVEN_MULT

        # Sports sizing passes category=SPORTS (not the global mix).
        seen = []

        def _cap(regime="SIDEWAYS", track_record_mult=None, risk_factor=None, category=None):
            seen.append(category)
            return 0.02

        se.dynamic_per_trade_cap = _cap
        g = {'game_id': 'g', 'league': 'NBA', 'fav_ticker': 'KXNBAGAME-T', 'fav_title': 'X',
             'fav_price': 0.80, 'fav_bid': 0.79, 'spread': 0.01, 'dog_ticker': 'd',
             'dog_price': 0.20, 'exp': '2026', 'edge_score': 40, 'edge_reasons': []}
        bets = se.size_bets([g], bankroll=100.0)
        assert seen == [se.CATEGORY], seen
        assert bets[0]['bet_pct'] <= 2.0 + 1e-9, bets[0]
        se.dynamic_per_trade_cap = orig_se_cap

        # Allocator: per-opp cap. Loser type cannot ride a winner's cap.
        def _alloc_cap(regime="SIDEWAYS", track_record_mult=None, risk_factor=None,
                       category=None):
            if category == "MACRO":
                return 0.01
            if category in ("SPORTS", "SPORTS_TONIGHT"):
                return 0.04
            return 0.08

        assert ma._cfg is not None
        ma._cfg.dynamic_per_trade_cap = _alloc_cap
        allocs, _why = ma.allocate(
            [_opp("MACRO", "W1"), _opp("SPORTS", "S1")],
            {"deployable": 100.0},
        )
        by = {a["ticker"]: a["allocation"] for a in allocs}
        assert by["W1"] <= 100.0 * 0.01 + 1e-9, by
        assert by["S1"] <= 100.0 * 0.04 + 1e-9, by
        assert by["S1"] > by["W1"]

        # KellySizer uses per-category cap when category is set.
        k_s = ks.KellySizer(10000)
        r_s = k_s.size_position(
            90, 5.0, regime="SIDEWAYS", concurrent_bets=1, category="SPORTS",
            track_record_mult=None, risk_factor=1.0)
        k_c = ks.KellySizer(10000)
        r_c = k_c.size_position(
            90, 5.0, regime="SIDEWAYS", concurrent_bets=1, category="CRYPTO_BRACKET",
            track_record_mult=None, risk_factor=1.0)
        if r_s.get("action") == "EXECUTE" and r_c.get("action") == "EXECUTE":
            assert r_c["position_pct"] >= r_s["position_pct"], (r_s, r_c)
    finally:
        se.dynamic_per_trade_cap = orig_se_cap
        if ma._cfg is not None and orig_cap is not None:
            ma._cfg.dynamic_per_trade_cap = orig_cap
        _restore(orig, path)


def test_cron_stagger():
    assert vc.SPORTS_CRON_MINUTE != vc.ALLOCATOR_CRON_MINUTE
    assert vc.SPORTS_CRON_MINUTE == 0
    assert vc.ALLOCATOR_CRON_MINUTE == 20

    install = open(os.path.join(ROOT, "scripts", "install_cron.sh")).read()
    sched = open(os.path.join(ROOT, "skills", "valedia", "cron-schedule.md")).read()
    se_run = open(os.path.join(HERE, "sports_run.py")).read()
    al_run = open(os.path.join(HERE, "allocator_run.py")).read()

    sm = re.search(r"^(\d+) \*/2 \* \* \* .*sports_run\.py", install, re.M)
    am = re.search(r"^(\d+) \*/4 \* \* \* .*allocator_run\.py", install, re.M)
    assert sm and am, (sm, am)
    assert int(sm.group(1)) == vc.SPORTS_CRON_MINUTE
    assert int(am.group(1)) == vc.ALLOCATOR_CRON_MINUTE
    assert sm.group(1) != am.group(1)

    assert "0 */2 * * *" in sched and "sports_run.py" in sched
    assert "20 */4 * * *" in sched and "allocator_run.py" in sched
    assert "0 */4 * * * | Every 4h | MASTER ALLOCATOR" not in sched
    assert "SPORTS_CRON_MINUTE" in se_run or ":00" in se_run
    assert "ALLOCATOR_CRON_MINUTE" in al_run or ":20" in al_run
    assert "reconcile_run.py" in install and "settle_run.py" in install


def run():
    os.environ.pop("SPOT_RECONCILE_HTTP", None)
    test_spot_get_helpers()
    test_per_strategy_cap()
    test_cron_stagger()
    print("slice-7 checks: PASS")


if __name__ == "__main__":
    run()
