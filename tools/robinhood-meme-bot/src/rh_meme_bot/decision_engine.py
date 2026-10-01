"""Nimble 2 decision engine (local Ollama System One endpoint).

Principle: the model answers narrow qualitative questions about precomputed facts. It never
computes or sees any threshold; every number is enforced afterwards by gates.py.
Fail-closed: any problem (down, timeout, refused pin, malformed answers) yields a skip.
"""
from __future__ import annotations

import json
import math
import re
import time
from decimal import Decimal
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from .config import DEFAULT_NIMBLE_URL, Config

# --- Nimble 2 pin rule ----------------------------------------------------------------
# Copied from Valedia; source of truth: scripts/integrations/systemone_hotpath_client.is_nimble2
# (this project must stay standalone, so it is duplicated here, not imported).
_TAG = r"[a-z0-9][a-z0-9._-]*"
_QUANT = r"(?:q\d[a-z0-9_]*|iq\d[a-z0-9_]*|fp16|fp32|bf16|f16|f32|int4|int8)"
_V2_RE = re.compile(
    r"nimble2(?::" + _TAG + r")?"
    r"|nimble:(?:2|v2|9b-v2)(?:-" + _QUANT + r")?"
    r"|(?:(?:hf\.co/)?bespokelabs/)?bespoke-nimble-9b-v2(?::" + _TAG + r")?"
)
_V2_BANNED = ("v1", "tev", "0.8")


def is_nimble2(name) -> bool:
    """True for a Nimble 2 tag only (case-insensitive). Never v1, never Tev."""
    low = str(name or "").strip().lower()
    if not low or any(bad in low for bad in _V2_BANNED):
        return False
    return _V2_RE.fullmatch(low) is not None


# --- Questions -------------------------------------------------------------------------
ACTIONS = ("buy", "hold", "skip")
RISK_LABELS = [str(i) for i in range(1, 11)]

# At pool creation there is NO momentum or buy-pressure data, so the criteria only talk about
# facts the state really contains; asking about momentum would invite guessing. No threshold
# or number appears in any question text: the code gates enforce every number afterwards.
# Risk is a 10-way "choice" instead of Ollama's "score" type: the score index scale is
# ambiguous (0- vs 1-based) and an off-by-one would silently move the risk gate. With choice
# labels "1".."10", int(choice) is unambiguous and the probabilities give a distribution.
QUESTIONS = {
    "action": {
        "type": "choice",
        "instructions": ("A new token pool was just created. Using only the facts in the state, "
                         "should a small paper position be opened right now?"),
        "criteria": {
            "buy": ("Facts look like a legitimate launch: real contract code, ownership "
                    "renounced, meaningful two-sided liquidity, plausible supply and price"),
            "hold": "Facts are mixed, incomplete, or too early to judge",
            "skip": ("Red flags: no contract code, active owner, one-sided or tiny liquidity, "
                     "absurd supply or price"),
        },
    },
    "risk_score": {
        "type": "choice",
        "instructions": ("Rate the rug-pull and scam risk of this launch from 1 (safe) to 10 "
                         "(extreme), using only the facts in the state."),
        "criteria": {
            "1": "no red flags at all", "2": "negligible concerns", "3": "minor concerns",
            "4": "some concerns but plausible", "5": "mixed picture", "6": "several red flags",
            "7": "serious red flags", "8": "many serious red flags",
            "9": "almost certainly a scam", "10": "near-certain rug pull",
        },
    },
}


class NimbleError(Exception):
    pass


def sanitize(obj: Any) -> Any:
    """JSON-safe copy: NaN/inf -> None, Decimal -> float, tuples -> lists, keys -> str."""
    if isinstance(obj, bool) or obj is None or isinstance(obj, (int, str)):
        return obj
    if isinstance(obj, Decimal):
        obj = float(obj)
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [sanitize(v) for v in obj]
    return str(obj)


def _http_transport() -> Callable[[str, dict, float], dict]:
    import requests  # persistent connection to the local Ollama

    session = requests.Session()

    def post(url: str, payload: dict, timeout: float) -> dict:
        resp = session.post(url, data=json.dumps(payload, allow_nan=False), timeout=timeout,
                            headers={"Content-Type": "application/json"})
        if resp.status_code != 200:
            raise NimbleError(f"http {resp.status_code}: {resp.text[:120]}")
        return resp.json()

    return post


def _fail(reason: str, ms: float, model: str, raw: Any = None) -> dict:
    return {"action": "skip", "confidence": 0.0, "risk_score": None, "risk_expected": None,
            "latency_ms": round(ms, 2), "model": model, "raw": raw, "error": reason}


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v)


class NimbleEngine:
    def __init__(self, url: str = DEFAULT_NIMBLE_URL, model: str = "nimble2",
                 timeout_s: float = 2.0, keep_alive: str = "30m", allow_remote: bool = False,
                 transport: Optional[Callable[[str, dict, float], dict]] = None,
                 clock: Callable[[], float] = time.perf_counter):
        self.url, self.model, self.timeout_s = url, model, timeout_s
        self.keep_alive, self.allow_remote = keep_alive, allow_remote
        self._transport, self._clock = transport, clock

    @classmethod
    def from_config(cls, cfg: Config, transport=None, clock=time.perf_counter) -> "NimbleEngine":
        return cls(cfg.nimble_url, cfg.nimble_model, cfg.decision_timeout_s,
                   cfg.nimble_keep_alive, cfg.nimble_allow_remote, transport, clock)

    @property
    def pin_ok(self) -> bool:
        return is_nimble2(self.model)

    def config_problem(self) -> Optional[str]:
        """Why this engine would refuse every request (pin or non-loopback URL), if so."""
        if not self.pin_ok:
            return f"NIMBLE_MODEL {self.model!r} is not a Nimble 2 tag; refusing to POST"
        host = urlparse(self.url).hostname or ""
        if not self.allow_remote and host not in ("127.0.0.1", "localhost", "::1"):
            return f"NIMBLE_URL host {host!r} is not loopback; set NIMBLE_ALLOW_REMOTE=1 to allow"
        return None

    def ask(self, state: Any, questions: dict) -> dict:
        """POST one System One request; returns the response body. Raises NimbleError."""
        problem = self.config_problem()
        if problem:
            raise NimbleError(f"refused: {problem}")
        payload = {"model": self.model, "state": sanitize(state), "questions": questions,
                   "keep_alive": self.keep_alive}
        try:
            json.dumps(payload, allow_nan=False)
        except (TypeError, ValueError) as e:
            raise NimbleError(f"state not serializable: {e}") from e
        if self._transport is None:
            self._transport = _http_transport()
        try:
            body = self._transport(self.url, payload, self.timeout_s)
        except NimbleError:
            raise
        except Exception as e:  # noqa: BLE001 - down, timeout, bad json: all fail closed
            raise NimbleError(f"{type(e).__name__}: {e}") from e
        if not isinstance(body, dict):
            raise NimbleError("response is not a JSON object")
        return body

    def decide(self, state: dict) -> dict:
        t0 = self._clock()
        body = None
        try:
            body = self.ask(state, QUESTIONS)
            out = self._parse(body)
        except NimbleError as e:
            return _fail(str(e), (self._clock() - t0) * 1000.0, self.model, body)
        out["latency_ms"] = round((self._clock() - t0) * 1000.0, 2)
        return out

    __call__ = decide

    def warmup(self) -> dict:
        """One tiny decide so the model is resident (keep_alive keeps it loaded)."""
        return self.decide({"warmup": True})

    def _parse(self, body: dict) -> dict:
        resp_model = body.get("model")
        if resp_model is not None and not is_nimble2(resp_model):
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


def _risk_expected(probs: Any) -> Optional[float]:
    """sum(k * p_k) over the risk labels (renormalised); None if unusable."""
    if not isinstance(probs, dict) or not probs:
        return None
    total = acc = 0.0
    for label, p in probs.items():
        pv = _num(p)
        if label not in RISK_LABELS or pv is None or pv < 0:
            return None
        total += pv
        acc += int(label) * pv
    return round(acc / total, 4) if total > 0 else None


_DEFAULT: Optional[NimbleEngine] = None


def _default_engine() -> NimbleEngine:
    global _DEFAULT
    if _DEFAULT is None:
        from .config import load_config
        _DEFAULT = engine_from_config(load_config(env_file=None))
    return _DEFAULT


def ask_nimble(state: Any, questions: dict, model: Optional[str] = None) -> dict:
    """Module-level convenience: one request using the env-configured default engine."""
    eng = _default_engine()
    if model and model != eng.model:
        eng = NimbleEngine(eng.url, model, eng.timeout_s, eng.keep_alive, eng.allow_remote,
                           eng._transport)
    return eng.ask(state, questions)


def decide(state: dict) -> dict:
    """Module-level ``decide(state)`` using env-configured defaults (lazily built)."""
    return _default_engine().decide(state)


def engine_from_config(cfg: Config, transport=None, clock=time.perf_counter) -> NimbleEngine:
    return NimbleEngine.from_config(cfg, transport, clock)
