#!/usr/bin/env python3
"""Multi-lens PAPER/SHADOW fight on MiroFish / opportunity inputs.

Lenses: buffett_value, momentum_kernel, mirofish_enriched, risk_veto.
Writes models/adversarial_round_*.json. H7 `arena_stress.py` scores vs fee-true FTL.

No venue orders. No LN/Arena sats. No fusion-weight change.
"""
import argparse
import json
import os
from datetime import datetime, timezone

import mirofish_neural_enrich as mne
import persona_lenses as pl
import valedia_config as vc

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_MODELS = os.path.join(ROOT, "models")
_SIGNAL = None
_ENRICH = None
_ERE = None

BUY, SELL, HOLD = 55.0, 45.0, 50.0
_f = mne._f
# ponytail: FTL winner/loser scoring is arena_stress.score_round (H7 wrap)
# ponytail: Buffett = persona_lenses.buffett (H2); no second checklist


def set_paths(models_dir=None, signal_path=None, enrich_path=None, ere_path=None):
    global _MODELS, _SIGNAL, _ENRICH, _ERE
    if models_dir:
        _MODELS = models_dir
    _SIGNAL = signal_path or os.path.join(_MODELS, "mirofish_signal.json")
    _ENRICH = enrich_path or os.path.join(_MODELS, "mirofish_neural_enrichment.json")
    _ERE = ere_path or os.path.join(_MODELS, "latest_etherion_risk.json")
    return _SIGNAL, _ENRICH, _ERE


set_paths()


def _now():
    return datetime.now(timezone.utc)


def _load(path):
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path) as f:
            d = json.load(f)
        return d if isinstance(d, dict) or isinstance(d, list) else {}
    except Exception:
        return {}


def _side_from_score(score):
    if score > BUY:
        return "BUY"
    if score < SELL:
        return "SELL"
    return "HOLD"


def _vote(lens, score, side, confidence, rationale, abstain=False, veto=False, verdict=None):
    return {
        "lens": lens,
        "score": round(_f(score, HOLD), 4),
        "side": str(side or "HOLD").upper(),
        "confidence": round(max(0.0, min(1.0, _f(confidence))), 4),
        "rationale": str(rationale or "")[:200],
        "abstain": bool(abstain),
        "veto": bool(veto),
        "verdict": verdict,
    }


def _opp(symbol, blob):
    blob = blob if isinstance(blob, dict) else {}
    score = _f(blob.get("score"), HOLD)
    direction = str(blob.get("direction") or blob.get("side") or "").upper()
    if direction not in ("BUY", "SELL", "HOLD"):
        direction = _side_from_score(score)
    conf = blob.get("confidence")
    return {
        "symbol": mne.canon_symbol(symbol),
        "score": score,
        "direction": direction,
        "confidence": _f(conf) if conf is not None else abs(score - HOLD) / HOLD,
        "asset_class": blob.get("asset_class"),
        "strategy": blob.get("strategy"),
        "raw": blob,
    }


def opportunities_from(signal_doc=None, opps=None):
    out, seen = [], set()
    if isinstance(opps, dict):
        opps = opps.get("opportunities") or opps.get("opps") or opps.get("items") or opps
    if isinstance(opps, list):
        for item in opps:
            if not isinstance(item, dict):
                continue
            o = _opp(item.get("symbol") or item.get("ticker") or item.get("asset"), item)
            if o["symbol"] and o["symbol"] not in seen:
                seen.add(o["symbol"])
                out.append(o)
    sigs = (signal_doc or {}).get("signals") if isinstance(signal_doc, dict) else None
    if isinstance(sigs, dict):
        for k, v in sigs.items():
            if not isinstance(v, dict):
                continue
            o = _opp(k, v)
            if o["symbol"] and o["symbol"] not in seen:
                seen.add(o["symbol"])
                out.append(o)
    return out


def lens_buffett_value(opp):
    L = pl.buffett(opp["symbol"], opp.get("raw") or {})
    score = _f(L.get("score"), HOLD)
    side = "HOLD" if L.get("abstain") else _side_from_score(score)
    why = L.get("verdict") or "HOLD"
    reasons = L.get("reasons") or []
    if reasons:
        why = why + ": " + "; ".join(str(r) for r in reasons[:2])
    return _vote("buffett_value", score, side, _f(L.get("confidence"), 0.3),
                 why, abstain=bool(L.get("abstain")), verdict=L.get("verdict"))


def lens_momentum_kernel(opp):
    score = _f(opp.get("score"), HOLD)
    side = _side_from_score(score)
    conf = min(1.0, abs(score - HOLD) / HOLD)
    neural = (opp.get("raw") or {}).get("neural") or {}
    regime = str((neural.get("regime") or {}).get("label") or "").lower()
    ndir = str((neural.get("signal") or {}).get("direction") or "").upper()
    if regime in ("bull", "trending_up", "trend_up") and side == "BUY":
        conf = min(1.0, conf + 0.1)
    if regime in ("bear", "trending_down", "trend_down") and side == "SELL":
        conf = min(1.0, conf + 0.1)
    if ndir in ("BUY", "SELL") and side in ("BUY", "SELL") and ndir != side:
        conf = max(0.0, conf - 0.15)
    return _vote("momentum_kernel", score, side, conf,
                 f"kernel-style follow score {score:.1f} → {side}")


def lens_mirofish_enriched(opp):
    score = _f(opp.get("score"), HOLD)
    side = str(opp.get("direction") or _side_from_score(score)).upper()
    if side not in ("BUY", "SELL", "HOLD"):
        side = _side_from_score(score)
    conf = _f(opp.get("confidence"), abs(score - HOLD) / HOLD)
    neural = (opp.get("raw") or {}).get("neural") or {}
    nsig = neural.get("signal") or {}
    ndir = str(nsig.get("direction") or "").upper()
    nq = nsig.get("quality")
    tag = "plain"
    if ndir in ("BUY", "SELL"):
        if ndir == side:
            conf = min(1.0, (0.6 * conf + 0.4 * min(1.0, abs(_f(nq)))) if nq is not None else conf + 0.1)
            tag = "neural_agree"
        elif side in ("BUY", "SELL"):
            conf = max(0.0, conf * 0.5)
            tag = "neural_disagree"
    return _vote("mirofish_enriched", score, side, conf,
                 f"mirofish {side} score {score:.1f} ({tag})")


def lens_risk_veto(opp, others, ctx):
    reasons = []
    fenced = vc.live_exec_fenced(
        opp.get("strategy") or (opp.get("raw") or {}).get("strategy"),
        ticker=opp.get("ticker") or (opp.get("raw") or {}).get("ticker"))
    if fenced:
        reasons.append("live-exec fenced strategy")
    ere = ctx.get("ere") if isinstance(ctx.get("ere"), dict) else {}
    if ere.get("safe") is False or str(ere.get("risk_level") or "").upper() in ("CRITICAL", "EXTREME", "HALT"):
        reasons.append("ERE halt")
    events = ctx.get("events") or []
    risk_off = any(
        str((e or {}).get("direction") or "").upper() == "RISK_OFF"
        for e in events if isinstance(e, dict)
    )
    buys = [v for v in others if v.get("side") == "BUY" and not v.get("abstain")]
    if risk_off and buys:
        reasons.append("RISK_OFF vs BUY")
    veto = bool(reasons)
    return _vote("risk_veto", HOLD, "HOLD", 0.8 if veto else 0.3,
                 ("VETO: " + "; ".join(reasons)) if veto else "pass (no veto)",
                 veto=veto, verdict="VETO" if veto else "PASS")


def fight_one(opp, ctx=None):
    ctx = ctx if isinstance(ctx, dict) else {}
    raw = dict(opp.get("raw") or {})
    for k in ("overall_bias", "macro_bias", "events"):
        if k in ctx and k not in raw:
            raw[k] = ctx[k]
    opp = dict(opp)
    opp["raw"] = raw
    b = lens_buffett_value(opp)
    m = lens_momentum_kernel(opp)
    f = lens_mirofish_enriched(opp)
    r = lens_risk_veto(opp, [b, m, f], ctx)
    lenses = [b, m, f, r]
    counts = {}
    directional = {}
    for v in (b, m, f):
        if v.get("abstain"):
            counts["ABSTAIN"] = counts.get("ABSTAIN", 0) + 1
            continue
        counts[v["side"]] = counts.get(v["side"], 0) + 1
        if v["side"] in ("BUY", "SELL"):
            directional[v["side"]] = directional.get(v["side"], 0) + 1
    if r.get("veto"):
        winner = {"lens": "risk_veto", "side": "HOLD", "score": HOLD}
        consensus = "HOLD"
    else:
        cands = [v for v in (b, m, f) if not v.get("abstain")]
        if not cands:
            winner = {"lens": "none", "side": "HOLD", "score": HOLD}
            consensus = "HOLD"
        else:
            w = max(cands, key=lambda v: abs(v["score"] - HOLD) * v["confidence"])
            winner = {"lens": w["lens"], "side": w["side"], "score": w["score"]}
            consensus = max(directional, key=directional.get) if directional else "HOLD"
    return {
        "symbol": opp["symbol"],
        "input": {k: opp[k] for k in ("symbol", "score", "direction", "confidence")},
        "lenses": lenses,
        "disagreement": len(directional) > 1,
        "side_counts": counts,
        "risk_veto": bool(r.get("veto")),
        "winner": winner,
        "consensus_side": consensus,
        "mode": "PAPER/SHADOW",
    }


def build_round(signal_doc=None, enrich=None, opps=None, ere=None, now=None):
    attached = mne.attach_to_signal(signal_doc or {}, enrich if enrich is not None else {})
    items = opportunities_from(attached, opps)
    if enrich and isinstance(enrich, dict):
        by = (enrich.get("symbols") or {}) if isinstance(enrich.get("symbols"), dict) else {}
        for o in items:
            slim = mne.slim_neural(by.get(o["symbol"]) or {})
            if slim:
                o["raw"] = dict(o.get("raw") or {})
                o["raw"]["neural"] = slim
    ctx = {
        "events": (attached.get("events") if isinstance(signal_doc, dict) else None) or [],
        "overall_bias": (signal_doc or {}).get("overall_bias") if isinstance(signal_doc, dict) else HOLD,
        "ere": ere if isinstance(ere, dict) else {},
    }
    ts = now or _now()
    ts_s = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
    fights = [fight_one(o, ctx) for o in items]
    return {
        "kind": "adversarial_paper_fight",
        "mode": "PAPER/SHADOW",
        "valedia_version": vc.VALEDIA_VERSION,
        "ts": ts_s,
        "no_orders": True,
        "no_ln": True,
        "auto_raise": False,
        "fusion_touched": False,
        "intents": [],
        "source": (signal_doc or {}).get("source") if isinstance(signal_doc, dict) else "opps",
        "n": len(fights),
        "fights": fights,
        "settle_later": "fee_true_ftl",
    }


def persist(doc, models_dir=None, ts=None):
    md = models_dir or _MODELS
    os.makedirs(md, exist_ok=True)
    stamp = (ts or _now()).strftime("%Y%m%dT%H%M%SZ")
    path = os.path.join(md, f"adversarial_round_{stamp}.json")
    payload = dict(doc)
    payload["path"] = path
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2, default=str)
        f.write("\n")
    os.replace(tmp, path)
    return path


def run_round(signal_doc=None, enrich=None, opps=None, ere=None, models_dir=None, write=True, now=None):
    if signal_doc is None:
        signal_doc = _load(_SIGNAL)
    if enrich is None:
        enrich = mne.load_enrichment(_ENRICH)
    if ere is None:
        ere = _load(_ERE)
    doc = build_round(signal_doc=signal_doc or {}, enrich=enrich or {}, opps=opps, ere=ere or {}, now=now)
    if write:
        doc["path"] = persist(doc, models_dir=models_dir, ts=now or _now())
    return doc


def main(argv=None):
    p = argparse.ArgumentParser(description="PAPER/SHADOW adversarial fight (no orders, no LN)")
    p.add_argument("--signal", default=None)
    p.add_argument("--enrich", default=None)
    p.add_argument("--opps", default=None)
    p.add_argument("--ere", default=None)
    p.add_argument("--models", default=None)
    p.add_argument("--out", default=None)
    p.add_argument("--no-write", action="store_true")
    args = p.parse_args(argv)
    set_paths(models_dir=args.models, signal_path=args.signal, enrich_path=args.enrich, ere_path=args.ere)
    opps = _load(args.opps) if args.opps else None
    doc = run_round(opps=opps, models_dir=args.models, write=not args.no_write)
    if args.out and not args.no_write:
        with open(args.out, "w") as f:
            json.dump(doc, f, indent=2, default=str)
            f.write("\n")
        doc["path"] = args.out
    print(json.dumps({
        "path": doc.get("path"),
        "n": doc.get("n"),
        "mode": doc.get("mode"),
        "no_orders": True,
        "no_ln": True,
        "valedia_version": doc.get("valedia_version"),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
