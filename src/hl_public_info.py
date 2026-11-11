"""H5: public Hyperliquid /info snapshot. Zero keys. No Exchange. No /exchange.

Inspired by Galleon Labs hypergrok-trading-desk `opening_bell.py` (MIT).
Stdlib urllib. HTTP opt-in (HL_PUBLIC_HTTP=1 or post_fn). Tests inject.
Writes models/hl_public_info.json. Never a fill, never a writer.
(c) GOATWORTHY Post LLC.
"""
from __future__ import annotations

import json
import os
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

import valedia_config as vc

INFO_URL = "https://api.hyperliquid.xyz/info"
BANDS_BPS = (5, 10, 25)
LEVEL_CAP = 20
_MODELS = os.path.expanduser("~/.openclaw/workspace/models")
_POST = None  # tests inject; None → urllib only if HTTP on


def set_post_fn(fn):
    global _POST
    _POST = fn


def http_enabled():
    env = os.environ.get("HL_PUBLIC_HTTP")
    if env is not None and str(env).strip() != "":
        return str(env).strip().lower() in ("1", "true", "yes", "on")
    return False


def _decimal(value, field):
    try:
        n = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as e:
        raise ValueError(f"{field} is not numeric") from e
    if not n.is_finite():
        raise ValueError(f"{field} is not finite")
    return n


def post_info(payload, timeout=15.0, url=INFO_URL):
    if _POST is not None:
        return _POST(payload)
    if not http_enabled():
        raise RuntimeError("HL public HTTP off (HL_PUBLIC_HTTP unset)")
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json",
                 "User-Agent": "valedia-hl-public-info/0.30"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def _levels(payload, coin):
    levels = payload.get("levels") if isinstance(payload, dict) else None
    if not isinstance(levels, list) or len(levels) != 2 or not levels[0] or not levels[1]:
        raise ValueError(f"{coin} book has no two-sided depth")
    return levels


def _top(levels):
    bid = _decimal(levels[0][0].get("px"), "best bid")
    ask = _decimal(levels[1][0].get("px"), "best ask")
    if bid <= 0 or ask <= 0 or bid > ask:
        raise ValueError("order book top is invalid")
    return bid, ask, (bid + ask) / Decimal(2)


def _depth(levels, mid, band_bps):
    limit = Decimal(band_bps) / Decimal(10_000)
    total = Decimal(0)
    reach = Decimal(0)
    for lvl in levels:
        px = _decimal(lvl.get("px"), "book price")
        sz = _decimal(lvl.get("sz"), "book size")
        dist = abs(px - mid) / mid
        if dist > reach:
            reach = dist
        if dist <= limit:
            total += sz
    reach_bps = reach * Decimal(10_000)
    complete = len(levels) < LEVEL_CAP or Decimal(band_bps) <= reach_bps
    return total, complete


def _perp_ctx(meta, coin):
    if not isinstance(meta, list) or len(meta) != 2:
        raise ValueError("metaAndAssetCtxs unexpected shape")
    universe = meta[0].get("universe") or []
    ctxs = meta[1]
    idx = next((i for i, a in enumerate(universe) if a.get("name") == coin), None)
    if idx is None or idx >= len(ctxs):
        raise ValueError(f"{coin} not in perp universe")
    return ctxs[idx]


def build_snapshot(meta, book, coin, network="mainnet"):
    """One-page snapshot. Extra nSigFigs paging left in Galleon Labs original."""
    ctx = _perp_ctx(meta, coin)
    levels = _levels(book, coin)
    bid, ask, mid = _top(levels)
    mark = _decimal(ctx.get("markPx"), "markPx")
    prev = _decimal(ctx.get("prevDayPx"), "prevDayPx")
    funding = _decimal(ctx.get("funding"), "funding")
    oi = _decimal(ctx.get("openInterest"), "openInterest")
    ts_ms = int(book.get("time") or 0)
    observed = (datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
                if ts_ms > 0 else datetime.now(timezone.utc))
    depth = {}
    for band in BANDS_BPS:
        bsz, b_ok = _depth(levels[0], mid, band)
        asz, a_ok = _depth(levels[1], mid, band)
        depth[str(band)] = {
            "bid_base": str(bsz), "ask_base": str(asz),
            "bid_usd": str(bsz * mid), "ask_usd": str(asz * mid),
            "bid_complete": b_ok, "ask_complete": a_ok,
        }
    chg = (mark / prev - 1) * Decimal(100) if prev else Decimal(0)
    return {
        "mode": "read-only",
        "network": network,
        "source": {
            "endpoint": "/info",
            "requests": ["metaAndAssetCtxs", "l2Book"],
            "observed_at": observed.isoformat().replace("+00:00", "Z"),
            "attribution": "Galleon Labs hypergrok-trading-desk opening_bell (MIT rewrite)",
        },
        "market": f"{coin}-PERP",
        "coin": coin,
        "prices": {
            "book_mid": str(mid), "mark": str(mark),
            "oracle": str(_decimal(ctx.get("oraclePx"), "oraclePx")),
            "previous_day": str(prev), "change_24h_pct": str(chg),
        },
        "funding": {
            "hourly_rate": str(funding),
            "hourly_pct": str(funding * Decimal(100)),
            "annualized_simple_pct": str(funding * Decimal(24 * 365 * 100)),
        },
        "activity": {
            "open_interest_base": str(oi),
            "open_interest_usd": str(oi * mark),
            "volume_24h_usd": str(_decimal(ctx.get("dayNtlVlm"), "dayNtlVlm")),
        },
        "book": {
            "best_bid": str(bid), "best_ask": str(ask),
            "spread_bps": str((ask - bid) / mid * Decimal(10_000)),
            "depth": depth,
        },
        "safety": "No key. No account. No /exchange. VENUE_WRITER hyperliquid stays none.",
        "valedia_version": vc.VALEDIA_VERSION,
    }


def fetch_snapshot(coin="BTC", timeout=15.0):
    meta = post_info({"type": "metaAndAssetCtxs"}, timeout=timeout)
    book = post_info({"type": "l2Book", "coin": coin}, timeout=timeout)
    return build_snapshot(meta, book, coin)


def write_snapshot(snap, models_dir=None):
    md = models_dir or _MODELS
    os.makedirs(md, exist_ok=True)
    path = os.path.join(md, "hl_public_info.json")
    with open(path, "w") as f:
        json.dump(snap, f, indent=2)
        f.write("\n")
    return path


def load_snapshot(models_dir=None):
    path = os.path.join(models_dir or _MODELS, "hl_public_info.json")
    if not os.path.exists(path):
        return {}
    try:
        d = json.load(open(path))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def opportunities(snap, notional=5.0):
    """Public /info → gatherer opps. Positive funding = short bias. No size invention."""
    out = []
    if isinstance((snap or {}).get("markets"), list):
        rows = [m for m in snap["markets"] if isinstance(m, dict)]
    elif (snap or {}).get("coin") or (snap or {}).get("market"):
        rows = [snap]
    else:
        return out
    for row in rows:
        coin = str(row.get("coin") or "").strip().upper()
        if not coin and row.get("market"):
            coin = str(row["market"]).replace("-PERP", "").strip().upper()
        if not coin:
            continue
        funding = row.get("funding")
        rate = (funding.get("hourly_rate") if isinstance(funding, dict)
                else row.get("hourly_rate") or row.get("funding"))
        try:
            rate_f = float(rate)
        except (TypeError, ValueError):
            continue
        if rate_f <= 0:
            continue
        score = min(50.0 + min(abs(rate_f) * 10000.0, 40.0), 90.0)
        edge = max(0.5, min(abs(rate_f) * 100.0, 15.0))
        out.append({
            "kind": "hl_public", "strategy": "HL_PUBLIC", "venue": "hyperliquid",
            "instrument": coin, "side": "sell", "score": round(score, 2),
            "edge_pct": round(edge, 4), "price": None, "notional": notional,
            "source_feed": "hl_public", "raw": row,
        })
    return out
