import json
import shutil
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from valedia_trader.research_loop import paper as pp
from valedia_trader.research_loop import risk_gate as rg
from valedia_trader.research_loop import strategy as st


def prices(n=40, seed=0):
    rng = np.random.default_rng(seed)
    up = 100 * np.exp(np.cumsum(0.01 + 0.002 * rng.standard_normal(n)))
    down = 100 * np.exp(np.cumsum(-0.01 + 0.002 * rng.standard_normal(n)))
    return pd.DataFrame({"UP": up, "DOWN": down}, index=pd.bdate_range("2026-01-05", periods=n))


def strat(name="mom_paper", max_pos=0.5):
    return st.RuleStrategy.from_spec({
        "name": name, "version": "1.0.0", "hypothesis": "Assets that rose over ten days keep rising", "market": ["UP", "DOWN"],
        "entry": {"rule": "momentum", "lookback": 10, "enter": 0.03, "exit": 0.0, "direction": "both"},
        "sizing": {"rule": "equal"}, "risk": {"max_position_pct": max_pos, "max_open_positions": 2, "stop_loss_pct": None}})


@pytest.fixture
def setup(tmp_path):
    limits = tmp_path / "config" / "risk.json"
    limits.parent.mkdir()
    limits.write_text(json.dumps({"max_daily_loss_pct": 50.0, "max_drawdown_pct": 50.0, "max_position_pct": 0.6, "max_open_positions": 5}))
    return tmp_path / "journal", limits


def trader(setup, **kw):
    kw.setdefault("halt_file", None)
    return pp.PaperTrader(setup[0], setup[1], **kw)


def test_orders_fill_on_the_next_day_and_the_books_add_up(setup):
    px, t, s = prices(), trader(setup, fee_bps=10.0, slippage_bps=20.0), strat()
    day = 11                                                          # first bar with a 10-day return
    first = t.step(px.iloc[: day + 1], {7: s})
    assert first["orders_placed"] == 2 and first["equity"] == 100_000.0     # decided today, nothing filled yet
    assert all(lot["units"] == 0 for lot in t.state["positions"]["mom_paper"].values())
    second = t.step(px.iloc[: day + 2], {7: s})
    fills = [e for e in t.journal.read() if e["type"] == "fill"]
    assert {f["symbol"] for f in fills} == {"UP", "DOWN"} and all(f["decided_on"] == str(px.index[day].date()) for f in fills)
    up = next(f for f in fills if f["symbol"] == "UP")
    assert up["price"] == pytest.approx(px["UP"].iloc[day + 1] * 1.002)       # next close plus slippage
    assert up["fee"] == pytest.approx(abs(up["units"]) * up["price"] * 0.001)
    down = next(f for f in fills if f["symbol"] == "DOWN")
    assert down["units"] < 0 and down["price"] == pytest.approx(px["DOWN"].iloc[day + 1] * 0.998)
    for k in range(day + 2, 30):
        last = t.step(px.iloc[: k + 1], {7: s})
    assert sum(last["strategy_values"].values()) == pytest.approx(last["equity"] - 100_000.0)   # attribution adds up
    assert last["equity"] > 100_000.0                                # long the riser, short the faller
    assert second["equity"] < 100_000.0                              # day one cost the fees and slippage
    with pytest.raises(ValueError, match="already processed"):
        t.step(px.iloc[:30], {7: s})


def test_every_decision_is_journaled_with_conditions_and_reasoning(setup):
    px, t, s = prices(), trader(setup, min_trade_pct=0.05), strat()  # a 5% no-trade band, so quiet days stay quiet
    for k in range(10, 15):                                          # bar 10 is the first with a 10-day return
        t.step(px.iloc[: k + 1], {7: s})
    entries = t.journal.read()
    decisions = [e for e in entries if e["type"] == "decision"]
    placed = [e for e in decisions if e["decision"] == "placed"]
    assert placed and all({"timestamp", "day", "strategy", "version", "market_conditions", "decision", "reasoning"} <= set(e) for e in decisions)
    assert placed[0]["version"] == "1.0.0" and placed[0]["hypothesis_id"] == 7 and placed[0]["risk_check"] == "OK"
    assert placed[0]["reasoning"].startswith("enter: entry rule") and set(placed[0]["market_conditions"]) == {"regime", "volatility_20d", "return_20d"}
    assert any(e["decision"] == "no trade" for e in decisions)       # holding is a decision too
    assert [e["type"] for e in entries].count("summary") == 5
    assert t.journal.read(since=px.index[13]) and len(t.journal.read(until=px.index[11])) < len(entries)
    late = pp.PaperTrader(setup[0].parent / "late", setup[1], halt_file=None)
    late.step(px.iloc[:16], {7: s})                                  # a trader that starts after the entry says so
    assert "catching up with the strategy's book (enter on" in [e for e in late.journal.read() if e.get("decision") == "placed"][0]["reasoning"]
    reopened = trader(setup)                                         # state survives a restart
    assert reopened.state["last_day"] == str(px.index[14].date()) and reopened.open_positions() == 2
    assert sum(e["decision"] == "placed" for e in decisions) == 2    # two entries, then nothing but holding


def test_a_blocked_order_never_reaches_the_book_or_the_venue(setup):
    setup[1].write_text(json.dumps({"max_daily_loss_pct": 50.0, "max_drawdown_pct": 50.0, "max_position_pct": 0.05, "max_open_positions": 5}))
    sent = []
    venue = SimpleNamespace(name="fake", submit=lambda sym, units, tag: sent.append(sym) or {"status": "accepted"})
    px, t = prices(), trader(setup, venue=venue)
    out = t.step(px.iloc[:12], {7: strat(max_pos=0.5)})              # the strategy wants 50%, the account allows 5%
    assert out["orders_placed"] == 0 and out["orders_blocked"] == 2 and t.state["pending"] == [] and sent == []
    blocked = [e for e in t.journal.read() if e.get("decision") == "blocked"]
    assert len(blocked) == 2 and all(e["risk_check"] == "Order is larger than the position limit." for e in blocked)
    setup[1].unlink()                                                # no limits file at all: fail closed
    out = t.step(px.iloc[:13], {7: strat()})
    assert out["orders_placed"] == 0 and out["orders_blocked"] == 2
    assert "No risk limits" in [e for e in t.journal.read() if e.get("decision") == "blocked"][-1]["risk_check"]


def test_drawdown_latches_the_kill_switch_but_exits_still_get_out(setup):
    setup[1].write_text(json.dumps({"max_daily_loss_pct": 50.0, "max_drawdown_pct": 3.0, "max_position_pct": 0.6, "max_open_positions": 5}))
    px, t, s = prices(), trader(setup), strat()
    crash = px.copy()
    crash.iloc[14:, 0] = crash.iloc[13, 0] * 0.80                    # the long leg gaps down 20% and stays there
    crash.iloc[14:, 1] = crash.iloc[13, 1] * 1.20
    for k in range(11, 14):
        t.step(crash.iloc[: k + 1], {7: s})
    hit = t.step(crash.iloc[:15], {7: s})
    assert hit["drawdown_pct"] >= 3.0 and t.latch_file.exists()
    entries = t.journal.read()
    assert any(e["type"] == "kill_switch" and e["decision"] == "latched" for e in entries)
    later = [t.step(crash.iloc[: k + 1], {7: s}) for k in range(15, 30)]
    after = [e for e in t.journal.read(since=crash.index[14]) if e["type"] == "decision" and "order" in e]
    assert after and all(e["decision"] == "blocked" for e in after if not e["order"]["reduces_risk"])
    assert all(e["decision"] == "placed" for e in after if e["order"]["reduces_risk"])    # the exits went through
    assert later[-1]["open_positions"] == 0
    assert t.reset_latch("burton", crash.index[29]) and not t.latch_file.exists() and not t.reset_latch("burton", crash.index[29])


def test_operator_halt_file_stops_everything(setup, tmp_path):
    halt = tmp_path / "halt.json"
    halt.write_text("{}")
    px, t = prices(), trader(setup, halt_file=halt)
    out = t.step(px.iloc[:12], {7: strat()})
    assert out["orders_placed"] == 0 and out["orders_blocked"] == 2
    import inspect
    assert inspect.signature(pp.PaperTrader.__init__).parameters["halt_file"].default == rg.HALT_FILE   # the default watches the system's own halt file


class FakeAlpaca:
    def __init__(self, base="https://paper-api.alpaca.markets"):
        self._base_url, self.orders = base, []

    def submit_order(self, req):
        self.orders.append(req)
        return SimpleNamespace(id="abc-123")

    def get_account(self):
        return SimpleNamespace(equity="100500.5", last_equity="100000", account_number="PA123")

    def get_all_positions(self):
        return [SimpleNamespace(symbol="SPY", qty="3.5")]


def test_alpaca_venue_only_ever_talks_to_a_paper_account():
    pytest.importorskip("alpaca")
    with pytest.raises(pp.VenueError, match="not an Alpaca paper key"):
        pp.AlpacaPaperVenue("AKLIVEKEY", "secret", client=FakeAlpaca())
    with pytest.raises(pp.VenueError, match="paper endpoint"):
        pp.AlpacaPaperVenue("PKPAPERKEY", "secret", client=FakeAlpaca("https://api.alpaca.markets"))
    fake = FakeAlpaca()
    v = pp.AlpacaPaperVenue("PKPAPERKEY", "secret", client=fake)
    assert v.account() == {"equity": 100500.5, "last_equity": 100000.0, "account_number": "PA123"} and v.positions() == {"SPY": 3.5}
    assert v.submit("SPY", 2.5, "tag")["qty"] == 2.5 and fake.orders[-1].side.value == "buy" and fake.orders[-1].time_in_force.value == "day"
    assert v.submit("SPY", -2.9, "tag")["qty"] == 2.0 and fake.orders[-1].side.value == "sell"       # whole shares on sells
    assert v.submit("SPY", -0.4, "tag")["status"] == "skipped"
    assert v.submit("BTC-USD", 0.01, "tag")["status"] == "accepted" and fake.orders[-1].symbol == "BTC/USD"
    assert fake.orders[-1].time_in_force.value == "gtc"
    real = pp.AlpacaPaperVenue("PKNOTAREALKEY", "x")                 # the real client object, never called
    assert "paper-api" in str(real._client._base_url.value)


class FakePanel:
    """Stands in for the decision panel: answers from a script, remembers what it was asked and what was settled."""
    def __init__(self, approve=True, fail=False, silent=False):
        self.approve, self.fail, self.silent, self.cases, self.settled = approve, fail, silent, [], []

    def evaluate(self, case_id, case):
        self.cases.append((case_id, case))
        if self.fail:
            raise RuntimeError("panel offline")
        verdicts = () if self.silent else (SimpleNamespace(member="decider-a", probability=0.7 if self.approve else 0.3, error=None),
                                           SimpleNamespace(member="vetter-b", probability=0.1, error="timeout"))
        return SimpleNamespace(approve=self.approve, probability=0.7 if self.approve else 0.3, vetoed=not self.approve,
                               reasons=() if self.approve else ("vetter-b: event risk",), verdicts=verdicts, dissent=0.0)

    def settle(self, case_id, outcome):
        self.settled.append((case_id, outcome))


FACTS = lambda prices, symbol: {"symbol": symbol, "momentum_20d_pct": 4.2}


def test_shadow_mode_records_the_panel_but_never_blocks(setup):
    px, panel = prices(), FakePanel(approve=False)
    t = trader(setup, panel=panel, panel_mode="shadow", facts=FACTS)
    out = t.step(px.iloc[:12], {7: strat()})
    assert out["orders_placed"] == 2 and out["orders_blocked"] == 0            # a refusal is written down, not enforced
    decisions = [e for e in t.journal.read() if e["type"] == "decision"]
    assert all(e["panel"]["approve"] is False and e["panel"]["mode"] == "shadow" for e in decisions)
    assert decisions[0]["panel"]["members"] == {"decider-a": 0.3, "vetter-b": "error: timeout"}
    case_id, case = panel.cases[0]
    assert case_id == f"mom_paper:UP:{px.index[11].date()}" and "closes higher over the next 5 trading days" in case["thesis"]
    assert case["facts"]["momentum_20d_pct"] == 4.2 and case["facts"]["order_side"] == "buy" and case["facts"]["strategy"] == "mom_paper"
    assert "closes lower" in panel.cases[1][1]["thesis"]                       # the short is judged on its own direction
    assert len(t.state["panel_cases"]) == 2


def test_veto_mode_blocks_on_a_refusal_and_on_an_unreachable_panel(setup, tmp_path):
    px = prices()
    refused = trader(setup, panel=FakePanel(approve=False), panel_mode="veto", facts=FACTS)
    out = refused.step(px.iloc[:12], {7: strat()})
    assert out["orders_placed"] == 0 and out["orders_blocked"] == 2
    blocked = [e for e in refused.journal.read() if e["type"] == "decision"]
    assert all(e["decision"] == "blocked" and "the panel refused: vetter-b: event risk" in e["risk_check"] for e in blocked)
    for bad in (FakePanel(fail=True), FakePanel(silent=True)):                 # failing closed: no panel answer, no new risk
        other = pp.PaperTrader(tmp_path / f"j{id(bad)}", setup[1], halt_file=None, panel=bad, panel_mode="veto", facts=FACTS)
        res = other.step(px.iloc[:12], {7: strat()})
        assert res["orders_placed"] == 0 and res["orders_blocked"] == 2
        assert "could not be reached" in [e for e in other.journal.read() if e["type"] == "decision"][0]["risk_check"]
    approved = pp.PaperTrader(tmp_path / "ok", setup[1], halt_file=None, panel=FakePanel(approve=True), panel_mode="veto", facts=FACTS)
    assert approved.step(px.iloc[:12], {7: strat()})["orders_placed"] == 2


def test_the_panel_is_scored_after_its_horizon_and_never_asked_about_exits(setup):
    px, panel = prices(), FakePanel()
    t = trader(setup, panel=panel, panel_mode="shadow", panel_horizon_days=3, facts=FACTS)
    for k in range(11, 16):
        t.step(px.iloc[: k + 1], {7: strat()})
    first_day = str(px.index[11].date())
    assert panel.settled[:2] == [(f"mom_paper:UP:{first_day}", 1), (f"mom_paper:DOWN:{first_day}", 1)]     # both theses came true
    assert all(outcome == 1 for _, outcome in panel.settled)                  # the riser kept rising, the faller kept falling
    settled = [e for e in t.journal.read() if e["type"] == "panel_settled"]
    assert len(settled) == len(panel.settled) and all(e["thesis_right"] for e in settled)
    assert all(c["decided_on"] > first_day for c in t.state["panel_cases"])   # only cases younger than the horizon stay open
    asked = len(panel.cases)
    flat = st.RuleStrategy.from_spec({**strat().to_spec(), "entry": {"rule": "momentum", "lookback": 10, "enter": 9.0, "exit": 8.0, "direction": "both"}})
    t.step(px.iloc[:17], {7: flat})                                            # the book is closed out: orders that only reduce risk
    assert len(panel.cases) == asked
    with pytest.raises(ValueError):
        trader(setup, panel_mode="veto")                                       # veto without a panel
    with pytest.raises(ValueError):
        trader(setup, panel=panel, panel_mode="always")
    assert trader(setup).panel_mode == "off"


# ---- the "hold the funds" benchmark (docs/specs/2026-11-12-paper-benchmark-design.md)
def _held(px, start_pos, upto_pos, capital=100_000.0):
    """What the gate's own comparator makes from the close of `start_pos` to the close of `upto_pos`."""
    from valedia_trader.backtest.engine import buy_and_hold
    basket = buy_and_hold(px.iloc[: upto_pos + 1])
    return capital * (float(np.prod(1.0 + basket.iloc[start_pos + 1:])) - 1.0)


def test_benchmark_is_the_gates_held_basket_from_the_day_the_first_orders_fill(setup):
    px, t, s, day = prices(), trader(setup), strat(), 11
    first = t.step(px.iloc[: day + 1], {7: s})
    assert first["benchmark_values"] == {"mom_paper": 0.0}             # decided today: nothing has been earned yet
    second = t.step(px.iloc[: day + 2], {7: s})
    assert second["benchmark_values"] == {"mom_paper": 0.0}            # the fill day is where both start counting
    third = t.step(px.iloc[: day + 3], {7: s})
    assert third["benchmark_values"]["mom_paper"] == pytest.approx(_held(px, day + 1, day + 2))
    fourth = t.step(px.iloc[: day + 4], {7: s})
    assert fourth["benchmark_values"]["mom_paper"] == pytest.approx(_held(px, day + 1, day + 3))
    assert fourth["benchmark_values"]["mom_paper"] != pytest.approx(fourth["strategy_values"]["mom_paper"])   # a different thing from the book


def test_benchmark_places_no_orders_and_leaves_the_book_alone(setup):
    px, t, s, day = prices(), trader(setup), strat(), 11
    for i in range(1, 5):
        out = t.step(px.iloc[: day + i], {7: s})
    row = px.iloc[day + 3]
    held = sum(lot["units"] * float(row[sym]) for lot in [None] for sym, lot in t.state["positions"]["mom_paper"].items())
    assert t.state["cash"] + held == pytest.approx(out["equity"])      # cash plus positions is still the whole account
    kinds = {e["type"] for e in t.journal.read()}
    assert kinds <= {"decision", "fill", "summary", "kill_switch", "panel_settled"}
    assert not any("benchmark" in json.dumps(e) for e in t.journal.read() if e["type"] in ("decision", "fill"))
    assert set(t.state["benchmarks"]["mom_paper"]) == {"start_day", "capital"}     # two facts; the value is recomputed from prices


def test_a_book_saved_before_the_benchmark_existed_gets_it_from_its_own_history(setup):
    px, s, day = prices(), strat(), 11
    t = trader(setup)
    t.step(px.iloc[: day + 1], {7: s})
    t.step(px.iloc[: day + 2], {7: s})
    t.state.pop("benchmarks")                                           # what a state file written by the older code looks like
    for h in t.state["history"]:
        h.pop("benchmark_values", None)
    t._save()
    again = trader(setup)
    third = again.step(px.iloc[: day + 3], {7: s})
    assert again.state["benchmarks"]["mom_paper"] == {"start_day": str(px.index[day].date()), "capital": 100_000.0}
    assert third["benchmark_values"]["mom_paper"] == pytest.approx(_held(px, day + 1, day + 2))


def test_benchmark_capital_is_fixed_on_the_strategys_first_day(setup):
    px, t, day = prices(), trader(setup), 11
    a, b = strat("first"), strat("second")
    t.step(px.iloc[: day + 1], {7: a})
    t.step(px.iloc[: day + 2], {7: a})
    out = t.step(px.iloc[: day + 3], {7: a, 8: b})                      # a second strategy joins: capital per strategy halves
    assert out["capital_per_strategy"] == pytest.approx(out["equity"] / 2)
    assert t.state["benchmarks"]["first"]["capital"] == 100_000.0
    assert out["benchmark_values"]["first"] == pytest.approx(_held(px, day + 1, day + 2))
    assert out["benchmark_values"]["second"] == 0.0 and t.state["benchmarks"]["second"]["start_day"] == str(px.index[day + 2].date())


# ---- the account's limits are on the account: a fund held by two strategies is one position

def test_two_strategies_in_one_fund_are_one_position_of_the_account(setup):
    setup[1].write_text(json.dumps({"max_daily_loss_pct": 50.0, "max_drawdown_pct": 50.0, "max_position_pct": 0.6, "max_open_positions": 2}))
    px, t = prices(), trader(setup)
    both = {7: strat("first_paper"), 8: strat("second_paper")}       # each wants UP and DOWN: two funds, four holdings
    out = t.step(px.iloc[:12], both)
    assert out["orders_placed"] == 4 and out["orders_blocked"] == 0
    assert t.open_positions() == 2 and out["open_positions"] == 2    # the broker would show two positions
    t.step(px.iloc[:13], both)
    assert t.open_positions() == 2 and all(abs(lot["units"]) > 0 for book in t.state["positions"].values() for lot in book.values())


def test_the_position_limit_is_on_the_accounts_whole_holding_of_a_fund(setup):
    setup[1].write_text(json.dumps({"max_daily_loss_pct": 50.0, "max_drawdown_pct": 50.0, "max_position_pct": 0.3, "max_open_positions": 5}))
    px, t = prices(), trader(setup)
    out = t.step(px.iloc[:12], {7: strat("first_paper"), 8: strat("second_paper")})
    # each strategy has half the account and wants half of that in each fund: 25% is inside the 30% limit,
    # but the second strategy's order would make the account's holding 50%
    assert out["orders_placed"] == 2 and out["orders_blocked"] == 2
    decisions = [e for e in t.journal.read() if e["type"] == "decision"]
    assert {e["strategy"] for e in decisions if e["decision"] == "placed"} == {"first_paper"}
    refused = [e for e in decisions if e["decision"] == "blocked"]
    assert {e["strategy"] for e in refused} == {"second_paper"} and all(e["risk_check"] == "Order is larger than the position limit." for e in refused)
    assert all(e["order"]["position_value_after"] == pytest.approx(2 * e["order"]["value"]) for e in refused)
    assert all(e["order"]["adds_to_existing"] for e in refused)      # the account is already in the fund


# ---- tax notes (T3): every order says what it would do for tax; tax off changes nothing

from valedia_trader.execution_and_risk import tax as tx  # noqa: E402

RULES = tx.TaxRules.load()
DAY0 = 30                                                             # the first traded bar of the scripted market


def scripted_market():
    """One fund AAA: 30 bars of history near 100, then the closes the scripted script trades on."""
    hist = list(100 + 0.5 * np.sin(np.arange(DAY0)))
    closes = [100.0, 100.0, 80.0, 80.0, 80.0, 80.0, 80.0, 80.0]
    return pd.DataFrame({"AAA": hist + closes}, index=pd.bdate_range("2025-11-24", periods=DAY0 + len(closes)))


# the strategy's target weight in AAA on each traded bar: buy half, hold, trim to a quarter, hold, back to half
SCRIPT = [0.5, 0.5, 0.25, 0.25, 0.5, 0.5, 0.5, 0.5]


def long_only(name="tax_paper", market=("AAA",)):
    return st.RuleStrategy.from_spec({
        "name": name, "version": "1.0.0", "hypothesis": "A long-only book for the tax notes", "market": list(market),
        "entry": {"rule": "momentum", "lookback": 10, "enter": 0.0, "exit": -0.02, "direction": "long"},
        "sizing": {"rule": "equal"}, "risk": {"max_position_pct": 0.5, "max_open_positions": 3, "stop_loss_pct": None}})


@pytest.fixture
def scripted(monkeypatch):
    def run(s, prices):
        w = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
        w.iloc[-1, w.columns.get_loc("AAA")] = SCRIPT[len(prices) - 1 - DAY0]
        return w, []
    monkeypatch.setattr(pp, "run_rules", run)
    return scripted_market()


def no_costs(setup, directory=None, **kw):
    kw.setdefault("halt_file", None)
    return pp.PaperTrader(directory or setup[0], setup[1], fee_bps=0.0, slippage_bps=0.0, **kw)


def notes(t):
    return [e for e in t.journal.read() if e["type"] == "decision" and "order" in e]


def test_tax_notes_give_the_numbers_for_a_trim_at_a_loss_and_a_buy_inside_its_window(setup, scripted):
    px, t, s = scripted, no_costs(setup, tax_rules=RULES), long_only()
    days = [t.step(px.iloc[: DAY0 + k + 1], {7: s}) for k in range(6)]
    d = {e["day"]: e for e in notes(t)}
    buy0 = d["2026-01-05"]["tax"]
    # day 0: buy 0.5 x 100,000 / 100 = 500 units; nothing sold before, so nothing to wash
    assert buy0["side"] == "buy" and buy0["disallowed_loss"] == 0 and buy0["washes"] == [] and buy0["clean_from"] == "2026-01-06"   # T5a F2: as of the fill day
    trim = d["2026-01-07"]["tax"]
    # day 2 at 80: equity 50,000 cash + 500 x 80 = 90,000; target 0.25 x 90,000 / 80 = 281.25, so sell 218.75.
    # basis 218.75 x 100 = 21,875, proceeds 218.75 x 80 = 17,500: a short-term loss of 4,375. The rest of the same
    # purchase is no replacement: nothing disallowed, all 4,375 at risk. T5a F2: estimated for the fill day 2026-01-08,
    # so clean from 2026-01-08 + 31 = 2026-02-08
    assert trim["side"] == "sell" and trim["estimated_at_price"] == pytest.approx(80.0)
    assert trim["gain_loss"] == {"short_term": pytest.approx(-4375.0), "long_term": pytest.approx(0.0)}
    assert trim["disallowed_loss"] == pytest.approx(0.0) and trim["loss_at_risk"] == pytest.approx(4375.0)
    assert trim["clean_from"] == "2026-02-08"
    assert trim["effect"] == "would realise a short-term loss of 4375.00; buying AAA again before 2026-02-08 would make it a wash sale."
    back = d["2026-01-09"]["tax"]
    # day 4: equity 67,500 cash + 281.25 x 80 = 90,000; target 0.5 x 90,000 / 80 = 562.5, so buy 281.25. The trim filled
    # on 2026-01-08 at 80 (a loss of 20 a unit on 218.75 units); 218.75 of the new units wash it all: 4,375 disallowed
    assert back["side"] == "buy" and back["disallowed_loss"] == pytest.approx(4375.0)
    assert back["washes"] == [{"sale_date": "2026-01-08", "units": pytest.approx(218.75), "disallowed_loss": pytest.approx(4375.0)}]
    assert back["clean_from"] == "2026-02-08"
    assert back["effect"] == "would turn 4375.00 of the loss sold on 2026-01-08 into a wash sale, carried into this lot."
    assert all(n["tax"]["unverified_rules"] == RULES.unverified() and n["tax"]["note"] == tx.NOTE for n in notes(t))
    held = days[3]["tax"]                                            # 2026-01-08, after the trim filled
    assert held == {"year": "2026", "short_term_net": pytest.approx(-4375.0), "long_term_net": pytest.approx(0.0),
                    "disallowed_loss": pytest.approx(0.0), "provisional_loss": pytest.approx(4375.0), "as_of": "2026-01-08",
                    "unverified_rules": RULES.unverified(), "note": tx.NOTE,
                    "provisional_loss_from_last_year": 0.0, "among_todays_orders": []}   # T5a F1/F3: two new keys
    washed = days[5]["tax"]                                          # 2026-01-12, after the buy back filled
    # the loss is now disallowed: -4,375 + 4,375 = 0 net, nothing provisional
    assert washed["short_term_net"] == pytest.approx(0.0) and washed["disallowed_loss"] == pytest.approx(4375.0)
    assert washed["provisional_loss"] == pytest.approx(0.0) and washed["as_of"] == "2026-01-12"
    year = t.tax_ledger.summary("2026", as_of="2026-01-12")
    assert washed["short_term_net"] + washed["long_term_net"] == pytest.approx(year["total"]["net_gain_loss"])   # it adds up
    assert year["total"]["gain_loss"] + year["total"]["disallowed_loss"] == pytest.approx(year["total"]["net_gain_loss"])
    lot = [x for x in t.tax_ledger.open_lots("AAA") if x["wash_adjustment"]]
    assert lot and lot[0]["cost_basis"] == pytest.approx(218.75 * 80 + 4375.0)


def _comparable(t):
    """Everything the trader wrote and holds, with the tax notes and timestamps taken out."""
    entries = []
    for e in t.journal.read():
        e = {k: v for k, v in e.items() if k not in ("timestamp", "tax")}
        entries.append(json.dumps(e, sort_keys=True, default=str))
    state = json.loads(json.dumps(t.state, default=str))
    for h in state["history"]:
        h.pop("tax", None)
    return entries, json.dumps(state, sort_keys=True)


def volatile(n=40, seed=3, funds=("F1", "F2", "F3")):
    rng = np.random.default_rng(seed)
    return pd.DataFrame({f: 100 * np.exp(np.cumsum(0.03 * rng.standard_normal(n))) for f in funds},
                        index=pd.bdate_range("2026-01-05", periods=n))


def test_a_twin_with_tax_off_places_and_blocks_the_same_orders_and_ends_with_the_same_book(setup, tmp_path):
    px, s = volatile(), long_only(market=("F1", "F2", "F3"))
    on = pp.PaperTrader(tmp_path / "on", setup[1], halt_file=None, tax_rules=RULES, min_trade_pct=0.0005)
    off = pp.PaperTrader(tmp_path / "off", setup[1], halt_file=None, min_trade_pct=0.0005)
    for k in range(11, 40):
        a, b = on.step(px.iloc[: k + 1], {7: s}), off.step(px.iloc[: k + 1], {7: s})
        assert {k2: v for k2, v in a.items() if k2 != "tax"} == b and "tax" in a and "tax" not in b
    assert _comparable(on) == _comparable(off)
    assert on.state["cash"] == off.state["cash"] and on.state["positions"] == off.state["positions"]
    assert not any("tax" in e for e in off.journal.read())                       # tax off: no new key anywhere
    sides = {n["tax"].get("side") for n in notes(on)}
    assert {"buy", "sell"} <= sides and on.tax_unavailable is None              # the long-only book was fully noted
    assert all(json.dumps(n["tax"]) for n in notes(on))


def test_a_long_and_short_book_keeps_trading_and_reports_tax_unavailable(setup, tmp_path):
    px, s = prices(), strat()                                                    # long UP, short DOWN
    on = pp.PaperTrader(tmp_path / "on", setup[1], halt_file=None, tax_rules=RULES)
    off = pp.PaperTrader(tmp_path / "off", setup[1], halt_file=None)
    out = [on.step(px.iloc[: k + 1], {7: s}) for k in range(11, 30)]
    for k in range(11, 30):
        off.step(px.iloc[: k + 1], {7: s})
    assert _comparable(on) == _comparable(off)
    first = {n["symbol"]: n["tax"] for n in notes(on) if n["day"] == str(px.index[11].date())}
    assert first["UP"]["side"] == "buy" and "unavailable" in first["DOWN"]      # a short sale cannot be noted
    assert "short" in on.tax_unavailable                                        # the DOWN fill took the ledger short
    assert all(set(o["tax"]) == {"unavailable"} for o in out[1:])
    assert all(set(n["tax"]) == {"unavailable"} for n in notes(on) if n["day"] > str(px.index[11].date()))
    again = pp.PaperTrader(tmp_path / "on", setup[1], halt_file=None, tax_rules=RULES)     # still unavailable after a restart
    assert "short" in again.tax_unavailable


def test_a_restarted_trader_gives_the_same_tax_block(setup, tmp_path, scripted):
    px, s = scripted, long_only()
    whole = no_costs(setup, tmp_path / "whole", tax_rules=RULES)
    runs = [whole.step(px.iloc[: DAY0 + k + 1], {7: s}) for k in range(8)]
    part = no_costs(setup, tmp_path / "part", tax_rules=RULES)
    for k in range(4):
        part.step(px.iloc[: DAY0 + k + 1], {7: s})
    restarted = no_costs(setup, tmp_path / "part", tax_rules=RULES)               # built once from the journal's fills
    later = [restarted.step(px.iloc[: DAY0 + k + 1], {7: s}) for k in range(4, 8)]
    assert [r["tax"] for r in later] == [r["tax"] for r in runs[4:]]
    assert [n["tax"] for n in notes(restarted)] == [n["tax"] for n in notes(whole)]


def test_a_tax_check_that_raises_blocks_nothing(setup, tmp_path, scripted):
    px, s = scripted, long_only()
    on = no_costs(setup, tmp_path / "on", tax_rules=RULES)
    off = no_costs(setup, tmp_path / "off")

    def boom(*a, **k):
        raise RuntimeError("tax check exploded")
    on.tax_ledger.check_sell = on.tax_ledger.check_buy = boom
    for k in range(6):
        on.step(px.iloc[: DAY0 + k + 1], {7: s})
        off.step(px.iloc[: DAY0 + k + 1], {7: s})
    assert _comparable(on) == _comparable(off)
    assert notes(on) and all(n["tax"] == {"unavailable": "RuntimeError: tax check exploded"} for n in notes(on))
    assert on.tax_unavailable is None and on.state["history"][-1]["tax"]["disallowed_loss"] == pytest.approx(4375.0)


# ---- T5a: notes see the same day's orders, are dated on the fill day, and January shows December's open losses

from valedia_trader.research_loop.strategy import _exchange_sessions  # noqa: E402

HIST = 30


def session_market(start, closes):
    """One fund AAA on real exchange sessions: 30 sessions of history near 100, then `closes` from `start`."""
    sessions = _exchange_sessions()
    i = sessions.searchsorted(pd.Timestamp(start))
    idx = sessions[i - HIST: i + len(closes)]
    return pd.DataFrame({"AAA": list(100 + 0.5 * np.sin(np.arange(HIST))) + list(closes)}, index=idx)


@pytest.fixture
def by_name(monkeypatch):
    """Scripted target weights in AAA per strategy name, one per traded session."""
    scripts = {}

    def run(s, prices):
        w = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
        w.iloc[-1, w.columns.get_loc("AAA")] = scripts[s.name][len(prices) - 1 - HIST]
        return w, []
    monkeypatch.setattr(pp, "run_rules", run)
    return scripts


class RefuseB:
    """A veto panel that approves everything but the strategy named tax_b."""
    def evaluate(self, case_id, case):
        ok = not case_id.startswith("tax_b:")
        v = SimpleNamespace(member="m", probability=0.7 if ok else 0.3, error=None)
        return SimpleNamespace(approve=ok, probability=v.probability, vetoed=not ok, reasons=() if ok else ("no",),
                               verdicts=(v,), dissent=0.0)

    def settle(self, case_id, outcome):
        pass


def three_way(setup, directory, by_name, panel=None):
    # 2026-01-07 (Wed) A buys; 2026-01-09 (Fri) at 80, A trims while B and C buy; they fill on Monday 2026-01-12
    px = session_market("2026-01-07", [100.0, 100.0, 80.0, 80.0])
    by_name.update({"tax_a": [0.5, 0.5, 0.25, 0.25], "tax_b": [0.0, 0.0, 0.1, 0.1], "tax_c": [0.0, 0.0, 0.1, 0.1]})
    kw = {"panel": panel, "panel_mode": "veto", "facts": FACTS} if panel else {}
    t = no_costs(setup, directory, tax_rules=RULES, starting_cash=900_000.0, **kw)
    strats = {1: long_only("tax_a"), 2: long_only("tax_b"), 3: long_only("tax_c")}
    days = [t.step(px.iloc[: HIST + k + 1], strats) for k in range(3)]
    return t, {e["strategy"]: e for e in notes(t) if e["day"] == "2026-01-09"}, days[2]["tax"]


def test_a_buy_note_sees_a_sale_decided_earlier_the_same_day(setup, tmp_path, by_name):
    t, n, block = three_way(setup, tmp_path / "all", by_name)
    # 900,000 cash, three strategies: 300,000 each. A buys 0.5 x 300,000 / 100 = 1,500 units, filled at 100.
    # Friday at 80: equity 750,000 + 1,500 x 80 = 870,000, so 290,000 each. A's target 0.25 x 290,000 / 80 = 906.25:
    # sell 593.75 at a loss of 20 a unit = 11,875. B and C each buy 0.1 x 290,000 / 80 = 362.5
    assert n["tax_a"]["tax"]["side"] == "sell" and n["tax_a"]["tax"]["gain_loss"]["short_term"] == pytest.approx(-11875.0)
    assert n["tax_a"]["tax"]["disallowed_loss"] == pytest.approx(0.0)             # nothing bought yet when A decided
    # B's note sees A's sale: 362.5 units x 20 = 7,250 washed, sold on the fill day 2026-01-12
    b = n["tax_b"]["tax"]
    assert b["disallowed_loss"] == pytest.approx(7250.0) and b["washes"][0]["sale_date"] == "2026-01-12"
    assert b["effect"] == "would turn 7250.00 of the loss sold on 2026-01-12 into a wash sale, carried into this lot."
    # C's note sees both: 593.75 - 362.5 = 231.25 loss units left, x 20 = 4,625
    assert n["tax_c"]["tax"]["disallowed_loss"] == pytest.approx(4625.0)
    # the day's block: all three filled together, 7,250 + 4,625 = 11,875 of the loss is washed
    assert block["among_todays_orders"] == [{"symbol": "AAA", "sale_units": pytest.approx(593.75), "buy_units": pytest.approx(725.0),
                                             "disallowed_loss": pytest.approx(11875.0)}]
    assert all(e["tax"]["estimated_for_day"] == "2026-01-12" for e in n.values())   # Friday's orders fill on Monday


def test_a_blocked_order_is_not_counted_among_the_days_orders(setup, tmp_path, by_name):
    t, n, block = three_way(setup, tmp_path / "veto", by_name, panel=RefuseB())
    assert n["tax_b"]["decision"] == "blocked" and n["tax_c"]["decision"] == "placed"
    # B's own note still says what it would have done (7,250); C's note ignores B: 362.5 x 20 = 7,250, not 4,625
    assert n["tax_b"]["tax"]["disallowed_loss"] == pytest.approx(7250.0)
    assert n["tax_c"]["tax"]["disallowed_loss"] == pytest.approx(7250.0)
    assert block["among_todays_orders"] == [{"symbol": "AAA", "sale_units": pytest.approx(593.75), "buy_units": pytest.approx(362.5),
                                             "disallowed_loss": pytest.approx(7250.0)}]


def year_end(setup, by_name):
    # sessions 2025-12-22, 23, 24, 26 (25 is Christmas), 29, 30, 31, 2026-01-02
    px = session_market("2025-12-22", [100.0, 100.0, 80.0, 80.0, 80.0, 80.0, 80.0, 80.0])
    by_name["tax_a"] = [0.5, 0.5, 0.25, 0.25, 0.25, 0.25, 0.25, 0.25]
    t = no_costs(setup, tax_rules=RULES)
    return px, t, [t.step(px.iloc[: HIST + k + 1], {1: long_only("tax_a")}) for k in range(8)]


def test_a_note_is_dated_on_the_next_session_and_its_clean_from_matches_the_ledger_after_the_fill(setup, by_name):
    px, t, days = year_end(setup, by_name)
    trim = next(e["tax"] for e in notes(t) if e["day"] == "2025-12-24")
    # decided Wednesday 2025-12-24; Christmas is closed, so it fills Friday 2025-12-26. 500 units bought at 100, then at 80
    # equity 90,000: target 281.25, sell 218.75, a loss of 4,375; clean from 2025-12-26 + 31 = 2026-01-26
    assert trim["estimated_for_day"] == "2025-12-26" and trim["clean_from"] == "2026-01-26"
    fill = next(e for e in t.journal.read() if e["type"] == "fill" and e["units"] < 0)
    assert fill["day"] == "2025-12-26"
    assert t.tax_ledger.wash_sale_windows("2025-12-26")["AAA"]["clean_from"] == trim["clean_from"]
    first = next(e["tax"] for e in notes(t) if e["day"] == "2025-12-22")
    assert first["estimated_for_day"] == "2025-12-23"


def test_the_january_block_shows_last_years_open_losses(setup, by_name):
    px, t, days = year_end(setup, by_name)
    dec31, jan2 = days[6]["tax"], days[7]["tax"]
    # the 4,375 loss sold 2025-12-26 stays provisional until 2026-01-25
    assert dec31["year"] == "2025" and dec31["provisional_loss"] == pytest.approx(4375.0)
    assert dec31["provisional_loss_from_last_year"] == pytest.approx(0.0)
    assert jan2["year"] == "2026" and jan2["provisional_loss"] == pytest.approx(0.0) and jan2["short_term_net"] == pytest.approx(0.0)
    assert jan2["provisional_loss_from_last_year"] == pytest.approx(4375.0)


def test_a_buy_note_names_each_sale_date_once():
    led = tx.TaxLedger(RULES)
    # two purchases of 10 at 100, both more than 30 days before a sale of all 20 at 90 on 2025-12-10: two portions,
    # each a loss of 100 on the same sale date. Buying 20 on 2025-12-15 washes both: 200 in all
    for f in (("2025-11-03", "AAA", 10, 100.0), ("2025-11-04", "AAA", 10, 100.0), ("2025-12-10", "AAA", -20, 90.0)):
        led.add_fill(*f)
    note = pp.tax_buy_note(led.check_buy("AAA", 20, "2025-12-15"), "2025-12-15")
    assert len(note["washes"]) == 2 and note["disallowed_loss"] == pytest.approx(200.0)
    assert note["effect"] == "would turn 200.00 of the loss sold on 2025-12-10 into a wash sale, carried into this lot."


# ---- what the order step reads: per strategy and fund, units held plus pending (T13)

def test_wanted_units_are_held_plus_pending_per_strategy_and_reading_them_changes_nothing(setup):
    t = trader(setup)
    t.state["positions"] = {"a": {"UP": {"units": 10.0, "cost": 100.0}, "DOWN": {"units": 0.0, "cost": 0.0}},
                            "b": {"UP": {"units": -4.0, "cost": 90.0}}}
    t.state["pending"] = [{"strategy": "a", "symbol": "UP", "units": 2.5}, {"strategy": "a", "symbol": "UP", "units": -0.5},
                          {"strategy": "b", "symbol": "DOWN", "units": 3.0}, {"strategy": "c", "symbol": "NEW", "units": 7.0},
                          {"strategy": "b", "symbol": "UP", "units": 4.0}]
    before = json.dumps(t.state, sort_keys=True)
    assert t.wanted_units() == {"a": {"UP": 12.0}, "b": {"DOWN": 3.0}, "c": {"NEW": 7.0}}   # zeros are left out
    assert json.dumps(t.state, sort_keys=True) == before                     # read only: no lot or strategy is created
    assert pp.PaperTrader(setup[0].parent / "other", setup[1], halt_file=None).wanted_units() == {}   # a new book


def test_wanted_units_after_a_real_step_match_the_book_and_the_orders(setup):
    px, t, s = prices(), trader(setup), strat()
    t.step(px.iloc[:12], {7: s})
    t.step(px.iloc[:13], {7: s})
    wanted = t.wanted_units()["mom_paper"]
    for sym in ("UP", "DOWN"):
        held = t.state["positions"]["mom_paper"][sym]["units"]
        pending = sum(o["units"] for o in t.state["pending"] if o["symbol"] == sym)
        assert wanted.get(sym, 0.0) == pytest.approx(held + pending)


# ---- T20: a torn journal line stops no reader; a write never glues onto it

def _journal_file(directory, day="2026-03-02"):
    return directory / f"{day}.jsonl"


def test_journal_read_skips_a_line_cut_short_and_never_raises(tmp_path):
    j = pp.Journal(tmp_path / "journal")
    j.write("2026-03-02", {"type": "note", "n": 1})
    with _journal_file(j.dir).open("a") as fh:
        fh.write('{"type": "note", "n": 2, "reasoning": "cut sho')           # a kill in the middle of a write
    assert [e["n"] for e in j.read()] == [1]


def test_journal_remembers_what_it_skipped_by_file_and_line_never_the_text(tmp_path):
    j = pp.Journal(tmp_path / "journal")
    j.write("2026-03-02", {"type": "note", "n": 1})
    path = _journal_file(j.dir)
    path.write_text(path.read_text() + '{"n": 2, "x": "Zq-TEXT-OF-THE-TORN-LINE\n\n' + json.dumps({"n": 3}) + "\n[1, 2]\n12\nnot json\n")
    assert [e["n"] for e in j.read()] == [1, 3]
    assert j.skipped_lines == [{"file": "2026-03-02.jsonl", "line": 2}, {"file": "2026-03-02.jsonl", "line": 5},
                               {"file": "2026-03-02.jsonl", "line": 6}, {"file": "2026-03-02.jsonl", "line": 7}]
    assert "Zq" not in json.dumps(j.skipped_lines)
    assert j.read(since="2026-03-03") == [] and j.skipped_lines == []               # what it remembers is the last read's


def test_journal_read_skips_bytes_that_are_not_text(tmp_path):
    j = pp.Journal(tmp_path / "journal")
    j.write("2026-03-02", {"type": "note", "n": 1})
    with _journal_file(j.dir).open("ab") as fh:
        fh.write(b'{"n": 2, "x": "\xff\xfe\x80"}\n\x00\x00\x00\n' + b"[" * 100_000 + b"\n")
    j.write("2026-03-02", {"type": "note", "n": 4})
    assert [e["n"] for e in j.read()] == [1, 4] and [s["line"] for s in j.skipped_lines] == [2, 3, 4]


def test_journal_write_after_a_torn_last_line_starts_on_a_new_line(tmp_path):
    j = pp.Journal(tmp_path / "journal")
    j.write("2026-03-02", {"type": "note", "n": 1})
    with _journal_file(j.dir).open("a") as fh:
        fh.write('{"type": "note", "n": 2, "reaso')
    j.write("2026-03-02", {"type": "note", "n": 3})
    j.write("2026-03-02", {"type": "note", "n": 4})
    lines = _journal_file(j.dir).read_text().split("\n")
    assert lines[1] == '{"type": "note", "n": 2, "reaso' and json.loads(lines[2])["n"] == 3 and lines[-1] == ""
    assert [e["n"] for e in j.read()] == [1, 3, 4] and j.skipped_lines == [{"file": "2026-03-02.jsonl", "line": 2}]


def test_journal_write_adds_no_blank_line_to_a_clean_file_or_a_new_one(tmp_path):
    j = pp.Journal(tmp_path / "journal")
    for n in range(3):
        j.write("2026-03-02", {"type": "note", "n": n})
    text = _journal_file(j.dir).read_text()
    assert text.count("\n") == 3 and "\n\n" not in text and j.skipped_lines == []


def test_journal_entries_that_are_valid_come_back_exactly_and_in_order(tmp_path):
    j = pp.Journal(tmp_path / "journal")
    written = [j.write(d, {"type": "note", "n": n, "text": "café   line", "nested": {"a": [1, 2.5, None]}})
               for n, d in enumerate(["2026-03-03", "2026-03-02", "2026-03-02", "2026-03-04"])]
    by_day = sorted(written, key=lambda r: r["day"])                                # files in date order, lines in write order
    assert j.read() == by_day and j.skipped_lines == []
    assert j.read(since="2026-03-03", until="2026-03-03") == [r for r in written if r["day"] == "2026-03-03"]


@pytest.mark.parametrize("seed", range(320))
def test_journal_survives_kills_in_the_middle_of_write_and_torn_lines_anywhere(tmp_path, seed):
    """Seeded: 320 journals. Random entries over three days; some writes are cut at a random byte (a kill), some
    junk lines are put in at random places. Every complete entry comes back, in order, and nothing raises."""
    rng = np.random.default_rng(seed)
    j = pp.Journal(tmp_path / "journal")
    expected: dict[str, list[int]] = {}
    for n in range(int(rng.integers(1, 14))):
        day = f"2026-03-0{int(rng.integers(2, 5))}"
        path = _journal_file(j.dir, day)
        before = path.read_bytes() if path.exists() else b""
        word = "".join(rng.choice(list("abc {}[]\":,\\\n\u00e9")) for _ in range(int(rng.integers(0, 30))))
        j.write(day, {"type": "note", "n": n, "word": word})
        appended = path.read_bytes()[len(before):]                                  # what this write put on the file
        roll = rng.random()
        if roll < 0.35:                                                             # killed part-way through this write
            cut = int(rng.integers(0, len(appended)))                               # keeps 0 .. all but the final newline
            path.write_bytes(before + appended[:cut])
            lead = 1 if appended.startswith(b"\n") else 0
            if cut - lead >= len(appended) - lead - 1:                              # the whole record, only its newline lost
                expected.setdefault(day, []).append(n)
        else:
            expected.setdefault(day, []).append(n)
            if roll < 0.55:                                                         # junk put in after it, with or without a newline
                with path.open("ab") as fh:
                    fh.write(bytes(rng.integers(0, 256, int(rng.integers(1, 12)), dtype=np.uint8)).replace(b"\n", b"") +
                             (b"\n" if rng.random() < 0.5 else b""))
    out = j.read()
    assert [e["n"] for e in out] == [n for day in sorted(expected) for n in expected[day]]
    assert all(set(s) == {"file", "line"} for s in j.skipped_lines)
    assert [e["n"] for e in pp.Journal(j.dir).read()] == [e["n"] for e in out]       # a new object reads the same


# ---- T20: reset_latch clears a latch that is a link, and says so when it cannot

def _tripped(setup):
    t = trader(setup)
    t.state["history"] = [{"day": "2026-03-02", "equity": 90_000.0, "capital_per_strategy": 90_000.0}]
    t.state["peak_equity"] = 120_000.0
    return t


def test_reset_latch_removes_a_dangling_link_as_a_link(setup, tmp_path):
    t = _tripped(setup)
    t.dir.mkdir(parents=True, exist_ok=True)
    target = tmp_path / "nowhere.json"
    t.latch_file.symlink_to(target)
    assert not t.latch_file.exists() and rg.file_is_there(t.latch_file)           # the gate blocks on it
    assert t.reset_latch("burton", "2026-03-02") is True
    assert not t.latch_file.is_symlink() and not target.exists()                   # the link is gone, nothing was made at its end
    assert t.state["peak_equity"] == 90_000.0
    assert [e["decision"] for e in t.journal.read() if e["type"] == "kill_switch"] == ["reset"]


def test_reset_latch_removes_a_link_loop(setup):
    t = _tripped(setup)
    t.dir.mkdir(parents=True, exist_ok=True)
    other = t.dir / "loop-b"
    other.symlink_to(t.latch_file)
    t.latch_file.symlink_to(other)
    assert t.reset_latch("burton", "2026-03-02") is True
    assert not t.latch_file.is_symlink() and other.is_symlink()                    # only the latch is removed


def test_reset_latch_never_deletes_what_a_link_points_to(setup, tmp_path):
    t = _tripped(setup)
    t.dir.mkdir(parents=True, exist_ok=True)
    precious = tmp_path / "precious.json"
    precious.write_text("keep me")
    t.latch_file.symlink_to(precious)
    assert t.reset_latch("burton", "2026-03-02") is True
    assert precious.read_text() == "keep me" and not t.latch_file.is_symlink()


def test_reset_latch_on_a_plain_file_still_clears_it_and_on_nothing_returns_false(setup):
    t = _tripped(setup)
    t.dir.mkdir(parents=True, exist_ok=True)
    t.latch_file.write_text("{}")
    assert t.reset_latch("burton", "2026-03-02") is True and not rg.file_is_there(t.latch_file)
    assert t.reset_latch("burton", "2026-03-02") is False


def test_reset_latch_that_is_a_directory_raises_a_clear_error_naming_the_path(setup):
    t = _tripped(setup)
    t.latch_file.mkdir(parents=True)
    (t.latch_file / "inside").write_text("x")
    with pytest.raises(pp.LatchResetError) as err:
        t.reset_latch("burton", "2026-03-02")
    assert str(t.latch_file) in str(err.value) and t.latch_file.is_dir() and (t.latch_file / "inside").exists()
    assert t.state["peak_equity"] == 120_000.0 and [e for e in t.journal.read() if e["type"] == "kill_switch"] == []


def test_reset_latch_with_no_permission_to_remove_raises_a_clear_error_naming_the_path(setup):
    t = _tripped(setup)
    t.dir.mkdir(parents=True, exist_ok=True)
    t.latch_file.write_text("{}")
    t.dir.chmod(0o500)
    try:
        with pytest.raises(pp.LatchResetError) as err:
            t.reset_latch("burton", "2026-03-02")
    finally:
        t.dir.chmod(0o700)
    assert str(t.latch_file) in str(err.value) and t.latch_file.exists() and t.state["peak_equity"] == 120_000.0


# ---- T20 (second round): tripping the latch never writes through what is already at the latch path

@pytest.mark.parametrize("how", ["dangling link", "link loop", "plain file", "directory"])
def test_tripping_the_latch_never_follows_or_replaces_what_is_already_there(setup, tmp_path, how):
    setup[1].write_text(json.dumps({"max_daily_loss_pct": 50.0, "max_drawdown_pct": 3.0, "max_position_pct": 0.6, "max_open_positions": 5}))
    px, t, s = prices(), trader(setup), strat()
    crash = px.copy()
    crash.iloc[14:, 0] = crash.iloc[13, 0] * 0.80
    crash.iloc[14:, 1] = crash.iloc[13, 1] * 1.20
    target = tmp_path / "inside" / "nowhere.json"                                    # a path inside a temporary folder
    for k in range(11, 14):                                                             # positions open, nothing latched yet
        t.step(crash.iloc[: k + 1], {7: s})
    if how == "dangling link":
        t.latch_file.symlink_to(target)
    elif how == "link loop":
        other = t.dir / "loop-b"
        other.symlink_to(t.latch_file)
        t.latch_file.symlink_to(other)
    elif how == "plain file":
        t.latch_file.write_text("earlier")
    else:
        t.latch_file.mkdir()
    hit = t.step(crash.iloc[:15], {7: s})                                               # the day the drawdown reaches the limit
    assert hit["drawdown_pct"] >= 3.0
    assert not target.exists() and not target.parent.exists()                           # nothing was made at the end of the link
    assert rg.file_is_there(t.latch_file)                                               # still there, so the gate blocks
    assert t.latch_file.is_symlink() == (how in ("dangling link", "link loop"))
    if how == "plain file":
        assert t.latch_file.read_text() == "earlier"
        # rev 4 (outcome 4): a latch file that cannot be read counts as tripped and the day journals it; never replaced
        assert [(e["day"], e["decision"]) for e in t.journal.read() if e["type"] == "kill_switch"] == [(str(crash.index[14].date()), "latched")]
    else:
        assert [e for e in t.journal.read() if e["type"] == "kill_switch"] == []        # it was already set: no new trip
    for k in range(15, 20):
        t.step(crash.iloc[: k + 1], {7: s})
    after = [e for e in t.journal.read(since=crash.index[14]) if e["type"] == "decision" and "order" in e]
    assert after and all(e["decision"] == "blocked" for e in after if not e["order"]["reduces_risk"])


def test_tripping_the_latch_with_nothing_there_still_writes_it_once(setup):
    setup[1].write_text(json.dumps({"max_daily_loss_pct": 50.0, "max_drawdown_pct": 3.0, "max_position_pct": 0.6, "max_open_positions": 5}))
    px, t, s = prices(), trader(setup), strat()
    crash = px.copy()
    crash.iloc[14:, 0] = crash.iloc[13, 0] * 0.80
    crash.iloc[14:, 1] = crash.iloc[13, 1] * 1.20
    for k in range(11, 20):
        t.step(crash.iloc[: k + 1], {7: s})
    assert json.loads(t.latch_file.read_text())["drawdown_pct"] >= 3.0 and not t.latch_file.is_symlink()
    assert [e["decision"] for e in t.journal.read() if e["type"] == "kill_switch"] == ["latched"]


# ---- T20 rev 2: a skipped line is never silent where a figure needs every entry

def _torn_after_a_buy(directory):
    j = pp.Journal(directory)
    j.write("2026-03-02", {"type": "fill", "symbol": "XLE", "units": 10, "price": 100.0, "fee": 0.0, "strategy": "s"})
    with (directory / "2026-03-03.jsonl").open("w") as fh:
        fh.write('{"type": "fill", "day": "2026-03-03", "symbol": "XLE", "units": -10, "price": 9')       # the sale, cut short
    return j


def test_read_complete_names_the_file_and_line_of_a_skipped_line_and_never_its_text(tmp_path):
    j = _torn_after_a_buy(tmp_path / "journal")
    with pytest.raises(pp.JournalDamaged) as err:
        j.read_complete()
    text = str(err.value)
    assert isinstance(err.value, ValueError) and "2026-03-03.jsonl line 1" in text and "\n" not in text
    assert "XLE" not in text and "units" not in text and err.value.skipped == [{"file": "2026-03-03.jsonl", "line": 1}]
    assert [e["day"] for e in j.read_complete(until="2026-03-02")] == ["2026-03-02"]       # a clean stretch reads as before


def test_a_torn_sell_line_makes_the_paper_trader_mark_tax_unavailable_and_say_why(setup, tmp_path):
    _torn_after_a_buy(tmp_path / "damaged")
    t = pp.PaperTrader(tmp_path / "damaged", setup[1], halt_file=None, tax_rules=RULES)
    assert t.tax_ledger is None
    assert t.tax_unavailable.startswith("the tax ledger could not take a fill: ")
    assert "2026-03-03.jsonl line 1" in t.tax_unavailable and "XLE" not in t.tax_unavailable
    px = prices()
    out = t.step(px.iloc[:12], {7: strat()})
    assert out["tax"] == {"unavailable": t.tax_unavailable}


def test_the_days_summary_counts_skipped_lines_of_the_last_30_days_only_when_there_are_some(setup, tmp_path):
    px, s = prices(), strat()
    clean = pp.PaperTrader(tmp_path / "clean", setup[1], halt_file=None)
    assert "skipped_journal_lines" not in clean.step(px.iloc[:12], {7: s})
    t = pp.PaperTrader(tmp_path / "damaged", setup[1], halt_file=None)
    near, far = str(px.index[5].date()), str(px.index[11].date() - pd.Timedelta(days=60))
    (t.dir / f"{near}.jsonl").write_text('{"type": "note", "n": 1}\n{"type": "no\n')
    (t.dir / f"{far}.jsonl").write_text('{"type": "no\n')                                   # too old to count
    out = t.step(px.iloc[:12], {7: s})
    assert out["skipped_journal_lines"] == 1
    assert [e["skipped_journal_lines"] for e in t.journal.read() if e["type"] == "summary"] == [1]


def test_reset_latch_on_a_directory_says_it_is_a_directory(setup):
    t = _tripped(setup)
    t.latch_file.mkdir(parents=True)
    with pytest.raises(pp.LatchResetError) as err:
        t.reset_latch("burton", "2026-03-02")
    assert "directory" in str(err.value) and str(t.latch_file) in str(err.value) and "\n" not in str(err.value)


# ---- T29: a held fund with no price that day is valued at its last price, never at nothing; it is not traded; the
# book adds up every day

def _adds_up(summaries, start=100_000.0):
    """Invariant 3: the strategies' value changes sum to the book's equity change, every day, to relative 1e-9."""
    previous = start
    for s in summaries:
        change = sum(s["strategy_value_change"].values())
        assert abs(change - (s["equity"] - previous)) <= 1e-9 * max(1.0, abs(s["equity"])), s["day"]
        previous = s["equity"]


def _held_now(t):
    return {sym for book in t.state["positions"].values() for sym, lot in book.items() if lot["units"]}


def _schedule_rules(schedules):
    """A `run_rules` stand-in: strategy `name`'s target weights on bar i are `schedules[name][i]`."""
    def run(s, prices):
        w = pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
        for sym, weight in schedules[s.name][len(prices) - 1].items():
            w.iloc[-1, w.columns.get_loc(sym)] = weight
        return w, []
    return run


GAP_KINDS = ("one day", "several days", "several funds", "first day held", "last day")
LEVELS = (-0.4, 0.0, 0.4)                     # weight steps of 0.4 against a 15% no-trade band: trades happen only when a
                                              # schedule changes, never from drift


def _gap_history(seed, values=None):
    """A seeded history: 2 to 4 funds, 1 to 3 scripted strategies joining on decision days, and gaps of the seed's kind
    (and maybe a second kind) placed where neither history would trade the fund: never on a decision day or the day its
    orders fill. Returns prices with gaps, the same prices gap-filled with the last known price, the schedules, the join
    days, the gaps by day and the kinds used."""
    rng = np.random.default_rng(seed)
    k, n = int(rng.integers(2, 5)), int(rng.integers(16, 23))
    funds = [f"G{i}" for i in range(k)]
    px = pd.DataFrame({f: 50 * np.exp(np.cumsum(0.012 * rng.standard_normal(n))) for f in funds},
                      index=pd.bdate_range("2026-02-02", periods=n))
    decide = [0]
    while decide[-1] + 6 < n - 2 and len(decide) < 4:
        decide.append(decide[-1] + int(rng.integers(3, 6)))
    busy = set(decide) | {d + 1 for d in decide}
    names = [f"strat_{j}" for j in range(int(rng.integers(1, 4)))]
    joins = {names[0]: 0, **{nm: int(rng.choice(decide)) for nm in names[1:]}}
    markets, schedules = {}, {}
    for nm in names:
        markets[nm] = sorted(rng.choice(funds, size=int(rng.integers(1, k + 1)), replace=False).tolist())
        weights, now = [], {f: 0.0 for f in markets[nm]}
        for i in range(n):
            if i in decide and i >= joins[nm]:
                now = {f: float(rng.choice(LEVELS)) for f in markets[nm]}
                if i == joins[nm] and not any(now.values()):
                    now[markets[nm][0]] = 0.4                      # every strategy holds something from its first day
            weights.append(dict(now))
        schedules[nm] = weights
    free = [i for i in range(1, n) if i not in busy]
    gaps: dict[int, set[str]] = {}
    kinds = [GAP_KINDS[seed % len(GAP_KINDS)]] + ([str(rng.choice(GAP_KINDS))] if rng.random() < 0.5 else [])
    used = []
    for kind in kinds:
        if kind == "one day":
            gaps.setdefault(int(rng.choice(free)), set()).add(str(rng.choice(funds)))
        elif kind == "several days":
            start, f = int(rng.choice(free)), str(rng.choice(funds))
            for i in range(start, min(n, start + int(rng.integers(2, 5)))):
                if i in free:
                    gaps.setdefault(i, set()).add(f)
        elif kind == "several funds":
            gaps.setdefault(int(rng.choice(free)), set()).update(rng.choice(funds, size=min(k, int(rng.integers(2, 4))), replace=False).tolist())
        elif kind == "first day held":
            held = [(min(i for i in decide if any(schedules[nm][i].get(f) for nm in names)) + 2, f) for f in funds
                    if any(schedules[nm][i].get(f) for nm in names for i in decide)]
            held = [(i, f) for i, f in held if i in free]
            if not held:
                continue
            i, f = held[int(rng.integers(len(held)))]
            gaps.setdefault(i, set()).add(f)
        elif kind == "last day" and n - 1 in free:
            gaps.setdefault(n - 1, set()).add(str(rng.choice(funds)))
        else:
            continue
        used.append(kind)
    gapped = px.copy()
    for i, fs in gaps.items():
        for f in fs:
            if values is None:
                bad = np.inf if rng.random() < 0.2 else np.nan
            else:                                                  # zero, negative, not a number, infinite: all no price
                bad = (0.0, -float(px.iloc[i][f]), np.nan, np.inf)[int(rng.integers(4))]
            gapped.iloc[i, gapped.columns.get_loc(f)] = bad
    filled = gapped.where(np.isfinite(gapped) & (gapped > 0)).ffill()
    strategies = {nm: long_only(nm, market=tuple(markets[nm])) for nm in names}
    return gapped, filled, schedules, joins, strategies, gaps, used


def _run(directory, limits, frame, strategies, joins, after_step=None):
    t = pp.PaperTrader(directory, limits, halt_file=None, min_trade_pct=0.15)
    out = []
    for i in range(len(frame)):
        active = {j + 1: s for j, (nm, s) in enumerate(strategies.items()) if joins[nm] <= i}
        out.append(t.step(frame.iloc[: i + 1], active))
        if after_step is not None:
            after_step(t, i, out[-1])
    return t, out


def _drop(*traders):
    """Remove the checked histories' journals: hundreds of them would otherwise fill the temporary folder."""
    for t in traders:
        shutil.rmtree(t.dir)


SAME = ("equity", "strategy_values", "strategy_value_change", "capital_per_strategy", "benchmark_values", "daily_loss_pct",
        "drawdown_pct", "open_positions", "orders_placed", "orders_blocked")


@pytest.mark.parametrize("block", range(6))
def test_a_history_with_price_gaps_values_the_book_exactly_as_the_same_history_gap_filled(setup, tmp_path, monkeypatch, block):
    """Invariants 1 to 3 on 300 seeded histories (50 a block), every gap kind, against the same history gap-filled. The
    limits refuse nothing here: a refused order is retried every day, on a gap day too, and that retry is a decision about
    a fund without a price, which the history with gaps rightly never makes."""
    limits = tmp_path / "loose.json"
    limits.write_text(json.dumps({"max_daily_loss_pct": 90.0, "max_drawdown_pct": 90.0, "max_position_pct": 1.0, "max_open_positions": 20}))
    kinds: dict[str, int] = {}
    carried = 0
    for seed in range(block * 50, block * 50 + 50):
        gapped, filled, schedules, joins, strategies, gaps, used = _gap_history(seed)
        for kind in used:
            kinds[kind] = kinds.get(kind, 0) + 1
        monkeypatch.setattr(pp, "run_rules", _schedule_rules(schedules))
        stale_seen = {}

        def check(t, i, summary):
            stale_seen[i] = (sorted(_held_now(t) & gaps.get(i, set())), summary.get("stale_prices"))

        tg, a = _run(tmp_path / f"gap{seed}", limits, gapped, strategies, joins, after_step=check)
        tf, b = _run(tmp_path / f"filled{seed}", limits, filled, strategies, joins)
        for x, y in zip(a, b):
            assert {key: x[key] for key in SAME} == {key: y[key] for key in SAME}, (seed, x["day"], used)
            assert x["orders_blocked"] == 0
            assert "stale_prices" not in y
        _adds_up(a)
        _adds_up(b)
        for t in (tg, tf):                                          # nothing traded a gapped fund on its gap day
            for e in t.journal.read():
                i = gapped.index.get_loc(pd.Timestamp(e["day"]))
                if e.get("symbol") in gaps.get(i, set()):
                    assert e["type"] not in ("fill", "decision"), (seed, e)
        assert tg.state["positions"] == tf.state["positions"] and tg.state["cash"] == tf.state["cash"]
        for i, (want, named) in stale_seen.items():                 # named when not empty, and only then
            assert named == ({f: _carried_run(gapped, f, i) for f in want} or None), (seed, i)
        carried += sum(bool(want) for want, _ in stale_seen.values())
        _drop(tg, tf)
    assert all(kinds.get(kind, 0) >= 8 for kind in GAP_KINDS), kinds
    assert carried >= 40, carried                                  # the gaps did fall on held funds, many times


def _carried_run(frame, fund, i):
    """Bars up to and including bar i on which `fund` has no price that is a finite number above zero, counted back."""
    n = 0
    while i - n >= 0 and not (np.isfinite(frame[fund].iloc[i - n]) and frame[fund].iloc[i - n] > 0):
        n += 1
    return n


def _two_funds_one_day_gap(setup, monkeypatch, fill_gap=False, **kw):
    """AAA and BBB; the strategy buys both on bar 30, they fill on bar 31; BBB has no price on bar 33."""
    px = scripted_market()
    px["BBB"] = 50.0 + np.arange(len(px)) * 0.25
    if not fill_gap:
        px.iloc[DAY0 + 3, px.columns.get_loc("BBB")] = np.nan
    else:
        px.iloc[DAY0 + 3, px.columns.get_loc("BBB")] = px["BBB"].iloc[DAY0 + 2]
    monkeypatch.setattr(pp, "run_rules", _schedule_rules({"two": [{"AAA": 0.3, "BBB": 0.3}] * len(px)}))
    t = no_costs(setup, min_trade_pct=0.15, **kw)
    s = long_only("two", market=("AAA", "BBB"))
    out = [t.step(px.iloc[: DAY0 + i + 1], {7: s}) for i in range(5)]
    return t, out, px


def test_a_held_fund_with_no_price_keeps_its_last_price_in_equity_strategy_value_and_capital(setup, monkeypatch, tmp_path):
    t, out, px = _two_funds_one_day_gap(setup, monkeypatch)
    lots = t.state["positions"]["two"]
    gap = out[3]
    carried = float(px["BBB"].iloc[DAY0 + 2])                      # the last price the book used for BBB
    assert gap["equity"] == pytest.approx(out[2]["equity"] + lots["AAA"]["units"] * (float(px["AAA"].iloc[DAY0 + 3]) - float(px["AAA"].iloc[DAY0 + 2])))
    assert gap["strategy_values"]["two"] == pytest.approx(lots["AAA"]["units"] * (float(px["AAA"].iloc[DAY0 + 3]) - lots["AAA"]["cost"])
                                                          + lots["BBB"]["units"] * (carried - lots["BBB"]["cost"]))
    assert gap["capital_per_strategy"] == gap["equity"]
    _adds_up(out)
    assert gap["stale_prices"] == {"BBB": 1} and all("stale_prices" not in o for i, o in enumerate(out) if i != 3)
    twin, filled, _ = _two_funds_one_day_gap(setup, monkeypatch, fill_gap=True, directory=tmp_path / "filled")
    assert [o["equity"] for o in out] == [o["equity"] for o in filled]
    assert [o["benchmark_values"] for o in out] == [o["benchmark_values"] for o in filled]


def test_on_a_day_with_no_price_the_fund_is_not_traded_and_its_order_waits(setup, monkeypatch, tmp_path):
    """Invariant 2: AAA and BBB are bought on bar 30; BBB has no price on bar 31, the fill day. BBB's order stays pending,
    no fill, no tax lot, no realised result; its wanted units are carried; AAA fills as usual; BBB fills on bar 32."""
    px = scripted_market()
    px["BBB"] = 50.0 + np.arange(len(px)) * 0.25
    px.iloc[DAY0 + 1, px.columns.get_loc("BBB")] = np.nan
    monkeypatch.setattr(pp, "run_rules", _schedule_rules({"two": [{"AAA": 0.3, "BBB": 0.3}] * len(px)}))
    t, s = no_costs(setup, tax_rules=RULES, min_trade_pct=0.15), long_only("two", market=("AAA", "BBB"))
    t.step(px.iloc[: DAY0 + 1], {7: s})
    wanted = t.wanted_units()
    day1 = t.step(px.iloc[: DAY0 + 2], {7: s})
    fills = [e for e in t.journal.read() if e["type"] == "fill"]
    assert [f["symbol"] for f in fills] == ["AAA"]
    assert [o["symbol"] for o in t.state["pending"]] == ["BBB"] and t.wanted_units() == wanted
    assert t.state["positions"]["two"]["BBB"]["units"] == 0.0 and t.state["realized"]["two"] == 0.0
    assert t.tax_ledger.open_lots("BBB") == [] and len(t.tax_ledger.open_lots("AAA")) == 1
    assert not any(e.get("symbol") == "BBB" for e in t.journal.read(since=px.index[DAY0 + 1]) if e["type"] == "decision")
    assert "stale_prices" not in day1                               # BBB was not held yet: nothing carried
    t.step(px.iloc[: DAY0 + 3], {7: s})
    bbb = [e for e in t.journal.read() if e["type"] == "fill" and e["symbol"] == "BBB"]
    assert len(bbb) == 1 and bbb[0]["price"] == pytest.approx(float(px["BBB"].iloc[DAY0 + 2])) and t.state["pending"] == []


def test_a_held_fund_with_no_earlier_price_in_the_data_is_valued_at_its_cost(setup, monkeypatch):
    t, out, px = _two_funds_one_day_gap(setup, monkeypatch)
    lot = t.state["positions"]["two"]["BBB"]
    blank = px.iloc[: DAY0 + 6].copy()
    blank["BBB"] = np.nan                                           # a frame that has no price for BBB at all
    later = t.step(blank, {7: long_only("two", market=("AAA", "BBB"))})
    aaa = t.state["positions"]["two"]["AAA"]
    # rev 5 (outcome 1): not its cost but the last valid close the book itself valued it at (bar 34)
    seen = float(px["BBB"].iloc[DAY0 + 4])
    assert later["equity"] == pytest.approx(t.state["cash"] + aaa["units"] * float(px["AAA"].iloc[DAY0 + 5]) + lot["units"] * seen)
    _adds_up(out + [later])
    assert later["stale_prices"] == {"BBB": DAY0 + 6}                # every bar of the frame given lacks it


class _Clock(pp.datetime):
    @classmethod
    def now(cls, tz=None):
        return pp.datetime(2026, 1, 1, tzinfo=tz)


def _plain_history(seed, directory, limits):
    """A gap-free seeded history with real momentum strategies (long-only and long-short) joining on different days and
    a restart from the state file half-way; returns the bytes of every journal file, the state file and the summaries."""
    rng = np.random.default_rng(seed)
    funds = [f"P{i}" for i in range(int(rng.integers(2, 4)))]
    n = int(rng.integers(24, 32))
    px = pd.DataFrame({f: 80 * np.exp(np.cumsum(float(rng.normal(0, 0.004)) + 0.015 * rng.standard_normal(n))) for f in funds},
                      index=pd.bdate_range("2026-03-02", periods=n))
    strategies = {}
    for j in range(int(rng.integers(1, 4))):
        strategies[j + 1] = (int(rng.integers(11, 16)), st.RuleStrategy.from_spec({
            "name": f"mom_{j}", "version": "1.0.0", "hypothesis": "Assets that rose over ten days keep rising",
            "market": sorted(rng.choice(funds, size=int(rng.integers(1, len(funds) + 1)), replace=False).tolist()),
            "entry": {"rule": "momentum", "lookback": int(rng.integers(5, 11)), "enter": float(rng.choice([0.01, 0.02, 0.03])),
                      "exit": float(rng.choice([0.0, -0.01])), "direction": str(rng.choice(["both", "long"]))},
            "sizing": {"rule": "equal"}, "risk": {"max_position_pct": float(rng.choice([0.3, 0.5])), "max_open_positions": 3,
                                                   "stop_loss_pct": None}}))
    kw = {"fee_bps": float(rng.choice([0.0, 1.0, 5.0])), "slippage_bps": float(rng.choice([0.0, 2.0])),
          "min_trade_pct": float(rng.choice([0.0005, 0.002, 0.01])), "halt_file": None}
    t, summaries, restart = pp.PaperTrader(directory, limits, **kw), [], int(rng.integers(13, n - 2))
    for i in range(11, n):
        if i == restart:
            t = pp.PaperTrader(directory, limits, **kw)            # the nightly job continues a state file
        summaries.append(t.step(px.iloc[: i + 1], {h: s for h, (join, s) in strategies.items() if join <= i}))
    files = sorted(p for p in directory.iterdir() if p.is_file() and p.name != "paper_state.lock")   # rev 4: the lock, empty
    assert not (directory / "paper_state.lock").exists() or (directory / "paper_state.lock").read_bytes() == b""
    out = [(p.name, p.read_bytes()) for p in files]
    for k, (name, body) in enumerate(out):                         # rev 5: the remembered closes, today's for every fund
        if name == "paper_state.json":                             # held, are the one key the base does not write
            state = json.loads(body)
            held = sorted({sym for book in state["positions"].values() for sym, lot in book.items() if lot["units"]})
            assert state.pop("last_valid_prices", {}) == {sym: {"price": float(px[sym].iloc[-1]), "day": str(px.index[-1].date())}
                                                          for sym in held}
            out[k] = (name, json.dumps(state, indent=1, default=str).encode())
    return out, json.dumps(summaries).encode()


# sha-256 of 200 gap-free histories as the base (7837ac4) writes them: every journal file and state file byte for byte,
# and every summary. Re-pin only in a change that means to alter what the paper trader writes on a day with every price.
PLAIN_DIGEST = "8949314e8489f08b19a12deb02632f68596ba7b43cced2328fcd3453f2c82244"


def test_where_no_price_is_missing_every_journal_entry_state_file_and_summary_is_as_before(setup, tmp_path, monkeypatch):
    """Invariant 4 on 200 gap-free histories (with a restart from the state file in each): byte for byte the base."""
    import hashlib
    monkeypatch.setattr(pp, "datetime", _Clock)
    digest = hashlib.sha256()
    for seed in range(200):
        files, summaries = _plain_history(seed, tmp_path / f"h{seed}", setup[1])
        shutil.rmtree(tmp_path / f"h{seed}")                          # checked: keep the disk free
        for name, body in files:
            digest.update(name.encode() + b"\0" + body + b"\0")
        digest.update(summaries + b"\1")
        assert b"stale_prices" not in summaries
    assert digest.hexdigest() == PLAIN_DIGEST


# ---- T29 rev 2: a price is a finite number above zero; `stale_prices` says for how many days

@pytest.mark.parametrize("block", range(6))
def test_zero_negative_and_missing_prices_mixed_value_the_book_as_the_same_history_gap_filled(setup, tmp_path, monkeypatch, block):
    """Outcomes 1 and 2 on 300 seeded histories (50 a block): every gap kind, each bad cell a zero, a negative number, not
    a number or infinite, against the same history with each bad cell filled by the last price above zero."""
    limits = tmp_path / "loose.json"
    limits.write_text(json.dumps({"max_daily_loss_pct": 90.0, "max_drawdown_pct": 90.0, "max_position_pct": 1.0, "max_open_positions": 20}))
    carried, kinds, values = 0, {}, set()
    for seed in range(block * 50, block * 50 + 50):
        gapped, filled, schedules, joins, strategies, gaps, used = _gap_history(seed, values="mixed")
        for kind in used:
            kinds[kind] = kinds.get(kind, 0) + 1
        for i, fs in gaps.items():
            values |= {"zero" if v == 0 else "negative" if v < 0 else "infinite" if np.isinf(v) else "nan"
                       for v in (float(gapped[f].iloc[i]) for f in fs)}
        monkeypatch.setattr(pp, "run_rules", _schedule_rules(schedules))
        seen = {}
        tg, a = _run(tmp_path / f"gap{seed}", limits, gapped, strategies, joins,
                     after_step=lambda t, i, summary: seen.__setitem__(i, (sorted(_held_now(t) & gaps.get(i, set())), summary.get("stale_prices"))))
        tf, b = _run(tmp_path / f"filled{seed}", limits, filled, strategies, joins)
        for x, y in zip(a, b):
            assert {key: x[key] for key in SAME} == {key: y[key] for key in SAME}, (seed, x["day"], used)
            assert x["orders_blocked"] == 0 and "stale_prices" not in y
            assert all(np.isfinite(v) for v in x["benchmark_values"].values())
        _adds_up(a)
        _adds_up(b)
        for e in tg.journal.read():                                 # nothing traded a fund on a day it had no price
            i = gapped.index.get_loc(pd.Timestamp(e["day"]))
            if e.get("symbol") in gaps.get(i, set()):
                assert e["type"] not in ("fill", "decision"), (seed, e)
            if e["type"] == "fill":
                assert e["price"] > 0, (seed, e)
        assert tg.state["positions"] == tf.state["positions"] and tg.state["cash"] == tf.state["cash"]
        assert tg.state["realized"] == tf.state["realized"]
        for i, (want, named) in seen.items():
            assert named == ({f: _carried_run(gapped, f, i) for f in want} or None), (seed, i)
        carried += sum(bool(want) for want, _ in seen.values())
        _drop(tg, tf)
    assert all(kinds.get(kind, 0) >= 8 for kind in GAP_KINDS), kinds
    assert values == {"zero", "negative", "infinite", "nan"} and carried >= 40, (values, carried)


def _exit_onto(close, setup, monkeypatch, tmp_path):
    """AAA bought on bar 30 (fills on 31 at 100), sold on bar 32 at 80; the fill day, bar 33, closes at `close`."""
    px = scripted_market()
    px.iloc[DAY0 + 3, 0] = close
    weights = [{"AAA": 0.5}] * (DAY0 + 2) + [{"AAA": 0.0}] * (len(px) - DAY0 - 2)
    monkeypatch.setattr(pp, "run_rules", _schedule_rules({"tax_paper": weights}))
    t, s = no_costs(setup, tax_rules=RULES), long_only()
    return t, [t.step(px.iloc[: DAY0 + i + 1], {7: s}) for i in range(6)], px


@pytest.mark.parametrize("close", [0.0, -3.0])
def test_a_close_of_zero_or_below_is_no_price_the_sell_waits_and_the_book_is_carried(setup, monkeypatch, tmp_path, close):
    t, out, px = _exit_onto(close, setup, monkeypatch, tmp_path)
    fills = [e for e in t.journal.read() if e["type"] == "fill"]
    sell = [f for f in fills if f["units"] < 0]
    assert len(sell) == 1 and sell[0]["day"] == str(px.index[DAY0 + 4].date()) and sell[0]["price"] == pytest.approx(80.0)
    day = out[3]                                                    # bar 33: the close was no price
    assert day["equity"] == pytest.approx(out[2]["equity"]) and day["daily_loss_pct"] == 0.0 and day["drawdown_pct"] == out[2]["drawdown_pct"]
    assert day["stale_prices"] == {"AAA": 1} and "stale_prices" not in out[4]
    assert all(np.isfinite(o["benchmark_values"]["tax_paper"]) for o in out)
    assert not t.latch_file.exists() and not any(e["type"] == "kill_switch" for e in t.journal.read())
    assert t.state["realized"]["tax_paper"] == pytest.approx(500 * (80.0 - 100.0))       # the real loss, once the price is back
    assert t.tax_ledger.open_lots("AAA") == [] and len([f for f in fills if f["day"] == str(px.index[DAY0 + 3].date())]) == 0
    _adds_up(out)


def test_stale_prices_counts_the_consecutive_days_each_held_fund_has_been_carried(setup, monkeypatch, tmp_path):
    px = scripted_market()
    px["BBB"] = 50.0 + np.arange(len(px)) * 0.25
    px.iloc[DAY0 + 3, px.columns.get_loc("BBB")] = np.nan
    px.iloc[DAY0 + 4, px.columns.get_loc("BBB")] = 0.0
    px.iloc[DAY0 + 4, px.columns.get_loc("AAA")] = -1.0
    px.iloc[DAY0 + 5, px.columns.get_loc("BBB")] = np.inf
    monkeypatch.setattr(pp, "run_rules", _schedule_rules({"two": [{"AAA": 0.3, "BBB": 0.3}] * len(px)}))
    t, s = no_costs(setup, min_trade_pct=0.15), long_only("two", market=("AAA", "BBB"))
    out = [t.step(px.iloc[: DAY0 + i + 1], {7: s}) for i in range(7)]
    assert [o.get("stale_prices") for o in out] == [None, None, None, {"BBB": 1}, {"AAA": 1, "BBB": 2}, {"BBB": 3}, None]
    assert list(out[4]["stale_prices"]) == ["AAA", "BBB"]                       # by fund name
    assert "stale_prices" not in json.dumps({k: v for k, v in t.state.items() if k != "history"})   # nothing new kept
    _adds_up(out)


# ---- T29 rev 3: a price must be believable: in range, and not a jump the next day does not confirm

def _judged(col):
    """The ticket's rule, written out independently: each close of one fund is 'valid', 'suspect' or 'invalid'."""
    def in_range(x):
        return isinstance(x, float) and np.isfinite(x) and 0.0001 <= x <= 10_000_000
    out, last = [], None
    for t, v in enumerate(col):
        if not in_range(v):
            out.append("invalid")
            continue
        prev = next((p for p in reversed(col[:t]) if in_range(p)), None)      # rev 4: the fund's last priced row
        if (last is None or (v <= 5 * last and 5 * v >= last)
                or (prev is not None and v <= 5 * prev and 5 * v >= prev)):
            out.append("valid")
            last = v
        else:
            out.append("suspect")
    return out


BELIEVABLE = ("tiny", "huge", "jump and return", "jump and stay", "zero", "negative", "missing")


def _believable_history(seed):
    """`_gap_history`'s funds, strategies and gap days, each bad cell now one of BELIEVABLE. Returns the history, its twin
    (every invalid or suspect close replaced by the last valid one), the cells judged not valid by day, the suspect
    cells by day with the value seen, and the kinds used."""
    _, filled, schedules, joins, strategies, gaps, _ = _gap_history(seed)
    rng = np.random.default_rng(10_000 + seed)
    frame, used, jumped = filled.copy(), set(), set()
    for i in sorted(gaps):
        for f in sorted(gaps[i]):
            kind = BELIEVABLE[int(rng.integers(len(BELIEVABLE)))]
            if kind.startswith("jump") and ((i - 1, f) in jumped or (kind == "jump and stay" and (gaps.get(i + 1) or gaps.get(i + 2)))):
                kind = "missing"     # a move that stays makes the next day a trading day: no other bad close may fall on it
            j, last = frame.columns.get_loc(f), float(frame[f].iloc[i])
            if kind == "jump and stay":
                frame.iloc[i:, j] = frame.iloc[i:, j] / 7.0
            else:
                frame.iloc[i, j] = {"tiny": float(rng.choice([1e-300, 5e-5])), "huge": float(rng.choice([2e7, 1e300])),
                                    "jump and return": last * float(rng.choice([7.0, 1 / 7.0])), "zero": 0.0,
                                    "negative": -last, "missing": np.nan}[kind]
            if kind.startswith("jump"):
                jumped.add((i, f))
            used.add(kind)
    bad, suspect = {}, {}
    twin = frame.copy()
    for f in frame.columns:
        marks = _judged([float(v) for v in frame[f]])
        for i, m in enumerate(marks):
            if m != "valid":
                bad.setdefault(i, set()).add(f)
                twin.iloc[i, twin.columns.get_loc(f)] = np.nan
            if m == "suspect":
                suspect.setdefault(i, {})[f] = float(frame[f].iloc[i])
    twin = twin.ffill()
    assert all(f in gaps.get(i, ()) for i, fs in bad.items() for f in fs), (seed, bad, gaps)    # bad only where nothing trades
    return frame, twin, schedules, joins, strategies, bad, suspect, used


def _bad_run(frame, i):
    """Rows up to and including row i on which `fund` has no valid close, counted back, per the ticket's rule."""
    marks = {f: _judged([float(v) for v in frame[f]]) for f in frame.columns}
    def run(f):
        n = 0
        while i - n >= 0 and marks[f][i - n] != "valid":
            n += 1
        return n
    return run


def _no_nonfinite_written(t):
    for p in t.dir.iterdir():
        if p.is_file():
            body = p.read_bytes()
            assert b"NaN" not in body and b"Infinity" not in body, p.name


@pytest.mark.parametrize("block", range(6))
def test_tiny_huge_jumping_zero_negative_and_missing_closes_value_the_book_as_the_twin(setup, tmp_path, monkeypatch, block):
    """Outcome 1 on 300 seeded histories (50 a block), against the twin in which every invalid or suspect close is the last
    valid one. The twin is run with the jump test off: a move that stays is a move in the twin, taken a day late here."""
    from valedia_trader.execution_and_risk.order_step import paper_targets
    limits = tmp_path / "loose.json"
    limits.write_text(json.dumps({"max_daily_loss_pct": 90.0, "max_drawdown_pct": 90.0, "max_position_pct": 1.0, "max_open_positions": 20}))
    kinds, carried, named = set(), 0, 0
    for seed in range(block * 50, block * 50 + 50):
        frame, twin, schedules, joins, strategies, bad, suspect, used = _believable_history(seed)
        kinds |= used
        monkeypatch.setattr(pp, "run_rules", _schedule_rules(schedules))
        seen, targets = {}, {}

        def check(t, i, summary):
            seen[i] = (sorted(_held_now(t) & bad.get(i, set())), summary.get("stale_prices"), summary.get("suspect_prices"))
            targets[i] = paper_targets(t)

        tg, a = _run(tmp_path / f"gap{seed}", limits, frame, strategies, joins, after_step=check)
        monkeypatch.setattr(pp, "JUMP_FACTOR", float("inf"), raising=False)
        twin_targets = {}
        tf, b = _run(tmp_path / f"twin{seed}", limits, twin, strategies, joins,
                     after_step=lambda t, i, s: twin_targets.__setitem__(i, paper_targets(t)))
        monkeypatch.setattr(pp, "JUMP_FACTOR", 5.0, raising=False)
        for x, y in zip(a, b):
            assert {key: x[key] for key in SAME} == {key: y[key] for key in SAME}, (seed, x["day"], used)
            assert x["orders_blocked"] == 0 and "stale_prices" not in y and "suspect_prices" not in y
        assert targets == twin_targets, seed                            # what the order step is handed, day by day
        _adds_up(a)
        _adds_up(b)
        for e in tg.journal.read():
            i = frame.index.get_loc(pd.Timestamp(e["day"]))
            if e.get("symbol") in bad.get(i, set()):
                assert e["type"] not in ("fill", "decision"), (seed, e)
        _no_nonfinite_written(tg)
        run = None
        for i, (want, stale, sus) in seen.items():
            run = _bad_run(frame, i)
            assert stale == ({f: run(f) for f in want} or None), (seed, i)
            assert sus == (dict(sorted(suspect.get(i, {}).items())) or None), (seed, i)
            carried += bool(want)
            named += bool(sus)
        _drop(tg, tf)
    assert kinds == set(BELIEVABLE) and carried >= 30 and named >= 10, (kinds, carried, named)


def _one_fund(closes):
    px = scripted_market()
    px.iloc[DAY0:DAY0 + len(closes), 0] = closes
    return px


def test_a_close_of_1e_300_is_no_price_and_nothing_infinite_is_written(setup, monkeypatch):
    from valedia_trader.execution_and_risk.order_step import paper_targets
    px = _one_fund([100.0, 100.0, 1e-300, 1e-300, 100.0])
    monkeypatch.setattr(pp, "run_rules", _schedule_rules({"tax_paper": [{"AAA": 0.5}] * len(px)}))
    t, s = no_costs(setup, min_trade_pct=0.002), long_only()
    out = [t.step(px.iloc[: DAY0 + i + 1], {7: s}) for i in range(5)]
    assert [o.get("stale_prices") for o in out] == [None, None, {"AAA": 1}, {"AAA": 2}, None]
    assert all(abs(u) < 1e6 for book in t.wanted_units().values() for u in book.values())
    assert out[2]["equity"] == pytest.approx(out[1]["equity"]) and out[3]["equity"] == pytest.approx(out[1]["equity"])
    assert all(abs(o["units"]) < 1e6 for o in t.state["pending"])
    assert paper_targets(t).equity == pytest.approx(out[4]["equity"])
    _no_nonfinite_written(t)
    _adds_up(out)


def test_a_one_day_jump_is_never_taken_and_a_jump_that_stays_is_taken_a_day_late(setup, monkeypatch):
    px = _one_fund([100.0, 100.0, 100.0, 900.0, 100.0, 15.0, 15.0, 15.0])
    monkeypatch.setattr(pp, "run_rules", _schedule_rules({"tax_paper": [{"AAA": 0.5}] * len(px)}))
    t, s = no_costs(setup, min_trade_pct=0.45), long_only()         # a 45% band: the book buys once, then only holds
    out = [t.step(px.iloc[: DAY0 + i + 1], {7: s}) for i in range(8)]
    units = t.state["positions"]["tax_paper"]["AAA"]["units"]
    assert units == pytest.approx(500.0)
    assert [o.get("suspect_prices") for o in out] == [None, None, None, {"AAA": 900.0}, None, {"AAA": 15.0}, None, None]
    assert [o.get("stale_prices") for o in out] == [None, None, None, {"AAA": 1}, None, {"AAA": 1}, None, None]
    assert out[3]["equity"] == pytest.approx(out[2]["equity"])              # the glitch: never taken
    assert out[5]["equity"] == pytest.approx(out[4]["equity"])              # the move: carried one day ...
    assert out[6]["equity"] == pytest.approx(out[4]["equity"] - 500 * 85.0)  # ... and taken the next
    _adds_up(out)


def test_a_text_column_does_not_stop_the_night_and_is_named(setup, monkeypatch):
    px = scripted_market()
    px["BBB"] = 50.0 + np.arange(len(px)) * 0.25
    monkeypatch.setattr(pp, "run_rules", _schedule_rules({"two": [{"AAA": 0.3, "BBB": 0.3}] * len(px)}))
    t, s = no_costs(setup, min_trade_pct=0.15), long_only("two", market=("AAA", "BBB"))
    out = [t.step(px.iloc[: DAY0 + i + 1], {7: s}) for i in range(3)]
    noted = px.iloc[: DAY0 + 4].copy()
    noted["NOTE"] = "closed early"                                     # a text column outside every market
    out.append(t.step(noted, {7: s}))
    assert out[-1]["unreadable_prices"] == ["NOTE"] and "stale_prices" not in out[-1]
    texty = px.iloc[: DAY0 + 5].astype(object)
    texty["BBB"] = "n/a"                                               # a held fund's column is text: no price on any day
    out.append(t.step(texty, {7: s}))
    lot = t.state["positions"]["two"]["BBB"]
    assert out[-1]["unreadable_prices"] == ["BBB"] and out[-1]["stale_prices"] == {"BBB": DAY0 + 5}
    aaa = t.state["positions"]["two"]["AAA"]
    seen = float(px["BBB"].iloc[DAY0 + 3])                             # rev 5 (outcome 1): the close the book last valued it at
    assert out[-1]["equity"] == pytest.approx(t.state["cash"] + aaa["units"] * 80.0 + lot["units"] * seen)
    assert "unreadable_prices" not in out[2]
    _adds_up(out)


def test_a_fund_with_no_column_is_counted_as_carried_on_every_row(setup, monkeypatch):
    t, out, px = _two_funds_one_day_gap(setup, monkeypatch)
    without = px.iloc[: DAY0 + 6].drop(columns=["BBB"])
    monkeypatch.setattr(pp, "run_rules", _schedule_rules({"two": [{"AAA": 0.3}] * len(px)}))
    later = t.step(without, {7: long_only("two", market=("AAA",))})
    assert later["stale_prices"] == {"BBB": DAY0 + 6}
    lot = t.state["positions"]["two"]["BBB"]
    aaa = t.state["positions"]["two"]["AAA"]
    seen = float(px["BBB"].iloc[DAY0 + 4])                             # rev 5 (outcome 1): the close the book last valued it at
    assert later["equity"] == pytest.approx(t.state["cash"] + aaa["units"] * float(px["AAA"].iloc[DAY0 + 5]) + lot["units"] * seen)
    _adds_up(out + [later])


class _Recording(FakePanel):
    def evaluate(self, case_id, case):
        return super().evaluate(case_id, json.loads(json.dumps(case, default=str)))


def _real_rules_history(seed, directory, limits, frame, panel):
    rng = np.random.default_rng(seed)
    strategies = {j + 1: st.RuleStrategy.from_spec({
        "name": f"mom_{j}", "version": "1.0.0", "hypothesis": "Assets that rose over ten days keep rising",
        "market": list(frame.columns),
        "entry": {"rule": "momentum", "lookback": int(rng.integers(5, 8)), "enter": 0.01, "exit": 0.0, "direction": "both"},
        "sizing": {"rule": "equal"}, "risk": {"max_position_pct": 0.4, "max_open_positions": 3, "stop_loss_pct": 0.05}})
        for j in range(2)}
    t = pp.PaperTrader(directory, limits, halt_file=None, min_trade_pct=0.002, panel=panel, panel_mode="shadow",
                       facts=lambda p, sym: {"recent": p[sym].dropna().round(6).tolist()[-6:], "missing": int(p[sym].isna().sum())})
    for i in range(8, len(frame)):
        t.step(frame.iloc[: i + 1], strategies)
    entries = [{k: v for k, v in e.items() if k not in ("timestamp", "suspect_prices")} for e in t.journal.read()]
    return entries, [c for c in panel.cases]


def test_real_rule_strategies_their_market_conditions_and_the_panel_read_a_bad_close_as_missing(setup, tmp_path):
    """Outcome 4: with real rule strategies, a history whose bad closes are zero, negative, tiny, huge or a one-day jump
    writes exactly what the same history with those closes missing writes: the rules, the market conditions and the
    panel's facts are all given the cleaned prices."""
    differs = 0
    for seed in range(30):
        rng = np.random.default_rng(500 + seed)
        n = 34
        px = pd.DataFrame({f: 60 * np.exp(np.cumsum(0.02 * rng.standard_normal(n))) for f in ("R1", "R2", "R3")},
                          index=pd.bdate_range("2026-04-01", periods=n))
        bad, missing = px.copy(), px.copy()
        for _ in range(3):
            i, f = int(rng.integers(9, n)), str(rng.choice(px.columns))
            if bad[f].iloc[i - 1] != px[f].iloc[i - 1] or bad[f].iloc[min(i + 1, n - 1)] != px[f].iloc[min(i + 1, n - 1)]:
                continue                                               # one bad close at a time, between good ones
            bad.loc[px.index[i], f] = float(rng.choice([0.0, -1.0, 1e-300, 1e300, float(px[f].iloc[i - 1]) * 8.0]))
            missing.loc[px.index[i], f] = np.nan
        a = _real_rules_history(seed, tmp_path / f"bad{seed}", setup[1], bad, _Recording())
        b = _real_rules_history(seed, tmp_path / f"missing{seed}", setup[1], missing, _Recording())
        assert a == b, seed
        differs += int(not bad.equals(missing))
        shutil.rmtree(tmp_path / f"bad{seed}")
        shutil.rmtree(tmp_path / f"missing{seed}")
    assert differs >= 25


def test_an_early_history_volatility_is_written_as_null_never_as_nan(setup):
    t = trader(setup)
    t.step(prices().iloc[:2], {7: strat()})                            # two bars: no 20-day volatility can be measured
    decision = [e for e in t.journal.read() if e["type"] == "decision"][0]
    assert decision["market_conditions"]["volatility_20d"] is None
    _no_nonfinite_written(t)


# ---- T29 rev 3, outcome 6: a day cut short by a kill and run again leaves one truth

RERUN_CHILD = r'''
import json, sys, time
import numpy as np, pandas as pd
from valedia_trader.research_loop import paper as pp
from valedia_trader.research_loop import strategy as st
from valedia_trader.execution_and_risk import tax as tx
directory, limits, upto, stop_after = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
armed, count = [False], [0]
def marked(fn):
    def run(*a, **k):
        out = fn(*a, **k)
        if armed[0]:
            count[0] += 1
            print("wrote", count[0], flush=True)
            if stop_after and count[0] >= stop_after:
                time.sleep(60)                                     # the parent kills it here, between two writes
        return out
    return run
name = "append" if hasattr(pp.Journal, "append") else "write"      # every journal line, whichever writes it
setattr(pp.Journal, name, marked(getattr(pp.Journal, name)))
pp.PaperTrader._save = marked(pp.PaperTrader._save)
rng = np.random.default_rng(29)
n = 46
px = pd.DataFrame({f: 70 * np.exp(np.cumsum(0.0015 + 0.02 * rng.standard_normal(n))) for f in ("K1", "K2", "K3")},
                  index=pd.bdate_range("2026-03-02", periods=n))
s = st.RuleStrategy.from_spec({"name": "rerun_paper", "version": "1.0.0", "hypothesis": "Assets that rose keep rising",
    "market": ["K1", "K2", "K3"], "entry": {"rule": "momentum", "lookback": 5, "enter": 0.0, "exit": -0.01, "direction": "long"},
    "sizing": {"rule": "equal"}, "risk": {"max_position_pct": 0.3, "max_open_positions": 3, "stop_loss_pct": None}})
t = pp.PaperTrader(directory, limits, halt_file=None, min_trade_pct=0.001, tax_rules=tx.TaxRules.load())
for i in range(10, upto + 1):
    if t.state["last_day"] is None or str(px.index[i].date()) > t.state["last_day"]:
        armed[0], count[0] = (i == upto or not stop_after), 0      # writes are counted day by day
        print("day", i, flush=True)
        t.step(px.iloc[: i + 1], {7: s})
print("done", flush=True)
'''


def _child(script, directory, limits, upto, stop_after, env):
    import subprocess
    import sys
    return subprocess.Popen([sys.executable, "-B", "-c", script, str(directory), str(limits), str(upto), str(stop_after)],
                            env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _comparable_journal(directory):
    return [{k: v for k, v in e.items() if k != "timestamp"} for e in pp.Journal(directory).read_complete()]


def test_a_day_killed_between_its_writes_and_run_again_books_nothing_twice(setup, tmp_path):
    """Outcome 6 (a): 30 real kills, each between two of a day's writes (journal lines and state saves; among them the
    one after the day's summary and before its state is saved), each followed by a rerun of the day. The journal, the state,
    the tax ledger and the summaries end exactly as one run without kills leaves them."""
    import os
    from pathlib import Path
    root = str(Path(pp.__file__).resolve().parents[2])
    env = dict(os.environ, PYTHONPATH=root + os.pathsep + os.environ.get("PYTHONPATH", ""), PYTHONHASHSEED="0")
    reference = tmp_path / "reference"
    ref = _child(RERUN_CHILD, reference, setup[1], 45, 0, env)
    out, err = ref.communicate(timeout=240)
    assert ref.returncode == 0, err[-2000:]
    writes, day = {}, None                                          # how many writes each day makes, from the run without kills
    for line in out.splitlines():
        if line.startswith("day "):
            day = int(line.split()[1])
        elif line.startswith("wrote "):
            writes[day] = int(line.split()[1])
    killed_dir, rng, kills = tmp_path / "killed", np.random.default_rng(6), 0
    for i in range(16, 46):
        stop = writes[i] - 1 if i % 3 == 0 else int(rng.integers(1, writes[i]))     # every third: after the last line
        child = _child(RERUN_CHILD, killed_dir, setup[1], i, stop, env)
        for line in child.stdout:
            if line.strip() == f"wrote {stop}":
                child.kill()                                       # SIGKILL to the child this test started, by its pid
                kills += 1
                break
        child.wait()
        child.stdout.close()
        child.stderr.close()
        again = _child(RERUN_CHILD, killed_dir, setup[1], i, 0, env)
        _, err = again.communicate(timeout=240)
        assert again.returncode == 0, err[-2000:]
        days = [e["day"] for e in pp.Journal(killed_dir).read() if e["type"] == "summary"]
        assert len(days) == len(set(days)), (i, stop)                  # one summary a day
    assert kills == 30
    assert _comparable_journal(killed_dir) == _comparable_journal(reference)
    a, b = (json.loads((d / "paper_state.json").read_text()) for d in (killed_dir, reference))
    assert a == b and "journal_outbox" not in a
    from valedia_trader.execution_and_risk.tax import TaxLedger
    lots = [TaxLedger.from_fills(pp.Journal(d).read_complete(), RULES).summary("2026", as_of="2026-12-31") for d in (killed_dir, reference)]
    assert lots[0] == lots[1]


# ---- T29 rev 4: a column that is not numbers is never a price; one night at a time on one book; the confirmation
# looks at the fund's last priced row; the latch file appears whole; the two decisions rev 3 left untested

def _held_two(directory, limits, monkeypatch, bbb=None):
    """`two` (AAA and BBB) bought on the scripted market's first day and held for three days. Returns the trader, the
    strategy, the summaries and the full price frame. From then on the strategy wants twice as much BBB, so a fund with
    a price would trade."""
    px = scripted_market()
    px["BBB"] = 50.0 + np.arange(len(px)) * 0.25 if bbb is None else bbb
    monkeypatch.setattr(pp, "run_rules", _schedule_rules({"two": [{"AAA": 0.3, "BBB": 0.3}] * len(px)}))
    t = pp.PaperTrader(directory, limits, fee_bps=0.0, slippage_bps=0.0, halt_file=None, min_trade_pct=0.15)
    s = long_only("two", market=("AAA", "BBB"))
    out = [t.step(px.iloc[: DAY0 + i + 1], {7: s}) for i in range(3)]
    monkeypatch.setattr(pp, "run_rules", _schedule_rules({"two": [{"AAA": 0.3, "BBB": 0.6}] * len(px)}))
    return t, s, out, px


def _not_numbers(kind, n):
    return {"booleans": np.array([True, False] * n)[:n],
            "nullable booleans": pd.array([True] * n, dtype="boolean"),
            "booleans among numbers": np.array([50.0] * (n - 1) + [True], dtype=object),
            "dates": pd.date_range("2026-01-01", periods=n).to_numpy(),
            "text": np.array(["n/a"] * n, dtype=object),
            "mixed objects": np.array([50.0] * (n - 1) + [{"close": 51.0}], dtype=object),
            "categories": pd.Categorical([50.0] * n),
            "all NaN": np.full(n, np.nan),
            "all missing": np.array([None] * n, dtype=object)}[kind]


@pytest.mark.parametrize("kind", ["booleans", "nullable booleans", "booleans among numbers", "dates", "text", "mixed objects",
                                  "categories", "all NaN", "all missing"])
def test_a_column_that_is_not_numbers_is_never_a_price_and_is_named(setup, monkeypatch, kind):
    """Outcome 1: a held fund whose column is booleans, dates, text, other objects, or holds no value at all: no price on
    any day, named in `unreadable_prices`, valued at the last close the book valued it at (rev 5; rev 4 said its cost, as
    this frame has no valid price of it), never traded."""
    t, s, out, px = _held_two(setup[0], setup[1], monkeypatch)
    frame = px.iloc[: DAY0 + 5].copy()
    frame["BBB"] = _not_numbers(kind, len(frame))
    lot = dict(t.state["positions"]["two"]["BBB"])
    day = t.step(frame, {7: s})
    assert day["unreadable_prices"] == ["BBB"] and day["stale_prices"] == {"BBB": DAY0 + 5}
    aaa = t.state["positions"]["two"]["AAA"]
    seen = float(px["BBB"].iloc[DAY0 + 2])                             # rev 5 (outcome 1): the close the book last valued it at
    assert day["equity"] == pytest.approx(t.state["cash"] + aaa["units"] * float(px["AAA"].iloc[DAY0 + 4]) + lot["units"] * seen)
    assert t.state["positions"]["two"]["BBB"] == lot
    assert not [e for e in t.journal.read(since=frame.index[-1]) if e.get("symbol") == "BBB"]          # no decision, no fill
    assert not [o for o in t.state["pending"] if o["symbol"] == "BBB"]
    _adds_up(out + [day])


def test_a_nullable_column_holding_pd_na_reads_those_rows_as_missing(setup, tmp_path, monkeypatch):
    """Outcome 1: a nullable Float64 frame with pd.NA in it runs, and writes what the same frame with NaN there writes."""
    t, s, _, px = _held_two(setup[0], setup[1], monkeypatch)
    u, _, _, _ = _held_two(tmp_path / "twin", setup[1], monkeypatch)
    frame = px.iloc[: DAY0 + 5].copy()
    nullable = frame.astype("Float64")
    nullable.iloc[-1, nullable.columns.get_loc("BBB")] = pd.NA
    twin = frame.copy()
    twin.iloc[-1, twin.columns.get_loc("BBB")] = np.nan
    a, b = t.step(nullable, {7: s}), u.step(twin, {7: s})
    assert a == b and a["stale_prices"] == {"BBB": 1} and "unreadable_prices" not in a


@pytest.mark.parametrize("dtype", ["int64", "Int64", "Float64"])
def test_a_column_of_integers_or_nullable_numbers_is_read_as_the_same_floats(setup, tmp_path, monkeypatch, dtype):
    """Outcome 1, unchanged: whole-number closes as integers (numpy or nullable) or as nullable floats write what the
    same closes as ordinary floats write."""
    whole = 50.0 + np.arange(DAY0 + 8)
    t, s, a, px = _held_two(setup[0], setup[1], monkeypatch, bbb=whole)
    u, _, b, _ = _held_two(tmp_path / "twin", setup[1], monkeypatch, bbb=pd.array(whole.astype(int) if dtype != "Float64" else whole, dtype=dtype))
    assert a == b
    frame = px.iloc[: DAY0 + 5].copy()
    other = frame.copy()
    other["BBB"] = pd.array(frame["BBB"].astype(int) if dtype != "Float64" else frame["BBB"], dtype=dtype)
    assert t.step(frame, {7: s}) == u.step(other, {7: s})


def _hold_lock(path):
    import fcntl
    import os
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)                  # as another run of the book would hold it
    return fd


def _files(directory):
    return {p.name: p.read_bytes() for p in directory.iterdir() if p.is_file()}


def test_a_start_that_finds_the_book_locked_does_nothing_and_names_the_lock(setup):
    """Outcome 2: while another process holds the book's lock, a start writes nothing (no state, no journal line, no
    latch, no temporary file), calls neither the venue nor the panel, and refuses naming the lock; once it is free the
    same trader runs the day."""
    import os
    px, t, s = prices(), trader(setup), strat()
    for k in range(11, 14):
        t.step(px.iloc[: k + 1], {7: s})
    calls = []

    class Venue:
        name = "spy"

        def submit(self, symbol, units, tag):
            calls.append(("venue", symbol))
            return {"status": "accepted"}

    class Panel(FakePanel):
        def evaluate(self, case_id, case):
            calls.append(("panel", case_id))
            return super().evaluate(case_id, case)

    t.latch_file.write_text(json.dumps({"tripped_on": "2026-01-02"}))
    lock = setup[0] / "paper_state.lock"
    fd = _hold_lock(lock)
    try:
        before = _files(setup[0])
        other = pp.PaperTrader(setup[0], setup[1], halt_file=None, venue=Venue(), panel=Panel(), panel_mode="shadow")
        for run in (lambda: other.step(px.iloc[:15], {7: s}), lambda: t.step(px.iloc[:15], {7: s}),
                    lambda: other.reset_latch("burton", "2026-01-26")):
            with pytest.raises(pp.PaperBookBusy) as err:
                run()
            assert str(lock) in str(err.value)
        assert _files(setup[0]) == before and calls == []
    finally:
        os.close(fd)
    assert other.step(px.iloc[:15], {7: s})["day"] == str(px.index[14].date())


LOCK_CHILD = r'''
import pathlib, sys, time
import numpy as np, pandas as pd
from valedia_trader.research_loop import paper as pp
from valedia_trader.research_loop import strategy as st
directory, limits, first, last, who, hold = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), sys.argv[5], sys.argv[6]
log = pathlib.Path(directory).parent / "venue.log"
class Venue:
    name = "spy"
    def submit(self, symbol, units, tag):
        with log.open("a") as fh:
            fh.write(f"{who} {tag}\n")
        return {"status": "accepted"}
save, armed = pp.PaperTrader._save, [False]
def held(self):
    if armed[0] and hold != "-":
        armed[0] = False
        print("inside", flush=True)                                  # the day is decided and not saved yet
        while not pathlib.Path(hold).exists():
            time.sleep(0.05)
    return save(self)
pp.PaperTrader._save = held
rng = np.random.default_rng(29)
n = 46
px = pd.DataFrame({f: 70 * np.exp(np.cumsum(0.0015 + 0.02 * rng.standard_normal(n))) for f in ("K1", "K2", "K3")},
                  index=pd.bdate_range("2026-03-02", periods=n))
s = st.RuleStrategy.from_spec({"name": "lock_paper", "version": "1.0.0", "hypothesis": "Assets that rose keep rising",
    "market": ["K1", "K2", "K3"], "entry": {"rule": "momentum", "lookback": 5, "enter": 0.0, "exit": -0.01, "direction": "long"},
    "sizing": {"rule": "equal"}, "risk": {"max_position_pct": 0.3, "max_open_positions": 3, "stop_loss_pct": None}})
t = pp.PaperTrader(directory, limits, halt_file=None, min_trade_pct=0.0001)
for i in range(first, last + 1):
    if i == last and who != "prep":
        t.venue, armed[0] = Venue(), True
    t.step(px.iloc[: i + 1], {7: s})
print("done", flush=True)
'''


def test_two_starts_at_once_in_two_processes_run_the_day_once(setup, tmp_path):
    """Outcome 2 with real processes: a second start while the first is inside the day exits non-zero naming the lock,
    and the day is written once, its venue orders sent once (by the first)."""
    import os
    import subprocess
    import sys
    from pathlib import Path
    root = str(Path(pp.__file__).resolve().parents[2])
    env = dict(os.environ, PYTHONPATH=root + os.pathsep + os.environ.get("PYTHONPATH", ""))
    book, hold = tmp_path / "book" / "journal", tmp_path / "release"

    def start(first, last, who, held):
        return subprocess.Popen([sys.executable, "-B", "-c", LOCK_CHILD, str(book), str(setup[1]), str(first), str(last), who, held],
                                env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    prep = start(10, 15, "prep", "-")
    _, err = prep.communicate(timeout=240)
    assert prep.returncode == 0, err[-2000:]
    a = start(16, 16, "A", str(hold))
    try:
        assert a.stdout.readline().strip() == "inside"
        before = _files(book)
        b = start(16, 16, "B", "-")
        _, err_b = b.communicate(timeout=240)
        assert b.returncode != 0 and "PaperBookBusy" in err_b and str(book / "paper_state.lock") in err_b
        assert _files(book) == before                                    # the second start wrote nothing
    finally:
        hold.touch()
        out_a, err_a = a.communicate(timeout=240)
    assert a.returncode == 0 and out_a.strip() == "done", err_a[-2000:]
    days = [e["day"] for e in pp.Journal(book).read() if e["type"] == "summary"]
    assert days.count(str(pd.bdate_range("2026-03-02", periods=17)[16].date())) == 1 and len(days) == len(set(days)) == 7
    sent = (tmp_path / "book" / "venue.log").read_text().split("\n") if (tmp_path / "book" / "venue.log").exists() else []
    assert [line for line in sent if line] and all(line.startswith("A ") for line in sent if line)


def _with_outbox(setup, monkeypatch):
    """A book whose last day was saved with its journal entries and killed before it appended them."""
    px, t, s = prices(), trader(setup), strat()
    for k in range(11, 14):
        t.step(px.iloc[: k + 1], {7: s})
    monkeypatch.setattr(t, "_flush_outbox", lambda: None)              # the kill: after the save, before the appends
    t.step(px.iloc[:15], {7: s})
    state = json.loads((setup[0] / "paper_state.json").read_text())
    assert state["journal_outbox"] and state["last_day"] == str(px.index[14].date())
    assert not [e for e in pp.Journal(setup[0]).read() if e["day"] == state["last_day"]]
    return px, s, state


def test_a_reader_is_never_made_to_wait_and_never_finishes_an_outbox_without_the_lock(setup, monkeypatch):
    """Outcome 2: while the lock is held, a program that only reads the book (the order step builds a PaperTrader and
    reads its targets) gets its answer at once and appends nothing; the next start that holds the lock finishes it."""
    import os
    import threading
    from valedia_trader.execution_and_risk.order_step import paper_targets
    px, s, state = _with_outbox(setup, monkeypatch)
    fd, got = _hold_lock(setup[0] / "paper_state.lock"), {}
    try:
        before = _files(setup[0])
        reader = threading.Thread(target=lambda: got.update(targets=paper_targets(pp.PaperTrader(setup[0], setup[1], halt_file=None))))
        reader.start()
        reader.join(timeout=10)
        alive = reader.is_alive()
    finally:
        os.close(fd)
    reader.join(timeout=30)
    assert not alive and got["targets"].day == state["last_day"]                # it did not wait for the lock
    assert _files(setup[0]) == before                                            # and appended nothing
    t = trader(setup)                                                            # the lock is free: this start finishes it
    days = [e["day"] for e in t.journal.read() if e["type"] == "summary"]
    assert days.count(state["last_day"]) == 1 and "journal_outbox" not in json.loads((setup[0] / "paper_state.json").read_text())


def test_a_start_that_finds_an_outbox_and_the_lock_held_finishes_it_when_it_runs_its_day(setup, monkeypatch):
    """Outcome 2: an outbox read while another holds the lock is finished by this trader's own next day, under the lock."""
    import os
    px, s, state = _with_outbox(setup, monkeypatch)
    fd = _hold_lock(setup[0] / "paper_state.lock")
    try:
        t = trader(setup)
    finally:
        os.close(fd)
    assert "journal_outbox" in t.state and not [e for e in t.journal.read() if e["day"] == state["last_day"]]
    t.step(px.iloc[:16], {7: s})
    days = [e["day"] for e in t.journal.read() if e["type"] == "summary"]
    assert days == sorted(set(days)) and state["last_day"] in days and str(px.index[15].date()) in days


@pytest.mark.parametrize("closes, suspect_on", [
    ([100.0, np.nan, 100.0, np.nan, 600.0, np.nan, 600.0, 600.0], {4}),                # priced every other row
    ([100.0, 100.0, 600.0, np.nan, np.nan, np.nan, 600.0, 600.0], {2}),                # three rows without a close between
    ([100.0, 100.0, 600.0, 0.0, -1.0, 600.0, 600.0, 600.0], {2}),                      # rows with no valid close between
    ([100.0, 100.0, 15.0, np.nan, 15.0, np.nan, 15.0, 15.0], {2})])                    # the same downward
def test_the_confirmation_is_the_funds_last_priced_row_however_far_back(setup, monkeypatch, closes, suspect_on):
    """Outcome 3: after a real move of more than five times, the first priced row is suspect and the next priced row
    confirms it: never locked out because the calendar row before had no close."""
    px = _one_fund(closes)
    monkeypatch.setattr(pp, "run_rules", _schedule_rules({"tax_paper": [{"AAA": 0.5}] * len(px)}))
    t, s = no_costs(setup, min_trade_pct=0.45), long_only()
    out, book = [], []
    for i in range(len(closes)):
        out.append(t.step(px.iloc[: DAY0 + i + 1], {7: s}))
        book.append((t.state["cash"], t.state["positions"]["tax_paper"]["AAA"]["units"]))
    assert {i for i, o in enumerate(out) if o.get("suspect_prices")} == suspect_on
    confirmed = min(i for i in range(len(closes)) if i > max(suspect_on) and np.isfinite(closes[i]) and closes[i] > 0)
    cash, units = book[confirmed]
    assert units and out[confirmed]["equity"] == pytest.approx(cash + units * closes[confirmed])          # taken
    assert not any(o.get("suspect_prices") for o in out[confirmed:])                                      # and kept
    _adds_up(out)


class _Killed(BaseException):
    """A kill, raised where a test stops a run."""


def _latch_setup(setup):
    setup[1].write_text(json.dumps({"max_daily_loss_pct": 50.0, "max_drawdown_pct": 3.0, "max_position_pct": 0.6, "max_open_positions": 5}))
    px, t, s = prices(), trader(setup), strat()
    crash = px.copy()
    crash.iloc[14:, 0] = crash.iloc[13, 0] * 0.80
    crash.iloc[14:, 1] = crash.iloc[13, 1] * 1.20
    for k in range(11, 14):
        t.step(crash.iloc[: k + 1], {7: s})
    return crash, t, s


def _latched(directory):
    return [e["day"] for e in pp.Journal(directory).read() if e["type"] == "kill_switch" and e["decision"] == "latched"]


def test_a_rerun_of_the_day_the_latch_tripped_journals_it_once(setup, monkeypatch):
    """Untested in rev 3 (`_latched_on`): a run killed after tripping the latch and before saving its day leaves the latch
    and nothing else; the rerun of that day journals the trip, once."""
    crash, t, s = _latch_setup(setup)

    def killed(self):
        raise _Killed()
    monkeypatch.setattr(pp.PaperTrader, "_save", killed)
    with pytest.raises(_Killed):
        t.step(crash.iloc[:15], {7: s})
    monkeypatch.undo()
    day = str(crash.index[14].date())
    assert json.loads(t.latch_file.read_text())["tripped_on"] == day and _latched(setup[0]) == []
    again = trader(setup)
    again.step(crash.iloc[:15], {7: s})
    again.step(crash.iloc[:16], {7: s})
    assert _latched(setup[0]) == [day]


def test_the_latch_file_never_exists_half_written(setup, monkeypatch):
    """Outcome 4: a kill while the latch is being written leaves no latch file (or a whole one), never an empty one; the
    rerun trips it and journals it."""
    crash, t, s = _latch_setup(setup)
    real = pp.os.fdopen

    def fdopen(fd, mode="r", *a, **k):
        fh = real(fd, mode, *a, **k)
        if "w" in mode:
            def write(text):
                raise _Killed()                                        # killed before a byte of the latch is written
            fh.write = write
        return fh
    monkeypatch.setattr(pp.os, "fdopen", fdopen)
    with pytest.raises(_Killed):
        t.step(crash.iloc[:15], {7: s})
    monkeypatch.undo()
    assert not rg.file_is_there(t.latch_file) or "tripped_on" in json.loads(t.latch_file.read_text() or "{}")
    again = trader(setup)
    again.step(crash.iloc[:15], {7: s})
    assert json.loads(again.latch_file.read_text())["tripped_on"] == str(crash.index[14].date())
    assert _latched(setup[0]) == [str(crash.index[14].date())]
    assert not [p for p in setup[0].iterdir() if p.name.endswith(".tmp")]


@pytest.mark.parametrize("body", ["", "{}", '{"tripped_on": 5}', "[1, 2]"])
def test_a_latch_file_that_cannot_be_read_or_holds_no_day_counts_as_tripped_and_is_journaled(setup, body):
    """Outcome 4: a latch file left empty (by a kill of an earlier revision) or holding no day counts as tripped today:
    new risk is blocked and the day journals the latch."""
    crash, t, s = _latch_setup(setup)
    t.latch_file.write_text(body)
    t.step(crash.iloc[:15], {7: s})
    assert _latched(setup[0]) == [str(crash.index[14].date())]
    assert t.latch_file.read_text() == body                              # what was there is kept, never replaced
    t.step(crash.iloc[:16], {7: s})
    after = [e for e in t.journal.read(since=crash.index[14]) if e["type"] == "decision" and "order" in e]
    assert all(e["decision"] == "blocked" for e in after if not e["order"]["reduces_risk"])


@pytest.mark.parametrize("close, suspect", [(500.0, False), (499.99, False), (500.01, True),
                                            (20.0, False), (20.01, False), (19.99, True)])
def test_a_close_at_exactly_five_times_is_taken_and_just_beyond_is_suspect(setup, monkeypatch, close, suspect):
    """Untested in rev 3 (`JUMP_FACTOR`): five times the last valid close, up or down, is a price; just beyond is suspect."""
    px = _one_fund([100.0, 100.0, 100.0, close])
    monkeypatch.setattr(pp, "run_rules", _schedule_rules({"tax_paper": [{"AAA": 0.5}] * len(px)}))
    t, s = no_costs(setup, min_trade_pct=0.002), long_only()
    out = [t.step(px.iloc[: DAY0 + i + 1], {7: s}) for i in range(4)]
    assert out[-1].get("suspect_prices") == ({"AAA": close} if suspect else None)
    assert out[-1].get("stale_prices") == ({"AAA": 1} if suspect else None)


def test_a_trader_built_before_another_run_saved_a_day_reads_the_book_again_under_the_lock(setup):
    """Outcome 2: the state a day is decided on is the one read under the lock. A trader built before another run saved
    that day refuses to run it again (it reads the saved day under the lock) and writes nothing."""
    px, s = prices(), strat()
    early = trader(setup)
    for k in range(11, 14):
        early.step(px.iloc[: k + 1], {7: s})
    late = trader(setup)
    late.step(px.iloc[:15], {7: s})                                     # another run saves the day first
    before = _files(setup[0])
    with pytest.raises(ValueError, match="already processed"):
        early.step(px.iloc[:15], {7: s})
    assert _files(setup[0]) == before
    assert early.step(px.iloc[:16], {7: s})["day"] == str(px.index[15].date())     # and carries on from the saved book
    days = [e["day"] for e in pp.Journal(setup[0]).read() if e["type"] == "summary"]
    assert len(days) == len(set(days)) == 5


# ---- T29 rev 5: a held fund is valued at the last valid close the BOOK has seen; an unopenable latch counts as set;
# a reader never crashes on a lock it cannot make; the tax ledger is rebuilt after an outbox is finished

def _rising_book(directory, monkeypatch, limit=10.0):
    """The check's case: AAA flat at 100; BBB 50 until bar 31, 80 from bar 32. `two` buys 30% of each on bar 30 (filled
    on bar 31 at 50) and holds; four nights run (bars 30 to 33). BBB is then worth far more than its cost, and valuing it
    at its cost would be a drawdown past the 10% limit."""
    px = scripted_market()
    px["AAA"] = 100.0
    px["BBB"] = np.where(np.arange(len(px)) < DAY0 + 2, 50.0, 80.0)
    limits = directory.parent / f"{directory.name}-limits.json"
    limits.write_text(json.dumps({"max_daily_loss_pct": 50.0, "max_drawdown_pct": limit, "max_position_pct": 0.6, "max_open_positions": 5}))
    monkeypatch.setattr(pp, "run_rules", _schedule_rules({"two": [{"AAA": 0.3, "BBB": 0.3}] * len(px)}))
    t = pp.PaperTrader(directory, limits, fee_bps=0.0, slippage_bps=0.0, halt_file=None, min_trade_pct=0.15)
    s = long_only("two", market=("AAA", "BBB"))
    out = [t.step(px.iloc[: DAY0 + i + 1], {7: s}) for i in range(4)]
    assert t.state["positions"]["two"]["BBB"]["cost"] == pytest.approx(50.0) and out[-1]["equity"] > 117_000.0
    return t, s, out, px, limits


def _bad_column(frame, kind):
    frame = frame.copy()
    if kind == "booleans":
        frame["BBB"] = np.array([True, False] * len(frame))[: len(frame)]
    elif kind == "one stray True":
        col = frame["BBB"].astype(object)
        col.iloc[5] = True
        frame["BBB"] = col
    elif kind == "all NaN":
        frame["BBB"] = np.nan
    elif kind == "column absent":
        frame = frame.drop(columns=["BBB"])
    else:
        frame["BBB"] = "n/a"
    return frame


@pytest.mark.parametrize("kind", ["booleans", "one stray True", "all NaN", "column absent", "text"])
def test_a_bad_data_night_values_a_held_fund_at_the_last_close_the_book_saw_and_trips_nothing(tmp_path, monkeypatch, kind):
    """Outcome 1, the check's case: the held fund's column cannot be read, so the frame holds no valid price of it. It
    is valued at the last valid close the book valued it at: the equity is that of the same night with yesterday's close
    repeated, there is no false drawdown and no latch, the fund is named and not traded."""
    t, s, out, px, _ = _rising_book(tmp_path / "bad", monkeypatch)
    u, _, _, _, _ = _rising_book(tmp_path / "twin", monkeypatch)
    monkeypatch.setattr(pp, "run_rules", _schedule_rules({"two": [{"AAA": 0.3}] * len(px)}))   # BBB is wanted out
    frame = px.iloc[: DAY0 + 5].copy()
    twin = frame.copy()
    twin.iloc[-1, twin.columns.get_loc("BBB")] = twin["BBB"].iloc[-2]                          # yesterday's close repeated
    lot = dict(t.state["positions"]["two"]["BBB"])
    bad, good = t.step(_bad_column(frame, kind), {7: s}), u.step(twin, {7: s})
    assert bad["equity"] == pytest.approx(good["equity"], rel=1e-12) and bad["drawdown_pct"] == pytest.approx(good["drawdown_pct"])
    assert bad["strategy_values"] == pytest.approx(good["strategy_values"]) and bad["drawdown_pct"] < 1.0
    assert not rg.file_is_there(t.latch_file) and not [e for e in t.journal.read() if e["type"] == "kill_switch"]
    assert bad["stale_prices"] == {"BBB": DAY0 + 5} and bad.get("unreadable_prices") == (None if kind == "column absent" else ["BBB"])
    assert t.state["positions"]["two"]["BBB"] == lot and not [o for o in t.state["pending"] if o["symbol"] == "BBB"]
    assert not [e for e in t.journal.read(since=frame.index[-1]) if e.get("symbol") == "BBB"]
    assert t.state["last_valid_prices"]["BBB"] == {"price": 80.0, "day": str(px.index[DAY0 + 3].date())}
    _adds_up(out + [bad])


def test_the_remembered_price_follows_every_good_night_and_never_replaces_a_valid_close(tmp_path, monkeypatch):
    """Outcome 1: each night with a valid close moves the remembered price to it; a valid close today is the mark; the
    next bad night uses the latest good close."""
    t, s, out, px, _ = _rising_book(tmp_path / "book", monkeypatch)
    px.iloc[DAY0 + 4, px.columns.get_loc("BBB")] = 81.0
    px.iloc[DAY0 + 5, px.columns.get_loc("BBB")] = 82.0
    for i in (4, 5):
        day = t.step(px.iloc[: DAY0 + i + 1], {7: s})
        aaa, bbb = t.state["positions"]["two"]["AAA"], t.state["positions"]["two"]["BBB"]
        assert day["equity"] == pytest.approx(t.state["cash"] + aaa["units"] * 100.0 + bbb["units"] * float(px["BBB"].iloc[DAY0 + i]))
        assert t.state["last_valid_prices"]["BBB"] == {"price": float(px["BBB"].iloc[DAY0 + i]), "day": str(px.index[DAY0 + i].date())}
        out.append(day)
    px.iloc[DAY0 + 6, px.columns.get_loc("AAA")] = 101.0                # AAA's close today is valid: it is AAA's mark
    bad = t.step(_bad_column(px.iloc[: DAY0 + 7], "text"), {7: s})
    assert bad["equity"] == pytest.approx(t.state["cash"] + aaa["units"] * 101.0 + bbb["units"] * 82.0)
    assert t.state["last_valid_prices"]["AAA"] == {"price": 101.0, "day": str(px.index[DAY0 + 6].date())}
    assert set(t.state["last_valid_prices"]) == {"AAA", "BBB"}                         # the funds held, nothing else
    _adds_up(out + [bad])


def _without_remembered_prices(t):
    state = json.loads(t.state_file.read_text())
    state.pop("last_valid_prices", None)                                # as the accepted revision and rev 4 save it
    t.state_file.write_text(json.dumps(state, indent=1))


def test_a_book_saved_without_remembered_prices_takes_them_from_the_frame_on_its_first_night(tmp_path, monkeypatch):
    """Outcome 1: a state from rev 4 or the accepted revision has no remembered prices; the first night takes each held
    fund's from the frame's last valid close (what rev 4 values it at), and the nights after use them."""
    t, s, out, px, limits = _rising_book(tmp_path / "old", monkeypatch)
    _without_remembered_prices(t)
    again = pp.PaperTrader(t.dir, limits, fee_bps=0.0, slippage_bps=0.0, halt_file=None, min_trade_pct=0.15)
    gap = px.iloc[: DAY0 + 5].copy()
    gap.iloc[-1, gap.columns.get_loc("BBB")] = np.nan                    # today's close missing: rev 4 carries bar 33's
    first = again.step(gap, {7: s})
    bbb = again.state["positions"]["two"]["BBB"]
    assert first["equity"] == pytest.approx(out[-1]["equity"]) and first["stale_prices"] == {"BBB": 1}
    assert again.state["last_valid_prices"]["BBB"] == {"price": 80.0, "day": str(px.index[DAY0 + 3].date())}
    bad = again.step(_bad_column(px.iloc[: DAY0 + 6], "booleans"), {7: s})
    assert bad["equity"] == pytest.approx(again.state["cash"] + again.state["positions"]["two"]["AAA"]["units"] * 100.0 + bbb["units"] * 80.0)
    assert not rg.file_is_there(again.latch_file)


def test_cost_only_for_a_fund_the_book_has_no_valid_close_of(tmp_path, monkeypatch):
    """Outcome 1: a book saved without remembered prices and a frame with no valid close of a held fund: only then its
    cost, and nothing is remembered for it."""
    t, s, out, px, limits = _rising_book(tmp_path / "old", monkeypatch, limit=50.0)
    _without_remembered_prices(t)
    again = pp.PaperTrader(t.dir, limits, fee_bps=0.0, slippage_bps=0.0, halt_file=None, min_trade_pct=0.15)
    day = again.step(_bad_column(px.iloc[: DAY0 + 5], "all NaN"), {7: s})
    bbb = again.state["positions"]["two"]["BBB"]
    assert day["equity"] == pytest.approx(again.state["cash"] + again.state["positions"]["two"]["AAA"]["units"] * 100.0 + bbb["units"] * 50.0)
    assert "BBB" not in again.state["last_valid_prices"] and again.state["last_valid_prices"]["AAA"]["price"] == 100.0


def test_a_latch_file_that_cannot_be_opened_counts_as_tripped_and_is_journaled(setup):
    """Outcome 2: a latch file that cannot even be opened (mode 000) is journaled as the latch, like one that opens but
    cannot be read; it is never replaced."""
    import os
    if os.geteuid() == 0:
        pytest.skip("root opens a file of mode 000")
    crash, t, s = _latch_setup(setup)
    t.latch_file.write_text('{"tripped_on": "2026-01-02"}')
    t.latch_file.chmod(0)
    try:
        t.step(crash.iloc[:15], {7: s})
    finally:
        t.latch_file.chmod(0o600)
    assert _latched(setup[0]) == [str(crash.index[14].date())]
    assert t.latch_file.read_text() == '{"tripped_on": "2026-01-02"}'
    after = [e for e in t.journal.read(since=crash.index[14]) if e["type"] == "decision" and "order" in e]
    assert all(e["decision"] == "blocked" for e in after if not e["order"]["reduces_risk"])


@pytest.mark.parametrize("how", ["read-only folder, no lock file", "lock file of mode 000"])
def test_a_reader_reads_a_book_whose_lock_cannot_be_made_and_a_writer_still_refuses(setup, monkeypatch, how):
    """Outcome 3: with an outbox waiting and a lock file that cannot be created or opened, building a PaperTrader (as the
    order step does) reads the state, leaves the outbox and writes nothing; a day run on it refuses, naming the lock."""
    import os
    from valedia_trader.execution_and_risk.order_step import paper_targets
    if os.geteuid() == 0:
        pytest.skip("root ignores the permissions this test sets")
    px, s, state = _with_outbox(setup, monkeypatch)
    lock = setup[0] / "paper_state.lock"
    files = lambda: {p.name: p.read_bytes() for p in setup[0].iterdir() if p.is_file() and p.name != "paper_state.lock"}
    before = files()
    if how.startswith("read-only"):
        lock.unlink()
        setup[0].chmod(0o500)
    else:
        lock.chmod(0)
    try:
        reader = pp.PaperTrader(setup[0], setup[1], halt_file=None)
        assert paper_targets(reader).day == state["last_day"] and "journal_outbox" in reader.state
        with pytest.raises(pp.PaperBookBusy, match="paper_state.lock"):
            reader.step(px.iloc[:16], {7: s})
    finally:
        setup[0].chmod(0o700)
        if lock.exists():
            lock.chmod(0o644)
    assert files() == before and (how.startswith("lock") or not lock.exists())


def test_a_start_that_finishes_an_outbox_rebuilds_the_tax_ledger_before_the_nights_sale(setup, tmp_path, scripted, monkeypatch):
    """Outcome 4: a kill after the state save leaves the buy's fill in the outbox; the next start appends it and must
    rebuild the tax ledger from it: the trim it decides that night carries the same tax note as a run without the kill."""
    px, s = scripted, long_only()
    ref = no_costs(setup, tmp_path / "reference", tax_rules=RULES)
    for k in range(3):
        ref.step(px.iloc[: DAY0 + k + 1], {7: s})
    t = no_costs(setup, tax_rules=RULES)
    t.step(px.iloc[: DAY0 + 1], {7: s})
    monkeypatch.setattr(t, "_flush_outbox", lambda: None)              # the kill: after the save, before the appends
    t.step(px.iloc[: DAY0 + 2], {7: s})
    assert not [e for e in pp.Journal(setup[0]).read() if e["type"] == "fill"]
    again = no_costs(setup, tax_rules=RULES)                          # this start finishes the outbox
    again.step(px.iloc[: DAY0 + 3], {7: s})                           # and trims at a loss the same night
    trim = str(px.index[DAY0 + 2].date())

    def notes(directory):
        return [e["tax"] for e in pp.Journal(directory).read() if e["type"] == "decision" and "order" in e and e["day"] == trim]
    assert notes(setup[0]) == notes(tmp_path / "reference") and notes(setup[0])[0]["side"] == "sell"
    assert notes(setup[0])[0]["gain_loss"] == {"short_term": pytest.approx(-4375.0), "long_term": pytest.approx(0.0)}
