"""Strategy Modules — Valedia ASI
DCA, Grid, Copy Trading, Rebalancing, Emerging Crypto, Scalping
All feed into the Unified Brain.
(c) GOATWORTHY Post LLC.
"""
import os, sys, json, time, secrets, requests
import numpy as np
from datetime import datetime, timezone, timedelta
sys.path.insert(0, "/Users/burtonchatelain/.openclaw/workspace/scripts/integrations")

# ══════════════════════════════════════════════════════════════════════
# 1. DCA (Dollar Cost Averaging)
# ══════════════════════════════════════════════════════════════════════
class DCAEngine:
    """Auto-buy fixed $ of top kernel picks on schedule.
    Best strategy for small capital — removes timing risk."""
    
    STATE_FILE = "/Users/burtonchatelain/.openclaw/workspace/models/dca_state.json"
    
    def __init__(self, weekly_budget=10, assets=None):
        self.weekly_budget = weekly_budget
        self.assets = assets or {"BTC-USD": 0.4, "ETH-USD": 0.3, "SOL-USD": 0.2, "XRP-USD": 0.1}
        self.state = self._load()
    
    def _load(self):
        try:
            with open(self.STATE_FILE) as f: return json.load(f)
        except: return {"last_buy": None, "total_invested": 0, "buys": []}
    
    def _save(self):
        with open(self.STATE_FILE, "w") as f: json.dump(self.state, f, indent=2, default=str)
    
    def should_buy(self):
        """Buy once per week (every 7 days)."""
        if not self.state["last_buy"]:
            return True
        last = datetime.fromisoformat(self.state["last_buy"])
        return (datetime.now(timezone.utc) - last).days >= 7
    
    def execute(self, buy_func, balance_func):
        """Execute DCA buys. Returns list of actions."""
        if not self.should_buy():
            days_left = 7 - (datetime.now(timezone.utc) - datetime.fromisoformat(self.state["last_buy"])).days
            return [f"DCA: next buy in {days_left} days"]
        
        available = balance_func()
        budget = min(self.weekly_budget, available * 0.5)
        if budget < 2:
            return ["DCA: insufficient funds"]
        
        actions = []
        for pair, weight in self.assets.items():
            amount = round(budget * weight, 2)
            if amount < 1:
                continue
            try:
                result = buy_func(pair, amount)
                actions.append(f"DCA: bought ${amount:.2f} {pair} -> {result.get('success', '?')}")
                self.state["buys"].append({"ts": datetime.now(timezone.utc).isoformat(),
                                           "pair": pair, "amount": amount})
            except Exception as e:
                actions.append(f"DCA: {pair} error: {e}")
        
        self.state["last_buy"] = datetime.now(timezone.utc).isoformat()
        self.state["total_invested"] = sum(b["amount"] for b in self.state["buys"])
        self.state["buys"] = self.state["buys"][-200:]
        self._save()
        return actions


# ══════════════════════════════════════════════════════════════════════
# 2. REBALANCING
# ══════════════════════════════════════════════════════════════════════
class Rebalancer:
    """Maintain target allocation. Sells winners, buys losers."""
    
    def __init__(self, targets=None):
        self.targets = targets or {"BTC": 0.40, "ETH": 0.25, "SOL": 0.20, "USD": 0.15}
    
    def compute_trades(self, balances, prices):
        """Given current balances + prices, compute rebalance trades.
        Returns list of (action, pair, amount) tuples."""
        total = 0
        current = {}
        for asset, bal in balances.items():
            if asset == "USD":
                val = bal
            else:
                pair = f"{asset}-USD"
                val = bal * prices.get(pair, 0)
            current[asset] = val
            total += val
        
        if total < 5:
            return []
        
        trades = []
        for asset, target_pct in self.targets.items():
            target_val = total * target_pct
            current_val = current.get(asset, 0)
            diff = target_val - current_val
            if abs(diff) < 1:
                continue
            if asset == "USD":
                continue
            pair = f"{asset}-USD"
            if diff > 0:
                trades.append(("BUY", pair, round(diff, 2)))
            else:
                qty = abs(diff) / prices.get(pair, 1)
                trades.append(("SELL", pair, round(qty, 8)))
        return trades
    
    def needs_rebalance(self, balances, prices, threshold=0.05):
        """Check if any asset is >5% off target."""
        total = sum(b * prices.get(f"{a}-USD", 1) if a != "USD" else b 
                    for a, b in balances.items())
        if total < 5: return False
        for asset, target in self.targets.items():
            val = balances.get(asset, 0) * prices.get(f"{asset}-USD", 1) if asset != "USD" else balances.get("USD", 0)
            actual_pct = val / total
            if abs(actual_pct - target) > threshold:
                return True
        return False


# ══════════════════════════════════════════════════════════════════════
# 3. COPY TRADING (Whale Wallet Tracking)
# ══════════════════════════════════════════════════════════════════════
class WhaleCopyTrader:
    """Monitor top whale wallets. When whales buy, we buy."""
    
    WHALE_WALLETS = [
        "0x28C6c06298d514Db089934071355E5743bf21d60",  # Binance hot wallet
        "0x21a31Ee1afC51d94C2eFcCAa2092aD1028285549",  # Binance whale
    ]
    
    def scan_whale_moves(self):
        """Check recent large transfers on Ethereum."""
        signals = []
        try:
            # Use public Etherscan-like API for whale moves
            for wallet in self.WHALE_WALLETS[:2]:
                r = requests.get(f"https://api.etherscan.io/api?module=account&action=txlist&address={wallet}&startblock=0&endblock=99999999&page=1&offset=5&sort=desc",
                               timeout=10)
                if r.ok:
                    txs = r.json().get("result", [])
                    for tx in txs[:3]:
                        val_eth = int(tx.get("value", 0)) / 1e18
                        if val_eth > 100:
                            signals.append({
                                "wallet": wallet[:10] + "...",
                                "value_eth": round(val_eth, 2),
                                "direction": "OUT" if tx["from"].lower() == wallet.lower() else "IN",
                                "ts": datetime.fromtimestamp(int(tx["timeStamp"]), tz=timezone.utc).isoformat()
                            })
        except:
            pass
        return signals
    
    def get_signal(self):
        """Aggregate whale signals into buy/sell bias."""
        moves = self.scan_whale_moves()
        if not moves:
            return {"signal": "NEUTRAL", "moves": 0}
        inflows = sum(1 for m in moves if m["direction"] == "IN")
        outflows = sum(1 for m in moves if m["direction"] == "OUT")
        if inflows > outflows:
            return {"signal": "WHALES_ACCUMULATING", "moves": len(moves), "bias": "BUY"}
        elif outflows > inflows:
            return {"signal": "WHALES_DISTRIBUTING", "moves": len(moves), "bias": "SELL"}
        return {"signal": "NEUTRAL", "moves": len(moves)}


# ══════════════════════════════════════════════════════════════════════
# 4. EMERGING CRYPTO SCANNER
# ══════════════════════════════════════════════════════════════════════
class EmergingCryptoScanner:
    """Find trending/new coins before they pump.
    Scans CoinGecko trending + volume spikes."""
    
    def get_trending(self):
        """CoinGecko trending coins (free API, no key needed)."""
        try:
            r = requests.get("https://api.coingecko.com/api/v3/search/trending", timeout=10)
            if r.ok:
                coins = r.json().get("coins", [])
                return [{"name": c["item"]["name"], "symbol": c["item"]["symbol"],
                         "rank": c["item"]["market_cap_rank"],
                         "price_btc": c["item"].get("price_btc", 0)}
                        for c in coins[:7]]
        except: pass
        return []
    
    def get_gainers(self):
        """Top gainers in last 24h."""
        try:
            r = requests.get("https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=percent_change_24h_desc&per_page=10&page=1&sparkline=false",
                           timeout=10)
            if r.ok:
                return [{"name": c["name"], "symbol": c["symbol"].upper(),
                         "price": c["current_price"], "change_24h": round(c["price_change_percentage_24h"] or 0, 1),
                         "volume": c["total_volume"], "mcap": c["market_cap"]}
                        for c in r.json()[:7] if c.get("price_change_percentage_24h")]
        except: pass
        return []
    
    def get_new_listings(self):
        """Recently listed coins with volume."""
        try:
            r = requests.get("https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&order=id_asc&per_page=20&page=1&sparkline=false",
                           timeout=10)
            if r.ok:
                # Filter for coins with decent volume but small mcap (potential movers)
                return [{"name": c["name"], "symbol": c["symbol"].upper(),
                         "price": c["current_price"], "mcap": c["market_cap"],
                         "volume": c["total_volume"]}
                        for c in r.json() 
                        if c.get("total_volume", 0) > 100000 and c.get("market_cap", 0) < 100000000][:5]
        except: pass
        return []
    
    def scan(self):
        """Full emerging crypto report."""
        return {
            "trending": self.get_trending(),
            "top_gainers": self.get_gainers(),
            "new_small_cap": self.get_new_listings(),
            "ts": datetime.now(timezone.utc).isoformat()
        }


# ══════════════════════════════════════════════════════════════════════
# 5. SCALPER (Alpaca stocks only — 0% commission)
# ══════════════════════════════════════════════════════════════════════
class MicroScalper:
    """Ultra-short-term mean-reversion on stocks.
    Only viable on Alpaca (0% fees). Uses 5-min momentum reversal.
    NOT high-frequency — runs 3x/day at brain checkpoints."""
    
    def __init__(self, max_hold_bars=10, entry_z=1.8):
        self.max_hold_bars = max_hold_bars
        self.entry_z = entry_z
    
    def generate_signal(self, prices_1d):
        """Generate scalp signal from daily data.
        Looks for intraday mean-reversion setups."""
        if len(prices_1d) < 20:
            return {"signal": "NONE", "score": 50}
        ret = np.diff(np.log(prices_1d))
        # Short-term z-score (5-day)
        mu5 = np.mean(ret[-5:])
        std5 = np.std(ret[-20:]) + 1e-20
        z = (ret[-1] - mu5) / std5
        # RSI-3 (ultra-short)
        r3 = ret[-3:]
        gains = r3[r3>0]; losses = -r3[r3<0]
        rs = (np.mean(gains) if len(gains)>0 else 1e-10) / (np.mean(losses) if len(losses)>0 else 1e-10)
        rsi3 = 100 - 100/(1+rs)
        
        score = 50
        # Fade extreme moves (mean-reversion)
        if z < -self.entry_z and rsi3 < 25:
            score = 70 + min(abs(z) * 5, 20)
            signal = "SCALP_LONG"
        elif z > self.entry_z and rsi3 > 75:
            score = 30 - min(abs(z) * 5, 20)
            signal = "SCALP_SHORT"
        else:
            signal = "NONE"
        
        return {"signal": signal, "score": round(score, 1), "z": round(z, 2),
                "rsi3": round(rsi3, 1)}
    
    def get_scalp_candidates(self, watchlist_prices):
        """Return ranked scalp opportunities."""
        candidates = []
        for ticker, prices in watchlist_prices.items():
            sig = self.generate_signal(prices)
            if sig["signal"] != "NONE":
                candidates.append({"ticker": ticker, **sig})
        candidates.sort(key=lambda x: abs(x["score"] - 50), reverse=True)
        return candidates[:3]


# ══════════════════════════════════════════════════════════════════════
# 6. GRID TRADING
# ══════════════════════════════════════════════════════════════════════
class GridTrader:
    """Buy/sell at evenly spaced price levels.
    Best in sideways/ranging markets (ERE regime=EQUILIBRIUM)."""
    
    STATE_FILE = "/Users/burtonchatelain/.openclaw/workspace/models/grid_state.json"
    
    def __init__(self, pair="BTC-USD", grid_size=5, spread_pct=2.0):
        self.pair = pair
        self.grid_size = grid_size
        self.spread_pct = spread_pct
        self.state = self._load()
    
    def _load(self):
        try:
            with open(self.STATE_FILE) as f: return json.load(f)
        except: return {"grids": {}, "fills": []}
    
    def _save(self):
        with open(self.STATE_FILE, "w") as f: json.dump(self.state, f, indent=2, default=str)
    
    def setup_grid(self, current_price, budget):
        """Create grid levels around current price."""
        levels = []
        for i in range(self.grid_size):
            offset = (i - self.grid_size // 2) * self.spread_pct / 100
            price_level = current_price * (1 + offset)
            side = "BUY" if offset < 0 else "SELL"
            amount = budget / self.grid_size
            levels.append({"price": round(price_level, 2), "side": side,
                          "amount": round(amount, 2), "filled": False})
        self.state["grids"][self.pair] = {"levels": levels, "center": current_price,
                                          "created": datetime.now(timezone.utc).isoformat()}
        self._save()
        return levels
    
    def check_fills(self, current_price):
        """Check if any grid levels were hit."""
        grid = self.state["grids"].get(self.pair)
        if not grid:
            return []
        fills = []
        for level in grid["levels"]:
            if level["filled"]:
                continue
            if level["side"] == "BUY" and current_price <= level["price"]:
                fills.append(level)
                level["filled"] = True
            elif level["side"] == "SELL" and current_price >= level["price"]:
                fills.append(level)
                level["filled"] = True
        if fills:
            self.state["fills"].extend(fills)
            self._save()
        return fills


# ══════════════════════════════════════════════════════════════════════
# MASTER REPORT
# ══════════════════════════════════════════════════════════════════════
def strategy_report():
    """Run all strategy modules and generate report."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"[STRATEGIES] {ts}", ""]
    
    # DCA status
    dca = DCAEngine(weekly_budget=10)
    if dca.should_buy():
        lines.append("DCA: READY to buy (weekly cycle)")
    else:
        last = datetime.fromisoformat(dca.state["last_buy"])
        days = (datetime.now(timezone.utc) - last).days
        lines.append(f"DCA: next buy in {7-days} days (${dca.state.get('total_invested',0):.0f} total invested)")
    
    # Emerging crypto
    scanner = EmergingCryptoScanner()
    trending = scanner.get_trending()
    if trending:
        names = ", ".join(f"{t['symbol']}" for t in trending[:5])
        lines.append(f"TRENDING: {names}")
    
    gainers = scanner.get_gainers()
    if gainers:
        top = gainers[0]
        lines.append(f"TOP GAINER: {top['symbol']} +{top['change_24h']}% (${top['price']:.4f})")
    
    # Whale activity
    whales = WhaleCopyTrader()
    whale_sig = whales.get_signal()
    lines.append(f"WHALES: {whale_sig['signal']} ({whale_sig['moves']} moves)")
    
    # Scalp candidates
    try:
        import yfinance as yf
        scalper = MicroScalper()
        wp = {}
        for t in ["SPY","QQQ","AAPL","NVDA","AMD","TSLA"]:
            try:
                wp[t] = yf.Ticker(t).history(period="1mo")["Close"].values
            except: pass
        candidates = scalper.get_scalp_candidates(wp)
        if candidates:
            for c in candidates:
                lines.append(f"SCALP: {c['ticker']} {c['signal']} z={c['z']:+.1f} rsi3={c['rsi3']:.0f}")
        else:
            lines.append("SCALP: no setups")
    except:
        lines.append("SCALP: data unavailable")
    
    return "\n".join(lines)


if __name__ == "__main__":
    print(strategy_report())
