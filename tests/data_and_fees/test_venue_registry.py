import math
from datetime import timedelta

import pandas as pd
import pytest

from valedia_trader.data_and_fees.venue_registry import (REFRESH_PLAN, SEED_FEES, UnverifiedFeeError,
                                                         VenueRegistry)


@pytest.fixture()
def reg():
    r = VenueRegistry()
    yield r
    r.close()


def test_every_venue_class_is_registered(reg):
    for venue in ("alpaca", "interactive_brokers", "tradier", "binance", "coinbase", "bybit", "hyperliquid",
                  "okx", "kraken", "polymarket", "kalshi", "the_odds_api"):
        assert venue in reg.venues()
    assert set(REFRESH_PLAN) <= set(reg.venues())


def test_every_seed_row_names_its_source():
    for row in SEED_FEES:
        assert row[7] and (row[10] is False or row[7].startswith("src/"))


# Expected values below are computed by hand from the legacy constants named in each comment.
def test_seeded_fees_reproduce_the_legacy_numbers(reg):
    # src/fee_engine.py STATIC_FEES['kraken']: maker 0.0016, taker 0.0026
    assert reg.fee_for("kraken", "spot", "taker", notional=10_000).total == pytest.approx(26.0)
    assert reg.fee_for("kraken", "spot", "maker", notional=10_000).bps == pytest.approx(16.0)
    # STATIC_FEES['coinbase']: taker 0.006 plus spread 0.005 -> 110 bps all-in
    cb = reg.fee_for("coinbase", "spot", "taker", notional=1_000)
    assert (cb.commission, cb.extra, cb.total) == pytest.approx((6.0, 5.0, 11.0))
    # STATIC_FEES['alpaca']: stocks commission 0, options 0.65 per contract
    assert reg.fee_for("alpaca", "equity", "taker", notional=50_000).total == 0.0
    assert reg.fee_for("alpaca", "option", "taker", quantity=10).total == pytest.approx(6.5)
    # STATIC_FEES['polymarket']: zero trading fee, 0.01 gas
    assert reg.fee_for("polymarket", "event_contract", "taker", notional=500).total == pytest.approx(0.01)
    # STATIC_FEES['defi_uniswap']: swap 0.003 plus 0.50 gas
    assert reg.fee_for("defi_uniswap", "swap", "taker", notional=1_000).total == pytest.approx(3.5)


def test_kalshi_quadratic_fee_rounds_up_to_the_cent(reg):
    # src/kalshi_fee_map.py: taker round_up(0.07 * C * P * (1 - P)), maker coefficient 0.0175
    taker = reg.fee_for("kalshi", "event_contract", "taker", quantity=100, price=0.50)
    assert taker.total == pytest.approx(1.75)                       # 0.07 * 100 * 0.25
    assert reg.fee_for("kalshi", "event_contract", "maker", quantity=100, price=0.50).total == pytest.approx(0.44)
    assert reg.fee_for("kalshi", "event_contract", "taker", quantity=3, price=0.10).total == pytest.approx(0.02)
    assert taker.bps == pytest.approx(1.75 / 50 * 1e4)
    assert reg.fee_for("kalshi", "event_contract", "taker", quantity=0, price=0.5).total == 0.0
    with pytest.raises(ValueError):
        reg.fee_for("kalshi", "event_contract", "taker", quantity=10, price=1.0)


def test_unknown_fees_fail_closed(reg):
    for venue, product in (("binance", "spot"), ("interactive_brokers", "equity"), ("hyperliquid", "perp"),
                           ("the_odds_api", "sportsbook_bet"), ("alpaca", "equity_regulatory")):
        with pytest.raises(UnverifiedFeeError):
            reg.fee_for(venue, product, "taker", notional=1_000)
    with pytest.raises(UnverifiedFeeError):
        reg.fee_for("kraken", "options", "taker", notional=1_000)   # product with no row at all
    with pytest.raises(KeyError):
        reg.fee_for("nowhere", "spot", "taker", notional=1_000)
    assert ("binance", "spot") in reg.unverified() and ("kraken", "spot") not in reg.unverified()
    with pytest.raises(ValueError):
        reg.upsert_fee("binance", "spot", "bps", None, None, effective_from="2026-01-01", source="x",
                       as_of="2026-01-01", verified=True)


def test_fee_lookups_are_point_in_time(reg):
    reg.upsert_fee("binance", "spot", "bps", 10.0, 10.0, effective_from="2026-06-01", known_at="2026-05-20",
                   source="test fixture", as_of="2026-05-20", verified=True)
    reg.upsert_fee("binance", "spot", "bps", 7.5, 7.5, effective_from="2026-09-01", known_at="2026-08-15",
                   source="test fixture", as_of="2026-08-15", verified=True)
    assert reg.fee_for("binance", "spot", "taker", notional=10_000, as_of="2026-07-01").total == pytest.approx(10.0)
    assert reg.fee_for("binance", "spot", "taker", notional=10_000, as_of="2026-09-02").total == pytest.approx(7.5)
    assert reg.fee_for("binance", "spot", "taker", notional=10_000, as_of="2026-08-31").total == pytest.approx(10.0)
    with pytest.raises(UnverifiedFeeError):                         # before the first verified row took effect
        reg.fee_for("binance", "spot", "taker", notional=10_000, as_of="2026-05-31")
    assert reg.round_trip_cost_bps("binance", "spot", 10_000, as_of="2026-10-01") == pytest.approx(15.0)
    # a correction to the 2026-03-13 schedule, learned on 2026-10-01, must not leak into earlier as-of queries
    reg.upsert_fee("kraken", "spot", "bps", 1.0, 1.0, effective_from="2026-03-13", known_at="2026-10-01",
                   source="test fixture", as_of="2026-10-01", verified=True)
    assert reg.fee_for("kraken", "spot", "taker", notional=10_000, as_of="2026-09-01").total == pytest.approx(26.0)
    assert reg.fee_for("kraken", "spot", "taker", notional=10_000, as_of="2026-10-02").total == pytest.approx(1.0)


def test_staleness_report(reg):
    stale = reg.stale_fees(90, today="2026-11-10")
    assert {"kraken", "coinbase", "alpaca", "kalshi"} <= set(stale["venue"])
    assert reg.stale_fees(100_000, today="2026-11-10").empty


def test_funding_is_never_assumed(reg):
    with pytest.raises(UnverifiedFeeError):
        reg.funding_cost("hyperliquid", "BTC", 10_000, 8)
    reg.record_funding("hyperliquid", "BTC", 1.0, 0.0000125, "2026-11-01 00:00", "test fixture")
    reg.record_funding("hyperliquid", "BTC", 1.0, -0.00002, "2026-11-02 00:00", "test fixture")
    assert reg.funding_cost("hyperliquid", "BTC", 10_000, 8, as_of="2026-11-01 12:00") == pytest.approx(1.0)
    assert reg.funding_cost("hyperliquid", "BTC", 10_000, 8, as_of="2026-11-03") == pytest.approx(-1.6)
    with pytest.raises(UnverifiedFeeError):
        reg.funding_cost("hyperliquid", "BTC", 10_000, 8, as_of="2026-10-31")


def test_nyse_sessions_holidays_and_early_closes(reg):
    assert reg.is_open("alpaca", "2026-11-10 15:00")                 # Tuesday 10:00 New York
    assert not reg.is_open("alpaca", "2026-11-10 22:30")             # after the 16:00 close
    assert not reg.is_open("alpaca", "2026-11-26 16:00")             # Thanksgiving
    assert not reg.is_open("alpaca", "2026-11-14 16:00")             # Saturday
    assert reg.next_close("alpaca", "2026-11-10 15:00") == pd.Timestamp("2026-11-10 21:00", tz="UTC")
    # Day after Thanksgiving closes at 13:00 New York (18:00 UTC)
    assert reg.next_close("alpaca", "2026-11-27 15:00") == pd.Timestamp("2026-11-27 18:00", tz="UTC")
    assert reg.time_to_close("alpaca", "2026-11-10 20:45") == timedelta(minutes=15)
    assert reg.next_close("alpaca", "2026-11-14 16:00") == pd.Timestamp("2026-11-16 21:00", tz="UTC")


def test_pre_close_flatten_rule(reg):
    assert reg.pre_close_action("alpaca", "2026-11-10 20:50").must_flatten
    calm = reg.pre_close_action("alpaca", "2026-11-10 18:00")
    assert not calm.must_flatten and calm.minutes_to_close == pytest.approx(180)
    assert reg.pre_close_action("alpaca", "2026-11-27 17:50").must_flatten        # early close
    assert not reg.pre_close_action("alpaca", "2026-11-14 16:00").must_flatten    # closed: nothing to do
    assert not reg.pre_close_action("kraken", "2026-11-10 20:50").must_flatten    # 24x7, nothing scheduled
    game = reg.pre_close_action("kalshi", "2026-11-10 20:50", market_close="2026-11-10 21:00")
    assert game.must_flatten and game.minutes_to_close == pytest.approx(10)


def test_crypto_maintenance_windows(reg):
    assert reg.is_open("binance", "2026-11-12 02:30") and reg.next_close("binance", "2026-11-12 02:30") is None
    reg.add_maintenance("binance", "2026-11-12 03:00", "2026-11-12 05:00", "scheduled upgrade")
    assert reg.next_close("binance", "2026-11-12 02:30") == pd.Timestamp("2026-11-12 03:00", tz="UTC")
    assert reg.pre_close_action("binance", "2026-11-12 02:50").must_flatten
    assert not reg.is_open("binance", "2026-11-12 04:00")
    assert reg.is_open("binance", "2026-11-12 05:00")
    with pytest.raises(ValueError):
        reg.add_maintenance("binance", "2026-11-12 05:00", "2026-11-12 03:00", "backwards")


def test_registry_persists_to_disk(tmp_path):
    path = str(tmp_path / "venues.duckdb")
    first = VenueRegistry(path)
    first.upsert_fee("okx", "spot", "bps", 8.0, 10.0, effective_from="2026-01-01", known_at="2026-01-01",
                     source="test fixture", as_of="2026-01-01", verified=True)
    first.close()
    again = VenueRegistry(path)
    assert again.fee_for("okx", "spot", "maker", notional=10_000, as_of="2026-02-01").total == pytest.approx(8.0)
    assert math.isclose(again.fee_for("kraken", "spot", "taker", notional=10_000).total, 26.0)
    again.close()
