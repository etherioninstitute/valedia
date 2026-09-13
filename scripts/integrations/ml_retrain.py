"""ML Retrain Script — Abundance Agent
Fetches fresh market data, retrains prediction models, logs performance.
Run nightly via cron at 03:00 CT.
"""
import os, sys, json, pickle
import numpy as np
import pandas as pd
import yfinance as yf
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score
import talib
from datetime import datetime, timezone

MODEL_DIR = "/Users/burtonchatelain/.openclaw/workspace/models"
os.makedirs(MODEL_DIR, exist_ok=True)

WATCHLIST = ["XRP-USD", "SOL-USD", "ETH-USD", "BTC-USD", "ADA-USD", "DOT-USD", "AVAX-USD", "LINK-USD"]

def fetch_and_prepare(ticker, period="6mo"):
    """Fetch data and create features."""
    data = yf.Ticker(ticker).history(period=period)
    if len(data) < 60:
        return None
    
    df = data.copy()
    close = df["Close"].values
    
    # Technical indicators as features
    df["rsi_14"] = talib.RSI(close, 14)
    df["rsi_7"] = talib.RSI(close, 7)
    macd, signal, hist = talib.MACD(close)
    df["macd"] = macd
    df["macd_signal"] = signal
    df["macd_hist"] = hist
    df["sma_10"] = talib.SMA(close, 10)
    df["sma_20"] = talib.SMA(close, 20)
    df["sma_50"] = talib.SMA(close, 50)
    upper, middle, lower = talib.BBANDS(close, 20)
    df["bb_width"] = (upper - lower) / middle
    df["bb_pos"] = (close - lower) / (upper - lower + 1e-10)
    df["atr"] = talib.ATR(df["High"].values, df["Low"].values, close, 14)
    df["adx"] = talib.ADX(df["High"].values, df["Low"].values, close, 14)
    
    # Price changes as features
    df["ret_1d"] = df["Close"].pct_change(1)
    df["ret_3d"] = df["Close"].pct_change(3)
    df["ret_5d"] = df["Close"].pct_change(5)
    df["vol_ratio"] = df["Volume"] / df["Volume"].rolling(20).mean()
    
    # Target: will price go up 2%+ in next 5 days?
    df["future_ret"] = df["Close"].shift(-5) / df["Close"] - 1
    df["target"] = (df["future_ret"] > 0.02).astype(int)
    
    df = df.dropna()
    return df

def train_model(ticker):
    """Train a prediction model for one ticker."""
    df = fetch_and_prepare(ticker)
    if df is None or len(df) < 50:
        return None, 0
    
    features = ["rsi_14", "rsi_7", "macd", "macd_signal", "macd_hist",
                "bb_width", "bb_pos", "atr", "adx",
                "ret_1d", "ret_3d", "ret_5d", "vol_ratio"]
    
    X = df[features].values
    y = df["target"].values
    
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, shuffle=False)
    
    model = GradientBoostingClassifier(n_estimators=100, max_depth=4, random_state=42)
    model.fit(X_train, y_train)
    
    accuracy = accuracy_score(y_test, model.predict(X_test))
    
    # Save model
    model_path = os.path.join(MODEL_DIR, f"{ticker.replace('-','_')}_model.pkl")
    with open(model_path, "wb") as f:
        pickle.dump(model, f)
    
    # Get latest prediction
    latest_pred = model.predict_proba(X[-1:])
    buy_prob = latest_pred[0][1] if len(latest_pred[0]) > 1 else 0
    
    return {
        "ticker": ticker,
        "accuracy": round(accuracy, 4),
        "samples": len(df),
        "buy_probability": round(buy_prob, 4),
        "signal": "BUY" if buy_prob > 0.6 else "HOLD" if buy_prob > 0.4 else "AVOID"
    }, accuracy

def retrain_all():
    """Retrain models for all watchlist assets."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"ML Retrain Report ({ts})", ""]
    
    results = []
    for ticker in WATCHLIST:
        try:
            result, acc = train_model(ticker)
            if result:
                results.append(result)
                name = ticker.replace("-USD", "")
                lines.append(f"  {name}: Acc={result['accuracy']:.1%} | P(+2%)={result['buy_probability']:.1%} | {result['signal']}")
        except Exception as e:
            lines.append(f"  {ticker}: Error - {str(e)[:40]}")
    
    # Summary
    if results:
        avg_acc = np.mean([r["accuracy"] for r in results])
        buys = [r for r in results if r["signal"] == "BUY"]
        lines.append(f"\n  Avg accuracy: {avg_acc:.1%}")
        lines.append(f"  BUY signals: {len(buys)}/{len(results)}")
        if buys:
            lines.append("  Top picks:")
            for b in sorted(buys, key=lambda x: x["buy_probability"], reverse=True):
                lines.append(f"    {b['ticker'].replace('-USD','')}: {b['buy_probability']:.1%} confidence")
    
    # Save results
    with open(os.path.join(MODEL_DIR, "latest_retrain.json"), "w") as f:
        json.dump({"timestamp": ts, "results": results}, f, indent=2)
    
    return "\n".join(lines)

if __name__ == "__main__":
    print(retrain_all())
