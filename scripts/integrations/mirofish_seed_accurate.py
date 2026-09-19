#!/usr/bin/env python3
"""MiroFish accurate seed builder — API-first, entity-dense.

Replaces the flaky yfinance-based seed. Pulls live market intelligence from
reliable APIs (CoinGecko, Yahoo chart API, alternative.me, Polymarket, CoinDesk
RSS) and writes a RICH, entity-dense markdown seed so the ontology extracts many
named entities (assets, companies, events, participant cohorts) → a real
multi-agent MiroFish simulation instead of a degenerate 1-agent one.

Design: each source is independent (try/except); partial data still yields a
usable seed. No cloud LLM, no yfinance, no external cron-output dependencies.
"""
import os, json, time, re
from datetime import datetime, timezone
import requests
import xml.etree.ElementTree as ET

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Valedia/1.0"}
SEED_DIR = os.path.expanduser("~/.openclaw/workspace/mirofish_seeds")

# Named universes — explicitly named so the ontology extracts each as an entity.
CRYPTO_IDS = ["bitcoin", "ethereum", "solana", "ripple", "binancecoin", "dogecoin", "cardano", "avalanche-2", "chainlink"]
EQUITIES = [
    ("SPY", "S&P 500 ETF"), ("QQQ", "Nasdaq-100 ETF"), ("DIA", "Dow Jones ETF"), ("IWM", "Russell 2000 ETF"),
    ("NVDA", "Nvidia"), ("AAPL", "Apple"), ("MSFT", "Microsoft"), ("GOOGL", "Alphabet"),
    ("TSLA", "Tesla"), ("META", "Meta Platforms"), ("AMZN", "Amazon"), ("COIN", "Coinbase Global"),
    ("MSTR", "MicroStrategy"), ("GLD", "Gold ETF"),
]


def _get(url, **kw):
    kw.setdefault("timeout", 15); kw.setdefault("headers", UA)
    return requests.get(url, **kw)


def crypto_section():
    out = ["## Cryptocurrency Market (live, CoinGecko)"]
    r = _get("https://api.coingecko.com/api/v3/coins/markets", params={
        "vs_currency": "usd", "ids": ",".join(CRYPTO_IDS),
        "order": "market_cap_desc", "price_change_percentage": "24h,7d"})
    for c in r.json():
        name, sym = c.get("name", "?"), (c.get("symbol", "?") or "").upper()
        px = c.get("current_price", 0) or 0
        d1 = c.get("price_change_percentage_24h", 0) or 0
        d7 = c.get("price_change_percentage_7d_in_currency", 0) or 0
        mc = (c.get("market_cap", 0) or 0) / 1e9
        athg = c.get("ath_change_percentage", 0) or 0
        vol = (c.get("total_volume", 0) or 0) / 1e9
        mood = "bullish momentum" if d7 > 5 else "bearish pressure" if d7 < -5 else "consolidating"
        out.append(
            f"- **{name} ({sym})** trades at ${px:,.2f}, {d1:+.1f}% 24h and {d7:+.1f}% 7d — {mood}. "
            f"Market cap ${mc:.1f}B, ${vol:.1f}B 24h volume, {athg:+.0f}% from all-time high. "
            f"{'Retail and institutional flows are watching this level closely.' if abs(d1) > 4 else 'Trading in-range.'}")
    return out


def trending_section():
    out = ["## Trending / Emerging Narratives (CoinGecko trending)"]
    r = _get("https://api.coingecko.com/api/v3/search/trending")
    for it in r.json().get("coins", [])[:8]:
        i = it.get("item", {})
        out.append(f"- **{i.get('name','?')} ({(i.get('symbol','') or '').upper()})** is trending "
                   f"(rank #{i.get('market_cap_rank','?')}) — often a signal of retail FOMO or an emerging narrative.")
    return out


def equity_section():
    out = ["## Equities & Indices (live, Yahoo Finance)"]
    for tk, name in EQUITIES:
        try:
            r = _get(f"https://query1.finance.yahoo.com/v8/finance/chart/{tk}",
                     params={"range": "1mo", "interval": "1d"})
            res = r.json()["chart"]["result"][0]
            meta = res["meta"]
            px = meta.get("regularMarketPrice", 0) or 0
            closes = [c for c in res["indicators"]["quote"][0]["close"] if c is not None]
            d1 = (closes[-1] / closes[-2] - 1) * 100 if len(closes) >= 2 else 0
            d5 = (closes[-1] / closes[-6] - 1) * 100 if len(closes) >= 6 else 0
            d1m = (closes[-1] / closes[0] - 1) * 100 if len(closes) >= 2 else 0
            trend = "uptrend" if d1m > 3 else "downtrend" if d1m < -3 else "range-bound"
            out.append(f"- **{name} ({tk})** at ${px:,.2f}: {d1:+.1f}% 1d, {d5:+.1f}% 5d, {d1m:+.1f}% 1mo — {trend}.")
        except Exception:
            continue
    return out


def sentiment_section():
    out = ["## Market Sentiment (Fear & Greed)"]
    r = _get("https://api.alternative.me/fng/?limit=7")
    data = r.json()["data"]
    cur = data[0]
    trend = " → ".join(d["value"] for d in reversed(data))
    fg = int(cur["value"])
    signal = ("EXTREME FEAR — historically a contrarian buy zone" if fg < 25 else
              "EXTREME GREED — historically precedes corrections" if fg > 75 else
              "neutral-to-mixed positioning")
    out.append(f"Crypto Fear & Greed Index is **{cur['value']} ({cur['value_classification']})**; "
               f"7-day trend {trend}. Interpretation: {signal}. This drives how retail cohorts and "
               f"momentum traders position versus contrarian institutions.")
    return out


def predictions_section():
    out = ["## Prediction-Market Expectations (Polymarket — what the crowd is pricing)"]
    try:
        r = _get("https://gamma-api.polymarket.com/markets",
                 params={"closed": "false", "order": "volume24hr", "ascending": "false", "limit": 10})
        mk = r.json()
        mk = mk if isinstance(mk, list) else mk.get("data", [])
        for m in mk[:8]:
            q = m.get("question") or m.get("title") or "?"
            vol = float(m.get("volume24hr", 0) or 0) / 1e3
            out.append(f"- \"{q}\" — ${vol:.0f}K 24h volume. The market is actively pricing this event.")
    except Exception as e:
        out.append(f"(prediction-market data unavailable: {str(e)[:60]})")
    if len(out) == 1:
        out.append("(no active high-volume markets retrieved)")
    return out


def kalshi_section():
    out = ["## Kalshi Event Markets (Valedia's own venue — regulated US event contracts)"]
    try:
        r = _get("https://api.elections.kalshi.com/trade-api/v2/markets",
                 params={"limit": 200, "status": "open"})
        mk = [m for m in r.json().get("markets", []) if (m.get("volume_24h") or 0) > 0]
        mk.sort(key=lambda m: m.get("volume_24h", 0) or 0, reverse=True)
        for m in mk[:10]:
            title = m.get("title") or m.get("ticker", "?")
            sub = m.get("subtitle", "") or m.get("yes_sub_title", "")
            last = m.get("last_price")  # cents, 0-100 == implied probability
            implied = f"{int(last)}% implied" if isinstance(last, (int, float)) and last else "thin/no last trade"
            vol = m.get("volume_24h", 0) or 0
            out.append(f"- **{title}{' — ' + sub if sub else ''}**: {implied} (Kalshi), {vol:,} contracts 24h. "
                       f"This is a live tradable event on Valedia's venue.")
    except Exception as e:
        out.append(f"(Kalshi data unavailable: {str(e)[:60]})")
    if len(out) == 1:
        out.append("(no active high-volume Kalshi markets retrieved)")
    return out


def news_section():
    out = ["## News Catalysts (headlines drive named events)"]
    feeds = [("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
             ("Cointelegraph", "https://cointelegraph.com/rss")]
    for src, url in feeds:
        try:
            r = _get(url)
            root = ET.fromstring(r.content)
            items = root.findall(".//item")[:5]
            for it in items:
                title = (it.findtext("title") or "").strip()
                if title:
                    out.append(f"- [{src}] {title}")
        except Exception:
            continue
    if len(out) == 1:
        out.append("(news feeds unavailable this run)")
    return out


def build_seed():
    lines = [f"# Live Market Intelligence Briefing — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
             "",
             "Comprehensive real-time market intelligence for multi-agent simulation. "
             "The named assets, companies, events, and participant cohorts below are the entities "
             "whose interactions should be simulated.",
             ""]
    for fn in (sentiment_section, crypto_section, trending_section, equity_section, predictions_section, kalshi_section, news_section):
        try:
            lines += fn() + [""]
        except Exception as e:
            lines.append(f"(section {fn.__name__} failed: {str(e)[:80]})\n")

    # Named participant cohorts — so the sim generates DIVERSE agents, not just asset-companies.
    lines += [
        "## Market Participants to Simulate",
        "Simulate the interactions and reactions of these distinct participant cohorts:",
        "- **Institutional traders** — large funds rebalancing on macro and flows.",
        "- **Retail investors** — sentiment-driven, reacting to Fear/Greed and social narratives.",
        "- **Quant funds** — systematic, momentum and mean-reversion signals.",
        "- **Market makers** — providing liquidity, sensitive to volatility and order flow.",
        "- **Crypto whales** — large on-chain holders whose moves shift markets.",
        "- **Crypto-native retail** — high-beta, narrative- and FOMO-driven.",
        "",
        "## Prediction Request",
        "Given the above conditions, simulate how these participant cohorts react over the next "
        "5 trading days. Focus on BTC, ETH, SOL, SPY, QQQ, and NVDA direction; key support/resistance; "
        "sentiment shifts; and catalysts that could trigger a regime change.",
    ]
    text = "\n".join(lines)
    try:
        import mirofish_neural_enrich as mne
        text = mne.apply_seed_text(text)
    except Exception:
        pass
    try:
        import persona_lenses as pl
        sig_path = os.path.expanduser("~/.openclaw/workspace/models/mirofish_signal.json")
        sig = {}
        if os.path.isfile(sig_path):
            with open(sig_path) as f:
                sig = json.load(f) or {}
        text = pl.apply_seed_text(text, signal_doc=sig)
    except Exception:
        pass
    return text


def generate_seed():
    os.makedirs(SEED_DIR, exist_ok=True)
    text = build_seed()
    path = os.path.join(SEED_DIR, f"market_seed_{datetime.now().strftime('%Y%m%d_%H%M')}.md")
    with open(path, "w") as f:
        f.write(text)
    return path, text


if __name__ == "__main__":
    p, t = generate_seed()
    print(f"Seed written: {p}")
    print(f"Length: {len(t)} chars, {t.count(chr(10))} lines")
    print("=" * 60)
    print(t)
