#!/usr/bin/env python3
"""DaviddTech / TradingView paper desk. Shadow decisions only. No orders.

  python3 daviddtech_paper.py            # write one fixture decision
  python3 daviddtech_paper.py --validate # check sample payload
  python3 daviddtech_paper.py --status   # counts + last rows; no secrets
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional, Tuple

MODE = "PAPER/SHADOW"
SOURCE = "daviddtech_paper"
VENUE = "daviddtech-paper"
DECISION_KEEP = 400
_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
DECISION_LOG = os.path.join(_ROOT, "data", "daviddtech-paper", "decisions.jsonl")

ACTIONS = frozenset({"entry", "exit", "tp1", "tp2", "breakeven"})
REQUIRED = ("passphrase", "timestamp", "strategy_id", "ticker", "action", "entry_price", "volume")

STRATEGY_MAP = {
    "Waddah_1H_Anchor": "WADDAH_1H_ANCHOR",
    "Trendhoo_45M": "TRENDHOO_45M",
    "TopG_5M_Scalp": "TOPG_5M_SCALP",
    "waddah_1h_anchor": "WADDAH_1H_ANCHOR",
    "trendhoo_45m": "TRENDHOO_45M",
    "topg_5m_scalp": "TOPG_5M_SCALP",
}

ALLOWLIST_IDS = frozenset(STRATEGY_MAP.keys())

try:
    import valedia_config as vc  # noqa: WPS433

    _VALEDIA_VERSION = getattr(vc, "VALEDIA_VERSION", "unknown")
except Exception:  # noqa: BLE001 — desk must load without config
    vc = None
    _VALEDIA_VERSION = "unknown"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _num(v: Any) -> Optional[float]:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def live_flag(env: Optional[dict] = None) -> bool:
    env = os.environ if env is None else env
    return str(env.get("DAVIDD_LIVE") or env.get("DAVIDDTECH_LIVE") or "0").strip().lower() in (
        "1", "true", "yes", "on",
    )


def trading_halted(env: Optional[dict] = None) -> bool:
    env = os.environ if env is None else env
    raw = str(env.get("VALEDIA_TRADING_HALT") or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    if vc is not None:
        try:
            return bool(vc.trading_halted())
        except Exception:  # noqa: BLE001
            return False
    return False


def map_strategy(strategy_id: Any) -> str:
    sid = str(strategy_id or "").strip()
    if not sid:
        return "UNKNOWN"
    if sid in STRATEGY_MAP:
        return STRATEGY_MAP[sid]
    # loose match
    key = sid.replace("-", "_")
    if key in STRATEGY_MAP:
        return STRATEGY_MAP[key]
    return "DAV_UNKNOWN_%s" % re.sub(r"[^A-Za-z0-9_]+", "_", sid).upper()[:48]


def side_from_action(action: str, body: Optional[dict] = None) -> str:
    body = body or {}
    explicit = str(body.get("side") or body.get("direction") or "").strip().lower()
    if explicit in ("buy", "long"):
        return "buy"
    if explicit in ("sell", "short"):
        return "sell"
    if explicit in ("flat", "close"):
        return "flat"
    a = str(action or "").strip().lower()
    if a == "entry":
        return "buy"
    if a in ("exit", "tp1", "tp2"):
        return "sell"
    if a == "breakeven":
        return "flat"
    return "flat"


def normalize_payload(body: Any) -> dict:
    if not isinstance(body, dict):
        return {}
    out = {
        "passphrase": "" if body.get("passphrase") is None else str(body.get("passphrase")),
        "timestamp": str(body.get("timestamp") or "").strip(),
        "strategy_id": str(body.get("strategy_id") or "").strip(),
        "ticker": str(body.get("ticker") or "").strip(),
        "action": str(body.get("action") or "").strip().lower(),
        "subaccount": None if body.get("subaccount") in (None, "") else str(body.get("subaccount")),
        "entry_price": _num(body.get("entry_price")),
        "volume": _num(body.get("volume")),
        "portfolio_risk": _num(body.get("portfolio_risk")),
    }
    if body.get("side") not in (None, ""):
        out["side"] = str(body.get("side")).strip().lower()
    if body.get("direction") not in (None, ""):
        out["direction"] = str(body.get("direction")).strip().lower()
    return out


def validate_payload(body: Any) -> Tuple[bool, str]:
    if not isinstance(body, dict):
        return False, "not_object"
    n = normalize_payload(body)
    for key in REQUIRED:
        if key == "passphrase":
            if not n.get("passphrase"):
                return False, "missing_passphrase"
            continue
        if key in ("entry_price", "volume"):
            if n.get(key) is None:
                return False, "missing_%s" % key
            continue
        if not n.get(key):
            return False, "missing_%s" % key
    if n["action"] not in ACTIONS:
        return False, "bad_action"
    if n["entry_price"] is not None and n["entry_price"] <= 0:
        return False, "bad_entry_price"
    if n["volume"] is not None and n["volume"] < 0:
        return False, "bad_volume"
    # timestamp: accept ISO-ish; refuse empty already covered
    ts = n["timestamp"]
    if len(ts) < 8:
        return False, "bad_timestamp"
    return True, "ok"


def _logged_raw(body: dict) -> dict:
    n = normalize_payload(body)
    return {
        "timestamp": n.get("timestamp"),
        "strategy_id": n.get("strategy_id"),
        "ticker": n.get("ticker"),
        "action": n.get("action"),
        "subaccount": n.get("subaccount"),
        "entry_price": n.get("entry_price"),
        "volume": n.get("volume"),
        "portfolio_risk": n.get("portfolio_risk"),
    }


def _strip_passphrase(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _strip_passphrase(v) for k, v in obj.items() if "passphrase" not in str(k).lower()}
    if isinstance(obj, list):
        return [_strip_passphrase(x) for x in obj]
    return obj


def payload_hash(body: dict) -> str:
    red = _logged_raw(body if isinstance(body, dict) else {})
    blob = json.dumps(red, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def band_for(n: dict, *, halt: bool = False) -> Tuple[str, str]:
    sid = n.get("strategy_id") or ""
    if halt:
        return "review_only", "trading_halt_shadow"
    if sid in ALLOWLIST_IDS or sid.replace("-", "_") in ALLOWLIST_IDS:
        return "allowlist", "mapped_strategy"
    if sid:
        return "review_only", "unknown_strategy_id"
    return "refuse", "missing_strategy_id"


def to_decision(body: dict, *, halt: bool = False, env: Optional[dict] = None) -> dict:
    """Build a paper decision. create_order and live are always false."""
    env = os.environ if env is None else env
    n = normalize_payload(body)
    halt = bool(halt) or trading_halted(env)
    band, reason = band_for(n, halt=halt)
    strategy = map_strategy(n.get("strategy_id"))
    side = side_from_action(n.get("action") or "", n)
    # hypothetical size only — never treat volume_tv as live USD
    size_usd = None
    row = {
        "type": "decision",
        "source": SOURCE,
        "strategy": strategy,
        "venue": VENUE,
        "ticker": n.get("ticker") or "",
        "side": side,
        "action": n.get("action") or "",
        "strategy_id": n.get("strategy_id") or "",
        "band": band,
        "reason": reason,
        "entry_price": n.get("entry_price"),
        "volume_tv": n.get("volume"),
        "portfolio_risk": n.get("portfolio_risk"),
        "subaccount": n.get("subaccount"),
        "size_usd": size_usd,
        "would_have_pnl": None,
        "filled": False,
        "create_order": False,
        "live": False,
        "paperTrading": True,
        "mode": MODE,
        "counts_toward_kelly": False,
        "davidd_live": False,
        "ts": _now(),
        "alert_timestamp": n.get("timestamp") or "",
        "valedia_version": _VALEDIA_VERSION,
        "raw_payload_sha256": payload_hash(body if isinstance(body, dict) else {}),
        "raw_payload": _logged_raw(body if isinstance(body, dict) else {}),
    }
    # Force fence even if caller stuffed live flags into body
    row["create_order"] = False
    row["live"] = False
    row["filled"] = False
    row["paperTrading"] = True
    row["counts_toward_kelly"] = False
    if live_flag(env):
        # still refuse live; note attempt
        row["reason"] = (row.get("reason") or "") + "|davidd_live_ignored"
        row["create_order"] = False
        row["live"] = False
    return row


def public_fields(row: dict) -> dict:
    keys = (
        "ticker", "side", "action", "strategy", "strategy_id", "band", "reason",
        "entry_price", "volume_tv", "size_usd", "create_order", "live", "mode",
        "filled", "paperTrading", "ts", "valedia_version",
    )
    return {k: row.get(k) for k in keys}


def append_decision(row: dict, path: Optional[str] = None, keep: int = DECISION_KEEP) -> str:
    path = path or DECISION_LOG
    row = dict(row or {})
    row.update({
        "type": "decision",
        "source": SOURCE,
        "venue": VENUE,
        "create_order": False,
        "live": False,
        "filled": False,
        "paperTrading": True,
        "counts_toward_kelly": False,
        "mode": MODE,
    })
    row = _strip_passphrase(row)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    line = json.dumps(row, default=str) + "\n"
    if '"passphrase":' in line.lower():
        raise ValueError("refusing jsonl write: passphrase field present")
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


def read_decisions(path: Optional[str] = None) -> list:
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


def _flag_true(v: Any) -> bool:
    if v is True or v == 1:
        return True
    return isinstance(v, str) and v.strip().lower() == "true"


def _flag_word(v: Any) -> str:
    if _flag_true(v):
        return "true"
    if v is False or v == 0 or (isinstance(v, str) and v.strip().lower() == "false"):
        return "false"
    return "false" if v is None else str(v).lower()


def resolve_decision_log(explicit: Optional[str] = None) -> str:
    """Prefer workspace-v2 jsonl, then this tree, then ~/.openclaw/workspace."""
    if explicit:
        return explicit
    home = os.path.expanduser("~")
    preferred = os.path.join(home, ".openclaw", "workspace-v2", "data", "daviddtech-paper", "decisions.jsonl")
    fallback = os.path.join(home, ".openclaw", "workspace", "data", "daviddtech-paper", "decisions.jsonl")
    for path in (preferred, DECISION_LOG, fallback):
        if os.path.isfile(path):
            return path
    return preferred


def status_report(path: str, last: int = 10) -> Tuple[str, int]:
    """Counts and compact rows. Exit 1 on create_order, live, or a passphrase key.

    Does not read DAVIDDTECH_WEBHOOK_PASSPHRASE. A passphrase row is not echoed.
    """
    if not path or not os.path.isfile(path):
        return "missing path=%s\n" % (path or ""), 2
    last = 10 if last is None else max(0, int(last))
    parsed = []
    fence = 0
    with open(path) as f:
        for raw in f:
            if not raw.strip():
                continue
            if "passphrase" in raw.lower():
                fence += 1
                parsed.append({"_redacted": "passphrase"})
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError:
                fence += 1
                parsed.append({"_redacted": "unparsed"})
                continue
            if not isinstance(row, dict):
                fence += 1
                parsed.append({"_redacted": "unparsed"})
                continue
            if _flag_true(row.get("create_order")) or _flag_true(row.get("live")):
                fence += 1
            parsed.append(row)
    counts = {}
    for row in parsed:
        if row.get("_redacted"):
            continue
        key = (str(row.get("strategy_id") or ""), str(row.get("action") or ""))
        counts[key] = counts.get(key, 0) + 1
    lines = ["rows=%s fence=%s path=%s" % (len(parsed), fence, path)]
    for (sid, action), n in sorted(counts.items()):
        lines.append("%s %s %s" % (sid, action, n))
    window = parsed[-last:] if last else []
    for row in window:
        if row.get("_redacted"):
            lines.append("%s=present" % row["_redacted"])
            continue
        ts = row.get("ts") or row.get("alert_timestamp") or row.get("timestamp") or ""
        lines.append(
            "%s %s %s %s create_order=%s live=%s"
            % (
                ts,
                row.get("strategy_id") or "",
                row.get("ticker") or "",
                row.get("action") or "",
                _flag_word(row.get("create_order")),
                _flag_word(row.get("live")),
            )
        )
    return "\n".join(lines) + "\n", (1 if fence else 0)


def fixture_payload() -> dict:
    return {
        "passphrase": "fixture-not-a-real-secret",
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "strategy_id": "Waddah_1H_Anchor",
        "ticker": "BTCUSDT",
        "action": "entry",
        "subaccount": "paper",
        "entry_price": 65000.0,
        "volume": 0.01,
        "portfolio_risk": 0.5,
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="DaviddTech paper/shadow desk")
    p.add_argument("--validate", action="store_true", help="Validate fixture payload only")
    p.add_argument("--status", action="store_true", help="Print jsonl counts and last rows")
    p.add_argument("-n", type=int, default=10, help="Rows to print with --status")
    p.add_argument("--jsonl", default="", help="jsonl path for --status")
    p.add_argument("--out", default=DECISION_LOG)
    p.add_argument("--halt", action="store_true", help="Simulate VALEDIA_TRADING_HALT")
    args = p.parse_args(argv)
    if args.status:
        text, code = status_report(resolve_decision_log(args.jsonl or None), args.n)
        print(text, end="")
        return code
    body = fixture_payload()
    ok, reason = validate_payload(body)
    if args.validate:
        print(json.dumps({"ok": ok, "reason": reason, "normalized": {
            k: v for k, v in normalize_payload(body).items() if k != "passphrase"
        }}))
        return 0 if ok else 2
    if not ok:
        print(json.dumps({"ok": False, "reason": reason}))
        return 2
    row = to_decision(body, halt=args.halt)
    append_decision(row, path=args.out)
    print(json.dumps({"ok": True, "path": args.out, "decision": public_fields(row)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
