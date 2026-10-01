"""spot_reconcile.py — Slice 4: upgrade UNRECONCILED rows from fill/GET payloads.

Given a ledger entry marked UNRECONCILED with a venue order_id, and either an
explicit fill payload or a mocked GET-order body, book a fee-true record_entry
(buy) or settle (sell) when qty+price exist. A sell FIFO-closes every open lot
opened at/before it for the filled quantity (spot_ledger.close_sell); a partly
sold lot re-opens its remainder; reruns never close a lot twice.

Never invents fills. Missing/partial/empty data stays UNRECONCILED.
Never live HTTP — pass fill_payload, get_response, or an injected getter.
Cron getter is mock unless SPOT_RECONCILE_HTTP is on (Slice 14).
Paper/sandbox intended_source is never upgraded to live.

No nautilus_trader. No --live. No keys.
(c) GOATWORTHY Post LLC.
"""
from __future__ import annotations

from typing import Any, Callable, Optional

import forward_test_ledger as ftl
import spot_ledger as sl  # close_sell: the one FIFO sell-settlement implementation

# Documented venue GET paths (not fetched by this module).
COINBASE_ORDER_GET = "/api/v3/brokerage/orders/historical/{order_id}"
ALPACA_ORDER_GET = "/v2/orders/{order_id}"


def coinbase_order_get_path(order_id: str) -> str:
    return COINBASE_ORDER_GET.format(order_id=order_id)


def alpaca_order_get_path(order_id: str) -> str:
    return ALPACA_ORDER_GET.format(order_id=order_id)


def is_unreconciled_entry(row: dict) -> bool:
    if not isinstance(row, dict) or row.get("type") != "entry":
        return False
    if str(row.get("fill_status") or "").upper() == "UNRECONCILED":
        return True
    return "unreconciled" in str(row.get("source") or "").lower()


def unwrap_order_get(get_response) -> dict:
    """Normalize Coinbase `{order: {...}}` or Alpaca order JSON to a flat blob."""
    raw = get_response if isinstance(get_response, dict) else {}
    if isinstance(raw.get("order"), dict):
        return {**raw, **raw["order"]}
    return dict(raw)


def fill_from_payload(venue, fill_payload=None, get_response=None) -> dict:
    """Parse an explicit fill or a mocked GET body. Empty/missing → incomplete."""
    if fill_payload is not None:
        blob = unwrap_order_get(fill_payload)
    elif get_response is not None:
        blob = unwrap_order_get(get_response)
    else:
        return {"complete": False, "price": 0.0, "qty": 0.0, "fee": None, "order_id": None}
    return ftl.extract_spot_fill(venue, blob)


def list_unreconciled(rows=None) -> list:
    recs = rows if rows is not None else ftl._read()
    done = {r.get("entry_id") for r in recs if r.get("type") == "settlement"}
    return [r for r in recs if is_unreconciled_entry(r) and r.get("id") not in done]


def _upgrade_source(entry: dict) -> str:
    """GET-reconcile may book live only if the original intent was live (or unset)."""
    intended = entry.get("intended_source")
    if intended and ftl._is_non_real_source(intended):
        return str(intended)
    return "live"


def _already_filled(order_id, rows) -> Optional[str]:
    if not order_id:
        return None
    for r in rows:
        if (r.get("type") == "entry" and r.get("order_id") == order_id
                and str(r.get("fill_status") or "").upper() == "FILLED"):
            return r.get("id")
    return None


def _close_stub(entry: dict, upgraded_id=None):
    """Append-only close of the UNRECONCILED stub. Source stays unreconciled (no Kelly)."""
    if entry.get("id") in ftl._settled_ids():
        return
    note = f"upgraded_to:{upgraded_id}" if upgraded_id else "reconciled_stub_close"
    ftl._append({
        "type": "settlement",
        "entry_id": entry["id"],
        "ticker": entry.get("ticker"),
        "strategy": entry.get("strategy", "?"),
        "result": "reconcile_close",
        "filled_contracts": 0,
        "fill_note": note,
        "realized_pnl_net": 0.0,
        "won": False,
        "hit": None,
        "source": "unreconciled",
        "order_id": entry.get("order_id"),
    })


def reconcile_unreconciled_entry(entry, fill_payload=None, get_response=None,
                                 getter: Optional[Callable[..., Any]] = None) -> dict:
    """Upgrade one UNRECONCILED row. Never invents fills. Never HTTP unless getter given."""
    if not is_unreconciled_entry(entry or {}):
        return {"upgraded": False, "reason": "not_unreconciled", "id": (entry or {}).get("id")}
    oid = entry.get("order_id")
    if not oid:
        return {"upgraded": False, "reason": "no_order_id", "id": entry.get("id")}

    rows = ftl._read()
    existing = _already_filled(oid, rows)
    if existing:
        return {"upgraded": False, "reason": "already_filled", "id": entry.get("id"),
                "existing_id": existing}
    if str(entry.get("side") or "").lower() == "sell" and (
            entry.get("id") in ftl._settled_ids() or sl.sell_already_settled(oid, rows)):
        _close_stub(entry)  # no-op if already closed; never closes another lot
        return {"upgraded": False, "reason": "already_settled", "id": entry.get("id"),
                "order_id": oid}

    payload, gresp = fill_payload, get_response
    if payload is None and gresp is None and getter is not None:
        try:
            gresp = getter(oid, entry.get("venue"))
        except Exception as e:
            return {"upgraded": False, "reason": f"getter_error:{str(e)[:80]}",
                    "id": entry.get("id"), "order_id": oid}

    fill = fill_from_payload(entry.get("venue"), fill_payload=payload, get_response=gresp)
    fill_oid = fill.get("order_id")
    if fill_oid and str(fill_oid) != str(oid):
        return {"upgraded": False, "reason": "order_id_mismatch", "id": entry.get("id"),
                "order_id": oid, "payload_order_id": fill_oid}
    if not fill.get("complete"):
        return {"upgraded": False, "reason": "incomplete_fill", "id": entry.get("id"),
                "order_id": oid}

    source = _upgrade_source(entry)
    venue = entry.get("venue")
    side = str(entry.get("side") or "buy").lower()
    blob = unwrap_order_get(payload if payload is not None else gresp) or {}
    if "order_id" not in blob and "id" not in blob:
        blob = {**blob, "order_id": oid}

    if side == "sell":
        # FIFO over every open lot opened at/before this sell, for the FILLED quantity
        # (spot_ledger.close_sell - the one implementation book_sell also uses). Settle
        # first, then close the stub: a rerun finds the order's settlements and stops.
        res = sl.close_sell(entry.get("ticker"), venue, fill,
                            exit_strategy=entry.get("strategy"), order_id=oid,
                            source=source, before_ts=entry.get("ts"))
        _close_stub(entry)
        return {"upgraded": True, "action": "settle", "id": entry.get("id"),
                "order_id": oid, "source": source, "realized": res["realized"],
                "closed": [x["entry_id"] for x in res["settlements"]],
                "excess": res.get("excess", 0.0), "excess_id": res.get("excess_id")}

    eid = ftl.book_spot_fill(
        entry.get("strategy") or "SPOT", entry.get("ticker"), side, venue, blob,
        intended_source=source, ts=entry.get("ts"),  # lot keeps its order time (FIFO)
        predicted_direction=entry.get("predicted_direction"),
        predicted_prob=entry.get("predicted_prob"))
    new_rows = [r for r in ftl._read() if r.get("id") == eid]
    booked_src = new_rows[0].get("source") if new_rows else source
    if booked_src == "unreconciled":
        return {"upgraded": False, "reason": "incomplete_fill", "id": entry.get("id"),
                "order_id": oid}
    _close_stub(entry, eid)
    return {"upgraded": True, "action": "record_entry", "id": eid,
            "from_id": entry.get("id"), "order_id": oid, "source": booked_src}


def reconcile_unreconciled(fill_payloads=None, get_responses=None,
                           getter: Optional[Callable[..., Any]] = None,
                           rows=None) -> dict:
    """Walk UNRECONCILED entries. Lookup by order_id. Default getter is None (no HTTP)."""
    fill_payloads = fill_payloads or {}
    get_responses = get_responses or {}
    pending = list_unreconciled(rows)
    out = []
    for e in pending:
        oid = e.get("order_id")
        rec = reconcile_unreconciled_entry(
            e,
            fill_payload=fill_payloads.get(oid) if oid else None,
            get_response=get_responses.get(oid) if oid else None,
            getter=getter if (oid and oid not in fill_payloads and oid not in get_responses) else None,
        )
        out.append(rec)
    n_up = sum(1 for r in out if r.get("upgraded"))
    return {"n": len(out), "upgraded": n_up, "results": out}
