"""Intel & Sentiment Engine — Valedia ASI
=========================================
Combined intelligence layer. Merges former sentiment_scanner + alpha_intelligence.

Sources:
  1. Fear/Greed Index (crypto + equity)
  2. Finnhub news sentiment + social sentiment
  3. SEC EDGAR insider buys (Form 4)
  4. Inversion analysis (contrarian detector)
  5. Performance tracking (prediction accuracy log)
  6. Headline keyword scoring

(c) GOATWORTHY Post LLC.
"""
import os, sys, json, requests
import numpy as np
from datetime import datetime, timezone, timedelta

FINNHUB_KEY = os.environ.get("FINNHUB_API_KEY", "")
FINNHUB_BASE = "https://finnhub.io/api/v1"
HEADERS = {"User-Agent": "Valedia-ASI research@goatworthy.com"}
PREDICTION_LOG = os.path.expanduser("~/.openclaw/workspace/models/prediction_log.json")

# ═══ FEAR/GREED ═══
def get_fear_greed():
    """Crypto Fear & Greed Index. Extreme fear = buy. Extreme greed = sell."""
    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
        if r.ok:
            d = r.json()["data"][0]
            return {"value": int(d["value"]), "label": d["value_classification"]}
    except: pass
    return {"value": 50, "label": "Neutral"}

# ═══ NEWS & SOCIAL SENTIMENT (Finnhub) ═══
def get_market_news(category="general"):
    r = requests.get(f"{FINNHUB_BASE}/news", params={"category": category, "token": FINNHUB_KEY}, timeout=10)
    return r.json()[:20] if r.ok else []

def get_social_sentiment(symbol):
    r = requests.get(f"{FINNHUB_BASE}/stock/social-sentiment", params={"symbol": symbol, "token": FINNHUB_KEY}, timeout=10)
    return r.json() if r.ok else None

def analyze_headlines(news):
    """Keyword-based sentiment scoring on headlines."""
    bull = ["surge","rally","bull","gain","jump","soar","breakout","upgrade","buy","growth","profit","record"]
    bear = ["crash","drop","bear","fall","plunge","dump","selloff","downgrade","sell","loss","fear","risk"]
    bc = sum(1 for a in news for w in bull if w in a.get("headline","").lower())
    ec = sum(1 for a in news for w in bear if w in a.get("headline","").lower())
    total = bc + ec
    if total == 0: return "NEUTRAL", 0.5
    ratio = bc / total
    return ("BULLISH" if ratio > 0.6 else "BEARISH" if ratio < 0.4 else "NEUTRAL"), ratio

# ═══ SEC INSIDER ACTIVITY ═══
def get_insider_buys(days=7):
    try:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)
        url = f"https://efts.sec.gov/LATEST/search-index?q=%22open+market+purchase%22&startdt={start.strftime('%Y-%m-%d')}&enddt={end.strftime('%Y-%m-%d')}&forms=4"
        r = requests.get(url, headers=HEADERS, timeout=15)
        if r.ok:
            return r.json().get("hits", {}).get("total", {}).get("value", 0)
    except: pass
    return 0

# ═══ INVERSION ANALYSIS ═══
def inversion_analysis(signals):
    """Contrarian detector: when consensus is extreme, flag reversal risk."""
    if not signals: return {"warning": None, "consensus": "NONE"}
    bull = sum(1 for s in signals.values() if s.get("direction") == "BUY")
    pct = bull / len(signals)
    if pct > 0.8:
        return {"warning": "EXTREME_BULL — watch for reversal", "consensus": "BULL",
                "action": "Tighten stops. Reduce sizes 20%."}
    elif pct < 0.2:
        return {"warning": "EXTREME_BEAR — watch for bottom", "consensus": "BEAR",
                "action": "Look for capitulation volume. Scale in slowly."}
    return {"warning": None, "consensus": "MIXED"}

# ═══ PERFORMANCE TRACKER ═══
class PerformanceTracker:
    def __init__(self):
        try:
            with open(PREDICTION_LOG) as f: self.log = json.load(f)
        except: self.log = {"predictions": [], "stats": {}}
    def _save(self):
        with open(PREDICTION_LOG, "w") as f: json.dump(self.log, f, indent=2, default=str)
    def record(self, ticker, direction, score, price):
        self.log["predictions"].append({"ts": datetime.now(timezone.utc).isoformat(),
            "ticker": ticker, "direction": direction, "score": score, "entry_price": price,
            "exit_price": None, "result": None})
        self.log["predictions"] = self.log["predictions"][-500:]
        self._save()
    def get_stats(self):
        resolved = [p for p in self.log["predictions"] if p.get("result")]
        if not resolved: return {"total": 0, "hit_rate": 0}
        wins = sum(1 for p in resolved if p["result"] == "WIN")
        return {"total": len(resolved), "wins": wins, "losses": len(resolved)-wins,
                "hit_rate": round(wins/len(resolved)*100, 1)}

# ═══ MASTER REPORT ═══
def intel_report():
    """Combined intelligence + sentiment briefing."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"[INTEL+SENTIMENT] {ts}"]

    # Fear & Greed
    fg = get_fear_greed()
    lines.append(f"  Fear/Greed: {fg['value']} ({fg['label']})")
    if fg["value"] < 25:   lines.append("  >> EXTREME FEAR — contrarian BUY signal")
    elif fg["value"] > 75: lines.append("  >> EXTREME GREED — consider taking profits")

    # Market news sentiment
    news = get_market_news()
    if news:
        sent, ratio = analyze_headlines(news)
        lines.append(f"  News sentiment: {sent} ({ratio:.0%} bull)")
    crypto_news = get_market_news("crypto")
    if crypto_news:
        cs, cr = analyze_headlines(crypto_news)
        lines.append(f"  Crypto news: {cs} ({cr:.0%} bull)")

    # SEC insider buys
    insider_count = get_insider_buys(14)
    lines.append(f"  SEC insider buys (14d): {insider_count} filings")

    # Social sentiment (key tickers)
    for symbol in ["AAPL", "TSLA", "NVDA", "COIN"]:
        try:
            data = get_social_sentiment(symbol)
            if data:
                reddit = data.get("reddit", [])
                r_mentions = sum(d.get("mention", 0) for d in reddit[-7:]) if reddit else 0
                if r_mentions > 0:
                    lines.append(f"  {symbol} social: {r_mentions} Reddit mentions (7d)")
        except: pass

    # Performance tracking
    tracker = PerformanceTracker()
    stats = tracker.get_stats()
    if stats["total"] > 0:
        lines.append(f"  Track record: {stats['hit_rate']:.1f}% ({stats['wins']}W/{stats['losses']}L)")

    return "\n".join(lines)

if __name__ == "__main__":
    print(intel_report())
