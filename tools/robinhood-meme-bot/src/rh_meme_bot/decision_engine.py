"""Nimble 2 decision engine (local Ollama System One endpoint).

Principle: the model answers narrow qualitative questions about precomputed facts. It never
computes or sees any threshold; every number is enforced afterwards by gates.py.
Fail-closed: any problem (down, timeout, refused pin, malformed answers) yields a skip.
"""
from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any, Callable, Optional
from urllib.parse import urlparse

from . import nimble_pin
from .nimble_questions import (ACTIONS, QUESTIONS, RAW_LIMIT, RISK_LABELS,  # noqa: F401
                               _num, _risk_expected, raw_snippet, sanitize)
from .nimble_pin import NIMBLE2_DIGEST, is_nimble2, needs_digest, normalize_tag  # noqa: F401

if TYPE_CHECKING:  # config imports this module; avoid the cycle at runtime
    from .config import Config

log = logging.getLogger(__name__)
DEFAULT_NIMBLE_URL = "http://127.0.0.1:11434/v1/systemone"
DEFAULT_NIMBLE_MODEL = "nimble:latest"  # Burton's Nimble 2; accepted only by digest pin

# Pin rule (is_nimble2 + NIMBLE2_DIGEST) lives in nimble_pin.py. Source of truth:
# Valedia's scripts/integrations/systemone_pin.py (copied, not imported).


class NimbleError(Exception):
    def __init__(self, message: str, raw: Any = None):
        super().__init__(message)
        self.raw = raw  # whatever the server returned, if anything


def _http_get() -> Callable[[str, float], Any]:
    import requests

    session = requests.Session()

    def get(url: str, timeout: float) -> Any:
        resp = session.get(url, timeout=timeout)
        if resp.status_code != 200:
            raise NimbleError(f"http {resp.status_code}")
        return resp.json()

    return get


def _http_transport() -> Callable[[str, dict, float], dict]:
    import requests  # persistent connection to the local Ollama

    session = requests.Session()

    def post(url: str, payload: dict, timeout: float) -> dict:
        resp = session.post(url, data=json.dumps(payload, allow_nan=False), timeout=timeout,
                            headers={"Content-Type": "application/json"})
        if resp.status_code != 200:
            raise NimbleError(f"http {resp.status_code}: {resp.text[:120]}",
                              raw=raw_snippet(resp.text))
        try:
            return resp.json()
        except ValueError as e:
            raise NimbleError(f"response is not JSON: {e}", raw=raw_snippet(resp.text)) from e

    return post


def _fail(reason: str, ms: float, model: str, raw: Any = None) -> dict:
    return {"action": "skip", "confidence": 0.0, "risk_score": None, "risk_expected": None,
            "latency_ms": round(ms, 2), "model": model, "raw": raw, "error": reason}


class NimbleEngine:
    def __init__(self, url: str = DEFAULT_NIMBLE_URL, model: str = DEFAULT_NIMBLE_MODEL,
                 timeout_s: float = 10.0, keep_alive: str = "30m", allow_remote: bool = False,
                 transport: Optional[Callable[[str, dict, float], dict]] = None,
                 clock: Callable[[], float] = time.perf_counter, warmup_timeout_s: float = 120.0,
                 digest: str = NIMBLE2_DIGEST,
                 tags_transport: Optional[Callable[[str, float], Any]] = None,
                 digest_ttl_s: Optional[float] = None):
        self.url, self.model, self.timeout_s = url, model, timeout_s
        self.warmup_timeout_s = warmup_timeout_s
        self.keep_alive, self.allow_remote = keep_alive, allow_remote
        self._transport, self._clock = transport, clock
        self.digest = digest
        self.digest_ttl_s = nimble_pin.digest_ok_ttl(digest_ttl_s)
        # GET /api/tags: explicit, else the POST transport's .get (fakes), else HTTP. A custom
        # POST transport without .get gets no network GET: the digest stays unverified.
        if tags_transport is None and transport is not None:
            tags_transport = getattr(transport, "get", None)
        self._tags = tags_transport if (tags_transport or transport) else None
        self._tags_default = tags_transport is None and transport is None

    @classmethod
    def from_config(cls, cfg: "Config", transport=None,
                    clock=time.perf_counter) -> "NimbleEngine":
        return cls(cfg.nimble_url, cfg.nimble_model, cfg.decision_timeout_s,
                   cfg.nimble_keep_alive, cfg.nimble_allow_remote, transport, clock,
                   cfg.warmup_timeout_s, cfg.nimble_digest,
                   digest_ttl_s=cfg.nimble_digest_ttl_s)

    @property
    def pin_ok(self) -> bool:
        return nimble_pin.pin_form_ok(self.model)

    def verify_digest(self) -> Optional[str]:
        """For nimble / nimble:latest: check /api/tags once per process. None = verified
        (or not needed); otherwise a 'digest_unverified: ...' / 'digest_mismatch: ...' reason.
        """
        if not needs_digest(self.model):
            return None
        key = (nimble_pin.tags_url(self.url), normalize_tag(self.model),
               nimble_pin.strip_digest(self.digest))
        hit = nimble_pin.cached(key, self.digest_ttl_s)
        if hit is None:
            if self._tags is None and self._tags_default:
                self._tags = _http_get()
            if self._tags is None:
                hit = (False, "digest_unverified: no /api/tags transport")
            else:
                try:
                    body = self._tags(key[0], min(self.timeout_s, 10.0))
                    hit = nimble_pin.check_tags(body, self.model, key[2])
                except Exception as e:  # noqa: BLE001 - down / bad json: unverified, retried
                    hit = (False, f"digest_unverified: {type(e).__name__}: {e}")
            nimble_pin.remember(key, hit)
            if hit[1]:
                log.warning("Nimble pin refused: %s", hit[1])
        return hit[1]

    def config_problem(self) -> Optional[str]:
        """Why this engine would refuse every request (pin or non-loopback URL), if so.
        No network: the digest check for nimble / nimble:latest happens in warmup()/ask()."""
        if not self.pin_ok:
            return f"NIMBLE_MODEL {self.model!r} is not a Nimble 2 tag; refusing to POST"
        host = urlparse(self.url).hostname or ""
        if not self.allow_remote and host not in ("127.0.0.1", "localhost", "::1"):
            return f"NIMBLE_URL host {host!r} is not loopback; set NIMBLE_ALLOW_REMOTE=1 to allow"
        return None

    def ask(self, state: Any, questions: dict, timeout: Optional[float] = None) -> dict:
        """POST one System One request; returns the response body. Raises NimbleError.

        ``timeout`` is a hard cap: it is passed to the transport AND enforced on the measured
        elapsed time, so a transport that ignores it still cannot yield a late answer.
        """
        problem = self.config_problem()
        if problem:
            raise NimbleError(f"refused: {problem}")
        problem = self.verify_digest()
        if problem:
            raise NimbleError(problem)
        timeout = self.timeout_s if timeout is None else timeout
        payload = {"model": self.model, "state": sanitize(state), "questions": questions,
                   "keep_alive": self.keep_alive}
        try:
            json.dumps(payload, allow_nan=False)
        except (TypeError, ValueError) as e:
            raise NimbleError(f"state not serializable: {e}") from e
        if self._transport is None:
            self._transport = _http_transport()
        t0 = self._clock()
        try:
            body = self._transport(self.url, payload, timeout)
        except NimbleError:
            raise
        except Exception as e:  # noqa: BLE001 - down, timeout, bad json: all fail closed
            raise NimbleError(f"{type(e).__name__}: {e}") from e
        if (self._clock() - t0) > timeout:
            raise NimbleError(f"timeout: answer took longer than {timeout}s", raw=raw_snippet(body))
        if not isinstance(body, dict):
            raise NimbleError("response is not a JSON object", raw=raw_snippet(body))
        return body

    def decide(self, state: dict, timeout: Optional[float] = None) -> dict:
        t0 = self._clock()
        body = None
        try:
            body = self.ask(state, QUESTIONS, timeout)
            out = self._parse(body)
        except NimbleError as e:
            raw = e.raw if e.raw is not None else body
            return _fail(str(e), (self._clock() - t0) * 1000.0, self.model, raw)
        out["latency_ms"] = round((self._clock() - t0) * 1000.0, 2)
        return out

    __call__ = decide

    def warmup(self) -> dict:
        """One tiny decide so the model is resident (keep_alive keeps it loaded).

        Uses its own, much longer timeout: the first call loads the 9B model into memory.
        For nimble / nimble:latest the digest is verified first (cached per process).
        """
        t0 = self._clock()
        problem = self.config_problem() or self.verify_digest()
        if problem:
            return _fail(problem, (self._clock() - t0) * 1000.0, self.model)
        return self.decide({"warmup": True}, timeout=self.warmup_timeout_s)

    def _parse(self, body: dict) -> dict:
        resp_model = body.get("model")
        same_pinned = (needs_digest(self.model) and isinstance(resp_model, str)
                       and normalize_tag(resp_model) == normalize_tag(self.model))
        if resp_model is not None and not is_nimble2(resp_model) and not same_pinned:
            raise NimbleError(f"refused: response model {resp_model!r} is not Nimble 2")
        answers = body.get("answers")
        if not isinstance(answers, dict):
            raise NimbleError("malformed: no answers")
        act = answers.get("action")
        if not isinstance(act, dict) or act.get("choice") not in ACTIONS:
            raise NimbleError(f"malformed: invalid action answer {act!r}"[:200])
        conf = _num(act.get("confidence"))
        if conf is None or not 0.0 <= conf <= 1.0:
            raise NimbleError(f"malformed: confidence {act.get('confidence')!r} not in [0,1]")
        risk = answers.get("risk_score")
        if not isinstance(risk, dict) or risk.get("choice") not in RISK_LABELS:
            raise NimbleError(f"malformed: invalid risk label {risk!r}"[:200])
        return {"action": act["choice"], "confidence": conf, "risk_score": int(risk["choice"]),
                "risk_expected": _risk_expected(risk.get("probabilities")),
                "latency_ms": 0.0, "model": self.model, "raw": body, "error": None}


_DEFAULT: Optional[NimbleEngine] = None


def _default_engine() -> NimbleEngine:
    global _DEFAULT
    if _DEFAULT is None:
        from .config import load_config
        _DEFAULT = engine_from_config(load_config())  # ./.env overlaid by the environment
    return _DEFAULT


def ask_nimble(state: Any, questions: dict, model: Optional[str] = None) -> dict:
    """Module-level convenience: one request using the env-configured default engine."""
    eng = _default_engine()
    if model and model != eng.model:
        eng = NimbleEngine(eng.url, model, eng.timeout_s, eng.keep_alive, eng.allow_remote,
                           eng._transport, warmup_timeout_s=eng.warmup_timeout_s,
                           digest=eng.digest, tags_transport=eng._tags,
                           digest_ttl_s=eng.digest_ttl_s)
    return eng.ask(state, questions)


def decide(state: dict) -> dict:
    """Module-level ``decide(state)`` using env-configured defaults (lazily built)."""
    return _default_engine().decide(state)


def engine_from_config(cfg: "Config", transport=None, clock=time.perf_counter) -> NimbleEngine:
    return NimbleEngine.from_config(cfg, transport, clock)
