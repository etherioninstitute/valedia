#!/usr/bin/env python3
"""Code-computed informed facts for one candidate (stock, crypto, prediction).

The model never sees a raw score to compare. Every rule is here, in code, and
every output fact is a boolean. `sources` (provenance, ages, paths) is for the
decision log only and is not sent to the model.

Sources (all offline; this module NEVER makes a network call):
- MiroFish: only through mirofish_intel_archive.load_for_fv (fresh < 24h,
  source mirofish_swarm, not near-neutral, not an archive). Anything else is
  absent: every mirofish_* fact False.
- News: a pre-fetched Finnhub buzz dict (dynamic_universe.scan_finnhub_news
  shape {SYM: {score, reasons, price}}) passed by the caller, plus the cached
  intel_sentiment prediction_log.json (per-ticker latest direction < 24h).
- ERE: models/latest_etherion_risk.json, fresh < 24h by `timestamp` / `ts`.
- FOMO radar (crypto only): fomo_radar_enrich.enrich over its existing data file
  (data/crypto-paper/fomo_radar_fixture.json) when present. Exact symbol/pair
  match only. Annotate only: never sizes money. A dated file older than 24h is
  ignored; missing → every fomo_* fact False.
- intel_layer is skipped: IntelLayer() fetches Fear/Greed over the network.

Direction: "up" for buy/long, "down" for sell/short. A prediction side
(yes/no) has no underlying direction unless the caller passes `direction`.
"""
from __future__ import annotations

import json
import math
import os

import fomo_radar_enrich as fre
import mirofish_intel_archive as mia

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
_HOME_MODELS = os.path.expanduser("~/.openclaw/workspace/models")
MIROFISH_PATHS = (os.path.join(_ROOT, "models", "mirofish_signal.json"),
                  os.path.join(_HOME_MODELS, "mirofish_signal.json"))
ERE_PATHS = (os.path.join(_ROOT, "models", "latest_etherion_risk.json"),
             os.path.join(_HOME_MODELS, "latest_etherion_risk.json"))
PREDICTION_LOG_PATHS = (os.path.join(_HOME_MODELS, "prediction_log.json"),)
FOMO_PATHS = (fre.DEFAULT_FIXTURE,)
# Wallet conviction = sum((score/100)^2) over trusted real-trade wallets
# (fomo_radar_enrich.conviction). 1.5 ~ three trusted wallets at >= 71.
FOMO_CONVICTION_HIGH = 1.5
FOMO_FACTS = ("fomo_present", "fomo_wallet_conviction_high", "fomo_exit_signal",
              "fomo_provenance_ok")

MAX_AGE_S = mia.MAX_AGE_S  # ponytail: same 24h window as MiroFish
# MiroFish macro rule: macro_bias under 40 is risk off, or more RISK_OFF than
# RISK_ON events while macro_bias is under neutral (50) or missing.
MACRO_RISK_OFF_BELOW = 40.0
MACRO_NEUTRAL = 50.0
# ERE risk_level values that mean risk off (valedia_config._risk_factor < 1,
# intel_refresh HIGH/ELEVATED, adversarial_paper_fight CRITICAL/EXTREME/HALT).
ERE_RISK_OFF_LEVELS = frozenset({"HIGH", "ELEVATED", "CAUTION", "WARNING", "HALT",
                                 "DANGER", "CRITICAL", "EXTREME"})
_UP = frozenset({"BUY", "LONG", "BULL", "BULLISH", "UP", "STRONG_BUY"})
_DOWN = frozenset({"SELL", "SHORT", "BEAR", "BEARISH", "DOWN", "STRONG_SELL"})
_SIDE_UP = frozenset({"buy", "long"})
_SIDE_DOWN = frozenset({"sell", "short", "exit", "close"})


def _num(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return None
    return float(v)


def _read(path):
    try:
        with open(path) as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def direction_of(side, direction=None):
    """'up' / 'down' / None. An explicit direction wins; yes/no stays None."""
    d = str(direction or "").strip().lower()
    if d in ("up", "down"):
        return d
    s = str(side or "").strip().lower()
    return "up" if s in _SIDE_UP else "down" if s in _SIDE_DOWN else None


def symbol_keys(symbol):
    """Lookup variants: BTC-USD / BTC/USD / BTCUSD / BTCUSDT → BTC, BTC-USD."""
    s = str(symbol or "").strip().upper()
    if not s:
        return []
    base = s
    for suf in ("-USDT", "/USDT", "-USD", "/USD", "USDT", "USD"):
        if base.endswith(suf) and len(base) > len(suf):
            base = base[: -len(suf)]
            break
    out = []
    for k in (s, base, base + "-USD"):
        if k not in out:
            out.append(k)
    return out


def _lookup(table, symbol):
    if not isinstance(table, dict):
        return None
    for k in symbol_keys(symbol):
        if k in table:
            return table[k]
    return None


def _view(label, d):
    """d is 'up'/'down'/None; label is a direction word. Returns (agrees, conflicts)."""
    lab = str(label or "").strip().upper()
    if d is None or lab not in _UP | _DOWN:
        return False, False
    lab_up = lab in _UP
    return (lab_up == (d == "up")), (lab_up != (d == "up"))


def mirofish_facts(symbol, d, *, paths=None, now=None):
    doc, used = None, None
    for p in paths or MIROFISH_PATHS:
        doc = mia.load_for_fv(p, now)
        if doc:
            used = p
            break
    facts = {"mirofish_fresh": False, "mirofish_has_symbol": False, "mirofish_agrees": False,
             "mirofish_conflicts": False, "mirofish_macro_risk_off": False}
    if not doc:
        src = {"used": False, "reason": "absent_stale_or_not_swarm"}
        return facts, src
    sig = _lookup(doc.get("signals"), symbol)
    sig = sig if isinstance(sig, dict) else None
    label = None
    if sig is not None:
        label = sig.get("direction")
        if not label:  # no direction field: score outside the neutral band decides
            score = _num(sig.get("score"))
            if score is not None and abs(score - MACRO_NEUTRAL) > mia.NEUTRAL_BAND:
                label = "BUY" if score > MACRO_NEUTRAL else "SELL"
    agrees, conflicts = _view(label, d)
    facts.update(mirofish_fresh=True, mirofish_has_symbol=sig is not None,
                 mirofish_agrees=agrees, mirofish_conflicts=conflicts,
                 mirofish_macro_risk_off=macro_risk_off(doc))
    view = mia.evaluate(doc, None, now)
    src = {"used": True, "path": used, "as_of": view["as_of"], "age_s": view["age_s"],
           "report_id": doc.get("report_id"), "signal": sig}
    return facts, src


def macro_risk_off(doc):
    macro = _num((doc or {}).get("macro_bias"))
    off = on = 0
    for ev in (doc or {}).get("events") or []:
        tag = str((ev or {}).get("direction") or "").upper() if isinstance(ev, dict) else ""
        off += tag == "RISK_OFF"
        on += tag == "RISK_ON"
    if macro is not None and macro < MACRO_RISK_OFF_BELOW:
        return True
    return off > on and (macro is None or macro < MACRO_NEUTRAL)


def _age_s(ts, now):
    """ISO ts, or ERE's '%Y-%m-%d %H:%M UTC'. Unparseable → None (treated as stale)."""
    if isinstance(ts, str) and ts.strip().upper().endswith(" UTC"):
        ts = ts.strip()[:-4] + "+00:00"
    t = mia._parse(ts)
    return None if t is None else max(0, int((mia._utc(now) - t).total_seconds()))


def intel_prediction(symbol, *, paths=None, now=None):
    """Latest intel_sentiment prediction for this symbol within MAX_AGE_S. Or None."""
    for p in paths or PREDICTION_LOG_PATHS:
        doc = _read(p)
        if not doc:
            continue
        keys = set(symbol_keys(symbol))
        best = None
        for row in doc.get("predictions") or []:
            if not isinstance(row, dict) or str(row.get("ticker") or "").upper() not in keys:
                continue
            age = _age_s(row.get("ts"), now)
            if age is None or age >= MAX_AGE_S:
                continue
            if best is None or age < best["age_s"]:
                best = {"direction": row.get("direction"), "age_s": age, "path": p}
        return best
    return None


def news_facts(symbol, d, *, news=None, prediction_log_paths=None, now=None):
    """Finnhub buzz: score > 0 bullish, score < 0 bearish (buzz keywords are
    bullish). intel_sentiment: latest BUY/SELL < 24h. Both sides → mixed."""
    buzz = _lookup(news, symbol) if isinstance(news, dict) else None
    buzz = buzz if isinstance(buzz, dict) else None
    score = _num((buzz or {}).get("score"))
    pred = intel_prediction(symbol, paths=prediction_log_paths, now=now)
    plab = str((pred or {}).get("direction") or "").upper()
    bull = bool(score is not None and score > 0) or plab in _UP
    bear = bool(score is not None and score < 0) or plab in _DOWN
    present = buzz is not None or pred is not None
    agrees = conflicts = False
    if d is not None and bull != bear:
        agrees, conflicts = _view("BUY" if bull else "SELL", d)
    facts = {"news_present": present, "news_bullish": bull, "news_bearish": bear,
             "news_agrees": agrees, "news_conflicts": conflicts}
    src = {"finnhub_provided": isinstance(news, dict), "finnhub": buzz, "intel_prediction": pred}
    return facts, src


def ere_facts(symbol, d, *, paths=None, now=None):
    """risk_off from risk_level; fade conflict: buy vs FADE_SHORT, sell vs FADE_LONG."""
    facts = {"ere_fresh": False, "ere_risk_off": False, "ere_fade_side_conflict": False}
    for p in paths or ERE_PATHS:
        doc = _read(p)
        if not doc:
            continue
        age = _age_s(doc.get("timestamp") or doc.get("ts"), now)
        src = {"path": p, "age_s": age, "source": doc.get("source"),
               "risk_level": doc.get("risk_level"), "regime": doc.get("regime")}
        if age is None or age >= MAX_AGE_S:
            src["used"] = False
            continue
        sig = _lookup(doc.get("signals"), symbol)
        fade = str((sig or {}).get("signal") or "").upper() if isinstance(sig, dict) else ""
        src.update(used=True, signal=fade or None)
        facts.update(
            ere_fresh=True,
            ere_risk_off=str(doc.get("risk_level") or "").upper() in ERE_RISK_OFF_LEVELS,
            ere_fade_side_conflict=(d == "up" and fade == "FADE_SHORT")
            or (d == "down" and fade == "FADE_LONG"))
        return facts, src
    return facts, {"used": False, "reason": "absent_or_stale"}


def _fomo_tag(enr, symbol):
    """Exact symbol/pair match only (a Kalshi ticker or Poly slug never matches by prefix)."""
    keys = list(symbol_keys(symbol))
    for k in list(keys):
        if "/" in k:
            keys.append(k.replace("/", "-"))
    for k in keys:
        for table in ((enr or {}).get("pairs") or {}, (enr or {}).get("symbols") or {}):
            if k in table:
                return k, table[k]
    return None, None


def fomo_facts(symbol, d, *, paths=None, asset_class=None, now=None):
    """Offline FOMO radar tags for one crypto symbol. Direction-free facts.

    present: the radar file has this symbol. conviction_high: provenance ok and
    wallet_conviction >= FOMO_CONVICTION_HIGH. exit_signal: radar exit tag
    (closed/trimmed by >= 2 sellers, provenance ok). provenance_ok: trusted
    real wallets, not seeded. Non-crypto asset_class → not looked up.
    """
    facts = {k: False for k in FOMO_FACTS}
    if asset_class is not None and str(asset_class).strip().lower() != "crypto":
        return facts, {"used": False, "reason": "not_crypto"}
    for p in paths or FOMO_PATHS:
        if not os.path.exists(p):
            continue
        try:
            with open(p) as f:
                doc = json.load(f)
        except (OSError, ValueError):
            continue
        ts = doc.get("as_of") or doc.get("ts") or doc.get("timestamp") if isinstance(doc, dict) else None
        src = {"used": True, "path": p, "dated": ts is not None, "annotate_only": True,
               "note": doc.get("note") if isinstance(doc, dict) else None}
        if ts is not None:
            age = _age_s(ts, now)
            src["age_s"] = age
            if age is None or age >= MAX_AGE_S:
                return facts, dict(src, used=False, reason="stale")
        key, tag = _fomo_tag(fre.enrich(doc), symbol)
        src["symbol_found"] = tag is not None
        if tag is None:
            return facts, src
        ok = bool(tag.get("provenance_ok"))
        conv = _num(tag.get("wallet_conviction"))
        facts.update(fomo_present=True, fomo_provenance_ok=ok,
                     fomo_wallet_conviction_high=ok and conv is not None and conv >= FOMO_CONVICTION_HIGH,
                     fomo_exit_signal=tag.get("exit") == "exit")
        src["tag"] = {"key": key, "wallet_conviction": conv, "burst": tag.get("burst"),
                      "exit": tag.get("exit"), "seeded": tag.get("seeded"),
                      "judgement_rank": tag.get("judgement_rank")}
        return facts, src
    return facts, {"used": False, "reason": "absent"}


def informed_facts(symbol, side=None, *, direction=None, news=None, mirofish_paths=None,
                   ere_paths=None, prediction_log_paths=None, fomo_paths=None, asset_class=None,
                   now=None):
    """{"facts": {booleans}, "sources": {provenance}}. Never raises, never fetches."""
    d = direction_of(side, direction)
    facts, sources = {}, {"direction": d, "intel_layer": "skipped_network"}
    for name, fn, kw in (
            ("mirofish", mirofish_facts, {"paths": mirofish_paths}),
            ("news", news_facts, {"news": news, "prediction_log_paths": prediction_log_paths}),
            ("ere", ere_facts, {"paths": ere_paths}),
            ("fomo", fomo_facts, {"paths": fomo_paths, "asset_class": asset_class})):
        try:
            f, s = fn(symbol, d, now=now, **kw)
        except Exception as e:  # a broken file must not break the caller
            f, s = {}, {"error": type(e).__name__}
        facts.update(f)
        sources[name] = s
    for k in ("mirofish_fresh", "mirofish_has_symbol", "mirofish_agrees", "mirofish_conflicts",
              "mirofish_macro_risk_off", "news_present", "news_bullish", "news_bearish",
              "news_agrees", "news_conflicts", "ere_fresh", "ere_risk_off",
              "ere_fade_side_conflict") + FOMO_FACTS:
        facts[k] = bool(facts.get(k, False))
    facts["any_conflict"] = (facts["mirofish_conflicts"] or facts["news_conflicts"]
                             or facts["ere_fade_side_conflict"])
    facts["any_risk_off"] = facts["mirofish_macro_risk_off"] or facts["ere_risk_off"]
    return {"facts": facts, "sources": sources}
