"""Daily price history for the research universe (adjusted close and volume, one column per symbol).

`refresh` downloads the whole history again and replaces the stored files only if the new data is
at least as long and reaches at least as far as the old. The previous files are kept beside them.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

__all__ = ["refresh", "UNIVERSE", "START"]

UNIVERSE = ("BTC-USD", "DBC", "EEM", "EFA", "ETH-USD", "GLD", "IEF", "IWM", "LQD", "QQQ", "SPY", "TLT", "XLE", "XLF",
            "XLK", "XLU", "XLV")
START = "2012-01-01"


def drop_unfinished_session(close: pd.DataFrame, volume: pd.DataFrame, now: pd.Timestamp | None = None,
                            session_end: str = "16:20") -> tuple[pd.DataFrame, pd.DataFrame, bool]:
    """Remove today's row while the US session is still running. A download taken during market hours
    carries a bar for today built from the prices so far; trading on it would be trading on a day that
    has not happened yet. Returns the frames and whether a row was removed."""
    now = pd.Timestamp.now(tz="America/New_York") if now is None else pd.Timestamp(now)
    if now.tzinfo is None:
        now = now.tz_localize("America/New_York")
    else:
        now = now.tz_convert("America/New_York")
    if len(close) and close.index[-1].date() == now.date() and now.strftime("%H:%M") < session_end:
        return close.iloc[:-1], volume.iloc[:-1], True
    return close, volume, False


def refresh(data_dir: str | Path, symbols: tuple[str, ...] | None = None, start: str = START, download=None) -> dict:
    """Update `close.parquet` and `volume.parquet` in `data_dir`. Returns what changed."""
    d = Path(data_dir)
    old_close = pd.read_parquet(d / "close.parquet") if (d / "close.parquet").exists() else None
    symbols = tuple(symbols or (old_close.columns if old_close is not None else UNIVERSE))
    if download is None:
        import yfinance as yf

        def download(tickers, begin):
            return yf.download(list(tickers), start=begin, auto_adjust=True, progress=False, threads=False)
    raw = download(symbols, start)
    close, volume = raw["Close"].sort_index().sort_index(axis=1), raw["Volume"].sort_index().sort_index(axis=1)
    close.index, volume.index = pd.DatetimeIndex(close.index).tz_localize(None), pd.DatetimeIndex(volume.index).tz_localize(None)
    close, volume = close.dropna(how="all"), volume.reindex(close.dropna(how="all").index)
    close, volume, unfinished = drop_unfinished_session(close, volume)
    missing = [s for s in symbols if s not in close.columns or close[s].dropna().empty]
    if missing:
        raise RuntimeError(f"no data came back for {missing}; the stored files were left alone")
    if old_close is not None and (len(close) < len(old_close) or close.index[-1] < old_close.index[-1]):
        raise RuntimeError("the download is shorter than what is stored; the stored files were left alone")
    d.mkdir(parents=True, exist_ok=True)
    for name, frame in (("close", close), ("volume", volume)):
        path = d / f"{name}.parquet"
        if path.exists():
            path.replace(d / f"{name}.prev.parquet")
        frame.to_parquet(path)
    return {"symbols": len(symbols), "rows": len(close), "first": str(close.index[0].date()), "last": str(close.index[-1].date()),
            "previous_last": None if old_close is None else str(old_close.index[-1].date()),
            "unfinished_session_dropped": bool(unfinished)}
