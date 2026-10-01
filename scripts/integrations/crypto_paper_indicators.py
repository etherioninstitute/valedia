"""Deterministic OHLCV indicators for the crypto paper desk.

Pure functions. No network. No model. SMA is the existing crypto_paper helper.
(c) GOATWORTHY Post LLC.
"""
from __future__ import annotations

from crypto_paper import sma


def ema_series(xs, n):
    """EMA at each index. None until n closes exist. Same SMA seed as ema()."""
    n = int(n)
    out = [None] * len(xs)
    if n <= 0 or len(xs) < n:
        return out
    k = 2.0 / (n + 1.0)
    acc = sum(float(x) for x in xs[:n]) / float(n)
    out[n - 1] = acc
    for i in range(n, len(xs)):
        acc = (float(xs[i]) * k) + (acc * (1.0 - k))
        out[i] = acc
    return out


def ema(xs, n):
    """EMA seeded with SMA of the first n closes. None until n closes exist."""
    series = ema_series(xs, n)
    if not series or series[-1] is None:
        return None
    return series[-1]


def macd(xs, fast=12, slow=26, signal=9):
    """Last MACD line, signal, and histogram. None until slow+signal-1 closes.

    Line = EMA(fast) - EMA(slow). Signal = EMA(signal) of that line.
    ponytail: SMA-seeded EMA, not a TradingView first-price seed.
    """
    fast, slow, signal = int(fast), int(slow), int(signal)
    if fast <= 0 or slow <= fast or signal <= 0 or len(xs) < slow + signal - 1:
        return None
    fast_s, slow_s = ema_series(xs, fast), ema_series(xs, slow)
    line = [fast_s[i] - slow_s[i] for i in range(len(xs))
            if fast_s[i] is not None and slow_s[i] is not None]
    sig = ema(line, signal)
    if sig is None:
        return None
    last = line[-1]
    return {"line": last, "signal": sig, "hist": last - sig}


def rsi(xs, n=14):
    """Wilder RSI. None until n+1 closes exist. Flat tape → 50."""
    n = int(n)
    if n <= 0 or len(xs) < n + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(xs)):
        d = float(xs[i]) - float(xs[i - 1])
        gains.append(d if d > 0 else 0.0)
        losses.append(-d if d < 0 else 0.0)
    ag = sum(gains[:n]) / float(n)
    al = sum(losses[:n]) / float(n)
    for i in range(n, len(gains)):
        ag = (ag * (n - 1) + gains[i]) / float(n)
        al = (al * (n - 1) + losses[i]) / float(n)
    if ag == 0 and al == 0:
        return 50.0
    if al == 0:
        return 100.0
    rs = ag / al
    return 100.0 - (100.0 / (1.0 + rs))


def vwap(bars, n):
    """Volume-weighted typical price over the last n bars. None if volume is 0."""
    n = int(n)
    if n <= 0 or len(bars) < n:
        return None
    num = den = 0.0
    for b in bars[-n:]:
        tp = (float(b["high"]) + float(b["low"]) + float(b["close"])) / 3.0
        vol = float(b.get("volume") or 0.0)
        num += tp * vol
        den += vol
    if den <= 0:
        return None
    return num / den


def _closes(bars):
    return [float(b["close"]) for b in bars or []]


def snapshot(bars, specs):
    """Latest indicator values named by each spec id. Missing math stays None."""
    closes = _closes(bars)
    out = {"close": closes[-1] if closes else None, "n": len(closes)}
    for spec in specs or []:
        fn = str(spec.get("fn") or "")
        period = int(spec.get("period") or 0)
        sid = spec.get("id") or fn
        if fn == "sma":
            out[sid] = sma(closes, period)
        elif fn == "ema":
            out[sid] = ema(closes, period)
        elif fn == "rsi":
            out[sid] = rsi(closes, period)
        elif fn == "vwap":
            out[sid] = vwap(bars, period)
        elif fn == "macd":
            got = macd(
                closes,
                int(spec.get("fast") or 12),
                int(spec.get("slow") or 26),
                int(spec.get("signal") or 9),
            )
            base = sid or "macd"
            out[base + "_line"] = None if not got else got["line"]
            out[base + "_signal"] = None if not got else got["signal"]
            out[base + "_hist"] = None if not got else got["hist"]
        else:
            out[sid] = None
    return out
