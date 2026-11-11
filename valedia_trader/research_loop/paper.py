"""Paper trading: fake money on real market data, every order through the kill switch, every
decision written to the journal with its reasoning.

`PaperTrader.step` is one trading day. It fills yesterday's accepted orders at today's close (so a
decision made at a close never trades at that close), marks the book, and decides today's orders
from each strategy's target weights. Each order is offered to `check_order`; a refused order goes
nowhere and the refusal is journaled.

The book kept here is the record used for attribution by strategy. Orders can also be mirrored to
an Alpaca paper account (`AlpacaPaperVenue`), which refuses to be built on anything but paper keys
and the paper endpoint. There is no live-money path in this module.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import pandas as pd

from valedia_trader.research_loop import risk_gate
from valedia_trader.research_loop.ledger import market_regime
from valedia_trader.research_loop.strategy import Strategy, run_rules

__all__ = ["Journal", "PaperTrader", "Venue", "AlpacaPaperVenue", "VenueError", "market_conditions"]


class VenueError(RuntimeError):
    pass


class Journal:
    """Append-only decision log: one JSON line per decision in `journal/YYYY-MM-DD.jsonl`."""

    def __init__(self, directory: str | Path) -> None:
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)

    def write(self, day, entry: dict) -> dict:
        record = {"timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"), "day": str(pd.Timestamp(day).date()), **entry}
        with (self.dir / f"{record['day']}.jsonl").open("a") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
        return record

    def read(self, since=None, until=None) -> list[dict]:
        lo = str(pd.Timestamp(since).date()) if since is not None else ""
        hi = str(pd.Timestamp(until).date()) if until is not None else "9999"
        out: list[dict] = []
        for path in sorted(self.dir.glob("????-??-??.jsonl")):
            if lo <= path.stem <= hi:
                out += [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        return out


def market_conditions(prices: pd.DataFrame, market: tuple[str, ...] | list[str], window: int = 63) -> dict:
    """What the market looked like at the decision: regime of the last `window` days against the
    whole history given, 20-day annualised volatility and 20-day return of the equal-weight basket."""
    cols = [m for m in market if m in prices.columns]
    basket = np.log(prices[cols].where(prices[cols] > 0)).diff().mean(axis=1).dropna()
    recent = basket.tail(20)
    return {"regime": market_regime(prices[cols].tail(window + 1), prices[cols]),
            "volatility_20d": float(recent.std() * np.sqrt(252.0)) if len(recent) > 2 else float("nan"),
            "return_20d": float(np.expm1(recent.sum())) if len(recent) else float("nan")}


class Venue(Protocol):
    name: str

    def submit(self, symbol: str, units: float, tag: str) -> dict: ...


class AlpacaPaperVenue:
    """Mirror of accepted orders to an Alpaca paper account.

    Built only when the key is a paper key (Alpaca paper key ids start with "PK") and the client
    reports the paper endpoint; anything else raises. Fractional orders cannot open shorts on Alpaca,
    so sells are sent as whole shares and the remainder is reported back."""
    name = "alpaca-paper"
    PAPER_HOST = "paper-api.alpaca.markets"

    def __init__(self, api_key: str, secret_key: str, client: Any | None = None) -> None:
        if not str(api_key).startswith("PK"):
            raise VenueError("this is not an Alpaca paper key (paper key ids start with PK); refusing to connect")
        if client is None:
            from alpaca.trading.client import TradingClient
            client = TradingClient(api_key, secret_key, paper=True)
        base = getattr(client, "_base_url", "")
        if self.PAPER_HOST not in str(getattr(base, "value", base)):
            raise VenueError("the Alpaca client is not pointed at the paper endpoint; refusing to trade")
        self._client = client

    def account(self) -> dict:
        a = self._client.get_account()
        return {"equity": float(a.equity), "last_equity": float(a.last_equity), "account_number": str(a.account_number)}

    def positions(self) -> dict[str, float]:
        return {p.symbol: float(p.qty) for p in self._client.get_all_positions()}

    def submit(self, symbol: str, units: float, tag: str) -> dict:
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import MarketOrderRequest
        crypto = symbol.endswith("-USD")
        qty = abs(float(units))
        if units < 0 and not crypto:
            qty = float(int(qty))                       # whole shares for sells: fractional shorts are not allowed
        if qty <= 0:
            return {"status": "skipped", "reason": "rounds to zero shares"}
        order = self._client.submit_order(MarketOrderRequest(
            symbol=symbol.replace("-USD", "/USD") if crypto else symbol, qty=qty,
            side=OrderSide.BUY if units > 0 else OrderSide.SELL,
            time_in_force=TimeInForce.GTC if crypto else TimeInForce.DAY, client_order_id=tag[:48]))
        return {"status": "accepted", "id": str(order.id), "qty": qty}


class PaperTrader:
    """One fake-money account shared by the strategies that reached paper trading."""

    def __init__(self, directory: str | Path, limits_file: str | Path, starting_cash: float = 100_000.0,
                 fee_bps: float = 1.0, slippage_bps: float = 2.0, venue: Venue | None = None,
                 halt_file: str | Path | None = risk_gate.HALT_FILE, min_trade_pct: float = 0.002) -> None:
        self.dir = Path(directory)
        self.journal = Journal(self.dir)
        self.limits_file, self.halt_file, self.venue = Path(limits_file), halt_file, venue
        self.fee_bps, self.slippage_bps, self.min_trade_pct = fee_bps, slippage_bps, min_trade_pct
        self.state_file, self.latch_file = self.dir / "paper_state.json", self.dir / "kill_switch.json"
        if self.state_file.exists():
            self.state = json.loads(self.state_file.read_text())
        else:
            self.state = {"starting_cash": starting_cash, "cash": starting_cash, "positions": {}, "pending": [],
                          "history": [], "peak_equity": starting_cash, "last_day": None, "realized": {}}

    # ---- book
    def _lot(self, strategy: str, symbol: str) -> dict:
        return self.state["positions"].setdefault(strategy, {}).setdefault(symbol, {"units": 0.0, "cost": 0.0})

    def equity(self, prices_row: pd.Series) -> float:
        value = sum(lot["units"] * float(prices_row[sym]) for book in self.state["positions"].values()
                    for sym, lot in book.items() if lot["units"] and np.isfinite(prices_row.get(sym, np.nan)))
        return float(self.state["cash"] + value)

    def open_positions(self) -> int:
        held = {(s, sym) for s, book in self.state["positions"].items() for sym, lot in book.items() if abs(lot["units"]) > 1e-12}
        held |= {(o["strategy"], o["symbol"]) for o in self.state["pending"] if not o["reduces_risk"]}
        return len(held)

    def _fill_pending(self, day, row: pd.Series) -> None:
        still = []
        for o in self.state["pending"]:
            px = float(row.get(o["symbol"], np.nan))
            if not np.isfinite(px):
                still.append(o)                           # no price today: the order waits
                continue
            fill = px * (1.0 + np.sign(o["units"]) * self.slippage_bps * 1e-4)
            fee = abs(o["units"]) * fill * self.fee_bps * 1e-4
            lot = self._lot(o["strategy"], o["symbol"])
            before = lot["units"]
            after = before + o["units"]
            realized = None
            if before and np.sign(o["units"]) != np.sign(before):          # closing all or part of a position
                closed = min(abs(o["units"]), abs(before))
                realized = float(closed * (fill - lot["cost"]) * np.sign(before))
            # a strategy's realised result carries every fee it paid, on entries as well as exits
            self.state["realized"][o["strategy"]] = self.state["realized"].get(o["strategy"], 0.0) + (realized or 0.0) - fee
            if abs(after) < 1e-12:
                lot["units"], lot["cost"] = 0.0, 0.0
            elif before == 0 or np.sign(after) != np.sign(before):
                lot["units"], lot["cost"] = after, fill
            elif np.sign(o["units"]) == np.sign(before):
                lot["cost"] = (lot["cost"] * abs(before) + fill * abs(o["units"])) / abs(after)
                lot["units"] = after
            else:
                lot["units"] = after
            self.state["cash"] -= o["units"] * fill + fee
            self.journal.write(day, {"type": "fill", "strategy": o["strategy"], "version": o["version"], "symbol": o["symbol"],
                                     "units": o["units"], "price": fill, "fee": fee, "decided_on": o["decided_on"],
                                     "realized_pnl": realized, "entry_conditions": o.get("conditions"), "reason": o["reasoning"]})
        self.state["pending"] = still

    # ---- one trading day
    def step(self, prices: pd.DataFrame, strategies: dict[int, Strategy]) -> dict:
        """Run one day using `prices` up to and including today (its last row). `strategies` maps
        the ledger's hypothesis id to the strategy. Returns the day's summary."""
        day = prices.index[-1]
        if self.state["last_day"] is not None and str(day.date()) <= self.state["last_day"]:
            raise ValueError(f"{day.date()} was already processed; paper trading only moves forward")
        row = prices.iloc[-1]
        previous = self.state["history"][-1]["equity"] if self.state["history"] else self.state["starting_cash"]
        before = self.state["history"][-1]["strategy_values"] if self.state["history"] else {}
        self._fill_pending(day, row)
        equity = self.equity(row)
        self.state["peak_equity"] = max(self.state["peak_equity"], equity)
        account = {"equity": equity, "daily_loss_pct": max(0.0, (previous - equity) / previous * 100.0),
                   "drawdown_pct": max(0.0, (self.state["peak_equity"] - equity) / self.state["peak_equity"] * 100.0),
                   "open_positions": self.open_positions()}
        try:
            limits = risk_gate.load_limits(self.limits_file)
        except risk_gate.RiskConfigError:
            limits = None                                  # check_order will then block every order
        if limits and account["drawdown_pct"] >= limits["max_drawdown_pct"] and not self.latch_file.exists():
            self.latch_file.write_text(json.dumps({"tripped_on": str(day.date()), "drawdown_pct": account["drawdown_pct"]}))
            self.journal.write(day, {"type": "kill_switch", "decision": "latched",
                                     "reasoning": f"drawdown {account['drawdown_pct']:.2f}% reached the limit; new risk is blocked until the latch is reset"})

        placed = blocked = 0
        share = equity / max(len(strategies), 1)
        for hid, s in strategies.items():
            weights, events = run_rules(s, prices)
            latest = {e["symbol"]: e for e in events}          # events are in time order: the last one per symbol wins
            cond = market_conditions(prices, s.market)
            acted = False
            for sym in s.market:
                px = float(row.get(sym, np.nan))
                if not np.isfinite(px) or px <= 0:
                    continue
                lot = self._lot(s.name, sym)
                target = float(weights[sym].iloc[-1]) * share / px
                delta = target - lot["units"]
                value = abs(delta) * px
                if value < self.min_trade_pct * share:
                    continue
                acted = True
                reduces = abs(target) < abs(lot["units"]) and (target == 0 or np.sign(target) == np.sign(lot["units"]))
                order = {"symbol": sym, "side": "buy" if delta > 0 else "sell", "value": value, "reduces_risk": bool(reduces),
                         "adds_to_existing": bool(lot["units"]), "position_value_after": abs(target) * px}
                ok, why = risk_gate.check_order(order, account, limits, latch_file=self.latch_file, halt_file=self.halt_file)
                ev = latest.get(sym)
                if ev is not None and ev["date"] == day:
                    why_now = f"{ev['action']}: {ev['reason']}"
                elif ev is not None:
                    why_now = f"catching up with the strategy's book ({ev['action']} on {ev['date'].date()}: {ev['reason']})"
                else:
                    why_now = "rebalance to the strategy's target weight"
                reasoning = f"{why_now}; target weight {float(weights[sym].iloc[-1]):+.3f} of the strategy's capital"
                entry = {"type": "decision", "hypothesis_id": hid, "strategy": s.name, "version": s.version, "symbol": sym,
                         "market_conditions": cond, "order": order, "decision": "placed" if ok else "blocked",
                         "risk_check": why, "reasoning": reasoning}
                if ok:
                    placed += 1
                    self.state["pending"].append({"strategy": s.name, "version": s.version, "symbol": sym, "units": delta,
                                                  "decided_on": str(day.date()), "reduces_risk": bool(reduces),
                                                  "reasoning": reasoning, "conditions": cond})
                    if not reduces:
                        account["open_positions"] = self.open_positions()
                    if self.venue is not None:
                        try:
                            entry["venue"] = {"name": self.venue.name, **self.venue.submit(sym, delta, f"{s.name}-{sym}-{day.date()}")}
                        except Exception as exc:  # the journal keeps the truth; the book stays the record
                            entry["venue"] = {"name": self.venue.name, "status": "error", "error": f"{type(exc).__name__}: {exc}"}
                else:
                    blocked += 1
                self.journal.write(day, entry)
            if not acted:
                self.journal.write(day, {"type": "decision", "hypothesis_id": hid, "strategy": s.name, "version": s.version,
                                         "market_conditions": cond, "decision": "no trade",
                                         "reasoning": "positions already match the strategy's targets"})
        by_strategy = {s: self._strategy_value(s, row) - before.get(s, 0.0) for s in self.state["positions"]}
        summary = {"day": str(day.date()), "equity": equity, "daily_loss_pct": account["daily_loss_pct"],
                   "drawdown_pct": account["drawdown_pct"], "open_positions": account["open_positions"],
                   "orders_placed": placed, "orders_blocked": blocked, "strategy_values": {s: self._strategy_value(s, row) for s in self.state["positions"]},
                   "strategy_value_change": by_strategy, "capital_per_strategy": share}
        self.state["history"].append(summary)
        self.state["last_day"] = str(day.date())
        self.journal.write(day, {"type": "summary", **summary})
        self._save()
        return summary

    def _strategy_value(self, strategy: str, row: pd.Series) -> float:
        """Open market value of a strategy's positions plus what it has realised so far."""
        open_value = sum(lot["units"] * (float(row[sym]) - lot["cost"]) for sym, lot in self.state["positions"].get(strategy, {}).items()
                         if lot["units"] and np.isfinite(row.get(sym, np.nan)))
        return float(open_value + self.state["realized"].get(strategy, 0.0))

    def reset_latch(self, who: str, day) -> bool:
        """A person clears the drawdown latch. The reset is journaled; the high-water mark restarts."""
        if not self.latch_file.exists():
            return False
        self.latch_file.unlink()
        self.state["peak_equity"] = self.state["history"][-1]["equity"] if self.state["history"] else self.state["starting_cash"]
        self.journal.write(day, {"type": "kill_switch", "decision": "reset", "reasoning": f"latch cleared by {who}"})
        self._save()
        return True

    def _save(self) -> None:
        tmp = self.state_file.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.state, indent=1, default=str))
        tmp.replace(self.state_file)
