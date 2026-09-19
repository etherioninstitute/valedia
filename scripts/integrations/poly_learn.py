"""POLY-LEARN: Polymarket US paper classroom.

Read-only HTTP (POLYMARKET_US_HTTP). Never create_order.
Proposals labeled DRY/SHADOW. POLYMARKET_US_LIVE stays False.
(c) GOATWORTHY Post LLC.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import polymarket_us_client as pmu
import valedia_config as vc

STATUS = "DRY/SHADOW"
PLATFORM = "polymarket_us"
OUT_NAME = "poly_us_proposals.json"
PAPER_NOTIONAL = 5.0
_MODELS = os.path.expanduser("~/.openclaw/workspace/models")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _load_json(path):
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _market_rows(doc):
    if not isinstance(doc, dict):
        return []
    rows = doc.get("markets") or doc.get("data") or []
    if isinstance(rows, dict):
        rows = rows.get("markets") or []
    return [r for r in rows if isinstance(r, dict)]


def cheap_tags(market, models_dir=None):
    """Nav + cached MiroFish/persona. No Ollama. Ears stay on."""
    m = market if isinstance(market, dict) else {}
    try:
        import polymarket_entertainment as pe
        info = pe.classify_market(m)
    except Exception:
        info = {"category": "OTHER", "tags": []}
    out = {
        "nav": info.get("category") or "OTHER",
        "nav_tags": info.get("tags") or [],
        "mirofish": False,
        "neural_regime": None,
        "persona": None,
    }
    if not models_dir:
        return out
    neural = _load_json(os.path.join(models_dir, "mirofish_neural_enrichment.json"))
    if neural:
        out["mirofish"] = True
        regime = neural.get("regime") if isinstance(neural.get("regime"), dict) else {}
        out["neural_regime"] = regime.get("label") or neural.get("regime_label")
        if out["neural_regime"] is None:
            syms = neural.get("symbols") if isinstance(neural.get("symbols"), dict) else {}
            first = next(iter(syms.values()), {}) if syms else {}
            if isinstance(first, dict):
                r = first.get("regime") if isinstance(first.get("regime"), dict) else {}
                out["neural_regime"] = r.get("label")
    persona = _load_json(os.path.join(models_dir, "persona_lens_scores.json"))
    if persona:
        out["persona"] = "cached"
        out["persona_fusion_touched"] = bool(persona.get("fusion_touched"))
    return out


def gather(client, limit=8, models_dir=None):
    """Public list + market-by-slug + BBO. Fail-open per market."""
    mkts = client.list_markets(limit=limit, active=True)
    opps = []
    for row in _market_rows(mkts)[:limit]:
        slug = row.get("slug") or row.get("marketSlug")
        if not slug:
            continue
        try:
            mkt = client.get_market(slug)
        except Exception:
            mkt = row
        if not isinstance(mkt, dict) or mkt.get("status") == 404:
            mkt = row
        try:
            bbo = client.get_bbo(slug)
        except Exception:
            bbo = {}
        sized = pmu.sizing_from_bbo(bbo if isinstance(bbo, dict) else {})
        if not sized.get("ok"):
            try:
                sized = pmu.sizing_from_bbo(client.get_book(slug))
            except Exception:
                pass
        if not sized.get("ok"):
            continue
        blob = mkt if isinstance(mkt, dict) else row
        tags = cheap_tags(blob, models_dir=models_dir)
        ask = float(sized["ask"])
        title = str(blob.get("question") or blob.get("title") or slug)[:80]
        opps.append({
            "platform": PLATFORM,
            "type": "POLY_US_LEARN",
            "ticker": str(slug),
            "title": title,
            "side": "buy",
            "price": ask,
            "bid": sized.get("bid"),
            "ask": ask,
            "spread": sized.get("spread"),
            "ev_per_dollar": 0.0,
            "hours_to_resolve": None,
            "confidence": 0.0,
            "tags": tags,
            "mode": STATUS,
            "status": STATUS,
            "create_order": False,
            "raw": {"slug": slug, "bbo_ok": True},
        })
    return opps


def propose(opps, paper_usd=None):
    """Paper allocations. Status never create_order."""
    cap = float(paper_usd if paper_usd is not None else
                getattr(vc, "TREASURY_FLOOR_POLYMARKET_US", 25.0) or 25.0)
    remaining = cap
    out = []
    for o in opps or []:
        if remaining < 0.50:
            break
        alloc = min(PAPER_NOTIONAL, remaining)
        remaining -= alloc
        qty = int(alloc / (float(o.get("price") or 1) + 1e-9)) or 1
        out.append({
            "platform": PLATFORM,
            "type": o.get("type") or "POLY_US_LEARN",
            "ticker": o.get("ticker"),
            "title": o.get("title"),
            "side": o.get("side") or "buy",
            "price": o.get("price"),
            "allocation": round(alloc, 2),
            "quantity": qty,
            "status": STATUS,
            "mode": STATUS,
            "create_order": False,
            "tags": o.get("tags") or {},
            "note": "POLY-LEARN classroom; POLYMARKET_US_LIVE stays False",
        })
    return out


def write_proposals(doc, models_dir=None):
    md = models_dir or _MODELS
    os.makedirs(md, exist_ok=True)
    path = os.path.join(md, OUT_NAME)
    with open(path, "w") as f:
        json.dump(doc, f, indent=2, default=str)
    return path


def run(client=None, models_dir=None, limit=8, paper_usd=None):
    """Gather → propose → write models/poly_us_proposals.json. Never create."""
    md = models_dir or _MODELS
    err = None
    opps = []
    try:
        c = client or pmu.PolymarketUSClient(live=False)
        if getattr(c, "live", False):
            raise RuntimeError("fence: POLYMARKET_US_LIVE must stay False")
        opps = gather(c, limit=limit, models_dir=md)
    except Exception as e:
        err = str(e)[:200]
    proposals = propose(opps, paper_usd=paper_usd)
    doc = {
        "ts": _now(),
        "valedia_version": vc.VALEDIA_VERSION,
        "status": STATUS,
        "mode": STATUS,
        "create_order": False,
        "live": False,
        "polymarket_us_live": False,
        "n": len(proposals),
        "proposals": proposals,
        "error": err,
        "note": "paper classroom; preview ok; create blocked",
    }
    path = write_proposals(doc, models_dir=md)
    doc["path"] = path
    return doc


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, default=str))
