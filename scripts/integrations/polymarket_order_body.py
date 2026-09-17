"""Intended Polymarket CLOB POST /order body (unsigned stub).

Valedia polymarket_client.py is scan-only. quant_engine.PolymarketAdapter.ENABLED
is False. Archived polymarket_executor.py is not a live writer.

LIVE_CLIENT_HOOK is None until Valedia runtime wires an NT Polymarket adapter
(SUBSUME Phase 4). Do not install/call py-clob-client from this lane. No keys.

Shape matches archived executor OrderArgs + FOK (create_and_post_order args
before signing). Signature fields are omitted on purpose.
(c) GOATWORTHY Post LLC.
"""
from __future__ import annotations

CLOB_PATH = "POST /order"
CLOB_HOST = "https://clob.polymarket.com"
# Documented hook for a later NT adapter. This lane never binds a client.
LIVE_CLIENT_HOOK = None


def shares_from_notional(notional, price):
    """USD notional / price → share size. Caller must pass a real price."""
    n = float(notional)
    p = float(price)
    if n <= 0 or p <= 0:
        raise ValueError("notional and price must be > 0")
    return n / p


def build_clob_order_body(token_id, side, price, size, order_type="FOK",
                          fee_rate_bps=0, client_order_id=None):
    """Unsigned CLOB order args. Does not sign, POST, or load keys."""
    tid = str(token_id or "").strip()
    if not tid:
        raise ValueError("token_id required")
    side_u = str(side or "").strip().upper()
    if side_u not in ("BUY", "SELL"):
        raise ValueError("side must be buy|sell")
    if price is None or float(price) <= 0:
        raise ValueError("price required")
    if size is None or float(size) <= 0:
        raise ValueError("size (shares) required")
    ot = str(order_type or "FOK").strip().upper()
    if ot not in ("FOK", "GTC", "GTD", "IOC"):
        raise ValueError("orderType must be FOK|GTC|GTD|IOC")
    return {
        "path": CLOB_PATH,
        "host": CLOB_HOST,
        "signed": False,
        "live_client_hook": LIVE_CLIENT_HOOK,
        "orderType": ot,
        "tokenID": tid,
        "price": f"{float(price):.4f}",
        "size": str(size),
        "side": side_u,
        "feeRateBps": int(fee_rate_bps or 0),
        "client_order_id": client_order_id,
        "note": "unsigned py-clob-client OrderArgs shape; signature omitted",
    }


def send_via_live_hook(body):
    """HOOK: later NT Polymarket adapter. Always refuse in this lane."""
    if LIVE_CLIENT_HOOK is None:
        return {"sent": False, "reason": "no live client (stub)"}
    raise RuntimeError("Slice 11 must not bind LIVE_CLIENT_HOOK")
