#!/usr/bin/env python3
"""KILL-SWITCH — VALEDIA_TRADING_HALT blocks writes, allows GET/preview.

No network, no keys, no --live, no writer/AUTOEXEC/US_LIVE flips.
Run: python3 test_kill_switch.py
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)

# Unit tests assert git-default OFF without Burton's ~/.openclaw/.env flips.
os.environ["OPENCLAW_SKIP_ENV"] = "1"

import alpaca_executor as alp
import coinbase_trader as cb
import execution_policy as ep
import kalshi_auth as ka
import master_allocator as ma
import polymarket_us_client as pmu
import rebalancer
import test_poly_us as tpu
import valedia_config as vc
import valedia_full_smoke as smoke
import informed_before_create as ibc
from test_informed_before_create import good_kalshi, good_poly, pin_kelly, unpin_kelly
import kalshi_fee_map as kfm


def _restore():
    vc.VALEDIA_TRADING_HALT = False
    vc.VALEDIA_TREASURY_AUTO = False
    vc.REBALANCER_AUTOEXEC = False
    vc.NAUTILUS_LIVE = False
    vc.NAUTILUS_ENABLED = False
    vc.SPOT_RECONCILE_HTTP = False
    vc.MIROFISH_AUTO_RAISE = False
    vc.VALEDIA_SELF_ADJUST = False
    vc.VENUE_WRITER["kalshi"] = "valedia"
    vc.VENUE_WRITER["coinbase"] = "valedia"
    vc.VENUE_WRITER["alpaca"] = "valedia"
    vc.VENUE_WRITER["polymarket"] = "valedia"
    vc.VENUE_WRITER["hyperliquid"] = "none"
    vc.VENUE_WRITER["kraken"] = "none"
    for k in ("VALEDIA_TRADING_HALT", "VALEDIA_TRADING_HALT_FILE",
              "VALEDIA_TRADING_HALT_PING", "POLYMARKET_US_LIVE",
              "POLYMARKET_US_HTTP", "REBALANCER_AUTOEXEC"):
        os.environ.pop(k, None)
    vc._STAMP_CACHE = None
    tpu._restore()


def _arm_env():
    os.environ["VALEDIA_TRADING_HALT"] = "1"


def _disarm_env():
    os.environ.pop("VALEDIA_TRADING_HALT", None)
    vc.VALEDIA_TRADING_HALT = False


def test_hygiene_and_default_off():
    _restore()
    assert vc.VALEDIA_TRADING_HALT is False
    assert vc.trading_halted() is False
    assert vc.require_not_halted("probe") is False
    assert vc.VALEDIA_VERSION == "0.75.0", vc.VALEDIA_VERSION
    assert pmu.live_enabled() is False
    assert vc.VENUE_WRITER["polymarket"] == "valedia"
    assert vc.MIROFISH_AUTO_RAISE is False
    assert vc.VALEDIA_SELF_ADJUST is False
    assert vc.REBALANCER_AUTOEXEC is False
    assert vc.VALEDIA_TREASURY_AUTO is False
    assert vc.NAUTILUS_LIVE is False
    assert vc.SPOT_RECONCILE_HTTP is False
    src = open(os.path.join(HERE, "valedia_config.py")).read()
    assert "def require_not_halted" in src
    assert "class HaltedError" in src
    cron = open(os.path.join(ROOT, "scripts", "install_cron.sh")).read()
    assert "valedia_trading_halt" not in cron
    rec = open(os.path.join(ROOT, "recommendations", "KILL-SWITCH.md")).read()
    assert "require_not_halted" in rec
    assert "does NOT block" in rec or "does not block" in rec.lower()
    audit = open(os.path.join(ROOT, "recommendations",
                              "PONYTAIL-AUDIT-KILL-SWITCH.md")).read()
    assert "yagni:" in audit.lower() or "yagni:" in audit


def test_env_halt_blocks_and_unhalt_restores():
    _restore()
    posted = []

    def fake_post(endpoint, body=None):
        posted.append((endpoint, body))
        return {"order": {"order_id": "should-not"}}

    orig_post = ka.post
    ka.post = fake_post
    _arm_env()
    try:
        ka.create_order("TICK", "yes", 1, 10)
        raise AssertionError("create_order must raise HaltedError")
    except vc.HaltedError as e:
        assert "VALEDIA_TRADING_HALT" in str(e)
        assert "kalshi.create_order" in str(e)
    assert posted == []
    try:
        ka.sell_position("TICK", "yes", 1, 10)
        raise AssertionError("sell_position must raise HaltedError")
    except vc.HaltedError:
        pass
    _disarm_env()
    try:
        ka.create_order("TICK", "yes", 1, 10)
        raise AssertionError("uninformed create must stay refused after unhalt")
    except ibc.InformedCreateRefused:
        pass
    assert posted == []
    qe, old = pin_kelly()
    fmap = {"TICK": kfm._row("TICK", 0.0, 0.0, "halt")}
    try:
        out = ka.create_order(
            "TICK", "yes", 1, 10, informed=good_kalshi(series="TICK", fmap=fmap))
    finally:
        unpin_kelly(qe, old)
    assert posted and posted[0][0] == "/portfolio/events/orders"
    assert out["order"]["order_id"] == "should-not"
    ka.post = orig_post


def test_halt_allows_get_and_preview():
    _restore()
    _arm_env()
    got = []

    def fake_get(endpoint):
        got.append(endpoint)
        return {"balance": 500}

    orig_get = ka.get
    ka.get = fake_get
    bal = ka.get_balance()
    assert bal["balance"] == 500
    assert got == ["/portfolio/balance"]

    x = tpu.MockXport()
    c = tpu._client(x, live=False)
    prev = c.preview_order({"marketSlug": "btc-100k", "quantity": 1})
    assert prev["kind"] == "preview"
    assert prev["filled"] is False
    try:
        c.create_order({"marketSlug": "btc-100k"})
        raise AssertionError("poly create must halt")
    except vc.HaltedError as e:
        assert "polymarket_us.create_order" in str(e)
    assert not any(call["path"] == "/v1/orders" for call in x.calls)
    live = tpu._client(x, live=True)
    try:
        live.create_order({"marketSlug": "btc-100k"})
        raise AssertionError("live poly create must still halt")
    except vc.HaltedError:
        pass
    _disarm_env()
    try:
        live.create_order({"marketSlug": "btc-100k"})
        raise AssertionError("uninformed poly create must stay refused after unhalt")
    except ibc.InformedCreateRefused:
        pass
    qe, old = pin_kelly()
    try:
        live.create_order(good_poly())
    finally:
        unpin_kelly(qe, old)
    posted = [call for call in x.calls if call["path"] == "/v1/orders"]
    assert posted and posted[-1]["method"] == "POST"
    ka.get = orig_get


def test_spot_writers_and_layers():
    _restore()
    _arm_env()
    try:
        cb.market_buy("BTC-USD", 1)
        raise AssertionError("coinbase buy must halt")
    except vc.HaltedError as e:
        assert "coinbase.market_buy" in str(e)
    try:
        alp.market_buy("AAPL", notional=1)
        raise AssertionError("alpaca buy must halt")
    except vc.HaltedError as e:
        assert "alpaca.market_buy" in str(e)
    mgr = ep.MakerOrderManager.__new__(ep.MakerOrderManager)
    mgr.api = object()
    try:
        mgr._place("TICK", "yes", 1, 10)
        raise AssertionError("_place must halt")
    except vc.HaltedError:
        pass
    vc.REBALANCER_AUTOEXEC = True
    os.environ["REBALANCER_AUTOEXEC"] = "1"
    assert rebalancer.rebalancer_may_exec({"live_flag": True}) is False
    vc.REBALANCER_AUTOEXEC = False
    os.environ.pop("REBALANCER_AUTOEXEC", None)
    rows = ma.execute_allocations([{
        "ticker": "TICK", "platform": "kalshi", "type": "SPORTS",
        "allocation": 1, "expected_profit": 0.1, "side": "yes", "price": 0.4,
    }], dry_run=False)
    assert rows[0]["status"] == "HALTED"
    dry = ma.execute_allocations([{
        "ticker": "TICK", "platform": "kalshi", "type": "SPORTS",
        "allocation": 1, "expected_profit": 0.1, "side": "yes", "price": 0.4,
    }], dry_run=True)
    assert dry[0]["status"] == "DRY_RUN"
    _disarm_env()
    os.environ["VALEDIA_TRADING_HALT"] = "0"
    assert vc.trading_halted() is False
    os.environ["VALEDIA_TRADING_HALT"] = "false"
    vc.require_not_halted("after-off")


def test_file_arm_env_off_wins_and_self_heal_report_only():
    """Writes and arms the halt file, so it replaces the two path functions for its duration.

    valedia_config.halt_state_path()/halt_ping_path() always return <repo>/state/halt*.json (the
    env override is unreachable by design); without this patch the test wrote the real files and
    left them halted: true. Note: the legacy "clear" writes a file with halted: false, but the
    current risk gate (valedia_trader/research_loop/risk_gate.py HALT_FILE) treats that file's
    PRESENCE as a halt, so clearing the real halt means removing the file.
    """
    td = tempfile.mkdtemp(prefix="halt_")
    prev_fns = (vc.halt_state_path, vc.halt_ping_path)
    vc.halt_state_path = lambda: os.path.join(td, "halt.json")
    vc.halt_ping_path = lambda: os.path.join(td, "ping.md")
    try:
        _file_arm_env_off_wins_and_self_heal_report_only(td)
    finally:
        vc.halt_state_path, vc.halt_ping_path = prev_fns


def _file_arm_env_off_wins_and_self_heal_report_only(td):
    _restore()
    halt_path = os.path.join(td, "halt.json")
    ping_path = os.path.join(td, "ping.md")
    os.environ["VALEDIA_TRADING_HALT_FILE"] = halt_path
    os.environ["VALEDIA_TRADING_HALT_PING"] = ping_path
    doc = vc.write_halt_state(True, reason="chain sick", source="cli")
    assert doc["halted"] is True
    assert vc.trading_halted() is True
    assert os.path.isfile(halt_path)
    assert os.path.isfile(ping_path)
    ping = open(ping_path).read()
    assert "not sent" in ping.lower()
    assert "valedia-exit-ping" in open(halt_path).read()
    os.environ["VALEDIA_TRADING_HALT"] = "0"
    assert vc.trading_halted() is False
    os.environ.pop("VALEDIA_TRADING_HALT", None)
    assert vc.trading_halted() is True
    vc.write_halt_state(False, reason="manual off", source="cli")
    assert vc.trading_halted() is False
    ev = vc.record_halt_event("cockpit panel error", source="observe", arm=False)
    assert ev["halted"] is False
    assert "cockpit panel error" in ev["reason"]
    assert ev["ping"]["send"] is False
    assert vc.panel_looks_sick(None) is True
    assert vc.panel_looks_sick({"error": "boom"}) is True
    assert vc.panel_looks_sick({"valedia_version": "0.43.0"}) is False
    armed = vc.record_halt_event("repeated writer exceptions", source="observe",
                                 arm=True)
    assert armed["halted"] is True
    os.environ["VALEDIA_TRADING_HALT"] = "false"
    assert vc.trading_halted() is False


def test_full_smoke_fence_notes_halt():
    _restore()
    row = smoke.check_fence()
    assert row["status"] == "PASS"
    assert row["extra"]["trading_halt"] is False
    assert vc.VENUE_WRITER["polymarket"] == "valedia"
    src = open(os.path.join(HERE, "valedia_full_smoke.py")).read()
    assert "trading_halt" in src
    assert 'POLYMARKET_US_LIVE"] = "1"' not in src


if __name__ == "__main__":
    test_hygiene_and_default_off()
    test_env_halt_blocks_and_unhalt_restores()
    test_halt_allows_get_and_preview()
    test_spot_writers_and_layers()
    test_file_arm_env_off_wins_and_self_heal_report_only()
    test_full_smoke_fence_notes_halt()
    print("kill-switch: PASS")
