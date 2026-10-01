#!/usr/bin/env python3
"""Wiring helpers: Nimble 2 (nimble_decide) at every stock + crypto entry point.

One call per ENTRY candidate, at the LAST step before a buy / paper fill, after
every existing code gate passed. The code's decision is never changed in shadow
(the default): the verdict is stamped and logged (decisions.jsonl) and that is all.
In veto mode (VALEDIA_NIMBLE_MODE_STOCK / _CRYPTO) a verdict with allow False
removes the entry with reason SKIP_NIMBLE. It never adds, sizes, flips or
re-prices an order, and exits / sells are never asked and never blocked.

gate() returns None when there is nothing to do: the class is not stock/crypto,
the side is an exit, mode is off, or the engine is off in a non-veto mode
(VALEDIA_SYSTEMONE unset → no work, no POST, no stamp, identical behavior).
It never raises: an unexpected error is shadow → allow (stamp carries the
error), veto → block the entry.

Venue-writer guard (not Nimble): every live spot BUY must first pass
venue_buy_allowed(venue), i.e. valedia_config.venue_writer(venue) == "valedia"
(coinbase / alpaca). Any other writer value ("nautilus", "none", unknown venue)
refuses the buy with SKIP_VENUE_WRITER. Exits / sells are deliberately NOT
guarded: the writer lock decides who may OPEN exposure; blocking a sell would
strand an existing position, and the existing semantics (valedia_config
assert_exclusive_writer / nautilus_may_dispatch) only gate who dispatches
orders, never forbid closing one. Today both venues are "valedia" (no change).

No network of its own, no keys, no order code.
"""
from __future__ import annotations

import re
import time

import informed_facts as inf
import nimble_decide as nd
import systemone_hotpath_client as s1

SKIP = "SKIP_NIMBLE"
SKIP_WRITER = "SKIP_VENUE_WRITER"
SPOT = ("stock", "crypto")
_HOOKS = {"post": None, "get": None}  # tests only: fake transport
# ponytail: bases a desk ticker may carry without a quote suffix (BTCUSD, BINANCE:ETHUSDT).
_CRYPTO_BASES = frozenset(
    "BTC ETH SOL XRP DOGE ADA AVAX LINK DOT LTC BCH MATIC POL ATOM UNI SHIB PEPE ARB OP "
    "NEAR APT SUI TRX XLM ALGO FIL HBAR".split())
_QUOTES = ("-USDT", "/USDT", "-USDC", "/USDC", "-USD", "/USD", "USDT", "USDC", "USD")
_EQUITY_CLS = frozenset({"equity", "stock", "etf"})


class NimbleSkip(Exception):
    """Raised by a wrapped buy function: the buy was vetoed and was not placed."""


class VenueWriterSkip(Exception):
    """Raised by a wrapped buy function: this venue's live writer is not valedia."""


def venue_buy_allowed(venue):
    """True only when valedia_config.venue_writer(venue) == "valedia". Fail-closed on error."""
    try:
        import valedia_config as vc
        return vc.venue_writer(venue) == "valedia"
    except Exception:
        return False


def writer_skip_line(name, venue):
    return "  %-6s %s %s writer!=valedia" % (name, SKIP_WRITER, venue)


# ---------------------------------------------------------------- classify
def _strip_quote(s):
    for q in _QUOTES:
        if s.endswith(q) and len(s) > len(q):
            return s[: -len(q)], q
    return s, ""


def classify(symbol, asset_cls=None):
    """'stock' | 'crypto' | None (None = not a spot name: no gate)."""
    a = str(asset_cls or "").strip().lower()
    if a == "crypto":
        return "crypto"
    if a in _EQUITY_CLS:
        return "stock"
    if a:
        return None
    s = str(symbol or "").strip().upper().split(":")[-1]
    if not s or "." in s or "!" in s:
        return None  # futures / perps / odd tickers
    base, quote = _strip_quote(s)
    if base in _CRYPTO_BASES or (quote and quote[0] in "-/") or quote in ("USDT", "USDC"):
        return "crypto"
    return "stock" if re.fullmatch(r"[A-Z]{1,5}", s) else None


def norm_symbol(symbol, cls):
    s = str(symbol or "").strip().upper().split(":")[-1]
    if cls == "crypto":
        return _strip_quote(s)[0] + "-USD"
    return s


# ---------------------------------------------------------------- verdicts
def _fallback(mode, allow, reason, error):
    return {"action": None, "confidence": None, "risk": None, "mode": mode,
            "allow": bool(allow), "reason": reason, "latency_ms": None,
            "within_budget": False, "error": error, "p_action": None, "p_buy": None}


def blocked(v):
    """True only for an explicit veto-mode refusal. Shadow can never block."""
    return isinstance(v, dict) and v.get("mode") == "veto" and v.get("allow") is False


def why(v):
    return str((v or {}).get("reason") or "")


def stamp(target, v, key="nimble"):
    """Record the verdict on a row / decision dict. No-op when v is None."""
    if v is not None and isinstance(target, dict):
        target[key] = v
    return target


def skip_line(name, v):
    return "  %-6s %s %s" % (name, SKIP, why(v))


def note(name, v):
    """Report lines for a verdict: [] when None, SKIP_NIMBLE when blocked, else a stamp."""
    if v is None:
        return []
    if blocked(v):
        return [skip_line(name, v)]
    return ["  %-6s nimble %s conf=%s risk=%s mode=%s" % (
        name, v.get("action"), v.get("confidence"), v.get("risk"), v.get("mode"))]


def warm_once():
    """Warmup once per process; only when the engine is on. Never raises."""
    try:
        return nd.warm(post=_HOOKS["post"], get=_HOOKS["get"])
    except Exception:
        return None


def spot_economics(venue, row=None):
    """{"roundtrip_fee_pct": ...} from fee_engine's STATIC schedule for `venue` (no network),
    or None for an unknown / paper venue (the probability floor applies). Notional: the row's
    amount / amount_usd / notional, else $100. Code only; never sent to the model.
    """
    try:
        import fee_engine
        v = str(venue or "").strip().lower()
        if v not in fee_engine.STATIC_FEES:
            return None
        r = row if isinstance(row, dict) else {}
        amt = 100.0
        for k in ("amount", "amount_usd", "notional"):
            if isinstance(r.get(k), (int, float)) and not isinstance(r.get(k), bool) and r[k] > 0:
                amt = float(r[k])
                break
        rt = fee_engine.roundtrip_cost(v, amt, use_static=True)["roundtrip_pct"]
        return {"roundtrip_fee_pct": float(rt), "venue": v}
    except Exception:
        return None


def gate(asset_class, symbol, row=None, *, source, side="buy", venue=None, label=None,
         code_facts=None, news=None, economics=None):
    """Nimble verdict for one ENTRY candidate, or None when there is nothing to do.

    row: the decision point's own numbers (fused, q, fee_check, has_pos, ere, ...);
    stock_facts / crypto_facts turn them into booleans in code. Unknown stays None.
    economics: break-even inputs for the veto rule (None -> spot_economics(venue, row)).
    """
    mode = "shadow"
    try:
        cls = classify(symbol, asset_class)
        if cls is None or nd.is_exit({}, side):
            return None
        mode = nd.mode_for(cls)
        if mode == "off" or (mode != "veto" and not s1.enabled()):
            return None
        on = s1.enabled()
        facts = code_facts if code_facts is not None else (
            nd.crypto_facts(row) if cls == "crypto" else nd.stock_facts(row))
        sym = norm_symbol(symbol, cls)
        informed = inf.informed_facts(sym, side, news=news, asset_class=cls) if on else {}
        if on:
            warm_once()
        return nd.decide_candidate(
            cls, {"symbol": sym, "venue": venue}, side=side, code_facts=facts,
            informed=informed, source=source, label=label,
            post=_HOOKS["post"], get=_HOOKS["get"],
            economics=economics if economics is not None else spot_economics(venue, row))
    except Exception as e:  # a wiring error must never crash a decision point
        return _fallback(mode, mode != "veto", "wiring_" + type(e).__name__,
                         type(e).__name__)


# ---------------------------------------------------------------- DCA facts
# DCA is a buy-and-hold accumulation, so there is no expected return to clear. fee_ok is
# therefore a ceiling on the static round-trip cost: fee_engine.roundtrip_cost(...)
# ["roundtrip_pct"] <= DCA_FEE_MAX_RT_PCT. 2.5 sits just above Coinbase's static 2.2%
# (fee_engine.STATIC_FEES) so a normal Coinbase DCA passes and a fee jump fails.
DCA_FEE_MAX_RT_PCT = 2.5


def dca_row(venue, amount, *, score=None, prices=None):
    """Row for gate() built only from what a DCA decision actually knows. Unknown stays absent.

    score  -> fused: nd._spot_facts turns it into fused_strong with nd.FUSED_STRONG (same
              code threshold as unified_brain); None when the caller has no kernel score.
    prices -> q.p5 = p[-1]/p[-6]-1 (same formula as unified_brain.compute_signal_quality)
              -> trend_up_5d; absent when fewer than 7 closes.
    already_held is deliberately NEVER set: DCA re-buys held names by design, and the spot
    question lists "already held" as a skip red flag, so a True here would veto every
    routine DCA buy. It stays None (unknown). The brain BUY path still passes has_pos.
    amount -> fee_ok from fee_engine static round trip vs DCA_FEE_MAX_RT_PCT.
    filter_passed, momentum_dominant, regime_risk_off are not known to DCA: left None.
    """
    row = {}
    if isinstance(score, (int, float)) and not isinstance(score, bool):
        row["fused"] = float(score)
    try:
        import fee_engine
        rt = fee_engine.roundtrip_cost(venue, float(amount), use_static=True)
        row["fee_check"] = {"pass": float(rt["roundtrip_pct"]) <= DCA_FEE_MAX_RT_PCT}
    except Exception:
        pass
    try:
        p = [float(x) for x in prices] if prices is not None else []
        if len(p) > 6 and p[-6] > 0:
            row["q"] = {"p5": p[-1] / p[-6] - 1.0}
    except Exception:
        pass
    return row


# ---------------------------------------------------------------- DCA buy wrapper
def wrap_buy(buy_fn, *, source, asset_class="crypto", venue="coinbase", rows=None):
    """buy_fn(pair, amount) with a gate in front. A veto raises NimbleSkip (not placed);
    a venue whose writer is not valedia raises VenueWriterSkip before any Nimble call.

    The wrapped function keeps `.verdicts` {pair: verdict} for the caller's log.
    """
    verdicts = {}

    def _buy(pair, amount, *a, **k):
        if not venue_buy_allowed(venue):
            raise VenueWriterSkip("%s %s writer!=valedia" % (SKIP_WRITER, venue))
        v = gate(asset_class, pair, (rows or {}).get(pair), source=source, venue=venue)
        if v is not None:
            verdicts[pair] = v
        if blocked(v):
            raise NimbleSkip("%s %s" % (SKIP, why(v)))
        return buy_fn(pair, amount, *a, **k)

    _buy.verdicts = verdicts
    return _buy


# ---------------------------------------------------------------- movers scan (shadow only)
# Movers rows carry s1.facts names; the spot question (nimble_decide._SPOT_BUY/_SPOT_SKIP)
# describes the nd._spot_facts names. Map only what means the same thing, never invent:
#   edge_positive   -> fee_ok        (row "edge" is the fee-true edge; > 0 means fees are cleared)
#   clears_size_bar -> filter_passed (the scan's own print-size filter; None stays None)
# has_print / thin_tape / is_oil / is_weather have no counterpart in the question text.
# The original s1 names are kept alongside (superset) so existing log readers keep working;
# fused_strong, already_held, trend_up_5d, momentum_dominant, regime_risk_off stay absent
# (a scan row has no such inputs), never defaulted.
MOVERS_FACT_MAP = {"edge_positive": "fee_ok", "clears_size_bar": "filter_passed"}


def movers_facts(row, limits):
    f = dict(s1.facts(row, limits))
    for src, dst in MOVERS_FACT_MAP.items():
        if src in f:
            f[dst] = f[src]
    return f


def scan_class(row):
    cls = str((row or {}).get("class") or "").lower()
    if (row or {}).get("oil_fenced"):
        return None
    return "crypto" if cls == "crypto" else "stock" if cls in _EQUITY_CLS else None


def _scan_envelope(v):
    """Keep the legacy `systemone_shadow` stamp shape so s1.summary / proof keep working."""
    ok = v.get("action") is not None and not v.get("error")
    return {
        "backend": "systemone_http" if ok else "stub", "fail_open": not ok,
        "error": v.get("error"), "mode": "shadow", "enforcement": "none",
        "create_order": False, "model": s1.model_pin() or None, "judge": "dont_trade",
        "answers": {"nimble_action": v.get("action"),
                    "nimble_confidence": v.get("confidence"),
                    "nimble_risk": v.get("risk")},
        "latency_ms": v.get("latency_ms"), "client_ms": v.get("latency_ms"),
        "within_budget": bool(v.get("within_budget")),
    }


def scan_rows(ranked, limits, *, post=None, get=None, clock=time.monotonic):
    """Stamp row['nimble'] (+ legacy row['systemone_shadow']) on stock/crypto mover rows.

    Shadow semantics regardless of mode: the scan never sizes, so the verdict is
    recorded with enforced=False and nothing reads allow. Weather / oil / other
    rows stay code-labelled and get no call. One wall-clock cap across classes.
    Never raises.
    """
    try:
        if not s1.enabled():
            return 0
        t0, cap, n = clock(), nd.many_cap_s(), 0
        for cls in SPOT:
            rows = [r for r in ranked if scan_class(r) == cls]
            if not rows or nd.mode_for(cls) == "off":
                continue
            items = []
            for r in rows:
                sym = norm_symbol(r.get("symbol"), cls)
                buzz = r.get("buzz")
                news = {sym: {"score": buzz, "reasons": r.get("reasons")}} if (
                    isinstance(buzz, (int, float)) and buzz > 0) else None
                items.append({
                    "candidate": {"symbol": sym, "venue": "movers_scan"}, "side": "buy",
                    "code_facts": movers_facts(r, limits), "label": r.get("label"),
                    "informed": inf.informed_facts(sym, "buy", news=news, asset_class=cls)})
            out = nd.decide_many(
                cls, items, source="movers_scan",
                cap_s=max(0.0, cap - (clock() - t0)),
                post=post or _HOOKS["post"], get=get or _HOOKS["get"], clock=clock)
            for r, v in zip(rows, out):
                r["nimble"] = dict(v, enforced=False)
                r["systemone_shadow"] = _scan_envelope(v)
                n += 1
        return n
    except Exception:
        return 0
