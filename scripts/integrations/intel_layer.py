"""Valedia Intelligence Layer — ASI Brain Fusion for ALL trades
═══════════════════════════════════════════════════════════════════════════
Reads ALL intel sources, produces a unified conviction score for any asset
or event. This is what makes Valedia smarter than a mechanical bot.

Every trade on every platform must pass through this layer.

(c) GOATWORTHY Post LLC. All rights reserved.
"""
import os, sys, json, re
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
MODELS = os.path.expanduser("~/.openclaw/workspace/models")

def _load_json(filename):
    path = os.path.join(MODELS, filename)
    if os.path.exists(path):
        try:
            with open(path) as f: return json.load(f)
        except: pass
    return {}

class IntelLayer:
    """Unified intelligence layer. Query with any ticker, event, or question."""
    
    def __init__(self):
        self.ere = _load_json("latest_etherion_risk.json")
        self.mirofish = _load_json("mirofish_signal.json")
        self.fusion = _load_json("latest_fusion.json")
        self.retrain = _load_json("latest_retrain.json")
        self.hedge = _load_json("hedge_fund_intel.json")
        self.fear_greed = self._load_fear_greed()
        self.regime = self._detect_regime()
    
    def _load_fear_greed(self):
        try:
            import requests
            r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=5)
            if r.ok: return int(r.json()["data"][0]["value"])
        except: pass
        return 50
    
    def _detect_regime(self):
        """BULL/BEAR/SIDEWAYS from ERE v2.0 + fusion data."""
        # Check ERE Kolmogorov spectrum
        signals = self.ere.get("signals", {})
        spy_sig = signals.get("SPY", {})
        regime_raw = spy_sig.get("regime", "MIXED")
        
        # Check fear/greed
        if self.fear_greed <= 20: return "EXTREME_FEAR"
        elif self.fear_greed <= 35: return "FEAR"  
        elif self.fear_greed >= 80: return "EXTREME_GREED"
        elif self.fear_greed >= 65: return "GREED"
        return "NEUTRAL"
    
    def get_asset_conviction(self, ticker):
        """Get conviction score (0-100) for a crypto/stock ticker.
        50 = neutral, >65 = bullish, <35 = bearish."""
        scores = []
        
        # ERE signal
        ere_sig = self.ere.get("signals", {}).get(ticker, {})
        if ere_sig:
            ere_score = 50
            if ere_sig.get("signal") == "FADE_LONG": ere_score = 70
            elif ere_sig.get("signal") == "FADE_SHORT": ere_score = 30
            conf = ere_sig.get("confidence", 50) / 100
            scores.append(("ERE", ere_score, conf))
        
        # MiroFish swarm
        mf_sig = self.mirofish.get("signals", {}).get(ticker, {})
        if mf_sig:
            scores.append(("MiroFish", mf_sig.get("score", 50), mf_sig.get("confidence", 0.3)))
        
        # ML Kernel
        for r in self.retrain.get("results", []):
            if r.get("ticker") == ticker:
                buy_prob = r.get("buy_probability", 0.5)
                ml_score = buy_prob * 100
                scores.append(("ML", ml_score, r.get("accuracy", 0.5)))
                break
        
        # Fusion (if exists)
        fus_sig = self.fusion.get("signals", {}).get(ticker, {})
        if fus_sig:
            scores.append(("Fusion", fus_sig.get("fused_score", 50), 0.8))
        
        if not scores:
            return {"conviction": 50, "direction": "NEUTRAL", "confidence": 0, "sources": []}
        
        # Weighted average by confidence
        total_w = sum(c for _, _, c in scores)
        if total_w == 0: return {"conviction": 50, "direction": "NEUTRAL", "confidence": 0, "sources": []}
        weighted = sum(s * c for _, s, c in scores) / total_w
        avg_conf = total_w / len(scores)
        
        direction = "BUY" if weighted > 60 else "SELL" if weighted < 40 else "HOLD"
        return {
            "conviction": round(weighted, 1), "direction": direction,
            "confidence": round(avg_conf, 2),
            "sources": [(n, round(s,1), round(c,2)) for n,s,c in scores],
            "regime": self.regime, "fear_greed": self.fear_greed,
        }
    
    def get_price_prediction(self, ticker, hours_ahead=24):
        """Predict price direction + magnitude using all intel.
        Returns: {direction, magnitude_pct, confidence, current_price}."""
        conv = self.get_asset_conviction(ticker)
        
        try:
            import yfinance as yf
            yfk = ticker if '=' in ticker else (ticker if '-' in ticker else f"{ticker}-USD")
            data = yf.Ticker(yfk).history(period="5d")
            current = data["Close"].iloc[-1]
            vol_5d = data["Close"].pct_change().std()
        except:
            current = 0
            vol_5d = 0.02
        
        # Expected move: conviction deviation from neutral × volatility × time
        deviation = (conv["conviction"] - 50) / 50  # -1 to +1
        expected_move_pct = deviation * vol_5d * (hours_ahead / 24) * 100
        
        return {
            "ticker": ticker, "current_price": round(current, 2),
            "direction": "UP" if expected_move_pct > 0.5 else "DOWN" if expected_move_pct < -0.5 else "FLAT",
            "expected_move_pct": round(expected_move_pct, 2),
            "expected_price": round(current * (1 + expected_move_pct/100), 2),
            "confidence": conv["confidence"],
            "conviction": conv["conviction"],
            "regime": self.regime,
        }
    
    def get_kalshi_conviction(self, ticker, title, yes_price, hours_to_exp):
        """Score a Kalshi market using ALL intelligence sources.
        Returns conviction that YES will win (0-100).
        >70 = strong YES bet, <30 = strong NO bet, 40-60 = skip."""
        
        title_lower = title.lower()
        conviction = 50  # Start neutral
        confidence = 0.3
        reasons = []
        
        # ── CRYPTO BRACKETS (BTC/ETH/etc price) ──
        if any(t in ticker for t in ['KXBTC','KXETH','KXSOL','KXXRP']):
            # Extract the crypto ticker and strike
            if 'BTC' in ticker: asset = 'BTC-USD'
            elif 'ETH' in ticker: asset = 'ETH-USD'
            elif 'SOL' in ticker: asset = 'SOL-USD'
            else: asset = 'BTC-USD'
            
            pred = self.get_price_prediction(asset, hours_to_exp)
            if pred["current_price"] > 0:
                # Check if predicted price is above/below the strike
                strike = self._extract_strike(ticker)
                if strike:
                    if '-B' in ticker:  # Bracket: is predicted price IN this bracket?
                        bracket_low = strike
                        bracket_high = strike + 20
                        if bracket_low <= pred["expected_price"] <= bracket_high:
                            conviction = 75
                            reasons.append(f"Predicted {asset}=${pred['expected_price']:.0f} IN bracket ${bracket_low}-${bracket_high}")
                        elif abs(pred["expected_price"] - (bracket_low+10)) < 50:
                            conviction = 55
                            reasons.append(f"Predicted {asset}=${pred['expected_price']:.0f} near bracket")
                        else:
                            conviction = 20
                            reasons.append(f"Predicted {asset}=${pred['expected_price']:.0f} OUTSIDE bracket ${bracket_low}-${bracket_high}")
                    elif '-T' in ticker:  # Threshold: above strike?
                        if pred["expected_price"] > strike * 1.01:
                            conviction = 75
                            reasons.append(f"Predicted {asset}=${pred['expected_price']:.0f} > ${strike}")
                        elif pred["expected_price"] > strike:
                            conviction = 60
                        else:
                            conviction = 30
                            reasons.append(f"Predicted {asset}=${pred['expected_price']:.0f} < ${strike}")
                confidence = pred["confidence"]
        
        # ── OIL PRICE ──
        elif 'KXWTI' in ticker or 'oil' in title_lower:
            pred = self.get_price_prediction("CL=F", hours_to_exp)
            strike = self._extract_strike(ticker)
            if strike and pred["current_price"] > 0:
                buffer = pred["expected_price"] - strike
                if buffer > 2:
                    conviction = 80; reasons.append(f"Oil predicted ${pred['expected_price']:.0f} > ${strike} (+${buffer:.0f} buffer)")
                elif buffer > 0:
                    conviction = 60; reasons.append(f"Oil predicted ${pred['expected_price']:.0f} just above ${strike}")
                elif buffer > -1:
                    conviction = 45; reasons.append(f"Oil predicted ${pred['expected_price']:.0f} ≈ ${strike} (coin flip)")
                else:
                    conviction = 25; reasons.append(f"Oil predicted ${pred['expected_price']:.0f} below ${strike}")
                confidence = 0.6
        
        # ── CPI/INFLATION ──
        elif 'KXCPI' in ticker or 'cpi' in title_lower:
            # Use sentiment + regime to judge inflation direction
            if self.regime in ("EXTREME_FEAR", "FEAR"):
                # Fear markets → deflation risk → CPI likely LOWER
                conviction = 35  # Lean NO on "CPI rises more than X%"
                reasons.append(f"Regime={self.regime}, deflation risk → CPI likely lower")
            elif self.regime in ("GREED", "EXTREME_GREED"):
                conviction = 65
                reasons.append(f"Regime={self.regime}, inflation risk → CPI likely higher")
            else:
                conviction = 50
            # Check if strike is extreme
            strike = self._extract_strike(ticker)
            if strike and strike > 1.0:
                conviction = min(conviction, 30)
                reasons.append(f"CPI >{strike}% is extreme → lean NO")
            confidence = 0.5
        
        # ── GDP ──
        elif 'KXGDP' in ticker or 'gdp' in title_lower:
            if self.regime in ("EXTREME_FEAR", "FEAR"):
                conviction = 35; reasons.append("Fear regime → GDP growth likely lower")
            elif self.regime in ("GREED", "EXTREME_GREED"):
                conviction = 65; reasons.append("Greed regime → GDP growth likely higher")
            strike = self._extract_strike(ticker)
            if strike and strike > 4.0:
                conviction = min(conviction, 25); reasons.append(f"GDP >{strike}% extreme → lean NO")
            confidence = 0.5
        
        # ── FED RATE ──
        elif 'KXFED' in ticker or 'federal funds' in title_lower:
            if self.regime in ("EXTREME_FEAR", "FEAR"):
                # Fear → Fed may cut rates
                conviction = 40; reasons.append("Fear regime → Fed likely to cut")
            confidence = 0.4
        
        # ── WEATHER (NOAA gives us hard data) ──
        elif any(x in ticker for x in ['KXHIGH','KXLOW','KXRAIN','KXSNOW']):
            # Weather arb already uses NOAA — conviction comes from forecast match
            # This is handled in weather_arb.py, just pass through
            conviction = 50; confidence = 0.3  # Default, weather_arb overrides
        
        # ── Apply regime modifier to all trades ──
        # In extreme fear: reduce conviction on bullish bets, boost bearish
        if self.regime == "EXTREME_FEAR" and conviction > 60:
            old = conviction
            conviction = conviction * 0.85  # Dampen bullish conviction in fear
            reasons.append(f"Extreme fear dampening: {old:.0f}→{conviction:.0f}")
        elif self.regime == "EXTREME_GREED" and conviction < 40:
            old = conviction
            conviction = conviction * 1.15  # Dampen bearish conviction in greed
            reasons.append(f"Extreme greed dampening: {old:.0f}→{conviction:.0f}")
        
        return {
            "conviction_yes": round(conviction, 1),
            "conviction_no": round(100 - conviction, 1),
            "recommended_side": "yes" if conviction > 60 else "no" if conviction < 40 else "skip",
            "confidence": round(confidence, 2),
            "reasons": reasons,
            "regime": self.regime,
            "fear_greed": self.fear_greed,
        }
    
    def _extract_strike(self, ticker):
        """Extract numeric strike from ticker like KXWTI-26MAR23-T99.99 or KXETH-26MAR2305-B2100."""
        import re
        m = re.search(r'[-]T([\d.]+)$', ticker)
        if m: return float(m.group(1))
        m = re.search(r'[-]B([\d.]+)$', ticker)
        if m: return float(m.group(1))
        return None


# ═══ SINGLETON ═══
_intel = None
def get_intel():
    global _intel
    if _intel is None:
        _intel = IntelLayer()
    return _intel


if __name__ == "__main__":
    intel = IntelLayer()
    print(f"Regime: {intel.regime} | Fear/Greed: {intel.fear_greed}")
    print()
    
    # Test asset conviction
    for t in ["BTC-USD", "ETH-USD", "SOL-USD", "SPY"]:
        c = intel.get_asset_conviction(t)
        print(f"{t:10} conviction:{c['conviction']:5.1f} dir:{c['direction']:4} conf:{c['confidence']:.2f} sources:{len(c['sources'])}")
    
    print()
    # Test price predictions
    for t in ["BTC-USD", "ETH-USD", "CL=F"]:
        p = intel.get_price_prediction(t, 24)
        print(f"{t:10} now:${p['current_price']:.0f} → ${p['expected_price']:.0f} ({p['direction']}, {p['expected_move_pct']:+.1f}%)")
    
    print()
    # Test Kalshi market scoring
    test_markets = [
        ("KXWTI-26MAR23-T99.99", "Will WTI oil be >$99.99 today?", 0.49, 4),
        ("KXWTI-26MAR23-T97.99", "Will WTI oil be >$97.99 today?", 0.70, 4),
        ("KXETH-26MAR2305-B2040", "ETH bracket $2040-$2060", 0.50, 2),
        ("KXCPI-26MAR-T0.7", "Will CPI rise >0.7% in March?", 0.85, 432),
        ("KXGDP-26APR30-T1.5", "Will GDP increase >1.5% Q1?", 0.84, 900),
    ]
    for ticker, title, ya, hours in test_markets:
        k = intel.get_kalshi_conviction(ticker, title, ya, hours)
        print(f"  {ticker:35} → {k['recommended_side']:4} conv:{k['conviction_yes']:.0f}/{k['conviction_no']:.0f} conf:{k['confidence']:.2f}")
        for r in k["reasons"]: print(f"    {r}")
