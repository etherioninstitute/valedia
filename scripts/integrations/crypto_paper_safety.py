"""CE-1 fail-closed safety gate for the crypto paper desk.

Indicators are computed in crypto_paper_indicators. This module asks
Ornith (LiteLLM alias ornith-1.5-k-m at :4000) to mark every required
entry/risk condition. A paper fill happens only when Ornith returns a
complete PASS and the local numbers agree. Model down, bad JSON, a
missing condition, a Qwen model name, or a local disagreement refuses.
No venue order, no Qwen retry. A CE-2 tick may rank the watchlist
with Qwen first. That call is not this gate.

(c) GOATWORTHY Post LLC.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

import crypto_paper as cp
import crypto_paper_indicators as ind
import nimble_wiring_spot as nws
import valedia_config as vc

MODEL = "ornith-1.5-k-m"
DEFAULT_URL = "http://127.0.0.1:4000/v1/chat/completions"
ROUTE = "litellm:4000 alias ornith-1.5-k-m"
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
DEFAULT_RULES = os.path.join(_ROOT, "data", "crypto-paper", "rules.json")
DEFAULT_BARS = os.path.join(_ROOT, "data", "crypto-paper", "bars_safety_pass.json")
DEFAULT_LOG = os.path.join(_ROOT, "data", "crypto-paper", "safety-check-log.json")

_OPS = {
    "gt": lambda a, b: a > b,
    "lt": lambda a, b: a < b,
    "gte": lambda a, b: a >= b,
    "lte": lambda a, b: a <= b,
    "eq": lambda a, b: a == b,
}


def load_rules(path=None):
    with open(path or DEFAULT_RULES) as f:
        doc = json.load(f)
    if doc.get("mode") not in (None, "PAPER/SHADOW"):
        raise ValueError("rules mode must be PAPER/SHADOW")
    return doc


def _num(v):
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _lookup(token, env):
    if isinstance(token, str) and token in env:
        return env[token]
    return token


def eval_one(check, env):
    op = str(check.get("op") or "")
    actual = _lookup(check.get("left"), env)
    threshold = _lookup(check.get("right"), env)
    if op == "in":
        ok = actual in (threshold or [])
    elif op in _OPS:
        a, b = _num(actual), _num(threshold)
        ok = a is not None and b is not None and _OPS[op](a, b)
    else:
        ok = False
    return {
        "id": check.get("id") or "",
        "label": check.get("label") or check.get("id") or "",
        "op": op,
        "pass": bool(ok),
        "actual": actual,
        "threshold": threshold,
    }


def bias_of(rules, env):
    groups = {}
    for check in rules.get("bias_criteria") or []:
        groups.setdefault(check.get("bias") or "neutral", []).append(check)
    rows = []
    for name in ("long", "short"):
        checks = groups.get(name) or []
        if not checks:
            continue
        ev = [eval_one(c, env) for c in checks]
        rows.extend(ev)
        if all(e["pass"] for e in ev):
            return name, rows
    return "neutral", rows


def _checks(rules, key, bias=None):
    node = rules.get(key) or []
    if isinstance(node, dict):
        return list(node.get(bias) or [])
    return list(node)


def local_view(rules, env, bias):
    """Entry + risk are required. Exit is recorded and does not open a fill."""
    required = _checks(rules, "entry_rules", bias) + list(rules.get("risk_rules") or [])
    extra = _checks(rules, "exit_rules")
    rows = [eval_one(c, env) for c in required + extra]
    req_ids = [c.get("id") for c in required]
    by_id = {r["id"]: r for r in rows}
    failed = [i for i in req_ids if not (by_id.get(i) or {}).get("pass")]
    return {
        "bias": bias,
        "required": req_ids,
        "conditions": rows,
        "failed_conditions": failed,
        "pass": bias == "long" and not failed,
        "evidence": {r["id"]: {"pass": r["pass"], "value": r["actual"], "threshold": r["threshold"]}
                     for r in rows},
    }


def proposal_for(snap, pair, cash, clip=None):
    close = snap.get("close")
    if not close or float(close) <= 0:
        return None
    frac = float(cp.CLIP_FRAC if clip is None else clip)
    qty = (float(cash) * frac) / float(close)
    if qty <= 0:
        return None
    return {
        "pair": cp.norm_pair(pair),
        "side": "buy",
        "qty": qty,
        "notional_frac": frac,
        "mid": float(close),
    }


def _litellm_key():
    """Bearer for LiteLLM :4000. Env first, then ~/.litellm/env, then master.key.

    Chat requires the master key; /health does not. Missing key → HTTP 401.
    """
    for name in ("LITELLM_MASTER_KEY", "LITELLM_API_KEY"):
        val = os.environ.get(name)
        if val:
            return val.strip()
    path = os.path.expanduser("~/.litellm/env")
    try:
        lines = open(path).read().splitlines()
    except OSError:
        lines = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, val = line.split("=", 1)
        if key.strip() in ("LITELLM_MASTER_KEY", "LITELLM_API_KEY"):
            return val.strip().strip('"').strip("'")
    try:
        mk = open(os.path.expanduser("~/.litellm/master.key")).read().strip()
    except OSError:
        mk = ""
    return mk


def _http_post(url, payload, timeout):
    data = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    key = _litellm_key()
    if not key:
        # Fail closed before the wire so FEED/gate see ornith_auth, not a bare 401.
        raise urllib.error.URLError("ornith_auth: no LITELLM_MASTER_KEY (env/~/.litellm/env/master.key)")
    headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            body = ""
        raise urllib.error.URLError(
            f"ornith_http_{exc.code}: {body or exc.reason}"
        ) from exc


def _content(raw):
    if isinstance(raw, str):
        return raw
    if not isinstance(raw, dict):
        return ""
    if "choices" not in raw and "pass" in raw:
        return json.dumps(raw)
    try:
        msg = raw["choices"][0]["message"] or {}
    except (KeyError, IndexError, TypeError):
        return ""
    content = msg.get("content") or ""
    if content:
        return content
    # Ornith may burn max_tokens on reasoning_content when LiteLLM
    # drop_params strips reasoning_effort=none. Prefer visible content;
    # fall back so a JSON object in reasoning still parses.
    return msg.get("reasoning_content") or ""


def extract_json(text):
    text = (text or "").strip()
    if not text:
        return None
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        i, j = text.find("{"), text.rfind("}")
        if i < 0 or j <= i:
            return None
        try:
            obj = json.loads(text[i:j + 1])
        except json.JSONDecodeError:
            return None
    return obj if isinstance(obj, dict) else None


def _prompt(local, proposal):
    brief = [{
        "id": c["id"], "op": c["op"], "actual": c["actual"],
        "threshold": c["threshold"], "local_pass": c["pass"],
    } for c in local["conditions"] if c["id"] in local["required"]]
    return (
        "Fail-closed PAPER safety check. Do not place an order. "
        "Reply with one JSON object only, keys: "
        "pass (bool), bias (string), failed_conditions (array of ids), "
        "evidence (object, one key per required id, each value has pass and value), "
        "model (string, exactly ornith-1.5-k-m). "
        "pass is true only when every required id passes. Use these numbers.\n"
        + json.dumps({
            "required": local["required"],
            "bias": local["bias"],
            "proposal": {"pair": proposal["pair"], "side": proposal["side"],
                         "notional_frac": proposal["notional_frac"]},
            "conditions": brief,
        }, default=str)
    )


def _model_ok(name):
    low = str(name or "").lower()
    return "ornith" in low and "qwen" not in low


def _ornith_shape(obj, required):
    if not isinstance(obj, dict) or not isinstance(obj.get("pass"), bool):
        return "malformed"
    if not isinstance(obj.get("failed_conditions"), list):
        return "malformed"
    if not isinstance(obj.get("evidence"), dict):
        return "malformed"
    if not _model_ok(obj.get("model")):
        return "model_rejected"
    missing = [i for i in required if i not in obj["evidence"]]
    if missing:
        return "missing_condition"
    if obj["pass"] and obj["failed_conditions"]:
        return "pass_with_failures"
    if obj["pass"]:
        for i in required:
            row = obj["evidence"][i]
            flag = row.get("pass") if isinstance(row, dict) else None
            if flag is not True:
                return "evidence_not_pass"
    return ""


def _blank(rules, snap, bias, reason, model, failed, conditions):
    ts = cp._now()
    return {
        "ts": ts,
        "timestamp": ts,
        "pass": False,
        "bias": bias,
        "failed_conditions": list(failed),
        "evidence": {c["id"]: {"pass": c["pass"], "value": c["actual"], "threshold": c["threshold"]}
                     for c in conditions},
        "model": model,
        "route": ROUTE,
        "symbol": snap.get("pair") or "",
        "timeframe": rules.get("timeframe") or "1h",
        "price": snap.get("close"),
        "indicators": {k: snap.get(k) for k in snap if k != "pair"},
        "conditions": conditions,
        "allPass": False,
        "paperTrading": True,
        "orderPlaced": False,
        "orderId": None,
        "would_have_fill": None,
        "create_order": False,
        "live": False,
        "mode": vc.CRYPTO_PAPER_MODE,
        "reason": reason,
        "valedia_version": vc.VALEDIA_VERSION,
    }


def judge(rules, snap, proposal, *, post_fn=None, url=None, timeout=20):
    """Return a decision. pass is true only for a complete Ornith PASS that matches local math."""
    env = dict(snap)
    if proposal:
        env.update(proposal)
    bias, bias_rows = bias_of(rules, env)
    if proposal is None or bias != "long":
        dec = _blank(rules, env, bias, "no_entry", "none", ["no_entry"], bias_rows)
        dec["symbol"] = (proposal or {}).get("pair") or env.get("pair") or ""
        return dec
    local = local_view(rules, env, bias)
    local["conditions"] = bias_rows + local["conditions"]
    url = url or DEFAULT_URL
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": _prompt(local, proposal)}],
        # ponytail: LiteLLM drop_params strips reasoning_effort, so Ornith
        # thinks before the JSON. 1200 finished on length once MACD rows
        # were in the prompt (reasoning ~3k chars, JSON cut). 2400 leaves
        # room for the object. Empty or cut content is still a refuse.
        "max_tokens": 2400,
        "temperature": 0,
        "reasoning_effort": "none",
    }
    try:
        raw = post_fn(payload) if post_fn else _http_post(url, payload, timeout)
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        msg = str(getattr(exc, "reason", "") or exc)
        if "ornith_auth" in msg:
            reason = "ornith_auth"
        elif "ornith_http_401" in msg or "401" in msg:
            reason = "ornith_auth"
        elif "ornith_http_" in msg:
            reason = "ornith_down"
        else:
            reason = "ornith_down"
        dec = _blank(rules, env, bias, reason, "down",
                     local["failed_conditions"] or [reason], local["conditions"])
        dec["symbol"] = proposal["pair"]
        dec["ornith_error"] = msg[:400]
        return dec
    except Exception as exc:
        if isinstance(exc, AssertionError):
            raise
        dec = _blank(rules, env, bias, "ornith_down", "down",
                     local["failed_conditions"] or ["ornith_down"], local["conditions"])
        dec["symbol"] = proposal["pair"]
        dec["ornith_error"] = f"{type(exc).__name__}: {exc}"[:400]
        return dec
    obj = extract_json(_content(raw))
    shape = _ornith_shape(obj, local["required"]) if obj else "malformed"
    model = str((obj or {}).get("model") or "")
    if shape:
        dec = _blank(rules, env, bias, shape, model or "malformed",
                     local["failed_conditions"] or [shape], local["conditions"])
        dec["symbol"] = proposal["pair"]
        return dec
    if not local["pass"]:
        dec = _blank(rules, env, bias, "local_disagree", model,
                     local["failed_conditions"], local["conditions"])
        dec["symbol"] = proposal["pair"]
        return dec
    if not obj["pass"]:
        failed = list(obj["failed_conditions"]) or local["failed_conditions"] or ["ornith_refuse"]
        dec = _blank(rules, env, bias, "ornith_refuse", model, failed, local["conditions"])
        dec["symbol"] = proposal["pair"]
        return dec
    dec = _blank(rules, env, bias, "pass", model, [], local["conditions"])
    dec["pass"] = True
    dec["allPass"] = True
    dec["symbol"] = proposal["pair"]
    return dec


def append_log(path, entry):
    doc = {"trades": []}
    if path and os.path.exists(path):
        try:
            prev = json.load(open(path))
            if isinstance(prev, dict) and isinstance(prev.get("trades"), list):
                doc = prev
            elif isinstance(prev, list):
                doc = {"trades": prev}
        except (OSError, json.JSONDecodeError):
            doc = {"trades": []}
    doc["trades"].append(entry)
    doc["mode"] = vc.CRYPTO_PAPER_MODE
    doc["create_order"] = False
    cp.persist(doc, path)
    return path


def _equity(state, cash, pair, source):
    end = cp.mark_to_market(state, {pair: state["fills"][-1]["mid"]} if state["fills"] else {})
    return {
        "ts": cp._now(),
        "valedia_version": vc.VALEDIA_VERSION,
        "status": cp.STATUS,
        "mode": cp.STATUS,
        "create_order": False,
        "live": False,
        "source": source,
        "pair": pair,
        "starting_equity": cash,
        "ending_equity": end,
        "would_have_pnl": round(end - cash, 6),
        "n_fills": len(state["fills"]),
        "fees_paid": round(state["fees_paid"], 6),
        "fills": state["fills"],
        "cash": round(state["cash"], 6),
        "positions": state["positions"],
        "note": "CE-1 paper fill after Ornith PASS; would-have only; no venue order",
    }


def run_safety(*, fixture_path=None, rules_path=None, pair="BTC-USDT", cash=10000.0,
               out_path=None, log_path=None, post_fn=None, ornith_url=None,
               timeout=20, maker=None, taker=None, slip=None, extra=None,
               live_data=False, get_fn=None):
    rules = load_rules(rules_path)
    pair = cp.norm_pair(pair)
    bars = [b for b in cp.load_watchlist_bars(
                [pair], live_data=live_data,
                fixture_path=fixture_path or DEFAULT_BARS, get_fn=get_fn)
            if cp.norm_pair(b.get("pair") or pair) == pair]
    snap = ind.snapshot(bars, rules.get("indicators"))
    snap["pair"] = pair
    bias, _rows = bias_of(rules, snap)
    proposal = proposal_for(snap, pair, cash) if bias == "long" else None
    dec = judge(rules, snap, proposal, post_fn=post_fn, url=ornith_url, timeout=timeout)
    dec["symbol"] = pair
    if dec["pass"] and proposal:
        # Nimble 2 beside Ornith (Ornith stays the safety gate). Shadow stamps only;
        # veto may additionally remove the paper entry. Never adds, sizes or exits.
        nv = nws.gate("crypto", pair, {"passes_filter": True, "has_pos": False},
                      source="crypto_paper_safety", venue="paper", label="pass")
        nws.stamp(dec, nv)
        if nws.blocked(nv):
            dec.update(reason=nws.SKIP, failed_conditions=list(dec["failed_conditions"]) + [nws.SKIP])
            dec["pass"] = dec["allPass"] = False
    fill = None
    if dec["pass"] and proposal and bars:
        maker = float(vc.CRYPTO_FEE_PCT if maker is None else maker)
        taker = float(vc.CRYPTO_FEE_PCT if taker is None else taker)
        slip = float(vc.CRYPTO_SLIPPAGE_PCT if slip is None else slip)
        st = cp.book(cash)
        fill = cp.apply_fill(
            st, pair, "buy", proposal["qty"], proposal["mid"],
            slip=slip, maker=maker, taker=taker, role="taker",
            ts=bars[-1].get("ts") or "",
        )
        if fill is None:
            dec["pass"] = False
            dec["allPass"] = False
            dec["reason"] = "cash_short"
        elif out_path:
            cp.persist(_equity(st, float(cash), pair, "public" if live_data else "fixture"), out_path)
            dec["equity_path"] = out_path
    dec["would_have_fill"] = fill
    dec["orderPlaced"] = False
    dec["create_order"] = False
    dec["live"] = False
    # Tick metadata only. Cannot flip pass, the fill, or the reason.
    for k, v in (extra or {}).items():
        if k not in ("pass", "allPass", "would_have_fill", "create_order",
                     "live", "orderPlaced", "reason", "model"):
            dec[k] = v
    path = append_log(log_path or DEFAULT_LOG, dec)
    dec["log_path"] = path
    return dec
