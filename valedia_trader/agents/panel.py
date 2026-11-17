"""Decision panel: several deciders and several vetters, pooled by measured track record.

A decider returns the probability that a trade thesis is right. A vetter says whether the trade
should be blocked. Members can be the typed decision model (Elephant-1, the one shared instance on
LM Studio, /v1/systemone), any OpenAI-compatible chat model (local Qwen), or a Hermes profile (scout, decider, auditor). Every
forecast is written to a ledger; when a case settles each member is scored by Brier and its
Darwinian weight moves. The panel never places an order: its output feeds the deterministic risk
officer, which keeps the final veto.

Fail-closed rules: too few valid deciders means no trade; too few valid vetters means vetoed; a
member that errors is never counted as agreement."""
from __future__ import annotations

import json
import math
import re
import sqlite3
import subprocess
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from valedia_trader.agents.darwinian import DarwinianWeights
from valedia_trader.core import decision_model as dm
from valedia_trader.core import legacy

__all__ = ["Verdict", "Member", "CallableMember", "SystemOneMember", "ChatMember", "HermesProfileMember",
           "ForecastLedger", "DecisionPanel", "PanelDecision", "build_panel", "load_roster", "DEFAULT_ROSTER"]

DEFAULT_ROSTER = Path(__file__).with_name("panel_roster.json")
ROLES = ("decider", "vetter", "scout")
PROMPT = ("You are {name}, a {role} on a trading decision panel. Judge the case below using only the facts "
          "given; they were computed in code and are correct. Reply with one JSON object and nothing else: "
          '{{"probability": <0..1 that the thesis is right>, "veto": <true if the trade must be blocked on risk '
          'grounds, else false>, "rationale": "<one sentence>"}}\n\nCASE:\n{case}')


@dataclass(frozen=True)
class Verdict:
    member: str
    role: str
    probability: float | None = None
    veto: bool | None = None
    rationale: str = ""
    latency_ms: float = 0.0
    error: str | None = None

    @property
    def valid(self) -> bool:
        if self.error is not None:
            return False
        if self.role == "decider":
            return self.probability is not None and 0.0 <= self.probability <= 1.0
        if self.role == "vetter":
            return isinstance(self.veto, bool)
        return True


class Member:
    """Base class. Subclasses implement _ask and return (probability, veto, rationale)."""

    def __init__(self, name: str, role: str) -> None:
        if role not in ROLES:
            raise ValueError(f"role must be one of {ROLES}")
        self.name, self.role = name, role

    def _ask(self, case: dict) -> tuple[float | None, bool | None, str]:
        raise NotImplementedError

    def ask(self, case: dict) -> Verdict:
        """Never raises: any failure becomes a Verdict with `error` set."""
        t0 = time.perf_counter()
        try:
            p, veto, why = self._ask(case)
            if p is not None and (isinstance(p, bool) or not math.isfinite(float(p)) or not 0.0 <= float(p) <= 1.0):
                raise ValueError("probability outside [0, 1]")
            return Verdict(self.name, self.role, None if p is None else float(p), veto, str(why)[:400],
                           (time.perf_counter() - t0) * 1e3)
        except Exception as exc:
            return Verdict(self.name, self.role, error=f"{type(exc).__name__}: {str(exc)[:160]}",
                           latency_ms=(time.perf_counter() - t0) * 1e3)


class CallableMember(Member):
    def __init__(self, name: str, role: str, fn: Callable[[dict], tuple]) -> None:
        super().__init__(name, role)
        self._fn = fn

    def _ask(self, case):
        return self._fn(case)


def parse_json_reply(text: str) -> tuple[float | None, bool | None, str]:
    """Extract {"probability", "veto", "rationale"} from a model reply (tolerates code fences and prose)."""
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        raise ValueError("no JSON object in reply")
    obj = json.loads(match.group(0))
    p, veto = obj.get("probability"), obj.get("veto")
    if p is not None and (isinstance(p, bool) or not isinstance(p, (int, float))):
        raise ValueError("probability is not a number")
    if veto is not None and not isinstance(veto, bool):
        raise ValueError("veto is not a boolean")
    return p, veto, str(obj.get("rationale", ""))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_HTTP = urllib.request.build_opener(_NoRedirect)


def _http_json(url: str, payload: dict | None, timeout: float, headers: dict | None = None) -> Any:
    data = None if payload is None else json.dumps(payload, allow_nan=False).encode()
    req = urllib.request.Request(url, data=data, method="GET" if data is None else "POST",
                                 headers={"Content-Type": "application/json", **(headers or {})})
    with _HTTP.open(req, timeout=timeout) as resp:
        raw = resp.read(1_048_577)
    if len(raw) > 1_048_576:
        raise ValueError("oversize response")
    return json.loads(raw)


def _loopback(url: str) -> bool:
    return bool(re.match(r"^http://(127\.0\.0\.1|localhost)(:\d+)?(/|$)", url))


class SystemOneMember(Member):
    """A typed decision on the shared decision model (`core.decision_model`: Elephant-1, one instance on loopback
    LM Studio). One ask reads the model list and, only when it shows exactly one right instance, posts the typed
    request naming that instance. The instance is loaded and kept by the order-book collector's keeper: this member
    never loads or unloads anything, so a missing or wrongly loaded instance is a refusal."""

    LIST_PATH, ASK_PATH, LIST_TIMEOUT = "/api/v1/models", "/v1/systemone", 5.0

    def __init__(self, name: str, role: str, base_url: str = dm.URL, timeout: float = 60.0,
                 transport: Callable[[str, dict | None, float], Any] | None = None) -> None:
        super().__init__(name, role)
        if not _loopback(base_url):
            raise ValueError("System One is loopback-only")
        self.base, self.timeout, self._http = base_url.rstrip("/"), timeout, transport or _http_json

    def _refusal(self) -> str | None:
        """Why the list does not show exactly one right instance named dm.INSTANCE (None when it does), one line."""
        try:
            listing = self._http(self.base + self.LIST_PATH, None, self.LIST_TIMEOUT)
        except Exception as exc:
            return f"the model list at {self.base} did not answer ({type(exc).__name__})"
        try:
            _, found = dm._instances(listing)
        except ValueError as exc:
            return f"the model list at {self.base} is malformed: {exc}"
        if not found:
            return f"{dm.INSTANCE} is not loaded at {self.base}"
        why = dm._wrong_found(found)
        return None if why is None else f"{dm.INSTANCE} is not loaded as it must be: {why}"

    def _ask(self, case):
        v3 = legacy.load("systemone_v3_candidate")
        why = self._refusal()
        if why is not None:
            raise RuntimeError(" ".join(why.split()))
        state = {"create_order": False, "thesis": case.get("thesis", ""), "facts": case.get("facts", {})}
        if self.role == "vetter":
            questions = {"risk": {"type": "noul", "instructions": "Does any fact in state show a breached risk limit, "
                                  "stale data, elevated volatility or event risk?",
                                  "criteria": {"true": "at least one fact in state shows such a problem",
                                               "false": "no fact in state shows such a problem"}}}
        else:
            questions = {"thesis": {"type": "noul", "instructions": "Do the facts in state support the thesis in state?",
                                    "criteria": {"true": "the facts support the thesis", "false": "they do not"}}}
        request = {**v3.make_request(state, questions, tag=v3.TAG), "model": dm.INSTANCE}   # validated, then named
        body = self._http(self.base + self.ASK_PATH, request, self.timeout)
        if not isinstance(body, dict) or body.get("model") != dm.INSTANCE:
            raise RuntimeError(f"the reply is not from the instance {dm.INSTANCE}")
        answers = v3.parse_answers({**body, "model": v3.TAG}, questions)            # the pinned parser's own name
        if self.role == "vetter":
            p_block = answers["risk"]["noul"]
            return p_block, p_block >= 0.5, f"typed probability of a risk problem {p_block:.2f}"
        return answers["thesis"]["noul"], None, "typed probability"


class ChatMember(Member):
    """Any OpenAI-compatible chat endpoint (local Qwen on LM Studio). Loopback unless allowed."""

    def __init__(self, name: str, role: str, base_url: str, model: str, timeout: float = 120.0,
                 allow_remote: bool = False, transport: Callable[..., Any] | None = None) -> None:
        super().__init__(name, role)
        if not allow_remote and not _loopback(base_url):
            raise ValueError("remote chat endpoints must be enabled explicitly")
        self.base, self.model, self.timeout, self._http = base_url.rstrip("/"), model, timeout, transport or _http_json

    def _ask(self, case):
        prompt = PROMPT.format(name=self.name, role=self.role, case=json.dumps(case, sort_keys=True, default=str))
        body = self._http(self.base + "/chat/completions",
                          {"model": self.model, "temperature": 0, "max_tokens": 400, "reasoning_effort": "none",
                           "messages": [{"role": "user", "content": prompt}]}, self.timeout)
        return parse_json_reply(body["choices"][0]["message"]["content"])


class HermesProfileMember(Member):
    """A Hermes profile answering one-shot (`hermes -p <profile> -z <prompt>`), with no tools.

    `toolsets` defaults to "clarify", which Hermes disables in one-shot mode, so the model gets no
    tool definitions and no skills index. Measured on the local 27B: the prompt falls from about
    15,700 tokens to about 1,950 and a cold call from 144 s to 54 s. Pass None to use the profile's
    own toolsets. `reasoning` overrides the profile's reasoning effort for the call."""

    REASONING = ("none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra")

    def __init__(self, name: str, role: str, profile: str, model: str | None = None, timeout: float = 180.0,
                 runner: Callable[[list[str], float], str] | None = None, toolsets: str | None = "clarify",
                 reasoning: str | None = None) -> None:
        super().__init__(name, role)
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", profile):
            raise ValueError("invalid profile name")
        if toolsets is not None and not re.fullmatch(r"[a-z0-9_-]+(,[a-z0-9_-]+)*", toolsets):
            raise ValueError("toolsets must be comma-separated toolset names")
        if reasoning is not None and reasoning not in self.REASONING:
            raise ValueError(f"reasoning must be one of {self.REASONING}")
        self.profile, self.model, self.timeout, self._run = profile, model, timeout, runner or self._subprocess
        self.toolsets, self.reasoning = toolsets, reasoning

    @staticmethod
    def _subprocess(argv: list[str], timeout: float) -> str:
        from valedia_trader.core.secrets import child_env
        out = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL, env=child_env())
        if out.returncode != 0:
            raise RuntimeError(f"hermes exited {out.returncode}")
        return out.stdout

    def _ask(self, case):
        prompt = PROMPT.format(name=self.name, role=self.role, case=json.dumps(case, sort_keys=True, default=str))
        argv = (["hermes", "-p", self.profile, "-z", prompt] + (["-t", self.toolsets] if self.toolsets else [])
                + (["--reasoning", self.reasoning] if self.reasoning else []) + (["-m", self.model] if self.model else []))
        return parse_json_reply(self._run(argv, self.timeout))


class ForecastLedger:
    """Every forecast and outcome, so each member has a measurable Brier score."""

    def __init__(self, path: str = ":memory:") -> None:
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.executescript(
            "CREATE TABLE IF NOT EXISTS forecasts (case_id TEXT, member TEXT, role TEXT, probability REAL, veto INTEGER, "
            "error TEXT, ts REAL, PRIMARY KEY (case_id, member));"
            "CREATE TABLE IF NOT EXISTS outcomes (case_id TEXT PRIMARY KEY, outcome INTEGER, ts REAL);")

    def record(self, case_id: str, verdicts: Sequence[Verdict]) -> None:
        self._db.executemany("INSERT OR REPLACE INTO forecasts VALUES (?,?,?,?,?,?,?)",
                             [(case_id, v.member, v.role, v.probability, None if v.veto is None else int(v.veto), v.error,
                               time.time()) for v in verdicts])
        self._db.commit()

    def settle(self, case_id: str, outcome: int) -> None:
        if outcome not in (0, 1):
            raise ValueError("outcome must be 0 (thesis wrong) or 1 (thesis right)")
        self._db.execute("INSERT OR REPLACE INTO outcomes VALUES (?,?,?)", (case_id, outcome, time.time()))
        self._db.commit()

    def brier(self, min_settled: int = 1) -> dict[str, float]:
        """Mean Brier score per member over settled cases. A decider forecasts the outcome; a vetter's
        probability is its probability of blocking, scored against the thesis being wrong."""
        rows = self._db.execute(
            "SELECT f.member, f.role, f.probability, o.outcome FROM forecasts f JOIN outcomes o USING (case_id) "
            "WHERE f.error IS NULL AND f.probability IS NOT NULL").fetchall()
        acc: dict[str, list[float]] = {}
        for member, role, p, outcome in rows:
            target = outcome if role == "decider" else 1 - outcome
            acc.setdefault(member, []).append((p - target) ** 2)
        return {m: sum(v) / len(v) for m, v in acc.items() if len(v) >= min_settled}

    def counts(self) -> dict[str, int]:
        return dict(self._db.execute("SELECT member, COUNT(*) FROM forecasts GROUP BY member").fetchall())

    def close(self) -> None:
        self._db.close()


@dataclass(frozen=True)
class PanelDecision:
    approve: bool
    probability: float | None
    vetoed: bool
    reasons: tuple[str, ...]
    verdicts: tuple[Verdict, ...]
    dissent: float            # spread of decider probabilities (0 = unanimous)


class DecisionPanel:
    def __init__(self, members: Sequence[Member], darwin: DarwinianWeights | None = None,
                 ledger: ForecastLedger | None = None, approve_threshold: float = 0.55, min_deciders: int = 2,
                 min_vetters: int = 1, veto_rule: str = "any", max_workers: int = 6) -> None:
        names = [m.name for m in members]
        if len(set(names)) != len(names):
            raise ValueError("member names must be unique")
        if veto_rule not in ("any", "majority"):
            raise ValueError("veto_rule must be 'any' or 'majority'")
        if not 0.5 <= approve_threshold < 1.0 or min_deciders < 1 or min_vetters < 0:
            raise ValueError("invalid thresholds")
        self.members = list(members)
        voting = [m.name for m in members if m.role != "scout"]
        self.darwin = darwin or DarwinianWeights(voting)
        self.ledger = ledger or ForecastLedger()
        self.threshold, self.min_deciders, self.min_vetters = approve_threshold, min_deciders, min_vetters
        self.veto_rule, self.max_workers = veto_rule, max_workers

    def _ask_all(self, members: Sequence[Member], case: dict) -> list[Verdict]:
        if not members:
            return []
        with ThreadPoolExecutor(max_workers=min(self.max_workers, len(members))) as pool:
            return list(pool.map(lambda m: m.ask(case), members))

    def evaluate(self, case_id: str, case: dict) -> PanelDecision:
        case = dict(case)
        scouts = self._ask_all([m for m in self.members if m.role == "scout"], case)
        briefs = [f"{v.member}: {v.rationale}" for v in scouts if v.valid and v.rationale]
        if briefs:
            case["scout_briefs"] = briefs
        verdicts = self._ask_all([m for m in self.members if m.role != "scout"], case)
        self.ledger.record(case_id, verdicts)
        weights = self.darwin.weights
        reasons: list[str] = []
        deciders = [v for v in verdicts if v.role == "decider" and v.valid]
        vetters = [v for v in verdicts if v.role == "vetter" and v.valid]
        failed = [v.member for v in verdicts if not v.valid]
        if failed:
            reasons.append("no usable answer from: " + ", ".join(failed))
        probability, dissent = None, 0.0
        if len(deciders) < self.min_deciders:
            reasons.append(f"only {len(deciders)} valid decider(s); need {self.min_deciders}")
        else:
            w = [weights.get(v.member, 1.0) for v in deciders]
            logits = [math.log(min(max(v.probability, 1e-6), 1 - 1e-6) / (1 - min(max(v.probability, 1e-6), 1 - 1e-6)))
                      for v in deciders]
            pooled = sum(a * b for a, b in zip(w, logits)) / sum(w)
            probability = 1.0 / (1.0 + math.exp(-pooled))
            ps = [v.probability for v in deciders]
            dissent = max(ps) - min(ps)
        vetoed = False
        if len(vetters) < self.min_vetters:
            vetoed = True
            reasons.append(f"only {len(vetters)} valid vetter(s); need {self.min_vetters}")
        elif vetters:
            if self.veto_rule == "any":
                blockers = [v.member for v in vetters if v.veto]
                vetoed = bool(blockers)
            else:
                total = sum(weights.get(v.member, 1.0) for v in vetters)
                against = sum(weights.get(v.member, 1.0) for v in vetters if v.veto)
                blockers = [v.member for v in vetters if v.veto]
                vetoed = against > 0.5 * total
            if vetoed:
                reasons.append("vetoed by: " + ", ".join(blockers))
        approve = bool(probability is not None and not vetoed and probability >= self.threshold)
        if probability is not None and not vetoed and not approve:
            reasons.append(f"pooled probability {probability:.3f} below {self.threshold:.2f}")
        return PanelDecision(approve, probability, vetoed, tuple(reasons), tuple(scouts) + tuple(verdicts), dissent)

    def settle(self, case_id: str, outcome: int, min_settled: int = 5) -> dict[str, float]:
        """Record the outcome, then run one Darwinian round on negative Brier scores. Returns weights."""
        self.ledger.settle(case_id, outcome)
        scores = {m: -b for m, b in self.ledger.brier(min_settled).items()}
        return self.darwin.update(scores)


def load_roster(path: str | Path = DEFAULT_ROSTER) -> dict:
    return json.loads(Path(path).read_text())


def build_panel(roster: dict | None = None, ledger_path: str = ":memory:") -> DecisionPanel:
    """Build a panel from a roster: {"members": [{"name", "role", "backend", ...}], thresholds...}."""
    roster = load_roster() if roster is None else roster
    members: list[Member] = []
    for spec in roster["members"]:
        if not spec.get("enabled", True):
            continue
        name, role, backend = spec["name"], spec["role"], spec["backend"]
        if backend == "systemone":
            members.append(SystemOneMember(name, role, spec.get("base_url", dm.URL), spec.get("timeout", 60.0)))
        elif backend == "chat":
            members.append(ChatMember(name, role, spec["base_url"], spec["model"], spec.get("timeout", 120.0),
                                      spec.get("allow_remote", False)))
        elif backend == "hermes":
            members.append(HermesProfileMember(name, role, spec["profile"], spec.get("model"), spec.get("timeout", 180.0),
                                               toolsets=spec.get("toolsets", "clarify"), reasoning=spec.get("reasoning")))
        else:
            raise ValueError(f"unknown backend '{backend}' for member '{name}'")
    return DecisionPanel(members, ledger=ForecastLedger(ledger_path),
                         approve_threshold=roster.get("approve_threshold", 0.55),
                         min_deciders=roster.get("min_deciders", 2), min_vetters=roster.get("min_vetters", 1),
                         veto_rule=roster.get("veto_rule", "any"))
