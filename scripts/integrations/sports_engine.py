"""Valedia Sports Engine v1.0 — 70-85¢ favorites only.

Sizing is the same unproven throttle the allocator uses (dynamic_per_trade_cap,
~4%/trade while the fee-true ledger is thin). The old 86%/100% WR was retracted
(W vs WIN bug + no-horizon resolver) — it is not a sizing input.

Architecture:
  1. SCAN — Find all sports games resolving in next 24h
  2. FILTER — Only 70-85¢ favorites
  3. SIZE — min(edge table, dynamic_per_trade_cap); never 10% while unproven
  4. EXECUTE — Limit at the ask, only after the allocator's pre-create chain
     (EV preflight → session_clocks.before_create → informed create_preflight)
     on real evidence (build_sports_opp). Missing evidence → SKIP_*, no order.
     EXECUTED+live only on real fill data
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
    """Swarm mood only when the live file is fresh and not near-neutral."""
    try:
        import mirofish_intel_archive as _mia
        mf = _mia.load_for_fv(os.path.join(MODELS, "mirofish_signal.json"))
        if not mf:
            return 50
        return mf.get("overall_bias", 50)
    except Exception:
        return 50

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
                        'series': series,
                        'fav_market': fav,  # Kalshi market dict: status/open/close/expiry for session_clocks
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
        try:
            import kalshi_fee_map as _kfm
            ok, info = _kfm.allow(g.get("fav_ticker"), series=g.get("series"))
            g["fee_map"] = info
            if not ok:
                continue
        except Exception:
            pass
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

# ═══ STEP 2b: EVIDENCE — what the INFORMED gate sees (never placeholders) ═══
# The only data this engine has is the Kalshi book itself: each game is two
# complementary YES markets (fav, dog). FV = de-vigged two-sided mid of that
# book. That is a MARKET price, so fv_source is "market" (not in
# informed_before_create.ALLOWED_FV): the gate refuses it with "fv_source"
# until a real model/researched FV exists. Attestation is read from a file
# Burton signs; this module never synthesizes one.
SPORTS_EVIDENCE_VERSION = "sports-evidence-1"
FV_METHOD = "kalshi_two_sided_mid_devig"
FV_COHERENCE_TOL = 0.05  # |fav_mid + dog_mid - 1| above this → books disagree → no fv_confidence
SPORTS_ATTESTATION_PATH = os.path.join(MODELS, "sports_attestation.json")
SKIP_LOG_MAX = 500
_MARKET_KEYS = ("status", "open_time", "close_time", "expected_expiration_time",
                "expiration_time", "can_close_early", "early_close_condition",
                "settlement_timer_seconds")


def _parse_ts(v):
    try:
        s = str(v).strip()
        dt = datetime.fromisoformat(s[:-1] + "+00:00" if s.endswith("Z") else s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def load_sports_attestation(path=None, now=None):
    """(attestation dict | None, status). Compliance/geo attestation Burton signs.

    Required shape: {"valid": true, "geo": "pass", "attested_by": str,
    "expires_at": ISO-8601}. Anything else (missing, unreadable, invalid,
    geo != pass, no/expired expiry) → None. Never returns a synthesized True.
    """
    p = path or SPORTS_ATTESTATION_PATH
    try:
        with open(p) as f:
            doc = json.load(f)
    except FileNotFoundError:
        return None, "attestation_missing"
    except Exception:
        return None, "attestation_unreadable"
    if not isinstance(doc, dict):
        return None, "attestation_shape"
    if doc.get("valid") is not True:
        return None, "attestation_invalid"
    if str(doc.get("geo") or "").lower() != "pass":
        return None, "attestation_geo"
    if not str(doc.get("attested_by") or "").strip():
        return None, "attestation_unsigned"
    exp = _parse_ts(doc.get("expires_at"))
    if exp is None:
        return None, "attestation_no_expiry"
    if exp <= (now or datetime.now(timezone.utc)):
        return None, "attestation_expired"
    return dict(doc), "ok"


def _levels(book, side):
    """[(price_dollars, qty)] from a Kalshi orderbook side (dollars or cents form)."""
    for key, scale in ((f"{side}_dollars", 1.0), (side, 0.01)):
        rows = book.get(key)
        if not rows:
            continue
        out = []
        for row in rows:
            try:
                px, qty = float(row[0]) * scale, float(row[1])
            except (TypeError, ValueError, IndexError):
                continue
            if 0 < px < 1 and qty > 0:
                out.append((round(px, 4), qty))
        if out:
            return out
    return []


def parse_orderbook(raw):
    """GET /markets/{t}/orderbook → YES bid/ask/depth. YES ask = 1 − best NO bid."""
    doc = raw if isinstance(raw, dict) else {}
    yes, no = [], []
    for cont in (doc.get("orderbook_fp"), doc.get("orderbook"), doc):
        if isinstance(cont, dict):
            yes, no = _levels(cont, "yes"), _levels(cont, "no")
            if yes or no:
                break
    bid = max(yes)[0] if yes else None
    best_no = max(no) if no else None
    ask = round(1.0 - best_no[0], 4) if best_no else None
    return {"bid": bid, "ask": ask, "depth": best_no[1] if best_no else None,
            "spread": round(ask - bid, 4) if bid is not None and ask is not None else None}


def read_book(ticker):
    """Read-only Kalshi orderbook GET. None when unavailable (fail-closed upstream)."""
    if not ticker:
        return None
    try:
        book = parse_orderbook(get(f"/markets/{ticker}/orderbook"))
    except Exception:
        return None
    return book if book.get("bid") is not None or book.get("ask") is not None else None


def fair_value_from_books(bet, fav_book, dog_book):
    """De-vig of the two complementary YES books: fv = fav_mid / (fav_mid + dog_mid).

    fv_confidence rule: both sides two-sided AND |fav_mid + dog_mid − 1| ≤
    FV_COHERENCE_TOL (the two books agree); otherwise None. fv_source is
    "market" — honest, and NOT admitted by informed_before_create.ALLOWED_FV.
    """
    out = {"fv": None, "fv_source": "market", "fv_confidence": None, "fv_method": FV_METHOD}
    mids = []
    for bk in (fav_book, dog_book):
        b, a = (bk or {}).get("bid"), (bk or {}).get("ask")
        if b is None or a is None or b <= 0 or a <= 0 or a < b:
            out["fv_why"] = "one_sided_book"
            return out
        mids.append((b + a) / 2.0)
    total = mids[0] + mids[1]
    out.update(fav_mid=round(mids[0], 4), dog_mid=round(mids[1], 4), book_sum=round(total, 4),
               fv=round(mids[0] / total, 4))
    if abs(total - 1.0) <= FV_COHERENCE_TOL:
        out["fv_confidence"] = "two_sided_coherent"
    else:
        out["fv_why"] = "books_disagree"
    return out


def build_sports_opp(b, bankroll=None, fv_fn=None, fmap=None, now=None):
    """(opp, evidence) for one sized bet. Missing evidence is left absent, never faked."""
    ticker = b['fav_ticker']
    n = int(b.get('contracts') or 0)
    fav_book, dog_book = read_book(ticker), read_book(b.get('dog_ticker'))
    market = b.get('fav_market') if isinstance(b.get('fav_market'), dict) else None
    if market is None:
        try:
            m = get(f"/markets/{ticker}")
            market = m.get("market", m) if isinstance(m, dict) else None
        except Exception:
            market = None
    fvd = (fv_fn or fair_value_from_books)(b, fav_book, dog_book) or {}
    att, att_status = load_sports_attestation(now=now)
    bid = (fav_book or {}).get("bid")
    ask = (fav_book or {}).get("ask")
    fv = fvd.get("fv")
    edge = round(fv - ask, 4) if fv is not None and ask is not None else None
    fee_per = ftl.kalshi_fee(ask, n) / n if ask is not None and n > 0 else None
    ev = {
        "version": SPORTS_EVIDENCE_VERSION,
        "ticker": ticker, "dog_ticker": b.get('dog_ticker'),
        "fv": fv, "fv_source": fvd.get("fv_source"), "fv_confidence": fvd.get("fv_confidence"),
        "fv_detail": {k: v for k, v in fvd.items() if k not in ("fv", "fv_source", "fv_confidence")},
        "bid": bid, "ask": ask, "depth": (fav_book or {}).get("depth"),
        "spread": (fav_book or {}).get("spread"), "dog_book": dog_book,
        "book_source": "GET /markets/{ticker}/orderbook" if fav_book else None,
        "slip": 0.0 if ask is not None else None, "slip_basis": "limit_at_best_ask",
        "edge": edge, "fee_per": fee_per,
        "edge_fee_true": round(edge - fee_per, 4) if edge is not None and fee_per is not None else None,
        "bankroll": bankroll if bankroll and bankroll > 0 else None,
        "market": {k: market.get(k) for k in _MARKET_KEYS if k in market} if market else None,
        "sports_attestation": att, "attestation_status": att_status,
        "order_price_cents": int(round(float(b['fav_price']) * 100)), "contracts": n,
    }
    opp = {
        "platform": "kalshi", "type": CATEGORY, "strategy": CATEGORY, "category": CATEGORY,
        "desk": "kalshi_sports", "ticker": ticker, "side": "yes",
        "series": b.get('series') or None, "price": ev["order_price_cents"] / 100.0,
        "market": market, "fv": fv, "fv_source": fvd.get("fv_source"),
        "fv_confidence": fvd.get("fv_confidence"), "edge": edge, "slip": ev["slip"],
        "bid": bid, "ask": ask, "yes_bid": bid, "yes_ask": ask, "depth": ev["depth"],
        "bankroll": ev["bankroll"], "sports_attestation": att, "fmap": fmap,
    }
    return {k: v for k, v in opp.items() if v is not None}, ev


def _skip(r, status, reasons):
    r['status'] = status
    r['reasons'] = list(reasons)
    r['error'] = ",".join(str(x) for x in reasons)[:160]


def _pre_create(opp, n, price_cents, r):
    """Allocator pre-create discipline (master_allocator.execute_allocations):
    EV preflight → session_clocks.before_create → informed create_preflight.
    Returns the preflight dict on admit, else None with r marked SKIP_*."""
    try:
        import kalshi_profit as _kp
        proceed, opp = _kp.annotate(opp, contracts=n)
        r['ev_preflight'] = opp.get("ev_preflight")
        if not proceed:
            _skip(r, 'SKIP_EV_PREFLIGHT', ["ev_preflight"])
            return None
        mf = opp.get("maker_first") or {}
        # No maker manager on this lane: a maker-preferred book is skipped, never upgraded.
        if _kp.maker_first_on() and mf.get("prefer") == "maker" and _kp.should_skip_taker(mf):
            _skip(r, 'SKIP_MAKER_FIRST', ["maker_first"])
            return None
    except Exception as e:
        _skip(r, 'SKIP_EV_PREFLIGHT', [f"ev preflight failed closed: {str(e)[:60]}"])
        return None
    import session_clocks as _sc
    blocked = _sc.before_create(opp)
    if blocked:
        _skip(r, blocked[0], [blocked[1]])
        return None
    import informed_before_create as _ibc
    pre = _ibc.create_preflight(opp, n, price_cents)
    if not pre["ok"]:
        _skip(r, 'SKIP_INFORMED', pre["reasons"])
        return None
    if pre.get("jev") is not None:
        r['jev'] = pre["jev"]
    return pre


# ═══ STEP 3: EXECUTE — Place orders ═══

def execute_bets(bets, dry_run=False, bankroll=None, fv_fn=None, fmap=None):
    """Place limit orders for each bet. Skip if already holding.

    Live path = same pre-create discipline as the allocator, on evidence from
    build_sports_opp. Any missing evidence → SKIP_* with reasons, no order.
    """
    from kalshi_auth import get_positions, get_fills

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
            opp, evidence = build_sports_opp(b, bankroll=bankroll, fv_fn=fv_fn, fmap=fmap)
        except Exception as e:
            _skip(r, 'SKIP_EVIDENCE', [f"evidence_error:{type(e).__name__}"])
            results.append(r)
            continue
        r['evidence'], r['evidence_version'] = evidence, SPORTS_EVIDENCE_VERSION
        price_cents = evidence["order_price_cents"]
        ask = evidence.get("ask")
        if ask is not None and int(round(ask * 100)) != price_cents:
            _skip(r, 'SKIP_EVIDENCE', ["price_moved"])  # scan ask ≠ live book ask: re-scan next cycle
            results.append(r)
            continue
        pre = _pre_create(opp, b['contracts'], price_cents, r)
        if pre is None:
            results.append(r)
            continue
        eft = evidence.get("edge_fee_true")
        if eft is None or eft <= 0:
            _skip(r, 'SKIP_EVIDENCE', ["edge_fee_true_nonpositive"])
            results.append(r)
            continue
        try:
            import informed_before_create as _ibc
            try:
                resp = create_order(b['fav_ticker'], 'yes', b['contracts'], price_cents,
                                    informed=pre["informed"])
            except _ibc.InformedCreateRefused as e:
                _skip(r, 'SKIP_INFORMED', e.reasons)
                results.append(r)
                continue
            fill, payload = ftl.resolve_kalshi_fill(resp, get_fills)
            r['order_id'] = fill.get('order_id')
            r['status'] = 'EXECUTED' if fill['complete'] else 'UNRECONCILED'
            try:
                ftl.book_kalshi_fill(
                    CATEGORY, b['fav_ticker'], 'yes', resp,
                    intended_source="live",
                    fallback_price=b['fav_price'],
                    fallback_contracts=b['contracts'],
                    fills_payload=payload)
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
    results = execute_bets(bets, dry_run=dry_run, bankroll=bankroll)
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
                "evidence_version": r.get("evidence_version"),
                "evidence": r.get("evidence"),
            })
        elif str(r['status']).startswith('SKIP_'):  # refusals are recorded, never settled
            log.setdefault("skips", []).append({
                "fav_ticker": r["fav_ticker"], "ts": r["ts"], "status": r["status"],
                "reasons": r.get("reasons", []), "contracts": r.get("contracts"),
                "fav_price": r.get("fav_price"),
                "evidence_version": r.get("evidence_version", SPORTS_EVIDENCE_VERSION),
                "evidence": r.get("evidence"),
            })
    if "skips" in log:
        log["skips"] = log["skips"][-SKIP_LOG_MAX:]
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
