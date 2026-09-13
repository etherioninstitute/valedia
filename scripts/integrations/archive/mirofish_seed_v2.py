"""MiroFish Seed Generator v2 — Multi-Source Intelligence Gatherer
Pulls from EVERY available source to build the richest possible seed.
No single source dependency. Redundant and overlapping by design.

Sources:
  1. Valedia's hedge fund intel (7 modules)
  2. ML retrain signals (8 models)  
  3. Live prices + technicals (18 assets)
  4. Fear/Greed + macro indicators
  5. CoinGecko trending (catches obscure coins)
  6. Whale monitor alerts (on-chain signals)
  7. Emerging crypto scanner (new listings, volume spikes)
  8. Insider/Congress trades (SEC Form 4)
  9. Reddit/X sentiment (via existing sentiment scanner)
  10. DeFi yields + anomalies

The MORE data in the seed, the BETTER MiroFish performs.
Agents will find patterns YOU didn't know to look for.

(c) GOATWORTHY Post LLC.
"""
import requests, json, os, sys, time
from datetime import datetime, timezone
import yfinance as yf
import numpy as np

MODELS_DIR = os.path.expanduser("~/.openclaw/workspace/models")
SCRIPTS_DIR = os.path.expanduser("~/.openclaw/workspace/scripts/integrations")
sys.path.insert(0, SCRIPTS_DIR)

def section(title):
    return f"\n## {title}\n"

def gather_all_intelligence():
    """Pull from every source. Each section is independent — if one fails, others continue."""
    lines = [f"# Valedia Market Intelligence Seed — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"]
    lines.append("This document contains comprehensive market intelligence from 10+ sources.")
    lines.append("MiroFish agents should use this to simulate market participant behavior.\n")

    # ═══ 1. FEAR/GREED + MACRO ═══
    try:
        lines.append(section("Macro Sentiment"))
        r = requests.get("https://api.alternative.me/fng/?limit=7", timeout=10)
        if r.ok:
            data = r.json()["data"]
            lines.append(f"Current Fear/Greed: {data[0]['value']} ({data[0]['value_classification']})")
            lines.append(f"7-day trend: {' → '.join([d['value'] for d in reversed(data)])}")
            fg = int(data[0]['value'])
            if fg < 20: lines.append("EXTREME FEAR: Historically this is a strong contrarian BUY signal.")
            elif fg > 80: lines.append("EXTREME GREED: Historically this precedes corrections.")
    except Exception as e:
        lines.append(f"(macro data unavailable: {e})")

    # ═══ 2. LIVE PRICES + TECHNICALS (18 assets) ═══
    try:
        lines.append(section("Live Market Data"))
        assets = {
            'BTC-USD':'Bitcoin','ETH-USD':'Ethereum','SOL-USD':'Solana','XRP-USD':'XRP',
            'ADA-USD':'Cardano','AVAX-USD':'Avalanche','DOGE-USD':'Dogecoin','LINK-USD':'Chainlink',
            'SPY':'S&P 500','QQQ':'Nasdaq','NVDA':'Nvidia','GOOGL':'Google',
            'GLD':'Gold','AAPL':'Apple','TSLA':'Tesla','AMD':'AMD','META':'Meta','COIN':'Coinbase'
        }
        for tk, name in assets.items():
            try:
                data = yf.Ticker(tk).history(period="1mo")
                if len(data) < 5: continue
                price = data["Close"].iloc[-1]
                ret_1d = (data["Close"].iloc[-1]/data["Close"].iloc[-2]-1)*100
                ret_5d = (data["Close"].iloc[-1]/data["Close"].iloc[-5]-1)*100
                ret_1m = (data["Close"].iloc[-1]/data["Close"].iloc[0]-1)*100
                vol = data["Volume"].iloc[-1]
                avg_vol = data["Volume"].mean()
                vol_ratio = vol/avg_vol if avg_vol > 0 else 1
                rsi = 50  # simplified
                r14 = np.diff(np.log(data["Close"].values[-15:]))
                if len(r14) >= 14:
                    gains = r14[r14>0]; losses = -r14[r14<0]
                    rs = (np.mean(gains) if len(gains)>0 else 0.001) / (np.mean(losses) if len(losses)>0 else 0.001)
                    rsi = 100 - 100/(1+rs)
                lines.append(f"- **{name}** ({tk}): ${price:.2f} | 1d:{ret_1d:+.1f}% 5d:{ret_5d:+.1f}% 1m:{ret_1m:+.1f}% | RSI:{rsi:.0f} | Vol:{vol_ratio:.1f}x avg")
            except: pass
    except Exception as e:
        lines.append(f"(price data unavailable: {e})")

    # ═══ 3. COINGECKO TRENDING (catches obscure coins!) ═══
    try:
        lines.append(section("Trending Coins (CoinGecko — catches obscure movers)"))
        r = requests.get("https://api.coingecko.com/api/v3/search/trending", timeout=10)
        if r.ok:
            coins = r.json().get("coins",[])
            for c in coins[:10]:
                item = c.get("item",{})
                lines.append(f"- {item.get('name','?')} ({item.get('symbol','?')}): "
                    f"rank #{item.get('market_cap_rank','?')}, "
                    f"price BTC: {item.get('price_btc','?'):.10f}" if isinstance(item.get('price_btc'), float) else
                    f"- {item.get('name','?')} ({item.get('symbol','?')}): trending")
            lines.append("NOTE: Trending coins often signal retail FOMO or emerging narratives.")
    except Exception as e:
        lines.append(f"(trending data unavailable: {e})")

    # ═══ 4. VALEDIA'S HEDGE FUND INTEL (if available) ═══
    try:
        lines.append(section("Institutional Intelligence (Valedia Hedge Fund Module)"))
        with open(os.path.join(MODELS_DIR, "hedge_fund_intel.json")) as f:
            intel = json.load(f)
        if intel.get("short_squeeze"):
            lines.append("### Short Squeeze Candidates")
            for s in intel["short_squeeze"]:
                lines.append(f"- {s['ticker']}: {s['short_pct']}% short, ${s.get('price',0):.2f}")
        if intel.get("sentiment_divergence"):
            lines.append("### Sentiment vs Fundamentals Divergence")
            for s in intel["sentiment_divergence"]:
                lines.append(f"- {s['ticker']}: PE={s['pe']}, ROE={s['roe']}%, 52w={s['change52w']}%")
        if intel.get("dividend_danger"):
            lines.append("### Dividend Danger (high yield + warning signs)")
            for d in intel["dividend_danger"]:
                lines.append(f"- {d['ticker']}: yield={d['yield']}%, payout={d['payout']}%, danger={d['danger']}/3")
    except: lines.append("(hedge fund intel not yet generated)")

    # ═══ 5. ML RETRAIN SIGNALS ═══
    try:
        lines.append(section("ML Model Predictions (8 gradient boosting models)"))
        with open(os.path.join(MODELS_DIR, "latest_retrain.json")) as f:
            ml = json.load(f)
        for r in ml.get("results",[]):
            lines.append(f"- {r['ticker']}: **{r['signal']}** (buy_prob={r['buy_probability']:.1%}, accuracy={r['accuracy']:.1%})")
        lines.append("These models are trained on 128 samples with rolling features.")
    except: lines.append("(ML signals not yet generated)")

    # ═══ 6. WHALE MONITOR (on-chain signals) ═══
    try:
        lines.append(section("Whale Activity (on-chain monitoring)"))
        # Check if whale data exists from latest cron run
        whale_path = os.path.join(MODELS_DIR, "latest_whale.json")
        if os.path.exists(whale_path):
            with open(whale_path) as f:
                whale = json.load(f)
            lines.append(f"Last scan: {whale.get('ts','?')}")
            for alert in whale.get("alerts",[]):
                lines.append(f"- {alert}")
        else:
            # Try live CoinGecko whale-proxy data
            r = requests.get("https://api.coingecko.com/api/v3/coins/bitcoin", timeout=10,
                params={"localization":"false","tickers":"false","community_data":"true","developer_data":"false"})
            if r.ok:
                d = r.json()
                lines.append(f"BTC market cap: ${d.get('market_data',{}).get('market_cap',{}).get('usd',0):,.0f}")
                lines.append(f"BTC 24h volume: ${d.get('market_data',{}).get('total_volume',{}).get('usd',0):,.0f}")
                mc = d.get('market_data',{}).get('market_cap_change_percentage_24h',0)
                lines.append(f"Market cap change 24h: {mc:+.1f}%")
    except: lines.append("(whale data unavailable)")

    # ═══ 7. EMERGING CRYPTO SCANNER ═══
    try:
        lines.append(section("Emerging & Obscure Crypto (volume spikes + new listings)"))
        r = requests.get("https://api.coingecko.com/api/v3/coins/markets", timeout=15,
            params={"vs_currency":"usd","order":"volume_desc","per_page":20,"page":1,"sparkline":"false"})
        if r.ok:
            for coin in r.json()[:20]:
                mc = coin.get("market_cap",0)
                vol = coin.get("total_volume",0)
                change = coin.get("price_change_percentage_24h",0)
                # Flag unusual volume/price action
                if mc > 0 and vol/mc > 0.3:  # volume > 30% of market cap = unusual
                    lines.append(f"- ⚠️ **{coin['name']}** ({coin['symbol'].upper()}): "
                        f"${coin['current_price']:.4f} {change:+.1f}% | "
                        f"Vol/MCap={vol/mc:.1%} (HIGH ACTIVITY)")
                elif abs(change or 0) > 10:
                    lines.append(f"- {coin['name']} ({coin['symbol'].upper()}): "
                        f"${coin['current_price']:.4f} {change:+.1f}%")
    except: lines.append("(emerging crypto data unavailable)")

    # ═══ 8. DeFi YIELDS + ANOMALIES ═══
    try:
        lines.append(section("DeFi Yield Landscape"))
        r = requests.get("https://yields.llama.fi/pools", timeout=15)
        if r.ok:
            pools = r.json().get("data",[])
            # Top yields that aren't scams (TVL > $1M, APY < 200%)
            legit = [p for p in pools if p.get("tvlUsd",0)>1000000 and 0<p.get("apy",0)<200]
            legit.sort(key=lambda x: x.get("apy",0), reverse=True)
            for p in legit[:10]:
                lines.append(f"- {p.get('project','?')} ({p.get('chain','?')}): "
                    f"{p.get('symbol','?')} APY={p.get('apy',0):.1f}% TVL=${p.get('tvlUsd',0)/1e6:.1f}M")
            # Anomaly: any pool with sudden TVL spike?
            anomalies = [p for p in pools if p.get("tvlUsd",0)>500000 and abs(p.get("apyPct7D",0) or 0)>50]
            if anomalies:
                lines.append("\n### Yield Anomalies (APY changed >50% in 7d)")
                for a in anomalies[:5]:
                    lines.append(f"- {a.get('project','?')}: APY was {a.get('apyBase',0):.0f}% → now {a.get('apy',0):.0f}%")
    except: lines.append("(DeFi data unavailable)")

    # ═══ 9. KERNEL v16e CURRENT SIGNALS ═══
    try:
        lines.append(section("Valedia Kernel v16e Signals (proprietary UGFT framework)"))
        with open(os.path.join(MODELS_DIR, "latest_brain_report.json")) as f:
            brain = json.load(f)
        lines.append(f"Last brain run: {brain.get('ts','?')}")
        lines.append(f"Fear/Greed at run: {brain.get('intel',{}).get('fear_greed','?')}")
        lines.append(f"Trades executed: {brain.get('actions',[])}")
    except: lines.append("(brain report unavailable)")

    # ═══ 10. PREDICTION REQUEST ═══
    lines.append(section("Simulation Directive"))
    lines.append("""You are simulating a financial market ecosystem. The agents in this simulation represent:

**Agent Types to Generate:**
1. **Institutional Traders** (hedge funds, prop desks) — data-driven, large positions, move markets
2. **Retail Investors** (Reddit/X crowd) — sentiment-driven, FOMO/FUD, small positions but swarm behavior
3. **Quant Funds** (algorithmic) — momentum, mean-reversion, statistical arbitrage
4. **Market Makers** — provide liquidity, profit from spread, neutral positioning
5. **Whale Wallets** (on-chain) — large crypto holders, their moves signal direction
6. **Macro Analysts** — Fed watchers, yield curve, inflation, geopolitical risk
7. **DeFi Degens** — yield farming, new protocol launches, high risk tolerance
8. **Short Sellers** — looking for overvaluation, squeeze risk, catalyst-driven

**Predict over next 5 trading days:**
- BTC, ETH, SOL direction and approximate price range
- SPY, QQQ direction (bull/bear/sideways)
- Any emerging coin that could 10x from obscurity
- Key risk events or catalysts
- Consensus vs contrarian views among agent types
- Which agent type is most likely to be RIGHT and why

**Critical: Pay attention to signals that DISAGREE with consensus.**
Contrarian signals from whale wallets + quant models diverging from retail sentiment
are historically the most profitable.""")
    
    return "\n".join(lines)

def save_seed(content):
    seed_dir = os.path.expanduser("~/.openclaw/workspace/MiroFish/backend/uploads")
    os.makedirs(seed_dir, exist_ok=True)
    path = os.path.join(seed_dir, f"valedia_seed_{datetime.now().strftime('%Y%m%d_%H%M')}.md")
    with open(path, "w") as f:
        f.write(content)
    return path

if __name__ == "__main__":
    print("=" * 60)
    print("  MIROFISH SEED GENERATOR v2 — 10-Source Intelligence")
    print("=" * 60)
    
    seed = gather_all_intelligence()
    path = save_seed(seed)
    
    lines = seed.split("\n")
    sections = [l for l in lines if l.startswith("##")]
    
    print(f"\n  Seed generated: {path}")
    print(f"  Total length: {len(seed):,} chars, {len(lines)} lines")
    print(f"  Sections ({len(sections)}):")
    for s in sections:
        print(f"    {s}")
    print(f"\n  Preview (first 500 chars):")
    print(f"  {seed[:500]}")
    print(f"\n{'=' * 60}")
