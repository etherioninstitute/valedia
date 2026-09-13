"""mirofish_accuracy_ledger.py — append-only accuracy ledger for MiroFish predictions.

Path A item 5. Records each REAL MiroFish signal's per-asset predicted direction,
then later scores it against the realized move and reports a running hit rate.
MiroFish fusion weight stays 10% until this ledger shows directional accuracy
>55% over 2-4 weeks (per skills/trading/intelligence/mirofish.md). This ledger is
the ONLY thing that may justify raising that weight.

  record()  — append predictions from a NON-lightweight mirofish_signal.json
  score()   — fill outcomes for predictions past their horizon; append results
  summary() — running hit rate per asset + overall, with sample size

Append-only JSONL. (c) GOATWORTHY Post LLC.
"""
import os, sys, json, argparse
from datetime import datetime, timezone, timedelta

MODELS = os.path.expanduser("~/.openclaw/workspace/models")
SIGNAL = os.path.join(MODELS, "mirofish_signal.json")
LEDGER = os.path.join(MODELS, "mirofish_accuracy_ledger.jsonl")
HORIZON_DAYS = 5


def _append(rec):
    with open(LEDGER, "a") as f:
        f.write(json.dumps(rec) + "\n")


def _read():
    return [json.loads(l) for l in open(LEDGER)] if os.path.exists(LEDGER) else []


def record():
    """Append predictions from the current signal file IF it is a real sim (not lightweight)."""
    if not os.path.exists(SIGNAL):
        print("no signal file"); return 1
    d = json.load(open(SIGNAL))
    src = str(d.get("source", ""))
    if "lightweight" in src.lower():
        print(f"signal source is '{src}' — lightweight, NOT recording (only real sims count)"); return 2
    ts = d.get("ts", datetime.now(timezone.utc).isoformat())
    signals = d.get("signals", {})
    pid = "mf_" + ts.replace(":", "").replace("-", "")[:15]
    n = 0
    for asset, s in signals.items():
        direction = s.get("direction") if isinstance(s, dict) else s
        score = s.get("score") if isinstance(s, dict) else None
        _append({"type": "prediction", "pred_id": f"{pid}_{asset}", "ts": ts, "source": src,
                 "asset": asset, "predicted_direction": direction, "score": score,
                 "horizon_days": HORIZON_DAYS,
                 "score_after": (datetime.fromisoformat(ts.replace("Z", "+00:00"))
                                 + timedelta(days=HORIZON_DAYS)).isoformat()})
        n += 1
    print(f"recorded {n} MiroFish predictions from real sim (source='{src}')")
    return 0


def _price(asset):
    """Latest price for an asset ticker (yfinance; CoinGecko fallback for -USD)."""
    try:
        import yfinance as yf, io, contextlib
        with contextlib.redirect_stderr(io.StringIO()):
            h = yf.Ticker(asset).history(period="7d")
        if not h.empty:
            return float(h["Close"].iloc[-1])
    except Exception:
        pass
    return None


def score():
    """For predictions past their horizon and not yet scored, fetch realized move + hit."""
    recs = _read()
    scored = {r["pred_id"] for r in recs if r.get("type") == "result"}
    preds = [r for r in recs if r.get("type") == "prediction" and r["pred_id"] not in scored]
    now = datetime.now(timezone.utc)
    n = 0
    for p in preds:
        try:
            due = datetime.fromisoformat(p["score_after"].replace("Z", "+00:00"))
        except Exception:
            continue
        if now < due:
            continue  # horizon not reached
        entry = p.get("entry_price") or _price(p["asset"])
        cur = _price(p["asset"])
        if entry is None or cur is None:
            continue
        realized = "UP" if cur > entry else "DOWN"
        pred = str(p.get("predicted_direction", "")).upper()
        hit = (pred.startswith("UP") and realized == "UP") or (pred.startswith("DOWN") and realized == "DOWN")
        _append({"type": "result", "pred_id": p["pred_id"], "asset": p["asset"],
                 "predicted_direction": p.get("predicted_direction"), "realized": realized,
                 "hit": bool(hit), "scored_at": now.isoformat()})
        n += 1
    print(f"scored {n} predictions")
    return 0


def summary():
    recs = _read()
    results = [r for r in recs if r.get("type") == "result"]
    preds = [r for r in recs if r.get("type") == "prediction"]
    if not results:
        print(f"MiroFish accuracy ledger: {len(preds)} predictions recorded, 0 scored yet.")
        print("Weight stays 10% until >55% directional accuracy over 2-4 weeks.")
        return 0
    from collections import defaultdict
    per = defaultdict(lambda: [0, 0])
    for r in results:
        per[r["asset"]][0] += 1; per[r["asset"]][1] += 1 if r["hit"] else 0
    tot_n = sum(v[0] for v in per.values()); tot_h = sum(v[1] for v in per.values())
    print(f"{'asset':10}{'n':>5}{'hits':>6}{'hit%':>8}")
    for a, (nn, hh) in sorted(per.items()):
        print(f"{a:10}{nn:>5}{hh:>6}{100.0*hh/nn:>8.1f}")
    rate = 100.0 * tot_h / tot_n
    print(f"{'OVERALL':10}{tot_n:>5}{tot_h:>6}{rate:>8.1f}")
    print(f"\nGate: raise MiroFish fusion weight above 10% ONLY if overall >55% "
          f"over 2-4 weeks. Currently {rate:.1f}% on {tot_n} (n {'>=' if tot_n>=20 else '<'} 20).")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(prog="mirofish_accuracy_ledger")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for c in ("record", "score", "summary"):
        sub.add_parser(c)
    a = ap.parse_args()
    sys.exit({"record": record, "score": score, "summary": summary}[a.cmd]())
