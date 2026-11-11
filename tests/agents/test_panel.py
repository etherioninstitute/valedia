import json
import math

import pytest

from valedia_trader.agents import panel as pn
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
    silent = pn.HermesProfileMember("d", "decider", "p", runner=lambda argv, t: "I cannot answer")
    assert not silent.ask(CASE).valid


def systemone_transport(digest_ok=True, choice="allow"):
    v3 = legacy.load("systemone_v3_candidate")
    def http(url, payload, timeout, headers=None):
        if url.endswith("/api/tags"):
            return {"models": [{"name": v3.TAG, "digest": "sha256:" + (v3.MANIFEST_SHA256 if digest_ok else "0" * 64)}]}
        if url.endswith("/api/version"):
            return {"version": "0.40.14"}
        assert payload["model"] == v3.TAG and payload["state"]["create_order"] is False
        if "risk" in payload["questions"]:
            ans = {"risk": {"type": "noul", "noul": 0.9 if choice == "block" else 0.05}}
        else:
            ans = {"thesis": {"type": "noul", "noul": 0.73}}
        return {"model": v3.TAG, "answers": ans, "usage": {"input_tokens": 50, "output_tokens": 1}}
    return http


def test_systemone_member_enforces_the_pin_and_parses_typed_answers():
    d = pn.SystemOneMember("nimble", "decider", transport=systemone_transport()).ask(CASE)
    assert d.valid and d.probability == 0.73
    v = pn.SystemOneMember("nimble-risk", "vetter", transport=systemone_transport(choice="block")).ask(CASE)
    assert v.valid and v.veto is True and v.probability == 0.9
    bad = pn.SystemOneMember("nimble", "decider", transport=systemone_transport(digest_ok=False)).ask(CASE)
    assert not bad.valid and "digest" in bad.error
    with pytest.raises(ValueError):
        pn.SystemOneMember("n", "decider", base_url="http://10.0.0.5:11434")


def test_default_roster_builds_the_requested_panel():
    roster = pn.load_roster()
    panel = pn.build_panel(roster)
    by_role = {r: [m.name for m in panel.members if m.role == r] for r in ("decider", "vetter", "scout")}
    assert by_role["decider"] == ["valedia-trading-decider", "nimble-v3", "qwen3-8b"]
    assert by_role["vetter"] == ["valedia-trading-auditor", "nimble-v3-risk"] and by_role["scout"] == []
    disabled = [m["name"] for m in roster["members"] if not m.get("enabled", True)]
    assert disabled == ["valedia-trading-scout", "qwen3.8-27b"]        # both load the 27B model; opt in deliberately
    assert json.loads(pn.DEFAULT_ROSTER.read_text())["veto_rule"] == "any"
    with pytest.raises(ValueError):
        pn.build_panel({"members": [{"name": "x", "role": "decider", "backend": "carrier-pigeon"}]})
