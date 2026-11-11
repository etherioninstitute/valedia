"""H8: failure-gated intel fetch fallback. Fortress engine PARKED.

FORTRESS_INTEL_FALLBACK default off. Classify {ok, empty, blocked, waf}.
API/RSS first. Token-capped headlines → models/intel_fetch_fallback.json.
Fail-closed stub: no Colima/Docker/CDP on this Mac. Tests inject engine_fn.

Never CDP at venues. Never captcha/proxy farms. Never second Kelly.
(c) GOATWORTHY Post LLC.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from urllib.parse import urlparse

import valedia_config as vc

_MODELS = os.path.expanduser("~/.openclaw/workspace/models")
OUT_NAME = "intel_fetch_fallback.json"
MAX_HEADLINES = 12
MAX_CHARS = 160
MAX_TOKENS = 400
# ~4 chars/token; cap is the persisted markdown, not a second swarm dump.

VENUE_HOSTS = frozenset({
    "clob.polymarket.com",
    "api.elections.kalshi.com",
    "trading-api.kalshi.com",
    "api.exchange.kalshi.com",
    "api.coinbase.com",
    "api.exchange.coinbase.com",
    "api.hyperliquid.xyz",
    "paper-api.alpaca.markets",
    "api.alpaca.markets",
})
VENUE_SUFFIXES = (".kalshi.com", ".coinbase.com", ".alpaca.markets",
                  ".hyperliquid.xyz")
INTEL_ALLOWLIST = frozenset({
    "api.coingecko.com",
    "api.alternative.me",
    "www.coindesk.com",
    "coindesk.com",
    "cointelegraph.com",
    "www.cointelegraph.com",
    "query1.finance.yahoo.com",
    "gamma-api.polymarket.com",
})
_WAF_BODY = ("just a moment", "attention required", "challenge-platform",
             "cf-browser-verification", "checking your browser before",
             "<title>access denied</title>")


def host_of(url):
    try:
        return (urlparse(str(url or "")).hostname or "").lower()
    except Exception:
        return ""


def _waf(body, headers, err=""):
    h = {str(k).lower(): str(v).lower() for k, v in (headers or {}).items()}
    low = str(body or "").lower()[:2000] + " " + str(err or "").lower()
    if h.get("cf-mitigated") or h.get("x-datadome"):
        return True
    if any(m in low for m in _WAF_BODY):
        return True
    if ("cf-ray" in h or "cf-ray" in low) and "cloudflare" in low:
        return True
    return False


def classify_fetch(status_code=None, body="", headers=None, error=None):
    """Structured status. Fallback only after 403/429/empty/WAF — not 5xx vibes."""
    code = None
    try:
        if status_code is not None and str(status_code).strip() != "":
            code = int(status_code)
    except (TypeError, ValueError):
        code = None
    waf = _waf(body, headers, error)
    if code in (401, 403, 407, 429) or waf:
        if waf:
            return {"status": "waf", "code": code, "reason": "waf"}
        reason = "rate_limited" if code == 429 else str(code)
        return {"status": "blocked", "code": code, "reason": reason}
    text = str(body or "").strip()
    empty = (code == 204) or (not text) or text in ("[]", "{}", "null")
    if empty and (code in (200, 204, None) or code == 404):
        return {"status": "empty", "code": code, "reason": "empty"}
    if code is not None and code >= 400:
        return {"status": "error", "code": code, "reason": "http"}
    if code is None and error:
        return {"status": "error", "code": None, "reason": "transport"}
    return {"status": "ok", "code": code or 200, "reason": "ok"}


def refuse_url(url):
    h = host_of(url)
    if not h:
        return "no_host"
    if h in VENUE_HOSTS or h.endswith(VENUE_SUFFIXES):
        return "venue_host_refused"
    if h not in INTEL_ALLOWLIST:
        return "host_not_allowlisted"
    return None


def engine_available(probe_fn=None):
    """PARKED. Default False — do not probe Docker/Colima (M1 qemu crash)."""
    if probe_fn is None:
        return False
    try:
        return bool(probe_fn())
    except Exception:
        return False


def cap_headlines(items, max_headlines=MAX_HEADLINES, max_chars=MAX_CHARS,
                  max_tokens=MAX_TOKENS):
    out, tokens = [], 0
    for it in items or []:
        s = " ".join(str(it or "").split())
        if not s:
            continue
        s = s[:max_chars]
        need = max(1, (len(s) + 3) // 4)
        if tokens + need > max_tokens:
            break
        out.append(s)
        tokens += need
        if len(out) >= max_headlines:
            break
    return out


def _base(classified, url=""):
    st = str((classified or {}).get("status") or "error")
    return {
        "ok": st == "ok",
        "empty": st == "empty",
        "blocked": st == "blocked",
        "waf": st == "waf",
        "status": st,
        "code": (classified or {}).get("code"),
        "headlines": [],
        "engine": "parked",
        "fallback_ran": False,
        "url_host": host_of(url),
        "reason": (classified or {}).get("reason") or st,
        "ts": datetime.now(timezone.utc).isoformat(),
        "valedia_version": vc.VALEDIA_VERSION,
        "kelly": False,
    }


def load(models_dir=None):
    path = os.path.join(models_dir or _MODELS, OUT_NAME)
    if not os.path.exists(path):
        return {}
    try:
        d = json.load(open(path))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def persist(result, models_dir=None):
    md = models_dir or _MODELS
    os.makedirs(md, exist_ok=True)
    prev = load(md)
    out = dict(result or {})
    if not out.get("headlines") and prev.get("headlines"):
        out["last_good_headlines"] = list(prev["headlines"])[:MAX_HEADLINES]
        out["kept_last_good"] = True
    path = os.path.join(md, OUT_NAME)
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
        f.write("\n")
    return path


def run_fallback(url, primary, engine_fn=None, probe_fn=None, models_dir=None,
                 write=True):
    """Harder fetch only after 403/429/empty/WAF. Flag off → classify only."""
    if isinstance(primary, dict) and primary.get("status"):
        classified = primary
    else:
        classified = classify_fetch(primary)
    result = _base(classified, url)
    st = result["status"]
    if st == "ok":
        result["reason"] = "primary_ok"
    elif st not in ("empty", "blocked", "waf"):
        result["reason"] = "not_failure_gated"
    else:
        why = refuse_url(url)
        if why:
            result["reason"] = why
        elif not vc.fortress_intel_fallback_enabled():
            result["reason"] = "flag_off"
        elif engine_fn is None:
            result["reason"] = "engine_parked"
        elif not engine_available(probe_fn or (lambda: True)):
            result["reason"] = "engine_parked"
        else:
            try:
                raw = engine_fn(url)
                items = raw if isinstance(raw, (list, tuple)) else [raw]
                headlines = cap_headlines(items)
                result["headlines"] = headlines
                result["ok"] = bool(headlines)
                result["empty"] = not headlines
                result["fallback_ran"] = True
                result["engine"] = "injected"
                result["reason"] = "fallback_ok" if headlines else "fallback_empty"
            except Exception as e:
                result["reason"] = "engine_fail_closed"
                result["engine_error"] = str(e)[:120]
    if write:
        persist(result, models_dir)
    return result


def observe(url, status_code=None, body="", headers=None, error=None,
            engine_fn=None, models_dir=None, write=True):
    """Seed/_get hook. Success is a no-write. Failures classify (+ optional stub)."""
    classified = classify_fetch(status_code, body, headers, error)
    if classified["status"] == "ok":
        return classified
    return run_fallback(url, classified, engine_fn=engine_fn,
                        models_dir=models_dir, write=write)
