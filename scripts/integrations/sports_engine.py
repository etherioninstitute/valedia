"""Valedia Sports Engine v1.0 — 70-85¢ favorites only.

Sizing is the same unproven throttle the allocator uses (dynamic_per_trade_cap,
~4%/trade while the fee-true ledger is thin). The old 86%/100% WR was retracted
(W vs WIN bug + no-horizon resolver) — it is not a sizing input.

Architecture:
  1. SCAN — Find all sports games resolving in next 24h
  2. FILTER — Only 70-85¢ favorites
  3. SIZE — min(edge table, dynamic_per_trade_cap); never 10% while unproven
  4. EXECUTE — Limit at the ask; EXECUTED+live only on real fill data
  5. TRACK — fee-inclusive ledger (UNRECONCILED until fill-true)

(c) GOATWORTHY Post LLC. All rights reserved.
"""
import os, sys, json, time
from datetime import datetime, timezone, timedelta
from valedia_config import dynamic_per_trade_cap, version_stamp

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
MODELS = os.path.expanduser("~/.openclaw/workspace/models")
SPORTS_LOG = os.path.join(MODELS, "sports_engine_log.json")

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
from kalshi_auth import get, create_order, get_balance
import forward_test_ledger as ftl  # the ONE fee-inclusive ledger (record_entry + category gate)

# ═══ CONFIGURATION — THE ONLY KNOBS ═══
MIN_PRICE = 0.70  # Minimum favorite price (below = too risky)
MAX_PRICE = 0.85  # Maximum favorite price (above = no edge over market)
FUND_PER_TRADE_MAX = 0.04  # fund-rules.md 4%/trade; unproven cap already ~4%
MAX_GAMES = 12    # Max games per cycle (diversification)
SERIES = ['KXNBAGAME', 'KXNHLGAME', 'KXNCAAMBGAME']  # MLB KILLED: 4W/3L -$24.94
CATEGORY = "SPORTS"  # single ledger/gate bucket for the whole sports strategy

# ═══ INTELLIGENCE SOURCES ═══
# These were all sitting idle. Now they boost every bet.

def get_mirofish_bias():
    """Read MiroFish swarm sentiment — overall market mood."""
    try:
        mf = json.load(open(os.path.join(MODELS, "mirofish_signal.json")))
        return mf.get("overall_bias", 50)
    except: return 50

def get_ere_regime():
    """Read ERE v2.0 regime — are markets calm or turbulent?"""
    try:
        ere = json.load(open(os.path.join(MODELS, "latest_etherion_risk.json")))
        signals = ere.get("signals", {})
        # If most signals are FLAT/MILD, markets are calm → sports favorites more reliable
        flat_count = sum(1 for s in signals.values() if isinstance(s, dict) and s.get("signal") in ("FLAT", "MILD"))
        return "CALM" if flat_count > len(signals) / 2 else "VOLATILE"
    except: return "UNKNOWN"

def get_fear_greed():
    """Current crypto Fear & Greed — extreme fear = less risk appetite = favorites win more."""
    try:
        import requests
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5)
        return int(r.json()["data"][0]["value"])
    except: return 50

def get_standings_edge(league, team_name):
    """Check if a team has injuries that affect the bet."""
    try:
        from injury_checker import get_injury_edge_adjustment
        # Extract team abbreviation from title
        # Title format: "Minnesota at Dallas Winner?" → favorite team is in ticker
        return get_injury_edge_adjustment(team_name[:3].upper(), league.lower())
    except:
        return 0

# ═══ STEP 1: SCAN — Find all games ═══

def scan_games():
    """Find all sports games with favorites in the 70-85¢ sweet spot."""
    games = []
    for series in SERIES:
        try:
            r = get(f'/markets?limit=50&series_ticker={series}&status=open')
            mkts = r.get('markets', [])
            
            # Group into game pairs (each game has 2 sides)
            pairs = {}
            for m in mkts:
                ticker = m['ticker']
                # Game ID = everything before last dash
                parts = ticker.rsplit('-', 1)
                game_id = parts[0]
                if game_id not in pairs:
                    pairs[game_id] = []
                pairs[game_id].append(m)
            
            for game_id, sides in pairs.items():
                if len(sides) < 2: continue
                
                # Find favorite (higher YES ask price)
                sides.sort(key=lambda x: float(x.get('yes_ask_dollars','0') or '0'), reverse=True)
                fav = sides[0]
                dog = sides[1]
                
                fav_price = float(fav.get('yes_ask_dollars', '0') or '0')
                fav_bid = float(fav.get('yes_bid_dollars', '0') or '0')
                dog_price = float(dog.get('yes_ask_dollars', '0') or '0')
                exp = fav.get('expected_expiration_time', '')[:16]
                spread = round(fav_price - fav_bid, 3)
                
                # FILTER: Only 70-85¢ favorites
                if MIN_PRICE <= fav_price <= MAX_PRICE:
                    league = series.replace('KX','').replace('GAME','')
                    
                    # ═══ INTELLIGENCE SCORING ═══
                    # Combine all signals into one edge_score
                    edge_score = 0
                    edge_reasons = []
                    
                    # 1. Spread analysis: tight spread = market confident
                    if spread <= 0.03:
                        edge_score += 10
                        edge_reasons.append("tight_spread")
                    elif spread >= 0.10:
                        edge_score -= 10
                        edge_reasons.append("wide_spread_caution")
                    
                    # 2. Price position: lower in range = more upside
                    if fav_price <= 0.75:
                        edge_score += 15
                        edge_reasons.append("high_roi_zone")
                    elif fav_price >= 0.82:
                        edge_score -= 5
                        edge_reasons.append("low_roi_zone")
                    
                    # 3. Dog price sanity: if dog is also expensive, game is close → skip
                    if dog_price >= 0.35:
                        edge_score -= 15
                        edge_reasons.append("close_game")
                    elif dog_price <= 0.15:
                        edge_score += 10
                        edge_reasons.append("clear_favorite")
                    
                    # 4. Injury check on favorite team
                    fav_team = fav['ticker'].rsplit('-', 1)[-1][:3]
                    inj_adj = get_standings_edge(league, fav_team)
                    if inj_adj != 0:
                        edge_score += inj_adj
                        edge_reasons.append(f"injuries_{inj_adj:+d}")
                    
                    games.append({
                        'game_id': game_id,
                        'league': league,
                        'fav_ticker': fav['ticker'],
                        'fav_title': fav.get('title', '')[:50],
                        'fav_price': fav_price,
                        'fav_bid': fav_bid,
                        'spread': spread,
                        'dog_ticker': dog['ticker'],
                        'dog_price': dog_price,
                        'exp': exp,
                        'edge_score': edge_score,
                        'edge_reasons': edge_reasons,
                    })
        except Exception as e:
            pass
    
    # Sort by edge_score (best bets first), then expiration
    games.sort(key=lambda x: (-x.get('edge_score', 0), x['exp']))
    
    # Add real sports-relevant signals (not crypto noise)
    fg = get_fear_greed()
    
    for g in games:
        league = g.get('league', '')
        
        # League rank only (not a sizing input). Retracted 86%/100% WR is not used.
        league_edge = {'NBA': 10, 'NCAAMB': 10, 'NHL': 0}.get(league, -5)
        if league_edge != 0:
            g['edge_score'] += league_edge
            g['edge_reasons'].append(f"league_{league}_{league_edge:+d}")
        
        # Fear/Greed: extreme fear (<20) = conservative market = favorites dominate
        # This is the one macro signal with real sports correlation
        if fg < 20:
            g['edge_score'] += 10
            g['edge_reasons'].append(f"extreme_fear_{fg}")
        elif fg > 75:
            g['edge_score'] -= 5
            g['edge_reasons'].append(f"greed_{fg}")
        
        g['fear_greed'] = fg
    
    # Re-sort with updated scores
    games.sort(key=lambda x: (-x.get('edge_score', 0), x['exp']))
    
    return games

# ═══ STEP 2: SIZE — Calculate bet sizes ═══

def size_bets(games, bankroll, per_trade_cap=None):
    """Size each bet through dynamic_per_trade_cap (same throttle as allocator).

    Edge ranks and can skip (< -10); it does not unlock 10% off a retracted WR.
    Ceiling = min(SPORTS category cap, fund 4%/trade). A hot other-category
    book cannot lift sports. Unproven cap is already ~4%.
    """
    try:
        if per_trade_cap is not None:
            cap = float(per_trade_cap)
        else:
            cap = float(dynamic_per_trade_cap(category=CATEGORY))
    except Exception:
        cap = FUND_PER_TRADE_MAX
    cap = max(0.0, min(cap, FUND_PER_TRADE_MAX))
    bets = []
    for g in games[:MAX_GAMES]:
        edge = g.get('edge_score', 0)
        if edge < -10:
            continue
        # Conviction as a fraction of the live cap — never a 10% WR printer.
        if edge >= 25:
            frac = 1.00
        elif edge >= 10:
            frac = 0.75
        elif edge >= 0:
            frac = 0.60
        else:
            frac = 0.50
        pct = min(frac * cap, cap)
        bet_size = bankroll * pct
        n_contracts = max(1, int(bet_size / g['fav_price']))
        cost = n_contracts * g['fav_price']
        fee = ftl.kalshi_fee(g['fav_price'], n_contracts)  # canonical Kalshi fee (single source)
        profit_if_win = n_contracts * (1.0 - g['fav_price']) - fee  # fee-true net

        bets.append({
            **g,
            'contracts': n_contracts,
            'cost': round(cost, 2),
            'fee': round(fee, 2),
            'profit_if_win': round(profit_if_win, 2),
            'roi_pct': round(profit_if_win / cost * 100, 1) if cost else 0.0,
            'bet_pct': round(pct * 100, 1),
        })
    return bets

# ═══ STEP 3: EXECUTE — Place orders ═══

def execute_bets(bets, dry_run=False):
    """Place limit orders for each bet. Skip if already holding."""
    from kalshi_auth import get_positions
    
    # Get existing positions to avoid duplicates
    pos = get_positions()
    held = {p.get('ticker','') for p in pos.get('market_positions',[])
            if abs(float(p.get('position_fp','0'))) > 0}
    
    results = []
    for b in bets:
        r = {**b, 'ts': datetime.now(timezone.utc).isoformat()}
        
        if b['fav_ticker'] in held:
            r['status'] = 'SKIP_HELD'
            results.append(r)
            continue
        
        if dry_run:
            r['status'] = 'DRY_RUN'
            results.append(r)
            continue
        
        try:
            price_cents = int(b['fav_price'] * 100)
            resp = create_order(b['fav_ticker'], 'yes', b['contracts'], price_cents)
            fill = ftl.extract_kalshi_fill(resp)
            r['order_id'] = fill.get('order_id')
            r['status'] = 'EXECUTED' if fill['complete'] else 'UNRECONCILED'
            try:
                ftl.book_kalshi_fill(
                    CATEGORY, b['fav_ticker'], 'yes', resp,
                    intended_source="live",
                    fallback_price=b['fav_price'],
                    fallback_contracts=b['contracts'])
            except Exception as e:
                print(f"[sports_engine] ftl.book_kalshi_fill failed: {e}", file=sys.stderr)
                if r['status'] == 'EXECUTED':
                    r['status'] = 'UNRECONCILED'  # never silent-live if bookkeeping failed
        except Exception as e:
            r['status'] = 'FAILED'
            r['error'] = str(e)[:80]
        results.append(r)
    return results

# ═══ STEP 4: TRACK — Log everything ═══

def load_log():
    if os.path.exists(SPORTS_LOG):
        try: return json.load(open(SPORTS_LOG))
        except: pass
    return {"bets": [], "stats": {}}

def save_log(log):
    with open(SPORTS_LOG, 'w') as f:
        json.dump(log, f, indent=2)

def check_settlements():
    """Check all pending sports bets for settlements. Update win/loss."""
    log = load_log()
    new_settlements = []
    
    for bet in log["bets"]:
        if bet.get("settled"): continue
        if bet.get("status") == "DRY_RUN": continue  # never settle a dry-run as real
        ticker = bet.get("fav_ticker", "")
        if not ticker: continue
        
        try:
            m = get(f"/markets/{ticker}")
            mkt = m.get("market", m)
            status = mkt.get("status", "")
            result = mkt.get("result", "")
            
            if status == "finalized":
                won = (result == "yes")
                bet["settled"] = True
                bet["result"] = result
                bet["won"] = won
                bet["pnl"] = bet["profit_if_win"] if won else -(bet["cost"] + bet.get("fee", 0.0))
                new_settlements.append(bet)
        except: pass
    
    # Recompute stats
    settled = [b for b in log["bets"] if b.get("settled")]
    wins = sum(1 for b in settled if b.get("won"))
    losses = len(settled) - wins
    total_won = sum(b["pnl"] for b in settled if b.get("won"))
    total_lost = sum(abs(b["pnl"]) for b in settled if not b.get("won"))
    
    log["stats"] = {
        "total_bets": len(settled),
        "wins": wins, "losses": losses,
        "win_rate": round(wins / len(settled) * 100, 1) if settled else 0,
        "total_won": round(total_won, 2),
        "total_lost": round(total_lost, 2),
        "net_pnl": round(total_won - total_lost, 2),
        "updated": datetime.now(timezone.utc).isoformat(),
    }
    save_log(log)
    return new_settlements, log["stats"]

# ═══ STEP 5: LEARN — Adjust based on real results ═══

def get_optimal_bet_pct():
    """Display/report size = live throttle. Not the retracted sports-log WR.

    sports_engine_log is not the admissible ledger (SDA HOLDS). Size_bets uses
    dynamic_per_trade_cap directly; this stays in lockstep for the report line.
    """
    try:
        return min(float(dynamic_per_trade_cap(category=CATEGORY)), FUND_PER_TRADE_MAX)
    except Exception:
        return FUND_PER_TRADE_MAX

# ═══ MAIN: Full cycle ═══

def run(dry_run=False):
    """Full sports engine cycle: scan → filter → size → execute → track."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    try:
        stamp = version_stamp()
    except Exception:
        stamp = ""
    banner = f"🏀 SPORTS ENGINE v1.0 ({ts}) {'[DRY]' if dry_run else '[LIVE]'}"
    if stamp:
        banner = f"{banner} {stamp}"
    lines = [banner, ""]
    
    # Check settlements first
    new_settles, stats = check_settlements()
    if new_settles:
        lines.append(f"SETTLEMENTS ({len(new_settles)}):")
        for s in new_settles:
            icon = "✅" if s["won"] else "❌"
            lines.append(f"  {icon} {s['fav_title'][:35]} ${s['pnl']:+.2f}")
    if stats.get("total_bets", 0) > 0:
        lines.append(f"RECORD: {stats['wins']}W/{stats['losses']}L ({stats['win_rate']}%) net:${stats['net_pnl']:+.2f}")
        lines.append("")
    
    # Get bankroll
    bal = get_balance()
    bankroll = bal.get("balance", 0) / 100
    lines.append(f"BANKROLL: ${bankroll:.2f}")
    
    if bankroll < 1:
        lines.append("⚠ No cash to deploy.")
        report = "\n".join(lines)
        print(report)
        return report
    
    # Show intelligence context
    fg = get_fear_greed()
    lines.append(f"INTEL: Fear/Greed={fg} | Edge: spread + price + injuries + F&G (WR not a size input)")
    
    # Adaptive sizing from real results
    optimal_pct = get_optimal_bet_pct()
    
    # Scan
    games = scan_games()
    lines.append(f"GAMES FOUND: {len(games)} in 70-85¢ sweet spot")
    
    if not games:
        lines.append("No games in sweet spot right now.")
        report = "\n".join(lines)
        print(report)
        return report
    
    # Size
    bets = size_bets(games, bankroll)
    total_cost = sum(b['cost'] for b in bets)
    total_potential = sum(b['profit_if_win'] for b in bets)
    
    # Show what we're betting
    for b in bets:
        reasons = ",".join(b.get('edge_reasons', [])[:3])
        lines.append(f"  {b['league']:4} @${b['fav_price']:.2f} x{b['contracts']} "
                     f"${b['cost']:.2f}→+${b['profit_if_win']:.2f} ({b['roi_pct']}%) "
                     f"edge:{b.get('edge_score',0):+d} [{reasons}] "
                     f"| {b['fav_title'][:30]}")
    
    lines.append(f"\nTOTAL: ${total_cost:.2f} deployed, ${total_potential:.2f} potential profit")
    lines.append(f"BET SIZE: {optimal_pct*100:.1f}% of bankroll (dynamic cap, fund 4% max)")
    
    # Loss-based category gate (real fee-inclusive ledger; fail-open, never permanent).
    try:
        gate_ok, gate_why = ftl.should_trade_category(CATEGORY)
    except Exception:
        gate_ok, gate_why = True, ""
    if not gate_ok:
        lines.append(f"\n⛔ CATEGORY GATE: {CATEGORY} blocked — {gate_why}")
        report = "\n".join(lines)
        print(report)
        return report

    # Execute
    results = execute_bets(bets, dry_run=dry_run)
    executed = [r for r in results if r['status'] == 'EXECUTED']
    skipped = [r for r in results if r['status'] != 'EXECUTED']
    
    lines.append(f"\nEXECUTED: {len(executed)} | SKIPPED: {len(skipped)}")
    for r in results:
        icon = ("✅" if r['status'] == 'EXECUTED'
                     else "⏳" if r['status'] == 'UNRECONCILED'
                     else "🔶" if r['status'] == 'DRY_RUN'
                     else "⏭" if 'SKIP' in r['status'] else "❌")
        lines.append(f"  {icon} {r['status']:12} {r['fav_title'][:30]} ${r['cost']:.2f}")
        if r.get('error'): lines.append(f"    ERR: {r['error']}")
    
    # Log bets
    log = load_log()
    for r in results:
        if r['status'] == 'EXECUTED':  # never persist a DRY_RUN as a real bet
            log["bets"].append({
                "fav_ticker": r["fav_ticker"], "fav_title": r["fav_title"],
                "fav_price": r["fav_price"], "contracts": r["contracts"],
                "cost": r["cost"], "fee": r.get("fee", 0.0),
                "profit_if_win": r["profit_if_win"],
                "league": r["league"], "exp": r["exp"],
                "ts": r["ts"], "status": r["status"],
                "settled": False,
            })
    save_log(log)
    
    # Signal: money invested (only for real executions)
    if executed and not dry_run:
        total_cost = sum(r['cost'] for r in executed)
        from signal_send import send
        send(f"Invested: {total_cost:.2f} on {len(executed)} games")
    
    report = "\n".join(lines)
    print(report)
    return report


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Valedia Sports Engine v1.0")
    p.add_argument("--mode", choices=["scan","dry-run","execute","settle"], default="dry-run")
    args = p.parse_args()
    
    if args.mode == "scan":
        games = scan_games()
        for g in games:
            print(f"  {g['league']:4} @${g['fav_price']:.2f} spread:${g['spread']:.3f} exp:{g['exp']} | {g['fav_title']}")
        print(f"\n{len(games)} games in sweet spot")
    elif args.mode == "settle":
        new, stats = check_settlements()
        print(f"Settled: {len(new)} new | {json.dumps(stats, indent=2)}")
    elif args.mode == "dry-run":
        run(dry_run=True)
    elif args.mode == "execute":
        run(dry_run=False)
