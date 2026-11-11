#!/usr/bin/env python3
"""POLY-US-2 preview dry-run. Default LIVE off for standalone.

Loads ~/.openclaw/.env. Forces POLYMARKET_US_HTTP=1. Default force_live_off
keeps create_order LiveWriteBlocked. Full smoke may pass force_live_off=False
when POLYMARKET_US_LIVE is already on: preview only, never create.
Run: python3 poly_us_smoke.py
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import polymarket_us_client as pmu  # noqa: E402


def _first_slug(doc):
    if not isinstance(doc, dict):
        return None
    rows = doc.get("markets") or doc.get("data") or []
    if isinstance(rows, dict):
        rows = rows.get("markets") or []
    for row in rows:
        if isinstance(row, dict):
            slug = row.get("slug") or row.get("marketSlug")
            if slug:
                return str(slug)
    return None


def run_smoke(client=None, force_live_off=True):
    pmu.load_openclaw_env()
    os.environ["POLYMARKET_US_HTTP"] = "1"
    if force_live_off:
        os.environ["POLYMARKET_US_LIVE"] = "0"
    live = False if force_live_off else bool(pmu.live_enabled())
    c = client or pmu.PolymarketUSClient(live=live, http=True, transport=None)
    if force_live_off and c.live:
        raise RuntimeError("fence: POLYMARKET_US_LIVE must stay False")
    if not c.has_keys():
        raise RuntimeError("missing POLYMARKET_KEY_ID / POLYMARKET_SECRET_KEY")
    ident = c.whoami()
    bals = c.balances()
    if not ident.get("ok") or ident.get("source") != "balances":
        raise RuntimeError("whoami/balances failed: " + json.dumps({
            "ok": ident.get("ok"), "source": ident.get("source"),
            "status": ident.get("status")}))
    if not pmu._http_ok(bals):
        raise RuntimeError("balances not ok")
    mkts = c.list_markets(limit=5, active=True)
    slug = _first_slug(mkts)
    if not slug:
        raise RuntimeError("no active market slug for preview")
    prev = c.preview_order({
        "marketSlug": slug,
        "intent": "ORDER_INTENT_BUY_LONG",
        "type": "ORDER_TYPE_LIMIT",
        "price": {"value": "0.01", "currency": "USD"},
        "quantity": 1,
    })
    if prev.get("kind") != "preview" or prev.get("filled") is not False:
        raise RuntimeError("preview must be dry-run, not a fill")
    if isinstance(prev.get("status"), int) and prev["status"] >= 400:
        raise RuntimeError("preview HTTP " + str(prev["status"]))
    create_blocked = False
    create_skipped = False
    if c.live:
        # LIVE on: create may succeed. Default smoke never places.
        create_skipped = True
    else:
        try:
            c.create_order({"marketSlug": slug, "quantity": 1})
            raise RuntimeError("create_order must raise LiveWriteBlocked")
        except pmu.LiveWriteBlocked:
            create_blocked = True
    return {
        "ok": True,
        "whoami": {k: ident[k] for k in ident if k != "note"},
        "preview": {"kind": prev.get("kind"), "filled": prev.get("filled"),
                    "marketSlug": slug, "quantity": 1, "price": "0.01"},
        "create_blocked": create_blocked,
        "create_skipped": create_skipped,
        "live": bool(c.live),
    }


if __name__ == "__main__":
    out = run_smoke()
    print(json.dumps(out, indent=2, default=str))
    print("POLY-US-2 smoke: PASS (preview only, create blocked)")
