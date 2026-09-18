"""Intended Hyperliquid /exchange order action (unsigned stub).

No live Valedia writer. Archived hyperliquid_client.py is Info-only (no
order POST). This module does not send, sign, import the SDK, or load keys.

LIVE_CLIENT_HOOK is None until Valedia runtime wires an NT Hyperliquid
adapter (SUBSUME Phase 3). Do not un-archive the old client as a writer.

Wire shape (HyperGrok + official SDK): POST /exchange, action.type=order.
There is no native market order — market-style is an Ioc limit at bound_px.
Leverage stays 0 until Burton (fund-rules). Size is never rounded up here.
(c) GOATWORTHY Post LLC.
"""
from __future__ import annotations

EXCHANGE_PATH = "POST /exchange"
EXCHANGE_HOST = "https://api.hyperliquid.xyz"
# Documented hook for a later NT adapter. This lane never binds a client.
LIVE_CLIENT_HOOK = None

_TIF = {"IOC": "Ioc", "GTC": "Gtc", "ALO": "Alo", "FOK": "Ioc", "GTD": "Gtc", "DAY": "Gtc"}


def hl_coin(instrument: str) -> str:
    """BTC-USD / BTCUSDT → BTC. Perps are coin names, not Coinbase product_ids."""
    t = str(instrument or "").strip().upper()
    if not t:
        return t
    for suffix in ("-USD", "-USDT", "-USDC", "USDT", "USDC"):
        if t.endswith(suffix) and len(t) > len(suffix):
            t = t[: -len(suffix)]
            break
    return t.strip("-")


def cloid_from_client_order_id(cid: str) -> str:
    """HL cloid = 0x + 32 hex. Derived from Intent client_order_id (uuid hex)."""
    h = str(cid or "").replace("-", "").replace("0x", "").replace("0X", "").lower()
    h = "".join(c for c in h if c in "0123456789abcdef")
    if len(h) < 32:
        h = (h + "0" * 32)[:32]
    else:
        h = h[:32]
    return "0x" + h


def build_perp_order_action(coin, is_buy, sz, bound_px, tif="IOC",
                            reduce_only=False, cloid=None, leverage=0,
                            asset_index=None):
    """Unsigned intended /exchange action. Does not send.

    `a` is the coin name in this stub. NT cutover resolves meta.universe index.
    leverage must be 0 until Burton approval.
    """
    if leverage not in (0, 0.0, None):
        raise ValueError("leverage must be 0 until Burton approval")
    if sz is None or float(sz) <= 0:
        raise ValueError("sz (base size) required")
    if bound_px is None or float(bound_px) <= 0:
        raise ValueError("bound_px required (HL market-style is Ioc limit)")
    coin_u = hl_coin(coin)
    if not coin_u:
        raise ValueError("coin required")
    tif_hl = _TIF.get(str(tif or "IOC").strip().upper(), "Ioc")
    cid = cloid if (cloid and str(cloid).startswith("0x")) else cloid_from_client_order_id(cloid or "")
    order = {
        "a": asset_index if asset_index is not None else coin_u,
        "b": bool(is_buy),
        "p": str(bound_px),
        "s": str(sz),
        "r": bool(reduce_only),
        "t": {"limit": {"tif": tif_hl}},
        "c": cid,
    }
    return {
        "path": EXCHANGE_PATH,
        "host": EXCHANGE_HOST,
        "signed": False,
        "live_client_hook": LIVE_CLIENT_HOOK,
        "leverage": 0,
        "action": {
            "type": "order",
            "orders": [order],
            "grouping": "na",
        },
        "note": "stub shape for shadow POST-diff; no live client; asset index at NT cutover",
    }


def send_via_live_hook(action):
    """HOOK: later NT Hyperliquid adapter. Always refuse in this lane."""
    if LIVE_CLIENT_HOOK is None:
        return {"sent": False, "reason": "no live client (stub)"}
    raise RuntimeError("Slice 11 must not bind LIVE_CLIENT_HOOK")
