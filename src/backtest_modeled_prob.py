"""backtest_modeled_prob.py — Honest out-of-sample test of the calibrated edge.

Walk-forward: for each day compute the RAW modeled_prob_up on the past only,
label = next-period up move. Fit isotonic calibration on the first half, test
on the second half (out-of-sample). Report Brier vs the 0.5 baseline, a
reliability table, and an EV backtest of betting when the calibrated edge
clears a margin. Prints the truth even if the edge is zero.
"""
import sys, warnings
import numpy as np
warnings.filterwarnings("ignore")
sys.path.insert(0, "/Users/burtonchatelain/.openclaw/workspace/scripts/integrations")
import modeled_prob as MP


def series_scores(prices, fair_window=20, horizon=1, warmup=60):
    """Walk-forward raw scores + next-horizon up/down outcomes (past-only)."""
    p = np.asarray(prices, dtype=float)
    raw, out = [], []
    for t in range(warmup, len(p) - horizon):
        s = MP.modeled_prob_up(p[:t], fair_window=fair_window, horizon=horizon)
        fut = (p[t + horizon] - p[t]) / (p[t] + 1e-12)
        raw.append(s)
        out.append(1.0 if fut > 0 else 0.0)
    return np.array(raw), np.array(out)


def run(tickers, period="3y", horizon=1, fair_window=20, margin=0.03):
    import yfinance as yf
    all_raw, all_out = [], []
    per_ticker = {}
    for tk in tickers:
        try:
            df = yf.download(tk, period=period, interval="1d", progress=False)
            px = df["Close"].values.astype(float).ravel()
            px = px[np.isfinite(px)]
            if len(px) < 200:
                print(f"  {tk}: too little data ({len(px)})"); continue
            raw, out = series_scores(px, fair_window, horizon)
            per_ticker[tk] = (raw, out)
            all_raw.append(raw); all_out.append(out)
            print(f"  {tk}: {len(raw)} samples, base up-rate {out.mean():.3f}")
        except Exception as e:
            print(f"  {tk}: error {e}")
    if not all_raw:
        print("no data"); return
    raw = np.concatenate(all_raw); out = np.concatenate(all_out)

    # Split first half (fit calibration) / second half (out-of-sample test)
    n = len(raw); h = n // 2
    cal = MP.calibrate_isotonic(raw[:h], out[:h])
    test_raw, test_out = raw[h:], out[h:]
    test_cal = np.array([cal(s) for s in test_raw])

    base = np.full_like(test_out, out[:h].mean())   # base-rate baseline
    b_model = MP.brier_score(test_cal, test_out)
    b_base = MP.brier_score(base, test_out)
    print("\n=== OUT-OF-SAMPLE (second half) ===")
    print(f"samples: {len(test_out)}  realized up-rate: {test_out.mean():.3f}")
    print(f"Brier  model={b_model:.4f}   baseline={b_base:.4f}   "
          f"skill={'+' if b_model<b_base else ''}{(b_base-b_model):.4f} "
          f"({'MODEL BETTER' if b_model<b_base else 'NO EDGE'})")
    print("reliability (bin_mid, mean_pred, realized, n):")
    for row in MP.reliability_table(test_cal, test_out, bins=5):
        print("  ", row)

    # EV backtest: bet direction when |cal-0.5|>margin; PnL in return units.
    signal = test_cal - 0.5
    bets = np.abs(signal) > margin
    if bets.sum() > 0:
        # realized direction return: +1 if we called it right (approx, using sign)
        called_up = signal[bets] > 0
        correct = (called_up == (test_out[bets] > 0.5))
        hit = correct.mean()
        print(f"\nEV gate (|edge|>{margin}): {int(bets.sum())} bets, "
              f"hit-rate {hit:.3f} (need >0.5 for directional edge)")
    else:
        print(f"\nEV gate (|edge|>{margin}): 0 bets — model never exceeds margin")
    return {"brier_model": b_model, "brier_base": b_base}


if __name__ == "__main__":
    print("### CRYPTO ###")
    run(["BTC-USD", "ETH-USD", "SOL-USD"], period="3y", horizon=1)
    print("\n### MEAN-REVERTING ASSETS (GLD/TLT/SPY) ###")
    run(["GLD", "TLT", "SPY"], period="5y", horizon=1)
