"""Read-only Coinbase/Alpaca order GET helpers (Slice 7).

Shipped OFF. Default cron uses reconcile_run.mock_spot_order_getter (no HTTP).
Live transport runs only when valedia_config.spot_reconcile_http_enabled() is True
AND no test inject is passed.

Burton/Valedia single flip later (NOT this lane):
    export SPOT_RECONCILE_HTTP=1
  or valedia_config.SPOT_RECONCILE_HTTP = True
Then reconcile_run.get_spot_order_getter() returns live_spot_order_getter.

Tests pass _request/_get injects. Never live venue HTTP. No POST. No --live.
No nautilus_trader.
(c) GOATWORTHY Post LLC.
"""
from __future__ import annotations


def _http_enabled():
    try:
        import valedia_config as vc
        return bool(vc.spot_reconcile_http_enabled())
    except Exception:
        return False


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

    Live JWT GET only if SPOT_RECONCILE_HTTP is on and _request is omitted.
    Tests inject _request (callable(method, path) -> dict|Response). Flag-off
    with no inject → None (no HTTP).
    """
    if not order_id:
        return None
    import spot_reconcile as sr
    path = sr.coinbase_order_get_path(str(order_id))
    if _request is not None:
        try:
            return _as_body(_request("GET", path))
        except Exception:
            return None
    if not _http_enabled():
        return None
    try:
        from coinbase_trader import get_order
        return get_order(order_id)
    except Exception:
        return None


def alpaca_order_get(order_id, _get=None):
    """GET /v2/orders/{order_id}. Read-only.

    Live HTTP only if SPOT_RECONCILE_HTTP is on and _get is omitted.
    Tests inject _get (callable(path) -> dict|Response). Flag-off with no
    inject → None (no HTTP).
    """
    if not order_id:
        return None
    import spot_reconcile as sr
    path = sr.alpaca_order_get_path(str(order_id))
    if _get is not None:
        try:
            return _as_body(_get(path))
        except Exception:
            return None
    if not _http_enabled():
        return None
    try:
        from alpaca_executor import get_order
        return get_order(order_id)
    except Exception:
        return None


def live_spot_order_getter(order_id, venue, _request=None, _alpaca_get=None):
    """Dispatch Coinbase/Alpaca GET. No HTTP unless flag-on or test inject.

    Signature matches reconcile_run getter: (order_id, venue) -> dict|None.
    Unknown venue → None (stay UNRECONCILED).
    """
    if not order_id:
        return None
    if _request is None and _alpaca_get is None and not _http_enabled():
        return None
    v = str(venue or "").lower()
    if "coinbase" in v:
        return coinbase_order_get(order_id, _request=_request)
    if "alpaca" in v:
        return alpaca_order_get(order_id, _get=_alpaca_get)
    return None
