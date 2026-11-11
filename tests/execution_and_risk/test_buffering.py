import numpy as np
import pandas as pd
import pytest

from valedia_trader import execution_and_risk as er
from valedia_trader.quant_engine.level10_ml_research import detect_lookahead


def setup(n=400, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2021-01-01", periods=n)
    px = pd.DataFrame(100 * np.exp(np.cumsum(0.01 * rng.standard_normal((n, 4)), axis=0)), index=idx, columns=list("ABCD"))
    tgt = pd.DataFrame(0.25 + 0.03 * rng.standard_normal((n, 4)), index=idx, columns=list("ABCD"))
    return px, tgt


def test_buffer_cuts_turnover_and_stays_inside_the_band():
    px, tgt = setup()
    buf = er.apply_buffer(tgt, px, 0.10)
    width = er.buffer_width(tgt, 0.10)
    assert ((buf - tgt).abs().le(width + 1e-12, axis=0)).all().all()
    turn = lambda w: w.diff().abs().sum().sum()
    assert turn(buf) < 0.5 * turn(tgt)
    assert np.allclose(er.apply_buffer(tgt, px, 0.0), tgt)             # zero width tracks the target exactly


def test_zero_targets_are_honoured_immediately_and_buffering_is_causal():
    px, tgt = setup()
    tgt.iloc[200:210] = 0.0                                             # a risk veto
    buf = er.apply_buffer(tgt, px, 0.2)
    assert (buf.iloc[200:210] == 0).all().all() and (buf.iloc[199] != 0).any()
    assert detect_lookahead(lambda df: er.apply_buffer(tgt.loc[df.index], df, 0.2), px, n_checks=5) == []
    with pytest.raises(ValueError):
        er.apply_buffer(tgt, px, 1.0)
    with pytest.raises(ValueError):
        er.apply_buffer(tgt.iloc[:-1], px)


def test_package_exports_the_risk_and_execution_surface():
    assert er.kelly_fraction(0.55, 1.0) == pytest.approx(0.10)
    assert er.sqrt_impact(1_000, 100_000, 0.02) == pytest.approx(0.002)
    assert callable(er.DrawdownCircuitBreaker) and callable(er.almgren_chriss)
