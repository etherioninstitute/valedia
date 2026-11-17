#!/usr/bin/env python3
"""Calibration ledger: every probability / decision source earns its weight from its record.

READ-ONLY analysis. No network, no keys, no orders, no config writes. It never
changes a live weight; earned_weight() is exposed for a later, Burton-approved,
backtest-first plug-in (see "Plugging in" below).

Outcomes come ONLY from the fee-inclusive forward_test_ledger (never brain_log).
Real rows only: an (entry, settlement) pair is dropped when either side's source
is paper / sandbox / unreconciled / backfill / shadow (mirofish_accuracy_ledger.
_ftl_pairs, which uses forward_test_ledger._is_non_real_source).

Sources scored (one key per source; a filter "x" matches "x" and "x:*"):
  decide:elephant-1@q8_0:<point>  decisions of the shared Elephant-1 instance (systemone_pin.SOURCE_PREFIX),
      admitted only from a row whose `model` is the instance name. Scored exactly as below, under its own
      key: no row under another key is ever mixed into it, and its rows enter no other key.
  nimble_decide:<point>  Nimble 2 decisions (data/systemone/decisions.jsonl), kept as history.
      p = P(buy): answers.action_probabilities normalised over their finite values;
      no probabilities (or no "buy" key) -> unscorable "no_p_buy". The logged
      `confidence` is NEVER the forecast: Nimble 2 returns 1 - normalised entropy of
      the probabilities (Burton's live sample: probabilities buy .4765 / skip .5235,
      confidence .00159), so it is reported only as sharpness (avg_confidence).
      Matching rule (traded): a real FTL entry whose ticker matches state.symbol
      (mirofish _assets_match), same side when the decision has one
      (yes/no kept distinct; buy/long and sell/short folded), and whose entry ts is
      in [decision ts - SKEW_S, decision ts + TRADE_WINDOW_S]; nearest in time wins.
      y = 1 when that trade's realized_pnl_net > 0 (fee-true profitable), else 0.
      Untraded (skip/hold, or buy with no fill): a real settlement on the same
      ticker with result yes/no, event ts in [ts - SKEW_S, ts + SETTLE_HORIZON_S];
      y = 1 when result == decision side (a binary that won pays > price + fee
      except at prices near 1, so "won" stands in for "profitable"). No such record
      -> unscorable (counted, never guessed). Several decisions by one source that
      resolve to the same outcome record count once: the latest one is kept, the
      others are counted as superseded (repeated scans must not inflate n).
  mirofish  MiroFish per-symbol predictions (mirofish_accuracy_ledger.jsonl).
      p = P(UP) = score / 100 (score is 0..100, 50 neutral); no score -> unscorable.
      y = 1 when the realized direction is UP. Realized direction from an existing
      fee_true result row, else the SAME matching as mirofish_accuracy_ledger.score()
      (_best_ftl_match + _fee_true_outcome, window [pred ts, score_after]), read-only:
      nothing is appended. Not yet matured and unmatched -> unscorable "pending".
  fv:<fv_source>  FV models: FTL entries (or their settlements) carrying fv + fv_source.
      p = P(yes) = fv (values > 1 read as cents); y = 1 when settlement result == yes.
      Spot round-trips (result "exit") are unscorable.

Metrics (avg_confidence = mean logged confidence of scored items, sharpness only):
brier = mean (p - y)^2; log_loss = mean -[y ln p + (1-y) ln(1-p)] with p
clipped to [EPS, 1-EPS]; hit_rate over p != 0.5 ((p > 0.5) == y); reliability bins
are N_BINS equal-width bins on [0, 1] (p == 1 falls in the top bin), non-empty only;
base_rate = mean y; bss = 1 - brier / (base_rate * (1 - base_rate)) (climatology).

earned_weight(source, base, cap): n_scorable < MIN_N -> base * SHRINK (unproven);
bss undefined (one-sided outcomes) -> base * SHRINK; else base * bss / BSS_REF,
clamped to [0, cap]. BSS_REF = 0.10: a source with 10% Brier skill over climatology
earns exactly its base; no skill or worse earns 0. Never raises.

Plugging in (later, Burton approves; backtest first):
  - valedia_config.effective_mirofish_weight() (valedia_config.py:629): wrap the
    mirofish_accuracy_ledger result as min(current, earned_weight("mirofish", ...)).
  - Per-point trust: nimble_decide.decide_candidate / nimble_wiring.gate could gate
    veto mode on earned_weight("decide:elephant-1@q8_0:<pt>") > 0. The veto already reads
    calibrate_ex("decide:elephant-1@q8_0:<pt>", p_buy) and holds until it is isotonic
    (calibration_map.py; confidence = sharpness floor).

CLI: python3 calibration_ledger.py [--source X] [--since ISO] [--json]
Writes data/calibration/summary.json (gitignored) unless --no-write.
(c) GOATWORTHY Post LLC.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import mirofish_accuracy_ledger as mfl  # noqa: E402  (outcome matching is reused, not copied)
import systemone_pin as _pin  # noqa: E402  (the decision model's source key prefix and instance name)

ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
SUMMARY_PATH = os.path.join(ROOT, "data", "calibration", "summary.json")
REPO_MODELS = os.path.join(ROOT, "models")

MIN_N = 30
SHRINK = 0.5
BSS_REF = 0.10
EPS = 1e-6
N_BINS = 10
SKEW_S = 300                       # an order can be stamped a little before the decision log line
TRADE_WINDOW_S = 6 * 3600          # a decision's order lands within 6h or it is not "its" trade
SETTLE_HORIZON_S = 30 * 86400      # market result counted for an untraded decision up to 30 days on
NIMBLE_PREFIX = "nimble_decide"
_SIDE = {"yes": "yes", "no": "no", "buy": "buy", "long": "buy", "sell": "sell", "short": "sell"}


# ---------------------------------------------------------------- inputs
def _first_existing(*cands):
    for c in cands:
        if c and os.path.exists(c):
            return c
    return cands[0] if cands else ""


def default_paths():
    """Live home paths when present, else this repo's copies. Read-only."""
    try:
        import forward_test_ledger as ftl
        live_ftl = ftl.LEDGER
    except Exception:
        live_ftl = ""
    try:
        live_mf = mfl.paths()["ledger"]
    except Exception:
        live_mf = ""
    dec = os.environ.get("VALEDIA_SYSTEMONE_LOG") or os.path.join(
        ROOT, "data", "systemone", "decisions.jsonl")
    return {
        "decisions": dec,
        "ftl": _first_existing(live_ftl, os.path.join(REPO_MODELS, "forward_test_ledger.jsonl")),
        "mirofish": _first_existing(live_mf, os.path.join(REPO_MODELS, "mirofish_accuracy_ledger.jsonl")),
    }


def _rows(path):
    """JSONL dicts only; malformed or non-object lines are skipped."""
    try:
        return [r for r in mfl._read_jsonl(path) if isinstance(r, dict)]
    except Exception:
        return []


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _ts(raw):
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    try:
        return mfl._parse_ts(raw)
    except Exception:
        return None


def _side(raw):
    return _SIDE.get(str(raw or "").strip().lower())


def _unit(p):
    return p is not None and 0.0 <= p <= 1.0


# ---------------------------------------------------------------- per-source items
# An item: {"source", "ts", "p" (or None), "y" (or None), "reason", "key"}.
def _item(source, ts, p=None, y=None, reason=None, key=None, conf=None):
    return {"source": source, "ts": ts, "p": p, "y": y, "reason": reason, "key": key,
            "conf": conf}


def _p_buy(ans):
    probs = ans.get("action_probabilities") if isinstance(ans.get("action_probabilities"), dict) else {}
    vals = {str(k).lower(): _num(v) for k, v in probs.items()}
    vals = {k: v for k, v in vals.items() if v is not None and v >= 0}
    tot = sum(vals.values())
    if "buy" in vals and tot > 0:
        return vals["buy"] / tot
    return None  # confidence is entropy-based sharpness, not P(buy): never a fallback


def _nimble_items(dec_rows, pairs):
    traded = []
    for e, s in pairs:
        if e.get("id") and _ts(e.get("ts")) is not None:
            traded.append((e, s, _ts(e.get("ts"))))
    markets = []
    for e, s in pairs:
        res = str(s.get("result") or "").lower()
        ets = mfl._event_ts(e, s)
        if res in ("yes", "no") and ets is not None:
            markets.append((e.get("ticker") or s.get("ticker"), res, ets))
    out = []
    inst = _pin.dm.INSTANCE if _pin.dm is not None else None
    for r in dec_rows:
        src = str(r.get("source") or "")
        # The shared instance's key: only a row that names the instance as its model.
        current = bool(_pin.SOURCE_PREFIX) and src.startswith(_pin.SOURCE_PREFIX + ":")
        if current and (inst is None or r.get("model") != inst):
            continue
        # A v3 source must carry the full pinned digest and its own model identity;
        # old rows are never promoted by sharing a decision-point name.
        if src.startswith("nimble_decide:v3:"):
            import systemone_v3_candidate as v3
            if not src.startswith("nimble_decide:v3:" + v3.MANIFEST_SHA256 + ":") or r.get("model") != v3.TAG:
                continue
        if not (current or src == NIMBLE_PREFIX or src.startswith(NIMBLE_PREFIX + ":")):
            continue
        dts = _ts(r.get("ts"))
        ans = r.get("answers") if isinstance(r.get("answers"), dict) else {}
        state = r.get("state") if isinstance(r.get("state"), dict) else {}
        if dts is None:
            out.append(_item(src, None, reason="no_ts"))
            continue
        if r.get("error") or not ans.get("action"):
            out.append(_item(src, dts, reason="no_answer"))
            continue
        p = _p_buy(ans)
        if p is None:
            out.append(_item(src, dts, reason="no_p_buy"))
            continue
        conf = _num(ans.get("confidence"))
        conf = conf if _unit(conf) else None
        sym, side = state.get("symbol"), _side(state.get("side"))
        if not sym:
            out.append(_item(src, dts, p, reason="no_symbol", conf=conf))
            continue
        lo, hi = dts - timedelta(seconds=SKEW_S), dts + timedelta(seconds=TRADE_WINDOW_S)
        best = None
        for e, s, ets in traded:
            if not (lo <= ets <= hi) or not mfl._assets_match(sym, e.get("ticker")):
                continue
            if side is not None and _side(e.get("side")) != side:
                continue
            gap = abs((ets - dts).total_seconds())
            if best is None or gap < best[0]:
                best = (gap, e, s)
        if best is not None:
            net = _num(best[2].get("realized_pnl_net"))
            if net is None:
                out.append(_item(src, dts, p, reason="no_net", conf=conf))
            else:
                out.append(_item(src, dts, p, 1 if net > 0 else 0, key="trade:" + str(best[1]["id"]),
                                 conf=conf))
            continue
        if side not in ("yes", "no"):
            out.append(_item(src, dts, p, reason="no_trade_no_market_outcome", conf=conf))
            continue
        hi = dts + timedelta(seconds=SETTLE_HORIZON_S)
        mk = None
        for tick, res, ets in markets:
            if lo <= ets <= hi and mfl._assets_match(sym, tick) and (mk is None or ets < mk[1]):
                mk = (res, ets)
        if mk is None:
            out.append(_item(src, dts, p, reason="no_trade_no_market_outcome", conf=conf))
        else:
            out.append(_item(src, dts, p, 1 if mk[0] == side else 0,
                             key="market:%s:%s" % (str(sym).upper(), mk[0]), conf=conf))
    return out


def _dedupe(items):
    """One vote per (source, outcome key): keep the latest decision, mark the rest."""
    latest = {}
    for i, it in enumerate(items):
        if it["key"] is None or it["y"] is None:
            continue
        k = (it["source"], it["key"])
        if k not in latest or it["ts"] >= items[latest[k]]["ts"]:
            latest[k] = i
    keep = set(latest.values())
    for i, it in enumerate(items):
        if it["key"] is not None and it["y"] is not None and i not in keep:
            it["y"], it["reason"] = None, "superseded"
    return items


def _mirofish_items(mf_rows, ftl_rows, now):
    results = {}
    for r in mf_rows:
        if r.get("type") == "result" and r.get("fee_true") and r.get("pred_id"):
            results[r["pred_id"]] = r
    pairs = mfl._ftl_pairs(ftl_rows)
    out = []
    for p in mf_rows:
        if p.get("type") != "prediction":
            continue
        pts = _ts(p.get("ts"))
        score = _num(p.get("score"))
        prob = None if score is None else max(0.0, min(1.0, score / 100.0))
        if prob is None:
            out.append(_item("mirofish", pts, reason="no_score"))
            continue
        res = results.get(p.get("pred_id"))
        realized = mfl._norm_dir(res.get("realized")) if res else None
        if realized is None:
            due = _ts(p.get("score_after"))
            if due is not None and now < due:
                out.append(_item("mirofish", pts, prob, reason="pending"))
                continue
            match = mfl._best_ftl_match(p, pairs, pts, due)
            if match is not None:
                realized = mfl._fee_true_outcome(*match)[0]
        if realized is None:
            out.append(_item("mirofish", pts, prob, reason="no_fee_true_outcome"))
        else:
            out.append(_item("mirofish", pts, prob, 1 if realized == "UP" else 0))
    return out


def _fv_items(ftl_rows):
    setts = {}
    for s in ftl_rows:
        if s.get("type") == "settlement" and s.get("entry_id"):
            setts[s["entry_id"]] = s
    real = {e.get("id") for e, _ in mfl._ftl_pairs(ftl_rows)}
    out = []
    for e in ftl_rows:
        if e.get("type") != "entry" or not e.get("id"):
            continue
        s = setts.get(e["id"], {})
        src = e.get("fv_source") or s.get("fv_source")
        fv = _num(e.get("fv") if e.get("fv") is not None else s.get("fv"))
        if not src or fv is None:
            continue
        name = "fv:" + str(src).strip().lower()
        ets = _ts(e.get("ts")) or _ts(e.get("logged_at"))
        if fv > 1.0:
            fv = fv / 100.0
        if not _unit(fv):
            out.append(_item(name, ets, reason="bad_fv"))
        elif not s:
            out.append(_item(name, ets, fv, reason="unsettled"))
        elif e["id"] not in real:
            out.append(_item(name, ets, fv, reason="non_real_source"))
        elif str(s.get("result") or "").lower() not in ("yes", "no"):
            out.append(_item(name, ets, fv, reason="no_market_outcome"))
        else:
            out.append(_item(name, ets, fv, 1 if str(s["result"]).lower() == "yes" else 0))
    return out


# ---------------------------------------------------------------- metrics
def _metrics(items):
    sc = [(float(it["p"]), int(it["y"])) for it in items if it["y"] is not None and it["p"] is not None]
    reasons, superseded = {}, 0
    for it in items:
        if it["y"] is None:
            if it["reason"] == "superseded":
                superseded += 1
            else:
                reasons[it["reason"] or "unknown"] = reasons.get(it["reason"] or "unknown", 0) + 1
    tss = [it["ts"] for it in items if it["ts"] is not None]
    st = {"n": len(items), "n_scorable": len(sc), "n_unscorable": sum(reasons.values()),
          "n_superseded": superseded, "unscorable": dict(sorted(reasons.items())),
          "brier": None, "log_loss": None, "hit_rate": None, "base_rate": None, "bss": None,
          "reliability_bins": [], "last_ts": max(tss).isoformat() if tss else None,
          "avg_confidence": None}
    confs = [it["conf"] for it in items if it["y"] is not None and it["conf"] is not None]
    if confs:  # sharpness only (Nimble: 1 - normalised entropy); never scored as a forecast
        st["avg_confidence"] = round(sum(confs) / len(confs), 6)
    if not sc:
        return st
    n = len(sc)
    st["brier"] = round(sum((p - y) ** 2 for p, y in sc) / n, 6)
    ll = 0.0
    for p, y in sc:
        q = min(max(p, EPS), 1.0 - EPS)
        ll -= y * math.log(q) + (1 - y) * math.log(1.0 - q)
    st["log_loss"] = round(ll / n, 6)
    decided = [(p, y) for p, y in sc if p != 0.5]
    if decided:
        st["hit_rate"] = round(sum(1 for p, y in decided if (p > 0.5) == bool(y)) / len(decided), 6)
    base = sum(y for _, y in sc) / n
    st["base_rate"] = round(base, 6)
    clim = base * (1.0 - base)
    if clim > 0:
        st["bss"] = round(1.0 - (sum((p - y) ** 2 for p, y in sc) / n) / clim, 6)
    bins = {}
    for p, y in sc:
        b = min(int(p * N_BINS), N_BINS - 1)
        bins.setdefault(b, []).append((p, y))
    for b in sorted(bins):
        grp = bins[b]
        st["reliability_bins"].append({
            "p_lo": round(b / N_BINS, 6), "p_hi": round((b + 1) / N_BINS, 6), "n": len(grp),
            "avg_p": round(sum(p for p, _ in grp) / len(grp), 6),
            "freq": round(sum(y for _, y in grp) / len(grp), 6)})
    return st


def _all_items(dec_rows, ftl_rows, mf_rows, now):
    pairs = [(e, s) for e, s in mfl._ftl_pairs(ftl_rows) if e]
    items = _dedupe(_nimble_items(dec_rows, pairs))
    return items + _mirofish_items(mf_rows, ftl_rows, now) + _fv_items(ftl_rows)


def _match_filter(src, flt):
    return flt is None or src == flt or src.startswith(str(flt) + ":")


def score(source_filter=None, since=None, *, decisions_path=None, ftl_path=None,
          mirofish_path=None, now=None):
    """{source: {n, n_scorable, brier, log_loss, hit_rate, reliability_bins, last_ts, ...}}.

    Deterministic for fixed files and `now` (now only decides MiroFish "pending").
    """
    d = default_paths()
    dec_rows = _rows(decisions_path or d["decisions"])
    ftl_rows = _rows(ftl_path or d["ftl"])
    mf_rows = _rows(mirofish_path or d["mirofish"])
    now = _ts(now) or datetime.now(timezone.utc)
    since_dt = _ts(since) if since is not None else None
    items = _all_items(dec_rows, ftl_rows, mf_rows, now)
    groups = {}
    for it in items:
        if not _match_filter(it["source"], source_filter):
            continue
        if since_dt is not None and (it["ts"] is None or it["ts"] < since_dt):
            continue
        groups.setdefault(it["source"], []).append(it)
    return {src: _metrics(groups[src]) for src in sorted(groups)}


def earned_weight(source, base, cap, *, min_n=MIN_N, shrink=SHRINK, bss_ref=BSS_REF,
                  stats=None, **score_kwargs):
    """Weight earned from the record (rule in the module docstring). Never raises."""
    try:
        b = float(base)
        c = float(cap)
        if not (math.isfinite(b) and math.isfinite(c)):
            return 0.0
    except (TypeError, ValueError):
        return 0.0
    fallback = max(0.0, min(b * float(shrink), c))
    try:
        st = stats if stats is not None else score(source_filter=source, **score_kwargs).get(source)
        if not isinstance(st, dict) or int(st.get("n_scorable") or 0) < int(min_n):
            return fallback
        bss = _num(st.get("bss"))
        if bss is None or not float(bss_ref) > 0:
            return fallback
        return max(0.0, min(b * bss / float(bss_ref), c))
    except Exception:
        return fallback


# ---------------------------------------------------------------- calibrate (Nimble veto input)
# calibrate(source, p) lives in calibration_map.py (identity under MIN_N, then Laplace-smoothed
# PAV isotonic map over this module's _all_items); re-exported here for existing callers.
from calibration_map import (  # noqa: E402,F401
    _FITS, _reset_cache, calibrate, calibrate_ex, isotonic_fit)


# ---------------------------------------------------------------- CLI
def _fmt(v, nd=3):
    return "-" if v is None else ("%.*f" % (nd, v))


def table(result):
    lines = ["%-32s%6s%7s%8s%8s%6s%8s  %s" % ("source", "n", "scored", "brier", "logloss",
                                              "hit", "bss", "unscorable")]
    for src, st in result.items():
        why = ",".join("%s=%d" % kv for kv in st["unscorable"].items())
        if st["n_superseded"]:
            why += (",superseded=%d" % st["n_superseded"]) if why else "superseded=%d" % st["n_superseded"]
        lines.append("%-32s%6d%7d%8s%8s%6s%8s  %s" % (
            src[:32], st["n"], st["n_scorable"], _fmt(st["brier"]), _fmt(st["log_loss"]),
            _fmt(st["hit_rate"], 2), _fmt(st["bss"]), why or "-"))
    if not result:
        lines.append("(no decisions or predictions found)")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="calibration_ledger")
    ap.add_argument("--source")
    ap.add_argument("--since")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--decisions")
    ap.add_argument("--ftl")
    ap.add_argument("--mirofish")
    ap.add_argument("--out", default=SUMMARY_PATH)
    ap.add_argument("--no-write", action="store_true")
    a = ap.parse_args(argv)
    d = default_paths()
    paths = {"decisions": a.decisions or d["decisions"], "ftl": a.ftl or d["ftl"],
             "mirofish": a.mirofish or d["mirofish"]}
    res = score(a.source, a.since, decisions_path=paths["decisions"], ftl_path=paths["ftl"],
                mirofish_path=paths["mirofish"])
    doc = {"generated_at": datetime.now(timezone.utc).isoformat(), "inputs": paths,
           "min_n": MIN_N, "shrink": SHRINK, "bss_ref": BSS_REF, "sources": res,
           "earned_weight_applied": False}
    if not a.no_write:
        try:
            os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
            with open(a.out, "w") as f:
                json.dump(doc, f, indent=2, sort_keys=True)
                f.write("\n")
        except OSError as e:
            print("summary not written: %s" % e, file=sys.stderr)
    if a.json:
        print(json.dumps(doc, indent=2, sort_keys=True))
    else:
        for k, v in paths.items():
            print("%-9s %s%s" % (k, v, "" if os.path.exists(v) else "  (missing)"))
        print(table(res))
        if not a.no_write:
            print("wrote %s" % a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
