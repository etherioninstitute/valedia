"""H2: Buffett/Lynch/Hayes checklists as MiroFish/intel advisory lenses.

Deterministic. No LLM, venue keys, Kelly, fusion-weight change, or Intents.
Checklists from council/advisors (ai-fund MIT harvest). Honest abstain when
fundamentals/PEG/liquidity series are missing — do not invent DCF.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import valedia_config as vc

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
_MODELS = os.path.join(ROOT, "models")
_OUT = os.path.join(_MODELS, "persona_lens_scores.json")

LENS_IDS = ("buffett", "lynch", "hayes")
FEAR, GREED = 35.0, 70.0
EQUITY_HINTS = frozenset({
    "SPY", "QQQ", "DIA", "IWM", "NVDA", "AAPL", "MSFT", "GOOGL", "TSLA",
    "META", "AMZN", "COIN", "MSTR", "GLD", "AMD", "NFLX", "JPM", "BRK.B",
})


def _now():
    return datetime.now(timezone.utc).isoformat()


def _f(v, default=None):
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def canon_symbol(s):
    t = str(s or "").strip().upper().replace("/", "-")
    if t.endswith("USD") and "-" not in t and len(t) > 3:
        return t[:-3] + "-USD"
    return t


def is_crypto(sym):
    t = canon_symbol(sym)
    base = t[:-4] if t.endswith("-USD") else t
    if base in EQUITY_HINTS or t in EQUITY_HINTS:
        return False
    return t.endswith("-USD") or t in ("BTC", "ETH", "SOL")


def _lens(lid, verdict, score, abstain, reasons, confidence):
    return {
        "id": lid,
        "verdict": verdict,
        "score": round(float(score), 1),
        "abstain": bool(abstain),
        "confidence": round(float(confidence), 2),
        "reasons": list(reasons),
    }


def _bias(ctx):
    return _f(ctx.get("overall_bias"), _f(ctx.get("macro_bias")))


def _event_tilt(ctx):
    n_on = n_off = 0
    for e in ctx.get("events") or []:
        if not isinstance(e, dict):
            continue
        d = str(e.get("direction") or "").upper()
        if d == "RISK_ON":
            n_on += 1
        elif d == "RISK_OFF":
            n_off += 1
    return n_on, n_off


def buffett(sym, ctx):
    if is_crypto(sym):
        return _lens("buffett", "OUTSIDE_CIRCLE", 50, True,
                     ["crypto is not productive capital"], 0.9)
    if not (ctx.get("fundamentals") or ctx.get("owner_earnings") or ctx.get("pe")):
        b = _bias(ctx)
        if b is None:
            return _lens("buffett", "INSUFFICIENT_DATA", 50, True,
                         ["no owner earnings / moat / margin of safety inputs"], 0.3)
        if b < FEAR:
            return _lens("buffett", "GREEDY_WHEN_FEARFUL", 58, False,
                         ["crowd fear; still no moat or intrinsic value"], 0.25)
        if b > GREED:
            return _lens("buffett", "FEARFUL_WHEN_GREEDY", 42, False,
                         ["crowd greed; cash is optionality until IV exists"], 0.25)
        return _lens("buffett", "INSUFFICIENT_DATA", 50, True,
                     ["equity without fundamentals; no buy"], 0.3)
    return _lens("buffett", "INSUFFICIENT_DATA", 50, True,
                 ["fundamentals present but DCF not computed this slice"], 0.2)


def lynch(sym, ctx):
    if is_crypto(sym):
        return _lens("lynch", "NOT_GARP", 50, True,
                     ["PEG needs earnings; crypto uncategorized"], 0.7)
    if ctx.get("peg") is None and ctx.get("earnings_growth") is None:
        buzz = _f(ctx.get("buzz_score"))
        if buzz is not None and buzz >= 70:
            return _lens("lynch", "STORY_UNCONFIRMED", 55, True,
                         ["high buzz; PEG unknown — observation is not a thesis"], 0.3)
        return _lens("lynch", "INSUFFICIENT_DATA", 50, True,
                     ["no PEG / earnings growth to categorize"], 0.3)
    return _lens("lynch", "INSUFFICIENT_DATA", 50, True,
                 ["growth fields present but PEG not computed this slice"], 0.2)


def hayes(sym, ctx):
    if not is_crypto(sym):
        return _lens("hayes", "OUTSIDE_CIRCLE", 50, True,
                     ["Hayes lens is crypto-macro, not equity GARP"], 0.8)
    b = _bias(ctx)
    n_on, n_off = _event_tilt(ctx)
    neural = (ctx.get("neural") or {}) if isinstance(ctx.get("neural"), dict) else {}
    regime = str((neural.get("regime") or {}).get("label") or "").lower()
    reasons = []
    tilt = 0
    if n_on > n_off:
        tilt += 1
        reasons.append("event RISK_ON > RISK_OFF")
    elif n_off > n_on:
        tilt -= 1
        reasons.append("event RISK_OFF > RISK_ON")
    if b is not None:
        if b > GREED:
            tilt += 1
            reasons.append(f"overall_bias {b} greedy/liquidity")
        elif b < FEAR:
            tilt -= 1
            reasons.append(f"overall_bias {b} fearful")
    if "bear" in regime or "risk-off" in regime:
        tilt -= 1
        reasons.append(f"neural regime {regime}")
    elif "bull" in regime or "risk-on" in regime:
        tilt += 1
        reasons.append(f"neural regime {regime}")
    if tilt > 0:
        return _lens("hayes", "STRUCTURAL_LONG", 50 + min(8, 3 * tilt), False,
                     reasons or ["crypto-macro tilt long"], 0.35)
    if tilt < 0:
        return _lens("hayes", "CAUTION", 50 + max(-8, 3 * tilt), False,
                     reasons or ["crypto-macro tilt cautious"], 0.35)
    return _lens("hayes", "HOLD", 50, True,
                 reasons or ["no liquidity/event tilt; not a leveraged bet"], 0.25)


def score_symbol(sym, ctx):
    ctx = dict(ctx or {})
    return {
        "buffett": buffett(sym, ctx),
        "lynch": lynch(sym, ctx),
        "hayes": hayes(sym, ctx),
    }


def score_document(signal_doc):
    doc = signal_doc if isinstance(signal_doc, dict) else {}
    ctx_base = {
        "overall_bias": doc.get("overall_bias"),
        "macro_bias": doc.get("macro_bias"),
        "events": doc.get("events") or [],
    }
    symbols = {}
    sigs = doc.get("signals") or {}
    if isinstance(sigs, dict):
        for k, v in sigs.items():
            blob = dict(v) if isinstance(v, dict) else {}
            ctx = {**ctx_base, **blob}
            symbols[canon_symbol(k)] = score_symbol(k, ctx)
    return {
        "version": "h2",
        "source": "persona_lenses",
        "lenses": list(LENS_IDS),
        "ts": _now(),
        "mirofish_weight_unchanged": True,
        "fusion_touched": False,
        "intents": [],
        "auto_raise": False,
        "valedia_version": vc.VALEDIA_VERSION,
        "symbols": symbols,
    }


def attach_to_signal(signal_doc, scores=None):
    """Stamp per-symbol persona lenses. Does not change score/direction/bias/weight."""
    doc = dict(signal_doc or {})
    sigs = dict(doc.get("signals") or {})
    pack = scores if isinstance(scores, dict) and scores.get("symbols") else score_document(doc)
    table = pack.get("symbols") or {}
    out = {}
    any_hit = False
    for k, v in sigs.items():
        blob = dict(v) if isinstance(v, dict) else {"direction": v}
        lenses = table.get(canon_symbol(k)) or table.get(k)
        if lenses:
            blob["persona"] = lenses
            any_hit = True
        out[k] = blob
    doc["signals"] = out
    doc["persona_lenses"] = any_hit
    return doc


def seed_section(scores=None, signal_doc=None):
    pack = scores if isinstance(scores, dict) and scores.get("symbols") else score_document(signal_doc or {})
    symbols = pack.get("symbols") or {}
    if not symbols:
        return ""
    lines = []
    for sym, lenses in sorted(symbols.items()):
        bits = []
        for lid in LENS_IDS:
            L = lenses.get(lid) or {}
            bits.append(f"{lid}={L.get('verdict')}")
        lines.append(f"- **{sym}**: " + "; ".join(bits) + ".")
    return (
        "## Persona lenses (advisory, not an order)\n"
        "Buffett/Lynch/Hayes checklists. Not a live order, not Kelly, "
        "not a fusion-weight change. Abstain means insufficient data or "
        "outside circle of competence.\n" + "\n".join(lines)
    )


def apply_seed_text(text, scores=None, signal_doc=None):
    extra = seed_section(scores, signal_doc)
    if not extra:
        return text
    return (text or "").rstrip() + "\n\n" + extra + "\n"


def write_scores(obj, path=None):
    path = path or _OUT
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)
    return path
