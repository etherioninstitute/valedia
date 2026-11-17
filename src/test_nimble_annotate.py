#!/usr/bin/env python3
"""Annotations (nimble_annotate, named for Nimble) on the shared Elephant-1 instance.
Replaces test_jev_shadow. Fakes only.

No network: the typed POST (the client's own transport) and the model-list GET are fakes.
No keys, no orders.
Run: python3 test_nimble_annotate.py
"""
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["OPENCLAW_SKIP_ENV"] = "1"
for _k in list(os.environ):
    if _k.startswith("VALEDIA_NIMBLE") or _k.startswith("VALEDIA_SYSTEMONE"):
        os.environ.pop(_k)
LOG = os.path.join(tempfile.mkdtemp(prefix="nimann_"), "decisions.jsonl")
os.environ["VALEDIA_SYSTEMONE_LOG"] = LOG

import informed_before_create as ibc  # noqa: E402
import nimble_annotate as na  # noqa: E402
import nimble_decide as nd  # noqa: E402
import systemone_hotpath_client as s1  # noqa: E402
from mirofish_signal_extractor import process_mirofish_report  # noqa: E402
import systemone_test_fakes as fk  # noqa: E402

nd._WARMED = True  # no warmup ping in these tests; warm() is covered by test_nimble_decide
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))

SIG = {
    "ts": "2026-09-20T00:00:00+00:00",
    "status": "active",
    "overall_bias": 58.5,
    "macro_bias": 40.0,
    "source": "mirofish_swarm",
    "events": [{"type": "regulation", "direction": "RISK_ON"}],
    "consensus": {"bullish": 5, "bearish": 1, "neutral": 0, "bull_ratio": 0.83},
    "signals": {
        "BTC-USD": {"score": 62.5, "direction": "BUY", "confidence": 0.7},
        "ETH-USD": {"score": 42.0, "direction": "SELL", "confidence": 0.4},
    },
}
REPORT = {
    "outline": {"summary": "Bitcoin likely to recover from extreme fear"},
    "markdown_content": ("Bitcoin rally surge accumulate buying pressure near support. "
                         "Institutional traders executed a BUY trade for BTC-USD."),
}
PICKS = {  # typed answers per question name (the reply is built for the questions asked)
    "stand_down": 0.3,
    "report_usable": 0.8,
    "we_are_the_exit": 0.7,
    "scan_usable": 0.6,
    "agree": {"agree": 0.7, "disagree": 0.2, "abstain": 0.1},
    "confidence": 0.6,
    "status_honesty": "honest",
}
_get = fk.list_get()


def _answers(questions):
    picks = dict(PICKS)
    keys = list(questions.get("action", {}).get("criteria", ()))
    picks["action"] = {"buy": 0.1, "hold": 0.1, "skip": 0.8} if "skip" in keys else "annotate"
    return fk.typed_reply(questions, picks, confidence=0.7)


class Engine:
    """VALEDIA_SYSTEMONE on/off + a fake loopback engine (raise=error path)."""

    def __init__(self, on=True, raise_=None, body=None):
        self.on, self.raise_, self.body, self.posts = on, raise_, body, []

    def post(self, url, payload, timeout):
        assert url == s1.DECIDE_URL == s1._pin.dm.URL + "/v1/systemone", url
        self.posts.append(payload)
        if self.raise_:
            raise self.raise_
        return self.body or _answers(payload["questions"])

    def __enter__(self):
        self._prev = os.environ.get("VALEDIA_SYSTEMONE")
        self._prev_log = os.environ.get("VALEDIA_SYSTEMONE_LOG")
        os.environ["VALEDIA_SYSTEMONE_LOG"] = LOG  # scoped: other test modules set it at import
        self._g, self._p = s1._get, s1._post
        s1._get, s1._post = _get, self.post
        s1._reset_check()
        if self.on:
            os.environ["VALEDIA_SYSTEMONE"] = "1"
        else:
            os.environ.pop("VALEDIA_SYSTEMONE", None)
        return self

    def __exit__(self, *exc):
        s1._get, s1._post = self._g, self._p
        s1._reset_check()
        os.environ.pop("VALEDIA_SYSTEMONE", None)
        if self._prev is not None:
            os.environ["VALEDIA_SYSTEMONE"] = self._prev
        os.environ.pop("VALEDIA_SYSTEMONE_LOG", None)
        if self._prev_log is not None:
            os.environ["VALEDIA_SYSTEMONE_LOG"] = self._prev_log
        return False


def _lines():
    if not os.path.exists(LOG):
        return []
    return [json.loads(x) for x in open(LOG).read().splitlines()]


def _conviction_opp(**over):
    d = {"ticker": "KXBTCY-26DEC31-T100000", "strategy": "CONVICTION_YES", "side": "yes",
         "price": 0.50, "edge": 0.20, "fv": 0.70, "fv_source": "model", "fv_confidence": True,
         "slip": 0.0, "bid": 0.49, "ask": 0.51, "depth": 20, "bankroll": 10000,
         "hours_to_resolve": 48.0, "market": {"status": "open"}}
    d.update(over)
    return d


def test_no_cloud_backend_anywhere_in_scripts():
    banned = ("api." + "typesafe" + ".ai", "TYPESAFE" + "_API_KEY", "jev" + "-1.",
              "VALEDIA_" + "JEV")
    hits = []
    for base, _dirs, files in os.walk(os.path.join(ROOT, "scripts")):
        for name in files:
            if not name.endswith((".py", ".sh", ".js", ".html", ".json", ".md")):
                continue
            path = os.path.join(base, name)
            text = open(path, encoding="utf-8", errors="ignore").read()
            hits += ["%s:%s" % (os.path.relpath(path, ROOT), b) for b in banned if b in text]
    assert hits == [], hits
    assert not os.path.exists(os.path.join(HERE, "jev" + "_shadow.py"))
    src = open(os.path.join(HERE, "nimble_annotate.py")).read()
    for bad in ("urllib", "https://", "create_order(", "place_order", "Authorization"):
        assert bad not in src, bad
    # Sites that already call nimble_wiring.gate get no second (annotation) call.
    for mod in ("master_allocator.py", "sports_evidence.py", "weather_live.py"):
        text = open(os.path.join(HERE, mod)).read()
        assert "nimble_wiring" in text or "_nw." in text, mod
        assert "nimble_annotate" not in text and "conviction_note" not in text, mod
    pre = open(os.path.join(HERE, "informed_before_create.py")).read()
    body = pre[pre.index("def create_preflight("):]
    assert "conviction_note(" not in body and "post=" not in body  # pure gate, no model call
    print("ok no cloud backend")


def test_questions_digit_free_and_fact_based():
    for qs in (na.REPORT_QUESTIONS, na.WEATHER_QUESTIONS, na.SCAN_QUESTIONS,
               na.CONVICTION_QUESTIONS):
        blob = json.dumps(qs)
        assert not re.search(r"\d", blob), re.findall(r".{12}\d.{6}", blob)
        for word in ("percent", "greater", "less than", "threshold", "sum of", "count the"):
            assert word not in blob.lower(), word
        for k, q in qs.items():
            assert "facts" in q["instructions"], k
    print("ok questions")


def test_off_is_identity_and_zero_calls():
    with Engine(on=False) as e:
        assert na.annotate_report(SIG) is SIG
        row = {"ticker": "KXHIGHNY", "fv": 0.2}
        assert na.annotate_weather(row) is row
        assert ibc.conviction_note(_conviction_opp(), ibc.informed_from_opp(_conviction_opp()),
                                   caller="t") is None
        note = na.season_scan([{"symbol": "PUMP", "label": "would_buy", "edge": 0.1,
                                "would_have": {"sized": True}, "chg_24h": 20.0}], print_bar=16.0)
        assert note["reason"] == "VALEDIA_SYSTEMONE_off" and note["facts"]["any_sized"] is True
        assert e.posts == []
    print("ok off")


def test_kalshi_conviction_state_carries_real_facts():
    opp = _conviction_opp()
    info = ibc.informed_from_opp(opp)  # exactly what create_preflight returns as `informed`
    os.environ["VALEDIA_NIMBLE_MODE_PREDICTION"] = "veto"  # mode never applies to a note
    try:
        with Engine() as e:
            n0 = len(_lines())
            note = ibc.conviction_note(opp, info, caller="kalshi_executor")
    finally:
        os.environ.pop("VALEDIA_NIMBLE_MODE_PREDICTION", None)
    assert len(e.posts) == 1
    st = e.posts[0]["state"]
    f = st["facts"]
    assert st["create_order"] is False and st["annotate_only"] is True
    assert st["asset_class"] == "prediction" and st["symbol"] == opp["ticker"] and st["side"] == "yes"
    for k in ("edge_positive", "fv_present", "fv_source_allowed", "clock_ok", "depth_ok",
              "spread_tight", "side_yes", "fv_confidence_present", "book_two_sided",
              "market_present", "informed_gate_passed"):
        assert f[k] is True, (k, f)
    assert all(v is None or isinstance(v, bool) for v in f.values()), f
    assert st["informed"] and all(isinstance(v, bool) for v in st["informed"].values())
    assert set(e.posts[0]["questions"]) == {"action"}
    assert "allow" not in note and note["annotate_only"] is True and note["oms"] is False
    assert note["action"] == "skip" and note["fail_open"] is False and note["facts"] == f
    ln = _lines()[n0:]
    assert [x["source"] for x in ln] == ["nimble_annotate:conviction:kalshi_executor"]
    assert ln[0]["label"]["code"] == "informed_pass" and ln[0]["state"]["facts"]["edge_positive"]
    # A weak opp carries the weak facts (information, not a constant).
    weak = _conviction_opp(edge=-0.05, fv_source="tag_prior", depth=0, bid=0.30, ask=0.60)
    with Engine() as e:
        ibc.conviction_note(weak, ibc.informed_from_opp(weak), caller="t")
    wf = e.posts[0]["state"]["facts"]
    assert wf["edge_positive"] is False and wf["fv_source_allowed"] is False
    assert wf["depth_ok"] is False and wf["spread_tight"] is False
    # Not CONVICTION, arb, or a 15m speed series: no note, no call.
    with Engine() as e:
        for o in (_conviction_opp(strategy="MODEL"), _conviction_opp(strategy="LATENCY_ARB"),
                  _conviction_opp(ticker="KXBTC15M-26OCT01-T1")):
            assert ibc.conviction_note(o, ibc.informed_from_opp(o), caller="t") is None
        assert e.posts == []
    print("ok conviction facts")


def test_weather_note_carries_facts_and_changes_nothing():
    row = {"ticker": "KXHIGHNY-26SEP23-T72", "tag": "official", "side": "yes", "fv": 0.62,
           "price": 0.55, "fee_true_ev": 0.03, "would_have_size": 2, "create_order": False,
           "dispatchable": 0, "informed_pass": False,
           "settlement_fv": {"value": 74, "source": "nws_cli", "confidence": "final",
                             "metric": "maximum"},
           "research_tags": [{"source": "weather_com", "forecast_or_observation": "forecast"}]}
    with Engine() as e:
        out = na.annotate_weather(row)
    st = e.posts[0]["state"]
    assert st["facts"] == {
        "settle_present": True, "settle_final_or_observation": True, "settle_is_forecast": False,
        "research_present": True, "research_has_forecast": True, "fv_present": True,
        "fv_above_book": True, "fv_below_book": False, "fee_true_ev_positive": True,
        "tag_official": True, "would_have_size": True, "lookup_unresolved": False,
        "side_yes": True}
    assert st["settle_source"] == "nws_cli" and st["research"] == ["weather_com"]
    assert "fv" not in st and "price" not in st  # numbers stay in code
    j = out[na.KEY]
    assert j["agree"] == "agree" and j["agree_confidence"] == 0.7 and j["confidence"] == 0.6
    assert j["status_honesty"] == "honest" and j["oms"] is False
    assert {k: out[k] for k in row} == row  # annotate only
    with Engine(raise_=TimeoutError("slow")):
        failed = na.annotate_weather(row)
    assert failed[na.KEY]["fail_open"] is True and failed[na.KEY]["error"] == "TimeoutError"
    assert failed[na.KEY]["facts"]["settle_present"] is True
    assert _lines()[-1]["source"] == "nimble_annotate:weather:weather_shadow"
    print("ok weather")


def test_mirofish_report_note_carries_facts():
    with Engine() as e:
        out = na.annotate_report(SIG, now=datetime(2026, 9, 20, 6, tzinfo=timezone.utc))
    f = e.posts[0]["state"]["facts"]
    assert f["has_signals"] and f["has_events"] and f["directions_conflict"]
    assert f["consensus_bull"] is True and f["consensus_crowded"] is True
    assert f["bias_bullish"] is True and f["bias_agrees_consensus"] is True
    assert f["source_swarm"] is True and f["macro_risk_off"] is False
    assert set(e.posts[0]["questions"]) == set(na.REPORT_QUESTIONS)
    j = out[na.KEY]
    assert j["report_usable"] == 0.8 and j["we_are_the_exit"] == 0.7 and j["stand_down"] == 0.3
    assert out["overall_bias"] == 58.5 and out["signals"] == SIG["signals"]
    assert na.report_facts({})["has_signals"] is False
    assert na.report_facts({})["consensus_bull"] is None
    # Extractor end to end: off = no key, on = note saved beside unchanged scores.
    md = tempfile.mkdtemp(prefix="nimann_mf_")
    with Engine(on=False) as e:
        off = process_mirofish_report(REPORT, enrichment={}, out_path=os.path.join(md, "a.json"))
        assert e.posts == [] and na.KEY not in off
    with Engine() as e:
        on = process_mirofish_report(REPORT, enrichment={}, out_path=os.path.join(md, "b.json"))
    assert on[na.KEY]["fail_open"] is False and on[na.KEY]["facts"]["has_signals"] is True
    assert on["signals"]["BTC-USD"]["score"] == off["signals"]["BTC-USD"]["score"]
    assert on["overall_bias"] == off["overall_bias"]
    assert _lines()[-1]["source"] == "nimble_annotate:report:mirofish_signal_extractor"
    print("ok mirofish")


def main():
    test_no_cloud_backend_anywhere_in_scripts()
    test_questions_digit_free_and_fact_based()
    test_off_is_identity_and_zero_calls()
    test_kalshi_conviction_state_carries_real_facts()
    test_weather_note_carries_facts_and_changes_nothing()
    test_mirofish_report_note_carries_facts()
    print("NIMBLE-ANNOTATE: PASS")


if __name__ == "__main__":
    main()
