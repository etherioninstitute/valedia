"""Paper trading: fake money on real market data, every order through the kill switch, every
decision written to the journal with its reasoning.

`PaperTrader.step` is one trading day. It fills yesterday's accepted orders at today's close (so a
decision made at a close never trades at that close), marks the book, and decides today's orders
from each strategy's target weights. Each order is offered to `check_order`; a refused order goes
nowhere and the refusal is journaled.

The book kept here is the record used for attribution by strategy. Orders can also be mirrored to
an Alpaca paper account (`AlpacaPaperVenue`), which refuses to be built on anything but paper keys
and the paper endpoint. There is no live-money path in this module.

Given tax rules (`tax_rules`), the trader also keeps the account's tax ledger (execution_and_risk/tax.py)
and writes beside every order what it would do for tax, estimated at the decision price, and in each day's
summary the year's realised result so far. These are notes only: they never block, delay, resize or
reorder an order. A ledger that cannot take a fill (a short position, for one) stops, and from then on
every note says it is unavailable and why; trading carries on exactly as with tax off.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import pandas as pd

from valedia_trader.backtest.engine import buy_and_hold
from valedia_trader.research_loop import diagnostics, risk_gate
from valedia_trader.research_loop.ledger import market_regime
from valedia_trader.research_loop.strategy import Strategy, _exchange_sessions, run_rules

__all__ = ["Journal", "PaperTrader", "Venue", "AlpacaPaperVenue", "VenueError", "market_conditions"]


_SESSIONS: list = []


def next_session(day) -> str:
    """The exchange session after `day`, when an order decided at its close fills; `day` itself if the calendar
    cannot answer."""
    d = pd.Timestamp(day).normalize()
    try:
        if not _SESSIONS:
            _SESSIONS.append(_exchange_sessions())
        sessions = _SESSIONS[0]
        i = sessions.searchsorted(d, side="right")
        if i < len(sessions):
            return str(sessions[i].date())
    except Exception:
        pass
    return str(d.date())


def tax_sell_note(c: dict, px: float, day: str) -> dict:
    """The compact note for a sale, from `TaxLedger.check_sell`."""
    st, lt = c["gain_loss"]["short_term"], c["gain_loss"]["long_term"]
    parts = [f"{term} {'gain' if v >= 0 else 'loss'} of {abs(v):.2f}" for term, v in (("short-term", st), ("long-term", lt)) if abs(v) >= 0.005]
    effect = "would realise " + (" and ".join(f"a {p}" for p in parts) if parts else "no gain or loss")
    if c["disallowed_loss"] > 0:
        bought = sorted({m["purchase_date"] for m in c["wash_sale"]["matches"]})
        effect += f"; {c['disallowed_loss']:.2f} of it would be disallowed as a wash sale against the purchase of {', '.join(bought)}"
    if c["wash_sale"]["loss_at_risk"] > 0:
        effect += f"; buying {c['symbol']} again before {c['wash_sale']['clean_from']} would make it a wash sale"
    return {"side": "sell", "estimated_at_price": float(px), "estimated_for_day": str(day), "gain_loss": {"short_term": st, "long_term": lt},
            "disallowed_loss": c["disallowed_loss"], "loss_at_risk": c["wash_sale"]["loss_at_risk"],
            "clean_from": c["wash_sale"]["clean_from"], "effect": effect + ".", "unverified_rules": c["unverified_rules"], "note": c["note"]}


def tax_buy_note(c: dict, day: str) -> dict:
    """The compact note for a purchase, from `TaxLedger.check_buy`; the sentence names each sale date once."""
    washes = [{"sale_date": m["sale_date"], "units": m["units"], "disallowed_loss": m["disallowed_loss"]} for m in c["matches"]]
    by_date: dict[str, float] = {}
    for w in washes:
        by_date[w["sale_date"]] = by_date.get(w["sale_date"], 0.0) + w["disallowed_loss"]
    if by_date:
        effect = ("would turn " + " and ".join(f"{v:.2f} of the loss sold on {k}" for k, v in by_date.items())
                  + " into a wash sale, carried into this lot")
    else:
        effect = f"would wash no earlier loss of {c['symbol']}"
    return {"side": "buy", "estimated_for_day": str(day), "disallowed_loss": c["disallowed_loss"], "washes": washes,
            "clean_from": c["clean_from"], "effect": effect + ".", "unverified_rules": c["unverified_rules"], "note": c["note"]}


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

    PANEL_MODES = ("off", "shadow", "veto")

    def __init__(self, directory: str | Path, limits_file: str | Path, starting_cash: float = 100_000.0,
                 fee_bps: float = 1.0, slippage_bps: float = 2.0, venue: Venue | None = None,
                 halt_file: str | Path | None = risk_gate.HALT_FILE, min_trade_pct: float = 0.002,
                 panel=None, panel_mode: str = "off", panel_horizon_days: int = 5, facts=None, tax_rules=None) -> None:
        if panel_mode not in self.PANEL_MODES:
            raise ValueError(f"panel_mode must be one of {self.PANEL_MODES}")
        if panel_mode != "off" and panel is None:
            raise ValueError("a panel is needed for shadow or veto mode")
        # The decision panel sees every order that adds risk. In shadow mode its verdict is only written
        # down; in veto mode a refusal (or an unreachable panel) blocks the order. It can never create one.
        self.panel, self.panel_mode, self.panel_horizon = panel, panel_mode, int(panel_horizon_days)
        self._facts = facts
        self.dir = Path(directory)
        self.journal = Journal(self.dir)
        self.limits_file, self.halt_file, self.venue = Path(limits_file), halt_file, venue
        self.fee_bps, self.slippage_bps, self.min_trade_pct = fee_bps, slippage_bps, min_trade_pct
        self.state_file, self.latch_file = self.dir / "paper_state.json", self.dir / "kill_switch.json"
        if self.state_file.exists():
            self.state = json.loads(self.state_file.read_text())
            self.state.setdefault("panel_cases", [])
        else:
            self.state = {"starting_cash": starting_cash, "cash": starting_cash, "positions": {}, "pending": [],
                          "history": [], "peak_equity": starting_cash, "last_day": None, "realized": {}, "panel_cases": []}
        # the account's tax ledger: built once from the journal's fills, then fed each new fill. Notes only.
        self.tax_rules, self.tax_ledger, self.tax_unavailable = tax_rules, None, None
        self._tax_today, self._tax_fill_day = [], None        # in memory only: the orders placed so far this step
        if tax_rules is not None:
            try:
                from valedia_trader.execution_and_risk.tax import TaxLedger
                self.tax_ledger = TaxLedger.from_fills(self.journal.read(), tax_rules)
            except Exception as exc:
                self.tax_unavailable = f"the tax ledger could not take a fill: {exc}"

    # ---- book
    def _lot(self, strategy: str, symbol: str) -> dict:
        return self.state["positions"].setdefault(strategy, {}).setdefault(symbol, {"units": 0.0, "cost": 0.0})

    def equity(self, prices_row: pd.Series) -> float:
        value = sum(lot["units"] * float(prices_row[sym]) for book in self.state["positions"].values()
                    for sym, lot in book.items() if lot["units"] and np.isfinite(prices_row.get(sym, np.nan)))
        return float(self.state["cash"] + value)

    def funds_held(self) -> set[str]:
        """Funds the account holds, or has an order in to open or add to."""
        held = {sym for book in self.state["positions"].values() for sym, lot in book.items() if abs(lot["units"]) > 1e-12}
        return held | {o["symbol"] for o in self.state["pending"] if not o["reduces_risk"]}

    def open_positions(self) -> int:
        """Positions of the account. A fund two strategies both hold is one position: the limits are the
        account's, and one position is what the broker would show."""
        return len(self.funds_held())

    def _units_elsewhere(self, strategy: str, symbol: str) -> float:
        """Units of the fund that the account holds or has ordered through its other strategies."""
        lots = sum(book.get(symbol, {}).get("units", 0.0) for name, book in self.state["positions"].items() if name != strategy)
        return float(lots + sum(o["units"] for o in self.state["pending"] if o["symbol"] == symbol and o["strategy"] != strategy))

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
            self._tax_take(day, o["symbol"], o["units"], fill, fee)
        self.state["pending"] = still

    # ---- tax notes (never change an order, a fill or the book)
    def _tax_take(self, day, symbol: str, units: float, price: float, fee: float) -> None:
        if self.tax_ledger is None or self.tax_unavailable is not None:
            return
        try:
            self.tax_ledger.add_fill(str(pd.Timestamp(day).date()), symbol, float(units), float(price), float(fee))
        except Exception as exc:
            self.tax_unavailable = f"the tax ledger could not take a fill: {exc}"

    def _tax_note(self, day, symbol: str, units: float, px: float) -> dict:
        """What this order would do for tax at today's price, as of the session it fills, taking the orders placed
        earlier today as filled first. Never raises."""
        if self.tax_unavailable is not None:
            return {"unavailable": self.tax_unavailable}
        try:
            d = self._tax_fill_day
            before = [(d, u, p) for sym, u, p in self._tax_today if sym == symbol]
            if units < 0:
                return tax_sell_note(self.tax_ledger.check_sell(symbol, -units, px, d, before=before), px, d)
            return tax_buy_note(self.tax_ledger.check_buy(symbol, units, d, before=before), d)
        except Exception as exc:
            return {"unavailable": f"{type(exc).__name__}: {str(exc)[:200]}"}

    def _tax_block(self, day) -> dict:
        """The year's realised tax result so far, for the day's summary, and what today's placed orders would do to
        each other once filled. Never raises."""
        if self.tax_unavailable is not None:
            return {"unavailable": self.tax_unavailable}
        d = str(pd.Timestamp(day).date())
        try:
            s = self.tax_ledger.summary(d[:4], as_of=d)
            last = self.tax_ledger.summary(str(int(d[:4]) - 1), as_of=d)
            among = []
            for sym in dict.fromkeys(sym for sym, _, _ in self._tax_today):
                try:
                    among += self.tax_ledger.same_day_interactions([(self._tax_fill_day, x, u, p) for x, u, p in self._tax_today if x == sym])
                except Exception as exc:
                    among.append({"symbol": sym, "unavailable": f"{type(exc).__name__}: {str(exc)[:200]}"})
            return {"year": d[:4], "short_term_net": s["short_term"]["net_gain_loss"], "long_term_net": s["long_term"]["net_gain_loss"],
                    "disallowed_loss": s["total"]["disallowed_loss"], "provisional_loss": s["total"]["provisional_loss"],
                    "provisional_loss_from_last_year": last["total"]["provisional_loss"], "among_todays_orders": among,
                    "as_of": s["as_of"], "unverified_rules": s["unverified_rules"], "note": s["note"]}
        except Exception as exc:
            return {"unavailable": f"{type(exc).__name__}: {str(exc)[:200]}"}

    # ---- decision panel
    def _settle_panel(self, prices: pd.DataFrame) -> None:
        """Score each open panel case whose horizon has passed: was the asset higher (for a buy) than when it was judged?"""
        if self.panel is None:
            return
        still = []
        for case in self.state.get("panel_cases", []):
            later = prices.index[prices.index > pd.Timestamp(case["decided_on"])]
            px = float(prices[case["symbol"]].iloc[-1]) if case["symbol"] in prices.columns else float("nan")
            if len(later) < case["horizon_days"] or not np.isfinite(px):
                still.append(case)
                continue
            outcome = int((px > case["price"]) if case["side"] == "buy" else (px < case["price"]))
            try:
                self.panel.settle(case["case_id"], outcome)
                self.journal.write(prices.index[-1], {"type": "panel_settled", "case_id": case["case_id"], "symbol": case["symbol"],
                                                      "thesis_right": bool(outcome), "price_then": case["price"], "price_now": px})
            except Exception as exc:
                self.journal.write(prices.index[-1], {"type": "panel_settled", "case_id": case["case_id"], "error": f"{type(exc).__name__}: {exc}"})
        self.state["panel_cases"] = still

    def _consult_panel(self, day, strategy: Strategy, symbol: str, prices: pd.DataFrame, order: dict, account: dict, px: float) -> dict:
        """Put one risk-adding order to the panel with facts computed in code. Never raises."""
        direction = "higher" if order["side"] == "buy" else "lower"
        try:
            facts = dict((self._facts or diagnostics.order_facts)(prices, symbol))
        except Exception as exc:
            facts = {"symbol": symbol, "facts_unavailable": f"{type(exc).__name__}: {exc}"}
        facts.update({"strategy": strategy.name, "order_side": order["side"], "order_value_pct_of_equity": order["value"] / account["equity"] * 100.0,
                      "account_drawdown_pct": account["drawdown_pct"], "open_positions": account["open_positions"], "risk_limits_breached": False})
        case_id = f"{strategy.name}:{symbol}:{pd.Timestamp(day).date()}"
        case = {"thesis": f"{symbol} closes {direction} over the next {self.panel_horizon} trading days", "facts": facts}
        try:
            d = self.panel.evaluate(case_id, case)
        except Exception as exc:
            return {"available": False, "error": f"{type(exc).__name__}: {str(exc)[:160]}", "mode": self.panel_mode}
        answered = [v for v in d.verdicts if v.error is None]
        if not answered:
            return {"available": False, "error": "no panel member answered", "mode": self.panel_mode}
        self.state["panel_cases"].append({"case_id": case_id, "symbol": symbol, "side": order["side"], "price": px,
                                          "decided_on": str(pd.Timestamp(day).date()), "horizon_days": self.panel_horizon})
        return {"available": True, "mode": self.panel_mode, "approve": bool(d.approve), "probability": d.probability, "vetoed": bool(d.vetoed),
                "dissent": d.dissent, "reasons": list(d.reasons)[:4],
                "members": {v.member: (v.probability if v.error is None else f"error: {v.error[:60]}") for v in d.verdicts}}

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
        self._settle_panel(prices)
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
        if self.tax_rules is not None:
            self._tax_today, self._tax_fill_day = [], next_session(day)
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
                # the position limit and the position count are the account's: what this strategy and the others hold together
                order = {"symbol": sym, "side": "buy" if delta > 0 else "sell", "value": value, "reduces_risk": bool(reduces),
                         "adds_to_existing": sym in self.funds_held(),
                         "position_value_after": abs(self._units_elsewhere(s.name, sym) + target) * px}
                ok, why = risk_gate.check_order(order, account, limits, latch_file=self.latch_file, halt_file=self.halt_file)
                panel_view = None
                if ok and not reduces and self.panel_mode != "off":
                    panel_view = self._consult_panel(day, s, sym, prices, order, account, px)
                    if self.panel_mode == "veto" and not (panel_view["available"] and panel_view["approve"]):
                        ok = False
                        why = ("the panel could not be reached; in veto mode that blocks new risk" if not panel_view["available"]
                               else "the panel refused: " + "; ".join(panel_view["reasons"] or ["below its approval threshold"]))
                ev = latest.get(sym)
                if ev is not None and ev["date"] == day:
                    why_now = f"{ev['action']}: {ev['reason']}"
                elif ev is not None:
                    why_now = f"catching up with the strategy's book ({ev['action']} on {ev['date'].date()}: {ev['reason']})"
                else:
                    why_now = "rebalance to the strategy's target weight"
                reasoning = f"{why_now}; target weight {float(weights[sym].iloc[-1]):+.3f} of the strategy's capital"
                entry = {"type": "decision", "hypothesis_id": hid, "strategy": s.name, "version": s.version, "symbol": sym,
                         "market_conditions": cond, "order": order, "account": dict(account),
                         "decision": "placed" if ok else "blocked", "risk_check": why, "reasoning": reasoning}
                if panel_view is not None:
                    entry["panel"] = panel_view
                if self.tax_rules is not None:
                    entry["tax"] = self._tax_note(day, sym, delta, px)
                    if ok:
                        self._tax_today.append((sym, float(delta), float(px)))
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
                   "strategy_value_change": by_strategy, "capital_per_strategy": share,
                   "benchmark_values": self._benchmark_values(prices, strategies, share)}
        if self.tax_rules is not None:
            summary["tax"] = self._tax_block(day)
        self.state["history"].append(summary)
        self.state["last_day"] = str(day.date())
        self.journal.write(day, {"type": "summary", **summary})
        self._save()
        return summary

    def _benchmark_values(self, prices: pd.DataFrame, strategies: dict[int, Strategy], share: float) -> dict[str, float]:
        """What simply holding each strategy's funds would have made, in dollars, since its first orders filled.

        The same comparator the research gate uses (`buy_and_hold`: equal weight in every fund with a price, rebalanced
        daily, no costs), started with the capital the strategy had on its first paper day. It is a yardstick, not a book:
        it places no orders, meets no risk check and pays no costs. Only the start day and capital are stored; the value
        is recomputed from prices each day. A book saved before this existed takes both from its own history."""
        books, out = self.state.setdefault("benchmarks", {}), {}
        for s in strategies.values():
            if s.name not in books:
                began = next((h for h in self.state["history"] if s.name in h.get("strategy_values", {})), None)
                books[s.name] = {"start_day": began["day"] if began else str(prices.index[-1].date()),
                                 "capital": float(began["capital_per_strategy"] if began else share)}
            funds = [m for m in s.market if m in prices.columns]
            since = prices.index[prices.index > pd.Timestamp(books[s.name]["start_day"])]   # since[0] is the day its first orders filled
            if len(since) < 2 or not funds:
                out[s.name] = 0.0
                continue
            held = buy_and_hold(prices.loc[since[0]:, funds])
            out[s.name] = float(books[s.name]["capital"] * (np.prod(1.0 + held.iloc[1:]) - 1.0))
        return out

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
