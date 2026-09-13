"""ML Kernel Retrain — Valedia ASI
Feeds kernel v9 signal accuracy into GradientBoosting models.
The ML models learn WHICH kernel signals are most predictive.
(c) GOATWORTHY Post LLC.
"""
import os, sys, json, pickle
import numpy as np
from datetime import datetime, timezone
sys.path.insert(0, "/Users/burtonchatelain/.openclaw/workspace/scripts/integrations")

MODEL_DIR = "/Users/burtonchatelain/.openclaw/workspace/models"
FEATURE_LOG = os.path.join(MODEL_DIR, "kernel_features.json")

def collect_features():
    """Run kernel on all assets and collect features + next-day return."""
    import yfinance as yf
    from kernel_predictor_v16e import KernelPredictor
    tickers = {"BTC":"BTC-USD","ETH":"ETH-USD","SOL":"SOL-USD",
               "SPY":"SPY","QQQ":"QQQ","GLD":"GLD","NVDA":"NVDA"}
    features = []
    for nm, tk in tickers.items():
        try:
            data = yf.Ticker(tk).history(period="1y")
            prices = data["Close"].values
            if len(prices) < 80: continue
            ac = "crypto" if "-" in tk else "equity"
            kp = KernelPredictor(ac)
            # Collect signals for last 60 days
            for i in range(len(prices)-61, len(prices)-1):
                sig = kp.predict(prices[:i+1])
                kp.prev = 0  # reset hysteresis
                next_ret = np.log(prices[i+1]/prices[i])
                features.append({
                    "ticker": nm, "score": sig["score"], "mom": sig["mom"],
                    "snr": sig["snr"], "se": sig["se"], "D": sig["D"],
                    "coh": sig["coh"], "gap_s": sig["gap_s"],
                    "vr": sig["vr"], "rsi": sig["rsi"],
                    "next_ret": float(next_ret),
                    "correct": 1 if (sig["score"]>50 and next_ret>0) or (sig["score"]<50 and next_ret<0) else 0
                })
        except: pass
    return features

def retrain_on_kernel():
    """Train GradientBoosting on kernel features to predict next-day direction."""
    from sklearn.ensemble import GradientBoostingClassifier
    from sklearn.model_selection import cross_val_score
    ts = datetime.now(timezone.utc).isoformat()
    lines = [f"[ML-KERNEL] Retrain {ts}"]
    features = collect_features()
    if len(features) < 100:
        lines.append(f"  Only {len(features)} samples, need 100+")
        return "\n".join(lines)
    X = np.array([[f["score"],f["mom"],f["snr"],f["se"],f["D"],
                   f["coh"],f["gap_s"],f["vr"],f["rsi"]] for f in features])
    y = np.array([f["correct"] for f in features])
    lines.append(f"  Samples: {len(X)}, base rate: {np.mean(y)*100:.1f}%")
    # Clean NaN/Inf values
    mask = np.isfinite(X).all(axis=1) & np.isfinite(y)
    X = X[mask]; y = y[mask]
    if len(X) < 50:
        lines.append(f"  Only {len(X)} clean samples after NaN removal, need 50+")
        return "\n".join(lines)
    lines.append(f"  Clean samples: {len(X)} (after NaN removal)")
    gb = GradientBoostingClassifier(n_estimators=100, max_depth=3, learning_rate=0.1)
    scores = cross_val_score(gb, X, y, cv=5, scoring="accuracy")
    lines.append(f"  CV accuracy: {np.mean(scores)*100:.1f}% +/- {np.std(scores)*100:.1f}%")
    gb.fit(X, y)
    # Feature importance
    feat_names = ["score","mom","snr","se","D","coh","gap_s","vr","rsi"]
    imp = sorted(zip(feat_names, gb.feature_importances_), key=lambda x: x[1], reverse=True)
    lines.append("  Feature importance:")
    for name, importance in imp:
        lines.append(f"    {name:8s}: {importance:.3f}")
    # Save model
    model_path = os.path.join(MODEL_DIR, "kernel_gb_model.pkl")
    with open(model_path, "wb") as f: pickle.dump(gb, f)
    # Save log
    with open(os.path.join(MODEL_DIR, "latest_kernel_retrain.json"), "w") as f:
        json.dump({"ts": ts, "samples": len(X), "cv_acc": float(np.mean(scores)),
                   "importance": {n:float(i) for n,i in imp}}, f, indent=2)
    lines.append(f"  Model saved to {model_path}")
    return "\n".join(lines)

if __name__ == "__main__":
    print(retrain_on_kernel())
