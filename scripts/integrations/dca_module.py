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

def run_dca():
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
                if cash >= per_asset:
                    row = nws.dca_row("coinbase", per_asset, score=asset["score"], prices=asset.get("prices"))
                    nv = nws.gate("crypto", asset["ticker"], row,
                                  source="dca_module", venue="coinbase", label="dca")
                    lines.extend(nws.note(asset["ticker"], nv))
                    if nws.blocked(nv):
                        continue
                    r = cb_buy(asset["ticker"], per_asset)
                    lines.append(f"  DCA BUY ${per_asset:.2f} {asset['ticker']} (score={asset['score']:.0f}) -> {r.get('success','?')}")
                    log_dca({"ts":ts,"ticker":asset["ticker"],"amount":per_asset,"score":asset["score"]})
                    try:
                        import forward_test_ledger as _ftl
                        _ftl.book_spot_fill("DCA", asset["ticker"], "buy", "coinbase",
                                            r or {}, intended_source="live")
                    except Exception:
                        pass
                else:
                    lines.append(f"  {asset['ticker']}: insufficient CB cash (${cash:.2f})")
            else:
                if not nws.venue_buy_allowed("alpaca"):
                    lines.append(f"  {asset['ticker']}: SKIP_VENUE_WRITER alpaca writer!=valedia")
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
                    r = alp_buy(asset["ticker"], notional=per_asset)
                    lines.append(f"  DCA BUY ${per_asset:.2f} {asset['ticker']} (score={asset['score']:.0f})")
                    log_dca({"ts":ts,"ticker":asset["ticker"],"amount":per_asset,"score":asset["score"]})
                    try:
                        import forward_test_ledger as _ftl
                        import alpaca_executor as _ae
                        live = _ae.alpaca_is_live() if hasattr(_ae, "alpaca_is_live") else getattr(_ae, "IS_LIVE", False)
                        src = "live" if live else "paper"
                        _ftl.book_spot_fill("DCA", asset["ticker"], "buy", "alpaca",
                                            r or {}, intended_source=src)
                    except Exception:
                        pass
                else:
                    lines.append(f"  {asset['ticker']}: insufficient Alpaca cash (${cash:.2f})")
        except Exception as e:
            lines.append(f"  {asset['ticker']}: error {str(e)[:50]}")
    return "\n".join(lines)

if __name__ == "__main__":
    save_config(load_config())
    print(run_dca())
