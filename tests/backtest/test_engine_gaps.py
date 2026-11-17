"""The engine on bars with no price (T38-3a).

A bar is PRICED for a symbol when its price is finite and above zero. On an unpriced bar the engine neither buys
nor sells the symbol: the weight carried from the bar before stays in effect (drifted by the book's return), it adds
nothing to turnover or trading costs, borrow and funding go on, and the return booked when the price comes back is
the move since the last priced bar. A position still open on an unpriced last bar is reported in `stranded`.

`run_backtest` below is a verbatim copy of the engine at 70d20fc: the reference that protects every result computed
before this change (frames whose unpriced bars all lie before each symbol's first priced bar)."""
import numpy as np
import pandas as pd
import pytest

from valedia_trader.backtest import engine
from valedia_trader.backtest.engine import BacktestResult, CostModel
from valedia_trader.quant_engine import level10_ml_research as research

FREE = CostModel(fees_bps=0.0, slippage_bps=0.0, borrow_annual=0.0, funding_annual=0.0)


# ---- reference: run_backtest exactly as at 70d20fc (valedia_trader/backtest/engine.py) -------------------------
def run_backtest(prices: pd.DataFrame, decided_weights: pd.DataFrame, cost: CostModel,
                 dollar_volume: pd.DataFrame | None = None, capital: float = 1_000_000.0, execution_lag: int = 1,
                 vol_window: int = 20, periods_per_year: float = 252.0) -> BacktestResult:
    """Run the backtest. `decided_weights.loc[t]` must depend only on prices up to and including t
    (verify with quant_engine.level10_ml_research.detect_lookahead); the engine adds the execution lag."""
    research.require_complete_costs(cost.as_config())
    if execution_lag < 1:
        raise ValueError("execution_lag must be >= 1: same-bar execution is look-ahead")
    if not prices.index.equals(decided_weights.index) or list(prices.columns) != list(decided_weights.columns):
        raise ValueError("prices and decided_weights must share index and columns")
    if not prices.index.is_monotonic_increasing or prices.index.has_duplicates:
        raise ValueError("price index must be strictly increasing")
    if capital <= 0:
        raise ValueError("capital must be positive")
    cols = prices.columns
    px = prices.to_numpy(dtype=float)
    ret = np.zeros_like(px)
    ret[1:] = px[1:] / px[:-1] - 1.0
    tradable = np.isfinite(px) & (px > 0)
    ret[~np.isfinite(ret)] = 0.0
    decided = decided_weights.to_numpy(dtype=float)
    decided = np.where(np.isfinite(decided), decided, 0.0)
    held = np.zeros_like(decided)
    held[execution_lag:] = decided[:-execution_lag]
    held = np.where(tradable, held, 0.0)

    fee = cost._per_asset(cost.fees_bps, cols, "fees") * 1e-4
    slip = cost._per_asset(cost.slippage_bps, cols, "slippage") * 1e-4
    fund = cost._per_asset(cost.funding_annual, cols, "funding") / periods_per_year
    borrow = float(cost.borrow_annual) / periods_per_year

    ret_df = pd.DataFrame(ret, index=prices.index, columns=cols)
    sigma = ret_df.rolling(vol_window).std().shift(1).to_numpy()            # known before the trade
    adv = None
    if dollar_volume is not None:
        adv = dollar_volume.reindex(index=prices.index, columns=cols).rolling(vol_window).mean().shift(1).to_numpy()

    n = len(prices)
    gross, turnover = np.zeros(n), np.zeros(n)
    c_comm, c_slip, c_imp, c_bor, c_fund = (np.zeros(n) for _ in range(5))
    equity = capital
    prev = np.zeros(len(cols))
    for t in range(n):
        g = float(prev @ ret[t])
        gross[t] = g
        drifted = prev * (1.0 + ret[t]) / (1.0 + g) if g > -1.0 else np.zeros_like(prev)
        trade = np.abs(held[t] - drifted)
        turnover[t] = trade.sum()
        c_comm[t], c_slip[t] = float(trade @ fee), float(trade @ slip)
        if adv is not None and turnover[t] > 0:
            part = np.divide(trade * equity, adv[t], out=np.zeros_like(trade), where=np.isfinite(adv[t]) & (adv[t] > 0))
            sig = np.where(np.isfinite(sigma[t]), sigma[t], 0.0)
            c_imp[t] = float(np.sum(trade * cost.impact_y * sig * np.sqrt(part)))
        c_bor[t] = float(np.maximum(-prev, 0.0).sum() * borrow)
        c_fund[t] = float(np.abs(prev) @ fund)
        net_t = g - (c_comm[t] + c_slip[t] + c_imp[t] + c_bor[t] + c_fund[t])
        equity *= max(1.0 + net_t, 1e-12)
        prev = held[t]
    costs = pd.DataFrame({"commission": c_comm, "slippage": c_slip, "impact": c_imp, "borrow": c_bor,
                          "funding": c_fund}, index=prices.index)
    gross_s = pd.Series(gross, index=prices.index, name="gross")
    return BacktestResult((gross_s - costs.sum(axis=1)).rename("net"), gross_s, costs,
                          pd.DataFrame(held, index=prices.index, columns=cols),
                          pd.Series(turnover, index=prices.index, name="turnover"))
# ---- end of reference ------------------------------------------------------------------------------------------


def _priced(px: pd.DataFrame) -> np.ndarray:
    a = px.to_numpy(dtype=float)
    return np.isfinite(a) & (a > 0)


def _no_interior_gap(px: pd.DataFrame) -> bool:
    """Every symbol's unpriced bars all lie before its first priced bar."""
    ok = _priced(px)
    seen = np.maximum.accumulate(ok, axis=0)
    return bool(np.all(ok | ~seen))


def _assert_same(new: BacktestResult, ref: BacktestResult, tol: float = 1e-12, rows: int | None = None) -> None:
    for name in ("net", "gross", "turnover"):
        a, b = getattr(new, name), getattr(ref, name)
        b = b.iloc[:rows] if rows is not None else b
        assert a.index.equals(b.index), name
        np.testing.assert_allclose(a.to_numpy(), b.to_numpy(), rtol=0, atol=tol, err_msg=name)
    for name in ("costs", "weights"):
        a, b = getattr(new, name), getattr(ref, name)
        b = b.iloc[:rows] if rows is not None else b
        assert a.index.equals(b.index) and list(a.columns) == list(b.columns), name
        np.testing.assert_allclose(a.to_numpy(), b.to_numpy(), rtol=0, atol=tol, err_msg=name)


def _frame(rng, n: int, m: int):
    idx = pd.bdate_range("2021-01-04", periods=n)
    cols = [f"S{j}" for j in range(m)]
    px = 100.0 * np.exp(np.cumsum(0.02 * rng.standard_normal((n, m)), axis=0))
    return idx, cols, px


def _inputs(rng, idx, cols, n, m):
    w = rng.uniform(-1.0, 1.0, (n, m)) * (rng.random((n, m)) < 0.7)
    w[rng.random((n, m)) < 0.05] = np.nan
    w[rng.random((n, m)) < 0.02] = np.inf
    w[rng.random((n, m)) < 0.02] = -np.inf
    dv = None
    if rng.random() < 0.5:
        d = rng.uniform(1e5, 1e7, (n, m))
        d[rng.random((n, m)) < 0.05] = np.nan
        d[rng.random((n, m)) < 0.02] = 0.0
        dv = pd.DataFrame(d, index=idx, columns=cols)
    if rng.random() < 0.5:
        fees = {c: float(rng.uniform(0, 20)) for c in cols}
        fund = {c: float(rng.choice([0.0, rng.uniform(0, 0.1)])) for c in cols}
    else:
        fees, fund = float(rng.uniform(0, 20)), float(rng.choice([0.0, 0.05]))
    cost = CostModel(fees, float(rng.uniform(0, 10)), float(rng.uniform(0, 0.05)), fund,
                     impact_y=float(rng.uniform(0.5, 2.0)))
    kw = dict(dollar_volume=dv, execution_lag=int(rng.integers(1, 4)), vol_window=int(rng.choice([3, 5, 20])),
              capital=float(rng.choice([1e5, 1e6, 1e8])))
    return pd.DataFrame(w, index=idx, columns=cols), cost, kw


def _late_listing_frame(rng):
    """A frame the old engine already handled: unpriced bars (NaN, zero, negative or infinite) only before each
    symbol's first priced bar; sometimes a symbol is never priced at all."""
    n, m = int(rng.integers(25, 90)), int(rng.integers(1, 5))
    idx, cols, px = _frame(rng, n, m)
    odd = rng.random() < 0.3                                   # some frames fill the pre-listing bars with junk
    for j in range(m):
        r = rng.random()
        first = 0 if r < 0.35 else (n if r > 0.92 else int(rng.integers(1, n)))
        if odd:
            px[:first, j] = rng.choice([np.nan, 0.0, -1.0, -50.0, np.inf, -np.inf], size=first)
        else:
            px[:first, j] = np.nan
    return pd.DataFrame(px, index=idx, columns=cols)


def _gappy_frame(rng):
    """Interior gaps: runs of NaN, zero, negative or infinite prices after a symbol's first priced bar."""
    n, m = int(rng.integers(25, 90)), int(rng.integers(1, 4))
    idx, cols, px = _frame(rng, n, m)
    for j in range(m):
        first = int(rng.integers(0, n // 3))
        px[:first, j] = np.nan
        for _ in range(int(rng.integers(1, 4))):
            a = int(rng.integers(first, n))
            b = min(n, a + int(rng.integers(1, 12)))
            px[a:b, j] = rng.choice([np.nan, 0.0, -2.0, np.inf])
    return pd.DataFrame(px, index=idx, columns=cols)


# ---- equivalence with the old engine on every frame without an interior gap --------------------------------------
def test_frames_without_interior_gaps_match_the_old_engine_to_1e12():
    rng = np.random.default_rng(38)
    frames, late, junk, impact, lags, dv_on, shorts = 0, 0, 0, 0, set(), 0, 0
    for _ in range(320):
        px = _late_listing_frame(rng)
        assert _no_interior_gap(px)
        w, cost, kw = _inputs(rng, px.index, list(px.columns), *px.shape)
        new = engine.run_backtest(px, w, cost, **kw)
        ref = run_backtest(px, w, cost, **kw)
        _assert_same(new, ref)
        assert new.stranded == {}
        frames += 1
        a = px.to_numpy(dtype=float)
        late += bool((~_priced(px))[0].any())
        junk += bool((np.isfinite(a) & (a <= 0)).any() | np.isinf(a).any())
        impact += bool(ref.costs["impact"].sum() > 0)
        lags.add(kw["execution_lag"])
        dv_on += kw["dollar_volume"] is not None
        shorts += bool((ref.weights.to_numpy() < 0).any())
    assert frames >= 300 and late > 100 and junk > 30 and impact > 50 and lags == {1, 2, 3}
    assert 50 < dv_on < frames - 50 and shorts > 100


# ---- ruling 1: the return across a gap ----------------------------------------------------------------------------
def test_the_move_across_a_gap_is_booked_on_the_bar_the_price_returns():
    idx = pd.bdate_range("2021-01-04", periods=6)
    px = pd.DataFrame({"A": [np.nan, 100.0, 0.0, -3.0, 120.0, 132.0]}, index=idx)   # bars 2, 3 unpriced
    w = pd.DataFrame(1.0, index=idx, columns=["A"])
    res = engine.run_backtest(px, w, FREE)
    assert res.gross.tolist() == pytest.approx([0.0, 0.0, 0.0, 0.0, 0.2, 0.1], abs=1e-15)
    assert res.weights["A"].tolist() == [0.0, 1.0, 1.0, 1.0, 1.0, 1.0]
    assert res.turnover.tolist() == [0.0, 1.0, 0.0, 0.0, 0.0, 0.0]


# ---- accounting through a gap -------------------------------------------------------------------------------------
@pytest.mark.parametrize("seed", range(20))
def test_one_symbol_at_weight_one_compounds_the_price_through_random_gaps(seed):
    rng = np.random.default_rng(seed)
    n = 120
    idx = pd.bdate_range("2021-01-04", periods=n)
    a = 100.0 * np.exp(np.cumsum(0.03 * rng.standard_normal(n)))
    first = int(rng.integers(1, 10))
    a[:first] = np.nan
    for _ in range(6):
        s = int(rng.integers(first + 1, n))
        a[s:s + int(rng.integers(1, 15))] = rng.choice([np.nan, 0.0, -1.0, np.inf])
    px = pd.DataFrame({"A": a}, index=idx)
    priced = _priced(px)[:, 0]
    res = engine.run_backtest(px, pd.DataFrame(1.0, index=idx, columns=["A"]), FREE)
    eq = res.equity.to_numpy()
    for t in range(n):
        if t < first:
            assert eq[t] == 1.0
        elif priced[t]:
            assert eq[t] == pytest.approx(a[t] / a[first], rel=1e-10)
        else:
            assert eq[t] == eq[t - 1]                                    # flat inside a gap
    assert res.turnover.iloc[first] == 1.0 and res.turnover.drop(idx[first]).sum() == 0.0
    assert res.stranded == ({} if priced[-1] else {"A": 1.0})


def test_two_symbols_one_with_a_gap_worked_by_hand():
    # Targets 0.5 / 0.5, lag 1, no costs. A has no price on bar 3.
    #  bar 1: book goes from cash to 0.5 / 0.5 (turnover 1).
    #  bar 2: A +10%, B 0%: g = 0.05. Drift: A .55/1.05 = 11/21, B .5/1.05 = 10/21; both trade back to 0.5.
    #  bar 3: A unpriced (return 0), B +20%: g = 0.5 * 0.2 = 0.1. A is carried: 0.5 / 1.1 = 5/11 (not traded),
    #         B drifts to 0.6 / 1.1 = 6/11 and trades to 0.5 (turnover 6/11 - 1/2 = 1/22).
    #  bar 4: A back at 132, the move since bar 2's 110 is +20%; B 0%: g = 5/11 * 0.2 = 1/11.
    #         Drift: A (5/11)(1.2)/(12/11) = 0.5, B 0.5/(12/11) = 11/24; trade B only: 1/2 - 11/24 = 1/24.
    #  bar 5: A 0%, B -10%: g = -0.05.
    #  Equity: 1.05 * 1.1 * (12/11) * 0.95 = 1.197.
    idx = pd.bdate_range("2021-01-04", periods=6)
    px = pd.DataFrame({"A": [100.0, 100.0, 110.0, np.nan, 132.0, 132.0],
                       "B": [100.0, 100.0, 100.0, 120.0, 120.0, 108.0]}, index=idx)
    res = engine.run_backtest(px, pd.DataFrame(0.5, index=idx, columns=["A", "B"]), FREE)
    assert res.gross.tolist() == pytest.approx([0.0, 0.0, 0.05, 0.1, 1 / 11, -0.05], abs=1e-15)
    assert res.weights["A"].tolist() == pytest.approx([0.0, 0.5, 0.5, 5 / 11, 0.5, 0.5], abs=1e-15)
    assert res.weights["B"].tolist() == pytest.approx([0.0, 0.5, 0.5, 0.5, 0.5, 0.5], abs=1e-15)
    assert res.turnover.iloc[:5].tolist() == pytest.approx([0.0, 1.0, 2 * (11 / 21 - 0.5), 1 / 22, 1 / 24],
                                                           abs=1e-15)
    assert res.equity.iloc[-1] == pytest.approx(1.197, rel=1e-13)
    assert res.stranded == {}


# ---- costs through a gap ------------------------------------------------------------------------------------------
GAP = [100.0, 101.0, 102.0, 103.0, 104.0, np.nan, -1.0, 0.0, 110.0, 111.0]   # bars 5, 6, 7 unpriced


def _gap_costs(weight_before: float, weight_after: float):
    idx = pd.bdate_range("2021-01-04", periods=len(GAP))
    px = pd.DataFrame({"A": GAP}, index=idx)
    w = pd.DataFrame({"A": [weight_before] * 5 + [weight_after] * 5}, index=idx)  # target changes at bar 6
    dv = pd.DataFrame(1e6, index=idx, columns=["A"])
    cost = CostModel(10.0, 5.0, borrow_annual=0.0252, funding_annual=0.0504)
    return engine.run_backtest(px, w, cost, dollar_volume=dv, vol_window=5)     # sigma at bar 8 sees bars 3, 4


@pytest.mark.parametrize("before,after", [(0.5, 0.3), (-0.4, -0.1)])
def test_a_gap_adds_no_trading_cost_carrying_costs_go_on_and_reentry_trades_the_difference(before, after):
    res = _gap_costs(before, after)
    gap = slice(5, 8)
    for col in ("commission", "slippage", "impact"):
        assert (res.costs[col].iloc[gap] == 0.0).all(), col
    assert (res.turnover.iloc[gap] == 0.0).all()
    assert res.weights["A"].iloc[gap].tolist() == pytest.approx([before] * 3, abs=1e-15)   # carried, not target
    short = max(-before, 0.0)
    for t in (5, 6, 7, 8):                                       # charged on the weight in effect from the bar before
        assert res.costs["funding"].iloc[t] == pytest.approx(abs(before) * 0.0504 / 252, rel=1e-12)
        assert res.costs["borrow"].iloc[t] == pytest.approx(short * 0.0252 / 252, rel=1e-12)
    assert (res.costs["borrow"].iloc[gap] > 0).all() == (before < 0)
    r = 110.0 / 104.0 - 1.0                                      # the move since the last priced bar
    assert res.gross.iloc[8] == pytest.approx(before * r, rel=1e-12)
    drifted = before * (1.0 + r) / (1.0 + before * r)
    assert res.turnover.iloc[8] == pytest.approx(abs(after - drifted), rel=1e-12)
    assert res.costs["commission"].iloc[8] == pytest.approx(abs(after - drifted) * 10e-4, rel=1e-12)
    assert res.costs["slippage"].iloc[8] == pytest.approx(abs(after - drifted) * 5e-4, rel=1e-12)
    assert res.costs["impact"].iloc[8] > 0
    assert res.weights["A"].iloc[8] == after
    np.testing.assert_allclose(res.net, res.gross - res.costs.sum(axis=1), rtol=0, atol=1e-15)


# ---- targets during a gap -----------------------------------------------------------------------------------------
def _ten_bars(prices, targets):
    idx = pd.bdate_range("2021-01-04", periods=len(prices))
    return engine.run_backtest(pd.DataFrame({"A": prices}, index=idx),
                               pd.DataFrame({"A": targets}, index=idx), FREE)


def test_a_target_that_opens_an_unpriced_symbol_opens_nothing_until_it_is_priced():
    p = [100.0, 101.0, 102.0, 103.0, np.nan, np.nan, np.nan, 107.0, 108.0, 109.0]
    res = _ten_bars(p, [0.0] * 4 + [0.5] * 6)                    # target 0.5 from bar 5, inside the gap
    assert res.weights["A"].tolist() == [0.0] * 7 + [0.5] * 3
    assert res.turnover.iloc[:8].tolist() == [0.0] * 7 + [0.5]                # later bars only rebalance drift
    assert (res.gross.iloc[:8] == 0.0).all() and res.gross.iloc[8] == pytest.approx(0.5 * (108 / 107 - 1))
    late = _ten_bars([np.nan] * 3 + p[3:], [0.5] * 10)            # a late listing: the same rule from bar 0
    assert late.weights["A"].tolist() == [0.0] * 3 + [0.5] + [0.5] * 6 and late.turnover.iloc[3] == 0.5


def test_a_zero_target_for_a_held_unpriced_symbol_closes_nothing_until_it_is_priced_then_closes():
    p = [100.0, 101.0, 102.0, 103.0, np.nan, 0.0, np.nan, 107.0, 108.0, 109.0]
    res = _ten_bars(p, [0.5] * 4 + [0.0] * 6)                    # target 0 from bar 5, inside the gap
    assert res.weights["A"].tolist() == pytest.approx([0.0] + [0.5] * 6 + [0.0] * 3, abs=1e-15)
    r = 107.0 / 103.0 - 1.0
    assert res.gross.iloc[7] == pytest.approx(0.5 * r, rel=1e-12)
    assert res.turnover.iloc[7] == pytest.approx(0.5 * (1 + r) / (1 + 0.5 * r), rel=1e-12)
    assert (res.turnover.iloc[4:7] == 0.0).all() and (res.gross.iloc[8:] == 0.0).all()


# ---- ruling 5: stranded positions ---------------------------------------------------------------------------------
def test_a_position_unpriced_on_the_last_bar_is_reported_not_valued():
    p = [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, np.nan, np.nan, -1.0]
    idx = pd.bdate_range("2021-01-04", periods=10)
    px = pd.DataFrame({"A": p, "B": [50.0] * 10, "C": [np.nan] * 10}, index=idx)
    w = pd.DataFrame({"A": [0.5] * 10, "B": [0.25] * 10, "C": [0.25] * 10}, index=idx)
    res = engine.run_backtest(px, w, FREE)
    assert set(res.stranded) == {"A"} and res.stranded["A"] == pytest.approx(res.weights["A"].iloc[-1])
    assert res.stranded["A"] != 0.0 and (res.gross.iloc[7:] == 0.0).all() and (res.turnover.iloc[7:] == 0.0).all()
    assert res.equity.iloc[-1] == pytest.approx(res.equity.iloc[6])            # no invented loss, no exit
    assert engine.run_backtest(px.iloc[:7], w.iloc[:7], FREE).stranded == {}
    flat = w.copy()
    flat["A"] = 0.0
    assert engine.run_backtest(px, flat, FREE).stranded == {}                 # unpriced but nothing held


def test_callers_that_build_the_result_positionally_still_work():
    idx = pd.bdate_range("2021-01-04", periods=3)
    s = pd.Series(0.0, index=idx)
    res = BacktestResult(s, s, pd.DataFrame(index=idx), pd.DataFrame(index=idx), s)
    assert res.stranded == {} and res.equity.tolist() == [1.0, 1.0, 1.0]


# ---- ruling 6: nothing after t reaches bar t ----------------------------------------------------------------------
@pytest.mark.parametrize("seed", range(40))
def test_the_first_k_rows_of_a_run_are_the_run_on_the_first_k_rows(seed):
    rng = np.random.default_rng(1000 + seed)
    px = _gappy_frame(rng)
    w, cost, kw = _inputs(rng, px.index, list(px.columns), *px.shape)
    full = engine.run_backtest(px, w, cost, **kw)
    n = len(px)
    unpriced_last = ~_priced(px)
    gap_rows = [k for k in range(2, n) if unpriced_last[k - 1].any()]
    ks = set(rng.integers(1, n + 1, size=6).tolist()) | set(rng.choice(gap_rows, size=min(3, len(gap_rows))).tolist())
    for k in sorted(ks):
        dv = kw["dollar_volume"]
        part = engine.run_backtest(px.iloc[:k], w.iloc[:k], cost, **{**kw, "dollar_volume": None if dv is None
                                                                    else dv.iloc[:k]})
        _assert_same(part, full, rows=k)
        want = {c: float(part.weights[c].iloc[-1]) for j, c in enumerate(px.columns)
                if unpriced_last[k - 1, j] and part.weights[c].iloc[-1] != 0.0}
        assert part.stranded == want
