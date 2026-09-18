"""Slice 12 — shadow Intent vs Valedia POST parity (mockable 7d window).

Brain Coinbase + HL short + Polymarket arb + intel. Records compare.ok / diffs.
Fail-closed on cutover-dangerous mismatches. Allocator Coinbase is not evidence.
No HTTP, keys, writer flips, live flags, SPOT_RECONCILE_HTTP, or AUTOEXEC.
(c) GOATWORTHY Post LLC.
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone

import valedia_config as vc
from nautilus_intent import DEFAULT_QUEUE, intent_from_dict
from nautilus_shadow_coinbase import compare_intent_to_post, post_body_from_intent
from nautilus_shadow_venues import (
    compare_intent_to_hl_post,
    compare_intent_to_pm_post,
    post_body_from_hl_intent,
    post_body_from_pm_intent,
)

WINDOW_DEFAULT = "7d"
EVIDENCE = frozenset({"brain_coinbase", "hl_short", "polymarket_arb", "intel_coinbase"})
CUTOVER_DAYS = 7

# Field names from venue compare_* that would send the wrong order at cutover.
_DANGEROUS_FIELD = {
    "side": "side",
    "side/is_buy": "side",
    "instrument/product_id": "instrument",
    "instrument/coin": "instrument",
    "instrument/tokenID": "instrument",
    "size": "size",
    "notional/quote_size": "size",
    "qty/base_size": "size",
    "qty/s": "size",
    "qty/size": "size",
    "venue": "venue",
    "signed": "signed",
    "live_client_hook": "live_client_hook",
    "leverage": "leverage",
    "reduce_only": "reduce_only",
    "tif": "tif",
    "tif/orderType": "tif",
    "order_type": "order_type",
    "post_body": "size",
}
_WINDOW_RE = re.compile(r"^(\d+)\s*d(ays?)?$", re.I)


def parse_window(spec) -> timedelta:
    """'7d' / '7 days' / int days / timedelta. Unknown spec fails closed."""
    if spec is None:
        spec = WINDOW_DEFAULT
    if isinstance(spec, timedelta):
        if spec.total_seconds() <= 0:
            raise ValueError("window must be > 0")
        return spec
    if isinstance(spec, (int, float)):
        if spec <= 0:
            raise ValueError("window must be > 0")
        return timedelta(days=float(spec))
    m = _WINDOW_RE.match(str(spec).strip())
    if not m:
        raise ValueError(f"window must be Nd (got {spec!r})")
    days = int(m.group(1))
    if days <= 0:
        raise ValueError("window must be > 0")
    return timedelta(days=days)


def parse_ts(raw):
    s = str(raw or "").strip()
    if not s:
        return None
    s = s.replace(" UTC", "").replace("Z", "+00:00")
    if "T" not in s and " " in s:
        s = s.replace(" ", "T")
        if "+" not in s and s[-1].isdigit():
            s += "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def row_ts(rec: dict):
    return parse_ts((rec or {}).get("enqueued_at")) or parse_ts((rec or {}).get("ts"))


def in_window(rec: dict, window: timedelta, now: datetime) -> bool:
    ts = row_ts(rec)
    if ts is None:
        return False
    return (now - window) <= ts <= now


def classify(rec: dict) -> str:
    """Bucket a queue row. Allocator Coinbase is never cutover evidence."""
    r = rec or {}
    kind = r.get("kind")
    venue = str(r.get("venue") or "").strip().lower()
    writer = r.get("writer")
    if kind == "hl_short" or venue == "hyperliquid":
        return "hl_short"
    if kind == "polymarket_arb" or venue == "polymarket":
        return "polymarket_arb"
    if r.get("allocator_call") is not None or r.get("allocator_post_compare") is not None:
        return "allocator_coinbase"
    if writer == "rebalancer" or r.get("proposed_trade") is not None:
        return "rebalancer"
    if writer == "unified_brain" or r.get("strategy") == "UNIFIED_BRAIN":
        return "brain_coinbase"
    feed = r.get("source_feed")
    note = str((r.get("gates") or {}).get("gate_note") or "")
    intelish = bool(feed) or "slice10_intel" in note or kind in (
        "crypto_listed", "crypto_new_listing", "stock", "kalshi", "arb")
    if intelish:
        if venue == "coinbase":
            return "intel_coinbase"
        if venue == "alpaca":
            return "intel_alpaca"
        if kind == "arb":
            return "intel_arb"
        return "intel_kalshi"
    return "other"


def flag_snapshot() -> dict:
    return {
        "NAUTILUS_ENABLED": bool(vc.NAUTILUS_ENABLED),
        "NAUTILUS_LIVE": bool(vc.NAUTILUS_LIVE),
        "SPOT_RECONCILE_HTTP": bool(vc.spot_reconcile_http_enabled()),
        "REBALANCER_AUTOEXEC": bool(vc.rebalancer_autoexec_enabled()),
        "VENUE_WRITER": dict(vc.VENUE_WRITER),
    }


def load_queue(path: str) -> tuple[list, list]:
    """Return (rows, corrupt_diffs). Corrupt JSONL is fail-closed."""
    rows, corrupt = [], []
    if not path or not os.path.exists(path):
        return rows, corrupt
    with open(path) as f:
        for i, line in enumerate(f, 1):
            s = line.strip()
            if not s:
                continue
            try:
                rec = json.loads(s)
            except json.JSONDecodeError as e:
                corrupt.append({
                    "category": "queue_corrupt", "field": "jsonl",
                    "line": i, "error": str(e)[:120],
                    "intent": None, "post": None,
                })
                continue
            if not isinstance(rec, dict):
                corrupt.append({
                    "category": "queue_corrupt", "field": "jsonl",
                    "line": i, "error": "not an object",
                    "intent": None, "post": None,
                })
                continue
            rows.append(rec)
    return rows, corrupt


def _intent(rec: dict):
    return intent_from_dict(rec)


def _alpaca_body(intent) -> dict:
    """Unsigned body matching alpaca_executor.market_buy/sell. No HTTP."""
    body = {
        "path": "POST /v2/orders",
        "signed": False,
        "live_client_hook": None,
        "symbol": intent.instrument,
        "side": str(intent.side or "").lower(),
        "type": "market",
        "time_in_force": "day",
    }
    if intent.notional is not None:
        body["notional"] = f"{float(intent.notional):.2f}"
    elif intent.qty is not None:
        body["qty"] = str(intent.qty)
    return body


def _kalshi_body(intent) -> dict:
    """Unsigned shape note. Does not invent contract count from notional."""
    return {
        "path": "POST /portfolio/orders",
        "signed": False,
        "live_client_hook": None,
        "ticker": intent.instrument,
        "action": intent.side,
        "notional": intent.notional,
        "note": "intel Intent is notional/IOC; Valedia create_order wants count+yes/no",
    }


def _compare_alpaca(intent, post: dict) -> dict:
    mismatches = []
    if (post or {}).get("symbol") != intent.instrument:
        mismatches.append({"field": "instrument/product_id", "intent": intent.instrument,
                           "post": (post or {}).get("symbol")})
    if str((post or {}).get("side") or "").lower() != str(intent.side or "").lower():
        mismatches.append({"field": "side", "intent": intent.side, "post": (post or {}).get("side")})
    if intent.notional is not None and "notional" in (post or {}):
        try:
            if abs(float(intent.notional) - float(post["notional"])) > 1e-9:
                mismatches.append({"field": "notional/quote_size", "intent": intent.notional,
                                   "post": post.get("notional")})
        except (TypeError, ValueError):
            mismatches.append({"field": "notional/quote_size", "intent": intent.notional,
                               "post": (post or {}).get("notional")})
    if (post or {}).get("signed") is True:
        mismatches.append({"field": "signed", "intent": False, "post": True})
    if (post or {}).get("live_client_hook") is not None:
        mismatches.append({"field": "live_client_hook", "intent": None,
                           "post": (post or {}).get("live_client_hook")})
    # tif IOC vs Alpaca DAY is a known Valedia-writer gap, not NT cutover evidence.
    return {"ok": not mismatches, "mismatches": mismatches}


def _compare_kalshi(intent, post: dict) -> dict:
    mismatches = []
    if (post or {}).get("ticker") != intent.instrument:
        mismatches.append({"field": "instrument/product_id", "intent": intent.instrument,
                           "post": (post or {}).get("ticker")})
    if (post or {}).get("signed") is True:
        mismatches.append({"field": "signed", "intent": False, "post": True})
    if (post or {}).get("live_client_hook") is not None:
        mismatches.append({"field": "live_client_hook", "intent": None,
                           "post": (post or {}).get("live_client_hook")})
    return {"ok": not mismatches, "mismatches": mismatches,
            "schema_gap": "kalshi_count_yes_no"}


def rebuild_post(rec: dict, intent, bucket: str):
    """What Valedia would POST from this Intent. Never sends."""
    stored = rec.get("canonical_post")
    if bucket in ("brain_coinbase", "intel_coinbase", "rebalancer"):
        return post_body_from_intent(intent), compare_intent_to_post
    if bucket == "hl_short":
        bound = None
        orders = (((stored or {}).get("action") or {}).get("orders") or [])
        if orders:
            bound = orders[0].get("p")
        if bound is None:
            raise ValueError("hl short needs bound_px on canonical_post")
        ro = bool(rec.get("reduce_only"))
        post = post_body_from_hl_intent(intent, bound, reduce_only=ro, leverage=0)
        return post, lambda i, p: compare_intent_to_hl_post(i, p, reduce_only=ro)
    if bucket == "polymarket_arb":
        return post_body_from_pm_intent(intent), compare_intent_to_pm_post
    if bucket == "intel_alpaca":
        return _alpaca_body(intent), _compare_alpaca
    if bucket in ("intel_kalshi", "intel_arb"):
        return _kalshi_body(intent), _compare_kalshi
    return None, None


def fence_diffs(rec: dict) -> list:
    """Always fail-closed: a shadow row must never look live-dispatchable."""
    out = []
    if rec.get("dispatchable") is True:
        out.append({"category": "dispatchable", "field": "dispatchable",
                    "intent": False, "post": True})
    gates = rec.get("gates") or {}
    if gates.get("live_flag") is True:
        out.append({"category": "live_flag", "field": "live_flag",
                    "intent": False, "post": True})
    if rec.get("live_client_hook") is not None:
        out.append({"category": "live_client_hook", "field": "live_client_hook",
                    "intent": None, "post": rec.get("live_client_hook")})
    post = rec.get("canonical_post") or {}
    if isinstance(post, dict) and post.get("signed") is True:
        out.append({"category": "signed", "field": "signed",
                    "intent": False, "post": True})
    if isinstance(post, dict) and post.get("live_client_hook") is not None:
        out.append({"category": "live_client_hook", "field": "live_client_hook",
                    "intent": None, "post": post.get("live_client_hook")})
    lev = rec.get("leverage")
    if lev not in (None, 0, 0.0):
        out.append({"category": "leverage", "field": "leverage",
                    "intent": 0, "post": lev})
    if rec.get("venue_writer") == "nautilus":
        out.append({"category": "writer_nautilus", "field": "venue_writer",
                    "intent": "valedia|none", "post": "nautilus"})
    return out


def _dangerize(mismatches, evidence: bool) -> tuple[list, list]:
    dangerous, advisory = [], []
    for m in mismatches or []:
        field = m.get("field")
        cat = _DANGEROUS_FIELD.get(field)
        item = dict(m)
        if cat:
            item["category"] = cat
            (dangerous if evidence else advisory).append(item)
        else:
            item["category"] = field or "other"
            advisory.append(item)
    return dangerous, advisory


def compare_row(rec: dict) -> dict:
    bucket = classify(rec)
    evidence = bucket in EVIDENCE
    dangerous = fence_diffs(rec)
    advisory = []
    mismatches = []
    rebuilt_ok = None
    stored_ok = (rec.get("compare") or {}).get("ok")
    try:
        intent = _intent(rec)
        builder, cmp_fn = rebuild_post(rec, intent, bucket)
    except Exception as e:
        intent, builder, cmp_fn = None, None, None
        if evidence:
            dangerous.append({
                "category": "size", "field": "post_body",
                "intent": rec.get("side"), "post": str(e)[:80],
            })
        else:
            advisory.append({
                "category": "post_body", "field": "post_body",
                "intent": rec.get("side"), "post": str(e)[:80],
            })
    if cmp_fn and builder is not None and intent is not None:
        rebuilt = cmp_fn(intent, builder)
        mismatches.extend(rebuilt.get("mismatches") or [])
        rebuilt_ok = rebuilt.get("ok")
        d, a = _dangerize(rebuilt.get("mismatches") or [], evidence)
        dangerous.extend(d)
        advisory.extend(a)
        stored_post = rec.get("canonical_post")
        if stored_post:
            stored = cmp_fn(intent, stored_post)
            mismatches.extend(stored.get("mismatches") or [])
            d, a = _dangerize(stored.get("mismatches") or [], evidence)
            if d and stored_ok is True:
                dangerous.append({
                    "category": "stale_compare_ok", "field": "compare.ok",
                    "intent": True, "post": False,
                })
            dangerous.extend(d)
            advisory.extend(a)
    compare_ok = (rebuilt_ok is True) and not dangerous
    return {
        "bucket": bucket,
        "intent_id": rec.get("intent_id"),
        "ts": rec.get("enqueued_at") or rec.get("ts"),
        "evidence": evidence,
        "compare_ok": bool(compare_ok) and not dangerous,
        "stored_compare_ok": stored_ok,
        "mismatches": mismatches,
        "dangerous": dangerous,
        "advisory": advisory,
    }


def _empty_bucket():
    return {"n": 0, "compare_ok": 0, "dangerous": 0, "days_ok": 0}


def report(*, queue_path=None, rows=None, window="7d", now=None, out_path=None):
    """Build the parity report. Never mutates live flags. Never POSTs."""
    before = flag_snapshot()
    now_dt = parse_ts(now) if not isinstance(now, datetime) else now
    if now_dt is None:
        now_dt = datetime.now(timezone.utc)
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)
    win = parse_window(window)
    corrupt = []
    if rows is None:
        qpath = queue_path or DEFAULT_QUEUE
        rows, corrupt = load_queue(qpath)
        source = qpath
    else:
        rows = list(rows)
        source = queue_path or "<rows>"
    in_win = [r for r in rows if in_window(r, win, now_dt)]
    buckets = {}
    diffs, dangerous, ignored = [], [], {}
    ok_dates = {}
    for rec in in_win:
        cr = compare_row(rec)
        b = cr["bucket"]
        buckets.setdefault(b, _empty_bucket())
        buckets[b]["n"] += 1
        if cr["compare_ok"]:
            buckets[b]["compare_ok"] += 1
            ts = row_ts(rec)
            if ts is not None and b in EVIDENCE:
                ok_dates.setdefault(b, set()).add(ts.date().isoformat())
        if cr["dangerous"]:
            buckets[b]["dangerous"] += 1
            for d in cr["dangerous"]:
                d = dict(d)
                d["intent_id"] = cr["intent_id"]
                d["bucket"] = b
                dangerous.append(d)
        if cr["mismatches"] or cr["advisory"]:
            diffs.append({
                "intent_id": cr["intent_id"], "bucket": b,
                "compare_ok": cr["compare_ok"],
                "mismatches": cr["mismatches"],
                "advisory": cr["advisory"],
            })
        if b not in EVIDENCE:
            ignored[b] = ignored.get(b, 0) + 1
    for b, dates in ok_dates.items():
        buckets.setdefault(b, _empty_bucket())["days_ok"] = len(dates)
    dangerous.extend(corrupt)
    after = flag_snapshot()
    if after != before:
        dangerous.append({
            "category": "flag_mutation", "field": "valedia_config",
            "intent": before, "post": after,
        })
    ok = not dangerous
    ready = {}
    for venue, bucket in (("coinbase", "brain_coinbase"),
                          ("hyperliquid", "hl_short"),
                          ("polymarket", "polymarket_arb")):
        b = buckets.get(bucket) or _empty_bucket()
        ready[venue] = bool(
            ok and b["n"] > 0 and b["compare_ok"] == b["n"]
            and b.get("days_ok", 0) >= CUTOVER_DAYS
        )
    # Intel coinbase is evidence for POST-diff, not a writer cutover surface.
    result = {
        "ok": ok,
        "window": str(window) if not isinstance(window, timedelta) else f"{window.days}d",
        "now": now_dt.isoformat(),
        "from_ts": (now_dt - win).isoformat(),
        "source": source,
        "n_rows": len(rows),
        "n_in_window": len(in_win),
        "n_ignored": sum(ignored.values()),
        "ignored": ignored,
        "buckets": buckets,
        "diffs": diffs,
        "dangerous": dangerous,
        "cutover_ready": ready,
        "flags": after,
        "note": "Allocator Coinbase is not cutover evidence. No live flags flipped.",
    }
    if out_path:
        parent = os.path.dirname(out_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(result, f, indent=2, default=str)
            f.write("\n")
    return result


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(description="Slice 12 shadow parity report (no HTTP)")
    p.add_argument("--queue", default=None, help="intent JSONL (default models queue)")
    p.add_argument("--window", default=WINDOW_DEFAULT)
    p.add_argument("--now", default=None, help="ISO timestamp (fixtures)")
    p.add_argument("--out", default=None, help="write JSON report")
    args = p.parse_args(argv)
    rep = report(queue_path=args.queue, window=args.window, now=args.now, out_path=args.out)
    print(json.dumps({
        "ok": rep["ok"], "n_in_window": rep["n_in_window"],
        "cutover_ready": rep["cutover_ready"],
        "n_dangerous": len(rep["dangerous"]),
        "buckets": rep["buckets"], "flags": rep["flags"],
    }, indent=2, default=str))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
