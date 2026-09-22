"""Offline FOMO-radar tags for CRYPTO-PAPER and MiroFish.

Ideas from cvxv666/fomo-robinhood-radar @ 0bfee25 (MIT), reimplemented here.
Not a port. Annotate only: radar score never sizes money.

No network, no fomo HTTP client, no browser scrape, no orders, no Kalshi V2.
(c) GOATWORTHY Post LLC.
"""
from __future__ import annotations

import json
import os

# Upstream defaults (analyze.TRUSTED, hot_*, provenance seed, exits min_exit).
TRUSTED = 60
BURST_DELTA = 4.0
BURST_MIN_WALLETS = 3
BURST_MIN_SPAN_S = 180
EXIT_MIN = 0.5
EXIT_MIN_SELLERS = 2
SEED_MIN = 3
PUSHED = frozenset({"dust", "direct", "seed"})
EXIT_STATES = frozenset({"closed", "trimmed"})

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
DEFAULT_FIXTURE = os.path.join(_ROOT, "data", "crypto-paper", "fomo_radar_fixture.json")


def conviction(scores):
    """Σ(score/100)^2. Same definition as analyze.conviction."""
    return round(sum((float(s) / 100.0) ** 2 for s in scores if s), 4)


def _holders(row):
    return [h for h in (row.get("holders") or []) if isinstance(h, dict)]


def _counts(row):
    """Trusted real vs pushed wallets. Explicit counts win when the fixture has them."""
    if row.get("real_wallets") is not None or row.get("pushed_wallets") is not None:
        return int(row.get("real_wallets") or 0), int(row.get("pushed_wallets") or 0)
    real = pushed = 0
    for h in _holders(row):
        sc = h.get("score")
        if sc is None or float(sc) < TRUSTED:
            continue
        kind = str(h.get("kind") or "trade").lower()
        if kind in PUSHED:
            pushed += 1
        elif kind == "trade":
            real += 1
    return real, pushed


def _real_scores(row):
    out = []
    for h in _holders(row):
        if str(h.get("kind") or "trade").lower() != "trade":
            continue
        sc = h.get("score")
        if sc is None or float(sc) < TRUSTED:
            continue
        out.append(float(sc))
    return out


def tag_token(row):
    """One shadow tag. Seeded or empty provenance zeros conviction and drops burst/exit."""
    row = row or {}
    real, pushed = _counts(row)
    seeded = pushed >= SEED_MIN and pushed > real
    ok = (not seeded) and real >= 1
    burst = exit_tag = None
    b = row.get("burst") if isinstance(row.get("burst"), dict) else None
    if ok and b:
        span = b.get("span_s")
        span_ok = span is None or float(span) >= BURST_MIN_SPAN_S
        if (float(b.get("conviction") or 0) >= BURST_DELTA
                and int(b.get("wallets") or 0) >= BURST_MIN_WALLETS
                and span_ok):
            burst = "burst"
    e = row.get("exit") if isinstance(row.get("exit"), dict) else None
    if ok and e:
        sellers = e.get("sellers")
        sellers_ok = sellers is None or int(sellers) >= EXIT_MIN_SELLERS
        pct = e.get("exit_pct")
        if (str(e.get("state") or "") in EXIT_STATES and pct is not None
                and float(pct) >= EXIT_MIN and sellers_ok):
            exit_tag = "exit"
    rank = row.get("judgement_rank")
    return {
        "source": "fomo_radar_offline",
        "annotate_only": True,
        "sizes_money": False,
        "symbol": str(row.get("symbol") or "").upper(),
        "pair": str(row.get("pair") or "").upper().replace("_", "-"),
        "wallet_conviction": conviction(_real_scores(row)) if ok else 0.0,
        "provenance_ok": ok,
        "seeded": seeded,
        "burst": burst,
        "exit": exit_tag,
        "tags": [t for t in (burst, exit_tag) if t],
        "judgement_rank": None if rank is None else int(rank),
        "note": "annotate only; radar score never sizes money",
    }


def _tokens(doc):
    if isinstance(doc, list):
        return doc
    if not isinstance(doc, dict):
        return []
    if isinstance(doc.get("tokens"), list):
        return doc["tokens"]
    if doc.get("symbol") or doc.get("pair") or doc.get("holders"):
        return [doc]
    return []


def enrich(doc):
    """Fixture JSON → {symbols, pairs, tokens}. create_order is always false."""
    tags = [tag_token(t) for t in _tokens(doc)]
    symbols, pairs = {}, {}
    for t in tags:
        if t["symbol"]:
            symbols[t["symbol"]] = t
            if "-" not in t["symbol"]:
                symbols[t["symbol"] + "-USD"] = t
        if t["pair"]:
            pairs[t["pair"]] = t
    return {
        "source": "fomo_radar_offline",
        "annotate_only": True,
        "sizes_money": False,
        "create_order": False,
        "symbols": symbols,
        "pairs": pairs,
        "tokens": tags,
    }


def load_fixture(path=None):
    with open(path or DEFAULT_FIXTURE) as f:
        return json.load(f)


def enrich_path(path=None):
    return enrich(load_fixture(path))


def _lookup(enr, key):
    if not key:
        return None
    k = str(key).upper().replace("_", "-")
    symbols = (enr or {}).get("symbols") or {}
    pairs = (enr or {}).get("pairs") or {}
    if k in pairs:
        return pairs[k]
    if k in symbols:
        return symbols[k]
    base = k.split("-")[0]
    return symbols.get(base) or pairs.get(base + "-USDT")


def stamp_paper(equity, enr):
    """Copy a crypto_paper equity doc and attach fomo_radar. Qty, px, cash, pnl stay."""
    doc = dict(equity or {})
    tag = _lookup(enr, doc.get("pair"))
    doc["fomo_radar"] = tag
    doc["fomo_radar_enriched"] = tag is not None
    if "fills" in doc:
        doc["fills"] = [
            {**f, "fomo_radar": _lookup(enr, f.get("pair") or doc.get("pair"))}
            for f in (doc.get("fills") or [])
        ]
    return doc


def stamp_signal(signal_doc, enr):
    """Copy a MiroFish signal doc. score/direction/confidence/weight stay put."""
    doc = dict(signal_doc or {})
    out, hit = {}, False
    for k, v in (doc.get("signals") or {}).items():
        blob = dict(v) if isinstance(v, dict) else {"direction": v}
        tag = _lookup(enr, k)
        if tag:
            blob["fomo_radar"] = tag
            hit = True
        out[k] = blob
    doc["signals"] = out
    doc["fomo_radar_enriched"] = hit
    return doc
