"""Kalshi Market Scanner — Valedia ASI
Port of kalshi-command-center's scan logic using our auth.
Scans 600+ Kalshi markets, scores by heuristic edge.

Edge Score = spread_tightness(15) + centrality(25) + liquidity(35) + time_value(25)
Optimized via grid search on 165 backtest markets.

(c) GOATWORTHY Post LLC — March 2026
"""
import os, sys, json, re, math
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kalshi_auth import get

# Blocked prefixes (weather, entertainment, social media)
BLOCKED_PREFIXES = {
    "KXHIGH", "KXLOW", "KXRAIN", "KXSNOW", "KXTEMP", "KXWIND",
    "KXWEATH", "KXCELEB", "KXMOVIE", "KXTIKTOK", "KXYT", "KXTWIT", "KXSTREAM",
}

# Noise patterns — markets where AI has no edge
NOISE_POLITICS = {
    "primary", "runoff", "special election", "city council",
    "state senate", "margin of victory", "vote share",
    "win by more than", "percentage of vote", "leave office",
}
COINFLIP = {"when will", "how many", "what will be the", "who will the next"}

_price_re = re.compile(
    r'(price|close|drop|fall|rise|trade|open|hit|reach|touch|break|stay)'
    r'\s+(above|below|over|under)', re.IGNORECASE)
_asset_re = re.compile(
    r'(bitcoin|btc|ethereum|eth|solana|sol|s&p|nasdaq|gold|oil|crude)'
    r'\s+.{0,20}(above|below|over|under|exceed)\s+\$?[\d,]+', re.IGNORECASE)


def scan_markets(include_sports=False, max_pages=3, top_n=10):
    """Scan Kalshi markets and rank by heuristic edge score.
    
    Returns list of dicts sorted by edge_score descending.
    Each dict has: ticker, title, yes_bid, yes_ask, mid, spread,
    volume, oi, days_to_close, edge_score.
    """
    all_markets = []
    cursor = None
    
    for page in range(max_pages):
        params = 'limit=200&status=open&mve_filter=exclude'
        if cursor:
            params += f'&cursor={cursor}'
        try:
            r = get(f'/markets?{params}')
            markets = r.get('markets', [])
            all_markets.extend(markets)
            cursor = r.get('cursor')
            if not cursor or not markets:
                break
        except:
            break

    scored = []
    for m in all_markets:
        ticker = m.get('ticker', '')
        title = m.get('title', '')
        # Kalshi v2 API uses _fp suffix fields
        volume = int(float(m.get('volume_fp', 0) or 0))
        oi = int(float(m.get('open_interest_fp', 0) or 0))
        vol24h = int(float(m.get('volume_24h_fp', 0) or 0))
        liquidity = float(m.get('liquidity_dollars', 0) or 0)
        yes_bid = float(m.get('yes_bid_dollars', '0') or '0') * 100
        yes_ask = float(m.get('yes_ask_dollars', '0') or '0') * 100
        
        if not yes_bid or not yes_ask:
            continue
        if any(ticker.upper().startswith(p) for p in BLOCKED_PREFIXES):
            continue
        # Use volume OR liquidity as activity proxy — allow low-activity markets
        # Edge scoring naturally penalizes illiquid markets via liq_score
        activity = max(volume, vol24h)
        
        title_lower = title.lower()
        if _price_re.search(title) or _asset_re.search(title):
            continue
        if any(p in title_lower for p in NOISE_POLITICS):
            continue
        
        # Sports filter
        sports_tokens = {'nba', 'nfl', 'nhl', 'mlb', 'ncaa', 'pga', 'ufc', 'ahl',
                         'mls', 'atp', 'wta', 'boxing', 'mma', 'nascar', 'f1',
                         'kxnba', 'kxnhl', 'kxmlb', 'kxahl', 'kxncaa',
                         'strikeout', 'double double', 'threes', 'touchdown'}
        is_sport = any(t in f"{ticker} {title}".lower() for t in sports_tokens)
        if is_sport and not include_sports:
            continue
        if include_sports and not is_sport:
            continue
        # Time filter
        exp = m.get('expected_expiration_time', '') or m.get('close_time', '')
        days_to_close = None
        if exp:
            try:
                exp_dt = datetime.fromisoformat(exp.replace('Z', '+00:00'))
                days_to_close = max(0, (exp_dt - datetime.now(timezone.utc)).total_seconds() / 86400)
            except: pass
        if days_to_close is not None and (days_to_close < 1 or days_to_close > 180):
            continue
        
        mid = (yes_bid + yes_ask) / 2
        spread = yes_ask - yes_bid
        
        # Coinflip filter
        if 40 <= mid <= 60:
            if any(p in title_lower for p in COINFLIP):
                continue
        
        # === EDGE SCORE (optimized weights) ===
        spread_pct = spread / max(mid, 1) * 100
        spread_score = max(0, 20 - spread_pct) / 20
        centrality = 1 - abs(mid - 50) / 50
        if mid < 15 or mid > 85:
            centrality *= 0.3
        liq_score = math.log1p(activity) * 0.6 + math.log1p(oi) * 0.4
        time_score = 1.0
        if days_to_close is not None:
            if days_to_close < 14:
                time_score = days_to_close / 14
            elif days_to_close > 60:
                time_score = max(0.3, 1 - (days_to_close - 60) / 120)
        edge_score = (spread_score * 15 + centrality * 25 
                     + liq_score * 35 + time_score * 25)
        
        scored.append({
            'ticker': ticker, 'title': title[:55],
            'yes_bid': int(yes_bid), 'yes_ask': int(yes_ask),
            'mid': int(mid), 'spread': int(spread),
            'spread_pct': round(spread_pct, 1),
            'volume': activity, 'oi': oi,
            'days_to_close': round(days_to_close, 1) if days_to_close else None,
            'edge_score': round(edge_score, 1),
        })
    
    scored.sort(key=lambda x: x['edge_score'], reverse=True)
    return scored[:top_n], len(all_markets), len(scored)


def run_scan(include_sports=False):
    """Run the scanner and print results."""
    top, total, passed = scan_markets(include_sports=include_sports)
    tag = "SPORTS" if include_sports else "MACRO"
    print(f"KALSHI {tag} SCAN — {total} markets, {passed} passed filters")
    print()
    for i, m in enumerate(top, 1):
        days = f"{m['days_to_close']:.0f}d" if m['days_to_close'] else "?"
        print(f"  {i}. [{m['edge_score']:>5.1f}] {m['title']}")
        print(f"     {m['yes_bid']}c/{m['yes_ask']}c spread:{m['spread']}c "
              f"vol:{m['volume']:,} OI:{m['oi']:,} {days}")
        print(f"     {m['ticker']}")
    return top


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--sports", action="store_true")
    args = parser.parse_args()
    run_scan(include_sports=args.sports)
