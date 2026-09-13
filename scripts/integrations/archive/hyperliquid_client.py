"""Hyperliquid Client - Abundance Agent
DeFi perpetual futures. Low fees, wide asset coverage.
Uses wallet address for read, private key for trades.
"""
import os, json, requests
from hyperliquid.utils import constants
from hyperliquid.info import Info

HL_WALLET = os.environ.get("HYPERLIQUID_WALLET", "")
HL_BASE = constants.MAINNET_API_URL

def get_info():
    return Info(HL_BASE, skip_ws=True)

def get_user_state():
    if not HL_WALLET:
        return None
    info = get_info()
    return info.user_state(HL_WALLET)

def get_open_orders():
    if not HL_WALLET:
        return []
    info = get_info()
    return info.open_orders(HL_WALLET)

def get_meta():
    info = get_info()
    return info.meta()

def get_all_mids():
    info = get_info()
    return info.all_mids()

def market_overview():
    lines = ["Hyperliquid Markets"]
    try:
        mids = get_all_mids()
        top_assets = ["BTC", "ETH", "SOL", "XRP", "AVAX", "LINK", "ADA", "DOT"]
        for asset in top_assets:
            price = mids.get(asset)
            if price:
                lines.append(f"  {asset}: ${float(price):,.2f}")
        lines.append(f"  Total markets: {len(mids)}")
        
        if HL_WALLET:
            state = get_user_state()
            if state:
                margin = state.get("marginSummary", {})
                equity = float(margin.get("accountValue", 0))
                lines.append(f"  Account equity: ${equity:,.2f}")
                positions = state.get("assetPositions", [])
                if positions:
                    lines.append(f"  Open positions: {len(positions)}")
                    for p in positions[:5]:
                        pos = p.get("position", {})
                        sym = pos.get("coin", "?")
                        size = float(pos.get("szi", 0))
                        pnl = float(pos.get("unrealizedPnl", 0))
                        lines.append(f"    {sym}: size={size} PnL=${pnl:,.2f}")
    except Exception as e:
        lines.append(f"  Error: {e}")
    return "\n".join(lines)

if __name__ == "__main__":
    print(market_overview())
