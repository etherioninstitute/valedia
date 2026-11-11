import numpy as np
import pandas as pd
import pytest

from valedia_trader.data_and_fees import timesfm_forecaster as tf
from valedia_trader.quant_engine.level10_ml_research import detect_lookahead


@pytest.fixture(scope="module")
def prices():
    rng = np.random.default_rng(7)
    idx = pd.bdate_range("2018-01-01", periods=700)
    drift = np.array([0.0006, -0.0004, 0.0002, 0.0, 0.0009, -0.0007])
    r = drift + 0.012 * rng.standard_normal((700, 6))
    df = pd.DataFrame(100.0 * np.exp(np.cumsum(r, axis=0)), index=idx, columns=list("ABCDEF"))
    df.iloc[:300, 5] = np.nan                      # an asset that lists late
    return df


class Recording:
    """Backend that records what it was shown and answers with a flat forecast."""
    name = "recording"

    def __init__(self):
        self.seen: list[np.ndarray] = []

    def forecast(self, contexts, horizon):
        self.seen += [np.array(c) for c in contexts]
        last = np.array([c[-1] for c in contexts])
        med = np.repeat(last[:, None], horizon, axis=1)
        return tf.QuantileForecast(med, med[:, :, None] + 0.02 * tf.ndtri(tf.QUANTILE_LEVELS)[None, None, :])


def test_quantile_forecast_probabilities_and_sigma():
    z = tf.ndtri(tf.QUANTILE_LEVELS)
    f = tf.QuantileForecast(np.array([[1.0]]), (1.0 + 0.1 * z)[None, None, :])
    assert f.sigma()[0] == pytest.approx(0.1)
    assert f.prob_above(1.0)[0] == pytest.approx(0.5)
    assert f.prob_above(1.0 + 0.1 * z[-1])[0] == pytest.approx(0.1)            # at the 90% decile
    assert f.prob_above(1.0 + 0.1 * z[0])[0] == pytest.approx(0.9)             # at the 10% decile
    assert f.prob_above(1.2)[0] == pytest.approx(0.0227501, abs=1e-6)          # 2 sigma: normal tail
    assert f.prob_above(0.8)[0] == pytest.approx(1 - 0.0227501, abs=1e-6)
    grid = f.prob_above(np.linspace(0.6, 1.4, 81).reshape(-1, 1)[:, 0][:1])    # shape check on a scalar-like input
    assert grid.shape == (1,)
    with pytest.raises(ValueError):
        tf.QuantileForecast(np.zeros((2, 3)), np.zeros((2, 3, 5)))


def test_prob_above_is_monotone_and_handles_degenerate_forecasts():
    z = tf.ndtri(tf.QUANTILE_LEVELS)
    q = (0.05 * z)[None, None, :]
    xs = np.linspace(-0.3, 0.3, 121)
    p = np.array([tf.QuantileForecast(np.zeros((1, 1)), q).prob_above(x)[0] for x in xs])
    assert np.all(np.diff(p) <= 1e-12) and 0.0 <= p.min() and p.max() <= 1.0
    flat = tf.QuantileForecast(np.zeros((1, 1)), np.zeros((1, 1, 9)))
    assert flat.sigma()[0] == 0.0 and flat.prob_above(-1.0)[0] == 1.0 and flat.prob_above(1.0)[0] == 0.0
    assert flat.prob_above(0.0)[0] == 0.5


def test_naive_backend_matches_a_drift_random_walk():
    rng = np.random.default_rng(1)
    x = np.cumsum(0.001 + 0.01 * rng.standard_normal(400))
    f = tf.NaiveDriftBackend(window=250).forecast([x], 5)
    d = np.diff(x[-251:])
    assert f.median[0, -1] == pytest.approx(x[-1] + 5 * d.mean())
    assert f.sigma()[0] == pytest.approx(d.std(ddof=1) * np.sqrt(5))
    assert np.all(np.diff(f.quantiles[0], axis=1) > 0)


def test_walk_forward_never_shows_the_backend_a_future_price(prices):
    rec = Recording()
    sig = tf.walk_forward_signals(prices, rec, horizon=5, step=5, context=200, min_context=64)
    logp = np.log(prices)
    assert rec.seen and all(len(c) <= 200 for c in rec.seen)
    # every context must be the tail of some asset's history ending on a rebalance row
    ends = {(round(float(logp.iloc[t, j]), 12)) for t in range(0, len(prices), 5) for j in range(prices.shape[1])
            if np.isfinite(logp.iloc[t, j])}
    assert all(round(float(c[-1]), 12) in ends for c in rec.seen)
    assert set(sig) == set(tf.SIGNAL_FIELDS)
    assert sig["score"].shape == prices.shape
    assert sig["score"].iloc[:60].isna().all().all()            # not enough history yet
    assert sig["score"]["F"].iloc[:360].isna().all()            # late listing: no signal before 64 prices exist
    assert sig["score"]["F"].iloc[400:].notna().all()


def test_signals_are_held_between_rebalances_and_causal(prices):
    backend = tf.NaiveDriftBackend()
    sig = tf.walk_forward_signals(prices, backend, horizon=5, step=5, context=250, min_context=64)
    er = sig["expected_return"]
    live = list("ABCDE")                                           # F has not listed yet at row 200
    assert (er[live].iloc[200:205].nunique() == 1).all()         # rows 200..204 share the forecast made at 200
    assert not er[live].iloc[200].equals(er[live].iloc[205]) and er["F"].iloc[200:205].isna().all()

    def sleeve(df):
        return tf.forecast_sleeve(df, tf.walk_forward_signals(df, backend, horizon=5, step=5, context=250, min_context=64))

    assert detect_lookahead(sleeve, prices, n_checks=6) == []
    w = sleeve(prices)
    gross = w.abs().sum(axis=1)
    assert gross.max() <= 1.0 + 1e-9 and gross.iloc[300:].min() == pytest.approx(1.0)
    assert np.sign(w["E"].iloc[300:]).mean() > 0.5 and np.sign(w["B"].iloc[300:]).mean() < 0.5     # follows the drift


def test_price_signals_equals_the_last_walk_forward_row(prices):
    backend = tf.NaiveDriftBackend()
    last = tf.price_signals(prices.iloc[:501], backend, horizon=5, context=250)
    panel = tf.walk_forward_signals(prices.iloc[:501], backend, horizon=5, step=5, context=250)
    for f in tf.SIGNAL_FIELDS:
        np.testing.assert_allclose(last[f].to_numpy(), panel[f].iloc[500].to_numpy(), rtol=1e-12)
    assert list(last.columns) == list(tf.SIGNAL_FIELDS) and list(last.index) == list(prices.columns)


def test_cache_round_trip_and_key_changes_with_prices(prices, tmp_path):
    rec = Recording()
    a = tf.walk_forward_signals(prices, rec, 5, 5, 200, 64, cache_dir=tmp_path)
    calls = len(rec.seen)
    b = tf.walk_forward_signals(prices, rec, 5, 5, 200, 64, cache_dir=tmp_path)
    assert len(rec.seen) == calls                                  # served from the cache
    for f in tf.SIGNAL_FIELDS:
        pd.testing.assert_frame_equal(a[f], b[f], check_freq=False, check_names=False)
    bumped = prices.copy()
    bumped.iloc[-1, 0] *= 1.01
    tf.walk_forward_signals(bumped, rec, 5, 5, 200, 64, cache_dir=tmp_path)
    assert len(rec.seen) > calls and len(list(tmp_path.glob("signals-*.parquet"))) == 2


def test_kelly_weights_use_fractional_kelly_and_the_leverage_cap():
    row = pd.DataFrame({"expected_return": [0.01, -0.02, 0.0, 0.01], "sigma": [0.02, 0.04, 0.03, 0.0],
                        "prob_up": [0.7, 0.3, 0.5, 0.5], "score": [0.5, -0.5, 0.0, 0.0]}, index=list("ABCD"))
    w = tf.kelly_weights(row, multiplier=0.25, max_gross=10.0)
    assert w["A"] == pytest.approx(0.25 * np.expm1(0.01) / 0.02 ** 2)
    assert w["B"] < 0 and w["C"] == 0.0 and w["D"] == 0.0           # zero variance means no trade
    capped = tf.kelly_weights(row, multiplier=0.25, max_gross=1.0)
    assert capped.abs().sum() == pytest.approx(1.0) and np.sign(capped["A"]) == 1


def test_evaluate_signals_scores_a_real_edge_and_a_null(prices):
    logp = np.log(prices)
    fwd = logp.shift(-5) - logp
    oracle = {"score": fwd, "expected_return": fwd, "sigma": fwd.abs() * 0 + 0.03, "prob_up": (fwd > 0).astype(float).where(fwd.notna())}
    rng = np.random.default_rng(3)
    noise = pd.DataFrame(rng.standard_normal(prices.shape), index=prices.index, columns=prices.columns).where(prices.notna())
    null = {"score": noise, "expected_return": noise * 0.01, "sigma": noise * 0 + 0.03, "prob_up": noise * 0 + 0.5}
    win = {"all": (None, None), "late": ("2019-06-01", None)}
    good, bad = tf.evaluate_signals(prices, oracle, 5, 5, win), tf.evaluate_signals(prices, null, 5, 5, win)
    assert good["all"]["ic_mean"] == pytest.approx(1.0) and good["all"]["hit_rate"] == 1.0 and good["all"]["brier_prob_up"] == 0.0
    assert abs(bad["all"]["ic_mean"]) < 0.1 and abs(bad["all"]["ic_tstat"]) < 3 and bad["all"]["brier_prob_up"] == pytest.approx(0.25)
    assert 0 < good["late"]["dates"] < good["all"]["dates"]


def test_backend_refuses_a_checkpoint_that_is_not_the_pinned_one(tmp_path):
    (tmp_path / "model.safetensors").write_bytes(b"not the model")
    fake = tf.Checkpoint("x", "x/y", "rev", tmp_path, "model.safetensors", 925181104, "0" * 64, "apache-2.0", "2025-09-02")
    with pytest.raises(tf.CheckpointError):
        tf.TimesFMBackend(fake).forecast([np.arange(200.0)], 5)
    with pytest.raises(tf.CheckpointError):
        tf.TimesFMBackend(tf.Checkpoint("x", "x/y", "rev", tmp_path / "nope", "model.safetensors", 1, "0" * 64, "a", "d")).verify()
    with pytest.raises(ValueError):
        tf.TimesFMBackend().forecast([np.arange(200.0)], 0)


def test_a_worker_that_dies_is_reported_not_waited_on(tmp_path, monkeypatch):
    (tmp_path / "model.safetensors").write_bytes(b"x" * 16)
    fake = tf.Checkpoint("x", "x/y", "rev", tmp_path, "model.safetensors", 16, "0" * 64, "apache-2.0", "2025-09-02")
    crash = tmp_path / "crash.py"
    crash.write_text("import sys; sys.stderr.write('boom: bad weights'); sys.exit(3)")
    monkeypatch.setattr(tf, "WORKER", crash)
    backend = tf.TimesFMBackend(fake)
    with pytest.raises(tf.CheckpointError, match="code 3.*boom"):
        backend.forecast([np.arange(200.0)], 5)
    assert backend._proc is None and backend._dir is None


@pytest.mark.skipif(not (tf.CHECKPOINT.path / tf.CHECKPOINT.weights).is_file(), reason="TimesFM weights are not on this machine")
def test_real_model_forecast_shapes_and_sanity():
    pytest.importorskip("timesfm")
    rng = np.random.default_rng(0)
    x = np.log(100) + np.cumsum(0.0005 + 0.01 * rng.standard_normal(600))
    import xgboost  # noqa: F401  the second OpenMP runtime that used to crash an in-process torch
    backend = tf.TimesFMBackend()
    contexts = [x, x[:300]]
    f = backend.forecast(contexts, 5)
    again = backend.forecast(contexts, 5)                          # the worker stays up between calls
    backend.close()
    np.testing.assert_allclose(f.median, again.median)
    assert len(contexts) == 2 and backend._proc is None
    assert f.median.shape == (2, 5) and f.quantiles.shape == (2, 5, 9)
    assert np.all(np.diff(f.quantiles, axis=2) >= 0)
    assert abs(f.median[0, -1] - x[-1]) < 0.1 and 0.005 < f.sigma()[0] < 0.1     # a week ahead, about 1% daily vol
    assert 0.2 < f.prob_above(x[-1])[0] < 0.8
