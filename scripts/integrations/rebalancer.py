"""Portfolio Rebalancer — Valedia ASI
Maintains target allocation across assets.

Slice 5: proposed trades emit never-dispatchable shadow Intents (Slice 1 schema).
Does not auto-execute. Does not call market_buy/market_sell. Writer stays valedia.

get_balances/get_price stay injectable so tests never hit Coinbase. Cron default
still reads balances/prices via coinbase_trader (existing, read-only for this path).
(c) GOATWORTHY Post LLC.
"""
import os
import sys
from datetime import datetime, timezone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

TARGET_ALLOC = {
    "BTC-USD": 0.35,
    "ETH-USD": 0.25,
    "SOL-USD": 0.20,
    "XRP-USD": 0.10,
    "ADA-USD": 0.05,
    "AVAX-USD": 0.05,
}
REBAL_THRESHOLD = 0.05  # rebalance if >5% off target


def holdings_from_balances(bal, get_price_fn, target_alloc=None):
    """Map venue balances + prices → {ticker: {amount, price, value}}. No orders."""
    target_alloc = target_alloc or TARGET_ALLOC
    usd = float((bal or {}).get("USD", {}).get("balance", 0) or 0)
    total = usd
    holdings = {}
    for ticker in target_alloc:
        base = ticker.replace("-USD", "")
        amt = float((bal or {}).get(base, {}).get("balance", 0) or 0)
        price = float(get_price_fn(ticker) or 0) if amt > 0 else 0.0
        val = amt * price
        holdings[ticker] = {"amount": amt, "price": price, "value": val}
        total += val
    return holdings, usd, total


def propose_rebalance_trades(holdings, usd, total, target_alloc=None,
                             threshold=None):
    """Pure: target-weight drift → proposed trades. Never sends orders.

    Buy: notional in USD. Sell: qty in base. Matches coinbase_trader POST units.
    """
    target_alloc = target_alloc or TARGET_ALLOC
    threshold = REBAL_THRESHOLD if threshold is None else float(threshold)
    usd = float(usd or 0)
    total = float(total or 0)
    if total < 5:
        return []
    trades = []
    for ticker, target_pct in target_alloc.items():
        h = (holdings or {}).get(ticker) or {"amount": 0, "price": 0, "value": 0}
        current_pct = h["value"] / total if total > 0 else 0
        diff = current_pct - target_pct
        if abs(diff) <= threshold:
            continue
        trade_val = abs(diff) * total
        if diff > 0 and h.get("amount", 0) > 0:
            price = float(h.get("price") or 0)
            sell_amt = trade_val / price if price > 0 else 0
            if sell_amt > 0.0001:
                trades.append({
                    "side": "sell", "ticker": ticker,
                    "qty": sell_amt, "notional": trade_val,
                })
        elif diff < 0 and usd >= 1:
            buy_val = min(trade_val, usd * 0.9)
            if buy_val >= 1:
                trades.append({
                    "side": "buy", "ticker": ticker,
                    "qty": None, "notional": buy_val,
                })
    return trades


def emit_rebalance_shadows(trades, queue_path=None, gates=None):
    """Enqueue never-dispatchable shadow Intents for proposed trades. No POSTs."""
    from nautilus_shadow_coinbase import emit_rebalancer_shadow
    out = []
    for t in trades or []:
        rec = emit_rebalancer_shadow(t, queue_path=queue_path, gates=gates)
        out.append(rec)
    return out


def rebalancer_may_exec(gates=None):
    """AUTOEXEC is necessary but not sufficient.

    Requires: REBALANCER_AUTOEXEC, writer=valedia, live_flag=True, not NT,
    not ERE HALT/DANGER. Default cron passes no live_flag → False.
    """
    import valedia_config as vc
    if not vc.rebalancer_autoexec_enabled():
        return False
    if vc.venue_writer("coinbase") != "valedia":
        return False
    if vc.NAUTILUS_ENABLED or vc.NAUTILUS_LIVE or vc.nautilus_may_dispatch("coinbase"):
        return False
    g = gates or {}
    if g.get("live_flag") is not True:
        return False
    if str(g.get("ere_level") or "NORMAL").upper() in ("HALT", "DANGER"):
        return False
    if g.get("fee_ok") is False or g.get("category_ok") is False:
        return False
    return True


def exec_rebalance_trades(trades, market_buy_fn=None, market_sell_fn=None, gates=None):
    """POST only if rebalancer_may_exec. Never NT. Never invent fills.

    Injected fns only — tests pass mocks. Cron default does not POST.
    """
    if not rebalancer_may_exec(gates):
        return []
    out = []
    for t in trades or []:
        side = str(t.get("side") or "").strip().lower()
        if side == "buy" and market_buy_fn and t.get("notional"):
            market_buy_fn(t["ticker"], t["notional"])
            out.append({"side": "buy", "ticker": t["ticker"], "posted": True})
        elif side == "sell" and market_sell_fn and t.get("qty"):
            market_sell_fn(t["ticker"], t["qty"])
            out.append({"side": "sell", "ticker": t["ticker"], "posted": True})
    return out


def run_rebalance(get_balances_fn=None, get_price_fn=None, queue_path=None,
                  emit_shadows=True, gates=None, autoexec=None,
                  market_buy_fn=None, market_sell_fn=None):
    """Compute drift, emit shadow Intents, print actions.

    Default: REBALANCER_AUTOEXEC=False → shadow only, no market_buy/sell.
    AUTOEXEC=True still cannot NT-dispatch and still needs live_flag.
    Default getters are coinbase_trader reads (existing cron). Tests inject mocks.
    """
    if get_balances_fn is None or get_price_fn is None:
        from coinbase_trader import get_balances, get_price
        get_balances_fn = get_balances_fn or get_balances
        get_price_fn = get_price_fn or get_price
    ts = datetime.now(timezone.utc).isoformat()
    lines = [f"[REBAL] {ts}"]
    bal = get_balances_fn()
    holdings, usd, total = holdings_from_balances(bal, get_price_fn)
    if total < 5:
        return "[REBAL] Total portfolio < $5, skipping"
    lines.append(f"  Total: ${total:.2f} (${usd:.2f} USD)")
    for ticker, target_pct in TARGET_ALLOC.items():
        h = holdings[ticker]
        current_pct = h["value"] / total if total > 0 else 0
        diff = current_pct - target_pct
        lines.append(
            f"  {ticker:10s}: ${h['value']:.2f} ({current_pct*100:.1f}% vs "
            f"{target_pct*100:.0f}% target) diff={diff*100:+.1f}%"
        )
    trades = propose_rebalance_trades(holdings, usd, total)
    shadows = emit_rebalance_shadows(trades, queue_path=queue_path, gates=gates) if (
        emit_shadows and trades) else []
    if trades:
        lines.append(f"  Rebalance needed: {len(trades)} trades")
        for t in trades:
            if t["side"] == "sell":
                lines.append(
                    f"    >> SELL {t['qty']:.6f} {t['ticker']} (${t['notional']:.2f})"
                )
            else:
                lines.append(f"    >> BUY ${t['notional']:.2f} {t['ticker']}")
        for rec in shadows:
            if rec.get("skipped"):
                continue
            lines.append(
                f"    shadow intent={rec.get('intent_id','')} "
                f"status={rec.get('status')} dispatchable=false"
            )
        posted = exec_rebalance_trades(
            trades, market_buy_fn=market_buy_fn,
            market_sell_fn=market_sell_fn, gates=gates)
        if posted:
            lines.append(f"  AUTOEXEC posted {len(posted)} via valedia writer")
        elif rebalancer_may_exec(gates):
            lines.append("  AUTOEXEC on but no writer fns — shadow only")
        else:
            lines.append("  (shadow only; REBALANCER_AUTOEXEC=False or gates blocked)")
    else:
        lines.append("  Portfolio within tolerance")
    return "\n".join(lines)


if __name__ == "__main__":
    print(run_rebalance())
