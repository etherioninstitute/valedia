"""TREASURY-1 — report-only multi-venue cash vs floors. NEVER moves money.

No ACH/wire/card charges. No wallet transfer. No venue withdraw.
VALEDIA_TREASURY_AUTO default False; even if flipped, this module refuses.
REBALANCER_AUTOEXEC stays False. Alerts are strings only (webhook unused).
(c) GOATWORTHY Post LLC.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import valedia_config as vc

_ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
_MODELS = os.path.join(_ROOT, "models")
STATUS_NAME = "treasury_status.json"

VENUES = (
    "kalshi",
    "polymarket_us",
    "coinbase",
    "alpaca",
    "hyperliquid",
    "kraken",
)
WRITER_KEY = {
    "kalshi": "kalshi",
    "polymarket_us": "polymarket",
    "coinbase": "coinbase",
    "alpaca": "alpaca",
    "hyperliquid": "hyperliquid",
    "kraken": "kraken",
}
HOOK = {
    "kalshi": "kalshi_auth.get_balance",
    "polymarket_us": "polymarket_us_client.balances",
    "coinbase": "coinbase_trader.get_balances",
    "alpaca": "alpaca_executor.get_account",
    "hyperliquid": None,
    "kraken": None,
}
DOCUMENT_ONLY = frozenset({"hyperliquid", "kraken"})


class TreasuryAutoRefused(RuntimeError):
    """Auto top-up is not implemented. Human fund still required."""


def attempt_auto_topup(*_a, **_k):
    """Always refuse. This slice never transfers, even if the flag is on."""
    raise TreasuryAutoRefused(
        "VALEDIA_TREASURY_AUTO transfers not implemented; first human fund still required"
    )


def post_alert_webhook(url=None, payload=None):
    """Unused stub. Never POSTs. Alerts stay strings on the report."""
    return {
        "posted": False,
        "url": url or vc.treasury_alert_webhook() or "",
        "reason": "TREASURY_ALERT_WEBHOOK stub unused",
    }


def cash_from_kalshi(raw):
    """Kalshi /portfolio/balance is cents → USD."""
    if not isinstance(raw, dict):
        return None
    if raw.get("balance") is None:
        return None
    return float(raw["balance"]) / 100.0


def cash_from_polymarket(raw):
    """USD buyingPower / currentBalance from GET /v1/account/balances."""
    bals = []
    if isinstance(raw, dict):
        if isinstance(raw.get("balances"), list):
            bals = raw["balances"]
        elif raw.get("buyingPower") is not None or raw.get("currentBalance") is not None:
            bals = [raw]
    for row in bals:
        if not isinstance(row, dict):
            continue
        cur = str(row.get("currency") or "USD").upper()
        if cur not in ("USD", "USDC"):
            continue
        for k in ("buyingPower", "currentBalance", "assetAvailable"):
            if row.get(k) is not None:
                return float(row[k])
    return None


def cash_from_coinbase(raw):
    if not isinstance(raw, dict) or "error" in raw:
        return None
    usd = (raw.get("USD") or {}).get("balance")
    if usd is None:
        return None
    return float(usd)


def cash_from_alpaca(raw):
    if not isinstance(raw, dict):
        return None
    if raw.get("cash") is None and raw.get("buying_power") is None:
        return None
    cash = float(raw.get("cash") or 0)
    bp = raw.get("buying_power")
    return {"cash": cash, "buying_power": float(bp) if bp is not None else None}


def _row(name, cash=None, buying_power=None, readable=False, note=None, error=None):
    floors = vc.treasury_floors()
    floor = floors.get(name)
    writer = vc.venue_writer(WRITER_KEY.get(name, name))
    alert = None
    if name in DOCUMENT_ONLY:
        status = "DOCUMENT"
        note = note or "writer none; no cash read this slice"
    elif not readable or cash is None:
        status = "UNREAD"
    elif floor is not None and cash < float(floor):
        status = "WARN"
        alert = f"{name} below floor: ${cash:.2f} < ${float(floor):.2f}"
    else:
        status = "OK"
    out = {
        "venue": name,
        "writer": writer,
        "hook": HOOK.get(name),
        "readable": bool(readable and cash is not None),
        "cash": cash,
        "buying_power": buying_power,
        "floor": floor,
        "status": status,
        "alert": alert,
        "note": note,
    }
    if error:
        out["error"] = str(error)[:200]
    return out


def _live_reader(name):
    """Fail-closed live wrappers. Never transfer. Tests do not call these."""
    if name in DOCUMENT_ONLY:
        return None
    try:
        if name == "kalshi":
            from kalshi_auth import get_balance
            return {"kind": "kalshi", "raw": get_balance()}
        if name == "polymarket_us":
            import polymarket_us_client as pmu
            c = pmu.PolymarketUSClient()
            if not c.has_keys():
                return {"unread": True, "note": "no POLYMARKET_KEY_ID"}
            return {"kind": "polymarket_us", "raw": c.balances()}
        if name == "coinbase":
            from coinbase_trader import get_balances
            return {"kind": "coinbase", "raw": get_balances()}
        if name == "alpaca":
            from alpaca_executor import get_account
            return {"kind": "alpaca", "raw": get_account()}
    except Exception as e:
        return {"error": str(e)[:200]}
    return {"unread": True, "note": "no reader"}


def _parse_injected(name, payload):
    if payload is None:
        return _row(name, note="no reader")
    if isinstance(payload, (int, float)):
        return _row(name, cash=float(payload), readable=True)
    if not isinstance(payload, dict):
        return _row(name, error="bad payload")
    if payload.get("unread") or payload.get("error"):
        return _row(name, note=payload.get("note"), error=payload.get("error"))
    kind = payload.get("kind") or name
    raw = payload.get("raw", payload)
    cash = payload.get("cash")
    bp = payload.get("buying_power")
    if cash is None:
        if kind == "kalshi":
            cash = cash_from_kalshi(raw)
        elif kind in ("polymarket_us", "polymarket"):
            cash = cash_from_polymarket(raw)
        elif kind == "coinbase":
            cash = cash_from_coinbase(raw)
        elif kind == "alpaca":
            parsed = cash_from_alpaca(raw)
            if isinstance(parsed, dict):
                cash, bp = parsed.get("cash"), parsed.get("buying_power")
            else:
                cash = parsed
    if cash is None:
        return _row(name, note=payload.get("note"), error=payload.get("error"))
    return _row(name, cash=float(cash), buying_power=bp, readable=True,
                note=payload.get("note"))


def collect_status(readers=None, live_read=False):
    """Per-venue cash vs floors. Default unread unless readers or live_read.

    live_read tries existing GET helpers fail-closed. Never moves money.
    """
    readers = readers or {}
    venues = []
    alerts = []
    for name in VENUES:
        if name in readers:
            row = _parse_injected(name, readers[name])
        elif live_read:
            row = _parse_injected(name, _live_reader(name))
        else:
            row = _row(name, note="unread (inject reader or live_read=True)")
        venues.append(row)
        if row.get("alert"):
            alerts.append(row["alert"])
    auto_flag = vc.treasury_auto_enabled()
    auto = {
        "flag": auto_flag,
        "executed": False,
        "refused": True,
        "reason": "this slice never moves money; first human fund still required",
    }
    if auto_flag:
        try:
            attempt_auto_topup()
        except TreasuryAutoRefused as e:
            auto["reason"] = str(e)
    webhook = post_alert_webhook()
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "valedia_version": vc.VALEDIA_VERSION,
        "moves_money": False,
        "compound_in_place": True,
        "rebalancer_autoexec": bool(vc.rebalancer_autoexec_enabled()),
        "polymarket_us_live": False,
        "venues": venues,
        "alerts": alerts,
        "auto": auto,
        "webhook": webhook,
        "fence": (
            "No ACH/wire/card. No withdraw. VALEDIA_TREASURY_AUTO refused. "
            "REBALANCER_AUTOEXEC stays False."
        ),
    }


def emit_status(path=None, readers=None, live_read=False):
    report = collect_status(readers=readers, live_read=live_read)
    dest = path or os.path.join(_MODELS, STATUS_NAME)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w") as f:
        json.dump(report, f, indent=2)
        f.write("\n")
    report["path"] = dest
    return report


if __name__ == "__main__":
    print(json.dumps(emit_status(live_read=True), indent=2, default=str))
