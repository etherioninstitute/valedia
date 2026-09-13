"""Kelly Criterion Position Sizing + Regime Detection — Valedia ASI
Mathematically optimal bet sizing based on edge strength.
Adapts to market regime (bull/bear/sideways).

Kelly Formula: f* = (p * b - q) / b
  f* = fraction of capital to bet
  p = probability of winning
  b = net odds (win amount / loss amount)
  q = 1 - p (probability of losing)

Half-Kelly: We use f*/2 for safety (reduces variance by 75%, reduces growth by only 25%)
"""
import os, sys, json, math
import numpy as np
from datetime import datetime, timezone
from valedia_config import KELLY_SAFETY_FRACTION, KELLY_FUSE, KELLY_DAILY_FUSE, dynamic_per_trade_cap  # shared source of truth


def bayesian_shrink_prob(raw_prob_pct, wins, total, prior_alpha=2, prior_beta=2):
    """Beta-Binomial shrinkage of a probability estimate toward a category's real posterior
    (adapted from kalshi-agent scoring.bayesian_kelly_prob). With little settled data -> heavy
    shrink toward the posterior; with lots -> trust the raw estimate. Returns pct in [1, 99].
        posterior = (a+wins)/(a+b+n) ; weight = n/(n+a+b) ; final = w*raw + (1-w)*posterior"""
    n = max(0, int(total)); wins = max(0, min(n, int(wins)))
    posterior = (prior_alpha + wins) / (prior_alpha + prior_beta + n)
    w = n / (n + prior_alpha + prior_beta) if (n + prior_alpha + prior_beta) > 0 else 0.0
    adjusted = w * (raw_prob_pct / 100.0) + (1 - w) * posterior
    return max(1.0, min(99.0, adjusted * 100.0))

sys.path.insert(0, "/Users/burtonchatelain/.openclaw/workspace/scripts/integrations")

# === REGIME DETECTION ===
class RegimeDetector:
    """Classify market as BULL, BEAR, or SIDEWAYS using SPY as proxy."""
    
    @staticmethod
    def detect():
        """Returns regime dict with type and confidence."""
        try:
            import yfinance as yf  # lazy: only regime detection needs market data, not the Kelly math
            spy = yf.Ticker("SPY").history(period="6mo")
            if len(spy) < 100:
                return {"regime": "UNKNOWN", "confidence": 0, "indicators": {}}
            
            close = spy["Close"].values
            
            # SMA crossover
            sma20 = np.mean(close[-20:])
            sma50 = np.mean(close[-50:])
            sma200 = np.mean(close[-200:]) if len(close) >= 200 else np.mean(close)
            sma_bull = sma20 > sma50 > sma200
            sma_bear = sma20 < sma50 < sma200
            
            # Trend strength (% above/below 200 SMA)
            trend_pct = (close[-1] - sma200) / sma200 * 100
            
            # Volatility (20-day realized vol annualized)
            returns = np.diff(np.log(close[-21:]))
            vol_20d = np.std(returns) * np.sqrt(252) * 100
            
            # Momentum (20-day return)
            mom_20d = (close[-1] / close[-20] - 1) * 100
            
            # ADX proxy (using directional movement)
            high = spy["High"].values[-15:]
            low = spy["Low"].values[-15:]
            up_moves = np.diff(high)
            down_moves = -np.diff(low)
            plus_dm = np.mean([max(u, 0) if u > d else 0 for u, d in zip(up_moves, down_moves)])
            minus_dm = np.mean([max(d, 0) if d > u else 0 for u, d in zip(up_moves, down_moves)])
            adx_proxy = abs(plus_dm - minus_dm) / (plus_dm + minus_dm + 1e-10) * 100
            
            # Classify
            if sma_bull and mom_20d > 2 and trend_pct > 2:
                regime = "BULL"
                confidence = min(95, 60 + trend_pct + mom_20d)
            elif sma_bear and mom_20d < -2 and trend_pct < -2:
                regime = "BEAR"
                confidence = min(95, 60 + abs(trend_pct) + abs(mom_20d))
            elif vol_20d < 15 and abs(mom_20d) < 3:
                regime = "SIDEWAYS"
                confidence = min(90, 50 + (15 - vol_20d) * 2)
            elif mom_20d > 0:
                regime = "BULL"
                confidence = min(80, 50 + mom_20d * 3)
            elif mom_20d < 0:
                regime = "BEAR"
                confidence = min(80, 50 + abs(mom_20d) * 3)
            else:
                regime = "SIDEWAYS"
                confidence = 50
            
            return {
                "regime": regime,
                "confidence": round(confidence, 1),
                "indicators": {
                    "sma20": round(sma20, 2),
                    "sma50": round(sma50, 2),
                    "sma200": round(sma200, 2),
                    "trend_pct": round(trend_pct, 2),
                    "vol_20d": round(vol_20d, 2),
                    "mom_20d": round(mom_20d, 2),
                    "adx_proxy": round(adx_proxy, 1),
                    "spy_price": round(close[-1], 2),
                },
                "strategy_bias": {
                    "BULL": {"swing_trades": 0.4, "staking": 0.2, "no_harvest": 0.1, "funding_arb": 0.2, "shorts": 0.0, "cash": 0.1},
                    "BEAR": {"swing_trades": 0.05, "staking": 0.3, "no_harvest": 0.3, "funding_arb": 0.15, "shorts": 0.1, "cash": 0.1},
                    "SIDEWAYS": {"swing_trades": 0.1, "staking": 0.25, "no_harvest": 0.2, "funding_arb": 0.3, "shorts": 0.0, "cash": 0.15},
                }.get(regime, {})
            }
        except Exception as e:
            return {"regime": "UNKNOWN", "confidence": 0, "error": str(e)}

# === KELLY CRITERION ===
class KellySizer:
    """Calculate optimal position sizes using Kelly Criterion."""
    
    def __init__(self, total_capital, max_daily_risk=KELLY_DAILY_FUSE, max_per_trade=KELLY_FUSE):
        self.total_capital = total_capital
        self.max_daily_risk = max_daily_risk
        self.max_per_trade = max_per_trade
        self.daily_risked = 0
    
    def full_kelly(self, win_prob, win_amount_ratio, loss_amount_ratio=1.0):
        """Calculate full Kelly fraction."""
        p = win_prob
        q = 1 - p
        b = win_amount_ratio / loss_amount_ratio
        kelly = (p * b - q) / b
        return max(0, kelly)
    
    def half_kelly(self, win_prob, win_amount_ratio, loss_amount_ratio=1.0):
        """Half-Kelly for safety (75% less variance, 25% less growth)."""
        return self.full_kelly(win_prob, win_amount_ratio, loss_amount_ratio) * KELLY_SAFETY_FRACTION
    
    def _concurrent_bets(self):
        """Live simultaneous bets (real open ledger entries) + the one being sized. 1 on error."""
        try:
            import forward_test_ledger as _ftl
            return max(1, _ftl.open_position_count() + 1)
        except Exception:
            return 1

    def size_position(self, composite_score, expected_return_pct, max_loss_pct=None, regime="SIDEWAYS", track_record_mult=None, risk_factor=None, concurrent_bets=None, category=None):
        """Calculate position size based on composite conviction score.
        
        Args:
            composite_score: 0-100 from signal fusion
            expected_return_pct: expected gain as % (e.g., 2.0)
            max_loss_pct: max possible loss as % (defaults to expected_return)
            regime: BULL/BEAR/SIDEWAYS from regime detector
        
        Returns:
            Position sizing recommendation
        """
        if max_loss_pct is None:
            max_loss_pct = expected_return_pct
        
        # Convert composite score to win probability (calibrated)
        # Score 80 = ~70% win rate, Score 90 = ~80%, Score 70 = ~60%
        if composite_score >= 80:
            win_prob = 0.60 + (composite_score - 80) * 0.01
        elif composite_score >= 70:
            win_prob = 0.55 + (composite_score - 70) * 0.005
        elif composite_score >= 60:
            win_prob = 0.50 + (composite_score - 60) * 0.005
        else:
            win_prob = 0.45  # Below threshold, don't trade
        
        win_prob = min(0.85, max(0.45, win_prob))

        # Bayesian shrinkage toward the category's REAL settled record (if a category is given).
        # Auto-sources (wins,total) from the fee-inclusive ledger — sizing informed by real edge.
        if category:
            try:
                import forward_test_ledger as _ftl
                cs = _ftl.category_stats().get(category)
                if cs:
                    win_prob = bayesian_shrink_prob(win_prob * 100, cs["wins"], cs["n"]) / 100.0
            except Exception:
                pass

        # Kelly sizing
        kelly_frac = self.half_kelly(win_prob, expected_return_pct / 100, max_loss_pct / 100)
        
        # Regime adjustment
        regime_multiplier = {
            "BULL": 1.2,     # More aggressive in bull
            "BEAR": 0.6,     # Conservative in bear
            "SIDEWAYS": 0.8, # Moderate in sideways
            "UNKNOWN": 0.7,
        }.get(regime, 0.8)
        
        adjusted_frac = kelly_frac * regime_multiplier

        # Thorp concurrent-bet reduction: with N simultaneous live bets, each is f/sqrt(N) so
        # aggregate risk stays bounded. Auto-sourced from real open ledger entries.
        n_conc = self._concurrent_bets() if concurrent_bets is None else max(1, int(concurrent_bets))
        if n_conc > 1:
            adjusted_frac /= math.sqrt(n_conc)

        # Dynamic per-trade ceiling — breathes with regime/track-record/risk; fused at KELLY_FUSE
        adjusted_frac = min(adjusted_frac, dynamic_per_trade_cap(regime, track_record_mult, risk_factor))
        
        # Check daily risk budget
        remaining_daily = self.max_daily_risk - self.daily_risked
        if remaining_daily <= 0:
            return {
                "action": "SKIP",
                "reason": "Daily risk budget exhausted",
                "daily_risked": round(self.daily_risked * 100, 2),
                "position_usd": 0,
                "position_pct": 0,
            }
        
        adjusted_frac = min(adjusted_frac, remaining_daily)
        
        position_usd = round(self.total_capital * adjusted_frac, 2)
        
        # Minimum viable trade
        min_trade = 5.0
        if position_usd < min_trade:
            return {
                "action": "SKIP",
                "reason": f"Position ${position_usd:.2f} below minimum ${min_trade}",
                "position_usd": position_usd,
                "position_pct": round(adjusted_frac * 100, 2),
            }
        
        # Signal classification
        if composite_score >= 80:
            conviction = "STRONG"
        elif composite_score >= 70:
            conviction = "STANDARD"
        elif composite_score >= 60:
            conviction = "WEAK"
        else:
            return {
                "action": "NO TRADE",
                "reason": f"Composite score {composite_score} below minimum 60",
                "position_usd": 0,
                "position_pct": 0,
            }
        
        self.daily_risked += adjusted_frac
        
        return {
            "action": "EXECUTE",
            "conviction": conviction,
            "composite_score": composite_score,
            "win_probability": round(win_prob * 100, 1),
            "kelly_fraction": round(kelly_frac * 100, 4),
            "regime_multiplier": regime_multiplier,
            "regime": regime,
            "position_pct": round(adjusted_frac * 100, 2),
            "position_usd": position_usd,
            "max_loss_usd": round(position_usd * max_loss_pct / 100, 2),
            "expected_profit_usd": round(position_usd * expected_return_pct / 100 * win_prob, 2),
            "daily_risk_used": round(self.daily_risked * 100, 2),
            "daily_risk_remaining": round((self.max_daily_risk - self.daily_risked) * 100, 2),
        }

# === UNIFIED TRADE DECISION ENGINE ===
def make_trade_decision(asset, composite_score, expected_return_pct, capital, regime_data=None):
    """The final gatekeeper. Combines fusion + Kelly + fees + regime into one decision."""
    from fee_engine import is_trade_profitable, compare_platforms
    
    if regime_data is None:
        regime_data = RegimeDetector.detect()
    
    regime = regime_data.get("regime", "SIDEWAYS")
    
    # Kelly sizing
    sizer = KellySizer(capital, max_daily_risk=KELLY_DAILY_FUSE, max_per_trade=KELLY_FUSE)
    sizing = sizer.size_position(composite_score, expected_return_pct, regime=regime)
    
    if sizing["action"] != "EXECUTE":
        return {"decision": "REJECT", "reason": sizing.get("reason", sizing["action"]), "sizing": sizing}
    
    # Fee check across platforms
    amount = sizing["position_usd"]
    platform_analysis = compare_platforms(amount, expected_return_pct)
    
    best = platform_analysis[0]
    if not best["profitable"]:
        return {
            "decision": "REJECT",
            "reason": f"No platform profitable after fees. Best: {best['platform']} net ${best['net_profit']:.4f}",
            "sizing": sizing,
            "fee_analysis": best,
        }
    
    return {
        "decision": "EXECUTE",
        "asset": asset,
        "conviction": sizing["conviction"],
        "composite_score": composite_score,
        "regime": regime,
        "regime_confidence": regime_data.get("confidence", 0),
        "platform": best["platform"],
        "position_usd": sizing["position_usd"],
        "position_pct": sizing["position_pct"],
        "expected_return": expected_return_pct,
        "net_profit_est": best["net_profit"],
        "net_return_pct": best["net_return_pct"],
        "fees_pct": best["fee_pct"],
        "win_probability": sizing["win_probability"],
        "max_loss_usd": sizing["max_loss_usd"],
        "daily_risk_used": sizing["daily_risk_used"],
        "sizing": sizing,
        "fee_analysis": best,
        "regime_strategy": regime_data.get("strategy_bias", {}),
    }

def full_asi_report(capital=80.78):
    """The full ASI-level analysis report."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"Valedia ASI Report ({ts})", f"Capital: ${capital:.2f}", ""]
    
    # Regime
    regime = RegimeDetector.detect()
    lines.append(f"REGIME: {regime['regime']} ({regime['confidence']:.0f}% confidence)")
    ind = regime.get("indicators", {})
    if ind:
        lines.append(f"  SPY: ${ind.get('spy_price', 0):.2f} | Vol: {ind.get('vol_20d', 0):.1f}% | Mom: {ind.get('mom_20d', 0):.1f}%")
        lines.append(f"  SMA20: {ind.get('sma20', 0):.0f} SMA50: {ind.get('sma50', 0):.0f} SMA200: {ind.get('sma200', 0):.0f}")
    
    bias = regime.get("strategy_bias", {})
    if bias:
        top = sorted(bias.items(), key=lambda x: x[1], reverse=True)[:3]
        lines.append(f"  Best strategies: {', '.join(f'{k} ({v*100:.0f}%)' for k, v in top)}")
    
    lines.append("")
    
    # Signal Fusion
    from signal_fusion import scan_all
    fusion_results = scan_all()
    
    lines.append("SIGNAL FUSION:")
    sizer = KellySizer(capital)
    
    for r in fusion_results:
        score = r.get("composite_score", 0)
        signal = r.get("signal", "?")
        asset = r.get("asset", "?")
        bar = "#" * int(score / 5) + "." * (20 - int(score / 5))
        lines.append(f"  {asset:5} [{bar}] {score:5.1f} {signal}")
        
        # If actionable, show Kelly sizing
        if score >= 60:
            decision = make_trade_decision(asset, score, 2.0, capital, regime)
            if decision["decision"] == "EXECUTE":
                lines.append(f"    >> {decision['conviction']} | ${decision['position_usd']:.2f} ({decision['position_pct']:.1f}%) on {decision['platform']} | net ${decision['net_profit_est']:.4f}")
            else:
                lines.append(f"    >> SKIP: {decision['reason']}")
    
    lines.append("")
    lines.append("RISK BUDGET:")
    lines.append(f"  Daily limit: {sizer.max_daily_risk*100:.0f}% = ${capital * sizer.max_daily_risk:.2f}")
    lines.append(f"  Used: {sizer.daily_risked*100:.1f}% = ${capital * sizer.daily_risked:.2f}")
    lines.append(f"  Remaining: {(sizer.max_daily_risk - sizer.daily_risked)*100:.1f}%")
    
    return "\n".join(lines)

if __name__ == "__main__":
    print(full_asi_report())
