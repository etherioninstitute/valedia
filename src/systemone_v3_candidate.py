"""Exact Ollama 0.40.5 /v1/systemone v3 pin and typed contract.

Source: ollama/ollama v0.40.5 api/types.go, decision/types.go and
 decision/systemone.go; official nimble-v3:9b-mxfp8 registry manifest.
No network, model load, order, trading decision, or database side effect here.
"""
from __future__ import annotations

import math
import re

TAG = "nimble-v3:9b-mxfp8"
MANIFEST_SHA256 = "d14c05dc038c325c73c7f1badd5214cee01fa16cfb24be2a030b4bc0418e1200"
MIN_VERSION = (0, 40, 4)


class ContractError(ValueError):
    pass


def version_ok(version):
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:[-+][A-Za-z0-9.-]+)?", str(version))
    return bool(match and tuple(map(int, match.groups())) >= MIN_VERSION)


def pinned_tag(tags, version):
    if not version_ok(version) or not isinstance(tags, dict) or not isinstance(tags.get("models"), list):
        return ""
    for row in tags["models"]:
        if isinstance(row, dict) and row.get("name") == TAG:
            digest = row.get("digest")
            if isinstance(digest, str) and digest.lower().removeprefix("sha256:") == MANIFEST_SHA256:
                return TAG
    return ""


def make_request(state, questions, *, tag):
    if tag != TAG or not isinstance(state, (str, dict, list)) or not state or not isinstance(questions, dict) or not questions:
        raise ContractError("invalid model, state or questions")
    for name, question in questions.items():
        if not isinstance(name, str) or not name or not isinstance(question, dict):
            raise ContractError("invalid question")
        kind, criteria = question.get("type"), question.get("criteria")
        if not isinstance(question.get("instructions"), (str, dict, list)) or not question["instructions"]:
            raise ContractError("missing instructions")
        if kind == "choice" and (not isinstance(criteria, dict) or len(criteria) < 2 or
                                  any(not isinstance(k, str) or not k or v is not None and not isinstance(v, str)
                                      for k, v in criteria.items())):
            raise ContractError("invalid choice criteria")
        if kind == "score" and (not isinstance(criteria, list) or len(criteria) < 2 or
                                 any(not isinstance(v, str) for v in criteria)):
            raise ContractError("invalid score rubric")
        if kind == "noul" and (not isinstance(criteria, dict) or
                                any(k not in ("true", "false") or not isinstance(v, str) for k, v in criteria.items())):
            raise ContractError("invalid noul criteria")
        if kind not in ("choice", "score", "noul"):
            raise ContractError("unsupported question type")
    return {"model": tag, "state": state, "questions": questions}


def _prob(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ContractError("invalid probability/confidence")
    return float(value)


def _distribution(value, keys):
    if not isinstance(value, dict) or list(value) != list(keys):
        raise ContractError("probability keys/order differ")
    probs = {k: _prob(value[k]) for k in keys}
    if not math.isclose(sum(probs.values()), 1.0, abs_tol=0.001, rel_tol=0):
        raise ContractError("probabilities not normalized")
    return probs


def parse_response(body, questions):
    """Return typed answers + usage, not inferred confidence or calibration."""
    if not isinstance(body, dict) or body.get("model") != TAG or not isinstance(body.get("answers"), dict) or not isinstance(questions, dict):
        raise ContractError("wrong model or missing answers")
    answers = body["answers"]
    if list(answers) != list(questions):
        raise ContractError("question keys/order differ")
    usage = body.get("usage")
    if not isinstance(usage, dict) or any(type(usage.get(k)) is not int or usage[k] < 0 for k in ("input_tokens", "output_tokens")):
        raise ContractError("invalid usage")
    result = {}
    for name, spec in questions.items():
        answer = answers[name]
        if not isinstance(answer, dict) or answer.get("type") != spec.get("type"):
            raise ContractError("answer type mismatch")
        kind = spec["type"]
        if kind == "noul":
            result[name] = {"type": kind, "noul": _prob(answer.get("noul"))}
        elif kind == "choice":
            keys = list(spec["criteria"])
            probs = _distribution(answer.get("probabilities"), keys)
            choice = answer.get("choice")
            if not isinstance(choice, str) or choice not in keys or probs[choice] < max(probs.values()) - 0.001:
                raise ContractError("inconsistent choice")
            result[name] = {"type": kind, "choice": choice, "probabilities": probs,
                            "confidence": _prob(answer.get("confidence"))}
        elif kind == "score":
            keys = [str(i) for i in range(len(spec["criteria"]))]
            probs = _distribution(answer.get("probabilities"), keys)
            legend = answer.get("legend")
            if not isinstance(legend, dict) or list(legend) != keys or [legend[k] for k in keys] != spec["criteria"]:
                raise ContractError("score legend mismatch")
            score = answer.get("score")
            if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
                raise ContractError("invalid score")
            if not math.isclose(score, sum(i * probs[str(i)] for i in range(len(keys))), abs_tol=0.003, rel_tol=0):
                raise ContractError("score inconsistent with rubric probabilities")
            result[name] = {"type": kind, "score": float(score), "legend": dict(legend),
                            "probabilities": probs, "confidence": _prob(answer.get("confidence"))}
        else:
            raise ContractError("unsupported answer type")
    return {"model": TAG, "answers": result,
            "usage": {"input_tokens": usage["input_tokens"], "output_tokens": usage["output_tokens"]}}


def parse_answers(body, questions):
    return parse_response(body, questions)["answers"]
