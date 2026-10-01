"""Nimble 2 pin and digest pin for the System One shadow client. Fail-open.

Pure move out of systemone_hotpath_client (kept under the 500-line rule); the
client re-exports every name here. Explicit Nimble 2 tags pass by name. The
floating `nimble:latest` / bare `nimble` pass ONLY while GET /api/tags shows a
digest starting with NIMBLE2_DIGEST (Burton's Nimble 2, confirmed 2026-10-01).
Never places an order. No network of its own: the caller passes the getter.
"""
from __future__ import annotations

import os
import re
import time

MODEL_DEFAULT = "nimble:latest"
TAGS_URL = "http://127.0.0.1:11434/api/tags"

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
# Digest verification of a floating tag, cached per process:
# {"key": (tag, prefix), "status": "ok"|"digest_mismatch"|"digest_unverified", "ts": t}
_DIGEST = {"key": None, "status": None, "ts": 0.0}


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


def verify_digest(get, timeout=3.0, force=False):
    """Verify the floating tag's digest via GET /api/tags. Cached for this process.

    ok is cached until the process restarts; a failure is cached for
    DIGEST_RETRY_S so a scan does not hammer /api/tags. Never raises.
    None for an explicit v2 tag. `get(url, timeout)` is the caller's transport.
    """
    raw = _raw_model()
    key = _digest_key(raw)
    if key is None:
        return None
    if not force and _DIGEST["key"] == key and _DIGEST["status"]:
        if _DIGEST["status"] == "ok" or time.monotonic() - _DIGEST["ts"] < DIGEST_RETRY_S:
            return _DIGEST["status"]
    try:
        body = get(TAGS_URL, timeout)
    except Exception:
        return _digest_set(raw, "digest_unverified")
    return _digest_set(raw, _digest_from_tags(body))
