"""forward_test_ledger.py — the measurement layer Valedia was missing.

Append-only, fee-inclusive, fill-reconciled per-trade ledger. This is the ONLY
admissible source of an accuracy/edge claim (per SDA HOLDS 2026-09-09). No sizing
change, no $ deployment, until THIS ledger shows a positive fee-inclusive edge
across many independent events.

Design:
  - Append-only JSONL at models/forward_test_ledger.jsonl. History is never rewritten;
    a settlement is a NEW record referencing an entry id.
  - ONE canonical fee (Scout found 5 divergent constants across engines): Kalshi's
    published trading fee = ceil(0.07 * C * P * (1-P)) rounded up to the cent, per side.
  - record_entry(): log an OPEN position with the model's prediction (direction/prob).
  - reconcile(): for each open entry, pull the REAL fill (kalshi get_fills) and the
    settled result; compute fee-inclusive realized P&L and whether the prediction hit.
  - summary(): per-strategy fee-inclusive net, hit rate, Brier (if probs present).

CLI: record | reconcile | summary | backfill
(c) GOATWORTHY Post LLC.
"""
import os, sys, json, math, time, uuid, argparse
from datetime import datetime, timezone

MODELS = os.path.expanduser("~/.openclaw/workspace/models")
LEDGER = os.path.join(MODELS, "forward_test_ledger.jsonl")
OVERRIDES = os.path.join(MODELS, "category_overrides.json")  # {"parked":[...],"retest":[...]}


# ── canonical fee (the single source of truth) ──────────────────────────────
def kalshi_fee(price, contracts):
    """Kalshi trading fee, per side: ceil(0.07 * C * P * (1-P)) to the next cent."""
    p = max(0.0, min(1.0, float(price)))
    raw = 0.07 * float(contracts) * p * (1.0 - p)
    return math.ceil(raw * 100.0) / 100.0


# Sports labels that must share one category-gate / Kelly bucket (allocator
# used SPORTS_TONIGHT while sports_engine used SPORTS — two crons, split gate).
CATEGORY_ALIASES = {"SPORTS_TONIGHT": "SPORTS"}


def canonical_category(category):
    """Map alias labels onto the gate/ledger bucket. Unknown names pass through."""
    c = str(category or "")
    return CATEGORY_ALIASES.get(c, c)


def _category_keys(name):
    """A category and every alias that folds into the same bucket."""
    raw = str(name or "")
    canon = canonical_category(raw)
    keys = {raw, canon}
    for alias, target in CATEGORY_ALIASES.items():
        if target == canon or alias == raw:
            keys.add(alias)
            keys.add(target)
    return keys


def _is_non_real_source(source):
    """Paper/sandbox/unreconciled/backfill/shadow rows never count as edge.
    'sandbox' also matches nautilus_sandbox. UNRECONCILED POST-acks are visible
    in the ledger but must not lift Kelly until a real fill is booked.
    `backfill:` (e.g. backfill:pnl_tracker) is unverified-fee history.
    `shadow` / `intel_shadow` are Slice 1/10 never-dispatchable Intents — same
    family. None of these may lift _track_record_mult or category_stats."""
    s = str(source or "").lower()
    return ("paper" in s or "sandbox" in s or "unreconciled" in s
            or "backfill" in s or "shadow" in s)


def _append(rec):
    rec.setdefault("logged_at", datetime.now(timezone.utc).isoformat())
    with open(LEDGER, "a") as f:
        f.write(json.dumps(rec) + "\n")


def _read():
    if not os.path.exists(LEDGER):
        return []
    return [json.loads(l) for l in open(LEDGER) if l.strip()]


def _spot_fee(venue, price, contracts):
    """Coinbase/Alpaca entry fee from fee_engine STATIC_FEES (no network). Kalshi uses kalshi_fee."""
    notional = abs(float(price or 0) * float(contracts or 0))
    if notional <= 0:
        return 0.0
    v = str(venue or "").lower()
    try:
        from fee_engine import STATIC_FEES
        if "coinbase" in v:
            f = STATIC_FEES["coinbase"]
            return round(notional * (float(f.get("taker", 0.006)) + float(f.get("spread", 0))), 6)
        if "alpaca" in v:
            return float(STATIC_FEES["alpaca"].get("commission_stocks", 0) or 0)
    except Exception:
        if "coinbase" in v:
            return round(notional * 0.006, 6)
    return 0.0


def _first_float(blob, keys, allow_zero=False):
    for k in keys:
        if blob.get(k) in (None, "", "?"):
            continue
        try:
            val = float(blob[k])
        except (TypeError, ValueError):
            continue
        if val > 0 or (allow_zero and val >= 0):
            return val
    return None


def extract_spot_fill(venue, raw):
    """Parse a Coinbase/Alpaca order body into fill fields. Quote/notional is NOT a fill."""
    raw = raw if isinstance(raw, dict) else {}
    sr = raw.get("success_response")
    blob = {**raw, **sr} if isinstance(sr, dict) else dict(raw)
    qty = _first_float(blob, ("filled_size", "filled_qty", "filled_contracts"))
    price = _first_float(blob, ("average_filled_price", "filled_avg_price", "avg_fill_price"))
    fee = _first_float(blob, ("total_fees", "commission", "filled_fees"), allow_zero=True)
    if (qty is None or price is None) and (
            blob.get("fill_status") == "FILLED" or blob.get("filled") is True):
        if qty is None:
            qty = _first_float(blob, ("qty", "contracts"))
        if price is None:
            price = _first_float(blob, ("price", "entry_price"))
    return {
        "complete": bool(price and price > 0 and qty and qty > 0),
        "price": price or 0.0, "qty": qty or 0.0, "fee": fee,
        "order_id": blob.get("order_id") or blob.get("id"),
    }


def spot_ack_ok(venue, resp):
    """True if the venue accepted the order. False = do not mark EXECUTED."""
    if not isinstance(resp, dict):
        return False
    if resp.get("success") is False or resp.get("error"):
        return False
    st = str(resp.get("status") or "").lower()
    if st in ("rejected", "canceled", "cancelled", "expired"):
        return False
    v = str(venue or "").lower()
    if "alpaca" in v and not (resp.get("id") or resp.get("order_id")):
        if resp.get("message") or resp.get("code"):
            return False
    return True


def book_spot_fill(strategy, ticker, side, venue, raw, intended_source="live", ts=None,
                   predicted_direction=None, predicted_prob=None):
    """Fee-true record_entry when fill qty+price exist; else UNRECONCILED (never silent live).

    paper/sandbox/unreconciled intended_source is never upgraded to live.
    """
    fill = extract_spot_fill(venue, raw)
    src_in = str(intended_source or "live")
    if not fill["complete"]:
        return record_unreconciled(
            strategy, ticker, side, fill["price"], fill["qty"], venue,
            predicted_direction=predicted_direction, predicted_prob=predicted_prob,
            ts=ts, order_id=fill.get("order_id"), intended_source=src_in,
        )
    fee = fill["fee"] if fill["fee"] is not None else _spot_fee(venue, fill["price"], fill["qty"])
    source = src_in if _is_non_real_source(src_in) else "live"
    return record_entry(
        strategy, ticker, side, fill["price"], fill["qty"], venue=venue,
        predicted_direction=predicted_direction, predicted_prob=predicted_prob,
        source=source, ts=ts, fill_status="FILLED", entry_fee=fee,
        order_id=fill.get("order_id"),
    )


def settle_spot_fill(ticker, venue, raw, intended_source="live", fallback_price=None, fee=None):
    """Settle a spot round-trip. Incomplete fill cannot book live P&L (unreconciled or no-op)."""
    fill = extract_spot_fill(venue, raw)
    if fill["complete"]:
        price = fill["price"]
        if fee is None:
            fee = fill["fee"] if fill["fee"] is not None else _spot_fee(venue, fill["price"], fill["qty"])
        src = intended_source
    else:
        if fallback_price is None or float(fallback_price) <= 0:
            return None
        price = float(fallback_price)
        fee = 0.0
        src = "unreconciled"
    if _is_non_real_source(intended_source):
        src = intended_source
    return settle_open(ticker, venue, price, fee=fee, source=src)


# ── record an entry (called by executors at order time) ─────────────────────
def record_entry(strategy, ticker, side, price, contracts, venue="kalshi",
                 predicted_direction=None, predicted_prob=None, source="live", ts=None,
                 fill_status=None, entry_fee=None, order_id=None, intended_source=None):
    eid = "ft_" + uuid.uuid4().hex[:12]
    v = str(venue or "")
    if entry_fee is None:
        if v.startswith("kalshi"):
            fee = kalshi_fee(price, contracts)
        else:
            fee = _spot_fee(v, price, contracts)
    else:
        fee = float(entry_fee)
    rec = {"type": "entry", "id": eid, "ts": ts or datetime.now(timezone.utc).isoformat(),
           "strategy": strategy, "venue": venue, "ticker": ticker, "side": side,
           "entry_price": round(float(price), 4), "contracts": float(contracts),
           "entry_fee": fee,
           "predicted_direction": predicted_direction, "predicted_prob": predicted_prob,
           "source": source}
    if fill_status:
        rec["fill_status"] = fill_status
    if order_id:
        rec["order_id"] = order_id
    if intended_source:
        rec["intended_source"] = intended_source
    _append(rec)
    return eid


def record_unreconciled(strategy, ticker, side, price, contracts, venue,
                        predicted_direction=None, predicted_prob=None, ts=None,
                        order_id=None, intended_source=None):
    """POST-ack with no venue fill yet (spot or Kalshi taker). Excluded from Kelly."""
    return record_entry(
        strategy, ticker, side, price, contracts, venue=venue,
        predicted_direction=predicted_direction, predicted_prob=predicted_prob,
        source="unreconciled", ts=ts, fill_status="UNRECONCILED",
        entry_fee=0.0, order_id=order_id, intended_source=intended_source,
    )


def _kalshi_px(blob):
    """Kalshi yes/no price: cents (1-99) or already-dollar (0-1)."""
    for k in ("yes_price", "no_price", "price", "avg_fill_price", "average_filled_price"):
        if blob.get(k) in (None, "", "?"):
            continue
        try:
            v = float(blob[k])
        except (TypeError, ValueError):
            continue
        if v > 1.0:
            v = v / 100.0
        if v > 0:
            return v
    return None


def extract_kalshi_fill(raw, fills_payload=None):
    """Parse a Kalshi create_order / get_fills blob. POST-ack is NOT a fill.

    Complete only when fill count > 0 (order.fill_count, remaining_count==0 on
    an executed order with count, or get_fills rows for this order_id).
    Mirrors MakerOrderManager._fills_by_order — record on fill, never on place.
    """
    raw = raw if isinstance(raw, dict) else {}
    nested = raw.get("order") if isinstance(raw.get("order"), dict) else {}
    blob = {**raw, **nested}
    order_id = blob.get("order_id") or blob.get("id")
    status = str(blob.get("status") or "").lower()
    qty_explicit = _first_float(blob, ("fill_count", "filled_count", "filled_contracts"),
                                allow_zero=True)
    remaining = _first_float(blob, ("remaining_count", "remaining"), allow_zero=True)
    count = _first_float(blob, ("count", "contracts", "count_fp"))
    if qty_explicit is not None:
        qty = qty_explicit  # 0 = resting/unfilled ack; do not fall through to requested count
    else:
        qty = None
        if status in ("executed", "filled") and remaining == 0 and count:
            qty = count
        elif blob.get("filled") is True and count:
            qty = count
    fills = None
    if isinstance(fills_payload, dict):
        fills = fills_payload.get("fills")
    if fills is None:
        fills = raw.get("fills")
    if isinstance(fills, list) and fills:
        matched = [f for f in fills if not order_id or f.get("order_id") == order_id]
        total = sum(float(f.get("count", 0) or f.get("count_fp", 0) or 0) for f in matched)
        if total > 0:
            qty = total
            if matched:
                blob = {**blob, **matched[-1]}
    price = _kalshi_px(blob) or 0.0
    return {
        "complete": bool(qty and qty > 0),
        "qty": qty or 0.0,
        "price": price,
        "order_id": order_id,
        "status": status,
    }


def resolve_kalshi_fill(raw, get_fills_fn=None):
    """POST-ack first; GET fills only if ack is incomplete and order_id is known.

    Attributed rewrite of kalshi-agent MarketMaker.check_fills (remaining_count
    / get_order). Their API has no get_order; Valedia polls kalshi_auth.get_fills.
    POST success is never a fill. No order_id → do not poll (would steal others).
    """
    fill = extract_kalshi_fill(raw)
    payload = None
    if fill["complete"] or get_fills_fn is None or not fill.get("order_id"):
        return fill, payload
    try:
        payload = get_fills_fn()
    except Exception:
        payload = None
    if payload:
        fill = extract_kalshi_fill(raw, payload)
    return fill, payload


def book_kalshi_fill(strategy, ticker, side, raw, intended_source="live",
                     fallback_price=None, fallback_contracts=None, ts=None,
                     fills_payload=None, predicted_direction=None, predicted_prob=None):
    """Fee-true record_entry when Kalshi fill count > 0; else UNRECONCILED.

    paper/sandbox/unreconciled/backfill intended_source is never upgraded to live.
    """
    fill = extract_kalshi_fill(raw, fills_payload)
    src_in = str(intended_source or "live")
    price = fill["price"] if fill["price"] > 0 else float(fallback_price or 0)
    qty = fill["qty"] if fill["complete"] else float(fallback_contracts or fill["qty"] or 0)
    strat = canonical_category(strategy)
    if not fill["complete"]:
        return record_unreconciled(
            strat, ticker, side, price, qty, "kalshi",
            predicted_direction=predicted_direction, predicted_prob=predicted_prob,
            ts=ts, order_id=fill.get("order_id"), intended_source=src_in,
        )
    source = src_in if _is_non_real_source(src_in) else "live"
    return record_entry(
        strat, ticker, side, price, fill["qty"], venue="kalshi",
        predicted_direction=predicted_direction, predicted_prob=predicted_prob,
        source=source, ts=ts, fill_status="FILLED",
        entry_fee=kalshi_fee(price, fill["qty"]),
        order_id=fill.get("order_id"),
    )


def _settled_ids():
    return {r["entry_id"] for r in _read() if r.get("type") == "settlement"}


def open_position_count():
    """Count REAL (non-paper) OPEN entries — live simultaneous bets — for Thorp concurrent
    sizing (kelly_sizing). Defensive: 0 on any error (-> no concurrent reduction)."""
    try:
        done = _settled_ids()
        return sum(1 for e in _read() if e.get("type") == "entry" and e["id"] not in done
                   and not _is_non_real_source(e.get("source", "")))
    except Exception:
        return 0


# ── reconcile open entries against real fills + settlement ──────────────────
def reconcile():
    entries = [r for r in _read() if r.get("type") == "entry"]
    done = _settled_ids()
    open_entries = [e for e in entries if e["id"] not in done]
    if not open_entries:
        print("nothing to reconcile"); return 0
    try:
        import kalshi_auth as K
    except Exception as e:
        print(f"kalshi_auth unavailable ({e}); cannot reconcile live"); return 1
    n = 0
    for e in open_entries:
        if not e["ticker"]:
            continue
        try:
            m = K.get(f"/markets/{e['ticker']}").get("market", {})
        except Exception as ex:
            print(f"  {e['ticker']}: market fetch failed ({str(ex)[:60]})"); continue
        result = m.get("result")
        if result not in ("yes", "no"):
            continue  # not settled yet
        # Real fill reconciliation (partial fills happen; do not assume full).
        filled = e["contracts"]; fill_note = "assumed_full"
        try:
            fills = K.get(f"/portfolio/fills?ticker={e['ticker']}").get("fills", [])
            if fills:
                filled = sum(float(f.get("count", 0)) for f in fills)
                fill_note = "reconciled"
        except Exception:
            pass
        won = (e["side"] == result)
        payout = filled * 1.0 if won else 0.0
        cost = filled * e["entry_price"]
        fee = kalshi_fee(e["entry_price"], filled)
        realized_net = round(payout - cost - fee, 4)
        hit = None
        if e.get("predicted_direction"):
            hit = (str(e["predicted_direction"]).lower() == result)
        _append({"type": "settlement", "entry_id": e["id"], "ticker": e["ticker"],
                 "strategy": e["strategy"], "result": result,
                 "filled_contracts": filled, "fill_note": fill_note,
                 "realized_pnl_net": realized_net, "fee": fee,
                 "won": won, "hit": hit, "predicted_prob": e.get("predicted_prob")})
        n += 1
        print(f"  settled {e['ticker']} [{e['strategy']}] result={result} net=${realized_net:+.2f} ({fill_note})")
    print(f"reconciled {n} newly-settled entries")
    return 0


# ── summary: fee-inclusive net + hit rate + Brier, per strategy ─────────────
def summary():
    recs = _read()
    setts = [r for r in recs if r.get("type") == "settlement"]
    entries = {r["id"]: r for r in recs if r.get("type") == "entry"}
    if not setts:
        print("no settled trades yet — ledger is the labeled dataset in progress "
              f"({len(entries)} open entries)"); return 0
    from collections import defaultdict
    agg = defaultdict(lambda: {"n": 0, "wins": 0, "net": 0.0, "hits": 0, "hit_n": 0,
                               "brier_sum": 0.0, "brier_n": 0})
    for s in setts:
        a = agg[s.get("strategy", "?")]
        a["n"] += 1
        a["wins"] += 1 if s.get("won") else 0
        a["net"] += s.get("realized_pnl_net", 0.0)
        if s.get("hit") is not None:
            a["hit_n"] += 1; a["hits"] += 1 if s["hit"] else 0
        pp = s.get("predicted_prob")
        if pp is not None:
            outcome = 1.0 if s.get("result") == "yes" else 0.0
            a["brier_sum"] += (float(pp) - outcome) ** 2; a["brier_n"] += 1
    print(f"{'strategy':16}{'n':>4}{'win%':>7}{'hit%':>7}{'net($)':>10}{'Brier':>8}")
    tot = {"n": 0, "net": 0.0}
    for st, a in sorted(agg.items(), key=lambda x: -x[1]["net"]):
        winp = 100.0 * a["wins"] / a["n"]
        hitp = (100.0 * a["hits"] / a["hit_n"]) if a["hit_n"] else float("nan")
        brier = (a["brier_sum"] / a["brier_n"]) if a["brier_n"] else float("nan")
        print(f"{st:16}{a['n']:>4}{winp:>7.1f}{hitp:>7.1f}{a['net']:>10.2f}{brier:>8.3f}")
        tot["n"] += a["n"]; tot["net"] += a["net"]
    print(f"{'TOTAL':16}{tot['n']:>4}{'':>7}{'':>7}{tot['net']:>10.2f}")
    print(f"\nEDGE GATE: deploy capital to a strategy only when its fee-inclusive net is")
    print(f"positive across MANY independent events here. Current ledger says otherwise.")
    return 0


# ── backfill the 38 known pnl_tracker trades as the seed labeled dataset ─────
def backfill():
    src = os.path.join(MODELS, "pnl_tracker.json")
    trades = json.load(open(src)).get("trades", [])
    existing = {r.get("ticker") + str(r.get("entry_price")) for r in _read() if r.get("type") == "entry"}
    added = 0
    for i, t in enumerate(trades):
        tk = t.get("ticker", "")
        cost = float(t.get("cost", 0) or 0)
        side = t.get("side", "yes")
        key = tk + str(round(cost, 4)) + str(i)  # allow duplicate tickers
        if key in existing:
            continue
        # Use pnl_tracker's RECORDED cost/profit directly (do not reconstruct price×contracts —
        # contracts is unreliable). Profit's fee-inclusiveness is [UNVERIFIED] per Scout, so flag it.
        eid = record_entry(t.get("strategy", "?"), tk, side, 0.0, 0.0,
                           source="backfill:pnl_tracker", ts=t.get("ts") or "2026-03-23T00:00:00Z")
        status = str(t.get("status", "")).upper()
        result = side if status == "WON" else (("no" if side == "yes" else "yes") if status == "LOST" else None)
        if result:
            _append({"type": "settlement", "entry_id": eid, "ticker": tk,
                     "strategy": t.get("strategy", "?"), "result": result,
                     "filled_contracts": None, "fill_note": "backfill_recorded_profit",
                     "realized_pnl_net": round(float(t.get("profit", 0) or 0), 4),
                     "fee": None, "fee_status": "unverified_source", "cost": cost,
                     "won": status == "WON", "hit": None, "predicted_prob": None,
                     "source": "backfill:pnl_tracker"})
        added += 1
    print(f"backfilled {added} trades (recorded profit; fee-status unverified — forward "
          f"trades use the canonical fee via reconcile())")
    return 0


# ── crypto/equities round-trip settlement (the ledger was kalshi-only) ──────
def settle_open(ticker, venue, exit_price, fee=0.0, source="live"):
    """Settle the most recent OPEN entry for ticker+venue at exit_price (a crypto/equities
    round-trip; kalshi uses reconcile() instead). Realized = (exit-entry)*contracts - fees.
    No-op returning None if there's no matching open entry. Defensive by design — the caller
    is a live trade path and must never break because settlement bookkeeping failed."""
    done = _settled_ids()
    opens = [e for e in _read() if e.get("type") == "entry" and e["id"] not in done
             and e.get("ticker") == ticker and e.get("venue") == venue]
    if not opens:
        return None
    e = opens[-1]
    if _is_non_real_source(e.get("source")):
        source = e["source"]  # never let a live settlement launder paper/sandbox/unreconciled
    c = float(e.get("contracts", 0) or 0)
    realized = round((float(exit_price) - float(e["entry_price"])) * c
                     - float(e.get("entry_fee", 0) or 0) - float(fee or 0), 4)
    _append({"type": "settlement", "entry_id": e["id"], "ticker": ticker,
             "strategy": e.get("strategy", "?"), "result": "exit",
             "filled_contracts": c, "fill_note": "roundtrip_exit",
             "realized_pnl_net": realized, "won": realized > 0, "hit": None,
             "source": source})
    return realized


# ── the ONE real track-record the sizing layer reads (cap + confidence) ─────
def real_track_record():
    """Aggregate REAL (non-paper) settled edge — the single source both the Kelly cap
    (valedia_config._track_record_mult) and the confidence multiplier (unified_brain
    .get_confidence_multiplier) read, so sizing breathes on real fee-inclusive outcomes
    instead of paper marks. Paper/sandbox/unreconciled/backfill trades (source
    contains those tokens, including nautilus_sandbox and backfill:pnl_tracker)
    are excluded if the settlement OR its entry is non-real (no live-source
    laundering). Returns {"n", "wins", "hit_rate", "recent_20", "net"}; empty -> 50/50."""
    recs = _read()
    entries = {r["id"]: r for r in recs if r.get("type") == "entry"}
    setts = []
    for r in recs:
        if r.get("type") != "settlement":
            continue
        entry = entries.get(r.get("entry_id"), {})
        if _is_non_real_source(r.get("source")) or _is_non_real_source(entry.get("source")):
            continue
        setts.append(r)
    n = len(setts)
    if not n:
        return {"n": 0, "wins": 0, "hit_rate": 50.0, "recent_20": 50.0, "net": 0.0}
    wins = sum(1 for s in setts if s.get("won"))
    r20  = setts[-20:]
    w20  = sum(1 for s in r20 if s.get("won"))
    return {"n": n, "wins": wins,
            "hit_rate":  round(wins / n * 100, 1),
            "recent_20": round(w20 / len(r20) * 100, 1),
            "net": round(sum(float(s.get("realized_pnl_net", 0) or 0) for s in setts), 2)}


# ── category calibration gate: trade a category only while it isn't a VERIFIED recent loser ──
def _overrides():
    """Manual controls (Burton): {"parked":[cats off now],"retest":[cats force-allowed]}.
    Missing/broken file -> {} (no overrides). retest wins over parked."""
    try:
        return json.load(open(OVERRIDES))
    except Exception:
        return {}


def category_stats(lookback_days=None):
    """Per-category REAL settled stats within a recency window. Counts ONLY type=='settlement'
    records with a real result — never failed/errored/pending trades (those have no settlement)
    — and excludes paper/sandbox/unreconciled/backfill. Alias labels (SPORTS_TONIGHT)
    fold into the canonical bucket (SPORTS) so two crons cannot split the gate.
    Recency is by the TRADE's time (entry ts), so stale real losses age out.
    Returns {cat:{"n","wins","net"}}."""
    from datetime import datetime, timezone, timedelta
    try:
        import valedia_config as _c
        days = lookback_days if lookback_days is not None else _c.CATEGORY_LOOKBACK_DAYS
    except Exception:
        days = lookback_days or 45
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    recs = _read()
    entries = {r["id"]: r for r in recs if r.get("type") == "entry"}
    out = {}
    for r in recs:
        if r.get("type") != "settlement":
            continue
        entry = entries.get(r.get("entry_id"), {})
        if _is_non_real_source(r.get("source")) or _is_non_real_source(entry.get("source")):
            continue
        when = entry.get("ts", "") or r.get("logged_at", "")
        if when and when < cutoff:
            continue  # aged out — self-healing, never permanent
        cat = canonical_category(r.get("strategy") or entry.get("strategy") or "?")
        d = out.setdefault(cat, {"n": 0, "wins": 0, "net": 0.0})
        d["n"] += 1
        d["wins"] += 1 if r.get("won") else 0
        d["net"] += float(r.get("realized_pnl_net", 0) or 0)
    for d in out.values():
        d["net"] = round(d["net"], 2)
    return out


def _learning_pause_until(category, now=None):
    """Learning pause expiry (pnl_tracker). Fail-open: errors → not paused.
    Pause is TTL-bounded; it is never a permanent kill."""
    try:
        import pnl_tracker as _pt
        return _pt.paused_until(category, now=now)
    except Exception:
        return None


def should_trade_category(category, lookback_days=None):
    """(allowed, reason) for a strategy/category. FAIL-OPEN: any uncertainty or error -> allow.
    Blocked by (a) a manual 'parked' entry, (b) an unexpired learning pause
    ('paused until …', never 'dead forever'), or (c) a VERIFIED recent record — at least
    CATEGORY_MIN_TRADES real settled trades in-window AND a negative fee-inclusive net (real
    money lost). Gates on NET, not win-rate, so a fat-tailed low-win winner isn't killed.
    Errors, failed/pending trades, paper, and thin/old samples NEVER block; a 'retest' override
    force-allows; pause TTL and recent losses age out -> nothing is permanent."""
    cat = canonical_category(category)
    try:
        ov = _overrides()
        retest = set()
        parked = set()
        for x in ov.get("retest", []):
            retest |= _category_keys(x)
        for x in ov.get("parked", []):
            parked |= _category_keys(x)
        if category in retest or cat in retest:
            return (True, "manual retest override")
        if category in parked or cat in parked:
            return (False, "manually parked (add to 'retest' to re-enable)")
    except Exception:
        pass  # override read failed -> never block on our own error
    try:
        until = _learning_pause_until(cat)
        if until is not None:
            iso = until.isoformat()
            return (False, f"paused until {iso} (learning TTL; not permanent)")
    except Exception:
        pass  # pause read failed -> never block on our own error
    try:
        import valedia_config as _c
        min_n, block_net = _c.CATEGORY_MIN_TRADES, _c.CATEGORY_BLOCK_NET
    except Exception:
        min_n, block_net = 4, 0.0
    try:
        s = category_stats(lookback_days).get(cat)
        if not s or s["n"] < min_n:
            return (True, f"unproven/thin ({0 if not s else s['n']} recent settled) — not blocked")
        if s["net"] < block_net:
            return (False, f"VERIFIED recent loss {s['wins']}/{s['n']} win, net ${s['net']:.2f} — pause/retest ages out")
        return (True, f"ok {s['wins']}/{s['n']} win, net ${s['net']:+.2f}")
    except Exception as e:
        return (True, f"gate error -> allow (fail-open): {str(e)[:40]}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(prog="forward_test_ledger")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("record")
    for k in ("strategy", "ticker", "side"):
        e.add_argument("--" + k, required=True)
    e.add_argument("--price", type=float, required=True); e.add_argument("--contracts", type=float, required=True)
    e.add_argument("--dir", default=None); e.add_argument("--prob", type=float, default=None)
    sub.add_parser("reconcile"); sub.add_parser("summary"); sub.add_parser("backfill")
    a = ap.parse_args()
    if a.cmd == "record":
        print(record_entry(a.strategy, a.ticker, a.side, a.price, a.contracts,
                           predicted_direction=a.dir, predicted_prob=a.prob))
    elif a.cmd == "reconcile": sys.exit(reconcile())
    elif a.cmd == "summary": sys.exit(summary())
    elif a.cmd == "backfill": sys.exit(backfill())
