"""POLY-US: Polymarket US DCM client. Not international CLOB.

Int'l CLOB/Gamma stays in polymarket_client.py. This module talks only to:
  AUTH     https://api.polymarket.us          (Ed25519 Key ID + Secret)
  PUBLIC   https://gateway.polymarket.us      (markets/events/tags/search)

Auth env: POLYMARKET_KEY_ID, POLYMARKET_SECRET_KEY.
Writes (create/cancel) require POLYMARKET_US_LIVE=1. Default False.
Real urllib requires POLYMARKET_US_HTTP=1 or an injected transport.
Retail identity is balances (whoami_retail), not GET /v1/whoami.
Tests inject transport. No py-clob-client. No polymarket-us SDK.
(c) GOATWORTHY Post LLC.
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

import valedia_config as vc

AUTH_BASE = os.environ.get("POLYMARKET_US_API_BASE", "https://api.polymarket.us").rstrip("/")
GATEWAY_BASE = os.environ.get(
    "POLYMARKET_US_GATEWAY_BASE", "https://gateway.polymarket.us").rstrip("/")
WS_PRIVATE = "wss://api.polymarket.us/v1/ws/private"
WS_MARKETS = "wss://api.polymarket.us/v1/ws/markets"

# Nav buckets (Burton POLY-E steer). Culture = Oscars/entertainment.
# Primary US tag slug first; extras are documented fallbacks, not auto-fetched.
NAV_PRIMARY_SLUG = {
    "SPORTS": "sports",
    "POLITICS": "politics",
    "CRYPTO": "crypto",
    "WEATHER": "weather",
    "TECH": "tech",
    "CULTURE": "culture",
    "ENTERTAINMENT": "entertainment",
    "ECON": "econ",
}
NAV_ORDER = ("SPORTS", "POLITICS", "CRYPTO", "WEATHER", "TECH",
             "CULTURE", "ENTERTAINMENT", "ECON")

WRITE_METHODS = frozenset({"create_order", "cancel_order"})
# GET /v1/whoami is institutional (api.prod.polymarketexchange.com bearer JWT).
# Retail Ed25519 host api.polymarket.us 404s that path. Identity = balances.
WHOAMI_RETAIL_SOURCE = "balances"
_BAL_KEEP = ("currency", "buyingPower", "currentBalance",
             "assetAvailable", "openOrders")


def _flag(name, default=False):
    env = os.environ.get(name)
    if env is not None and str(env).strip() != "":
        return str(env).strip().lower() in ("1", "true", "yes", "on")
    return bool(default)


def live_enabled():
    return _flag("POLYMARKET_US_LIVE", False)


def http_enabled():
    return _flag("POLYMARKET_US_HTTP", False)


def load_openclaw_env():
    """setdefault from ~/.openclaw/.env. Does not override existing env."""
    path = os.path.expanduser("~/.openclaw/.env")
    if not os.path.isfile(path):
        return False
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("'").strip('"'))
    return True


def _http_ok(doc):
    if not isinstance(doc, dict):
        return False
    st = doc.get("status")
    if isinstance(st, int) and st >= 400:
        return False
    return "error" not in doc or "balances" in doc or "positions" in doc


def _redact_balances(doc):
    bals = doc.get("balances") if isinstance(doc, dict) else None
    if not isinstance(bals, list):
        return []
    out = []
    for row in bals:
        if isinstance(row, dict):
            out.append({k: row[k] for k in _BAL_KEEP if k in row})
    return out


def _positions_n(doc):
    raw = doc.get("positions") if isinstance(doc, dict) else None
    if isinstance(raw, list):
        return len(raw)
    if isinstance(raw, dict):
        return len(raw)
    return 0


def nav_category(name):
    n = str(name or "").strip().upper().replace(" ", "_")
    if n in ("CULTURE", "OSCARS", "AWARDS"):
        return "ENTERTAINMENT" if n != "CULTURE" else "CULTURE"
    return n


def nav_tag_slug(category):
    n = nav_category(category)
    if n in NAV_PRIMARY_SLUG:
        return NAV_PRIMARY_SLUG[n]
    try:
        import polymarket_entertainment as pe
        tags = pe.NAV_TAGS.get(n) or pe.NAV_TAGS.get(
            "ENTERTAINMENT" if n == "CULTURE" else n)
        if tags:
            return next(iter(tags))
    except Exception:
        pass
    return None


def _sign(secret_b64, method, path):
    """Ed25519 over `{timestamp}{METHOD}{path}` as docs.polymarket.us auth."""
    from cryptography.hazmat.primitives.asymmetric import ed25519
    raw = base64.b64decode(secret_b64)
    key = ed25519.Ed25519PrivateKey.from_private_bytes(raw[:32])
    ts = str(int(time.time() * 1000))
    msg = f"{ts}{method.upper()}{path}".encode("utf-8")
    sig = base64.b64encode(key.sign(msg)).decode("ascii")
    return ts, sig


class LiveWriteBlocked(RuntimeError):
    """create/cancel refused while POLYMARKET_US_LIVE is off."""


class PolymarketUSClient:
    def __init__(self, key_id=None, secret_key=None, live=None, http=None,
                 transport=None, auth_base=None, gateway_base=None):
        self.key_id = key_id if key_id is not None else os.environ.get("POLYMARKET_KEY_ID")
        self.secret_key = (secret_key if secret_key is not None
                           else os.environ.get("POLYMARKET_SECRET_KEY"))
        self.live = live_enabled() if live is None else bool(live)
        self.http = http_enabled() if http is None else bool(http)
        self.transport = transport
        self.auth_base = (auth_base or AUTH_BASE).rstrip("/")
        self.gateway_base = (gateway_base or GATEWAY_BASE).rstrip("/")

    def has_keys(self):
        return bool(str(self.key_id or "").strip() and str(self.secret_key or "").strip())

    def _headers(self, method, path, auth):
        h = {"Accept": "application/json",
             "Content-Type": "application/json",
             "User-Agent": f"valedia-poly-us/{vc.VALEDIA_VERSION}"}
        if not auth:
            return h
        kid = str(self.key_id or "").strip()
        if kid:
            h["X-PM-Access-Key"] = kid
        sec = str(self.secret_key or "").strip()
        if sec:
            try:
                ts, sig = _sign(sec, method, path)
                h["X-PM-Timestamp"] = ts
                h["X-PM-Signature"] = sig
            except Exception:
                if self.transport is None:
                    raise
        elif self.transport is None:
            raise RuntimeError("missing POLYMARKET_KEY_ID / POLYMARKET_SECRET_KEY")
        return h

    def _request(self, method, path, query=None, body=None, auth=False):
        q = ""
        if query:
            items = []
            for k, v in query.items():
                if v is None:
                    continue
                if isinstance(v, bool):
                    v = "true" if v else "false"
                items.append((k, str(v)))
            if items:
                q = "?" + urllib.parse.urlencode(items)
        host = self.auth_base if auth else self.gateway_base
        url = host + path + q
        headers = self._headers(method, path, auth)
        payload = None if body is None else json.dumps(body).encode("utf-8")
        if self.transport is not None:
            return self.transport(method, path, url, headers, body)
        if not self.http:
            raise RuntimeError("POLY-US HTTP off (inject transport or POLYMARKET_US_HTTP=1)")
        req = urllib.request.Request(url, data=payload, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                return json.loads(raw.decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(err or "{}")
            except json.JSONDecodeError:
                parsed = {"error": err, "status": e.code}
            if isinstance(parsed, dict):
                parsed.setdefault("status", e.code)
            return parsed

    def whoami(self):
        """Retail identity via balances. Does not call GET /v1/whoami."""
        return self.whoami_retail()

    def whoami_retail(self, include_positions=True):
        """Identity on api.polymarket.us = GET /v1/account/balances.

        GET /v1/whoami is institutional (api.prod.polymarketexchange.com,
        bearer JWT) and 404s on the retail Ed25519 host.
        """
        bals = self.balances()
        ok = _http_ok(bals)
        out = {
            "ok": ok,
            "source": WHOAMI_RETAIL_SOURCE,
            "host": "api.polymarket.us",
            "balances": _redact_balances(bals) if ok else [],
            "note": ("retail identity via GET /v1/account/balances; "
                     "GET /v1/whoami is institutional bearer JWT, not retail"),
        }
        if not ok and isinstance(bals, dict):
            st = bals.get("status")
            if st is not None:
                out["status"] = st
        if include_positions and ok:
            try:
                pos = self.positions(limit=5)
                if _http_ok(pos):
                    out["positions_n"] = _positions_n(pos)
            except Exception:
                pass
        return out

    def balances(self):
        return self._request("GET", "/v1/account/balances", auth=True)

    def positions(self, market=None, limit=100):
        return self._request("GET", "/v1/portfolio/positions",
                             query={"market": market, "limit": limit}, auth=True)

    def activities(self, limit=50):
        return self._request("GET", "/v1/portfolio/activities",
                             query={"limit": limit}, auth=True)

    def list_markets(self, limit=20, active=True, **extra):
        q = {"limit": limit, "active": active, **extra}
        return self._request("GET", "/v1/markets", query=q, auth=False)

    def list_events(self, limit=20, active=True, tag_slug=None, **extra):
        q = {"limit": limit, "active": active, **extra}
        if tag_slug:
            q["tagSlug"] = tag_slug
        return self._request("GET", "/v1/events", query=q, auth=False)

    def search(self, query, limit=10):
        return self._request("GET", "/v1/search",
                             query={"query": query, "limit": limit}, auth=False)

    def list_sports(self):
        return self._request("GET", "/v1/sports", auth=False)

    def list_tags(self, limit=50):
        return self._request("GET", "/v1/tags", query={"limit": limit}, auth=False)

    def events_for_tag(self, slug, limit=20, active=True):
        path = "/v2/tags/" + urllib.parse.quote(str(slug), safe="") + "/events"
        return self._request("GET", path,
                             query={"limit": limit, "active": active}, auth=False)

    def list_nav_markets(self, category, limit=20):
        """Sports/Politics/Crypto/Weather/Tech/Culture/Econ via US tag slug."""
        slug = nav_tag_slug(category)
        if not slug:
            raise ValueError("unknown nav category: " + str(category))
        return self.events_for_tag(slug, limit=limit)

    def list_nav_all(self, limit=10):
        out = {}
        for name in NAV_ORDER:
            out[name] = self.list_nav_markets(name, limit=limit)
        return out

    def get_open_orders(self, market_slug=None):
        q = {"marketSlug": market_slug} if market_slug else None
        return self._request("GET", "/v1/orders/open", query=q, auth=True)

    def get_order(self, order_id):
        oid = urllib.parse.quote(str(order_id or ""), safe="")
        if not oid:
            raise ValueError("order_id required")
        return self._request("GET", f"/v1/order/{oid}", auth=True)

    def preview_order(self, body):
        """Dry-run. Not a create. Never a fill.

        Retail OpenAPI wants `{request: <order>}`. Unwrapped body 400s
        ("Request is required") on api.polymarket.us.
        """
        payload = body
        if isinstance(body, dict) and "request" not in body:
            payload = {"request": body}
        resp = self._request("POST", "/v1/order/preview", body=payload, auth=True)
        wrap = resp if isinstance(resp, dict) else {"raw": resp}
        return {**wrap, "kind": "preview", "filled": False,
                "note": "preview != create; ack != fill"}

    def _require_live(self, method):
        if not self.live:
            raise LiveWriteBlocked(
                f"{method} refused (POLYMARKET_US_LIVE off)")

    def create_order(self, body):
        self._require_live("create_order")
        resp = self._request("POST", "/v1/orders", body=body, auth=True)
        wrap = resp if isinstance(resp, dict) else {"raw": resp}
        oid = wrap.get("id") or (wrap.get("order") or {}).get("id")
        return {**wrap, "kind": "create_ack", "id": oid, "filled": False,
                "note": "create ack != fill; GET order/trade id"}

    def cancel_order(self, order_id, body=None):
        self._require_live("cancel_order")
        oid = urllib.parse.quote(str(order_id or ""), safe="")
        if not oid:
            raise ValueError("order_id required")
        return self._request("POST", f"/v1/order/{oid}/cancel",
                             body=body or {}, auth=True)


def reconcile_us_fill(ack, get_fn=None):
    """preview != create; create ack != fill. GET by exchange order/trade id."""
    import forward_test_ledger as ftl
    raw = ack if isinstance(ack, dict) else {}
    if str(raw.get("kind") or "").lower() == "preview":
        return {"complete": False, "qty": 0.0, "price": 0.0,
                "order_id": raw.get("id"), "trade_id": None,
                "status": "preview"}, None
    return ftl.resolve_polymarket_fill(raw, get_fn)
