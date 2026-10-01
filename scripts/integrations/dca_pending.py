"""DCAEngine ambiguous-POST guard (strategy_modules.DCAEngine). Never double-buy.

A pending marker is saved BEFORE each buy. It is cleared only on a DEFINITIVE answer:
  acked     the venue accepted the order (DCAEngine._acked)
  refused   an explicit venue rejection: `rejected` flag (Coinbase 2xx success=false), a 4xx
            (not 408) carrying a reason, or a rejection reason in the text (insufficient funds,
            invalid/unsupported product, ...); or a pre-POST refusal (Nimble veto, writer lock,
            trading halt) raised before any order was sent.
Everything else is AMBIGUOUS and keeps the marker: 5xx / 408 (`ambiguous` flag from the venue
wrapper), timeouts, connection errors, unparseable or bare `success: False` bodies.
A leftover marker is resolved on the next DUE run via the venue's read-only recent_buy_orders:
found → booked (counts as an ack only if the attempt belongs to the current window), never
re-bought; venue says none → cleared and retried; lookup failed → pair skipped this run.
No network of its own; the lookup is the venue module's read-only helper (or an injected fake).
"""
from datetime import datetime, timedelta

NOT_PLACED = ("NimbleSkip", "VenueWriterSkip", "HaltedError")  # raised before any POST
REJECT_WORDS = ("insufficient", "invalid", "rejected", "unsupported", "unknown product",
                "not allowed", "forbidden", "below minimum", "minimum order")
SKEW_S = 120  # look back before the attempt (venue clock skew)


def outcome(result=None, exc=None, acked=False):
    """"acked" | "refused" | "ambiguous" for one buy attempt."""
    if exc is not None:
        return "refused" if type(exc).__name__ in NOT_PLACED else "ambiguous"
    if acked:
        return "acked"
    if not isinstance(result, dict) or result.get("ambiguous"):
        return "ambiguous"
    if result.get("rejected"):
        return "refused"
    st = result.get("http_status")
    text = str(result.get("error") or result.get("message") or "").strip()
    if isinstance(st, int) and (st >= 500 or st == 408):
        return "ambiguous"
    if isinstance(st, int) and 400 <= st < 500 and text:
        return "refused"
    low = text.lower()
    return "refused" if any(w in low for w in REJECT_WORDS) else "ambiguous"


def _lookup(venue, lookup):
    if lookup is not None:
        return lookup
    mod = {"coinbase": "coinbase_trader", "alpaca": "alpaca_executor"}.get(venue)
    return (lambda pair, since: __import__(mod).recent_buy_orders(pair, since)) if mod else None


def resolve(engine, venue, lookup=None, book=None):
    """(skip_pairs, found_acks, actions). Call only when the week is due."""
    pend = engine.state.get("pending") or {}
    last = engine.state.get("last_buy")
    skip, found, acts = set(), 0, []
    fn = _lookup(venue, lookup)
    for pair, p in list(pend.items()):
        since = datetime.fromisoformat(p["ts"]) - timedelta(seconds=SKEW_S)
        try:
            orders = fn(pair, since.strftime("%Y-%m-%dT%H:%M:%SZ")) if fn else None
        except Exception:
            orders = None
        if orders is None:
            skip.add(pair)
            acts.append(f"DCA: {pair} previous attempt unresolved (venue lookup failed); not re-buying")
            continue
        pend.pop(pair)
        if not orders:
            continue
        o = dict(orders[0], success=True, pair=pair)
        engine._record_buy(pair, p["amount"], o, venue, book)
        if not last or p["ts"] > last:  # this window's attempt → it IS this week's buy
            found += 1
            skip.add(pair)
        acts.append(f"DCA: {pair} previous attempt found ({o.get('order_id')}) -> booked, not re-bought")
    engine.state["pending"] = pend
    if acts:
        try:
            engine._save()
        except Exception:
            pass
    return skip, found, acts
