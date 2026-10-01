#!/usr/bin/env python3
"""Valedia paper sink for TradingView strategy alerts. $504 bankroll. No orders.

Crypto: CE-3 CRYPTO_FEE_PCT + CRYPTO_SLIPPAGE_PCT via crypto_paper.fill_px.
Equity: fee_engine.STATIC_FEES alpaca taker plus EQUITY_SLIPPAGE_PCT. Not Kelly.
"""
from __future__ import annotations

import fcntl
import hmac
import json
import math
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional, Tuple

import nimble_wiring_spot as nws
import valedia_config as vc
from crypto_paper import CLIP_FRAC, fee_usd, fill_px
from fee_engine import STATIC_FEES

MODE = "PAPER/SHADOW"
SOURCE = "tc_auto_paper"
VENUE = "valedia-paper"
PAPER_BANKROLL = 504.0
EQUITY_SLIPPAGE_PCT = 0.0001  # alpaca stock commission is 0; 1 bp is paper friction
STRATEGY_ALLOW = frozenset({"supertrend_paper", "ugft_supertrend_admit"})
SECRET_KEYS = frozenset({
    "passphrase", "api_token", "token", "secret", "authorization", "password",
})
_CURLY = "\u201c\u201d\u2018\u2019"
_OIL = frozenset({"CL", "CL1!", "USO", "USOIL", "BRENT", "WTI", "OIL"})
_CRYPTO_PREFIX = ("COINBASE:", "KRAKEN:", "BINANCE:", "BINANCEUS:")
_CRYPTO_BASE = ("BTC", "ETH", "SOL", "XRP", "DOGE", "ADA")

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
DEFAULT_BOOK = os.path.join(_ROOT, "data", "tc-auto-paper", "book.json")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _num(v: Any) -> Optional[float]:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _floor_qty(q: float, places: int = 8) -> float:
    scale = 10 ** places
    return math.floor((float(q) + 1e-12) * scale) / scale


def fresh_book() -> dict:
    return _fence({
        "type": "paper_book", "bankroll": PAPER_BANKROLL, "cash": PAPER_BANKROLL,
        "positions": {}, "fees_paid": 0.0, "realized_pnl": 0.0, "fills": 0,
    })


def book_dir(book_path: str) -> str:
    return os.path.dirname(book_path) or "."


def _sidecar(book_path: str, name: str) -> str:
    return os.path.join(book_dir(book_path), name)


def asset_class(ticker: str) -> str:
    raw = str(ticker or "").strip().upper()
    base = raw.split(":")[-1]
    if base in _OIL or "OIL" in base:
        return "oil"
    if raw.startswith(_CRYPTO_PREFIX) or any(base.startswith(b) for b in _CRYPTO_BASE):
        return "crypto"
    if re.fullmatch(r"[A-Z]{1,5}", base):
        return "equity"
    return "unknown"


def fee_book(kind: str) -> dict:
    if kind == "crypto":
        return {
            "name": "CE-3",
            "fee_pct": float(vc.CRYPTO_FEE_PCT),
            "slip_pct": float(vc.CRYPTO_SLIPPAGE_PCT),
        }
    if kind == "equity":
        return {
            "name": "alpaca_static",
            "fee_pct": float(STATIC_FEES["alpaca"]["taker"]),
            "slip_pct": EQUITY_SLIPPAGE_PCT,
        }
    raise ValueError(kind)


def normalize_action(action: Any) -> str:
    a = str(action or "").strip().lower()
    if a in ("buy", "long", "entry"):
        return "buy"
    if a in ("sell", "flat", "close", "exit"):
        return "sell"
    if a in ("short",):
        return "short"
    return a


def has_curly(body: Any) -> bool:
    if isinstance(body, str):
        return any(c in body for c in _CURLY)
    if isinstance(body, dict):
        return any(has_curly(k) or has_curly(v) for k, v in body.items())
    if isinstance(body, list):
        return any(has_curly(x) for x in body)
    return False


def _strip_secrets(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            k: _strip_secrets(v)
            for k, v in obj.items()
            if str(k).lower() not in SECRET_KEYS and "passphrase" not in str(k).lower()
        }
    if isinstance(obj, list):
        return [_strip_secrets(x) for x in obj]
    return obj


def expected_passphrase(env: Optional[dict] = None) -> str:
    env = os.environ if env is None else env
    return str(env.get("VALEDIA_TV_WEBHOOK_PASSPHRASE") or "")


def live_flag(env: Optional[dict] = None) -> bool:
    env = os.environ if env is None else env
    raw = str(env.get("VALEDIA_TV_LIVE") or env.get("TC_AUTO_LIVE") or "0").strip().lower()
    return raw in ("1", "true", "yes", "on")


def trading_halted(env: Optional[dict] = None) -> bool:
    env = os.environ if env is None else env
    raw = str(env.get("VALEDIA_TRADING_HALT") or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    try:
        return bool(vc.trading_halted())
    except Exception:  # noqa: BLE001
        return False


def load_book(path: Optional[str] = None) -> dict:
    path = path or DEFAULT_BOOK
    if not os.path.exists(path):
        return fresh_book()
    with open(path) as f:
        doc = json.load(f)
    if not isinstance(doc, dict):
        return fresh_book()
    doc["bankroll"] = PAPER_BANKROLL
    return _fence(doc)


def _locked(path: str):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    lock = open(path + ".lock", "a+")
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
    return lock


def save_book(book: dict, path: str) -> None:
    book = _strip_secrets(book)
    book["create_order"] = False
    book["live"] = False
    book["counts_toward_kelly"] = False
    raw = json.dumps(book, indent=2, default=str)
    if '"passphrase":' in raw.lower() or '"api_token":' in raw.lower():
        raise ValueError("refusing book write: secret field present")
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(raw + "\n")
    os.replace(tmp, path)


def append_jsonl(path: str, row: dict) -> None:
    row = _strip_secrets(row)
    line = json.dumps(row, default=str) + "\n"
    low = line.lower()
    if '"passphrase":' in low or '"api_token":' in low:
        raise ValueError("refusing jsonl write: secret field present")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a") as f:
        f.write(line)


def size_buy_qty(cash: float, mid: float, slip: float, fee_pct: float, contracts: Optional[float]) -> float:
    # 10% of paper equity (SuperTrend pine default_qty_value). Never Kelly. Never up.
    unit = float(mid) * (1.0 + float(slip)) * (1.0 + float(fee_pct))
    if unit <= 0 or cash <= 0 or mid <= 0:
        return 0.0
    clip_qty = (float(cash) * float(CLIP_FRAC)) / float(mid)
    affordable = float(cash) / unit
    qty = min(clip_qty, affordable)
    if contracts is not None:
        qty = min(qty, float(contracts))
    return _floor_qty(max(0.0, qty))


def _fence(row: dict) -> dict:
    row.update({
        "create_order": False, "live": False, "dispatchable": False,
        "paperTrading": True, "counts_toward_kelly": False, "mode": MODE,
        "source": SOURCE, "venue": VENUE, "oil": "NO_HARVEST",
    })
    return row


def _intent(fill: dict, gates: dict) -> dict:
    notional = round(float(fill["px"]) * float(fill["qty"]), 8)
    return _fence({
        "type": "intent", "strategy": fill["strategy"], "instrument": fill["ticker"],
        "side": fill["side"], "order_type": "market", "tif": "IOC",
        "qty": fill["qty"], "max_qty": fill["qty"], "notional": notional,
        "max_notional": notional, "limit_price": fill["px"], "ledger_source": "paper",
        "status": "dry_run", "reason": "paper_sink_only", "gates": gates,
        "live_attach_later": "oms_locked_until_valedia_runtime_unlock",
        "ts": fill["ts"],
    })


def decide(body: Any, book: dict, env: Optional[dict] = None) -> Tuple[int, dict]:
    env = os.environ if env is None else env
    if not isinstance(body, dict):
        return 422, {"ok": False, "error": "not_object", "create_order": False, "live": False}
    if has_curly(body):
        return 422, {"ok": False, "error": "curly_quotes", "create_order": False, "live": False}
    expected = expected_passphrase(env)
    got = str(body.get("passphrase") or "")
    if not expected or not hmac.compare_digest(got, expected):
        return 401, {"ok": False, "error": "unauthorized", "create_order": False, "live": False}

    token_stripped = any(str(k).lower() in ("api_token", "token") for k in body)
    action = normalize_action(body.get("action"))
    ticker = str(body.get("ticker") or "").strip()
    price = _num(body.get("price"))
    contracts = _num(body.get("contracts"))
    strategy = str(body.get("strategy") or body.get("strategy_id") or "supertrend_paper").strip()
    if strategy.lower() in ("supertrend", "grok supertrend strategy paper"):
        strategy = "supertrend_paper"
    strategy = strategy.lower()

    if action not in ("buy", "sell", "short"):
        return 422, {"ok": False, "error": "bad_action", "create_order": False, "live": False}
    if not ticker:
        return 422, {"ok": False, "error": "missing_ticker", "create_order": False, "live": False}
    if price is None or price <= 0:
        return 422, {"ok": False, "error": "bad_price", "create_order": False, "live": False}
    if "contracts" in body and body.get("contracts") not in (None, "") and contracts is None:
        return 422, {"ok": False, "error": "bad_contracts", "create_order": False, "live": False}
    if contracts is not None and contracts < 0:
        return 422, {"ok": False, "error": "bad_contracts", "create_order": False, "live": False}

    kind = asset_class(ticker)
    halt = trading_halted(env)
    ignored_live = live_flag(env)
    gates = {
        "fee_ok": False,
        "filled": False,
        "halt": halt,
        "create_order": False,
        "live": False,
        "counts_toward_kelly": False,
        "oil": "NO_HARVEST",
        "ugft_admit": "context_only_not_a_live_gate",
        "size_rule": "supertrend_pine_10pct_of_paper_equity",
        "kelly": False,
    }
    base = {
        "type": "decision",
        "ticker": ticker,
        "action": action,
        "strategy": strategy,
        "price": price,
        "band": "allowlist",
        "reason": "ok",
        "filled": False,
        "asset_class": kind,
        "token_stripped": token_stripped,
        "live_flag_ignored": ignored_live,
        "ts": _now(),
        "gates": gates,
        "cash_before": round(float(book.get("cash") or 0), 8),
    }
    if ignored_live:
        base["reason"] = "live_flag_ignored"

    def _refuse(code: int, error: str, *, journal: bool) -> Tuple[int, dict]:
        base["reason"] = error
        base["band"] = "refuse"
        base["filled"] = False
        row = _fence(base)
        if journal:
            book.setdefault("_journal", []).append(row)
        return code, {
            "ok": False,
            "error": error,
            "create_order": False,
            "live": False,
            "filled": False,
            "decision": public_fields(row),
        }

    if kind == "oil":
        return _refuse(422, "oil_no_harvest", journal=True)
    if kind != "crypto" and kind != "equity":
        return _refuse(422, "unknown_asset_class", journal=True)
    if strategy not in STRATEGY_ALLOW:
        return _refuse(422, "unknown_strategy", journal=True)
    if action == "short":
        return _refuse(422, "no_short", journal=True)
    if halt:
        base["band"] = "review_only"
        base["reason"] = "trading_halt_shadow"
        row = _fence(base)
        book.setdefault("_journal", []).append(row)
        return 200, {
            "ok": True,
            "create_order": False,
            "live": False,
            "filled": False,
            "decision": public_fields(row),
        }

    book_fees = fee_book(kind)
    slip = float(book_fees["slip_pct"])
    fee_pct = float(book_fees["fee_pct"])
    gates["fee_ok"] = True
    gates["fee_book"] = book_fees["name"]

    if action == "buy":
        qty = size_buy_qty(float(book["cash"]), price, slip, fee_pct, contracts)
        if qty <= 0:
            return _refuse(422, "size_zero", journal=True)
        px = float(fill_px(price, "buy", slip))
        fee = float(fee_usd(px * qty, "taker", fee_pct, fee_pct))
        spend = px * qty + fee
        if float(book["cash"]) + 1e-8 < spend:
            return _refuse(422, "insufficient_cash", journal=True)
        # Last step before the paper fill: Nimble 2 verdict (shadow stamps; veto may skip the entry).
        nv = nws.gate(kind, ticker, {"fee_check": {"pass": True}, "has_pos": ticker in book["positions"]},
                      source="tc_auto_paper", venue=VENUE, label=strategy)
        nws.stamp(base, nv)
        if nws.blocked(nv):
            base["band"], base["reason"] = "review_only", nws.SKIP
            row = _fence(base)
            book.setdefault("_journal", []).append(row)
            return 200, {"ok": True, "create_order": False, "live": False, "filled": False,
                         "decision": public_fields(row)}
        book["cash"] = float(book["cash"]) - spend
        book["fees_paid"] = float(book.get("fees_paid") or 0) + fee
        pos = book["positions"].get(ticker) or {"qty": 0.0, "cost_cash": 0.0}
        pos["qty"] = float(pos["qty"]) + qty
        pos["cost_cash"] = float(pos["cost_cash"]) + spend
        book["positions"][ticker] = pos
        fill = {
            "type": "fill", "ticker": ticker, "side": "buy", "strategy": strategy,
            "qty": round(qty, 8), "mid": price, "px": round(px, 8), "fee": round(fee, 8),
            "fee_book": book_fees["name"], "fee_pct": fee_pct, "slip_pct": slip,
            "notional": round(px * qty, 8), "realized": None,
            "paper": True, "create_order": False, "live": False, "ts": base["ts"],
        }
    else:
        pos = book["positions"].get(ticker)
        held = float(pos["qty"]) if pos else 0.0
        if held <= 1e-12:
            base["band"] = "review_only"
            base["reason"] = "no_position_no_short"
            row = _fence(base)
            book.setdefault("_journal", []).append(row)
            return 200, {
                "ok": True, "create_order": False, "live": False, "filled": False,
                "decision": public_fields(row),
            }
        qty = held if contracts is None or contracts == 0 else min(held, float(contracts))
        qty = _floor_qty(qty)
        if qty <= 0:
            return _refuse(422, "size_zero", journal=True)
        px = float(fill_px(price, "sell", slip))
        fee = float(fee_usd(px * qty, "taker", fee_pct, fee_pct))
        proceeds = px * qty - fee
        frac = qty / float(pos["qty"])
        released = float(pos["cost_cash"]) * frac
        realized = proceeds - released
        book["cash"] = float(book["cash"]) + proceeds
        book["fees_paid"] = float(book.get("fees_paid") or 0) + fee
        book["realized_pnl"] = float(book.get("realized_pnl") or 0) + realized
        pos["qty"] = float(pos["qty"]) - qty
        pos["cost_cash"] = float(pos["cost_cash"]) - released
        if pos["qty"] <= 1e-10:
            book["positions"].pop(ticker, None)
        else:
            book["positions"][ticker] = pos
        fill = {
            "type": "fill", "ticker": ticker, "side": "sell", "strategy": strategy,
            "qty": round(qty, 8), "mid": price, "px": round(px, 8), "fee": round(fee, 8),
            "fee_book": book_fees["name"], "fee_pct": fee_pct, "slip_pct": slip,
            "notional": round(px * qty, 8), "realized": round(realized, 8),
            "paper": True, "create_order": False, "live": False, "ts": base["ts"],
        }

    book["fills"] = int(book.get("fills") or 0) + 1
    base["filled"] = True
    base["reason"] = "paper_fill"
    if ignored_live:
        base["reason"] = "paper_fill|live_flag_ignored"
    base["fill"] = fill
    gates["filled"] = True
    row = _fence(base)
    intent = _intent(fill, gates)
    book.setdefault("_journal", []).append(row)
    book.setdefault("_fills", []).append(fill)
    book.setdefault("_intents", []).append(intent)
    return 200, {
        "ok": True,
        "create_order": False,
        "live": False,
        "filled": True,
        "mode": MODE,
        "decision": public_fields(row),
        "fill": fill,
        "intent": {
            "status": intent["status"],
            "dispatchable": False,
            "venue": VENUE,
            "side": intent["side"],
            "qty": intent["qty"],
            "create_order": False,
        },
        "book": book_public(book),
    }


def public_fields(row: dict) -> dict:
    keys = (
        "ticker", "action", "strategy", "band", "reason", "filled", "asset_class",
        "create_order", "live", "mode", "paperTrading", "counts_toward_kelly",
        "token_stripped", "ts", "cash_before",
    )
    out = {k: row.get(k) for k in keys}
    if row.get("fill"):
        out["fill"] = row["fill"]
    return out


def book_public(book: dict) -> dict:
    cash = round(float(book.get("cash") or 0), 8)
    return {
        "bankroll": PAPER_BANKROLL, "cash": cash,
        "fees_paid": round(float(book.get("fees_paid") or 0), 8),
        "realized_pnl": round(float(book.get("realized_pnl") or 0), 8),
        "pnl_vs_bankroll": round(cash - PAPER_BANKROLL, 8),
        "positions": book.get("positions") or {}, "fills": int(book.get("fills") or 0),
        "create_order": False, "live": False, "counts_toward_kelly": False,
        "oil": "NO_HARVEST",
    }


def persist_decide(book: dict, book_path: str) -> dict:
    journal = book.pop("_journal", [])
    fills = book.pop("_fills", [])
    intents = book.pop("_intents", [])
    for row in journal:
        append_jsonl(_sidecar(book_path, "decisions.jsonl"), row)
    for row in fills:
        append_jsonl(_sidecar(book_path, "fills.jsonl"), row)
    for row in intents:
        append_jsonl(_sidecar(book_path, "intents.jsonl"), row)
    save_book(book, book_path)
    return book


def handle_alert(body: Any, *, env: Optional[dict] = None, book_path: Optional[str] = None) -> Tuple[int, dict]:
    book_path = book_path or DEFAULT_BOOK
    lock = _locked(book_path)
    try:
        book = load_book(book_path)
        code, resp = decide(body, book, env)
        if code != 401 and (code == 200 or book.get("_journal")):
            persist_decide(book, book_path)
        else:
            book.pop("_journal", None)
            book.pop("_fills", None)
            book.pop("_intents", None)
        resp["create_order"] = False
        resp["live"] = False
        return code, resp
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()



