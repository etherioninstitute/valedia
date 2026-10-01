"""Nimble 2 pin rule: explicit v2 names, or the bare/latest tag pinned by Ollama digest.

Copied from Valedia; source of truth: scripts/integrations/systemone_hotpath_client.py
(is_nimble2 and the NIMBLE2_DIGEST rule; this project must stay standalone, so it is
duplicated here, not imported). Semantics match that file: same constant name
NIMBLE2_DIGEST, same refusal reasons ``digest_unverified`` / ``digest_mismatch``.

Burton's Nimble 2 is ``nimble:latest`` (ollama list ID 24e550a16a70). A bare ``nimble`` or
``nimble:latest`` tag says nothing about the version on its own, so it is accepted ONLY when
GET {nimble base}/api/tags shows that model's digest starting with NIMBLE2_DIGEST (or the
NIMBLE_DIGEST override). Unverified or mismatched -> refuse (fail-closed skip). Explicit v1
tags (nimble:9b, nimble:v1, ...) and Tev are always refused.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

NIMBLE2_DIGEST = "24e550a16a70"
DIGEST_PINNED = ("nimble", "nimble:latest")

_TAG = r"[a-z0-9][a-z0-9._-]*"
_QUANT = r"(?:q\d[a-z0-9_]*|iq\d[a-z0-9_]*|fp16|fp32|bf16|f16|f32|int4|int8)"
_V2_RE = re.compile(
    r"nimble2(?::" + _TAG + r")?"
    r"|nimble:(?:2|v2|9b-v2)(?:-" + _QUANT + r")?"
    r"|(?:(?:hf\.co/)?bespokelabs/)?bespoke-nimble-9b-v2(?::" + _TAG + r")?"
)
_V2_BANNED = ("v1", "tev", "0.8")


def is_nimble2(name) -> bool:
    """True for an explicit Nimble 2 tag only (case-insensitive). Never v1, never Tev."""
    low = str(name or "").strip().lower()
    if not low or any(bad in low for bad in _V2_BANNED):
        return False
    return _V2_RE.fullmatch(low) is not None


def normalize_tag(name) -> str:
    """Ollama naming: a tag without ':' means ':latest'."""
    low = str(name or "").strip().lower()
    return low if ":" in low or not low else low + ":latest"


def needs_digest(name) -> bool:
    """True for the names accepted only after digest verification (nimble, nimble:latest)."""
    return str(name or "").strip().lower() in DIGEST_PINNED


def pin_form_ok(name) -> bool:
    """Name is acceptable before any network check (explicit v2, or digest-pinnable)."""
    return is_nimble2(name) or needs_digest(name)


def tags_url(nimble_url: str) -> str:
    """{scheme}://{host:port}/api/tags for the configured System One endpoint."""
    u = urlparse(nimble_url)
    return f"{u.scheme}://{u.netloc}/api/tags"


def check_tags(body: Any, model: str, prefix: str) -> Tuple[bool, Optional[str]]:
    """Inspect an /api/tags body. (verified, reason). reason is None only when verified."""
    want, prefix = normalize_tag(model), str(prefix or "").strip().lower()
    if not prefix:
        return False, "digest_unverified: no digest prefix configured"
    models = body.get("models") if isinstance(body, dict) else None
    if not isinstance(models, list):
        return False, "digest_unverified: /api/tags returned no model list"
    for m in models:
        if not isinstance(m, dict):
            continue
        names = {normalize_tag(m.get("name")), normalize_tag(m.get("model"))}
        if want not in names:
            continue
        digest = str(m.get("digest") or "").strip().lower()
        digest = digest[7:] if digest.startswith("sha256:") else digest
        if not digest:
            return False, f"digest_unverified: {want} listed without a digest"
        if digest.startswith(prefix):
            return True, None
        return False, (f"digest_mismatch: {want} digest {digest[:12] or '-'} does not start "
                       f"with {prefix}")
    return False, f"digest_unverified: {want} not listed in /api/tags"


# Per-process cache: (tags url, model, prefix) -> (verified, reason). Only definitive
# answers are cached (verified, or a listed model with the wrong digest); a transport failure
# or a missing model is retried on the next call.
_CACHE: Dict[Tuple[str, str, str], Tuple[bool, Optional[str]]] = {}


def cached(key: Tuple[str, str, str]) -> Optional[Tuple[bool, Optional[str]]]:
    return _CACHE.get(key)


def remember(key: Tuple[str, str, str], result: Tuple[bool, Optional[str]]) -> None:
    if result[0] or (result[1] or "").startswith("digest_mismatch"):
        _CACHE[key] = result


def clear_cache() -> None:
    _CACHE.clear()
