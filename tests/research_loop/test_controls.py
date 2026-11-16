"""Controls for the gauntlet: the reference edge must pass, the no-edge book must fail, and the ladder must behave.

Pass marks come from the builder's measurements on three seeded synthetic universes (seeds 1, 2, 3 of `universe`
below, default `run_controls` arguments, 30 prior attempts drawn from N(0.005, 0.03) with seed 0). Each mark has
both numbers in its comment: what was measured, and the mark, set at about three times the margin seen.
"""
import json
import os
import sqlite3
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from valedia_trader.backtest.engine import CostModel
from valedia_trader.backtest.walkforward import build_cost_model
from valedia_trader.data_and_fees.venue_registry import VenueRegistry
from valedia_trader.research_loop import REPO
from valedia_trader.research_loop import controls
from valedia_trader.research_loop import ledger as lg

NAMES = ("BTC-USD", "DBC", "EEM", "EFA", "ETH-USD", "GLD", "IEF", "IWM", "LQD", "QQQ", "SPY", "TLT", "XLE", "XLF",
         "XLK", "XLU", "XLV")
# annual drift, annual volatility, equity beta, bond beta
SHAPE = {"SPY": (.09, .16, 1.0, 0), "QQQ": (.12, .21, 1.15, 0), "IWM": (.07, .22, 1.15, 0), "EFA": (.05, .17, .9, 0),
         "EEM": (.03, .21, 1.0, 0), "XLE": (.04, .28, .9, 0), "XLF": (.08, .21, 1.1, 0), "XLK": (.14, .23, 1.15, 0),
         "XLU": (.07, .17, .5, .3), "XLV": (.08, .16, .75, 0), "IEF": (.02, .07, -.1, 1.0), "TLT": (.02, .15, -.2, 2.0),
         "LQD": (.03, .08, .1, .9), "GLD": (.05, .15, .05, .3), "DBC": (.01, .18, .4, 0),
         "BTC-USD": (.35, .70, .4, 0), "ETH-USD": (.35, .85, .45, 0)}
PRIORS = [float(x) for x in np.random.default_rng(0).normal(0.005, 0.03, 30)]     # per-period Sharpes of 30 earlier attempts
UNIVERSE = 3                       # the measured universe on which the one-bar edge fails (delayed fills): both readings exercised


def universe(seed=7, years=14):
    """17 symbols, 14 years of daily closes ending 2026-11-13: one equity factor with calm and stress regimes,
    one bond factor, fat-tailed (t, 5 dof) idiosyncratic noise, and uniform dollar volume of 0.5 to 5 billion."""
    rng = np.random.default_rng(seed)
    n = years * 252
    idx = pd.bdate_range(end="2026-11-13", periods=n)
    stress, on = np.zeros(n, bool), False
    for t in range(n):
        on = (rng.random() > 1 / 40) if on else (rng.random() < 1 / 300)
        stress[t] = on
    eq = np.where(stress, 0.40, 0.13) / np.sqrt(252) * rng.standard_t(5, n) / np.sqrt(5 / 3) - np.where(stress, 0.0015, 0.0)
    bond = 0.06 / np.sqrt(252) * rng.standard_normal(n) + np.where(stress, 0.0003, 0.0)
    cols = {}
    for c in NAMES:
        mu, vol, b_eq, b_b = SHAPE[c]
        idio = np.sqrt(max(vol ** 2 - (b_eq * 0.16) ** 2 - (b_b * 0.06) ** 2, (0.3 * vol) ** 2)) / np.sqrt(252)
        r = mu / 252 + b_eq * eq + b_b * bond + idio * rng.standard_t(5, n) / np.sqrt(5 / 3)
        cols[c] = 100 * np.exp(np.cumsum(r))
    prices = pd.DataFrame(cols, index=idx)[list(NAMES)]
    dollar = pd.DataFrame(rng.uniform(5e8, 5e9, (n, len(NAMES))), index=idx, columns=list(NAMES))
    return prices, dollar


def cost_for(prices) -> CostModel:
    reg = VenueRegistry()                                                 # in memory, seeded; no file
    try:
        return build_cost_model(prices.columns, reg, as_of=prices.index[-1])
    finally:
        reg.close()


@pytest.fixture(scope="module")
def market():
    prices, dollar = universe(UNIVERSE)
    return prices, dollar, cost_for(prices)


@pytest.fixture(scope="module")
def full(market, tmp_path_factory):
    prices, dollar, cost = market
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(tempfile, "tempdir", str(tmp_path_factory.mktemp("controls_tmp")))
        return controls.run_controls(prices, dollar, cost, prior_attempt_sharpes=PRIORS)


def small(market, seed=20261116, hit_rates=(0.55,), **kw):
    prices, dollar, cost = market
    return controls.run_controls(prices, dollar, cost, seed=seed, hit_rates=hit_rates, seeds_per_rate=1, **kw)


def rung(report, ladder, h):
    return next(r for r in report[ladder]["ladder"] if r["hit_rate"] == h)


# ---- the controls

def test_the_reference_edge_passes_development_and_the_sealed_test(full):
    ref = full["reference_edge"]
    assert ref["hit_rate"] == 0.60 and ref["horizon"] == 5 and ref["runs"] == 5
    # measured 15 of 15 seeds over three universes, fresh and at 30 prior attempts: the mark is every seed
    assert ref["passed_development"] == ref["passed_sealed"] == 5 and ref["failed_checks"] == {}
    # reference net Sharpe per seed measured 2.02 to 2.63 (the gate needs about 0.53 for a Deflated Sharpe of 0.95
    # at one attempt); mark 1.0 = 0.53 plus a third of the smallest margin seen (1.49)
    assert min(s["net_sharpe"] for s in rung(full, "fresh_ledger", 0.60)["seeds"]) >= 1.0
    at_prior = rung(full, "at_prior_attempts", 0.60)
    # at 30 prior attempts measured 15 of 15 seeds, lowest Deflated Sharpe 0.9984 against the bar 0.95
    assert at_prior["passed_sealed"] == 5 and min(s["dsr"] for s in at_prior["seeds"]) >= 0.95
    assert full["prediction"]["fail_if_sharpe_below"] == 0.0 and full["prediction"]["fail_if_drawdown_above"] == 1.0


def test_the_no_edge_book_fails_development_on_every_seed(full):
    ne = full["no_edge"]
    assert ne["runs"] == 5 and ne["passed_development"] == 0 and ne["passed_sealed"] == 0
    assert all("deflated_sharpe" in s["failed"] for s in ne["seeds"])
    # median Deflated Sharpe measured 0.021, 0.022 and 0.127 on the three universes; mark 0.5 (about three times
    # the largest, still far under the bar 0.95). Highest single seed measured 0.856 (universe 1).
    assert ne["median_dsr"] <= 0.5
    assert all(s["attempts_on_market_before"] == 0 and s["attempts_on_market"] == 1 for s in ne["seeds"])


def test_the_frame_peeking_cheat_is_flagged_and_refused_and_the_oracle_is_not_flagged(full):
    fp = full["frame_peeking"]
    assert fp["refused"] is True and "future data" in fp["error"] and fp["attempts_recorded"] == 0
    assert fp["flagged_columns"] >= 1                                     # measured 17 of 17 on all three universes
    assert full["detector_sees_frame_peeking"] is True
    assert full["detector_sees_carried_future"] is False                   # the blind spot, measured
    assert full["detector_flags_no_edge"] is False
    assert "carries its own copy of the future" in full["detector_blind_spot"]


def test_the_detector_itself_on_both_cheats(market):
    prices, _, _ = market
    probe = prices.iloc[1500:2400]
    peek = controls.FramePeekingCheat(prices.columns, 1.0, 5, np.random.default_rng(1), len(prices))
    oracle = controls.OracleCheat(prices, 1.0, 5, np.random.default_rng(1))
    from valedia_trader.quant_engine import level10_ml_research as research
    assert research.detect_lookahead(peek.target_weights, probe)
    assert research.detect_lookahead(oracle.target_weights, probe) == []
    w_peek, w_oracle = peek.target_weights(probe), oracle.target_weights(probe)
    assert (w_peek.iloc[:-10] == w_oracle.iloc[:-10]).all().all()        # the same rule, until the frame runs out


def test_the_ladder_is_monotone_enough(full):
    for name in ("fresh_ledger", "at_prior_attempts"):
        rows = sorted(full[name]["ladder"], key=lambda r: r["hit_rate"])
        assert [r["hit_rate"] for r in rows] == [0.50, 0.52, 0.55, 0.60, 0.70, 1.00]
        for i, lo in enumerate(rows):
            for hi in rows[i + 1:]:
                for key in ("passed_development", "passed_sealed"):
                    assert hi[key] >= lo[key] - lo["runs"] / 5, (name, lo["hit_rate"], hi["hit_rate"], key)


def test_smallest_passing_hit_rate_reads_back_from_the_table(full):
    fresh, prior = full["fresh_ledger"]["smallest_passing_hit_rate"], full["at_prior_attempts"]["smallest_passing_hit_rate"]
    # measured fresh 0.50, 0.50, 0.52 and at 30 prior attempts 0.60, 0.60, 0.60 on the three universes
    assert fresh is not None and prior is not None and prior >= fresh and prior <= 0.70
    assert controls.smallest_passing_hit_rate(full["fresh_ledger"]["ladder"]) == fresh
    table = [{"hit_rate": h, "runs": 5, "passed_sealed": k} for h, k in ((0.5, 5), (0.52, 3), (0.55, 4), (0.6, 4), (0.7, 5))]
    assert controls.smallest_passing_hit_rate(table) == 0.55              # 0.50 passes but 0.52 above it does not
    assert controls.smallest_passing_hit_rate([dict(r, passed_sealed=3) for r in table]) is None


def test_every_rung_reports_equity_growth_and_impact(full):
    for r in full["fresh_ledger"]["ladder"]:
        assert r["median_log10_equity_multiple_end_of_development"] is not None
        assert r["median_sealed_impact_share_of_gross"] is not None and "median_market_basket_sharpe" in r
    # impact share measured 0.21 at h = 1.00 against 0.022 at h = 0.60 (universe 3): the fast compounder pays for it
    assert (rung(full, "fresh_ledger", 1.00)["median_sealed_impact_share_of_gross"]
            > 3 * rung(full, "fresh_ledger", 0.60)["median_sealed_impact_share_of_gross"])


def test_the_single_bar_edge_is_reported_with_its_failing_check(full):
    sb = full["single_bar_edge"]
    assert sb["horizon"] == 1 and sb["hit_rate"] == 1.0 and sb["runs"] == 1
    # universe 3 measured: development failed on delayed_fills only (delayed-fills Sharpe -0.215), DSR 1.0
    assert sb["passed_development"] == 0 and sb["seeds"][0]["failed"] == ["delayed_fills"]
    assert sb["statement"] == "this gate turns away an edge that lives in one bar, because of the delayed-fills check"


# ---- the ledgers

def test_each_run_starts_from_the_seeded_count_only(full):
    for r in full["fresh_ledger"]["ladder"]:
        assert all(s["attempts_on_market_before"] == 0 and s["attempts_on_market"] == 1 and s["variations_of_idea"] == 1
                   for s in r["seeds"])
    for r in full["at_prior_attempts"]["ladder"]:
        assert all(s["attempts_on_market_before"] == 30 and s["attempts_on_market"] == 31 for s in r["seeds"])
    assert full["at_prior_attempts"]["prior_attempts"] == 30
    assert full["at_prior_attempts"]["median_variance_used"] > 0


def test_prior_attempts_raise_the_bar_and_an_empty_list_changes_nothing(full, market, tmp_path):
    for fresh_row, prior_row in zip(full["fresh_ledger"]["ladder"], full["at_prior_attempts"]["ladder"]):
        for a, b in zip(fresh_row["seeds"], prior_row["seeds"]):
            assert a["net_sharpe"] == b["net_sharpe"]                     # the same book; only the bar moved
            assert b["dsr"] <= a["dsr"] + 1e-12
    lowest = rung(full, "fresh_ledger", 0.50)["seeds"]
    assert all(b["dsr"] < a["dsr"] for a, b in zip(lowest, rung(full, "at_prior_attempts", 0.50)["seeds"]))
    # two-way: the same rung and seed rebuilt with an empty prior list is the fresh one, with the priors the seeded one
    prices, dollar, cost = market
    ctx = controls._Context(prices, dollar, cost, None, tmp_path, 20261116)
    empty, seeded = controls._rung(ctx, 0.50, 5, 1, []), controls._rung(ctx, 0.50, 5, 1, PRIORS)
    assert empty["seeds"][0]["dsr"] == lowest[0]["dsr"] and empty["seeds"][0]["attempts_on_market"] == 1
    assert seeded["seeds"][0]["dsr"] == rung(full, "at_prior_attempts", 0.50)["seeds"][0]["dsr"] < empty["seeds"][0]["dsr"]


class _SharedLedger(lg.HypothesisLedger):
    """The broken variant: one ledger for every run (and the duplicate guard waived so the runs can share it)."""

    def propose(self, idea, source_agent, market, **kw):
        kw["conditions_changed"] = True
        return super().propose(idea, source_agent, market, **kw)

    def close(self):
        pass


def test_a_shared_ledger_would_raise_the_bar_on_later_rungs(market, tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    same = small(market, hit_rates=(0.50, 0.50))                          # two identical rungs, each on its own ledger
    a, b = (r["seeds"][0] for r in same["fresh_ledger"]["ladder"])
    assert a["dsr"] == b["dsr"] and a["attempts_on_market"] == b["attempts_on_market"] == 1
    shared = _SharedLedger(tmp_path / "shared.db")
    monkeypatch.setattr(controls, "_fresh_ledger", lambda paths: shared)
    broken = small(market, hit_rates=(0.50, 0.50))
    a, b = (r["seeds"][0] for r in broken["fresh_ledger"]["ladder"])
    assert a["net_sharpe"] == b["net_sharpe"]                             # the same book
    assert b["attempts_on_market_before"] == 1 and b["attempts_on_market"] == 2 and b["dsr"] < a["dsr"]
    counts = [s["attempts_on_market"] for r in broken["fresh_ledger"]["ladder"] for s in r["seeds"]]
    assert counts == sorted(counts) and broken["no_edge"]["seeds"][0]["attempts_on_market_before"] > 1
    shared._db.close()


# ---- the report and its footprint

def test_the_report_is_strict_json_and_the_same_seed_gives_the_same_report(market, tmp_path, monkeypatch):
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    one, two, other = small(market), small(market), small(market, seed=7)
    text = json.dumps(one, allow_nan=False, sort_keys=True)
    assert json.loads(text) == one
    assert text == json.dumps(two, allow_nan=False, sort_keys=True)
    assert json.dumps(other, allow_nan=False, sort_keys=True) != text      # two-way: the seed does change it
    assert one["data"] == {"symbols": 17, "first": str(market[0].index[0].date()), "last": "2026-11-13",
                           "sealed_from": str(market[0].index[int(len(market[0]) * 0.75)].date())}
    assert one["at_prior_attempts"] is None


def test_it_leaves_no_file_and_never_uses_a_real_path(market, tmp_path, monkeypatch):
    parent, cwd = tmp_path / "parent", tmp_path / "cwd"
    parent.mkdir(), cwd.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(parent))
    monkeypatch.chdir(cwd)
    real_paths, roots, opened = controls.LoopPaths, [], []

    def guarded(root=None):                                               # LoopPaths raises on its default root
        if root is None or os.path.realpath(root).startswith(os.path.realpath(REPO)) \
                or not os.path.realpath(root).startswith(os.path.realpath(parent)):
            raise AssertionError(f"LoopPaths used outside the temporary directory: {root}")
        roots.append(root)
        return real_paths(root)

    connect = sqlite3.connect

    def record(db, *a, **k):
        opened.append(os.path.realpath(str(db)))
        return connect(db, *a, **k)

    monkeypatch.setattr(controls, "LoopPaths", guarded)
    monkeypatch.setattr(sqlite3, "connect", record)
    before = sorted(p.name for p in parent.rglob("*"))
    rep = small(market, hit_rates=(0.60,))
    assert rep["reference_edge"]["runs"] == 1 and roots and opened
    assert all(p.startswith(os.path.realpath(parent)) for p in opened)
    assert not any(p.startswith(os.path.realpath(REPO)) for p in opened)
    assert sorted(p.name for p in parent.rglob("*")) == before == [] and list(cwd.iterdir()) == []


# ---- the command line's read-only ledger

def _ledger_with_attempts(path: Path) -> list[float]:
    led = lg.HypothesisLedger(path)
    a = led.propose("Momentum on exchange traded funds lasts for months", "price-analyst", "us-etf+crypto")
    b = led.propose("Something about a different market entirely", "price-analyst", "elsewhere")
    for s in (0.03, -0.01, 0.02):
        led.record_attempt(a, "backtest", s, 2000, "1.0.0")
    led.record_attempt(a, "revision", None, None, "1.1.0")              # no Sharpe: not an attempt Sharpe
    led.record_attempt(b, "backtest", 0.5, 2000, "1.0.0")
    led.close()
    return [0.03, -0.01, 0.02]


def test_prior_attempts_are_read_from_a_ledger_opened_read_only(tmp_path):
    path = tmp_path / "ledger.db"
    want = _ledger_with_attempts(path)
    raw, mtime = path.read_bytes(), path.stat().st_mtime_ns
    assert controls.read_prior_attempt_sharpes(path, "us-etf+crypto") == want
    assert controls.read_prior_attempt_sharpes(path, "nowhere") == []
    con = controls._open_read_only(path)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        con.execute("INSERT INTO findings (hypothesis_id, source, finding, recorded_at) VALUES (NULL, 'x', 'y', 'z')")
    con.close()
    assert path.read_bytes() == raw and path.stat().st_mtime_ns == mtime
    assert sorted(p.name for p in tmp_path.iterdir()) == ["ledger.db"]  # no journal or lock file left beside it
    with pytest.raises(FileNotFoundError):
        controls.read_prior_attempt_sharpes(tmp_path / "missing.db", "us-etf+crypto")
    assert not (tmp_path / "missing.db").exists()


def test_the_command_line_reads_the_data_the_research_way_and_passes_the_priors(market, tmp_path, monkeypatch, capsys):
    prices, dollar, _ = market
    data = tmp_path / "data"
    data.mkdir()
    prices.to_parquet(data / "close.parquet")
    volume = dollar / prices
    for c in ("BTC-USD", "ETH-USD"):                                      # the vendor reports crypto volume in dollars
        volume[c] = dollar[c]
    volume.to_parquet(data / "volume.parquet")
    ledger = tmp_path / "ledger.db"
    want = _ledger_with_attempts(ledger)
    seen = {}

    def fake(p, d, c, **kw):
        seen.update(prices=p, dollar=d, cost=c, **kw)
        return {"ok": float("nan")}

    monkeypatch.setattr(controls, "run_controls", lambda p, d, c, **kw: controls.gt._clean(fake(p, d, c, **kw)))
    assert controls.main(["--data", str(data), "--prior-from-ledger", str(ledger), "--market", "us-etf+crypto"]) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": None}
    assert list(seen["prior_attempt_sharpes"]) == want
    from valedia_trader.research_loop import cli
    _, _, research_cost = cli._market(type("A", (), {"data": str(data)})())
    assert seen["cost"] == research_cost and seen["cost"].slippage_bps["BTC-USD"] == 5.0
    assert seen["prices"].shape == prices.shape
    assert np.allclose(seen["dollar"].to_numpy(), dollar.to_numpy())      # dollar volume as the research command reads it
    monkeypatch.setattr(controls, "run_controls", lambda p, d, c, **kw: seen.update(none=kw["prior_attempt_sharpes"]) or {})
    controls.main(["--data", str(data)])
    assert list(seen["none"]) == []
