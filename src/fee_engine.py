"""Fee-Aware Execution Engine — Valedia ASI
Every trade must be profitable AFTER all fees. No exceptions.
Queries live fee schedules, calculates net P&L, gates all executions.
"""
import os, json, time, requests, math
from datetime import datetime, timezone

FEE_CACHE_FILE = "/Users/burtonchatelain/.openclaw/workspace/fee_schedules.json"
FEE_CACHE_TTL = 86400  # Refresh daily

# === Static Fee Schedules (fallback) ===
STATIC_FEES = {
    "kraken": {
        "maker": 0.0016, "taker": 0.0026,
        "deposit_ach": 0, "deposit_wire": 0, "deposit_crypto": 0,
        "withdraw_ach": 0, "withdraw_wire": 4.00,
        "withdraw_btc": 0.00002, "withdraw_eth": 0.0015, "withdraw_xrp": 0.02,
        "earn_fee": "included_in_yield",
        "source": "static", "updated": "2026-03-13"
    },
    "coinbase": {
        "maker": 0.004, "taker": 0.006,
        "deposit_ach": 0, "deposit_wire": 10,
        "withdraw_ach": 0, "withdraw_wire": 25,
        "spread": 0.005,
        "source": "static", "updated": "2026-03-13"
    },
    "kalshi": {
        "maker_per_contract": 0, "taker_per_contract": 0.02,
        "deposit": 0, "withdraw": 0,
        "settlement_fee": 0,
        "source": "static", "updated": "2026-03-13"
    },
    "polymarket": {
        "maker": 0, "taker": 0,
        "deposit_gas": 0.01, "withdraw_gas": 0.01,
        "source": "static", "updated": "2026-03-13"
    },
    "alpaca": {
        "maker": 0, "taker": 0,
        "commission_stocks": 0, "commission_etfs": 0,
        "commission_options": 0.65,
        "deposit": 0, "withdraw": 0,
        "source": "static", "updated": "2026-03-13"
    },
    "defi_uniswap": {
        "swap_fee": 0.003, "gas_estimate_usd": 0.50,
        "slippage_default": 0.005,
        "source": "static", "updated": "2026-03-13"
    }
}

def load_fee_cache():
    try:
        with open(FEE_CACHE_FILE) as f:
            cache = json.load(f)
        if time.time() - cache.get("_timestamp", 0) < FEE_CACHE_TTL:
            return cache
    except:
        pass
    return None

def save_fee_cache(fees):
    fees["_timestamp"] = time.time()
    fees["_updated"] = datetime.now(timezone.utc).isoformat()
    with open(FEE_CACHE_FILE, "w") as f:
        json.dump(fees, f, indent=2)

def fetch_kraken_fees():
    """Fetch live Kraken fee schedule via API."""
    try:
        with open(os.path.expanduser("~/.hermes/secrets/kraken_keys.json")) as f:
            keys = json.load(f)
        import hashlib, hmac, base64, urllib.parse
        api_key = keys["api_key"]
        private_key = keys["private_key"]
        urlpath = "/0/private/TradeVolume"
        nonce = str(int(time.time() * 1000))
        data = {"nonce": nonce}
        postdata = urllib.parse.urlencode(data)
        encoded = (nonce + postdata).encode()
        message = urlpath.encode() + hashlib.sha256(encoded).digest()
        mac = hmac.new(base64.b64decode(private_key), message, hashlib.sha512)
        sig = base64.b64encode(mac.digest()).decode()
        r = requests.post(f"https://api.kraken.com{urlpath}",
                         headers={"API-Key": api_key, "API-Sign": sig}, data=data, timeout=10)
        result = r.json()
        if not result.get("error"):
            fees = result.get("result", {}).get("fees", {})
            if fees:
                sample = list(fees.values())[0] if fees else {}
                return {
                    "maker": float(sample.get("fee", 0.16)) / 100,
                    "taker": float(sample.get("fee", 0.26)) / 100,
                    "source": "live_api",
                    "updated": datetime.now(timezone.utc).isoformat()
                }
    except:
        pass
    return None

def get_fees(platform):
    """Get fee schedule for a platform (cached + live fallback)."""
    cache = load_fee_cache()
    if cache and platform in cache:
        return cache[platform]

    live = None
    if platform == "kraken":
        live = fetch_kraken_fees()

    if live:
        if not cache:
            cache = dict(STATIC_FEES)
        cache[platform] = {**STATIC_FEES.get(platform, {}), **live}
        save_fee_cache(cache)
        return cache[platform]

    return STATIC_FEES.get(platform, {})

def kalshi_fee_per_contract(price):
    """Canonical REAL Kalshi fee per contract at a given price: 0.07*P*(1-P).
    (The ceil-to-cent in the whole-trade formula is a sub-cent rounding, negligible per
    contract.) Use this in per-contract edge/EV calcs instead of a flat rate — a flat 0.02
    overcharges ~3x at the price extremes and wrongly rejects cheap-contract edges."""
    p = max(0.0, min(1.0, float(price)))
    return 0.07 * p * (1 - p)


def calculate_trade_cost(platform, side, amount_usd, is_maker=False, price=None,
                         use_static=False):
    """Calculate total fees for a trade. For Kalshi, pass price (0<P<1) to use the REAL
    published nonlinear fee ceil(0.07*contracts*P*(1-P)); without price it falls back to a
    flat per-contract approximation (callers should pass price).
    use_static=True reads STATIC_FEES only (no cache, no HTTP). Brain fee gate uses this."""
    fees = dict(STATIC_FEES.get(platform, {})) if use_static else get_fees(platform)
    rate = fees.get("maker" if is_maker else "taker", 0.003)
    trading_fee = amount_usd * rate

    extra = 0
    if platform == "coinbase":
        extra = amount_usd * fees.get("spread", 0.005)
    elif platform == "kalshi":
        if price and 0 < float(price) < 1:
            p = float(price)
            contracts = amount_usd / p
            trading_fee = math.ceil(round(0.07 * contracts * p * (1 - p) * 100, 6)) / 100
        else:
            trading_fee = int(amount_usd / 1.0) * fees.get("taker_per_contract", 0.02)
    elif platform == "defi_uniswap":
        extra = fees.get("gas_estimate_usd", 0.50)
        trading_fee = amount_usd * fees.get("swap_fee", 0.003)

    return {
        "platform": platform,
        "amount": amount_usd,
        "trading_fee": round(trading_fee, 6),
        "extra_costs": round(extra, 6),
        "total_cost": round(trading_fee + extra, 6),
        "cost_pct": round((trading_fee + extra) / amount_usd * 100, 4) if amount_usd > 0 else 0
    }

def is_trade_profitable(platform, amount_usd, expected_return_pct, is_maker=False, price=None,
                        use_static=False):
    """Gate: Is this trade profitable after all fees?"""
    cost = calculate_trade_cost(platform, "buy", amount_usd, is_maker, price=price,
                                use_static=use_static)
    gross_profit = amount_usd * (expected_return_pct / 100)
    net_profit = gross_profit - cost["total_cost"]

    return {
        "profitable": net_profit > 0,
        "gross_profit": round(gross_profit, 4),
        "total_fees": cost["total_cost"],
        "fee_pct": cost["cost_pct"],
        "net_profit": round(net_profit, 4),
        "net_return_pct": round((net_profit / amount_usd) * 100, 4) if amount_usd > 0 else 0,
        "min_return_to_profit": cost["cost_pct"],
        "verdict": "EXECUTE" if net_profit > 0 else "REJECT (fees eat profit)"
    }

def roundtrip_cost(platform, amount_usd, is_maker=False, use_static=False):
    """Full round-trip cost: buy + sell + deposit + withdraw."""
    fees = dict(STATIC_FEES.get(platform, {})) if use_static else get_fees(platform)
    buy_cost = calculate_trade_cost(platform, "buy", amount_usd, is_maker, use_static=use_static)
    sell_cost = calculate_trade_cost(platform, "sell", amount_usd, is_maker, use_static=use_static)
    deposit_fee = fees.get("deposit_ach", fees.get("deposit", 0))
    withdraw_fee = fees.get("withdraw_ach", fees.get("withdraw_flat", 0))

    total = buy_cost["total_cost"] + sell_cost["total_cost"] + deposit_fee + withdraw_fee
    return {
        "platform": platform,
        "amount": amount_usd,
        "buy_fee": buy_cost["total_cost"],
        "sell_fee": sell_cost["total_cost"],
        "deposit_fee": deposit_fee,
        "withdraw_fee": withdraw_fee,
        "total_roundtrip": round(total, 4),
        "roundtrip_pct": round((total / amount_usd) * 100, 4) if amount_usd > 0 else 0,
        "min_gain_to_profit": round((total / amount_usd) * 100, 4) if amount_usd > 0 else 0
    }

def compare_platforms(amount_usd, expected_return_pct):
    """Compare all active platforms for the same trade. Pick cheapest."""
    results = []
    for platform in ["kraken", "coinbase", "alpaca", "kalshi", "polymarket"]:
        analysis = is_trade_profitable(platform, amount_usd, expected_return_pct)
        analysis["platform"] = platform
        results.append(analysis)
    results.sort(key=lambda x: x["net_profit"], reverse=True)
    return results

def fee_report():
    """Generate fee awareness report."""
    lines = ["Fee Engine Report", ""]
    lines.append("Round-trip cost for $50 trade:")
    for platform in ["kraken", "coinbase", "kalshi", "alpaca", "polymarket"]:
        rt = roundtrip_cost(platform, 50)
        lines.append(f"  {platform}: ${rt['total_roundtrip']:.4f} ({rt['roundtrip_pct']:.2f}%) — need {rt['min_gain_to_profit']:.2f}% gain to profit")

    lines.append("")
    lines.append("Best platform by trade size:")
    for size in [10, 50, 100, 500]:
        results = compare_platforms(size, 2.0)
        best = results[0]
        lines.append(f"  ${size} @ 2% return: {best['platform']} (net ${best['net_profit']:.2f}, {best['net_return_pct']:.2f}%)")

    lines.append("")
    kf = get_fees("kraken")
    lines.append(f"Kraken fees: maker={kf.get('maker',0)*100:.2f}% taker={kf.get('taker',0)*100:.2f}% (source: {kf.get('source','static')})")

    return "\n".join(lines)

if __name__ == "__main__":
    print(fee_report())
