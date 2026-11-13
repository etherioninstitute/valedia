"""The paper journal never writes a remembered secret, and writes every ordinary record exactly as
before. All secrets here are made up; checks are booleans and counts so a failure prints no value."""
import base64
import json
import math
import random
import string

import pandas as pd

from valedia_trader.core import secrets as s
from valedia_trader.research_loop import paper as pp


def _made_up(rng, n=32, alphabet=string.ascii_letters + string.digits):
    return "".join(rng.choice(alphabet) for _ in range(n))


def _leaks(text, value):
    low, v = text.lower(), value.lower()
    return any(v[i:i + 8] in low for i in range(len(v) - 7))


def _remember_ten(rng):
    values = [_made_up(rng, rng.randint(20, 48)) for _ in range(10)]
    env = {f"MADE_UP_{i}_TOKEN": v for i, v in enumerate(values)}
    for name in env:
        s.secret(name, env)
    return values


def _ordinary_records(rng, n=60):
    """Records shaped like the ones the paper trader writes: decisions, fills, summaries, panels."""
    days = pd.bdate_range("2026-01-05", periods=n)
    out = []
    for i, day in enumerate(days):
        kind = ["decision", "fill", "summary", "panel_settled", "kill_switch", "decision"][i % 6]
        cond = {"regime": rng.choice(["calm", "volatile", "trending"]), "volatility_20d": rng.random(), "return_20d": rng.uniform(-0.1, 0.1)}
        if kind == "decision":
            entry = {"type": "decision", "hypothesis_id": f"h{rng.randint(1, 30)}", "strategy": "mom_paper", "version": "1.0.0",
                     "symbol": rng.choice(["UP", "DOWN", "SPY", "BTC-USD"]), "market_conditions": cond,
                     "order": {"symbol": "UP", "units": rng.uniform(-50, 50), "price": 100 * rng.random()},
                     "account": {"equity": 100_000.0, "open_positions": rng.randint(0, 4), "daily_loss_pct": 0.0},
                     "decision": rng.choice(["placed", "blocked", "no trade"]), "risk_check": "within limits",
                     "reasoning": f"10-day momentum {rng.uniform(-0.1, 0.1):+.3f} > enter 0.030; target weight +0.250 of the strategy's capital"}
            if i % 4 == 0:
                entry["venue"] = {"name": "alpaca-paper", "status": "accepted", "id": f"{rng.getrandbits(64):016x}"}
            if i % 5 == 0:
                entry["tax"] = {"wash_sale_window": [str(day.date()), str((day + pd.Timedelta(days=30)).date())], "loss": float("nan")}
        elif kind == "fill":
            entry = {"type": "fill", "strategy": "mom_paper", "version": "1.0.0", "symbol": "DOWN", "units": rng.uniform(-9, 9),
                     "price": 99.5, "fee": 0.12, "decided_on": str(day.date()), "realized_pnl": None,
                     "entry_conditions": cond, "reason": "catching up with the strategy's book (enter on 2026-01-05)"}
        elif kind == "summary":
            entry = {"type": "summary", "day": str(day.date()), "equity": 100_000 + rng.uniform(-500, 500), "orders_placed": 1,
                     "strategy_values": {"mom_paper": 50_000.0}, "benchmark_values": {"held": [1.0, 1.01, math.inf]},
                     "when": pd.Timestamp(day), "note": "café — ünïcode, tabs\tand \"quotes\""}
        elif kind == "panel_settled":
            entry = {"type": "panel_settled", "case_id": f"case-{i:04d}-{rng.getrandbits(32):08x}", "symbol": "UP",
                     "outcome": rng.choice([True, False]), "scores": [rng.random() for _ in range(3)]}
        else:
            entry = {"type": "kill_switch", "decision": "latched", "reasoning": "drawdown 12.5% exceeds the 10.0% limit",
                     "drawdown_pct": 12.5, "nested": [[{"x": (1, "two")}]]}
        out.append((day, entry))
    return out


def test_ordinary_records_are_written_byte_for_byte_as_before(tmp_path):
    rng = random.Random(1)
    _remember_ten(rng)
    journal = pp.Journal(tmp_path / "journal")
    start = s.tripped()
    expected = {}
    records = _ordinary_records(rng)
    assert len(records) >= 50
    for day, entry in records:
        written = journal.write(day, entry)
        line = json.dumps({"timestamp": written["timestamp"], "day": str(pd.Timestamp(day).date()), **entry}, default=str) + "\n"
        expected.setdefault(written["day"], []).append(line)
    for name, lines in expected.items():
        same = (tmp_path / "journal" / f"{name}.jsonl").read_text() == "".join(lines)
        assert same
    assert s.tripped() == start and not any("secret_redacted" in e for e in journal.read())


def test_a_planted_secret_never_reaches_the_journal_file(tmp_path):
    rng = random.Random(2)
    values = _remember_ten(rng)
    v = values[3]
    journal = pp.Journal(tmp_path / "journal")
    entry = {"type": "decision", "symbol": "UP",
             "venue": {"name": "webull", "status": "error", "error": f"WebullError: 401 for app key {v}"},
             "headers": [{"Authorization": f"Basic {base64.b64encode(f'k:{v}'.encode()).decode()}"}],
             f"leaked-{v[2:14]}": {"hex": v.encode().hex(), "upper": v.upper()}, "units": 3.0}
    record = journal.write("2026-03-02", entry)
    text = (tmp_path / "journal" / "2026-03-02.jsonl").read_text()
    leaked = any(_leaks(text, x) for x in values)
    assert not leaked and record["secret_redacted"] == 5
    back = journal.read()
    assert len(back) == 1 and back[0]["secret_redacted"] == 5 and back[0]["units"] == 3.0 and back[0]["venue"]["name"] == "webull"
    assert list(back[0])[-1] == "secret_redacted"


def test_an_account_number_held_as_a_number_never_reaches_the_journal(tmp_path):
    rng = random.Random(3)
    account = int("4" + _made_up(rng, 9, string.digits))
    s.remember(str(account))
    journal = pp.Journal(tmp_path / "journal")
    record = journal.write("2026-03-03", {"type": "fill", "account": account, "by_account": {account: 2.5}, "units": 2.5})
    text = (tmp_path / "journal" / "2026-03-03.jsonl").read_text()
    leaked = _leaks(text, str(account))
    assert not leaked and record["secret_redacted"] == 2 and record["units"] == 2.5
