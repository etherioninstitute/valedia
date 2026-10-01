"""QD-parity virtual costs for a labeled paper compare.

Spirit of OpenByteInc/QuantDinger @ 9e3095d (Apache-2.0)
`calculate_virtual_fill` + commission on qty*fill. Not a copy of the
database executor. Upstream order adapters stay unread. Default Valedia
book stays CRYPTO_FEE_PCT unless opt_in labels this row quantdinger_virtual.
(c) GOATWORTHY Post LLC.
"""
from __future__ import annotations

import json
import os

import valedia_config as vc
from crypto_paper import fee_usd, fill_px

COMMISSION_RATE = 0.0005
SLIPPAGE_RATE = 0.0005
LIQUIDITY_ROLE = "taker"
SOURCE = "quantdinger_paper"
FEE_TABLE = "quantdinger_virtual"
DEFAULT_BOOK = "CRYPTO_FEE_PCT"
MODE = "PAPER/SHADOW"
FIXTURE_TS = "2026-09-30T00:20:00+00:00"

_ENTRY = frozenset({"open_long", "add_long", "open_short", "add_short"})
_EXIT = frozenset({"reduce_long", "close_long", "reduce_short", "close_short"})
_BUY = frozenset({"open_long", "add_long", "reduce_short", "close_short"})

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
DEFAULT_FIXTURE = os.path.join(_ROOT, "data", "quantdinger-paper", "decision.json")

# One flat long round-trip, then one flat short round-trip. Offline only.
SCRIPT = (
    ("open_long", 2.0, 100.0, 5.0),
    ("add_long", 2.0, 110.0, 5.0),
    ("reduce_long", 1.0, 120.0, 1.0),
    ("close_long", 0.0, 90.0, 1.0),
    ("open_short", 1.0, 50.0, 10.0),
    ("add_short", 1.0, 40.0, 1.0),
    ("reduce_short", 1.0, 45.0, 1.0),
    ("close_short", 0.0, 30.0, 1.0),
)


def _action(action):
    return str(action or "").strip().lower()


def fill_price(action, reference, slippage_rate=SLIPPAGE_RATE):
    side = "buy" if _action(action) in _BUY else "sell"
    return max(0.0, fill_px(reference, side, slippage_rate))


def commission_quote(quantity, price, rate=COMMISSION_RATE, leverage=1.0):
    # ponytail: leverage is a label. Fee base is qty*px. Multiply only if a margin schedule charges borrowed notional.
    _ = leverage
    qty = max(0.0, float(quantity or 0.0))
    px = max(0.0, float(price or 0.0))
    return fee_usd(qty * px, LIQUIDITY_ROLE, rate, rate)


def slippage_quote(quantity, reference, price):
    qty = max(0.0, float(quantity or 0.0))
    return qty * abs(float(price or 0.0) - float(reference or 0.0))


def calculate_virtual_fill(action, requested_quantity, price,
                           current_size=0.0, current_entry_price=0.0):
    """Open/add/reduce/close long and short. No broker, no cash account."""
    normalized = _action(action)
    requested = max(0.0, float(requested_quantity or 0.0))
    px = max(0.0, float(price or 0.0))
    size = max(0.0, float(current_size or 0.0))
    entry = max(0.0, float(current_entry_price or 0.0))
    if normalized in _ENTRY:
        if requested <= 0 or px <= 0:
            return _tr(0.0, size, entry, 0.0, "rejected")
        nxt = size + requested
        avg = ((size * entry) + (requested * px)) / nxt
        return _tr(requested, nxt, avg, 0.0, "filled")
    if normalized in _EXIT:
        if size <= 0 or px <= 0:
            return _tr(0.0, size, entry, 0.0, "no_position")
        qty = size if requested <= 0 else min(size, requested)
        gross = (entry - px) * qty if normalized.endswith("_short") else (px - entry) * qty
        return _tr(qty, max(0.0, size - qty), entry, gross, "filled")
    return _tr(0.0, size, entry, 0.0, "rejected")


def _tr(fill_quantity, next_size, next_entry, gross, status):
    return {
        "fill_quantity": fill_quantity,
        "next_size": next_size,
        "next_entry_price": next_entry,
        "gross_realized_pnl": gross,
        "status": status,
    }


def cost_compare(notional):
    n = abs(float(notional or 0.0))
    return {
        "notional": n,
        "quantdinger_commission": n * COMMISSION_RATE,
        "crypto_paper_fee": n * float(vc.CRYPTO_FEE_PCT),
        "quantdinger_slip_rate": SLIPPAGE_RATE,
        "crypto_paper_slip": float(vc.CRYPTO_SLIPPAGE_PCT),
        "default_book": DEFAULT_BOOK,
    }


def _r(x):
    return round(float(x), 8)


def walk(script=SCRIPT):
    size = entry = 0.0
    out = []
    for action, req, ref, lev in script:
        px = _r(fill_price(action, ref))
        tr = calculate_virtual_fill(action, req, px, size, entry)
        comm = _r(commission_quote(tr["fill_quantity"], px, leverage=lev))
        size = tr["next_size"]
        entry = tr["next_entry_price"] if size > 1e-12 else 0.0
        gross = _r(tr["gross_realized_pnl"])
        row = {
            "action": action,
            "requested_quantity": req,
            "reference_price": ref,
            "leverage": lev,
            "fill_price": px,
            "fill_quantity": _r(tr["fill_quantity"]),
            "next_size": _r(tr["next_size"]),
            "next_entry_price": _r(tr["next_entry_price"]),
            "gross_realized_pnl": gross,
            "commission": comm,
            "slippage_quote": _r(slippage_quote(tr["fill_quantity"], ref, px)),
            "net_realized": _r(gross - comm),
            "status": tr["status"],
        }
        out.append(row)
    return out


def paper_decision(opt_in=False, ts=None):
    """QD-tagged paper row. create_order and Kelly stay false."""
    return {
        "type": "decision",
        "source": SOURCE,
        "strategy": "QUANTDINGER_PAPER",
        "venue": "paper",
        "ticker": "BTC-USDT",
        "create_order": False,
        "live": False,
        "counts_toward_kelly": False,
        "paperTrading": True,
        "opt_in": bool(opt_in),
        "fee_table": FEE_TABLE if opt_in else DEFAULT_BOOK,
        "default_book": DEFAULT_BOOK,
        "liquidity_role": LIQUIDITY_ROLE,
        "commission_rate": COMMISSION_RATE,
        "slippage_rate": SLIPPAGE_RATE,
        "leverage_in_commission": False,
        "compare": cost_compare(100.0),
        "steps": walk(),
        "oil": "NO_HARVEST",
        "weather": "watch",
        "mode": MODE,
        "ts": ts or FIXTURE_TS,
        "valedia_version": vc.VALEDIA_VERSION,
    }


def write_fixture(path=None, opt_in=True):
    path = path or DEFAULT_FIXTURE
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(paper_decision(opt_in=opt_in), f, indent=2, sort_keys=True)
        f.write("\n")
    return path


if __name__ == "__main__":
    print(write_fixture())
