"""Polymarket Client — Abundance Agent
Scans Polymarket CLOB API for NO harvest and arbitrage opportunities.
Public API — no auth needed for market data.
"""
import requests, json
from datetime import datetime, timezone

POLY_BASE = "https://clob.polymarket.com"
GAMMA_BASE = "https://gamma-api.polymarket.com"

def get_active_markets(limit=100):
    """Fetch active markets from Polymarket Gamma API."""
    r = requests.get(f"{GAMMA_BASE}/markets", params={
        "limit": limit, "active": True, "closed": False
    })
    r.raise_for_status()
    return r.json()

def find_no_harvest(max_yes_price=0.15):
    """Find markets where YES is cheap (NO is expensive = high-prob NO).
    These are tail-risk markets where retail overpays for unlikely outcomes.
    """
    markets = get_active_markets(limit=200)
    opportunities = []
    for m in markets:
        tokens = m.get("tokens", [])
        if len(tokens) >= 2:
            # Find YES token price
            for t in tokens:
                if t.get("outcome", "").lower() == "yes":
                    price = float(t.get("price", 0.5))
                    if 0 < price <= max_yes_price:
                        no_price = 1.0 - price
                        edge = (no_price - 0.90) / 0.10 * 100 if no_price >= 0.90 else 0
                        opportunities.append({
                            "question": m.get("question", "?")[:60],
                            "slug": m.get("slug", ""),
                            "yes_price": round(price, 4),
                            "no_price": round(no_price, 4),
                            "edge_pct": round(edge, 1),
                            "volume": m.get("volume", 0),
                            "liquidity": m.get("liquidity", 0),
                            "end_date": m.get("endDate", "")[:10],
                        })
    opportunities.sort(key=lambda x: x["edge_pct"], reverse=True)
    return opportunities

def find_arb_vs_kalshi():
    """Find price discrepancies between Polymarket and Kalshi on similar events."""
    poly_markets = get_active_markets(limit=100)
    # Return raw data for the agent to cross-reference with Kalshi
    return [{
        "question": m.get("question", "?")[:60],
        "yes_price": None,
        "tags": m.get("tags", []),
        "slug": m.get("slug", ""),
    } for m in poly_markets[:50]]

def scan_report():
    """Generate Polymarket scan report for Signal."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"Polymarket Scan ({ts})", ""]
    
    nos = find_no_harvest(max_yes_price=0.15)
    lines.append(f"NO Harvest (YES <= 15c): {len(nos)} found")
    for opp in nos[:5]:
        lines.append(f"  > {opp['question']}")
        lines.append(f"    YES:{opp['yes_price']} NO:{opp['no_price']} Edge:{opp['edge_pct']}% Vol:{opp['volume']}")
    
    if not nos:
        lines.append("  No opportunities this scan.")
    
    return "\n".join(lines)

if __name__ == "__main__":
    print(scan_report())
