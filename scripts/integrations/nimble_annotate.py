#!/usr/bin/env python3
"""Nimble 2 annotations, annotate only. Replaces the retired Jev shadow.

Nimble 2 is the only decision model. There is no cloud backend and no key. Every
annotation is one loopback call through systemone_hotpath_client.ask (Nimble 2
digest pin, NaN coercion, append-only decision log with source
`nimble_annotate:<kind>:<caller>` and the code's own label). Off unless
VALEDIA_SYSTEMONE=1: identity / None and zero calls.

Gates live in code. Every fact below is computed here from the real candidate
(booleans, None when unknown); the state carries identifiers and those facts,
no raw numbers, and the questions contain no digits. An annotation never gates,
sizes, reorders, flips, or changes a score, bias, weight, direction, or price.
The decision-point veto lives only in nimble_wiring; a site that already calls
nimble_wiring.gate references that verdict and does not call here.

Kinds:
- conviction: Kalshi CONVICTION after INFORMED passed (nimble_decide prediction
  questions on prediction_facts + informed_facts for the underlying).
- weather: weather_shadow scored row (agree / confidence / status_honesty).
- report: MiroFish signal doc (action / stand_down / report_usable / we_are_the_exit).
- scan: all_knowing_movers ranked list (scan_usable / stand_down / we_are_the_exit).
"""
from __future__ import annotations

import informed_facts as inf
import mirofish_intel_archive as mia
import nimble_decide as nd
import systemone_hotpath_client as s1

KEY = "nimble_note"
# ponytail: four in five voting agent types on one side is a crowded consensus.
CROWDED_RATIO = 0.80
NEUTRAL = 50.0
BAND = mia.NEUTRAL_BAND  # same near-neutral band as MiroFish FV use
SETTLED = frozenset({"final", "observation"})


def _noul_q(instructions, true, false):
    return {"type": "noul", "instructions": instructions,
            "criteria": {"true": true, "false": false}}


REPORT_QUESTIONS = {
    "action": {
        "type": "choice",
        "instructions": (
            "Using the code-computed `facts`, which shadow desk action fits this "
            "MiroFish report? Do not count or compare. Code owns sizing and orders. "
            "This is a note only."),
        "criteria": {
            "annotate": "Stamp the report; it reads as usable seasoning.",
            "veto": "In shadow, the report should not feed conviction.",
            "resize": "Keep the report but read conviction as weaker in shadow.",
            "stand_down": "Sit this cycle out in shadow; not a trading halt.",
        },
    },
    "stand_down": _noul_q(
        "Using the code-computed `facts`, should the desk stand down on this report "
        "in shadow (sit out, not a halt)?",
        "Report is not actionable; sit out this cycle.",
        "Report can be used as seasoning."),
    "report_usable": _noul_q(
        "Using the code-computed `facts`, is this MiroFish report usable as shadow "
        "seasoning (fresh, from the swarm, not neutral, not contradictory)?",
        "Enough structure to judge.",
        "Too thin, stale, neutral or contradictory to use."),
    "we_are_the_exit": _noul_q(
        "Using the code-computed `facts`, does this setup look like we are the exit "
        "(a crowded one-sided consensus, chasing, providing the other side's liquidity)?",
        "We would be the exit or a late chase.",
        "Not an exit-liquidity read."),
}

WEATHER_QUESTIONS = {
    "agree": {
        "type": "choice",
        "instructions": (
            "Using the code-computed `facts`, plus the settle source and research "
            "sources, do settle and research agree with the book? Do not compare "
            "numbers. Shadow only. Do not send."),
        "criteria": {
            "agree": "Settle and research point the same way versus the book.",
            "disagree": "Settle and research conflict, or the book is the other side.",
            "abstain": "Not enough to say.",
        },
    },
    "confidence": _noul_q(
        "Using the code-computed `facts`, is that agree or disagree read confident? Shadow only.",
        "Confident.", "Not confident."),
    "status_honesty": {
        "type": "choice",
        "instructions": (
            "Using the code-computed `facts`, is the settlement status honest (final "
            "or observation), or is a forecast being treated as final?"),
        "criteria": {
            "honest": "Final or observation, and research stays research.",
            "forecast_as_final": "A forecast is being used as if it settled.",
            "missing": "No settle value.",
        },
    },
}

# Same buy / hold / skip question nimble_decide asks at the prediction gate; the
# digit-labelled risk question is left out so annotation questions stay digit-free.
CONVICTION_QUESTIONS = {"action": nd.QUESTIONS_ACTION["prediction"]}

SCAN_QUESTIONS = {
    "scan_usable": _noul_q(
        "Using the code-computed `facts`, is this paper movers scan usable as shadow "
        "seasoning? The ranking is already fixed by code. Do not count or compare.",
        "The scan has printed names, a positive fee-true edge, and no risk off.",
        "Nothing printed, nothing sized, or the macro read is risk off."),
    "stand_down": REPORT_QUESTIONS["stand_down"],
    "we_are_the_exit": REPORT_QUESTIONS["we_are_the_exit"],
}


def on():
    """Same switch as every Nimble 2 call: VALEDIA_SYSTEMONE=1."""
    return s1.enabled()


# ---------------------------------------------------------------- helpers
def _num(v):
    return inf._num(v)


def _dict(v):
    return v if isinstance(v, dict) else {}


def _parse_for(questions):
    """Parser: choice → <k>, <k>_confidence, <k>_probabilities; noul → <k> as a float."""
    def parse(body):
        ans = _dict(_dict(body).get("answers"))
        out = {}
        for k, q in questions.items():
            a = _dict(ans.get(k))
            if q["type"] == "choice":
                c = str(a.get("choice") or "").strip()
                out[k] = c if c in q["criteria"] else None
                out[k + "_confidence"] = _num(a.get("confidence"))
                out[k + "_probabilities"] = s1._probs(a.get("probabilities"))
            else:
                out[k] = s1._prob(a)
        return out
    return parse


def _ask(kind, caller, state, questions, parse, label, post, get):
    """One logged loopback call. Returns the stamp (facts always carried)."""
    source = "nimble_annotate:%s:%s" % (kind, caller or "-")
    stamp = {"annotate_only": True, "oms": False, "create_order": False, "kind": kind,
             "source": source, "facts": dict(state.get("facts") or {})}
    try:
        nd.warm(post=post, get=get)
        res = s1.ask(state, questions, source=source, label=label, post=post,
                     timeout=nd.timeout_s(), parse=parse, get=get)
    except Exception as e:  # an annotation must never break its caller
        res = {"error": "internal_" + type(e).__name__}
    err = res.get("error")
    stamp.update({"model": res.get("pin"), "error": err, "fail_open": bool(err),
                  "latency_ms": res.get("latency_ms"),
                  "within_budget": bool(res.get("within_budget"))})
    if not err:
        stamp.update(res.get("answers") or {})
    return stamp


def _stamp_doc(doc, kind, caller, state_fn, questions, label_fn, post, get):
    if not on():
        return doc
    out = dict(doc or {})
    try:
        state = state_fn(out)
        label = label_fn(out)
    except Exception as e:
        out[KEY] = {"annotate_only": True, "oms": False, "kind": kind, "fail_open": True,
                    "error": "facts_" + type(e).__name__}
        return out
    out[KEY] = _ask(kind, caller, state, questions, _parse_for(questions), label, post, get)
    return out


# ---------------------------------------------------------------- MiroFish report
def report_facts(doc, now=None):
    d = _dict(doc)
    sigs = _dict(d.get("signals"))
    dirs = {str(_dict(v).get("direction") or "HOLD").upper() for v in sigs.values()}
    cons = _dict(d.get("consensus"))
    bull, bear, neu = _num(cons.get("bullish")), _num(cons.get("bearish")), _num(cons.get("neutral"))
    known = bull is not None and bear is not None
    total = (bull or 0) + (bear or 0) + (neu or 0)
    bias = _num(d.get("overall_bias"))
    fresh = mia.evaluate(d, now=now)
    agree = None
    if known and bias is not None and bull != bear and abs(bias - NEUTRAL) > BAND:
        agree = (bias > NEUTRAL) == (bull > bear)
    return {
        "has_signals": bool(sigs),
        "has_events": bool(d.get("events")),
        "directions_conflict": len(dirs - {"HOLD"}) > 1,
        "consensus_bull": None if not known else bull > bear,
        "consensus_bear": None if not known else bear > bull,
        "consensus_split": None if not known else bull == bear,
        "consensus_crowded": None if not known or total <= 0
        else max(bull, bear) / total >= CROWDED_RATIO,
        "bias_bullish": None if bias is None else bias > NEUTRAL + BAND,
        "bias_bearish": None if bias is None else bias < NEUTRAL - BAND,
        "bias_near_neutral": None if bias is None else abs(bias - NEUTRAL) <= BAND,
        "bias_agrees_consensus": agree,
        "macro_risk_off": inf.macro_risk_off(d),
        "source_swarm": d.get("source") == "mirofish_swarm",
        "fresh": not fresh["stale"],
        "usable_for_fv": bool(fresh["usable_for_fv"]),
    }


def report_state(doc, now=None):
    d = _dict(doc)
    return {"kind": "mirofish_report", "source": d.get("source") or "mirofish",
            "symbols": sorted(str(k) for k in _dict(d.get("signals")))[:8],
            "facts": report_facts(d, now=now), "create_order": False}


def annotate_report(doc, *, caller="mirofish_signal_extractor", post=None, get=None, now=None):
    """Stamp `nimble_note` on a MiroFish signal doc. Off = identity. Scores unchanged."""
    return _stamp_doc(doc, "report", caller, lambda d: report_state(d, now=now),
                      REPORT_QUESTIONS,
                      lambda d: {"code": "mirofish_signal", "overall_bias": d.get("overall_bias")},
                      post, get)


# ---------------------------------------------------------------- weather
def weather_facts(row):
    b = _dict(row)
    sf = _dict(b.get("settlement_fv"))
    conf = str(sf.get("confidence") or "").strip().lower()
    present = sf.get("value") is not None
    tags = [t for t in (b.get("research_tags") or []) if isinstance(t, dict)]
    fv, px, ev = _num(b.get("fv")), _num(b.get("price")), _num(b.get("fee_true_ev"))
    tag = str(b.get("tag") or "").strip().lower()
    side = str(b.get("side") or "").strip().lower()
    return {
        "settle_present": present,
        "settle_final_or_observation": (conf in SETTLED) if present else None,
        "settle_is_forecast": (conf == "forecast") if present else None,
        "research_present": bool(tags),
        "research_has_forecast": any(
            str(t.get("forecast_or_observation") or "").lower() == "forecast" for t in tags),
        "fv_present": fv is not None,
        "fv_above_book": None if fv is None or px is None else fv > px,
        "fv_below_book": None if fv is None or px is None else fv < px,
        "fee_true_ev_positive": None if ev is None else ev > 0,
        "tag_official": (tag == "official") if tag else None,
        "would_have_size": (_num(b.get("would_have_size")) or 0) > 0,
        "lookup_unresolved": bool(b.get("lookup_unresolved")),
        "side_yes": True if side == "yes" else False if side == "no" else None,
    }


def weather_state(row):
    b = _dict(row)
    sf = _dict(b.get("settlement_fv"))
    return {"kind": "weather", "ticker": b.get("ticker"), "settle_source": sf.get("source"),
            "metric": sf.get("metric"), "tag": b.get("tag"),
            "research": [t.get("source") for t in (b.get("research_tags") or [])[:6]
                         if isinstance(t, dict)],
            "facts": weather_facts(b), "create_order": False}


def annotate_weather(row, *, caller="weather_shadow", post=None, get=None):
    """Stamp `nimble_note` on a scored weather row. Off = identity. Agree is not a pass."""
    return _stamp_doc(row, "weather", caller, weather_state, WEATHER_QUESTIONS,
                      lambda r: {"code": r.get("tag"), "would_have_size": r.get("would_have_size")},
                      post, get)


# ---------------------------------------------------------------- Kalshi CONVICTION
def conviction_facts(opp, informed):
    """prediction_facts on the opp merged with create_preflight's `informed` dict."""
    o, info = _dict(opp), _dict(informed)
    merged = dict(o)
    merged["informed"] = info
    ev = _dict(o.get("ev_preflight")).get("net")
    if _num(ev) is not None and merged.get("fee_true_edge") is None:
        merged["fee_true_edge"] = ev
    facts = nd.prediction_facts(merged)
    facts.update({
        "fv_confidence_present": bool(info.get("fv_confidence")),
        "book_two_sided": None if _num(info.get("bid")) is None or _num(info.get("ask")) is None
        else 0 < _num(info["bid"]) <= _num(info["ask"]),
        "market_present": isinstance(info.get("market"), dict) and bool(info.get("market")),
        # create_preflight ran kalshi_reasons (fee map, FV, edge, book, depth, size, clocks).
        "informed_gate_passed": True,
    })
    if facts.get("clock_ok") is None:
        facts["clock_ok"] = True  # the clock checks are part of the passed INFORMED gate
    return facts


def conviction_note(opp, informed, *, caller, side=None, post=None, get=None):
    """Annotate a Kalshi CONVICTION create that INFORMED already admitted. None when off."""
    if not on():
        return None
    try:
        import nimble_wiring as nw
        o = _dict(opp)
        side = str(side or o.get("side") or "").strip().lower() or None
        sym = nw._symbol(o)
        cand = {"ticker": o.get("ticker") or sym, "venue": o.get("platform") or "kalshi",
                "side": side}
        facts = conviction_facts(o, informed)
        inf_doc = inf.informed_facts(sym, side, direction=nw.direction_for(o, side))
        state = nd.build_state("prediction", cand, side, facts, inf_doc.get("facts"))
        state["annotate_only"] = True
        label = {"code": "informed_pass", "strategy": _dict(informed).get("strategy"),
                 "informed_sources": inf_doc.get("sources")}
    except Exception as e:
        return {"annotate_only": True, "oms": False, "kind": "conviction", "fail_open": True,
                "error": "facts_" + type(e).__name__}
    stamp = _ask("conviction", caller, state, CONVICTION_QUESTIONS, nd._parse,
                 label, post, get)
    stamp["informed"] = dict(state["informed"])
    return stamp


# ---------------------------------------------------------------- movers scan
def scan_facts(ranked, *, print_bar=None, informed=None):
    rows = [r for r in (ranked or []) if isinstance(r, dict)]
    top = rows[0] if rows else {}
    sized = lambda r: bool(_dict(r.get("would_have")).get("sized"))
    chg, bar = _num(top.get("chg_24h")), _num(print_bar)
    return {
        "has_rows": bool(rows),
        "any_sized": any(sized(r) for r in rows),
        "any_would_buy": any(r.get("label") == "would_buy" for r in rows),
        "any_edge_positive": any((_num(r.get("edge")) or 0) > 0 for r in rows),
        "all_watch_or_pass": all(r.get("label") in ("watch", "would_pass") for r in rows),
        "top_sized": sized(top) if rows else None,
        "top_printed": None if chg is None or bar is None else chg >= bar,
        "top_fenced": (bool(top.get("oil_fenced")) or top.get("class") == "weather") if rows else None,
        "macro_risk_off": inf.macro_risk_off(informed) if isinstance(informed, dict) else None,
    }


def season_scan(ranked, *, print_bar=None, informed=None, caller="all_knowing_movers",
                post=None, get=None):
    """Note on a fixed ranking. Off: facts only, no call. Never reorders."""
    facts = scan_facts(ranked, print_bar=print_bar, informed=informed)
    if not on():
        return {"annotate_only": True, "oms": False, "kind": "scan", "enabled": False,
                "fail_open": True, "reason": "VALEDIA_SYSTEMONE_off", "facts": facts}
    state = {"kind": "movers_scan",
             "symbols": [r.get("symbol") for r in (ranked or [])[:8] if isinstance(r, dict)],
             "facts": facts, "create_order": False}
    stamp = _ask("scan", caller, state, SCAN_QUESTIONS, _parse_for(SCAN_QUESTIONS),
                 {"code": "fee_true_rank_fixed"}, post, get)
    stamp["enabled"] = True
    return stamp
