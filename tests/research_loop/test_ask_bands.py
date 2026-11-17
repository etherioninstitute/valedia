"""Tests of the locked "ask bands" program (docs/research/2026-11-17-ask-bands-prediction.md).

Synthetic universes only, generated with a seed. Nothing here touches the real stores.
"""
import hashlib
import json
import math
import random
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from valedia_trader.research_loop import ask_bands as ab
from valedia_trader.venues import kalshi_history as kh

UTC = timezone.utc
H = 3600


# ---------------------------------------------------------------- synthetic universes
@dataclass
class Spec:
    event: str
    ticker: str
    close: datetime            # W: the market's close; S: the game moment (its last stored candle ends here)
    result: object
    bid: object                # cents (int) or a raw stored value (float / None / str / ...)
    ask: object
    bid12: object = None
    ask12: object = None


def dollars(v):
    return v / 100 if isinstance(v, int) and not isinstance(v, bool) else v


def blk(v):
    return {"open": v, "high": v, "low": v, "close": v}


def w_candle(end_ts, bid, ask):
    return {"end_ts": end_ts, "volume": 10.0, "open_interest": 100.0, "price": blk(None),
            "yes_bid": blk(dollars(bid)), "yes_ask": blk(dollars(ask))}


def iso(dt):
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_w(root, specs, *, corrupt=(), candles_override=None, skip_files=()):
    """markets.json and one candle file a market. Decoy candles sit before and after the one the program must use."""
    base = Path(root) / "artifacts" / "kalshi_weather"
    (base / "candles").mkdir(parents=True, exist_ok=True)
    markets = []
    for s in specs:
        cts = int(s.close.timestamp())
        markets.append({"ticker": s.ticker, "event_ticker": s.event, "title": "t", "subtitle": "", "yes_sub_title": "",
                        "strike_type": "greater", "floor_strike": 50, "cap_strike": None,
                        "open_time": iso(s.close - timedelta(days=2)), "close_time": iso(s.close),
                        "settlement_ts": iso(s.close), "status": "finalized", "result": s.result,
                        "expiration_value": "x", "volume": 1.0, "tier": "historical"})
        if s.ticker in skip_files:
            continue
        b12, a12 = (s.bid, s.ask) if s.bid12 is None else (s.bid12, s.ask12)
        candles = [w_candle(cts - 48 * H, 1, 2), w_candle(cts - 24 * H, s.bid, s.ask),
                   w_candle(cts - 23 * H, 98, 99), w_candle(cts - 12 * H, b12, a12), w_candle(cts - 1 * H, 97, 98)]
        if candles_override and s.ticker in candles_override:
            candles = candles_override[s.ticker]
        path = base / "candles" / ("%s.json" % s.ticker)
        if s.ticker in corrupt:
            path.write_text("{{ this is not json")
        else:
            path.write_text(json.dumps({"ticker": s.ticker, "tier_hint": "historical", "start": "a", "end": "b",
                                        "candles": candles}))
    (base / "markets.json").write_text(json.dumps({"series": "KXHIGHNY", "fetched_at": "x", "markets": markets}))


def s_blk(v):
    d = None if v is None else "%.4f" % (v / 100)
    return {"open_dollars": d, "high_dollars": d, "low_dollars": d, "close_dollars": d}


def s_candle(end_ts, bid, ask):
    return {"end_period_ts": end_ts, "volume_fp": "5.00", "open_interest_fp": "9.00", "price": {"close_dollars": None},
            "yes_bid": s_blk(bid), "yes_ask": s_blk(ask)}


def write_s(root, specs, *, corrupt_candles=()):
    db = Path(root) / "artifacts" / "sports_lines" / "kalshi.sqlite"
    store = kh.HistoryStore(db)
    mrows, crows = [], []
    for s in specs:
        raw = {"ticker": s.ticker, "event_ticker": s.event, "result": s.result, "status": "finalized",
               "close_time": "2026-03-01T00:00:00Z"}
        mrows.append((s.ticker, "KXNBAGAME", s.event, "historical", json.dumps(raw), "x"))
        cts = int(s.close.timestamp())
        for k, (b, a) in ((-120, (1, 2)), (-60, (3, 4)), (0, (s.bid, s.ask))):
            raw_c = s_candle(cts + k, b, a)
            if s.ticker in corrupt_candles:
                crows.append((s.ticker, cts + k, "historical", "{{ not json", "x"))
            else:
                crows.append((s.ticker, cts + k, "historical", json.dumps(raw_c), "x"))
    store.db.executemany("INSERT INTO markets VALUES (?, ?, ?, ?, ?, ?)", mrows)
    store.db.executemany("INSERT INTO candles VALUES (?, ?, ?, ?, ?)", crows)
    store.db.commit()
    store.close()


W_DEV_DAY = date(2022, 1, 1)
W_SEALED_DAY = date(2025, 3, 1)
S_DEV_DAY = date(2026, 1, 5)       # 17 days to 2026-01-21
S_SEALED_DAY = date(2026, 1, 22)   # 12 days to 2026-02-02


def gen(seed, n_events, k, *, sealed, universe="W", kind="calibrated", planted=(25, 45), edge=6, mlo=20, mhi=55,
        tag=None):
    """k independent markets an event. Quote: bid = m - 1, ask = m + 1 cents. A yes buyer's true win probability is
    m cents (calibrated) or, inside the planted band of the yes ask, the ask plus `edge` cents."""
    rng = random.Random(seed)
    tag = tag or ("S" if sealed else "D")
    out = []
    for i in range(n_events):
        if universe == "W":
            day = (W_SEALED_DAY + timedelta(days=i % 300)) if sealed else (W_DEV_DAY + timedelta(days=i % 700))
            when = datetime(day.year, day.month, day.day, 5, 0, tzinfo=UTC)
        else:
            day = (S_SEALED_DAY + timedelta(days=i % 12)) if sealed else (S_DEV_DAY + timedelta(days=i % 17))
            when = datetime(day.year, day.month, day.day, 18, 0, tzinfo=UTC)
        ev = "%s%s%05d" % ("KXHIGHNY-" if universe == "W" else "KXNBAGAME-", tag, i)
        for j in range(k):
            m = rng.randint(mlo, mhi)
            ask = m + 1
            p = m / 100
            if kind == "planted" and planted[0] <= ask < planted[1]:
                p = (ask + edge) / 100
            out.append(Spec(ev, "%s-M%d" % (ev, j), when, "yes" if rng.random() < p else "no", m - 1, ask))
    return out


def gen_mirrored(seed, n_events, *, sealed=False, edge=8):
    """Two markets an event, the second the complement of the first: A's no side and B's yes side are one trade."""
    rng = random.Random(seed)
    out = []
    for i in range(n_events):
        day = W_DEV_DAY + timedelta(days=i % 700)
        when = datetime(day.year, day.month, day.day, 5, 0, tzinfo=UTC)
        ev = "KXHIGHNY-X%05d" % i
        m = rng.randint(30, 39)
        yes_a = rng.random() < (m + 1 + edge) / 100
        out.append(Spec(ev, ev + "-A", when, "yes" if yes_a else "no", m - 1, m + 1))
        out.append(Spec(ev, ev + "-B", when, "no" if yes_a else "yes", 99 - m, 101 - m))
    return out


def canon(obj):
    return json.dumps(obj, sort_keys=True)


# ---------------------------------------------------------------- the pure pieces
def test_constants_of_the_prediction():
    assert ab.PREDICTION_COMMIT == "1431132"
    assert ab.SEED == 20261117 and ab.RESAMPLES == 2000
    assert ab.COST_CENTS == 2
    assert len(ab.valid_bands()) == 153
    assert len(ab.CELLS) == 18 and ab.CELLS[0] == (5, 9) and ab.CELLS[-1] == (90, 95)


@pytest.mark.parametrize("cents,cell", [(4, None), (5, 0), (9, 0), (10, 1), (94, 17), (95, 17), (96, None), (0, None),
                                        (100, None), (50, 9), (89, 16), (90, 17)])
def test_cell_boundaries(cents, cell):
    assert ab.cell_of(cents) == cell


def test_band_validation_accepts_exactly_the_153():
    ok = [(lo, hi) for lo in range(0, 101) for hi in range(0, 101) if ab.is_valid_band(lo, hi)]
    assert len(ok) == 153 and (5, 15) in ok and (85, 95) in ok and (5, 95) in ok
    for bad in [(5, 10), (10, 5), (7, 20), (5, 100), (0, 10), (50, 50), (5, 96), (90, 95), (95, 95)]:
        assert not ab.is_valid_band(*bad)


def test_fee_tables():
    # 0.07 x P x (1 - P) dollars, rounded up to the cent: 1.75 cents at the middle -> 2
    assert [ab.taker_fee_cents(p) for p in (5, 10, 50, 90, 95)] == [1, 1, 2, 1, 1]
    assert max(ab.taker_fee_cents(p) for p in range(1, 100)) == 2


def test_trade_result_in_whole_cents():
    assert ab.trade_result(57, True, 2) == 100 - 57 - 2
    assert ab.trade_result(57, False, 2) == -57 - 2
    assert isinstance(ab.trade_result(57, True, 2), int)


@pytest.mark.parametrize("v,status", [(0.57, "ok"), (57, "ok"), (None, "missing"), (float("nan"), "invalid"),
                                      (float("inf"), "invalid"), (-0.2, "invalid"), (True, "invalid"),
                                      ("0.57", "invalid"), ([0.5], "invalid"), (0.5705, "not_whole_cent"),
                                      (0.570002, "not_whole_cent"), (0.5700004, "ok")])
def test_to_cents_statuses(v, status):
    assert ab.to_cents(v)[0] == status


def test_to_cents_values_are_integers():
    st, c = ab.to_cents(0.29)          # 0.29 * 100 = 28.999999999999996 in floating point
    assert st == "ok" and c == 29 and type(c) is int


def test_side_prices():
    assert ab.side_price("yes", 0.55, 0.57) == (57, None)
    assert ab.side_price("no", 0.55, 0.57) == (45, None)
    assert ab.side_price("yes", 0.0, 0.57) == (57, None)         # no bid at all is not a crossed book
    # outside the cells
    assert ab.side_price("yes", 0.03, 0.04) == (None, "outside_5_95")
    assert ab.side_price("yes", 0.04, 0.05) == (5, None)
    assert ab.side_price("yes", 0.94, 0.95) == (95, None)
    assert ab.side_price("yes", 0.95, 0.96) == (None, "outside_5_95")
    assert ab.side_price("no", 0.04, 0.05) == (None, "outside_5_95")           # no side costs 96
    assert ab.side_price("no", 0.95, 0.96) == (5, None)
    # the ask must be above 0 and below 1
    assert ab.side_price("yes", 0.0, 0.0) == (None, "price_out_of_range")
    assert ab.side_price("yes", 0.5, 1.0) == (None, "price_out_of_range")
    assert ab.side_price("no", 0.0, 0.5) == (None, "price_out_of_range")      # no ask = 1 - 0 = 1
    assert ab.side_price("no", 1.0, 1.0) == (None, "price_out_of_range")      # no ask = 0
    # crossed and locked: both sides are left out
    assert ab.side_price("yes", 0.58, 0.57) == (None, "crossed_or_locked")
    assert ab.side_price("no", 0.58, 0.57) == (None, "crossed_or_locked")
    assert ab.side_price("yes", 0.57, 0.57) == (None, "crossed_or_locked")
    assert ab.side_price("no", 0.57, 0.57) == (None, "crossed_or_locked")


@pytest.mark.parametrize("bad,reason", [(None, "price_missing"), (float("nan"), "price_invalid"),
                                        (-0.5, "price_invalid"), (True, "price_invalid"), (False, "price_invalid"),
                                        ("0.57", "price_invalid"), (0.5705, "price_not_whole_cent"),
                                        (0.0, "price_out_of_range")])
def test_bad_prices_are_skips_never_zeros(bad, reason):
    assert ab.side_price("yes", 0.50, bad) == (None, reason)
    assert ab.side_price("no", bad, 0.60) == (None, reason)


def test_the_other_quote_must_be_usable_too():
    for bad in (None, float("nan"), True, "0.5", -1.0, 0.5005):
        assert ab.side_price("yes", bad, 0.57) == (None, "other_quote_unusable")
        assert ab.side_price("no", 0.50, bad) == (None, "other_quote_unusable")


def test_choose_band_thresholds_and_ties():
    # stats: (trades, events, total cents)
    stats = {(5, 15): (300, 150, 900), (10, 20): (299, 200, 9000), (15, 25): (400, 149, 9000),
             (20, 30): (500, 200, 1000)}
    assert ab.choose_band(stats) == (5, 15)        # means 3.0 / 2.0: the others fail the minimums or trail
    stats = {(5, 15): (300, 150, 900), (5, 25): (600, 300, 1800), (10, 20): (300, 150, 900)}
    assert ab.choose_band(stats) == (5, 25)        # equal means: the wider band
    stats = {(10, 20): (300, 150, 900), (5, 15): (300, 150, 900)}
    assert ab.choose_band(stats) == (5, 15)        # equal means, equal width: the lower start
    assert ab.choose_band({(5, 15): (299, 150, 900), (10, 20): (300, 149, 900)}) is None
    # exact comparison of means, not floats: (10, 20) is exactly 1/3, (5, 15) is a hair below it (equal as floats)
    stats = {(5, 15): (3 * 10 ** 20, 150, 10 ** 20 - 1), (10, 20): (300, 150, 100)}
    assert ab.choose_band(stats) == (10, 20)


def test_p_value_extremes_and_determinism():
    assert ab.event_p([5] * 50)[0] == 0                  # every resample is above zero
    assert ab.event_p([-5] * 50) == (2000, 1.0)          # none is
    assert ab.event_p([0] * 50)[0] == 2000               # not above zero counts
    mixed = [3, -3, 3, -3, 1, -1, 2, -3] * 20           # mean a little below zero
    a, b = ab.event_p(mixed), ab.event_p(mixed)
    assert a == b and 0 < a[0] < 2000 and a[1] == a[0] / 2000


# ---------------------------------------------------------------- fixtures: universes on disk
@pytest.fixture(scope="module")
def planted_w(tmp_path_factory):
    root = tmp_path_factory.mktemp("planted_w")
    write_w(root, gen(11, 1500, 3, sealed=False, kind="planted", tag="D") +
            gen(12, 1500, 3, sealed=True, kind="planted", tag="S"))
    return root


@pytest.fixture(scope="module")
def calibrated_w(tmp_path_factory):
    root = tmp_path_factory.mktemp("calibrated_w")
    write_w(root, gen(21, 1500, 3, sealed=False, tag="D") + gen(22, 600, 3, sealed=True, tag="S"))
    return root


@pytest.fixture(scope="module")
def dev_only_w(tmp_path_factory):
    root = tmp_path_factory.mktemp("dev_only_w")
    write_w(root, gen(31, 1500, 3, sealed=False, kind="planted", tag="D") +
            gen(32, 1500, 3, sealed=True, kind="calibrated", tag="S"))
    return root


@pytest.fixture(scope="module")
def planted_s(tmp_path_factory):
    root = tmp_path_factory.mktemp("planted_s")
    write_s(root, gen(41, 1500, 2, sealed=False, universe="S", kind="planted", tag="D") +
            gen(42, 4000, 2, sealed=True, universe="S", kind="planted", tag="S"))     # power: the sealed period is short
    return root


def develop(root, u="W", **kw):
    return ab.run_develop(u, root=root, **kw)


def dev_state(root, u="W"):
    d = Path(root) / "artifacts" / "ask_bands"
    return d / ("develop-%s.json" % u), d / ("develop-%s.json.sha256" % u), d / ("sealed-%s.used" % u)


@pytest.fixture
def clean_marker(request):
    """Remove a sealed marker and the sealed output a test made on a module-scoped root."""
    yield
    for name in ("planted_w", "dev_only_w", "planted_s"):
        if name in request.fixturenames:
            root = request.getfixturevalue(name)
            for p in (Path(root) / "artifacts" / "ask_bands").glob("sealed-*"):
                p.unlink()


# ---------------------------------------------------------------- the three decision outcomes
def test_calibrated_universe_fails_at_step_1(calibrated_w):
    out = develop(calibrated_w)
    r = out["result"]
    assert r["step_1"]["verdict"] == "FAIL"
    assert r["step_1"]["mean_cents"] <= 0 or r["step_1"]["p_value"] > 0.025
    # the whole table loses about the spread plus the cost
    assert sum(c["total_result_cents"] for c in r["cell_table"]) < 0


def test_planted_underpricing_develop_chooses_band_inside_and_sealed_passes(planted_w, clean_marker):
    out = develop(planted_w)
    s1 = out["result"]["step_1"]
    assert s1["verdict"] == "PROCEED"
    lo, hi = s1["band"]
    assert 25 <= lo and hi <= 45 and s1["trades"] >= 300 and s1["events"] >= 150
    assert s1["mean_cents"] > 0 and s1["p_value"] <= 0.025
    sealed = ab.run_sealed("W", (lo, hi), flag=True, root=planted_w)
    assert sealed["result"]["verdict"]["verdict"] == "PASS"
    assert sealed["result"]["stats"]["mean_cents"] > 0 and sealed["result"]["stats"]["p_value"] <= 0.025


def test_edge_only_in_development_fails_the_sealed_period(dev_only_w, clean_marker):
    out = develop(dev_only_w)
    s1 = out["result"]["step_1"]
    assert s1["verdict"] == "PROCEED"
    sealed = ab.run_sealed("W", tuple(s1["band"]), flag=True, root=dev_only_w)
    v = sealed["result"]["verdict"]
    assert v["verdict"] == "FAIL" and v["failed"]


def test_planted_underpricing_in_sports_universe(planted_s, clean_marker):
    out = develop(planted_s, "S")
    s1 = out["result"]["step_1"]
    assert s1["verdict"] == "PROCEED" and 25 <= s1["band"][0] and s1["band"][1] <= 45
    sealed = ab.run_sealed("S", tuple(s1["band"]), flag=True, root=planted_s)
    assert sealed["result"]["verdict"]["verdict"] == "PASS"


def test_mirrored_markets_event_resampling_gives_larger_p_than_trade_resampling(tmp_path):
    write_w(tmp_path, gen_mirrored(51, 200))
    col = ab.collect("W", "development", root=tmp_path)
    trades = col.labels["24h"].trades                    # (event, paid_cents, win)
    in_band = [t for t in trades if 31 <= t[1] <= 40]
    assert len(in_band) == 400                            # each event: A yes and B no, one trade twice
    res = [ab.trade_result(t[1], t[2], 2) for t in in_band]
    assert sum(res) > 0
    per_event = {}
    for t, r in zip(in_band, res):
        per_event[t[0]] = per_event.get(t[0], 0) + r
    p_event = ab.event_p(list(per_event.values()))[1]
    rng = np.random.default_rng(20261117)               # the trade resampling the program must NOT use
    arr = np.array(res)
    idx = rng.integers(0, len(arr), size=(2000, len(arr)))
    p_trade = float(np.mean(arr[idx].sum(axis=1) <= 0))
    assert p_event > p_trade
    stats = ab.band_stats(in_band, 2)
    assert stats["events"] == 200 and stats["trades"] == 400


# ---------------------------------------------------------------- skip rules, counted by reason
def one_market(ev="KXHIGHNY-Z1", result="yes", bid=50, ask=52, close=None, **kw):
    close = close or datetime(2023, 6, 1, 5, 0, tzinfo=UTC)
    return Spec(ev, ev + "-M", close, result, bid, ask, **kw)


def run_w(tmp_path, specs, period="development", **kw):
    write_w(tmp_path, specs, **kw)
    return ab.collect("W", period, root=tmp_path)


def test_a_clean_market_gives_two_sides(tmp_path):
    col = run_w(tmp_path, [one_market()])
    lab = col.labels["24h"]
    assert lab.sides_considered == 2 and lab.side_skips == {} and lab.market_skips == {}
    # yes bought at 52 (wins), no bought at 100 - 50 = 50 (loses)
    assert sorted((t[1], t[2]) for t in lab.trades) == [(50, False), (52, True)]
    assert col.markets_in_period == 1 and col.events_in_period == 1


def test_no_candle_24_hours_before_close_is_left_out_and_counted(tmp_path):
    close = datetime(2023, 6, 1, 5, 0, tzinfo=UTC)
    only_late = [w_candle(int(close.timestamp()) - 23 * H, 50, 52)]            # ends 23 hours before: too late
    col = run_w(tmp_path, [one_market(close=close)], candles_override={"KXHIGHNY-Z1-M": only_late})
    lab = col.labels["24h"]
    assert lab.market_skips == {"no_candle": 1} and lab.trades == [] and lab.sides_considered == 0
    # and a market whose candle file does not exist at all
    col2 = run_w(tmp_path / "b", [one_market()], skip_files={"KXHIGHNY-Z1-M"})
    assert col2.labels["24h"].market_skips == {"no_candle": 1}


def test_the_candle_ending_exactly_24_hours_before_close_is_used_and_later_ones_are_not(tmp_path):
    close = datetime(2023, 6, 1, 5, 0, tzinfo=UTC)
    cts = int(close.timestamp())
    cs = [w_candle(cts - 24 * H, 50, 52), w_candle(cts - 24 * H + 1, 80, 82), w_candle(cts - 25 * H, 10, 12)]
    col = run_w(tmp_path, [one_market(close=close)], candles_override={"KXHIGHNY-Z1-M": cs})
    assert sorted(t[1] for t in col.labels["24h"].trades) == [50, 52]


def test_a_candle_list_out_of_order_gives_the_same_trades(tmp_path):
    close = datetime(2023, 6, 1, 5, 0, tzinfo=UTC)
    cts = int(close.timestamp())
    cs = [w_candle(cts - 23 * H, 98, 99), w_candle(cts - 24 * H, 50, 52), w_candle(cts - 48 * H, 1, 2),
          w_candle(cts - 1 * H, 97, 98)]
    col = run_w(tmp_path, [one_market(close=close)], candles_override={"KXHIGHNY-Z1-M": cs})
    assert sorted(t[1] for t in col.labels["24h"].trades) == [50, 52]


def test_two_candles_ending_at_the_same_moment_is_ambiguous(tmp_path):
    close = datetime(2023, 6, 1, 5, 0, tzinfo=UTC)
    cts = int(close.timestamp())
    cs = [w_candle(cts - 24 * H, 50, 52), w_candle(cts - 24 * H, 30, 32)]
    col = run_w(tmp_path, [one_market(close=close)], candles_override={"KXHIGHNY-Z1-M": cs})
    assert col.labels["24h"].market_skips == {"ambiguous_moment": 1}


def test_candle_records_that_cannot_be_read_skip_the_market(tmp_path):
    close = datetime(2023, 6, 1, 5, 0, tzinfo=UTC)
    cts = int(close.timestamp())
    for i, bad in enumerate(([{"end_ts": "x", "yes_bid": {}, "yes_ask": {}}], [None], [{"yes_bid": {}}],
                             [{"end_ts": True, "yes_bid": {}, "yes_ask": {}}])):
        col = run_w(tmp_path / str(i), [one_market(close=close)], candles_override={"KXHIGHNY-Z1-M": bad})
        assert col.labels["24h"].market_skips == {"bad_candle_record": 1}, bad
    col = run_w(tmp_path / "c", [one_market(close=close)], corrupt={"KXHIGHNY-Z1-M"})
    assert col.labels["24h"].market_skips == {"bad_candle_record": 1}


def test_a_result_that_is_not_exactly_yes_or_no_skips_the_market(tmp_path):
    specs = [one_market(ev="KXHIGHNY-R%d" % i, result=r) for i, r in
             enumerate(["YES", "yes ", "", None, True, 1, "void", "scalar", ["yes"], "no"])]
    col = run_w(tmp_path, specs)
    lab = col.labels["24h"]
    assert lab.market_skips == {"bad_result": 9}
    assert lab.sides_considered == 2 and len(lab.trades) == 2           # only the "no" market was traded


def test_bad_prices_in_a_stored_candle_are_counted_by_reason(tmp_path):
    specs = [one_market(ev="KXHIGHNY-P%d" % i, bid=b, ask=a) for i, (b, a) in enumerate([
        (50, None), (50, float("nan")), (50, -0.1), (50, True), (50, "0.52"), (50, 0.5205), (50, 0.0), (50, 1.0),
        (None, 52), (53, 52), (52, 52), (3, 4), (95, 96)])]
    col = run_w(tmp_path, specs)
    lab = col.labels["24h"]
    assert lab.market_skips == {}
    assert lab.sides_considered == 26
    assert lab.side_skips == {"price_missing": 2, "price_invalid": 4, "price_not_whole_cent": 1,
                              "price_out_of_range": 2, "other_quote_unusable": 7, "crossed_or_locked": 5,
                              "outside_5_95": 3}
    # the only trades: the no side of (50, 1.0), costing 50, and the no side of (95, 96), costing 5; never a zero
    assert sorted(t[1] for t in lab.trades) == [5, 50]
    assert sum(lab.side_skips.values()) + len(lab.trades) == 26


@pytest.mark.parametrize("bid,ask,expect", [
    (50, None, {"price_missing": 1, "other_quote_unusable": 1}),
    (None, 52, {"price_missing": 1, "other_quote_unusable": 1}),
    (53, 52, {"crossed_or_locked": 2}),
    (52, 52, {"crossed_or_locked": 2}),
    (3, 4, {"outside_5_95": 2}),
])
def test_side_skips_one_market_at_a_time(tmp_path, bid, ask, expect):
    lab = run_w(tmp_path, [one_market(bid=bid, ask=ask)]).labels["24h"]
    assert lab.sides_considered == 2 and lab.trades == []
    assert lab.side_skips == expect


def test_a_market_without_a_usable_event_or_time_is_counted_unassigned(tmp_path):
    good = one_market()
    no_event = one_market(ev="KXHIGHNY-Q1")
    write_w(tmp_path, [good, no_event])
    base = Path(tmp_path) / "artifacts" / "kalshi_weather" / "markets.json"
    d = json.loads(base.read_text())
    d["markets"][1]["event_ticker"] = None
    d["markets"].append(dict(d["markets"][0], ticker="KXHIGHNY-Z9-M", close_time="not a time"))
    d["markets"].append(dict(d["markets"][0], ticker="KXHIGHNY-Z8-M", close_time=None))
    d["markets"].append("garbage")
    base.write_text(json.dumps(d))
    col = ab.collect("W", "development", root=tmp_path)
    assert col.unassignable == {"bad_event": 1, "bad_time": 2, "bad_record": 1}
    assert col.markets_in_period == 1


def test_duplicate_tickers_and_unsafe_tickers_are_skipped(tmp_path):
    write_w(tmp_path, [one_market()])
    p = Path(tmp_path) / "artifacts" / "kalshi_weather" / "markets.json"
    d = json.loads(p.read_text())
    d["markets"].append(dict(d["markets"][0]))                                   # same ticker again
    d["markets"].append(dict(d["markets"][0], ticker="../../etc/passwd", event_ticker="KXHIGHNY-E2"))
    p.write_text(json.dumps(d))
    col = ab.collect("W", "development", root=tmp_path)
    assert col.labels["24h"].market_skips == {"duplicate_ticker": 1, "bad_ticker": 1}
    assert len(col.labels["24h"].trades) == 2


def test_w_at_12_hours_uses_the_candle_12_hours_before_close(tmp_path):
    col = run_w(tmp_path, [one_market(bid=50, ask=52, bid12=60, ask12=62)])
    assert sorted(t[1] for t in col.labels["24h"].trades) == [50, 52]
    assert sorted(t[1] for t in col.labels["12h"].trades) == [40, 62]


# ---------------------------------------------------------------- periods
def test_w_period_is_decided_by_the_close_date_in_utc(tmp_path):
    specs = [one_market(ev="KXHIGHNY-A", close=datetime(2024, 12, 31, 23, 59, 59, tzinfo=UTC)),
             one_market(ev="KXHIGHNY-B", close=datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)),
             one_market(ev="KXHIGHNY-C", close=datetime(2024, 12, 31, 20, 0, tzinfo=timezone(timedelta(hours=-5))))]
    write_w(tmp_path, specs)
    dev = ab.collect("W", "development", root=tmp_path)
    sealed = ab.collect("W", "sealed", root=tmp_path)
    assert dev.markets_in_period == 1 and {t[0] for t in dev.labels["24h"].trades} == {"KXHIGHNY-A"}
    assert sealed.markets_in_period == 2        # B, and C: 01:00 UTC on 2025-01-01


def test_s_period_is_decided_by_the_purchase_moment_and_an_event_straddling_goes_to_sealed(tmp_path):
    d1 = datetime(2026, 1, 21, 23, 59, tzinfo=UTC)
    d2 = datetime(2026, 1, 22, 0, 0, tzinfo=UTC)
    specs = [Spec("KXNBAGAME-E1", "KXNBAGAME-E1-X", d1, "yes", 50, 52),
             Spec("KXNBAGAME-E2", "KXNBAGAME-E2-X", d2, "yes", 50, 52),
             Spec("KXNBAGAME-E3", "KXNBAGAME-E3-X", d1, "yes", 50, 52),
             Spec("KXNBAGAME-E3", "KXNBAGAME-E3-Y", d2, "no", 50, 52)]
    write_s(tmp_path, specs)
    dev = ab.collect("S", "development", root=tmp_path)
    sealed = ab.collect("S", "sealed", root=tmp_path)
    assert {t[0] for t in dev.labels["last"].trades} == {"KXNBAGAME-E1"}
    assert {t[0] for t in sealed.labels["last"].trades} == {"KXNBAGAME-E2", "KXNBAGAME-E3"}
    assert dev.markets_in_period == 1 and sealed.markets_in_period == 3


def test_s_uses_the_last_stored_candle_and_skips_markets_without_candles(tmp_path):
    when = datetime(2026, 1, 10, 18, 0, tzinfo=UTC)
    write_s(tmp_path, [Spec("KXNBAGAME-E1", "KXNBAGAME-E1-X", when, "yes", 50, 52)])
    store = kh.HistoryStore(Path(tmp_path) / "artifacts" / "sports_lines" / "kalshi.sqlite")
    store.put_market({"ticker": "KXNBAGAME-E9-X", "event_ticker": "KXNBAGAME-E9", "result": "yes"}, "historical",
                     "KXNBAGAME")
    store.close()
    col = ab.collect("S", "development", root=tmp_path)
    assert sorted(t[1] for t in col.labels["last"].trades) == [50, 52]
    assert col.unassignable == {"no_stored_candle": 1}


def test_s_store_is_opened_read_only(tmp_path):
    write_s(tmp_path, [Spec("KXNBAGAME-E1", "KXNBAGAME-E1-X", datetime(2026, 1, 10, 18, 0, tzinfo=UTC), "yes", 50, 52)])
    db = Path(tmp_path) / "artifacts" / "sports_lines" / "kalshi.sqlite"
    before = (db.stat().st_size, db.stat().st_mtime_ns, hashlib.sha256(db.read_bytes()).hexdigest())
    ab.collect("S", "development", root=tmp_path)
    ab.collect("S", "sealed", root=tmp_path)
    assert (db.stat().st_size, db.stat().st_mtime_ns, hashlib.sha256(db.read_bytes()).hexdigest()) == before
    ro = ab.open_history_readonly(db)
    with pytest.raises(sqlite3.OperationalError):
        ro.db.execute("INSERT INTO cutoffs VALUES ('a', 'b')")
    ro.close()


# ---------------------------------------------------------------- the period wall
def poison_sealed_w():
    """Sealed events whose prices and results would crash the program or change a develop number if used."""
    out = []
    for i in range(40):
        when = datetime(2025, 2, 1 + i % 20, 5, 0, tzinfo=UTC)
        ev = "KXHIGHNY-POISON%d" % i
        out.append(Spec(ev, ev + "-A", when, ["boom"], float("nan"), "0.5"))
        out.append(Spec(ev, ev + "-B", when, {"x": 1}, 50, 52))        # a clean, very profitable looking market
    return out


def test_develop_never_uses_a_sealed_event_w(tmp_path):
    dev = gen(61, 700, 3, sealed=False, kind="planted", tag="D")
    a, b = tmp_path / "a", tmp_path / "b"
    write_w(a, dev)
    poison = poison_sealed_w()
    write_w(b, dev + poison + gen(62, 50, 3, sealed=True, kind="planted", tag="S"),
            corrupt={s.ticker for s in poison} | {s.ticker for s in gen(62, 50, 3, sealed=True, tag="S")[:30]})
    ra, rb = develop(a)["result"], develop(b)["result"]
    assert canon(ra) == canon(rb)


def test_develop_never_uses_a_sealed_event_s(tmp_path):
    dev = gen(71, 500, 2, sealed=False, universe="S", kind="planted", tag="D")
    sealed = gen(72, 60, 2, sealed=True, universe="S", kind="planted", tag="S")
    a, b = tmp_path / "a", tmp_path / "b"
    write_s(a, dev)
    for s in sealed[:40]:
        s.result = ["boom"]
    write_s(b, dev + sealed, corrupt_candles={s.ticker for s in sealed[:40]})
    ra, rb = develop(a, "S")["result"], develop(b, "S")["result"]
    assert canon(ra) == canon(rb)


def test_sealed_never_uses_a_development_event(planted_w, clean_marker, tmp_path):
    root = tmp_path / "copy"
    shutil.copytree(planted_w, root)
    lo, hi = develop(root)["result"]["step_1"]["band"]
    baseline = ab.run_sealed("W", (lo, hi), flag=True, root=root, out=root / "x.json")["result"]
    for p in (root / "artifacts" / "ask_bands").glob("sealed-*.used"):
        p.unlink()
    for p in (root / "artifacts" / "kalshi_weather" / "candles").glob("*-D*.json"):
        p.write_text("{{ corrupt")
    again = ab.run_sealed("W", (lo, hi), flag=True, root=root, out=root / "y.json")["result"]
    assert canon(baseline) == canon(again)


def test_output_is_deterministic(tmp_path):
    write_w(tmp_path, gen(81, 300, 3, sealed=False, tag="D"))
    assert canon(develop(tmp_path)["result"]) == canon(develop(tmp_path)["result"])


# ---------------------------------------------------------------- the sealed gate and the marker
def test_sealed_refuses_without_the_flag(planted_w, clean_marker):
    band = tuple(develop(planted_w)["result"]["step_1"]["band"])
    with pytest.raises(ab.Refused):
        ab.run_sealed("W", band, flag=False, root=planted_w)
    assert not dev_state(planted_w)[2].exists()
    assert ab.main(["sealed", "--universe", "W", "--band", str(band[0]), str(band[1]), "--root", str(planted_w)]) != 0
    assert not dev_state(planted_w)[2].exists()


def test_sealed_refuses_a_band_that_is_not_one_of_the_153(planted_w, clean_marker):
    s1 = develop(planted_w)["result"]["step_1"]
    for bad in [(7, 20), (5, 10), (50, 45), (5, 100), (0, 10), (95, 95)]:
        with pytest.raises(ab.Refused):
            ab.run_sealed("W", bad, flag=True, root=planted_w)
        assert not dev_state(planted_w)[2].exists()


def test_sealed_refuses_a_band_other_than_the_one_development_chose(planted_w, clean_marker):
    s1 = develop(planted_w)["result"]["step_1"]
    other = (5, 15) if tuple(s1["band"]) != (5, 15) else (10, 20)
    with pytest.raises(ab.Refused):
        ab.run_sealed("W", other, flag=True, root=planted_w)
    assert not dev_state(planted_w)[2].exists()


def test_marker_is_written_before_any_sealed_row_is_read_and_a_second_run_refuses(planted_w, clean_marker, monkeypatch):
    s1 = develop(planted_w)["result"]["step_1"]
    band = tuple(s1["band"])
    dev_file, sidecar, marker = dev_state(planted_w)
    seen = {}
    real = ab.collect

    def spy(universe, period, **kw):
        seen["period"] = period
        seen["marker_existed"] = marker.exists()
        return real(universe, period, **kw)
    monkeypatch.setattr(ab, "collect", spy)
    ab.run_sealed("W", band, flag=True, root=planted_w)
    assert seen == {"period": "sealed", "marker_existed": True}
    m = json.loads(marker.read_text())
    assert m["band"] == list(band) and m["development_sha256"] == hashlib.sha256(dev_file.read_bytes()).hexdigest()
    assert m["time"] and m["universe"] == "W"
    monkeypatch.setattr(ab, "collect", lambda *a, **k: pytest.fail("a second sealed run read data"))
    with pytest.raises(ab.Refused):
        ab.run_sealed("W", band, flag=True, root=planted_w)
    assert ab.main(["sealed", "--universe", "W", "--band", str(band[0]), str(band[1]),
                    "--i-am-opening-the-sealed-period", "--root", str(planted_w)]) != 0


def test_sealed_refuses_with_a_missing_or_different_development_file(planted_w, clean_marker):
    s1 = develop(planted_w)["result"]["step_1"]
    band = tuple(s1["band"])
    dev_file, sidecar, marker = dev_state(planted_w)
    good, good_sum = dev_file.read_bytes(), sidecar.read_text()
    try:
        sidecar.unlink()                                            # the hash is missing
        with pytest.raises(ab.Refused):
            ab.run_sealed("W", band, flag=True, root=planted_w)
        sidecar.write_text(good_sum)
        dev_file.write_bytes(good + b" ")                           # the development file changed after its hash
        with pytest.raises(ab.Refused):
            ab.run_sealed("W", band, flag=True, root=planted_w)
        dev_file.unlink()                                           # the development file is missing
        with pytest.raises(ab.Refused):
            ab.run_sealed("W", band, flag=True, root=planted_w)
        assert not marker.exists()
    finally:
        dev_file.write_bytes(good)
        sidecar.write_text(good_sum)


def test_sealed_refuses_when_development_failed_or_belongs_to_another_universe_or_code(calibrated_w, planted_w,
                                                                                         clean_marker):
    develop(calibrated_w)
    with pytest.raises(ab.Refused):
        ab.run_sealed("W", (5, 15), flag=True, root=calibrated_w)             # step 1 failed: sealed is not opened
    assert not dev_state(calibrated_w)[2].exists()
    # a development file whose code hash is not this code's
    s1 = develop(planted_w)["result"]["step_1"]
    dev_file, sidecar, marker = dev_state(planted_w)
    good, good_sum = dev_file.read_bytes(), sidecar.read_text()
    try:
        d = json.loads(good)
        d["provenance"]["code_sha256"] = "0" * 64
        raw = json.dumps(d, sort_keys=True, indent=1).encode()
        dev_file.write_bytes(raw)
        sidecar.write_text(hashlib.sha256(raw).hexdigest())
        with pytest.raises(ab.Refused):
            ab.run_sealed("W", tuple(s1["band"]), flag=True, root=planted_w)
        assert not marker.exists()
    finally:
        dev_file.write_bytes(good)
        sidecar.write_text(good_sum)


def test_sealed_s_does_not_see_the_w_marker(planted_w, planted_s, clean_marker):
    # markers are per universe
    ab.run_sealed("W", tuple(develop(planted_w)["result"]["step_1"]["band"]), flag=True, root=planted_w)
    assert dev_state(planted_w, "W")[2].exists() and not dev_state(planted_w, "S")[2].exists()


# ---------------------------------------------------------------- output
def test_output_files_and_table(planted_w, capsys):
    rc = ab.main(["develop", "--universe", "W", "--root", str(planted_w)])
    assert rc == 0
    text = capsys.readouterr().out
    assert "information: decides nothing" in text and "step 1" in text.lower()
    dev_file, sidecar, _ = dev_state(planted_w)
    assert sidecar.read_text().strip() == hashlib.sha256(dev_file.read_bytes()).hexdigest()
    d = json.loads(dev_file.read_text())
    assert d["provenance"]["prediction_commit"] == "1431132"
    assert d["provenance"]["code_sha256"] == hashlib.sha256(Path(ab.__file__).read_bytes()).hexdigest()
    files = d["provenance"]["data_files"]
    assert files["markets.json"]["size"] > 0 and files["markets.json"]["mtime"] > 0
    r = d["result"]
    for k in ("markets_in_period", "events_in_period", "unassignable", "sides_considered", "side_skips",
              "market_skips", "step_1", "cell_table"):
        assert k in r, k
    assert len(r["cell_table"]) == 18
    for name in ("information_cost_1_cent", "information_kalshi_taker_fee", "information_w_12_hours_before_close"):
        t = r[name]
        assert t["note"] == "information: decides nothing" and len(t["cell_table"]) == 18
    row = r["cell_table"][9]
    assert set(row) >= {"cell", "trades", "events", "share_won", "mean_price_cents", "mean_result_cents",
                        "total_result_cents"}
    assert type(row["total_result_cents"]) is int and type(row["trades"]) is int


def test_out_argument_and_sports_output_has_no_12_hour_table(planted_s, tmp_path, capsys):
    out = tmp_path / "o" / "s.json"
    assert ab.main(["develop", "--universe", "S", "--root", str(planted_s), "--out", str(out)]) == 0
    d = json.loads(out.read_text())
    assert "information_w_12_hours_before_close" not in d["result"]
    assert Path(str(out) + ".sha256").exists()
    assert "S" == d["result"]["universe"] and d["result"]["period"] == "development"


def test_money_is_integer_cents_throughout(planted_s):
    col = ab.collect("S", "development", root=planted_s)
    for ev, paid, win in col.labels["last"].trades:
        assert type(paid) is int and type(ev) is str
        assert type(ab.trade_result(paid, win, 2)) is int
    stats = ab.band_stats([t for t in col.labels["last"].trades if 25 <= t[1] < 45], 2)
    assert type(stats["total_cents"]) is int


def test_cost_variants_change_only_the_information_tables(tmp_path):
    write_w(tmp_path, gen(91, 400, 3, sealed=False, tag="D"))
    r = develop(tmp_path)["result"]
    base = r["cell_table"][8]
    cheap = r["information_cost_1_cent"]["cell_table"][8]
    fee = r["information_kalshi_taker_fee"]["cell_table"][8]
    assert cheap["trades"] == base["trades"] and cheap["total_result_cents"] == base["total_result_cents"] + base["trades"]
    assert fee["trades"] == base["trades"] and fee["mean_price_cents"] == base["mean_price_cents"]
