"""Append-only JSONL decision log (one line per pool, every outcome) and the review CLI."""
from __future__ import annotations

import argparse
import json
import os
import threading
from typing import Iterable, List, Optional


class DecisionLog:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()

    def append(self, record: dict) -> None:
        """Write one complete line with a single O_APPEND write (no partial interleaving)."""
        line = (json.dumps(record, sort_keys=True, default=str, allow_nan=False) + "\n").encode()
        parent = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(parent, exist_ok=True)
        with self._lock:
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
            try:
                os.write(fd, line)
            finally:
                os.close(fd)


def read_records(path: str) -> List[dict]:
    """Parse the log; unreadable lines are skipped, a missing file is empty."""
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if isinstance(rec, dict):
                out.append(rec)
    return out


def filter_records(recs: Iterable[dict], action: Optional[str] = None,
                   final: Optional[str] = None, last: Optional[int] = None) -> List[dict]:
    """``action`` matches the model's answer, ``final`` the post-gate outcome."""
    out = [r for r in recs
           if (action is None or (r.get("decision") or {}).get("action") == action)
           and (final is None or r.get("final_action") == final)]
    return out[-last:] if last else out


def format_record(r: dict) -> str:
    d, s = r.get("decision") or {}, r.get("state") or {}
    failed = [f"{g['gate']}={g['value']}" for g in r.get("gates", []) if not g.get("passed")]
    conf = d.get("confidence")
    return (f"{r.get('ts', '?')} {s.get('symbol') or r.get('pool', '?')} "
            f"model={d.get('action')}@{conf if conf is None else format(conf, '.2f')} "
            f"risk={d.get('risk_score')} liq_usd={s.get('liquidity_usd')} "
            f"{r.get('latency_ms')}ms -> {str(r.get('final_action')).upper()}"
            + (f" | failed: {', '.join(failed)}" if failed else "")
            + (f" | error: {d['error']}" if d.get("error") else ""))


def review_main(argv: List[str], default_path: str = "decisions.jsonl") -> int:
    ap = argparse.ArgumentParser(prog="rh_meme_bot review",
                                 description="Read the decision log (yes and no decisions).")
    ap.add_argument("--action", choices=["skip", "hold", "buy"],
                    help="filter on the MODEL's answer")
    ap.add_argument("--final", choices=["skip", "buy"], help="filter on the final (gated) action")
    ap.add_argument("--last", type=int, metavar="N", help="only the last N matching entries")
    ap.add_argument("--file", default=default_path, help="decision log path")
    ap.add_argument("--json", action="store_true", help="print full JSON records")
    args = ap.parse_args(argv)
    recs = filter_records(read_records(args.file), args.action, args.final, args.last)
    for r in recs:
        print(json.dumps(r, sort_keys=True) if args.json else format_record(r))
    print(f"-- {len(recs)} record(s) from {args.file}")
    return 0
