"""MiroFish Signal Extractor — Converts swarm report → numerical brain signal
Parses MiroFish markdown report and extracts structured predictions
that feed directly into Valedia's unified brain fused score.

Input: MiroFish report (markdown text with sections)
Output: Structured JSON with per-asset predictions + confidence

(c) GOATWORTHY Post LLC.
"""
import json, re, os, sys
from datetime import datetime, timezone

def extract_signals_from_report(markdown_content, report_summary=""):
    """Parse MiroFish report and extract actionable trading signals.
    
    Uses keyword/pattern matching (no LLM needed = instant).
    Falls back to LLM extraction for complex reports.
    """
    signals = {}
    text = (report_summary + "\n" + markdown_content).lower()
    
    # First: extract explicit action signals (BUY/SELL/AVOID for specific tickers)
    action_signals = _extract_action_signals(report_summary + "\n" + markdown_content)
    
    # Assets to look for
    asset_map = {
        "btc": "BTC-USD", "bitcoin": "BTC-USD",
        "eth": "ETH-USD", "ethereum": "ETH-USD",
        "sol": "SOL-USD", "solana": "SOL-USD",
        "xrp": "XRP-USD", "ripple": "XRP-USD",
        "ada": "ADA-USD", "cardano": "ADA-USD",
        "avax": "AVAX-USD", "avalanche": "AVAX-USD",
        "doge": "DOGE-USD", "dogecoin": "DOGE-USD",
        "link": "LINK-USD", "chainlink": "LINK-USD",
        "spy": "SPY", "s&p": "SPY", "s&p 500": "SPY",
        "qqq": "QQQ", "nasdaq": "QQQ",
        "nvda": "NVDA", "nvidia": "NVDA",
        "gld": "GLD", "gold": "GLD",
        "aapl": "AAPL", "apple": "AAPL",
        "tsla": "TSLA", "tesla": "TSLA",
        "meta": "META", "facebook": "META",
        "amd": "AMD", "googl": "GOOGL", "google": "GOOGL",
    }
    
    # Bullish keywords (weighted)
    bull_strong = ["rally", "surge", "breakout", "accumulate", "buying pressure",
                   "bullish", "upside", "bounce", "recovery", "moon", "pump"]
    bull_mild = ["support", "hold", "stable", "buying", "positive", "optimistic",
                 "oversold", "undervalued", "contrarian buy", "fear is opportunity"]
    
    # Bearish keywords
    bear_strong = ["crash", "dump", "collapse", "sell-off", "capitulation",
                   "bearish", "downside", "breakdown", "plunge"]
    bear_mild = ["resistance", "overbought", "overvalued", "caution", "risk",
                 "uncertainty", "selling pressure", "distribution"]
    
    # For each asset, scan surrounding text for sentiment
    for keyword, ticker in asset_map.items():
        if keyword not in text:
            continue
        
        # Find all occurrences and get surrounding context (200 chars each side)
        score = 0
        mentions = 0
        for match in re.finditer(re.escape(keyword), text):
            start = max(0, match.start() - 200)
            end = min(len(text), match.end() + 200)
            context = text[start:end]
            mentions += 1
            
            # Score the context
            for word in bull_strong:
                if word in context: score += 3
            for word in bull_mild:
                if word in context: score += 1
            for word in bear_strong:
                if word in context: score -= 3
            for word in bear_mild:
                if word in context: score -= 1
        
        if mentions == 0:
            continue
        
        # Normalize score to [-1, 1] range
        raw_score = score / (mentions * 3 + 1)  # normalize by mentions
        raw_score = max(-1.0, min(1.0, raw_score))
        
        # Convert to brain-compatible format (50 = neutral, 0-100 scale)
        brain_score = 50 + raw_score * 25  # maps [-1,1] to [25,75]
        
        # Confidence based on mention count + score magnitude
        conf = min(mentions / 5.0, 1.0) * min(abs(raw_score) * 2, 1.0)
        conf = max(0.1, min(conf, 0.9))
        
        direction = "BUY" if brain_score > 55 else "SELL" if brain_score < 45 else "HOLD"
        
        signals[ticker] = {
            "score": round(brain_score, 1),
            "direction": direction,
            "confidence": round(conf, 3),
            "mentions": mentions,
            "raw_sentiment": round(raw_score, 3),
        }
    
    # Apply explicit action signals (override/boost from embedded quotes)
    for action_ticker, actions in action_signals.items():
        # Map to our ticker format
        mapped = action_ticker if action_ticker in signals else f"{action_ticker}-USD" if f"{action_ticker}-USD" in signals else None
        if not mapped:
            # Try the asset_map reverse lookup
            for keyword, tk in asset_map.items():
                if action_ticker.lower().replace("-usd","").replace("-","") == keyword.replace("-usd",""):
                    mapped = tk
                    break
        if not mapped and action_ticker.endswith("-USD"):
            mapped = action_ticker
        if not mapped:
            mapped = f"{action_ticker}-USD"
        
        buy_count = sum(1 for a in actions if a == "BUY")
        sell_count = sum(1 for a in actions if a in ("SELL", "AVOID"))
        net = buy_count - sell_count
        
        if mapped in signals:
            signals[mapped]["score"] = round(min(75, max(25, signals[mapped]["score"] + net * 8)), 1)
            signals[mapped]["confidence"] = round(min(0.9, signals[mapped]["confidence"] + 0.2), 3)
            signals[mapped]["direction"] = "BUY" if signals[mapped]["score"] > 55 else "SELL" if signals[mapped]["score"] < 45 else "HOLD"
        elif abs(net) > 0:
            score = 50 + net * 12
            signals[mapped] = {
                "score": round(min(75, max(25, score)), 1),
                "direction": "BUY" if score > 55 else "SELL" if score < 45 else "HOLD",
                "confidence": 0.5,
                "mentions": len(actions),
                "raw_sentiment": round(net / (len(actions) + 1), 3),
            }
    
    return signals

def _extract_action_signals(text):
    """Extract explicit action signals embedded in MiroFish reports.
    Catches patterns like: 'suggests a BUY action for SOL-USD'
    'executed a BUY trade for BTC-USD', 'AVOID action for LINK-USD'"""
    actions = {}
    patterns = [
        r'suggests\s+(?:a|an)\s+(BUY|SELL|AVOID|HOLD)\s+action\s+for\s+([A-Z]+-?[A-Z]*)',
        r'executed\s+(?:a|an)\s+(BUY|SELL|AVOID|HOLD)\s+trade\s+for\s+([A-Z]+-?[A-Z]*)',
        r'(BUY|SELL|AVOID|HOLD)\s+(?:signal|action)\s+(?:for|on)\s+([A-Z]+-?[A-Z]*)',
    ]
    for pat in patterns:
        for m in re.finditer(pat, text, re.IGNORECASE):
            action = m.group(1).upper()
            ticker = m.group(2).upper()
            if ticker not in actions:
                actions[ticker] = []
            actions[ticker].append(action)
    return actions

def extract_price_targets(markdown_content):
    """Extract any specific price targets mentioned in report."""
    targets = {}
    # Match patterns like "$72,000" or "$72K" or "72000" near asset names
    price_patterns = [
        r'\$([0-9,]+(?:\.[0-9]+)?)\s*(?:k|K)?',
        r'(?:target|resistance|support|level|price)\s*(?:of|at|:)?\s*\$?([0-9,]+(?:\.[0-9]+)?)',
    ]
    for pattern in price_patterns:
        for match in re.finditer(pattern, markdown_content):
            try:
                price_str = match.group(1).replace(",", "")
                price = float(price_str)
                if price > 0:
                    # Get context to determine which asset
                    start = max(0, match.start() - 100)
                    context = markdown_content[start:match.start()].lower()
                    for name, ticker in [("btc","BTC-USD"),("bitcoin","BTC-USD"),
                                         ("eth","ETH-USD"),("ethereum","ETH-USD"),
                                         ("sol","SOL-USD"),("spy","SPY"),("qqq","QQQ")]:
                        if name in context:
                            if ticker not in targets:
                                targets[ticker] = []
                            targets[ticker].append(price)
            except: pass
    return targets

def extract_agent_consensus(markdown_content):
    """Count how many agent types are bullish vs bearish."""
    agent_types = ["institutional", "retail", "quant", "market maker",
                   "whale", "macro", "defi", "short seller"]
    consensus = {"bullish": 0, "bearish": 0, "neutral": 0, "agents": {}}
    text = markdown_content.lower()
    
    for agent in agent_types:
        if agent not in text: continue
        # Find agent mentions and surrounding sentiment
        for match in re.finditer(re.escape(agent), text):
            context = text[max(0,match.start()-150):min(len(text),match.end()+150)]
            bull = sum(1 for w in ["buy","bull","accumul","long","optimist","support"] if w in context)
            bear = sum(1 for w in ["sell","bear","short","pessimist","caution","risk"] if w in context)
            if bull > bear:
                consensus["bullish"] += 1
                consensus["agents"][agent] = "BULL"
            elif bear > bull:
                consensus["bearish"] += 1
                consensus["agents"][agent] = "BEAR"
            else:
                consensus["neutral"] += 1
                consensus["agents"][agent] = "NEUTRAL"
            break  # one vote per agent type
    
    total = consensus["bullish"] + consensus["bearish"] + consensus["neutral"]
    consensus["bull_ratio"] = consensus["bullish"] / max(total, 1)
    return consensus

# RC3 (2026-09-13): event/catalyst layer. The per-ticker extractor above only
# fires when a specific ticker/name appears; a macro report (Fed, IPO, regulation,
# geopolitics) named few tickers, so its breadth never reached the brain. This
# scans the report for market-moving EVENTS and emits a directional macro bias
# that process_mirofish_report folds into overall_bias.
EVENT_MAP = {
    "monetary_policy": ["fomc", "federal reserve", "fed funds", "rate hike",
                         "rate cut", "interest rate", "rate decision", "powell",
                         "warsh", "quantitative", "hawkish", "dovish"],
    "ipo": ["ipo", "public offering", "direct listing", "goes public", "s-1 filing"],
    "fda": ["fda approval", "fda", "clinical trial", "phase 3", "phase iii"],
    "earnings": ["earnings", "guidance", "profit warning", "beat estimates",
                  "missed estimates"],
    "m_and_a": ["merger", "acquisition", "takeover", "buyout", "acquire"],
    "regulation": ["regulatory", "regulation", "lawsuit", "sec charges",
                    "sec lawsuit", "crackdown", "ban ", "sanction"],
    "geopolitics": ["geopolitic", "tariff", "war", "conflict", "election",
                     "sanctions"],
}

# Inherent directional sign for UNAMBIGUOUS event keywords. Context only scales magnitude,
# never flips these — otherwise a broadly-bullish report makes even "war"/"crackdown" read
# risk-on (the RC3 inversion bug). Keywords without a sign here stay purely context-scored.
KEYWORD_SIGN = {
    # RISK_OFF (defensive / bearish regardless of report tone)
    "war": -1, "conflict": -1, "sanction": -1, "sanctions": -1, "tariff": -1,
    "crackdown": -1, "lawsuit": -1, "sec charges": -1, "sec lawsuit": -1, "ban ": -1,
    "rate hike": -1, "hawkish": -1, "tightening": -1, "profit warning": -1,
    "missed estimates": -1, "contagion": -1, "crash": -1, "selloff": -1, "sell-off": -1,
    # RISK_ON (supportive / bullish)
    "rate cut": 1, "dovish": 1, "stimulus": 1, "fda approval": 1,
    "beat estimates": 1, "recovery": 1,
}

def extract_events(markdown_content, report_summary=""):
    """Detect market-moving events/catalysts and score each with the surrounding
    bull/bear context. Returns (events_list, macro_bias) where macro_bias is a
    0-100 score (50 = neutral) or None if no events were found."""
    text = (report_summary + "\n" + markdown_content).lower()
    bull_strong = ["rally", "surge", "breakout", "bullish", "upside", "recovery",
                   "risk-on", "risk on", "supportive", "tailwind"]
    bull_mild = ["stable", "positive", "optimistic", "oversold", "undervalued",
                 "easing", "cut", "dovish", "stimulus"]
    bear_strong = ["crash", "sell-off", "selloff", "bearish", "downside",
                   "plunge", "risk-off", "risk off", "capitulation", "contagion"]
    bear_mild = ["caution", "risk", "uncertainty", "headwind", "hawkish",
                 "tightening", "hike", "pressure", "defensive"]

    events = []
    sentiments = []
    for etype, keywords in EVENT_MAP.items():
        best = None
        for kw in keywords:
            for m in re.finditer(re.escape(kw), text):
                start = max(0, m.start() - 200)
                end = min(len(text), m.end() + 200)
                ctx = text[start:end]
                ctx_score = 0
                for w in bull_strong:
                    if w in ctx: ctx_score += 3
                for w in bull_mild:
                    if w in ctx: ctx_score += 1
                for w in bear_strong:
                    if w in ctx: ctx_score -= 3
                for w in bear_mild:
                    if w in ctx: ctx_score -= 1
                base = KEYWORD_SIGN.get(kw, 0)
                if base != 0:
                    # Anchored event: inherent sign wins; context only scales magnitude (1..4), can't flip.
                    s = base * min(4, 1 + abs(ctx_score))
                else:
                    s = ctx_score  # ambiguous event: pure context
                snippet = markdown_content[start:end].strip().replace("\n", " ")
                if best is None or abs(s) > abs(best[0]):
                    best = (s, kw, snippet)
        if best is None:
            continue
        raw = max(-1.0, min(1.0, best[0] / 4.0))
        sentiments.append(raw)
        events.append({
            "type": etype,
            "matched": best[1],
            "sentiment": round(raw, 3),
            "direction": "RISK_ON" if raw > 0.15 else "RISK_OFF" if raw < -0.15 else "NEUTRAL",
            "snippet": best[2][:240],
        })

    if not events:
        return [], None
    macro_bias = 50 + (sum(sentiments) / len(sentiments)) * 25  # [-1,1] -> [25,75]
    return events, round(max(0.0, min(100.0, macro_bias)), 1)


def process_mirofish_report(report_json, enrichment=None, out_path=None):
    """Main entry: takes MiroFish report JSON → returns brain-ready signal.
    
    Output format (saved to mirofish_signal.json):
    {
        "ts": "2026-03-13T...",
        "status": "active",
        "signals": {
            "BTC-USD": {"score": 62.5, "direction": "BUY", "confidence": 0.7},
            "ETH-USD": {"score": 58.0, "direction": "BUY", "confidence": 0.5},
            ...
        },
        "consensus": {"bullish": 5, "bearish": 2, "bull_ratio": 0.71},
        "price_targets": {"BTC-USD": [72000, 75000]},
        "overall_bias": 58.5  ← brain reads this as MiroFish's market view
    }
    """
    markdown = report_json.get("markdown_content", "")
    summary = ""
    if report_json.get("outline"):
        summary = report_json["outline"].get("summary", "")
    
    signals = extract_signals_from_report(markdown, summary)
    targets = extract_price_targets(markdown)
    consensus = extract_agent_consensus(markdown)
    events, macro_bias = extract_events(markdown, summary)  # RC3: event/catalyst layer

    # Per-asset bias: confidence-weighted average of signal scores (None if none).
    if signals:
        scores = [s["score"] * s["confidence"] for s in signals.values()]
        weights = [s["confidence"] for s in signals.values()]
        asset_bias = sum(scores) / sum(weights) if sum(weights) > 0 else None
    else:
        asset_bias = None

    # Overall bias blends per-asset sentiment with the macro/event read. Critically,
    # a macro-heavy report (Fed/IPO/regulation) that named no tickers used to fall
    # through to neutral 50; now it carries the event bias to the brain.
    if asset_bias is not None and macro_bias is not None:
        overall = 0.65 * asset_bias + 0.35 * macro_bias
    elif asset_bias is not None:
        overall = asset_bias
    elif macro_bias is not None:
        overall = macro_bias
    else:
        overall = 50  # neutral if neither assets nor events extracted

    result = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "status": "active",
        "signals": signals,
        "consensus": consensus,
        "price_targets": targets,
        "events": events,            # RC3: market-moving catalysts (Fed, IPO, FDA, M&A, ...)
        "macro_bias": macro_bias,    # RC3: 0-100 event-driven bias (None if no events)
        "overall_bias": round(overall, 1),
        "source": "mirofish_swarm"
    }

    dest = out_path or os.path.expanduser("~/.openclaw/workspace/models/mirofish_signal.json")
    # NT-2: stamp neural context when present. Does not change scores/bias/weight.
    try:
        import mirofish_neural_enrich as mne
        if enrichment is not None:
            enr = enrichment
        else:
            sibling = os.path.join(os.path.dirname(os.path.abspath(dest)),
                                   "mirofish_neural_enrichment.json")
            enr = mne.load_enrichment(sibling)
            if not (isinstance(enr, dict) and enr.get("symbols")):
                enr = mne.load_enrichment()
        result = mne.attach_to_signal(result, enr)
    except Exception:
        result["neural_enriched"] = False

    # H2: persona lenses. Does not change scores/bias/weight/Kelly.
    try:
        import persona_lenses as pl
        pack = pl.score_document(result)
        result = pl.attach_to_signal(result, pack)
        if pack.get("symbols"):
            sibling = os.path.join(os.path.dirname(os.path.abspath(dest)),
                                   "persona_lens_scores.json")
            pl.write_scores(pack, sibling)
    except Exception:
        result["persona_lenses"] = False

    os.makedirs(os.path.dirname(dest) or ".", exist_ok=True)
    with open(dest, "w") as f:
        json.dump(result, f, indent=2)

    return result

if __name__ == "__main__":
    # Test with a sample report
    sample = {
        "outline": {
            "summary": "Bitcoin likely to recover from extreme fear levels as institutional agents accumulate"
        },
        "markdown_content": """
## Market Overview
Bitcoin has fallen to $69,000 amid extreme fear (index: 15). However, institutional traders
in the simulation began accumulating BTC aggressively, seeing this as a contrarian buying opportunity.
The whale wallets showed significant buying pressure near support at $68,500.

## Crypto Predictions
BTC: Bullish bounce expected from $69K to $72K-$74K within 3-5 days. Institutional agents
drove the rally in simulation. Confidence: HIGH.

ETH: Ethereum followed Bitcoin with a moderate recovery. Target $2,200 from current $2,042.
Retail investors remained cautious but quant models showed oversold signals.

SOL: Solana showed strongest momentum among altcoins. DeFi degen agents heavily accumulated.
Price target $95-100 from current $86. Bullish.

## Equity Analysis  
SPY: S&P 500 showed sideways action. Macro analysts flagged recession risk but market makers
maintained support. Neutral to slightly bearish.

QQQ: Nasdaq under selling pressure from institutional rotation out of tech.
Short sellers targeting NVDA and META. Bearish short-term.

## Contrarian Signals
The strongest disagreement was between retail (bearish, capitulating) and whales (bullish, accumulating).
Historically, when whales diverge from retail, whales win 72% of the time.
"""
    }
    
    result = process_mirofish_report(sample)
    print(json.dumps(result, indent=2))
