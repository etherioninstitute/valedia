"""HKUDS Shadow Account spirit as a Valedia paper log. No codegen. No broker.

Inventory of Alpha Zoo / connectors lives in inventory(). Nothing from the
HKUDS tree is imported. Fixture journal only — no network.

  python3 vibe_shadow_paper.py
  python3 vibe_shadow_paper.py --inventory
"""
from __future__ import annotations

import argparse
import fcntl
import json
import math
import os
from datetime import datetime, timezone

import valedia_config as vc

MODE = "PAPER/SHADOW"
SOURCE = "vibe_shadow_paper"  # "paper" keeps Kelly / real_track_record off
DIP = -0.08  # one price feature from Shadow Account: prior_5d_return
MAX_TRADE = 25.0
MAX_DAILY = 100.0
MIN_SIZE = 5.0
DECISION_KEEP = 400  # ponytail: tail rewrite. Dated archive when an audit needs the head.
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
DECISION_LOG = os.path.join(_ROOT, "data", "vibe-shadow", "decisions.jsonl")
CLONE = "MemPalace/sources/repos/github/HKUDS-Vibe-Trading"
HEAD = "a65427a3d587e43cd5c7541948b36524e8fd2f12"

# Module counts at HEAD. qlib158 is the folder name; 154 modules are on disk.
ZOO = (
    ("academic", 12, "agent/src/factors/zoo/academic"),
    ("alpha101", 101, "agent/src/factors/zoo/alpha101"),
    ("fundamental", 4, "agent/src/factors/zoo/fundamental"),
    ("gtja191", 191, "agent/src/factors/zoo/gtja191"),
    ("qlib158", 154, "agent/src/factors/zoo/qlib158"),
)
CONNECTORS = (
    "alpaca", "binance", "dhan", "etoro", "futu", "ibkr", "kis", "longbridge",
    "mt5", "okx", "robinhood", "scalable", "shoonya", "tiger", "toss",
    "trading212", "upbit", "zerodha",
)
FENCED = (
    "agent/src/trading/connectors/",
    "agent/src/live/sdk_order_gate.py",
    "agent/src/live/runtime/runner.py",
    "agent/src/tools/bash_tool.py",
    "agent/src/tools/alpha_bench_tool.py",
    "agent/src/shadow_account/codegen.py",
    "agent/src/core/runner.py",
    "agent/mcp_server.py",
    "agent/api_server.py",
)


class VibeFence(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def live_enabled(env=None):
    """Hard off. An env flag cannot open an HKUDS broker connector."""
    return False


def place_order(venue, payload=None):
    raise VibeFence("vibe_broker_write")


def create_order(*_args, **_kwargs):
    raise VibeFence("vibe_broker_write")


def exec_generated(source=""):
    raise VibeFence("vibe_codegen")


def shell(command=""):
    raise VibeFence("vibe_shell")


def inventory():
    return {
        "clone": CLONE,
        "head": HEAD,
        "distinct_from": "vaughanf1-vibe-trader@f9dbdae8766d48589c46f12730f0d3916eb61b16",
        "license": "MIT",
        "qlib_license": "Apache-2.0",
        "zoo": [{"family": n, "modules": c, "path": p} for n, c, p in ZOO],
        "zoo_modules": sum(c for _, c, _ in ZOO),
        "connectors": list(CONNECTORS),
        "fenced": list(FENCED),
        "vendored": False,
        "mcp_installed": False,
        "pypi_installed": False,
        "create_order": False,
        "broker_write": False,
        "absorbed": "scripts/integrations/vibe_shadow_paper.py",
    }


def _now():
    return datetime.now(timezone.utc).isoformat()


def _num(v):
    if v in (None, ""):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _limits(over=None):
    base = {"max_trade_size_usd": MAX_TRADE, "max_daily_notional_usd": MAX_DAILY,
            "min_size_usd": MIN_SIZE, "kill_switch": False}
    if over:
        base.update(over)
    return base


def _row(trade, reason, size):
    trade = trade or {}
    gross, fee = _num(trade.get("gross_pnl")), _num(trade.get("fee_usd"))
    net = None if gross is None or fee is None else round(gross - fee, 6)
    return {
        "type": "decision", "source": SOURCE, "strategy": "SHADOW_DIP",
        "venue": "vibe-paper", "ticker": trade.get("symbol") or "",
        "side": "would-shadow", "reason": reason,
        "prior_5d_return": _num(trade.get("prior_5d_return")),
        "gross_pnl": gross, "fee_usd": fee, "net_pnl": net,
        "size_usd": round(float(size or 0), 6),
        "would_shadow": reason == "shadow_log", "filled": False,
        "create_order": False, "broker_write": False, "start_copy": False,
        "live": False, "counts_toward_kelly": False, "paperTrading": True,
        "mode": MODE, "ts": _now(), "valedia_version": vc.VALEDIA_VERSION,
    }


def decide(trade, state, limits=None, env=None):
    """Paper hypothetical only. create_order and broker_write stay false."""
    limits = _limits(limits)
    state = state or {"exposure_today_usd": 0.0, "kill_switch": False}
    env = {} if env is None else env
    if env.get("_attempt_order") or env.get("_attempt_copy"):
        return _row(trade, "vibe_broker_write", 0)
    if state.get("kill_switch") or limits.get("kill_switch"):
        return _row(trade, "kill_switch", 0)
    if _num((trade or {}).get("gross_pnl")) is None or _num((trade or {}).get("fee_usd")) is None:
        return _row(trade, "missing_fee", 0)
    if _num(trade.get("gross_pnl")) - _num(trade.get("fee_usd")) <= 0:
        return _row(trade, "fee_true", 0)
    prior = _num(trade.get("prior_5d_return"))
    if prior is None:
        return _row(trade, "missing_feature", 0)
    if prior > DIP:
        return _row(trade, "review_only", 0)
    remaining = max(0.0, float(limits["max_daily_notional_usd"]) - float(state.get("exposure_today_usd") or 0))
    size = min(float(limits["max_trade_size_usd"]), remaining)
    if size <= 0:
        return _row(trade, "daily_notional", 0)
    if size < float(limits["min_size_usd"]):
        return _row(trade, "size_too_small", 0)
    return _row(trade, "shadow_log", size)


def read_decisions(path=None):
    path = path or DECISION_LOG
    if not path or not os.path.exists(path):
        return []
    out = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def append_decision(row, path=None, keep=DECISION_KEEP):
    path = path or DECISION_LOG
    row = dict(row or {})
    row.update({"type": "decision", "source": SOURCE, "create_order": False,
                "broker_write": False, "start_copy": False, "live": False,
                "counts_toward_kelly": False})
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    line = json.dumps(row, default=str) + "\n"
    with open(path + ".write.lock", "a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            with open(path, "a") as f:
                f.write(line)
            if keep and keep > 0:
                with open(path) as f:
                    lines = f.readlines()
                if len(lines) > keep:
                    with open(path, "w") as f:
                        f.writelines(lines[-keep:])
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
    return path


def run_journal(trades, *, path=None, kill_switch=False, limits=None, env=None):
    path = path or DECISION_LOG
    state = {"exposure_today_usd": 0.0, "kill_switch": bool(kill_switch)}
    rows = []
    for trade in trades or []:
        row = decide(trade, state, limits=limits, env=env)
        append_decision(row, path)
        rows.append(row)
        if row.get("reason") == "shadow_log":
            state["exposure_today_usd"] += float(row.get("size_usd") or 0)
    if not rows:
        row = _row({}, "no_journal", 0)
        append_decision(row, path)
        rows.append(row)
    return rows


def tick_status(path=None, now=None, stale_s=7200):
    rows = read_decisions(path)
    last = rows[-1] if rows else None
    if not last:
        return {"last_tick_age_s": "none", "last_decision": "none", "stale": True, "n": 0}
    try:
        t = datetime.fromisoformat(str(last.get("ts")).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        age = max(0, int(((now or datetime.now(timezone.utc)) - t).total_seconds()))
    except (TypeError, ValueError):
        age = None
    return {"last_tick_age_s": "none" if age is None else age,
            "last_decision": str(last.get("reason") or "none"),
            "stale": age is None or age > int(stale_s), "n": len(rows)}


def fixture_journal():
    return [
        {"symbol": "BTC-USD", "prior_5d_return": -0.10, "gross_pnl": 40.0, "fee_usd": 2.0},
        {"symbol": "ETH-USD", "prior_5d_return": -0.09, "gross_pnl": 1.0, "fee_usd": 2.0},
        {"symbol": "SOL-USD", "prior_5d_return": 0.04, "gross_pnl": 20.0, "fee_usd": 1.0},
        {"symbol": "DOGE-USD", "prior_5d_return": None, "gross_pnl": 10.0, "fee_usd": 0.5},
    ]


def _public(row):
    return {k: row.get(k) for k in (
        "ticker", "reason", "size_usd", "net_pnl", "create_order",
        "broker_write", "live", "source")}


def main(argv=None):
    p = argparse.ArgumentParser(description="Vibe shadow paper desk")
    p.add_argument("--out", default=DECISION_LOG)
    p.add_argument("--inventory", action="store_true")
    args = p.parse_args(argv)
    if args.inventory:
        print(json.dumps(inventory()))
        return 0
    rows = run_journal(fixture_journal(), path=args.out, env={})
    print(json.dumps({"ok": True, "n": len(rows), "create_order": False,
                      "broker_write": False, "rows": [_public(r) for r in rows]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
