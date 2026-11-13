import json
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
