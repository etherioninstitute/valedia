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
    assert pp.PaperTrader.__init__.__defaults__[-2] == rg.HALT_FILE      # the default watches the system's own halt file


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
