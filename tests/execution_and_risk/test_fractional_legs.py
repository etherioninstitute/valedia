"""T51 A2: the two legs of one fund on the practice path (R4 with ruling B, R9, R1's residual on the summary).

With `fractional` on, the order step splits each fund's difference with `split_difference` (A1): the whole leg is a
LIMIT order tagged `{batch}-{sym}` as today, the remainder leg a MARKET order by quantity of under one share tagged
`{batch}-{sym}.r`, intended with its collar price c (the price a LIMIT leg would carry) and no limit price. The
remainder leg goes only after its fund's LIMIT leg of the same run was accepted by the venue (or the split has none).
Every leg is one order against max_orders_per_day. The residual is journaled per fund on the summary. With
`fractional` off nothing changes. Live stays refused for fractions before anything is read or written.

Properties over seeded worlds through the real `run_order_step`, with a stand-in venue; nothing here reaches a
network or any file outside the test's temporary folder."""
import json
import math
import random
import sqlite3
from decimal import Decimal

import pytest

from valedia_trader.execution_and_risk import order_step as osx
from test_order_step import Booked, GENEROUS, approve, live_world, paper, propose, ticket_rows, ticket_world

PRICES = [0.37, 3.3, 7.77, 20.0, 55.5, 101.25, 500.0]
SIZES = [0.0, 0.0, 0.004, 0.2, 1.0 / 3, 0.37, 1.0, 1.5, 2.5, 3.99999, 7.3, 12.5, 2.004]
FUNDS = ("SPY", "QQQ", "IWM", "TLT")
NEW_FIELDS = ("order_type", "leg", "collar_price", "residuals")


class LegVenue(Booked):
    """Booked, keeping every submit's own arguments; with an rng it accepts, refuses or rejects at random."""

    def __init__(self, rng=None, refuse=0.0, **kw):
        super().__init__(**kw)
        self.rng, self.refuse = rng, refuse
        self.args = []

    def submit(self, symbol, units, tag, price=None, limit_price=None, session="CORE"):
        self.args.append({"symbol": symbol, "units": units, "tag": tag, "price": price, "limit_price": limit_price,
                          "session": session})
        if self.rng is not None:
            r = self.rng.random()
            self.submit_mode = "refuse" if r < self.refuse / 2 else "reject" if r < self.refuse else "accept"
        return super().submit(symbol, units, tag, price, limit_price, session)


def leg_world(tmp, rng=None, refuse=0.0, held=None, cap=40, fill=0.0):
    v = LegVenue(rng, refuse, held=held or {})
    v.fill_at_once = fill
    w = ticket_world(tmp, v)
    w.limits.write_text(json.dumps({**GENEROUS, "max_orders_per_day": cap}))
    return w


def random_paper(rng):
    return paper({s: {sym: float(rng.choice(SIZES)) for sym in rng.sample(FUNDS, rng.randint(1, 4))} for s in ("a", "b")})


def batch_orders(w, batch):
    return [o for o in w.orders() if o["batch"] == batch]


def book_without(w, batch):
    """The ledger's own book as the run used it: every fill but this batch's own (the stand-in fills only at submit)."""
    return osx.broker_book(f for f in w.fills() if not f["tag"].startswith(batch + "-"))


def todays_units(target, owned):
    """The units today's order step sends for one fund with `fractional` on (order_step._differences at 1919a07)."""
    units = osx.round_units(target - owned, True)
    if units < 0:
        units = osx.round_units(max(units, -max(owned, 0.0)), True)
    return units


def dec(x):
    return Decimal(repr(float(x)))


def legs_of(out, sym):
    """Every leg of `sym` the run worked on, whatever became of it: sent, left over, refused or dropped."""
    return [e for key in ("sent", "left_over", "refused", "dropped") for e in out.get(key, []) if e["symbol"] == sym]


def check_shape(w, out):
    """R4: every order of the run is a LIMIT of whole units tagged {batch}-{sym}, or a MARKET of under one unit tagged
    {batch}-{sym}.r with its collar and no limit; the venue was handed exactly that."""
    batch = out["batch"]
    args = {a["tag"]: a for a in w.venue.args}
    n = 0
    for o in batch_orders(w, batch):
        sym, u = o["symbol"], o["units"]
        if o["tag"] == f"{batch}-{sym}":
            assert o["order_type"] == "LIMIT" and o["collar_price"] is None and o["limit_price"] is not None, o
            assert u == math.trunc(u) and abs(u) >= 1, o
            assert o["limit_price"] == osx.limit_price(u, o["reference_price"], u < 0), o
        else:
            assert o["tag"] == f"{batch}-{sym}.r", o
            assert o["order_type"] == "MARKET" and o["limit_price"] is None and 0 < abs(u) < 1, o
            assert o["collar_price"] == osx.limit_price(u, o["reference_price"], u < 0), o
        if o["tag"] in args:
            a = args[o["tag"]]
            assert a["units"] == u and a["session"] == "CORE" and a["price"] == o["reference_price"], (a, o)
            assert a["limit_price"] == o["limit_price"], (a, o)        # MARKET: None; LIMIT: its limit
        n += 1
    return n


def check_fence(w, out, book):
    """No order of the run sells more of a fund than the ledger's own book of it, across both legs."""
    sold = {}
    for o in batch_orders(w, out["batch"]):
        if o["units"] < 0:
            sold[o["symbol"]] = sold.get(o["symbol"], Decimal(0)) + dec(-o["units"])
    for sym, s in sold.items():
        assert s <= dec(book.get(sym, 0.0)), (sym, s, book)


def check_gate(w, out):
    """No remainder leg is handed to the venue unless its fund's LIMIT leg of the same run was accepted before it,
    or its fund had no whole leg this run."""
    batch = out["batch"]
    order = [a["tag"] for a in w.venue.args]
    recs = {o["tag"]: o for o in batch_orders(w, batch)}
    whole_wanted = {e["symbol"] for key in ("sent", "left_over", "refused", "dropped") for e in out.get(key, [])
                    if e.get("leg") == "whole"}
    whole_wanted |= {e["symbol"] for e in out.get("skipped", []) if e.get("leg") == "whole"}
    for i, tag in enumerate(order):
        if not (tag.startswith(batch + "-") and tag.endswith(".r")):
            continue
        sym = tag[len(batch) + 1:-2]
        if sym in whole_wanted:
            whole = f"{batch}-{sym}"
            assert whole in order[:i], (tag, order)
            assert recs[whole]["state"] not in ("intended", "refused"), recs[whole]
            assert not any(e["symbol"] == sym and e.get("leg") == "whole" for e in out["left_over"] + out["refused"]), out


# ---- R4: the shape of every order, the sum of the legs and the residual, the fence, over seeded worlds

def test_every_order_of_a_fractional_run_is_a_whole_limit_or_a_market_remainder_and_the_legs_sum_to_todays_difference(
        tmp_path):
    shapes = markets = limits = residuals = funds = 0
    for k in range(40):
        rng = random.Random(51_200 + k)
        held = {"SPY": float(rng.choice([0, 0, 3]))}                     # the owner's: the baseline, never sold
        w = leg_world(tmp_path / f"w{k}", held={s: u for s, u in held.items() if u}, fill=rng.choice([0.0, 0.5, 1.0]))
        for day in range(3):
            w.paper = random_paper(rng)
            for sym in FUNDS:
                w.prices[sym] = rng.choice(PRICES)
            capital = rng.choice([None, None, 1_000.0, 250_000.0])
            out = w.run(fractional=True, capital=capital)
            assert out["status"] == "done", out
            batch = out["batch"]
            shapes += check_shape(w, out)
            book = book_without(w, batch)
            check_fence(w, out, book)
            check_gate(w, out)
            target, _ = osx.targets(w.paper, capital or w.paper.equity)
            skipped = {e["symbol"] for e in out["skipped"]}
            res = {e["symbol"]: e for e in out["residuals"]}
            for sym in sorted(set(target) | set(book)):
                if sym in skipped:
                    continue
                today = todays_units(target.get(sym, 0.0), book.get(sym, 0.0))
                legs = legs_of(out, sym)
                assert all(e.get("leg") in ("whole", "remainder") for e in legs), legs
                got = sum((dec(e["units"]) for e in legs), Decimal(0)) + (dec(res[sym]["units"]) if sym in res else 0)
                assert got == dec(today), (k, day, sym, today, legs, res.get(sym))
                assert len({e["leg"] for e in legs}) == len(legs)          # one leg of each kind at most
                if sym in res:
                    r = res[sym]
                    assert r["units"] != 0 and r["why"] and r["value"] == pytest.approx(abs(r["units"]) * w.prices[sym])
                    residuals += 1
                funds += abs(today) > 0
            markets += sum(1 for o in batch_orders(w, batch) if o["order_type"] == "MARKET")
            limits += sum(1 for o in batch_orders(w, batch) if o["order_type"] == "LIMIT")
            assert "faults" not in out, out                                # a residual is never a fault
            w.next_day()
    assert markets > 40 and limits > 40 and residuals > 10 and funds > 100, (markets, limits, residuals, funds)


def test_a_remainder_worth_less_than_the_minimum_is_a_residual_on_the_summary_never_an_order_or_a_fault(tmp_path):
    w = leg_world(tmp_path / "w")
    w.paper = paper({"a": {"SPY": 2.004, "QQQ": 0.004}})              # 0.004 x 500 = 2 and 0.004 x 400 = 1.60: under 5
    out = w.run(fractional=True)
    assert out["status"] == "done" and "faults" not in out
    assert [(o["tag"].split("-")[-1], o["units"], o["order_type"]) for o in w.orders()] == [("SPY", 2.0, "LIMIT")]
    res = {e["symbol"]: e for e in out["residuals"]}
    assert set(res) == {"QQQ", "SPY"}
    assert res["SPY"]["units"] == 0.004 and res["SPY"]["value"] == pytest.approx(2.0) and "not sent" in res["SPY"]["why"]
    assert res["QQQ"]["units"] == 0.004 and res["QQQ"]["value"] == pytest.approx(1.6) and "taken as zero" in res["QQQ"]["why"]
    summary = w.entries("summary")[-1]
    assert summary["residuals"] == out["residuals"]
    led = w.ledger()
    try:
        assert led.attention() == []                                  # never an attention item
    finally:
        led.close()


def test_two_legs_of_one_fund_carry_two_tags_and_the_journal_keys_order_and_fill_lines_by_leg(tmp_path):
    w = leg_world(tmp_path / "w", fill=1.0)
    w.paper = paper({"a": {"SPY": 2.5}})                              # 2 whole + 0.5 x 500 = 250
    out = w.run(fractional=True)
    b = out["batch"]
    assert [a["tag"] for a in w.venue.args] == [f"{b}-SPY", f"{b}-SPY.r"]
    assert [(a["units"], a["limit_price"]) for a in w.venue.args] == [(2.0, 505.0), (0.5, None)]
    o = {x["tag"]: x for x in w.orders()}
    assert o[f"{b}-SPY.r"]["collar_price"] == 505.0 and o[f"{b}-SPY.r"]["reference_price"] == 500.0
    lines = [(e["tag"], e["order_type"], e["leg"], e["units"], e["limit_price"], e.get("collar_price"))
             for e in w.entries("order")]
    assert lines == [(f"{b}-SPY", "LIMIT", "whole", 2.0, 505.0, None), (f"{b}-SPY.r", "MARKET", "remainder", 0.5, None, 505.0)]
    fills = {e["tag"]: e for e in w.entries("fill")}
    assert (fills[f"{b}-SPY"]["order_type"], fills[f"{b}-SPY"]["leg"]) == ("LIMIT", "whole")
    assert (fills[f"{b}-SPY.r"]["order_type"], fills[f"{b}-SPY.r"]["leg"]) == ("MARKET", "remainder")
    assert list(fills[f"{b}-SPY.r"])[-1] == "verdict"                 # the verdict stays the line's last field
    # A2's seam (A3 judges a MARKET fill against its collar): today's rules leave an order with no limit unjudged
    assert fills[f"{b}-SPY.r"]["verdict"] == "unjudged" and fills[f"{b}-SPY"]["verdict"] == "ok"
    assert out["cap_unjudged"] == ["SPY"] and "faults" not in out
    assert w.book() == {"SPY": 2.5}


def test_a_target_taken_as_zero_sells_the_ledgers_whole_holding_each_strategy_its_own_part(tmp_path):
    w = leg_world(tmp_path / "w", fill=1.0)
    w.prices["IWM"] = 2.0
    w.paper = paper({"a": {"IWM": 1.5}, "b": {"IWM": 0.5}})           # 2 IWM: a LIMIT of 2 (a 1.5, b 0.5)
    w.run(fractional=True)
    assert w.book() == {"IWM": 2.0}
    w.later(30)
    w.paper = paper({"a": {"IWM": 0.3}, "b": {"IWM": 0.2}})           # a target of 0.5, worth 1.00: taken as zero
    out = w.run(fractional=True)
    assert out["status"] == "done" and out["refused"] == [], out
    o = batch_orders(w, out["batch"])
    assert [(x["units"], x["order_type"]) for x in o] == [(-2.0, "LIMIT")]
    assert sorted((p["strategy"], p["units"]) for p in o[0]["parts"]) == [("a", -1.5), ("b", -0.5)]
    assert [(r["symbol"], r["units"]) for r in out["residuals"]] == [("IWM", 0.5)]
    assert "taken as zero" in out["residuals"][0]["why"] and w.book() == {"IWM": 0.0}


def test_the_risk_gate_sees_a_remainder_leg_on_top_of_its_funds_limit_leg_of_the_same_run(tmp_path):
    w = leg_world(tmp_path / "w")
    w.limits.write_text(json.dumps({**GENEROUS, "max_position_pct": 0.01}))   # 1% of 1,000,000: 10,000 in one fund
    w.paper = paper({"a": {"SPY": 20.5, "QQQ": 1.5}})                 # SPY: 20 x 500 = 10,000 passes; 20.5 x 500 does not
    out = w.run(fractional=True)
    assert [(e["symbol"], e["leg"]) for e in out["sent"]] == [("SPY", "whole"), ("QQQ", "whole"), ("QQQ", "remainder")]
    assert [(e["symbol"], e["leg"], e["by"]) for e in out["refused"]] == [("SPY", "remainder", "risk gate")]
    line = [e for e in w.entries("refused") if e["symbol"] == "SPY"]
    assert len(line) == 1 and line[0]["leg"] == "remainder"
    assert line[0]["order"]["position_value_after"] == pytest.approx(20.5 * 500.0) and line[0]["order"]["adds_to_existing"]


# ---- R4: the remainder leg only after its LIMIT leg was accepted; R9: every leg counts, sales first

def test_a_remainder_leg_goes_only_after_its_funds_limit_leg_was_accepted_and_every_leg_counts_against_the_cap(tmp_path):
    held_back = sent_r = cut = 0
    for k in range(60):
        rng = random.Random(52_900 + k)
        cap = rng.choice([1, 2, 3, 4, 6, 40])
        w = leg_world(tmp_path / f"w{k}", rng=rng, refuse=rng.choice([0.0, 0.3, 0.6]), cap=cap,
                      fill=rng.choice([0.0, 1.0]))
        sent_before = 0
        for run in range(2):
            w.paper = random_paper(rng)
            for sym in FUNDS:
                w.prices[sym] = rng.choice(PRICES[3:])
            book = osx.broker_book(w.fills())
            out = w.run(fractional=True)
            assert out["status"] == "done", out
            batch = out["batch"]
            check_shape(w, out)
            check_gate(w, out)
            check_fence(w, out, book_without(w, batch))
            mine = [a for a in w.venue.args if a["tag"].startswith(batch + "-")]
            counted = [o for o in batch_orders(w, batch) if o["state"] not in ("refused", "intended")]
            assert len(counted) + sent_before <= cap                    # each leg is one order against the day's cap
            sent_before += len(counted)
            sides = [a["units"] < 0 for a in mine]
            assert sides == sorted(sides, reverse=True)                 # sales first, as today
            for e in out["left_over"]:
                if e["leg"] == "remainder" and "limit leg" in e["reason"].lower():
                    held_back += 1
                if e["leg"] == "whole" and "cap" in e["reason"]:
                    cut += 1
                    assert not any(a["tag"] == f"{batch}-{e['symbol']}.r" for a in mine)
            sent_r += sum(1 for a in mine if a["tag"].endswith(".r"))
            w.later(30)
    assert held_back > 10 and sent_r > 20 and cut > 5, (held_back, sent_r, cut)


def test_a_refused_limit_leg_holds_its_remainder_back_in_words(tmp_path):
    w = leg_world(tmp_path / "w")
    w.venue.submit_mode = ["refuse"]
    w.paper = paper({"a": {"SPY": 2.5, "QQQ": 1.5}})
    out = w.run(fractional=True)
    b = out["batch"]
    assert [a["tag"] for a in w.venue.args] == [f"{b}-SPY", f"{b}-QQQ", f"{b}-QQQ.r"]
    left = [e for e in out["left_over"] if e["symbol"] == "SPY"]
    assert len(left) == 1 and left[0]["leg"] == "remainder" and left[0]["units"] == 0.5
    assert "LIMIT leg" in left[0]["reason"] and "not sent in this run" in left[0]["reason"]
    assert [e for e in w.entries("left_over") if e["symbol"] == "SPY"][0]["leg"] == "remainder"


def test_the_cap_cuts_a_limit_leg_and_its_remainder_is_never_sent(tmp_path):
    w = leg_world(tmp_path / "w", cap=1)
    w.paper = paper({"a": {"SPY": 2.5, "QQQ": 3.5}})                  # QQQ 1200 + 200, SPY 1000 + 250: QQQ's LIMIT first
    out = w.run(fractional=True)
    b = out["batch"]
    assert [a["tag"] for a in w.venue.args] == [f"{b}-QQQ"]
    left = {(e["symbol"], e["leg"]): e["reason"] for e in out["left_over"]}
    assert set(left) == {("QQQ", "remainder"), ("SPY", "whole"), ("SPY", "remainder")}
    assert "cap of 1 orders" in left[("QQQ", "remainder")] and "cap of 1 orders" in left[("SPY", "whole")]
    assert "LIMIT leg" in left[("SPY", "remainder")]


def test_a_propose_counts_every_leg_against_the_cap_and_writes_the_collar_as_the_remainder_lines_limit(tmp_path):
    w = leg_world(tmp_path / "w", cap=3)
    w.paper = paper({"a": {"SPY": 2.5, "QQQ": 1.25}})
    out = propose(w, fractional=True)
    lines = out["ticket"]["orders"]
    assert [(ln["fund"], ln["leg"], ln["units"], ln["limit"]) for ln in lines] == [    # by value: 1000, 400, 250, (100)
        ("SPY", "whole", 2.0, 505.0), ("QQQ", "whole", 1.0, 404.0), ("SPY", "remainder", 0.5, 505.0)]
    rows = ticket_rows(w, "lines")
    assert [(r["symbol"], r["units"], r["limit_price"]) for r in rows] == [("SPY", 2.0, 505.0), ("QQQ", 1.0, 404.0),
                                                                          ("SPY", 0.5, 505.0)]
    assert set(rows[0]) == {"day", "number", "line", "symbol", "side", "units", "quote", "limit_price", "value"}
    assert [(e["symbol"], e["leg"]) for e in out["left_over"]] == [("QQQ", "remainder")]


# ---- ruling B: the ticket keeps its layout; a ticket proposed and approved yields exactly the proposed legs

def _lines_of(out):
    return sorted((ln["fund"], ln["leg"], ln["units"]) for ln in out["ticket"]["orders"])


def test_a_ticket_proposed_and_approved_yields_exactly_the_proposed_legs_over_seeded_worlds(tmp_path):
    tickets = remainders = 0
    for k in range(40):
        rng = random.Random(53_100 + k)
        w = leg_world(tmp_path / f"w{k}", fill=1.0)
        w.paper = random_paper(rng)
        for sym in FUNDS:
            w.prices[sym] = rng.choice(PRICES[3:])
        w.run(fractional=True)                                         # a book of this program's own, with decimals
        w.later(30)
        w.paper = random_paper(rng)
        out = propose(w, fractional=True)
        if not out["ticket"]["orders"]:
            continue
        number = out["ticket_number"]
        proposed = _lines_of(out)
        w.later(1)
        out2 = approve(w, number, fractional=True)
        assert out2["status"] == "done", out2
        b = out2["batch"]
        sent = sorted((o["symbol"], "remainder" if o["tag"].endswith(".r") else "whole", o["units"])
                      for o in batch_orders(w, b))
        assert sent == proposed, (k, sent, proposed)
        for o in batch_orders(w, b):
            assert o["order_type"] == ("MARKET" if o["tag"].endswith(".r") else "LIMIT")
        check_shape(w, out2)
        assert [o["outcome"] for o in out2["ticket"]["orders"]] == ["sent"] * len(proposed)
        tickets += 1
        remainders += sum(1 for ln in proposed if ln[1] == "remainder")
    assert tickets > 25 and remainders > 25, (tickets, remainders)


def _tamper(w, number, sql, args=()):
    """A hand edit of the ticket file: its append-only rule lifted for the one UPDATE and put back as it was."""
    con = sqlite3.connect(osx.ticket_path_for(w.state_path))
    try:
        with con:
            rules = con.execute("SELECT name, sql FROM sqlite_master WHERE type = 'trigger' AND name IN "
                                "('lines_no_update', 'tickets_no_update')").fetchall()
            for name, _ in rules:
                con.execute(f"DROP TRIGGER {name}")
            assert con.execute(sql.format(where="number = ?"), (*args, number)).rowcount >= 1
            for _, text in rules:
                con.execute(text)
    finally:
        con.close()


@pytest.mark.parametrize("how, words", [
    ("whole with decimals", "with decimals"),
    ("zero units", "no units"),
    ("two lines of one leg", "two lines"),
    ("remainder on a whole-share ticket", "under one unit"),
])
def test_a_malformed_ticket_is_refused_in_words_and_nothing_is_sent(tmp_path, how, words):
    w = leg_world(tmp_path / "w")
    w.paper = paper({"a": {"SPY": 2.5, "QQQ": 1.0}})
    out = propose(w, fractional=True)
    number = out["ticket_number"]
    fractional = True
    if how == "whole with decimals":
        _tamper(w, number, "UPDATE lines SET units = 2.5 WHERE symbol = 'SPY' AND units = 2.0 AND {where}")
    elif how == "zero units":
        _tamper(w, number, "UPDATE lines SET units = 0 WHERE symbol = 'QQQ' AND {where}")
    elif how == "two lines of one leg":                               # QQQ's whole line made a second SPY whole line
        _tamper(w, number, "UPDATE lines SET symbol = 'SPY' WHERE symbol = 'QQQ' AND {where}")
    else:                                                              # the flag off: a remainder line is refused
        _tamper(w, number, "UPDATE tickets SET fractional = 0 WHERE {where}")
        fractional = False
    out2 = approve(w, number, fractional=fractional)
    assert out2["status"] == "stopped" and out2["stage"] == "ticket" and words in out2["reason"], out2
    assert w.venue.args == [] and w.orders() == []
    assert ticket_rows(w, "verdicts")[-1]["verdict"] == "refused"


def test_no_ticket_can_be_approved_into_an_order_of_another_leg_type_or_size_over_seeded_tamperings(tmp_path):
    refused = partly = 0
    for k in range(60):
        rng = random.Random(54_400 + k)
        w = leg_world(tmp_path / f"w{k}")
        w.paper = paper({"a": {sym: rng.choice([0.5, 1.5, 2.5, 3.0, 1.37]) for sym in rng.sample(FUNDS, 3)}})
        out = propose(w, fractional=True)
        number = out["ticket_number"]
        lines = out["ticket"]["orders"]
        ln = rng.choice(lines)
        u = ln["units"]
        new = rng.choice([u + 0.5, u + 1.0, u / 2, 0.25, 0.999, 1.0, 2.0, -u, u + 1e-6, u - 1e-6])
        if new == u:
            continue
        _tamper(w, number, "UPDATE lines SET units = ? WHERE line = ? AND {where}", (new, ln["line"]))
        out2 = approve(w, number, fractional=True)
        allowed = {(x["fund"], x["leg"], x["units"]) for x in lines if x["line"] != ln["line"]}
        for o in batch_orders(w, out2["batch"]):
            leg = "remainder" if o["tag"].endswith(".r") else "whole"
            assert (o["symbol"], leg, o["units"]) in allowed, (k, o, ln, new)
            assert o["order_type"] == ("MARKET" if leg == "remainder" else "LIMIT")
        if out2["status"] == "stopped":
            assert out2["stage"] == "ticket" and w.venue.args == []
            refused += 1
        else:
            partly += 1
    assert refused > 5 and partly > 20, (refused, partly)


# ---- ruling A: with `fractional` off nothing new is written

def test_with_fractional_off_no_line_carries_a_new_field_no_tag_a_suffix_and_every_order_is_a_whole_limit(tmp_path):
    runs = orders = 0
    for k in range(20):
        rng = random.Random(55_000 + k)
        w = leg_world(tmp_path / f"w{k}", rng=rng, refuse=0.2, cap=rng.choice([2, 40]), fill=rng.choice([0.0, 1.0]))
        for run in range(3):
            w.paper = random_paper(rng)
            for sym in FUNDS:
                w.prices[sym] = rng.choice(PRICES[3:])
            mode = rng.choice(["ordinary", "ticket"])
            if mode == "ordinary":
                outs = [w.run()]
            else:
                p = propose(w)
                outs = [p]
                if p.get("ticket_number"):
                    w.later(1)
                    outs.append(approve(w, p["ticket_number"]))
            for out in outs:
                text = json.dumps(out)
                assert not any(f'"{f}"' in text for f in NEW_FIELDS), out
                assert out["status"] == "done", out                     # the whole run, not one cut short
                for e in out.get("sent", []):
                    assert set(e) == {"symbol", "units", "side", "limit_price", "quote", "value", "tag", "parts",
                                      "reduces_risk"}, e
                runs += 1
            w.later(30)
        for e in w.entries():
            text = json.dumps(e)
            assert not any(f'"{f}"' in text for f in NEW_FIELDS), e
        for o in w.orders():
            assert o["order_type"] == "LIMIT" and o["collar_price"] is None and not o["tag"].endswith(".r")
            assert o["limit_price"] is not None and o["units"] == math.trunc(o["units"])
            orders += 1
        assert all(a["limit_price"] is not None for a in w.venue.args)
    assert runs > 60 and orders > 40, (runs, orders)


# ---- live stays refused for fractions, before anything is read or written

class _Untouchable:
    """Anything the run must not look at: every attribute read or call is recorded."""

    def __init__(self, seen, name):
        object.__setattr__(self, "_seen", seen)
        object.__setattr__(self, "_name", name)

    def __getattr__(self, attr):
        self._seen.append((self._name, attr))
        raise AssertionError(f"{self._name}.{attr} was read")

    def __call__(self, *a, **k):
        self._seen.append((self._name, "call"))
        raise AssertionError(f"{self._name} was called")


class _Watched:
    """A real ledger, every method asked of it recorded."""

    def __init__(self, ledger, seen):
        self._ledger, self._seen = ledger, seen

    def __getattr__(self, attr):
        self._seen.append(("ledger", attr))
        return getattr(self._ledger, attr)


def test_a_live_run_with_fractional_on_stops_before_anything_is_read_or_written_over_seeded_worlds(tmp_path):
    """Propose and approve alike: no venue, quote, paper book, live file, second source or journal is touched, no file
    changes; the summary reads the ledger's fills only (as every run stopped before the lock does today)."""
    for k in range(30):
        rng = random.Random(56_000 + k)
        w = live_world(tmp_path / f"w{k}")
        w.ledger().close()                                             # the harness's ledger file, made before
        if rng.random() < 0.5:                                         # an earlier journal line
            w.journal.write("2026-11-13", {"type": "note"})
        before = {str(p): (p.read_bytes() if p.is_file() else None) for p in sorted(w.tmp.rglob("*"))}
        seen = []
        kw = {"capital": rng.choice([100.0, 1000.0, 5000.0]), "fractional": True}
        if rng.random() < 0.5:
            kw["propose"] = True
        else:
            kw["approve"] = f"{rng.randrange(1_000_000):06d}"
        led = w.ledger()
        try:
            out = osx.run_order_step(ledger=_Watched(led, seen), venue=_Untouchable(seen, "venue"),
                                     paper=_Untouchable(seen, "paper"), quotes=_Untouchable(seen, "quotes"),
                                     journal=_Untouchable(seen, "journal"), limits_file=w.limits, state=w.state_path,
                                     clock=w.clock, calendar=_Untouchable(seen, "calendar"), halt_file=w.halt,
                                     latch_file=w.latch, live=_Untouchable(seen, "live"),
                                     second_opinion=_Untouchable(seen, "second"), **kw)
        finally:
            led.close()
        assert out["status"] == "stopped" and out["stage"] == "live" and "whole shares only" in out["reason"], out
        assert seen == [("ledger", "fills"), ("ledger", "fills")], seen
        after = {str(p): (p.read_bytes() if p.is_file() else None) for p in sorted(w.tmp.rglob("*"))}
        assert after == before
