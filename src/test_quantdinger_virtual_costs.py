#!/usr/bin/env python3
"""QUANTDINGER Phase 1 — offline virtual fills. No network. No orders.

Run: python3 test_quantdinger_virtual_costs.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)

# Unit tests assert git-default OFF without Burton's ~/.openclaw/.env flips.
os.environ["OPENCLAW_SKIP_ENV"] = "1"

import crypto_paper as cp
import forward_test_ledger as ftl
import quantdinger_virtual_costs as qv
import valedia_config as vc


def test_transitions():
    flat = qv.calculate_virtual_fill("open_long", 2, 100, 0, 0)
    assert flat["status"] == "filled" and flat["fill_quantity"] == 2
    assert flat["next_size"] == 2 and flat["next_entry_price"] == 100
    assert flat["gross_realized_pnl"] == 0

    add = qv.calculate_virtual_fill("add_long", 2, 110, 2, 100)
    assert add["next_size"] == 4 and add["next_entry_price"] == 105
    assert add["gross_realized_pnl"] == 0

    red = qv.calculate_virtual_fill("reduce_long", 1, 120, 4, 105)
    assert red["fill_quantity"] == 1 and red["next_size"] == 3
    assert red["next_entry_price"] == 105 and red["gross_realized_pnl"] == 15

    close = qv.calculate_virtual_fill("close_long", 0, 90, 3, 105)
    assert close["fill_quantity"] == 3 and close["next_size"] == 0
    assert close["gross_realized_pnl"] == (90 - 105) * 3
    cap = qv.calculate_virtual_fill("close_long", 99, 90, 3, 105)
    assert cap["fill_quantity"] == 3

    short = qv.calculate_virtual_fill("open_short", 1, 50, 0, 0)
    assert short["next_size"] == 1 and short["next_entry_price"] == 50
    sadd = qv.calculate_virtual_fill("add_short", 1, 40, 1, 50)
    assert sadd["next_entry_price"] == 45 and sadd["gross_realized_pnl"] == 0
    sred = qv.calculate_virtual_fill("reduce_short", 1, 30, 2, 45)
    assert sred["gross_realized_pnl"] == (45 - 30) * 1
    assert sred["next_size"] == 1 and sred["next_entry_price"] == 45
    sclose = qv.calculate_virtual_fill("close_short", 0, 60, 1, 45)
    assert sclose["fill_quantity"] == 1
    assert sclose["gross_realized_pnl"] == (45 - 60) * 1
    assert sclose["next_size"] == 0

    bad = qv.calculate_virtual_fill("buy", 1, 100, 1, 100)
    assert bad["status"] == "rejected" and bad["next_size"] == 1
    assert qv.calculate_virtual_fill("open_long", 0, 100)["status"] == "rejected"
    assert qv.calculate_virtual_fill("open_short", 1, 0)["status"] == "rejected"
    assert qv.calculate_virtual_fill("reduce_long", 1, 100, 0, 0)["status"] == "no_position"
    assert qv.calculate_virtual_fill("close_short", 1, 0, 2, 10)["status"] == "no_position"


def test_costs_ignore_leverage():
    assert qv.fill_price("open_long", 100) == cp.fill_px(100, "buy", qv.SLIPPAGE_RATE)
    assert qv.fill_price("close_long", 100) == cp.fill_px(100, "sell", qv.SLIPPAGE_RATE)
    assert qv.fill_price("open_short", 100) == cp.fill_px(100, "sell", qv.SLIPPAGE_RATE)
    assert qv.fill_price("close_short", 100) == cp.fill_px(100, "buy", qv.SLIPPAGE_RATE)
    assert qv.fill_price("open_long", 100) > 100
    assert qv.fill_price("open_short", 100) < 100

    same = qv.commission_quote(2, 100, leverage=10)
    assert same == qv.commission_quote(2, 100, leverage=1)
    assert same == cp.fee_usd(200, "taker", 0.0005, 0.0005)
    assert same == 0.1
    px = qv.fill_price("open_long", 100)
    assert qv.slippage_quote(2, 100, px) == 2 * (px - 100)
    cmp_ = qv.cost_compare(100)
    assert cmp_["quantdinger_commission"] == 0.05
    assert cmp_["crypto_paper_fee"] == 0.1
    assert cmp_["default_book"] == "CRYPTO_FEE_PCT"
    assert vc.CRYPTO_FEE_PCT == 0.001 and vc.CRYPTO_SLIPPAGE_PCT == 0.0005
    assert cp.FEE_TABLE == "CRYPTO_FEE_PCT"


def test_labels_and_fence():
    off = qv.paper_decision(opt_in=False)
    on = qv.paper_decision(opt_in=True)
    for row in (off, on):
        assert row["create_order"] is False
        assert row["live"] is False
        assert row["counts_toward_kelly"] is False
        assert row["source"] == "quantdinger_paper"
        assert row["oil"] == "NO_HARVEST"
        assert row["weather"] == "watch"
        assert row["liquidity_role"] == "taker"
        assert row["commission_rate"] == 0.0005
        assert row["slippage_rate"] == 0.0005
        assert row["leverage_in_commission"] is False
        assert ftl._is_non_real_source(row["source"]) is True
        assert len(row["steps"]) == 8
        assert row["steps"][0]["action"] == "open_long"
        assert row["steps"][3]["next_size"] == 0
        assert row["steps"][-1]["action"] == "close_short"
        assert row["steps"][-1]["next_size"] == 0
        assert row["steps"][0]["commission"] == round(qv.commission_quote(
            row["steps"][0]["fill_quantity"], row["steps"][0]["fill_price"],
            leverage=row["steps"][0]["leverage"]), 8)
    assert off["fee_table"] == "CRYPTO_FEE_PCT" and off["opt_in"] is False
    assert on["fee_table"] == "quantdinger_virtual" and on["opt_in"] is True
    assert "KXWTI" in vc.LIVE_EXEC_FENCED_SERIES
    assert "OIL_BRACKET" in vc.LIVE_EXEC_FENCED
    assert "WEATHER_ARB" in vc.LIVE_EXEC_FENCED


def test_fixture_file():
    path = os.path.join(ROOT, "data", "quantdinger-paper", "decision.json")
    with open(path) as f:
        disk = json.load(f)
    fresh = qv.paper_decision(opt_in=True)
    assert disk == json.loads(json.dumps(fresh))
    assert disk["create_order"] is False
    assert disk["counts_toward_kelly"] is False
    assert disk["fee_table"] == "quantdinger_virtual"
    assert disk["default_book"] == "CRYPTO_FEE_PCT"


def test_source_fence():
    src = open(os.path.join(HERE, "quantdinger_virtual_costs.py")).read()
    for needle in (
        "live_trading", "stripe", "usdt", "docker", "compose",
        "AGENT_LIVE", "MIROFISH_AUTO_RAISE", "create_order(",
        "allocator", "--live",
    ):
        assert needle not in src, needle


def run():
    test_transitions()
    test_costs_ignore_leverage()
    test_labels_and_fence()
    test_fixture_file()
    test_source_fence()
    print("PASS")


if __name__ == "__main__":
    run()
