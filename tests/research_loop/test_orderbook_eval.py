"""The evaluation of the order-book loop (T24), against docs/research/2026-11-13-orderbook-loop-prediction.md: every
outcome of the prediction's table, the three quoting rules, fills, the valuation and its three fee variants, the
tape-cover rule of note 3, day counting, the periods, the one-time scoring and no look-ahead. Every record here is
built through the collector's own `Store` in a temporary folder; nothing opens the real record."""
import hashlib
import json
import math
import random
import sqlite3
import statistics
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pytest

from valedia_trader.research_loop import orderbook_eval as oe
from valedia_trader.research_loop import orderbook_loop as ol

UTC = timezone.utc
D0 = datetime(2026, 11, 14, tzinfo=UTC).timestamp()      # the first full UTC day of the generated records
START = D0 - 1800                                        # collection began 2026-11-13 23:30 UTC
A = START + 60 * 40 + 2                                  # a decision's "now" snapshot (minute 40) ...
DTS = A + 18                                             # ... and the decision, 18 s later


def snap(end, mid, start=None):
    return {"book_start_ts": end - 1 if start is None else start, "book_end_ts": end, "end_ts": end + 1, "mid": mid}


def path(mids, a=A):
    """The snapshots after the decision: minute k (1, 2, ...) at a + 60k with mids[k - 1]."""
    return [snap(a + 60 * k, m) for k, m in enumerate(mids, 1)]


def trade(ts, side, price_c=50.0):
    return {"created_ts": ts, "taker_side": side, "price_c": price_c, "trade_id": "t%.3f" % ts}


# ================================================================= the outcomes of the prediction's table

def test_equal_mids_are_neither_up_nor_down_and_a_flat_path_is_quiet():
    o = oe.mid_outcomes(50.0, 4.0, 0.0, A, path([50.0] * 31))
    assert o == {"up_15m": False, "down_15m": False, "move_15m": False, "regime": "quiet"}


def test_up_and_down_compare_the_mid_fifteen_minutes_later_with_the_mid_now():
    mids = [50.0] * 31
    mids[14] = 50.5
    o = oe.mid_outcomes(50.0, 4.0, 0.0, A, path(mids))
    assert (o["up_15m"], o["down_15m"]) == (True, False)
    mids[14] = 49.5
    o = oe.mid_outcomes(50.0, 4.0, 0.0, A, path(mids))
    assert (o["up_15m"], o["down_15m"]) == (False, True)


def test_the_mid_later_is_the_snapshot_nearest_the_horizon_within_thirty_seconds():
    base = path([50.0] * 14)
    assert oe.mid_outcomes(50.0, 4.0, 0.0, A, base + [snap(A + 931, 51.0)])["up_15m"] is None
    assert oe.mid_outcomes(50.0, 4.0, 0.0, A, base + [snap(A + 870, 51.0)])["up_15m"] is True
    tie = base + [snap(A + 880, 51.0), snap(A + 920, 49.0)]          # equally near: the earlier one
    assert oe.mid_outcomes(50.0, 4.0, 0.0, A, tie)["up_15m"] is True
    one_sided = base + [snap(A + 900, None)]                          # the nearest has no mid: not known
    assert oe.mid_outcomes(50.0, 4.0, 0.0, A, one_sided)["up_15m"] is None
    assert oe.mid_outcomes(None, None, 0.0, A, path([50.0] * 31)) == dict.fromkeys(
        ("up_15m", "down_15m", "move_15m", "regime"))


def test_a_move_of_exactly_half_the_spread_counts_and_a_little_less_does_not():
    mids = [50.0] * 31
    mids[6] = 52.0                                                    # spread 4: half is 2
    assert oe.mid_outcomes(50.0, 4.0, 0.0, A, path(mids))["move_15m"] is True
    mids[6] = 51.5
    assert oe.mid_outcomes(50.0, 4.0, 0.0, A, path(mids))["move_15m"] is False
    assert oe.mid_outcomes(50.0, 3.0, 0.0, A, path(mids))["move_15m"] is True    # half of 3 is 1.5, exactly
    mids[6] = 48.0                                                    # down counts too
    assert oe.mid_outcomes(50.0, 4.0, 0.0, A, path(mids))["move_15m"] is True
    mids[6] = 50.0
    mids[15] = 55.0                                                   # minute 16 is outside the 15 minutes
    assert oe.mid_outcomes(50.0, 4.0, 0.0, A, path(mids))["move_15m"] is False
    gap = [s for k, s in enumerate(path([50.0] * 31), 1) if k != 9]  # minute 9 not covered: not known
    gap[2]["mid"] = 53.0
    assert oe.mid_outcomes(50.0, 4.0, 0.0, A, gap)["move_15m"] is None


def test_regime_quiet_trending_and_mean_reverting():
    def regime(mids, prev):
        return oe.mid_outcomes(50.0, 4.0, prev, A, path(mids))["regime"]
    mids = [50.0] * 31
    mids[10], mids[29] = 52.0, 51.0                  # inside the two-cent band, ends within one cent: quiet
    assert regime(mids, 1.0) == "quiet"
    mids[10] = 52.5                                  # left the band: not quiet
    assert regime(mids, 1.0) == "trending"           # moved one cent in the direction of the last 30 minutes
    assert regime(mids, -1.0) == "mean_reverting"
    mids[29] = 52.0                                  # the last 30 minutes did not move: two cents either way
    assert regime(mids, 0.0) == "trending"
    mids[29] = 51.5
    assert regime(mids, 0.0) == "mean_reverting"
    mids[10], mids[29] = 50.0, 48.5                  # ends 1.5 down: not quiet
    assert regime(mids, -0.5) == "trending" and regime(mids, 0.5) == "mean_reverting"
    assert regime(mids, None) is None


def test_toxic_is_defined_only_when_a_trade_happens_in_the_next_fifteen_minutes():
    after = path([50.0] * 32)
    assert oe.toxic_outcome(50.0, A, DTS, after, []) == (None, None)
    assert oe.toxic_outcome(50.0, A, DTS, after, [trade(DTS, "yes")]) == (None, None)       # not after the decision
    assert oe.toxic_outcome(50.0, A, DTS, after, [trade(DTS + 900.5, "yes")]) == (None, None)
    assert oe.toxic_outcome(50.0, A, DTS, after, [trade(DTS + 900, "yes")]) == (False, "yes")  # the edge counts


def test_toxic_takes_the_first_trade_and_its_takers_side():
    mids = [50.0] * 32
    mids[16] = 51.0                                  # minute 17 (A + 1020) is nearest 15 minutes after DTS + 100
    after = path(mids)
    first = [trade(DTS + 100, "yes"), trade(DTS + 200, "no")]
    assert oe.toxic_outcome(50.0, A, DTS, after, first) == (True, "yes")
    assert oe.toxic_outcome(50.0, A, DTS, after, [trade(DTS + 100, "no")]) == (False, "no")
    mids[16] = 49.0
    after = path(mids)
    assert oe.toxic_outcome(50.0, A, DTS, after, [trade(DTS + 100, "no")]) == (True, "no")
    assert oe.toxic_outcome(50.0, A, DTS, after, first) == (False, "yes")
    mids[16] = 50.0                                  # level: false either way
    after = path(mids)
    assert oe.toxic_outcome(50.0, A, DTS, after, [trade(DTS + 100, "no")]) == (False, "no")
    both = [trade(DTS + 100, "yes"), trade(DTS + 100, "no")]   # two sides at the same first moment: not known
    assert oe.toxic_outcome(50.0, A, DTS, after, both) == (None, None)


def test_the_mid_just_before_the_trade_is_the_latest_snapshot_before_it_and_must_be_recent():
    mids = [51.0] * 32
    mids[15] = 52.0                                  # minute 16: nearest 15 minutes after DTS + 5 (A + 923)
    after = path(mids)
    # a trade 5 s after the decision: the latest book before it is the "now" snapshot (mid 50 from the state)
    assert oe.toxic_outcome(50.0, A, DTS, after, [trade(DTS + 5, "yes")]) == (True, "yes")
    # a trade at DTS + 100: the latest book before it is minute 1 (51.0); 15 minutes later minute 17 is 51.0
    assert oe.toxic_outcome(50.0, A, DTS, after, [trade(DTS + 100, "yes")]) == (False, "yes")
    stale = [s for k, s in enumerate(after, 1) if k > 3]               # nothing from A to A + 240
    assert oe.toxic_outcome(50.0, A, DTS, stale, [trade(DTS + 200, "yes")]) == (None, "yes")


# ================================================================= paper quoting

def test_quotes_rest_one_cent_inside_the_book_and_need_room():
    assert oe.quote_prices(50.0, 4.0) == (49.0, 51.0)
    assert oe.quote_prices(50.5, 3.0) == (50.0, 51.0)
    assert oe.quote_prices(50.0, 2.0) is None and oe.quote_prices(None, None) is None


def test_the_three_quoting_rules():
    base = {"spread_cents": 4.0, "imbalance_top": 0.5, "trades_5m": 2}

    def ans(qe=2.0, mv=0.49):
        return {"quote_env": {"type": "score", "score": qe}, "move_15m": {"type": "noul", "noul": mv}}
    assert oe.quoting_rules(base, ans()) == {"always": True, "plain": True, "model": True}
    for change in ({"spread_cents": 3.0}, {"imbalance_top": 0.29}, {"imbalance_top": 0.71}, {"trades_5m": 3},
                   {"trades_5m": None}, {"imbalance_top": None}, {"spread_cents": None}):
        assert oe.quoting_rules(dict(base, **change), ans())["plain"] is False, change
    for change in ({"imbalance_top": 0.3}, {"imbalance_top": 0.7}, {"trades_5m": 0}, {"spread_cents": 15.0}):
        assert oe.quoting_rules(dict(base, **change), ans())["plain"] is True, change
    assert oe.quoting_rules(base, ans(qe=1.99))["model"] is False
    assert oe.quoting_rules(base, ans(mv=0.5))["model"] is False
    assert oe.quoting_rules(base, ans(qe=3.0, mv=0.0))["model"] is True
    assert oe.quoting_rules(base, {})["model"] is False


def test_a_resting_quote_fills_on_a_trade_at_or_through_its_price_within_five_minutes():
    q = (49.0, 51.0)
    assert oe.find_fills(q, DTS, [trade(DTS + 10, "no", 49.0)]) == [{"side": "buy", "ts": DTS + 10, "price_c": 49.0}]
    assert oe.find_fills(q, DTS, [trade(DTS + 10, "no", 48.0)]) == [{"side": "buy", "ts": DTS + 10, "price_c": 49.0}]
    assert oe.find_fills(q, DTS, [trade(DTS + 10, "no", 50.0)]) == []          # above the buy price
    assert oe.find_fills(q, DTS, [trade(DTS + 10, "yes", 49.0)]) == []         # a taker buying fills no buy
    assert oe.find_fills(q, DTS, [trade(DTS + 10, "yes", 51.0)]) == [{"side": "sell", "ts": DTS + 10, "price_c": 51.0}]
    assert oe.find_fills(q, DTS, [trade(DTS + 10, "yes", 52.0)]) == [{"side": "sell", "ts": DTS + 10, "price_c": 51.0}]
    assert oe.find_fills(q, DTS, [trade(DTS + 10, "yes", 50.0)]) == []
    assert oe.find_fills(q, DTS, [trade(DTS + 300, "no", 49.0)])[0]["ts"] == DTS + 300
    assert oe.find_fills(q, DTS, [trade(DTS + 301, "no", 49.0)]) == []         # one second outside
    assert oe.find_fills(q, DTS, [trade(DTS, "no", 49.0)]) == []               # not after the decision
    tape = [trade(DTS + 50, "no", 50.0), trade(DTS + 80, "no", 49.0), trade(DTS + 120, "no", 48.0),
            trade(DTS + 90, "yes", 53.0)]
    assert oe.find_fills(q, DTS, sorted(tape, key=lambda x: x["created_ts"])) == [
        {"side": "buy", "ts": DTS + 80, "price_c": 49.0}, {"side": "sell", "ts": DTS + 90, "price_c": 51.0}]


def test_each_fill_is_valued_against_the_mid_fifteen_minutes_after_it():
    mids = [50.0] * 32
    mids[15] = 52.0                                    # minute 16 (A + 960) is nearest DTS + 40 + 900 = A + 958
    after = path(mids)
    fills = [{"side": "buy", "ts": DTS + 40, "price_c": 49.0}, {"side": "sell", "ts": DTS + 40, "price_c": 51.0}]
    v = oe.value_fills(fills, after)
    assert [(f["mid_after"], f["gross_c"]) for f in v] == [(52.0, 3.0), (52.0, -1.0)]
    assert oe.value_fills([], after) == []
    assert oe.value_fills(fills, [s for s in after if s["book_end_ts"] != A + 960]) is None


def test_the_maker_fee_and_its_three_variants():
    assert oe.maker_fee_c(50.0) == 1.0 and oe.maker_fee_c(10.0) == 1.0 and oe.maker_fee_c(50.0, 0.0) == 0.0
    assert oe.maker_fee_c(50.0, 3.0) == 2.0           # 3 x 0.0175 x 0.25 = 1.3125 cents, rounded up
    mk, quad = oe.MAKER_FEE_TYPE, "quadratic"
    assert oe.fill_fee_c("series", 50.0, mk, 1.0) == 1.0
    assert oe.fill_fee_c("series", 50.0, quad, 1.0) == 0.0
    assert oe.fill_fee_c("series", 50.0, None, None) == 1.0          # unknown fee type: charged
    assert oe.fill_fee_c("none", 50.0, mk, 1.0) == 0.0
    assert oe.fill_fee_c("everywhere", 50.0, quad, 1.0) == 1.0
    assert oe.fill_fee_c("everywhere", 50.0, quad, 0.0) == 1.0
    with pytest.raises(ValueError):
        oe.fill_fee_c("half", 50.0, mk, 1.0)


# ================================================================= the tape-cover rule of note 3

def reads(*spec, ok=1):
    return [{"trades_ok": ok, "trades_min_ts": mn, "trades_start_ts": st} for st, mn in spec]


def test_tape_cover_is_judged_from_the_spans_of_the_successful_reads():
    # reads each minute from 1000, each asking from 150 s before the previous one began (the collector's read-back)
    spec = [(1000 + 60 * k, 1000 + 60 * (k - 1) - 150) for k in range(20)]
    assert oe.Reads(reads(*spec)).covers(1100, 1400)
    # reads 6 to 8 failed and read 9 asked only from 30 s before its start (as after a hole of over 24 hours): the
    # trades created after 1240 (read 5's start, 1300, less the lag) and before 1390 (read 10's read-back) are unread
    hole = reads(*spec[:6]) + reads(*spec[6:9], ok=0) + reads((1540, 1510)) + reads(
        *[(1000 + 60 * k, 1000 + 60 * (k - 1) - 150) for k in range(10, 20)])
    r = oe.Reads(hole)
    assert not r.covers(1100, 1400) and not r.covers(1250, 1380) and not r.covers(1100, 1241)
    assert r.covers(1100, 1240) and r.covers(1390, 1700)
    # failed reads alone leave no hole: the next good read asks from 150 s before the last good one began
    failed = reads(*spec[:6]) + reads(*spec[6:8], ok=0) + reads((1480, 1300 - 150)) + reads(*spec[9:])
    assert oe.Reads(failed).covers(1100, 1400)
    assert oe.Reads(reads(*[(st, None) for st, _ in spec])).covers(1100, 1400) is False
    # the stretch counts only when a read began at least the tape's lag (60 s) after its end
    assert oe.Reads(reads(*spec[:8])).covers(1100, 1360) and not oe.Reads(reads(*spec[:8])).covers(1100, 1361)
    assert oe.Reads(reads(*spec)).covers(790, 1100) and not oe.Reads(reads(*spec)).covers(789, 1100)


def test_day_counting_at_exactly_eighty_percent():
    assert oe.counts(10, 8) and not oe.counts(10, 7) and not oe.counts(0, 0)
    assert oe.counts(288, 231) and not oe.counts(288, 230) and oe.counts(5, 4)


# ================================================================= periods

def test_periods_are_whole_utc_days_from_the_first_full_day_of_collection():
    per = oe.periods(datetime(2026, 11, 13, 15, 46, tzinfo=UTC).timestamp())
    first = date(2026, 11, 14)
    assert per["first_full_day"] == first
    assert per["development"] == [first + timedelta(days=k) for k in range(5)]
    assert (per["test_first"], per["test_last"], per["test_max_last"]) == (
        date(2026, 11, 19), date(2026, 12, 2), date(2026, 12, 9))
    assert oe.periods(D0)["first_full_day"] == first                     # collection from exactly midnight
    assert oe.periods(D0 + 1)["first_full_day"] == date(2026, 11, 15)
    assert oe.periods(D0 - 1)["first_full_day"] == first
    assert oe.day_of(D0 - 0.001) == date(2026, 11, 13) and oe.day_of(D0) == first
    assert oe.day_start(first) == D0 and oe.day_end(first) == D0 + 86400
    names = [oe.period_of(per, first + timedelta(days=k)) for k in (-1, 0, 4, 5, 18, 19, 25, 26)]
    assert names == ["before", "development", "development", "test", "test", "test_extension", "test_extension",
                     "after"]


def test_the_test_period_is_extended_until_it_has_ten_counted_days_and_at_most_to_21():
    per = oe.periods(START)
    tf = per["test_first"]
    every = {tf + timedelta(days=k) for k in range(21)}
    due = oe.day_end(per["test_last"]) + oe.OUTCOME_WAIT_S
    assert oe.test_end(per, every, due - 1) == (None, False)
    assert oe.test_end(per, every, due) == (per["test_last"], True)
    nine = {tf + timedelta(days=k) for k in range(9)}
    assert oe.test_end(per, nine, due) == (None, False)                 # waits for day 15
    later = nine | {tf + timedelta(days=16)}
    day17 = oe.day_end(tf + timedelta(days=16)) + oe.OUTCOME_WAIT_S
    assert oe.test_end(per, later, day17 - 1) == (None, False)
    assert oe.test_end(per, later, day17) == (tf + timedelta(days=16), True)
    never = oe.day_end(per["test_max_last"]) + oe.OUTCOME_WAIT_S
    assert oe.test_end(per, nine, never) == (per["test_max_last"], False)


# ================================================================= records built through the collector's Store

COLS = ("watch_id", "ticker", "book_start_iso", "book_start_ts", "book_end_iso", "book_end_ts", "trades_start_iso",
        "trades_start_ts", "trades_end_iso", "trades_end_ts", "end_iso", "end_ts", "trades_min_ts", "trades_ok",
        "book_state", "yes_json", "no_json", "best_bid", "best_bid_size", "best_ask", "best_ask_size", "mid", "spread",
        "new_trades", "tail", "invalid_reason", "raw_json")


def answers(up=0.5, down=0.5, move=0.5, toxic=0.5, regime="quiet", qe=2.0):
    keys = ["trending", "mean_reverting", "quiet"]
    return {"up_15m": {"type": "noul", "noul": up}, "down_15m": {"type": "noul", "noul": down},
            "move_15m": {"type": "noul", "noul": move}, "toxic_15m": {"type": "noul", "noul": toxic},
            "regime": {"type": "choice", "choice": regime, "probabilities": {k: (0.8 if k == regime else 0.1)
                                                                            for k in keys}, "confidence": 0.5},
            "quote_env": {"type": "score", "score": qe}}


def state_of(book, **kw):
    s = dict.fromkeys(ol.STATE_KEYS)
    if book[0] == "ok":
        s.update(mid_cents=(book[1] + book[2]) / 2, spread_cents=float(book[2] - book[1]))
    s.update({"imbalance_top": 0.5, "trades_5m": 0, "mid_change_15m_cents": 0.0, "mid_change_30m_cents": 0.0})
    s.update(kw)
    return s


def write_record(path, markets, decisions, *, start=START, n_min, drop=True):
    """A record through the collector's Store: per market a book and a trades read each minute (book at +2 s, trades
    read from +2.5 s), its trades, and the decisions given (slot = the minute, decided 20 s into it).
    books: ("ok", bid, ask) | ("one", bid) | ("bad",); reads: "good" | "fail" | "break" (asks only from 30 s before
    its start, as the collector's 24-hour limit does after a long hole)."""
    store = ol.Store(path)
    c = store.conn
    store.insert("events", {**ol.stamp("", start), "kind": "start", "ticker": None, "detail_json": "{}"})
    wids = {}
    for m in markets:
        wids[m["ticker"]] = store.insert("markets", {
            "selection_id": 1, "ticker": m["ticker"], "series": m["series"], **ol.stamp("selected", start),
            **ol.stamp("close", start + 60 * 86400), "market_json": "{}", "book_json": "{}",
            "fee_type": m["fee_type"], "fee_multiplier": m["mult"], "why": "test", "phase": m["phase"],
            **ol.stamp("watch_start", start)})
        rows, last = [], None
        for i in range(n_min):
            t = start + 60 * i
            b = m["books"][i]
            kind = m["reads"][i] if "reads" in m else "good"
            st = t + 2.5
            if kind == "fail":
                ok, mn = 0, None
            elif kind == "break":
                ok, mn = 1, int(st - 30)
            else:
                ok, mn = 1, int((last if last is not None else st + 90) - 150)
            if ok:
                last = st
            if b[0] == "ok":
                yes, no = [[b[1] / 100, 10.0]], [[round(1 - b[2] / 100, 6), 10.0]]
                bid, ask = b[1] / 100, round(1 - no[0][0], 6)
                summ = ("two_sided", bid, 10.0, ask, 10.0, round((bid + ask) / 2, 6), round(ask - bid, 6), None)
            elif b[0] == "one":
                yes, no = [[b[1] / 100, 10.0]], []
                summ = ("one_sided_yes", b[1] / 100, 10.0, None, None, None, None, None)
            else:
                yes, no = [], []
                summ = ("invalid", None, None, None, None, None, None, "crossed")
            rows.append((wids[m["ticker"]], m["ticker"], ol.iso(t + 1), t + 1, ol.iso(t + 2), t + 2,
                         ol.iso(st) if ok else None, st if ok else None, ol.iso(st + 0.3) if ok else None,
                         st + 0.3 if ok else None, ol.iso(t + 3), t + 3, mn, ok, summ[0], json.dumps(yes),
                         json.dumps(no), summ[1], summ[2], summ[3], summ[4], summ[5], summ[6], 0, 0, summ[7], None))
        with c:
            c.executemany("INSERT INTO snapshots (%s) VALUES (%s)" % (",".join(COLS), ",".join("?" * len(COLS))), rows)
            c.executemany("INSERT INTO trades (trade_id, ticker, created_iso, created_ts, yes_price, no_price, count, "
                          "taker_side, is_block_trade, seen_iso, seen_ts, snapshot_id) "
                          "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                          [("%s-%d" % (m["ticker"], k), m["ticker"], ol.iso(ts), ts, p / 100, round(1 - p / 100, 6),
                            1.0, side, 0, ol.iso(ts + 60), ts + 60, None)
                           for k, (ts, side, p) in enumerate(m["trades"])])
    with c:
        for d in decisions:
            slot = start + 60 * d["i"]
            c.execute("INSERT INTO decisions (watch_id, ticker, slot_iso, slot_ts, decision_iso, decision_ts, status, "
                      "forced, battery_id, state_json, model_tag, answers_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                      (wids[d["ticker"]], d["ticker"], ol.iso(slot), slot, ol.iso(slot + 20), slot + 20, d["status"],
                       d.get("forced", 0), d.get("battery", oe.BATTERY_IN_FORCE),
                       None if d.get("state") is None else json.dumps(d["state"]), ol.TAG,
                       None if d.get("answers") is None else json.dumps(d["answers"])))
        if drop:
            for m in markets:
                c.execute("INSERT INTO market_drops (watch_id, ticker, dropped_iso, dropped_ts, reason) VALUES "
                          "(?,?,?,?,?)", (wids[m["ticker"]], m["ticker"], ol.iso(start + 60 * n_min),
                                          start + 60 * n_min, "end of test"))
    store.close()
    return path


def one_market(books, trades=(), reads=None, ticker="AAA-1", fee_type=oe.MAKER_FEE_TYPE):
    m = {"ticker": ticker, "series": ticker.split("-")[0], "fee_type": fee_type, "mult": 1.0, "phase": 0,
         "books": books, "trades": list(trades)}
    if reads is not None:
        m["reads"] = reads
    return m


def flat_books(n, bid=48, ask=52):
    return [("ok", bid, ask)] * n


def dec(i, book, ticker="AAA-1", **kw):
    return {"ticker": ticker, "i": i, "status": "ok", "state": kw.pop("state", state_of(book)),
            "answers": kw.pop("answers", answers()), **kw}


def test_the_record_is_opened_read_only(tmp_path):
    p = write_record(tmp_path / "r.sqlite", [one_market(flat_books(60))], [], n_min=60)
    rec = oe.open_record(p)
    with pytest.raises(sqlite3.OperationalError):
        rec.conn.execute("INSERT INTO events (kind) VALUES ('x')")
    with pytest.raises(oe.Refused):
        oe.open_record(tmp_path / "missing.sqlite")
    assert not (tmp_path / "missing.sqlite").exists()


def test_a_day_counts_from_the_collectors_own_slot_count_at_exactly_eighty_percent(tmp_path):
    # one watch from 00:00 of a day: warm at 00:30:02, so its slots on phase 0 are 00:35, 00:40, ..., ten in all
    n = 35 + 50
    books = flat_books(n)
    for collected, counts in ((8, True), (7, False)):
        ds = [dec(35 + 5 * k, books[0], status="ok" if k < collected else "late",
                  answers=answers() if k < collected else None) for k in range(10)]
        p = write_record(tmp_path / ("d%d.sqlite" % collected), [one_market(books)], ds, start=D0, n_min=n)
        rec = oe.open_record(p)
        row = oe.day_table(rec, D0 + 2 * 86400)[date(2026, 11, 14)]
        assert (row["expected"], row["collected"], row["counts"]) == (10, collected, counts)


def test_a_decision_under_another_battery_is_not_collected_and_not_scored(tmp_path):
    n = 35 + 50
    books = flat_books(n)
    ds = [dec(35 + 5 * k, books[0]) for k in range(8)] + [dec(75, books[0]), dec(80, books[0], battery="0" * 64)]
    rec = oe.open_record(write_record(tmp_path / "b.sqlite", [one_market(books)], ds, start=D0, n_min=n))
    row = oe.day_table(rec, D0 + 2 * 86400)[date(2026, 11, 14)]
    assert (row["expected"], row["collected"], row["other_battery"]) == (10, 9, 1)
    ob = oe.observe(rec, [date(2026, 11, 14)])
    assert len(ob["obs"]) == 9 and ob["counts"]["other_battery"] == 1


def test_a_slot_without_tape_cover_is_left_out_for_all_three_rules_and_counted(tmp_path):
    n = 120
    books = flat_books(n)
    rds = ["good"] * n
    rds[41:45] = ["fail", "fail", "fail", "break"]        # a hole in the trade reads just after minute 40
    trades = [(START + 60 * 40 + 60, "no", 49.0), (START + 60 * 60 + 60, "no", 49.0)]
    ds = [dec(40, books[0], answers=answers(qe=3.0, move=0.1)), dec(60, books[0], answers=answers(qe=3.0, move=0.1))]
    rec = oe.open_record(write_record(tmp_path / "c.sqlite", [one_market(books, trades, rds)], ds, n_min=n))
    ob = oe.observe(rec, [oe.day_of(START + 2400)])
    first, second = ob["obs"]
    assert first["policy"]["status"] == "no_cover" and not first["toxic_cover"]
    assert first["outcomes"]["toxic_15m"] is None and first["outcomes"]["up_15m"] is False   # mids are not affected
    assert second["policy"]["status"] == "quoted" and second["toxic_cover"]
    pol = oe.policy(ob["obs"])
    assert pol["slots"] == 1 and pol["left_out_no_cover"] == 1
    v = pol["variants"]["none"]
    assert {r: v["rules"][r]["slots"] for r in oe.RULES} == {"always": 1, "plain": 1, "model": 1}
    assert v["rules"]["always"]["mean"] == 1.0                       # bought at 49, mid 50 fifteen minutes later
    assert pol["variants"]["series"]["rules"]["always"]["mean"] == 0.0   # less a one-cent maker fee


# ================================================================= no look-ahead

def lookahead_record(path, after_change=False, before_change=False):
    n = 140
    rnd = random.Random(3)
    books = []
    bid = 45
    for i in range(n):
        bid = min(60, max(30, bid + rnd.choice((-1, 0, 0, 1))))
        books.append(("ok", bid, bid + 4))
    trades = [(START + 60 * i + rnd.uniform(5, 55), rnd.choice(("yes", "no")), books[i][1] + rnd.randint(0, 4))
              for i in range(n) if rnd.random() < 0.5]
    if after_change:          # rows after every outcome window of the decision at minute 40 (DTS + 1860)
        books = books[:73] + [("ok", 20, 30)] * (n - 73)
        trades = [x for x in trades if x[0] < START + 60 * 73] + [(START + 60 * 80 + 7, "yes", 25)]
    if before_change:         # rows before the decision: older books and earlier trades
        books = [("ok", 10, 14)] * 39 + books[39:]
        trades = [x for x in trades if x[0] > DTS] + [(START + 60 * 30 + 5, "no", 12)]
    ds = [dec(40, books[40], answers=answers(qe=3.0, move=0.1),
              state=state_of(books[40], mid_change_15m_cents=1.0, imbalance_top=0.6, trades_5m=1))]
    return write_record(path, [one_market(books, trades)], ds, n_min=n)


def test_no_look_ahead(tmp_path):
    def outcome(p):
        o = oe.observe(oe.open_record(p), [oe.day_of(DTS)])["obs"][0]
        return o["outcomes"], o["regime"], o["policy"]
    base = outcome(lookahead_record(tmp_path / "base.sqlite"))
    assert base == outcome(lookahead_record(tmp_path / "after.sqlite", after_change=True))
    assert base == outcome(lookahead_record(tmp_path / "before.sqlite", before_change=True))
    assert any(v is not None for v in base[0].values()) and base[2]["status"] == "quoted"


def test_the_decisions_inputs_are_read_from_the_state_never_recomputed(tmp_path):
    n = 80
    books = flat_books(n)
    # the stored state says spread 6 around 50 (the book says 48/52): the quotes come from the state, 48 and 52
    st = state_of(books[40], spread_cents=6.0)
    rec = oe.open_record(write_record(tmp_path / "s.sqlite", [one_market(books, [(DTS + 30, "no", 48.0)])],
                                      [dec(40, books[40], state=st)], n_min=n))
    o = oe.observe(rec, [oe.day_of(DTS)])["obs"][0]
    assert [(f["side"], f["price_c"]) for f in o["policy"]["fills"]] == [("buy", 48.0)]
    # a state whose mid differs from the book it was built on is left out and counted
    bad = state_of(books[40], mid_cents=51.0)
    rec = oe.open_record(write_record(tmp_path / "m.sqlite", [one_market(books)], [dec(40, books[40], state=bad)],
                                      n_min=n))
    ob = oe.observe(rec, [oe.day_of(DTS)])
    assert ob["counts"]["now_mismatch"] == 1 and ob["obs"][0]["excluded"] == "now_mismatch"
    assert oe.policy(ob["obs"])["slots"] == 0


# ================================================================= a generated record: 5 development and 14 test days

MARKETS = (("AAA-26NOV-X", "AAA", oe.MAKER_FEE_TYPE, 1.0, 0), ("BBB-26NOV-Y", "BBB", "quadratic", 1.0, 2))
N_MIN = (19 * 86400 + 1800 + 5400) // 60
LOW = {date(2026, 11, 16): 0.25, date(2026, 11, 23): 0.3}      # a development and a test day under 80%
P_LEVELS = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]
OFF_BATTERY = (0, 9000)                                          # (market, minute) answered under another battery


def generate(path, seed=11):
    rnd = random.Random(seed)
    markets, decisions = [], []
    for mk, (ticker, series, fee_type, mult, phase) in enumerate(MARKETS):
        bid, spread, books, rds, trades = 40, 4, [], [], []
        for i in range(N_MIN):
            if rnd.random() < 0.3:
                bid = min(70, max(20, bid + rnd.choice((-1, 1))))
            if rnd.random() < 0.1:
                spread = rnd.choice((2, 3, 4, 5, 6))
            r = rnd.random()
            books.append(("bad",) if r < 0.002 else ("one", bid) if r < 0.005 else ("ok", bid, bid + spread))
            if len(rds) <= i:                                  # a hole: 3 to 6 failed reads, then a short one
                r = rnd.random()
                if i > 0 and r < 0.0015:
                    rds += ["fail"] * rnd.randint(3, 6) + ["break", "good"]
                else:
                    rds.append("fail" if i > 0 and r < 0.01 else "good")
            if rnd.random() < 0.4:
                trades.append((START + 60 * i + rnd.uniform(4.1, 59.3), rnd.choice(("yes", "no")),
                               rnd.randint(bid - 1, bid + spread + 1)))
        rds = rds[:N_MIN]
        markets.append({"ticker": ticker, "series": series, "fee_type": fee_type, "mult": mult, "phase": phase,
                        "books": books, "reads": rds, "trades": trades})
        for i in range(40, N_MIN - 40):
            t = START + 60 * i
            if (t // 60) % 5 != phase:
                continue
            b = books[i]
            if b[0] == "bad":
                decisions.append({"ticker": ticker, "i": i, "status": "stale_data"})
                continue
            if rnd.random() < LOW.get(oe.day_of(t), 0.0):
                decisions.append({"ticker": ticker, "i": i, "status": "late", "state": state_of(b)})
                continue
            fut = [x[1:] for x in books[i + 1:i + 16]]
            moved = b[0] == "ok" and all(len(x) == 2 for x in fut) and \
                max(abs((x[0] + x[1]) / 2 - (b[1] + b[2]) / 2) for x in fut) >= (b[2] - b[1]) / 2
            mv = min(0.9, max(0.1, (0.8 if moved else 0.2) + rnd.choice((-0.1, 0.0, 0.1))))
            st = state_of(b, imbalance_top=rnd.choice((0.2, 0.3, 0.5, 0.7, 0.8, None)),
                          trades_5m=rnd.choice((0, 1, 2, 3, 4, None)),
                          mid_change_15m_cents=rnd.choice((-1.0, 0.0, 1.0, None)),
                          mid_change_30m_cents=rnd.choice((-1.5, 0.0, 2.0, None)))
            ans = answers(up=rnd.choice(P_LEVELS), down=rnd.choice(P_LEVELS), move=mv, toxic=rnd.choice(P_LEVELS),
                          regime=rnd.choice(("trending", "mean_reverting", "quiet")), qe=rnd.uniform(0.0, 3.0))
            d = {"ticker": ticker, "i": i, "status": "ok", "state": st, "answers": ans}
            if (mk, i) == OFF_BATTERY:
                d["battery"] = "0" * 64
            decisions.append(d)
            if (mk, i) == (1, 9002):
                decisions.append(dict(d, forced=1))           # a smoke-test decision: never counts
    write_record(path, markets, decisions, n_min=N_MIN)
    return markets, decisions


# ---- the independent calculation: from the generator's own minute lists, by minute index

def _mid(b):
    return (b[1] + b[2]) / 2 if b[0] == "ok" else None


def _minute_near(ts):
    """The minute whose book (at +2 s) is nearest ts; ties to the earlier."""
    x = (ts - START - 2) / 60
    k = math.floor(x)
    return k if x - k <= 0.5 else k + 1


def _holes(rds):
    """Each hole in created time: after the last good read g before a `break` read b, the trades from g's start less
    the 60 s lag to b's start less 150 s (where the read after b asks from) are never read."""
    out = []
    for b, kind in enumerate(rds):
        if kind == "break":
            g = max(m for m in range(b) if rds[m] != "fail")
            out.append((START + 60 * g + 2.5 - 60, int(START + 60 * b + 2.5 - 150)))
    return out


def _covered(holes, s, e):
    return not any(lo < e and hi > s for lo, hi in holes)


def independent(markets, decisions):
    by_t = {m["ticker"]: dict(m, holes=_holes(m["reads"])) for m in markets}
    obs = []
    for d in decisions:
        if d["status"] != "ok" or d.get("forced") or d.get("battery"):
            continue
        m = by_t[d["ticker"]]
        books, i = m["books"], d["i"]
        st, ans = d["state"], d["answers"]
        dts = START + 60 * i + 20
        mid0, sp0 = st["mid_cents"], st["spread_cents"]
        o = {"day": oe.day_of(dts).isoformat(), "probs": {q: ans[q]["noul"] for q in oe.QUESTIONS}, "y": {}, "g": {},
             "regime_answer": ans["regime"]["choice"], "regime": None}

        def mid_at(k):
            return _mid(books[k]) if 0 <= k < len(books) else None
        o["y"]["up_15m"] = o["y"]["down_15m"] = o["y"]["move_15m"] = None
        if mid0 is not None:
            m15 = mid_at(i + 15)
            if m15 is not None:
                o["y"]["up_15m"], o["y"]["down_15m"] = m15 > mid0, m15 < mid0
            p15 = [mid_at(i + k) for k in range(1, 16)]
            if None not in p15:
                o["y"]["move_15m"] = max(abs(x - mid0) for x in p15) >= sp0 / 2
            p30 = [mid_at(i + k) for k in range(1, 31)]
            prev = st["mid_change_30m_cents"]
            if None not in p30 and prev is not None:
                dd = p30[-1] - mid0
                if abs(dd) <= 1 and all(abs(x - mid0) <= 2 for x in p30):
                    o["regime"] = "quiet"
                elif (prev > 0 and dd >= 1) or (prev < 0 and dd <= -1) or (prev == 0 and abs(dd) >= 2):
                    o["regime"] = "trending"
                else:
                    o["regime"] = "mean_reverting"
        tox_cover = _covered(m["holes"], dts, dts + 900)
        o["tox_cover"] = tox_cover
        o["y"]["toxic_15m"], side = None, None
        win = sorted(x for x in m["trades"] if dts < x[0] <= dts + 900)
        if tox_cover and win:
            t1, side = win[0][0], win[0][1]
            j = math.floor((t1 - START - 2) / 60)          # the latest minute whose book is before the trade
            while j > i and books[j][0] == "bad":
                j -= 1
            before = mid0 if j == i else _mid(books[j])
            if t1 - (START + 60 * j + 2) <= 90 and before is not None:
                after = mid_at(_minute_near(t1 + 900))
                if after is not None:
                    o["y"]["toxic_15m"] = after > before if side == "yes" else after < before
        c15 = st["mid_change_15m_cents"]
        o["g"]["up_15m"] = o["g"]["down_15m"] = ("unknown" if c15 is None else "up" if c15 > 0 else "down"
                                                 if c15 < 0 else "flat")
        o["g"]["move_15m"] = "unknown"
        if mid0 is not None:
            back = [k for k in range(1, 16) if books[i - k][0] != "bad"]
            mids = [_mid(books[i - k]) for k in back if _mid(books[i - k]) is not None]
            if len(back) >= 14 and mids:
                o["g"]["move_15m"] = "moved" if max(abs(x - mid0) for x in mids) >= sp0 / 2 else "still"
        imb = st["imbalance_top"]
        o["g"]["toxic_15m"] = None if side is None else "unknown" if imb is None else (
            "leans" if (side == "yes" and imb > 0.5) or (side == "no" and imb < 0.5) else "not")
        # the policy part
        rules = {"always": True,
                 "plain": sp0 is not None and sp0 >= 4 and imb is not None and 0.3 <= imb <= 0.7 and
                 st["trades_5m"] is not None and st["trades_5m"] < 3,
                 "model": ans["quote_env"]["score"] >= 2.0 and ans["move_15m"]["noul"] < 0.5}
        o["rules"], o["fees"] = rules, m["fee_type"]
        if not _covered(m["holes"], dts, dts + 300):
            o["pstatus"] = "no_cover"
        elif mid0 is None or sp0 < 3:
            o["pstatus"], o["fills"] = "no_quote", []
        else:
            buy, sell = mid0 - sp0 / 2 + 1, mid0 + sp0 / 2 - 1
            w5 = sorted(x for x in m["trades"] if dts < x[0] <= dts + 300)
            fills = []
            b = next((x for x in w5 if x[1] == "no" and x[2] <= buy), None)
            s = next((x for x in w5 if x[1] == "yes" and x[2] >= sell), None)
            if b:
                fills.append(("buy", b[0], buy))
            if s:
                fills.append(("sell", s[0], sell))
            vals = []
            for side_, ts, price in fills:
                after = mid_at(_minute_near(ts + 900))
                if after is None:
                    vals = None
                    break
                vals.append((after - price if side_ == "buy" else price - after, price))
            if vals is None:
                o["pstatus"] = "unvalued"
            else:
                o["pstatus"], o["fills"] = "quoted", vals
        obs.append(o)
    return obs


def ind_fee(variant, fee_type, price):
    if variant == "none" or (variant == "series" and fee_type != oe.MAKER_FEE_TYPE):
        return 0.0
    return float(math.ceil(0.0175 * (price / 100) * (1 - price / 100) * 100 - 1e-12))


def t_of(xs):
    if len(xs) < 2 or statistics.stdev(xs) == 0:
        return None
    return statistics.mean(xs) / (statistics.stdev(xs) / math.sqrt(len(xs)))


def auc_ranks(p, y):
    """Mann-Whitney with average ranks."""
    p, y = np.asarray(p, float), np.asarray(y, int)
    npos = int(y.sum())
    nneg = len(y) - npos
    if npos == 0 or nneg == 0:
        return None
    order = np.argsort(p, kind="mergesort")
    _, inv, cnt = np.unique(p[order], return_inverse=True, return_counts=True)
    avg = np.cumsum(cnt) - (cnt - 1) / 2.0
    ranks = np.empty(len(p))
    ranks[order] = avg[inv]
    return float((ranks[y == 1].sum() - npos * (npos + 1) / 2) / (npos * nneg))


def ind_fit(obs):
    out = {}
    for q in oe.QUESTIONS:
        rows = [(o["y"][q], o["g"][q]) for o in obs if o["y"][q] is not None]
        groups = {}
        for y, g in rows:
            groups.setdefault(g, []).append(y)
        out[q] = {"n": len(rows), "plain": sum(y for y, _ in rows) / len(rows),
                  "split": {g: {"n": len(v), "rate": sum(v) / len(v)} for g, v in groups.items()}}
    return out


def ind_judgment(obs, fit, resamples):
    out = {}
    for qi, q in enumerate(oe.QUESTIONS):
        f = fit[q]
        rows = [(o["day"], o["probs"][q], float(o["y"][q]), o["g"][q]) for o in obs if o["y"][q] is not None]

        def yard(g):
            return f["split"][g]["rate"] if g in f["split"] else f["plain"]
        bm = [(p - y) ** 2 for _, p, y, _ in rows]
        bp = [(f["plain"] - y) ** 2 for _, _, y, _ in rows]
        bs = [(yard(g) - y) ** 2 for _, _, y, g in rows]
        days = sorted({r[0] for r in rows})
        dp = [statistics.mean([bp[k] - bm[k] for k, r in enumerate(rows) if r[0] == d]) for d in days]
        ds = [statistics.mean([bs[k] - bm[k] for k, r in enumerate(rows) if r[0] == d]) for d in days]
        rng = np.random.default_rng([oe.SEED, qi])
        draws = rng.integers(0, len(days), size=(resamples, len(days)))
        per_day = {d: ([r[1] for r in rows if r[0] == d], [r[2] for r in rows if r[0] == d]) for d in days}
        vals = []
        for row in draws:
            p = sum((per_day[days[k]][0] for k in row), [])
            y = sum((per_day[days[k]][1] for k in row), [])
            v = auc_ranks(p, y)
            if v is not None:
                vals.append(v)
        lo, hi = np.percentile(vals, [2.5, 97.5])
        r = {"n": len(rows), "days": len(days), "brier_model": statistics.mean(bm), "brier_plain": statistics.mean(bp),
             "brier_split": statistics.mean(bs), "t_vs_plain": t_of(dp), "t_vs_split": t_of(ds),
             "ranking": auc_ranks([x[1] for x in rows], [x[2] for x in rows]), "ranking_95": [lo, hi]}
        r["pass"] = (r["brier_model"] < r["brier_plain"] and r["brier_model"] < r["brier_split"] and
                     (r["t_vs_plain"] or 0) >= 2 and (r["t_vs_split"] or 0) >= 2 and lo > 0.5)
        out[q] = r
    return out


def ind_policy(obs):
    slots = [o for o in obs if o["pstatus"] in ("quoted", "no_quote")]
    out = {"slots": len(slots), "left_out_no_cover": sum(o["pstatus"] == "no_cover" for o in obs),
           "left_out_unvalued": sum(o["pstatus"] == "unvalued" for o in obs), "variants": {}}
    for v in oe.VARIANTS:
        vals = {r: [] for r in oe.RULES}
        daily = {}
        for o in slots:
            x = sum(g - ind_fee(v, o["fees"], p) for g, p in o["fills"])
            for r in oe.RULES:
                y = x if o["rules"][r] else 0.0
                vals[r].append(y)
                daily.setdefault(o["day"], {rr: [] for rr in oe.RULES})[r].append(y)
        means = {r: statistics.mean(vals[r]) for r in oe.RULES}
        t = {o: t_of([statistics.mean(daily[d]["model"]) - statistics.mean(daily[d][o]) for d in sorted(daily)])
             for o in ("always", "plain")}
        out["variants"][v] = {"means": means, "t": t, "pass": means["model"] > 0 and means["model"] > means["always"]
                              and means["model"] > means["plain"] and all((t[o] or 0) >= 2 for o in t)}
    return out


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    path = tmp_path_factory.mktemp("rec") / "orderbook_loop.sqlite"
    markets, decisions = generate(path)
    return path, markets, decisions


def per_and_days():
    per = oe.periods(START)
    dev = per["development"]
    test = [per["test_first"] + timedelta(days=k) for k in range(14)]
    return per, dev, test


def ind_counted(decisions, days):
    """A full day counts when 80% of its 2 x 288 grid slots have an answered decision under the battery in force."""
    out = []
    for d in days:
        got = sum(1 for x in decisions if x["status"] == "ok" and not x.get("forced") and not x.get("battery")
                  and oe.day_of(START + 60 * x["i"]) == d)
        if 5 * got >= 4 * 576:
            out.append(d)
    return out


def test_a_generated_record_is_scored_as_an_independent_calculation_says(generated, tmp_path):
    path, markets, decisions = generated
    per, dev, test = per_and_days()
    now = oe.day_end(per["test_last"]) + oe.OUTCOME_WAIT_S
    dev_c, test_c = ind_counted(decisions, dev), ind_counted(decisions, test)
    assert date(2026, 11, 16) not in dev_c and date(2026, 11, 23) not in test_c and len(test_c) == 13
    obs = independent(markets, decisions)
    dev_obs = [o for o in obs if o["day"] in {d.isoformat() for d in dev_c}]
    test_obs = [o for o in obs if o["day"] in {d.isoformat() for d in test_c}]

    fit = oe.cmd_fit(path, tmp_path / "eval", now)["fitted"]
    want = ind_fit(dev_obs)
    assert fit["counted_days"] == [d.isoformat() for d in dev_c]
    for q in oe.QUESTIONS:
        got = fit["questions"][q]
        assert got["n"] == want[q]["n"] > 100
        assert got["plain"] == pytest.approx(want[q]["plain"], rel=1e-12)
        assert {g: (s["n"], pytest.approx(s["rate"], rel=1e-12)) for g, s in got["split"].items()} == \
            {g: (s["n"], s["rate"]) for g, s in want[q]["split"].items()}

    res = oe.cmd_score(path, tmp_path / "eval", now, resamples=300)
    assert res["test_end"] == per["test_last"].isoformat() and res["reached"] is True
    assert res["counted_days"] == [d.isoformat() for d in test_c]
    jw = ind_judgment(test_obs, want, 300)
    for q in oe.QUESTIONS:
        g, w = res["judgment"][q], jw[q]
        assert (g["n"], g["days"]) == (w["n"], w["days"]), q
        for k in ("brier_model", "brier_plain", "brier_split", "t_vs_plain", "t_vs_split", "ranking"):
            assert g[k] == pytest.approx(w[k], rel=1e-9), (q, k)
        assert g["ranking_95"] == pytest.approx(w["ranking_95"], rel=1e-9), q
        assert g["pass"] == w["pass"], q
    assert res["judgment"]["toxic_15m"]["left_out_no_cover"] == sum(not o["tox_cover"] for o in test_obs) > 0
    assert res["judgment"]["move_15m"]["pass"] is True                # the generator gave move_15m real skill

    pw = ind_policy(test_obs)
    pg = res["policy"]
    assert (pg["slots"], pg["left_out_no_cover"], pg["left_out_unvalued"]) == (
        pw["slots"], pw["left_out_no_cover"], pw["left_out_unvalued"])
    assert pg["left_out_no_cover"] > 0 and pg["left_out_unvalued"] > 0
    for v in oe.VARIANTS:
        for r in oe.RULES:
            assert pg["variants"][v]["rules"][r]["mean"] == pytest.approx(pw["variants"][v]["means"][r], rel=1e-9,
                                                                          abs=1e-12), (v, r)
        for o in ("always", "plain"):
            assert pg["variants"][v]["model_minus_" + o]["t"] == pytest.approx(pw["variants"][v]["t"][o], rel=1e-9)
        assert pg["variants"][v]["pass"] == pw["variants"][v]["pass"]
    assert pg["variants"]["series"]["rules"]["always"]["mean"] < pg["variants"]["none"]["rules"]["always"]["mean"]

    rg = [o for o in test_obs if o["regime"] is not None]
    assert res["regime"]["n"] == len(rg)
    assert res["regime"]["accuracy"] == pytest.approx(sum(o["regime"] == o["regime_answer"] for o in rg) / len(rg))

    line = res["pass_line"]
    passing = [q for q in oe.QUESTIONS if jw[q]["pass"]]
    assert line["questions_passing"] == passing
    assert line["passes"] == (len(passing) >= 2 and pw["variants"]["series"]["pass"])


def test_the_same_seed_gives_the_same_ranges(generated):
    path, _, _ = generated
    rec = oe.open_record(path)
    per, dev, test = per_and_days()
    ob = oe.observe(rec, test)                      # 14 days: enough distinct draws for the seed to matter
    fit = oe.fit_yardsticks(ob["obs"])
    a = oe.judgment(ob["obs"], fit, resamples=200)
    b = oe.judgment(ob["obs"], fit, resamples=200)
    c = oe.judgment(ob["obs"], fit, resamples=200, seed=oe.SEED + 1)
    assert a == b and a["up_15m"]["ranking_95"] != c["up_15m"]["ranking_95"]


# ================================================================= the commands

def record_sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(capsys, *argv, now):
    code = oe.main(list(argv), clock=lambda: now)
    out = capsys.readouterr()
    return code, out.out, out.err


def test_status_shows_each_day_its_period_and_cover_and_reads_no_answer(generated, tmp_path, capsys, monkeypatch):
    path, _, _ = generated
    for name in ("observe", "mid_outcomes", "toxic_outcome", "fit_yardsticks", "judgment", "policy"):
        monkeypatch.setattr(oe, name, lambda *a, **k: pytest.fail("status compared an answer with an outcome"))
    now = D0 + 21 * 86400
    code, out, _ = run(capsys, "status", "--db", str(path), "--eval-dir", str(tmp_path / "e"), now=now)
    assert code == 0
    lines = {ln.split()[0]: ln for ln in out.splitlines() if ln[:4] == "2026"}
    assert "development" in lines["2026-11-14"] and " yes " in lines["2026-11-14"]
    assert " no " in lines["2026-11-16"] and "test " in lines["2026-11-19"] and " no " in lines["2026-11-23"]
    assert "before" in lines["2026-11-13"]
    assert "first full day 2026-11-14" in out and "no tape cover" in out
    assert not (tmp_path / "e").exists()                                 # status writes nothing


def test_fit_is_refused_before_the_development_days_are_complete_and_made_once(generated, tmp_path, capsys):
    path, _, _ = generated
    per, dev, _ = per_and_days()
    due = oe.day_end(dev[-1]) + oe.OUTCOME_WAIT_S
    ev = str(tmp_path / "e")
    code, _, err = run(capsys, "fit", "--db", str(path), "--eval-dir", ev, now=due - 1)
    assert code == 2 and "development" in err
    code, out, _ = run(capsys, "fit", "--db", str(path), "--eval-dir", ev, now=due)
    assert code == 0
    stored = json.loads(out)
    assert stored["fitted_sha256"] == hashlib.sha256(oe.canon(stored["fitted"]).encode()).hexdigest()
    assert stored["code"] == oe.code_hashes()
    code, _, err = run(capsys, "fit", "--db", str(path), "--eval-dir", ev, now=due + 86400)
    assert code == 2 and "already" in err


def test_score_is_refused_without_a_fit_and_before_the_test_period_is_complete(generated, tmp_path, capsys):
    path, _, _ = generated
    per, dev, _ = per_and_days()
    ev = str(tmp_path / "e")
    due = oe.day_end(per["test_last"]) + oe.OUTCOME_WAIT_S
    code, _, err = run(capsys, "score", "--db", str(path), "--eval-dir", ev, now=due)
    assert code == 2 and "fit" in err
    assert run(capsys, "fit", "--db", str(path), "--eval-dir", ev, now=due)[0] == 0
    code, _, err = run(capsys, "score", "--db", str(path), "--eval-dir", ev, now=due - 1)
    assert code == 2 and "not complete" in err
    es = oe.EvalStore(ev)
    assert es.result() is None
    es.close()


def test_the_test_period_is_scored_once_and_a_second_run_recomputes_nothing(generated, tmp_path, capsys,
                                                                            monkeypatch):
    path, _, _ = generated
    per, _, _ = per_and_days()
    ev = str(tmp_path / "e")
    due = oe.day_end(per["test_last"]) + oe.OUTCOME_WAIT_S
    before = record_sha(path)
    assert run(capsys, "fit", "--db", str(path), "--eval-dir", ev, now=due)[0] == 0
    code, out, _ = run(capsys, "score", "--db", str(path), "--eval-dir", ev, now=due)
    assert code == 0
    first = json.loads(out)
    assert first["period"] == "test" and set(first["pass_line"]) >= {"passes", "judgment", "policy"}
    monkeypatch.setattr(oe, "observe", lambda *a, **k: pytest.fail("the stored result was recomputed"))
    monkeypatch.setattr(oe, "open_record", lambda *a, **k: pytest.fail("the record was read again"))
    code, out, _ = run(capsys, "score", "--db", str(path), "--eval-dir", ev, now=due + 86400)
    assert code == 0
    again = json.loads(out)
    assert again["result"] == first and again["note"].startswith("the stored result")
    assert record_sha(path) == before
    es = oe.EvalStore(ev)
    kinds = [r["command"] for r in es.log_entries()]
    assert kinds == ["fit", "score", "score_shown"]
    for sql in ("UPDATE result SET result_json = '{}'", "DELETE FROM result", "DELETE FROM fit", "DELETE FROM log"):
        with pytest.raises(sqlite3.DatabaseError):
            es.conn.execute(sql)
    es.close()


def test_score_is_refused_when_the_code_changed_since_the_fit(generated, tmp_path, capsys, monkeypatch):
    path, _, _ = generated
    per, _, _ = per_and_days()
    ev = str(tmp_path / "e")
    due = oe.day_end(per["test_last"]) + oe.OUTCOME_WAIT_S
    assert run(capsys, "fit", "--db", str(path), "--eval-dir", ev, now=due)[0] == 0
    real = oe.code_hashes()
    monkeypatch.setattr(oe, "code_hashes", lambda: dict(real, orderbook_eval="0" * 64))
    code, _, err = run(capsys, "score", "--db", str(path), "--eval-dir", ev, now=due)
    assert code == 2 and "--code-changed" in err


def test_development_numbers_are_labelled_and_can_be_asked_for_again(generated, tmp_path, capsys):
    path, _, _ = generated
    per, dev, _ = per_and_days()
    ev = str(tmp_path / "e")
    due = oe.day_end(dev[-1]) + oe.OUTCOME_WAIT_S
    outs = []
    for _ in range(2):
        code, out, _ = run(capsys, "development", "--db", str(path), "--eval-dir", ev, now=due)
        assert code == 0
        outs.append(json.loads(out))
    assert outs[0] == outs[1] and outs[0]["period"] == "development" and "DEVELOPMENT" in outs[0]["label"]
    assert outs[0]["counted_days"] == [d.isoformat() for d in dev if d != date(2026, 11, 16)]
    es = oe.EvalStore(ev)
    assert es.fit() is None and es.result() is None and len(es.log_entries()) == 2
    es.close()


def test_one_writer_at_a_time(tmp_path):
    es = oe.EvalStore(tmp_path / "e")
    with pytest.raises(oe.Refused):
        oe.EvalStore(tmp_path / "e")
    es.close()
    oe.EvalStore(tmp_path / "e").close()


def test_the_battery_in_force_is_the_one_the_prediction_names():
    assert oe.BATTERY_IN_FORCE == "3e07adb11ae25c23b334bb9f9576208854686cdb1177d0728026d2d9bd927730"
    assert oe.BATTERY_IN_FORCE == ol.BATTERY_ID
