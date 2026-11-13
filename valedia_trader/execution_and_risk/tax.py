"""The account's tax lots: what each sale realised, short-term or long-term, and what a wash sale took away.

Taxes belong to the account, not to a strategy, so lots are kept per symbol across every strategy. The
ledger replays the paper journal's fills in date order and keeps:

* lots         a buy opens a lot (units, purchase date, basis = units x price + fee); a sale closes the
               oldest units first, and its proceeds (units x price - fee) are shared out by units
* term         a closed portion is long-term when the sale date is later than the lot's holding start
               plus the rules file's number of years, else short-term
* wash sales   a loss on a portion is disallowed for each replacement unit bought within the rules file's
               window around the sale; the disallowed loss moves into the replacement units' basis and the
               days the sold units were held move their holding start earlier. A replacement bought after
               the sale is matched when it arrives, so a loss stays provisional until the window has passed.
               A replacement is a different purchase from the one the sold units came from: each buy fill
               is one purchase, and every lot split off from it keeps that purchase's identity. Whether the
               still-held rest of the same purchase counts is itself a rule in the file
               (`wash_sale_same_purchase.remainder_is_replacement`). When a wash sale splits a lot, the
               part matched as a replacement (higher basis, earlier holding start) sits before the remainder
               of the same purchase, so a later sale closes the replacement part first

Every legal parameter comes from `config/tax_rules.json`; this module holds none of its own. Each report
and check says which of those rules are still unverified, the date it is computed as of, and that it is
computed from the rules file and is not tax advice.

Out of scope, by design: short sales (a fill that would take a position below zero raises), options,
corporate actions (splits, mergers, spin-offs), and "substantially identical" securities that trade under
different symbols (only the same symbol is matched). Tax rates are not modelled. Where the rules file
does not say, the ledger takes the more conservative reading: a holding start on 29 February reaches its
anniversary on 1 March, and a loss is provisional through the last day of its window, the 30th day.

    python -m valedia_trader.execution_and_risk.tax report [--journal DIR] [--period 2026] [--as-of 2026-11-11]
    python -m valedia_trader.execution_and_risk.tax check --symbol XLE --side sell --units 10 --price 60 [--day DATE]
    python -m valedia_trader.execution_and_risk.tax check --symbol XLE --side buy --units 10 [--day DATE]
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

from valedia_trader.research_loop import LoopPaths

__all__ = ["TaxRules", "TaxLedger", "NOTE", "main"]

NOTE = "Computed from the rules file; not tax advice."
EPS = 1e-9                                    # units this close to zero are zero
REQUIRED_KEYS = (("jurisdiction",), ("long_term", "after_years"), ("wash_sale", "window_days"), ("wash_sale", "applies_to"),
                 ("wash_sale_same_purchase", "remainder_is_replacement"), ("lot_method", "method"))
PERIOD = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")


def _today() -> date:
    return date.today()


def _day(value) -> date:
    if isinstance(value, date):
        return date(value.year, value.month, value.day)
    return date.fromisoformat(str(value)[:10])


def _plus_years(d: date, years: int) -> date:
    """The same calendar day `years` later; 29 February goes to 1 March in a year without one (the later date)."""
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return date(d.year + years, 3, 1)


@dataclass(frozen=True)
class TaxRules:
    """The legal parameters, read from the rules file and nowhere else."""
    raw: dict
    path: str | None = None

    @classmethod
    def load(cls, path: str | Path | None = None) -> "TaxRules":
        path = Path(path) if path is not None else LoopPaths().config / "tax_rules.json"
        rules = cls(json.loads(Path(path).read_text()), str(path))
        for keys in REQUIRED_KEYS:                    # every key the ledger reads, refused at load when absent
            node = rules.raw
            for k in keys:
                if not isinstance(node, dict) or k not in node:
                    raise ValueError(f"the rules file has no {'.'.join(keys)}")
                node = node[k]
        if rules.lot_method != "FIFO":
            raise ValueError(f"only the FIFO lot method is implemented; the rules file says {rules.lot_method!r}")
        return rules

    @property
    def jurisdiction(self) -> str: return self.raw["jurisdiction"]
    @property
    def long_term_years(self) -> int: return int(self.raw["long_term"]["after_years"])
    @property
    def window_days(self) -> int: return int(self.raw["wash_sale"]["window_days"])
    @property
    def applies_to(self) -> dict: return dict(self.raw["wash_sale"]["applies_to"])
    @property
    def lot_method(self) -> str: return self.raw["lot_method"]["method"]
    @property
    def remainder_is_replacement(self) -> bool: return bool(self.raw["wash_sale_same_purchase"]["remainder_is_replacement"])

    def unverified(self) -> list[str]:
        """Names of the rules whose `verified_on` is still null, in the file's order."""
        return [k for k, v in self.raw.items() if isinstance(v, dict) and "verified_on" in v and v["verified_on"] is None]

    def rules_as_of(self) -> str | None:
        """The date the whole set is verified as of: the oldest `verified_on`, or None while any rule is unverified."""
        dates = [v["verified_on"] for v in self.raw.values() if isinstance(v, dict) and "verified_on" in v]
        return None if (not dates or any(d is None for d in dates)) else min(str(d) for d in dates)

    def washes(self, asset_class: str) -> bool:
        if asset_class not in self.applies_to:
            raise ValueError(f"asset class {asset_class!r} is not in the rules file's wash_sale.applies_to "
                             f"({sorted(self.applies_to)}); it is out of scope for this ledger")
        return bool(self.applies_to[asset_class])

    def stamp(self) -> dict:
        return {"rules_as_of": self.rules_as_of(), "unverified_rules": self.unverified(), "rules_file": self.path,
                "note": NOTE}


def asset_class_of(symbol: str, asset_class: str | None = None) -> str:
    if asset_class:
        return str(asset_class)
    return "crypto" if symbol.endswith("-USD") else "security"


@dataclass
class _Lot:
    id: str
    symbol: str
    asset_class: str
    units: float
    basis: float
    purchase_date: date
    holding_start: date
    purchase_id: str                           # the buy fill it came from; kept by every lot split off from it
    used_as_replacement: bool = False
    wash_adjustment: float = 0.0
    replaces: list = field(default_factory=list)

    def split(self, units: float, new_id: str) -> "_Lot":
        """Cut `units` off into a new lot with its share of the basis; this lot keeps the rest."""
        part = copy.deepcopy(self)
        part.id, part.units, part.basis = new_id, units, self.basis * units / self.units
        self.units, self.basis = self.units - units, self.basis - part.basis
        return part


@dataclass
class _Sale:
    id: str
    symbol: str
    asset_class: str
    sale_date: date
    units: float
    proceeds: float
    basis: float
    lot_id: str
    purchase_id: str
    purchase_date: date
    holding_start: date
    term: str
    washable: bool
    matches: list = field(default_factory=list)

    @property
    def gain(self) -> float: return self.proceeds - self.basis
    @property
    def matched_units(self) -> float: return sum(m["units"] for m in self.matches)
    @property
    def disallowed(self) -> float: return sum(m["disallowed_loss"] for m in self.matches)
    @property
    def loss_per_unit(self) -> float: return max(0.0, -self.gain / self.units)
    @property
    def held_days(self) -> int: return (self.sale_date - self.holding_start).days

    def open_loss_units(self) -> float:
        """Loss units not yet matched to a replacement (zero for a gain or an asset class that is not washed)."""
        if not self.washable or self.gain >= -EPS:
            return 0.0
        left = self.units - self.matched_units
        return left if left > EPS else 0.0


class TaxLedger:
    """FIFO lots and realised sales for one account, fed fills in date order."""

    def __init__(self, rules: TaxRules) -> None:
        self.rules = rules
        self._lots: dict[str, list[_Lot]] = {}
        self._sales: list[_Sale] = []
        self._fills: list[tuple] = []
        self._next = 0
        self._nsales = 0

    # ---- building
    @classmethod
    def from_fills(cls, fills: Iterable[dict], rules: TaxRules) -> "TaxLedger":
        """Replay journal entries: only `type: fill` entries count (an entry without a type is taken as a fill),
        in date order, keeping the order of arrival within a day."""
        rows = [f for f in fills if f.get("type", "fill") == "fill"]
        led = cls(rules)
        for f in sorted(rows, key=lambda f: str(f["day"])[:10]):
            led.add_fill(f["day"], f["symbol"], float(f["units"]), float(f["price"]), float(f.get("fee") or 0.0),
                         f.get("asset_class"))
        return led

    @property
    def last_day(self) -> date | None:
        return self._fills[-1][0] if self._fills else None

    def _id(self, symbol: str) -> str:
        self._next += 1
        return f"{symbol}-{self._next}"

    def add_fill(self, day, symbol: str, units: float, price: float, fee: float = 0.0, asset_class: str | None = None) -> dict:
        """Apply one fill (positive units buy, negative sell). Returns what it did: the portions a sale closed,
        or the earlier loss sales a buy was matched to as a replacement."""
        d, units, price, fee = _day(day), float(units), float(price), float(fee or 0.0)
        cls = asset_class_of(symbol, asset_class)
        washable = self.rules.washes(cls)
        if abs(units) <= EPS:
            raise ValueError(f"a fill needs nonzero units ({symbol} on {d})")
        if self.last_day is not None and d < self.last_day:
            raise ValueError(f"fills must arrive in date order: {symbol} on {d} came after a fill on {self.last_day}")
        lots = self._lots.setdefault(symbol, [])
        if units < 0:
            held = sum(lot.units for lot in lots)
            if -units > held + EPS:
                raise ValueError(f"selling {-units:g} {symbol} on {d} with {held:g} held would leave the position short; "
                                 "short sales are out of scope")
            out = {"closed": self._close(lots, d, symbol, cls, washable, -units, price, fee)}
        else:
            lot_id = self._id(symbol)
            lot = _Lot(lot_id, symbol, cls, units, units * price + fee, d, d, purchase_id=lot_id)
            lots.append(lot)
            out = {"matches": self._match_new_buy(lots, lot, d) if washable else [], "purchase_id": lot_id}
        self._fills.append((d, symbol, units, price, fee, asset_class))
        return out

    def _close(self, lots: list[_Lot], d: date, symbol: str, cls: str, washable: bool, units: float, price: float,
               fee: float) -> list[_Sale]:
        per_unit = (units * price - fee) / units
        left, closed = units, []
        while left > EPS:
            lot = lots[0]
            take = lot.units if lot.units <= left + EPS else left
            basis = lot.basis * take / lot.units
            long_from = _plus_years(lot.holding_start, self.rules.long_term_years)
            self._nsales += 1
            closed.append(_Sale(f"sale-{self._nsales}", symbol, cls, d, take, per_unit * take, basis,
                                lot.id, lot.purchase_id, lot.purchase_date, lot.holding_start, "long" if d > long_from else "short",
                                washable))
            lot.units, lot.basis = lot.units - take, lot.basis - basis
            if lot.units <= EPS:
                lots.pop(0)
            left -= take
        self._sales += closed
        lo = d - timedelta(days=self.rules.window_days)
        for sale in closed:                                       # replacements already held: earliest purchase first
            i = 0
            while sale.open_loss_units() > EPS and i < len(lots):
                lot = lots[i]
                same = lot.purchase_id == sale.purchase_id and not self.rules.remainder_is_replacement
                if not lot.used_as_replacement and not same and lo <= lot.purchase_date <= d:
                    if lot.units > sale.open_loss_units() + EPS:
                        part = lot.split(sale.open_loss_units(), self._id(symbol))
                        lots.insert(i, part)
                        lot = part
                    self._replace(sale, lot)
                i += 1
        return closed

    def _match_new_buy(self, lots: list[_Lot], lot: _Lot, d: date) -> list[dict]:
        """Earlier loss sales of the symbol inside the window, with loss units still unmatched, take this buy's units."""
        lo, made = d - timedelta(days=self.rules.window_days), []
        for sale in self._sales:
            if sale.symbol != lot.symbol or not (lo <= sale.sale_date <= d) or sale.open_loss_units() <= EPS:
                continue
            if lot.used_as_replacement or lot.units <= EPS:
                break
            target = lot
            if lot.units > sale.open_loss_units() + EPS:
                target = lot.split(sale.open_loss_units(), self._id(lot.symbol))
                lots.insert(next(i for i, x in enumerate(lots) if x is lot), target)
            made.append(self._replace(sale, target))
        return made

    def _replace(self, sale: _Sale, lot: _Lot) -> dict:
        disallowed = sale.loss_per_unit * lot.units
        lot.basis += disallowed
        lot.wash_adjustment += disallowed
        lot.holding_start -= timedelta(days=sale.held_days)
        lot.used_as_replacement = True
        match = {"replacement_lot": lot.id, "purchase_date": lot.purchase_date.isoformat(), "units": lot.units,
                 "disallowed_loss": disallowed, "holding_days_added": sale.held_days, "sale_id": sale.id,
                 "sale_date": sale.sale_date.isoformat(), "loss_per_unit": sale.loss_per_unit, "purchase_id": lot.purchase_id}
        sale.matches.append(match)
        lot.replaces.append({"sale_id": sale.id, "sale_date": sale.sale_date.isoformat(), "disallowed_loss": disallowed})
        return match

    def _at(self, as_of) -> tuple["TaxLedger", date]:
        """This ledger as it stood at the end of `as_of` (default: the last fill's day, or today with no fills)."""
        d = _day(as_of) if as_of is not None else (self.last_day or _today())
        if self.last_day is None or d >= self.last_day:
            return self, d
        led = TaxLedger(self.rules)
        for f in self._fills:
            if f[0] <= d:
                led.add_fill(*f)
        return led, d

    # ---- reading
    def _provisional(self, sale: _Sale, as_of: date) -> bool:
        return sale.open_loss_units() > EPS and as_of <= sale.sale_date + timedelta(days=self.rules.window_days)

    def _record(self, sale: _Sale, as_of: date) -> dict:
        open_loss = sale.open_loss_units() * sale.loss_per_unit
        return {"id": sale.id, "symbol": sale.symbol, "asset_class": sale.asset_class, "sale_date": sale.sale_date.isoformat(),
                "lot_id": sale.lot_id, "purchase_id": sale.purchase_id, "purchase_date": sale.purchase_date.isoformat(),
                "holding_start": sale.holding_start.isoformat(), "term": sale.term, "units": sale.units,
                "proceeds": sale.proceeds, "cost_basis": sale.basis, "gain_loss": sale.gain,
                "disallowed_loss": sale.disallowed, "allowed_gain_loss": sale.gain + sale.disallowed,
                "wash_sales": [dict(m) for m in sale.matches], "wash_sale_applies": sale.washable,
                "provisional": self._provisional(sale, as_of), "unmatched_loss": open_loss,
                "provisional_until": (sale.sale_date + timedelta(days=self.rules.window_days)).isoformat()}

    def realized(self, start=None, end=None, as_of=None) -> list[dict]:
        """One record per closed lot portion with its sale date in [start, end]; `provisional` is judged as of
        `as_of` (default the last fill's day)."""
        led, d = self._at(as_of)
        lo, hi = (_day(start) if start else date.min), (_day(end) if end else date.max)
        return [led._record(s, d) for s in led._sales if lo <= s.sale_date <= hi and s.sale_date <= d]

    def open_lots(self, symbol: str | None = None) -> list[dict]:
        out = []
        for sym in sorted(self._lots) if symbol is None else [symbol]:
            for lot in self._lots.get(sym, []):
                out.append({"id": lot.id, "purchase_id": lot.purchase_id, "symbol": lot.symbol, "asset_class": lot.asset_class, "units": lot.units,
                            "cost_basis": lot.basis, "basis_per_unit": lot.basis / lot.units,
                            "purchase_date": lot.purchase_date.isoformat(), "holding_start": lot.holding_start.isoformat(),
                            "long_term_after": _plus_years(lot.holding_start, self.rules.long_term_years).isoformat(),
                            "wash_adjustment": lot.wash_adjustment, "used_as_replacement": lot.used_as_replacement,
                            "replaces": [dict(r) for r in lot.replaces]})
        return out

    def summary(self, period: str, as_of=None) -> dict:
        """Realised gain and loss for a year ("2026"), month ("2026-11") or day ("2026-11-11"), by term."""
        if not PERIOD.match(str(period)):
            raise ValueError(f"period must be YYYY, YYYY-MM or YYYY-MM-DD, not {period!r}")
        led, d = self._at(as_of)
        recs = [led._record(s, d) for s in led._sales if s.sale_date.isoformat().startswith(period) and s.sale_date <= d]

        def total(rows: list[dict]) -> dict:
            return {"count": len(rows), "proceeds": sum(r["proceeds"] for r in rows),
                    "cost_basis": sum(r["cost_basis"] for r in rows), "gain_loss": sum(r["gain_loss"] for r in rows),
                    "disallowed_loss": sum(r["disallowed_loss"] for r in rows),
                    "net_gain_loss": sum(r["allowed_gain_loss"] for r in rows),
                    "provisional_loss": sum(r["unmatched_loss"] for r in rows if r["provisional"])}

        return {"period": period, "as_of": d.isoformat(),
                "short_term": total([r for r in recs if r["term"] == "short"]),
                "long_term": total([r for r in recs if r["term"] == "long"]), "total": total(recs),
                "provisional_records": sum(r["provisional"] for r in recs), "records": recs, **self.rules.stamp()}

    def _at_risk(self, symbol: str, d: date) -> list[_Sale]:
        w = timedelta(days=self.rules.window_days)
        return [s for s in self._sales if s.symbol == symbol and s.open_loss_units() > EPS and s.sale_date <= d <= s.sale_date + w]

    def _clean_from(self, symbol: str, d: date) -> str:
        """The first day a purchase of `symbol` would be matched to no earlier loss sale."""
        risk = self._at_risk(symbol, d)
        return (max(s.sale_date for s in risk) + timedelta(days=self.rules.window_days + 1)).isoformat() if risk else d.isoformat()

    def wash_sale_windows(self, as_of) -> dict:
        """Per symbol, the loss a purchase made now would disallow and the first day buying is clean."""
        led, d = self._at(as_of)
        out = {}
        for sym in sorted({s.symbol for s in led._sales}):
            risk = led._at_risk(sym, d)
            if risk:
                out[sym] = {"loss_at_risk": sum(s.open_loss_units() * s.loss_per_unit for s in risk),
                            "clean_from": led._clean_from(sym, d),
                            "sales": [{"day": s.sale_date.isoformat(), "units": s.open_loss_units(),
                                       "loss": s.open_loss_units() * s.loss_per_unit} for s in risk]}
        return out

    # ---- pre-trade checks (on a copy; the ledger itself is never changed)
    def _sim(self, symbol: str, d: date, before=()) -> "TaxLedger":
        """A scratch copy holding only what a check of `symbol` on `d` can touch: its open lots and its sales inside
        the window, with `before` ((day, units, price[, fee]) fills of the symbol, in order) applied to it. Cheaper than
        copying the whole ledger, and the ledger itself is never changed."""
        sim = TaxLedger(self.rules)
        first = min([d] + [_day(f[0]) for f in before])
        lo = first - timedelta(days=self.rules.window_days)
        sim._lots = {symbol: copy.deepcopy(self._lots.get(symbol, []))}
        sim._sales = copy.deepcopy([x for x in self._sales if x.symbol == symbol and x.sale_date >= lo])
        sim._fills, sim._next, sim._nsales = self._fills[-1:], self._next, self._nsales
        for f in before:
            sim.add_fill(f[0], symbol, f[1], f[2], f[3] if len(f) > 3 else 0.0)
        return sim

    def same_day_interactions(self, fills) -> list[dict]:
        """What a day's orders would do to each other once all have filled: `fills` are (day, symbol, units, price)
        in the order decided. Per symbol, the loss that a sale among them would have disallowed by a purchase among
        them (either order). Symbols where nothing interacts are left out. The ledger itself is never changed."""
        out, symbols = [], list(dict.fromkeys(f[1] for f in fills))
        for sym in symbols:
            mine = [f for f in fills if f[1] == sym]
            sim = self._sim(sym, min(_day(f[0]) for f in mine))
            bought, sold, sales = set(), 0.0, []
            for day, _, units, price in mine:
                done = sim.add_fill(day, sym, units, price)
                if units > 0:
                    bought.add(done["purchase_id"])
                else:
                    sold += -float(units)
                    sales += done["closed"]
            # a sale keeps every match made against it, whether the purchase came before or after it
            washed = sum(m["disallowed_loss"] for x in sales for m in x.matches if m["purchase_id"] in bought)
            if washed > EPS:
                out.append({"symbol": sym, "sale_units": sold, "buy_units": sum(float(f[2]) for f in mine if f[2] > 0),
                            "disallowed_loss": washed})
        return out

    def _check_day(self, side: str, symbol: str, day) -> date:
        d = _day(day)
        if self.last_day is not None and d < self.last_day:
            raise ValueError(f"cannot check a {side} of {symbol} on {d}: the ledger already holds fills through "
                             f"{self.last_day}, so a check can only be for that day or later")
        return d

    def check_sell(self, symbol: str, units: float, price: float, day, fee: float = 0.0, asset_class: str | None = None,
                   before=()) -> dict:
        """What selling `units` of `symbol` at `price` on `day` would realise, by term, and what a wash sale would take.
        `before`: (day, units, price) fills of the symbol taken to happen first (orders placed earlier the same day)."""
        d = self._check_day("sell", symbol, day)
        sim = self._sim(symbol, d, before)
        held = sum(lot.units for lot in sim._lots.get(symbol, []))
        sales = sim.add_fill(d, symbol, -abs(float(units)), price, fee, asset_class)["closed"]
        recs = [sim._record(s, d) for s in sales]
        by = lambda key, term: sum(r[key] for r in recs if r["term"] == term)
        risk = sum(s.open_loss_units() * s.loss_per_unit for s in sales)
        return {"symbol": symbol, "side": "sell", "units": abs(float(units)), "price": float(price), "day": d.isoformat(),
                "held_units": held,
                "proceeds": sum(r["proceeds"] for r in recs), "cost_basis": sum(r["cost_basis"] for r in recs),
                "gain_loss": {"short_term": by("gain_loss", "short"), "long_term": by("gain_loss", "long")},
                "allowed_gain_loss": {"short_term": by("allowed_gain_loss", "short"), "long_term": by("allowed_gain_loss", "long")},
                "disallowed_loss": sum(r["disallowed_loss"] for r in recs),
                "wash_sale": {"applies": bool(sales and sales[0].washable),
                              "matches": [m for r in recs for m in r["wash_sales"]],
                              "loss_at_risk": risk, "clean_from": sim._clean_from(symbol, d)},
                "portions": recs, "as_of": d.isoformat(), **self.rules.stamp()}

    def check_buy(self, symbol: str, units: float, day, asset_class: str | None = None, before=()) -> dict:
        """What buying `units` of `symbol` on `day` would do: which earlier loss sales it would wash, how much loss
        that disallows, and the first day a purchase would wash nothing. `before` as for `check_sell`."""
        d = self._check_day("buy", symbol, day)
        sim = self._sim(symbol, d, before)
        clean_from = sim._clean_from(symbol, d)
        matches = sim.add_fill(d, symbol, abs(float(units)), 0.0, 0.0, asset_class)["matches"]   # matching ignores price
        matched = sum(m["units"] for m in matches)
        return {"symbol": symbol, "side": "buy", "units": abs(float(units)), "day": d.isoformat(),
                "wash_sale_applies": self.rules.washes(asset_class_of(symbol, asset_class)),
                "disallowed_loss": sum(m["disallowed_loss"] for m in matches),
                "matches": [{k: m[k] for k in ("sale_date", "units", "loss_per_unit", "disallowed_loss", "holding_days_added")}
                            for m in matches],
                "unmatched_units": abs(float(units)) - matched, "clean_from": clean_from,
                "replacement_for_loss_sales_until": (d + timedelta(days=self.rules.window_days)).isoformat(),
                "as_of": d.isoformat(), **self.rules.stamp()}


def main(argv=None) -> int:
    paths = LoopPaths()
    ap = argparse.ArgumentParser(prog="tax", description="Tax lots, realised gains and wash sales from the paper journal.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    rep = sub.add_parser("report", help="realised gain and loss for a period, by term")
    rep.add_argument("--journal", default=str(paths.journal))
    rep.add_argument("--period", default=None, help="YYYY, YYYY-MM or YYYY-MM-DD (default: the as-of year)")
    rep.add_argument("--as-of", dest="as_of", default=None, help="default: today, or the last fill's day if that is later")
    chk = sub.add_parser("check", help="what a proposed buy or sale would do")
    chk.add_argument("--journal", default=str(paths.journal))
    chk.add_argument("--symbol", required=True)
    chk.add_argument("--side", required=True, choices=("buy", "sell"))
    chk.add_argument("--units", required=True, type=float)
    chk.add_argument("--price", type=float, default=None)
    chk.add_argument("--day", default=None, help="default: today")
    args = ap.parse_args(argv)
    from valedia_trader.research_loop.paper import Journal       # the journal's reader; imported only when needed
    try:
        led = TaxLedger.from_fills(Journal(args.journal).read(), TaxRules.load())
        if args.cmd == "report":
            today = _today()
            as_of = _day(args.as_of) if args.as_of else (today if led.last_day is None or today > led.last_day else led.last_day)
            s = led.summary(args.period or str(as_of.year), as_of=as_of)
            s["wash_sale_windows"] = led.wash_sale_windows(s["as_of"])
            print(json.dumps(s, indent=1))
            return 0
        day = args.day or _today().isoformat()
        if args.side == "sell":
            if args.price is None:
                raise ValueError("a sale check needs --price")
            out = led.check_sell(args.symbol, args.units, args.price, day)
        else:
            out = led.check_buy(args.symbol, args.units, day)
        print(json.dumps(out, indent=1))
        return 0
    except ValueError as e:
        print(f"tax: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
