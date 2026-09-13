"""Hedge Fund Intelligence Module — Valedia ASI
7 institutional-grade research prompts automated.
Feeds results into trading brain as alpha signals.
(c) GOATWORTHY Post LLC.
"""
import requests, json, os, sys, re
from datetime import datetime, timezone, timedelta
sys.path.insert(0, "/Users/burtonchatelain/.openclaw/workspace/scripts/integrations")

REPORT_FILE = "/Users/burtonchatelain/.openclaw/workspace/models/hedge_fund_intel.json"

def _get(url, timeout=12):
    try: return requests.get(url, timeout=timeout, headers={"User-Agent":"Valedia-ASI research@goatworthy.com"})
    except: return None

# ═══ 1. SHORT SQUEEZE SCREENER ═══
def short_squeeze_scan():
    lines = ["[1] SHORT SQUEEZE SCREENER"]
    try:
        # Finviz-style: high short interest stocks
        # Using Yahoo Finance screener data
        import yfinance as yf
        candidates = ["GME","AMC","CVNA","MARA","RIOT","BYND","UPST","SOFI","HOOD","COIN"]
        results = []
        for tk in candidates:
            try:
                info = yf.Ticker(tk).info
                si = info.get("shortPercentOfFloat", 0)
                if si and si > 0.15:
                    results.append({
                        "ticker": tk, "short_pct": round(si*100,1),
                        "price": info.get("currentPrice",0),
                        "avg_vol": info.get("averageVolume",0),
                        "float": info.get("floatShares",0),
                    })
            except: pass
        results.sort(key=lambda x: x["short_pct"], reverse=True)
        for r in results[:5]:
            lines.append(f"  {r['ticker']}: {r['short_pct']}% short, ${r['price']:.2f}")
        if not results: lines.append("  No high-short candidates found")
    except Exception as e:
        lines.append(f"  Error: {e}")
    return "\n".join(lines), results[:5] if 'results' in dir() else []

# ═══ 2. SENTIMENT vs FUNDAMENTALS ARBITRAGE ═══
def sentiment_fundamental_divergence():
    lines = ["[2] SENTIMENT vs FUNDAMENTALS DIVERGENCE"]
    try:
        import yfinance as yf
        # Stocks beaten down but fundamentally strong
        candidates = ["INTC","PYPL","DIS","BA","NKE","PFE","BABA","SNAP","WBD","PLTR"]
        ideas = []
        for tk in candidates:
            try:
                info = yf.Ticker(tk).info
                pe = info.get("forwardPE",0)
                pb = info.get("priceToBook",0)
                roe = info.get("returnOnEquity",0)
                change52 = info.get("52WeekChange",0)
                # Divergence: beaten down (negative 52w change) but cheap PE + positive ROE
                if change52 and change52 < -0.1 and pe and 0 < pe < 20 and roe and roe > 0.05:
                    ideas.append({"ticker":tk,"pe":round(pe,1),"roe":round(roe*100,1),
                                  "change52w":round(change52*100,1),"price":info.get("currentPrice",0)})
            except: pass
        for i in ideas[:6]:
            lines.append(f"  {i['ticker']}: PE={i['pe']} ROE={i['roe']}% 52w={i['change52w']}%")
        if not ideas: lines.append("  No divergence candidates found")
    except Exception as e:
        lines.append(f"  Error: {e}")
    return "\n".join(lines), ideas[:6] if 'ideas' in dir() else []

# ═══ 3. DIVIDEND DANGER RADAR ═══
def dividend_danger():
    lines = ["[3] DIVIDEND DANGER RADAR (>5% yield with warning signs)"]
    try:
        import yfinance as yf
        candidates = ["T","VZ","MO","PM","KHC","LYB","DOW","LUMN","MPW","OHI","AGNC","ET"]
        dangers = []
        for tk in candidates:
            try:
                info = yf.Ticker(tk).info
                div_yield = info.get("dividendYield",0)
                payout = info.get("payoutRatio",0)
                debt_eq = info.get("debtToEquity",0)
                if div_yield and div_yield > 0.05:
                    danger_score = 0
                    if payout and payout > 0.8: danger_score += 1
                    if debt_eq and debt_eq > 200: danger_score += 1
                    fcf = info.get("freeCashflow",0)
                    if fcf and fcf < 0: danger_score += 1
                    if danger_score >= 1:
                        dangers.append({"ticker":tk,"yield":round(div_yield*100,1),
                                        "payout":round(payout*100,1) if payout else "?",
                                        "debt_eq":round(debt_eq,0) if debt_eq else "?",
                                        "danger":danger_score})
            except: pass
        dangers.sort(key=lambda x: x["danger"], reverse=True)
        for d in dangers[:5]:
            lines.append(f"  {d['ticker']}: yield={d['yield']}% payout={d['payout']}% D/E={d['debt_eq']} danger={d['danger']}/3")
    except Exception as e:
        lines.append(f"  Error: {e}")
    return "\n".join(lines), dangers[:5] if 'dangers' in dir() else []

# ═══ 4. CRISIS CORRELATION MAP ═══
def crisis_correlations():
    lines = ["[4] CRISIS CORRELATION MAP (unusual correlations)"]
    try:
        import yfinance as yf
        import numpy as np
        assets = {"SPY":"Stocks","GLD":"Gold","TLT":"Bonds","UUP":"Dollar",
                  "BTC-USD":"Bitcoin","USO":"Oil","VIX":"Vol"}
        prices = {}
        for tk, name in assets.items():
            try:
                p = yf.Ticker(tk).history(period="3mo")["Close"].values
                if len(p) > 20: prices[name] = np.diff(np.log(p))[-20:]
            except: pass
        # Compute correlation matrix
        names = list(prices.keys())
        n = len(names)
        for i in range(n):
            for j in range(i+1, n):
                if len(prices[names[i]]) == len(prices[names[j]]):
                    corr = np.corrcoef(prices[names[i]], prices[names[j]])[0,1]
                    # Flag unusual: gold-stocks positive, bonds-stocks positive
                    unusual = False
                    if names[i]=="Gold" and names[j]=="Stocks" and corr > 0.3: unusual=True
                    if names[i]=="Bonds" and names[j]=="Stocks" and corr > 0.3: unusual=True
                    if "Bitcoin" in [names[i],names[j]] and "Gold" in [names[i],names[j]] and abs(corr)>0.5: unusual=True
                    if unusual:
                        lines.append(f"  UNUSUAL: {names[i]}/{names[j]} corr={corr:.2f}")
        if len(lines) == 1: lines.append("  No unusual correlations detected (normal regime)")
    except Exception as e:
        lines.append(f"  Error: {e}")
    return "\n".join(lines)

# ═══ 5. INSTITUTIONAL POSITIONING (13F proxy) ═══
def institutional_positioning():
    lines = ["[5] INSTITUTIONAL POSITIONING (proxy via ETF flows)"]
    try:
        import yfinance as yf
        # Track sector ETF volume spikes as proxy for institutional moves
        etfs = {"XLK":"Tech","XLF":"Finance","XLE":"Energy","XLV":"Health",
                "XLI":"Industrial","XLP":"Staples","XLU":"Utilities","XLRE":"RealEstate"}
        for tk, sector in etfs.items():
            try:
                data = yf.Ticker(tk).history(period="1mo")
                if len(data) < 10: continue
                avg_vol = data["Volume"].mean()
                recent_vol = data["Volume"].iloc[-3:].mean()
                ret_1m = (data["Close"].iloc[-1]/data["Close"].iloc[0]-1)*100
                flow = "ACCUMULATING" if recent_vol > avg_vol*1.3 and ret_1m > 0 else \
                       "DISTRIBUTING" if recent_vol > avg_vol*1.3 and ret_1m < 0 else "NEUTRAL"
                if flow != "NEUTRAL":
                    lines.append(f"  {sector} ({tk}): {flow} vol={recent_vol/avg_vol:.1f}x ret={ret_1m:+.1f}%")
            except: pass
        if len(lines) == 1: lines.append("  No significant institutional signals")
    except Exception as e:
        lines.append(f"  Error: {e}")
    return "\n".join(lines)

# ═══ 6. TOP-DOWN MACRO ═══
def macro_analysis():
    lines = ["[6] TOP-DOWN MACRO ANALYSIS"]
    try:
        # Fear/Greed as macro proxy
        r = _get("https://api.alternative.me/fng/?limit=1")
        if r and r.ok:
            fg = r.json()["data"][0]
            lines.append(f"  Fear/Greed: {fg['value']} ({fg['value_classification']})")
        # Treasury yields proxy via TLT
        import yfinance as yf
        tlt = yf.Ticker("TLT").history(period="3mo")["Close"].values
        if len(tlt) > 20:
            tlt_ret = (tlt[-1]/tlt[0]-1)*100
            lines.append(f"  Bond trend (TLT 3mo): {tlt_ret:+.1f}% ({'rates falling' if tlt_ret>0 else 'rates rising'})")
        # DXY proxy via UUP
        uup = yf.Ticker("UUP").history(period="1mo")["Close"].values
        if len(uup) > 5:
            uup_ret = (uup[-1]/uup[0]-1)*100
            lines.append(f"  Dollar (UUP 1mo): {uup_ret:+.1f}% ({'strong' if uup_ret>0 else 'weak'})")
        # Sector recommendation based on macro
        fg_val = int(fg["value"]) if r and r.ok else 50
        if fg_val < 25:
            lines.append(f"  MACRO SIGNAL: Extreme fear -> historically bullish for risk assets")
            lines.append(f"  Favored: Value stocks, beaten-down tech, crypto")
        elif fg_val > 75:
            lines.append(f"  MACRO SIGNAL: Extreme greed -> historically bearish")
            lines.append(f"  Favored: Defensives, gold, utilities, cash")
    except Exception as e:
        lines.append(f"  Error: {e}")
    return "\n".join(lines)

# ═══ 7. PORTFOLIO HEDGING STRATEGY ═══
def hedging_strategy():
    lines = ["[7] PORTFOLIO HEDGING STRATEGY"]
    try:
        import yfinance as yf
        import numpy as np
        # Check VIX level
        vix = yf.Ticker("^VIX").history(period="1mo")["Close"].values
        if len(vix) > 0:
            vix_now = vix[-1]
            vix_avg = np.mean(vix)
            lines.append(f"  VIX: {vix_now:.1f} (1mo avg: {vix_avg:.1f})")
            if vix_now > 25:
                lines.append(f"  HIGH VOL: Hedging expensive. Use inverse ETFs over options.")
                lines.append(f"  Recommended: SH (inverse SPY), SQQQ (3x inverse QQQ)")
            elif vix_now < 15:
                lines.append(f"  LOW VOL: Cheap protection available.")
                lines.append(f"  Recommended: Put options on SPY (30 delta, 60 DTE)")
            else:
                lines.append(f"  MODERATE VOL: Collar strategy optimal.")
        # Hedge sizing
        lines.append(f"  Hedge size recommendation: 5-10% of portfolio in protective positions")
    except Exception as e:
        lines.append(f"  Error: {e}")
    return "\n".join(lines)

# ═══ MASTER REPORT ═══
def generate_full_report():
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"{'='*60}", f"  HEDGE FUND INTELLIGENCE REPORT — {ts}", f"{'='*60}", ""]
    
    # Run all 7 modules
    short_report, short_data = short_squeeze_scan()
    lines.append(short_report); lines.append("")
    
    sent_report, sent_data = sentiment_fundamental_divergence()
    lines.append(sent_report); lines.append("")
    
    div_report, div_data = dividend_danger()
    lines.append(div_report); lines.append("")
    
    lines.append(crisis_correlations()); lines.append("")
    lines.append(institutional_positioning()); lines.append("")
    lines.append(macro_analysis()); lines.append("")
    lines.append(hedging_strategy()); lines.append("")
    
    lines.append(f"{'='*60}")
    
    # Save structured data for brain consumption
    report = {
        "ts": ts,
        "short_squeeze": short_data,
        "sentiment_divergence": sent_data,
        "dividend_danger": div_data,
    }
    try:
        with open(REPORT_FILE, "w") as f:
            json.dump(report, f, indent=2, default=str)
    except: pass
    
    return "\n".join(lines)

if __name__ == "__main__":
    print(generate_full_report())
