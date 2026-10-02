#!/usr/bin/env python3
"""H7: Arena energy without sats — PAPER stress wrap of adversarial_paper_fight.

Lenses + optional Arena primes fight the same opps. Scored on fee-true FTL.
Risk veto records disagreement. Never orders. Never LN/faucet.

Not crontab-installed. ARENA_MODE is always paper.
"""
import argparse
import json
import os
from datetime import datetime, timezone

import adversarial_paper_fight as apf
import mirofish_accuracy_ledger as mal

ARENA_MODE = "paper"
ACTIONS = ("BUY", "SELL", "HOLD", "ABSTAIN", "VETO")
LN_KEYS = ("LF_OPERATOR_KEY", "LF_OPERATOR", "ARENA_FAUCET", "LNBITS_ADMIN")
BAND = 15.0


def ln_status(env=None):
    env = env if env is not None else os.environ
    seen = [k for k in LN_KEYS if env.get(k)]
    return {
        "arena_mode": ARENA_MODE,
        "ln_refused": True,
        "ln_keys_seen": bool(seen),
        "burton_arena_ln": bool(env.get("BURTON_ARENA_LN")),
        "funded": False,
        "note": "paper only; harness does not fund or call a faucet",
    }


def _clamp_side(side):
    s = str(side or "HOLD").upper()
    return s if s in ("BUY", "SELL", "HOLD") else "HOLD"


def prime_strategic(opp, fight):
    dirs = [v for v in fight.get("lenses") or []
            if v.get("side") in ("BUY", "SELL") and not v.get("abstain")]
    if fight.get("disagreement") and dirs:
        w = max(dirs, key=lambda v: abs(v["score"] - apf.HOLD) * v["confidence"])
        return apf._vote("strategic", w["score"], _clamp_side(w["side"]),
                         min(1.0, w["confidence"] + 0.1),
                         f"exploit split → {w['side']}")
    score = apf._f(opp.get("score"), apf.HOLD)
    side = apf._side_from_score(score)
    return apf._vote("strategic", score, side, 0.5, f"no split, take {side}")


def prime_competitive(opp, fight):
    score = apf._f(opp.get("score"), apf.HOLD)
    side = "BUY" if score >= apf.HOLD else "SELL"
    out = score if score != apf.HOLD else apf.BUY
    return apf._vote("competitive", out, side, 0.55, f"never-hold {side}")


def prime_cooperative(opp, fight):
    if fight.get("disagreement") or fight.get("risk_veto"):
        return apf._vote("cooperative", apf.HOLD, "HOLD", 0.7, "split/veto → stand down")
    c = _clamp_side(fight.get("consensus_side") or "HOLD")
    score = apf._f(opp.get("score"), apf.HOLD)
    return apf._vote("cooperative", score, c, 0.6, f"follow consensus {c}")


def prime_neutral(opp, fight):
    score = apf._f(opp.get("score"), apf.HOLD)
    if abs(score - apf.HOLD) < BAND:
        return apf._vote("neutral", score, "HOLD", 0.4, "inside band → HOLD")
    side = apf._side_from_score(score)
    return apf._vote("neutral", score, side, 0.45, f"outside band {side}")


def attach_primes(doc):
    fights = []
    for row in doc.get("fights") or []:
        r = dict(row)
        inp = r.get("input") or {}
        opp = {"symbol": r.get("symbol"), "score": inp.get("score", apf.HOLD),
               "direction": inp.get("direction"), "confidence": inp.get("confidence")}
        r["primes"] = [
            prime_strategic(opp, r),
            prime_competitive(opp, r),
            prime_cooperative(opp, r),
            prime_neutral(opp, r),
        ]
        fights.append(r)
    out = dict(doc)
    out["fights"] = fights
    return out


def _score_vote(vote, realized, net, entry_dir):
    if vote.get("abstain") or vote.get("veto"):
        return "skip", None, 0.0
    d = mal._norm_dir(vote.get("side"))
    if d is None:
        return "skip", None, 0.0
    hit = d == realized
    if entry_dir is None:
        followed = net if hit else -abs(float(net or 0))
    else:
        followed = net if d == entry_dir else -float(net or 0)
    return ("hit" if hit else "miss"), hit, followed


def _empty_lens():
    return {"n": 0, "hits": 0, "misses": 0, "skips": 0, "net": 0.0,
            "veto_ok": 0, "veto_fp": 0}


def _pre_veto_side(fight):
    c = fight.get("side_counts") or {}
    buy, sell = int(c.get("BUY") or 0), int(c.get("SELL") or 0)
    if buy > sell:
        return "BUY"
    if sell > buy:
        return "SELL"
    return None


def score_round(doc, ftl_rows=None):
    """Score lens/prime votes vs fee-true FTL. Read-only. No weight change."""
    pairs = mal._ftl_pairs(ftl_rows)
    by = {}
    rows = []
    n_scored = n_unscored = 0

    def bump(lens, result, hit, net):
        d = by.setdefault(lens, _empty_lens())
        if result == "skip":
            d["skips"] += 1
            return
        d["n"] += 1
        d["hits"] += 1 if hit else 0
        d["misses"] += 0 if hit else 1
        d["net"] = round(d["net"] + float(net or 0), 4)

    for fight in doc.get("fights") or []:
        match = mal._best_ftl_match({"asset": fight.get("symbol")}, pairs, None, None)
        if not match:
            n_unscored += 1
            rows.append({"symbol": fight.get("symbol"), "scored": False})
            continue
        entry, sett = match
        realized, net, fee = mal._fee_true_outcome(entry, sett)
        if realized is None:
            n_unscored += 1
            rows.append({"symbol": fight.get("symbol"), "scored": False,
                         "reason": "flat_or_unusable"})
            continue
        n_scored += 1
        entry_dir = mal._norm_dir(entry.get("side"))
        votes = list(fight.get("lenses") or []) + list(fight.get("primes") or [])
        lens_out = []
        for v in votes:
            result, hit, followed = _score_vote(v, realized, net, entry_dir)
            bump(v.get("lens") or "?", result, hit, followed)
            lens_out.append({
                "lens": v.get("lens"), "side": v.get("side"),
                "result": result, "hit": hit, "net": round(float(followed or 0), 4),
            })
        veto_note = None
        if fight.get("risk_veto"):
            pre = mal._norm_dir(_pre_veto_side(fight))
            if pre is not None:
                veto_note = "ok" if pre != realized else "fp"
                d = by.setdefault("risk_veto", _empty_lens())
                if veto_note == "ok":
                    d["veto_ok"] += 1
                else:
                    d["veto_fp"] += 1
        rows.append({
            "symbol": fight.get("symbol"), "scored": True, "fee_true": True,
            "realized": realized, "ftl_net": net, "fee": fee,
            "ftl_entry_id": entry.get("id"), "lenses": lens_out,
            "risk_veto": bool(fight.get("risk_veto")), "veto": veto_note,
        })
    cands = [(k, v) for k, v in by.items() if v["n"] > 0]
    winner = None
    if cands:
        k, _ = max(cands, key=lambda kv: (kv[1]["net"], kv[1]["hits"], -kv[1]["misses"]))
        winner = k
    for v in by.values():
        v["net"] = round(v["net"], 4)
    return {
        "fee_true": True,
        "source": "forward_test_ledger",
        "n_scored": n_scored,
        "n_unscored": n_unscored,
        "by_lens": by,
        "winner_lens": winner,
        "fights": rows,
        "fusion_touched": False,
        "auto_raise": False,
        "intents": [],
    }


def persist(doc, models_dir=None, ts=None):
    md = models_dir or apf._MODELS
    os.makedirs(md, exist_ok=True)
    stamp = (ts or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    path = os.path.join(md, f"arena_stress_{stamp}.json")
    payload = dict(doc)
    payload["path"] = path
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2, default=str)
        f.write("\n")
    os.replace(tmp, path)
    return path


def run_stress(signal_doc=None, enrich=None, opps=None, ere=None,
               ftl_rows=None, models_dir=None, write=True, now=None,
               include_primes=True, env=None):
    status = ln_status(env)
    doc = apf.run_round(signal_doc=signal_doc, enrich=enrich, opps=opps,
                        ere=ere, models_dir=models_dir, write=False, now=now)
    if include_primes:
        doc = attach_primes(doc)
    doc["kind"] = "arena_offline_stress"
    doc["wraps"] = "adversarial_paper_fight"
    doc["arena_mode"] = ARENA_MODE
    doc["actions"] = list(ACTIONS)
    doc.update(status)
    doc["ftl_score"] = score_round(doc, ftl_rows=ftl_rows)
    doc["intents"] = []
    doc["fusion_touched"] = False
    doc["auto_raise"] = False
    if write:
        doc["path"] = persist(doc, models_dir=models_dir, ts=now)
    return doc


def _load_ftl(path):
    if not path:
        return None
    if path.endswith(".jsonl"):
        rows = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows
    d = apf._load(path)
    if isinstance(d, list):
        return d
    if isinstance(d, dict):
        return d.get("rows") or d.get("records") or []
    return []


def main(argv=None):
    p = argparse.ArgumentParser(description="PAPER Arena stress (no orders, no LN)")
    p.add_argument("--signal", default=None)
    p.add_argument("--enrich", default=None)
    p.add_argument("--opps", default=None)
    p.add_argument("--ere", default=None)
    p.add_argument("--ftl", default=None)
    p.add_argument("--models", default=None)
    p.add_argument("--no-write", action="store_true")
    p.add_argument("--no-primes", action="store_true")
    args = p.parse_args(argv)
    apf.set_paths(models_dir=args.models, signal_path=args.signal,
                  enrich_path=args.enrich)
    opps = apf._load(args.opps) if args.opps else None
    signal = apf._load(args.signal) if args.signal else None
    enrich = apf._load(args.enrich) if args.enrich else None
    ere = apf._load(args.ere) if args.ere else None
    doc = run_stress(
        signal_doc=signal, enrich=enrich, opps=opps, ere=ere,
        ftl_rows=_load_ftl(args.ftl), models_dir=args.models,
        write=not args.no_write, include_primes=not args.no_primes,
    )
    print(json.dumps({
        "path": doc.get("path"),
        "n": doc.get("n"),
        "arena_mode": ARENA_MODE,
        "ln_refused": True,
        "funded": False,
        "no_orders": True,
        "ftl_scored": (doc.get("ftl_score") or {}).get("n_scored"),
        "winner_lens": (doc.get("ftl_score") or {}).get("winner_lens"),
        "valedia_version": doc.get("valedia_version"),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
