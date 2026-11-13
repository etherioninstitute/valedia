"""Daily open, high, low, close and volume, one column per symbol, for models that read whole price bars.

The research price folders hold close and volume only. A model such as Kronos reads the full bar, so
`refresh_ohlc` downloads the five fields (adjusted, from yfinance with `auto_adjust=True`) into a folder
of its own: `open.parquet`, `high.parquet`, `low.parquet`, `close.parquet` and `volume.parquet`.

It fails closed: if any symbol comes back without any one of the five fields (volume included), or the download is shorter than what is
stored, nothing is written and the stored files stay as they were. A bar for a US session that is
still running is dropped, as `prices.refresh` does.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from valedia_trader.data_and_fees import prices

__all__ = ["FIELDS", "refresh_ohlc", "load_ohlc"]

FIELDS = ("open", "high", "low", "close", "volume")


def _download(tickers, begin):
    import yfinance as yf
    return yf.download(list(tickers), start=begin, auto_adjust=True, progress=False, threads=False)


def refresh_ohlc(folder: str | Path, symbols, start: str, download=None) -> dict:
    """Download the five daily fields for `symbols` from `start` into `folder`. Returns what changed."""
    d, symbols = Path(folder), tuple(symbols)
    raw = (download or _download)(symbols, start)
    present = set(raw.columns.get_level_values(0))
    absent = [f for f in FIELDS if f.capitalize() not in present]
    if absent:
        raise RuntimeError(f"no {', '.join(absent)} came back for {sorted(symbols)}; the stored files were left alone")
    frames = {}
    for f in FIELDS:
        frame = raw[f.capitalize()].sort_index().sort_index(axis=1)
        frame.index = pd.DatetimeIndex(frame.index).tz_localize(None)
        frames[f] = frame
    rows = frames["close"].dropna(how="all").index
    frames = {f: v.reindex(rows) for f, v in frames.items()}
    _, _, unfinished = prices.drop_unfinished_session(frames["close"], frames["volume"])
    if unfinished:
        frames = {f: v.iloc[:-1] for f, v in frames.items()}
    missing = {f: sorted(s for s in symbols if s not in frames[f].columns or frames[f][s].dropna().empty) for f in FIELDS}
    missing = {f: s for f, s in missing.items() if s}
    if missing:                                           # every field, volume too: a symbol without volume has no window
        said = "; ".join(f"no {f} came back for {s}" for f, s in missing.items())
        raise RuntimeError(f"{said}; the stored files were left alone")
    close = frames["close"]
    old = pd.read_parquet(d / "close.parquet") if (d / "close.parquet").exists() else None
    if old is not None and (len(close) < len(old) or close.index[-1] < old.index[-1]):
        raise RuntimeError("the download is shorter than what is stored; the stored files were left alone")
    d.mkdir(parents=True, exist_ok=True)
    try:
        for f, frame in frames.items():                   # write every field first, then swap them in
            frame[list(symbols)].sort_index(axis=1).to_parquet(d / f"{f}.new.parquet")
    except BaseException:
        for f in FIELDS:
            (d / f"{f}.new.parquet").unlink(missing_ok=True)
        raise
    for f in FIELDS:
        path = d / f"{f}.parquet"
        if path.exists():
            path.replace(d / f"{f}.prev.parquet")
        (d / f"{f}.new.parquet").replace(path)
    return {"folder": str(d), "symbols": len(symbols), "rows": len(close), "first": str(close.index[0].date()),
            "last": str(close.index[-1].date()), "previous_last": None if old is None else str(old.index[-1].date()),
            "unfinished_session_dropped": bool(unfinished)}


def load_ohlc(folder: str | Path) -> dict[str, pd.DataFrame]:
    """The five fields from `folder`, every one on the close file's dates and the same columns."""
    d = Path(folder)
    frames = {f: pd.read_parquet(d / f"{f}.parquet") for f in FIELDS}
    idx = frames["close"].index
    cols = sorted(set.intersection(*(set(v.columns) for v in frames.values())))
    return {f: v.reindex(index=idx, columns=cols) for f, v in frames.items()}
