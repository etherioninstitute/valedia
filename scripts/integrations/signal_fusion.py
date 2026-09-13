"""Composite Signal Fusion Engine — Valedia ASI
Merges ALL data sources into a single conviction score per asset.
Only trades when composite score > threshold.

Signal Weights:
  ML Model (40%) — GradientBoosting prediction confidence
  Technical (30%) — RSI, MACD, Bollinger Band position
  Sentiment (15%) — News bull/bear ratio
  Volume (10%) — Volume vs 20-day average
  Whale Activity (5%) — On-chain whale movements (when available)

Output: 0-100 conviction score per asset
  > 80: STRONG BUY (high conviction)
  > 70: BUY (standard conviction)
  > 50: HOLD (insufficient edge)
  < 40: AVOID (bearish signals)
  < 25: SHORT CANDIDATE (if perps available)
"""
import os, sys, json, pickle, requests
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timezone

sys.path.insert(0, "/Users/burtonchatelain/.openclaw/workspace/scripts/integrations")

MODEL_DIR = "/Users/burtonchatelain/.openclaw/workspace/models"
WATCHLIST = {
    "XRP": "XRP-USD", "SOL": "SOL-USD", "ETH": "ETH-USD",
    "BTC": "BTC-USD", "ADA": "ADA-USD", "DOT": "DOT-USD",
    "AVAX": "AVAX-USD", "LINK": "LINK-USD"
}

WEIGHTS = {
    "ml": 0.40,
    "technical": 0.30,
    "sentiment": 0.15,
    "volume": 0.10,
    "whale": 0.05,
}

def get_ml_score(ticker):
    """Get ML model prediction score (0-100)."""
    try:
        model_file = os.path.join(MODEL_DIR, f"{ticker.replace('-','_')}_model.pkl")
        retrain_file = os.path.join(MODEL_DIR, "latest_retrain.json")
        with open(retrain_file) as f:
            data = json.load(f)
        for r in data.get("results", []):
            if r["ticker"] == ticker:
                return r["buy_probability"] * 100, r["accuracy"]
        return 50, 0.5
    except:
        return 50, 0.5

def get_technical_score(ticker):
    """Calculate technical score from RSI, MACD, Bollinger (0-100)."""
    try:
        import talib
        data = yf.Ticker(ticker).history(period="3mo")
        if len(data) < 50:
            return 50
        close = data["Close"].values
        rsi = talib.RSI(close, 14)[-1]
        macd, signal, hist = talib.MACD(close)
        macd_hist = hist[-1]
        sma20 = talib.SMA(close, 20)[-1]
        sma50 = talib.SMA(close, 50)[-1]
        upper, middle, lower = talib.BBANDS(close, 20)
        bb_pos = (close[-1] - lower[-1]) / (upper[-1] - lower[-1]) if (upper[-1] - lower[-1]) > 0 else 0.5

        score = 50
        if rsi < 30: score += 20
        elif rsi < 40: score += 10
        elif rsi > 70: score -= 20
        elif rsi > 60: score -= 10
        if macd_hist > 0: score += 10
        if macd_hist < 0: score -= 10
        if sma20 > sma50: score += 10
        else: score -= 10
        if bb_pos < 0.2: score += 10
        elif bb_pos > 0.8: score -= 10

        return max(0, min(100, score))
    except:
        return 50

def get_sentiment_score():
    """Get news sentiment score (0-100). Shared across all assets."""
    try:
        fkey = os.environ.get("FINNHUB_API_KEY", "")
        if not fkey:
            return 50
        r = requests.get(f"https://finnhub.io/api/v1/news?category=general&token={fkey}", timeout=10)
        news = r.json()[:20] if r.status_code == 200 else []
        bull_words = ["surge","rally","bull","gain","jump","soar","high","breakout","buy","growth","profit","record"]
        bear_words = ["crash","drop","bear","fall","plunge","dump","low","selloff","sell","loss","fear","risk","recession"]
        bc = sum(1 for n in news for w in bull_words if w in n.get("headline","").lower())
        brc = sum(1 for n in news for w in bear_words if w in n.get("headline","").lower())
        total = bc + brc
        if total == 0:
            return 50
        ratio = bc / total
        return int(ratio * 100)
    except:
        return 50

def get_volume_score(ticker):
    """Volume relative to 20-day average (0-100). High volume on up day = bullish."""
    try:
        data = yf.Ticker(ticker).history(period="1mo")
        if len(data) < 5:
            return 50
        vol_ratio = data["Volume"].iloc[-1] / data["Volume"].rolling(20).mean().iloc[-1]
        price_change = (data["Close"].iloc[-1] - data["Close"].iloc[-2]) / data["Close"].iloc[-2]

        if vol_ratio > 1.5 and price_change > 0:
            return 80
        elif vol_ratio > 1.5 and price_change < 0:
            return 20
        elif vol_ratio > 1.0:
            return 60 if price_change > 0 else 40
        else:
            return 50
    except:
        return 50

def calculate_composite_score(asset, ticker):
    """Calculate the unified conviction score for one asset."""
    ml_score, ml_accuracy = get_ml_score(ticker)
    tech_score = get_technical_score(ticker)
    sent_score = get_sentiment_score()
    volume_score = get_volume_score(ticker)
    whale_score = 50  # Placeholder until whale tracking is real-time

    composite = (
        ml_score * WEIGHTS["ml"] +
        tech_score * WEIGHTS["technical"] +
        sent_score * WEIGHTS["sentiment"] +
        volume_score * WEIGHTS["volume"] +
        whale_score * WEIGHTS["whale"]
    )

    # Confidence adjustment: if ML accuracy is low, regress toward neutral
    if ml_accuracy < 0.55:
        composite = composite * 0.85 + 50 * 0.15

    if composite > 80:
        signal = "STRONG BUY"
    elif composite > 70:
        signal = "BUY"
    elif composite > 50:
        signal = "HOLD"
    elif composite > 40:
        signal = "WEAK"
    elif composite > 25:
        signal = "AVOID"
    else:
        signal = "SHORT CANDIDATE"

    return {
        "asset": asset,
        "ticker": ticker,
        "composite_score": round(composite, 1),
        "signal": signal,
        "components": {
            "ml": {"score": round(ml_score, 1), "weight": WEIGHTS["ml"], "accuracy": round(ml_accuracy, 3)},
            "technical": {"score": round(tech_score, 1), "weight": WEIGHTS["technical"]},
            "sentiment": {"score": round(sent_score, 1), "weight": WEIGHTS["sentiment"]},
            "volume": {"score": round(volume_score, 1), "weight": WEIGHTS["volume"]},
            "whale": {"score": round(whale_score, 1), "weight": WEIGHTS["whale"]},
        }
    }

def scan_all():
    """Scan all watchlist assets and return ranked results."""
    results = []
    for asset, ticker in WATCHLIST.items():
        try:
            result = calculate_composite_score(asset, ticker)
            results.append(result)
        except Exception as e:
            results.append({"asset": asset, "composite_score": 0, "signal": f"ERROR: {e}"})
    results.sort(key=lambda x: x.get("composite_score", 0), reverse=True)
    return results

def fusion_report():
    """Generate Signal Fusion report for Valedia."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"Signal Fusion Scan ({ts})", ""]

    results = scan_all()

    for r in results:
        score = r.get("composite_score", 0)
        signal = r.get("signal", "?")
        asset = r.get("asset", "?")
        bar = "#" * int(score / 5) + "." * (20 - int(score / 5))
        lines.append(f"  {asset:5} [{bar}] {score:5.1f} {signal}")
        if "components" in r:
            c = r["components"]
            parts = [f"{k}:{v.get('score',50):.0f}" for k, v in c.items()]
            lines.append(f"        {' | '.join(parts)}")

    buys = [r for r in results if r.get("signal", "").startswith("BUY") or r.get("signal", "").startswith("STRONG")]
    lines.append("")
    if buys:
        lines.append(f"  Actionable signals: {len(buys)}")
        for b in buys:
            lines.append(f"    >> {b['asset']} @ {b['composite_score']:.0f} ({b['signal']})")
    else:
        lines.append("  No actionable signals. HOLD all positions.")

    with open(os.path.join(MODEL_DIR, "latest_fusion.json"), "w") as f:
        json.dump({"timestamp": ts, "results": results}, f, indent=2)

    return "\n".join(lines)

if __name__ == "__main__":
    print(fusion_report())
