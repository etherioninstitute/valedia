"""The gate-rule study: R0 judged from the kept material must be the real gate's verdict, every rule's pure function
must say what it claims on hand-built inputs, the surrogate prices must keep the cross-section, and the report must be
strict JSON, deterministic for a seed, and leave nothing behind.

Pass marks come from the builder's Step 0 measurements on the RISING universe `universe(7)` (17 symbols, 14 years,
the equal-weight basket rising; the shape of the real data), unless a comment names another. 30 prior attempts are
drawn from N(0.005, 0.03) with seed 0, as in test_controls. Each mark has both numbers in its comment: what was
measured, and the mark, set at about three times the margin seen.

Addendum 1 (listing): `late_listing(prices)` is the same universe with BTC-USD and ETH-USD unpriced on its first
1,500 rows, the shape of the real data; the marks of the listed-window, listing-regime and N4a/N4b tests were measured
by the builder before the code was committed, both numbers in each comment.
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
from valedia_trader.research_loop import gate_study as gs
from valedia_trader.research_loop import gauntlet as gt
from valedia_trader.research_loop.strategy import CRYPTO, SCORES, RuleStrategy, Strategy, StrategyRisk, run_rules

NAMES = ("BTC-USD", "DBC", "EEM", "EFA", "ETH-USD", "GLD", "IEF", "IWM", "LQD", "QQQ", "SPY", "TLT", "XLE", "XLF",
         "XLK", "XLU", "XLV")
# annual drift, annual volatility, equity beta, bond beta (the shape test_controls uses)
SHAPE = {"SPY": (.09, .16, 1.0, 0), "QQQ": (.12, .21, 1.15, 0), "IWM": (.07, .22, 1.15, 0), "EFA": (.05, .17, .9, 0),
         "EEM": (.03, .21, 1.0, 0), "XLE": (.04, .28, .9, 0), "XLF": (.08, .21, 1.1, 0), "XLK": (.14, .23, 1.15, 0),
         "XLU": (.07, .17, .5, .3), "XLV": (.08, .16, .75, 0), "IEF": (.02, .07, -.1, 1.0), "TLT": (.02, .15, -.2, 2.0),
         "LQD": (.03, .08, .1, .9), "GLD": (.05, .15, .05, .3), "DBC": (.01, .18, .4, 0),
         "BTC-USD": (.35, .70, .4, 0), "ETH-USD": (.35, .85, .45, 0)}
PRIORS = [float(x) for x in np.random.default_rng(0).normal(0.005, 0.03, 30)]     # per-period Sharpes of 30 earlier attempts
CRITERIA = gt.Criteria()


def universe(seed=7, years=14, drift=True):
    """17 symbols, 14 years of daily closes ending 2026-11-13 (the generator of test_controls, copied): one equity
    factor with calm and stress regimes, one bond factor, fat-tailed idiosyncratic noise, dollar volume 0.5 to 5 bn."""
    rng = np.random.default_rng(seed)
    n = years * 252
    idx = pd.bdate_range(end="2026-11-13", periods=n)
    stress, on = np.zeros(n, bool), False
    for t in range(n):
        on = (rng.random() > 1 / 40) if on else (rng.random() < 1 / 300)
        stress[t] = on
    tilt = 1.0 if drift else 0.0
    eq = np.where(stress, 0.40, 0.13) / np.sqrt(252) * rng.standard_t(5, n) / np.sqrt(5 / 3) - tilt * np.where(stress, 0.0015, 0.0)
    bond = 0.06 / np.sqrt(252) * rng.standard_normal(n) + tilt * np.where(stress, 0.0003, 0.0)
    cols = {}
    for c in NAMES:
        mu, vol, b_eq, b_b = SHAPE[c]
        idio = np.sqrt(max(vol ** 2 - (b_eq * 0.16) ** 2 - (b_b * 0.06) ** 2, (0.3 * vol) ** 2)) / np.sqrt(252)
        mu = mu if drift else -0.5 * vol ** 2
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
    prices, dollar = universe(7)
    return prices, dollar, cost_for(prices)


LATE = ("BTC-USD", "ETH-USD")


def late_listing(prices, rows=1500):
    """The two coins unpriced on the first `rows` rows: they list late, as on the real data."""
    late = prices.copy()
    late.iloc[:rows, late.columns.get_indexer(list(LATE))] = np.nan
    return late


# ---- the anchor: R0 judged from the kept material is the real gate's verdict

def _sample(prices):
    """The anchor sample on one frame: every family's kind of book."""
    rng = lambda *p: gs._rng(5, *p)                                       # noqa: E731
    out = [controls.OracleCheat(prices, 0.5, 5, rng("N1", 5000, k), name="study_oracle") for k in range(3)]
    out += [controls.OracleCheat(prices, 0.5, 20, rng("N2", 5000, 0), name="study_oracle"),
            controls.NoEdgeBook(prices, rng("N3", 0), name="study_no_edge")]
    out += [controls.OracleCheat(prices, h, 5, rng("E1", round(h * 1e4), 0), name="study_oracle") for h in (0.55, 0.60)]
    out += [controls.OracleCheat(prices, 0.60, 20, rng("E2", 6000, 0), name="study_oracle"),
            gs.NeutralOracle(prices, 0.60, 5, rng("E3", 6000, 0))]
    return [(prices, s) for s in out]


class Spliced(Strategy):
    """A test book with a known change at `switch`: the weights `before` decides (a strategy walked by `run_rules`
    on the whole frame, or None for flat) on rows before `switch`, and those of `after` (a strategy, or a weights
    frame) from `switch` on. It carries its whole book, as the oracle does, so its row t does not change when the
    frame is cut after t."""
    rebalance_days = None

    def __init__(self, prices, before, after, switch, name):
        self.name, self.version, self.market = name, "1.0.0", tuple(prices.columns)
        self.hypothesis = "A control book whose edge changes at a known date"
        self.risk = StrategyRisk(max_position_pct=1.0, max_open_positions=len(self.market), max_gross=1.0, stop_loss_pct=None)

        def weights(x):
            if x is None:
                return pd.DataFrame(0.0, index=prices.index, columns=prices.columns)
            return x if isinstance(x, pd.DataFrame) else run_rules(x, prices)[0]
        early = pd.Series(prices.index < switch, index=prices.index)
        self._w = weights(before).where(early, weights(after), axis=0)

    def walk(self, prices):
        return self._w.reindex(index=prices.index, columns=prices.columns).fillna(0.0), []

    def entry(self, prices):
        return np.sign(self.walk(prices)[0]).astype(int)

    def exit(self, prices, side):
        return np.sign(self.walk(prices)[0]) != side

    def size(self, prices):
        return self.walk(prices)[0].abs()


def _split(frame, cost):
    """The sealed start and the first bar of the last walk-forward window, from the gate's own split."""
    g = gt.Gauntlet(frame, None, cost, None, None, CRITERIA)              # only its split is read; it runs nothing
    windows = np.array_split(np.arange(CRITERIA.warmup_days, len(g.dev_prices)), CRITERIA.walk_forward_windows)
    return g.sealed_start, g.dev_prices.index[windows[-1][0]]


def _constructed(prices, cost):
    """Books built to fail in one known place.
    * dev_then_wrong: the oracle at h = 0.60 on the development rows and h = 0.40 from the sealed start on: passes
      development, fails the sealed test.
    * volatility_drag: the h = 0.60 oracle in development; from the sealed start the whole book in XLU, whose sealed
      prices are rebuilt to alternate +10% and -9.5%: a positive mean daily return (Sharpe above 0, the prediction
      terms pass) and a compounded loss (made_money fails).
    * last_window: flat until the last walk-forward window, then the h = 0.80 oracle to the end: fails development
      on walk_forward alone."""
    rng = lambda *p: gs._rng(6, *p)                                       # noqa: E731
    sealed, last = _split(prices, cost)
    o60 = controls.OracleCheat(prices, 0.60, 5, rng("E1", 6000, 1), name="study_oracle")
    o40 = controls.OracleCheat(prices, 0.40, 5, rng("E1", 4000, 1), name="study_oracle")
    out = [(prices, Spliced(prices, o60, o40, sealed, "study_dev_then_wrong"))]
    drag = prices.copy()
    cut = drag.index.get_loc(sealed)
    steps = np.where(np.arange(len(drag) - cut) % 2 == 0, 1.10, 0.905)
    drag.iloc[cut:, drag.columns.get_loc("XLU")] = drag["XLU"].iloc[cut - 1] * np.cumprod(steps)
    xlu = pd.DataFrame(0.0, index=drag.index, columns=drag.columns)
    xlu["XLU"] = 1.0
    out.append((drag, Spliced(drag, controls.OracleCheat(drag, 0.60, 5, rng("E1", 6000, 2), name="study_oracle"), xlu,
                              sealed, "study_volatility_drag")))
    o80 = controls.OracleCheat(prices, 0.80, 5, rng("E1", 8000, 3), name="study_oracle")
    out.append((prices, Spliced(prices, None, o80, last, "study_last_window")))
    return out


@pytest.fixture(scope="module")
def anchor_rows(market, tmp_path_factory):
    """Each book of the sample, on the data as given and detrended (plus two drawn rule strategies on a surrogate and
    the three constructed books): the study's own `_Book` (the gate's fresh-ledger run plus the kept material) and the
    real gate again in a ledger seeded with the priors."""
    prices, dollar, cost = market
    tmp = tmp_path_factory.mktemp("anchor")
    books = _sample(prices) + _sample(controls.detrend(prices))
    sur = gs.surrogate_prices(prices, gs._rng(5, "surrogate", 0))
    spec_rng = gs._rng(5, "spec", 0)
    books += [(sur, RuleStrategy.from_spec(gs.draw_rule_spec(sur.columns, spec_rng, i))) for i in range(2)]
    books += _constructed(prices, cost)
    rows = []
    for i, (frame, s) in enumerate(books):
        book = gs._Book(frame, dollar, cost, CRITERIA, tmp / f"b{i}", s)
        book.sealed()                                                     # computed for every book of the sample
        at_prior = gs._gate_run(frame, dollar, cost, CRITERIA, tmp / f"p{i}", s, PRIORS)
        rows.append({"name": s.name, "material": book.material, "fresh": book.gate, "at_prior": at_prior,
                     "mismatches": book.mismatches})
    return rows


def _gate_verdicts(row, at_prior: bool) -> tuple[bool, bool]:
    g = row["at_prior" if at_prior else "fresh"]
    if g["refused"]:
        return False, False
    return g["dev_pass"], g["dev_pass"] and g["sealed_pass"]


def _anchor_disagreements(rows) -> int:
    bad = 0
    for row in rows:
        m = row["material"]
        for at_prior, others in ((False, []), (True, PRIORS)):
            dev, both = _gate_verdicts(row, at_prior)
            bad += gs.develops("R0", m, others, CRITERIA) != dev
            bad += gs.passes("R0", m, others, CRITERIA) != both
    return bad


def test_r0_judged_from_the_material_is_the_gate_s_verdict_fresh_and_at_prior_attempts(anchor_rows):
    assert len(anchor_rows) == 23 and not any(r["fresh"]["refused"] or r["at_prior"]["refused"] for r in anchor_rows)
    assert _anchor_disagreements(anchor_rows) == 0
    assert all(r["mismatches"] == [] for r in anchor_rows)               # the rebuilt material gives the report's numbers
    for at_prior in (False, True):                                       # two-way inside the sample: both verdicts occur
        verdicts = [_gate_verdicts(r, at_prior)[1] for r in anchor_rows]
        assert any(verdicts) and not all(verdicts), at_prior
    for row in anchor_rows:                                              # the gate's sealed checks where it ran them
        if row["fresh"]["dev_pass"]:
            assert row["material"]["sealed"] == row["fresh"]["sealed"]
        # the Deflated Sharpe at the prior attempts, from the material, is the gate's number exactly
        m, own = row["material"], row["at_prior"]["own"]
        assert own == m["own"]
        assert gs.deflated_value(m["moments"], own, PRIORS) == row["at_prior"]["report"]["deflated_sharpe"]["value"]


def test_a_wrong_r0_fails_the_anchor(anchor_rows, monkeypatch):
    assert _anchor_disagreements(anchor_rows) == 0
    with monkeypatch.context() as mp:                                    # the Deflated Sharpe check dropped
        mp.setattr(gs, "deflated_value", lambda *a, **k: 1.0)
        assert _anchor_disagreements(anchor_rows) > 0                    # measured 22
    with monkeypatch.context() as mp:                                    # the attempt count ignored (one trial)
        plain = gs.deflated_value
        mp.setattr(gs, "deflated_value", lambda m, own, others, **k: plain(m, own, others, one_trial=True))
        assert _anchor_disagreements(anchor_rows) > 0                    # measured 14
    with monkeypatch.context() as mp:                                    # the sealed made_money check dropped
        mp.setattr(gs, "_SEALED_CHECKS", ("prediction_failure_terms",))
        assert _anchor_disagreements(anchor_rows) > 0                    # measured 2 (the volatility-drag book, at both counts)
    with monkeypatch.context() as mp:                                    # the walk_forward check dropped
        mp.setattr(gs, "_gate_checks_ok", lambda m: all(ok for k, ok in m["checks"].items()
                                                         if k not in ("deflated_sharpe", "walk_forward")))
        assert _anchor_disagreements(anchor_rows) > 0                    # measured 4
    assert _anchor_disagreements(anchor_rows) == 0


def test_the_constructed_books_fail_where_they_were_built_to(anchor_rows):
    row = {r["name"]: r for r in anchor_rows}
    wrong, drag, last = row["study_dev_then_wrong"], row["study_volatility_drag"], row["study_last_window"]
    assert wrong["fresh"]["dev_pass"] and not wrong["fresh"]["sealed_pass"]          # the gate's own verdicts
    assert drag["fresh"]["dev_pass"] and drag["fresh"]["sealed"] == {"made_money": False, "prediction_failure_terms": True}
    failed = [k for k, ok in last["fresh"]["report"]["checks"].items() if not ok["passed"]]
    assert failed == ["walk_forward"] and last["material"]["sealed"] == {"made_money": True, "prediction_failure_terms": True}
    # flat in the first four windows: none of them made money, so 20% of the windows did
    assert last["fresh"]["report"]["positive_windows"] == 0.2


# ---- each rule's pure function on hand-built inputs

_LISTED = {"timing_p": "timing_p_listed", "strict_p": "strict_p_listed", "active_moments": "active_moments_listed"}


def material(**kw):
    """A book that passes everything: per-period Sharpe 0.05 over 2000 bars (PSR 0.987), re-timing p 0.01. The
    listed-window figures equal the gate's (no late listing) unless given."""
    m = {"refused": False, "own": [0.05], "timing_p": 0.01, "strict_p": {}, "development_sharpe": 0.8,
         "checks": dict.fromkeys(("prediction_failure_terms", "deflated_sharpe", "walk_forward", "double_costs",
                                  "delayed_fills", "worst_periods"), True),
         "moments": {"sharpe": 0.05, "n_obs": 2000, "skew": 0.0, "kurt": 3.0},
         "active_moments": {"sharpe": 0.05, "n_obs": 2000, "skew": 0.0, "kurt": 3.0},
         "sealed": {"made_money": True, "prediction_failure_terms": True}, "listed_share": 1.0}
    m.update(kw)
    for gate, listed in _LISTED.items():
        if listed not in kw:
            m[listed] = m[gate]
    return m


def verdicts(m, others=(), version="gate"):
    """R0, R1, R2 and R4 (keyed by base name) with the gate's or the listed window's figures."""
    return {r.split("_")[0]: gs.develops(r, m, list(others), CRITERIA)
            for r in ("R0", f"R1_{version}", f"R2_{version}", f"R4_{version}")}


STRICT = {"strict_p": {1: 0.001}, "strict_p_listed": {1: 0.001}}        # R3's strict p at one attempt, both versions


def test_the_rules_come_as_gate_and_listed_versions():
    assert gs.RULES == ("R0", "R1_gate", "R1_listed", "R2_gate", "R2_listed", "R3_gate", "R3_listed", "R4_gate",
                        "R4_listed")
    assert gs.TIMED == ("gate", "listed")
    with pytest.raises(ValueError, match="unknown rule"):
        gs.develops("R1", material(), [], CRITERIA)                       # the bare name is not a rule any more


def test_a_book_that_passes_r0_and_fails_r1_on_its_re_timing_p_value():
    m = material(timing_p=0.2)
    for version in gs.TIMED:
        assert verdicts(m, version=version) == {"R0": True, "R1": False, "R2": False, "R4": True}
    assert gs.passes("R0", m, [], CRITERIA) and not gs.passes("R1_gate", m, [], CRITERIA)
    assert not gs.develops("R1_gate", material(timing_p=None), [], CRITERIA)     # no re-timing test: not timed


def test_the_listed_figures_decide_the_listed_rules_only_and_unavailable_fails_closed():
    # the gate's p passes and the listed window's does not (a late-listing book): only the _listed rules fail
    m = material(timing_p=0.01, timing_p_listed=0.4, strict_p={1: 0.001}, strict_p_listed={1: 0.3},
                 active_moments_listed={"sharpe": 0.0, "n_obs": 2000, "skew": 0.0, "kurt": 3.0})
    assert {r: gs.develops(r, m, [], CRITERIA) for r in gs.RULES} == {r: not r.endswith("_listed") for r in gs.RULES}
    # and the other way round: the listed figures pass and the gate's do not
    m = material(timing_p=0.4, timing_p_listed=0.01, strict_p={1: 0.3}, strict_p_listed={1: 0.001},
                 active_moments={"sharpe": 0.0, "n_obs": 2000, "skew": 0.0, "kurt": 3.0},
                 active_moments_listed={"sharpe": 0.05, "n_obs": 2000, "skew": 0.0, "kurt": 3.0})
    assert {r: gs.develops(r, m, [], CRITERIA) for r in gs.RULES} == {r: r == "R0" or r.endswith("_listed")
                                                                      for r in gs.RULES}
    # the listed window too short for the test: p_listed, its strict p and its active moments are None, and every
    # _listed rule fails; the _gate rules and R0 are untouched
    gone = material(timing_p_listed=None, strict_p={1: 0.001}, strict_p_listed={1: None}, active_moments_listed=None)
    assert {r: gs.develops(r, gone, [], CRITERIA) for r in gs.RULES} == {r: not r.endswith("_listed") for r in gs.RULES}
    with pytest.raises(KeyError):
        gs.develops("R3_listed", material(strict_p={1: 0.001}, strict_p_listed={}), [], CRITERIA)   # needed
    assert gs.develops("R3_gate", material(strict_p={1: 0.001}), [], CRITERIA)


def test_a_cell_counts_the_books_whose_re_timing_test_was_unavailable():
    v = [{1: dict.fromkeys(gs.RULES, False)} for _ in range(3)]
    cell = gs._cell_report("N1", 0.5, v, [1], [0.1, 0.2, 0.3], 0, [None] * 3,
                           timing=[(True, False, 0.4), (True, True, 1.0), (False, False, None)])
    assert cell["timing_unavailable"] == {"gate": 1, "listed": 2} and cell["median_listed_share"] == 0.7
    assert list(cell["by_attempts"]["1"]) == list(gs.RULES)


def test_a_book_that_fails_r0_only_because_its_own_sharpe_is_in_the_variance_passes_r2():
    others = [0.001, -0.001, 0.0005]                                     # three earlier attempts, nearly no spread
    m = material()
    with_own = gs.deflated_value(m["moments"], m["own"], others)
    without = gs.deflated_value(m["moments"], m["own"], others, leave_own_out=True)
    assert with_own < CRITERIA.min_dsr <= without                        # measured 0.913 and 0.986
    for version in gs.TIMED:
        assert verdicts(m, others, version) == {"R0": False, "R1": False, "R2": True, "R4": True}
    # the variance is the only difference: R0 with the other attempts' variance would pass
    var_others = float(np.var(others, ddof=1))
    from valedia_trader.quant_engine import level10_ml_research as research
    assert research.deflated_sharpe_ratio(0.05, 2000, 4, var_others, 0.0, 3.0) == without
    # fewer than 3 others: 1 / n_obs, as the gate does today
    two = gs.deflated_value(m["moments"], m["own"], others[:2], leave_own_out=True)
    assert two == research.deflated_sharpe_ratio(0.05, 2000, 3, 1 / 2000, 0.0, 3.0)


def test_thresholds_at_exact_equality():
    m = material()
    value = gs.deflated_value(m["moments"], m["own"], [])
    at = gt.Criteria(min_dsr=value)
    assert gs.develops("R0", m, [], at) and not gs.develops("R0", m, [], gt.Criteria(min_dsr=np.nextafter(value, 1)))
    above, third = float(np.nextafter(0.05, 1)), float(np.nextafter(0.05 / 3, 1))
    others = [0.01, 0.02]                                                # N = 3 attempts counted: R3 needs p <= 0.05 / 3
    for v, p_key, s_key in (("gate", "timing_p", "strict_p"), ("listed", "timing_p_listed", "strict_p_listed")):
        assert gs.develops(f"R1_{v}", material(**{p_key: 0.05}), [], CRITERIA)
        assert not gs.develops(f"R1_{v}", material(**{p_key: above}), [], CRITERIA)
        assert gs.develops(f"R3_{v}", material(**{s_key: {3: 0.05 / 3}}), others, CRITERIA)
        assert not gs.develops(f"R3_{v}", material(**{s_key: {3: third}}), others, CRITERIA)
    assert gs.r3_shifts(1) == 400 and gs.r3_shifts(31) == 12400        # 20 times the reciprocal of 0.05 / N
    assert all(gs.r3_shifts(n) >= 20 / (0.05 / n) for n in range(1, 60))


def test_r3_drops_the_deflation_and_needs_its_strict_p_only_when_the_rest_passes():
    spread = [0.04, -0.04, 0.03, -0.03, 0.0]                             # a wide spread of attempts: a high bar
    for v in gs.TIMED:
        m = material(strict_p={6: 0.001})
        assert not gs.develops("R0", m, spread, CRITERIA)                 # deflated by 6 attempts
        assert gs.develops(f"R3_{v}", m, spread, CRITERIA)                # one trial, p 0.001 <= 0.05 / 6
        assert not gs.develops(f"R3_{v}", material(strict_p={6: 0.01}), spread, CRITERIA)
        with pytest.raises(KeyError):
            gs.develops(f"R3_{v}", material(), spread, CRITERIA)          # the rest passes: the strict p is needed
        failing = material(checks=dict(material()["checks"], delayed_fills=False))
        assert gs.develops(f"R3_{v}", failing, spread, CRITERIA) is False  # the rest fails: no p needed, no KeyError


def test_r4_judges_the_active_returns_and_every_rule_keeps_the_gate_s_other_checks():
    m = material(active_moments={"sharpe": 0.0, "n_obs": 2000, "skew": 0.0, "kurt": 3.0})
    for v in gs.TIMED:
        assert verdicts(m, version=v) == {"R0": True, "R1": True, "R2": True, "R4": False}
        assert verdicts(material(active_moments=None), version=v)["R4"] is False     # no usable active returns: value 0
    for check in ("prediction_failure_terms", "walk_forward", "double_costs", "delayed_fills", "worst_periods"):
        bad = material(checks=dict(material()["checks"], **{check: False}), **STRICT)
        assert not any(gs.develops(r, bad, [], CRITERIA) for r in gs.RULES), check
    # the report's own deflated_sharpe boolean is not read: each rule computes its own Sharpe test
    m = material(checks=dict(material()["checks"], deflated_sharpe=False), **STRICT)
    assert all(gs.develops(r, m, [], CRITERIA) for r in gs.RULES)


def test_the_sealed_checks_and_refused_books():
    m = material(sealed={"made_money": False, "prediction_failure_terms": True}, **STRICT)
    assert all(gs.develops(r, m, [], CRITERIA) and not gs.passes(r, m, [], CRITERIA) for r in gs.RULES)
    with pytest.raises(KeyError):
        gs.passes("R0", material(sealed=None), [], CRITERIA)
    refused = material(refused=True, **STRICT)
    assert not any(gs.develops(r, refused, [], CRITERIA) for r in gs.RULES)
    with pytest.raises(ValueError, match="unknown rule"):
        gs.develops("R9", material(), [], CRITERIA)


# ---- the Wilson interval

def test_the_wilson_interval_against_known_values():
    known = {(0, 10): (0.0, 0.2775), (5, 10): (0.2366, 0.7634), (10, 10): (0.7225, 1.0), (1, 100): (0.0018, 0.0545),
             (20, 400): (0.0326, 0.0760)}
    from statsmodels.stats.proportion import proportion_confint
    for (k, n), (lo, hi) in known.items():
        got = gs.wilson(k, n)
        assert abs(got[0] - lo) < 5e-5 and abs(got[1] - hi) < 5e-5, (k, n, got)
        ref = proportion_confint(k, n, alpha=0.05, method="wilson")
        assert np.allclose(got, ref, rtol=0, atol=1e-12), (k, n, got, ref)
    assert gs.wilson(0, 0) is None
    with pytest.raises(ValueError):
        gs.wilson(3, 2)


# ---- the surrogate resampler

def _simple(px: pd.DataFrame) -> pd.DataFrame:
    return px.pct_change(fill_method=None).iloc[1:]


def test_the_surrogate_keeps_the_cross_section_and_each_symbol_s_moments(market):
    prices = market[0]
    a = _simple(prices)
    pairs = np.triu_indices(prices.shape[1], 1)
    mats, worst_pair_mean, worst_mean, worst_sd = [], 0.0, 0.0, 0.0
    for k in range(5):
        sur = gs.surrogate_prices(prices, gs._rng(1, "surrogate", k))
        assert sur.shape == prices.shape and sur.index.equals(prices.index) and (sur.iloc[0] == prices.iloc[0]).all()
        b = _simple(sur)
        mats.append(b.corr().to_numpy())
        worst_pair_mean = max(worst_pair_mean, float(np.abs(mats[-1] - a.corr().to_numpy())[pairs].mean()))
        worst_mean = max(worst_mean, float(((b.mean() - a.mean()).abs() / a.std()).max()))
        worst_sd = max(worst_sd, float((b.std() / a.std() - 1).abs().max()))
        assert not np.allclose(b.to_numpy(), a.to_numpy())               # it did resample
    # Cross-section: ONE surrogate's worst pair is sampling noise (measured 0.057 over these 5, 0.073 over 20, with
    # t-distributed returns), so the 0.05 is applied to the correlation matrix averaged over the 5 (measured 0.022) and
    # to each surrogate's mean absolute pair difference (measured at most 0.0285 over 20 surrogates).
    assert float(np.abs(np.mean(mats, axis=0) - a.corr().to_numpy())[pairs].max()) <= 0.05
    assert worst_pair_mean <= 0.05
    # two-way: resampling each column on its own breaks the cross-section (measured 0.88 on the worst pair)
    r = a.to_numpy()
    rng = np.random.default_rng(0)
    alone = np.column_stack([r[gs.stationary_bootstrap_rows(len(r), len(r), 20, rng), j] for j in range(r.shape[1])])
    assert float(np.abs(np.corrcoef(alone.T) - a.corr().to_numpy())[pairs].max()) > 0.5
    # marginals: mean within 0.032 of a daily sd and sd within 7.3% of its own, measured over these 5; marks 0.10, 0.22
    assert worst_mean <= 0.10 and worst_sd <= 0.22


def test_the_surrogate_is_deterministic_for_a_seed_and_different_for_another(market):
    prices = market[0]
    one, two = (gs.surrogate_prices(prices, gs._rng(1, "surrogate", 0)) for _ in range(2))
    other = gs.surrogate_prices(prices, gs._rng(1, "surrogate", 1))
    assert one.equals(two) and not one.equals(other)
    rows = gs.stationary_bootstrap_rows(100_000, 5000, 20, np.random.default_rng(3))
    breaks = int(np.sum(np.diff(rows) % 5000 != 1)) + 1
    assert 0 <= rows.min() and rows.max() < 5000
    assert abs(100_000 / breaks - 20) <= 0.3                             # mean block 20: measured 20.10, mark 0.3


def test_the_surrogate_resamples_within_listing_regimes_and_keeps_the_priced_pattern(market):
    prices = late_listing(market[0])
    prices.iloc[2000:2010, prices.columns.get_loc("GLD")] = np.nan       # a gap inside a regime
    assert gs.listing_regimes(prices) == [(0, 1500), (1500, len(prices))]   # a gap does not end a listing
    orig = _simple(prices)
    for block in (1, 20):
        sur = gs.surrogate_prices(prices, gs._rng(1, "surrogate", block), block)
        assert sur.isna().equals(prices.isna())                          # the priced symbols of every bar: the original's
        assert gs.listing_regimes(sur) == gs.listing_regimes(prices) and np.nanmin(sur.to_numpy()) > 0
        assert all(sur[c].iloc[1500] == prices[c].iloc[1500] for c in LATE)   # rebuilt from its own first price
        r = _simple(sur)                                                  # row k is bar k + 1
        assert not np.allclose(r.iloc[1:1499, 2:].fillna(0), orig.iloc[1:1499, 2:].fillna(0))   # it did resample
        # every bar of a regime is a return row of the SAME regime of the data (bars after its first; its symbols; a
        # gap read as no move): rebuilt prices, so rounding only
        for a, b in gs.listing_regimes(prices):
            cols = [c for c in prices.columns if prices[c].iloc[a:b].notna().any()]
            pool = orig.iloc[a:b - 1][cols].fillna(0.0).to_numpy()
            got = r.iloc[max(a - 1, 0):b - 1][cols].dropna().to_numpy()[::4]
            assert len(got) > 300 and max(float(np.abs(pool - x).max(axis=1).min()) for x in got) <= 1e-9, (block, a)


def _early_and_late():
    """The late-listing universe with the old symbols' early period (rows 1 to 1,499) given +0.2% a day: the
    checker's contrast, a pre-listing period that behaves unlike the later one."""
    prices = late_listing(universe(7)[0])
    r = _simple(prices)
    r.iloc[:1499] += 0.002
    early = pd.concat([prices.iloc[:1].fillna(1.0), prices.iloc[:1].fillna(1.0).to_numpy() * (1 + r.fillna(0)).cumprod()])
    early[prices.isna()] = np.nan
    for c in LATE:                                                        # the coins as they were from their listing
        early.loc[early.index[1500]:, c] = prices.loc[prices.index[1500]:, c]
    return early


def test_each_regime_keeps_its_own_moments_per_symbol():
    data = _early_and_late()
    a = _simple(data)
    worst_z, worst_sd = {}, {}
    for block in (1, 20):
        zs, sds = [], []
        for k in range(5):
            b = _simple(gs.surrogate_prices(data, gs._rng(2, "surrogate", k), block))
            for lo, hi in gs.listing_regimes(data):
                cols = [c for c in data.columns if data[c].iloc[lo:hi].notna().all()]
                x, y = a.iloc[lo:hi - 1][cols].dropna(), b.iloc[lo:hi - 1][cols].dropna()
                zs.append(float(((y.mean() - x.mean()).abs() / (x.std() / np.sqrt(len(x)))).max()))
                sds.append(float((y.std() / x.std() - 1).abs().max()))
        worst_z[block], worst_sd[block] = max(zs), max(sds)
    # Worst over 5 surrogates, both regimes and every symbol (up to 170 comparisons): the mean differs from the
    # regime's by at most 2.77 (block 1) and 3.09 (block 20) of its standard errors; the sd by at most 7.0% and 17.9%
    # (fat-tailed returns, a long block). Marks: 4.5 standard errors and 30%.
    assert max(worst_z.values()) <= 4.5 and max(worst_sd.values()) <= 0.30, (worst_z, worst_sd)


def test_a_pre_listing_period_with_a_different_mean_keeps_it():
    data = _early_and_late()
    old = [c for c in data.columns if c not in LATE]

    def contrast(r):                                                       # early mean minus late mean, old symbols
        return float((r.iloc[:1499][old].mean() - r.iloc[1500:][old].mean()).mean())
    want = contrast(_simple(data))                                        # measured 0.00166 a day
    got = [contrast(_simple(gs.surrogate_prices(data, gs._rng(2, "surrogate", k), b))) for b in (1, 20) for k in range(5)]
    # measured 0.00130 to 0.00180 (block 1) and 0.00123 to 0.00208 (block 20); mark: within 0.0008 of the data's (the
    # widest miss seen 0.00043), so the contrast is kept on every surrogate
    assert all(abs(g - want) <= 0.0008 for g in got), (want, got)
    # two-way: rows resampled from the WHOLE history, the old way, lose it (measured -0.00033 to 0.00013)
    r = _simple(data)[old].to_numpy()
    rng = np.random.default_rng(0)
    pooled = [float(np.nanmean(r[gs.stationary_bootstrap_rows(1499, len(r), 1, rng)])
                    - np.nanmean(r[gs.stationary_bootstrap_rows(len(r) - 1500, len(r), 1, rng)])) for _ in range(5)]
    assert all(abs(p) <= 0.0005 for p in pooled) and min(got) > 0.0008 > max(pooled)


def _one_bar_sharpe(px):
    """A one-bar rule: hold each symbol the way its last return went, for one bar."""
    x = _simple(px).to_numpy()
    pnl = (np.sign(x[:-1]) * x[1:]).ravel()
    return float(pnl.mean() / pnl.std(ddof=1) * np.sqrt(252))


def test_a_one_bar_rule_s_edge_is_gone_in_n4a_and_kept_in_n4b():
    assert gs.SURROGATE_BLOCK == {"N4a": 1, "N4b": 20}
    assert "short-range dependence of the data kept: a rule that lives inside 20 bars keeps its edge here" in \
        gs.FAMILIES["N4b"][3]
    rng = np.random.default_rng(0)
    e = rng.standard_normal((3528, 2)) * 0.01
    x = np.zeros_like(e)
    for t in range(1, len(x)):                                            # AR(1), lag-1 autocorrelation 0.3
        x[t] = 0.3 * x[t - 1] + e[t]
    ar = pd.DataFrame(100 * np.cumprod(1 + x, axis=0), index=pd.bdate_range(end="2026-11-13", periods=len(x)),
                      columns=["A", "B"])
    data = _one_bar_sharpe(ar)                                            # measured 4.19 (two symbols pooled)
    got = {fam: [_one_bar_sharpe(gs.surrogate_prices(ar, gs._rng(1, "surrogate", fam, k), block)) for k in range(10)]
           for fam, block in gs.SURROGATE_BLOCK.items()}
    # N4a: mean -0.013 over 10 surrogates, range -0.25 to 0.23 (one 14-year Sharpe has sampling sd about 0.19 here);
    # marks: the mean within 0.3 of zero and every surrogate below 0.75, against 4.19 on the data
    assert data >= 3.0 and abs(np.mean(got["N4a"])) <= 0.3 and max(got["N4a"]) <= 0.75, (data, got)
    # N4b: mean 3.89, range 3.50 to 4.21: the edge is kept; mark: every surrogate at least half the data's
    assert min(got["N4b"]) >= 0.5 * data, (data, got)


# ---- the books

def test_the_surrogate_bootstrap_resamples_whole_surrogates():
    rng = lambda: np.random.default_rng(4)                               # noqa: E731
    # two surrogates, one book each, one passing: resampled shares are 0, 0.5 or 1 with chances 1/4, 1/2, 1/4
    assert gs.surrogate_bootstrap([1, 0], [1, 1], rng()) == [0.0, 1.0]
    # every surrogate at the same share: the interval is that share
    assert gs.surrogate_bootstrap([2, 2, 2, 2], [10, 10, 10, 10], rng()) == [0.2, 0.2]
    # clustered passes (one surrogate passes all 20 of its books, nine pass none): the family share is 0.10 and the
    # surrogate interval is far wider than Wilson's, which treats the 200 books as independent
    passes, books = [20] + [0] * 9, [20] * 10
    lo, hi = gs.surrogate_bootstrap(passes, books, rng())
    w = gs.wilson(20, 200)
    assert lo == 0.0 and hi >= 0.2 and hi - lo > 2 * (w[1] - w[0])
    assert gs.surrogate_bootstrap(passes, books, rng()) == [lo, hi]                    # seeded
    assert gs.surrogate_bootstrap(passes, books, rng(), n_boot=2000) == [lo, hi] and gs.N_BOOT == 2000
    # unequal book counts: the share of a resample is its passes over its books (6/8 and 0/2: 0.6 for the pair)
    many = np.random.default_rng(1)
    idx = many.integers(0, 2, size=(2000, 2))
    want = np.array([6, 0])[idx].sum(1) / np.array([8, 2])[idx].sum(1)
    assert gs.surrogate_bootstrap([6, 0], [8, 2], np.random.default_rng(1)) == [float(np.quantile(want, 0.025)),
                                                                                  float(np.quantile(want, 0.975))]
    assert gs.surrogate_bootstrap([3], [5], rng()) is None                 # one surrogate: no interval
    with pytest.raises(ValueError):
        gs.surrogate_bootstrap([1, 0], [1], rng())


def test_identical_books_on_one_surrogate_are_counted_once(market):
    prices = market[0]
    out = gs._surrogate_books(prices, False, 3, 0, 40)
    names = [s.to_spec()["entry"]["rule"] for _, s in out["books"]]
    kept = len(out["books"])
    assert kept + out["duplicate_specs_dropped"] + out["duplicate_books_dropped"] == 40
    assert names.count("hold") == 1 and out["duplicate_specs_dropped"] >= 1     # the hold book has no parameters
    prints = [controls._fingerprint_weights(run_rules(s, out["frame"])[0]) for _, s in out["books"]]
    assert len(set(prints)) == kept                                       # every kept book is a different book
    specs = [json.dumps({k: v for k, v in s.to_spec().items() if k != "name"}, sort_keys=True) for _, s in out["books"]]
    assert len(set(specs)) == kept
    again = gs._surrogate_books(prices, False, 3, 0, 40)
    assert [s.to_spec() for _, s in again["books"]] == [s.to_spec() for _, s in out["books"]]
    assert [k for k, _ in out["books"]] == sorted(k for k, _ in out["books"])          # first draw kept, order kept


def test_drawn_rule_strategies_are_valid_long_only_and_inside_the_stated_ranges(market):
    cols = market[0].columns
    rng = gs._rng(1, "spec", 0)
    specs = [gs.draw_rule_spec(cols, rng, i) for i in range(400)]
    assert {s["entry"]["rule"] for s in specs} == set(SCORES)
    for s in specs:
        RuleStrategy.from_spec(s)                                         # the library accepts every draw
        e = s["entry"]
        assert e["direction"] == "long" and e["exit"] <= e["enter"] and s["sizing"] == {"rule": "equal"} and "risk" not in s
        rg = gs.DRAW_RANGES[e["rule"]]
        for k, v in e.items():
            if k in rg and isinstance(rg[k], tuple):
                assert rg[k][0] <= v <= rg[k][1], (e, k)
    again = [gs.draw_rule_spec(cols, gs._rng(1, "spec", 0), i) for i in range(1)]
    assert again[0] == specs[0]


def test_the_neutral_oracle_has_equal_gross_on_each_side_and_no_crypto(market):
    prices = market[0]
    s = gs.NeutralOracle(prices, 0.6, 5, np.random.default_rng(2))
    w, _ = run_rules(s, prices)
    assert not w[list(CRYPTO)].abs().to_numpy().any()
    held = w[w.abs().sum(axis=1) > 0]
    assert len(held) > 0.9 * len(w)
    assert np.allclose(held.clip(lower=0).sum(axis=1), 0.5) and np.allclose(held.clip(upper=0).sum(axis=1), -0.5)
    from valedia_trader.quant_engine import level10_ml_research as research
    assert research.detect_lookahead(s.target_weights, prices.iloc[1500:2400]) == []     # carries its own future


# ---- the re-timing test on the listed window, and R4's average book

def _held_sharpe(series):
    x = series.to_numpy(float)
    return float(x.mean() / x.std(ddof=1) * np.sqrt(252.0))


def test_r4_s_average_book_is_timing_null_s_exactly_with_its_first_bar_alignment(market):
    prices, dollar, cost = market
    s = controls.OracleCheat(prices, 0.6, 5, gs._rng(3, "E1", 6000, 0), name="study_oracle")
    o = gs.oos_run(prices, dollar, cost, CRITERIA, s)
    w, r, net = o["weights"], o["returns"], o["net"]
    held = gs.held_average_book(w, r)
    # direct: |w| averaged over the window times the sign of the summed weight, earning book[:-1] * r[1:]
    cols = [c for c in w.columns if w[c].abs().sum() > 0]
    book = w[cols].abs().mean() * np.sign(w[cols].sum())
    direct = (r[cols].fillna(0.0).iloc[1:] * book).sum(axis=1)
    assert held.index.equals(w.index[1:]) and np.allclose(held.to_numpy(), direct.to_numpy(), rtol=0, atol=1e-15)
    assert _held_sharpe(held) == gt.timing_null(w, r)["average_book_held_throughout_sharpe"]       # exactly
    active = gs.active_returns(net, w, r)
    assert active.index.equals(w.index[1:]) and active.equals(net.iloc[1:] - held)
    # two-way: the same book one bar earlier (r[:-1], the alignment fixed) is not timing_null's number
    early = pd.Series((r[cols].fillna(0.0).iloc[:-1] * book).sum(axis=1).to_numpy(), index=w.index[1:])
    assert _held_sharpe(early) != gt.timing_null(w, r)["average_book_held_throughout_sharpe"]


def test_the_listed_window_starts_where_every_held_symbol_is_priced(market):
    prices, dollar, cost = market
    late = late_listing(prices)
    s = controls.OracleCheat(late, 0.5, 5, gs._rng(3, "N1", 5000, 0), name="study_oracle")
    o = gs.oos_run(late, dollar, cost, CRITERIA, s)
    w, r, px = o["weights"], o["returns"], o["prices"]
    assert set(LATE) <= {c for c in w.columns if w[c].abs().sum() > 0}   # the book holds the coins
    s0 = int(px.index.get_loc(late.index[1500]))                          # the coins' first price, inside the window
    assert gs.listed_start(w, px) == s0 > 0
    t = gs.timing_listed(w, r, px)
    # the same test as timing_null on the stretch, its average book from the stretch only
    same = gt.timing_null(w.iloc[s0:], r.iloc[s0:])
    assert {k: t[k] for k in same} == same and t["listed_start"] == s0
    assert t["listed_share"] == (len(w) - s0) / len(w)
    assert t["average_book_held_throughout_sharpe"] == _held_sharpe(gs.held_average_book(w.iloc[s0:], r.iloc[s0:]))
    # a book holding only the funds: its listed window is the whole window, and the test is the gate's own
    funds = w.copy()
    funds[list(LATE)] = 0.0
    assert gs.listed_start(funds, px) == 0 and gs.timing_listed(funds, r, px)["p_value"] == gt.timing_null(funds, r)["p_value"]
    # too short a stretch (timing_null's own minimum, 84 bars) or never priced: unavailable
    short = px.copy()
    short.iloc[:-60, short.columns.get_loc("BTC-USD")] = np.nan
    assert "unavailable" in gs.timing_listed(w, r, short) and gs.timing_listed(w, r, short)["listed_start"] == len(w) - 60
    never = px.copy()
    never["BTC-USD"] = np.nan
    gone = gs.timing_listed(w, r, never)
    assert "unavailable" in gone and gone["listed_start"] is None and gs.listed_start(w, never) is None
    assert gs.listed_start(w * 0.0, px) == 0                              # holds nothing


def _p_values(prices, dollar, cost, n):
    """The gate's re-timing p and p_listed of `n` no-skill books (N1: the oracle at hit rate 0.50, 5-bar blocks)."""
    gate, listed, shares = [], [], []
    for k in range(n):
        s = controls.OracleCheat(prices, 0.5, 5, gs._rng(9, "N1", 5000, k), name="study_oracle")
        o = gs.oos_run(prices, dollar, cost, CRITERIA, s)
        gate.append(gt.timing_null(o["weights"], o["returns"])["p_value"])
        t = gs.timing_listed(o["weights"], o["returns"], o["prices"])
        listed.append(t.get("p_value"))
        shares.append(t["listed_share"])
    return gate, listed, shares


def test_p_listed_is_near_uniform_on_no_skill_books_where_the_gate_s_p_is_not(market):
    prices, dollar, cost = market
    n = 100
    gate, listed, shares = _p_values(late_listing(prices), dollar, cost, n)
    assert None not in listed
    share = {"gate": sum(p <= 0.05 for p in gate) / n, "listed": sum(p <= 0.05 for p in listed) / n}
    # Measured on these 100 no-skill books with the two coins listing 1,500 rows late: the gate's p <= 0.05 on 16 of
    # 100 (median p 0.226), p_listed on 7 of 100 (median 0.535); median share of the window inside the listed window
    # 0.479. Marks: p_listed's share within 0.065 of 0.05 (three binomial sds at n = 100: at most 0.115) and its median
    # within 0.15 of 0.5; the gate's share at least 0.12 and its median at most 0.35.
    assert share["listed"] <= 0.115 and abs(float(np.median(listed)) - 0.5) <= 0.15, share
    assert share["gate"] >= 0.12 and float(np.median(gate)) <= 0.35, share
    assert 0.3 <= float(np.median(shares)) <= 0.7


def test_with_no_late_listing_p_listed_is_the_gate_s_p_for_every_book(market):
    prices, dollar, cost = market
    gate, listed, shares = _p_values(prices, dollar, cost, 100)
    assert listed == gate and set(shares) == {1.0}                       # identical, all 100 books (measured 100 of 100)
    # without late listing the gate's own p is near uniform too: measured 5 of 100 at most 0.05; mark as above
    assert sum(p <= 0.05 for p in gate) / 100 <= 0.115


# ---- the report's own anchor counter, two-way

def _anchor_counts(book, frame, market, anchor_book, anchor_priors):
    _, dollar, cost = market
    v = {1: book.judge([]), 31: book.judge(PRIORS)}
    a = {"books": 0, "development_disagreements": 0, "sealed_compared": 0, "sealed_disagreements": 0,
         "material_mismatches": 0, "mismatch_kinds": {}, "at_prior_attempts_books": 0, "at_prior_attempts_disagreements": 0}
    with tempfile.TemporaryDirectory() as tmp:
        anchor_book(a, book, v)
        anchor_priors(a, book, frame, dollar, cost, CRITERIA, Path(tmp) / "p", PRIORS, v[31])
    return a


def test_the_anchor_counter_catches_a_wrong_sealed_verdict(market, tmp_path, monkeypatch):
    prices, dollar, cost = market
    frame, s = _constructed(prices, cost)[0]                              # passes development, fails the sealed test
    book = gs._Book(frame, dollar, cost, CRITERIA, tmp_path / "b", s)
    assert book.gate["dev_pass"] and not book.gate["sealed_pass"]
    ok = _anchor_counts(book, frame, market, gs._anchor_book, gs._anchor_priors)
    assert ok["sealed_compared"] == 1 and ok["sealed_disagreements"] == ok["at_prior_attempts_disagreements"] == 0
    # R0 made wrong in the sealed step (no sealed check): BOTH counters must see it
    monkeypatch.setattr(gs, "_SEALED_CHECKS", ())
    wrong = _anchor_counts(book, frame, market, gs._anchor_book, gs._anchor_priors)
    assert wrong["sealed_disagreements"] == 1 and wrong["at_prior_attempts_disagreements"] == 1

    # two-way: the two mutants of the counter the checker found surviving do NOT see it, so this test kills them
    def no_sealed_comparison(anchor, book, verdicts):                     # `_anchor_book` without its sealed step
        anchor["books"] += 1
        anchor["development_disagreements"] += gs.develops("R0", book.material, (), book.criteria) != book.gate["dev_pass"]

    def development_only(anchor, book, frame, dollar, cost, criteria, folder, priors, verdict):
        g = gs._gate_run(frame, dollar, cost, criteria, folder, book.strategy, priors)
        anchor["at_prior_attempts_books"] += 1
        anchor["at_prior_attempts_disagreements"] += g["dev_pass"] != gs.develops("R0", book.material, priors, criteria)
    mutant = _anchor_counts(book, frame, market, no_sealed_comparison, development_only)
    assert mutant["sealed_disagreements"] == mutant["at_prior_attempts_disagreements"] == 0


def test_two_specs_with_the_same_whole_history_book_are_counted_once(market, monkeypatch):
    prices = market[0]
    base = gs.draw_rule_spec(prices.columns, gs._rng(1, "spec", 0), 0)
    never = 1000.0                                                        # a threshold no score reaches: never enters
    specs = [dict(base, name="study_rule_0", entry={"rule": "momentum", "lookback": 20, "skip": 0, "enter": never,
                                                    "exit": never - 1, "direction": "long"}),
             dict(base, name="study_rule_1", entry={"rule": "ma_cross", "fast": 5, "slow": 20, "enter": never,
                                                    "exit": never - 1, "direction": "long"}),
             dict(base, name="study_rule_2", entry={"rule": "hold", "enter": 1, "exit": 0, "direction": "long"})]
    books = [run_rules(RuleStrategy.from_spec(s), prices)[0] for s in specs]
    assert not books[0].abs().to_numpy().any() and books[0].equals(books[1]) and books[2].abs().to_numpy().any()
    monkeypatch.setattr(gs, "draw_rule_spec", lambda cols, rng, k: specs[k])
    out = gs._surrogate_books(prices, False, 3, 0, 3)
    assert out["duplicate_specs_dropped"] == 0 and out["duplicate_books_dropped"] == 1
    assert [s.to_spec()["entry"]["rule"] for _, s in out["books"]] == ["momentum", "hold"]     # the first kept


# ---- the study end to end

def _guard(monkeypatch, parent: Path):
    real_paths, roots, opened = gs.LoopPaths, [], []

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

    monkeypatch.setattr(gs, "LoopPaths", guarded)
    monkeypatch.setattr(sqlite3, "connect", record)
    return roots, opened


SMALL = {"n_null": 4, "n_edge": 2, "n_surrogates": 2, "specs_per_surrogate": 2}
TINY = {"n_null": 1, "n_edge": 1, "n_surrogates": 1, "specs_per_surrogate": 1}


@pytest.fixture(scope="module")
def small(market, tmp_path_factory):
    """The study at small sizes with the priors, under the real-path guard, in a parent folder of its own."""
    prices, dollar, cost = market
    parent, cwd = tmp_path_factory.mktemp("parent"), tmp_path_factory.mktemp("cwd")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(tempfile, "tempdir", str(parent))
        mp.chdir(cwd)
        roots, opened = _guard(mp, parent)
        rep = gs.study(prices, dollar, cost, prior_attempt_sharpes=PRIORS, **SMALL)
    return {"report": rep, "roots": roots, "opened": opened, "left": sorted(p.name for p in parent.rglob("*")),
            "cwd": list(cwd.iterdir()), "parent": os.path.realpath(parent)}


def test_it_leaves_no_file_and_never_uses_a_real_path(small):
    assert small["roots"] and small["opened"] and small["left"] == [] and small["cwd"] == []
    assert not any(p.startswith(os.path.realpath(REPO)) for p in small["opened"])
    assert all(p.startswith(small["parent"]) for p in small["opened"])


def test_the_report_is_strict_json_with_every_family_rule_and_attempt_count(small):
    rep = small["report"]
    text = json.dumps(rep, allow_nan=False, sort_keys=True)
    assert json.loads(text) == rep
    assert rep["attempt_counts"] == [1, 31] and rep["prior_attempts"] == 30
    books = 0
    for version in ("given", "detrended"):
        fams = rep["results"][version]
        assert list(fams) == ["N1", "N2", "N3", "N4a", "N4b", "E1", "E2", "E3"]
        for fam, cells in fams.items():
            want = {"N1": 4, "N2": 4, "N3": 4, "N4a": 4, "N4b": 4}.get(fam, 2)
            sur = fam in ("N4a", "N4b")
            assert [c["hit_rate"] for c in cells] == ([0.5] if fam in ("N1", "N2") else [None] if fam == "N3" or sur
                                                      else [0.52, 0.55, 0.60])
            for c in cells:
                books += c["runs"]
                if sur:                                                  # identical books on a surrogate count once
                    assert c["surrogates"] == 2 and c["runs"] + c["duplicate_specs_dropped"] + c["duplicate_books_dropped"] == want
                    want = c["runs"]
                assert c["runs"] == want and set(c["by_attempts"]) == {"1", "31"}
                # no late listing here: every book's re-timing tests ran and the listed window is the whole window
                assert c["timing_unavailable"] == {"gate": 0, "listed": 0} and c["median_listed_share"] == 1.0
                for n, row in c["by_attempts"].items():
                    assert list(row) == list(gs.RULES)
                    for rule, r in row.items():
                        assert r["runs"] == want and r["share"] == r["passes"] / want
                        assert r["wilson_95"] == gs.wilson(r["passes"], want)
                        if rule.endswith("_listed"):                     # the same p-values, so the same verdicts
                            assert r == row[rule.replace("_listed", "_gate")]
                        if sur:
                            per = r["per_surrogate_share"]
                            assert 0 <= per["min"] <= per["median"] <= per["max"] <= 1
                            lo, hi = r["surrogate_bootstrap_95"]
                            assert per["min"] <= lo <= r["share"] <= hi <= per["max"]
    n4 = sum(c["runs"] for v in rep["results"].values() for f in ("N4a", "N4b") for c in v[f])
    assert rep["books"] == books == 2 * (12 + 18) + n4
    a = rep["anchor"]
    assert a["books"] == books and a["at_prior_attempts_books"] == 2 * 14     # the first book of every cell
    assert a["at_prior_attempts_scope"] == "the first book of every cell"
    assert a["development_disagreements"] == a["sealed_disagreements"] == a["material_mismatches"] == 0
    assert a["at_prior_attempts_disagreements"] == 0 and a["sealed_compared"] > 0
    assert rep["r3_re_timing"]["shifts"] == {"1": 400, "31": 12400}
    assert rep["surrogates"]["mean_block"] == {"N4a": 1, "N4b": 20}
    assert rep["surrogates"]["listing_regimes"] == [{"from": "2013-05-08", "to": "2026-11-13", "bars": 3528,
                                                     "listed_symbols": 17}]
    assert "keeps its edge here" in rep["families"]["N4b"]


def test_the_reading_says_it_in_words(small):
    rep, reading = small["report"], small["report"]["reading"]
    # per rule: each no-skill family, version and attempt count, and each edge family likewise; one closing sentence
    # 9 rules x 5 no-skill families x 2 versions x 2 counts; 9 x 3 edge families x 2 x 2; the gate's and the listed
    # window's verdicts side by side for R1 to R4 on the 5 no-skill families, x 2 x 2; one N4b label; one closing
    assert sum("false-pass share on" in s for s in reading) == 9 * 5 * 2 * 2
    assert sum("smallest hit rate passing at least 80%" in s for s in reading) == 9 * 3 * 2 * 2
    assert sum(" pass with the gate's " in s and " with the listed window's " in s for s in reading) == 4 * 5 * 2 * 2
    assert sum(s.startswith(("Rules whose false-pass interval", "No rule has a false-pass")) for s in reading) == 1
    assert sum("short-range dependence of the data kept: a rule that lives inside 20 bars keeps its edge here" in s
               for s in reading) == 1
    assert reading[-1].startswith(f"Anchor: R0 judged from the kept material agreed with the real gate on {rep['books']} books")
    side = rep["results"]["detrended"]["N2"][0]
    g, li = side["by_attempts"]["31"]["R3_gate"], side["by_attempts"]["31"]["R3_listed"]
    assert (f"R3 on N2 (the detrended data, 31 attempts): {g['passes']} of {g['runs']} pass with the gate's re-timing "
            f"p-value, {li['passes']} of {li['runs']} with the listed window's (re-timing test unavailable: 0 and 0 "
            "books).") in reading
    # N4a and N4b are read from the bootstrap over whole surrogates, and say so
    for fam in ("N4a", "N4b"):
        r4 = rep["results"]["given"][fam][0]["by_attempts"]["1"]["R0"]
        lo, hi = r4["surrogate_bootstrap_95"]
        assert (f"R0 false-pass share on {fam} (the data as given, 1 attempt): {r4['passes']} of {r4['runs']} "
                f"({r4['share']:.3f}, 95% interval {lo:.3f} to {hi:.3f} from a bootstrap over 2 whole surrogates, not "
                "the Wilson interval).") in reading
    r = rep["results"]["given"]["N1"][0]["by_attempts"]["1"]["R0"]
    lo, hi = r["wilson_95"]
    assert (f"R0 false-pass share on N1 (the data as given, 1 attempt): {r['passes']} of {r['runs']} "
            f"({r['share']:.3f}, 95% interval {lo:.3f} to {hi:.3f}).") in reading
    named = [s for s in reading if s.startswith("Rules whose false-pass interval")]
    def lower(r, f):
        return (r["surrogate_bootstrap_95"] or [0.0])[0] if f in ("N4a", "N4b") else r["wilson_95"][0]
    above = {rule for rule in gs.RULES for v in ("given", "detrended") for n in ("1", "31") for f in gs.NO_SKILL
             if lower(rep["results"][v][f][0]["by_attempts"][n][rule], f) > 0.05}
    assert (named and all(f"{rule} (" in named[0] for rule in above)) or (not named and not above)
    # two-way: a report with a no-skill interval wholly above 0.05 names that rule
    risen = json.loads(json.dumps(rep))
    risen["results"]["given"]["N3"][0]["by_attempts"]["1"]["R3_listed"].update(passes=4, runs=4, share=1.0,
                                                                               wilson_95=gs.wilson(4, 4))
    assert any(s.startswith("Rules whose false-pass interval") and "R3_listed (N3 given 1 attempt" in s
               for s in gs._reading(risen))
    # for N4a/N4b the surrogate bootstrap decides, not Wilson: a Wilson interval above 0.05 alone does not name it ...
    import re

    def named_for(report, rule):
        line = next((x for x in gs._reading(report) if x.startswith("Rules whose false-pass interval")), "")
        found = re.search(rf"{rule} \(([^)]*)\)", line)
        return found.group(1).split(", ") if found else []
    wide = json.loads(json.dumps(rep))
    cell = wide["results"]["given"]["N4a"][0]["by_attempts"]["1"]["R4_gate"]
    cell.update(wilson_95=[0.2, 0.9], surrogate_bootstrap_95=[0.0, 0.9])
    assert "N4a given 1 attempt" not in named_for(wide, "R4_gate")
    # ... and a bootstrap interval above 0.05 does
    cell.update(wilson_95=[0.0, 0.9], surrogate_bootstrap_95=[0.2, 0.9])
    assert "N4a given 1 attempt" in named_for(wide, "R4_gate")


def test_the_small_study_s_numbers(small):
    res = small["report"]["results"]
    given, flat = res["given"], res["detrended"]

    def k(version, fam, rule, n="1", h=0):
        return res[version][fam][h]["by_attempts"][n][rule]["passes"]
    # Measured at Step 0 at these sizes (4 books per no-skill family, 2 per edge cell, 30 priors):
    # on the RISING data the gate as it is passed 4 of 4 random-timing books (N1) at one attempt (78 of 100 at Step 0)
    # and no other rule passed any; mark: R0 at least 2, every other rule at most 1
    assert k("given", "N1", "R0") >= 2 and all(k("given", "N1", r) <= 1 for r in gs.RULES[1:])
    # detrended, no rule passed any no-skill book at either count (0 of 4 everywhere); mark: at most 1 of 4
    assert all(k("detrended", f, r, n) <= 1 for f in gs.NO_SKILL for r in gs.RULES for n in ("1", "31"))
    # the h = 0.60 oracle passed every rule at one attempt in E1 and E3, both versions (2 of 2); mark: at least 1 of 2
    assert all(k(v, f, r, "1", 2) >= 1 for v in ("given", "detrended") for f in ("E1", "E3") for r in gs.RULES)
    # median development Sharpe: E1 h = 0.60 2.37 against N1 0.75 as given (gap 1.62; mark 0.55, a third), and the
    # detrended N1 median -0.15 (mark: within 0.45 of zero, three times)
    assert given["E1"][2]["median_development_sharpe"] - given["N1"][0]["median_development_sharpe"] >= 0.55
    assert abs(flat["N1"][0]["median_development_sharpe"]) <= 0.45
    # the priors raise R0's bar: at 31 attempts R0 never passes more books than at one, in any cell
    assert all(c["by_attempts"]["31"]["R0"]["passes"] <= c["by_attempts"]["1"]["R0"]["passes"]
               for v in res.values() for cells in v.values() for c in cells)


def test_the_same_seed_gives_the_same_report_and_another_seed_another(market, tmp_path, monkeypatch, capsys):
    prices, dollar, cost = market
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(gs, "PROGRESS_EVERY", 5)
    one = gs.study(prices, dollar, cost, **TINY)
    err = capsys.readouterr().err
    assert one["books"] == 28                                             # 14 cells, one book each, two versions
    assert sum(line.startswith("gate_study: ") and " books in " in line for line in err.splitlines()) == 28 // 5
    two, other = gs.study(prices, dollar, cost, **TINY), gs.study(prices, dollar, cost, seed=7, **TINY)
    text = json.dumps(one, allow_nan=False, sort_keys=True)
    assert text == json.dumps(two, allow_nan=False, sort_keys=True)
    assert json.dumps(other, allow_nan=False, sort_keys=True) != text      # two-way: the seed does change it
    assert one["attempt_counts"] == [1] and one["anchor"]["at_prior_attempts_books"] == 0
    assert one["anchor"]["at_prior_attempts_scope"] == "no prior attempts"
    assert list(tmp_path.iterdir()) == []


def test_anchor_all_re_runs_every_book_at_the_prior_attempts(market, tmp_path, monkeypatch):
    prices, dollar, cost = market
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    rep = gs.study(prices, dollar, cost, prior_attempt_sharpes=PRIORS[:5], anchor_all=True, **TINY)
    a = rep["anchor"]
    assert a["at_prior_attempts_books"] == a["books"] == rep["books"] and a["at_prior_attempts_scope"] == "every book"
    assert a["at_prior_attempts_disagreements"] == a["development_disagreements"] == a["sealed_disagreements"] == 0
    assert list(tmp_path.iterdir()) == []


def test_bad_sizes_and_priors_are_refused(market):
    prices, dollar, cost = market
    with pytest.raises(ValueError, match="at least 1"):
        gs.study(prices, dollar, cost, n_null=0)
    with pytest.raises(ValueError, match="finite"):
        gs.study(prices, dollar, cost, prior_attempt_sharpes=[0.1, float("nan")])


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
    from valedia_trader.research_loop import ledger as lg
    led = lg.HypothesisLedger(ledger)
    a = led.propose("Momentum on exchange traded funds lasts for months", "price-analyst", "us-etf+crypto")
    for s in (0.03, -0.01, 0.02):
        led.record_attempt(a, "backtest", s, 2000, "1.0.0")
    led.close()
    raw = ledger.read_bytes()
    seen = {}

    def fake(p, d, c, **kw):
        seen.update(prices=p, dollar=d, cost=c, **kw)
        return gt._clean({"ok": float("nan")})

    monkeypatch.setattr(gs, "study", fake)
    assert gs.main(["--data", str(data), "--prior-from-ledger", str(ledger), "--market", "us-etf+crypto",
                    "--n-null", "7", "--n-edge", "3", "--n-surrogates", "2", "--specs-per-surrogate", "5",
                    "--seed", "9"]) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": None}
    assert list(seen["prior_attempt_sharpes"]) == [0.03, -0.01, 0.02] and ledger.read_bytes() == raw
    assert (seen["n_null"], seen["n_edge"], seen["n_surrogates"], seen["specs_per_surrogate"], seen["seed"]) == (7, 3, 2, 5, 9)
    assert seen["anchor_all"] is False                                    # the default stays as it was
    gs.main(["--data", str(data), "--anchor-all"])
    assert seen["anchor_all"] is True
    from valedia_trader.research_loop import cli
    _, _, research_cost = cli._market(type("A", (), {"data": str(data)})())
    assert seen["cost"] == research_cost and seen["prices"].shape == prices.shape
    gs.main(["--data", str(data)])
    assert list(seen["prior_attempt_sharpes"]) == [] and seen["n_null"] == 400 and seen["seed"] == 20261116
