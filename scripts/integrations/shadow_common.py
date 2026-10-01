"""Shared oil/weather shadow ledger bits. Not a writer. apply stays False.

Shadow hit-rate is not an accuracy claim. category_stats and Kelly ignore it.
"""
import json
import os
from datetime import datetime, timezone

# |fv-0.5| at or under this is not size fuel. Same 5-point band as MiroFish FV.
NEAR = 0.05
# ponytail: ring cap. A ledger file is not a database.
HISTORY_CAP = 200
LIVE_MODELS = os.path.expanduser("~/.openclaw/workspace/models")


def drop_near_neutral(tag, fv):
    if tag == "official" and fv is not None and abs(float(fv) - 0.5) <= NEAR:
        return "near_neutral", None
    return tag, fv


def ftl_shaped(row, strategy):
    """Would-have row. source shadow. Never a live fill. Never reconcile."""
    row = row if isinstance(row, dict) else {}
    return {
        "type": "would_have",
        "strategy": strategy,
        "ticker": row.get("ticker"),
        "source": "shadow",
        "fill_status": "PAPER",
        "contracts": row.get("would_have_size") or 0,
        "price": row.get("price"),
        "fee": row.get("fee"),
        "realized_pnl_net": row.get("would_have_pnl"),
        "reconcile": False,
        "live_fill": False,
    }


def accuracy_rollup(rows):
    settled = []
    for r in rows or []:
        if not isinstance(r, dict) or r.get("settlement") is None:
            continue
        if not r.get("would_have_size"):
            continue
        pnl = r.get("would_have_pnl")
        if pnl is None:
            continue
        settled.append(float(pnl))
    n = len(settled)
    hits = sum(1 for p in settled if p > 0)
    return {
        "n": n,
        "hits": hits,
        "hit_rate": None if n == 0 else round(hits / n, 4),
        "mean_pnl": None if n == 0 else round(sum(settled) / n, 4),
        "admissible_for_kelly": False,
        "admissible_for_category_stats": False,
    }


def threshold_hint(rollup, band=NEAR):
    """Advisory only. apply is always False. Does not write a gate."""
    suggest = None
    n = (rollup or {}).get("n") or 0
    hr = (rollup or {}).get("hit_rate")
    if n >= 20 and hr is not None and hr < 0.45:
        suggest = round(min(0.20, float(band) + 0.05), 2)
    return {
        "suggest_min_abs_edge": suggest,
        "apply": False,
        "reason": "shadow_not_admissible",
    }


def _key(row):
    return (
        row.get("ticker"), row.get("tag"), row.get("price"), row.get("fv"),
        row.get("book"), row.get("settlement"), row.get("fee_true_ev"),
    )


def load_json(path):
    try:
        with open(path) as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else {}
    except (OSError, ValueError):
        return {}


def dump(path, doc):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(doc, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)


def finish_ledger(path, body):
    """Keep prior scored rows, append new ones, stamp a non-binding rollup."""
    prior = load_json(path)
    hist = [r for r in (prior.get("history") or []) if isinstance(r, dict)]
    if not hist:
        hist = [r for r in (prior.get("scored") or []) if isinstance(r, dict)]
    seen = {_key(r) for r in hist}
    for row in body.get("scored") or []:
        if not isinstance(row, dict):
            continue
        k = _key(row)
        if k in seen:
            continue
        seen.add(k)
        hist.append(row)
    if len(hist) > HISTORY_CAP:
        hist = hist[-HISTORY_CAP:]
    out = dict(body)
    out["history"] = hist
    out["accuracy"] = accuracy_rollup(hist)
    out["threshold_hint"] = threshold_hint(out["accuracy"])
    out["create_order"] = False
    out["dispatchable"] = 0
    out["live_exec"] = False
    out["appended_at"] = datetime.now(timezone.utc).isoformat()
    dump(path, out)
    return out


def append_rows(path, rows, header):
    """Append scored rows onto an existing shadow ledger. Forces the fence flags."""
    prior = load_json(path)
    scored = [r for r in (prior.get("scored") or []) if isinstance(r, dict)]
    scored.extend(r for r in (rows or []) if isinstance(r, dict))
    prior.update(header or {})
    prior["scored"] = scored
    prior["n_scored"] = len(scored)
    prior["create_order"] = False
    prior["dispatchable"] = 0
    prior["live_exec"] = False
    return finish_ledger(path, prior)


def mirror(path, live_name):
    """Copy one ledger into the live models dir. No code. No version bump."""
    if not os.path.isdir(LIVE_MODELS):
        return None
    doc = load_json(path)
    if not doc:
        return None
    live = os.path.join(LIVE_MODELS, live_name)
    dump(live, doc)
    return live
