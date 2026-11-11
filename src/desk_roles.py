"""H5: Galleon Labs hypergrok desk as labels on shadow Intents.

Seven live bots collapse to stages: research → risk → exec → review.
Not a second Kelly, not a writer, not a chat send path.
Inspired by galleonlabs/hypergrok-trading-desk (MIT). Attribute Galleon Labs.
(c) GOATWORTHY Post LLC.
"""
from __future__ import annotations

import re

STAGES = ("research", "risk", "exec", "review")
# Desk Lead / Analyst / RM / ET / Reviewer → these four. No bots.
_APPROVE = re.compile(r"\bapprove\s+HG-([A-Za-z0-9-]+)\b", re.I)


def stage_from_gates(gates, venue=None):
    """research until gates exist; risk after ERE/fee; exec only if a writer exists.

    Writer-none venues (hyperliquid today) stay at risk. Shadow never dispatches.
    """
    g = gates or {}
    if "fee_ok" not in g and "ere_level" not in g and "ere_ok" not in g:
        return "research"
    ere_halt = str(g.get("ere_level") or "").strip().upper() in ("HALT", "DANGER")
    ere_ok = (g.get("ere_ok") is not False) and not ere_halt
    if not ere_ok or g.get("fee_ok") is not True:
        return "risk"
    writer = "none"
    if venue:
        import valedia_config as vc
        writer = vc.venue_writer(venue)
    if writer in ("valedia", "nautilus"):
        return "exec"
    return "risk"


def label_shadow(gates, venue=None, stage=None):
    s = stage or stage_from_gates(gates, venue)
    if s not in STAGES:
        s = "research"
    return {"desk_stage": s, "desk": "hypergrok-labels"}


def ticket_from_record(rec, extra=None):
    """Thin human twin of an Intent. stop is None — KellySizer owns size."""
    d = rec or {}
    x = extra or {}
    iid = str(d.get("intent_id") or "")
    short = iid.replace("-", "")[:8] if iid else ""
    src = x.get("source_feed") or d.get("source_feed")
    return {
        "id": iid,
        "hg_id": f"HG-{short}" if short else "",
        "instrument": d.get("instrument"),
        "side": d.get("side"),
        "size": d.get("qty") if d.get("qty") is not None else d.get("notional"),
        "stop": None,
        "sources": [src] if src else [],
        "utc": d.get("ts") or d.get("enqueued_at"),
        "desk_stage": x.get("desk_stage") or d.get("desk_stage"),
        "approve_phrase": f"approve HG-{short}" if short else "",
        "send": False,
    }


def parse_approve(text):
    """Grammar only. 'yes' is not approval. Does not send."""
    m = _APPROVE.search(str(text or ""))
    return m.group(1) if m else None
