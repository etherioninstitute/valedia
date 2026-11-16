"""The gate-rule study: R0 judged from the kept material must be the real gate's verdict, every rule's pure function
must say what it claims on hand-built inputs, the surrogate prices must keep the cross-section, and the report must be
strict JSON, deterministic for a seed, and leave nothing behind.

Pass marks come from the builder's Step 0 measurements on the RISING universe `universe(7)` (17 symbols, 14 years,
the equal-weight basket rising; the shape of the real data), unless a comment names another. 30 prior attempts are
drawn from N(0.005, 0.03) with seed 0, as in test_controls. Each mark has both numbers in its comment: what was
measured, and the mark, set at about three times the margin seen.
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
from valedia_trader.research_loop.strategy import CRYPTO, SCORES, RuleStrategy, run_rules

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


@pytest.fixture(scope="module")
def anchor_rows(market, tmp_path_factory):
    """Each book of the sample, on the data as given and detrended (plus two drawn rule strategies on a surrogate):
    the study's own `_Book` (the gate's fresh-ledger run plus the kept material) and the real gate again in a ledger
    seeded with the priors."""
    prices, dollar, cost = market
    tmp = tmp_path_factory.mktemp("anchor")
    books = _sample(prices) + _sample(controls.detrend(prices))
    sur = gs.surrogate_prices(prices, gs._rng(5, "surrogate", 0))
    spec_rng = gs._rng(5, "spec", 0)
    books += [(sur, RuleStrategy.from_spec(gs.draw_rule_spec(sur.columns, spec_rng, i))) for i in range(2)]
    rows = []
    for i, (frame, s) in enumerate(books):
        book = gs._Book(frame, dollar, cost, CRITERIA, tmp / f"b{i}", s)
        book.sealed()                                                     # computed for every book of the sample
        at_prior = gs._gate_run(frame, dollar, cost, CRITERIA, tmp / f"p{i}", s, PRIORS)
        rows.append({"material": book.material, "fresh": book.gate, "at_prior": at_prior, "mismatches": book.mismatches})
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
    assert len(anchor_rows) == 20 and not any(r["fresh"]["refused"] or r["at_prior"]["refused"] for r in anchor_rows)
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
    assert _anchor_disagreements(anchor_rows) == 0


# ---- each rule's pure function on hand-built inputs

def material(**kw):
    """A book that passes everything: per-period Sharpe 0.05 over 2000 bars (PSR 0.987), re-timing p 0.01."""
    m = {"refused": False, "own": [0.05], "timing_p": 0.01, "strict_p": {}, "development_sharpe": 0.8,
         "checks": dict.fromkeys(("prediction_failure_terms", "deflated_sharpe", "walk_forward", "double_costs",
                                  "delayed_fills", "worst_periods"), True),
         "moments": {"sharpe": 0.05, "n_obs": 2000, "skew": 0.0, "kurt": 3.0},
         "active_moments": {"sharpe": 0.05, "n_obs": 2000, "skew": 0.0, "kurt": 3.0},
         "sealed": {"made_money": True, "prediction_failure_terms": True}}
    m.update(kw)
    return m


def verdicts(m, others=()):
    return {r: gs.develops(r, m, list(others), CRITERIA) for r in ("R0", "R1", "R2", "R4")}


def test_a_book_that_passes_r0_and_fails_r1_on_its_re_timing_p_value():
    m = material(timing_p=0.2)
    assert verdicts(m) == {"R0": True, "R1": False, "R2": False, "R4": True}
    assert gs.passes("R0", m, [], CRITERIA) and not gs.passes("R1", m, [], CRITERIA)
    assert not gs.develops("R1", material(timing_p=None), [], CRITERIA)          # no re-timing test: not timed


def test_a_book_that_fails_r0_only_because_its_own_sharpe_is_in_the_variance_passes_r2():
    others = [0.001, -0.001, 0.0005]                                     # three earlier attempts, nearly no spread
    m = material()
    with_own = gs.deflated_value(m["moments"], m["own"], others)
    without = gs.deflated_value(m["moments"], m["own"], others, leave_own_out=True)
    assert with_own < CRITERIA.min_dsr <= without                        # measured 0.913 and 0.986
    assert verdicts(m, others) == {"R0": False, "R1": False, "R2": True, "R4": True}
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
    assert gs.develops("R1", material(timing_p=0.05), [], CRITERIA)
    assert not gs.develops("R1", material(timing_p=float(np.nextafter(0.05, 1))), [], CRITERIA)
    others = [0.01, 0.02]                                                # N = 3 attempts counted: R3 needs p <= 0.05 / 3
    assert gs.develops("R3", material(strict_p={3: 0.05 / 3}), others, CRITERIA)
    assert not gs.develops("R3", material(strict_p={3: float(np.nextafter(0.05 / 3, 1))}), others, CRITERIA)
    assert gs.r3_shifts(1) == 400 and gs.r3_shifts(31) == 12400        # 20 times the reciprocal of 0.05 / N
    assert all(gs.r3_shifts(n) >= 20 / (0.05 / n) for n in range(1, 60))


def test_r3_drops_the_deflation_and_needs_its_strict_p_only_when_the_rest_passes():
    spread = [0.04, -0.04, 0.03, -0.03, 0.0]                             # a wide spread of attempts: a high bar
    m = material(strict_p={6: 0.001})
    assert not gs.develops("R0", m, spread, CRITERIA)                     # deflated by 6 attempts
    assert gs.develops("R3", m, spread, CRITERIA)                         # one trial, p 0.001 <= 0.05 / 6
    assert not gs.develops("R3", material(strict_p={6: 0.01}), spread, CRITERIA)
    with pytest.raises(KeyError):
        gs.develops("R3", material(), spread, CRITERIA)                   # the rest passes: the strict p is needed
    failing = material(checks=dict(material()["checks"], delayed_fills=False))
    assert gs.develops("R3", failing, spread, CRITERIA) is False         # the rest fails: no p needed, no KeyError


def test_r4_judges_the_active_returns_and_every_rule_keeps_the_gate_s_other_checks():
    m = material(active_moments={"sharpe": 0.0, "n_obs": 2000, "skew": 0.0, "kurt": 3.0})
    assert verdicts(m) == {"R0": True, "R1": True, "R2": True, "R4": False}
    assert verdicts(material(active_moments=None))["R4"] is False          # no usable active returns: value 0
    for check in ("prediction_failure_terms", "walk_forward", "double_costs", "delayed_fills", "worst_periods"):
        bad = material(checks=dict(material()["checks"], **{check: False}), strict_p={1: 0.001})
        assert not any(gs.develops(r, bad, [], CRITERIA) for r in gs.RULES), check
    # the report's own deflated_sharpe boolean is not read: each rule computes its own Sharpe test
    m = material(checks=dict(material()["checks"], deflated_sharpe=False), strict_p={1: 0.001})
    assert all(gs.develops(r, m, [], CRITERIA) for r in gs.RULES)


def test_the_sealed_checks_and_refused_books():
    m = material(sealed={"made_money": False, "prediction_failure_terms": True}, strict_p={1: 0.001})
    assert all(gs.develops(r, m, [], CRITERIA) and not gs.passes(r, m, [], CRITERIA) for r in gs.RULES)
    with pytest.raises(KeyError):
        gs.passes("R0", material(sealed=None), [], CRITERIA)
    refused = material(refused=True, strict_p={1: 0.001})
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


def test_the_surrogate_keeps_missing_prices_missing_and_resamples_complete_rows_only(market):
    prices = market[0].copy()
    prices.iloc[:800, prices.columns.get_loc("ETH-USD")] = np.nan         # listed later
    prices.iloc[1500:1510, prices.columns.get_loc("GLD")] = np.nan       # a gap
    sur = gs.surrogate_prices(prices, gs._rng(1, "surrogate", 0))
    assert sur.isna().equals(prices.isna())
    assert sur["ETH-USD"].iloc[800] == prices["ETH-USD"].iloc[800]        # rebuilt from its own first price
    r = _simple(sur).to_numpy()
    assert np.isfinite(r[800:1499]).all() and (np.nanmin(sur.to_numpy()) > 0)
    # every surrogate bar is a complete row of the original: its cross-section appears there whole
    orig = _simple(prices)
    complete = orig[orig.notna().all(axis=1)].to_numpy()
    nearest = [float(np.abs(complete - x).max(axis=1).min()) for x in r[900:1400]]
    assert max(nearest) <= 1e-9                                          # rebuilt prices: rounding only
    short = prices.copy()
    short.iloc[:, 0] = np.nan
    short.iloc[-200:, 0] = 100.0
    with pytest.raises(ValueError, match="at least 252"):
        gs.surrogate_prices(short, gs._rng(1, "surrogate", 0))


# ---- the books

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


SMALL = {"n_null": 4, "n_edge": 2, "n_surrogates": 1, "specs_per_surrogate": 4}
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
        assert list(fams) == ["N1", "N2", "N3", "N4", "E1", "E2", "E3"]
        for fam, cells in fams.items():
            want = {"N1": 4, "N2": 4, "N3": 4, "N4": 4}.get(fam, 2)
            assert [c["hit_rate"] for c in cells] == ([0.5] if fam in ("N1", "N2") else [None] if fam in ("N3", "N4")
                                                      else [0.52, 0.55, 0.60])
            for c in cells:
                books += c["runs"]
                assert c["runs"] == want and set(c["by_attempts"]) == {"1", "31"}
                for n, row in c["by_attempts"].items():
                    assert list(row) == list(gs.RULES)
                    for r in row.values():
                        assert r["runs"] == want and r["share"] == r["passes"] / want
                        assert r["wilson_95"] == gs.wilson(r["passes"], want)
    assert rep["books"] == books == 2 * (12 + 4 + 18)
    a = rep["anchor"]
    assert a["books"] == books and a["at_prior_attempts_books"] == 2 * 13     # the first book of every cell
    assert a["development_disagreements"] == a["sealed_disagreements"] == a["material_mismatches"] == 0
    assert a["at_prior_attempts_disagreements"] == 0 and a["sealed_compared"] > 0
    assert rep["r3_re_timing"]["shifts"] == {"1": 400, "31": 12400}


def test_the_reading_says_it_in_words(small):
    rep, reading = small["report"], small["report"]["reading"]
    # per rule: each no-skill family, version and attempt count, and each edge family likewise; one closing sentence
    assert sum("false-pass share on" in s for s in reading) == 5 * 4 * 2 * 2
    assert sum("smallest hit rate passing at least 80%" in s for s in reading) == 5 * 3 * 2 * 2
    assert sum(s.startswith(("Rules whose false-pass interval", "No rule has a false-pass")) for s in reading) == 1
    assert reading[-1].startswith("Anchor: R0 judged from the kept material agreed with the real gate on 68 books")
    r = rep["results"]["given"]["N1"][0]["by_attempts"]["1"]["R0"]
    lo, hi = r["wilson_95"]
    assert (f"R0 false-pass share on N1 (the data as given, 1 attempt): {r['passes']} of {r['runs']} "
            f"({r['share']:.3f}, 95% interval {lo:.3f} to {hi:.3f}).") in reading
    named = [s for s in reading if s.startswith("Rules whose false-pass interval")]
    above = {rule for rule in gs.RULES for v in ("given", "detrended") for n in ("1", "31") for f in gs.NO_SKILL
             if rep["results"][v][f][0]["by_attempts"][n][rule]["wilson_95"][0] > 0.05}
    assert (named and all(f"{rule} (" in named[0] for rule in above)) or (not named and not above)
    # two-way: a report with a no-skill interval wholly above 0.05 names that rule
    risen = json.loads(json.dumps(rep))
    risen["results"]["given"]["N3"][0]["by_attempts"]["1"]["R3"].update(passes=4, runs=4, share=1.0, wilson_95=gs.wilson(4, 4))
    assert any(s.startswith("Rules whose false-pass interval") and "R3 (N3 given 1 attempt" in s for s in gs._reading(risen))


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
    assert sum(line.startswith("gate_study: ") and " books in " in line for line in err.splitlines()) == 26 // 5
    two, other = gs.study(prices, dollar, cost, **TINY), gs.study(prices, dollar, cost, seed=7, **TINY)
    text = json.dumps(one, allow_nan=False, sort_keys=True)
    assert text == json.dumps(two, allow_nan=False, sort_keys=True)
    assert json.dumps(other, allow_nan=False, sort_keys=True) != text      # two-way: the seed does change it
    assert one["attempt_counts"] == [1] and one["anchor"]["at_prior_attempts_books"] == 0
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
    from valedia_trader.research_loop import cli
    _, _, research_cost = cli._market(type("A", (), {"data": str(data)})())
    assert seen["cost"] == research_cost and seen["prices"].shape == prices.shape
    gs.main(["--data", str(data)])
    assert list(seen["prior_attempt_sharpes"]) == [] and seen["n_null"] == 400 and seen["seed"] == 20261116
