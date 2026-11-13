"""The evaluation of the order-book loop, exactly as its locked prediction says.

The prediction is docs/research/2026-11-13-orderbook-loop-prediction.md (with its notes 1 to 6, written before
collection began); every rule and threshold below comes from it, and nothing is tuned on data. The collector's
record (`orderbook_loop.py`) is opened read-only (`mode=ro`) always; this module keeps its own small append-only
store (default `artifacts/orderbook_eval/`, one writer at a time) for the yardsticks it fits and the result it scores.
No network, no model call, no Kalshi request.

    python -m valedia_trader.research_loop.orderbook_eval status      --db <record>  # days, periods, cover
    python -m valedia_trader.research_loop.orderbook_eval fit         --db <record>  # the yardsticks, once
    python -m valedia_trader.research_loop.orderbook_eval score       --db <record>  # the test period, once
    python -m valedia_trader.research_loop.orderbook_eval development --db <record>  # development numbers, any time

What each part reads (the readings marked "reading" are where the prediction leaves a choice; each took the stricter
or the more literal one and is listed for a dated clarification):

* Decisions. Only non-forced decisions with status `ok`, answers, and the battery in force (BATTERY_IN_FORCE, note 1),
  one per market and slot (the first by row id). A decision's day is the UTC day of its slot. Its inputs are read
  from its stored state (`state_json`), never recomputed: the mid and the spread now, the imbalance, the trade
  count, the mid changes. Its "now" snapshot is the newest valid snapshot of its market finished before the decision
  (the collector's own rule, `state_at`); a state whose mid differs from that snapshot's is left out of everything
  and counted (`now_mismatch`). Reading: the yes/no answers are the typed `noul` probabilities; `quote_env` is the
  rubric's expected `score`.
* Outcomes use only snapshots that began after the decision (`book_start_ts` after `decision_ts`) and trades created
  after it; "now" is the state's mid at the now snapshot's book time `a`. "The mid h minutes later" is the snapshot
  whose book read ended nearest a + h within 30 s either side (the collector's `reference`), among the valid ones;
  a one-sided book there gives no mid, and then the outcome is not known. A path of one-minute mids (minutes 1 to
  15, or 1 to 30) is taken by the collector's window rule (minute k: the snapshot nearest a + 60k within 30 s, each
  snapshot used once); reading: every minute of the path must be covered by a two-sided book, or the outcome is not
  known.
  - `up_15m` / `down_15m`: the mid at a + 15 min above / below the mid now (equal: both false).
  - `move_15m`: some minute of the 15 is at least half the spread now away from the mid now (exactly half counts).
  - `toxic_15m`: the first trade created in (decision, decision + 15 min] (several at the same first moment with
    different taker sides: not known); its direction is up when its taker bought yes. "The mid just before it" is
    the latest valid snapshot whose book read ended before the trade (the now snapshot included, with the state's
    mid); reading: it must have ended at most 90 s (the collector's STALE_S) before the trade. The outcome is true
    when the mid at the trade + 15 min is beyond that mid on the taker's side. Note 3: counted only where the trade
    reads cover the whole 15 minutes (below); a slot without cover is left out and counted.
  - `regime` (not part of the pass line; reported as the share of answers that named it): quiet when the mid at
    a + 30 min is within one cent of the mid now and no minute of the 30 is more than two cents from it (reading: the
    two-cent band is two cents either side); else trending when the mid at a + 30 min moved a cent or more in the
    direction of the state's `mid_change_30m_cents` (two cents or more either way when that was zero); else
    mean-reverting. A null `mid_change_30m_cents` leaves it unknown.
* Tape cover (note 3): judged only from the spans of the successful trade reads stored with the snapshots, never from
  the event rows. A read covers the trades created from its `trades_min_ts` to its start (the collector's span, as in
  `_covered`) less 60 s (reading: the tape's lag, TAPE_LAG_S, so that a trade printed late inside the stretch has
  been read); the spans of all successful reads are merged where they touch, and a stretch is covered when one merged
  span holds all of it. A paper fill needs (decision, decision + 5 min]; `toxic_15m` needs (decision, decision +
  15 min].
* Paper quoting. At each slot, from the state: a buy one cent above the best bid and a sell one cent below the best
  ask, for five minutes. Reading: with a spread under 3 cents (the two quotes would meet or cross) or a one-sided
  book no rule can quote, and the slot earns zero for all three. A buy fills on the first trade in (decision,
  decision + 5 min] whose taker sold (taker side `no`) at or below its price; a sell, on the first whose taker bought
  yes at or above its price; each fills at its own price, one contract. Each fill is valued at the mid 15 minutes
  after the fill (the nearest snapshot, as above) less the maker fee; a fill that cannot be valued leaves the slot
  out for all three rules alike (counted, `unvalued`); a slot without tape cover likewise (`no_cover`). Inventory is
  not carried. The rules, from the state and the answers: always; plain (spread at least 4 cents, top-of-book
  imbalance from 0.3 to 0.7 inclusive, fewer than 3 trades in the last five minutes; a null input: no quote); model
  (`quote_env` 2.0 or more and `move_15m` below 0.5).
* The maker fee, one contract at price P (dollars): M x 0.0175 x P x (1 - P), rounded up to a whole cent (reading: the
  secondary source's formula, rounded up as a cent-precision account pays it; at every price on these markets it is
  one cent when M is 1). Three variants: `series` (the main one: where the market's series fee type is
  `quadratic_with_maker_fees`, with the series' multiplier; reading: an unknown fee type is charged), `none`, and
  `everywhere` (every market, with its multiplier, or 1 where it has none or zero).
* Days and periods. A day counts when at least 80% of its decision slots were collected (the collector's own count,
  `day_status`; less any slot answered under another battery, so the share can only fall), judged in integers. The
  first full UTC day is the first that begins at or after the collection's first `start` event. Development: the
  first 5 full days; reading: only its counted days are fitted on. Test: the next 14 days, extended day by day to at
  most 21 until 10 have counted. A period is complete OUTCOME_WAIT_S (1 h, reading) after its last day ends, when
  every outcome window of its last decisions has closed.
* Yardsticks, fitted on the development period only, over the decisions whose outcome is known: (a) the plain rate
  at which each yes/no question came true; (b) that rate split by one number known at the decision: for `up_15m` and
  `down_15m` the sign of the state's `mid_change_15m_cents` (up, down, flat, unknown); for `move_15m` whether some
  minute of the last 15 (the collector's `window` over the snapshots before the decision) was at least half the
  spread now away from the mid now (moved, still, unknown when the window is not covered); for `toxic_15m` whether
  the state's `imbalance_top` leans to the first trade's taker side (above 0.5 for a yes taker, below 0.5 for a no
  taker; unknown when null). A group not seen in development takes the plain rate.
* Judgment, per yes/no question on the counted test days: the Brier score of the model and of each yardstick; each
  difference's by-day t statistic (the mean over days of each day's mean of yardstick Brier less model Brier, over its
  standard error; fewer than two days or no spread: no t, which fails); the ranking (AUC, ties count half) and its 95%
  range by resampling whole days: BOOTSTRAP draws, with `numpy.random.default_rng([SEED, question index])`, each draw
  `rng.integers(0, D, size=(BOOTSTRAP, D))` over the days in date order, the AUC of the pooled draw (a draw with no
  true or no false outcome is dropped and counted), and the 2.5 and 97.5 percentiles (numpy's linear rule). A
  question passes when the model's Brier is below both yardsticks', both t are 2 or more, and the range's lower end
  is above 0.5.
* Policy, on the same slots for the three rules: mean profit per slot (cents) over all included slots; the model rule
  passes when that mean is above zero and above the always and plain rules' means, each difference (by day: each
  day's mean of the model rule less that day's mean of the other) with a t of 2 or more. Judged on the `series` fee
  variant; the other two are reported.
* The idea passes when the test reached 10 counted days, at least two questions pass and the policy passes.

`fit` stores the yardsticks once, with the SHA-256 of their canonical JSON and of this module's and the collector's
source; `score` refuses without a stored fit, before the test period is complete, and when the code differs from the
fit's unless `--code-changed "<reason>"` is given (logged); it scores once, and a later run prints the stored result
without reading the record. Every command except `status` writes a line to the store's log.
"""
from __future__ import annotations

import argparse
import bisect
import fcntl
import hashlib
import json
import math
import sqlite3
import statistics
import sys
import time
import urllib.parse
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Iterable

import numpy as np

from valedia_trader.research_loop import REPO
from valedia_trader.research_loop import orderbook_loop as ol

__all__ = ["BATTERY_IN_FORCE", "QUESTIONS", "RULES", "VARIANTS", "Refused", "open_record", "day_of", "day_start",
           "day_end", "periods", "period_of", "counts", "day_table", "test_end", "Reads", "mid_outcomes",
           "toxic_outcome", "quote_prices", "quoting_rules", "find_fills", "value_fills", "maker_fee_c", "fill_fee_c",
           "observe", "fit_yardsticks", "judgment", "policy", "pass_line", "EvalStore", "code_hashes", "canon",
           "cmd_status", "cmd_fit", "cmd_score", "cmd_development", "main"]

UTC = timezone.utc
EPS = 1e-9

# ---- the prediction's settings (locked)
BATTERY_IN_FORCE = "3e07adb11ae25c23b334bb9f9576208854686cdb1177d0728026d2d9bd927730"   # note 1
QUESTIONS = ("up_15m", "down_15m", "move_15m", "toxic_15m")
RULES = ("always", "plain", "model")
VARIANTS = ("series", "none", "everywhere")
DEV_DAYS, TEST_DAYS, TEST_MAX_DAYS, TEST_MIN_COUNTED = 5, 14, 21, 10
COUNT_NUM, COUNT_DEN = 4, 5                  # a day counts at 80% of its decision slots collected
HORIZON_S, REGIME_S, QUOTE_S, VALUE_S = 900, 1800, 300, 900
QUIET_END_C, QUIET_BAND_C, TREND_C, TREND_FLAT_C = 1.0, 2.0, 1.0, 2.0
PLAIN_MIN_SPREAD_C, PLAIN_IMB_LO, PLAIN_IMB_HI, PLAIN_MAX_TRADES = 4.0, 0.3, 0.7, 3
MODEL_MIN_QUOTE_ENV, MODEL_MAX_MOVE = 2.0, 0.5
T_LINE, RANK_LINE, MIN_PASSING = 2.0, 0.5, 2

# ---- readings (see the docstring)
REF_S = ol.REF_WINDOW_S                      # 30 s either side, the collector's reference rule
STALE_S = ol.STALE_S                         # the mid just before a trade: at most 90 s before it
TAPE_LAG_S = ol.TAPE_LAG_S                   # a stretch is covered by a read begun at least 60 s after its end
MIN_QUOTE_SPREAD_C = 3.0                     # under this the buy and the sell would meet: no quote
MAKER_RATE, MAKER_FEE_TYPE = 0.0175, "quadratic_with_maker_fees"
OUTCOME_WAIT_S = 3600                        # a period is complete an hour after its last day ends
AFTER_S = 2 * HORIZON_S + 2 * REF_S          # how far after a decision its outcomes can reach
BOOTSTRAP, SEED = 2000, 20261113

DEFAULT_DIR = REPO / "artifacts" / "orderbook_eval"
EVAL_DB, LOCK_FILE = "orderbook_eval.sqlite", "writer.lock"
SPLIT_BY = {"up_15m": "sign of the state's mid_change_15m_cents",
            "down_15m": "sign of the state's mid_change_15m_cents",
            "move_15m": "a minute of the last 15 at least half the spread now from the mid now",
            "toxic_15m": "the state's imbalance_top leans to the first trade's taker side"}
DEV_LABEL = ("DEVELOPMENT PERIOD: may be looked at to find faults; yardsticks fitted in-sample; nothing here is the "
             "test")


class Refused(Exception):
    """A command that may not run now; nothing was written to the evaluation's results."""


def canon(x) -> str:
    return json.dumps(x, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False, default=str)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def code_hashes() -> dict:
    """SHA-256 of the source of this module and of the collector whose functions it uses."""
    return {"orderbook_eval": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "orderbook_loop": hashlib.sha256(Path(ol.__file__).read_bytes()).hexdigest()}


# ============================================================================ the record, read-only

def open_record(path) -> ol.Store:
    """The collector's record opened read-only (`mode=ro`, and `query_only`), as an `orderbook_loop.Store` so that
    its own `warm_time` and `day_status` run on it unchanged. A missing file is refused, never created."""
    p = Path(path).resolve()
    if not p.is_file():
        raise Refused("no collector record at %s" % p)
    conn = sqlite3.connect("file:%s?mode=ro" % urllib.parse.quote(str(p)), uri=True)
    conn.execute("PRAGMA query_only = ON")
    store = ol.Store.__new__(ol.Store)          # no __init__: it would create tables and set the journal mode
    store.path, store.conn = p, conn
    return store


def collection_start(store) -> float:
    r = store.conn.execute("SELECT MIN(ts) FROM events WHERE kind = 'start'").fetchone()[0]
    if r is None:
        raise Refused("the record has no `start` event: collection has not begun")
    return float(r)


# ============================================================================ days and periods

def day_of(ts: float) -> date:
    return datetime.fromtimestamp(ts, UTC).date()


def day_start(d: date) -> float:
    return datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp()


def day_end(d: date) -> float:
    return day_start(d + timedelta(days=1))


def periods(start_ts: float) -> dict:
    """The first full UTC day (the first that begins at or after the collection's start), the 5 development days,
    and the test period's first day, its 14th and its 21st."""
    d = day_of(start_ts)
    first = d if day_start(d) >= start_ts else d + timedelta(days=1)
    tf = first + timedelta(days=DEV_DAYS)
    return {"collection_start_ts": start_ts, "first_full_day": first,
            "development": [first + timedelta(days=k) for k in range(DEV_DAYS)], "test_first": tf,
            "test_last": tf + timedelta(days=TEST_DAYS - 1), "test_max_last": tf + timedelta(days=TEST_MAX_DAYS - 1)}


def period_of(per: dict, d: date) -> str:
    if d < per["first_full_day"]:
        return "before"
    if d < per["test_first"]:
        return "development"
    if d <= per["test_last"]:
        return "test"
    if d <= per["test_max_last"]:
        return "test_extension"
    return "after"


def counts(expected: int, collected: int) -> bool:
    """At least 80% of the day's decision slots collected, in integers (exactly 80% counts)."""
    return expected > 0 and COUNT_DEN * collected >= COUNT_NUM * expected


def day_table(store, now: float) -> dict:
    """Per UTC day, from the collector's own `day_status`: slots expected and collected, less any slot answered under
    another battery (so the share can only fall), the share and whether the day counts."""
    other: dict = {}
    for ticker, slot, status, answers, battery in store.conn.execute(
            "SELECT ticker, slot_ts, status, answers_json, battery_id FROM decisions WHERE forced = 0"):
        if battery != BATTERY_IN_FORCE and (status == "ok" or (status == "late" and answers is not None)):
            other.setdefault(day_of(slot), set()).add((ticker, slot))
    out = {}
    for r in ol.day_status(store, now):
        d = date.fromisoformat(r["day"])
        off = len(other.get(d, ()))
        got = max(0, r["collected"] - off)
        out[d] = {"day": d, "expected": r["expected"], "collected": got, "other_battery": off,
                  "share": got / r["expected"] if r["expected"] else None, "counts": counts(r["expected"], got),
                  "ended": now >= day_end(d), "statuses": r["statuses"]}
    return out


def counted_days(table: dict, days: Iterable[date]) -> list:
    return [d for d in days if d in table and table[d]["counts"] and table[d]["ended"]]


def test_end(per: dict, counted: set, now: float) -> tuple:
    """(the test period's last day, whether it reached 10 counted days); (None, False) while it is not complete.
    It ends on its 14th day if 10 have counted by then, else on the first later day that brings the count to 10, and
    at the latest on its 21st day."""
    tf = per["test_first"]
    for k in range(TEST_DAYS - 1, TEST_MAX_DAYS):
        d = tf + timedelta(days=k)
        if now < day_end(d) + OUTCOME_WAIT_S:
            return None, False
        if sum(1 for j in range(k + 1) if tf + timedelta(days=j) in counted) >= TEST_MIN_COUNTED:
            return d, True
    return per["test_max_last"], False


# ============================================================================ tape cover (note 3)

class Reads:
    """The successful trade reads of one market (from its snapshots). Each covers the trades created from its
    `trades_min_ts` (where it asked from) to its start less TAPE_LAG_S (a trade reaches the tape up to a minute late),
    the collector's span (`_covered`: from `trades_min_ts` to the start) less the lag; the spans of all successful
    reads are merged where they touch or overlap, as `_covered` chains them."""

    def __init__(self, snaps: Iterable[dict]):
        spans = sorted((s["trades_min_ts"], s["trades_start_ts"] - TAPE_LAG_S) for s in snaps
                       if s.get("trades_ok") and s.get("trades_min_ts") is not None
                       and s.get("trades_start_ts") is not None
                       and s["trades_min_ts"] <= s["trades_start_ts"] - TAPE_LAG_S)
        merged: list = []
        for lo, hi in spans:
            if merged and lo <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], hi)
            else:
                merged.append([lo, hi])
        self.los, self.his = [m[0] for m in merged], [m[1] for m in merged]

    def covers(self, s: float, e: float) -> bool:
        """Whether the merged spans cover the whole stretch [s, e] without a hole."""
        k = bisect.bisect_right(self.los, s) - 1
        return k >= 0 and self.his[k] >= e


# ============================================================================ outcomes

def _near(after: list, target: float) -> dict | None:
    """The collector's reference rule over the snapshots after the decision (sorted by book time)."""
    ends = [s["book_end_ts"] for s in after]
    lo, hi = bisect.bisect_left(ends, target - REF_S), bisect.bisect_right(ends, target + REF_S)
    return ol.reference(after[lo:hi], target)


def _mid_at(after: list, target: float) -> float | None:
    r = _near(after, target)
    return None if r is None else r["mid"]


def forward_mids(after: list, a: float, minutes: int) -> list | None:
    """The mids of minutes 1..n after a, by the collector's window rule (minute k: the snapshot nearest a + 60k
    within 30 s, each snapshot used once, minute 1 first); None unless every minute has a two-sided book."""
    ends = [s["book_end_ts"] for s in after]
    used: set = set()
    out = []
    for k in range(1, minutes + 1):
        t = a + 60 * k
        lo, hi = bisect.bisect_left(ends, t - REF_S), bisect.bisect_right(ends, t + REF_S)
        ref = ol.reference([s for n, s in enumerate(after[lo:hi], lo) if n not in used], t)
        if ref is None or ref["mid"] is None:
            return None
        used.add(next(n for n in range(lo, hi) if after[n] is ref))
        out.append(ref["mid"])
    return out


def mid_outcomes(mid0, spread0, prev30, a, after: list) -> dict:
    """`up_15m`, `down_15m`, `move_15m` and `regime` from the mid and spread now (the state's), the state's
    `mid_change_30m_cents`, the now snapshot's book time a and the valid snapshots begun after the decision."""
    out = {"up_15m": None, "down_15m": None, "move_15m": None, "regime": None}
    if mid0 is None or a is None:
        return out
    m15 = _mid_at(after, a + HORIZON_S)
    if m15 is not None:
        out["up_15m"], out["down_15m"] = m15 > mid0, m15 < mid0
    if spread0 is not None:
        p15 = forward_mids(after, a, HORIZON_S // 60)
        if p15 is not None:
            out["move_15m"] = max(abs(m - mid0) for m in p15) >= spread0 / 2 - EPS
    m30 = _mid_at(after, a + REGIME_S)
    p30 = forward_mids(after, a, REGIME_S // 60)
    if m30 is not None and p30 is not None and prev30 is not None:
        d = m30 - mid0
        if abs(d) <= QUIET_END_C + EPS and all(abs(m - mid0) <= QUIET_BAND_C + EPS for m in p30):
            out["regime"] = "quiet"
        elif ((prev30 > 0 and d >= TREND_C - EPS) or (prev30 < 0 and d <= -TREND_C + EPS)
              or (prev30 == 0 and abs(d) >= TREND_FLAT_C - EPS)):
            out["regime"] = "trending"
        else:
            out["regime"] = "mean_reverting"
    return out


def toxic_outcome(mid0, a, decision_ts: float, after: list, trades: list) -> tuple:
    """(`toxic_15m`, the first trade's taker side): (None, None) without a trade in (decision, decision + 15 min] or
    when its first moment has trades of both sides; (None, side) when the mids around it are not known."""
    win = [x for x in trades if decision_ts < x["created_ts"] <= decision_ts + HORIZON_S]
    if not win:
        return None, None
    t1 = min(x["created_ts"] for x in win)
    sides = {x["taker_side"] for x in win if x["created_ts"] == t1}
    if len(sides) != 1 or not sides <= {"yes", "no"}:
        return None, None
    side = sides.pop()
    before = [s for s in after if s["book_end_ts"] < t1]
    b_ts, b_mid = (before[-1]["book_end_ts"], before[-1]["mid"]) if before else (a, mid0)
    if b_ts is None or b_mid is None or t1 - b_ts > STALE_S:
        return None, side
    m = _mid_at(after, t1 + HORIZON_S)
    if m is None:
        return None, side
    return (m > b_mid if side == "yes" else m < b_mid), side


# ============================================================================ paper quoting

def quote_prices(mid0, spread0) -> tuple | None:
    """(buy, sell) in cents: one cent above the best bid and one below the best ask of the state; None when the book
    was one-sided or the spread is under 3 cents."""
    if mid0 is None or spread0 is None or spread0 < MIN_QUOTE_SPREAD_C - EPS:
        return None
    bid, ask = mid0 - spread0 / 2, mid0 + spread0 / 2
    return round(bid + 1, 4), round(ask - 1, 4)


def quoting_rules(state: dict, answers: dict) -> dict:
    spread, imb, n5 = state.get("spread_cents"), state.get("imbalance_top"), state.get("trades_5m")
    plain = (spread is not None and spread >= PLAIN_MIN_SPREAD_C - EPS and imb is not None
             and PLAIN_IMB_LO <= imb <= PLAIN_IMB_HI and n5 is not None and n5 < PLAIN_MAX_TRADES)
    qe = (answers.get("quote_env") or {}).get("score")
    mv = (answers.get("move_15m") or {}).get("noul")
    model = qe is not None and mv is not None and qe >= MODEL_MIN_QUOTE_ENV and mv < MODEL_MAX_MOVE
    return {"always": True, "plain": bool(plain), "model": bool(model)}


def find_fills(quote: tuple, decision_ts: float, trades: list) -> list:
    """The fills of the two resting quotes in (decision, decision + 5 min], from trades sorted by creation: a buy on
    the first trade whose taker sold at or below its price, a sell on the first whose taker bought at or above."""
    buy, sell = quote
    win = [x for x in trades if decision_ts < x["created_ts"] <= decision_ts + QUOTE_S and x["price_c"] is not None]
    out = []
    b = next((x for x in win if x["taker_side"] == "no" and x["price_c"] <= buy + EPS), None)
    s = next((x for x in win if x["taker_side"] == "yes" and x["price_c"] >= sell - EPS), None)
    if b is not None:
        out.append({"side": "buy", "ts": b["created_ts"], "price_c": buy})
    if s is not None:
        out.append({"side": "sell", "ts": s["created_ts"], "price_c": sell})
    return out


def value_fills(fills: list, after: list) -> list | None:
    """Each fill with the mid 15 minutes after it and its value before fees (cents); None when one cannot be valued."""
    out = []
    for f in fills:
        m = _mid_at(after, f["ts"] + VALUE_S)
        if m is None:
            return None
        gross = m - f["price_c"] if f["side"] == "buy" else f["price_c"] - m
        out.append(dict(f, mid_after=m, gross_c=round(gross, 4)))
    return out


def maker_fee_c(price_c: float, multiplier: float = 1.0) -> float:
    """One contract's maker fee in cents: M x 0.0175 x P x (1 - P) dollars, rounded up to a whole cent."""
    p = price_c / 100.0
    return float(math.ceil(round(multiplier * MAKER_RATE * p * (1 - p) * 100.0, 6)))


def fill_fee_c(variant: str, price_c: float, fee_type: str | None, multiplier: float | None) -> float:
    if variant == "none":
        return 0.0
    if variant == "series":
        if fee_type is not None and fee_type != MAKER_FEE_TYPE:
            return 0.0
        return maker_fee_c(price_c, 1.0 if multiplier is None else multiplier)
    if variant == "everywhere":
        return maker_fee_c(price_c, multiplier if multiplier else 1.0)
    raise ValueError("fee variant must be one of %s" % (VARIANTS,))


# ============================================================================ the observations

def _sign_group(x) -> str:
    return "unknown" if x is None else "up" if x > 0 else "down" if x < 0 else "flat"


def _lean_group(side, imb) -> str | None:
    if side is None:
        return None
    if imb is None:
        return "unknown"
    return "leans" if (side == "yes" and imb > 0.5) or (side == "no" and imb < 0.5) else "not"


def _move_group(before: list, mid0, spread0) -> str:
    if mid0 is None or spread0 is None or not before:
        return "unknown"
    w = ol.window(before, HORIZON_S // 60)
    if w is None:
        return "unknown"
    mids = [s["mid"] for k, s in w.items() if k > 0 and s["mid"] is not None]
    if not mids:
        return "unknown"
    return "moved" if max(abs(m - mid0) for m in mids) >= spread0 / 2 - EPS else "still"


class Tape:
    """One market's snapshots (every watch of it and its tails) and trades, read once."""

    def __init__(self, conn, ticker: str, trades: bool = True):
        snaps = []
        for bs, be, en, mid, state, bad, ok, mn, ts in conn.execute(
                "SELECT book_start_ts, book_end_ts, end_ts, mid, book_state, invalid_reason, trades_ok, trades_min_ts, "
                "trades_start_ts FROM snapshots WHERE ticker = ? ORDER BY book_end_ts, id", (ticker,)):
            invalid = bad is not None or state == "invalid"
            snaps.append({"book_start_ts": bs, "book_end_ts": be, "end_ts": en, "invalid": invalid,
                          "mid": round(mid * 100.0, 4) if mid is not None and not invalid and state == "two_sided"
                          else None, "trades_ok": ok, "trades_min_ts": mn, "trades_start_ts": ts})
        self.valid = [s for s in snaps if not s["invalid"]]
        self.ends = [s["book_end_ts"] for s in self.valid]
        self.reads = Reads(snaps)
        self.trades = []
        if trades:
            self.trades = [{"created_ts": c, "taker_side": side, "price_c": None if p is None else round(p * 100.0, 4),
                            "trade_id": tid} for c, side, p, tid in conn.execute(
                "SELECT created_ts, taker_side, yes_price, trade_id FROM trades WHERE ticker = ? "
                "ORDER BY created_ts, trade_id", (ticker,))]
        self.created = [x["created_ts"] for x in self.trades]

    def now_index(self, t: float) -> int:
        """The newest valid snapshot finished before t (the collector's "now"), or -1."""
        i = bisect.bisect_left(self.ends, t) - 1
        while i >= 0 and self.valid[i]["end_ts"] >= t:
            i -= 1
        return i

    def after(self, t: float) -> list:
        j = bisect.bisect_right(self.ends, t)
        k = bisect.bisect_right(self.ends, t + AFTER_S)
        return [s for s in self.valid[j:k] if s["book_start_ts"] > t]

    def before(self, i: int, t: float) -> list:
        a = self.valid[i]["book_end_ts"]
        lo = bisect.bisect_left(self.ends, a - HORIZON_S - REF_S)
        return [s for s in self.valid[lo:i + 1] if s["end_ts"] < t]

    def trades_in(self, t0: float, t1: float) -> list:
        return self.trades[bisect.bisect_right(self.created, t0):bisect.bisect_right(self.created, t1)]

    def observe(self, row: tuple, n: Counter) -> dict:
        _, ticker, slot, dts, _, _, state_json, answers_json, fee_type, mult = row
        state, answers = json.loads(state_json), json.loads(answers_json)
        mid0, spread0 = state.get("mid_cents"), state.get("spread_cents")
        o = {"day": day_of(slot).isoformat(), "ticker": ticker, "slot_ts": slot, "decision_ts": dts, "excluded": None,
             "outcomes": dict.fromkeys(QUESTIONS), "regime": None,
             "probs": {q: (answers.get(q) or {}).get("noul") for q in QUESTIONS},
             "regime_answer": (answers.get("regime") or {}).get("choice"), "groups": dict.fromkeys(QUESTIONS),
             "toxic_cover": False, "policy": {"status": "excluded", "fills": [], "rules": quoting_rules(state, answers),
                                              "fee_type": fee_type, "fee_multiplier": mult}}
        i = self.now_index(dts)
        now_mid = self.valid[i]["mid"] if i >= 0 else None
        if i < 0 or (mid0 is None) != (now_mid is None) or (mid0 is not None and abs(now_mid - mid0) > 0.05):
            n["now_mismatch"] += 1
            o["excluded"] = "now_mismatch"
            return o
        a = self.valid[i]["book_end_ts"]
        after = self.after(dts)
        mo = mid_outcomes(mid0, spread0, state.get("mid_change_30m_cents"), a, after)
        o["regime"] = mo.pop("regime")
        o["outcomes"].update(mo)
        o["toxic_cover"] = self.reads.covers(dts, dts + HORIZON_S)
        side = None
        if o["toxic_cover"]:
            o["outcomes"]["toxic_15m"], side = toxic_outcome(mid0, a, dts, after, self.trades_in(dts, dts + HORIZON_S))
        g15 = _sign_group(state.get("mid_change_15m_cents"))
        o["groups"].update(up_15m=g15, down_15m=g15, move_15m=_move_group(self.before(i, dts), mid0, spread0),
                           toxic_15m=_lean_group(side, state.get("imbalance_top")))
        p = o["policy"]
        if not self.reads.covers(dts, dts + QUOTE_S):
            p["status"] = "no_cover"
            return o
        quote = quote_prices(mid0, spread0)
        if quote is None:
            p["status"] = "no_quote"
            return o
        valued = value_fills(find_fills(quote, dts, self.trades_in(dts, dts + QUOTE_S)), after)
        if valued is None:
            p["status"] = "unvalued"
            return o
        p["status"], p["fills"] = "quoted", valued
        return o


def _decisions(store, days: list) -> tuple:
    """The decisions of these days that are scored, grouped by market, and the count of each one left out."""
    n: Counter = Counter()
    by: dict = {}
    if not days:
        return by, n
    dayset = set(days)
    seen: set = set()
    for row in store.conn.execute(
            "SELECT d.id, d.ticker, d.slot_ts, d.decision_ts, d.status, d.battery_id, d.state_json, d.answers_json, "
            "m.fee_type, m.fee_multiplier FROM decisions d LEFT JOIN markets m ON m.watch_id = d.watch_id "
            "WHERE d.forced = 0 AND d.slot_ts >= ? AND d.slot_ts < ? ORDER BY d.ticker, d.slot_ts, d.id",
            (day_start(min(days)), day_end(max(days)))):
        _, ticker, slot, _, status, battery, state_json, answers_json = row[:8]
        if day_of(slot) not in dayset:
            continue
        if battery != BATTERY_IN_FORCE:
            n["other_battery"] += 1
        elif status != "ok" or answers_json is None or state_json is None:
            n["not_answered"] += 1
        elif (ticker, slot) in seen:
            n["duplicate"] += 1
        else:
            seen.add((ticker, slot))
            by.setdefault(ticker, []).append(row)
    return by, n


def observe(store, days: Iterable[date]) -> dict:
    """Every scored decision of these days with its outcomes, answers, yardstick groups and paper quotes."""
    by, n = _decisions(store, sorted(set(days)))
    obs = []
    for ticker, rows in sorted(by.items()):
        tape = Tape(store.conn, ticker)
        obs += [tape.observe(r, n) for r in rows]
    obs.sort(key=lambda o: (o["slot_ts"], o["ticker"]))
    n["scored"] = len(obs)
    return {"obs": obs, "counts": dict(sorted(n.items()))}


def cover_by_day(store, days: Iterable[date]) -> dict:
    """For `status`: per day, the scored slots, and how many lack tape cover for a paper fill and for `toxic_15m`.
    Reads no answer and no outcome."""
    by, _ = _decisions(store, sorted(set(days)))
    out: dict = {}
    for ticker, rows in sorted(by.items()):
        reads = Tape(store.conn, ticker, trades=False).reads
        for r in rows:
            d = out.setdefault(day_of(r[2]), Counter())
            d["slots"] += 1
            d["no_cover_fill"] += not reads.covers(r[3], r[3] + QUOTE_S)
            d["no_cover_toxic"] += not reads.covers(r[3], r[3] + HORIZON_S)
    return out


# ============================================================================ yardsticks, judgment, policy

def fit_yardsticks(obs: list) -> dict:
    """For each yes/no question, on the decisions given whose outcome is known: (a) the plain rate it came true and
    (b) that rate by group."""
    out = {}
    for q in QUESTIONS:
        rows = [(o["outcomes"][q], o["groups"][q]) for o in obs if not o["excluded"] and o["outcomes"][q] is not None]
        groups: dict = {}
        for y, g in rows:
            groups.setdefault(g, []).append(1.0 if y else 0.0)
        out[q] = {"n": len(rows), "plain": (sum(1.0 for y, _ in rows if y) / len(rows)) if rows else None,
                  "split_by": SPLIT_BY[q],
                  "split": {g: {"n": len(v), "rate": sum(v) / len(v)} for g, v in sorted(groups.items())}}
    return out


def _t(xs: list) -> float | None:
    if len(xs) < 2:
        return None
    sd = statistics.stdev(xs)
    if sd == 0:
        return None
    return statistics.mean(xs) / (sd / math.sqrt(len(xs)))


def _auc(pos: np.ndarray, neg: np.ndarray) -> float | None:
    """AUC from counts of true and false outcomes at each answer level (levels ascending); ties count half."""
    npos, nneg = float(pos.sum()), float(neg.sum())
    if npos == 0 or nneg == 0:
        return None
    below = np.cumsum(neg) - neg
    return float((pos * (below + 0.5 * neg)).sum() / (npos * nneg))


def _ranking(rows: list, resamples: int, rng) -> tuple:
    """The AUC of (day, p, y) rows, and its 95% range resampling whole days (see the module docstring)."""
    levels = sorted({p for _, p, _ in rows})
    li = {p: k for k, p in enumerate(levels)}
    days = sorted({d for d, _, _ in rows})
    di = {d: k for k, d in enumerate(days)}
    pos, neg = np.zeros((len(days), len(levels))), np.zeros((len(days), len(levels)))
    for d, p, y in rows:
        (pos if y else neg)[di[d], li[p]] += 1
    point = _auc(pos.sum(axis=0), neg.sum(axis=0))
    draws = rng.integers(0, len(days), size=(resamples, len(days)))
    vals, dropped = [], 0
    for row in draws:
        w = np.bincount(row, minlength=len(days)).astype(float)
        v = _auc(w @ pos, w @ neg)
        if v is None:
            dropped += 1
        else:
            vals.append(v)
    rng95 = [float(x) for x in np.percentile(vals, [2.5, 97.5])] if vals else None
    return point, rng95, dropped


def judgment(obs: list, fit: dict, *, resamples: int = BOOTSTRAP, seed: int = SEED) -> dict:
    out = {}
    for qi, q in enumerate(QUESTIONS):
        f = fit[q]
        rows = [(o["day"], o["probs"][q], 1.0 if o["outcomes"][q] else 0.0, o["groups"][q]) for o in obs
                if not o["excluded"] and o["outcomes"][q] is not None and o["probs"][q] is not None]
        r: dict = {"n": len(rows), "days": len({x[0] for x in rows}), "split_by": SPLIT_BY[q]}
        if q == "toxic_15m":
            r["left_out_no_cover"] = sum(1 for o in obs if not o["excluded"] and not o["toxic_cover"])
        if not rows or f["plain"] is None:
            r["pass"] = False
            out[q] = r
            continue

        def yard(g):
            s = f["split"].get(g)
            return s["rate"] if s else f["plain"]
        bm = [(p - y) ** 2 for _, p, y, _ in rows]
        bp = [(f["plain"] - y) ** 2 for _, _, y, _ in rows]
        bs = [(yard(g) - y) ** 2 for _, _, y, g in rows]
        per_day: dict = {}
        for k, (d, _, _, _) in enumerate(rows):
            per_day.setdefault(d, []).append(k)
        dp = [statistics.mean(bp[k] - bm[k] for k in ks) for _, ks in sorted(per_day.items())]
        ds = [statistics.mean(bs[k] - bm[k] for k in ks) for _, ks in sorted(per_day.items())]
        point, rng95, dropped = _ranking([(d, p, y) for d, p, y, _ in rows], resamples,
                                         np.random.default_rng([seed, qi]))
        r.update(brier_model=statistics.mean(bm), brier_plain=statistics.mean(bp), brier_split=statistics.mean(bs),
                 t_vs_plain=_t(dp), t_vs_split=_t(ds), ranking=point, ranking_95=rng95, resamples=resamples,
                 resamples_dropped=dropped, seed=[seed, qi])
        r["pass"] = bool(r["brier_model"] < r["brier_plain"] and r["brier_model"] < r["brier_split"]
                         and r["t_vs_plain"] is not None and r["t_vs_plain"] >= T_LINE
                         and r["t_vs_split"] is not None and r["t_vs_split"] >= T_LINE
                         and rng95 is not None and rng95[0] > RANK_LINE)
        out[q] = r
    return out


def regime_report(obs: list) -> dict:
    rows = [o for o in obs if not o["excluded"] and o["regime"] is not None]
    hit = sum(1 for o in rows if o["regime_answer"] == o["regime"])
    return {"n": len(rows), "accuracy": hit / len(rows) if rows else None,
            "outcomes": dict(sorted(Counter(o["regime"] for o in rows).items())),
            "answers": dict(sorted(Counter(str(o["regime_answer"]) for o in rows).items())),
            "note": "not part of the pass line"}


def _slot_value(o: dict, variant: str) -> float:
    p = o["policy"]
    return sum(f["gross_c"] - fill_fee_c(variant, f["price_c"], p["fee_type"], p["fee_multiplier"]) for f in p["fills"])


def policy(obs: list) -> dict:
    live = [o for o in obs if not o["excluded"]]
    st = Counter(o["policy"]["status"] for o in live)
    slots = [o for o in live if o["policy"]["status"] in ("quoted", "no_quote")]
    out = {"slots": len(slots), "left_out_no_cover": st["no_cover"], "left_out_unvalued": st["unvalued"],
           "no_quote_possible": st["no_quote"], "variants": {}}
    for v in VARIANTS:
        vals: dict = {r: [] for r in RULES}
        daily: dict = {}
        quoted, fills = Counter(), Counter()
        for o in slots:
            x = _slot_value(o, v)
            for r in RULES:
                on = o["policy"]["rules"][r] and o["policy"]["status"] == "quoted"
                quoted[r] += on
                fills[r] += len(o["policy"]["fills"]) if on else 0
                y = x if o["policy"]["rules"][r] else 0.0
                vals[r].append(y)
                daily.setdefault(o["day"], {rr: [] for rr in RULES})[r].append(y)
        res: dict = {"rules": {r: {"slots": len(vals[r]), "quoted": quoted[r], "fills": fills[r],
                                   "mean": statistics.mean(vals[r]) if vals[r] else None} for r in RULES}}
        for other in ("always", "plain"):
            ds = [statistics.mean(daily[d]["model"]) - statistics.mean(daily[d][other]) for d in sorted(daily)]
            res["model_minus_" + other] = {"mean_daily": statistics.mean(ds) if ds else None, "t": _t(ds),
                                           "days": len(ds)}
        m = {r: res["rules"][r]["mean"] for r in RULES}
        res["pass"] = bool(m["model"] is not None and m["model"] > 0 and m["model"] > m["always"]
                           and m["model"] > m["plain"]
                           and all(res["model_minus_" + o]["t"] is not None and res["model_minus_" + o]["t"] >= T_LINE
                                   for o in ("always", "plain")))
        out["variants"][v] = res
    return out


def pass_line(judg: dict, pol: dict, reached: bool) -> dict:
    passing = [q for q in QUESTIONS if judg[q]["pass"]]
    j, p = len(passing) >= MIN_PASSING, pol["variants"]["series"]["pass"]
    return {"reached_10_counted_days": reached, "questions_passing": passing, "judgment": j, "policy": p,
            "passes": bool(reached and j and p)}


def compute(store, days: list, fit: dict, *, resamples: int = BOOTSTRAP, seed: int = SEED) -> dict:
    ob = observe(store, days)
    return {"judgment": judgment(ob["obs"], fit, resamples=resamples, seed=seed), "policy": policy(ob["obs"]),
            "regime": regime_report(ob["obs"]), "counts": ob["counts"]}


# ============================================================================ the evaluation's own store

_EVAL_TABLES = ("fit", "result", "log")
_EVAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS fit (id INTEGER PRIMARY KEY CHECK (id = 1), at TEXT NOT NULL, record TEXT NOT NULL,
    collection_start_ts REAL NOT NULL, fitted_json TEXT NOT NULL, fitted_sha256 TEXT NOT NULL, code_json TEXT NOT NULL,
    code_sha256 TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS result (id INTEGER PRIMARY KEY CHECK (id = 1), at TEXT NOT NULL, fitted_sha256 TEXT NOT NULL,
    code_sha256 TEXT NOT NULL, result_json TEXT NOT NULL, result_sha256 TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS log (id INTEGER PRIMARY KEY, at TEXT NOT NULL, command TEXT NOT NULL,
    detail_json TEXT NOT NULL);
"""


class EvalStore:
    """The evaluation's own store: append-only (triggers refuse UPDATE and DELETE), one writer (an exclusive lock on
    the folder's lock file for as long as it is open)."""

    def __init__(self, folder):
        self.dir = Path(folder)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._lock = open(self.dir / LOCK_FILE, "a")
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._lock.close()
            raise Refused("another evaluation run holds %s: one writer at a time" % (self.dir / LOCK_FILE)) from None
        self.conn = sqlite3.connect(str(self.dir / EVAL_DB))
        self.conn.executescript(_EVAL_SCHEMA)
        for table in _EVAL_TABLES:
            for verb in ("UPDATE", "DELETE"):
                self.conn.execute("CREATE TRIGGER IF NOT EXISTS no_%s_%s BEFORE %s ON %s BEGIN SELECT RAISE(ABORT, "
                                  "'append-only: no %s on %s'); END" % (verb.lower(), table, verb, table, verb, table))
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
        self._lock.close()

    def fit(self) -> dict | None:
        r = self.conn.execute("SELECT at, record, collection_start_ts, fitted_json, fitted_sha256, code_json, "
                              "code_sha256 FROM fit WHERE id = 1").fetchone()
        if r is None:
            return None
        if _sha(r[3]) != r[4]:
            raise Refused("the stored fit no longer matches its hash; look at %s" % (self.dir / EVAL_DB))
        return {"at": r[0], "record": r[1], "collection_start_ts": r[2], "fitted": json.loads(r[3]),
                "fitted_sha256": r[4], "code": json.loads(r[5]), "code_sha256": r[6]}

    def result(self) -> dict | None:
        r = self.conn.execute("SELECT at, result_json, result_sha256 FROM result WHERE id = 1").fetchone()
        if r is None:
            return None
        if _sha(r[1]) != r[2]:
            raise Refused("the stored result no longer matches its hash; look at %s" % (self.dir / EVAL_DB))
        return {"at": r[0], "result": json.loads(r[1]), "result_sha256": r[2]}

    def log(self, at: str, command: str, detail: dict) -> None:
        with self.conn:
            self.conn.execute("INSERT INTO log (at, command, detail_json) VALUES (?, ?, ?)",
                              (at, command, canon(detail)))

    def log_entries(self) -> list:
        return [{"at": a, "command": c, "detail": json.loads(d)} for a, c, d in
                self.conn.execute("SELECT at, command, detail_json FROM log ORDER BY id")]

    def put_fit(self, at: str, record: str, start: float, fitted: dict, code: dict) -> dict:
        fj = canon(fitted)
        with self.conn:
            self.conn.execute("INSERT INTO fit (id, at, record, collection_start_ts, fitted_json, fitted_sha256, "
                              "code_json, code_sha256) VALUES (1, ?, ?, ?, ?, ?, ?, ?)",
                              (at, record, start, fj, _sha(fj), canon(code), _sha(canon(code))))
        return self.fit()

    def put_result(self, at: str, fitted_sha: str, code_sha: str, result: dict) -> None:
        rj = canon(result)
        with self.conn:
            self.conn.execute("INSERT INTO result (id, at, fitted_sha256, code_sha256, result_json, result_sha256) "
                              "VALUES (1, ?, ?, ?, ?, ?)", (at, fitted_sha, code_sha, rj, _sha(rj)))


def _peek_eval(folder) -> dict:
    """What the evaluation's store holds, read without creating or locking it (for `status`)."""
    p = Path(folder) / EVAL_DB
    if not p.is_file():
        return {"fit": None, "result": None}
    c = sqlite3.connect("file:%s?mode=ro" % urllib.parse.quote(str(p.resolve())), uri=True)
    try:
        f = c.execute("SELECT at, fitted_sha256 FROM fit WHERE id = 1").fetchone()
        r = c.execute("SELECT at, result_sha256 FROM result WHERE id = 1").fetchone()
    finally:
        c.close()
    return {"fit": f, "result": r}


# ============================================================================ commands

def _iso(ts: float) -> str:
    return ol.iso(ts)


def cmd_status(db, eval_dir, now: float) -> str:
    rec = open_record(db)
    start = collection_start(rec)
    per = periods(start)
    table = day_table(rec, now)
    last = day_of(now)
    days = [day_of(start) + timedelta(days=k) for k in range((last - day_of(start)).days + 1)]
    cover = cover_by_day(rec, days)
    n_dec: Counter = Counter()
    for slot, status, battery in rec.conn.execute("SELECT slot_ts, status, battery_id FROM decisions WHERE forced = 0"):
        d = day_of(slot)
        n_dec[d, "all"] += 1
        n_dec[d, "ok"] += status == "ok" and battery == BATTERY_IN_FORCE
    held = _peek_eval(eval_dir)
    counted = set(counted_days(table, days))
    end, reached = test_end(per, counted, now)
    tf = per["test_first"]
    lines = ["collection started %s; first full day %s; development %s to %s; test from %s, 14 days (%s), at most 21 "
             "(%s)" % (_iso(start), per["first_full_day"], per["development"][0], per["development"][-1], tf,
                       per["test_last"], per["test_max_last"]),
             "a day counts at 80%% of its decision slots collected; counted so far: development %d of 5, test %d "
             "(needs 10); test period %s" % (
                 len(counted & set(per["development"])),
                 len([d for d in counted if tf <= d <= per["test_max_last"]]),
                 "complete, ends %s%s" % (end, "" if reached else " WITHOUT 10 counted days") if end else
                 "not complete"),
             "fit stored: %s; test result stored: %s" % (
                 "no" if held["fit"] is None else "yes (%s, %s)" % (held["fit"][0], held["fit"][1][:12]),
                 "no" if held["result"] is None else "yes (%s)" % held["result"][0]),
             "no tape cover: scored slots whose trade reads do not cover the 5 minutes of a paper fill (fill) or the "
             "15 minutes of toxic_15m (toxic), judged from the reads' spans",
             "%-10s  %-14s %8s %9s %7s %6s %9s %7s %7s %6s %7s" % (
                 "day", "period", "expected", "collected", "share", "counts", "decisions", "ok", "other", "fill",
                 "toxic")]
    for d in days:
        r = table.get(d, {"expected": 0, "collected": 0, "share": None, "counts": False, "ended": now >= day_end(d),
                          "other_battery": 0})
        c = cover.get(d, Counter())
        lines.append("%-10s  %-14s %8d %9d %7s %6s %9d %7d %7d %6d %7d" % (
            d.isoformat(), period_of(per, d), r["expected"], r["collected"],
            "-" if r["share"] is None else "%.1f%%" % (100 * r["share"]),
            ("yes" if r["counts"] else "no") if r["ended"] else "open", n_dec[d, "all"], n_dec[d, "ok"],
            r["other_battery"], c["no_cover_fill"], c["no_cover_toxic"]))
    return "\n".join(lines)


def _fit_due(per: dict, now: float) -> None:
    last = per["development"][-1]
    if now < day_end(last) + OUTCOME_WAIT_S:
        raise Refused("the development period (%s to %s) is not complete until %s; nothing was fitted"
                      % (per["development"][0], last, _iso(day_end(last) + OUTCOME_WAIT_S)))


def cmd_fit(db, eval_dir, now: float) -> dict:
    es = EvalStore(eval_dir)
    try:
        held = es.fit()
        if held is not None:
            raise Refused("a fit is already stored (%s, %s); it is made once" % (held["at"], held["fitted_sha256"]))
        rec = open_record(db)
        start = collection_start(rec)
        per = periods(start)
        _fit_due(per, now)
        table = day_table(rec, now)
        dev = counted_days(table, per["development"])
        if not dev:
            raise Refused("no development day counts (80% of its slots collected); nothing was fitted")
        ob = observe(rec, dev)
        fitted = {"questions": fit_yardsticks(ob["obs"]),
                  "development_days": [d.isoformat() for d in per["development"]], "counted_days": [d.isoformat() for d in dev], "counts": ob["counts"], "battery": BATTERY_IN_FORCE}
        held = es.put_fit(_iso(now), str(rec.path), start, fitted, code_hashes())
        es.log(_iso(now), "fit", {"fitted_sha256": held["fitted_sha256"], "code_sha256": held["code_sha256"],
                                  "record": str(rec.path)})
        return held
    finally:
        es.close()


def cmd_score(db, eval_dir, now: float, code_changed: str | None = None, *, resamples: int = BOOTSTRAP) -> dict:
    es = EvalStore(eval_dir)
    try:
        stored = es.result()
        if stored is not None:                           # scored once: shown, never recomputed, the record not read
            es.log(_iso(now), "score_shown", {"result_sha256": stored["result_sha256"]})
            return {"result": stored["result"], "scored_at": stored["at"], "result_sha256": stored["result_sha256"],
                    "note": "the stored result: the test period is scored once"}
        held = es.fit()
        if held is None:
            raise Refused("the test period is scored only with a stored fit (`fit` first); nothing was scored")
        code = code_hashes()
        changed = None
        if code != held["code"]:
            if not (code_changed and code_changed.strip()):
                raise Refused("the code differs from the fit's (%s): give --code-changed \"<reason>\" to score with "
                              "the changed code; nothing was scored" % ", ".join(
                                  k for k in code if code[k] != held["code"].get(k)))
            changed = {"reason": code_changed.strip(), "at_fit": held["code"], "now": code}
        rec = open_record(db)
        start = collection_start(rec)
        if abs(start - held["collection_start_ts"]) > 1e-6:
            raise Refused("this record's collection began at %s, the fit's at %s; nothing was scored"
                          % (_iso(start), _iso(held["collection_start_ts"])))
        per = periods(start)
        table = day_table(rec, now)
        span = [per["test_first"] + timedelta(days=k) for k in range(TEST_MAX_DAYS)]
        end, reached = test_end(per, set(counted_days(table, span)), now)
        if end is None:
            raise Refused("the test period is not complete (it ends no earlier than %s, extended until 10 days count, "
                          "and is scored %d s after its last day); nothing was scored"
                          % (per["test_last"], OUTCOME_WAIT_S))
        if changed is not None:
            es.log(_iso(now), "code_changed", changed)
        days = [d for d in span if d <= end]
        counted = counted_days(table, days)
        res = {"period": "test", "test_first": per["test_first"].isoformat(), "test_end": end.isoformat(),
               "reached": reached, "counted_days": [d.isoformat() for d in counted],
               "not_counted_days": [d.isoformat() for d in days if d not in counted],
               "fitted_sha256": held["fitted_sha256"], "code": code, "code_changed": changed,
               "battery": BATTERY_IN_FORCE, "seed": SEED, "resamples": resamples, "scored_at": _iso(now)}
        res.update(compute(rec, counted, held["fitted"]["questions"], resamples=resamples))
        res["pass_line"] = pass_line(res["judgment"], res["policy"], reached)
        res = json.loads(canon(res))
        es.put_result(_iso(now), held["fitted_sha256"], _sha(canon(code)), res)
        es.log(_iso(now), "score", {"result_sha256": _sha(canon(res)), "passes": res["pass_line"]["passes"]})
        return res
    finally:
        es.close()


def cmd_development(db, eval_dir, now: float, *, resamples: int = BOOTSTRAP) -> dict:
    es = EvalStore(eval_dir)
    try:
        rec = open_record(db)
        per = periods(collection_start(rec))
        table = day_table(rec, now)
        done = [d for d in per["development"] if now >= day_end(d) + OUTCOME_WAIT_S]
        dev = counted_days(table, done)
        if not dev:
            raise Refused("no complete development day counts yet; nothing to show")
        ob = observe(rec, dev)
        fit = fit_yardsticks(ob["obs"])
        res = {"period": "development", "label": DEV_LABEL, "counted_days": [d.isoformat() for d in dev],
               "not_counted_days": [d.isoformat() for d in done if d not in dev], "yardsticks_in_sample": fit,
               "judgment": judgment(ob["obs"], fit, resamples=resamples), "policy": policy(ob["obs"]),
               "regime": regime_report(ob["obs"]), "counts": ob["counts"], "seed": SEED, "resamples": resamples}
        res["pass_line_if_this_were_the_test"] = pass_line(res["judgment"], res["policy"], True)
        res = json.loads(canon(res))
        es.log(_iso(now), "development", {"result_sha256": _sha(canon(res))})
        return res
    finally:
        es.close()


def main(argv=None, *, clock: Callable[[], float] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="orderbook_eval", description="Evaluate the order-book loop as its prediction "
                                                                    "says.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("status", "fit", "score", "development"):
        p = sub.add_parser(name)
        p.add_argument("--db", required=True, help="the collector's record (opened read-only)")
        p.add_argument("--eval-dir", default=str(DEFAULT_DIR), help="the evaluation's own store")
        if name == "score":
            p.add_argument("--code-changed", default=None, help="why the code differs from the fit's (logged)")
    a = ap.parse_args(argv)
    now = (clock or time.time)()
    try:
        if a.cmd == "status":
            print(cmd_status(a.db, a.eval_dir, now))
            return 0
        if a.cmd == "fit":
            out = cmd_fit(a.db, a.eval_dir, now)
        elif a.cmd == "score":
            out = cmd_score(a.db, a.eval_dir, now, a.code_changed)
        else:
            out = cmd_development(a.db, a.eval_dir, now)
    except Refused as e:
        print("refused: %s" % e, file=sys.stderr)
        return 2
    print(json.dumps(out, indent=1, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
