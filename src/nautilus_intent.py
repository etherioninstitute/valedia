"""nautilus_intent.py — Valedia → Nautilus sidecar Intent seam (Phase 0).

An Intent is Valedia's already-gated decision, not a Nautilus SubmitOrder.
This module validates, serializes, and append-only enqueues. It does not
talk to venues, load keys, or import nautilus_trader.

Dispatch is refused unless NAUTILUS_ENABLED and VENUE_WRITER[venue]==nautilus.
Missing/false gates fail closed. Size is never rounded up.
(c) GOATWORTHY Post LLC.
"""
from __future__ import annotations

import json
import math
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Optional

import valedia_config as vc

MODELS = os.path.expanduser("~/.openclaw/workspace/models")
DEFAULT_QUEUE = os.path.join(MODELS, "nautilus_intent_queue.jsonl")

ORDER_TYPES = frozenset({"market", "limit", "maker_improve"})
SIDES = frozenset({"buy", "sell"})
TIFS = frozenset({"GTC", "IOC", "FOK", "GTD", "DAY"})
HALT_ERE = frozenset({"HALT", "DANGER"})
GATE_BOOLS = ("fee_ok", "category_ok", "blacklist_ok")
GATE_PRESENT = GATE_BOOLS + ("exec_dry_run", "live_flag")


@dataclass
class IntentDecision:
    ok: bool            # schema+gates valid enough to enqueue (held/dry_run/dispatchable)
    dispatchable: bool  # legal to hand to the sidecar NOW
    status: str         # dispatchable | held | dry_run | refused
    reason: str


@dataclass
class Intent:
    strategy: str
    venue: str
    instrument: str
    side: str
    order_type: str
    tif: str
    max_notional: float
    max_qty: float
    gates: dict = field(default_factory=dict)
    ledger_source: str = "paper"
    intent_id: str = ""
    ts: str = ""
    qty: Optional[float] = None
    notional: Optional[float] = None
    limit_price: Optional[float] = None
    client_order_id: str = ""

    def __post_init__(self):
        if not self.intent_id:
            self.intent_id = str(uuid.uuid4())
        if not self.ts:
            self.ts = datetime.now(timezone.utc).isoformat()
        if not self.client_order_id:
            self.client_order_id = self.intent_id
        self.venue = str(self.venue or "").strip().lower()
        self.side = str(self.side or "").strip().lower()
        self.order_type = str(self.order_type or "").strip().lower()
        self.tif = str(self.tif or "").strip().upper()
        self.ledger_source = str(self.ledger_source or "paper").strip().lower()
        if not isinstance(self.gates, dict):
            self.gates = {}


def make_intent(**kwargs) -> Intent:
    return Intent(**kwargs)


def intent_from_dict(d: dict) -> Intent:
    known = {k: d[k] for k in Intent.__dataclass_fields__ if k in d}
    return Intent(**known)


def intent_to_dict(intent: Intent) -> dict:
    return asdict(intent)


def round_size_down(size, increment):
    """Lot/slippage rounding — never up. increment<=0 returns size unchanged."""
    size = float(size)
    inc = float(increment)
    if inc <= 0:
        return size
    return math.floor(size / inc) * inc


def ere_ok(gates: dict) -> bool:
    g = gates or {}
    if "ere_ok" in g and g["ere_ok"] is False:
        return False
    level = str(g.get("ere_level", "")).strip().upper()
    if level in HALT_ERE:
        return False
    if "ere_ok" in g:
        return bool(g["ere_ok"])
    if level:
        return True
    return False


def _num(v) -> Optional[float]:
    if v is None or v == "":
        return None
    return float(v)


def _schema_reason(intent: Intent) -> Optional[str]:
    if not intent.strategy or not intent.venue or not intent.instrument:
        return "strategy, venue, instrument required"
    if intent.side not in SIDES:
        return f"side must be buy|sell, got {intent.side!r}"
    if intent.order_type not in ORDER_TYPES:
        return f"order_type must be market|limit|maker_improve, got {intent.order_type!r}"
    if intent.tif not in TIFS:
        return f"tif must be one of {sorted(TIFS)}"
    try:
        max_n = float(intent.max_notional)
        max_q = float(intent.max_qty)
    except (TypeError, ValueError):
        return "max_notional and max_qty required"
    if max_n <= 0 or max_q <= 0:
        return "max_notional and max_qty must be > 0"
    qty = _num(intent.qty)
    notional = _num(intent.notional)
    if (qty is None) == (notional is None):
        return "exactly one of qty or notional required"
    if qty is not None:
        if qty <= 0:
            return "qty must be > 0"
        if qty > max_q:
            return "qty exceeds max_qty (no silent clip-up)"
    if notional is not None:
        if notional <= 0:
            return "notional must be > 0"
        if notional > max_n:
            return "notional exceeds max_notional (no silent clip-up)"
    if intent.order_type in ("limit", "maker_improve"):
        lp = _num(intent.limit_price)
        if lp is None or lp <= 0:
            return "limit_price required for limit/maker_improve"
    return None


def _gate_reason(intent: Intent) -> Optional[str]:
    g = intent.gates or {}
    missing = [k for k in GATE_PRESENT if k not in g]
    if missing:
        return f"gates missing {missing} (fail-closed)"
    if not ere_ok(g):
        return "ere_ok false or ere_level in {HALT,DANGER}"
    for k in GATE_BOOLS:
        if g.get(k) is not True:
            return f"gate {k} is not True (fail-closed)"
    if g.get("live_flag") is None:
        return "live_flag missing (fail-closed)"
    if g.get("exec_dry_run") is None:
        return "exec_dry_run missing (fail-closed)"
    return None


def validate_intent(intent: Intent, dispatch: bool = False) -> IntentDecision:
    """Fail-closed. dispatch=True is a sidecar-send request; still never
    dispatchable unless NAUTILUS_ENABLED and this venue's writer is nautilus."""
    if not isinstance(intent, Intent):
        intent = intent_from_dict(intent)
    schema = _schema_reason(intent)
    if schema:
        return IntentDecision(False, False, "refused", schema)
    gates = _gate_reason(intent)
    if gates:
        return IntentDecision(False, False, "refused", gates)

    if intent.gates.get("exec_dry_run") is True:
        return IntentDecision(True, False, "dry_run", "exec_dry_run — never dispatchable")

    if intent.ledger_source == "live" and not vc.NAUTILUS_ENABLED:
        return IntentDecision(
            True, False, "held", "source=live while NAUTILUS_ENABLED=False"
        )

    writer = vc.venue_writer(intent.venue)
    if writer != "nautilus":
        return IntentDecision(
            True, False, "held", f"venue writer is {writer!r}, not 'nautilus'"
        )

    if not vc.NAUTILUS_ENABLED:
        return IntentDecision(True, False, "held", "NAUTILUS_ENABLED=False")

    if intent.ledger_source == "live" and intent.gates.get("live_flag") is not True:
        return IntentDecision(True, False, "held", "live_flag is not True")

    if dispatch and intent.ledger_source == "live" and not vc.NAUTILUS_LIVE:
        return IntentDecision(
            True, False, "held", "NAUTILUS_LIVE=False — live dispatch denied"
        )

    return IntentDecision(True, True, "dispatchable", "ok")


def enqueue_intent(intent: Intent, dispatch: bool = False, queue_path: Optional[str] = None,
                   extra: Optional[dict] = None) -> dict:
    """Append-only JSONL. Ungated/disabled Intents may be logged as held/refused
    but dispatchable is always False unless validate_intent says otherwise.
    extra may add fields (shadow, compare) but cannot set dispatchable/status."""
    if not isinstance(intent, Intent):
        intent = intent_from_dict(intent)
    extra = dict(extra or {})
    if extra.get("shadow"):
        dispatch = False  # shadow Intents are never a sidecar-send request
    decision = validate_intent(intent, dispatch=dispatch)
    rec = intent_to_dict(intent)
    rec["status"] = decision.status
    rec["validate_reason"] = decision.reason
    rec["enqueued_at"] = datetime.now(timezone.utc).isoformat()
    locked = {"dispatchable", "status", "dispatch_requested", "validate_reason"}
    for k, v in extra.items():
        if k not in locked:
            rec[k] = v
    rec["dispatch_requested"] = bool(dispatch)
    rec["dispatchable"] = bool(decision.dispatchable and dispatch)
    if extra.get("shadow") and rec["status"] == "dispatchable":
        rec["status"] = "held"
        rec["validate_reason"] = "shadow — never dispatchable"
    path = queue_path or DEFAULT_QUEUE
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(rec) + "\n")
    return rec
