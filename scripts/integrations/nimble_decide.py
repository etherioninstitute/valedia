#!/usr/bin/env python3
"""Nimble 2 quick decision for every asset class: stock, crypto, prediction.

One narrow question (buy / hold / skip, plus an optional risk label). Every
number and comparison is computed in code first (stock_facts / crypto_facts /
prediction_facts and informed_facts); the state carries booleans only and the
questions contain no digits apart from the risk choice labels. Every call is
logged by systemone_hotpath_client.ask (digest pin, loopback, NaN coercion,
latency budget) with source "nimble_decide:<source>" and the code's own
decision in `label`.

Modes (env VALEDIA_NIMBLE_MODE_STOCK / _CRYPTO / _PREDICTION, else
VALEDIA_NIMBLE_MODE, else shadow):
- off:    no call, allow True.
- shadow: call when VALEDIA_SYSTEMONE=1, compute + stamp the veto rule, allow ALWAYS True.
- veto:   allow only when the veto rule below passes. Engine off, error, refusal, digest
          mismatch or timeout → allow False (fail-closed).
Exits (side sell/exit/close, or candidate action sell / exit True) are never blocked and
never sent. Veto only removes an order; it never adds, sizes, flips or reprices one.

Confidence semantics (verified on Burton's Mac): `confidence` = 1 - normalized entropy of
`probabilities` (sharpness), NOT P(chosen): buy .4765 / skip .5235 → confidence .00159.
Veto rule (entries only): allow iff action == "buy" and confidence >= min_sharpness (only a
"not a coin flip" floor) and p_cal >= required_p, where
  min_sharpness  VALEDIA_NIMBLE_MIN_SHARPNESS (0.05); VALEDIA_NIMBLE_MIN_CONF = deprecated alias;
  p_cal          calibration_ledger.calibrate("nimble_decide:<source>", p_buy): identity under
                 30 scorable decisions, then isotonic (`calibration` records which);
  required_p     max(VALEDIA_NIMBLE_P_FLOOR (0.60), break_even_p + margin), floor if unknown;
  margin         VALEDIA_NIMBLE_BASE_MARGIN (0.02) * phi_multiplier;
  phi_multiplier 0.5 + 2.5 * (1 - rf), rf = valedia_config._risk_factor(), the SAME Etherion
                 risk_level scalar dynamic_per_trade_cap uses: NORMAL 0.5 .. CAUTION 1.5 .. HALT
                 3.0; missing / no risk_level / stale (VALEDIA_NIMBLE_PHI_MAX_AGE_H, 24) → 1.0.
break_even_p from the caller's `economics` (code only, NEVER in the model state), else None:
  prediction    price of the side bought (`<side>_price` else `price`; YES for a yes buy, NO
                for a no buy; > 1 = cents) + fee_per_contract + half_spread + slip (known ones).
  stock/crypto  0.5 + roundtrip_fee_pct / 100 / 2 (percent, fee_engine.roundtrip_cost).
                APPROXIMATION for a symmetric +-m outcome, exact break-even 0.5 + fee / (2 m):
                this is m = 100% and UNDERSTATES it for real moves. `expected_move_pct` gives
                0.5 + fee_pct / (2 move_pct); an explicit `break_even_p` is used as given.
Reasons: veto_model_<hold|skip>|not_sharp|no_p_buy|p_below_required; allow = veto_model_buy.
Shadow stamps all of it (would-be reason in `rule_reason`) and never blocks.

This module never places an order and never fetches market data.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime

import informed_facts as inf
import systemone_hotpath_client as s1

CLASSES = ("stock", "crypto", "prediction")
MODES = ("off", "shadow", "veto")
DEFAULT_MODE = "shadow"
MIN_SHARPNESS_DEFAULT = 0.05
P_FLOOR_DEFAULT = 0.60
BASE_MARGIN_DEFAULT = 0.02
PHI_MULT_LO, PHI_MULT_HI = 0.5, 3.0
PHI_MAX_AGE_H_DEFAULT = 24.0
TIMEOUT_S_DEFAULT = 10.0
MANY_CAP_S_DEFAULT = 30.0
ACTIONS = ("buy", "hold", "skip")
RISK_LABELS = tuple(str(i) for i in range(1, 11))
EXIT_SIDES = frozenset({"sell", "exit", "close"})
# ponytail: fused conviction at or above 65 is "strong" (intel_layer: >65 bullish).
FUSED_STRONG = 65.0
# ponytail: a binary book spread at or under 5c (dollar units) is tight.
SPREAD_TIGHT = 0.05
# ponytail: regime words that mean risk off when a row carries `regime`.
RISK_OFF_REGIMES = frozenset({"BEAR", "FEAR", "EXTREME_FEAR", "RISK_OFF", "VOLATILE", "HALT"})

_ACTION_INSTR = (
    "A {what} entry candidate already passed the code's own gates. Read only the "
    "code-computed `facts` and `informed` booleans in the state. Do not compute or "
    "compare anything. Should this entry go ahead right now? This is a check, not an order.")
_HOLD = ("The facts are mixed or incomplete: several facts are unknown, or the informed "
         "sources are silent or disagree with each other.")
_SPOT_BUY = ("The facts line up: the fused signal is strong, the filter passed, the fee "
             "check is fine, the trend is up, the name is not already held, and MiroFish, "
             "news and the risk engine agree or stay silent with no conflict and no risk off.")
_SPOT_SKIP = ("Red flags: an informed source conflicts with the side, the risk engine or the "
              "MiroFish macro view is risk off, the regime is risk off, the fee check fails, "
              "the filter failed, or the name is already held.")
QUESTIONS_ACTION = {
    "stock": {"type": "choice", "instructions": _ACTION_INSTR.format(what="stock"),
              "criteria": {"buy": _SPOT_BUY, "hold": _HOLD, "skip": _SPOT_SKIP}},
    "crypto": {"type": "choice", "instructions": _ACTION_INSTR.format(what="spot crypto"),
               "criteria": {"buy": _SPOT_BUY, "hold": _HOLD, "skip": _SPOT_SKIP}},
    "prediction": {
        "type": "choice", "instructions": _ACTION_INSTR.format(what="prediction market"),
        "criteria": {
            "buy": ("The fee-true edge is positive, the fair value source is allowed, "
                    "resolution is official, the clock is fine, the book has depth, the "
                    "spread is tight, and no informed source conflicts."),
            "hold": _HOLD,
            "skip": ("Red flags: no positive fee-true edge, a fair value source that is not "
                     "allowed, unofficial resolution, a clock or depth problem, a wide spread, "
                     "an informed source that conflicts, or risk off."),
        }},
}
QUESTION_RISK = {
    "type": "choice",
    "instructions": ("Rate how risky this entry looks, using only the facts in the state. "
                     "The first label is the safest and the last label is the riskiest. "
                     "Do not compute anything."),
    "criteria": {
        "1": "no red flags and the informed sources agree", "2": "negligible concerns",
        "3": "minor concerns", "4": "some concerns but plausible",
        "5": "a mixed picture or many unknown facts", "6": "several red flags",
        "7": "serious red flags", "8": "many serious red flags",
        "9": "an informed source conflicts and the code facts are weak",
        "10": "conflicts and risk off everywhere",
    },
}
QUESTIONS = {c: {"action": QUESTIONS_ACTION[c], "risk": QUESTION_RISK} for c in CLASSES}

_WARMED = False
_MIN_CONF_NOTED = False


# ---------------------------------------------------------------- env + modes
def mode_for(asset_class):
    """off | shadow | veto. Unknown values fall back to shadow (no behavior change)."""
    for key in ("VALEDIA_NIMBLE_MODE_" + str(asset_class or "").upper(), "VALEDIA_NIMBLE_MODE"):
        raw = os.environ.get(key, "").strip().lower()
        if raw:
            return raw if raw in MODES else DEFAULT_MODE
    return DEFAULT_MODE


def _env_float(key, default, lo, hi):
    try:
        v = float(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default
    return v if lo <= v <= hi else default


def min_sharpness():
    """VALEDIA_NIMBLE_MIN_SHARPNESS, else deprecated VALEDIA_NIMBLE_MIN_CONF, else 0.05."""
    global _MIN_CONF_NOTED
    key = "VALEDIA_NIMBLE_MIN_SHARPNESS"
    if not os.environ.get(key, "").strip() and os.environ.get("VALEDIA_NIMBLE_MIN_CONF", "").strip():
        key = "VALEDIA_NIMBLE_MIN_CONF"
        if not _MIN_CONF_NOTED:
            _MIN_CONF_NOTED = True
            print("nimble_decide: VALEDIA_NIMBLE_MIN_CONF is deprecated; read as the sharpness "
                  "floor VALEDIA_NIMBLE_MIN_SHARPNESS", file=sys.stderr)
    return _env_float(key, MIN_SHARPNESS_DEFAULT, 0.0, 1.0)


min_conf = min_sharpness  # deprecated name


def p_floor():
    return _env_float("VALEDIA_NIMBLE_P_FLOOR", P_FLOOR_DEFAULT, 0.0, 1.0)


def base_margin():
    return _env_float("VALEDIA_NIMBLE_BASE_MARGIN", BASE_MARGIN_DEFAULT, 0.0, 1.0)


def timeout_s():
    return _env_float("VALEDIA_NIMBLE_TIMEOUT_S", TIMEOUT_S_DEFAULT, 0.1, 600.0)


def many_cap_s():
    return _env_float("VALEDIA_NIMBLE_MANY_CAP_S", MANY_CAP_S_DEFAULT, 0.1, 3600.0)


def warm(post=None, get=None, timeout=None):
    """systemone warmup once per process (cold-load timeout unless given). None if skipped."""
    global _WARMED
    if _WARMED or s1._WARM or not s1.enabled():
        return None
    _WARMED = True
    return s1.warmup(post=post, timeout=timeout, get=get)


# ---------------------------------------------------------------- fact builders
def _num(v):
    return inf._num(v)


def _bool(v):
    return v if isinstance(v, bool) else None


def _first(d, *keys):
    for k in keys:
        if isinstance(d, dict) and d.get(k) is not None:
            return d.get(k)
    return None


def _any_known(*vals):
    """True if any True, False if any False, else None."""
    if any(v is True for v in vals):
        return True
    return False if any(v is False for v in vals) else None


def _spot_facts(row):
    row = row if isinstance(row, dict) else {}
    q = _first(row, "q", "filter")
    q = q if isinstance(q, dict) else {}
    fee = row.get("fee_check")
    ere = row.get("ere")
    fused, p5 = _num(row.get("fused")), _num(q.get("p5"))
    dom = q.get("dom")
    regime = str(row.get("regime") or "").upper()
    ere_off = (not ere["safe"]) if isinstance(ere, dict) and isinstance(ere.get("safe"), bool) else None
    reg_off = (regime in RISK_OFF_REGIMES) if regime else None
    return {
        "fused_strong": None if fused is None else fused >= FUSED_STRONG,
        "filter_passed": _bool(_first(q, "passes_filter")) if "passes_filter" in q
        else _bool(row.get("passes_filter")),
        "fee_ok": _bool(fee.get("pass")) if isinstance(fee, dict) else _bool(row.get("fee_ok")),
        "already_held": _bool(_first(row, "has_pos", "already_held")),
        "trend_up_5d": None if p5 is None else p5 > 0,
        "momentum_dominant": None if dom in (None, "", "?") else dom == "MOM",
        "regime_risk_off": _any_known(ere_off, reg_off),
    }


def stock_facts(row):
    """unified_brain row: fused, q{passes_filter,p5,dom}, fee_check{pass}, has_pos, ere{safe}."""
    return _spot_facts(row)


def crypto_facts(row):
    """Same fields as stock_facts (unified_brain / dca / crypto paper rows)."""
    return _spot_facts(row)


def _allowed_fv(src, poly):
    try:
        import informed_before_create as ibc
    except Exception:
        return None
    return src in (ibc.POLY_FV_OK if poly else ibc.ALLOWED_FV)


def prediction_facts(opp, *, poly=False):
    """Allocator opp (+ `raw`, + create_preflight `informed`) or a Poly row. Unknown → None."""
    opp = opp if isinstance(opp, dict) else {}
    raw = opp.get("raw") if isinstance(opp.get("raw"), dict) else {}
    info = opp.get("informed") if isinstance(opp.get("informed"), dict) else {}
    m = {**raw, **opp, **info}
    edge = _num(_first(m, "edge", "edge_pts", "ev_per_dollar"))
    fee_true = _num(_first(m, "fee_true_edge", "net_edge"))
    src = str(m.get("fv_source") or "").strip().lower()
    hours = _num(_first(m, "hours_to_resolve", "hours_to_exp"))
    clock = _bool(m.get("clock_ok"))
    if clock is None and isinstance(m.get("late_observe"), bool):
        clock = not m["late_observe"]
    if clock is None and hours is not None:
        clock = hours > 0
    depth, min_depth = _num(m.get("depth")), _num(m.get("min_depth"))
    spread = _num(m.get("spread"))
    bid, ask = _num(m.get("bid")), _num(m.get("ask"))
    if spread is None and bid is not None and ask is not None:
        spread = ask - bid
    if spread is not None and spread > 1.0:  # cents: a binary spread over a dollar is impossible
        spread = spread / 100.0
    side = str(m.get("side") or "").strip().lower()
    return {
        "edge_positive": None if edge is None else edge > 0,
        "fee_true_edge_positive": None if fee_true is None else fee_true > 0,
        "fv_present": _num(m.get("fv")) is not None if "fv" in m else None,
        "fv_source_allowed": _allowed_fv(src, poly) if src else None,
        "official_resolution": _bool(_first(m, "official_resolution", "resolution_official")),
        "clock_ok": clock,
        "depth_ok": None if depth is None else depth >= (1.0 if min_depth is None else min_depth),
        "spread_tight": None if spread is None else 0.0 <= spread <= SPREAD_TIGHT,
        "side_yes": True if side == "yes" else False if side == "no" else None,
    }


# ---------------------------------------------------------------- veto rule (docstring)
def phi_multiplier(now=None):
    """(multiplier in [0.5, 3.0], phi_source). Reuses valedia_config._risk_factor (Kelly cap)."""
    try:
        import valedia_config as vc
        path = os.path.join(vc._MODELS, "latest_etherion_risk.json")
        if not os.path.exists(path):
            return 1.0, "etherion_missing"
        with open(path) as f:
            doc = json.load(f)
        lvl = str(doc.get("risk_level") or "").strip().upper() if isinstance(doc, dict) else ""
        if not lvl:
            return 1.0, "etherion_no_risk_level"
        try:
            t = datetime.fromisoformat(str(doc.get("ts"))).timestamp()
        except (TypeError, ValueError):
            t = os.path.getmtime(path)
        max_h = _env_float("VALEDIA_NIMBLE_PHI_MAX_AGE_H", PHI_MAX_AGE_H_DEFAULT, 0.0, 1e6)
        if ((time.time() if now is None else now) - t) / 3600.0 > max_h:
            return 1.0, "etherion_stale"
        rf = min(max(float(vc._risk_factor()), 0.0), 1.0)
        return round(PHI_MULT_LO + (PHI_MULT_HI - PHI_MULT_LO) * (1.0 - rf), 6), "etherion:" + lvl
    except Exception as e:
        return 1.0, "etherion_error_" + type(e).__name__


def break_even_p(asset_class, economics, side=None):
    """Break-even P(profitable) from caller economics (docstring), or None. Never raises."""
    e = economics if isinstance(economics, dict) else {}
    try:
        given = _num(e.get("break_even_p"))
        if given is not None:
            return given if given >= 0.0 else None
        if asset_class == "prediction":
            sd = str(e.get("side") or side or "").strip().lower()
            price = _num(e.get(sd + "_price")) if sd in ("yes", "no") else None
            price = _num(e.get("price")) if price is None else price
            price = price / 100.0 if price is not None and price > 1.0 else price  # cents
            if sd not in ("yes", "no") or price is None or not 0.0 < price < 1.0:
                return None
            extra = [_num(e.get(k)) for k in ("fee_per_contract", "half_spread", "slip")]
            return round(price + sum(max(0.0, x) for x in extra if x is not None), 6)
        if asset_class in ("stock", "crypto"):
            rt, move = _num(e.get("roundtrip_fee_pct")), _num(e.get("expected_move_pct"))
            if rt is None or rt < 0:
                return None
            return round(0.5 + rt / 2.0 / (move if move and move > 0 else 100.0), 6)
    except Exception:
        pass
    return None


def rule_inputs(asset_class, economics=None, side=None):
    """Everything the veto rule needs before the model answers. Code only, never sent."""
    be = break_even_p(asset_class, economics, side)
    mult, phi_src = phi_multiplier()
    margin, floor = round(base_margin() * mult, 6), p_floor()
    return {"min_sharpness": min_sharpness(), "p_floor": floor, "break_even_p": be,
            "margin": margin, "phi_multiplier": mult, "phi_source": phi_src,
            "required_p": floor if be is None else max(floor, round(be + margin, 6))}


def apply_rule(action, confidence, p_cal, rule):
    """'veto_model_buy' when the rule allows, else the veto reason."""
    if action != "buy":
        return "veto_model_%s" % action
    if confidence is None or confidence < rule["min_sharpness"]:
        return "veto_not_sharp"
    if p_cal is None:
        return "veto_no_p_buy"
    return "veto_p_below_required" if p_cal < rule["required_p"] else "veto_model_buy"


def _calibrate(source, p_buy):
    try:
        import calibration_ledger as cl
        return cl.calibrate_ex(source, p_buy)
    except Exception:
        return {"p_cal": p_buy, "calibration": "identity"}


# ---------------------------------------------------------------- decide
def _parse(body):
    ans = (body or {}).get("answers") or {}
    act = ans.get("action") if isinstance(ans.get("action"), dict) else {}
    choice = str(act.get("choice") or "").strip().lower()
    action = choice if choice in ACTIONS else None
    probs = s1._probs(act.get("probabilities"))
    conf = _num(act.get("confidence"))
    if conf is None and action and probs:
        conf = probs.get(action)
    if conf is not None and not 0.0 <= conf <= 1.0:
        conf = None
    # confidence is 1 - normalized entropy, not P(chosen): record the real odds beside it.
    lower = {str(k).strip().lower(): v for k, v in (probs or {}).items()}
    p_action = lower.get(action) if action else None
    p_buy = lower.get("buy")
    rk = ans.get("risk") if isinstance(ans.get("risk"), dict) else {}
    rc = str(rk.get("choice") or "").strip()
    return {"action": action, "confidence": conf, "action_probabilities": probs,
            "p_action": p_action, "p_buy": p_buy,
            "risk": int(rc) if rc in RISK_LABELS else None,
            "risk_probabilities": s1._probs(rk.get("probabilities"))}


def is_exit(candidate, side):
    c = candidate if isinstance(candidate, dict) else {}
    if str(side or "").strip().lower() in EXIT_SIDES or c.get("exit") is True:
        return True
    return str(c.get("action") or "").strip().lower() in EXIT_SIDES


VERDICT_EXTRA = ("action", "confidence", "risk", "latency_ms", "error", "p_action", "p_buy",
                 "p_cal", "calibration", "required_p", "break_even_p", "margin",
                 "phi_multiplier", "phi_source", "rule_reason")


def _verdict(mode, allow, reason, *, within_budget=False, **kw):
    v = {k: kw.get(k) for k in VERDICT_EXTRA}
    v.update(mode=mode, allow=bool(allow), reason=reason, within_budget=bool(within_budget))
    return v


def _no_call(mode, exit_, reason):
    """Mode-appropriate default without a model call. Veto blocks entries; exits pass."""
    if exit_:
        return _verdict(mode, True, "exit_never_blocked")
    if mode == "veto":
        return _verdict(mode, False, "veto_" + reason, error=reason)
    return _verdict(mode, True, reason)


def build_state(asset_class, candidate, side, code_facts, informed_facts_):
    """Identifiers + booleans only. Numbers in code_facts are dropped, never sent."""
    c = candidate if isinstance(candidate, dict) else {}
    keep = lambda d: {str(k): v for k, v in (d or {}).items() if v is None or isinstance(v, bool)}
    return {
        "asset_class": asset_class,
        "symbol": _first(c, "symbol", "ticker", "market", "slug"),
        "venue": _first(c, "venue", "platform", "desk"),
        "side": str(side or "").strip().lower() or None,
        "facts": keep(code_facts if isinstance(code_facts, dict) else {}),
        "informed": keep(informed_facts_ if isinstance(informed_facts_, dict) else {}),
        "create_order": False,
    }


def decide_candidate(asset_class, candidate, *, side, code_facts, informed=None, source,
                     label=None, post=None, get=None, economics=None):
    """Verdict {mode, allow, reason, within_budget} + VERDICT_EXTRA. Rule: module docstring.

    informed: informed_facts.informed_facts(...) output; None → computed here from local
    files (no network); {} → none. economics: break-even inputs, code only. Never raises.
    """
    cls = str(asset_class or "").strip().lower()
    mode = mode_for(cls)
    try:
        exit_ = is_exit(candidate, side)
        if cls not in CLASSES:
            return _no_call(mode, exit_, "bad_asset_class")
        if exit_:
            return _no_call(mode, True, "exit")
        if mode == "off":
            return _verdict(mode, True, "mode_off")
        if not s1.enabled():
            return _no_call(mode, False, "engine_disabled")
        if informed is None:
            sym = build_state(cls, candidate, side, {}, {})["symbol"]
            informed = inf.informed_facts(sym, side)
        inf_facts = informed.get("facts") if isinstance(informed, dict) else None
        state = build_state(cls, candidate, side, code_facts, inf_facts)
        warm(post=post, get=get)
        rule = rule_inputs(cls, economics, side)
        log_label = {"code": label, "mode": mode, "rule": rule,
                     "informed_sources": (informed or {}).get("sources")}
        res = s1.ask(state, QUESTIONS[cls], source="nimble_decide:%s" % source, label=log_label,
                     post=post, timeout=timeout_s(), parse=_parse, get=get)
    except Exception as e:  # defensive: a decision point must never crash here
        return _no_call(mode, False, "internal_" + type(e).__name__)
    ans = res.get("answers") or {}
    err = res.get("error") or (None if ans.get("action") else "bad_action")
    cal = _calibrate("nimble_decide:%s" % source, ans.get("p_buy"))
    rr = "veto_engine_error" if err else apply_rule(
        ans.get("action"), ans.get("confidence"), cal.get("p_cal"), rule)
    out = dict(ans, **rule, **cal, latency_ms=res.get("latency_ms"), error=err, rule_reason=rr,
               within_budget=res.get("within_budget"))  # _verdict keeps VERDICT_EXTRA keys only
    if mode == "shadow":  # computed and stamped, never enforced
        return _verdict(mode, True, "shadow_error" if err else "shadow_logged", **out)
    return _verdict(mode, rr == "veto_model_buy", rr, **out)


def decide_many(asset_class, items, *, source, cap_s=None, post=None, get=None,
                clock=time.monotonic):
    """Sequential decide_candidate over items, in order, with a wall-clock cap.

    items: dicts {candidate, side, code_facts, informed?, label?, economics?}. Once cap_s
    (default VALEDIA_NIMBLE_MANY_CAP_S, 30 s) has elapsed, no further calls are
    made; the rest get the mode default with reason cap_reached (shadow/off allow,
    veto blocks entries, exits always allowed). Returns one verdict per item.
    """
    cap = many_cap_s() if cap_s is None else float(cap_s)
    t0, out = clock(), []
    for it in items or []:
        it = it if isinstance(it, dict) else {}
        if clock() - t0 >= cap:
            mode = mode_for(asset_class)
            exit_ = is_exit(it.get("candidate"), it.get("side"))
            v = _no_call(mode, exit_, "cap_reached")
            if mode == "off" and not exit_:
                v = _verdict(mode, True, "mode_off")
            out.append(v)
            continue
        out.append(decide_candidate(
            asset_class, it.get("candidate"), side=it.get("side"),
            code_facts=it.get("code_facts"), informed=it.get("informed"), source=source,
            label=it.get("label"), post=post, get=get, economics=it.get("economics")))
    return out
