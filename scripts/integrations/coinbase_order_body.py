"""Pure Coinbase Advanced Trade market-order POST body.

No network, no keys, no jwt. Used by coinbase_trader (the live writer) and
by nautilus_shadow_coinbase (Slice 2 compare). Keep these shapes identical.
(c) GOATWORTHY Post LLC.
"""
import secrets


def build_market_order_body(pair, side, usd_amount=None, crypto_amount=None,
                            client_order_id=None):
    """POST /api/v3/brokerage/orders body. Does not send, sign, or load keys."""
    side_u = str(side or "").strip().upper()
    cid = client_order_id or secrets.token_hex(16)
    if side_u == "BUY":
        if usd_amount is None:
            raise ValueError("buy requires usd_amount (quote_size)")
        ioc = {"quote_size": f"{float(usd_amount):.2f}"}
    elif side_u == "SELL":
        if crypto_amount is None:
            raise ValueError("sell requires crypto_amount (base_size)")
        ioc = {"base_size": f"{crypto_amount}"}
    else:
        raise ValueError("side must be buy|sell")
    return {
        "client_order_id": cid,
        "product_id": pair,
        "side": side_u,
        "order_configuration": {"market_market_ioc": ioc},
    }
