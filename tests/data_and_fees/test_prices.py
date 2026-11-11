import numpy as np
import pandas as pd

from valedia_trader.data_and_fees import prices as px


def _frames(last_day):
    idx = pd.bdate_range(end=last_day, periods=6)
    close = pd.DataFrame({"SPY": np.linspace(100, 105, 6), "BTC-USD": np.linspace(50, 55, 6)}, index=idx)
    return close, close * 10


def test_a_bar_for_a_session_still_running_is_dropped():
    close, volume = _frames("2026-11-11")
    c, v, dropped = px.drop_unfinished_session(close, volume, now=pd.Timestamp("2026-11-11 12:05", tz="America/New_York"))
    assert dropped and c.index[-1] == pd.Timestamp("2026-11-10") and len(v) == len(c) == 5
    c, v, dropped = px.drop_unfinished_session(close, volume, now=pd.Timestamp("2026-11-11 16:30", tz="America/New_York"))
    assert not dropped and len(c) == 6                                 # the session is over: the bar is final
    c, v, dropped = px.drop_unfinished_session(close, volume, now=pd.Timestamp("2026-11-12 09:00", tz="America/New_York"))
    assert not dropped                                                 # yesterday's bar is complete
    c, v, dropped = px.drop_unfinished_session(close, volume, now=pd.Timestamp("2026-11-11 17:05", tz="UTC"))
    assert dropped                                                     # 17:05 UTC is 12:05 in New York


def test_refresh_never_stores_an_unfinished_bar(tmp_path, monkeypatch):
    today = pd.Timestamp.now(tz="America/New_York").normalize().tz_localize(None)
    idx = pd.bdate_range(end=today, periods=8)
    if idx[-1] != today:                                               # a weekend: there is no bar for today to drop
        idx = idx.append(pd.DatetimeIndex([today]))
    raw = pd.concat({"Close": pd.DataFrame({"SPY": np.arange(len(idx), dtype=float) + 100}, index=idx),
                     "Volume": pd.DataFrame({"SPY": np.full(len(idx), 1e6)}, index=idx)}, axis=1)
    monkeypatch.setattr(px, "drop_unfinished_session", lambda c, v, now=None: (c.iloc[:-1], v.iloc[:-1], True))
    out = px.refresh(tmp_path, symbols=("SPY",), download=lambda tickers, begin: raw)
    stored = pd.read_parquet(tmp_path / "close.parquet")
    assert out["unfinished_session_dropped"] is True and stored.index[-1] < today and out["last"] == str(stored.index[-1].date())
