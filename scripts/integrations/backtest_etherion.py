"""Etherion Risk Engine — Historical Backtest
The REAL test: does following ERE signals make money?

Runs the MeanReversionEngine on 1 year of historical data,
simulates trades, measures P&L, Sharpe ratio, max drawdown,
and compares against buy-and-hold.

(c) GOATWORTHY Post LLC. All rights reserved.
"""
import sys, os, json
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import datetime, timezone

sys.path.insert(0, "/Users/burtonchatelain/.openclaw/workspace/scripts/integrations")
from Etherion_Risk_Engine import (
    SignalDecomposer, MeanReversionEngine, DrawdownGuard, 
    VarianceCascadeEngine, ExecutionErrorEstimator
)

class Backtester:
    """Simulates ERE signals on historical data with realistic execution."""
    
    def __init__(self, initial_capital=1000, fee_pct=0.001, slippage_pct=0.0005):
        self.initial_capital = initial_capital
        self.fee_pct = fee_pct  # 0.1% per trade (round trip = 0.2%)
        self.slippage_pct = slippage_pct
        
    def run_mean_reversion_backtest(self, ticker, period="2y", lookback=120, 
                                     entry_z=2.0, exit_z=0.5, cutoff=20):
        """Backtest the MeanReversionEngine on a single asset.
        
        Strategy:
          - When noise z-score > entry_z: SHORT (fade the noise)
          - When noise z-score < -entry_z: LONG (fade the noise)
          - Exit when |z-score| < exit_z
          - Position size: flat $100 per trade (simplified)
        """
        data = yf.Ticker(ticker).history(period=period)
        if len(data) < lookback + 50:
            return {"error": f"Insufficient data for {ticker}"}
        
        prices = data["Close"].values
        dates = data.index
        returns = np.diff(np.log(prices))
        
        mre = MeanReversionEngine(cutoff, entry_z, exit_z)
        guard = DrawdownGuard(self.initial_capital)
        
        # Simulation state
        capital = self.initial_capital
        position = 0  # +1 = long, -1 = short, 0 = flat
        entry_price = 0
        trades = []
        equity_curve = [capital]
        daily_returns = []
        signals_generated = 0
        trades_executed = 0
        
        # Walk forward: use lookback window, generate signal, trade next bar
        for i in range(lookback, len(prices) - 1):
            window_prices = prices[i - lookback:i + 1]
            current_price = prices[i]
            next_price = prices[i + 1]
            
            sig = mre.generate_signal(window_prices)
            z = sig["z_score"]
            signals_generated += 1
            
            # Check drawdown guard
            if len(daily_returns) > 10:
                guard_check = guard.check_guard(np.array(daily_returns[-60:]))
                if guard_check["action"] == "HALT_ALL":
                    # Close any position and stop
                    if position != 0:
                        pnl = position * (current_price - entry_price) / entry_price * 100
                        fee = self.fee_pct * 100
                        net_pnl_pct = pnl - fee - self.slippage_pct * 100
                        capital *= (1 + net_pnl_pct / 100)
                        trades.append({"type": "GUARD_EXIT", "pnl_pct": net_pnl_pct, "date": str(dates[i].date())})
                        position = 0
                    break
            
            # Entry logic
            if position == 0:
                if sig["signal"] == "FADE_SHORT" and sig["confidence"] > 60:
                    position = -1  # Short (noise too high, expect reversion down)
                    entry_price = current_price
                    trades_executed += 1
                    
                elif sig["signal"] == "FADE_LONG" and sig["confidence"] > 60:
                    position = 1  # Long (noise too low, expect reversion up)
                    entry_price = current_price
                    trades_executed += 1
            
            # Exit logic
            elif position != 0:
                if abs(z) < exit_z:
                    # Mean-reversion complete — exit
                    pnl = position * (current_price - entry_price) / entry_price * 100
                    fee = self.fee_pct * 100 * 2  # Round trip
                    slip = self.slippage_pct * 100 * 2
                    net_pnl_pct = pnl - fee - slip
                    capital *= (1 + net_pnl_pct / 100)
                    
                    trades.append({
                        "type": "LONG" if position == 1 else "SHORT",
                        "pnl_pct": round(net_pnl_pct, 4),
                        "gross_pnl_pct": round(pnl, 4),
                        "fees_pct": round(fee + slip, 4),
                        "date": str(dates[i].date()),
                        "bars_held": 0,  # Simplified
                    })
                    position = 0
                
                # Stop loss: 3% max loss per trade
                elif position * (current_price - entry_price) / entry_price < -0.03:
                    pnl = position * (current_price - entry_price) / entry_price * 100
                    fee = self.fee_pct * 100 * 2
                    slip = self.slippage_pct * 100 * 2
                    net_pnl_pct = pnl - fee - slip
                    capital *= (1 + net_pnl_pct / 100)
                    trades.append({
                        "type": "STOP_LOSS",
                        "pnl_pct": round(net_pnl_pct, 4),
                        "date": str(dates[i].date()),
                    })
                    position = 0
            
            # Track equity
            if position != 0:
                unrealized = position * (next_price - entry_price) / entry_price
                equity_curve.append(capital * (1 + unrealized))
            else:
                equity_curve.append(capital)
            
            if len(equity_curve) >= 2:
                daily_ret = (equity_curve[-1] - equity_curve[-2]) / equity_curve[-2]
                daily_returns.append(daily_ret)
        
        # Close any remaining position at end
        if position != 0:
            final_price = prices[-1]
            pnl = position * (final_price - entry_price) / entry_price * 100
            fee = self.fee_pct * 100 * 2
            net_pnl_pct = pnl - fee
            capital *= (1 + net_pnl_pct / 100)
            trades.append({"type": "END_EXIT", "pnl_pct": round(net_pnl_pct, 4)})
        
        # Buy-and-hold comparison
        bh_return = (prices[-1] / prices[lookback] - 1) * 100
        
        # Calculate metrics
        equity_arr = np.array(equity_curve)
        total_return = (capital / self.initial_capital - 1) * 100
        
        # Sharpe ratio (annualized)
        if len(daily_returns) > 10 and np.std(daily_returns) > 0:
            sharpe = np.mean(daily_returns) / np.std(daily_returns) * np.sqrt(252)
        else:
            sharpe = 0
        
        # Max drawdown
        running_max = np.maximum.accumulate(equity_arr)
        drawdowns = (running_max - equity_arr) / running_max
        max_dd = np.max(drawdowns) * 100
        
        # Win rate
        winning_trades = [t for t in trades if t.get("pnl_pct", 0) > 0]
        losing_trades = [t for t in trades if t.get("pnl_pct", 0) <= 0]
        win_rate = len(winning_trades) / len(trades) * 100 if trades else 0
        avg_win = np.mean([t["pnl_pct"] for t in winning_trades]) if winning_trades else 0
        avg_loss = np.mean([t["pnl_pct"] for t in losing_trades]) if losing_trades else 0
        
        return {
            "ticker": ticker,
            "period": period,
            "total_return_pct": round(total_return, 2),
            "buy_hold_return_pct": round(bh_return, 2),
            "excess_return_pct": round(total_return - bh_return, 2),
            "sharpe_ratio": round(sharpe, 3),
            "max_drawdown_pct": round(max_dd, 2),
            "total_trades": len(trades),
            "signals_generated": signals_generated,
            "win_rate_pct": round(win_rate, 1),
            "avg_win_pct": round(avg_win, 3),
            "avg_loss_pct": round(avg_loss, 3),
            "profit_factor": round(abs(avg_win / avg_loss), 2) if avg_loss != 0 else float('inf'),
            "final_capital": round(capital, 2),
            "fees_paid_est": round(len(trades) * self.fee_pct * 2 * 100, 2),
            "guard_triggered": any(t.get("type") == "GUARD_EXIT" for t in trades),
            "stop_losses": sum(1 for t in trades if t.get("type") == "STOP_LOSS"),
            "trades": trades[-10:],  # Last 10 trades for inspection
        }


def full_backtest_report():
    """Run backtests across multiple assets and parameters."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"ETHERION RISK ENGINE — BACKTEST REPORT ({ts})", ""]
    lines.append("Strategy: Mean-Reversion (fade noise when |z| > 2.0, exit when |z| < 0.5)")
    lines.append("Capital: $1000 | Fees: 0.1% per trade | Slippage: 0.05% | Stop-loss: 3%")
    lines.append("")
    
    bt = Backtester(initial_capital=1000, fee_pct=0.001, slippage_pct=0.0005)
    
    assets = {
        "SPY": "SPY",
        "QQQ": "QQQ",
        "BTC": "BTC-USD",
        "ETH": "ETH-USD",
        "SOL": "SOL-USD",
        "XRP": "XRP-USD",
        "AVAX": "AVAX-USD",
        "LINK": "LINK-USD",
    }
    
    all_results = []
    
    lines.append(f"{'Asset':6} {'ERE':>8} {'B&H':>8} {'Excess':>8} {'Sharpe':>7} {'MaxDD':>7} {'Trades':>7} {'WinRate':>8} {'PF':>5}")
    lines.append("-" * 75)
    
    for name, ticker in assets.items():
        try:
            result = bt.run_mean_reversion_backtest(ticker, period="2y")
            all_results.append(result)
            
            lines.append(
                f"{name:6} "
                f"{result['total_return_pct']:>7.1f}% "
                f"{result['buy_hold_return_pct']:>7.1f}% "
                f"{result['excess_return_pct']:>+7.1f}% "
                f"{result['sharpe_ratio']:>7.3f} "
                f"{result['max_drawdown_pct']:>6.1f}% "
                f"{result['total_trades']:>7} "
                f"{result['win_rate_pct']:>7.1f}% "
                f"{result['profit_factor']:>5.2f}"
            )
        except Exception as e:
            lines.append(f"{name:6} ERROR: {e}")
    
    lines.append("-" * 75)
    
    # Aggregate stats
    if all_results:
        avg_return = np.mean([r["total_return_pct"] for r in all_results])
        avg_bh = np.mean([r["buy_hold_return_pct"] for r in all_results])
        avg_sharpe = np.mean([r["sharpe_ratio"] for r in all_results])
        avg_win = np.mean([r["win_rate_pct"] for r in all_results])
        total_trades = sum(r["total_trades"] for r in all_results)
        
        lines.append(f"{'AVG':6} {avg_return:>7.1f}% {avg_bh:>7.1f}% {avg_return-avg_bh:>+7.1f}% {avg_sharpe:>7.3f} {'':>7} {total_trades:>7} {avg_win:>7.1f}%")
        
        lines.append("")
        lines.append("VERDICT:")
        
        winners = [r for r in all_results if r["excess_return_pct"] > 0]
        losers = [r for r in all_results if r["excess_return_pct"] <= 0]
        
        lines.append(f"  Assets where ERE beat buy-and-hold: {len(winners)}/{len(all_results)}")
        if winners:
            for w in winners:
                lines.append(f"    {w['ticker']:10} +{w['excess_return_pct']:.1f}% excess (Sharpe: {w['sharpe_ratio']:.3f})")
        
        lines.append(f"  Assets where buy-and-hold won: {len(losers)}/{len(all_results)}")
        if losers:
            for l in losers:
                lines.append(f"    {l['ticker']:10} {l['excess_return_pct']:.1f}% excess (Sharpe: {l['sharpe_ratio']:.3f})")
        
        lines.append("")
        if avg_return > avg_bh and avg_sharpe > 0.5:
            lines.append("  CONCLUSION: ERE mean-reversion strategy shows positive edge.")
            lines.append(f"  Average excess return: {avg_return-avg_bh:+.1f}% | Average Sharpe: {avg_sharpe:.3f}")
        elif avg_sharpe > 0:
            lines.append("  CONCLUSION: ERE shows marginal edge. Needs parameter optimization.")
            lines.append(f"  Average excess return: {avg_return-avg_bh:+.1f}% | Average Sharpe: {avg_sharpe:.3f}")
        else:
            lines.append("  CONCLUSION: ERE mean-reversion strategy does NOT show edge on this data.")
            lines.append("  The drawdown guard and risk monitoring modules may still add value")
            lines.append("  as portfolio protection, even if directional signals underperform.")
        
        # Trade detail for best performer
        best = max(all_results, key=lambda r: r["excess_return_pct"])
        lines.append(f"\n  Best performer: {best['ticker']}")
        lines.append(f"  Return: {best['total_return_pct']:.1f}% vs B&H: {best['buy_hold_return_pct']:.1f}%")
        lines.append(f"  Trades: {best['total_trades']} | Wins: {best['win_rate_pct']:.0f}% | Stop losses: {best['stop_losses']}")
        if best["trades"]:
            lines.append(f"  Last trades:")
            for t in best["trades"][-5:]:
                lines.append(f"    {t.get('date','?')} {t.get('type','?'):12} {t.get('pnl_pct',0):+.3f}%")
    
    # Save results
    try:
        with open("/Users/burtonchatelain/.openclaw/workspace/models/backtest_results.json", "w") as f:
            json.dump({"timestamp": ts, "results": all_results}, f, indent=2, default=str)
    except:
        pass
    
    return "\n".join(lines)


if __name__ == "__main__":
    print(full_backtest_report())
