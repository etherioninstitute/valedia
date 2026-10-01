"""spot_ledger.py — book LIVE Coinbase/Alpaca spot fills into forward_test_ledger.

Bookkeeping only. Never places, retries or blocks an order. Used by the live spot
writers (unified_brain BUY/SELL, strategy_modules.DCAEngine, dca_module.run_dca).

  book_buy()  -> forward_test_ledger.book_spot_fill: fee-true FILLED entry when the
                 broker body carries fill qty+price (fee from the body, else fee_engine
                 static fee with fee_status="modeled"); POST-ack only -> UNRECONCILED
                 entry with the venue order_id (spot_reconcile cron upgrades it later).
  book_sell() -> complete fill: FIFO-close the open live lots for ticker+venue with
                 fee-true settlements (realized = (exit-entry)*qty - entry fee - exit fee);
                 POST-ack only -> UNRECONCILED sell entry with order_id (cron settles).

Returned status is "EXECUTED" only when extract_spot_fill(...).complete; otherwise
"UNRECONCILED". Paper/shadow/sandbox sources write nothing ("SKIPPED_NON_LIVE").
Any bookkeeping error is printed to stderr and falls back to an UNRECONCILED row
(mirrors sports_engine: never silent-live when bookkeeping failed).
(c) GOATWORTHY Post LLC.
"""
import sys

BRAIN = {"coinbase": "BRAIN_CRYPTO", "alpaca": "BRAIN_EQUITY"}
DCA = {"coinbase": "DCA_CRYPTO", "alpaca": "DCA_EQUITY"}
EPS = 1e-9


def _ftl():
    import forward_test_ledger as ftl  # lazy: honors test fakes in sys.modules
    return ftl


def _warn(where, err):
    print(f"[spot_ledger] {where} failed: {err}", file=sys.stderr)


def venue_source(venue, alpaca_live=None):
    """"live" for Coinbase, Alpaca only when alpaca_is_live(); anything else "paper"."""
    v = str(venue or "").lower()
    if "coinbase" in v:
        return "live"
    if "alpaca" in v:
        if alpaca_live is None:
            try:
                import alpaca_executor as ae
                alpaca_live = bool(ae.alpaca_is_live())
            except Exception as e:
                _warn("alpaca_is_live", e)
                alpaca_live = False
        return "live" if alpaca_live else "paper"
    return "paper"


def _clean(resp):
    """Broker body as a dict; coinbase_trader's "?" placeholder is not an order id."""
    raw = dict(resp) if isinstance(resp, dict) else {}
    if str(raw.get("order_id")) in ("?", "", "None"):
        raw.pop("order_id", None)
    return raw


def _order_id(raw):
    sr = raw.get("success_response") if isinstance(raw.get("success_response"), dict) else {}
    oid = raw.get("order_id") or raw.get("id") or sr.get("order_id")
    return None if str(oid) in ("?", "", "None") else oid


def _stub(ftl, strategy, ticker, side, venue, raw, where, err):
    """Bookkeeping failed: log, then leave a visible UNRECONCILED row (never live)."""
    _warn(where, err)
    try:
        eid = ftl.record_unreconciled(strategy, ticker, side, 0.0, 0.0, venue,
                                      order_id=_order_id(raw), intended_source="live")
    except Exception as e2:
        _warn(f"{where} UNRECONCILED fallback", e2)
        eid = None
    return {"status": "UNRECONCILED", "id": eid, "error": str(err)[:120]}


def _gate(ftl, venue, raw, intended_source):
    if str(intended_source or "") != "live":
        return {"status": "SKIPPED_NON_LIVE", "id": None}
    if not ftl.spot_ack_ok(venue, raw):
        return {"status": "NOT_ACKED", "id": None}
    return None


def book_buy(strategy, venue, ticker, resp, intended_source="live"):
    """One ledger entry per acked live buy. Never raises."""
    raw = _clean(resp)
    try:
        ftl = _ftl()
    except Exception as e:
        _warn("import forward_test_ledger", e)
        return {"status": "UNRECONCILED", "id": None, "error": str(e)[:120]}
    try:
        skip = _gate(ftl, venue, raw, intended_source)
        if skip:
            return skip
        fill = ftl.extract_spot_fill(venue, raw)
        eid = ftl.book_spot_fill(strategy, ticker, "buy", venue, raw, intended_source="live")
        return {"status": "EXECUTED" if fill["complete"] else "UNRECONCILED", "id": eid}
    except Exception as e:
        return _stub(ftl, strategy, ticker, "buy", venue, raw, f"book_buy {strategy} {ticker}", e)


def _open_lots(ftl, ticker, venue):
    recs = ftl._read()
    done = {r.get("entry_id") for r in recs if r.get("type") == "settlement"}
    lots = []
    for e in recs:
        if e.get("type") != "entry" or e.get("id") in done:
            continue
        if e.get("ticker") != ticker or e.get("venue") != venue:
            continue
        if str(e.get("side") or "buy").lower() != "buy" or e.get("source") != "live":
            continue  # paper/sandbox/unreconciled lots are never closed by a live sell
        if float(e.get("contracts") or 0) > 0 and float(e.get("entry_price") or 0) > 0:
            lots.append(e)
    return sorted(lots, key=lambda e: str(e.get("ts") or ""))  # FIFO (stable)


def settle_lots(ticker, venue, exit_price, qty, exit_fee, fee_status="modeled",
                exit_strategy=None, order_id=None):
    """FIFO-close open live lots with fee-true settlements. Returns (settlements, excess).

    A lot only partly covered is settled for the sold part; the rest re-opens as a new
    FILLED entry (same ts/price, pro-rata entry fee) referenced by remainder_id.
    """
    ftl = _ftl()
    exit_price, qty, exit_fee = float(exit_price), float(qty), float(exit_fee or 0)
    remaining, out = qty, []
    for e in _open_lots(ftl, ticker, venue):
        if remaining <= EPS:
            break
        c = float(e["contracts"])
        take = min(c, remaining)
        frac = take / c
        entry_px = float(e["entry_price"])
        entry_fee = round(float(e.get("entry_fee") or 0) * frac, 6)
        exit_share = round(exit_fee * take / qty, 6) if qty > 0 else 0.0
        gross = round((exit_price - entry_px) * take, 6)
        net = round(gross - entry_fee - exit_share, 4)
        rem_id = None
        if c - take > EPS:
            rem_id = ftl.record_entry(
                e.get("strategy", "?"), ticker, e.get("side", "buy"), entry_px, round(c - take, 10),
                venue=venue, predicted_direction=e.get("predicted_direction"),
                predicted_prob=e.get("predicted_prob"), source="live", ts=e.get("ts"),
                fill_status="FILLED", entry_fee=round(float(e.get("entry_fee") or 0) - entry_fee, 6),
                order_id=e.get("order_id"), fee_status=e.get("fee_status"))
        both_venue = fee_status == "venue" and e.get("fee_status") == "venue"
        rec = {"type": "settlement", "entry_id": e["id"], "ticker": ticker,
               "strategy": e.get("strategy", "?"), "result": "exit",
               "filled_contracts": take, "fill_note": "roundtrip_exit",
               "realized_pnl_net": net, "won": net > 0, "hit": None, "source": "live",
               "exit_price": exit_price, "gross": gross,
               "fee": round(entry_fee + exit_share, 6), "entry_fee": entry_fee,
               "exit_fee": exit_share, "fee_status": "venue" if both_venue else "modeled",
               "exit_strategy": exit_strategy, "order_id": order_id}
        if rem_id:
            rec["remainder_id"] = rem_id
        ftl._append(rec)
        out.append(rec)
        remaining -= take
    return out, (remaining if remaining > EPS else 0.0)


def book_sell(exit_strategy, venue, ticker, resp, intended_source="live"):
    """Settle an acked live sell. Never raises."""
    raw = _clean(resp)
    try:
        ftl = _ftl()
    except Exception as e:
        _warn("import forward_test_ledger", e)
        return {"status": "UNRECONCILED", "id": None, "error": str(e)[:120]}
    try:
        skip = _gate(ftl, venue, raw, intended_source)
        if skip:
            return skip
        fill = ftl.extract_spot_fill(venue, raw)
        oid = fill.get("order_id") or _order_id(raw)
        if not fill["complete"]:
            eid = ftl.record_unreconciled(exit_strategy, ticker, "sell", fill["price"],
                                          fill["qty"], venue, order_id=oid,
                                          intended_source="live")
            return {"status": "UNRECONCILED", "id": eid, "settlements": []}
        if fill["fee"] is not None:
            fee, fee_status = float(fill["fee"]), "venue"
        else:
            fee, fee_status = ftl._spot_fee(venue, fill["price"], fill["qty"]), "modeled"
        setts, excess = settle_lots(ticker, venue, fill["price"], fill["qty"], fee,
                                    fee_status, exit_strategy=exit_strategy, order_id=oid)
        out = {"status": "EXECUTED", "id": None, "settlements": setts,
               "realized": round(sum(s["realized_pnl_net"] for s in setts), 4)}
        if excess > 0:
            # Sold more than the ledger holds (pre-ledger lots, or a buy still UNRECONCILED).
            # No order_id: the cron must not settle a later, unrelated lot with this exit.
            out["excess_id"] = ftl.record_unreconciled(
                exit_strategy, ticker, "sell", fill["price"], excess, venue,
                intended_source="live")
            out["excess"] = excess
        return out
    except Exception as e:
        return _stub(ftl, exit_strategy, ticker, "sell", venue, raw,
                     f"book_sell {exit_strategy} {ticker}", e)
