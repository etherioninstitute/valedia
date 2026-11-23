"""PhasePacer: requests only inside a quiet part of each wall-clock minute, at most one a second.
All time is simulated; nothing sleeps and nothing touches the network."""
import pytest

from valedia_trader.venues import kalshi_data as kd
from valedia_trader.venues import kalshi_history as kh

MINUTE0 = 1_700_000_040.0 - 1_700_000_040.0 % 60     # a wall time that is exactly second 0 of a minute


class SimTime:
    """One simulated timeline: `clock` (monotonic) and `wall` read it, `sleep` advances it."""
    def __init__(self, second=0.0, overshoots=()):
        self.t = 5000.0
        self.base = MINUTE0 + second - self.t      # wall = t + base, so wall % 60 == `second` now
        self.overshoots, self.sleeps = list(overshoots), []     # extra seconds each sleep runs long, in order

    def clock(self):
        return self.t

    def wall(self):
        return self.t + self.base

    def phase(self):
        return self.wall() % 60.0

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s + (self.overshoots.pop(0) if self.overshoots else 0.0)


def make(second=0.0, window=(42.0, 57.0), spacing=1.0, overshoots=()):
    sim = SimTime(second, overshoots)
    return kd.PhasePacer(spacing, window, sleep=sim.sleep, clock=sim.clock, wall=sim.wall), sim


def test_before_window_waits_until_lo():
    p, sim = make(second=10.0)
    p.wait()
    assert sim.phase() == pytest.approx(42.0, abs=0.01)
    assert sim.phase() >= 42.0


def test_at_hi_waits_to_next_minute_lo():
    p, sim = make(second=57.0)
    start = sim.t
    p.wait()
    assert 42.0 <= sim.phase() < 57.0
    assert sim.t - start == pytest.approx(45.0, abs=0.01)     # 3 s to the minute, 42 more


def test_after_hi_waits_to_next_minute_lo():
    p, sim = make(second=58.5)
    start = sim.t
    p.wait()
    assert 42.0 <= sim.phase() < 57.0
    assert sim.t - start == pytest.approx(43.5, abs=0.01)


def test_inside_window_spacing_alone_applies():
    p, sim = make(second=45.0)
    p.wait()
    assert sim.sleeps == []                                    # first request goes at once
    p.wait()
    assert sim.sleeps == [pytest.approx(1.0)]
    assert sim.phase() == pytest.approx(46.0)


def test_custom_spacing_two_seconds():
    p, sim = make(second=45.0, spacing=2.0)
    p.wait()
    t0 = sim.t
    p.wait()
    assert sim.t - t0 == pytest.approx(2.0)


def test_spacing_past_hi_rolls_to_next_window():
    p, sim = make(second=56.5)
    p.wait()
    assert sim.sleeps == []
    t0 = sim.t
    p.wait()                                                   # spacing alone lands at 57.5: outside
    assert 42.0 <= sim.phase() < 57.0
    assert sim.t - t0 == pytest.approx(45.5, abs=0.01)


@pytest.mark.parametrize("second,overshoots", [(10.0, [20.0]), (10.0, [35.0]), (40.0, [16.0]), (30.0, [29.0, 29.0])])
def test_overshooting_sleep_never_returns_outside_window(second, overshoots):
    # e.g. from second 40 the sleep to 42 runs 16 s long and lands at 58, past hi: it must roll on, not return
    p, sim = make(second=second, overshoots=overshoots)
    p.wait()
    assert 42.0 <= sim.phase() < 57.0
    assert len(sim.sleeps) >= 2                                # the overshoot forced another sleep


def test_overshoot_after_spacing_sleep_rechecks_window():
    p, sim = make(second=56.0, overshoots=[3.0])
    p.wait()                                                   # inside: no sleep
    p.wait()                                                   # spacing sleep overshoots to second 0, then to 42
    assert 42.0 <= sim.phase() < 57.0


def test_ten_minutes_back_to_back():
    p, sim = make(second=0.0)
    starts, end = [], sim.t + 600.0
    while True:
        p.wait()
        if sim.t >= end:
            break
        starts.append((sim.t, sim.phase()))
    assert all(42.0 <= ph < 57.0 for _, ph in starts)
    assert all(b[0] - a[0] >= 1.0 - 1e-9 for a, b in zip(starts, starts[1:]))
    per_minute = len(starts) / 10.0
    assert 14.0 <= per_minute <= 16.0, per_minute              # about 15 a minute


@pytest.mark.parametrize("window", [(-1.0, 10.0), (10.0, 10.0), (20.0, 10.0), (0.0, 61.0), (42.0, 60.5)])
def test_bad_window_raises(window):
    with pytest.raises(ValueError):
        kd.PhasePacer(1.0, window)


@pytest.mark.parametrize("spacing", [0.99, 0.5, 0.0, -1.0])
def test_bad_spacing_raises(spacing):
    with pytest.raises(ValueError):
        kd.PhasePacer(spacing)


def test_full_minute_window_is_allowed():
    kd.PhasePacer(1.0, (0.0, 60.0))


def test_quiet_pacer_factory():
    p = kd.quiet_pacer()
    assert isinstance(p, kd.PhasePacer) and p.spacing == 1.0 and p.window == (42.0, 55.0)
    assert kd.quiet_pacer((10.0, 20.0)).window == (10.0, 20.0)


class CountingPacer:
    def __init__(self):
        self.calls = 0

    def wait(self):
        self.calls += 1


def test_kalshi_data_uses_given_pacer():
    p, urls = CountingPacer(), []
    c = kd.KalshiData(fetch=lambda u: (urls.append(u), b"{}")[1], pacer=p)
    c.get_json("/markets")
    c.get_json("/markets")
    assert p.calls == 2 and len(urls) == 2
    assert c.pacer is p


def test_history_client_uses_given_pacer():
    p, urls = CountingPacer(), []
    c = kh.HistoryClient(fetch=lambda u: (urls.append(u), b"{}")[1], pacer=p)
    c.get_json("/markets")
    c.get_json("/markets")
    assert p.calls == 2 and len(urls) == 2
    assert c.pacer is p


def test_defaults_unchanged():
    d = kd.KalshiData(fetch=lambda u: b"{}")
    assert type(d.pacer) is kd.Pacer
    assert d.pacer.spacing == pytest.approx(1.0 / 3.0 + 0.01)
    h = kh.HistoryClient(fetch=lambda u: b"{}")
    assert type(h.pacer) is kh.kd.Pacer
    assert h.pacer.spacing == pytest.approx(1.0 / 5.0 + 0.001)


def test_nan_spacing_is_refused():
    import math
    from valedia_trader.venues.kalshi_data import PhasePacer
    with pytest.raises(ValueError):
        PhasePacer(spacing=math.nan)


def test_a_quiet_request_always_ends_before_second_58():
    lo, hi = kd.QUIET_WINDOW
    assert hi + kd.QUIET_TIMEOUT <= 58.0


def test_quiet_fetch_uses_the_short_timeout(monkeypatch):
    seen = {}
    monkeypatch.setattr(kd, "urllib_get_dated", lambda url, timeout: (seen.setdefault("t", timeout), None) and (b"{}", None))
    kd.quiet_fetch("https://example.invalid/x")
    assert seen["t"] == kd.QUIET_TIMEOUT
