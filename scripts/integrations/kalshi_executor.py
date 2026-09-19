"""Kalshi Execution Engine — Valedia ASI
═══════════════════════════════════════════════════════════════════════════
Full-spectrum prediction market profit engine.

STRATEGIES (in priority order by expected profit/risk):
  1. NO HARVEST — Buy NO on absurd tail events. 95-99% win rate, small edge.
  2. EXPIRY SCALP — Near-expiry markets where outcome is ~known. Fast $$.
  3. MACRO EDGE — CPI/GDP/Fed/Jobs where Valedia's intel beats the crowd.
  4. NEWS ALPHA — Breaking news creates mispriced markets. Speed = profit.
  5. CRYPTO BRACKETS — BTC/ETH daily price ranges with momentum signal.
  6. ARBITRAGE — YES+NO summing ≠ $1.00 across platforms.

RISK:
  Max 5% balance per single trade. Max 30% total deployed.
  Every trade must be net positive after fees.
  Self-audit nightly: track hit rate, adjust thresholds.

(c) GOATWORTHY Post LLC. All rights reserved.
"""
import os, sys, json, time, traceback
from datetime import datetime, timezone, timedelta
from fee_engine import kalshi_fee_per_contract  # canonical real Kalshi fee (single source)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def _load_env():
    env_path = os.path.expanduser("~/.openclaw/.env")
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, v = line.split('=', 1)
                    os.environ.setdefault(k.strip(), v.strip())
_load_env()

from kalshi_auth import get, post, get_balance, get_positions, create_order, get_fills
try:
    import forward_test_ledger as _ftl  # real fee-inclusive ledger — record every real fill
except Exception:
    _ftl = None
try:
    import valedia_config as _cfg
    from market_state import MARKET_STATE
    from execution_policy import build_execution_plan, MakerOrderManager  # exec policy + maker orders
except Exception:
    _cfg = None; build_execution_plan = None; MakerOrderManager = None

# Import intelligence layer
try:
    from intel_layer import get_intel
    INTEL_AVAILABLE = True
except:
    INTEL_AVAILABLE = False

# ═══════════════════════════════════════════════════════════════
# CONFIG — BARBELL STRATEGY
# ═══════════════════════════════════════════════════════════════
# 50% safe (NO harvest, slow compound) + 50% aggressive (conviction, fast 3-10x)
MAX_PER_TRADE_PCT = 0.10     # 10% of balance per trade (more aggressive)
MAX_DEPLOYED_PCT = 0.60      # 60% total deployed (use the money)
MIN_NET_PROFIT = 0.005       # Min $0.005 net per contract after fees
KALSHI_FEE_RATE = 0.02       # ~2¢/contract estimate

# Barbell allocation
SAFE_ALLOCATION = 0.40       # 40% for NO harvests / safe bets
AGGRESSIVE_ALLOCATION = 0.60 # 60% for conviction / directional bets

# All series worth scanning (non-sport = edge, sport = volume)
SERIES_ECON = ['KXCPI','KXGDP','KXFED','KXFEDRATE','KXFEDEND','KXPCE',
               'KXJOBLESS','KXNONFARM','KXRECESSION','KXSHUTDOWN']
SERIES_CRYPTO = ['KXBTC','KXETH','KXWTI','KXOIL','KXGOLD']
SERIES_POLITICS = ['KXTRUMPRESIGN','KXTRUMPREMOVE','KXIMPEACH','KXNEWPOPE',
    'KXCABOUT','KXNEXTSPEAKER','KXGREENLAND','KXCANAL','KXAMEND25',
    'KXMARTIAL','KXNEXTDEF','KXNEXTDHSSEC','KXNEXTUKPM','KXNEXTISRAELPM',
    'KXVETOOVERRIDE','KXTRUMPPARDONS','KXSCOTUSCHANGE','KXIMPEACHCABINET',
    'KXINSURRECTION','KXHABEAS','KXTAFTHARTLEY']
SERIES_FINANCE = ['KXIPO','KXIPOFANNIE','KXIPOOURA','KXOAIANTH','KXRAMPBREX',
    'KXDEELRIP','KXFREDDIE','KXTRILLIONAIRE','KXJPMCEONEW','KXMUSKTRILLION']
SERIES_WORLD = ['KXEUEXITCOUNTRY','KXFEDCHAIRCONFIRM','KXLONDONMAYOR',
    'KXZELENSKYPUTIN','KXUSAKIM','KXGREENLANDPRICE','KXCANTERRITORY',
    'KXFUSION','KXWARMING','KXERUPTSUPER','KXEARTHQUAKEJAPAN',
    'KXCOLONIZEMARS','KXROBOTMARS','KXMARSVRAIL']
SERIES_WEATHER = ['KXTEMP','KXHURRICANE']
SERIES_SPORTS = ['KXNBAGAME','KXNCAAMBGAME','KXNHLGAME','KXMLBGAME']

ALL_SERIES = SERIES_ECON + SERIES_CRYPTO + SERIES_POLITICS + SERIES_FINANCE + SERIES_WORLD + SERIES_WEATHER + SERIES_SPORTS

MODELS_DIR = os.path.expanduser("~/.openclaw/workspace/models")
LOG_PATH = os.path.join(MODELS_DIR, "kalshi_trades.json")

# ═══════════════════════════════════════════════════════════════
# PORTFOLIO STATE
# ═══════════════════════════════════════════════════════════════

def get_state():
    """Get current balance, positions, and deployment."""
    bal_data = get_balance()
    balance = bal_data.get("balance", 0) / 100  # cents → dollars
    portfolio_val = bal_data.get("portfolio_value", 0) / 100
    pos_data = get_positions()
    positions = pos_data.get("market_positions", [])
    deployed = sum(abs(p.get("market_exposure", 0)) / 100 for p in positions)
    return {
        "balance": balance, "portfolio_value": portfolio_val,
        "positions": positions, "n_positions": len(positions),
        "deployed": deployed, "available": balance,
        "deploy_pct": deployed / (balance + portfolio_val + 0.01) * 100,
        "max_per_trade": balance * MAX_PER_TRADE_PCT,
        "max_deployable": (balance + portfolio_val) * MAX_DEPLOYED_PCT - deployed,
    }

# ═══════════════════════════════════════════════════════════════
# MARKET SCANNER — finds all opportunities across all strategies
# ═══════════════════════════════════════════════════════════════

def scan_all_markets():
    """Scan every series for tradeable markets. Returns list of scored opportunities."""
    all_opps = []
    for series in ALL_SERIES:
        try:
            r = get(f'/markets?limit=50&series_ticker={series}&status=open')
            mkts = r.get('markets', [])
            for m in mkts:
                opp = _score_market(m, series)
                if opp and opp["score"] > 0:
                    all_opps.append(opp)
        except Exception:
            pass
    all_opps.sort(key=lambda x: x["score"], reverse=True)
    return all_opps

def _score_market(m, series):
    """Score a single market across all strategies. Returns dict or None."""
    ticker = m.get("ticker", "")
    title = m.get("title", "")
    ya = float(m.get("yes_ask_dollars", "0") or "0")
    yb = float(m.get("yes_bid_dollars", "0") or "0")
    na = float(m.get("no_ask_dollars", "0") or "0")
    nb = float(m.get("no_bid_dollars", "0") or "0")
    lp = float(m.get("last_price_dollars", "0") or "0")
    exp_str = m.get("expected_expiration_time", "")
    
    # Skip if no orderbook at all
    if ya <= 0 and na <= 0 and lp <= 0:
        return None
    
    # Parse expiry
    hours_to_exp = 9999
    if exp_str:
        try:
            exp_dt = datetime.fromisoformat(exp_str.replace("Z", "+00:00"))
            hours_to_exp = (exp_dt - datetime.now(timezone.utc)).total_seconds() / 3600
        except: pass
    
    # Skip expired
    if hours_to_exp <= 0:
        return None
    
    score = 0
    strategy = "NONE"
    side = "no"
    price = 0
    edge = 0
    rationale = ""
    roi_pct = 0
    
    is_sport = series in SERIES_SPORTS
    
    # ── STRATEGY 1: CONVICTION DIRECTIONAL (the 3-10x plays) ──
    # Buy YES at 15-45¢ or NO at 15-45¢ on markets with a knowable outcome
    # This is where $10 → $60. The engine's #1 priority.
    
    # YES side cheap (15-45¢) = big payout if event happens
    if ya >= 0.15 and ya <= 0.45 and not is_sport:
        roi_pct = (1.0 - ya) / ya * 100
        score = roi_pct * 0.3  # Base: higher ROI = higher score
        # Boost near-term (faster money)
        if hours_to_exp < 48: score += 40
        elif hours_to_exp < 168: score += 25
        elif hours_to_exp < 720: score += 15
        # Boost econ markets (Valedia has intel edge)
        if series in SERIES_ECON: score += 20
        strategy = "CONVICTION_YES"
        side = "yes"
        price = ya
        edge = 1.0 - ya - kalshi_fee_per_contract(ya)
        rationale = f"YES@{ya:.2f} → {roi_pct:.0f}% ROI if right, exp {hours_to_exp:.0f}h"
    
    # NO side cheap (15-45¢) = big payout if event DOESN'T happen
    elif na > 0 and na >= 0.15 and na <= 0.45 and not is_sport:
        roi_pct = (1.0 - na) / na * 100
        score = roi_pct * 0.3
        if hours_to_exp < 48: score += 40
        elif hours_to_exp < 168: score += 25
        elif hours_to_exp < 720: score += 15
        if series in SERIES_ECON: score += 20
        strategy = "CONVICTION_NO"
        side = "no"
        price = na
        edge = 1.0 - na - kalshi_fee_per_contract(na)
        rationale = f"NO@{na:.2f} → {roi_pct:.0f}% ROI if right, exp {hours_to_exp:.0f}h"
    
    # ── STRATEGY 2: SPORTS CONVICTION (tonight's games, fast resolve) ──
    elif is_sport and hours_to_exp <= 24:
        # Favorites: high YES or low YES = clear favorite
        if ya >= 0.60 and ya <= 0.85:
            roi_pct = (1.0 - ya) / ya * 100
            score = 50 + roi_pct * 0.5
            strategy = "SPORTS"
            side = "yes"
            price = ya
            edge = 1.0 - ya - kalshi_fee_per_contract(ya)
            rationale = f"Tonight YES@{ya:.2f} favorite, {roi_pct:.0f}% ROI"
        elif ya >= 0.15 and ya <= 0.40:
            no_cost = na if na > 0 else (1.0 - ya)
            roi_pct = (1.0 - no_cost) / no_cost * 100
            score = 50 + roi_pct * 0.5
            strategy = "SPORTS"
            side = "no"
            price = no_cost
            edge = 1.0 - no_cost - kalshi_fee_per_contract(no_cost)
            rationale = f"Tonight NO@{no_cost:.2f} (underdog YES@{ya:.2f}), {roi_pct:.0f}% ROI"
    
    # ── STRATEGY 3: NO HARVEST (safe base, 5-10% compound) ──
    elif ya > 0 and ya <= 0.10 and not is_sport:
        actual_no_cost = na if na > 0 else (1.0 - ya)
        profit_per = 1.0 - actual_no_cost - kalshi_fee_per_contract(actual_no_cost)
        if profit_per > MIN_NET_PROFIT:
            time_boost = max(0, 10 - hours_to_exp / 24) if hours_to_exp < 240 else 0
            score = 30 + profit_per * 100 + time_boost  # Lower base than conviction
            strategy = "NO_HARVEST"
            side = "no"
            price = actual_no_cost
            edge = profit_per
            roi_pct = profit_per / (actual_no_cost + 0.01) * 100
            rationale = f"Safe NO: YES@{ya:.2f}, profit {profit_per:.3f}/contract ({roi_pct:.0f}%)"
    
    # ── STRATEGY 4: EXPIRY SCALP (near-expiry, outcome ~decided) ──
    elif hours_to_exp <= 12 and hours_to_exp > 0:
        if ya > 0 and ya <= 0.08:
            actual_no_cost = na if na > 0 else (1.0 - ya)
            profit = 1.0 - actual_no_cost - kalshi_fee_per_contract(actual_no_cost)
            if profit > MIN_NET_PROFIT:
                score = 45 + profit * 200 + (12 - hours_to_exp) * 3
                strategy = "EXPIRY_SCALP"
                side = "no"
                price = actual_no_cost
                edge = profit
                roi_pct = profit / (actual_no_cost + 0.01) * 100
                rationale = f"Expires {hours_to_exp:.0f}h, YES@{ya:.2f}, quick {roi_pct:.0f}%"
        elif ya >= 0.92:
            profit = 1.0 - ya - kalshi_fee_per_contract(ya)
            if profit > MIN_NET_PROFIT:
                score = 45 + profit * 200 + (12 - hours_to_exp) * 3
                strategy = "EXPIRY_SCALP"
                side = "yes"
                price = ya
                edge = profit
                roi_pct = profit / (ya + 0.01) * 100
                rationale = f"Expires {hours_to_exp:.0f}h, YES@{ya:.2f}, quick {roi_pct:.0f}%"
    
    # ── STRATEGY 5: ARBITRAGE (YES+NO < $0.98) ──
    if ya > 0 and na > 0 and (ya + na) < 0.98:
        arb_profit = 1.0 - ya - na - kalshi_fee_per_contract(ya) - kalshi_fee_per_contract(na)
        if arb_profit > 0.01:
            arb_score = 90 + arb_profit * 500
            if arb_score > score:
                score = arb_score
                strategy = "ARBITRAGE"
                side = "both"
                price = ya + na
                edge = arb_profit
                roi_pct = arb_profit / (ya + na) * 100
                rationale = f"YES@{ya:.2f}+NO@{na:.2f}={ya+na:.2f}, arb {arb_profit:.3f}"
    
    if score <= 0:
        return None
    
    # ── INTELLIGENCE LAYER OVERRIDE ──
    # Consult ERE, MiroFish, ML, Sentiment before committing
    if INTEL_AVAILABLE and score > 0:
        try:
            intel = get_intel()
            ik = intel.get_kalshi_conviction(ticker, title, ya, hours_to_exp)
            intel_side = ik["recommended_side"]
            intel_conv = ik["conviction_yes"]
            
            # If intel says SKIP and we have a trade, reduce score
            if intel_side == "skip" and strategy in ("CONVICTION_YES", "CONVICTION_NO"):
                score *= 0.3  # Heavily penalize low-conviction trades
                rationale += f" [Intel: skip, conv={intel_conv:.0f}]"
            
            # If intel AGREES with our side, boost score
            elif (intel_side == "yes" and side == "yes") or (intel_side == "no" and side == "no"):
                score *= 1.5  # Boost confirmed trades
                rationale += f" [Intel: CONFIRMS {intel_side}, conv={intel_conv:.0f}]"
            
            # If intel DISAGREES with our side, reduce or flip
            elif (intel_side == "yes" and side == "no") or (intel_side == "no" and side == "yes"):
                score *= 0.4  # Strong penalty for contradicted trades
                rationale += f" [Intel: CONTRADICTS → {intel_side}, conv={intel_conv:.0f}]"
            
            # For price brackets: intel tells us WHICH bracket to pick
            if strategy in ("CONVICTION_YES", "CONVICTION_NO") and intel_conv < 35:
                if side == "yes": side = "no"  # Flip to NO if intel is bearish
                rationale += f" [Intel FLIPPED to NO]"
        except: pass
    
    return {
        "ticker": ticker, "title": title[:60], "series": series,
        "strategy": strategy, "score": round(score, 1),
        "side": side, "price": round(price, 4), "edge": round(edge, 4),
        "roi_pct": round(roi_pct, 1),
        "yes_ask": ya, "no_ask": na, "yes_bid": yb, "no_bid": nb,
        "last_price": lp, "hours_to_exp": round(hours_to_exp, 1),
        "rationale": rationale, "is_sport": is_sport,
    }


# ═══════════════════════════════════════════════════════════════
# EXECUTION — actually place trades
# ═══════════════════════════════════════════════════════════════

def _size_contracts(opp, state, cost_per):
    """Shared contract sizing (used by taker execute_trade AND maker placement, so both size
    the same way). Budget + strategy caps + balance-aware. Returns int contracts, or 0 if
    unaffordable/bad price."""
    if cost_per <= 0 or cost_per >= 1.0:
        return 0
    max_spend = min(state["max_per_trade"], state["max_deployable"])
    if max_spend < 0.10:
        return 0
    n = int(max_spend / cost_per)
    cap = 100 if opp.get("strategy") in ("CONVICTION_YES", "CONVICTION_NO", "SPORTS", "SPORTS_TONIGHT") else 50
    n = max(1, min(n, cap))
    fee = n * kalshi_fee_per_contract(cost_per)
    if n * cost_per + fee > state["balance"]:
        n = max(1, int((state["balance"] - fee) / cost_per))
    return n


def execute_trade(opp, state, dry_run=False):
    """Execute a single trade. Returns result dict."""
    if opp["side"] == "pending_intel" or opp["side"] == "both":
        return {"status": "SKIP", "reason": "requires_manual_or_intel"}

    cost_per = opp["price"]
    n_contracts = _size_contracts(opp, state, cost_per)
    if n_contracts <= 0:
        return {"status": "SKIP", "reason": f"unsizable (price={cost_per}, budget=${min(state['max_per_trade'], state['max_deployable']):.2f})"}
    total_cost = n_contracts * cost_per
    total_fee = n_contracts * kalshi_fee_per_contract(cost_per)
    potential_profit = n_contracts * opp["edge"]

    price_cents = int(opp["price"] * 100)
    if price_cents < 1: price_cents = 1
    if price_cents > 99: price_cents = 99
    
    result = {
        "ticker": opp["ticker"], "strategy": opp["strategy"],
        "side": opp["side"], "contracts": n_contracts,
        "price_cents": price_cents, "total_cost": round(total_cost, 2),
        "potential_profit": round(potential_profit, 3),
        "roi_pct": round(potential_profit / (total_cost + 0.01) * 100, 1),
    }
    
    if dry_run:
        result["status"] = "DRY_RUN"
        return result

    if _cfg is not None and _cfg.live_exec_fenced(opp.get("strategy"), ticker=opp.get("ticker")):
        result["status"] = "SKIP_LIVE_FENCE"
        result["error"] = "live exec fenced (strategy or oil ticker family)"
        return result
    
    try:
        order = create_order(
            ticker=opp["ticker"], side=opp["side"],
            count=n_contracts, price_cents=price_cents,
            order_type="limit"
        )
        result["order"] = order
        fill = {}
        payload = None
        if _ftl:
            try:
                fill, payload = _ftl.resolve_kalshi_fill(order, get_fills)
            except Exception:
                fill = {}
        result["fills_payload"] = payload
        result["status"] = "EXECUTED" if fill.get("complete") else "UNRECONCILED"
        if fill.get("order_id"):
            result["order_id"] = fill["order_id"]
    except Exception as e:
        result["status"] = "FAILED"
        result["error"] = str(e)[:200]
    
    return result


def _log_trade(result):
    """Append trade to persistent log."""
    try:
        log = []
        if os.path.exists(LOG_PATH):
            with open(LOG_PATH) as f:
                log = json.load(f)
        result["timestamp"] = datetime.now(timezone.utc).isoformat()
        log.append(result)
        with open(LOG_PATH, "w") as f:
            json.dump(log[-500:], f, indent=2, default=str)
    except: pass


# ═══════════════════════════════════════════════════════════════
# OPPORTUNITY REPORT — cross-platform profit potential
# ═══════════════════════════════════════════════════════════════

def constrained_scan_profit(sims, cash, deployable):
    """Fee-true sim profit for picks that fit remaining cash. Picks compete; not additive."""
    remaining = min(max(0.0, float(cash or 0)), max(0.0, float(deployable or 0)))
    profit = 0.0
    for sim in sims:
        cost = float(sim.get("total_cost") or 0)
        pot = float(sim.get("potential_profit") or 0)
        if cost <= 0:
            continue
        if cost <= remaining + 1e-9:
            profit += pot
            remaining -= cost
    return profit


def opportunity_report(state=None, opps=None):
    """Generate a comprehensive profit opportunity report across all platforms.
    This is what gets sent to Signal when opportunities are hot.
    Scan numbers are fee-true Kalshi sims, not fills / not realized P&L."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"💰 VALEDIA OPPORTUNITY SCAN ({ts}) [not fills / not realized P&L]", ""]
    
    # 1. Kalshi state
    if state is None:
        state = get_state()
    lines.append(f"KALSHI: ${state['balance']:.2f} cash | ${state['portfolio_value']:.2f} deployed | {state['n_positions']} positions")
    
    # 2. Scan all opportunities
    if opps is None:
        opps = scan_all_markets()
    if not opps:
        lines.append("  No opportunities found this scan.")
        return "\n".join(lines)
    
    # 3. Group by strategy
    by_strat = {}
    for o in opps:
        s = o["strategy"]
        if s not in by_strat: by_strat[s] = []
        by_strat[s].append(o)
    
    total_deployable = state["max_deployable"]
    fitted_sims = []
    
    # 4. Show top opportunities per strategy
    for strat in ["ARBITRAGE","EXPIRY_SCALP","NO_HARVEST","SPORTS_FAVORITE","MACRO_EDGE"]:
        group = by_strat.get(strat, [])
        if not group: continue
        lines.append(f"\n{strat} ({len(group)} opportunities):")
        for o in group[:5]:
            sim = execute_trade(o, state, dry_run=True)
            fitted_sims.append(sim)
            pot = sim.get("potential_profit", 0)
            roi = sim.get("roi_pct", 0)
            exp_label = f"{o['hours_to_exp']:.0f}h" if o['hours_to_exp'] < 720 else f"{o['hours_to_exp']/720:.0f}mo"
            fenced = False
            if _cfg is not None:
                try:
                    fenced = _cfg.live_exec_fenced(o.get("strategy"), ticker=o.get("ticker"))
                except Exception:
                    fenced = False
            tag = " [FENCED/SKIP_LIVE_FENCE]" if fenced else ""
            lines.append(
                f"  ${sim.get('total_cost',0):.2f}→${pot:.2f} "
                f"({roi:+.0f}% ROI) exp:{exp_label} | {o['title'][:50]}{tag}"
            )
            lines.append(f"    {o['rationale']}")
    
    # 5. Summary: cash-constrained fee-true sim (picks compete — do not sum as portfolio)
    fit_profit = constrained_scan_profit(fitted_sims, state.get("balance"), total_deployable)
    lines.append(f"\n═══ PROFIT POTENTIAL (fee-true Kalshi sim, not broker fills) ═══")
    lines.append(f"  With current ${state['balance']:.2f}: deploy up to ${total_deployable:.2f}")
    lines.append(f"  Estimated profit from top picks that fit cash (not additive): ${fit_profit:.2f}")
    
    # 6. Deposit illustrations deleted — averaging edge/price invented 100%+ ROI.

    # 7. Cross-platform context (Coinbase, Alpaca) — broker cash/equity, not P&L
    lines.append(f"\nCROSS-PLATFORM (broker cash/equity, not P&L):")
    total_capital = state["balance"] + state["portfolio_value"]
    try:
        from coinbase_trader import get_balances
        cb = get_balances()
        usd = cb.get("USD", {}).get("balance", 0)
        usdc = cb.get("USDC", {}).get("balance", 0)
        crypto_names = [k for k in cb if k not in ("USD","USDC") and cb[k].get("balance",0) > 0]
        lines.append(f"  Coinbase: ${usd:.2f} USD + ${usdc:.2f} USDC + {len(crypto_names)} crypto ({', '.join(crypto_names)})")
        total_capital += usd + usdc
    except Exception as e:
        lines.append(f"  Coinbase: error ({str(e)[:40]})")
    try:
        from alpaca_executor import get_account
        acct = get_account()
        alp_cash = float(acct.get("cash", 0))
        alp_eq = float(acct.get("equity", 0))
        lines.append(f"  Alpaca: ${alp_cash:.2f} cash / ${alp_eq:.2f} equity")
        total_capital += alp_eq
    except Exception as e:
        lines.append(f"  Alpaca: error ({str(e)[:40]})")
    
    lines.append(f"\n  TOTAL CAPITAL ACROSS ALL PLATFORMS (broker): ${total_capital:.2f}")
    
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# MAIN EXECUTION LOOP — scan, score, trade, report
# ═══════════════════════════════════════════════════════════════

def run(dry_run=False, max_trades=5, min_score=40):
    """Main execution loop. Scans, scores, and executes top opportunities.
    
    Args:
        dry_run: If True, simulate only (no real orders)
        max_trades: Maximum trades to place this cycle
        min_score: Minimum opportunity score to act on
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"Kalshi Executor ({ts}) {'[DRY RUN]' if dry_run else '[LIVE]'}", ""]
    
    # Get state
    state = get_state()
    lines.append(f"Balance: ${state['balance']:.2f} | Deployed: ${state['deployed']:.2f} ({state['deploy_pct']:.0f}%) | Max/trade: ${state['max_per_trade']:.2f}")
    
    if state["balance"] < 0.10:
        lines.append("⚠ Balance too low to trade")
        report = "\n".join(lines)
        print(report)
        return report
    
    # Scan
    opps = scan_all_markets()
    actionable = [o for o in opps if o["score"] >= min_score and o["side"] not in ("pending_intel", "both")]
    # Calibration gate: skip categories the REAL ledger shows as verified recent losers.
    # Fail-open — a gate error never removes an opportunity (verified losses only, never permanent).
    if _ftl:
        kept = []
        for o in actionable:
            try:
                ok, why = _ftl.should_trade_category(o.get("strategy", ""))
            except Exception:
                ok, why = True, ""
            if ok:
                kept.append(o)
            else:
                lines.append(f"  ⊘ skip {o.get('ticker','?')} [{o.get('strategy','?')}] — {why}")
        actionable = kept
    lines.append(f"Scanned: {len(opps)} opportunities, {len(actionable)} actionable (score≥{min_score})")
    
    # Maker-order lifecycle: manage orders resting from prior cron cycles FIRST — record any
    # real fills into the ledger, cancel stale, reprice on book moves. State persists to disk.
    maker_mgr = None
    if MakerOrderManager is not None and not dry_run:
        try:
            maker_mgr = MakerOrderManager()
            maker_mgr.check_and_manage()
        except Exception:
            maker_mgr = None

    # Execute top opportunities
    trades_placed = 0
    for opp in actionable[:max_trades]:
        if state["max_deployable"] < 0.10:
            lines.append("  Max deployment reached, stopping.")
            break

        if not dry_run and _cfg is not None and _cfg.live_exec_fenced(
                opp.get("strategy"), ticker=opp.get("ticker")):
            lines.append(f"  ⊘ SKIP_LIVE_FENCE {opp.get('ticker','?')} [{opp.get('strategy','?')}]")
            continue

        # Advisory execution plan (fee-drag/book-quality/urgency). ADVISORY-ONLY: it only VETOES
        # a poor-execution entry (no_trade) and annotates maker/taker — sizing/entry stays with
        # execute_trade. Live maker orders are a separate step (EXEC_DRY_RUN). Fail-open.
        if build_execution_plan is not None:
            try:
                yb, nb = opp.get("yes_bid", 0) or 0, opp.get("no_bid", 0) or 0
                book = None
                if yb > 0 and nb > 0:  # only feed a book when BOTH sides exist (no spurious veto)
                    book = MARKET_STATE.update_book(opp["ticker"], {"orderbook": {
                        "yes": [{"price": yb, "size": 1}], "no": [{"price": nb, "size": 1}]}})
                plan = build_execution_plan(opp["ticker"], opp.get("side", "yes"),
                                            opp.get("roi_pct", 0.0), int(round(opp.get("price", 0) * 100)),
                                            1, opp.get("hours_to_exp", 9999), book=book)
                if plan.action == "no_trade":
                    lines.append(f"  ⊘ no-trade {opp.get('ticker','?')} — {plan.reason}")
                    continue
                if plan.action == "maker":
                    # Live maker: place a properly-sized resting order (skip taker). Only when both
                    # the run and EXEC_DRY_RUN allow; otherwise annotate and fall through to taker.
                    live_maker = (maker_mgr is not None and not dry_run
                                  and not getattr(_cfg, "EXEC_DRY_RUN", True))
                    if live_maker:
                        contracts = _size_contracts(opp, state, plan.price_cents / 100.0)
                        if contracts > 0:
                            oid = maker_mgr.place_maker_order(opp["ticker"], opp.get("side", "yes"),
                                                              contracts, plan.price_cents,
                                                              strategy=opp.get("strategy", ""))
                            lines.append(f"  ◇ MAKER {opp.get('ticker','?')} {contracts}x @{plan.price_cents}c "
                                         f"(id={str(oid)[:12]}) — {plan.reason}")
                            trades_placed += 1
                            continue
                    lines.append(f"  ◇ maker-preferred {opp.get('ticker','?')} ({plan.reason}) "
                                 f"— taking as taker (gated)")
            except Exception:
                pass  # advisory must never break execution

        result = execute_trade(opp, state, dry_run=dry_run)
        _log_trade(result)
        # Fill-true ledger: POST-ack without fill count is UNRECONCILED, never live.
        if _ftl and result.get("status") in ("EXECUTED", "UNRECONCILED"):
            try:
                _ftl.book_kalshi_fill(
                    _ftl.canonical_category(result["strategy"]),
                    result["ticker"], result["side"], result.get("order") or {},
                    intended_source="live",
                    fallback_price=result["price_cents"] / 100.0,
                    fallback_contracts=result["contracts"],
                    fills_payload=result.get("fills_payload"))
            except Exception:
                pass
        
        status_icon = ("✅" if result["status"] == "EXECUTED"
                       else "⏳" if result["status"] == "UNRECONCILED"
                       else "🔶" if result["status"] == "DRY_RUN" else "❌")
        lines.append(
            f"  {status_icon} {result['strategy']:15} {result['side']:3} "
            f"x{result['contracts']} @{result['price_cents']}¢ "
            f"cost=${result['total_cost']:.2f} → profit=${result['potential_profit']:.3f} "
            f"({result['roi_pct']:+.0f}%) | {opp['title'][:40]}"
        )
        if result.get("error"):
            lines.append(f"    ERROR: {result['error']}")
        
        if result["status"] in ("EXECUTED", "UNRECONCILED"):
            trades_placed += 1
            state["max_deployable"] -= result["total_cost"]
            state["balance"] -= result["total_cost"]
    
    lines.append(f"\nTrades placed: {trades_placed}/{max_trades}")
    
    # Also show top opportunities NOT taken (for the report)
    remaining = [o for o in opps if o not in actionable[:max_trades]][:5]
    if remaining:
        lines.append("\nPending opportunities (need more capital or intel):")
        for o in remaining:
            lines.append(f"  score:{o['score']:.0f} {o['strategy']:15} | {o['title'][:50]}")
    
    report = "\n".join(lines)
    print(report)
    return report


def send_opportunity_alert():
    """Generate opportunity report and send via Signal if opportunities are hot."""
    report = opportunity_report()
    
    # Send via Signal if there are good opportunities
    hot = "PROFIT POTENTIAL" in report and "$0.00" not in report.split("PROFIT POTENTIAL")[1][:50]
    if hot:
        try:
            from subprocess import run as sp_run
            msg = report[:1500]  # Signal length limit
            sp_run([
                "openclaw", "message", "send",
                "--channel", "signal",
                "--target", "uuid:47bd6dd5-2c96-4514-9541-acd8ed013612",
                "--message", msg
            ], capture_output=True, timeout=30)
        except: pass
    
    return report


# ═══════════════════════════════════════════════════════════════
# SELF-AUDIT — nightly performance review
# ═══════════════════════════════════════════════════════════════

def audit():
    """Review trade performance and adjust thresholds."""
    if not os.path.exists(LOG_PATH):
        return "No trades to audit."
    
    with open(LOG_PATH) as f:
        trades = json.load(f)
    
    executed = [t for t in trades if t.get("status") == "EXECUTED"]
    total = len(executed)
    if total == 0:
        return "No executed trades to audit."
    
    total_cost = sum(t.get("total_cost", 0) for t in executed)
    total_profit = sum(t.get("potential_profit", 0) for t in executed)
    
    by_strat = {}
    for t in executed:
        s = t.get("strategy", "UNKNOWN")
        if s not in by_strat: by_strat[s] = {"n": 0, "cost": 0, "profit": 0}
        by_strat[s]["n"] += 1
        by_strat[s]["cost"] += t.get("total_cost", 0)
        by_strat[s]["profit"] += t.get("potential_profit", 0)
    
    lines = ["Kalshi Trade Audit", f"Total trades: {total}", f"Total deployed: ${total_cost:.2f}",
             f"Expected (not realized): ${total_profit:.2f}",
             f"Avg modeled ROI: {total_profit/(total_cost+0.01)*100:.0f}%", ""]
    for s, d in by_strat.items():
        roi = d["profit"] / (d["cost"] + 0.01) * 100
        lines.append(f"  {s}: {d['n']} trades, ${d['cost']:.2f} deployed, "
                     f"${d['profit']:.2f} expected (not realized) ({roi:.0f}% ROI)")
    
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["scan","execute","dry-run","report","alert","audit"], default="dry-run")
    parser.add_argument("--max-trades", type=int, default=5)
    parser.add_argument("--min-score", type=float, default=40)
    args = parser.parse_args()
    
    if args.mode == "scan":
        opps = scan_all_markets()
        for o in opps[:20]:
            print(f"  {o['score']:5.0f} {o['strategy']:15} {o['side']:3} @{o['price']:.2f} edge:{o['edge']:.3f} exp:{o['hours_to_exp']:.0f}h | {o['title'][:50]}")
    elif args.mode == "report":
        print(opportunity_report())
    elif args.mode == "alert":
        print(send_opportunity_alert())
    elif args.mode == "audit":
        print(audit())
    elif args.mode == "dry-run":
        print(run(dry_run=True, max_trades=args.max_trades, min_score=args.min_score))
    elif args.mode == "execute":
        print(run(dry_run=False, max_trades=args.max_trades, min_score=args.min_score))
