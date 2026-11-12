"""Loading a price folder and costing its symbols must not assume the core universe."""
import numpy as np
import pandas as pd

from valedia_trader.backtest import walkforward as wf
from valedia_trader.data_and_fees.prices import UNIVERSE
from valedia_trader.data_and_fees.venue_registry import VenueRegistry


def _folder(tmp_path, columns, days=6, holes=None):
    idx = pd.bdate_range("2020-01-06", periods=days)
    close = pd.DataFrame({c: 100.0 + np.arange(days) for c in columns}, index=idx)
    for column, day in holes or []:
        close.loc[idx[day], column] = np.nan
    volume = pd.DataFrame(1_000.0, index=idx, columns=columns)
    close.to_parquet(tmp_path / "close.parquet")
    volume.to_parquet(tmp_path / "volume.parquet")
    return idx


def test_a_price_folder_without_spy_loads_on_the_days_its_funds_traded(tmp_path):
    idx = _folder(tmp_path, ["FXE", "USO", "EWJ"], holes=[("USO", 2)])
    close, dollar = wf.load_universe(tmp_path)
    assert list(close.columns) == ["EWJ", "FXE", "USO"]
    assert list(close.index) == list(idx)                      # a day one fund missed is still a trading day
    assert dollar.loc[idx[0], "FXE"] == 100.0 * 1_000.0


def test_with_spy_present_the_calendar_is_still_spys(tmp_path):
    idx = _folder(tmp_path, ["SPY", "BTC-USD", "TLT"], holes=[("SPY", 3)])
    close, _ = wf.load_universe(tmp_path)
    assert list(close.index) == [d for i, d in enumerate(idx) if i != 3]


def test_a_fund_outside_the_core_universe_is_costed_at_a_wider_spread():
    registry = VenueRegistry()
    try:
        cost = wf.build_cost_model(["SPY", "RWX", "BTC-USD"], registry, as_of=pd.Timestamp("2026-11-12"))
    finally:
        registry.close()
    assert "RWX" not in UNIVERSE and "SPY" in UNIVERSE
    assert cost.slippage_bps["SPY"] == wf.HALF_SPREAD_BPS["etf"] == 1.0          # the vetted, liquid funds are unchanged
    assert cost.slippage_bps["BTC-USD"] == wf.HALF_SPREAD_BPS["crypto"]
    assert cost.slippage_bps["RWX"] == wf.HALF_SPREAD_BPS["etf_unvetted"] == 5.0  # not measured yet: assume the worse case
    assert cost.fees_bps["RWX"] == cost.fees_bps["SPY"]                          # same venue, same commission
