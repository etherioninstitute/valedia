"""Allocator execute routing: which allocations may reach a venue, and how.

Two default-OFF flags (env), read per call:

- VALEDIA_SCANNER_EXECUTE: `kalshi_scanner` (SCANNER_MACRO) allocations run the
  full Kalshi path in master_allocator.execute_allocations (halt, pause, live-exec
  fence, blacklist, category gate, intel vetting, held, EV preflight, session
  clocks, informed create_preflight, Nimble). Off → SKIP_SCANNER_DISABLED.
  Scanner opps carry no fv/fv_source; the informed gate refuses them. Nothing
  here fills evidence in.
- VALEDIA_ALLOCATOR_SPOT_EXECUTE: Coinbase / Alpaca allocations call the real
  writers (coinbase_trader.market_buy, alpaca_executor.market_buy) after
  venue_writer == "valedia" and require_not_halted. Fill-truth only:
  EXECUTED needs extract_spot_fill.complete; a POST-ack is UNRECONCILED.
  Alpaca follows ALPACA_PAPER (alpaca_executor.alpaca_is_live): a paper account
  books intended_source="paper" and the status is PAPER_*. Off →
  SKIP_ALLOCATOR_SPOT_DISABLED.

No flag here changes EXEC_DRY_RUN, ALLOWED_FV, VENUE_WRITER or ALPACA_PAPER.
(c) GOATWORTHY Post LLC. All rights reserved.
"""
import os

SCANNER_FLAG = "VALEDIA_SCANNER_EXECUTE"
SPOT_FLAG = "VALEDIA_ALLOCATOR_SPOT_EXECUTE"
SCANNER_PLATFORM = "kalshi_scanner"
KALSHI_PLATFORMS = ("kalshi", "kalshi_weather")
SPOT_VENUES = ("coinbase", "alpaca")
_ON = frozenset({"1", "true", "yes", "on"})


def _flag(name, env=None):
    v = (os.environ if env is None else env).get(name)
    return str(v or "").strip().lower() in _ON


def scanner_execute_on(env=None):
    """VALEDIA_SCANNER_EXECUTE. Default OFF."""
    return _flag(SCANNER_FLAG, env)


def spot_execute_on(env=None):
    """VALEDIA_ALLOCATOR_SPOT_EXECUTE. Default OFF."""
    return _flag(SPOT_FLAG, env)


def kalshi_path(platform, env=None):
    """True when the allocation runs the full Kalshi gate chain and create path."""
    p = str(platform or "").strip().lower()
    return p in KALSHI_PLATFORMS or (p == SCANNER_PLATFORM and scanner_execute_on(env))


def skip_unrouted(platform):
    """(status, error) for an allocation no execute branch takes."""
    if str(platform or "").strip().lower() == SCANNER_PLATFORM:
        return ("SKIP_SCANNER_DISABLED",
                f"{SCANNER_FLAG} off (default): scanner opps are sized, never executed")
    return "SKIP", f"Unknown platform: {platform}"


def spot_disabled(venue):
    return ("SKIP_ALLOCATOR_SPOT_DISABLED",
            f"{SPOT_FLAG} off (default): allocator {venue} execute disabled")


def _pair(ticker):
    t = str(ticker or "").strip().upper()
    return t if "-" in t else t + "-USD"


def _writer(venue):
    """(buy_fn, intended_source). The real Valedia writers; no other order path."""
    if venue == "coinbase":
        import coinbase_trader as ct
        return (lambda a: ct.market_buy(_pair(a["ticker"]), float(a["allocation"]))), "live"
    import alpaca_executor as ae
    src = "live" if ae.alpaca_is_live() else "paper"
    return (lambda a: ae.market_buy(a["ticker"], notional=float(a["allocation"]))), src


def _rejected(raw):
    """Venue said no (nothing placed): Coinbase success False / error, Alpaca error body."""
    if raw.get("error"):
        return str(raw["error"])
    if raw.get("success") is False:
        return "venue success=false"
    if raw.get("code") is not None and raw.get("message") and not raw.get("id"):
        return str(raw["message"])
    return None


def spot_execute(a, venue, r, *, cfg, fill_status, book=None, recovery=None):
    """Coinbase/Alpaca buy for one allocation. Mutates and returns r. Never raises
    for a gate refusal; a writer exception propagates to the caller's FAILED path.

    cfg: valedia_config (None → refused: venue_writer cannot be checked).
    fill_status(venue, raw) → EXECUTED | UNRECONCILED (master_allocator.spot_fill_status).
    book: forward_test_ledger.book_spot_fill (fill-true or UNRECONCILED row).
    """
    from recovery_runtime import execute_leg
    return execute_leg(a, venue, r, recovery)
    venue = str(venue or "").strip().lower()
    if venue not in SPOT_VENUES:
        r["status"], r["error"] = "SKIP", f"Unknown spot venue: {venue}"
        return r
    if not spot_execute_on():
        r["status"], r["error"] = spot_disabled(venue)
        return r
    if cfg is None:
        r["status"], r["error"] = "SKIP_WRITER", "valedia_config unavailable; writer unverifiable"
        return r
    if cfg.venue_writer(venue) != "valedia":
        r["status"], r["error"] = "SKIP_WRITER", f"{venue} writer not valedia"
        return r
    try:
        cfg.require_not_halted(f"allocator.{venue}")
    except cfg.HaltedError as e:
        r["status"], r["error"] = "HALTED", str(e)
        return r
    side = str(a.get("side") or "").strip().lower()
    if side != "buy":
        r["status"], r["error"] = "SKIP_SPOT_SIDE", f"allocator spot path is buy-only (side={side})"
        return r
    try:
        amount = float(a.get("allocation") or 0)
    except (TypeError, ValueError):
        amount = 0.0
    if amount <= 0:
        r["status"], r["error"] = "SKIP_SPOT_SIZE", "allocation <= 0"
        return r
    buy, src = _writer(venue)
    r["intended_source"] = src
    resp = buy(a)
    raw = resp if isinstance(resp, dict) else {}
    why = _rejected(raw)
    if why:
        r["status"], r["error"] = "FAILED", why[:80]
        return r
    st = fill_status(venue, raw)
    r["status"] = st if src == "live" else "PAPER_" + st
    if raw.get("order_id") or raw.get("id"):
        r["order_id"] = str(raw.get("order_id") or raw.get("id"))[:24]
    if book is not None:
        strat = a.get("type") or ("CRYPTO" if venue == "coinbase" else "EQUITY")
        try:
            book(strat, a["ticker"], side, venue, raw, intended_source=src)
        except Exception as e:
            r["ledger_error"] = str(e)[:80]
    return r
