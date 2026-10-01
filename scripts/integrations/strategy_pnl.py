#!/usr/bin/env python3
"""strategy_pnl.py — per-strategy fee-true P&L, read from forward_test_ledger ONLY.

report(since=None, ledger_path=None) -> {strategy: {n_open, n_settled, wins, losses,
    gross, fees, net, win_rate, avg_net, last_ts, proven, n_unreconciled, backfill}}

  - Live rows: entry AND settlement are real (forward_test_ledger._is_non_real_source
    false). Paper/sandbox/shadow rows are dropped entirely.
  - UNRECONCILED entries (POST-ack, no fill yet) are counted in n_unreconciled only;
    a stub closed by spot_reconcile is not a settled trade.
  - Backfill rows (source contains "backfill", fees unverified) are reported under
    "<STRATEGY> [backfill]" with backfill=True and proven=False — never mixed into live.
  - net = realized_pnl_net; fees = settlement "fee" when recorded, else the entry fee;
    gross = net + fees. win = net > 0, loss = net < 0 (fee-true, not the binary outcome).
  - proven = n_settled >= 30 (live only).
  - since: ISO date/datetime (or datetime); filters settlements by settle time. Open and
    unreconciled counts are current state and are not filtered.

daily_lines() gives a short text block a report sender can include (not wired here).
CLI: python3 strategy_pnl.py [--since 2026-09-01] [--ledger path] [--json]
Read-only: no network, no orders, no writes.
(c) GOATWORTHY Post LLC.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import forward_test_ledger as ftl  # noqa: E402

PROVEN_MIN = 30
BACKFILL_TAG = " [backfill]"


def _parse_ts(v):
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    s = str(v or "").strip()
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _rows(ledger_path):
    path = ledger_path or ftl.LEDGER
    if not os.path.exists(path):
        return []
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue  # one bad line never hides the rest of the ledger
    return out


def _bucket(stats, key, backfill):
    return stats.setdefault(key, {
        "n_open": 0, "n_settled": 0, "wins": 0, "losses": 0, "gross": 0.0, "fees": 0.0,
        "net": 0.0, "win_rate": 0.0, "avg_net": 0.0, "last_ts": None, "proven": False,
        "n_unreconciled": 0, "backfill": backfill})


def _touch(b, when):
    if when and (b["last_ts"] is None or when > _parse_ts(b["last_ts"])):
        b["last_ts"] = when.isoformat()


def _is_backfill(src):
    return "backfill" in str(src or "").lower()


def report(since=None, ledger_path=None):
    rows = _rows(ledger_path)
    cutoff = _parse_ts(since) if since else None
    entries = {r.get("id"): r for r in rows if r.get("type") == "entry"}
    settled_ids = set()
    stats = {}
    for s in rows:
        if s.get("type") != "settlement":
            continue
        settled_ids.add(s.get("entry_id"))
        e = entries.get(s.get("entry_id"), {})
        src_s, src_e = s.get("source"), e.get("source")
        backfill = _is_backfill(src_s) or _is_backfill(src_e)
        if not backfill and (ftl._is_non_real_source(src_s) or ftl._is_non_real_source(src_e)):
            continue  # paper / sandbox / shadow / unreconciled stub-close
        when = _parse_ts(s.get("logged_at")) or _parse_ts(e.get("ts"))
        if cutoff and (when is None or when < cutoff):
            continue
        strat = ftl.canonical_category(s.get("strategy") or e.get("strategy") or "?")
        b = _bucket(stats, strat + BACKFILL_TAG if backfill else strat, backfill)
        net = float(s.get("realized_pnl_net") or 0)
        fee = s.get("fee")
        fee = float(fee) if fee is not None else float(e.get("entry_fee") or 0)
        b["n_settled"] += 1
        b["wins"] += net > 0
        b["losses"] += net < 0
        b["net"] += net
        b["fees"] += fee
        b["gross"] += net + fee
        _touch(b, when)
    for e in rows:
        if e.get("type") != "entry" or e.get("id") in settled_ids:
            continue
        src = str(e.get("source") or "")
        when = _parse_ts(e.get("ts")) or _parse_ts(e.get("logged_at"))
        strat = ftl.canonical_category(e.get("strategy") or "?")
        if _is_backfill(src):
            b = _bucket(stats, strat + BACKFILL_TAG, True)
            b["n_open"] += 1
        elif str(e.get("fill_status") or "").upper() == "UNRECONCILED" or src == "unreconciled":
            if ftl._is_non_real_source(e.get("intended_source") or "live"):
                continue  # an unreconciled paper/sandbox intent is not a live order
            b = _bucket(stats, strat, False)
            b["n_unreconciled"] += 1
        elif not ftl._is_non_real_source(src):
            b = _bucket(stats, strat, False)
            b["n_open"] += 1
        else:
            continue
        _touch(b, when)
    for b in stats.values():
        n = b["n_settled"]
        for k in ("gross", "fees", "net"):
            b[k] = round(b[k], 4)
        b["win_rate"] = round(100.0 * b["wins"] / n, 1) if n else 0.0
        b["avg_net"] = round(b["net"] / n, 4) if n else 0.0
        b["proven"] = (not b["backfill"]) and n >= PROVEN_MIN
    return stats


def _order(stats):
    return sorted(stats.items(), key=lambda kv: (kv[1]["backfill"], -kv[1]["net"], kv[0]))


def table(stats):
    hdr = (f"{'strategy':28}{'open':>5}{'unrec':>6}{'settled':>8}{'W':>4}{'L':>4}"
           f"{'win%':>7}{'gross$':>10}{'fees$':>9}{'net$':>10}{'avg$':>9}  proven")
    out = [hdr, "-" * len(hdr)]
    for k, b in _order(stats):
        out.append(f"{k:28}{b['n_open']:>5}{b['n_unreconciled']:>6}{b['n_settled']:>8}"
                   f"{b['wins']:>4}{b['losses']:>4}{b['win_rate']:>7.1f}{b['gross']:>10.2f}"
                   f"{b['fees']:>9.2f}{b['net']:>10.2f}{b['avg_net']:>9.2f}  "
                   f"{'yes' if b['proven'] else ('n/a (unverified fees)' if b['backfill'] else 'no')}")
    if not stats:
        out.append("(ledger empty)")
    if any(b["backfill"] for b in stats.values()):
        out.append("[backfill] = pnl_tracker history, fees UNVERIFIED; excluded from live edge.")
    return "\n".join(out)


def daily_lines(since=None, ledger_path=None, max_lines=8):
    """Short fee-true P&L block for a daily report. Pure: builds text, sends nothing."""
    try:
        stats = report(since=since, ledger_path=ledger_path)
    except Exception as e:
        return [f"Strategy P&L: unavailable ({str(e)[:60]})"]
    live = [(k, b) for k, b in _order(stats) if not b["backfill"]]
    back = [b for b in stats.values() if b["backfill"]]
    lines = ["Strategy P&L (fee-true, live ledger):"]
    if not live:
        lines.append("  no live spot/Kalshi rows yet")
    for k, b in live[:max_lines]:
        tag = "proven" if b["proven"] else f"unproven {b['n_settled']}/{PROVEN_MIN}"
        extra = f", {b['n_unreconciled']} unreconciled" if b["n_unreconciled"] else ""
        lines.append(f"  {k}: {b['wins']}W/{b['losses']}L net ${b['net']:+.2f} "
                     f"(fees ${b['fees']:.2f}), {b['n_open']} open{extra} [{tag}]")
    if len(live) > max_lines:
        lines.append(f"  … {len(live) - max_lines} more")
    if back:
        n = sum(b["n_settled"] for b in back)
        net = sum(b["net"] for b in back)
        lines.append(f"  backfill (fees unverified, not edge): {n} settled, net ${net:+.2f}")
    return lines


def main(argv=None):
    ap = argparse.ArgumentParser(prog="strategy_pnl")
    ap.add_argument("--since", default=None, help="ISO date/datetime lower bound")
    ap.add_argument("--ledger", default=None, help="ledger path (default: forward_test_ledger.LEDGER)")
    ap.add_argument("--json", action="store_true", help="JSON only")
    a = ap.parse_args(argv)
    stats = report(since=a.since, ledger_path=a.ledger)
    if not a.json:
        print(f"ledger: {a.ledger or ftl.LEDGER}" + (f"  since {a.since}" if a.since else ""))
        print(table(stats))
        print()
    print(json.dumps(stats, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
