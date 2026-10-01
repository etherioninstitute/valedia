"""Kalshi Latency Arbitrage Engine — Valedia ASI
Exploits the lag between CEX spot price moves and Kalshi binary contract repricing.

Architecture:
  1. INGEST: Coinbase WebSocket for real-time BTC/ETH/SOL spot prices
  2. DETECT: Price_Delta_Trigger fires when spot moves >0.3% in <30s
  3. VERIFY: Cross-check with Twelve Data API for confirmation
  4. EXECUTE: Buy YES/NO on Kalshi 15-min binaries before market reprices
  5. TRACK: Log every trigger, execution, and settlement
  6. LEARN: Capability Evolver analyzes win/loss patterns

(c) GOATWORTHY Post LLC — March 2026
"""
import os, sys, json, time, hmac, hashlib, threading, logging, ssl
from datetime import datetime, timezone, timedelta
from collections import deque
import requests
from requests.adapters import HTTPAdapter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from kalshi_auth import get, create_order
from latency_arb_evidence import (  # noqa: F401 - re-exported: evidence + pre-create chain
    ARB_EVIDENCE_VERSION, ARB_FV_SOURCE, arb_pre_create, build_arb_evidence,
    heuristic_fair_value)

# ═══ CONFIGURATION ═══
KALSHI_SERIES = {
    'BTC': ['KXBTCD', 'KXBTC'],  # Directional + Range contracts
    'ETH': ['KXETH'],
    'SOL': [],  # No SOL contracts on Kalshi currently
}

# Trigger thresholds
DELTA_THRESHOLD = 0.0015   # 0.15% spot move triggers arb check (was 0.3%, never fired)
DELTA_WINDOW_SEC = 30     # Must happen within 30 seconds
MIN_EDGE_CENTS = 3        # Minimum edge (Kalshi price vs implied prob)
MAX_BET_DOLLARS = 25      # Hard cap per trade (kalshi-command-center default)
MAX_DAILY_LOSS = 50       # Daily loss cutoff
MAX_CONTRACTS = 20        # Max contracts per order
COOLDOWN_SEC = 120        # Minimum seconds between trades on same asset

# Data paths
MODELS = os.path.expanduser("~/.openclaw/workspace/models")
ARB_LOG = os.path.join(MODELS, "latency_arb_log.json")
ARB_STATE = os.path.join(MODELS, "latency_arb_state.json")

logging.basicConfig(level=logging.INFO, format='%(asctime)s [LAT-ARB] %(message)s')
log = logging.getLogger("latency_arb")


# ═══ TLS: load verify locations once on the main thread ═══
# OpenSSL 3 SIGSEGV (exit 139): concurrent SSL_CTX_load_verify_locations
# from per-request SSLContext in the poller vs Kalshi GETs on main.

class _PinnedTLSAdapter(HTTPAdapter):
    def __init__(self, ssl_context, **kwargs):
        self._ssl_context = ssl_context
        super().__init__(**kwargs)

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        pool_kwargs["ssl_context"] = self._ssl_context
        return super().init_poolmanager(connections, maxsize, block=block, **pool_kwargs)

    def cert_verify(self, conn, url, verify, cert):
        super().cert_verify(conn, url, verify, cert)
        # requests would set conn.ca_certs=certifi and urllib3 would
        # SSL_CTX_load_verify_locations on every GET (the SIGSEGV).
        conn.ca_certs = None
        conn.ca_cert_dir = None


_HTTPS = None
_SSL_CTX = None
_HTTPS_LOCK = threading.Lock()


def _make_ssl_context(cafile=None):
    if cafile:
        return ssl.create_default_context(cafile=cafile)
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def https_session(cafile=None):
    """Build SSLContext + Session once. Call from the main thread first."""
    global _HTTPS, _SSL_CTX
    with _HTTPS_LOCK:
        if _HTTPS is None:
            _SSL_CTX = _make_ssl_context(cafile=cafile)
            sess = requests.Session()
            ad = _PinnedTLSAdapter(_SSL_CTX, pool_connections=8, pool_maxsize=8)
            sess.mount("https://", ad)
            sess.mount("http://", HTTPAdapter())
            _HTTPS = sess
        return _HTTPS


def reset_https_session():
    """Test helper. Next https_session() rebuilds the pinned Session."""
    global _HTTPS, _SSL_CTX
    with _HTTPS_LOCK:
        if _HTTPS is not None:
            try:
                _HTTPS.close()
            except Exception:
                pass
        _HTTPS = None
        _SSL_CTX = None


# ═══ STEP 1: PRICE INGESTION — Coinbase WebSocket ═══

class SpotPriceTracker:
    """Tracks real-time spot prices via Coinbase Advanced Trade WebSocket.
    Maintains a rolling window of prices for delta detection."""
    
    def __init__(self, pairs=None):
        self.pairs = pairs or ['BTC-USD', 'ETH-USD', 'SOL-USD']
        self.prices = {p: deque(maxlen=100) for p in self.pairs}  # (timestamp, price)
        self.current = {p: 0.0 for p in self.pairs}
        self._lock = threading.Lock()
        self._running = False

    def update_price(self, pair, price, ts=None):
        """Thread-safe price update."""
        ts = ts or time.time()
        with self._lock:
            self.prices[pair].append((ts, price))
            self.current[pair] = price
    
    def get_delta(self, pair, window_sec=DELTA_WINDOW_SEC):
        """Calculate price delta within the rolling window.
        Returns (delta_pct, direction, current_price, window_start_price)."""
        with self._lock:
            history = list(self.prices[pair])
        
        if len(history) < 2:
            return 0.0, 'FLAT', 0.0, 0.0
        
        now = time.time()
        cutoff = now - window_sec
        window = [(t, p) for t, p in history if t >= cutoff]
        
        if len(window) < 2:
            return 0.0, 'FLAT', 0.0, 0.0
        
        start_price = window[0][1]
        end_price = window[-1][1]
        
        if start_price == 0:
            return 0.0, 'FLAT', 0.0, 0.0
        
        delta = (end_price - start_price) / start_price
        direction = 'UP' if delta > 0 else 'DOWN' if delta < 0 else 'FLAT'
        return delta, direction, end_price, start_price

    def poll_prices(self, interval=2.0):
        """Poll Coinbase for spot prices. Fallback when WS unavailable.
        Uses CoinGecko as backup (no auth needed)."""
        sess = https_session()
        self._running = True
        log.info(f"Polling spot prices every {interval}s for {self.pairs}")
        
        while self._running:
            for pair in self.pairs:
                try:
                    # Primary: CoinGecko (no auth, no IP lock)
                    coin_id = pair.replace('-USD', '').lower()
                    coin_map = {'btc': 'bitcoin', 'eth': 'ethereum', 'sol': 'solana'}
                    cg_id = coin_map.get(coin_id, coin_id)
                    r = sess.get(
                        f'https://api.coingecko.com/api/v3/simple/price?ids={cg_id}&vs_currencies=usd',
                        timeout=3
                    )
                    if r.ok:
                        price = r.json().get(cg_id, {}).get('usd', 0)
                        if price > 0:
                            self.update_price(pair, price)
                except Exception:
                    pass
            time.sleep(interval)
    
    def start_polling(self, interval=2.0):
        """Start polling in background thread."""
        t = threading.Thread(target=self.poll_prices, args=(interval,), daemon=True)
        t.start()
        return t
    
    def stop(self):
        self._running = False


# ═══ STEP 2: PRICE DELTA TRIGGER ═══

def price_delta_trigger(tracker, pair='BTC-USD'):
    """Core latency arb trigger. Fires when spot moves >DELTA_THRESHOLD
    within DELTA_WINDOW_SEC seconds.
    
    Returns:
        dict with signal details if triggered, None otherwise.
        {
            'pair': 'BTC-USD',
            'direction': 'UP',        # or 'DOWN'
            'delta_pct': 0.45,         # percent move
            'spot_price': 87234.50,
            'window_start': 86842.10,
            'timestamp': '2026-03-30T02:15:00Z',
            'action': 'BUY_YES',       # buy YES if UP, buy NO if DOWN
            'confidence': 0.82,
        }
    """
    delta, direction, current, start = tracker.get_delta(pair)
    
    if abs(delta) < DELTA_THRESHOLD:
        return None
    
    # Determine action: if BTC pumps, buy YES on "BTC above X" contract
    # If BTC dumps, buy YES on "BTC below X" contract (or buy NO on "above")
    if direction == 'UP':
        action = 'BUY_YES'  # Buy YES on "above current level" contract
    elif direction == 'DOWN':
        action = 'BUY_NO'   # Buy NO on "above current level" (equivalent to YES on below)
    else:
        return None
    
    # Confidence scales with delta magnitude
    confidence = min(0.95, abs(delta) / 0.01)  # 1% move = 0.95 confidence
    
    return {
        'pair': pair,
        'direction': direction,
        'delta_pct': round(abs(delta) * 100, 3),
        'spot_price': current,
        'window_start': start,
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'action': action,
        'confidence': round(confidence, 3),
    }


# ═══ STEP 3: KALSHI CONTRACT FINDER ═══

def find_kalshi_contract(signal):
    """Find the best Kalshi crypto strike-price contract to exploit the delta.
    
    Kalshi crypto contracts have strike prices (e.g., KXBTCD-26MAR3017-T66999.99 = 
    "BTC above $66,999.99 at 5pm ET"). When spot pumps past a strike, the YES side
    should reprice upward — if the orderbook is lagging, we buy cheap.
    
    Returns the contract ticker and optimal side, or None."""
    asset = signal['pair'].replace('-USD', '')
    series_list = KALSHI_SERIES.get(asset, [])
    if not series_list:
        log.warning(f"No Kalshi series for {asset}")
        return None
    
    spot = signal['spot_price']
    direction = signal['direction']
    
    try:
        all_candidates = []
        for series in series_list:
            r = get(f'/markets?limit=50&series_ticker={series}&status=open')
            markets = r.get('markets', [])
            
            for m in markets:
                ticker = m.get('ticker', '')
                ya = float(m.get('yes_ask_dollars', '0') or '0')
                yb = float(m.get('yes_bid_dollars', '0') or '0')
                
                if ya <= 0.01 or ya >= 0.99:
                    continue  # Skip dead contracts
                
                # Parse strike price from ticker
                # Format: KXBTCD-26MAR3017-T66999.99 or B66999.99
                # T = above threshold, B = below/between
                parts = ticker.split('-')
                strike_part = parts[-1] if len(parts) >= 3 else ''
                strike_type = strike_part[0] if strike_part else ''  # T or B
                try:
                    strike_price = float(strike_part[1:])
                except:
                    continue
                
                # Distance from current spot to strike
                distance = abs(spot - strike_price)
                distance_pct = distance / spot * 100
                
                # Determine if this contract benefits from our signal direction
                edge = 0
                side = 'yes'
                if direction == 'UP' and strike_type == 'T' and strike_price <= spot * 1.005:
                    # BTC pumped, strike is at or below spot → YES should be high
                    # If YES is still cheap (lagging), that's our edge
                    implied_prob = ya
                    fair_value = heuristic_fair_value(distance_pct)  # Rough estimate
                    edge = int((fair_value - implied_prob) * 100)
                    side = 'yes'
                elif direction == 'DOWN' and strike_type == 'T' and strike_price >= spot * 0.995:
                    # BTC dumped, strike is at or above spot → NO should be high
                    implied_prob = 1.0 - yb if yb > 0 else 1.0 - ya
                    fair_value = heuristic_fair_value(distance_pct)
                    edge = int((fair_value - implied_prob) * 100)
                    side = 'no'
                else:
                    continue
                
                if edge < MIN_EDGE_CENTS:
                    continue
                
                all_candidates.append({
                    'ticker': ticker,
                    'title': m.get('title', '')[:50],
                    'yes_ask': ya,
                    'yes_bid': yb,
                    'spread': round(ya - yb, 3),
                    'strike': strike_price,
                    'strike_type': strike_type,
                    'distance_pct': round(distance_pct, 3),
                    'side': side,
                    'edge_cents': edge,
                    'fair_value': round(fair_value, 4),
                    'signal': signal,
                    'exp': m.get('expected_expiration_time', '')[:16],
                })
        
        if not all_candidates:
            log.info(f"No contracts with sufficient edge for {asset} {direction}")
            return None
        
        # Best = highest edge, then nearest strike
        all_candidates.sort(key=lambda x: (-x['edge_cents'], x['distance_pct']))
        best = all_candidates[0]
        log.info(f"Best contract: {best['ticker']} {best['side'].upper()} "
                 f"edge:{best['edge_cents']}c strike:${best['strike']:,.2f} "
                 f"(spot:${spot:,.2f}, dist:{best['distance_pct']:.2f}%)")
        return best
        
    except Exception as e:
        log.error(f"Contract search failed: {e}")
        return None


# ═══ STEP 4: EXECUTION ═══

def load_state():
    """Load arb state (cooldowns, daily P&L, etc.)."""
    try:
        with open(ARB_STATE) as f: return json.load(f)
    except: return {"daily_pnl": 0, "daily_trades": 0, "last_trade": {}, "date": ""}

def save_state(state):
    with open(ARB_STATE, 'w') as f: json.dump(state, f, indent=2)

def load_arb_log():
    try:
        with open(ARB_LOG) as f: return json.load(f)
    except: return {"trades": [], "stats": {}}

def save_arb_log(log_data):
    with open(ARB_LOG, 'w') as f: json.dump(log_data, f, indent=2, default=str)

def ack_status(order):
    """POST-ack is UNRECONCILED until Kalshi fill count > 0. Dry-run stays DRY_RUN."""
    try:
        import forward_test_ledger as _ftl
        fill = _ftl.extract_kalshi_fill(order if isinstance(order, dict) else {})
        return "EXECUTED" if fill.get("complete") else "UNRECONCILED"
    except Exception:
        return "UNRECONCILED"

def execute_arb(contract, dry_run=True):
    """Execute the latency arb trade on Kalshi.
    
    Args:
        contract: dict from find_kalshi_contract()
        dry_run: if True, log but don't execute
    
    Returns:
        dict with execution result
    """
    state = load_state()
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if state.get("date") != today:
        state = {"daily_pnl": 0, "daily_trades": 0, "last_trade": {}, "date": today}
    
    # RISK GATE 1: Daily loss cutoff
    if state["daily_pnl"] <= -MAX_DAILY_LOSS:
        log.warning(f"Daily loss cutoff hit: ${state['daily_pnl']:.2f}")
        return {"status": "SKIP_DAILY_LOSS", "pnl": state["daily_pnl"]}
    
    # RISK GATE 2: Cooldown per asset
    pair = contract['signal']['pair']
    last_ts = state.get("last_trade", {}).get(pair, 0)
    if time.time() - last_ts < COOLDOWN_SEC:
        remaining = int(COOLDOWN_SEC - (time.time() - last_ts))
        log.info(f"Cooldown: {pair} ({remaining}s remaining)")
        return {"status": "SKIP_COOLDOWN", "remaining_sec": remaining}
    
    # Size the trade
    side = contract['side']
    price_cents = int(contract['yes_ask'] * 100) if side == 'yes' else int((1.0 - contract['yes_bid']) * 100)
    n_contracts = min(MAX_CONTRACTS, int(MAX_BET_DOLLARS / (price_cents / 100)))
    cost = n_contracts * price_cents / 100
    potential_profit = n_contracts * (1.0 - price_cents / 100)
    
    result = {
        "ticker": contract['ticker'],
        "side": side,
        "contracts": n_contracts,
        "price_cents": price_cents,
        "cost": round(cost, 2),
        "potential_profit": round(potential_profit, 2),
        "edge_cents": contract['edge_cents'],
        "signal": contract['signal'],
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    if dry_run:
        result["status"] = "DRY_RUN"
        log.info(f"DRY RUN: {side.upper()} {n_contracts}x {contract['ticker']} "
                 f"@{price_cents}c edge:{contract['edge_cents']}c "
                 f"${cost:.2f}→+${potential_profit:.2f}")
    else:
        pre = arb_pre_create(contract, n_contracts, price_cents, result)
        if pre is None:
            # Refused before any order: record the evidence, leave cooldown/daily state alone.
            log.warning(f"{result['status']}: {contract['ticker']} {result.get('error', '')}")
            arb_log = load_arb_log()
            arb_log["trades"].append(result)
            save_arb_log(arb_log)
            return result
        try:
            order = create_order(contract['ticker'], side, n_contracts, price_cents,
                                 informed=pre["informed"])
            result["status"] = ack_status(order)
            nested = order.get("order") if isinstance(order.get("order"), dict) else {}
            result["order_id"] = nested.get("order_id") or order.get("order_id") or "?"
            log.info(f"{result['status']}: {side.upper()} {n_contracts}x {contract['ticker']} "
                     f"@{price_cents}c edge:{contract['edge_cents']}c")
        except Exception as e:
            result["status"] = "FAILED"
            result["error"] = str(e)[:100]
            log.error(f"FAILED: {e}")
    
    # Update state
    state["daily_trades"] += 1
    state["last_trade"][pair] = time.time()
    if result["status"] == "EXECUTED":
        state["daily_pnl"] -= cost  # Debit now, credit on settlement
    save_state(state)
    # Log the trade
    arb_log = load_arb_log()
    arb_log["trades"].append(result)
    save_arb_log(arb_log)
    
    return result


# ═══ STEP 5: SETTLEMENT CHECKER ═══

def check_arb_settlements():
    """Check all pending arb trades for settlement."""
    arb_log = load_arb_log()
    settled = 0
    for trade in arb_log["trades"]:
        if trade.get("settled") or trade["status"] not in ("EXECUTED",):
            continue
        ticker = trade.get("ticker", "")
        try:
            m = get(f"/markets/{ticker}")
            mkt = m.get("market", m)
            if mkt.get("status") == "settled":
                result = mkt.get("result", "")
                side = trade.get("side", "yes")
                won = (result == side)
                cost = trade["cost"]
                pnl = trade["potential_profit"] if won else -cost
                trade["settled"] = True
                trade["result"] = result
                trade["won"] = won
                trade["pnl"] = round(pnl, 2)
                settled += 1
                log.info(f"SETTLED: {ticker} {'WIN' if won else 'LOSS'} ${pnl:+.2f}")
        except:
            pass
    
    if settled > 0:
        save_arb_log(arb_log)
    
    # Compute stats
    executed = [t for t in arb_log["trades"] if t["status"] == "EXECUTED"]
    done = [t for t in executed if t.get("settled")]
    wins = sum(1 for t in done if t.get("won"))
    losses = len(done) - wins
    net_pnl = sum(t.get("pnl", 0) for t in done)
    arb_log["stats"] = {
        "total_trades": len(executed),
        "settled": len(done),
        "wins": wins,
        "losses": losses,
        "win_rate": round(wins / len(done) * 100, 1) if done else 0,
        "net_pnl": round(net_pnl, 2),
        "updated": datetime.now(timezone.utc).isoformat(),
    }
    save_arb_log(arb_log)
    return arb_log["stats"]


# ═══ STEP 6: MAIN LOOP ═══

def scan_once(tracker, dry_run=True):
    """Single scan cycle: check all pairs for delta triggers."""
    results = []
    for pair in tracker.pairs:
        signal = price_delta_trigger(tracker, pair)
        if signal:
            log.info(f"TRIGGER: {pair} {signal['direction']} "
                     f"{signal['delta_pct']:.3f}% in {DELTA_WINDOW_SEC}s")
            contract = find_kalshi_contract(signal)
            if contract:
                result = execute_arb(contract, dry_run=dry_run)
                results.append(result)
            else:
                log.info(f"No matching Kalshi contract for {pair}")
    return results

def run(mode='scan', duration_min=30):
    """Run the latency arb engine.
    
    Modes:
        scan     — Show current price deltas, no trades
        dry-run  — Detect triggers, log but don't execute
        execute  — LIVE trading (respects all risk gates)
        settle   — Check settlements only
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"LATENCY ARB ENGINE ({ts}) [{mode.upper()}]", ""]
    
    if mode == 'settle':
        stats = check_arb_settlements()
        lines.append(f"Settlements: {stats.get('settled',0)} checked")
        lines.append(f"Record: {stats.get('wins',0)}W/{stats.get('losses',0)}L "
                     f"({stats.get('win_rate',0)}%) net:${stats.get('net_pnl',0):+.2f}")
        report = "\n".join(lines)
        print(report)
        return report
    
    # Pin CA store on main thread before the poller starts.
    https_session()
    tracker = SpotPriceTracker()
    tracker.start_polling(interval=2.0)
    
    # Wait for initial prices
    log.info("Warming up price tracker (10s)...")
    time.sleep(10)
    
    for pair in tracker.pairs:
        lines.append(f"  {pair}: ${tracker.current[pair]:,.2f}")
    lines.append("")
    dry_run = mode != 'execute'
    
    if mode == 'scan':
        # Single scan
        for pair in tracker.pairs:
            delta, direction, current, start = tracker.get_delta(pair)
            lines.append(f"  {pair}: delta={abs(delta)*100:.3f}% {direction} "
                         f"(${start:,.2f}→${current:,.2f})")
            if abs(delta) >= DELTA_THRESHOLD:
                lines.append(f"    TRIGGER! Would fire arb")
        tracker.stop()
        report = "\n".join(lines)
        print(report)
        return report
    
    # Continuous monitoring loop
    end_time = time.time() + duration_min * 60
    scan_count = 0
    triggers = 0
    
    lines.append(f"Monitoring for {duration_min}min (scan every 5s)...")
    print("\n".join(lines))
    
    try:
        while time.time() < end_time:
            results = scan_once(tracker, dry_run=dry_run)
            scan_count += 1
            triggers += len(results)
            
            for r in results:
                status = r.get("status", "?")
                ticker = r.get("ticker", "?")[:30]
                cost = r.get("cost", 0)
                log.info(f"  {status}: {ticker} ${cost:.2f}")
            
            time.sleep(5)  # Scan every 5 seconds
    except KeyboardInterrupt:
        log.info("Stopped by user")
    finally:
        tracker.stop()
    
    log.info(f"Session: {scan_count} scans, {triggers} triggers")
    return f"{scan_count} scans, {triggers} triggers"


# ═══ CLI ═══

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Kalshi Latency Arbitrage Engine")
    parser.add_argument("--mode", choices=["scan", "dry-run", "execute", "settle"],
                        default="scan", help="Operating mode")
    parser.add_argument("--duration", type=int, default=30,
                        help="Monitor duration in minutes (for dry-run/execute)")
    args = parser.parse_args()
    run(mode=args.mode, duration_min=args.duration)
