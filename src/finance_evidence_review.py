#!/usr/bin/env python3
"""Bounded, read-only advisory review of a self-described finance evidence packet.

Original implementation; no trading, model, database, network or external dependencies.
A packet is assertions, not authenticated receipts. Completeness is not accuracy.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path
import re
import sys

MAX_BYTES = 262144
MAX_SOURCES = 128
MAX_INTENTS = 128
TOP_FIELDS = frozenset({"schema_version", "venue", "market_id", "decision_at", "deadline_at", "sources", "timing", "rules", "model_observation", "intents", "fills", "costs", "settlement", "evaluation"})
SHA = re.compile(r"[0-9a-fA-F]{64}\Z")


def stamp(value):
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc) if dt.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def number(value, positive=False):
    return type(value) in (float, int) and math.isfinite(value) and (value > 0 if positive else value >= 0)


def text(value):
    return isinstance(value, str) and bool(value.strip())


def obj(value):
    return value if isinstance(value, dict) else {}


def review(packet):
    """Return structured refusals; never compute or certify an investment return.

    The rules decision is a separate caller-supplied advisory observation: this
    checker does not attest it gated historical paper intents or fills.
    """
    if not isinstance(packet, dict):
        return {"status": "INSUFFICIENT_EVIDENCE", "reasons": ["INVALID_PACKET"], "promotion_authorized": False}
    reasons = set()
    if packet.get("schema_version") != 1 or set(packet) - TOP_FIELDS:
        reasons.add("INVALID_PACKET")
    decision, deadline = stamp(packet.get("decision_at")), stamp(packet.get("deadline_at"))
    if not decision or not deadline or decision >= deadline:
        reasons.add("TIMING_INSUFFICIENT")
    venue, market = packet.get("venue"), packet.get("market_id")
    if venue != "polymarket_us" or not text(market):
        reasons.add("VENUE_MISMATCH")
    sources = packet.get("sources")
    if not isinstance(sources, list) or not 2 <= len(sources) <= MAX_SOURCES:
        reasons.add("SOURCES_INSUFFICIENT")
        sources = []
    ids, hashes, publishers = set(), set(), set()
    for src in sources:
        s = obj(src)
        identity, digest, publisher = s.get("id"), s.get("sha256"), s.get("publisher")
        if not text(identity) or not text(publisher) or not isinstance(digest, str) or not SHA.fullmatch(digest):
            reasons.add("SOURCES_INSUFFICIENT")
        elif identity in ids or digest.lower() in hashes:
            reasons.add("SOURCE_DUPLICATE")
        else:
            ids.add(identity); hashes.add(digest.lower()); publishers.add(publisher.strip().casefold())
        published, observed, captured = (stamp(s.get(key)) for key in ("published_at", "observed_at", "captured_at"))
        if not all((published, observed, captured)):
            reasons.add("SOURCES_INSUFFICIENT")
        elif not (published <= observed <= captured):
            reasons.add("SOURCE_CHRONOLOGY")
        if decision and any(t and t > decision for t in (published, observed, captured)):
            reasons.add("SOURCE_FUTURE")
        if decision and observed and decision - observed > timedelta(hours=24):
            reasons.add("SOURCE_STALE")
    if len(publishers) < 2:
        reasons.add("SOURCE_DIVERSITY")
    timing = obj(packet.get("timing"))
    groups = (("retrieval_started_at", "retrieval_finished_at"), ("model_started_at", "model_finished_at"), ("end_to_end_started_at", "end_to_end_finished_at"))
    intervals = [(stamp(timing.get(a)), stamp(timing.get(b))) for a, b in groups]
    if any(not a or not b or a > b for a, b in intervals):
        reasons.add("TIMING_INSUFFICIENT")
    else:
        (rs, rf), (ms, mf), (es, ef) = intervals
        if not (es <= rs <= rf <= ms <= mf <= ef <= decision):
            reasons.add("TIMING_CHRONOLOGY")
        for src in sources:
            captured = stamp(obj(src).get("captured_at"))
            if captured is not None and rf is not None and captured > rf:
                reasons.add("SOURCE_CHRONOLOGY")
        if deadline and (mf > deadline or ef > deadline):
            reasons.add("MISSED_DEADLINE")
    rules = obj(packet.get("rules"))
    if rules.get("decision") not in ("abstain", "allow", "deny") or not text(rules.get("receipt")):
        reasons.add("RULES_INSUFFICIENT")
    intents = packet.get("intents")
    fills = packet.get("fills")
    if not isinstance(intents, list) or not 1 <= len(intents) <= MAX_INTENTS:
        reasons.add("INTENTS_INSUFFICIENT"); intents = []
    if not isinstance(fills, list) or len(fills) > MAX_INTENTS * 4:
        reasons.add("FILLS_UNRECONCILED"); fills = []
    intent_map = {}
    for row in intents:
        i = obj(row); key = i.get("id")
        if not text(key) or key in intent_map or i.get("status") != "paper" or i.get("venue") != venue or i.get("market_id") != market or i.get("side") not in ("yes", "no") or not number(i.get("quantity"), True) or not number(i.get("price")) or i.get("price") > 1:
            reasons.add("INTENTS_INSUFFICIENT")
        elif key not in intent_map:
            intent_map[key] = i
    totals = {key: 0 for key in intent_map}
    receipts = set()
    if not fills and intents:
        reasons.add("FILLS_UNRECONCILED")
    for row in fills:
        f = obj(row); key = f.get("intent_id"); receipt = f.get("receipt"); ts = stamp(f.get("filled_at"))
        # Fill identity is optional only as a pair; any supplied identity must agree.
        if ("venue" in f) != ("market_id" in f) or ("venue" in f and (f["venue"] != venue or f["market_id"] != market)):
            reasons.add("VENUE_MISMATCH")
        if not text(key) or key not in intent_map or not text(receipt) or receipt in receipts or not number(f.get("quantity"), True) or not number(f.get("price")) or f.get("price") > 1 or not ts or (decision and ts < decision) or (deadline and ts > deadline):
            reasons.add("FILLS_UNRECONCILED")
        else:
            receipts.add(receipt); totals[key] += f["quantity"]
    for key, i in intent_map.items():
        if not math.isclose(totals[key], i["quantity"], rel_tol=0, abs_tol=1e-9):
            reasons.add("FILLS_UNRECONCILED")
    costs = obj(packet.get("costs"))
    if not all(number(costs.get(k)) for k in ("fee", "slippage", "inference")) or not text(costs.get("receipt")):
        reasons.add("COSTS_INSUFFICIENT")
    settlement = obj(packet.get("settlement"))
    settled = stamp(settlement.get("settled_at"))
    if settlement.get("venue") != venue or settlement.get("market_id") != market:
        reasons.add("VENUE_MISMATCH")
    if settlement.get("outcome") not in ("yes", "no") or not settled or not text(settlement.get("receipt")) or (decision and settled <= decision):
        reasons.add("SETTLEMENT_INSUFFICIENT")
    if settled is not None and any((filled_at := stamp(obj(fill).get("filled_at"))) is not None and settled < filled_at for fill in fills):
        reasons.add("SETTLEMENT_INSUFFICIENT")
    evaluation = obj(packet.get("evaluation"))
    registered = stamp(evaluation.get("registered_at"))
    holdout = evaluation.get("holdout_ids")
    valid_holdout = False
    if isinstance(holdout, list) and holdout and all(text(item) for item in holdout):
        valid_holdout = market in holdout and len(holdout) == len(set(holdout))
    if not registered or not decision or registered >= decision or not valid_holdout:
        reasons.add("HOLDOUT_INSUFFICIENT")
    baseline = obj(evaluation.get("baseline"))
    if not text(baseline.get("name")) or not text(baseline.get("receipt")) or baseline.get("same_holdout") is not True or not text(obj(evaluation.get("candidate")).get("receipt")):
        reasons.add("BASELINE_INSUFFICIENT")
    return {"status": "INSUFFICIENT_EVIDENCE" if reasons else "STRUCTURALLY_COMPLETE_UNVERIFIED", "reasons": sorted(reasons), "rules_decision": rules.get("decision"), "rules_fill_gate_certified": False, "promotion_authorized": False, "receipt_authenticity_verified": False, "comparison_scored": False, "timing_ms": ({name: int((end - start).total_seconds() * 1000) for name, (start, end) in zip(("retrieval", "model", "end_to_end"), intervals)} if "TIMING_INSUFFICIENT" not in reasons else None)}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline advisory completeness check; no model or network")
    parser.add_argument("packet", type=Path, help="local JSON packet (max 256 KiB)")
    args = parser.parse_args(argv)
    try:
        if args.packet.is_symlink() or not args.packet.is_file() or args.packet.stat().st_size > MAX_BYTES:
            raise ValueError("invalid, symlinked or oversized packet")
        raw = args.packet.read_bytes()
        if len(raw) > MAX_BYTES:
            raise ValueError("oversized packet")
        result = review(json.loads(raw))
    except (OSError, UnicodeError, ValueError, TypeError) as error:
        result = {"status": "INSUFFICIENT_EVIDENCE", "reasons": ["INVALID_PACKET"], "promotion_authorized": False, "error": type(error).__name__}
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0 if result["status"] == "STRUCTURALLY_COMPLETE_UNVERIFIED" else 2


if __name__ == "__main__":
    sys.exit(main())
