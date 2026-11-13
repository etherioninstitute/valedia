import json
from datetime import date, datetime, timedelta, timezone

import pytest

from valedia_trader.research_loop import weather_markets as wm
from valedia_trader.venues import kalshi_data as kd

UTC = timezone.utc
D = date(2025, 11, 5)                                   # the main scenario's event date (a Wednesday)


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, tzinfo=UTC) + timedelta(hours=hour, minutes=minute)


def iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def run(runtime: datetime, event_day: date, txn) -> dict:
    """One NBS row: the daily high for `event_day` sits on the row valid at 00Z of the day after."""
    return {"runtime": iso(runtime), "ftime": iso(at(event_day + timedelta(days=1), 0)), "txn": txn}


def ticker(day: date, suffix: str, prefix="KXHIGHNY") -> str:
    return "%s-%s-%s" % (prefix, day.strftime("%y%b%d").upper(), suffix)


def mkt(day, suffix, kind, floor=None, cap=None, result="no", sub="", prefix="KXHIGHNY"):
    return {"ticker": ticker(day, suffix, prefix), "strike_type": kind, "floor_strike": floor, "cap_strike": cap,
            "yes_sub_title": sub, "result": result, "open_time": iso(at(day - timedelta(days=1), 15)),
            "close_time": iso(at(day + timedelta(days=1), 4, 59)), "tier": "historical"}


def candle(t: datetime, bid, ask) -> dict:
    return {"end_ts": int(t.timestamp()), "yes_bid": {"close": bid}, "yes_ask": {"close": ask}}


# ------------------------------------------------------------------ parsing

def test_the_three_strike_kinds_come_from_the_strike_fields():
    day, rule = wm.parse_market(mkt(D, "T66", "greater", floor=66))          # "67 or above"
    assert day == D and rule == wm.Rule("greater", 66, None)
    assert wm.is_yes(rule, 67) and not wm.is_yes(rule, 66)
    _, rule = wm.parse_market(mkt(D, "T59", "less", cap=59))                  # "58 or below"
    assert rule == wm.Rule("less", None, 59) and wm.is_yes(rule, 58) and not wm.is_yes(rule, 59)
    _, rule = wm.parse_market(mkt(D, "B59.5", "between", floor=59, cap=60))   # "59 to 60"
    assert rule == wm.Rule("between", 59, 60)
    assert [wm.is_yes(rule, h) for h in (58, 59, 60, 61)] == [False, True, True, False]


def test_old_markets_take_their_rule_from_the_ticker_and_subtitle():
    old = {"ticker": "HIGHNY-21AUG06-T86", "strike_type": None, "floor_strike": None, "cap_strike": None,
           "yes_sub_title": "87 or higher"}
    assert wm.parse_market(old) == (date(2021, 8, 6), wm.Rule("greater", 86, None))
    old = {"ticker": "HIGHNY-22SEP15-B74.5", "strike_type": None, "yes_sub_title": "74° to 75°"}
    assert wm.parse_market(old) == (date(2022, 9, 15), wm.Rule("between", 74, 75))
    old = {"ticker": "HIGHNY-22JAN05-T30", "strike_type": None, "yes_sub_title": "29° or below"}
    assert wm.parse_market(old) == (date(2022, 1, 5), wm.Rule("less", None, 30))
    old = {"ticker": "HIGHNY-22JAN05-B30.5", "strike_type": None, "yes_sub_title": ""}   # bracket from the ticker alone
    assert wm.parse_market(old) == (date(2022, 1, 5), wm.Rule("between", 30, 31))


def test_a_decimal_subtitle_like_44_point_0_or_higher_parses():
    row = {"ticker": "KXHIGHNY-25DEC10-T43", "strike_type": None, "yes_sub_title": "44.0 or higher"}
    day, rule = wm.parse_market(row)                                          # 44 or more = above 43
    assert day == date(2025, 12, 10) and rule == wm.Rule("greater", 43, None)
    assert wm.is_yes(rule, 44) and not wm.is_yes(rule, 43)
    row = {"ticker": "KXHIGHNY-25DEC10-T37", "strike_type": None, "yes_sub_title": "36.0 or below"}
    assert wm.parse_market(row)[1] == wm.Rule("less", None, 37)


def test_the_two_odd_2022_subtitles_parse():
    # seen once each in the real list: HIGHNY-22JUL20-T94 "<94°" and HIGHNY-22JUL23-B96.0 "96°" (subtitle "96-96°")
    row = {"ticker": "HIGHNY-22JUL20-T94", "strike_type": None, "yes_sub_title": "<94°"}
    assert wm.parse_market(row) == (date(2022, 7, 20), wm.Rule("less", None, 94))
    row = {"ticker": "HIGHNY-22JUL20-T94", "strike_type": None, "yes_sub_title": ">94°"}
    assert wm.parse_market(row)[1] == wm.Rule("greater", 94, None)
    row = {"ticker": "HIGHNY-22JUL23-B96.0", "strike_type": None, "yes_sub_title": "96°"}
    _, rule = wm.parse_market(row)
    assert rule == wm.Rule("between", 96, 96) and wm.is_yes(rule, 96) and not wm.is_yes(rule, 95)


def test_an_unreadable_market_is_none():
    assert wm.parse_market({"ticker": "KXHIGHNY-25NOV05-X9", "strike_type": None, "yes_sub_title": "weird"}) is None
    assert wm.parse_market({"ticker": "garbage", "strike_type": "greater", "floor_strike": 66}) is None
    assert wm.parse_market({"ticker": "HIGHNY-21AUG06-T86", "strike_type": None, "yes_sub_title": ""}) is None


# ------------------------------------------------------------------ forecast at a decision time

def test_forecast_at_never_uses_a_run_issued_after_the_decision_time_less_the_lag():
    runs = [run(at(D - timedelta(days=1), 19), D, 66.0),
            run(at(D, 1), D, 70.0),
            {"runtime": iso(at(D, 13)), "ftime": iso(at(D + timedelta(days=1), 12)), "txn": 50.0},  # a low, not the high
            run(at(D, 1), D + timedelta(days=1), 99.0)]                       # the next day's high: not ours
    assert wm.forecast_at(runs, D, at(D, 2, 59)) == (66.0, at(D - timedelta(days=1), 19))   # 01Z + 2 h > 02:59
    assert wm.forecast_at(runs, D, at(D, 3)) == (70.0, at(D, 1))
    assert wm.forecast_at(runs, D, at(D, 20)) == (70.0, at(D, 1))             # 13Z has no high for D
    assert wm.forecast_at(runs, D, at(D - timedelta(days=1), 20, 59)) is None
    assert wm.forecast_at(runs, D, at(D, 1), lag_hours=0) == (70.0, at(D, 1))


# ------------------------------------------------------------------ error history

def history(days_before: range, txn=60.0, pattern=(-1, 0, 0, 1), hours=((19, 1), (7, 0)), d=D):
    """Runs and observed highs for event dates d - k (k in days_before, oldest first): for each (hour, offset) a run
    issued `offset` days before the event at `hour` UTC says txn; the observed high is txn + pattern[i % 4]."""
    runs, observed = [], {}
    for i, k in enumerate(sorted(days_before, reverse=True)):
        e = d - timedelta(days=k)
        for hour, offset in hours:
            runs.append(run(at(e - timedelta(days=offset), hour), e, txn))
        observed[e.isoformat()] = int(txn + pattern[i % len(pattern)])
    return runs, observed


def test_error_sample_never_uses_an_event_date_later_than_two_days_before():
    runs, observed = history(range(2, 132))                               # D-131 .. D-2: 130 dates
    r1, o1 = history(range(1, 2), pattern=(40,))                          # D-1 missed by 40
    runs, observed = runs + r1, {**observed, **o1}
    errs = wm.error_sample(runs, observed, D, run_hour=19, day_offset=1)
    assert len(errs) == 130 and 40 not in errs
    assert sorted(set(errs)) == [-1, 0, 1]
    assert wm.error_sample(runs, observed, D, 19, 1, min_count=131) is None   # too few once D-1 is barred
    last = wm.error_sample(runs, observed, D, 19, 1, window=4, min_count=1)
    # the newest four are D-5..D-2, i = 126..129 -> pattern[126%4=2, 3, 0, 1] = 0, 1, -1, 0 (oldest first)
    assert last == [0, 1, -1, 0]


def test_error_sample_pairs_the_cycle_hours_across_the_may_2026_shift():
    d = date(2026, 5, 20)
    before, ob = history(range(16, 50), hours=((7, 0),), pattern=(-1,), d=d)     # 07Z runs, dates before 2026-05-05
    after, oa = history(range(2, 16), hours=((6, 0),), pattern=(1,), d=d)        # 06Z runs from 2026-05-05 on
    other, _ = history(range(2, 50), txn=55.0, hours=((13, 0),), pattern=(0,), d=d)  # a 13Z cycle: errors +4 / +6
    runs, observed = before + after + other, {**ob, **oa}
    six = wm.error_sample(runs, observed, d, run_hour=6, day_offset=0, min_count=1)
    seven = wm.error_sample(runs, observed, d, run_hour=7, day_offset=0, min_count=1)
    assert six == seven and sorted(set(six)) == [-1, 1] and len(six) == 48
    assert sorted(set(wm.error_sample(runs, observed, d, 12, 0, min_count=1))) == [4, 6]
    assert wm.error_sample(runs, observed, d, 6, 1, min_count=1) is None       # no run a day ahead at that hour


# ------------------------------------------------------------------ probability, quote, fee

def test_probability_counts_forecast_plus_error_and_smooths():
    rule = wm.Rule("greater", 66, None)
    # forecast 65 + errors -> 63, 65, 66, 67, 68: two above 66, so (2 + 0.5) / (5 + 1) = 0.416667
    assert wm.probability(rule, 65.0, [-2, 0, 1, 2, 3]) == pytest.approx(2.5 / 6)
    # between 65..66: 65, 66 are yes -> (2 + 0.5) / 6
    assert wm.probability(wm.Rule("between", 65, 66), 65.0, [-2, 0, 1, 2, 3]) == pytest.approx(2.5 / 6)
    assert wm.probability(rule, 50.0, [0, 1]) == pytest.approx(0.5 / 3)          # none: never exactly 0


def test_quote_at_takes_the_last_candle_ending_by_then_and_refuses_stale_or_one_sided_books():
    t = at(D, 12)
    candles = [candle(t - timedelta(hours=2), 0.40, 0.44), candle(t, 0.41, 0.45), candle(t + timedelta(hours=1), 0.9, 0.95)]
    assert wm.quote_at(candles, t) == (0.41, 0.45)                             # the 13:00 candle is not yet over
    assert wm.quote_at(candles, t - timedelta(minutes=1)) == (0.40, 0.44)
    assert wm.quote_at([candle(t - timedelta(hours=3), 0.4, 0.5)], t) == (0.4, 0.5)  # exactly 3 h old: still good
    assert wm.quote_at([candle(t - timedelta(hours=3, seconds=1), 0.4, 0.5)], t) is None
    assert wm.quote_at([candle(t, 0.0, 0.5)], t) is None                       # no bid
    assert wm.quote_at([candle(t, 0.4, 1.0)], t) is None                       # no ask
    assert wm.quote_at([candle(t, None, 0.5)], t) is None
    assert wm.quote_at([candle(t + timedelta(hours=1), 0.4, 0.5)], t) is None and wm.quote_at([], t) is None


def test_the_taker_fee():
    assert wm.taker_fee(0.40) == pytest.approx(0.0168)                        # 0.07 x 0.40 x 0.60
    assert wm.taker_fee(0.12) == pytest.approx(0.007392)                      # 0.07 x 0.12 x 0.88
    assert wm.taker_fee(0.0) == 0.0 and wm.taker_fee(1.0) == 0.0


def test_t_by_week_counts_each_iso_week_once():
    # week 45 of 2025: 1, 3 -> 2; week 46: 4; week 47: 0, 0, 6 -> 2. Weekly means 2, 4, 2: mean 8/3,
    # sample sd sqrt(((2/3)^2 + (4/3)^2 + (2/3)^2) / 2) = sqrt(4/3) = 1.1547, se 1.1547 / sqrt(3) = 2/3, t = 4.0
    days = [date(2025, 11, 3), date(2025, 11, 7), date(2025, 11, 12), date(2025, 11, 17), date(2025, 11, 18), date(2025, 11, 23)]
    t, weeks = wm.t_by_week(days, [1, 3, 4, 0, 0, 6])
    assert weeks == 3 and t == pytest.approx(4.0)
    assert wm.t_by_week([date(2025, 11, 3)], [1.0]) == (0.0, 1)
    assert wm.t_by_week([], []) == (None, 0)


# ------------------------------------------------------------------ the evaluation, worked by hand

def scenario(d=D):
    """Event date d. History d-201..d-2: two runs a day (19Z the day before, 07Z the day) both saying 60, observed
    60 + (-1, 0, 0, 1) in turn, so each cycle's 200 errors are fifty -1, a hundred 0, fifty +1. d-1 missed by 40
    (must never be used). For d: 19Z the day before says 66, 07Z says 67, a 13Z run has no high for d; observed 68.

    Model at -3 h and +3 h (19Z run, 66): values 65 x50, 66 x100, 67 x50. At +9 h and +15 h (07Z run, 67): 66, 67, 68.
      A  T66   above 66   yes   p = 50.5/201 = 0.251244 then 150.5/201 = 0.748756
      B  B65.5 65 to 66   no    p = 150.5/201 then 50.5/201
      C  B67.5 67 to 68   yes   only quote is at -3 h and 0.10/0.40 is too wide; later it is stale
      H  T70   above 70   no    p = 0.5/201 = 0.002488 at every hour; a sell edge appears only at +15 h
      E  on d-150: forecast exists, but only 50 errors before it                    -> too little error history
      G  on d+3: no run gives a high for it                                         -> no forecast
    plus one unreadable market and one before the period."""
    runs, observed = history(range(2, 202), d=d)
    r1, o1 = history(range(1, 2), pattern=(40,), d=d)
    runs += r1
    observed.update(o1)
    prev = d - timedelta(days=1)
    runs += [run(at(prev, 19), d, 66.0), run(at(d, 7), d, 67.0),
             {"runtime": iso(at(d, 13)), "ftime": iso(at(d + timedelta(days=1), 12)), "txn": 50.0}]
    observed[d.isoformat()] = 68
    e_day, g_day = d - timedelta(days=150), d + timedelta(days=3)
    markets = [mkt(d, "T66", "greater", floor=66, result="yes"), mkt(d, "B65.5", "between", floor=65, cap=66),
               mkt(d, "B67.5", "between", floor=67, cap=68, result="yes"), mkt(d, "T70", "greater", floor=70),
               mkt(e_day, "T70", "greater", floor=70), mkt(g_day, "T60", "greater", floor=60, result="yes"),
               {"ticker": ticker(d, "X9"), "strike_type": None, "yes_sub_title": "weird", "result": "no"},
               mkt(d - timedelta(days=300), "T50", "greater", floor=50)]
    h = {k: at(d, k) for k in (-3, 3, 9, 15)}
    candles = {ticker(d, "T66"): [candle(h[-3], .10, .12), candle(h[3], .13, .15), candle(h[9], .70, .72), candle(h[15], .90, .92)],
               ticker(d, "B65.5"): [candle(h[-3], .85, .87), candle(h[3], .80, .82), candle(h[9], .20, .22), candle(h[15], .05, .07)],
               ticker(d, "B67.5"): [candle(h[-3], .10, .40)],
               ticker(d, "T70"): [candle(h[-3], .02, .04), candle(h[3], .02, .04), candle(h[9], .02, .04), candle(h[15], .30, .32)],
               ticker(e_day, "T70"): [candle(at(e_day, k), .10, .12) for k in (-3, 3, 9, 15)],
               ticker(g_day, "T60"): [candle(at(g_day, k), .10, .12) for k in (-3, 3, 9, 15)]}
    return markets, candles, runs, observed


def evaluate(markets, candles, runs, observed, d=D):
    return wm.evaluate(markets, candles, runs, observed, d - timedelta(days=200), d + timedelta(days=25), decision_hours=(-3, 3, 9))


def test_brier_scores_of_model_and_market_by_hour_and_pooled():
    markets, candles, runs, observed = scenario()
    out = evaluate(markets[:2], candles, runs, observed)
    lo, hi = (50.5 / 201) ** 2, (150.5 / 201) ** 2                          # 0.063123, 0.560636
    by = out["accuracy"]["by_hour"]
    # -3 h: A p=0.251 y=1 -> (1-0.251)^2 = hi; B p=0.749 y=0 -> hi. Mids 0.11 and 0.86: (0.89^2 + 0.86^2)/2 = 0.76585
    assert by["-3"]["n"] == 2 and by["-3"]["model_brier"] == pytest.approx(hi)
    assert by["-3"]["market_brier"] == pytest.approx(0.76585)
    assert by["-3"]["model_minus_market"] == pytest.approx(hi - 0.76585)    # -0.205214: the model better
    # +3 h: same model; mids 0.14 and 0.81: (0.86^2 + 0.81^2)/2 = 0.69785
    assert by["3"]["model_brier"] == pytest.approx(hi) and by["3"]["market_brier"] == pytest.approx(0.69785)
    # +9 h: A p=0.749 y=1 -> lo; B p=0.251 y=0 -> lo. Mids 0.71, 0.21: (0.29^2 + 0.21^2)/2 = 0.0641
    assert by["9"]["model_brier"] == pytest.approx(lo) and by["9"]["market_brier"] == pytest.approx(0.0641)
    assert by["15"]["n"] == 2                                              # reported, not pooled
    pooled = out["accuracy"]["pooled_primary"]
    # six rows: model (4 hi + 2 lo)/6 = 0.394798; market (0.7921+0.7396+0.7396+0.6561+0.0841+0.0441)/6 = 0.509267
    assert pooled["n"] == 6 and pooled["model_brier"] == pytest.approx((4 * hi + 2 * lo) / 6)
    assert pooled["market_brier"] == pytest.approx(3.0556 / 6)
    assert pooled["weeks"] == 1 and pooled["t_by_week"] == 0.0             # one week: no spread to test against


def test_a_buy_and_a_sell_worked_through_to_settlement_one_trade_per_market():
    out = evaluate(*scenario())
    tr = out["trading"]
    # A at -3 h: 0.251244 - 0.12 - fee 0.007392 = 0.1239 > 0.05 -> buy at 0.12; it settles yes: 1 - 0.12 - 0.007392 = 0.872608
    #   (at +3 h 0.251244 - 0.15 - 0.008925 = 0.0923 would buy again: one trade per market forbids it)
    # B at -3 h: 0.85 - 0.748756 - fee 0.008925 = 0.0923 > 0.05 -> sell at 0.85; it settles no: 0.85 - 0 - 0.008925 = 0.841075
    # H: best edge in the primary hours 0.02 - 0.002488 - 0.001372 = 0.0161 < 0.05; +15 h (0.30 bid) is not a trading time
    assert [(t["ticker"], t["side"], t["hour"], t["price"]) for t in tr["trades_list"]] == [
        (ticker(D, "B65.5"), "sell", -3, 0.85), (ticker(D, "T66"), "buy", -3, 0.12)]
    assert tr["trades"] == 2 and tr["buys"] == 1 and tr["sells"] == 1
    assert tr["mean_profit"] == pytest.approx((0.872608 + 0.841075) / 2)      # 0.8568415
    assert tr["total_profit"] == pytest.approx(1.713683) and tr["win_share"] == 1.0
    assert tr["weeks"] == 1 and tr["t_by_week"] == 0.0


def test_skipped_market_times_are_counted_by_reason():
    out = evaluate(*scenario())
    # C: wide at -3 h, then its only candle is 6, 12 and 18 h old -> 3 no quote; E: 4 x too little history; G: 4 x no forecast
    assert out["skipped"]["total"] == {"no_forecast": 4, "too_little_error_history": 4, "no_quote": 3, "wide_spread": 1}
    assert out["skipped"]["by_hour"]["-3"] == {"no_forecast": 1, "too_little_error_history": 1, "no_quote": 0, "wide_spread": 1}
    assert out["markets_in_period"] == 6 and out["unparsed_markets"] == 1
    assert out["scored_market_times"] == 12                                  # A, B, H at four hours


@pytest.mark.parametrize("d, era", [(date(2023, 6, 14), "before_2024-10-25"), (date(2025, 3, 12), "2024-10-25_to_2025-06-30"),
                                    (date(2025, 11, 5), "from_2025-07-01")])
def test_the_eras_split_both_blocks(d, era):
    out = evaluate(*scenario(d), d=d)
    eras = out["eras"]
    assert set(eras) == {"before_2024-10-25", "2024-10-25_to_2025-06-30", "from_2025-07-01"}
    assert eras[era]["trading"]["trades"] == 2 and eras[era]["accuracy"]["pooled_primary"]["n"] == 9
    assert sum(e["trading"]["trades"] for e in eras.values()) == 2
    assert eras[era]["trading"]["total_profit"] == pytest.approx(1.713683)
    others = [v for k, v in eras.items() if k != era]
    assert all(o["accuracy"]["pooled_primary"]["n"] == 0 and o["accuracy"]["pooled_primary"]["model_brier"] is None
               for o in others)
    json.dumps(out)                                                          # the whole result is plain JSON


def test_a_wider_margin_stops_the_trades():
    markets, candles, runs, observed = scenario()
    out = wm.evaluate(markets, candles, runs, observed, date(2025, 6, 1), date(2025, 11, 30), (-3, 3, 9), margin=0.2)
    assert out["trading"]["trades"] == 0 and out["trading"]["mean_profit"] is None


# ------------------------------------------------------------------ the command line, on synthetic files only

def write_artifacts(root, markets, candles, runs, observed):
    base = root / "artifacts" / "kalshi_weather"
    (base / "candles").mkdir(parents=True)
    (base / "nbs").mkdir()
    (base / "markets.json").write_text(json.dumps({"series": "KXHIGHNY", "markets": markets}))
    for t, cs in candles.items():
        (base / "candles" / ("%s.json" % t)).write_text(json.dumps({"ticker": t, "candles": cs}))
    (base / "nbs" / "KNYC_2025-01-01_2026-01-01.json").write_text(json.dumps({"rows": runs}))
    (base / "observed.json").write_text(json.dumps({"station": "USW00094728", "highs": observed}))
    return base


def test_evaluate_on_the_command_line_prints_the_same_json(tmp_path, capsys):
    markets, candles, runs, observed = scenario()
    write_artifacts(tmp_path, markets, candles, runs, observed)
    assert wm.main(["--root", str(tmp_path), "evaluate", "--start", "2025-06-01", "--end", "2025-11-30"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["trading"]["trades"] == 2 and out["settings"]["lag_hours"] == 2 and out["settings"]["margin"] == 0.05


def test_check_reports_candle_coverage_result_agreement_and_unparsed(tmp_path, capsys):
    markets, candles, runs, observed = scenario()
    markets[0]["result"] = "no"                                               # A said no, but 68 is above 66
    write_artifacts(tmp_path, markets, candles, runs, observed)
    assert wm.main(["--root", str(tmp_path), "check"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["markets"] == 8 and out["with_candles"] == 6 and out["unparsed"] == [ticker(D, "X9")]
    # with an observed high: A, B, C, H on d and E on d-150 (d+3 and d-300 have none). B, C, H, E agree; A does not
    assert out["with_observed_high"] == 5 and out["result_agrees"] == 4 and out["result_disagrees"] == [ticker(D, "T66")]


def test_fetch_caches_everything_and_continues_where_it_stopped(tmp_path):
    hist = {"markets": [kd_market(ticker(D, "T66"), D, "greater", 66), kd_market(ticker(D, "T59"), D, "less", None, 59),
                        kd_market(ticker(D - timedelta(days=1), "T66"), D - timedelta(days=1), "greater", 66)], "cursor": ""}
    calls = []

    def kalshi_fetch(url):
        calls.append(url)
        if "/candlesticks" in url:
            return json.dumps({"candlesticks": [dict(LIVE_LIKE, end_period_ts=1762318800)]}).encode()
        if "/historical/markets?" in url:
            return json.dumps(hist).encode()
        return json.dumps({"markets": [], "cursor": ""}).encode()

    weather = []

    def weather_fetch(url):
        weather.append(url)
        if "rcc-acis" in url:
            return json.dumps({"data": [[D.isoformat(), "68"]]}).encode()
        return json.dumps([]).encode()

    sleeps = []
    client = kd.KalshiData(fetch=kalshi_fetch, sleep=sleeps.append, clock=lambda: 0.0)
    got = wm.run_fetch(tmp_path, "KXHIGHNY", limit_markets=2, history_days=0, client=client, weather_fetch=weather_fetch,
                       sleep=sleeps.append, clock=lambda: 0.0, progress=lambda s: None)
    base = tmp_path / "artifacts" / "kalshi_weather"
    assert got["selected"] == 2 and got["candles_fetched"] == 2
    assert sorted(p.name for p in (base / "candles").iterdir()) == sorted("%s.json" % ticker(D, s) for s in ("T59", "T66"))
    assert json.loads((base / "observed.json").read_text())["highs"] == {D.isoformat(): 68}
    assert any("mos.py" in u for u in weather)
    n = len(calls)
    again = wm.run_fetch(tmp_path, "KXHIGHNY", limit_markets=3, history_days=0, client=client, weather_fetch=weather_fetch,
                         sleep=sleeps.append, clock=lambda: 0.0, progress=lambda s: None)
    assert again["candles_fetched"] == 1 and again["candles_cached"] == 2
    assert sum("/candlesticks" in u for u in calls[n:]) == 1 and not any("/markets?" in u for u in calls[n:])


LIVE_LIKE = {"open_interest": "1.00", "volume": "1.00", "price": {"close": None},
             "yes_bid": {"close": "0.4"}, "yes_ask": {"close": "0.5"}}


def kd_market(t, day, kind, floor=None, cap=None):
    return {"ticker": t, "event_ticker": t.rsplit("-", 1)[0], "strike_type": kind, "floor_strike": floor, "cap_strike": cap,
            "yes_sub_title": "", "open_time": iso(at(day - timedelta(days=1), 15)), "close_time": iso(at(day + timedelta(days=1), 4, 59)),
            "result": "no", "volume_fp": "1.00"}
