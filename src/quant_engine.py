"""Prediction Market Quant Engine — Valedia ASI
Implements the LunarResearcher/hedge fund framework:
  1. LMSR pricing model (understand market maker mechanics)
  2. EV gap scanning (your probability vs market price)
  3. Kelly criterion sizing (optimal bankroll fraction)
  4. Bayesian probability estimation (base rates + updates)
  5. Cross-platform arbitrage (Kalshi vs Polymarket)

PLATFORMS:
  - Kalshi: SCAN-ONLY from this module (writers are executor/allocator/sports)
  - Polymarket: BUILT BUT DISABLED (waiting for non-sports access)

The math: C(q) = b * ln(sum(e^(qi/b)))
  - Same softmax function GPT uses to pick next token
  - Every share you buy moves the price against you
  - Edge = your_probability - market_probability
  - Only trade when edge > fees + slippage

(c) GOATWORTHY Post LLC — March 2026
"""
import os, sys, json, math
from datetime import datetime, timezone
from valedia_config import (  # shared source of truth (drift-guarded)
    MIROFISH_WEIGHT, effective_mirofish_weight, KELLY_SAFETY_FRACTION, dynamic_per_trade_cap,
)
from fee_engine import kalshi_fee_per_contract  # canonical real Kalshi fee (single source)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

MODELS = os.path.expanduser("~/.openclaw/workspace/models")
QUANT_LOG = os.path.join(MODELS, "quant_engine_log.json")
QUANT_STATE = os.path.join(MODELS, "quant_engine_state.json")

# ═══ FORMULA 1: LMSR PRICING MODEL ═══
# C(q) = b * ln(sum(e^(qi/b))) where b = liquidity parameter

def lmsr_cost(quantities, b=100):
    """LMSR cost function. Returns total cost of current market state.
    quantities: list of outstanding shares per outcome [yes_shares, no_shares]
    b: liquidity parameter (higher = less price impact per trade)"""
    return b * math.log(sum(math.exp(q / b) for q in quantities))

def lmsr_price(quantities, outcome_idx, b=100):
    """LMSR price for a specific outcome (probability implied by market).
    This is the softmax function — same math as GPT token selection."""
    exp_vals = [math.exp(q / b) for q in quantities]
    return exp_vals[outcome_idx] / sum(exp_vals)

def lmsr_trade_cost(quantities, outcome_idx, shares, b=100):
    """Cost to buy 'shares' of outcome_idx. Price moves against you."""
    new_quantities = list(quantities)
    new_quantities[outcome_idx] += shares
    return lmsr_cost(new_quantities, b) - lmsr_cost(quantities, b)

def lmsr_price_impact(market_price, shares, b=100):
    """Estimate price impact of buying N shares.
    Returns (avg_price_per_share, final_market_price, slippage_cents)."""
    # Convert market price to implied quantities
    p = max(0.01, min(0.99, market_price))
    q_yes = b * math.log(p / (1 - p))
    quantities = [q_yes, 0]
    
    cost = lmsr_trade_cost(quantities, 0, shares, b)
    avg_price = cost / shares if shares > 0 else 0
    new_q = [q_yes + shares, 0]
    final_price = lmsr_price(new_q, 0, b)
    slippage = (avg_price - market_price) * 100  # in cents
    
    return round(avg_price, 4), round(final_price, 4), round(slippage, 2)


# ═══ FORMULA 2: EXPECTED VALUE GAP ═══

def expected_value(true_prob, market_price, fees_pct=0.007):
    """Calculate EV per dollar. The core edge equation.
    true_prob: your estimated probability (0-1)
    market_price: current market price in dollars (0-1)
    fees_pct: platform fees (Kalshi ~0.7%, Polymarket ~0.1%)
    
    Returns: EV per share, edge in cents, recommendation"""
    if market_price <= 0 or market_price >= 1:
        return 0, 0, "SKIP"
    
    # EV = P(win) * payout - P(lose) * cost - fees
    payout_if_win = 1.0 - market_price  # profit per share if YES wins
    cost_if_lose = market_price          # loss per share if YES loses
    fee = kalshi_fee_per_contract(market_price)  # real Kalshi fee 0.07*P*(1-P) (was market_price*fees_pct)
    
    ev_yes = true_prob * payout_if_win - (1 - true_prob) * cost_if_lose - fee
    ev_no = (1 - true_prob) * market_price - true_prob * (1 - market_price) - fee
    
    edge_cents = round(max(ev_yes, ev_no) * 100, 1)
    
    if ev_yes > 0.03:  # 3 cent minimum edge
        return round(ev_yes, 4), edge_cents, "BUY_YES"
    elif ev_no > 0.03:
        return round(ev_no, 4), edge_cents, "BUY_NO"
    else:
        return 0, edge_cents, "SKIP"


# ═══ FORMULA 3: KELLY CRITERION ═══

def kelly_size(true_prob, market_price, bankroll, fraction=KELLY_SAFETY_FRACTION):
    """Kelly criterion position sizing. Quarter-Kelly by default (conservative).
    full Kelly: f* = (p*b - q) / b where b = payout ratio, q = 1-p
    fraction: KELLY_SAFETY_FRACTION (half-Kelly). This scanner does not place orders.
    
    Returns: (bet_amount, contracts, kelly_fraction)"""
    if market_price <= 0 or market_price >= 1 or true_prob <= 0:
        return 0, 0, 0
    
    b = (1 - market_price) / market_price  # payout ratio (odds)
    p = true_prob
    q = 1 - p
    
    # Full Kelly fraction
    full_kelly = (p * b - q) / b if b > 0 else 0
    
    if full_kelly <= 0:
        return 0, 0, 0  # No edge — don't bet
    
    # Apply fractional Kelly (half-Kelly; shared: valedia_config.KELLY_SAFETY_FRACTION)
    f = full_kelly * fraction
    f = min(f, dynamic_per_trade_cap())  # dynamic ceiling (shared: valedia_config.dynamic_per_trade_cap)
    
    bet_amount = round(bankroll * f, 2)
    contracts = max(1, int(bet_amount / market_price))
    
    return bet_amount, contracts, round(f, 4)


# ═══ FORMULA 4: BAYESIAN PROBABILITY ESTIMATION ═══

def bayesian_update(prior, evidence_for, evidence_against):
    """Update probability estimate given new evidence.
    prior: starting probability (0-1)
    evidence_for: strength of evidence supporting YES (0-1)
    evidence_against: strength of evidence supporting NO (0-1)
    
    Returns: posterior probability"""
    # Likelihood ratio
    lr = (evidence_for + 0.01) / (evidence_against + 0.01)
    
    # Bayes' rule: posterior = prior * LR / (prior * LR + (1-prior))
    numerator = prior * lr
    posterior = numerator / (numerator + (1 - prior))
    
    return round(max(0.01, min(0.99, posterior)), 4)

def estimate_probability(market_price, external_sources):
    """Combine multiple probability sources into one estimate.
    external_sources: list of (source_name, probability, weight) tuples
    
    Example:
        estimate_probability(0.40, [
            ("CME_FedWatch", 0.51, 0.4),
            ("MiroFish", 0.55, 0.2),
            ("ERE_regime", 0.48, 0.1),
            ("base_rate", 0.50, 0.3),
        ])
    """
    if not external_sources:
        return market_price  # No external info, trust the market
    
    total_weight = sum(w for _, _, w in external_sources)
    if total_weight == 0:
        return market_price
    
    weighted_prob = sum(p * w for _, p, w in external_sources) / total_weight
    
    # Shrink toward market price (market is usually more right than you think)
    # 70% external model, 30% market price (conservative)
    blended = 0.70 * weighted_prob + 0.30 * market_price
    
    return round(max(0.01, min(0.99, blended)), 4)


# ═══ FORMULA 5: CROSS-PLATFORM ARBITRAGE ═══

def find_arbitrage(kalshi_yes, kalshi_no, poly_yes=None, poly_no=None):
    """Find risk-free arbitrage when same event priced differently.
    If YES on one platform + NO on other < $1.00, that's free money.
    
    Returns: (arb_exists, profit_per_dollar, strategy)"""
    results = []
    
    # Kalshi internal: YES + NO should = ~$1.00
    if kalshi_yes and kalshi_no:
        total = kalshi_yes + kalshi_no
        if total < 0.98:  # Less than 98 cents = arb
            profit = 1.0 - total
            results.append((profit, f"Kalshi internal: buy YES@{kalshi_yes:.2f} + NO@{kalshi_no:.2f} = {total:.2f}"))
    
    # Cross-platform: Kalshi YES + Polymarket NO (or vice versa)
    if poly_yes is not None and kalshi_no is not None:
        total = poly_yes + kalshi_no
        if total < 0.97:  # Need bigger margin for cross-platform (withdrawal fees)
            profit = 1.0 - total
            results.append((profit, f"Cross: Poly YES@{poly_yes:.2f} + Kalshi NO@{kalshi_no:.2f}"))
    
    if poly_no is not None and kalshi_yes is not None:
        total = kalshi_yes + poly_no
        if total < 0.97:
            profit = 1.0 - total
            results.append((profit, f"Cross: Kalshi YES@{kalshi_yes:.2f} + Poly NO@{poly_no:.2f}"))
    
    if results:
        best = max(results, key=lambda x: x[0])
        return True, round(best[0], 4), best[1]
    return False, 0, "No arbitrage"


# ═══ PLATFORM ADAPTERS ═══

class KalshiAdapter:
    """Live Kalshi market data. ACTIVE."""
    ENABLED = True
    FEES = 0.007  # ~0.7% average
    
    @staticmethod
    def scan_markets(categories=None):
        """Scan Kalshi for EV opportunities using quant framework."""
        os.environ.setdefault('KALSHI_PRIVATE_KEY_PATH', os.path.expanduser('~/.hermes/secrets/kalshi_private_key.pem'))
        from kalshi_auth import get
        
        opportunities = []
        cursor = None
        for page in range(3):
            params = 'limit=200&status=open&mve_filter=exclude'
            if cursor:
                params += f'&cursor={cursor}'
            try:
                r = get(f'/markets?{params}')
                markets = r.get('markets', [])
                for m in markets:
                    ya = float(m.get('yes_ask_dollars', '0') or '0')
                    yb = float(m.get('yes_bid_dollars', '0') or '0')
                    if ya <= 0.01 or ya >= 0.99 or yb <= 0:
                        continue
                    
                    mid = (ya + yb) / 2
                    spread = ya - yb
                    ticker = m.get('ticker', '')
                    title = m.get('title', '')
                    
                    opportunities.append({
                        'platform': 'kalshi',
                        'ticker': ticker,
                        'title': title[:60],
                        'yes_ask': ya,
                        'yes_bid': yb,
                        'mid': mid,
                        'spread': spread,
                        'implied_prob': mid,
                    })
                cursor = r.get('cursor')
                if not cursor:
                    break
            except:
                break
        return opportunities

class PolymarketAdapter:
    """Polymarket data. DISABLED — built for future use."""
    ENABLED = False  # Toggle when Burton gets non-sports access
    FEES = 0.001  # ~0.1% US
    
    @staticmethod
    def scan_markets(categories=None):
        """Scan Polymarket for EV opportunities. DISABLED."""
        if not PolymarketAdapter.ENABLED:
            return []
        # Future: use Polymarket CLOB API
        # https://docs.polymarket.com/#introduction
        # GET /markets for market list
        # GET /book for orderbook
        # Requires USDC wallet + Polygon chain
        return []


# ═══ MAIN QUANT SCANNER ═══

def scan_for_edges(bankroll=300, min_edge_cents=3):
    """Full quant scan: pull markets, estimate probabilities, find edges, size bets.
    This is the complete LunarResearcher pipeline."""
    
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    results = []
    
    # Load intelligence for probability estimation
    intel_sources = []
    try:
        import mirofish_intel_archive as _mia
        mf = _mia.load_for_fv(os.path.join(MODELS, "mirofish_signal.json"))
        if mf:
            intel_sources.append(("MiroFish", mf.get("overall_bias", 50) / 100, effective_mirofish_weight()))  # capped until proven; valedia_config
    except: pass
    try:
        from ere_runtime import read_gate
        gate = read_gate("BOOK", models_dir=MODELS)
        regime = "calm" if gate.get("safe") and gate.get("multiplier", 0) >= 1.0 else "volatile"
        intel_sources.append(("ERE", 0.55 if regime == "calm" else 0.45, 0.10))
    except: pass
    
    # Scan Kalshi
    kalshi_markets = KalshiAdapter.scan_markets()
    
    for m in kalshi_markets:
        market_price = m['mid']
        spread = m['spread']
        
        # Skip wide spreads (illiquid = can't execute)
        if spread > 0.15:
            continue
        # Skip near-certain outcomes (no edge possible)
        if market_price < 0.10 or market_price > 0.90:
            continue
        
        # Estimate true probability using available intelligence
        # For now, use market price as base (we improve this per-category later)
        true_prob = estimate_probability(market_price, intel_sources)
        
        # Calculate EV
        ev, edge_cents, action = expected_value(true_prob, m['yes_ask'], KalshiAdapter.FEES)
        
        if action == "SKIP" or edge_cents < min_edge_cents:
            continue
        
        # Kelly sizing
        price = m['yes_ask'] if action == "BUY_YES" else (1 - m['yes_bid'])
        bet_amt, contracts, kelly_f = kelly_size(true_prob, price, bankroll)
        
        if contracts < 1:
            continue
        
        # Price impact check
        avg_price, final_price, slippage = lmsr_price_impact(price, contracts)
        
        results.append({
            'platform': 'kalshi',
            'ticker': m['ticker'],
            'title': m['title'],
            'market_price': round(market_price, 3),
            'true_prob': true_prob,
            'action': action,
            'ev_per_share': ev,
            'edge_cents': edge_cents,
            'kelly_fraction': kelly_f,
            'bet_amount': bet_amt,
            'contracts': contracts,
            'slippage_cents': slippage,
            'spread': round(spread, 3),
        })
    
    # Scan Polymarket (disabled but ready)
    poly_markets = PolymarketAdapter.scan_markets()
    # Future: same analysis pipeline for Polymarket
    
    # Sort by edge (highest first)
    results.sort(key=lambda x: -x['edge_cents'])
    
    return results, ts


def run(bankroll=300, top_n=10):
    """Run the full quant scan and display results."""
    results, ts = scan_for_edges(bankroll)
    
    print(f"QUANT ENGINE ({ts}) [SCAN-ONLY]")
    print(f"Bankroll: {bankroll:.2f} | Min edge: 3c | Kelly: half (display, no orders)")
    print(f"Kalshi: SCAN | Polymarket: DISABLED")
    print(f"Found {len(results)} opportunities\n")
    
    for i, r in enumerate(results[:top_n], 1):
        print(f"  {i}. [{r['edge_cents']:>4.1f}c] {r['action']:8} {r['title'][:40]}")
        print(f"     mkt:{r['market_price']:.2f} est:{r['true_prob']:.2f} "
              f"kelly:{r['kelly_fraction']:.3f} bet:{r['bet_amount']:.2f} "
              f"x{r['contracts']} slip:{r['slippage_cents']:.1f}c")
        print(f"     {r['ticker']}")
    
    # Save results
    with open(QUANT_LOG, 'w') as f:
        json.dump({
            'ts': ts, 'bankroll': bankroll,
            'opportunities': results[:20],
            'total_scanned': len(results),
            'platforms': {'kalshi': True, 'polymarket': PolymarketAdapter.ENABLED},
        }, f, indent=2)
    
    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Prediction Market Quant Engine")
    parser.add_argument("--bankroll", type=float, default=300)
    parser.add_argument("--top", type=int, default=10)
    args = parser.parse_args()
    run(bankroll=args.bankroll, top_n=args.top)
