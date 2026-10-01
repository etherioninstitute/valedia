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

No network of its own, no keys, no order code.
"""
from __future__ import annotations

import re
import time

import informed_facts as inf
import nimble_decide as nd
import systemone_hotpath_client as s1

SKIP = "SKIP_NIMBLE"
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
            "within_budget": False, "error": error}


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


def gate(asset_class, symbol, row=None, *, source, side="buy", venue=None, label=None,
         code_facts=None, news=None):
    """Nimble verdict for one ENTRY candidate, or None when there is nothing to do.

    row: the decision point's own numbers (fused, q, fee_check, has_pos, ere, ...);
    stock_facts / crypto_facts turn them into booleans in code. Unknown stays None.
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
        informed = inf.informed_facts(sym, side, news=news) if on else {}
        if on:
            warm_once()
        return nd.decide_candidate(
            cls, {"symbol": sym, "venue": venue}, side=side, code_facts=facts,
            informed=informed, source=source, label=label,
            post=_HOOKS["post"], get=_HOOKS["get"])
    except Exception as e:  # a wiring error must never crash a decision point
        return _fallback(mode, mode != "veto", "wiring_" + type(e).__name__,
                         type(e).__name__)


# ---------------------------------------------------------------- DCA buy wrapper
def wrap_buy(buy_fn, *, source, asset_class="crypto", venue="coinbase", rows=None):
    """buy_fn(pair, amount) with a gate in front. A veto raises NimbleSkip (not placed).

    The wrapped function keeps `.verdicts` {pair: verdict} for the caller's log.
    """
    verdicts = {}

    def _buy(pair, amount, *a, **k):
        v = gate(asset_class, pair, (rows or {}).get(pair), source=source, venue=venue)
        if v is not None:
            verdicts[pair] = v
        if blocked(v):
            raise NimbleSkip("%s %s" % (SKIP, why(v)))
        return buy_fn(pair, amount, *a, **k)

    _buy.verdicts = verdicts
    return _buy


# ---------------------------------------------------------------- movers scan (shadow only)
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
                    "code_facts": s1.facts(r, limits), "label": r.get("label"),
                    "informed": inf.informed_facts(sym, "buy", news=news)})
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
