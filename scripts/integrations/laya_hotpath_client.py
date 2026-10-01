#!/usr/bin/env python3
"""Laya shadow client. RETIRED from the movers scan on 2026-09-30.

The scan no longer imports this module. Nimble System One replaced it.
Left in place so the old unit test still documents the :8766 contract.
VALEDIA_LAYA=1 does not enable the movers scan. The scan ignores that env.
Code still owns the fee-true label and would-have size. This module
never places an order. A down service returns a stub and the scan continues.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request

HOST = "127.0.0.1"
PORT = 8766
PACK_ID = "valedia-paper-v1"
DECIDE_URL = "http://127.0.0.1:8766/v1/decide"
HEALTH_URL = "http://127.0.0.1:8766/health"
TIMEOUT_S = 20.0  # ponytail: ANE predict is seconds; raise only if receipts show client timeouts

_INFORMED_PATHS = (
    os.path.abspath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "models", "mirofish_signal.json")),
    os.path.expanduser("~/.openclaw/workspace/models/mirofish_signal.json"),
)


def enabled():
    return os.environ.get("VALEDIA_LAYA") == "1"


def load_informed():
    """Local MiroFish file if it is already on disk. No model call."""
    for path in _INFORMED_PATHS:
        try:
            with open(path) as f:
                doc = json.load(f)
        except Exception:
            continue
        if not isinstance(doc, dict):
            continue
        sigs = doc.get("signals")
        return {
            "present": True,
            "signals": sigs if isinstance(sigs, dict) else {},
        }
    return {"present": False, "signals": {}}


def _venue(row):
    if row.get("oil_fenced") or row.get("class") == "oil":
        return "oil_blocked"
    cls = str(row.get("class") or "")
    if cls == "weather":
        return "weather_watch"
    if cls == "crypto":
        return "crypto"
    if cls in ("equity", "etf"):
        return "equity"
    if cls in ("prediction", "kalshi", "polymarket"):
        return "prediction"
    return cls or "unknown"


def _flags(row, informed):
    sigs = (informed or {}).get("signals") or {}
    sym = str(row.get("symbol") or "")
    news = float(row.get("buzz") or 0) > 0
    if not news:
        for reason in row.get("reasons") or []:
            text = str(reason).lower()
            if "finnhub" in text or "news" in text:
                news = True
                break
    label = row.get("laya")
    watch = label == "watch" or _venue(row) in ("weather_watch", "oil_blocked")
    return {
        "mirofish": sym in sigs,
        "news": bool(news),
        "watch": bool(watch),
    }


def compact_state(row, informed=None):
    """Small non-secret state. No keys, no headlines, no order."""
    return {
        "symbol": row.get("symbol"),
        "class": _venue(row),
        "chg_pct": row.get("chg_24h"),
        "fee_true_edge": row.get("edge"),
        "impact_usd": row.get("impact_usd"),
        "informed": _flags(row, informed),
        "create_order": False,
    }


def _stub(reason):
    return {
        "backend": "stub",
        "fail_open": True,
        "error": str(reason)[:80],
        "mode": "shadow",
        "enforcement": "none",
        "create_order": False,
        "answers": {},
        "latency_ms": None,
        "client_ms": None,
    }


def _answers(body):
    ans = (body or {}).get("answers") or {}
    admit = ans.get("admit_paper") or {}
    edge = ans.get("edge_quality") or {}
    venue = ans.get("venue_class") or {}
    return {
        "admit_paper": admit.get("noul"),
        "edge_quality": edge.get("score"),
        "venue_class": venue.get("choice"),
    }


def _post(url, payload, timeout):
    if url != DECIDE_URL and url != HEALTH_URL:
        raise ValueError("refusing non-loopback laya url")
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def _get(url, timeout):
    if url != HEALTH_URL:
        raise ValueError("refusing non-loopback laya url")
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def decide(state, post=None, timeout=None):
    """POST one shadow decide. Timeout or a bad body → stub."""
    fn = post or _post
    t0 = time.perf_counter()
    try:
        body = fn(DECIDE_URL, {"state": state, "pack_id": PACK_ID}, timeout or TIMEOUT_S)
    except Exception as e:
        out = _stub(type(e).__name__)
        out["client_ms"] = round((time.perf_counter() - t0) * 1000.0, 2)
        return out
    client_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    if not isinstance(body, dict):
        out = _stub("bad_response")
        out["client_ms"] = client_ms
        return out
    if body.get("mode") != "shadow" or body.get("enforcement") != "none":
        out = _stub("fence")
        out["client_ms"] = client_ms
        return out
    if "answers" not in body:
        out = _stub("no_answers")
        out["client_ms"] = client_ms
        return out
    return {
        "backend": "coreml_http",
        "fail_open": False,
        "error": None,
        "mode": "shadow",
        "enforcement": "none",
        "create_order": False,
        "pack_id": PACK_ID,
        "answers": _answers(body),
        "latency_ms": body.get("latency_ms"),
        "client_ms": client_ms,
    }


def health(get=None, timeout=3.0):
    fn = get or _get
    try:
        body = fn(HEALTH_URL, timeout)
    except Exception as e:
        return {"ok": False, "error": type(e).__name__, "mode": None, "enforcement": None}
    if not isinstance(body, dict):
        return {"ok": False, "error": "bad_response"}
    return body


def summary(ranked, informed=None):
    """Scan-level backend stamp. coreml_http only if a decide actually returned."""
    if not enabled():
        return {
            "enabled": False,
            "backend": "stub",
            "fail_open": False,
            "mode": "shadow",
            "enforcement": "none",
            "create_order": False,
            "pack_id": PACK_ID,
            "informed_mirofish": False,
        }
    rows = [r.get("laya_shadow") or {} for r in ranked or []]
    ok = [r for r in rows if r.get("backend") == "coreml_http" and not r.get("fail_open")]
    lats = [r.get("latency_ms") for r in ok if isinstance(r.get("latency_ms"), (int, float))]
    return {
        "enabled": True,
        "backend": "coreml_http" if ok else "stub",
        "fail_open": not bool(ok),
        "mode": "shadow",
        "enforcement": "none",
        "create_order": False,
        "pack_id": PACK_ID,
        "n_ok": len(ok),
        "n": len(rows),
        "latency_ms_max": max(lats) if lats else None,
        "informed_mirofish": bool((informed or {}).get("present")),
    }
