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
