"""Sports evidence — what the INFORMED gate sees for a sports_engine bet.

Moved out of sports_engine.py (500-line rule), behavior unchanged; sports_engine
re-exports these names and passes its own kalshi `get` and attestation path, so the
Kalshi GET seam stays sports_engine.get. No orders here; read-only GETs only.

The only data sports_engine has is the Kalshi book itself: each game is two
complementary YES markets (fav, dog). FV = de-vigged two-sided mid of that
book. That is a MARKET price, so fv_source is "market" (not in
informed_before_create.ALLOWED_FV): the gate refuses it with "fv_source"
until a real model/researched FV exists. Attestation is read from a file
Burton signs; this module never synthesizes one.

(c) GOATWORTHY Post LLC. All rights reserved.
"""
import json
from datetime import datetime, timezone

import forward_test_ledger as ftl

SPORTS_EVIDENCE_VERSION = "sports-evidence-1"
FV_METHOD = "kalshi_two_sided_mid_devig"
FV_COHERENCE_TOL = 0.05  # |fav_mid + dog_mid - 1| above this → books disagree → no fv_confidence
_MARKET_KEYS = ("status", "open_time", "close_time", "expected_expiration_time",
                "expiration_time", "can_close_early", "early_close_condition",
                "settlement_timer_seconds")


def _parse_ts(v):
    try:
        s = str(v).strip()
        dt = datetime.fromisoformat(s[:-1] + "+00:00" if s.endswith("Z") else s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def load_sports_attestation(path, now=None):
    """(attestation dict | None, status). Compliance/geo attestation Burton signs.

    Required shape: {"valid": true, "geo": "pass", "attested_by": str,
    "expires_at": ISO-8601}. Anything else (missing, unreadable, invalid,
    geo != pass, no/expired expiry) → None. Never returns a synthesized True.
    """
    try:
        with open(path) as f:
            doc = json.load(f)
    except FileNotFoundError:
        return None, "attestation_missing"
    except Exception:
        return None, "attestation_unreadable"
    if not isinstance(doc, dict):
        return None, "attestation_shape"
    if doc.get("valid") is not True:
        return None, "attestation_invalid"
    if str(doc.get("geo") or "").lower() != "pass":
        return None, "attestation_geo"
    if not str(doc.get("attested_by") or "").strip():
        return None, "attestation_unsigned"
    exp = _parse_ts(doc.get("expires_at"))
    if exp is None:
        return None, "attestation_no_expiry"
    if exp <= (now or datetime.now(timezone.utc)):
        return None, "attestation_expired"
    return dict(doc), "ok"


def _levels(book, side):
    """[(price_dollars, qty)] from a Kalshi orderbook side (dollars or cents form)."""
    for key, scale in ((f"{side}_dollars", 1.0), (side, 0.01)):
        rows = book.get(key)
        if not rows:
            continue
        out = []
        for row in rows:
            try:
                px, qty = float(row[0]) * scale, float(row[1])
            except (TypeError, ValueError, IndexError):
                continue
            if 0 < px < 1 and qty > 0:
                out.append((round(px, 4), qty))
        if out:
            return out
    return []


def parse_orderbook(raw):
    """GET /markets/{t}/orderbook → YES bid/ask/depth. YES ask = 1 − best NO bid."""
    doc = raw if isinstance(raw, dict) else {}
    yes, no = [], []
    for cont in (doc.get("orderbook_fp"), doc.get("orderbook"), doc):
        if isinstance(cont, dict):
            yes, no = _levels(cont, "yes"), _levels(cont, "no")
            if yes or no:
                break
    bid = max(yes)[0] if yes else None
    best_no = max(no) if no else None
    ask = round(1.0 - best_no[0], 4) if best_no else None
    return {"bid": bid, "ask": ask, "depth": best_no[1] if best_no else None,
            "spread": round(ask - bid, 4) if bid is not None and ask is not None else None}


def read_book(ticker, get):
    """Read-only Kalshi orderbook GET. None when unavailable (fail-closed upstream)."""
    if not ticker:
        return None
    try:
        book = parse_orderbook(get(f"/markets/{ticker}/orderbook"))
    except Exception:
        return None
    return book if book.get("bid") is not None or book.get("ask") is not None else None


def fair_value_from_books(bet, fav_book, dog_book):
    """De-vig of the two complementary YES books: fv = fav_mid / (fav_mid + dog_mid).

    fv_confidence rule: both sides two-sided AND |fav_mid + dog_mid − 1| ≤
    FV_COHERENCE_TOL (the two books agree); otherwise None. fv_source is
    "market" — honest, and NOT admitted by informed_before_create.ALLOWED_FV.
    """
    out = {"fv": None, "fv_source": "market", "fv_confidence": None, "fv_method": FV_METHOD}
    mids = []
    for bk in (fav_book, dog_book):
        b, a = (bk or {}).get("bid"), (bk or {}).get("ask")
        if b is None or a is None or b <= 0 or a <= 0 or a < b:
            out["fv_why"] = "one_sided_book"
            return out
        mids.append((b + a) / 2.0)
    total = mids[0] + mids[1]
    out.update(fav_mid=round(mids[0], 4), dog_mid=round(mids[1], 4), book_sum=round(total, 4),
               fv=round(mids[0] / total, 4))
    if abs(total - 1.0) <= FV_COHERENCE_TOL:
        out["fv_confidence"] = "two_sided_coherent"
    else:
        out["fv_why"] = "books_disagree"
    return out


def build_sports_opp(b, get, attestation_path, bankroll=None, fv_fn=None, fmap=None,
                     now=None, category="SPORTS"):
    """(opp, evidence) for one sized bet. Missing evidence is left absent, never faked."""
    ticker = b['fav_ticker']
    n = int(b.get('contracts') or 0)
    fav_book, dog_book = read_book(ticker, get), read_book(b.get('dog_ticker'), get)
    market = b.get('fav_market') if isinstance(b.get('fav_market'), dict) else None
    if market is None:
        try:
            m = get(f"/markets/{ticker}")
            market = m.get("market", m) if isinstance(m, dict) else None
        except Exception:
            market = None
    fvd = (fv_fn or fair_value_from_books)(b, fav_book, dog_book) or {}
    att, att_status = load_sports_attestation(attestation_path, now=now)
    bid = (fav_book or {}).get("bid")
    ask = (fav_book or {}).get("ask")
    fv = fvd.get("fv")
    edge = round(fv - ask, 4) if fv is not None and ask is not None else None
    fee_per = ftl.kalshi_fee(ask, n) / n if ask is not None and n > 0 else None
    ev = {
        "version": SPORTS_EVIDENCE_VERSION,
        "ticker": ticker, "dog_ticker": b.get('dog_ticker'),
        "fv": fv, "fv_source": fvd.get("fv_source"), "fv_confidence": fvd.get("fv_confidence"),
        "fv_detail": {k: v for k, v in fvd.items() if k not in ("fv", "fv_source", "fv_confidence")},
        "bid": bid, "ask": ask, "depth": (fav_book or {}).get("depth"),
        "spread": (fav_book or {}).get("spread"), "dog_book": dog_book,
        "book_source": "GET /markets/{ticker}/orderbook" if fav_book else None,
        "slip": 0.0 if ask is not None else None, "slip_basis": "limit_at_best_ask",
        "edge": edge, "fee_per": fee_per,
        "edge_fee_true": round(edge - fee_per, 4) if edge is not None and fee_per is not None else None,
        "bankroll": bankroll if bankroll and bankroll > 0 else None,
        "market": {k: market.get(k) for k in _MARKET_KEYS if k in market} if market else None,
        "sports_attestation": att, "attestation_status": att_status,
        "order_price_cents": int(round(float(b['fav_price']) * 100)), "contracts": n,
    }
    opp = {
        "platform": "kalshi", "type": category, "strategy": category, "category": category,
        "desk": "kalshi_sports", "ticker": ticker, "side": "yes",
        "series": b.get('series') or None, "price": ev["order_price_cents"] / 100.0,
        "market": market, "fv": fv, "fv_source": fvd.get("fv_source"),
        "fv_confidence": fvd.get("fv_confidence"), "edge": edge, "slip": ev["slip"],
        "bid": bid, "ask": ask, "yes_bid": bid, "yes_ask": ask, "depth": ev["depth"],
        "bankroll": ev["bankroll"], "sports_attestation": att, "fmap": fmap,
    }
    return {k: v for k, v in opp.items() if v is not None}, ev


def _skip(r, status, reasons):
    r['status'] = status
    r['reasons'] = list(reasons)
    r['error'] = ",".join(str(x) for x in reasons)[:160]


def _pre_create(opp, n, price_cents, r):
    """Allocator pre-create discipline (master_allocator.execute_allocations):
    EV preflight → session_clocks.before_create → informed create_preflight.
    Returns the preflight dict on admit, else None with r marked SKIP_*."""
    try:
        import kalshi_profit as _kp
        proceed, opp = _kp.annotate(opp, contracts=n)
        r['ev_preflight'] = opp.get("ev_preflight")
        if not proceed:
            _skip(r, 'SKIP_EV_PREFLIGHT', ["ev_preflight"])
            return None
        mf = opp.get("maker_first") or {}
        # No maker manager on this lane: a maker-preferred book is skipped, never upgraded.
        if _kp.maker_first_on() and mf.get("prefer") == "maker" and _kp.should_skip_taker(mf):
            _skip(r, 'SKIP_MAKER_FIRST', ["maker_first"])
            return None
    except Exception as e:
        _skip(r, 'SKIP_EV_PREFLIGHT', [f"ev preflight failed closed: {str(e)[:60]}"])
        return None
    import session_clocks as _sc
    blocked = _sc.before_create(opp)
    if blocked:
        _skip(r, blocked[0], [blocked[1]])
        return None
    import informed_before_create as _ibc
    pre = _ibc.create_preflight(opp, n, price_cents)
    if not pre["ok"]:
        _skip(r, 'SKIP_INFORMED', pre["reasons"])
        return None
    if pre.get("jev") is not None:
        r['jev'] = pre["jev"]
    return pre


def admit_bet(b, r, build):
    """Evidence → price check → allocator chain → fee-true edge, for one bet.

    (preflight, price_cents) on admit; (None, price_cents|None) with r marked SKIP_*.
    """
    try:
        opp, evidence = build(b)
    except Exception as e:
        _skip(r, 'SKIP_EVIDENCE', [f"evidence_error:{type(e).__name__}"])
        return None, None
    r['evidence'], r['evidence_version'] = evidence, SPORTS_EVIDENCE_VERSION
    price_cents = evidence["order_price_cents"]
    ask = evidence.get("ask")
    if ask is not None and int(round(ask * 100)) != price_cents:
        _skip(r, 'SKIP_EVIDENCE', ["price_moved"])  # scan ask ≠ live book ask: re-scan next cycle
        return None, price_cents
    pre = _pre_create(opp, b['contracts'], price_cents, r)
    if pre is None:
        return None, price_cents
    eft = evidence.get("edge_fee_true")
    if eft is None or eft <= 0:
        _skip(r, 'SKIP_EVIDENCE', ["edge_fee_true_nonpositive"])
        return None, price_cents
    return pre, price_cents


def record_skip(log, r):
    """Append one SKIP_* result to log["skips"] (never log["bets"]: skips never settle)."""
    log.setdefault("skips", []).append({
        "fav_ticker": r["fav_ticker"], "ts": r["ts"], "status": r["status"],
        "reasons": r.get("reasons", []), "contracts": r.get("contracts"),
        "fav_price": r.get("fav_price"),
        "evidence_version": r.get("evidence_version", SPORTS_EVIDENCE_VERSION),
        "evidence": r.get("evidence"),
    })
