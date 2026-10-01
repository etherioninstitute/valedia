"""Kalshi book quotes in 0–1 dollars.

Prefer yes_bid_dollars / yes_ask_dollars / last_price_dollars (already 0–1).
Fall back to legacy yes_bid / yes_ask / last_price (cents if >1, else 0–1).
Zero or missing is no quote — do not invent a book.
"""


def _to_dollars(raw):
    if raw is None or raw == "":
        return None
    try:
        x = float(raw)
    except (TypeError, ValueError):
        return None
    if x > 1.0:
        x = x / 100.0
    if x <= 0.0:
        return None
    return round(x, 6)


def quote(market):
    """Return (yes_bid, yes_ask, last, mid) as 0–1 floats, or None when absent."""
    m = market if isinstance(market, dict) else {}
    bid = _to_dollars(m.get("yes_bid_dollars"))
    if bid is None:
        bid = _to_dollars(m.get("yes_bid"))
    ask = _to_dollars(m.get("yes_ask_dollars"))
    if ask is None:
        ask = _to_dollars(m.get("yes_ask"))
    last = _to_dollars(m.get("last_price_dollars"))
    if last is None:
        last = _to_dollars(m.get("last_price"))
    mid = round((bid + ask) / 2.0, 6) if bid is not None and ask is not None else None
    return bid, ask, last, mid


def yes_price(market):
    """Buy-YES fill for fee-true weather.

    Mid when both bid and ask quote. Else last. Else the one side that exists.
    """
    bid, ask, last, mid = quote(market)
    if mid is not None:
        return mid
    if last is not None:
        return last
    if bid is not None:
        return bid
    return ask
