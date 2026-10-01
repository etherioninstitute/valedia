#!/usr/bin/env python3
"""Nimble 9B System One shadow client. Loopback Ollama only. Fail-open.

VALEDIA_SYSTEMONE=1 turns the movers scan onto POST /v1/systemone.
Default off. Model pin is Nimble 2 (Bespoke-Nimble-9B-v2, 8192 ctx).
Nimble v1 tags and Tev1, including Tev1-0.8B, are refused and never
posted. Burton's Nimble 2 is the Ollama tag `nimble:latest` (digest
24e550a16a70, confirmed 2026-10-01). A floating `latest` tag can change on
the next pull, so `nimble:latest` and bare `nimble` are accepted ONLY while
/api/tags shows a digest starting with NIMBLE2_DIGEST (digest pin). Code still owns the fee-true label and the would-have size.
This module never places an order. A down server returns a stub and
the scan continues.

Gates live in code, not in the model call. Nimble 2 is weak at arithmetic
and counting, so every threshold and comparison is computed in code
(facts()) and the questions only refer to those facts, with no numbers.
Every call is logged, yes and no alike, to an append-only decisions.jsonl.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
import urllib.request

from systemone_log import budget_ms, finite_tree, log_decision, log_path  # noqa: F401

HOST = "127.0.0.1"
PORT = 11434
# Nimble 2 = Hugging Face bespokelabs/Bespoke-Nimble-9B-v2 (release
# Bespoke-Nimble-9B-v2, 8192 ctx). On Burton's Mac it is the Ollama tag
# `nimble:latest`, digest 24e550a16a70 (`ollama list`: 9.5 GB; Burton
# confirmed 2026-10-01). Override the tag with VALEDIA_SYSTEMONE_MODEL.
MODEL_DEFAULT = "nimble:latest"
# Digest pin for the floating tag. `ollama list` shows the first 12 hex chars
# of the sha256 that /api/tags returns in models[].digest. Env override for a
# deliberate re-pin: VALEDIA_SYSTEMONE_DIGEST (hex prefix, at least 12 chars).
NIMBLE2_DIGEST = "24e550a16a70"
DIGEST_ENV = "VALEDIA_SYSTEMONE_DIGEST"
DIGEST_RETRY_S = 30.0  # a failed digest check is not retried inside this window
# Explicit Nimble 2 names only. Full-match, case-insensitive. The floating
# tags (nimble, nimble:latest) are NOT here: they pass only via the digest pin.
# v1 tags (nimble:9b, nimble:1, ...) match nothing and stay refused.
_TAG = r"[a-z0-9][a-z0-9._-]*"
_QUANT = r"(?:q\d[a-z0-9_]*|iq\d[a-z0-9_]*|fp16|fp32|bf16|f16|f32|int4|int8)"
_V2_RE = re.compile(
    r"nimble2(?::" + _TAG + r")?"                        # nimble2, nimble2:<tag>
    r"|nimble:(?:2|v2|9b-v2)(?:-" + _QUANT + r")?"       # nimble:2, :v2, :9b-v2 [+ quant]
    # Release name, bare or under the vendor namespace only. Other namespaces
    # and hosts have no provenance here, so they are refused.
    r"|(?:(?:hf\.co/)?bespokelabs/)?bespoke-nimble-9b-v2(?::" + _TAG + r")?"
)
_FLOATING = ("nimble", "nimble:latest")  # Ollama resolves bare `nimble` to :latest
_V2_BANNED = ("v1", "tev", "0.8")
DECIDE_URL = "http://127.0.0.1:11434/v1/systemone"
VERSION_URL = "http://127.0.0.1:11434/api/version"
TAGS_URL = "http://127.0.0.1:11434/api/tags"
ALLOWED_URLS = frozenset({DECIDE_URL, VERSION_URL, TAGS_URL})
TIMEOUT_S = 120.0  # ponytail: cold 9B load; warm calls are far under this
KEEP_ALIVE = "10m"
_WARM = False  # a successful call this process: the model is resident
# Digest verification of a floating tag, cached per process:
# {"key": (tag, prefix), "status": "ok"|"digest_mismatch"|"digest_unverified", "ts": t}
_DIGEST = {"key": None, "status": None, "ts": 0.0}

# Same three judgments as packs/valedia-paper-v1. Stamp only. No numbers and
# no comparisons live here: the state carries code-computed `facts` booleans
# and the criteria refer to them qualitatively.
QUESTIONS = {
    "admit_paper": {
        "type": "noul",
        "instructions": (
            "Paper shadow only. Read the code-computed `facts` in the state. "
            "Is this name admissible as a paper note? Do not compute or compare "
            "anything. Oil and weather stay blocked. Do not place an order."
        ),
        "criteria": {
            "false": "Do not admit. The facts show no print, no positive edge, oil, weather, or a thin tape.",
            "true": "Admissible as a paper shadow note: a print, a positive edge, and no oil, weather, or thin-tape flag. Still not a live order.",
        },
    },
    "edge_quality": {
        "type": "score",
        "instructions": (
            "How strong is the fee-true edge as a paper shadow note, judged "
            "from the code-computed `facts` only? This score does not size "
            "and does not send."
        ),
        "criteria": [
            "none: the facts show no print, or no positive edge",
            "thin: the edge is positive but the paper size bar is not cleared",
            "printable: the paper size bar is cleared and the edge stays positive",
            "stretched: the tape is thin; watch, do not treat as size",
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
WARM_QUESTIONS = {
    "ping": {
        "type": "noul",
        "instructions": "Warmup ping so the model is resident. Is this a ping?",
        "criteria": {"true": "This is a ping.", "false": "This is not a ping."},
    },
}

_INFORMED_PATHS = (
    os.path.abspath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "models", "mirofish_signal.json")),
    os.path.expanduser("~/.openclaw/workspace/models/mirofish_signal.json"),
)


def enabled():
    return os.environ.get("VALEDIA_SYSTEMONE") == "1"


def is_nimble2(name):
    """True for an EXPLICIT Nimble 2 tag only (case-insensitive). Never v1, never Tev.

    Stateless. The floating `nimble:latest` / `nimble` are False here: they
    pass only through the digest pin (is_floating_nimble + verify_digest).
    """
    low = str(name or "").strip().lower()
    if not low or any(bad in low for bad in _V2_BANNED):
        return False
    return _V2_RE.fullmatch(low) is not None


def is_floating_nimble(name):
    """True for `nimble:latest` or bare `nimble` (the digest-pinned candidates)."""
    return str(name or "").strip().lower() in _FLOATING


def digest_prefix():
    """Pinned digest hex prefix (env VALEDIA_SYSTEMONE_DIGEST). "" if unusable."""
    raw = os.environ.get(DIGEST_ENV, NIMBLE2_DIGEST).strip().lower()
    if raw.startswith("sha256:"):
        raw = raw[7:]
    return raw if re.fullmatch(r"[0-9a-f]{12,64}", raw) else ""


def _raw_model():
    return os.environ.get("VALEDIA_SYSTEMONE_MODEL", MODEL_DEFAULT).strip()


def configured_model():
    """The configured tag if it could be Nimble 2 (explicit v2 or floating). Else ""."""
    raw = _raw_model()
    return raw if is_nimble2(raw) or is_floating_nimble(raw) else ""


def _digest_key(raw):
    return ("nimble:latest", digest_prefix()) if is_floating_nimble(raw) else None


def _digest_from_tags(tags_body):
    """Compare the local digest of nimble:latest with the pin. Pure."""
    prefix = digest_prefix()
    if not prefix or not isinstance(tags_body, dict):
        return "digest_unverified"
    rows = [r for r in (tags_body.get("models") or []) if isinstance(r, dict)
            and "nimble:latest" in (str(r.get("name") or "").strip().lower(),
                                    str(r.get("model") or "").strip().lower())]
    digs = [str(r.get("digest") or "").strip().lower() for r in rows]
    digs = [d[7:] if d.startswith("sha256:") else d for d in digs]
    digs = [d for d in digs if d]
    if not digs:
        return "digest_unverified"
    return "ok" if any(d.startswith(prefix) for d in digs) else "digest_mismatch"


def _digest_set(raw, status):
    _DIGEST.update(key=_digest_key(raw), status=status, ts=time.monotonic())
    return status


def _reset_digest():
    _DIGEST.update(key=None, status=None, ts=0.0)


def digest_status(raw=None):
    """Cached status for the configured floating tag: ok / digest_mismatch / digest_unverified.

    None for an explicit v2 tag (no digest needed). Reads the cache only.
    """
    raw = _raw_model() if raw is None else raw
    key = _digest_key(raw)
    if key is None:
        return None
    return _DIGEST["status"] if _DIGEST["key"] == key and _DIGEST["status"] else "digest_unverified"


def verify_digest(get=None, timeout=3.0, force=False):
    """Verify the floating tag's digest via GET /api/tags. Cached for this process.

    ok is cached until the process restarts; a failure is cached for
    DIGEST_RETRY_S so a scan does not hammer /api/tags. Never raises.
    None for an explicit v2 tag.
    """
    raw = _raw_model()
    key = _digest_key(raw)
    if key is None:
        return None
    if not force and _DIGEST["key"] == key and _DIGEST["status"]:
        if _DIGEST["status"] == "ok" or time.monotonic() - _DIGEST["ts"] < DIGEST_RETRY_S:
            return _DIGEST["status"]
    try:
        body = (get or _get)(TAGS_URL, timeout)
    except Exception:
        return _digest_set(raw, "digest_unverified")
    return _digest_set(raw, _digest_from_tags(body))


def model_pin():
    """Pin to POST, or "" (the caller must not POST). Never touches the network.

    Explicit Nimble 2 passes as before. `nimble:latest` / `nimble` pass only if
    this process already verified the digest (verify_digest / health / ask).
    """
    raw = _raw_model()
    if is_nimble2(raw):
        return raw
    if is_floating_nimble(raw) and digest_status(raw) == "ok":
        return raw
    return ""


def pin_refusal():
    """Why model_pin() is empty: model_refused, digest_unverified, digest_mismatch."""
    raw = _raw_model()
    if is_nimble2(raw):
        return None
    if is_floating_nimble(raw):
        status = digest_status(raw)
        return None if status == "ok" else status
    return "model_refused"


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
    label = row.get("label")
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


def _num(v):
    """Finite real number or None. Bools and strings are not numbers."""
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return v


def facts(row, limits=None):
    """Code-computed booleans. Every threshold and comparison lives here.

    limits: {"print_bar_pct", "min_quote_usd"} from the scan's own constants.
    A fact is None when its input is unknown. The model never compares.
    """
    lim = limits or {}
    chg, edge = _num(row.get("chg_24h")), _num(row.get("edge"))
    bar, minq, vol = _num(lim.get("print_bar_pct")), _num(lim.get("min_quote_usd")), _num(row.get("quote_volume_usd"))
    venue = _venue(row)
    return {
        "has_print": chg is not None,
        "edge_positive": edge is not None and edge > 0,
        "clears_size_bar": None if bar is None or chg is None else chg >= bar,
        "thin_tape": None if minq is None or vol is None else vol < minq,
        "is_oil": venue == "oil_blocked",
        "is_weather": venue == "weather_watch",
    }


def compact_state(row, informed=None, limits=None):
    """Small non-secret state. No keys, no headlines, no order."""
    return {
        "symbol": row.get("symbol"),
        "class": _venue(row),
        "chg_pct": _finite(row.get("chg_24h")),
        "fee_true_edge": _finite(row.get("edge")),
        "impact_usd": _finite(row.get("impact_usd")),
        "facts": facts(row, limits),
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
        "within_budget": False,
    }


def _prob(d):
    """noul probability. Accepts the `noul` or `probability` key."""
    d = d if isinstance(d, dict) else {}
    v = _num(d.get("noul"))
    return v if v is not None else _num(d.get("probability"))


def _probs(d):
    return {str(k): _num(v) for k, v in d.items()} if isinstance(d, dict) else None


def _answers(body):
    ans = (body or {}).get("answers") or {}
    admit = ans.get("admit_paper") or {}
    edge = ans.get("edge_quality") or {}
    venue = ans.get("venue_class") or {}
    return {
        "admit_paper": _prob(admit),
        "edge_quality": edge.get("score"),
        "edge_quality_confidence": _num(edge.get("confidence")),
        "venue_class": venue.get("choice"),
        "venue_confidence": _num(venue.get("confidence")),
        "venue_probabilities": _probs(venue.get("probabilities")),
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
    got = (body or {}).get("model") or pin
    if is_nimble2(got):
        return True
    # A floating answer name is fine only when the pin itself is the
    # digest-verified floating tag; a v1 name never is.
    return is_floating_nimble(got) and is_floating_nimble(pin) and model_pin() == pin


def ask(state, questions, *, source, label=None, post=None, timeout=None, parse=None, get=None):
    """One loopback shadow call. Always logged. Never raises.

    Returns {pin, body, error, answers, cold, latency_ms, within_budget}.
    error is None only when a model answer came back from an allowed pin.
    """
    global _WARM
    state = finite_tree(state)
    verify_digest(get)  # no-op for an explicit v2 tag; cached once verified; never raises
    pin = model_pin()
    res = {"pin": pin or None, "body": None, "error": None, "answers": None, "cold": not _WARM}
    t0 = time.perf_counter()
    try:
        if not pin:
            res["error"] = pin_refusal() or "model_refused"
        else:
            body = (post or _post)(
                DECIDE_URL,
                {"model": pin, "state": state, "questions": questions, "keep_alive": KEEP_ALIVE},
                timeout or TIMEOUT_S,
            )
            res["body"] = body
            if not isinstance(body, dict):
                res["error"] = "bad_response"
            elif not _response_model_ok(body, pin):
                res["error"] = "model_refused"
            elif "answers" not in body:
                res["error"] = "no_answers"
            elif parse:
                res["answers"] = parse(body)
    except Exception as e:
        res["error"] = type(e).__name__
    res["latency_ms"] = round((time.perf_counter() - t0) * 1000.0, 2)
    res["within_budget"] = res["error"] is None and res["latency_ms"] <= budget_ms()
    if res["error"] is None:
        _WARM = True
    log_decision(source, "stub" if res["error"] else "systemone_http", res["pin"], state,
                 res["body"], res["error"], res["answers"], res["latency_ms"], label, res["cold"])
    return res


def warmup(post=None, timeout=None, get=None):
    """One tiny call so the model is resident (keep_alive). Once per scan."""
    if not enabled():
        return None
    return ask({"symbol": "warmup", "create_order": False}, WARM_QUESTIONS,
               source="warmup", post=post, timeout=timeout, get=get)


def decide(state, post=None, timeout=None, label=None, source="movers_scan", get=None):
    """POST one shadow decide. Timeout, bad model, or a bad body → stub.

    label is the code's own fee-true label, logged beside the model's answer.
    """
    res = ask(state, QUESTIONS, source=source, label=label, post=post,
              timeout=timeout, parse=_answers, get=get)
    ms = res["latency_ms"]
    if res["error"]:
        out = _stub(res["error"])
        out["client_ms"] = ms
        out["cold"] = res["cold"]
        return out
    usage = res["body"].get("usage")
    return {
        "backend": "systemone_http",
        "fail_open": False,
        "error": None,
        "mode": "shadow",
        "enforcement": "none",
        "create_order": False,
        "model": res["pin"],
        "judge": "dont_trade",
        "answers": res["answers"],
        "latency_ms": ms,
        "client_ms": ms,
        "cold": res["cold"],
        "within_budget": res["within_budget"],
        "usage": usage if isinstance(usage, dict) else None,
    }


def health(get=None, timeout=3.0):
    """Version + tags only. Does not send a decide.

    For the floating `nimble:latest` it also checks the digest pin against
    /api/tags and caches the verdict for this process (digest field).
    """
    fn = get or _get
    raw = _raw_model()
    try:
        ver_body = fn(VERSION_URL, timeout)
        tags_body = fn(TAGS_URL, timeout)
    except Exception as e:
        return {
            "ok": False,
            "error": type(e).__name__,
            "version": None,
            "model_present": False,
            "model": configured_model() or None,
            "digest": digest_status(raw),
            "mode": "shadow",
            "enforcement": "none",
        }
    version = str((ver_body or {}).get("version") or "") if isinstance(ver_body, dict) else ""
    names = []
    if isinstance(tags_body, dict):
        for row in tags_body.get("models") or []:
            if isinstance(row, dict) and row.get("name"):
                names.append(str(row["name"]))
    pin = configured_model().lower()
    # Ollama lists an untagged pull as <name>:latest.
    want = {pin, pin + ":latest"} if pin and ":" not in pin.rsplit("/", 1)[-1] else {pin}
    present = bool(pin) and any(n.lower() in want for n in names)
    digest = None
    if present and is_floating_nimble(raw):
        digest = _digest_set(raw, _digest_from_tags(tags_body))
    elif is_floating_nimble(raw):
        digest = digest_status(raw)
    digest_ok = digest in (None, "ok")
    ready = _version_ok(version) and present and digest_ok
    if ready:
        err = None
    elif not _version_ok(version):
        err = "version"
    elif not present:
        err = "model_missing"
    else:
        err = digest
    return {
        "ok": ready,
        "error": err,
        "version": version,
        "model_present": present,
        "model": configured_model() or None,
        "digest": digest,
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
            "model": configured_model() or None,
            "judge": "dont_trade",
            "budget_ms": budget_ms(),
            "informed_mirofish": False,
        }
    rows = [r.get("systemone_shadow") or {} for r in ranked or []]
    ok = [r for r in rows if r.get("backend") == "systemone_http" and not r.get("fail_open")]
    lats = [r.get("latency_ms") for r in ok if isinstance(r.get("latency_ms"), (int, float))]
    warm = sorted(r["latency_ms"] for r in ok
                  if not r.get("cold") and isinstance(r.get("latency_ms"), (int, float)))

    def pct(q):  # nearest rank over warm calls only; cold loads are counted apart
        return warm[min(len(warm) - 1, max(0, math.ceil(q * len(warm)) - 1))] if warm else None

    return {
        "enabled": True,
        "backend": "systemone_http" if ok else "stub",
        "fail_open": not bool(ok),
        "mode": "shadow",
        "enforcement": "none",
        "create_order": False,
        "model": configured_model() or None,
        "digest": digest_status(),
        "judge": "dont_trade",
        "n_ok": len(ok),
        "n": len(rows),
        "latency_ms_min": min(lats) if lats else None,
        "latency_ms_max": max(lats) if lats else None,
        "latency_ms_p50": pct(0.50),
        "latency_ms_p95": pct(0.95),
        "budget_ms": budget_ms(),
        "within_budget_n": sum(1 for r in ok if r.get("within_budget")),
        "cold_n": sum(1 for r in ok if r.get("cold")),
        "informed_mirofish": bool((informed or {}).get("present")),
    }
