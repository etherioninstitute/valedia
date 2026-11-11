"""eToro Public API client. GET research only.

Writes (copy start/adjust/close, real or demo orders, money) raise before any
socket. They stay refused unless ETORO_LIVE=1 and ETORO_UNLOCK=burton-documented,
and this slice still raises write_endpoint or half_live instead of sending.

Keys: ETORO_API_KEY or ETORO_PUBLIC_KEY, ETORO_USER_KEY or ETORO_PRIVATE_KEY.
Never log the values.
"""
from __future__ import annotations

import os
import time
import uuid
import urllib.error
import urllib.parse
import urllib.request
from collections import deque

BASE = "https://public-api.etoro.com"
USER_AGENT = "Valedia-ASI research@goatworthy.com"
UNLOCK = "burton-documented"  # documented in recommendations/ETORO-PAPER.md. This slice never sets it.
RATE_LIMIT = 60  # shared default quota. Market-data's 120/60s pool is tighter here on purpose.
RATE_WINDOW = 60.0
_KEY_NAMES = ("ETORO_API_KEY", "ETORO_PUBLIC_KEY", "ETORO_USER_KEY", "ETORO_PRIVATE_KEY")
# Documented write paths. request() never calls them.
BLOCKED = {
    "copy_start": "POST /api/v2/trading/copy",
    "copy_adjust": "POST /api/v2/trading/copy",
    "copy_start_demo": "POST /api/v2/trading/copy/demo",
    "copy_close": "DELETE /api/v2/trading/copy",
    "copy_close_json": "POST /api/v2/trading/copy/close",
    "order_create": "POST /api/v1/trading/execution/market-open-orders/by-amount",
    "order_create_demo": "POST /api/v1/trading/execution/demo/market-open-orders/by-amount",
    "order_cancel": "DELETE /api/v1/trading/execution/limit-orders/{orderId}",
    "order_cancel_demo": "DELETE /api/v1/trading/execution/demo/limit-orders/{orderId}",
    "money": "POST money/crypto withdraw",
}


class EtoroError(Exception):
    def __init__(self, code, detail=""):
        self.code = code
        self.detail = detail
        super().__init__(code if not detail else "%s: %s" % (code, detail))


class EtoroHTTP(EtoroError):
    def __init__(self, status, path):
        self.status = int(status)
        super().__init__("http", "%s %s" % (self.status, path))


def _env_of(env):
    return os.environ if env is None else env


def live_flag(env=None):
    return str(_env_of(env).get("ETORO_LIVE") or "").strip() == "1"


def unlocked(env=None):
    return str(_env_of(env).get("ETORO_UNLOCK") or "").strip() == UNLOCK


def _paperish(env):
    mode = str(env.get("ETORO_MODE") or "research").strip().lower()
    return mode in ("", "research", "paper", "shadow", "paper/shadow")


def gate(action, env=None, hedge=False):
    """Refuse code. Does not open a socket.

    copy_start when a copy write is attempted while ETORO_LIVE!=1.
    etoro_live when any other write is attempted while live is off, or unlock is missing.
    half_live when live is on but the desk is still research/paper or a sim hedge is set.
    write_endpoint when live and unlock are both set and the mode is not paper.
    This slice has no sender behind write_endpoint.
    """
    env = _env_of(env)
    action = str(action or "write")
    if not live_flag(env):
        return "copy_start" if action == "copy_start" else "etoro_live"
    # Live flag plus the research/paper desk is a real copy beside a simulated book.
    if hedge or str(env.get("ETORO_HEDGE") or "").strip() in ("1", "true", "yes") or _paperish(env):
        return "half_live"
    if not unlocked(env):
        return "etoro_live"
    return "write_endpoint"


def load_key_file(path=None):
    """Return only the eToro key names. Other .env lines are ignored."""
    path = path or os.path.expanduser("~/.openclaw/.env")
    found = {}
    try:
        fh = open(path)
    except OSError:
        return found
    with fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            if k not in _KEY_NAMES:
                continue
            v = v.strip().strip('"').strip("'")
            if v:
                found[k] = v
    return found


def resolve_keys(env=None, file_env=None):
    env = _env_of(env)
    if file_env is None:
        file_env = load_key_file() if env is os.environ else {}
    def one(*names):
        for n in names:
            v = env.get(n)
            if v is None or str(v).strip() == "":
                v = file_env.get(n)
            if v is not None and str(v).strip():
                return str(v).strip().strip('"').strip("'")
        return ""
    api = one("ETORO_API_KEY", "ETORO_PUBLIC_KEY")
    user = one("ETORO_USER_KEY", "ETORO_PRIVATE_KEY")
    if not api or not user:
        raise EtoroError("missing_keys")
    return api, user


def keys_present(env=None, file_env=None):
    try:
        resolve_keys(env, file_env)
    except EtoroError:
        return False
    return True


class RateGate:
    """ponytail: one shared deque. Per-endpoint buckets if market-data needs its own 120."""

    def __init__(self, limit=RATE_LIMIT, window=RATE_WINDOW, now=None, sleep=None):
        self.limit = int(limit)
        self.window = float(window)
        self.now = now or time.monotonic
        self.sleep = sleep or time.sleep
        self.hits = deque()

    def acquire(self):
        now = self.now()
        while self.hits and now - self.hits[0] >= self.window:
            self.hits.popleft()
        if len(self.hits) >= self.limit:
            delay = self.window - (now - self.hits[0])
            self.sleep(max(0.0, delay))
            now = self.now()
            while self.hits and now - self.hits[0] >= self.window:
                self.hits.popleft()
        self.hits.append(self.now())


class EtoroClient:
    def __init__(self, env=None, file_env=None, transport=None, rate=None):
        self.env = env
        self.file_env = file_env
        self.transport = transport
        self.rate = rate if rate is not None else RateGate()

    def headers(self):
        api, user = resolve_keys(self.env, self.file_env)
        return {
            "x-api-key": api,
            "x-user-key": user,
            "x-request-id": str(uuid.uuid4()),
            "accept": "application/json",
            "user-agent": USER_AGENT,
        }

    def _refuse(self, action, hedge=False):
        code = gate(action, self.env, hedge=hedge)
        raise EtoroError(code, BLOCKED.get(action, action))

    def request(self, method, path, query=None):
        method = str(method or "GET").upper()
        if method != "GET":
            self._refuse("write")
        return self.get(path, query)

    def get(self, path, query=None):
        if not str(path).startswith("/api/"):
            raise EtoroError("bad_path")
        hdrs = self.headers()
        self.rate.acquire()
        url = BASE + path
        if query:
            url += "?" + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})
        if self.transport:
            status, body = self.transport("GET", url, hdrs)
        else:
            status, body = _urlopen_get(url, hdrs)
        if int(status) >= 400:
            raise EtoroHTTP(status, path)
        return body

    def rankings(self, period="OneYearAgo", sort="-copiers", page=1, page_size=20,
                 popular_investor=None, extra=None):
        q = {"period": period, "sort": sort, "page": int(page), "pageSize": int(page_size)}
        if popular_investor is not None:
            q["popularInvestor"] = "true" if popular_investor else "false"
        if extra:
            q.update(extra)
        return self.get("/api/v2/portfolios/rankings", q)

    def search_portfolios(self, kind, query, limit=10):
        """OpenAPI parameter is `type` (trader | smart-portfolio). A 400 retries `Type`."""
        kind = str(kind or "").strip().lower()
        if kind not in ("trader", "smart-portfolio"):
            raise EtoroError("bad_type")
        q = {"type": kind, "query": query, "limit": int(limit)}
        try:
            return self.get("/api/v1/portfolios/search", q)
        except EtoroHTTP as exc:
            if exc.status != 400:
                raise
            q = {"Type": kind, "query": query, "limit": int(limit)}
            return self.get("/api/v1/portfolios/search", q)

    def instrument_types(self):
        return self.get("/api/v1/market-data/instrument-types")

    def search_instruments(self, query, limit=10):
        return self.get("/api/v2/market-data/instruments/search", {"query": query, "limit": int(limit)})

    def me(self):
        return self.get("/api/v1/me")

    def people(self, usernames):
        names = usernames if isinstance(usernames, str) else ",".join(usernames)
        return self.get("/api/v1/user-info/people", {"usernames": names})

    def people_search(self, query=None):
        return self.get("/api/v1/user-info/people/search", query or {"page": 1, "pageSize": 5})

    def copier_stats(self, username):
        user = urllib.parse.quote(str(username), safe="")
        return self.get("/api/v2/portfolios/%s/copiers" % user)

    def start_copy(self, username, amount, demo=False, hedge=False):
        self._refuse("copy_start_demo" if demo else "copy_start", hedge=hedge)

    def adjust_copy(self, username, amount, hedge=False):
        self._refuse("copy_adjust", hedge=hedge)

    def close_copy(self, username, hedge=False):
        self._refuse("copy_close", hedge=hedge)

    def create_order(self, body=None, demo=False):
        self._refuse("order_create_demo" if demo else "order_create")

    def cancel_order(self, order_id, demo=False):
        self._refuse("order_cancel_demo" if demo else "order_cancel")

    def money_withdraw(self, body=None):
        self._refuse("money")


def _urlopen_get(url, headers):
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            raw = res.read().decode("utf-8", "replace")
            status = getattr(res, "status", 200)
    except urllib.error.HTTPError as exc:
        return exc.code, {}
    import json
    try:
        body = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        body = {}
    return status, body
