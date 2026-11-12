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
