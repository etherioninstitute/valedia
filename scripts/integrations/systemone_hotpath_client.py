#!/usr/bin/env python3
"""Nimble 9B System One shadow client. Loopback Ollama only. Fail-open.

VALEDIA_SYSTEMONE=1 turns the movers scan onto POST /v1/systemone.
Default off. Model pin is nimble (nimble:9b / nimble:latest). Tev1,
including Tev1-0.8B, is refused. Code still owns the fee-true label
and the would-have size. This module never places an order.
A down server returns a stub and the scan continues.
"""
from __future__ import annotations

import json
import math
import os
import time
import urllib.request

HOST = "127.0.0.1"
PORT = 11434
MODEL_DEFAULT = "nimble"
ALLOWED_MODELS = frozenset({"nimble", "nimble:9b", "nimble:latest"})
DECIDE_URL = "http://127.0.0.1:11434/v1/systemone"
VERSION_URL = "http://127.0.0.1:11434/api/version"
TAGS_URL = "http://127.0.0.1:11434/api/tags"
ALLOWED_URLS = frozenset({DECIDE_URL, VERSION_URL, TAGS_URL})
TIMEOUT_S = 120.0  # ponytail: cold 9B load; warm calls are far under this
KEEP_ALIVE = "10m"

# Same three judgments as packs/valedia-paper-v1. Stamp only.
QUESTIONS = {
    "admit_paper": {
        "type": "noul",
        "instructions": (
            "Paper shadow only. Given the symbol, venue class, fee-true edge, "
            "impact, and informed flags, is this name admissible as a paper note? "
            "Oil and weather stay blocked. Do not place an order."
        ),
        "criteria": {
            "false": "Do not admit. No print, negative fee-true edge, oil, weather, or too thin.",
            "true": "Admissible as a paper shadow note. Still not a live order.",
        },
    },
    "edge_quality": {
        "type": "score",
        "instructions": (
            "How strong is the fee-true edge as a paper shadow note after "
            "roundtrip cost? This score does not size and does not send."
        ),
        "criteria": [
            "none — no print, or fee-true edge is negative",
            "thin — positive edge but under a 16 percent day",
            "printable — day move clears the paper size bar and edge stays positive",
            "stretched — huge print or thin tape; watch, do not treat as size",
        ],
    },
    "venue_class": {
        "type": "choice",
        "instructions": (
            "Which venue class is this symbol? Shadow label only. "
            "weather_watch and oil_blocked are not live admits."
        ),
        "criteria": {
            "crypto": "Spot crypto tape.",
            "equity": "Stock or ETF.",
            "prediction": "Prediction market that is not weather and not oil.",
            "weather_watch": "Weather contract. Watch only.",
            "oil_blocked": "Oil or oil-linked. No harvest.",
        },
    },
}

_INFORMED_PATHS = (
    os.path.abspath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "models", "mirofish_signal.json")),
    os.path.expanduser("~/.openclaw/workspace/models/mirofish_signal.json"),
)


def enabled():
    return os.environ.get("VALEDIA_SYSTEMONE") == "1"


def model_pin():
    """Nimble only. Empty string means the caller must not POST."""
    raw = os.environ.get("VALEDIA_SYSTEMONE_MODEL", MODEL_DEFAULT).strip().lower()
    if not raw or "0.8" in raw or raw.startswith("tev"):
        return ""
    if raw not in ALLOWED_MODELS:
        return ""
    return raw


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


def _finite(value):
    """Ollama rejects NaN/Infinity. Missing print is null, not a token."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return value
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def compact_state(row, informed=None):
    """Small non-secret state. No keys, no headlines, no order."""
    return {
        "symbol": row.get("symbol"),
        "class": _venue(row),
        "chg_pct": _finite(row.get("chg_24h")),
        "fee_true_edge": _finite(row.get("edge")),
        "impact_usd": _finite(row.get("impact_usd")),
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
        "model": model_pin() or None,
        "judge": "dont_trade",
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


def _version_ok(version):
    parts = []
    for piece in str(version or "").split("."):
        num = ""
        for ch in piece:
            if ch.isdigit():
                num += ch
            else:
                break
        if not num:
            break
        parts.append(int(num))
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3]) >= (0, 35, 0)


def _check_url(url):
    if url not in ALLOWED_URLS:
        raise ValueError("refusing non-loopback systemone url")


def _post(url, payload, timeout):
    _check_url(url)
    data = json.dumps(payload, allow_nan=False).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def _get(url, timeout):
    _check_url(url)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def _response_model_ok(body, pin):
    got = str((body or {}).get("model") or pin).strip().lower()
    if "0.8" in got or got.startswith("tev"):
        return False
    if got in ALLOWED_MODELS or got.startswith("nimble"):
        return True
    return False


def decide(state, post=None, timeout=None):
    """POST one shadow decide. Timeout, bad model, or a bad body → stub."""
    pin = model_pin()
    t0 = time.perf_counter()
    if not pin:
        out = _stub("model_refused")
        out["client_ms"] = round((time.perf_counter() - t0) * 1000.0, 2)
        return out
    fn = post or _post
    try:
        body = fn(
            DECIDE_URL,
            {
                "model": pin,
                "state": state,
                "questions": QUESTIONS,
                "keep_alive": KEEP_ALIVE,
            },
            timeout or TIMEOUT_S,
        )
    except Exception as e:
        out = _stub(type(e).__name__)
        out["client_ms"] = round((time.perf_counter() - t0) * 1000.0, 2)
        return out
    client_ms = round((time.perf_counter() - t0) * 1000.0, 2)
    if not isinstance(body, dict):
        out = _stub("bad_response")
        out["client_ms"] = client_ms
        return out
    if not _response_model_ok(body, pin):
        out = _stub("model_refused")
        out["client_ms"] = client_ms
        return out
    if "answers" not in body:
        out = _stub("no_answers")
        out["client_ms"] = client_ms
        return out
    usage = body.get("usage")
    return {
        "backend": "systemone_http",
        "fail_open": False,
        "error": None,
        "mode": "shadow",
        "enforcement": "none",
        "create_order": False,
        "model": pin,
        "judge": "dont_trade",
        "answers": _answers(body),
        "latency_ms": client_ms,
        "client_ms": client_ms,
        "usage": usage if isinstance(usage, dict) else None,
    }


def health(get=None, timeout=3.0):
    """Version + tags only. Does not send a decide."""
    fn = get or _get
    try:
        ver_body = fn(VERSION_URL, timeout)
        tags_body = fn(TAGS_URL, timeout)
    except Exception as e:
        return {
            "ok": False,
            "error": type(e).__name__,
            "version": None,
            "model_present": False,
            "model": MODEL_DEFAULT,
            "mode": "shadow",
            "enforcement": "none",
        }
    version = str((ver_body or {}).get("version") or "") if isinstance(ver_body, dict) else ""
    names = []
    if isinstance(tags_body, dict):
        for row in tags_body.get("models") or []:
            if isinstance(row, dict) and row.get("name"):
                names.append(str(row["name"]))
    present = any(n == "nimble" or n.startswith("nimble:") for n in names)
    ready = _version_ok(version) and present
    return {
        "ok": ready,
        "error": None if ready else ("version" if not _version_ok(version) else "model_missing"),
        "version": version,
        "model_present": present,
        "model": MODEL_DEFAULT,
        "mode": "shadow",
        "enforcement": "none",
    }


def summary(ranked, informed=None):
    """Scan-level stamp. systemone_http only if a decide actually returned."""
    if not enabled():
        return {
            "enabled": False,
            "backend": "stub",
            "fail_open": False,
            "mode": "shadow",
            "enforcement": "none",
            "create_order": False,
            "model": MODEL_DEFAULT,
            "judge": "dont_trade",
            "informed_mirofish": False,
        }
    rows = [r.get("systemone_shadow") or {} for r in ranked or []]
    ok = [r for r in rows if r.get("backend") == "systemone_http" and not r.get("fail_open")]
    lats = [r.get("latency_ms") for r in ok if isinstance(r.get("latency_ms"), (int, float))]
    return {
        "enabled": True,
        "backend": "systemone_http" if ok else "stub",
        "fail_open": not bool(ok),
        "mode": "shadow",
        "enforcement": "none",
        "create_order": False,
        "model": model_pin() or None,
        "judge": "dont_trade",
        "n_ok": len(ok),
        "n": len(rows),
        "latency_ms_min": min(lats) if lats else None,
        "latency_ms_max": max(lats) if lats else None,
        "informed_mirofish": bool((informed or {}).get("present")),
    }
