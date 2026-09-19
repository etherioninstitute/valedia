"""MiroFish accuracy ledger — fee-true closed loop (Slice 13).

Append-only JSONL. Predictions from local mirofish_signal.json; outcomes from
fee-inclusive forward_test_ledger settlements. Paper/sandbox/backfill/shadow
never count. No HTTP. yfinance is dead unless MIROFISH_PRICE_HTTP=1 (this
lane never sets that).

Weight: effective_weight() ≤ MIROFISH_UNPROVEN_CAP until proven
(directional accuracy > MIROFISH_ACCURACY_BAR on ≥ MIROFISH_MIN_SCORED
fee-true results, or Burton-configured bar). Does not auto-raise
(MIROFISH_AUTO_RAISE=False). Fusion/intel read this module.

  record()  — directional predictions from a NON-lightweight signal
  score()   — match matured preds to fee-true FTL settlements
  summary() — hit rate + proven flag + effective weight

(c) GOATWORTHY Post LLC.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone, timedelta

import valedia_config as vc

_MODELS = os.path.expanduser("~/.openclaw/workspace/models")
_LEDGER = None
_SIGNAL = None
_FTL = None
_SUMMARY = None
HORIZON_DAYS = 5

# Back-compat names (healthcheck / docs).
MODELS = _MODELS
LEDGER = os.path.join(_MODELS, "mirofish_accuracy_ledger.jsonl")
SIGNAL = os.path.join(_MODELS, "mirofish_signal.json")


def set_paths(models_dir=None, ledger_path=None, signal_path=None,
              ftl_path=None, summary_path=None):
    """Injectable paths for tests. None leaves that slot unchanged."""
    global _MODELS, _LEDGER, _SIGNAL, _FTL, _SUMMARY, MODELS, LEDGER, SIGNAL
    if models_dir is not None:
        _MODELS = models_dir
        MODELS = models_dir
        _LEDGER = _SIGNAL = _FTL = _SUMMARY = None
    if ledger_path is not None:
        _LEDGER = ledger_path
    if signal_path is not None:
        _SIGNAL = signal_path
    if ftl_path is not None:
        _FTL = ftl_path
    if summary_path is not None:
        _SUMMARY = summary_path
    p = paths()
    LEDGER = p["ledger"]
    SIGNAL = p["signal"]


def paths():
    models = _MODELS
    return {
        "models": models,
        "ledger": _LEDGER or os.path.join(models, "mirofish_accuracy_ledger.jsonl"),
        "signal": _SIGNAL or os.path.join(models, "mirofish_signal.json"),
        "ftl": _FTL or os.path.join(models, "forward_test_ledger.jsonl"),
        "summary": _SUMMARY or os.path.join(models, "mirofish_accuracy_summary.json"),
    }


def _append(rec):
    path = paths()["ledger"]
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(rec) + "\n")


def _read_jsonl(path):
    if not path or not os.path.exists(path):
        return []
    out = []
    with open(path) as f:
        for line in f:
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _read():
    return _read_jsonl(paths()["ledger"])


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


def _norm_dir(raw):
    s = str(raw or "").strip().upper()
    if not s:
        return None
    if s.startswith(("UP", "BUY", "LONG", "YES", "BULL")):
        return "UP"
    if s.startswith(("DOWN", "SELL", "SHORT", "NO", "BEAR")):
        return "DOWN"
    return None


def _assets_match(pred_asset, ticker):
    a = str(pred_asset or "").strip().upper().replace(" ", "")
    b = str(ticker or "").strip().upper().replace(" ", "")
    if not a or not b:
        return False
    if a == b:
        return True
    if a + "-USD" == b or b + "-USD" == a:
        return True
    return a.replace("-", "") == b.replace("-", "")


def _non_real(source):
    try:
        import forward_test_ledger as ftl
        return bool(ftl._is_non_real_source(source))
    except Exception:
        s = str(source or "").lower()
        return ("paper" in s or "sandbox" in s or "unreconciled" in s
                or "backfill" in s or "shadow" in s)


def record(signal_doc=None):
    """Append directional predictions from a REAL swarm signal. No HTTP."""
    if signal_doc is None:
        sig_path = paths()["signal"]
        if not os.path.exists(sig_path):
            print("no signal file")
            return 1
        try:
            signal_doc = json.load(open(sig_path))
        except Exception:
            print("signal unreadable")
            return 1
    d = signal_doc or {}
    src = str(d.get("source", ""))
    if "lightweight" in src.lower():
        print(f"signal source is '{src}' — lightweight, NOT recording")
        return 2
    ts = d.get("ts") or datetime.now(timezone.utc).isoformat()
    signals = d.get("signals") or {}
    pid = "mf_" + str(ts).replace(":", "").replace("-", "")[:15]
    n = 0
    due_dt = _parse_ts(ts)
    due = ((due_dt or datetime.now(timezone.utc))
           + timedelta(days=HORIZON_DAYS)).isoformat()
    for asset, s in signals.items():
        blob = s if isinstance(s, dict) else {"direction": s}
        direction = blob.get("direction") or blob.get("predicted_direction")
        if _norm_dir(direction) is None:
            continue
        score = blob.get("score")
        neural = False
        if isinstance(blob, dict):
            nblob = blob.get("neural")
            neural = bool(isinstance(nblob, dict) and nblob.get("present"))
            if blob.get("neural_enriched") is True:
                neural = True
        _append({
            "type": "prediction",
            "pred_id": f"{pid}_{asset}",
            "ts": ts,
            "source": src,
            "asset": asset,
            "predicted_direction": direction,
            "score": score,
            "horizon_days": HORIZON_DAYS,
            "score_after": due,
            "neural_enriched": bool(neural),
        })
        n += 1
    print(f"recorded {n} MiroFish predictions from real sim (source='{src}')")
    return 0


def _ftl_pairs(rows=None):
    recs = rows if rows is not None else _read_jsonl(paths()["ftl"])
    entries = {r["id"]: r for r in recs if r.get("type") == "entry" and r.get("id")}
    pairs = []
    for s in recs:
        if s.get("type") != "settlement":
            continue
        e = entries.get(s.get("entry_id"), {})
        if _non_real(s.get("source")) or _non_real(e.get("source")):
            continue
        pairs.append((e, s))
    return pairs


def _event_ts(entry, sett):
    return (_parse_ts(sett.get("ts"))
            or _parse_ts(sett.get("logged_at"))
            or _parse_ts(entry.get("ts"))
            or _parse_ts(entry.get("logged_at")))


def _fee_true_outcome(entry, sett):
    try:
        net = float(sett.get("realized_pnl_net"))
    except (TypeError, ValueError):
        return None, None, None
    fee = sett.get("fee")
    if fee is None:
        fee = entry.get("entry_fee")
    if net == 0:
        return None, net, fee
    side = str(entry.get("side") or "").strip().lower()
    if side in ("sell", "short", "no"):
        realized = "DOWN" if net > 0 else "UP"
    else:
        realized = "UP" if net > 0 else "DOWN"
    return realized, net, fee


def _best_ftl_match(pred, pairs, pts, due):
    asset = pred.get("asset")
    best = None
    best_ts = None
    for entry, sett in pairs:
        ticker = entry.get("ticker") or sett.get("ticker")
        if not _assets_match(asset, ticker):
            continue
        ets = _event_ts(entry, sett)
        if ets is None:
            continue
        if pts is not None and ets < pts:
            continue
        if due is not None and ets > due:
            continue
        if best_ts is None or ets < best_ts:
            best = (entry, sett)
            best_ts = ets
    return best


def score(now=None, ftl_rows=None):
    """Score matured predictions vs fee-true FTL settlements. No HTTP."""
    if os.environ.get("MIROFISH_PRICE_HTTP") == "1":
        # Explicit opt-in only. Default path never fetches prices.
        pass
    recs = _read()
    scored = {r["pred_id"] for r in recs if r.get("type") == "result" and r.get("pred_id")}
    preds = [r for r in recs if r.get("type") == "prediction" and r.get("pred_id") not in scored]
    now = now or datetime.now(timezone.utc)
    pairs = _ftl_pairs(ftl_rows)
    n = 0
    for p in preds:
        due = _parse_ts(p.get("score_after"))
        pts = _parse_ts(p.get("ts"))
        if due is not None and now < due:
            continue
        pred_dir = _norm_dir(p.get("predicted_direction"))
        if pred_dir is None:
            continue
        match = _best_ftl_match(p, pairs, pts, due)
        if match is None:
            continue
        entry, sett = match
        realized, net, fee = _fee_true_outcome(entry, sett)
        if realized is None:
            continue
        hit = realized == pred_dir
        _append({
            "type": "result",
            "pred_id": p["pred_id"],
            "asset": p.get("asset"),
            "predicted_direction": p.get("predicted_direction"),
            "realized": realized,
            "hit": bool(hit),
            "fee_true": True,
            "realized_pnl_net": net,
            "fee": fee,
            "ftl_entry_id": entry.get("id"),
            "source": "forward_test_ledger",
            "scored_at": now.isoformat() if hasattr(now, "isoformat") else str(now),
            "neural_enriched": bool(p.get("neural_enriched")),
        })
        n += 1
    print(f"scored {n} predictions")
    return 0


def _weight_from(proven):
    """Seatbelted 10/15 path. Used when VALEDIA_SELF_ADJUST is off."""
    configured = float(getattr(vc, "MIROFISH_WEIGHT", 0.10))
    unproven_cap = float(getattr(vc, "MIROFISH_UNPROVEN_CAP", 0.10))
    proven_cap = float(getattr(vc, "MIROFISH_PROVEN_CAP", 0.15))
    auto_raise = bool(getattr(vc, "MIROFISH_AUTO_RAISE", False))
    cap = proven_cap if (proven and auto_raise) else unproven_cap
    return min(configured, cap)


def _band_weight(st):
    """SELF_ADJUST on: raise/decay inside band. Never opens writers."""
    configured = float(getattr(vc, "MIROFISH_WEIGHT", 0.10))
    unproven = float(getattr(vc, "MIROFISH_UNPROVEN_CAP", 0.10))
    floor = float(getattr(vc, "MIROFISH_BAND_FLOOR", 0.05))
    top = float(getattr(vc, "MIROFISH_BAND_TOP", 0.25))
    ceiling = float(getattr(vc, "MIROFISH_HARD_CEILING", 0.30))
    bonus = float(getattr(vc, "MIROFISH_ENRICH_BONUS", 0.02))
    bar = float(st.get("bar") or getattr(vc, "MIROFISH_ACCURACY_BAR", 0.55))
    min_n = int(st.get("min_scored") or getattr(vc, "MIROFISH_MIN_SCORED", 20))
    n = int(st.get("n_scored") or 0)
    acc = st.get("directional_accuracy")
    if n < min_n or acc is None:
        return min(configured, unproven)
    w = top if acc > bar else floor
    enr_n = int(st.get("n_scored_enriched") or 0)
    plain_n = int(st.get("n_scored_plain") or 0)
    enr_acc = st.get("enriched_accuracy")
    plain_acc = st.get("plain_accuracy")
    if (enr_n >= 5 and plain_n >= 5
            and enr_acc is not None and plain_acc is not None
            and enr_acc > plain_acc):
        w = w + bonus
    return max(floor, min(w, top, ceiling))


def _runtime_weight(st):
    if getattr(vc, "self_adjust_enabled", lambda: False)():
        return _band_weight(st)
    return _weight_from(bool(st.get("proven")))


def accuracy_stats(rows=None):
    recs = rows if rows is not None else _read()
    preds = [r for r in recs if r.get("type") == "prediction"]
    results = [r for r in recs if r.get("type") == "result" and r.get("fee_true")]
    legacy = [r for r in recs if r.get("type") == "result" and not r.get("fee_true")]
    hits = sum(1 for r in results if r.get("hit"))
    n = len(results)
    acc = (hits / n) if n else None
    bar = float(getattr(vc, "MIROFISH_ACCURACY_BAR", 0.55))
    min_n = int(getattr(vc, "MIROFISH_MIN_SCORED", 20))
    proven = bool(n >= min_n and acc is not None and acc > bar)
    net = 0.0
    for r in results:
        try:
            net += float(r.get("realized_pnl_net") or 0)
        except (TypeError, ValueError):
            pass
    enr = [r for r in results if r.get("neural_enriched")]
    plain = [r for r in results if not r.get("neural_enriched")]
    def _dir_acc(group):
        if not group:
            return None
        return round(sum(1 for r in group if r.get("hit")) / len(group), 4)
    st = {
        "n_predictions": len(preds),
        "n_scored": n,
        "n_scored_enriched": len(enr),
        "n_scored_plain": len(plain),
        "hits": hits,
        "directional_accuracy": None if acc is None else round(acc, 4),
        "enriched_accuracy": _dir_acc(enr),
        "plain_accuracy": _dir_acc(plain),
        "fee_true_net": round(net, 4),
        "legacy_n": len(legacy),
        "proven": proven,
        "bar": bar,
        "min_scored": min_n,
        "auto_raise": bool(getattr(vc, "MIROFISH_AUTO_RAISE", False)),
        "self_adjust": bool(getattr(vc, "self_adjust_enabled", lambda: False)()),
        "source": "forward_test_ledger",
    }
    st["weight"] = _runtime_weight(st)
    return st


def is_proven(stats=None):
    st = stats if stats is not None else accuracy_stats()
    return bool(st.get("proven"))


def effective_weight(stats=None):
    """Runtime MiroFish fusion weight. Seatbelt 10/15 unless SELF_ADJUST band."""
    st = stats if stats is not None else accuracy_stats()
    return _runtime_weight(st)


def write_summary_snapshot(stats=None):
    st = stats if stats is not None else accuracy_stats()
    path = paths()["summary"]
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(st, f, indent=2)
    return st


HARD_CEILING = 0.15  # Burton cap until a later go; this lane never writes MIROFISH_WEIGHT


def _learning_note(now=None, pause_path=None):
    """Pause/TTL is not a grave. Fail-open if the registry is unreadable."""
    note = {
        "permanent_blacklist": False,
        "live_exec_fenced": sorted(getattr(vc, "LIVE_EXEC_FENCED", ())),
        "note": "TTL/probation re-enables evaluation+sizing. Unpause != live-buy for weather/oil.",
    }
    try:
        import pnl_tracker as pt
        note["pause_ttl_days"] = int(pt._pause_ttl_days())
        reg = pt.load_pause_registry(path=pause_path, now=now)
        note["paused"] = [p.get("strategy") for p in (reg.get("paused") or [])]
        note["probation"] = [p.get("strategy") for p in (reg.get("probation") or [])]
    except Exception as e:
        note["pause_error"] = str(e)[:80]
    return note


def recommend_weight(stats=None, out_path=None, now=None, pause_path=None):
    """Local JSON recommendation. Never assigns MIROFISH_WEIGHT / AUTO_RAISE.

    Unproven → ≤10% (UNPROVEN_CAP). Proven → recommend ≤15% (PROVEN_CAP,
    hard ceiling). Runtime weight stays effective_weight() until Burton
    flips AUTO_RAISE or the constant. No HTTP. No Intent path.
    """
    st = stats if stats is not None else accuracy_stats()
    proven = bool(st.get("proven"))
    flipped = bool(getattr(vc, "self_adjust_enabled", lambda: False)())
    unproven_cap = min(float(getattr(vc, "MIROFISH_UNPROVEN_CAP", 0.10)), 0.10)
    if flipped:
        ceiling = float(getattr(vc, "MIROFISH_HARD_CEILING", 0.30))
        proven_cap = min(float(getattr(vc, "MIROFISH_BAND_TOP", 0.25)), ceiling)
        recommended = float(effective_weight(st))
    else:
        ceiling = HARD_CEILING
        proven_cap = min(float(getattr(vc, "MIROFISH_PROVEN_CAP", 0.15)), HARD_CEILING)
        recommended = min(proven_cap if proven else unproven_cap, HARD_CEILING)
    runtime = float(effective_weight(st))
    configured = float(getattr(vc, "MIROFISH_WEIGHT", 0.10))
    rec = {
        "recommended_weight": recommended,
        "runtime_weight": runtime,
        "configured_weight": configured,
        "cap": proven_cap if proven else unproven_cap,
        "hard_ceiling": ceiling,
        "proven": proven,
        "n_scored": st.get("n_scored"),
        "directional_accuracy": st.get("directional_accuracy"),
        "bar": st.get("bar"),
        "min_scored": st.get("min_scored"),
        "auto_raise": bool(getattr(vc, "MIROFISH_AUTO_RAISE", False)),
        "self_adjust": flipped,
        "applied": False,
        "action": ("burton_may_set_MIROFISH_WEIGHT_or_AUTO_RAISE"
                   if proven else "keep_unproven_cap"),
        "reason": (
            f"proven fee-true acc={st.get('directional_accuracy')} n={st.get('n_scored')}; "
            f"runtime stays {runtime} until Burton raises"
            if proven else
            f"unproven n={st.get('n_scored')} acc={st.get('directional_accuracy')}; "
            f"cap {unproven_cap}"
        ),
        "learning": _learning_note(now=now, pause_path=pause_path),
        "source": "mirofish_accuracy_ledger",
    }
    path = out_path or os.path.join(paths()["models"], "mirofish_weight_recommendation.json")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(rec, f, indent=2)
        f.write("\n")
    rec["path"] = path
    return rec


def read_summary(models_dir=None):
    """Intel/fusion read path: snapshot JSON if present, else that dir's ledger.

    A caller-supplied models_dir never falls through to the live home ledger.
    """
    if models_dir:
        snap = os.path.join(models_dir, "mirofish_accuracy_summary.json")
        if os.path.exists(snap):
            try:
                d = json.load(open(snap))
                if isinstance(d, dict) and "proven" in d:
                    return d
            except Exception:
                pass
        led = os.path.join(models_dir, "mirofish_accuracy_ledger.jsonl")
        return accuracy_stats(_read_jsonl(led) if os.path.exists(led) else [])
    return accuracy_stats()


def summary():
    st = write_summary_snapshot()
    n_pred, n, hits = st["n_predictions"], st["n_scored"], st["hits"]
    if n == 0:
        print(f"MiroFish accuracy ledger: {n_pred} predictions recorded, 0 fee-true scored.")
        print(f"Weight stays {st['weight']:.0%} until >{st['bar']:.0%} directional "
              f"on ≥{st['min_scored']} fee-true results.")
        return 0
    rate = 100.0 * hits / n
    print(f"OVERALL n={n} hits={hits} hit%={rate:.1f} fee_true_net={st['fee_true_net']:+.2f}")
    print(f"proven={st['proven']} weight={st['weight']:.0%} auto_raise={st['auto_raise']}")
    print("Gate: raise fusion weight above the unproven cap ONLY if proven AND "
          "Burton sets MIROFISH_AUTO_RAISE. This lane does not auto-raise.")
    return 0


def recommend():
    rec = recommend_weight()
    print(f"recommend={rec['recommended_weight']:.0%} runtime={rec['runtime_weight']:.0%} "
          f"proven={rec['proven']} applied={rec['applied']} action={rec['action']}")
    print(f"wrote {rec['path']}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(prog="mirofish_accuracy_ledger")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for c in ("record", "score", "summary", "recommend"):
        sub.add_parser(c)
    a = ap.parse_args()
    sys.exit({"record": record, "score": score, "summary": summary,
              "recommend": recommend}[a.cmd]())
