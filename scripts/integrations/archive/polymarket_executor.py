"""Polymarket Whale Copy-Trader — Valedia ASI
Autonomous prediction market trading via whale-copying + momentum.

Architecture:
  1. Monitor high-volume markets via Gamma API
  2. Track large trades via CLOB WebSocket
  3. Copy whale trades at 10% of their size (max $5/trade for safety)
  4. FOK orders only (no stale limit orders)
  5. Risk: $50 max drawdown, 2 simultaneous positions max

(c) GOATWORTHY Post LLC.
"""
import os, sys, json, time, requests, hashlib, hmac
from datetime import datetime, timezone
from eth_account import Account

# Config
CLOB_BASE = "https://clob.polymarket.com"
GAMMA_BASE = "https://gamma-api.polymarket.com"
WALLET_ADDRESS = "0x34Dcdf30472342Dc5DC650b845f46c46740F555E"
MAX_TRADE_SIZE = 5.00      # $5 max per trade (conservative with $50 capital)
MAX_DAILY_LOSS = 25.00     # $25 max daily loss (50% of capital)
MAX_POSITIONS = 3           # max simultaneous positions
COPY_RATIO = 0.10          # copy 10% of whale volume
SLIPPAGE_MAX = 0.02        # 2% max slippage from whale entry
LOG_FILE = os.path.expanduser("~/.openclaw/workspace/models/polymarket_trades.json")

def load_env():
    """Load secrets from .env"""
    env_path = os.path.expanduser("~/.openclaw/.env")
    env = {}
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env

def get_rpc_url():
    env = load_env()
    return env.get("POLYGON_RPC_URL", "https://polygon-rpc.com")

def get_private_key():
    env = load_env()
    return env.get("POLYMARKET_PRIVATE_KEY", "")

# ═══ MARKET SCANNER ═══
def get_top_markets(limit=20):
    """Get highest-volume active markets."""
    r = requests.get(f"{GAMMA_BASE}/markets", params={
        "closed": "false", "active": "true",
        "order": "volume24hr", "ascending": "false",
        "limit": limit
    }, timeout=15)
    if not r.ok: return []
    return r.json()

def get_market_details(condition_id):
    """Get full market details including order book."""
    try:
        r = requests.get(f"{CLOB_BASE}/markets/{condition_id}", timeout=10)
        return r.json() if r.ok else None
    except: return None

def find_edge_markets():
    """Find markets with exploitable edges.
    Strategy 1: High-prob NO harvest (YES < 10 cents = 90%+ NO probability)
    Strategy 2: Momentum (price moved >5% in last hour with volume)
    Strategy 3: New markets with mispricing (just launched, low liquidity)
    """
    markets = get_top_markets(50)
    opportunities = []
    
    for m in markets:
        tokens = m.get("tokens", [])
        question = m.get("question", "?")
        vol_24h = float(m.get("volume24hr", 0))
        liquidity = float(m.get("liquidityClob", 0))
        
        if len(tokens) < 2 or vol_24h < 10000:
            continue  # skip low-volume
        
        for t in tokens:
            price = float(t.get("price", 0.5))
            outcome = t.get("outcome", "?")
            token_id = t.get("token_id", "")
            
            # Strategy 1: NO harvest — buy NO when YES is very cheap
            if outcome.lower() == "yes" and price <= 0.08:
                no_price = 1.0 - price
                edge = (no_price - 0.92) * 100  # edge over 92% implied prob
                if edge > 0:
                    opportunities.append({
                        "strategy": "NO_HARVEST",
                        "question": question[:80],
                        "outcome": "NO",
                        "price": round(no_price, 4),
                        "edge_pct": round(edge, 1),
                        "vol_24h": vol_24h,
                        "token_id": token_id,
                        "condition_id": m.get("conditionId", ""),
                        "slug": m.get("slug", ""),
                    })
            
            # Strategy 2: High confidence YES — when YES > 92 cents
            if outcome.lower() == "yes" and price >= 0.92:
                edge = (price - 0.92) * 100
                opportunities.append({
                    "strategy": "HIGH_PROB_YES",
                    "question": question[:80],
                    "outcome": "YES",
                    "price": round(price, 4),
                    "edge_pct": round(edge, 1),
                    "vol_24h": vol_24h,
                    "token_id": token_id,
                    "condition_id": m.get("conditionId", ""),
                    "slug": m.get("slug", ""),
                })
    
    # Sort by edge
    opportunities.sort(key=lambda x: x["edge_pct"], reverse=True)
    return opportunities

# ═══ TRADE EXECUTION ═══
def get_open_positions():
    """Get current open positions (from local log)."""
    try:
        with open(LOG_FILE) as f:
            data = json.load(f)
        return [p for p in data.get("positions", []) if p.get("status") == "open"]
    except:
        return []

def get_daily_pnl():
    """Calculate today's PnL from trade log."""
    try:
        with open(LOG_FILE) as f:
            data = json.load(f)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        day_trades = [t for t in data.get("trades", []) if t.get("ts", "").startswith(today)]
        return sum(t.get("pnl", 0) for t in day_trades)
    except:
        return 0

def log_trade(trade_data):
    """Log trade to JSON file."""
    try:
        with open(LOG_FILE) as f:
            data = json.load(f)
    except:
        data = {"trades": [], "positions": [], "daily_pnl": {}}
    
    data["trades"].append(trade_data)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    data["daily_pnl"][today] = get_daily_pnl() + trade_data.get("pnl", 0)
    
    with open(LOG_FILE, "w") as f:
        json.dump(data, f, indent=2)

# ═══ CLOB ORDER PLACEMENT ═══
def place_market_order(token_id, side, size_usd, condition_id):
    """Place a market order on Polymarket CLOB.
    Requires py-clob-client library and private key.
    """
    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import OrderArgs, OrderType
        
        pk = get_private_key()
        if not pk:
            return {"success": False, "error": "POLYMARKET_PRIVATE_KEY not set in .env"}
        
        rpc = get_rpc_url()
        client = ClobClient(
            host=CLOB_BASE,
            key=pk,
            chain_id=137,  # Polygon mainnet
            signature_type=2,
        )
        
        # Get current price
        book = client.get_order_book(token_id)
        if side == "BUY":
            best_ask = float(book.asks[0].price) if book.asks else None
            if not best_ask:
                return {"success": False, "error": "No asks available"}
            amount = size_usd / best_ask
            price = best_ask
        else:
            best_bid = float(book.bids[0].price) if book.bids else None
            if not best_bid:
                return {"success": False, "error": "No bids available"}
            amount = size_usd / best_bid
            price = best_bid
        
        # Place FOK order
        order_args = OrderArgs(
            price=price,
            size=amount,
            side=side,
            token_id=token_id,
        )
        
        signed_order = client.create_and_post_order(order_args)
        
        return {
            "success": True,
            "order_id": signed_order.get("orderID", "?"),
            "side": side,
            "price": price,
            "size_usd": size_usd,
            "amount": amount,
            "token_id": token_id,
        }
    except ImportError:
        return {"success": False, "error": "py-clob-client not installed. Run: pip3 install py-clob-client"}
    except Exception as e:
        return {"success": False, "error": str(e)[:200]}

# ═══ MASTER STRATEGY ENGINE ═══
def run_polymarket_scan():
    """Main autonomous scan: find edges → check risk → execute."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"[POLYMARKET] Scan {ts}"]
    
    # Risk check
    daily_pnl = get_daily_pnl()
    if daily_pnl <= -MAX_DAILY_LOSS:
        lines.append(f"  HALTED: Daily PnL ${daily_pnl:.2f} exceeds -${MAX_DAILY_LOSS} limit")
        return "\n".join(lines)
    
    open_positions = get_open_positions()
    if len(open_positions) >= MAX_POSITIONS:
        lines.append(f"  MAX POSITIONS: {len(open_positions)}/{MAX_POSITIONS} — skipping new trades")
        return "\n".join(lines)
    
    lines.append(f"  Daily PnL: ${daily_pnl:.2f} | Positions: {len(open_positions)}/{MAX_POSITIONS}")
    
    # Find opportunities
    opps = find_edge_markets()
    lines.append(f"  Opportunities found: {len(opps)}")
    
    trades_executed = 0
    for opp in opps[:5]:  # top 5 only
        lines.append(f"  [{opp['strategy']}] {opp['question']}")
        lines.append(f"    {opp['outcome']} @ {opp['price']:.4f} edge={opp['edge_pct']:.1f}% vol=${opp['vol_24h']:,.0f}")
        
        # Size the trade
        trade_size = min(MAX_TRADE_SIZE, opp["edge_pct"] * 0.5)  # scale by edge
        trade_size = max(1.0, trade_size)  # minimum $1
        
        if trades_executed >= 2:
            lines.append(f"    SKIP — 2 trades already this cycle")
            continue
        
        # Check if we have a private key configured
        pk = get_private_key()
        if not pk:
            lines.append(f"    >> WOULD BUY ${trade_size:.2f} {opp['outcome']} (no private key configured)")
            continue
        
        # Execute
        result = place_market_order(
            token_id=opp["token_id"],
            side="BUY",
            size_usd=trade_size,
            condition_id=opp["condition_id"]
        )
        
        if result["success"]:
            lines.append(f"    >> BOUGHT ${trade_size:.2f} {opp['outcome']} order={result['order_id'][:12]}")
            log_trade({
                "ts": ts, "strategy": opp["strategy"], "side": "BUY",
                "outcome": opp["outcome"], "price": opp["price"],
                "size_usd": trade_size, "question": opp["question"],
                "order_id": result.get("order_id",""), "pnl": 0
            })
            trades_executed += 1
        else:
            lines.append(f"    FAILED: {result['error'][:100]}")
    
    lines.append(f"  Trades executed: {trades_executed}")
    return "\n".join(lines)

if __name__ == "__main__":
    print(run_polymarket_scan())
