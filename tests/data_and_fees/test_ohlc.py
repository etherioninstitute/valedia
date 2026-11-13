import numpy as np
import pandas as pd
import pytest

from valedia_trader.data_and_fees import ohlc

FIELDS = ("Open", "High", "Low", "Close", "Volume")


def _raw(symbols, days=30, last_day="2024-03-28", empty=()):
    """What yfinance returns: one column per (field, symbol). A symbol in `empty` came back with no prices."""
    idx = pd.bdate_range(end=last_day, periods=days)
    cols = {}
    for k, sym in enumerate(symbols):
        close = 100.0 + k + np.arange(days, dtype=float)
        vals = {"Open": close - 0.5, "High": close + 1.0, "Low": close - 1.0, "Close": close, "Volume": np.full(days, 1e6)}
        for f in FIELDS:
            cols[(f, sym)] = np.full(days, np.nan) if sym in empty else vals[f]
    return pd.DataFrame(cols, index=idx)


def test_refresh_writes_the_five_daily_fields_and_load_returns_them_on_one_index(tmp_path):
    seen = {}
    def fake(tickers, begin):
        seen.update(tickers=tuple(tickers), begin=begin)
        return _raw(tickers)
    out = ohlc.refresh_ohlc(tmp_path, ("SPY", "GLD"), "2012-01-01", download=fake)
    assert seen == {"tickers": ("SPY", "GLD"), "begin": "2012-01-01"}
    for name in ("open", "high", "low", "close", "volume"):
        assert (tmp_path / f"{name}.parquet").exists()
    assert out["symbols"] == 2 and out["rows"] == 30 and out["last"] == "2024-03-28"
    got = ohlc.load_ohlc(tmp_path)
    assert set(got) == {"open", "high", "low", "close", "volume"}
    idx = got["close"].index
    assert all(got[f].index.equals(idx) for f in got) and all(list(got[f].columns) == ["GLD", "SPY"] for f in got)
    assert float(got["high"]["SPY"].iloc[0]) == 101.0 and float(got["low"]["SPY"].iloc[0]) == 99.0


def test_refresh_fails_closed_when_a_symbol_comes_back_empty(tmp_path):
    ohlc.refresh_ohlc(tmp_path, ("SPY", "GLD"), "2012-01-01", download=lambda t, b: _raw(t))
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    with pytest.raises(RuntimeError, match="GLD"):
        ohlc.refresh_ohlc(tmp_path, ("SPY", "GLD"), "2012-01-01", download=lambda t, b: _raw(t, last_day="2024-04-30", empty=("GLD",)))
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before          # the stored files were left alone


def test_refresh_fails_closed_when_one_field_is_missing_for_a_symbol(tmp_path):
    raw = _raw(("SPY", "GLD"))
    raw[("Open", "GLD")] = np.nan
    with pytest.raises(RuntimeError, match="GLD"):
        ohlc.refresh_ohlc(tmp_path, ("SPY", "GLD"), "2012-01-01", download=lambda t, b: raw)
    assert list(tmp_path.iterdir()) == []


def test_refresh_never_stores_a_bar_for_a_session_still_running(tmp_path, monkeypatch):
    from valedia_trader.data_and_fees import prices
    monkeypatch.setattr(prices, "drop_unfinished_session", lambda c, v, now=None: (c.iloc[:-1], v.iloc[:-1], True))
    out = ohlc.refresh_ohlc(tmp_path, ("SPY",), "2012-01-01", download=lambda t, b: _raw(t))
    got = ohlc.load_ohlc(tmp_path)
    assert out["unfinished_session_dropped"] is True and out["last"] == "2024-03-27"
    assert all(got[f].index[-1] == pd.Timestamp("2024-03-27") for f in got)
