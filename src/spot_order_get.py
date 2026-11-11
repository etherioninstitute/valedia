"""Read-only Coinbase/Alpaca order GET helpers (Slice 7 + Slice 14).

Shipped OFF. Default cron uses reconcile_run.mock_spot_order_getter (no HTTP).
Valedia-runtime getter is live_spot_order_getter, selected only when
valedia_config.spot_reconcile_http_enabled() is True.

Single Burton/Valedia flip (NOT this lane — default stays False):
    export SPOT_RECONCILE_HTTP=1
  or valedia_config.SPOT_RECONCILE_HTTP = True
Then reconcile_run.get_spot_order_getter() returns live_spot_order_getter
(no extra set_spot_order_getter call).

Tests: inject mocked transport via set_spot_http_transport / _request / _get.
Never live venue HTTP. No POST. No --live. No nautilus_trader.
(c) GOATWORTHY Post LLC.
"""
from __future__ import annotations

# Test inject. Does not flip SPOT_RECONCILE_HTTP. Live traders run only when
# the flag is on AND both of these are None.
_injected_request = None      # callable(method, path) -> dict|Response|None
_injected_alpaca_get = None   # callable(path) -> dict|Response|None


def set_spot_http_transport(request=None, alpaca_get=None):
    """Inject mocked GET transport. None/omit both to clear.

    request: Coinbase callable(method, path) -> dict|Response|None
    alpaca_get: Alpaca callable(path) -> dict|Response|None
    Does not enable live HTTP. Flag-off cron still uses mock_spot_order_getter.
    """
    global _injected_request, _injected_alpaca_get
    _injected_request = request
    _injected_alpaca_get = alpaca_get


def _http_enabled():
    try:
        import valedia_config as vc
        return bool(vc.spot_reconcile_http_enabled())
    except Exception:
        return False


def _has_inject(_request=None, _alpaca_get=None):
    return (
        _request is not None
        or _alpaca_get is not None
        or _injected_request is not None
        or _injected_alpaca_get is not None
    )


def _test_transport_installed():
    """Any module inject means tests own the wire — never fall through to live."""
    return _injected_request is not None or _injected_alpaca_get is not None


def _as_body(r):
    """Normalize a requests.Response, a dict, or None into a JSON dict."""
    if r is None:
        return None
    if isinstance(r, dict):
        return r
    if hasattr(r, "ok") and not r.ok:
        return None
    if hasattr(r, "json"):
        try:
            body = r.json()
        except Exception:
            return None
        return body if isinstance(body, dict) else None
    return None


def coinbase_order_get(order_id, _request=None):
    """GET /api/v3/brokerage/orders/historical/{order_id}. Read-only.

    Live JWT GET only if SPOT_RECONCILE_HTTP is on and no inject is set.
    Tests inject _request or set_spot_http_transport. Flag-off with no
    inject → None (no HTTP).
    """
    if not order_id:
        return None
    import spot_reconcile as sr
    path = sr.coinbase_order_get_path(str(order_id))
    req = _request if _request is not None else _injected_request
    if req is not None:
        try:
            return _as_body(req("GET", path))
        except Exception:
            return None
    if _test_transport_installed() or not _http_enabled():
        return None
    try:
        from coinbase_trader import get_order
        return get_order(order_id)
    except Exception:
        return None


def alpaca_order_get(order_id, _get=None):
    """GET /v2/orders/{order_id}. Read-only.

    Live HTTP only if SPOT_RECONCILE_HTTP is on and no inject is set.
    Tests inject _get or set_spot_http_transport. Flag-off with no inject
    → None (no HTTP).
    """
    if not order_id:
        return None
    import spot_reconcile as sr
    path = sr.alpaca_order_get_path(str(order_id))
    getfn = _get if _get is not None else _injected_alpaca_get
    if getfn is not None:
        try:
            return _as_body(getfn(path))
        except Exception:
            return None
    if _test_transport_installed() or not _http_enabled():
        return None
    try:
        from alpaca_executor import get_order
        return get_order(order_id)
    except Exception:
        return None


def live_spot_order_getter(order_id, venue, _request=None, _alpaca_get=None):
    """Valedia-runtime getter. Signature: (order_id, venue) -> dict|None.

    No HTTP unless SPOT_RECONCILE_HTTP is on or a test inject is set.
    Unknown venue → None (stay UNRECONCILED).
    """
    if not order_id:
        return None
    if not _has_inject(_request, _alpaca_get) and not _http_enabled():
        return None
    v = str(venue or "").lower()
    if "coinbase" in v:
        return coinbase_order_get(order_id, _request=_request)
    if "alpaca" in v:
        return alpaca_order_get(order_id, _get=_alpaca_get)
    return None
