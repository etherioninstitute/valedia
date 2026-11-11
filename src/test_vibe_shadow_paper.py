#!/usr/bin/env python3
"""VIBE-TRADING-SUBSUME-1 — paper shadow log. No network. No broker.

Run: python3 test_vibe_shadow_paper.py
"""
import os
import socket
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Unit tests assert git-default OFF without Burton's ~/.openclaw/.env flips.
os.environ["OPENCLAW_SKIP_ENV"] = "1"

import forward_test_ledger as ftl
import valedia_config as vc
import vibe_shadow_paper as vs


def _src():
    return open(os.path.join(HERE, "vibe_shadow_paper.py")).read()


def test_fixture_fee_and_fence():
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "decisions.jsonl")
        rows = vs.run_journal(vs.fixture_journal(), path=path, env={})
    by = {r["ticker"]: r for r in rows}
    assert by["BTC-USD"]["reason"] == "shadow_log"
    assert by["BTC-USD"]["size_usd"] == 25.0
    assert by["BTC-USD"]["net_pnl"] == 38.0
    assert by["ETH-USD"]["reason"] == "fee_true"
    assert by["ETH-USD"]["size_usd"] == 0
    assert by["SOL-USD"]["reason"] == "review_only"
    assert by["DOGE-USD"]["reason"] == "missing_feature"
    for r in rows:
        assert "paper" in r["source"]
        assert r["source"] == "vibe_shadow_paper"
        assert r["create_order"] is False
        assert r["broker_write"] is False
        assert r["start_copy"] is False
        assert r["live"] is False
        assert r["filled"] is False
        assert r["counts_toward_kelly"] is False
        assert r["valedia_version"] == "0.74.1"
    assert ftl._is_non_real_source("vibe_shadow_paper") is True


def test_missing_fee_and_daily_cap():
    bare = {"symbol": "XRP-USD", "prior_5d_return": -0.20, "gross_pnl": 10.0}
    row = vs.decide(bare, {"exposure_today_usd": 0.0}, env={})
    assert row["reason"] == "missing_fee"
    assert row["create_order"] is False
    dips = [{"symbol": "BTC-USD", "prior_5d_return": -0.11,
             "gross_pnl": 8.0, "fee_usd": 0.1} for _ in range(5)]
    with tempfile.TemporaryDirectory() as td:
        rows = vs.run_journal(dips, path=os.path.join(td, "d.jsonl"), env={})
    assert [r["reason"] for r in rows] == ["shadow_log"] * 4 + ["daily_notional"]
    assert sum(r["size_usd"] for r in rows) == 100.0


def test_writes_raise_and_env_cannot_live():
    def boom(*_a, **_k):
        raise AssertionError("socket")
    orig = socket.socket
    socket.socket = boom
    try:
        for fn in (vs.place_order, vs.create_order):
            try:
                fn("ibkr", {"symbol": "BTC"})
                raise AssertionError("returned")
            except vs.VibeFence as exc:
                assert exc.code == "vibe_broker_write"
        try:
            vs.exec_generated("print(1)")
            raise AssertionError("codegen ran")
        except vs.VibeFence as exc:
            assert exc.code == "vibe_codegen"
        try:
            vs.shell("id")
            raise AssertionError("shell ran")
        except vs.VibeFence as exc:
            assert exc.code == "vibe_shell"
        blocked = vs.decide(vs.fixture_journal()[0], {"exposure_today_usd": 0},
                            env={"_attempt_order": "1", "VIBE_TRADING_LIVE": "1"})
        assert blocked["reason"] == "vibe_broker_write"
        assert blocked["create_order"] is False
        assert vs.live_enabled({"VIBE_TRADING_LIVE": "1"}) is False
        assert vs.live_enabled(os.environ) is False
    finally:
        socket.socket = orig


def test_inventory_and_source_hygiene():
    inv = vs.inventory()
    assert inv["head"] == "a65427a3d587e43cd5c7541948b36524e8fd2f12"
    assert inv["zoo_modules"] == 462
    assert inv["vendored"] is False
    assert inv["mcp_installed"] is False
    assert inv["pypi_installed"] is False
    assert inv["create_order"] is False
    assert "etoro" in inv["connectors"] and "ibkr" in inv["connectors"]
    assert "agent/src/tools/bash_tool.py" in inv["fenced"]
    assert "vaughanf1-vibe-trader" in inv["distinct_from"]
    src = _src()
    for bad in ("subprocess", "urllib", "requests", "pickle", "eval(", "exec(",
                "shell=True", "import socket"):
        assert bad not in src, bad
    assert vc.VALEDIA_VERSION == "0.74.1"


def test_kill_switch_and_empty():
    row = vs.decide(vs.fixture_journal()[0], {"exposure_today_usd": 0, "kill_switch": True}, env={})
    assert row["reason"] == "kill_switch" and row["size_usd"] == 0
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "empty.jsonl")
        rows = vs.run_journal([], path=path, env={})
        assert rows[0]["reason"] == "no_journal"
        assert vs.tick_status(path)["last_decision"] == "no_journal"
        assert vs.tick_status(os.path.join(td, "missing.jsonl"))["stale"] is True


if __name__ == "__main__":
    test_fixture_fee_and_fence()
    test_missing_fee_and_daily_cap()
    test_writes_raise_and_env_cannot_live()
    test_inventory_and_source_hygiene()
    test_kill_switch_and_empty()
    print("test_vibe_shadow_paper PASS")
