import numpy as np
import pandas as pd
import pytest

from valedia_trader.agents import specialists as sp
from valedia_trader.quant_engine.level10_ml_research import detect_lookahead


@pytest.fixture(scope="module")
def prices():
    rng = np.random.default_rng(0)
    idx = pd.bdate_range("2018-01-01", periods=500)
    px = pd.DataFrame(100 * np.exp(np.cumsum(0.012 * rng.standard_normal((500, 6)), axis=0)), index=idx,
                      columns=list("ABCDEF"))
    px.loc[px.index[:150], "F"] = np.nan                                # an asset that lists later
    return px


@pytest.mark.parametrize("name", list(sp.SLEEVES))
def test_every_sleeve_is_causal_and_bounded(prices, name):
    assert detect_lookahead(lambda df: sp.sleeve_weights(name, df), prices, n_checks=6) == []
    w = sp.sleeve_weights(name, prices)
    assert w.shape == prices.shape and np.isfinite(w.to_numpy()).all()
    assert w.abs().sum(axis=1).max() <= 1.0 + 1e-9
    assert (w["F"].iloc[:150] == 0).all()                               # nothing held before listing


def test_sleeve_shapes(prices):
    rp = sp.risk_parity(prices)
    assert (rp >= 0).all().all() and rp.iloc[-1].sum() == pytest.approx(1.0)
    xs = sp.cross_sectional_momentum(prices)
    assert xs.iloc[-1].sum() == pytest.approx(0.0, abs=1e-12) and xs.iloc[-1].abs().sum() == pytest.approx(1.0)
    tf = sp.trend_following(prices)
    past = prices.shift(5).iloc[-1] / prices.shift(126).iloc[-1] - 1
    assert (np.sign(tf.iloc[-1]) == np.sign(past)).all()
    rev = sp.short_term_reversal(prices, entry_z=0.0)
    move = prices.iloc[-1] / prices.shift(5).iloc[-1] - 1
    assert (np.sign(rev.iloc[-1]) == -np.sign(move)).all()
    with pytest.raises(KeyError):
        sp.sleeve_weights("astrology", prices)
