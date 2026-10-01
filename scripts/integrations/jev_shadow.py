"""AUTONOMY-2: optional TypeSafe Jev shadow gates. Fail-open.

VALEDIA_JEV default False → pass-through, zero TypeSafe calls.
When on: POST /v1/systemone with a tiny JSON state + CIO questions.
Timeouts/errors/missing key: log + continue. Never blocks live Kalshi.
Does not change score/direction/bias/weight/Kelly. Research helper, not OMS.

Gates live in code, not in the model call: no number or comparison is asked of
the model. tiny_state/weather_state add code-computed `facts` and the questions
refer to those facts. Every decision, both backends, is logged (systemone_log).
VALEDIA_JEV_BACKEND=nimble2 (opt-in) sends the same questions to loopback
Ollama /v1/systemone on the Nimble 2 pin (nimble:latest, digest-pinned to
24e550a16a70 in systemone_hotpath_client; a mismatch or unverified digest
fails open, reason digest_mismatch / digest_unverified); default stays the
TypeSafe cloud.
"""
import json
import logging
import os
import time
import urllib.request

log = logging.getLogger("jev_shadow")

SYSTEMONE_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-1.13.0"
TIMEOUT_S = 3.0
BACKEND_LOCAL = "nimble2"

QUESTIONS = {
    "action": {
        "type": "choice",
        "instructions": (
            "Using the code-computed `facts`, which shadow CIO action fits "
            "this report? Do not count or compare. Code owns sizing and live "
            "orders. Seasoning only."
        ),
        "criteria": {
            "annotate": "Stamp the report; no shadow veto or resize.",
            "veto": "Shadow-veto this report; do not use it for conviction.",
            "resize": "Keep the report but scale conviction down in shadow.",
            "stand_down": "Sit this cycle out in shadow; not a live trading halt.",
        },
    },
    "stand_down": {
        "type": "noul",
        "instructions": (
            "Using the code-computed `facts`, should the desk stand down on "
            "this report in shadow (sit out, not a live halt)?"
        ),
        "criteria": {
            "true": "Report is not actionable; sit out this cycle.",
            "false": "Report can be used as seasoning.",
        },
    },
    "report_usable": {
        "type": "noul",
        "instructions": (
            "Using the code-computed `facts`, is this compressed MiroFish "
            "report usable as shadow seasoning?"
        ),
        "criteria": {
            "true": "Enough structure to judge.",
            "false": "Too thin or contradictory to use.",
        },
    },
    "we_are_the_exit": {
        "type": "noul",
        "instructions": (
            "Using the code-computed `facts`, does this setup look like we "
            "are the exit (chasing, late, providing the other side's liquidity)?"
        ),
        "criteria": {
            "true": "We would be the exit / late chase.",
            "false": "Not an exit-liquidity read.",
        },
    },
}


def jev_on():
    try:
        import valedia_config as vc
        return bool(vc.jev_enabled())
    except Exception:
        env = os.environ.get("VALEDIA_JEV")
        if env is not None and str(env).strip() != "":
            return str(env).strip().lower() in ("1", "true", "yes", "on")
        return False


def _timeout():
    raw = os.environ.get("VALEDIA_JEV_TIMEOUT")
    if raw:
        try:
            return max(0.2, float(raw))
        except (TypeError, ValueError):
            pass
    return TIMEOUT_S


def _model():
    return os.environ.get("VALEDIA_JEV_MODEL") or MODEL


def _load_openclaw_env():
    """Load ~/.openclaw/.env via setdefault. Prefer valedia_config wire."""
    try:
        import valedia_config as vc
        if hasattr(vc, "ensure_openclaw_env"):
            vc.ensure_openclaw_env()
            return
    except Exception:
        pass
    path = os.path.expanduser("~/.openclaw/.env")
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def api_key():
    key = os.environ.get("TYPESAFE_API_KEY") or os.environ.get("TYPESAFE_AI_API_KEY")
    if key:
        return key.strip()
    _load_openclaw_env()
    key = os.environ.get("TYPESAFE_API_KEY") or os.environ.get("TYPESAFE_AI_API_KEY")
    return (key or "").strip()


def tiny_state(doc):
    """Compress a MiroFish signal dict. Never send markdown / GraphRAG."""
    doc = doc if isinstance(doc, dict) else {}
    sigs = doc.get("signals") or {}
    dirs = {}
    if isinstance(sigs, dict):
        for v in sigs.values():
            blob = v if isinstance(v, dict) else {}
            d = str(blob.get("direction") or "HOLD").upper()
            dirs[d] = dirs.get(d, 0) + 1
        n_signals = len(sigs)
    else:
        n_signals = 0
    events = []
    for e in (doc.get("events") or [])[:8]:
        if isinstance(e, dict):
            events.append({"type": e.get("type"), "direction": e.get("direction")})
        else:
            events.append(str(e)[:40])
    cons = doc.get("consensus") if isinstance(doc.get("consensus"), dict) else {}
    bull, bear = _real(cons.get("bullish")), _real(cons.get("bearish"))
    facts = {
        "has_signals": n_signals > 0,
        "has_events": bool(events),
        "directions_conflict": len(dirs) > 1,
        "consensus_side": (
            None if bull is None or bear is None
            else "bull" if bull > bear else "bear" if bear > bull else "split"),
    }
    return {
        "facts": facts,
        "overall_bias": doc.get("overall_bias"),
        "macro_bias": doc.get("macro_bias"),
        "n_signals": n_signals,
        "directions": dirs,
        "events": events,
        "consensus_bull_ratio": cons.get("bull_ratio"),
        "source": doc.get("source") or "mirofish",
    }


def _real(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _noul(ans, key):
    v = (ans or {}).get(key) or {}
    for k in ("noul", "probability"):
        try:
            return float(v.get(k))
        except (TypeError, ValueError):
            continue
    return None


def parse_answers(body):
    ans = (body or {}).get("answers") or {}
    action = ans.get("action") or {}
    return {
        "enabled": True,
        "fail_open": False,
        "model": (body or {}).get("model") or _model(),
        "action": action.get("choice"),
        "action_confidence": action.get("confidence"),
        "action_probabilities": action.get("probabilities"),
        "stand_down": _noul(ans, "stand_down"),
        "report_usable": _noul(ans, "report_usable"),
        "we_are_the_exit": _noul(ans, "we_are_the_exit"),
    }


def _fail(reason):
    log.warning("jev fail-open: %s", reason)
    return {"enabled": True, "fail_open": True, "error": str(reason)[:80]}


def http_post(url, payload, headers, timeout):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def backend():
    """typesafe (default, cloud) or nimble2 (opt-in, loopback Ollama)."""
    raw = str(os.environ.get("VALEDIA_JEV_BACKEND") or "").strip().lower()
    return BACKEND_LOCAL if raw == BACKEND_LOCAL else "typesafe"


def _log(caller, backend_name, model, state, body, err, parsed, ms, label):
    try:
        import systemone_log as slog
        slog.log_decision("jev_shadow:" + caller, backend_name, model, state, body,
                          err, parsed, ms, label)
    except Exception:
        pass


def _run(caller, state, questions, parse, post, local_post, label=None):
    """One shadow call on the chosen backend. Logged. Returns a stamp dict."""
    if backend() == BACKEND_LOCAL:
        try:
            import systemone_hotpath_client as s1
            res = s1.ask(state, questions, source="jev_shadow:" + caller, label=label,
                         post=local_post, timeout=_timeout(), parse=parse)
        except Exception as e:
            return _fail(type(e).__name__)
        if res["error"]:
            return _fail(res["error"])
        out = dict(res["answers"])
        out["backend"] = BACKEND_LOCAL
        out["model"] = res["body"].get("model") or res["pin"]
        return out
    t0 = time.perf_counter()
    body = out = err = None
    key = api_key()
    if not key:
        err = "no_key"
    else:
        try:
            body = (post or http_post)(
                SYSTEMONE_URL,
                {"state": state, "model": _model(), "questions": questions},
                {"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                _timeout(),
            )
            if not isinstance(body, dict) or "answers" not in body:
                err = "bad_response"
            else:
                out = parse(body)
                out["backend"] = "typesafe"
        except Exception as e:
            err = type(e).__name__
    ms = None if err == "no_key" else round((time.perf_counter() - t0) * 1000.0, 2)
    _log(caller, "typesafe_cloud", _model(), state, body, err, out, ms, label)
    return _fail(err) if err else out


def annotate(doc, post=None, local_post=None):
    """Pass-through if Jev off. Shadow-stamp `jev` if on. Fail-open. Never skips live."""
    if not jev_on():
        return doc
    doc = dict(doc or {})
    doc["jev"] = _run("annotate", tiny_state(doc), QUESTIONS, parse_answers, post, local_post)
    return doc


WEATHER_QUESTIONS = {
    "agree": {
        "type": "choice",
        "instructions": (
            "Using the code-computed `facts`, plus the settle source and research "
            "tags, do they agree with the book? Do not compare numbers. "
            "Shadow only. Do not send."
        ),
        "criteria": {
            "agree": "Settle and research point the same way versus the book.",
            "disagree": "Settle and research conflict, or the book is the other side.",
            "abstain": "Not enough to say.",
        },
    },
    "confidence": {
        "type": "noul",
        "instructions": "Is that agree/disagree read confident? Shadow only.",
        "criteria": {
            "true": "Confident.",
            "false": "Not confident.",
        },
    },
    "status_honesty": {
        "type": "choice",
        "instructions": (
            "Is the settlement status honest (final or observation), "
            "or is a forecast being treated as final?"
        ),
        "criteria": {
            "honest": "Final or observation, and research stays research.",
            "forecast_as_final": "A forecast is being used as if it settled.",
            "missing": "No settle value.",
        },
    },
}


def weather_state(bundle):
    """Tiny weather bundle. No rules text, no keys, no order."""
    b = bundle if isinstance(bundle, dict) else {}
    sf = b.get("settlement_fv") if isinstance(b.get("settlement_fv"), dict) else {}
    tags = []
    for t in (b.get("research_tags") or [])[:6]:
        if isinstance(t, dict):
            tags.append(t.get("source"))
    fv, px, ev = _real(b.get("fv")), _real(b.get("price")), _real(b.get("fee_true_ev"))
    facts = {
        "settle_present": sf.get("value") is not None,
        "fv_vs_book": (
            None if fv is None or px is None
            else "above" if fv > px else "below" if fv < px else "equal"),
        "fee_true_ev_positive": None if ev is None else ev > 0,
    }
    return {
        "facts": facts,
        "ticker": b.get("ticker"),
        "settle_source": sf.get("source"),
        "settle_value": sf.get("value"),
        "settle_confidence": sf.get("confidence"),
        "metric": sf.get("metric"),
        "fv": b.get("fv"),
        "price": b.get("price"),
        "fee_true_ev": b.get("fee_true_ev"),
        "tag": b.get("tag"),
        "research": tags,
    }


def parse_weather_answers(body):
    ans = (body or {}).get("answers") or {}
    agree = ans.get("agree") or {}
    honest = ans.get("status_honesty") or {}
    return {
        "enabled": True,
        "fail_open": False,
        "oms": False,
        "model": (body or {}).get("model") or _model(),
        "agree": agree.get("choice"),
        "confidence": agree.get("confidence"),
        "agree_probabilities": agree.get("probabilities"),
        "confidence_noul": _noul(ans, "confidence"),
        "status_honesty": honest.get("choice"),
    }


def annotate_weather(bundle, post=None, local_post=None):
    """Shadow stamp on a weather row. Off = identity. Fail-open.

    Adds `jev` only. Does not resize, veto, or send. Agree is not a pass.
    """
    if not jev_on():
        return bundle
    bundle = dict(bundle or {})
    bundle["jev"] = _run(
        "annotate_weather", weather_state(bundle), WEATHER_QUESTIONS,
        parse_weather_answers, post, local_post, label=bundle.get("tag"))
    bundle["jev"]["oms"] = False
    return bundle


def attach_to_signal(signal_doc, post=None):
    """Stamp `jev` on a MiroFish signal. Off = identity. Does not change scores."""
    try:
        return annotate(signal_doc, post=post)
    except Exception as e:
        if not jev_on():
            return signal_doc
        doc = dict(signal_doc or {})
        doc["jev"] = _fail(type(e).__name__)
        return doc
