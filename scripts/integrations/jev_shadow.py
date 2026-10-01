"""AUTONOMY-2: optional TypeSafe Jev shadow gates. Fail-open.

VALEDIA_JEV default False → pass-through, zero TypeSafe calls.
When on: POST /v1/systemone with a tiny JSON state + CIO questions.
Timeouts/errors/missing key: log + continue. Never blocks live Kalshi.
Does not change score/direction/bias/weight/Kelly. Research helper, not OMS.
"""
import json
import logging
import os
import urllib.request

log = logging.getLogger("jev_shadow")

SYSTEMONE_URL = "https://api.typesafe.ai/v1/systemone"
MODEL = "jev-1.13.0"
TIMEOUT_S = 3.0

QUESTIONS = {
    "action": {
        "type": "choice",
        "instructions": (
            "Which shadow CIO action fits `overall_bias`, `directions`, and "
            "`events`? Code owns sizing and live orders. Seasoning only."
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
            "Should the desk stand down on this report in shadow "
            "(sit out, not a live halt)?"
        ),
        "criteria": {
            "true": "Report is not actionable; sit out this cycle.",
            "false": "Report can be used as seasoning.",
        },
    },
    "report_usable": {
        "type": "noul",
        "instructions": "Is this compressed MiroFish report usable as shadow seasoning?",
        "criteria": {
            "true": "Enough structure to judge.",
            "false": "Too thin or contradictory to use.",
        },
    },
    "we_are_the_exit": {
        "type": "noul",
        "instructions": (
            "Does this setup look like we are the exit "
            "(chasing, late, providing the other side's liquidity)?"
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
    return {
        "overall_bias": doc.get("overall_bias"),
        "macro_bias": doc.get("macro_bias"),
        "n_signals": n_signals,
        "directions": dirs,
        "events": events,
        "consensus_bull_ratio": cons.get("bull_ratio"),
        "source": doc.get("source") or "mirofish",
    }


def _noul(ans, key):
    v = (ans or {}).get(key) or {}
    try:
        return float(v.get("noul"))
    except (TypeError, ValueError):
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


def annotate(doc, post=None):
    """Pass-through if Jev off. Shadow-stamp `jev` if on. Fail-open. Never skips live."""
    if not jev_on():
        return doc
    doc = dict(doc or {})
    key = api_key()
    if not key:
        doc["jev"] = _fail("no_key")
        return doc
    fn = post or http_post
    try:
        body = fn(
            SYSTEMONE_URL,
            {"state": tiny_state(doc), "model": _model(), "questions": QUESTIONS},
            {
                "Authorization": "Bearer " + key,
                "Content-Type": "application/json",
            },
            _timeout(),
        )
        if not isinstance(body, dict) or "answers" not in body:
            doc["jev"] = _fail("bad_response")
            return doc
        doc["jev"] = parse_answers(body)
    except Exception as e:
        doc["jev"] = _fail(type(e).__name__)
    return doc


WEATHER_QUESTIONS = {
    "agree": {
        "type": "choice",
        "instructions": (
            "Given settle value, research tags, and the book price, do they agree? "
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
    return {
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
        "confidence_noul": _noul(ans, "confidence"),
        "status_honesty": honest.get("choice"),
    }


def annotate_weather(bundle, post=None):
    """Shadow stamp on a weather row. Off = identity. Fail-open.

    Adds `jev` only. Does not resize, veto, or send. Agree is not a pass.
    """
    if not jev_on():
        return bundle
    bundle = dict(bundle or {})
    key = api_key()
    if not key:
        bundle["jev"] = _fail("no_key")
    else:
        fn = post or http_post
        try:
            body = fn(
                SYSTEMONE_URL,
                {"state": weather_state(bundle), "model": _model(), "questions": WEATHER_QUESTIONS},
                {
                    "Authorization": "Bearer " + key,
                    "Content-Type": "application/json",
                },
                _timeout(),
            )
            if not isinstance(body, dict) or "answers" not in body:
                bundle["jev"] = _fail("bad_response")
            else:
                bundle["jev"] = parse_weather_answers(body)
        except Exception as e:
            bundle["jev"] = _fail(type(e).__name__)
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
