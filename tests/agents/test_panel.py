import json
import math

import pytest

from valedia_trader.agents import panel as pn
from valedia_trader.core import decision_model as dm
from valedia_trader.core import legacy


def fixed(name, role, p=None, veto=None, why="ok"):
    return pn.CallableMember(name, role, lambda case: (p, veto, why))


def boom(name, role):
    def fail(case):
        raise TimeoutError("model did not answer")
    return pn.CallableMember(name, role, fail)


CASE = {"thesis": "SPY closes higher tomorrow", "facts": {"trend_up": True, "event_risk": False}}


def test_log_odds_pooling_and_approval():
    panel = pn.DecisionPanel([fixed("a", "decider", 0.70), fixed("b", "decider", 0.60), fixed("v", "vetter", 0.1, False)])
    d = panel.evaluate("c1", CASE)
    logit = lambda p: math.log(p / (1 - p))
    assert d.probability == pytest.approx(1 / (1 + math.exp(-(logit(0.7) + logit(0.6)) / 2)))
    assert d.approve and not d.vetoed and d.dissent == pytest.approx(0.10) and d.reasons == ()
    low = pn.DecisionPanel([fixed("a", "decider", 0.52), fixed("b", "decider", 0.50), fixed("v", "vetter", 0.1, False)])
    out = low.evaluate("c2", CASE)
    assert not out.approve and "below 0.55" in out.reasons[0]


def test_darwinian_weights_tilt_the_pool():
    members = [fixed("good", "decider", 0.9), fixed("bad", "decider", 0.2), fixed("v", "vetter", 0.1, False)]
    even = pn.DecisionPanel(members).evaluate("c", CASE).probability
    panel = pn.DecisionPanel(members)
    panel.darwin.weights.update({"good": 2.5, "bad": 0.3})
    assert panel.evaluate("c", CASE).probability > even


def test_quorum_and_veto_rules_fail_closed():
    one = pn.DecisionPanel([fixed("a", "decider", 0.9), boom("b", "decider"), fixed("v", "vetter", 0.1, False)])
    d = one.evaluate("c", CASE)
    assert not d.approve and d.probability is None and "no usable answer from: b" in d.reasons[0] and "need 2" in d.reasons[1]
    no_vetter = pn.DecisionPanel([fixed("a", "decider", 0.9), fixed("b", "decider", 0.9), boom("v", "vetter")])
    d = no_vetter.evaluate("c", CASE)
    assert d.vetoed and not d.approve                       # a silent vetter is not consent
    any_rule = pn.DecisionPanel([fixed("a", "decider", 0.9), fixed("b", "decider", 0.9), fixed("v1", "vetter", 0.9, True),
                                 fixed("v2", "vetter", 0.1, False), fixed("v3", "vetter", 0.1, False)])
    assert any_rule.evaluate("c", CASE).vetoed
    majority = pn.DecisionPanel(any_rule.members, veto_rule="majority")
    assert not majority.evaluate("c", CASE).vetoed and majority.evaluate("c", CASE).approve
    with pytest.raises(ValueError):
        pn.DecisionPanel([fixed("a", "decider", 0.9), fixed("a", "decider", 0.8)])
    bad = pn.DecisionPanel([fixed("a", "decider", 1.7), fixed("b", "decider", 0.8), fixed("v", "vetter", 0.1, False)])
    assert not bad.evaluate("c", CASE).approve              # an out-of-range probability is an error, not a vote


def test_scout_brief_reaches_the_voters():
    seen = {}
    def decider(case):
        seen.update(case)
        return 0.8, None, "ok"
    panel = pn.DecisionPanel([fixed("scout", "scout", why="CPI print tomorrow 08:30"), pn.CallableMember("a", "decider", decider),
                              fixed("b", "decider", 0.8), fixed("v", "vetter", 0.1, False)])
    panel.evaluate("c", CASE)
    assert seen["scout_briefs"] == ["scout: CPI print tomorrow 08:30"] and "scout" not in panel.darwin.weights


def test_ledger_brier_and_darwinian_learning(tmp_path):
    ledger = pn.ForecastLedger(str(tmp_path / "panel.sqlite"))
    members = [fixed("sharp", "decider", 0.85), fixed("coin", "decider", 0.5), fixed("wrong", "decider", 0.1),
               fixed("contra", "decider", 0.3), fixed("v", "vetter", 0.1, False)]
    panel = pn.DecisionPanel(members, ledger=ledger)
    for i in range(12):
        panel.evaluate(f"case{i}", CASE)
        weights = panel.settle(f"case{i}", 1)               # the thesis keeps being right
    b = ledger.brier()
    assert b["sharp"] == pytest.approx(0.15**2) and b["wrong"] == pytest.approx(0.9**2) and b["v"] == pytest.approx(0.1**2)
    assert weights["sharp"] > 1.0 > weights["wrong"] and weights["coin"] == 1.0
    assert ledger.counts()["sharp"] == 12
    with pytest.raises(ValueError):
        ledger.settle("case0", 2)


def test_reply_parsing():
    assert pn.parse_json_reply('```json\n{"probability": 0.62, "veto": false, "rationale": "trend"}\n```') == (0.62, False, "trend")
    assert pn.parse_json_reply('Sure. {"probability": 1, "veto": true}')[:2] == (1, True)
    for bad in ("no json here", '{"probability": "high"}', '{"probability": 0.5, "veto": "no"}'):
        with pytest.raises(ValueError):
            pn.parse_json_reply(bad)


def test_chat_and_hermes_members_use_their_transports():
    calls = []
    def http(url, payload, timeout, headers=None):
        calls.append((url, payload))
        return {"choices": [{"message": {"content": '{"probability": 0.7, "veto": false, "rationale": "r"}'}}]}
    chat = pn.ChatMember("qwen", "decider", "http://127.0.0.1:1235/v1", "qwen3-8b", transport=http)
    v = chat.ask(CASE)
    assert v.valid and v.probability == 0.7 and calls[0][0].endswith("/chat/completions") and calls[0][1]["model"] == "qwen3-8b"
    assert "SPY closes higher" in calls[0][1]["messages"][0]["content"]
    with pytest.raises(ValueError):
        pn.ChatMember("x", "decider", "https://api.example.com/v1", "m")   # remote needs an explicit opt-in
    argv_seen = []
    hermes = pn.HermesProfileMember("auditor", "vetter", "valedia-trading-auditor",
                                    runner=lambda argv, t: argv_seen.append(argv) or '{"probability": 0.9, "veto": true, "rationale": "event risk"}')
    v = hermes.ask(CASE)
    assert v.veto is True and argv_seen[0][:4] == ["hermes", "-p", "valedia-trading-auditor", "-z"]
    with pytest.raises(ValueError):
        pn.HermesProfileMember("x", "vetter", "bad profile; rm -rf")
    assert argv_seen[0][-2:] == ["-t", "clarify"]                      # one-shot with no tools by default
    seen: list = []
    tuned = pn.HermesProfileMember("s", "scout", "p", model="m1", toolsets=None, reasoning="none",
                                   runner=lambda argv, t: seen.append(argv) or '{"probability": 0.5, "veto": false, "rationale": "x"}')
    assert tuned.ask(CASE).probability == 0.5 and "-t" not in seen[0] and seen[0][-4:] == ["--reasoning", "none", "-m", "m1"]
    for bad in ({"toolsets": "web; rm -rf"}, {"toolsets": ""}, {"reasoning": "extreme"}):
        with pytest.raises(ValueError):
            pn.HermesProfileMember("x", "vetter", "p", **bad)
    built = pn.build_panel({"members": [{"name": "h", "role": "decider", "backend": "hermes", "profile": "p", "reasoning": "low"},
                                        {"name": "h2", "role": "vetter", "backend": "hermes", "profile": "p", "toolsets": None}]})
    by_name = {m.name: m for m in built.members}
    assert (by_name["h"].toolsets, by_name["h"].reasoning, by_name["h2"].toolsets) == ("clarify", "low", None)
    silent = pn.HermesProfileMember("d", "decider", "p", runner=lambda argv, t: "I cannot answer")
    assert not silent.ask(CASE).valid


# ---- T44-2 ruling 1: the typed seats ask the shared decision model (Elephant-1 on LM Studio), never load anything

LIST_PATH, ASK_PATH = "/api/v1/models", "/v1/systemone"


def right_instance(**config):
    return {"id": dm.INSTANCE, "config": {"context_length": dm.CONTEXT, "parallel": dm.SLOTS,
                                          "physical_batch_size": dm.PHYSICAL_BATCH, "eval_batch_size": dm.EVAL_BATCH,
                                          **config}}


def right_entry(instances=None, **over):
    return {"key": dm.KEY, "type": "decision", "decision_type": dm.DECISION_TYPE, "publisher": dm.PUBLISHER,
            "quantization": {"name": dm.QUANT}, "size_bytes": dm.LISTED_SIZES[0],
            "loaded_instances": [right_instance()] if instances is None else instances, **over}


OTHER = {"key": "qwen/qwen3.8-27b", "type": "llm", "loaded_instances": [{"id": "qwen/qwen3.8-27b", "config": {}}]}
RIGHT_LIST = {"models": [OTHER, right_entry()]}


class FakeLMStudio:
    """LM Studio on loopback, as the member sees it through its transport. Records every request."""

    def __init__(self, listing=RIGHT_LIST, list_error=None, reply=None, choice="allow"):
        self.listing, self.list_error, self.reply, self.choice, self.calls = listing, list_error, reply, choice, []

    def __call__(self, url, payload, timeout, headers=None):
        self.calls.append((url, payload, timeout))
        if url == dm.URL + LIST_PATH and payload is None:
            if self.list_error is not None:
                raise self.list_error
            return self.listing
        if url == dm.URL + ASK_PATH and payload is not None:
            if self.reply is not None:
                return self.reply
            if "risk" in payload["questions"]:
                ans = {"risk": {"type": "noul", "noul": 0.9 if self.choice == "block" else 0.05}}
            else:
                ans = {"thesis": {"type": "noul", "noul": 0.73}}
            return {"model": dm.INSTANCE, "answers": ans, "usage": {"input_tokens": 50, "output_tokens": 1}}
        raise AssertionError(f"the member asked for {url}")

    def posts(self):
        return [c for c in self.calls if c[1] is not None]


def _only_the_two_paths(fake):
    assert {url for url, _, _ in fake.calls} <= {dm.URL + LIST_PATH, dm.URL + ASK_PATH}
    assert not [url for url, _, _ in fake.calls if "load" in url]           # no load, no unload, ever


def test_t44_2_a_right_list_gives_one_post_naming_the_instance_and_the_parsed_answer():
    v3 = legacy.load("systemone_v3_candidate")
    fake = FakeLMStudio()
    d = pn.SystemOneMember("elephant-1", "decider", timeout=77.0, transport=fake).ask(CASE)
    assert d.valid and d.error is None and d.probability == 0.73 and d.veto is None and d.rationale == "typed probability"
    assert [(u, p is None, t) for u, p, t in fake.calls] == [(dm.URL + LIST_PATH, True, 5.0), (dm.URL + ASK_PATH, False, 77.0)]
    body = fake.posts()[0][1]
    state = {"create_order": False, "thesis": CASE["thesis"], "facts": CASE["facts"]}
    assert body == {**v3.make_request(state, body["questions"], tag=v3.TAG), "model": dm.INSTANCE}
    assert body["model"] == "elephant-1" and set(body) == {"model", "state", "questions"} and list(body["questions"]) == ["thesis"]
    _only_the_two_paths(fake)


@pytest.mark.parametrize("choice, p, veto", [("block", 0.9, True), ("allow", 0.05, False)])
def test_t44_2_the_risk_seat_asks_the_risk_question_and_vetoes_at_one_half(choice, p, veto):
    fake = FakeLMStudio(choice=choice)
    v = pn.SystemOneMember("elephant-1-risk", "vetter", transport=fake).ask(CASE)
    assert v.valid and v.probability == p and v.veto is veto and v.rationale == f"typed probability of a risk problem {p:.2f}"
    assert len(fake.posts()) == 1 and list(fake.posts()[0][1]["questions"]) == ["risk"]
    assert fake.posts()[0][1]["model"] == dm.INSTANCE
    _only_the_two_paths(fake)


LIST_FAULTS = [
    ("unreachable", dict(list_error=OSError("connection refused")), "did not answer"),
    ("not a list", dict(listing={"data": []}), "malformed"),
    ("not json object", dict(listing=["elephant-1"]), "malformed"),
    ("bad instances", dict(listing={"models": [right_entry(instances="elephant-1")]}), "malformed"),
    ("no instance", dict(listing={"models": [OTHER, right_entry(instances=[])]}), "not loaded"),
    ("no entry", dict(listing={"models": [OTHER]}), "not loaded"),
    ("wrong setting", dict(listing={"models": [right_entry(instances=[right_instance(context_length=4096)])]}),
     "context_length 4096, not 8192"),
    ("wrong slots", dict(listing={"models": [right_entry(instances=[right_instance(parallel=4)])]}), "parallel 4, not 1"),
    ("wrong file", dict(listing={"models": [right_entry(quantization={"name": "Q4_K_M"})]}), "quantisation 'Q4_K_M', not Q8_0"),
    ("two instances", dict(listing={"models": [right_entry(instances=[right_instance(), right_instance()])]}),
     "2 instances named elephant-1"),
    ("two entries", dict(listing={"models": [right_entry(), right_entry()]}), "2 instances named elephant-1"),
]


@pytest.mark.parametrize("role", ["decider", "vetter"])
@pytest.mark.parametrize("label, fault, words", LIST_FAULTS, ids=[f[0] for f in LIST_FAULTS])
def test_t44_2_a_list_fault_is_a_one_line_refusal_and_nothing_is_posted(label, fault, words, role):
    fake = FakeLMStudio(**fault)
    v = pn.SystemOneMember("elephant-1", role, transport=fake).ask(CASE)
    assert not v.valid and v.error.startswith("RuntimeError: ") and words in v.error, v.error
    assert "\n" not in v.error and v.probability is None and v.veto is None
    assert fake.posts() == [] and [u for u, _, _ in fake.calls] == [dm.URL + LIST_PATH]
    _only_the_two_paths(fake)


USAGE = {"input_tokens": 50, "output_tokens": 1}
REPLY_FAULTS = [
    ("the old tag", {"model": "nimble-v3:9b-mxfp8", "answers": {"thesis": {"type": "noul", "noul": 0.73}}}, "RuntimeError"),
    ("another model", {"model": "qwen/qwen3.8-27b", "answers": {"thesis": {"type": "noul", "noul": 0.73}}}, "RuntimeError"),
    ("no model", {"answers": {"thesis": {"type": "noul", "noul": 0.73}}}, "RuntimeError"),
    ("not an object", [{"model": "elephant-1"}], "RuntimeError"),
    ("no answers", {"model": "elephant-1", "answers": {}, "usage": USAGE}, ""),
    ("answers not typed", {"model": "elephant-1", "answers": {"thesis": {"type": "noul", "noul": "high"}}, "usage": USAGE}, ""),
    ("answers a string", {"model": "elephant-1", "answers": "yes", "usage": USAGE}, ""),
    ("no usage", {"model": "elephant-1", "answers": {"thesis": {"type": "noul", "noul": 0.73}}}, ""),
]


@pytest.mark.parametrize("label, reply, kind", REPLY_FAULTS, ids=[f[0] for f in REPLY_FAULTS])
def test_t44_2_a_reply_naming_another_model_or_malformed_is_an_error_after_one_post(label, reply, kind):
    fake = FakeLMStudio(reply=reply)
    v = pn.SystemOneMember("elephant-1", "decider", transport=fake).ask(CASE)
    assert not v.valid and v.error is not None and v.probability is None and v.error.startswith(kind)
    if kind:
        assert "elephant-1" in v.error
    assert len(fake.posts()) == 1
    _only_the_two_paths(fake)


def test_t44_2_the_member_defaults_to_the_shared_address_and_stays_on_loopback():
    assert pn.SystemOneMember("e", "decider").base == dm.URL == "http://127.0.0.1:1235"
    built = pn.build_panel({"members": [{"name": "e", "role": "decider", "backend": "systemone"}]}).members[0]
    assert isinstance(built, pn.SystemOneMember) and built.base == dm.URL
    for remote in ("http://10.0.0.5:1235", "https://127.0.0.1:1235", "http://127.0.0.1.example.com:1235"):
        with pytest.raises(ValueError):
            pn.SystemOneMember("n", "decider", base_url=remote)
    seen = []
    other = pn.SystemOneMember("e", "decider", base_url="http://localhost:1235/",
                               transport=lambda u, p, t, headers=None: seen.append(u) or (_ for _ in ()).throw(OSError("x")))
    assert "did not answer" in other.ask(CASE).error and seen == ["http://localhost:1235" + LIST_PATH]


# ---- T44-2 ruling 2: the roster's typed seats are Elephant-1; the Ollama-only member is gone

ELEPHANT_SOURCE = ("the date the model's publisher last updated its release (baselabs/elephant-1 on Hugging Face); "
                   "its training data ends earlier")


def test_default_roster_builds_the_requested_panel():
    roster = pn.load_roster()
    panel = pn.build_panel(roster)
    by_role = {r: [m.name for m in panel.members if m.role == r] for r in ("decider", "vetter", "scout")}
    assert by_role["decider"] == ["valedia-trading-decider", "elephant-1"]
    assert by_role["vetter"] == ["valedia-trading-auditor", "elephant-1-risk"] and by_role["scout"] == []
    assert len(by_role["decider"]) >= roster["min_deciders"] and len(by_role["vetter"]) >= roster["min_vetters"]
    specs = {s["name"]: s for s in roster["members"]}
    for name, role in (("elephant-1", "decider"), ("elephant-1-risk", "vetter")):
        assert specs[name] == {"name": name, "role": role, "backend": "systemone", "timeout": 150,
                               "model_label": "elephant-1@q8_0", "unseen_after": "2026-10-22",
                               "unseen_after_source": ELEPHANT_SOURCE}
        assert isinstance([m for m in panel.members if m.name == name][0], pn.SystemOneMember)
    assert not {"qwen3-8b", "nimble-v3", "nimble-v3-risk"} & set(specs)
    for text in (pn.DEFAULT_ROSTER.read_text(), (pn.DEFAULT_ROSTER.parent / "team.json").read_text()):
        assert "Nimble" not in text and "nimble" not in text and "Qwen 8B" not in text
    assert "Elephant-1" in json.loads((pn.DEFAULT_ROSTER.parent / "team.json").read_text())["local_forecasters"]
    disabled = [m["name"] for m in roster["members"] if not m.get("enabled", True)]
    assert disabled == ["valedia-trading-scout", "qwen3.8-27b"]        # both load the 27B model; opt in deliberately
    assert json.loads(pn.DEFAULT_ROSTER.read_text())["veto_rule"] == "any"
    with pytest.raises(ValueError):
        pn.build_panel({"members": [{"name": "x", "role": "decider", "backend": "carrier-pigeon"}]})


# ---- T44-2 ruling 5: nothing in the write set knows the Ollama path (the words are built here, not written)

WRITE_SET = ("valedia_trader/agents/panel.py", "valedia_trader/agents/panel_roster.json", "valedia_trader/agents/team.json",
             "valedia_trader/doctor.py", "valedia_trader/research_loop/tournament.py",
             "valedia_trader/research_loop/owner_page.py", "tests/agents/test_panel.py", "tests/core/test_doctor.py",
             "tests/research_loop/test_tournament.py", "tests/research_loop/test_owner_page.py")
OLLAMA_WORDS = ("114" + "34", "/api/" + "tags", "/api/" + "version", "pinned" + "_tag")


@pytest.mark.parametrize("rel", WRITE_SET)
def test_t44_2_no_file_of_the_write_set_names_the_ollama_path(rel):
    root = pn.DEFAULT_ROSTER.resolve().parents[2]
    text = (root / rel).read_text()
    assert [w for w in OLLAMA_WORDS if w in text] == [], rel
