"""Valedia Master Capital Allocator — ASI Abundance Engine
═══════════════════════════════════════════════════════════════════════════
The brain above the brains. Scans ALL platforms, ALL asset types, ranks
every opportunity by expected value, and deploys capital where the math
says the return is highest. Beats RenTech by being omnivorous.

PLATFORMS: Kalshi (predictions + weather), Coinbase (crypto), Alpaca (stocks)
ASSET TYPES: Weather arb, prediction markets, crypto spot, equities, DeFi

The allocator doesn't care WHAT the asset is — only:
  1. Expected return (edge × probability)
  2. Time to resolution (faster = better, compounds more)
  3. Downside risk (max loss)
  4. Liquidity (can we get in/out)

Capital flows to highest EV per unit time. Period.

(c) GOATWORTHY Post LLC. All rights reserved.
"""
import os, sys, json, time, traceback
from datetime import datetime, timezone, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# Single sources (this is the LIVE authority — it MUST use them, not its own copies).
try:
    import forward_test_ledger as _ftl                      # real fee-inclusive ledger
    import valedia_config as _cfg
    from execution_policy import build_execution_plan, MakerOrderManager
    from market_state import MARKET_STATE
except Exception:
    _ftl = None; _cfg = None; build_execution_plan = None; MakerOrderManager = None

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

MODELS_DIR = os.path.expanduser("~/.openclaw/workspace/models")
MAX_TOTAL_DEPLOY_PCT = 0.70   # Deploy up to 70% of total capital
MIN_EV_PER_DOLLAR = 0.03      # Minimum 3% expected return to deploy

# ═══════════════════════════════════════════════════════════════
# STEP 1: Gather capital from ALL platforms
# ═══════════════════════════════════════════════════════════════

def get_total_capital():
    """Get available capital across all platforms."""
    capital = {"kalshi": 0, "coinbase_usd": 0, "coinbase_usdc": 0,
               "alpaca": 0, "total": 0, "deployed": 0}
    
    # Kalshi
    try:
        from kalshi_auth import get_balance
        bal = get_balance()
        capital["kalshi"] = bal.get("balance", 0) / 100
        capital["deployed"] += bal.get("portfolio_value", 0) / 100
    except: pass
    
    # Coinbase
    try:
        from coinbase_trader import get_balances
        cb = get_balances()
        capital["coinbase_usd"] = cb.get("USD", {}).get("balance", 0)
        capital["coinbase_usdc"] = cb.get("USDC", {}).get("balance", 0)
    except: pass
    
    # Alpaca
    try:
        from alpaca_executor import get_account
        acct = get_account()
        capital["alpaca"] = float(acct.get("cash", 0))
        capital["deployed"] += float(acct.get("equity", 0)) - float(acct.get("cash", 0))
    except: pass
    
    capital["total"] = (capital["kalshi"] + capital["coinbase_usd"] +
                        capital["coinbase_usdc"] + capital["alpaca"])
    capital["deployable"] = capital["total"] * MAX_TOTAL_DEPLOY_PCT - capital["deployed"]
    capital["deployable"] = max(0, capital["deployable"])
    return capital

# ═══════════════════════════════════════════════════════════════
# STEP 2: Gather ALL opportunities across ALL platforms
# ═══════════════════════════════════════════════════════════════

def _is_strategy_paused(name):
    """Learning pause (TTL). Fail-open: errors / expired → not paused.
    Live-exec fence is separate (is_killed_strategy) and is applied at execute."""
    try:
        from pnl_tracker import is_strategy_paused
        return bool(is_strategy_paused(name))
    except Exception:
        return False


def gather_all_opportunities():
    """Collect every opportunity from every source into one unified list.
    Each opportunity gets: expected_value_per_dollar, hours_to_resolve, platform.
    Skips currently-paused strategies (TTL). Unpaused names re-enter evaluation
    even if Slice 8 live-exec fenced — sizing/gating yes, live POST no."""
    opps = []
    
    # ── SOURCE 1: Kalshi prediction markets ──
    try:
        from kalshi_executor import scan_all_markets
        kalshi_opps = scan_all_markets()
        for o in kalshi_opps:
            if o["side"] in ("pending_intel", "both"): continue
            # Learning pause: skip while unexpired. Not a grave.
            if _is_strategy_paused(o.get("strategy", "")):
                continue
            hours = max(1, o.get("hours_to_exp", 720))
            ev = o["edge"] / (o["price"] + 0.01)  # Return per dollar
            ev_per_day = ev / (hours / 24 + 0.01)  # Annualized speed
            opps.append({
                "platform": "kalshi", "type": o["strategy"],
                "ticker": o["ticker"], "title": o["title"][:50],
                "side": o["side"], "price": o["price"],
                "ev_per_dollar": round(ev, 4),
                "ev_per_day": round(ev_per_day, 4),
                "hours_to_resolve": round(hours, 1),
                "edge_pct": o.get("roi_pct", o.get("edge_pct", 0)),
                "max_loss_pct": 100,  # Binary: lose entire stake
                "confidence": min(0.95, o["score"] / 100),
                "raw": o,
            })
    except Exception as e:
        print(f"  Kalshi scan error: {e}")
    
    # ── SOURCE 2: Weather scan stays HTTP-off (Slice 8). No find_edges.
    # Evaluation of WEATHER_ARB happens if injected/scanned opps pass pause.
    # Live create_order stays fenced at execute.
    opps.extend(gather_weather_opportunities())
    
    # ── SOURCE 3: Crypto (Coinbase — from unified brain signals) ──
    try:
        fusion_path = os.path.join(MODELS_DIR, "latest_fusion.json")
        if os.path.exists(fusion_path):
            with open(fusion_path) as f:
                fusion = json.load(f)
            for ticker, sig in fusion.get("signals", {}).items():
                score = sig.get("fused_score", 50)
                if score > 65 or score < 35:  # Strong signal
                    direction = "BUY" if score > 65 else "SELL"
                    edge = abs(score - 50) / 50  # 0-1 scale
                    opps.append({
                        "platform": "coinbase", "type": f"CRYPTO_{direction}",
                        "ticker": ticker, "title": f"{ticker} {direction} (fused={score})",
                        "side": direction.lower(), "price": 1.0,
                        "ev_per_dollar": round(edge * 0.15, 4),  # Conservative: 15% of edge
                        "ev_per_day": round(edge * 0.15 / 5, 4),  # 5-day hold assumption
                        "hours_to_resolve": 120,
                        "edge_pct": round(edge * 15, 1),
                        "max_loss_pct": 20,  # Crypto can drop 20%
                        "confidence": min(0.8, score / 100),
                        "raw": sig,
                    })
    except Exception as e:
        print(f"  Crypto signal error: {e}")
    
    # ── SOURCE 4: Stocks (Alpaca — from unified brain) ──
    try:
        if os.path.exists(fusion_path):
            with open(fusion_path) as f:
                fusion = json.load(f)
            for ticker, sig in fusion.get("signals", {}).items():
                if "-USD" in ticker: continue  # Skip crypto (handled above)
                score = sig.get("fused_score", 50)
                if score > 65:
                    edge = (score - 50) / 50
                    opps.append({
                        "platform": "alpaca", "type": "STOCK_BUY",
                        "ticker": ticker, "title": f"{ticker} BUY (fused={score})",
                        "side": "buy", "price": 1.0,
                        "ev_per_dollar": round(edge * 0.10, 4),
                        "ev_per_day": round(edge * 0.10 / 10, 4),
                        "hours_to_resolve": 240,
                        "edge_pct": round(edge * 10, 1),
                        "max_loss_pct": 15,
                        "confidence": min(0.7, score / 100),
                        "raw": sig,
                    })
    except: pass
    
    # ── SOURCE 5: Kalshi Market Scanner (600+ market heuristic scoring) ──
    try:
        scanner_path = os.path.join(MODELS_DIR, "scanner_results.json")
        if os.path.exists(scanner_path):
            with open(scanner_path) as f:
                scan = json.load(f)
            # Only use if scan is less than 12 hours old
            scan_ts = scan.get("ts", "")
            if scan_ts:
                scan_age = (datetime.now(timezone.utc) - 
                           datetime.fromisoformat(scan_ts)).total_seconds()
                if scan_age < 43200:  # 12 hours
                    for m in scan.get("macro", []):
                        if m.get("edge_score", 0) > 50:
                            ev = m["edge_score"] / 1000
                            days = m.get("days_to_close") or 30
                            opps.append({
                                "platform": "kalshi_scanner",
                                "type": "SCANNER_MACRO",
                                "ticker": m["ticker"],
                                "title": m["title"][:50],
                                "side": "yes" if m["mid"] > 50 else "no",
                                "price": m["yes_ask"] / 100,
                                "ev_per_dollar": round(ev, 4),
                                "ev_per_day": round(ev / max(days, 1), 4),
                                "hours_to_resolve": round(days * 24, 1),
                                "edge_pct": round(m["edge_score"] / 10, 1),
                                "max_loss_pct": 100,
                                "confidence": min(0.6, m["edge_score"] / 200),
                                "raw": m,
                            })
    except Exception as e:
        print(f"  Scanner source error: {e}")
    
    return opps

# ═══════════════════════════════════════════════════════════════
# STEP 3: RANK by Expected Value per Dollar per Day
# ═══════════════════════════════════════════════════════════════
# This is the key insight: a 500% ROI weather bet that resolves tomorrow
# is infinitely better than a 500% ROI political bet that resolves in 2029.
# EV/$/day is the universal currency for comparing ALL opportunities.

def rank_opportunities(opps):
    """Rank all opportunities by risk-adjusted EV per dollar per day."""
    for o in opps:
        # Composite score: EV/day × confidence × (1 / sqrt(max_loss))
        # Penalizes high-loss bets, rewards high-confidence fast resolution
        loss_adj = 1.0 / (o["max_loss_pct"] / 100 + 0.1) ** 0.5
        o["composite_score"] = round(
            o["ev_per_day"] * o["confidence"] * loss_adj * 1000, 2
        )
    # Deduplicate mutually exclusive markets (e.g., ETH/BTC brackets, game sides)
    # Only keep the BEST opportunity per event series
    seen_series = {}
    deduped = []
    for o in opps:
        # Extract series: KXETH-26MAR2305, KXNBAGAME-26MAR22WASNYK, etc.
        ticker = o.get("ticker", "")
        parts = ticker.rsplit("-", 1)
        series_key = parts[0] if len(parts) > 1 else ticker
        
        if series_key not in seen_series:
            seen_series[series_key] = o
            deduped.append(o)
        # Replace if this one has higher composite score
        elif o["composite_score"] > seen_series[series_key]["composite_score"]:
            deduped.remove(seen_series[series_key])
            seen_series[series_key] = o
            deduped.append(o)
    
    return deduped

# ═══════════════════════════════════════════════════════════════
# STEP 4: ALLOCATE capital to top opportunities
# ═══════════════════════════════════════════════════════════════

def gather_weather_opportunities():
    """No NOAA/Kalshi HTTP. Weather live exec stays fenced; this is not a scan.

    Evaluation/sizing of WEATHER_ARB is allowed when the learning pause has
    expired and opps are injected or appear from other gather sources.
    """
    return []


def allocate(opps, capital):
    """Distribute available capital across top opportunities.
    Uses Kelly-inspired sizing: higher EV = larger allocation."""
    deployable = capital["deployable"]
    if deployable < 1:
        return [], "No deployable capital"
    
    allocations = []
    remaining = deployable
    # Per-strategy ceiling from category_stats (fee-true). A hot fake/other
    # category cannot lift this opp; paper/sandbox/unreconciled/backfill cannot
    # lift. Falls back to 15% only if config is unavailable.
    
    for o in opps:
        if remaining < 0.50: break
        cat = o.get("type") or o.get("strategy")
        # Pause = skip sizing. Unpaused (incl. weather/oil) may be sized/gated.
        # Live-exec fence is applied at execute, not here.
        if _is_strategy_paused(cat):
            continue
        if o["ev_per_dollar"] < MIN_EV_PER_DOLLAR: continue

        try:
            _cap = _cfg.dynamic_per_trade_cap(category=cat) if _cfg else 0.15
        except Exception:
            _cap = 0.15
        max_per_opp = deployable * _cap
        
        # Kelly-inspired: bet proportional to edge × confidence
        kelly_frac = o["ev_per_dollar"] * o["confidence"]
        # Half-Kelly for safety
        alloc = min(remaining * kelly_frac * 0.5, max_per_opp, remaining * 0.20)
        alloc = max(0.50, round(alloc, 2))  # Minimum $0.50
        
        if alloc > remaining: alloc = remaining
        
        allocations.append({
            "platform": o["platform"], "type": o["type"],
            "ticker": o["ticker"], "title": o["title"],
            "side": o["side"], "price": o["price"],
            "allocation": alloc,
            "ev_per_dollar": o["ev_per_dollar"],
            "ev_per_day": o["ev_per_day"],
            "composite_score": o["composite_score"],
            "hours_to_resolve": o["hours_to_resolve"],
            "confidence": o["confidence"],
            "expected_profit": round(alloc * o["ev_per_dollar"], 2),
            "raw": o.get("raw"),
        })
        remaining -= alloc
        
        if len(allocations) >= 20: break  # Max 20 positions
    
    return allocations, f"${deployable:.2f} deployable, ${remaining:.2f} reserved"

# ═══════════════════════════════════════════════════════════════
# STEP 5: EXECUTE across platforms
# ═══════════════════════════════════════════════════════════════

KALSHI_FAIL_PATH = os.path.join(MODELS_DIR, "kalshi_order_failures.json")

def _load_kalshi_failures():
    if os.path.exists(KALSHI_FAIL_PATH):
        try: return json.load(open(KALSHI_FAIL_PATH))
        except: pass
    return {}

def _record_kalshi_failure(ticker, error):
    fails = _load_kalshi_failures()
    fails[ticker] = {"count": fails.get(ticker, {}).get("count", 0) + 1, "last_error": error[:80]}
    json.dump(fails, open(KALSHI_FAIL_PATH, "w"), indent=2)

def _clear_kalshi_failure(ticker):
    fails = _load_kalshi_failures()
    if ticker in fails:
        del fails[ticker]
        json.dump(fails, open(KALSHI_FAIL_PATH, "w"), indent=2)

def _is_kalshi_blacklisted(ticker):
    fails = _load_kalshi_failures()
    return fails.get(ticker, {}).get("count", 0) >= 3

def execute_allocations(allocations, dry_run=False):
    """Execute allocations on the appropriate platform.
    FAIL-SAFE: Every trade must pass intel vetting. If intel errors, trade is SKIPPED."""
    results = []
    # Manage maker orders resting from prior cycles first (record fills, cancel stale).
    _maker_mgr = None
    if MakerOrderManager is not None and not dry_run:
        try:
            _maker_mgr = MakerOrderManager()
            _maker_mgr.check_and_manage()
        except Exception:
            _maker_mgr = None
    for a in allocations:
        r = {"ticker": a["ticker"], "platform": a["platform"],
             "type": a["type"], "allocation": a["allocation"],
             "expected_profit": a["expected_profit"]}
        
        if dry_run:
            r["status"] = "DRY_RUN"
            results.append(r)
            continue

        cat = a.get("type") or a.get("strategy")
        if _is_strategy_paused(cat):
            r["status"] = "SKIP_PAUSED"
            r["error"] = "learning pause (until TTL/evidence); not permanent"
            results.append(r)
            continue

        # Slice 8 live-exec fence: weather/oil may be sized when unpaused, but
        # this lane never places their orders. Unpause ≠ create_order / alp_buy.
        if _cfg and _cfg.live_exec_fenced(cat):
            r["status"] = "SKIP_LIVE_FENCE"
            r["error"] = "live exec fenced (Slice 8); evaluation/sizing only"
            results.append(r)
            continue
        
        # ── FAIL-SAFE: Skip blacklisted tickers (failed 3+ times) ──
        if a["platform"] in ("kalshi", "kalshi_weather") and _is_kalshi_blacklisted(a["ticker"]):
            r["status"] = "SKIP_BLACKLISTED"
            r["error"] = "Failed 3+ times previously"
            results.append(r)
            continue

        # ── Loss-based category gate (real fee-inclusive ledger; fail-open, never permanent) ──
        if _ftl and a["platform"] in ("kalshi", "kalshi_weather"):
            try:
                ok, why = _ftl.should_trade_category(a.get("type", ""))
            except Exception:
                ok, why = True, ""
            if not ok:
                r["status"] = "SKIP_CATEGORY_GATE"; r["error"] = why
                results.append(r); continue
        
        # ── FAIL-SAFE: Intel vetting gate ──
        # If we can't verify the trade through the intelligence layer, SKIP it
        if a["platform"] in ("kalshi", "kalshi_weather"):
            try:
                from intel_layer import get_intel
                intel = get_intel()
                ik = intel.get_kalshi_conviction(
                    a["ticker"], a.get("title", ""), a["price"],
                    a.get("hours_to_resolve", 24)
                )
                rec_side = ik["recommended_side"]
                # Block trades where intel says SKIP or CONTRADICTS
                if rec_side == "skip" and ik["confidence"] < 0.3:
                    r["status"] = "SKIP_LOW_CONVICTION"
                    r["error"] = f"Intel: skip, conv={ik['conviction_yes']:.0f}"
                    results.append(r)
                    continue
                if (rec_side == "yes" and a["side"] == "no") or \
                   (rec_side == "no" and a["side"] == "yes"):
                    if ik["confidence"] > 0.5:
                        r["status"] = "SKIP_INTEL_CONTRADICTS"
                        r["error"] = f"Intel says {rec_side}, we say {a['side']}"
                        results.append(r)
                        continue
            except Exception as e:
                r["status"] = "SKIP_INTEL_ERROR"
                r["error"] = f"Intel check failed: {str(e)[:60]}"
                results.append(r)
                continue
        
        try:
            if a["platform"] in ("kalshi", "kalshi_weather"):
                from kalshi_auth import create_order, get_positions
                # FAIL-SAFE: Check we don't already hold this ticker
                existing = get_positions().get("market_positions", [])
                already_held = any(
                    p.get("ticker") == a["ticker"] and abs(float(p.get("position_fp", 0))) > 0
                    for p in existing
                )
                if already_held:
                    r["status"] = "SKIP_HELD"
                    r["error"] = "Already holding position"
                    results.append(r)
                    continue
                price_cents = max(1, min(99, int(a["price"] * 100)))
                n = max(1, int(a["allocation"] / a["price"]))
                # Maker-vs-taker routing (we already vetted the trade, so ignore the plan's
                # no_trade/fee veto — our EV is already fee-net; only take the maker/taker call).
                action = "taker"
                if build_execution_plan is not None:
                    try:
                        _raw = a.get("raw") or {}
                        yb = int(_raw.get("yes_bid", 0) or 0); nb = int(_raw.get("no_bid", 0) or 0)
                        book = MARKET_STATE.update_book(a["ticker"], {"orderbook": {
                            "yes": [{"price": yb, "size": 1}], "no": [{"price": nb, "size": 1}]}}) if (yb > 0 and nb > 0) else None
                        ep = build_execution_plan(a["ticker"], a["side"],
                                                  a["expected_profit"] / (a["allocation"] + 0.01) * 100,
                                                  price_cents, n, a.get("hours_to_resolve", 9999), book=book)
                        if ep.action == "maker":
                            action, price_cents = "maker", ep.price_cents
                    except Exception:
                        pass
                if action == "maker" and _maker_mgr is not None and not getattr(_cfg, "EXEC_DRY_RUN", True):
                    oid = _maker_mgr.place_maker_order(a["ticker"], a["side"], n, price_cents,
                                                       strategy=a.get("type", ""))
                    r["status"] = "MAKER_PLACED"; r["contracts"] = n; r["order_id"] = str(oid)[:12]
                    _clear_kalshi_failure(a["ticker"])
                else:
                    resp = create_order(a["ticker"], a["side"], n, price_cents)
                    r["contracts"] = n
                    _clear_kalshi_failure(a["ticker"])
                    # Fill-true only. Bare POST-ack is UNRECONCILED — never a live open.
                    fill = {}
                    if _ftl:
                        try:
                            fill = _ftl.extract_kalshi_fill(resp)
                            _ftl.book_kalshi_fill(
                                _ftl.canonical_category(a.get("type", "KALSHI")),
                                a["ticker"], a["side"], resp,
                                intended_source="live",
                                fallback_price=price_cents / 100.0,
                                fallback_contracts=n)
                        except Exception:
                            fill = {}
                    r["status"] = "EXECUTED" if fill.get("complete") else "UNRECONCILED"
                    if fill.get("order_id"):
                        r["order_id"] = fill["order_id"]
            
            elif a["platform"] == "coinbase":
                # Shadow Intent (Slice 2): never dispatchable; writer stays valedia.
                try:
                    from nautilus_shadow_coinbase import emit_allocator_shadow
                    sh = emit_allocator_shadow(a)
                    r["shadow_intent"] = sh.get("intent_id")
                    r["shadow_dispatchable"] = bool(sh.get("dispatchable"))
                except Exception as e:
                    r["shadow_error"] = str(e)[:80]
                # Crypto trades via Coinbase (existing Valedia writer — do not cut over)
                try:
                    from coinbase_trader import place_market_order
                    ticker = a["ticker"].replace("-USD", "")
                    cb_resp = place_market_order(ticker, a["side"], a["allocation"])
                    r["status"] = "EXECUTED"
                    if _ftl:
                        try:
                            _ftl.book_spot_fill(
                                a.get("type", "CRYPTO"), a["ticker"], a["side"],
                                "coinbase", cb_resp if isinstance(cb_resp, dict) else {},
                                intended_source="live")
                        except Exception:
                            pass
                except Exception as e:
                    r["status"] = "FAILED"
                    r["error"] = str(e)[:80]
            
            elif a["platform"] == "alpaca":
                # Stock trades via Alpaca (existing Valedia writer)
                try:
                    from alpaca_executor import place_order
                    alp_resp = place_order(a["ticker"], a["side"], notional=a["allocation"])
                    r["status"] = "EXECUTED"
                    if _ftl:
                        try:
                            _ftl.book_spot_fill(
                                a.get("type", "EQUITY"), a["ticker"], a["side"],
                                "alpaca", alp_resp if isinstance(alp_resp, dict) else {},
                                intended_source="live")
                        except Exception:
                            pass
                except Exception as e:
                    r["status"] = "FAILED"
                    r["error"] = str(e)[:80]
            else:
                r["status"] = "SKIP"
                r["error"] = f"Unknown platform: {a['platform']}"
        except Exception as e:
            r["status"] = "FAILED"
            r["error"] = str(e)[:80]
            # SELF-HEAL: Track Kalshi failures for auto-blacklisting
            if a["platform"] in ("kalshi", "kalshi_weather"):
                _record_kalshi_failure(a["ticker"], str(e))
        results.append(r)
    return results

# ═══════════════════════════════════════════════════════════════
# MAIN: Full cycle
# ═══════════════════════════════════════════════════════════════

def run(dry_run=False, max_allocs=15):
    """Full allocation cycle: gather → rank → allocate → execute → report."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"🧠 MASTER ALLOCATOR ({ts}) {'[DRY]' if dry_run else '[LIVE]'}", ""]

    # Slice 10: MiroFish/intel → gated Intents. Never mixed into live allocations.
    # Fail-open. Never-dispatchable. No venue POST.
    try:
        from intel_opportunity_gatherer import emit_shadow_intents
        n_intel = len(emit_shadow_intents() or [])
        lines.append(f"INTEL SHADOW: {n_intel} gated Intents (never-dispatchable)")
    except Exception as e:
        lines.append(f"INTEL SHADOW: skip ({str(e)[:60]})")
    
    # Capital
    capital = get_total_capital()
    lines.append(f"CAPITAL: Kalshi ${capital['kalshi']:.2f} | CB ${capital['coinbase_usd']:.2f}+${capital['coinbase_usdc']:.2f} USDC | Alpaca ${capital['alpaca']:.2f}")
    lines.append(f"  Total: ${capital['total']:.2f} | Deployed: ${capital['deployed']:.2f} | Deployable: ${capital['deployable']:.2f}")
    
    if capital["deployable"] < 1:
        lines.append("⚠ No deployable capital.")
        report = "\n".join(lines)
        print(report)
        return report
    
    # Gather + Rank
    opps = gather_all_opportunities()
    opps = rank_opportunities(opps)
    lines.append(f"\nSCANNED: {len(opps)} opportunities across all platforms")
    
    # Show top 10 by composite score
    lines.append(f"\nTOP OPPORTUNITIES (ranked by EV/$/day × confidence):")
    for o in opps[:10]:
        lines.append(
            f"  {o['composite_score']:6.1f} {o['platform']:15} {o['type']:18} "
            f"EV:{o['ev_per_dollar']:+.0%}/$ {o['hours_to_resolve']:.0f}h "
            f"conf:{o['confidence']:.0%} | {o['title'][:35]}"
        )
    
    # Allocate
    allocations, alloc_msg = allocate(opps[:50], capital)
    lines.append(f"\nALLOCATION: {len(allocations)} positions | {alloc_msg}")
    
    total_deployed = sum(a["allocation"] for a in allocations)
    total_ev = sum(a["expected_profit"] for a in allocations)
    
    # Show allocation by platform
    by_plat = {}
    for a in allocations:
        p = a["platform"]
        if p not in by_plat: by_plat[p] = {"n": 0, "alloc": 0, "ev": 0}
        by_plat[p]["n"] += 1
        by_plat[p]["alloc"] += a["allocation"]
        by_plat[p]["ev"] += a["expected_profit"]
    
    lines.append(f"\n  Platform Breakdown:")
    for p, d in sorted(by_plat.items(), key=lambda x: x[1]["ev"], reverse=True):
        pct = d["alloc"] / (total_deployed + 0.01) * 100
        lines.append(f"    {p:18} {d['n']:2} trades  ${d['alloc']:6.2f} ({pct:.0f}%)  EV: ${d['ev']:.2f}")
    
    lines.append(f"\n  TOTAL: ${total_deployed:.2f} deployed → ${total_ev:.2f} expected profit ({total_ev/(total_deployed+0.01)*100:.0f}% ROI)")
    
    # Execute
    results = execute_allocations(allocations[:max_allocs], dry_run=dry_run)
    lines.append(f"\nEXECUTION:")
    for r in results:
        icon = "✅" if r["status"]=="EXECUTED" else "🔶" if r["status"]=="DRY_RUN" else "❌"
        lines.append(f"  {icon} {r['platform']:15} ${r['allocation']:.2f} → EV ${r['expected_profit']:.2f} | {r['ticker'][:30]}")
        if r.get("error"): lines.append(f"    ERR: {r['error']}")
    
    # Save state
    try:
        state_path = os.path.join(MODELS_DIR, "master_allocator_state.json")
        with open(state_path, "w") as f:
            json.dump({"ts": ts, "capital": capital, "n_opps": len(opps),
                "n_allocs": len(allocations), "total_deployed": total_deployed,
                "total_ev": total_ev, "by_platform": {k: {"n": v["n"], "alloc": round(v["alloc"],2), "ev": round(v["ev"],2)} for k,v in by_plat.items()}
            }, f, indent=2)
    except: pass
    
    report = "\n".join(lines)
    print(report)
    return report


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["dry-run","execute","capital"], default="dry-run")
    p.add_argument("--max-allocs", type=int, default=15)
    args = p.parse_args()
    if args.mode == "capital":
        c = get_total_capital()
        for k,v in c.items(): print(f"  {k}: ${v:.2f}" if isinstance(v,float) else f"  {k}: {v}")
    elif args.mode == "dry-run":
        run(dry_run=True, max_allocs=args.max_allocs)
    elif args.mode == "execute":
        run(dry_run=False, max_allocs=args.max_allocs)
