"""POLY-E: Polymarket entertainment / Oscars / awards path (shadow only).

Categorize Gamma markets. Local JSON → gatherer + category ENTERTAINMENT.
Shadow Intents reuse polymarket_order_body + nautilus_shadow_venues.
Never dispatchable. Writer stays none. No HTTP unless a test injects get_fn.
(c) GOATWORTHY Post LLC.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import valedia_config as vc

_MODELS = os.path.expanduser("~/.openclaw/workspace/models")
OUT_NAME = "polymarket_entertainment.json"
CATEGORY = "ENTERTAINMENT"
STRATEGY = "POLYMARKET_ENTERTAINMENT"
KIND = "polymarket_entertainment"
TAG = "entertainment"

# Polymarket nav (Burton POLY-E steer): Sports, Politics, Crypto, Weather,
# Tech, Culture, Econ. Culture = Oscars/awards/entertainment.
NAV_TAGS = {
    "SPORTS": frozenset({"sports", "nba", "nfl", "mlb", "nhl", "soccer",
                         "football", "ufc", "mma", "tennis"}),
    "POLITICS": frozenset({"politics", "elections", "election", "president",
                           "congress", "senate", "white-house"}),
    "CRYPTO": frozenset({"crypto", "bitcoin", "ethereum", "solana", "btc",
                         "eth"}),
    "WEATHER": frozenset({"weather", "climate", "temperature", "hurricane"}),
    "TECH": frozenset({"tech", "technology", "ai", "openai", "apple",
                       "nvidia", "science-and-technology"}),
    "ENTERTAINMENT": frozenset({
        "entertainment", "awards", "oscars", "oscar", "academy-awards",
        "emmys", "emmy", "grammys", "grammy", "golden-globes", "movies",
        "film", "tv", "music", "pop-culture", "culture", "hollywood",
        "celebrity", "box-office", "bafta", "tonys",
    }),
    "ECON": frozenset({"econ", "economics", "economy", "fed", "cpi",
                       "inflation", "gdp", "financials", "finance"}),
}
NAV_WORDS = {
    "SPORTS": ("nfl", "nba", "mlb", "nhl", "fifa", "ufc", "premier league",
               "super bowl", "world cup", "march madness", "ncaa"),
    "POLITICS": ("president", "election", "primary", "senate", "congress",
                 "white house", "democrat", "republican"),
    "CRYPTO": ("bitcoin", "ethereum", "solana", "btc ", "eth ", "crypto"),
    "WEATHER": ("hurricane", "temperature above", "inches of snow", "noaa"),
    "TECH": ("openai", "chatgpt", "apple event", "nvidia", "ai model"),
    "ENTERTAINMENT": (
        "oscar", "oscars", "academy award", "emmy", "grammy", "golden globe",
        "best picture", "best actor", "best actress", "best director",
        "bafta", "cannes", "tony award", "billboard music", "mtv",
        "box office", "hollywood",
    ),
    "ECON": ("fed rate", "cpi ", "inflation", "recession", "gdp ", "fomc"),
}
NAV_ORDER = ("ENTERTAINMENT", "SPORTS", "POLITICS", "CRYPTO", "WEATHER",
             "TECH", "ECON")
STRATEGY_FOR = {
    "ENTERTAINMENT": STRATEGY,
    "SPORTS": "POLYMARKET_SPORTS",
    "POLITICS": "POLYMARKET_POLITICS",
    "CRYPTO": "POLYMARKET_CRYPTO",
    "WEATHER": "POLYMARKET_WEATHER",
    "TECH": "POLYMARKET_TECH",
    "ECON": "POLYMARKET_ECON",
}


def _tags(market):
    raw = (market or {}).get("tags") or []
    out = []
    if isinstance(raw, str):
        raw = [raw]
    for t in raw:
        if isinstance(t, dict):
            s = t.get("slug") or t.get("label") or t.get("name") or ""
        else:
            s = t
        s = str(s or "").strip().lower().replace(" ", "-")
        if s:
            out.append(s)
    cat = str((market or {}).get("category") or "").strip().lower().replace(" ", "-")
    if cat:
        out.append(cat)
    return out


def _text(market):
    m = market or {}
    parts = [m.get("question"), m.get("title"), m.get("slug"),
             m.get("description"), " ".join(_tags(m))]
    return " ".join(str(p or "") for p in parts).lower()


def classify_market(market):
    """Nav bucket: Culture/Oscars, sports, politics, crypto, weather, tech, econ."""
    tags = _tags(market)
    text = _text(market)
    hit_tags, hit_words, bucket = [], [], "OTHER"
    for name in NAV_ORDER:
        t_hits = [t for t in tags if t in NAV_TAGS[name]]
        w_hits = [w for w in NAV_WORDS[name] if w in text]
        if t_hits or w_hits:
            bucket = name
            hit_tags, hit_words = t_hits, w_hits
            break
    is_ent = bucket == CATEGORY
    out_tags = [bucket.lower()] if bucket != "OTHER" else []
    if is_ent:
        if TAG not in out_tags:
            out_tags.insert(0, TAG)
        if "oscar" in text or "oscars" in tags or "oscars" in text:
            out_tags.append("oscars")
        if "award" in text:
            out_tags.append("awards")
    return {
        "is_entertainment": is_ent,
        "category": bucket,
        "tags": out_tags,
        "hit_tags": hit_tags,
        "hit_words": hit_words,
    }


def _tokens(market):
    m = market or {}
    tokens = m.get("tokens") or []
    if isinstance(tokens, str):
        try:
            tokens = json.loads(tokens)
        except Exception:
            tokens = []
    if isinstance(tokens, list) and tokens:
        return [t for t in tokens if isinstance(t, dict)]
    raw = m.get("clobTokenIds") or m.get("clob_token_ids") or []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = [raw] if raw else []
    prices = m.get("outcomePrices") or m.get("outcome_prices") or []
    if isinstance(prices, str):
        try:
            prices = json.loads(prices)
        except Exception:
            prices = []
    out = []
    for i, tid in enumerate(raw or []):
        px = None
        if i < len(prices):
            try:
                px = float(prices[i])
            except (TypeError, ValueError):
                px = None
        out.append({"token_id": str(tid), "price": px,
                    "outcome": (m.get("outcomes") or ["Yes", "No"])[i]
                    if isinstance(m.get("outcomes"), list) and i < len(m["outcomes"])
                    else ("Yes" if i == 0 else "No")})
    return out


def to_candidate(market, notional=5.0):
    """Share-qty candidate for CLOB shadow. Never invents token_id or price."""
    info = classify_market(market)
    if info["category"] == "OTHER":
        return None
    tokens = _tokens(market)
    tok = None
    for t in tokens:
        if str(t.get("outcome") or "").lower() == "yes":
            tok = t
            break
    if tok is None and tokens:
        tok = tokens[0]
    if not tok or not str(tok.get("token_id") or "").strip():
        return None
    try:
        price = float(tok.get("price"))
    except (TypeError, ValueError):
        price = None
    if price is None or price <= 0 or price >= 1:
        return None
    vol = 0.0
    for k in ("volume24hr", "volume", "liquidity"):
        try:
            vol = max(vol, float((market or {}).get(k) or 0))
        except (TypeError, ValueError):
            pass
    score = min(90.0, 55.0 + min(vol / 1e5, 30.0))
    edge = max(0.5, min(abs(price - 0.5) * 20.0, 15.0))
    return {
        "token_id": str(tok["token_id"]).strip(),
        "side": "buy",
        "price": price,
        "notional": notional,
        "strategy": STRATEGY_FOR.get(info["category"], STRATEGY),
        "kind": KIND if info["is_entertainment"] else f"polymarket_{info['category'].lower()}",
        "category": info["category"],
        "tags": info["tags"],
        "score": round(score, 2),
        "edge_pct": round(edge, 4),
        "question": (market or {}).get("question") or (market or {}).get("title"),
    }


def scan_markets(markets, notional=5.0):
    out = []
    for m in markets or []:
        if not isinstance(m, dict):
            continue
        c = to_candidate(m, notional=notional)
        if c:
            out.append(c)
    return out


def candidates_from_doc(doc, notional=5.0):
    if isinstance(doc, list):
        return scan_markets(doc, notional=notional)
    d = doc or {}
    extra = [c for c in (d.get("candidates") or [])
             if isinstance(c, dict) and c.get("token_id") and c.get("price")]
    if extra:
        out = []
        for c in extra:
            payload = dict(c)
            payload.setdefault("strategy", STRATEGY)
            payload.setdefault("kind", KIND)
            payload.setdefault("tags", [TAG])
            out.append(payload)
        return out
    return scan_markets(d.get("markets") or d.get("data") or [], notional=notional)


def opportunities(doc, notional=5.0):
    """Gatherer shape. Local JSON only."""
    out = []
    for c in candidates_from_doc(doc, notional=notional):
        tid = str(c.get("token_id") or "").strip()
        if not tid:
            continue
        try:
            price = float(c.get("price"))
            if price <= 0 or price >= 1:
                continue
        except (TypeError, ValueError):
            continue
        kind = str(c.get("kind") or KIND)
        tags = list(c.get("tags") or ([TAG] if kind == KIND else []))
        feed = "pm_entertainment" if kind == KIND else "pm_nav"
        out.append({
            "kind": kind,
            "strategy": str(c.get("strategy") or STRATEGY),
            "venue": "polymarket",
            "instrument": tid,
            "side": str(c.get("side") or "buy").lower(),
            "score": float(c.get("score") or 60.0),
            "edge_pct": float(c.get("edge_pct") or 0.5),
            "price": price,
            "notional": float(c.get("notional") or notional),
            "source_feed": feed,
            "tags": tags,
            "raw": {**c, "opportunity_tags": tags},
        })
    return out


def write_scan(markets, models_dir=None, notional=5.0):
    md = models_dir or _MODELS
    os.makedirs(md, exist_ok=True)
    cands = scan_markets(markets, notional=notional)
    classified = []
    for m in markets or []:
        if not isinstance(m, dict):
            continue
        classified.append({**m, **classify_market(m)})
    doc = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "markets": classified,
        "candidates": cands,
        "n_entertainment": len(cands),
        "valedia_version": vc.VALEDIA_VERSION,
        "writer": vc.venue_writer("polymarket"),
        "dispatchable": False,
    }
    path = os.path.join(md, OUT_NAME)
    with open(path, "w") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")
    return path, doc


def fetch_markets(get_fn=None):
    """No default HTTP. Tests inject. Live Gamma GET is a later Burton flip."""
    if get_fn is None:
        raise RuntimeError("POLY-E HTTP off (inject get_fn)")
    return get_fn()


def apply_seed_text(text, models_dir=None):
    """Advisory MiroFish section from local entertainment JSON. No HTTP."""
    path = os.path.join(models_dir or _MODELS, OUT_NAME)
    if not os.path.exists(path):
        return text
    try:
        doc = json.load(open(path)) or {}
    except Exception:
        return text
    cands = candidates_from_doc(doc)
    if not cands:
        return text
    lines = ["", "## Polymarket entertainment / awards (local scan, shadow only)",
             "Opportunity tags: entertainment. Fee-true accuracy ledger may score later."]
    for c in cands[:8]:
        q = str(c.get("question") or c.get("token_id") or "?")[:120]
        lines.append(f"- {q} — yes {float(c.get('price') or 0):.2f} tags={c.get('tags')}")
    return str(text or "").rstrip() + "\n" + "\n".join(lines) + "\n"
