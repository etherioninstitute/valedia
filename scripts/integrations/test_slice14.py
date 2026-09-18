#!/usr/bin/env python3
"""Slice 14 — Burton-gated spot GET (SPOT_RECONCILE_HTTP default False).

Default/cron = mock, no HTTP. Flag-on in tests = mocked transport only.
No network, no keys, no --live, no nautilus_trader.
Run: python3 test_slice14.py
"""
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import forward_test_ledger as ftl
import reconcile_run as rr
import spot_order_get as sog
import spot_reconcile as sr
import valedia_config as vc


def _restore():
    vc.SPOT_RECONCILE_HTTP = False
    vc.NAUTILUS_ENABLED = False
    vc.NAUTILUS_LIVE = False
    vc.REBALANCER_AUTOEXEC = False
    vc.VENUE_WRITER["coinbase"] = "valedia"
    vc.VENUE_WRITER["kalshi"] = "valedia"
    vc.VENUE_WRITER["alpaca"] = "valedia"
    vc.VENUE_WRITER["hyperliquid"] = "none"
    vc.VENUE_WRITER["polymarket"] = "none"
    os.environ.pop("SPOT_RECONCILE_HTTP", None)
    os.environ.pop("REBALANCER_AUTOEXEC", None)
    rr.set_spot_order_getter(None)
    sog.set_spot_http_transport()


def _flags():
    return {
        "NAUTILUS_ENABLED": vc.NAUTILUS_ENABLED,
        "NAUTILUS_LIVE": vc.NAUTILUS_LIVE,
        "SPOT_RECONCILE_HTTP": vc.spot_reconcile_http_enabled(),
        "REBALANCER_AUTOEXEC": vc.rebalancer_autoexec_enabled(),
        "VENUE_WRITER": dict(vc.VENUE_WRITER),
        "SPOT_RECONCILE_HTTP_CONST": vc.SPOT_RECONCILE_HTTP,
    }


def _assert_fence(before=None):
    now = _flags()
    if before is not None:
        assert now == before, (before, now)
    assert vc.NAUTILUS_ENABLED is False
    assert vc.NAUTILUS_LIVE is False
    assert vc.SPOT_RECONCILE_HTTP is False
    assert vc.spot_reconcile_http_enabled() is False
    assert vc.REBALANCER_AUTOEXEC is False
    assert vc.VENUE_WRITER["coinbase"] == "valedia"
    assert vc.VENUE_WRITER["hyperliquid"] == "none"
    assert vc.VENUE_WRITER["polymarket"] == "none"


def _ledger():
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    orig = ftl.LEDGER
    ftl.LEDGER = path
    return orig, path


def _drop_ledger(orig, path):
    ftl.LEDGER = orig
    try:
        os.remove(path)
    except OSError:
        pass


def _cb_get(oid="oid-s14", size="0.01", px="2000", fee="0.22"):
    return {"order": {
        "order_id": oid, "product_id": "ETH-USD", "side": "BUY", "status": "FILLED",
        "filled_size": size, "average_filled_price": px, "total_fees": fee,
    }}


def _alp_get(oid="a1", qty="2", px="10"):
    return {"id": oid, "status": "filled", "filled_qty": qty, "filled_avg_price": px}


def test_fence_and_single_flip_docs():
    _restore()
    before = _flags()
    src_rr = open(os.path.join(HERE, "reconcile_run.py")).read()
    src_sog = open(os.path.join(HERE, "spot_order_get.py")).read()
    src_vc = open(os.path.join(HERE, "valedia_config.py")).read()
    src_sr = open(os.path.join(HERE, "spot_reconcile.py")).read()

    assert "import requests" not in src_rr
    assert "import coinbase_trader" not in src_rr
    assert "from coinbase_trader" not in src_rr
    assert "import alpaca_executor" not in src_rr
    assert "import nautilus_trader" not in src_rr
    assert "No --live" in src_rr
    assert "argparse" not in src_rr
    assert "import requests" not in src_sog.split("def ")[0]
    assert "from coinbase_trader import" not in src_sog.split("def coinbase_order_get")[0]
    assert "def set_spot_http_transport" in src_sog
    assert "def live_spot_order_getter" in src_sog
    assert re.search(r"^SPOT_RECONCILE_HTTP = False", src_vc, re.M)
    assert "export SPOT_RECONCILE_HTTP=1" in src_vc
    assert "SPOT_RECONCILE_HTTP = True" in src_vc
    assert "export SPOT_RECONCILE_HTTP=1" in src_rr
    assert "set_spot_http_transport" in src_rr
    assert "SPOT_RECONCILE_HTTP" in src_sr
    assert vc.SPOT_RECONCILE_HTTP is False
    assert vc.spot_reconcile_http_enabled() is False
    _assert_fence(before)


def test_default_off_mock_no_http():
    _restore()
    orig, path = _ledger()
    calls = []
    try:
        def _must_not(method, path):
            calls.append((method, path))
            raise RuntimeError("must not HTTP")

        sog.set_spot_http_transport(request=_must_not, alpaca_get=lambda p: calls.append(p))
        assert rr.get_spot_order_getter() is rr.mock_spot_order_getter
        assert rr.mock_spot_order_getter("oid", "coinbase") is None
        eid = ftl.book_spot_fill(
            "CRYPTO_MOMENTUM", "ETH-USD", "buy", "coinbase",
            {"success": True, "order_id": "oid-s14-off", "usd": 20.0},
            intended_source="live")
        stub = [r for r in ftl._read() if r.get("id") == eid][0]
        assert stub["source"] == "unreconciled"
        rec = rr.reconcile_unreconciled_run()
        assert rec["upgraded"] == 0, rec
        still = [r for r in ftl._read() if r.get("id") == eid][0]
        assert still["source"] == "unreconciled"
        assert still["fill_status"] == "UNRECONCILED"
        rc = rr.run(kalshi=False)
        assert rc == 0
        still = [r for r in ftl._read() if r.get("id") == eid][0]
        assert still["source"] == "unreconciled"
        # Cron path never invoked the transport while flag is off.
        assert calls == []
        # Direct helper with inject is test-only (fail-closed on boom).
        assert sog.coinbase_order_get("x") is None
        assert calls == [("GET", sr.coinbase_order_get_path("x"))]
        sog.set_spot_http_transport()
        assert sog.live_spot_order_getter("oid-s14-off", "coinbase") is None
        assert sog.coinbase_order_get("oid-s14-off") is None
        assert sog.alpaca_order_get("oid-s14-off") is None
        assert rr.get_spot_order_getter() is rr.mock_spot_order_getter
    finally:
        _drop_ledger(orig, path)
        _restore()


def test_flag_on_mocked_transport_upgrades():
    _restore()
    orig, path = _ledger()
    seen = []
    try:
        eid = ftl.book_spot_fill(
            "CRYPTO_MOMENTUM", "ETH-USD", "buy", "coinbase",
            {"success": True, "order_id": "oid-s14", "usd": 20.0},
            intended_source="live")
        stub = [r for r in ftl._read() if r.get("id") == eid][0]
        assert stub["source"] == "unreconciled"

        def _req(method, path):
            seen.append((method, path))
            assert method == "GET"
            assert path == sr.coinbase_order_get_path("oid-s14")
            return _cb_get("oid-s14")

        def _alp(p):
            seen.append(("ALPACA", p))
            raise RuntimeError("alpaca must not run on coinbase row")

        sog.set_spot_http_transport(request=_req, alpaca_get=_alp)
        os.environ["SPOT_RECONCILE_HTTP"] = "1"
        assert vc.spot_reconcile_http_enabled() is True
        assert vc.SPOT_RECONCILE_HTTP is False  # constant stays False; env is the flip
        g = rr.get_spot_order_getter()
        assert g is sog.live_spot_order_getter

        rec = rr.reconcile_unreconciled_run()  # no getter arg — runtime path
        assert rec["upgraded"] == 1, rec
        assert seen == [("GET", "/api/v3/brokerage/orders/historical/oid-s14")]
        filled = [r for r in ftl._read()
                  if r.get("type") == "entry" and r.get("order_id") == "oid-s14"
                  and r.get("fill_status") == "FILLED"]
        assert filled and filled[0]["source"] == "live"
        assert filled[0]["entry_fee"] > 0
        assert ftl.real_track_record()["n"] == 0
        assert ftl.open_position_count() == 1
    finally:
        _drop_ledger(orig, path)
        _restore()
    _assert_fence()


def test_flag_on_alpaca_mocked_and_incomplete():
    _restore()
    orig, path = _ledger()
    alp_seen = []
    try:
        eid_a = ftl.book_spot_fill(
            "EQUITY_MOMENTUM", "AAPL", "buy", "alpaca",
            {"id": "a1", "status": "accepted"},
            intended_source="live")
        eid_m = ftl.book_spot_fill(
            "CRYPTO_MOMENTUM", "BTC-USD", "buy", "coinbase",
            {"success": True, "order_id": "oid-miss", "usd": 10.0},
            intended_source="live")

        def _req(method, path):
            return {"order": {"order_id": "oid-miss", "status": "OPEN", "filled_size": "0"}}

        def _alp(p):
            alp_seen.append(p)
            return _alp_get("a1")

        sog.set_spot_http_transport(request=_req, alpaca_get=_alp)
        os.environ["SPOT_RECONCILE_HTTP"] = "1"
        rec = rr.reconcile_unreconciled_run()
        assert rec["n"] >= 2
        filled = [r for r in ftl._read()
                  if r.get("order_id") == "a1" and r.get("fill_status") == "FILLED"]
        assert filled and filled[0]["source"] == "live"
        assert alp_seen == [sr.alpaca_order_get_path("a1")]
        still = [r for r in ftl._read() if r.get("id") == eid_m][0]
        assert still["source"] == "unreconciled"
        assert still["fill_status"] == "UNRECONCILED"
        pap_n = ftl.real_track_record()["n"]
        eid_p = ftl.book_spot_fill(
            "EQUITY_MOMENTUM", "MSFT", "buy", "alpaca",
            {"id": "p-s14", "status": "accepted"},
            intended_source="paper")

        def _alp2(p):
            oid = p.rsplit("/", 1)[-1]
            return _alp_get(oid)

        sog.set_spot_http_transport(request=_req, alpaca_get=_alp2)
        rr.reconcile_unreconciled_run()
        pap = [r for r in ftl._read()
               if r.get("order_id") == "p-s14" and r.get("fill_status") == "FILLED"]
        assert pap and pap[0]["source"] == "paper"
        assert ftl.real_track_record()["n"] == pap_n
    finally:
        _drop_ledger(orig, path)
        _restore()


def test_config_constant_flip_and_env_off():
    _restore()
    orig, path = _ledger()
    seen = []
    try:
        ftl.book_spot_fill(
            "CRYPTO_MOMENTUM", "ETH-USD", "buy", "coinbase",
            {"success": True, "order_id": "oid-cfg", "usd": 20.0},
            intended_source="live")

        def _req(method, path):
            seen.append(path)
            return _cb_get("oid-cfg")

        sog.set_spot_http_transport(request=_req)
        os.environ.pop("SPOT_RECONCILE_HTTP", None)
        vc.SPOT_RECONCILE_HTTP = True
        assert vc.spot_reconcile_http_enabled() is True
        assert rr.get_spot_order_getter() is sog.live_spot_order_getter
        rec = rr.reconcile_unreconciled_run()
        assert rec["upgraded"] == 1, rec
        assert seen == [sr.coinbase_order_get_path("oid-cfg")]

        # Env 0/false wins over constant True — back to mock, no more HTTP.
        os.environ["SPOT_RECONCILE_HTTP"] = "0"
        assert vc.spot_reconcile_http_enabled() is False
        assert rr.get_spot_order_getter() is rr.mock_spot_order_getter
        os.environ["SPOT_RECONCILE_HTTP"] = "false"
        assert vc.spot_reconcile_http_enabled() is False
    finally:
        _drop_ledger(orig, path)
        _restore()
    assert vc.SPOT_RECONCILE_HTTP is False
    assert vc.spot_reconcile_http_enabled() is False


def test_unknown_venue_and_empty_id():
    _restore()
    os.environ["SPOT_RECONCILE_HTTP"] = "1"
    sog.set_spot_http_transport(
        request=lambda m, p: (_ for _ in ()).throw(RuntimeError("no")),
        alpaca_get=lambda p: (_ for _ in ()).throw(RuntimeError("no")),
    )
    try:
        assert sog.live_spot_order_getter("", "coinbase") is None
        assert sog.live_spot_order_getter("oid", "kalshi") is None
        assert sog.coinbase_order_get("") is None
        assert sog.alpaca_order_get("") is None
    finally:
        _restore()


def run():
    _restore()
    test_fence_and_single_flip_docs()
    test_default_off_mock_no_http()
    test_flag_on_mocked_transport_upgrades()
    test_flag_on_alpaca_mocked_and_incomplete()
    test_config_constant_flip_and_env_off()
    test_unknown_venue_and_empty_id()
    _restore()
    _assert_fence()
    print("slice-14 checks: PASS")


if __name__ == "__main__":
    run()
