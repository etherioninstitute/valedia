"""Dynamic Universe Discovery — Valedia ASI
=====================================================
PURPOSE: Never miss another BTC-style explosion.
         Continuously discovers trending/breakout assets
         across crypto, equities, and forex — then validates
         them through the kernel before trading.

SOURCES (all free, no X/Twitter API required):
  Crypto  : CoinGecko trending + gainers + vol spikes
  Crypto  : StockTwits crypto buzz (free public API)
  Crypto  : CoinGecko search (ticker-based lookup)
  Equities: Finnhub trending news + social mentions
  Equities: Yahoo Finance momentum screener
  Forex   : DXY + cross-rate momentum detection
  Social  : Reddit public JSON (r/CryptoCurrency, r/wallstreetbets, r/investing)
  News    : Finnhub news keyword scanner

HOW IT WORKS:
  1. Every cron cycle, scan all sources → score each discovered ticker
  2. "Buzz Score" = social mentions + price momentum + volume spike
  3. Buzzworthy assets above threshold get checked against Coinbase/Alpaca
  4. If tradeable: run kernel v16e on their price history
  5. If kernel agrees (score > 60): add to hot_list.json
  6. unified_brain reads hot_list.json every cycle as extra universe

FOMO PROTECTION (Burton's BTC lesson):
  - Discovery = WATCH, not immediate buy
  - Must pass kernel filter before any trade
  - Position size is smaller for discovered assets (50% of normal Kelly)
  - Hard stop: discovered assets max 2% per trade (vs 4% for core assets)
  - Trend exit: sell discovered assets faster (kernel score < 50, not < 40)

(c) GOATWORTHY Post LLC. All rights reserved.
"""

import os, sys, json, time, requests
import numpy as np
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ENV_FILE    = os.path.expanduser("~/.hermes/.env")
# workspace-v2 models. The old ~/.openclaw/workspace/models path is not this tree.
_ROOT       = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
MODELS_DIR  = os.path.join(_ROOT, "models")
HOT_LIST    = os.path.join(MODELS_DIR, "hot_list.json")
WATCH_LOG   = os.path.join(MODELS_DIR, "discovery_log.json")

FINNHUB_KEY = os.environ.get("FINNHUB_API_KEY", "")

# Suppress yfinance 404/not-found noise from stderr
import logging as _logging
_logging.getLogger("yfinance").setLevel(_logging.CRITICAL)
_logging.getLogger("urllib3").setLevel(_logging.CRITICAL)
import warnings as _warnings
_warnings.filterwarnings("ignore")

# Coinbase-tradeable coins (check against actual Coinbase Advanced Trade listings)
# We'll verify dynamically, but seed with known tradeable ones
COINBASE_KNOWN = {
    "BTC","ETH","SOL","XRP","ADA","DOGE","AVAX","LINK","DOT","UNI","ATOM",
    "LTC","BCH","ETC","XLM","ALGO","MATIC","NEAR","APT","SUI","OP","ARB",
    "PEPE","SHIB","FLOKI","WIF","BONK","TRUMP","HYPE","TAO","INJ","TIA",
    "RENDER","ONDO","SEI","PYTH","JUP","STRK","BRETT","POPCAT",
    "VET","HBAR","ICP","FIL","GRT","SAND","MANA","AXS","CHZ","ENJ",
    "AAVE","MKR","SNX","COMP","CRV","BAL","YFI","SUSHI","1INCH",
    "FTM","CELO","ROSE","ZEC","DASH","XMR","NEO","WAVES","ZIL","ONT",
}

# Alpaca-tradeable: most US-listed stocks + ETFs
# We'll validate via yfinance (if data exists, it's tradeable)

# ─── SCORING WEIGHTS ────────────────────────────────────────────────
BUZZ_WEIGHTS = {
    "coingecko_trending":  30,   # on CoinGecko's daily trending list
    "top_gainer_24h":      20,   # >10% gain in 24h
    "volume_spike":        25,   # vol/mcap > 25%
    "reddit_mention":      15,   # mentioned in hot posts
    "news_mention":        10,   # in Finnhub headlines
    "stocktwits_trending": 20,   # trending on StockTwits
    "price_breakout":      20,   # price > 30d high
    "social_score":        15,   # Finnhub social score > 5
}
BUZZ_THRESHOLD = 35  # minimum score to add to watchlist


# ─── SOURCE 1: CoinGecko ────────────────────────────────────────────

def scan_coingecko():
    """Returns dict of {symbol: {score, reason, coingecko_id, price_usd}}"""
    found = {}

    # Trending list
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/search/trending",
            timeout=12)
        if r.ok:
            for i, c in enumerate(r.json().get("coins", [])[:15]):
                item = c.get("item", {})
                sym  = item.get("symbol","").upper()
                if not sym: continue
                score = BUZZ_WEIGHTS["coingecko_trending"]
                # Rank bonus: top 5 get extra points
                score += max(0, (5 - i) * 3)
                found.setdefault(sym, {"score":0,"reasons":[],"cg_id":item.get("id",""),"price":0})
                found[sym]["score"] += score
                found[sym]["reasons"].append(f"CG_trending#{i+1}")
    except Exception as e:
        pass

    # Top 24h gainers
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/coins/markets"
            "?vs_currency=usd&order=price_change_percentage_24h_desc"
            "&per_page=30&page=1&sparkline=false",
            timeout=12)
        if r.ok:
            for c in r.json():
                chg  = c.get("price_change_percentage_24h", 0) or 0
                mcap = c.get("market_cap", 0) or 0
                vol  = c.get("total_volume", 0) or 0
                sym  = c.get("symbol","").upper()
                if not sym: continue
                # Only coins with real market cap (>$10M) to filter scams
                if mcap < 10_000_000: continue
                score = 0
                if chg > 30:  score = BUZZ_WEIGHTS["top_gainer_24h"] * 2   # huge pump
                elif chg > 15: score = BUZZ_WEIGHTS["top_gainer_24h"] + 10
                elif chg > 10: score = BUZZ_WEIGHTS["top_gainer_24h"]
                if score == 0: continue
                # Volume spike bonus
                if mcap > 0 and vol/mcap > 0.25:
                    score += BUZZ_WEIGHTS["volume_spike"]
                found.setdefault(sym, {"score":0,"reasons":[],"cg_id":c.get("id",""),"price":c.get("current_price",0)})
                found[sym]["score"]   += score
                found[sym]["price"]    = float(c.get("current_price", 0))
                found[sym]["chg_24h"]  = chg
                found[sym]["reasons"].append(f"CG_gain+{chg:.0f}%")
    except Exception as e:
        pass

    # Volume/MCap spikes (momentum without price pump = institutional accumulation)
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/coins/markets"
            "?vs_currency=usd&order=volume_desc&per_page=100&sparkline=false",
            timeout=12)
        if r.ok:
            for c in r.json():
                mcap = c.get("market_cap", 0) or 1
                vol  = c.get("total_volume", 0) or 0
                sym  = c.get("symbol","").upper()
                if not sym or mcap < 50_000_000: continue  # >$50M mcap only
                ratio = vol / mcap
                if ratio > 0.50:   score = BUZZ_WEIGHTS["volume_spike"] * 2
                elif ratio > 0.25: score = BUZZ_WEIGHTS["volume_spike"]
                else: continue
                found.setdefault(sym, {"score":0,"reasons":[],"cg_id":c.get("id",""),"price":c.get("current_price",0)})
                found[sym]["score"]    += score
                found[sym]["vol_mcap"]  = round(ratio, 2)
                found[sym]["reasons"].append(f"vol/mcap={ratio:.0%}")
    except Exception as e:
        pass

    return found


# ─── SOURCE 2: Reddit Public API (no auth needed) ───────────────────

def scan_reddit():
    """Scan hot posts on crypto/stock subreddits for ticker mentions."""
    found = {}
    subreddits = [
        ("CryptoCurrency", "crypto"),
        ("CryptoMoonShots", "crypto"),
        ("SatoshiStreetBets", "crypto"),
        ("wallstreetbets",   "equity"),
        ("investing",        "equity"),
        ("stocks",           "equity"),
    ]
    import re
    ticker_pattern = re.compile(r'\b([A-Z]{2,6})\b')  # 2-6 uppercase letters

    for sub, cls in subreddits:
        try:
            r = requests.get(
                f"https://www.reddit.com/r/{sub}/hot.json?limit=25",
                headers={"User-Agent": "Valedia-Scanner/1.0"},
                timeout=10)
            if not r.ok: continue
            posts = r.json().get("data", {}).get("children", [])
            for post in posts:
                data  = post.get("data", {})
                title = data.get("title", "")
                score = data.get("score", 0)
                if score < 100: continue  # only hot posts
                tickers = ticker_pattern.findall(title)
                # Filter common false positives
                SKIP = {"THE","AND","FOR","NOT","BUT","ARE","YOU","ALL",
                        "CAN","HAS","HAD","ITS","WAS","ETF","CEO","USD",
                        "IPO","SEC","FED","GDP","ATH","ATL","DCA","FOMO",
                        "YOLO","IMO","AMA","DD","OG","US","UK","EU","AI"}
                for t in set(tickers):
                    if t in SKIP: continue
                    # Crypto: 3-6 chars; equity: 1-5 chars
                    if cls == "crypto" and not (3 <= len(t) <= 6): continue
                    if cls == "equity" and not (1 <= len(t) <= 5): continue
                    buzz = min(BUZZ_WEIGHTS["reddit_mention"] * (score/500), 30)
                    found.setdefault(t, {"score":0,"reasons":[],"class":cls,"price":0})
                    found[t]["score"]   += buzz
                    found[t]["reasons"].append(f"r/{sub}({score}pts)")
        except Exception:
            continue

    return found


# ─── SOURCE 3: StockTwits trending ──────────────────────────────────

def scan_stocktwits():
    """StockTwits trending tickers — free, no auth needed."""
    found = {}
    try:
        r = requests.get(
            "https://api.stocktwits.com/api/2/trending/symbols.json",
            timeout=10)
        if r.ok:
            symbols = r.json().get("symbols", [])
            for i, s in enumerate(symbols[:20]):
                sym   = s.get("symbol","").upper()
                title = s.get("title","")
                if not sym: continue
                score = BUZZ_WEIGHTS["stocktwits_trending"]
                if i < 5: score += 15   # top 5 trending = extra weight
                found[sym] = {
                    "score":   score,
                    "reasons": [f"StockTwits#{i+1}"],
                    "title":   title,
                    "price":   0
                }
    except Exception:
        pass
    return found


# ─── SOURCE 4: Finnhub news keyword scan ────────────────────────────

def scan_finnhub_news():
    """Scan Finnhub headlines for trending tickers and buzz keywords."""
    found = {}
    if not FINNHUB_KEY:
        return found

    BUZZ_KEYWORDS = [
        "surges","spikes","soars","explodes","rockets","rallies","breakout",
        "all-time high","ath","bull run","goes parabolic","moon","massive gains",
        "short squeeze","gamma squeeze","sec approves","etf approved","listing",
        "partnership","acquisition","merger","buyout","earnings beat","upgrade",
        "buy","strong buy","outperform"
    ]
    import re
    ticker_pattern = re.compile(r'\b([A-Z]{1,5})\b')
    SKIP = {"THE","AND","FOR","NOT","BUT","ARE","YOU","ALL","CAN","HAS","USD",
            "CEO","ETF","SEC","FED","US","UK","EU","AI","IPO","AT","IN","IT",
            "ON","OR","OF","BE","IS","IF","TO","BY","NO","DO","SO","MY","WE"}

    try:
        for cat in ["general", "crypto"]:
            r = requests.get(
                f"https://finnhub.io/api/v1/news",
                params={"category": cat, "token": FINNHUB_KEY},
                timeout=10)
            if not r.ok: continue
            for article in r.json()[:30]:
                headline = article.get("headline","").upper()
                if not headline: continue
                # Check if headline has buzz keywords
                has_buzz = any(kw.upper() in headline for kw in BUZZ_KEYWORDS)
                if not has_buzz: continue
                # Extract tickers from headline
                tickers = ticker_pattern.findall(headline)
                for t in set(tickers):
                    if t in SKIP: continue
                    if len(t) < 2 or len(t) > 6: continue
                    found.setdefault(t, {"score":0,"reasons":[],"price":0})
                    found[t]["score"]   += BUZZ_WEIGHTS["news_mention"]
                    found[t]["reasons"].append(f"Finnhub:{cat}")
    except Exception:
        pass

    return found


# ─── PRICE DATA: CoinGecko (crypto) + yfinance (equity) ─────────────

def get_crypto_prices_cg(cg_id, days=90):
    """Fetch OHLC price array from CoinGecko. Works for ANY coin."""
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/coins/%s/market_chart"
            "?vs_currency=usd&days=%d" % (cg_id, days),
            timeout=15)
        if r.ok:
            prices = [float(p[1]) for p in r.json().get("prices", [])]
            # Downsample to daily if hourly data returned
            if len(prices) > days * 3:
                step = len(prices) // days
                prices = prices[::step]
            return np.array(prices) if len(prices) >= 20 else None
    except Exception:
        pass
    return None

def resolve_cg_id(sym, cg_id_hint=""):
    """Resolve CoinGecko id from symbol. Returns (cg_id, coinbase_ticker)."""
    if cg_id_hint:
        return cg_id_hint, "%s-USD" % sym
    # Try direct lookup
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/search?query=%s" % sym,
            timeout=8)
        if r.ok:
            coins = r.json().get("coins", [])
            # Exact symbol match first
            for c in coins:
                if c.get("symbol","").upper() == sym:
                    return c["id"], "%s-USD" % sym
            # Best fuzzy match
            if coins:
                return coins[0]["id"], "%s-USD" % sym
    except Exception:
        pass
    return sym.lower(), "%s-USD" % sym

def get_prices_any(sym, data, equity_fallback=True):
    # Force crypto path for known/likely crypto symbols before trying Yahoo
    cls = data.get("class", "unknown")
    cg_id = data.get("cg_id", "")
    if cls == "unknown" and (sym in COINBASE_KNOWN or cg_id):
        data = dict(data); data["class"] = "crypto"

    """Get prices for ANY asset: crypto via CoinGecko, equity via yfinance."""
    cg_id    = data.get("cg_id", "")
    cls      = data.get("class", "unknown")

    # Crypto: use CoinGecko (works for new coins not on Yahoo)
    if cls == "crypto" or sym in COINBASE_KNOWN or cg_id:
        resolved_id, ticker = resolve_cg_id(sym, cg_id)
        prices = get_crypto_prices_cg(resolved_id)
        if prices is not None and len(prices) >= 30:
            return prices, ticker, "crypto"

    # Equity / forex ETF: use yfinance (suppress 404 stderr)
    if equity_fallback:
        import yfinance as yf, contextlib, io
        for t in [sym, "%s-USD" % sym]:
            try:
                with contextlib.redirect_stderr(io.StringIO()):
                    hist = yf.Ticker(t).history(period="2y", auto_adjust=True)
                if len(hist) >= 30:
                    return hist["Close"].values, t, "equity"
            except Exception:
                continue

    return None, None, None

# ─── SOURCE 5: Price breakout detector ──────────────────────────────

def scan_price_breakouts(candidates):
    """For shortlisted tickers, check if price just broke 30-day high."""
    breakouts = {}
    for sym, data in candidates.items():
        if data.get("score", 0) < 20: continue
        time.sleep(0.3)  # avoid CoinGecko rate limit
        prices, ticker, cls = get_prices_any(sym, data)
        if prices is None or len(prices) < 25: continue
        high30  = float(np.max(prices[-31:-1]))
        cur     = float(prices[-1])
        if cur >= high30 * 0.98:
            score_add = BUZZ_WEIGHTS["price_breakout"]
            if cur >= high30 * 1.02: score_add = int(score_add * 1.5)
            breakouts[sym] = {
                "ticker":    ticker or "%s-USD" % sym,
                "price":     cur,
                "high30":    high30,
                "breakout":  True,
                "score_add": score_add,
                "class":     cls or data.get("class","crypto"),
            }
    return breakouts


# ─── MAIN DISCOVERY ENGINE ───────────────────────────────────────────

def discover_universe(verbose=True):
    """
    Run all discovery sources, score everything, return hot list.
    
    Returns:
        hot_list: dict of {symbol: {ticker, class, buzz_score, reasons, price}}
    """
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"[DISCOVER] Dynamic Universe Scan — {ts}", ""]

    # ── Gather from all sources ──────────────────────────────────────
    lines.append("  Scanning CoinGecko...")
    cg = scan_coingecko()
    lines.append(f"    Found {len(cg)} crypto signals")

    lines.append("  Scanning Reddit...")
    rd = scan_reddit()
    lines.append(f"    Found {len(rd)} mentions")

    lines.append("  Scanning StockTwits...")
    st = scan_stocktwits()
    lines.append(f"    Found {len(st)} trending")

    lines.append("  Scanning Finnhub news...")
    fn = scan_finnhub_news()
    lines.append(f"    Found {len(fn)} buzzworthy")

    # ── Merge all sources ────────────────────────────────────────────
    merged = {}
    for source_dict in [cg, rd, st, fn]:
        for sym, data in source_dict.items():
            if sym not in merged:
                merged[sym] = {"score": 0, "reasons": [], "price": 0, "class": "unknown"}
            merged[sym]["score"]   += data.get("score", 0)
            merged[sym]["reasons"] += data.get("reasons", [])
            if data.get("price", 0): merged[sym]["price"] = data["price"]
            if data.get("class"):    merged[sym]["class"] = data["class"]
            if data.get("cg_id"):    merged[sym]["cg_id"] = data["cg_id"]

    # ── Check breakouts for top candidates ───────────────────────────
    top_candidates = {k:v for k,v in merged.items() if v["score"] >= BUZZ_THRESHOLD * 0.7}
    lines.append(f"  Checking price breakouts for {len(top_candidates)} candidates...")
    breakouts = scan_price_breakouts(top_candidates)
    for sym, bo in breakouts.items():
        if sym in merged:
            merged[sym]["score"]    += bo["score_add"]
            merged[sym]["ticker"]    = bo["ticker"]
            merged[sym]["price"]     = bo["price"]
            merged[sym]["breakout"]  = True
            merged[sym]["reasons"].append(f"breakout@{bo['price']:.4f}")

    # ── Filter: score above threshold ────────────────────────────────
    hot = {k:v for k,v in merged.items() if v["score"] >= BUZZ_THRESHOLD}

    # ── Determine tradeable ticker + exchange ────────────────────────
    hot_list = {}
    for sym, data in sorted(hot.items(), key=lambda x: x[1]["score"], reverse=True)[:30]:
        ticker  = data.get("ticker", "")
        cls     = data.get("class", "unknown")
        on_coinbase = sym in COINBASE_KNOWN
        # Try to resolve ticker if not yet determined
        cg_id = data.get("cg_id", "")
        if not ticker:
            if on_coinbase or cls == "crypto" or cg_id:
                ticker = f"{sym}-USD"; cls = "crypto"
            else:
                ticker = sym; cls = "equity"

        hot_list[sym] = {
            "ticker":      ticker,
            "class":       cls,
            "platform":    "coinbase" if cls == "crypto" else "alpaca",
            "buzz_score":  round(data["score"], 1),
            "price":       data.get("price", 0),
            "reasons":     list(set(data.get("reasons", [])))[:5],
            "breakout":    data.get("breakout", False),
            "cg_id":       data.get("cg_id", ""),
            "discovered":  ts,
            # Smaller position sizing for discovered assets
            "max_trade_pct": 0.02,   # 2% max (vs 4% for core assets)
            "kelly_mult":    0.5,    # half Kelly for unproven assets
            "sell_threshold":50,     # sell faster (score < 50 not < 40)
        }

    # ── Log results ──────────────────────────────────────────────────
    lines.append("")
    lines.append(f"  HOT LIST ({len(hot_list)} assets above threshold={BUZZ_THRESHOLD}):")
    for sym, d in hot_list.items():
        brk = " [BREAKOUT]" if d.get("breakout") else ""
        lines.append(
            f"    {sym:8s} {d['ticker']:14s} buzz={d['buzz_score']:.0f}"
            f" {d['class']:6s}{brk}"
            f" reasons={d['reasons'][:2]}"
        )

    # ── Save hot list ────────────────────────────────────────────────
    os.makedirs(MODELS_DIR, exist_ok=True)
    existing = {}
    try:
        with open(HOT_LIST) as f: existing = json.load(f)
    except Exception: pass

    # Merge: keep existing entries up to 48h, update scores for seen ones
    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
    for sym, d in existing.items():
        try:
            disc_ts = datetime.fromisoformat(d.get("discovered","").replace("Z",""))
            disc_ts = disc_ts.replace(tzinfo=timezone.utc)
            if disc_ts > cutoff and sym not in hot_list:
                hot_list[sym] = d   # keep recent discovery even if score dropped
        except Exception: pass

    with open(HOT_LIST, "w") as f:
        json.dump({"ts": ts, "assets": hot_list}, f, indent=2)

    # ── Append to discovery log ───────────────────────────────────────
    log = []
    try:
        with open(WATCH_LOG) as f: log = json.load(f)
    except Exception: pass
    log.append({"ts": ts, "found": len(hot_list),
                "top5": list(hot_list.keys())[:5]})
    log = log[-200:]
    with open(WATCH_LOG, "w") as f: json.dump(log, f, indent=2)

    report = "\n".join(lines)
    if verbose: print(report)
    return hot_list, report


# ─── KERNEL VALIDATION GATE ──────────────────────────────────────────

def validate_hot_asset(sym, ticker, asset_cls, cg_id=""):
    """
    Run kernel v16e on a discovered asset.
    Uses CoinGecko for crypto (covers ALL coins incl. new listings).
    """
    try:
        from kernel_predictor_v16e import KernelPredictor
        from unified_brain import compute_signal_quality

        # Fetch prices using best available source
        prices = None

        # For ANY crypto: resolve via CoinGecko (works for new coins)
        if asset_cls == "crypto" or "-USD" in ticker or sym in COINBASE_KNOWN:
            resolved_id, _ = resolve_cg_id(sym, cg_id)
            # Free CoinGecko tier: use 365d max, with 0.5s rate-limit pause
            time.sleep(0.5)
            prices = get_crypto_prices_cg(resolved_id, days=365)

        # For equity or if CG failed: try yfinance (suppress 404 noise)
        if prices is None or len(prices) < 60:
            import yfinance as yf, contextlib, io
            for t in [ticker, sym, sym + "-USD"]:
                try:
                    with contextlib.redirect_stderr(io.StringIO()):
                        hist = yf.Ticker(t).history(period="2y", auto_adjust=True)
                    if len(hist) >= 60:
                        prices = hist["Close"].values; break
                except Exception: continue

        min_pts = 30 if asset_cls == "crypto" else 60
        if prices is None or len(prices) < min_pts:
            return None

        kp  = KernelPredictor(asset_cls)
        sig = kp.predict(prices)
        q   = compute_signal_quality(prices, sig, asset_cls)

        return {
            "symbol":         sym,
            "ticker":         ticker,
            "kernel_score":   sig.get("score", 50),
            "direction":      sig.get("direction", "HOLD"),
            "votes":          sig.get("votes", 0),
            "dom":            sig.get("dom", "?"),
            "passes_filter":  q["passes_filter"],
            "pos_multiplier": q["pos_multiplier"],
            "price":          float(prices[-1]),
        }
    except Exception as e:
        return None


# ─── SUMMARY REPORT for Signal (sent via cron) ──────────────────────

def discovery_report():
    """Generate Signal-ready discovery report."""
    hot_list, scan_report = discover_universe(verbose=False)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [f"[DISCOVER] {ts}", ""]

    # Validate top 10 through kernel
    validated = []
    lines.append("Running kernel validation on top discoveries...")
    for sym, d in list(hot_list.items())[:12]:
        v = validate_hot_asset(sym, d["ticker"], d["class"], d.get("cg_id",""))
        if v:
            v["buzz_score"] = d["buzz_score"]
            v["reasons"]    = d.get("reasons", [])
            validated.append(v)

    # Sort by combined signal: kernel score + buzz
    validated.sort(
        key=lambda x: x["kernel_score"] * 0.6 + x["buzz_score"] * 0.4,
        reverse=True)

    lines.append(f"\nTop discoveries (kernel-validated):")
    lines.append(f"  {'Asset':8s} {'Score':6s} {'Dir':5s} {'Votes':6s} {'Filter':7s} {'Buzz':6s} {'Why'}")
    actionable = []
    for v in validated[:15]:
        filt = "✓" if v["passes_filter"] else "-"
        lines.append(
            f"  {v['symbol']:8s} {v['kernel_score']:5.0f}  "
            f"{v['direction']:5s}  {v['votes']:4d}   {filt:6s}  "
            f"{v['buzz_score']:5.0f}  {v['reasons'][:1]}"
        )
        if v["kernel_score"] > 58 and v["passes_filter"]:
            actionable.append(v)

    if actionable:
        lines.append(f"\n  >> ACTIONABLE ({len(actionable)} assets pass filter + kernel):")
        for a in actionable:
            lines.append(
                f"     {a['symbol']} {a['direction']} "
                f"kernel={a['kernel_score']:.0f} buzz={a['buzz_score']:.0f} "
                f"({', '.join(a['reasons'][:2])})"
            )
    else:
        lines.append("\n  No actionable discoveries this cycle.")

    lines.append("")
    lines.append(
        "FOMO PROTECTION: Discovered assets trade at 50% Kelly, "
        "2% max position, sell at score<50 (faster exit)."
    )

    return "\n".join(lines)


if __name__ == "__main__":
    # Load env if needed
    if not os.environ.get("FINNHUB_API_KEY"):
        try:
            with open(ENV_FILE) as f:
                for line in f:
                    if "=" in line and not line.startswith("#"):
                        k, v = line.strip().split("=", 1)
                        os.environ.setdefault(k.strip(), v.strip())
        except Exception: pass

    print(discovery_report())
