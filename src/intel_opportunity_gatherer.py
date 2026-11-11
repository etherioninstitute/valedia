"""Slice 10 — MiroFish + in-tree intel → gated never-dispatchable Intents.

Local JSON only. No HTTP/keys/orders. ledger_source=paper. Writer=valedia.
Slice 13: MiroFish blend uses effective (capped) weight from the fee-true ledger.
(c) GOATWORTHY Post LLC.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone, timedelta

import valedia_config as vc
from desk_roles import label_shadow
from nautilus_intent import enqueue_intent, make_intent

_MODELS = os.path.expanduser("~/.openclaw/workspace/models")

# Core listed crypto (brain/rebalancer universe). Everything else is a new-listing
# *candidate* — watch, not a live buy.
LISTED_CRYPTO = frozenset({
    "BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "ADA-USD", "AVAX-USD",
    "LINK-USD", "DOGE-USD", "DOT-USD", "UNI-USD",
})
CRYPTO_BASES = frozenset({
    "BTC", "ETH", "SOL", "XRP", "ADA", "AVAX", "LINK", "DOGE", "DOT", "UNI",
    "NEAR", "ZEC", "ATOM", "LTC", "BCH", "APT", "SUI", "OP", "ARB", "PEPE",
    "SHIB", "TAO", "INJ", "TIA", "SEI", "ONDO", "RENDER",
})
EQUITY_HINTS = frozenset({
    "SPY", "QQQ", "NVDA", "AAPL", "TSLA", "META", "AMD", "GOOGL", "GLD",
    "MARA", "UPST", "BYND", "PYPL", "NKE", "BABA", "INTC", "DIS", "HOOD", "COIN",
})
BUY_SCORE = 65.0
SELL_SCORE = 35.0
SHADOW_NOTIONAL = 5.0
ARB_MAX_AGE_H = 24.0
SCANNER_MAX_AGE_H = 12.0
SCANNER_EDGE_MIN = 50.0
HOT_BUZZ_MIN = 70.0


def _load(path):
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f) or {}
    except Exception:
        return {}


def _now():
    return datetime.now(timezone.utc)


def _parse_ts(raw):
    s = str(raw or "").strip()
    if not s:
        return None
    s = s.replace(" UTC", "").replace("Z", "+00:00")
    if "T" not in s and " " in s:
        s = s.replace(" ", "T")
        if "+" not in s:
            s += "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _fresh(ts, max_hours):
    dt = _parse_ts(ts)
    if dt is None:
        return False
    return (_now() - dt) <= timedelta(hours=max_hours)


def _norm_ticker(raw):
    t = str(raw or "").strip().upper()
    if not t:
        return t
    if t.endswith("-USD"):
        return t
    if t in CRYPTO_BASES:
        return f"{t}-USD"
    return t


def _is_crypto(ticker):
    t = _norm_ticker(ticker)
    if t in EQUITY_HINTS:
        return False
    return t.endswith("-USD") or t.replace("-USD", "") in CRYPTO_BASES


def _side_from_score(score):
    if score > BUY_SCORE:
        return "buy"
    if score < SELL_SCORE:
        return "sell"
    return None


def _edge_pct_from_score(score):
    # Allocator conservative: |score-50|/50 * 15 (crypto) — percent, not fraction.
    return abs(float(score) - 50.0) / 50.0 * 15.0


def _score_of(blob):
    if not isinstance(blob, dict):
        return None
    for k in ("fused_score", "composite_score", "score", "buzz_score"):
        if blob.get(k) is not None:
            try:
                return float(blob[k])
            except (TypeError, ValueError):
                pass
    return None


def _opp(kind, strategy, venue, instrument, side, score, edge_pct, feed, raw,
         price=None):
    return {
        "kind": kind,
        "strategy": strategy,
        "venue": venue,
        "instrument": instrument,
        "side": side,
        "score": round(float(score), 2),
        "edge_pct": round(float(edge_pct), 4),
        "price": price,
        "notional": SHADOW_NOTIONAL,
        "source_feed": feed,
        "raw": raw or {},
    }


def _mf_weight():
    try:
        return float(vc.effective_mirofish_weight())
    except Exception:
        return float(getattr(vc, "MIROFISH_WEIGHT", 0.10))


def _mf_accuracy(models_dir=None):
    try:
        import mirofish_accuracy_ledger as mal
        return mal.read_summary(models_dir)
    except Exception:
        return {}


def collect_mirofish(doc, listed=LISTED_CRYPTO, accuracy=None):
    """MiroFish swarm JSON → listed crypto / equity / new-listing candidates."""
    out = []
    signals = (doc or {}).get("signals") or {}
    mf_w = _mf_weight()
    acc = accuracy if isinstance(accuracy, dict) else {}
    for raw_tk, sig in signals.items():
        if not isinstance(sig, dict):
            continue
        score = _score_of(sig)
        if score is None:
            continue
        side = _side_from_score(score)
        if side is None:
            continue
        tk = _norm_ticker(raw_tk)
        if _is_crypto(tk):
            kind = "crypto_listed" if tk in listed else "crypto_new_listing"
            strategy = "CRYPTO_BRACKET" if kind == "crypto_listed" else "NEW_LISTING"
            venue = "coinbase"
        else:
            kind, strategy, venue = "stock", "STOCK_BUY", "alpaca"
            if side != "buy":
                # Alpaca shadow is long-only here (no short Intent from intel).
                continue
        out.append(_opp(kind, strategy, venue, tk, side, score,
                        _edge_pct_from_score(score), "mirofish",
                        {**sig, "mirofish_weight": mf_w,
                         "mirofish_proven": bool(acc.get("proven")),
                         "mirofish_accuracy": acc.get("directional_accuracy"),
                         "mirofish_n_scored": acc.get("n_scored")}))
    return out


def collect_fusion(doc, listed=LISTED_CRYPTO):
    """latest_fusion.json — listed crypto + Alpaca universe. Both shapes."""
    out = []
    items = []
    signals = (doc or {}).get("signals")
    if isinstance(signals, dict):
        for tk, sig in signals.items():
            if isinstance(sig, dict):
                items.append((tk, sig))
    for row in (doc or {}).get("results") or []:
        if isinstance(row, dict):
            items.append((row.get("ticker") or row.get("asset"), row))
    for raw_tk, sig in items:
        score = _score_of(sig)
        if score is None:
            continue
        side = _side_from_score(score)
        if str(sig.get("signal") or "").upper() == "HOLD":
            side = _side_from_score(score)
        if side is None:
            continue
        tk = _norm_ticker(raw_tk)
        if _is_crypto(tk):
            kind = "crypto_listed" if tk in listed else "crypto_new_listing"
            strategy = "CRYPTO_BRACKET" if kind == "crypto_listed" else "NEW_LISTING"
            venue = "coinbase"
        else:
            if side != "buy":
                continue
            kind, strategy, venue = "stock", "STOCK_BUY", "alpaca"
        out.append(_opp(kind, strategy, venue, tk, side, score,
                        _edge_pct_from_score(score), "fusion", sig))
    return out


def collect_hot_list(doc, listed=LISTED_CRYPTO):
    """dynamic_universe hot_list.json — new-listing / discovery candidates."""
    out = []
    assets = (doc or {}).get("assets") or {}
    if isinstance(assets, list):
        assets = {a.get("ticker"): a for a in assets if isinstance(a, dict)}
    for raw_tk, row in assets.items():
        if not isinstance(row, dict):
            continue
        buzz = _score_of(row)
        if buzz is None or buzz < HOT_BUZZ_MIN:
            continue
        tk = _norm_ticker(row.get("ticker") or raw_tk)
        klass = str(row.get("class") or "").lower()
        plat = str(row.get("platform") or "").lower()
        crypto = _is_crypto(tk) or klass == "crypto" or plat == "coinbase" or row.get("cg_id")
        if crypto:
            if tk in listed:
                continue  # listed handled by mirofish/fusion
            out.append(_opp("crypto_new_listing", "NEW_LISTING", "coinbase", tk,
                            "buy", min(buzz, 100.0), max(2.0, (buzz - 50) / 10.0),
                            "hot_list", row))
        else:
            out.append(_opp("stock", "STOCK_BUY", "alpaca", tk, "buy",
                            min(buzz, 100.0), max(1.0, (buzz - 50) / 20.0),
                            "hot_list", row))
    return out


def collect_hedge(doc):
    """hedge_fund_intel.json — Alpaca stock candidates (no HTTP)."""
    out = []
    for row in (doc or {}).get("short_squeeze") or []:
        if not isinstance(row, dict) or not row.get("ticker"):
            continue
        tk = _norm_ticker(row["ticker"])
        si = float(row.get("short_pct") or 0)
        if si < 15:
            continue
        score = min(50.0 + si, 85.0)
        out.append(_opp("stock", "STOCK_BUY", "alpaca", tk, "buy", score,
                        max(1.5, si / 10.0), "hedge_squeeze", row))
    for row in (doc or {}).get("sentiment_divergence") or []:
        if not isinstance(row, dict) or not row.get("ticker"):
            continue
        tk = _norm_ticker(row["ticker"])
        chg = float(row.get("change52w") or 0)
        score = min(50.0 + abs(chg) / 2.0, 80.0)
        out.append(_opp("stock", "STOCK_BUY", "alpaca", tk, "buy", score,
                        max(1.0, abs(chg) / 25.0), "hedge_divergence", row))
    return out


def collect_scanner(doc):
    """Kalshi scanner_results.json — markets with heuristic edge."""
    out = []
    if not _fresh((doc or {}).get("ts"), SCANNER_MAX_AGE_H):
        return out
    for m in (doc or {}).get("macro") or []:
        if not isinstance(m, dict):
            continue
        edge = float(m.get("edge_score") or 0)
        if edge < SCANNER_EDGE_MIN:
            continue
        ticker = str(m.get("ticker") or "")
        if not ticker:
            continue
        yes_ask = m.get("yes_ask")
        mid = m.get("mid")
        px = None
        try:
            px = float(yes_ask) / 100.0 if yes_ask is not None else float(mid) / 100.0
        except (TypeError, ValueError):
            px = 0.50
        if px <= 0 or px >= 1:
            continue
        side = "buy" if (m.get("mid") or 50) >= 50 else "sell"
        out.append(_opp("kalshi", "SCANNER_MACRO", "kalshi", ticker, side,
                        min(edge, 100.0), edge / 10.0, "scanner", m, price=px))
    return out


def collect_hl_public(doc):
    """models/hl_public_info.json — public /info only. Never a fill."""
    from hl_public_info import opportunities
    return opportunities(doc, notional=SHADOW_NOTIONAL)


def collect_pm_entertainment(doc):
    """models/polymarket_entertainment.json — Oscars/awards/culture. Shadow."""
    from polymarket_entertainment import opportunities
    return opportunities(doc, notional=SHADOW_NOTIONAL)


def collect_arb(doc):
    """latency_arb_log.json — *candidates* only. Always extra-shadow later."""
    out = []
    trades = (doc or {}).get("trades") or (doc or {}).get("candidates") or []
    if isinstance(doc, list):
        trades = doc
    for t in trades:
        if not isinstance(t, dict):
            continue
        if not _fresh(t.get("ts") or (t.get("signal") or {}).get("timestamp"), ARB_MAX_AGE_H):
            continue
        ticker = str(t.get("ticker") or "")
        if not ticker:
            continue
        cents = t.get("price_cents")
        try:
            px = float(cents) / 100.0 if cents is not None else float(t.get("price") or 0)
        except (TypeError, ValueError):
            px = 0.0
        if px <= 0 or px >= 1:
            continue
        edge_c = float(t.get("edge_cents") or 0)
        side_raw = str(t.get("side") or "yes").lower()
        side = "buy" if side_raw in ("yes", "buy") else "sell"
        edge_pct = (edge_c / max(cents or (px * 100), 1)) * 100.0
        out.append(_opp("arb", "LATENCY_ARB", "kalshi", ticker, side,
                        min(50.0 + abs(edge_c), 90.0), max(edge_pct, 0.5),
                        "arb", t, price=px))
    return out


def _blend_key(o):
    return (o["venue"], o["instrument"], o["side"], o["kind"])


def merge_opps(opps):
    """Dedupe. MiroFish blend uses effective (capped) weight when fusion is present."""
    mf_w = _mf_weight()
    buckets = {}
    for o in opps:
        buckets.setdefault(_blend_key(o), []).append(o)
    out = []
    for _, group in buckets.items():
        feeds = {g["source_feed"]: g for g in group}
        if "fusion" in feeds and "mirofish" in feeds:
            base = dict(feeds["fusion"])
            mf = feeds["mirofish"]
            base["score"] = round(base["score"] * (1.0 - mf_w) + mf["score"] * mf_w, 2)
            base["edge_pct"] = round(
                base["edge_pct"] * (1.0 - mf_w) + mf["edge_pct"] * mf_w, 4)
            base["source_feed"] = "fusion+mirofish"
            base["raw"] = {"fusion": feeds["fusion"]["raw"], "mirofish": mf["raw"]}
            out.append(base)
        else:
            # Prefer fusion > mirofish > others by score.
            group.sort(key=lambda g: (
                0 if g["source_feed"] == "fusion" else
                1 if g["source_feed"] == "mirofish" else 2,
                -g["score"],
            ))
            out.append(group[0])
    return out


def _fee_gate(opp):
    """Fee-true, static table only (no HTTP cache)."""
    from fee_engine import is_trade_profitable, roundtrip_cost
    venue = opp["venue"]
    notional = float(opp["notional"])
    edge = float(opp["edge_pct"])
    if venue in ("coinbase", "alpaca"):
        rt = roundtrip_cost(venue, notional, use_static=True)
        net = notional * (edge / 100.0) - rt["total_roundtrip"]
        return {
            "profitable": net > 0,
            "net_profit": round(net, 4),
            "fee_pct": rt["roundtrip_pct"],
        }
    return is_trade_profitable(
        venue, notional, edge, price=opp.get("price"), use_static=True)


def _paused(strategy):
    try:
        from pnl_tracker import is_strategy_paused
        return bool(is_strategy_paused(strategy))
    except Exception:
        return False


def _ere_level(models_dir):
    """Canonical ERE v3 reader. Fail-closed: missing/invalid state -> HALT."""
    try:
        from ere_runtime import read_gate
        return str(read_gate("BOOK", models_dir=models_dir).get("risk_level", "HALT")).upper() or "HALT"
    except Exception:
        return "HALT"


def evaluate_gates(opp, models_dir=_MODELS, ere_level=None):
    """Fee-true + category + learning-pause + ERE. live_flag always False."""
    ere = ere_level if ere_level is not None else _ere_level(models_dir)
    fee = _fee_gate(opp)
    pause = _paused(opp["strategy"])
    cat_ok, cat_why = True, "unproven/thin"
    try:
        import forward_test_ledger as ftl
        cat_ok, cat_why = ftl.should_trade_category(opp["strategy"])
    except Exception as e:
        cat_ok, cat_why = True, f"gate error -> allow: {str(e)[:40]}"
    if pause:
        cat_ok = False
        cat_why = "paused until (learning TTL; not permanent)"
    return {
        "ere_level": ere,
        "ere_ok": ere not in ("HALT", "DANGER"),
        "fee_ok": bool(fee.get("profitable")),
        "category_ok": bool(cat_ok),
        "blacklist_ok": (not pause),
        "exec_dry_run": True,
        "live_flag": False,
        "gate_note": "slice10_intel_shadow",
        "category_reason": cat_why,
        "fee_net": fee.get("net_profit"),
    }


def opp_to_intent(opp, gates):
    g = dict(gates or {})
    g["exec_dry_run"] = True
    g["live_flag"] = False
    n = float(opp["notional"])
    return make_intent(
        strategy=str(opp["strategy"]),
        venue=str(opp["venue"]),
        instrument=str(opp["instrument"]),
        side=str(opp["side"]),
        order_type="market",
        tif="IOC",
        notional=n,
        qty=None,
        max_notional=n,
        max_qty=n,
        gates=g,
        ledger_source="paper",
    )


def gather(models_dir=_MODELS, listed=None):
    """Collect opportunities from local intel files. No HTTP. No ledger writes."""
    listed = listed if listed is not None else LISTED_CRYPTO
    md = models_dir
    opps = []
    try:
        import mirofish_intel_archive as _mia
        mf_doc = _mia.load_for_fv(os.path.join(md, "mirofish_signal.json"))
    except Exception:
        mf_doc = None
    opps.extend(collect_mirofish(mf_doc or {}, listed, accuracy=_mf_accuracy(md)))
    opps.extend(collect_fusion(_load(os.path.join(md, "latest_fusion.json")), listed))
    opps.extend(collect_hot_list(_load(os.path.join(md, "hot_list.json")), listed))
    opps.extend(collect_hedge(_load(os.path.join(md, "hedge_fund_intel.json"))))
    opps.extend(collect_scanner(_load(os.path.join(md, "scanner_results.json"))))
    opps.extend(collect_arb(_load(os.path.join(md, "latency_arb_log.json"))))
    opps.extend(collect_hl_public(_load(os.path.join(md, "hl_public_info.json"))))
    opps.extend(collect_pm_entertainment(
        _load(os.path.join(md, "polymarket_entertainment.json"))))
    return merge_opps(opps)


def emit_shadow_intents(models_dir=_MODELS, queue_path=None, opps=None,
                        dispatch=False):
    """Enqueue never-dispatchable Slice-1 Intents. dispatch is ignored (always False)."""
    if not getattr(vc, "INTEL_SHADOW_INTENTS", True):
        return []
    dispatch = False  # this module cannot request sidecar send
    models_dir = models_dir or _MODELS
    opps = list(opps) if opps is not None else gather(models_dir=models_dir)
    ere = _ere_level(models_dir)
    recs = []
    for o in opps:
        gates = evaluate_gates(o, models_dir=models_dir, ere_level=ere)
        intent = opp_to_intent(o, gates)
        extra = {
            "shadow": True,
            "kind": o["kind"],
            "source_feed": o["source_feed"],
            "intel_edge_pct": o["edge_pct"],
        }
        if o.get("tags"):
            extra["opportunity_tags"] = list(o["tags"])
        extra.update(label_shadow(gates, venue=o["venue"]))
        if o["kind"] == "arb":
            extra["arb_candidate"] = True
        rec = enqueue_intent(
            intent, dispatch=dispatch, queue_path=queue_path, extra=extra)
        recs.append(rec)
    return recs


if __name__ == "__main__":
    recs = emit_shadow_intents()
    print(f"intel shadow intents: {len(recs)} (never-dispatchable)")
    for r in recs[:12]:
        print(f"  {r.get('status'):10} disp={r.get('dispatchable')} "
              f"{r.get('venue')} {r.get('instrument')} {r.get('side')} "
              f"{r.get('kind')} {r.get('validate_reason')}")

