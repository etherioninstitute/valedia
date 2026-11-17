"""Scoped fakes of the shared Elephant-1 instance on LM Studio for System One tests (not a test module).

Builds model lists from the shared module's own constants (reached through systemone_pin, by file), typed
replies that the pinned parser accepts, and a scope that installs a fake list getter (`_get`) and a fake typed
transport (`_post`) on the client plus a per-test log path, and restores all of them afterwards, so combined
pytest runs cannot leak a fake between modules. Outside a reply the scope's `_post` refuses, so no request ever
leaves the process. Script mode: call setup() first and teardown() last.
"""
import copy
import os

import systemone_pin as _pin

DM = _pin.dm


def instance(**cfg):
    """A loaded instance named INSTANCE with the required settings (cfg overrides them)."""
    conf = {"context_length": DM.CONTEXT, "parallel": DM.SLOTS,
            "physical_batch_size": DM.PHYSICAL_BATCH, "eval_batch_size": DM.EVAL_BATCH}
    conf.update(cfg)
    return {"id": DM.INSTANCE, "config": conf}


def entry(instances=None, **over):
    """The model's list entry, right in every field (over overrides any)."""
    e = {"key": DM.KEY, "type": "decision", "decision_type": DM.DECISION_TYPE,
         "publisher": DM.PUBLISHER, "quantization": {"name": DM.QUANT},
         "size_bytes": DM.LISTED_SIZES[0],
         "loaded_instances": [instance()] if instances is None else instances}
    e.update(over)
    return e


def listing(*entries):
    """`GET /api/v1/models` body; no argument: the one right instance."""
    return {"models": list(entries) if entries else [entry()]}


def _choice(keys, pick, p):
    if isinstance(pick, dict):
        probs = {k: float(pick.get(k, 0.0)) for k in keys}
        tot = sum(probs.values()) or 1.0
        probs = {k: v / tot for k, v in probs.items()}
        return max(keys, key=lambda k: probs[k]), probs
    chosen = pick if pick in keys else keys[0]
    rest = (1.0 - p) / (len(keys) - 1)
    return chosen, {k: (p if k == chosen else rest) for k in keys}


def typed_reply(questions, picks=None, *, model=None, confidence=0.5, p=0.8):
    """A reply the pinned parser accepts for these questions. picks: {name: choice key | {key: prob} |
    noul float | score index}. model defaults to the instance name."""
    picks = picks or {}
    answers = {}
    for name, q in questions.items():
        kind, pick = q["type"], picks.get(name)
        if kind == "noul":
            answers[name] = {"type": "noul", "noul": p if pick is None else float(pick)}
        elif kind == "choice":
            chosen, probs = _choice(list(q["criteria"]), pick, p)
            answers[name] = {"type": "choice", "choice": chosen, "probabilities": probs,
                             "confidence": confidence}
        else:
            keys = [str(i) for i in range(len(q["criteria"]))]
            idx = keys[0] if pick is None else str(int(pick))
            probs = {k: (1.0 if k == idx else 0.0) for k in keys}
            answers[name] = {"type": "score", "score": float(int(idx)),
                             "legend": {k: q["criteria"][int(k)] for k in keys},
                             "probabilities": probs, "confidence": confidence}
    return {"model": DM.INSTANCE if model is None else model, "answers": answers,
            "usage": {"input_tokens": 10, "output_tokens": 2}}


def typed_post(picks=None, seen=None, **kw):
    """A fake `_post(url, payload, timeout)` that records each call and answers in the typed shape."""
    def post(url, payload, timeout):
        if seen is not None:
            seen.append({"url": url, "payload": copy.deepcopy(payload), "timeout": timeout})
        return typed_reply(payload["questions"], picks, **kw)
    return post


def refuse_post(url, payload, timeout):
    """The scope's default transport: nothing listens in tests."""
    raise ConnectionRefusedError("no server in tests")


def list_get(body=None, seen=None, list_url=None):
    """A fake `_get(url, timeout)` serving this model list at the list URL only."""
    want = list_url or _pin.LIST_URL

    def get(url, timeout):
        if seen is not None:
            seen.append(url)
        if url != want:
            raise AssertionError("unexpected url %s" % url)
        return copy.deepcopy(listing() if body is None else body)
    return get


class Scoped:
    def __init__(self, s1, log):
        self.s1, self.log, self._saved = s1, log, (None, None, None)
        self.posts, self.gets = [], []

    def use(self, body=None):
        """Serve this model list (default: the one right instance) and drop the check cache."""
        self.s1._get = list_get(body, self.gets)
        self.s1._reset_check()

    def reply(self, picks=None, **kw):
        """Answer typed calls through the module's own transport; calls land in self.posts."""
        self.s1._post = typed_post(picks, self.posts, **kw)

    def setup(self):
        self._saved = (self.s1._get, self.s1._post, os.environ.get("VALEDIA_SYSTEMONE_LOG"))
        os.environ["VALEDIA_SYSTEMONE_LOG"] = self.log
        self.posts, self.gets = [], []
        self.s1._post = refuse_post
        self.use()

    def teardown(self):
        self.s1._get, self.s1._post, old = self._saved
        os.environ.pop("VALEDIA_SYSTEMONE_LOG", None)
        if old is not None:
            os.environ["VALEDIA_SYSTEMONE_LOG"] = old
        self.s1._reset_check()

    def fixture(self):
        """Autouse pytest fixture (None when pytest is absent: script mode)."""
        try:
            import pytest
        except ImportError:
            return None

        @pytest.fixture(autouse=True)
        def _scoped_fakes():
            self.setup()
            yield
            self.teardown()
        return _scoped_fakes
