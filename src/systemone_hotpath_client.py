#!/usr/bin/env python3
"""System One shadow client (named for the first model, Nimble): the shared Elephant-1 instance on the
loopback LM Studio, and nothing else. Fail-closed on the pin, fail-open for the scan.

VALEDIA_SYSTEMONE=1 turns the chain on (POST <decision_model.URL>/v1/systemone). Default off: no request at
all. The pin is systemone_pin's list test of the shared module (exactly one right instance). Only two URLs are
ever requested: the model list (GET) and the typed call (POST). The request names decision_model.INSTANCE, the
reply's `model` must equal it, and the reply is checked by the pinned typed parser (systemone_v3_candidate,
given the reply with `model` set to its own TAG). The client never loads or unloads a model: the instance is
loaded and kept by the order-book collector, so there is no warmup request. An injected `post` is not honoured
on the typed call; an injected `get` for the read-only list check is. Code still owns the fee-true label and
the would-have size. This module never places an order. A down server returns a stub and the scan continues.

Gates live in code, not in the model call. Every threshold and comparison is computed in code (facts()) and the
questions only refer to those facts, with no numbers. Every call is logged, yes and no alike, to an
append-only decisions.jsonl.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
import urllib.request
SMOKE_STATE = {"symbol": "SYNTHETIC", "create_order": False}
SMOKE_QUESTIONS = {"ping": {"type": "noul", "instructions": "Is this a synthetic nonfinancial smoke?",
                            "criteria": {"true": "yes", "false": "no"}}}

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

_HTTP = urllib.request.build_opener(NoRedirect)

def _bounded_json(resp):
    raw = resp.read(262145)
    if len(raw) > 262144:
        raise ValueError("oversize response")
    value = json.loads(raw)
    todo = [(value, 0)]
    count = 0
    while todo:
        node, depth = todo.pop()
        count += 1
        if count > 10000 or depth > 32:
            raise ValueError("response shape exceeded bounds")
        if isinstance(node, dict):
            todo.extend((v, depth + 1) for v in node.values())
        elif isinstance(node, list):
            todo.extend((v, depth + 1) for v in node)
    return value

def synthetic_smoke(get=None):
    """One exact, nonfinancial fixture through the same admitted client path."""
    if SMOKE_STATE != {"symbol": "SYNTHETIC", "create_order": False} or SMOKE_QUESTIONS != {
            "ping": {"type": "noul", "instructions": "Is this a synthetic nonfinancial smoke?",
                     "criteria": {"true": "yes", "false": "no"}}}:
        raise ValueError("synthetic fixture changed")
    return ask(SMOKE_STATE, SMOKE_QUESTIONS, source="synthetic_smoke", timeout=60, get=get)


from systemone_log import budget_ms, finite_tree, log_decision, log_path  # noqa: F401

# The pin lives in systemone_pin (re-exported here); the shared module is reached through it, by file.
from systemone_pin import (  # noqa: F401,E402
    CHECK_RETRY_S, LIST_URL, _raw_model, _reset_check, check_status, configured_model, model_pin,
    pin_refusal, refusal_reason)
import systemone_pin as _pin  # noqa: E402
import systemone_v3_candidate as _v3  # noqa: E402  (the pinned typed parser, unchanged)

DECIDE_URL = _pin.dm.URL + "/v1/systemone" if _pin.dm is not None else ""
ALLOWED_URLS = frozenset(u for u in (DECIDE_URL, LIST_URL) if u)
TIMEOUT_S = 60.0  # the typed call's deadline: 0 < timeout <= TIMEOUT_S
_WARM = False  # a successful call this process

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
    # Explicit opt-in only. Shadow path. Nothing in the repository sets it.
    return os.environ.get("VALEDIA_SYSTEMONE") == "1"


def verify_instance(get=None, timeout=3.0, force=False):
    """The pin's list check (systemone_pin.verify) through `get` or this module's own GET. Cached."""
    return _pin.verify(get or _get, timeout, force)



def load_informed(paths=None, now=None):
    """MiroFish only via mirofish_intel_archive.load_for_fv: fresh (<24h), source
    mirofish_swarm, not near-neutral, not an archive. Stale or lightweight → absent."""
    import mirofish_intel_archive as mia
    for path in paths or _INFORMED_PATHS:
        doc = mia.load_for_fv(path, now)
        if doc:
            sigs = doc.get("signals")
            return {"present": True, "signals": sigs if isinstance(sigs, dict) else {}}
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
    """Strict JSON rejects NaN/Infinity. Missing print is null, not a token."""
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


def _check_url(url, allowed=None):
    if not url or url not in ALLOWED_URLS or allowed is not None and url != allowed:
        raise ValueError("refusing systemone url")


def _post(url, payload, timeout):
    """The module's own transport for the typed call: that one URL, no redirects, bounded response."""
    _check_url(url, DECIDE_URL)
    data = json.dumps(payload, allow_nan=False).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with _HTTP.open(req, timeout=timeout) as resp:
        return _bounded_json(resp)


def _get(url, timeout):
    """The module's own GET: the model list only."""
    _check_url(url, LIST_URL)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with _HTTP.open(req, timeout=timeout) as resp:
        return _bounded_json(resp)


def ask(state, questions, *, source, label=None, post=None, timeout=None, parse=None, get=None):
    """One loopback typed call to the shared instance. Always logged. Never raises.

    Returns {pin, body, error, answers, cold, latency_ms, within_budget}. error is None only when a typed
    answer came back from the instance. `post` is accepted and NOT honoured: the call goes through this
    module's own `_post`. `get` (the read-only list check) is honoured. timeout: 0 < timeout <= TIMEOUT_S.
    """
    # Direct callers, including synthetic_smoke, must not bypass enabled().
    if not enabled():
        return {"pin": None, "body": None, "error": "systemone_disabled",
                "answers": None, "cold": True, "latency_ms": 0.0, "within_budget": False}
    global _WARM
    state = finite_tree(state)
    t0 = time.perf_counter()
    start = time.monotonic()
    res = {"pin": None, "body": None, "error": None, "answers": None, "cold": not _WARM}
    try:
        verify_instance(get)  # read-only list check; bounded by the same deadline
        pin = model_pin()
        res["pin"] = pin or None
        if not pin:
            res["error"] = pin_refusal() or "model_refused"
            res["refusal"] = refusal_reason()
        else:
            duration = TIMEOUT_S if timeout is None else timeout
            if not 0 < duration <= TIMEOUT_S:
                raise ValueError("the typed call's deadline must be at most %ds" % TIMEOUT_S)
            payload = _v3.make_request(state, questions, tag=_v3.TAG)  # the typed contract's checks
            payload["model"] = pin                                      # the request names the instance
            remaining = duration - (time.monotonic() - start)
            if remaining <= 0:
                raise TimeoutError("client admission deadline elapsed")
            body = _post(DECIDE_URL, payload, remaining)  # never an injected transport
            res["body"] = body
            if not isinstance(body, dict):
                res["error"] = "bad_response"
            elif body.get("model") != pin:
                res["error"] = "model_refused"
            elif "answers" not in body:
                res["error"] = "no_answers"
            else:
                typed = _v3.parse_response(dict(body, model=_v3.TAG), questions)
                res["typed_answers"] = typed["answers"]
                res["answers"] = typed["answers"]
                res["usage"] = typed["usage"]
                if parse:
                    res["answers"] = parse({"answers": typed["answers"]})
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
    """No request: the shared instance is loaded and kept by another program. Kept for its callers."""
    return None


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
        "typed_answers": res.get("typed_answers"),
        "latency_ms": ms,
        "client_ms": ms,
        "cold": res["cold"],
        "within_budget": res["within_budget"],
        "usage": usage if isinstance(usage, dict) else None,
    }


def health(get=None, timeout=3.0):
    """The list check only (forced). Does not send a typed call. Off: no request at all.

    ok is the list check; `version` stays as a key and is None (no version endpoint is allowed)."""
    out = {"ok": False, "error": None, "version": None, "model_present": False,
           "model": configured_model() or None, "instance_check": None, "mode": "shadow",
           "enforcement": "none"}
    if not enabled():
        out["error"] = "systemone_disabled"
        return out
    status = verify_instance(get, timeout, force=True)
    out.update(ok=status == "ok", error=None if status == "ok" else status,
               model_present=status in ("ok", "instance_wrong"), instance_check=status)
    return out


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
        "instance_check": check_status(),
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
