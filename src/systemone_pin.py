"""Pin for the System One shadow client: the one shared Elephant-1 instance on LM Studio. Fail-closed.

Named for the first model this chain spoke to; it now knows exactly one decision model, the shared instance
described by `valedia_trader/core/decision_model.py`. That module is loaded here BY FILE, from the copy in the
same tree as this `src/` file (any interpreter, nothing put on sys.path, never raises at import). If it does not
load, every pin refuses with a one-line reason. Its constants and its list test are used as they are; none is
copied here.

The pin is the shared module's list test: `GET <decision_model.URL>/api/v1/models` shows exactly one right
instance (`_wrong_found` is None). A passed check is trusted for check_ttl() seconds (env
VALEDIA_SYSTEMONE_DIGEST_TTL_S, the name kept from the first model); a failed one is not retried inside
CHECK_RETRY_S. `VALEDIA_SYSTEMONE_MODEL` unset or `elephant-1` is accepted, any other value is refused.
`VALEDIA_SYSTEMONE_DIGEST` is ignored (one deprecation line, once). Never loads or unloads a model, never places
an order. No network of its own: the caller passes the getter.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import time
from pathlib import Path

DM_PATH = Path(__file__).resolve().parent.parent / "valedia_trader" / "core" / "decision_model.py"


def _load_dm(path=DM_PATH):
    """(module, None) or (None, one-line reason). Never raises."""
    try:
        spec = importlib.util.spec_from_file_location("valedia_decision_model_by_file", path)
        if spec is None or spec.loader is None:
            return None, "decision model file not loadable: %s" % path
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        for name in ("URL", "KEY", "INSTANCE", "QUANT", "_instances", "_wrong_found"):
            getattr(mod, name)
        return mod, None
    except Exception as e:  # missing, unreadable or broken: the pin refuses
        return None, "decision model file not loaded (%s): %s" % (type(e).__name__, path)


dm, DM_ERROR = _load_dm()

MODEL_ENV = "VALEDIA_SYSTEMONE_MODEL"
DEPRECATED_ENV = "VALEDIA_SYSTEMONE_DIGEST"     # ignored since the port to the shared instance
TTL_ENV = "VALEDIA_SYSTEMONE_DIGEST_TTL_S"      # the cache window's override, name kept
CHECK_RETRY_S = 30.0   # a failed list check is not retried inside this window
CHECK_OK_TTL_S = 300.0  # a passed check expires too, so a later change of the instance is caught
LIST_URL = dm.URL + "/api/v1/models" if dm is not None else ""
# Source key prefix of a decision: names the model, so no record of another model is ever mixed in.
SOURCE_PREFIX = "decide:%s@%s" % (dm.INSTANCE, dm.QUANT.lower()) if dm is not None else ""

_CHECK = {"status": None, "why": None, "ts": 0.0}
_NOTED = {"deprecated": False}


def _note_deprecated():
    if os.environ.get(DEPRECATED_ENV, "").strip() and not _NOTED["deprecated"]:
        _NOTED["deprecated"] = True
        print("systemone_pin: %s is ignored: the pin is the shared instance's list test" % DEPRECATED_ENV,
              file=sys.stderr)


def _raw_model():
    return os.environ.get(MODEL_ENV, "").strip()


def configured_model():
    """The instance name when the env allows it (unset or the instance name), else ""."""
    _note_deprecated()
    if dm is None:
        return ""
    return dm.INSTANCE if _raw_model() in ("", dm.INSTANCE) else ""


def listing_status(listing):
    """(status, why) for a model list: ok / instance_missing / instance_wrong / instance_unverified. Pure."""
    try:
        _, found = dm._instances(listing)
        if not found:
            return "instance_missing", "no loaded instance named %s" % dm.INSTANCE
        why = dm._wrong_found(found)
    except Exception as e:
        return "instance_unverified", "model list malformed: %s" % (str(e)[:80] or type(e).__name__)
    return ("instance_wrong", why) if why else ("ok", None)


def check_ttl():
    """Seconds a passed check stays valid. Bad or non-positive env: the default."""
    try:
        v = float(os.environ.get(TTL_ENV, CHECK_OK_TTL_S))
    except (TypeError, ValueError):
        return CHECK_OK_TTL_S
    return v if v > 0 and v == v and v != float("inf") else CHECK_OK_TTL_S


def _reset_check():
    _CHECK.update(status=None, why=None, ts=0.0)


def check_status():
    """Cached list-check status. Reads the cache only; a passed check older than check_ttl() is unverified."""
    st = _CHECK["status"]
    if st is None or st == "ok" and time.monotonic() - _CHECK["ts"] >= check_ttl():
        return "instance_unverified"
    return st


def verify(get, timeout=3.0, force=False):
    """Run the list check through `get(url, timeout)` (the caller's transport). Cached. Never raises.

    No request when the decision model file did not load or the env names another model."""
    if dm is None:
        return "decision_model_unavailable"
    if not configured_model():
        return "model_refused"
    st, age = _CHECK["status"], time.monotonic() - _CHECK["ts"]
    if not force and st is not None and (st == "ok" and age < check_ttl() or st != "ok" and age < CHECK_RETRY_S):
        return st
    try:
        status, why = listing_status(get(LIST_URL, timeout))
    except Exception as e:
        status, why = "instance_unverified", "model list unreachable: %s" % type(e).__name__
    _CHECK.update(status=status, why=why, ts=time.monotonic())
    return status


def model_pin():
    """The instance name to call, or "" (the caller must not call). Never touches the network."""
    if dm is None or not configured_model():
        return ""
    return dm.INSTANCE if check_status() == "ok" else ""


def pin_refusal():
    """Why model_pin() is empty (None when it is not): decision_model_unavailable, model_refused, or the
    list check's status."""
    if model_pin():
        return None
    if dm is None:
        return "decision_model_unavailable"
    if not configured_model():
        return "model_refused"
    return check_status()


def refusal_reason():
    """One line saying why the pin refuses, or None."""
    code = pin_refusal()
    if code is None:
        return None
    if code == "decision_model_unavailable":
        return DM_ERROR
    if code == "model_refused":
        return "%s=%r is refused: only %s is accepted" % (MODEL_ENV, _raw_model()[:60], dm.INSTANCE)
    return "%s: %s" % (code, _CHECK["why"] or "the list check has not passed")
