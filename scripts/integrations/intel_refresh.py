"""Lightweight Intel Refresh — Valedia ASI
Bypasses the broken MiroFish swarm pipeline (times out at 120s).
Directly pulls Fear/Greed, CoinGecko, and ESPN data into signal files.
Runs as fallback when MiroFish runner fails.
(c) GOATWORTHY Post LLC
"""
import json, os, requests
from datetime import datetime, timezone

MODELS = os.path.expanduser("~/.openclaw/workspace/models")

def refresh_all():
    ts = datetime.now(timezone.utc).isoformat()
    results = {}
    
    # 1. Fear & Greed Index
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
        fg = r.json()["data"][0]
        results["fear_greed"] = {
            "value": int(fg["value"]),
            "classification": fg["value_classification"],
            "ts": ts,
        }
    except Exception as e:
        results["fear_greed"] = {"error": str(e)}

    # 2. Crypto prices (CoinGecko)
    try:
        ids = "bitcoin,ethereum,solana,avalanche-2,chainlink"
        r = requests.get(f"https://api.coingecko.com/api/v3/simple/price?ids={ids}&vs_currencies=usd&include_24hr_change=true", timeout=10)
        prices = r.json()
        results["crypto"] = {}
        for cg_id, data in prices.items():
            price = data.get("usd", 0)
            change = data.get("usd_24h_change", 0)
            results["crypto"][cg_id] = {"price": price, "change_24h": round(change, 2)}
    except Exception as e:
        results["crypto"] = {"error": str(e)}
    
    # 3. Build MiroFish-compatible signal file
    # Simple momentum-based bias: >2% up = BUY, <-2% down = SELL, else HOLD
    signals = {}
    ticker_map = {
        "bitcoin": "BTC-USD", "ethereum": "ETH-USD",
        "solana": "SOL-USD", "avalanche-2": "AVAX-USD",
        "chainlink": "LINK-USD",
    }
    overall_bias = 50
    if "error" not in results.get("crypto", {}):
        biases = []
        for cg_id, ticker in ticker_map.items():
            data = results["crypto"].get(cg_id, {})
            change = data.get("change_24h", 0)
            if change > 3:
                direction, score = "BUY", min(85, 60 + change * 3)
            elif change > 1:
                direction, score = "BUY", 55 + change * 2
            elif change < -3:
                direction, score = "SELL", max(15, 40 - abs(change) * 3)
            elif change < -1:
                direction, score = "SELL", 45 - abs(change) * 2
            else:
                direction, score = "HOLD", 50
            signals[ticker] = {"direction": direction, "score": round(score, 1)}
            biases.append(score)
        overall_bias = round(sum(biases) / len(biases), 1) if biases else 50

    # Adjust bias by Fear/Greed
    fg_val = results.get("fear_greed", {}).get("value", 50)
    if fg_val < 20:  # Extreme Fear
        overall_bias = max(overall_bias - 10, 20)  # More bearish in fear
    elif fg_val > 80:  # Extreme Greed
        overall_bias = min(overall_bias + 10, 80)
    
    mirofish_data = {
        "overall_bias": round(overall_bias, 1),
        "signals": signals,
        "fear_greed": fg_val,
        "source": "intel_refresh (lightweight)",
        "ts": ts,
    }
    
    # Write MiroFish signal file — but do NOT clobber a fresh REAL sim signal.
    # (Real swarm is primary; this lightweight write is fallback only.)
    mf_path = os.path.join(MODELS, "mirofish_signal.json")
    _skip = False
    try:
        if os.path.exists(mf_path):
            import time as _t
            _cur = json.load(open(mf_path))
            _fresh = (_t.time() - os.path.getmtime(mf_path)) < 24 * 3600
            if "lightweight" not in str(_cur.get("source", "")).lower() and _fresh:
                _skip = True
                print(f"[intel_refresh] keeping fresh REAL MiroFish signal (source={_cur.get('source')}); not overwriting")
    except Exception:
        pass
    if not _skip:
        with open(mf_path, "w") as f:
            json.dump(mirofish_data, f, indent=2)
    
    # Fear & Greed remains intelligence, never a risk-state writer.
    return results, mirofish_data

def run():
    results, mf = refresh_all()
    fg = results.get("fear_greed", {})
    print(f"Intel refreshed at {datetime.now(timezone.utc).strftime('%H:%M UTC')}")
    print(f"  Fear/Greed: {fg.get('value','?')} ({fg.get('classification','?')})")
    print(f"  MiroFish bias: {mf.get('overall_bias','?')}")
    crypto = results.get("crypto", {})
    if "error" not in crypto:
        for cg_id, d in crypto.items():
            print(f"  {cg_id}: ${d.get('price',0):,.0f} ({d.get('change_24h',0):+.1f}%)")

if __name__ == "__main__":
    run()
