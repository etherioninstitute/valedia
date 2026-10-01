"""DCA Module — Valedia ASI
Auto-buy fixed $ of top kernel picks on schedule.
Splits available cash across top-scoring assets.
(c) GOATWORTHY Post LLC.
"""
import os, sys, json
from datetime import datetime, timezone
sys.path.insert(0, "/Users/burtonchatelain/.openclaw/workspace/scripts/integrations")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import nimble_wiring_spot as nws  # Nimble 2 entry veto (shadow default)

DCA_CONFIG = "/Users/burtonchatelain/.openclaw/workspace/models/dca_config.json"
DCA_LOG = "/Users/burtonchatelain/.openclaw/workspace/models/dca_log.json"

DEFAULT_CONFIG = {
    "enabled": True,
    "amount_per_cycle": 5.0,
    "max_assets": 3,
    "crypto_tickers": ["BTC-USD","ETH-USD","SOL-USD","XRP-USD","ADA-USD","AVAX-USD"],
    "equity_tickers": ["SPY","QQQ","GLD","XLE"],
    "min_score": 50,
}

def load_config():
    try:
        with open(DCA_CONFIG) as f: return json.load(f)
    except: return DEFAULT_CONFIG

def save_config(cfg):
    with open(DCA_CONFIG, "w") as f: json.dump(cfg, f, indent=2)

def log_dca(entry):
    try:
        with open(DCA_LOG) as f: log = json.load(f)
    except: log = []
    log.append(entry)
    log = log[-200:]
    with open(DCA_LOG, "w") as f: json.dump(log, f, indent=2, default=str)

# Exits: none here. run_dca only BUYs, and a writer lock must never block a sell.
# No last-run state: every scheduled run re-evaluates, so a vetoed / blocked run
# already retries on the next run (unlike strategy_modules.DCAEngine's weekly last_buy).

def _book_dca(venue, ticker, resp):
    """Book one DCA buy (spot_ledger -> forward_test_ledger.book_spot_fill) as DCA_CRYPTO /
    DCA_EQUITY: fee-true EXECUTED row on a confirmed fill, UNRECONCILED on a POST-ack,
    nothing for a rejected order or Alpaca paper. Bookkeeping only: never raises."""
    try:
        import spot_ledger as sl
        return sl.book_buy(sl.DCA[venue], venue, ticker, resp,
                           intended_source=sl.venue_source(venue))["status"]
    except Exception as e:
        print(f"[dca_module] ledger booking failed for {ticker}: {e}", file=sys.stderr)
        return None

# Ambiguous buys (5xx / 408 / timeout / unclear body — dca_pending.outcome): the venue MAY
# have taken the order, so it is never booked as "not filled". An UNRECONCILED ledger stub
# (no order id, intended notional, reason ambiguous_5xx) is written instead. Before the next
# buy of that ticker its open stubs are resolved against the venue's read-only
# recent_buy_orders: found (and not already booked) → booked as the fill, stub closed, no
# re-buy; none → stub closed, buy proceeds; lookup or ledger read failed → ticker skipped.
# Stubs (ts) and venue orders (created_time) are paired oldest-first.
AMBIGUOUS_REASON = "ambiguous_5xx"


def _stub_ambiguous(venue, ticker, amount, why):
    import uuid
    import forward_test_ledger as ftl
    import spot_ledger as sl
    rec = {"type": "entry", "id": "ft_" + uuid.uuid4().hex[:12],
           "ts": datetime.now(timezone.utc).isoformat(), "strategy": sl.DCA[venue],
           "venue": venue, "ticker": ticker, "side": "buy", "entry_price": 0.0,
           "contracts": 0.0, "entry_fee": 0.0, "predicted_direction": None,
           "predicted_prob": None, "source": "unreconciled", "fill_status": "UNRECONCILED",
           "intended_source": sl.venue_source(venue), "intended_notional": round(float(amount), 2),
           "reason": AMBIGUOUS_REASON, "error": str(why)[:160]}
    ftl._append(rec)
    return rec["id"]


def _resolve_stubs(venue, ticker, lookup=None):
    """("clear"|"found"|"unknown", lines). Only "clear" lets this run buy the ticker."""
    import dca_pending as dp
    try:
        import forward_test_ledger as ftl
        rows = ftl._read()
        done = {r.get("entry_id") for r in rows if r.get("type") == "settlement"}
    except Exception as e:  # ledger unreadable: a stub may exist → never buy blind
        print(f"[dca_module] ambiguous-stub read failed, skipping {ticker}: {e}", file=sys.stderr)
        return "unknown", [f"  {ticker}: ledger unreadable (stub check failed); not buying"]
    stubs = sorted((r for r in rows if r.get("type") == "entry"
                    and r.get("reason") == AMBIGUOUS_REASON and r.get("venue") == venue
                    and r.get("ticker") == ticker and r.get("id") not in done),
                   key=lambda r: str(r.get("ts")))
    if not stubs:
        return "clear", []
    since = datetime.fromisoformat(stubs[0]["ts"]) - dp.timedelta(seconds=dp.SKEW_S)
    fn = dp._lookup(venue, lookup)
    try:
        orders = fn(ticker, since.strftime("%Y-%m-%dT%H:%M:%SZ")) if fn else None
    except Exception:
        orders = None
    if orders is None:
        return "unknown", [f"  {ticker}: earlier ambiguous buy unresolved (venue lookup failed); not re-buying"]
    import spot_ledger as sl
    import spot_reconcile as sr
    orders, found, lines = dp.by_created(dp.unbooked(orders, rows)), 0, []  # oldest first
    for st in stubs:
        if orders:
            o = dict(orders.pop(0), success=True)
            res = sl.book_buy(sl.DCA[venue], venue, ticker, o, intended_source=sl.venue_source(venue))
            sr._close_stub(st, (res or {}).get("id"))
            found += 1
            lines.append(f"  {ticker}: earlier ambiguous buy found ({o.get('order_id')}) -> booked, not re-bought")
        else:
            sr._close_stub(st)
            lines.append(f"  {ticker}: earlier ambiguous buy not at venue -> stub closed")
    return ("found" if found else "clear"), lines


def _buy_or_stub(venue, ticker, amount, buy):
    """Place one buy; book an ack/refusal as before, stub an ambiguous outcome. Returns resp."""
    import dca_pending as dp
    try:
        r = buy()
    except Exception as e:
        if dp.outcome(exc=e) == "ambiguous":
            _stub_ambiguous(venue, ticker, amount, e)
            return {"success": False, "error": str(e)[:120], "ambiguous": True}
        raise
    try:
        import forward_test_ledger as ftl
        acked = bool(ftl.spot_ack_ok(venue, r))
    except Exception:
        acked = False
    if dp.outcome(r, acked=acked) == "ambiguous":
        _stub_ambiguous(venue, ticker, amount, (r or {}).get("error") if isinstance(r, dict) else r)
    else:
        _book_dca(venue, ticker, r)
    return r if isinstance(r, dict) else {"success": False, "error": str(r)[:120]}


def run_dca(lookup=None):
    import yfinance as yf
    from kernel_predictor_v16e import KernelPredictor
    from coinbase_trader import get_balances, market_buy as cb_buy
    from alpaca_executor import get_account, market_buy as alp_buy
    cfg = load_config()
    if not cfg["enabled"]: return "[DCA] Disabled"
    ts = datetime.now(timezone.utc).isoformat()
    lines = [f"[DCA] {ts}"]
    # Score all tickers
    scored = []
    for tk in cfg["crypto_tickers"]:
        try:
            p = yf.Ticker(tk).history(period="6mo")["Close"].values
            if len(p)<80: continue
            sig = KernelPredictor("crypto").predict(p)
            if sig["score"] >= cfg["min_score"]:
                scored.append({"ticker":tk,"score":sig["score"],"type":"crypto","prices":p})
        except: pass
    for tk in cfg["equity_tickers"]:
        try:
            p = yf.Ticker(tk).history(period="6mo")["Close"].values
            if len(p)<80: continue
            sig = KernelPredictor("equity").predict(p)
            if sig["score"] >= cfg["min_score"]:
                scored.append({"ticker":tk,"score":sig["score"],"type":"equity","prices":p})
        except: pass
    scored.sort(key=lambda x: x["score"], reverse=True)
    top = scored[:cfg["max_assets"]]
    if not top:
        lines.append("  No assets above min score")
        return "\n".join(lines)
    per_asset = cfg["amount_per_cycle"] / len(top)
    for asset in top:
        try:
            if asset["type"] == "crypto":
                bal = get_balances()
                cash = bal.get("USD",{}).get("balance",0)
                if not nws.venue_buy_allowed("coinbase"):
                    lines.append(f"  {asset['ticker']}: SKIP_VENUE_WRITER coinbase writer!=valedia")
                    continue
                state, notes = _resolve_stubs("coinbase", asset["ticker"], lookup)
                lines.extend(notes)
                if state != "clear":
                    continue
                if cash >= per_asset:
                    row = nws.dca_row("coinbase", per_asset, score=asset["score"], prices=asset.get("prices"))
                    nv = nws.gate("crypto", asset["ticker"], row,
                                  source="dca_module", venue="coinbase", label="dca")
                    lines.extend(nws.note(asset["ticker"], nv))
                    if nws.blocked(nv):
                        continue
                    r = _buy_or_stub("coinbase", asset["ticker"], per_asset,
                                     lambda: cb_buy(asset["ticker"], per_asset))
                    lines.append(f"  DCA BUY ${per_asset:.2f} {asset['ticker']} (score={asset['score']:.0f}) -> {r.get('success','?')}")
                    log_dca({"ts":ts,"ticker":asset["ticker"],"amount":per_asset,"score":asset["score"]})
                else:
                    lines.append(f"  {asset['ticker']}: insufficient CB cash (${cash:.2f})")
            else:
                if not nws.venue_buy_allowed("alpaca"):
                    lines.append(f"  {asset['ticker']}: SKIP_VENUE_WRITER alpaca writer!=valedia")
                    continue
                state, notes = _resolve_stubs("alpaca", asset["ticker"], lookup)
                lines.extend(notes)
                if state != "clear":
                    continue
                acct = get_account()
                cash = float(acct.get("cash",0))
                if cash >= per_asset:
                    row = nws.dca_row("alpaca", per_asset, score=asset["score"], prices=asset.get("prices"))
                    nv = nws.gate("equity", asset["ticker"], row,
                                  source="dca_module", venue="alpaca", label="dca")
                    lines.extend(nws.note(asset["ticker"], nv))
                    if nws.blocked(nv):
                        continue
                    _buy_or_stub("alpaca", asset["ticker"], per_asset,
                                 lambda: alp_buy(asset["ticker"], notional=per_asset))
                    lines.append(f"  DCA BUY ${per_asset:.2f} {asset['ticker']} (score={asset['score']:.0f})")
                    log_dca({"ts":ts,"ticker":asset["ticker"],"amount":per_asset,"score":asset["score"]})
                else:
                    lines.append(f"  {asset['ticker']}: insufficient Alpaca cash (${cash:.2f})")
        except Exception as e:
            lines.append(f"  {asset['ticker']}: error {str(e)[:50]}")
    return "\n".join(lines)

if __name__ == "__main__":
    save_config(load_config())
    print(run_dca())
