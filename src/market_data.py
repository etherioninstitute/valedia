"""Market Data Hub — Abundance Agent
Aggregates data from yfinance, Finnhub, and technical analysis.
"""
import os, json
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

FINNHUB_KEY = os.environ.get("FINNHUB_API_KEY", "")

def get_stock_data(ticker, period="1mo"):
    """Fetch stock data via yfinance."""
    stock = yf.Ticker(ticker)
    hist = stock.history(period=period)
    return hist

def get_market_summary():
    """Get key market indices + crypto."""
    tickers = {
        "SPY": "S&P 500", "QQQ": "Nasdaq 100", "DIA": "Dow Jones",
        "IWM": "Russell 2000", "VIX": "Volatility Index",
        "BTC-USD": "Bitcoin", "ETH-USD": "Ethereum",
        "GLD": "Gold", "TLT": "20yr Treasury", "DXY": "USD Index"
    }
    summary = []
    for sym, name in tickers.items():
        try:
            data = yf.Ticker(sym).history(period="2d")
            if len(data) >= 2:
                prev = data["Close"].iloc[-2]
                curr = data["Close"].iloc[-1]
                chg = ((curr - prev) / prev) * 100
                summary.append({"symbol": sym, "name": name, "price": round(curr, 2), "change_pct": round(chg, 2)})
        except:
            pass
    return summary

def technical_signals(ticker, period="3mo"):
    """Basic technical analysis signals."""
    try:
        import talib
        data = get_stock_data(ticker, period)
        close = data["Close"].values
        signals = {}
        signals["sma_20"] = round(talib.SMA(close, 20)[-1], 2)
        signals["sma_50"] = round(talib.SMA(close, 50)[-1], 2)
        signals["rsi_14"] = round(talib.RSI(close, 14)[-1], 2)
        macd, signal, hist = talib.MACD(close)
        signals["macd"] = round(macd[-1], 4)
        signals["macd_signal"] = round(signal[-1], 4)
        signals["macd_hist"] = round(hist[-1], 4)
        upper, middle, lower = talib.BBANDS(close, 20)
        signals["bb_upper"] = round(upper[-1], 2)
        signals["bb_lower"] = round(lower[-1], 2)
        signals["price"] = round(close[-1], 2)
        # Simple signal
        if signals["rsi_14"] < 30:
            signals["rsi_signal"] = "OVERSOLD"
        elif signals["rsi_14"] > 70:
            signals["rsi_signal"] = "OVERBOUGHT"
        else:
            signals["rsi_signal"] = "NEUTRAL"
        signals["trend"] = "BULLISH" if signals["sma_20"] > signals["sma_50"] else "BEARISH"
        return signals
    except Exception as e:
        return {"error": str(e)}

def morning_market_brief():
    """Generate morning market briefing for Signal."""
    _ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    lines = ["📊 Market Morning Brief", f"Time: {_ts}", ""]
    summary = get_market_summary()
    for s in summary:
        arrow = "🟢" if s["change_pct"] > 0 else "🔴" if s["change_pct"] < 0 else "⚪"
        lines.append(f"  {arrow} {s['name']}: ${s['price']:,.2f} ({s['change_pct']:+.2f}%)")
    
    # Key technicals
    lines.append("\nKey Technicals:")
    for ticker in ["SPY", "BTC-USD"]:
        sigs = technical_signals(ticker)
        if "error" not in sigs:
            lines.append(f"  {ticker}: RSI={sigs['rsi_14']} ({sigs['rsi_signal']}) | Trend={sigs['trend']}")
    
    return "\n".join(lines)

if __name__ == "__main__":
    print(morning_market_brief())
