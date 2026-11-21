"""The wallet screen of the forward copy test (T55a), run ONCE.

The specification is docs/research/2026-11-18-forward-copy-on-webull-prediction.md, section "The wallets (the screen),
fixed once" (locked; it is never edited here). This module reads the copy-leaders store READ ONLY (an SQLite
`mode=ro` URI) and computes, for each candidate wallet whose full trade history it holds, the figures of that
section over the window W, the four eligibility tests, the rank, and the set (the first 20 eligible wallets). It then
writes the set as the file the leaders' record takes (`leaders_monitor fix-leaders --wallets-file`) and a report, into
the output folder it is given and nowhere else.

    python -m valedia_trader.research_loop.copy_screen run --store FILE --out-dir DIR [--run-day YYYY-MM-DD]
        [--cutoff-month YYYY-MM --cutoff-not-derived-from-the-run-day] [--max-memory-mb MB]

Exit codes: 0 the screen ran (also when it finds that the test does not start: that is a finding, printed and in the
report), 2 refused (nothing written), 4 stopped by the memory limit (nothing written).

Where the locked document is silent or ambiguous, the readings taken (also in every report, `readings`):

* The command is this module's own `run`, beside `copy_leaders`' and `leaders_monitor`'s: copy_leaders.py and
  leaders_monitor.py are not changed (the screen reuses their functions: the trade and resolution readers, the
  average-cost realized-profit pass, the shared lock and the integrity check, the wallet-file reader).
* The cutoff. T47 is closed, so resolutions are read up to the end of the last complete calendar month before the
  RUN DAY (a UTC date, today by default); W is the twelve complete calendar months ending there. A resolution whose
  `resolved_ts` is before the cutoff (the first second of the next month) is a KNOWN resolution (read exactly as the
  store's rows have been read under T47's document and its five amendments: `resolution_from_row`, and a store whose
  oracle-shaped rows are not read under the fifth amendment is refused); any later one is not known, and the position
  stays at its cost. Trades from the cutoff on are not read. Any other cutoff needs `--cutoff-month` together with
  `--cutoff-not-derived-from-the-run-day` (the flag the lead names in the run record), and is then an earlier complete
  month only; the report says it was an override.
* Realized profit follows copy_leaders' average-cost pass (a sale removes cost in proportion; a resolution realizes the
  payout of the shares held less their cost; a sale beyond the holding earns nothing beyond it), with the trades before
  W read for the cost basis, and only the months of W taken. A month is the UTC calendar month of the sale or of the
  known resolution.
* A CLOSED position is one holding of one outcome token going to nothing: by a sale that sells the last of it, or by
  the known resolution of its market while it is held. A partial sale closes nothing. A token bought again after it
  closed and closed again is a second closed position. It belongs to the month of the closing sale or resolution. A
  month COUNTS when at least 5 positions closed in it.
* "Held to a known resolution" (test 4): each outcome token still held when its market's known resolution falls in W
  is one position (both tokens of a market, if both are held, are two). Its average price paid a share is its cost
  over its shares then held, taken in whole micro-dollars a share (halves up); the share it won is its payout a share
  over one dollar (a half payout is 0.5, any other fractional payout its fraction). "The share that won is ABOVE the
  mean price" is the sum of the won shares compared with the sum of the prices, exactly, in micro-units: equal is not
  above. The point-4 margin is (share won less mean price), exact.
* Shares traded (test 2) are the shares of every trade of W, buys and sells, in markets not yet resolved at the trade
  (copy_leaders' own rule: a trade at or after its market's resolution is ignored). "Fewer than 20 shares for each
  dollar of profit" is shares < 20 x profit, compared in micro-units without rounding.
* Markets traded in W (test 3) are the distinct markets (condition ids) with at least one counted trade in W; a market
  is one where both outcomes were bought when the wallet BOUGHT both outcome indices at any time in W. "At most 10%"
  is 10 x (such markets) <= (markets traded).
* The rank is the mean of the counted months' profit over their POPULATION standard deviation (divisor n). The
  document asks for "their standard deviation": the deviation of those numbers as a set, which is the divisor n; the
  twelve months are the whole of what the screen describes, not a sample from which a parent is estimated, and the
  divisor n does not penalize a wallet for having only 9 counted months against 12 (n - 1 would, unevenly). The ranking
  is done on the exact value (mean squared over variance, with its sign, in integers), so equal ratios are equal
  without floating point. No spread at all (every counted month equal): a positive mean ranks above every finite ratio
  (the ratio is unbounded), a zero mean is a ratio of zero, a negative mean ranks below every finite ratio. Ties go to
  the larger point-4 margin, and then to the wallet name (as copy_leaders breaks ties in profit); the report shows the
  ratio and its exact ingredients. Only eligible wallets are ranked.
* The set is the first 20 eligible wallets in rank order, written one address a line in that order, with no comment, so
  the record's reader (`leaders_monitor.read_wallets_file`) takes it as it is, which the run checks before it writes.
  Fewer than 5 eligible: the test does not start, the report says so, and no set file is written.
* The candidates are the wallets of the store's candidate table whose trade walk finished. A wallet whose walk ended at
  the page limit is named in the report as not screened; any other unfinished trade walk, or a traded market with no
  resolution answer, refuses the run (the pull is incomplete).
* One run: the command refuses when the set file OR the report already exists in the output folder (the report is
  written first and then the set file, so a run that stopped between the two is also spent, and says so).

Memory: one wallet at a time (the trades of a wallet are streamed twice through a cursor, the same plan as
copy_leaders' T47c reads); the state held is by token and market of that wallet only, freed before the next. The
resident memory is read after each wallet and above --max-memory-mb the run stops with nothing written.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from fractions import Fraction
from pathlib import Path

from valedia_trader.research_loop import copy_leaders as cl
from valedia_trader.research_loop import leaders_monitor as lm

UTC = timezone.utc
Refused = cl.Refused

TEST_DOC = "docs/research/2026-11-18-forward-copy-on-webull-prediction.md"
WINDOW_MONTHS = 12
MIN_CLOSED_IN_MONTH = 5                  # a month counts when the wallet closed at least this many positions in it
MIN_COUNTED_MONTHS = 9
MIN_CLOSED = 100
MAX_SHARES_PER_DOLLAR = 20               # fewer than this many shares traded for each dollar of profit
BOTH_PERCENT = 10                        # both outcomes bought in at most this share of the markets traded
MIN_HELD_RESOLVED = 50
SET_SIZE = 20
MIN_SET = 5
SET_FILE = "copy_screen_set.txt"
REPORT_FILE = "copy_screen_report.json"
OVERRIDE_FLAG = "--cutoff-not-derived-from-the-run-day"
MICRO = 1_000_000

READINGS = (
    "The command is this module's own `run`; copy_leaders.py and leaders_monitor.py are not changed.",
    "Cutoff: the end of the last complete UTC calendar month before the run day; W is the twelve complete months "
    "ending there; a resolution is known only when its resolved_ts is before the cutoff; trades from the cutoff on "
    "are not read; any other cutoff needs the named flag and an earlier complete month.",
    "Realized profit: copy_leaders' average-cost pass, trades before W read for the cost, the months of W taken; a "
    "month is the UTC month of the sale or of the known resolution; a position neither sold nor resolved counts at its "
    "cost.",
    "A closed position is one holding of one outcome token that went to nothing by the sale of its last shares or by "
    "its market's known resolution while held; a partial sale closes nothing; a token closed and bought and closed "
    "again counts twice; a month counts at 5 or more closed.",
    "Held to a known resolution: each outcome token held when its market's known resolution falls in W, one position "
    "each; price a share is cost over shares held then, in whole micro-dollars; won share is payout over one dollar "
    "(half is 0.5); the share won must be above the mean price (equal is not above), compared exactly.",
    "Shares traded: every buy and sell of W in a market not yet resolved at the trade; fewer than 20 a dollar is "
    "shares < 20 x profit, exact in micro-units.",
    "Markets traded: distinct markets with a counted trade in W; both outcomes bought: both outcome indices bought at "
    "any time in W; at most 10% is 10 x such markets <= markets traded.",
    "Rank: mean of the counted months' profit over their population standard deviation (divisor n), compared exactly; "
    "no spread: positive mean first, zero mean zero, negative mean last; ties by the larger point-4 margin, then by "
    "wallet name.",
    "The set is the first 20 eligible in rank order, one address a line, no comment; fewer than 5: the test does not "
    "start and no set file is written.",
    "Candidates are the store's candidate wallets with a finished trade walk; a walk ended at the page limit is named "
    "as not screened; any other unfinished walk or a traded market with no resolution answer refuses the run.",
)


def _today() -> date:
    return datetime.now(UTC).date()


def _say_err(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# ---------------------------------------------------------------- the day, the cutoff, the window
def last_complete_month(run_day: date) -> tuple[int, int]:
    """The last calendar month that is complete before `run_day`: the month before the run day's own."""
    return cl.add_months((run_day.year, run_day.month), -1)


def window_of(last_ym) -> list:
    """The twelve complete calendar months ending with `last_ym`."""
    first = cl.add_months(tuple(last_ym), -(WINDOW_MONTHS - 1))
    return [cl.add_months(first, k) for k in range(WINDOW_MONTHS)]


def cutoff_ts(last_ym) -> int:
    """The first second after `last_ym`: a resolution before it is known, nothing from it on is read."""
    return cl.month_start(*cl.add_months(tuple(last_ym), 1))


def _cutoff(run_day: date, today: date, cutoff_month, override: bool) -> dict:
    derived = last_complete_month(run_day)
    last = derived if cutoff_month is None else tuple(cutoff_month)
    names = {"run_day": run_day.isoformat(), "derived_last_month": cl.month_name(derived)}
    if run_day != today and not override:
        raise Refused("the run day %s is not today (%s): the cutoff would not be derived from the day the screen is run; "
                      "to run it so, name %s in the run record (nothing read, nothing written)"
                      % (run_day, today, OVERRIDE_FLAG))
    if last != derived and not override:
        raise Refused("the cutoff %s is not the end of the last complete month before the run day %s (%s): to run "
                      "with it, name %s in the run record (nothing read, nothing written)"
                      % (cl.month_name(last), run_day, cl.month_name(derived), OVERRIDE_FLAG))
    if last > derived:
        raise Refused("the cutoff %s is not a complete calendar month before the run day %s: no flag allows it "
                      "(nothing read, nothing written)" % (cl.month_name(last), run_day))
    derived_from_run_day = last == derived and run_day == today
    return {**names, "last_month": cl.month_name(last), "resolutions_before_ts": cutoff_ts(last),
            "derived_from_run_day": derived_from_run_day, "override": not derived_from_run_day, "_last": last}


# ---------------------------------------------------------------- one wallet
class _ScreenFold(cl._WalletFold):
    """copy_leaders' own pass over one wallet (its average-cost realized profit by month, its resolutions placed at
    their times), with what the screen adds: the positions closed by month, those held to a known resolution in W (with
    their payout and price), and the markets traded and bought in W. The new-position machinery is not used."""

    def __init__(self, tokens_of: dict, resolutions: dict, lo: int):
        super().__init__(tokens_of, resolutions, None)
        self.lo = lo
        self.closed: dict = defaultdict(int)
        self.traded: dict = {}                    # market -> bit mask of the outcome indices bought in W
        self.held_n = self.held_win = self.held_price = 0

    def _position(self, t) -> None:
        return None

    def _resolve(self, ts, cond) -> None:
        r = self.res[cond]
        for tok, oi in self.tokens_of[cond].items():
            held = self.held[tok]
            if held > 0:
                self.closed[cl._bucket(ts)] += 1
                if ts >= self.lo:
                    self.held_n += 1
                    self.held_win += r.payouts[oi]
                    self.held_price += cl.idiv_half_up(self.basis[tok] * MICRO, held)
        super()._resolve(ts, cond)

    def _trade(self, t) -> None:
        if t.condition in self.done:
            super()._trade(t)                     # counted by copy_leaders as a trade after the resolution, ignored
            return
        before = self.held[t.token]
        super()._trade(t)
        if self.held[t.token] == 0 and before > 0:
            self.closed[cl._bucket(t.ts)] += 1
        if t.ts >= self.lo:
            bit = (1 << t.outcome_index) if t.side == "BUY" else 0
            self.traded[t.condition] = self.traded.get(t.condition, 0) | bit


def _unknown(cond: str) -> cl.Resolution:
    return cl.Resolution(cond, "unresolved", None, None)


def screen_wallet(s, wallet: str, *, window, end: int, counts: Counter | None = None) -> dict:
    """One wallet's figures over `window` (twelve months), reading its trades before `end` through a cursor twice
    (once for its markets' tokens, once in time order) and its markets' resolutions (known only before `end`)."""
    counts = counts if counts is not None else Counter()
    lo = cl.month_start(*window[0])
    rows, tokens_of = 0, {}
    for r in s.trade_stream(wallet, end, 0):
        rows += 1
        t, why = cl.trade_from_row(wallet, r["seq"], r)
        if why:
            counts["trade_" + why] += 1
            continue
        tokens_of.setdefault(t.condition, {})[t.token] = t.outcome_index
    answered = s.resolution_answers(tokens_of)
    missing = sum(1 for c in tokens_of if c not in answered)
    if missing:
        raise Refused("no resolution answer for %d markets this wallet (%s) traded: the pull is unfinished (nothing "
                      "written)" % (missing, wallet[:10]))
    res = {}
    for cond, row in s.resolution_rows(tokens_of).items():
        r = cl.resolution_from_row(row)
        res[cond] = r if r.state == "unresolved" or r.resolved_ts < end else _unknown(cond)
    counts["resolution_rows_read"] += len(res)
    fold = _ScreenFold(tokens_of, res, lo)
    for r in s.trade_stream(wallet, end, 0, by_time=True):
        t, why = cl.trade_from_row(wallet, r["seq"], r)
        if not why:
            fold.add(t)
    fold.finish()
    b = fold.book
    months = []
    for ym in window:
        closed = fold.closed.get(ym, 0)
        months.append({"month": cl.month_name(ym), "profit_micro": b.realized.get(ym, 0), "closed": closed,
                       "counted": closed >= MIN_CLOSED_IN_MONTH})
    counted = [m["profit_micro"] for m in months if m["counted"]]
    f = {"wallet": wallet, "months": months, "counted_months": len(counted),
         "closed_positions": sum(m["closed"] for m in months), "profit_micro": sum(m["profit_micro"] for m in months),
         "shares_micro": sum(b.shares.get(ym, 0) for ym in window),
         "markets_traded": len(fold.traded), "markets_both": sum(1 for v in fold.traded.values() if v == 3),
         "held_resolved": fold.held_n, "win_sum_micro": fold.held_win, "price_sum_micro": fold.held_price,
         "counted_profit_sum_micro": sum(counted), "counted_profit_sumsq_micro": sum(x * x for x in counted),
         "trade_rows_read": rows}
    _derive(f)
    return f


# ---------------------------------------------------------------- the tests, the rank, the set (pure)
def judge(f: dict) -> list:
    """The eligibility lines this wallet fails, each named (empty: eligible); every line is tried, not only the first."""
    out = []
    if f["counted_months"] < MIN_COUNTED_MONTHS:
        out.append("1_counted_months_below_%d" % MIN_COUNTED_MONTHS)
    if f["closed_positions"] < MIN_CLOSED:
        out.append("1_closed_positions_below_%d" % MIN_CLOSED)
    if f["profit_micro"] <= 0:
        out.append("2_profit_not_above_zero")
    elif f["shares_micro"] >= MAX_SHARES_PER_DOLLAR * f["profit_micro"]:
        out.append("2_%d_or_more_shares_per_dollar" % MAX_SHARES_PER_DOLLAR)
    if 100 * f["markets_both"] > BOTH_PERCENT * f["markets_traded"]:
        out.append("3_both_outcomes_in_over_%d_percent" % BOTH_PERCENT)
    if f["held_resolved"] < MIN_HELD_RESOLVED:
        out.append("4_fewer_than_%d_held_to_resolution" % MIN_HELD_RESOLVED)
    elif f["win_sum_micro"] <= f["price_sum_micro"]:
        out.append("4_win_share_not_above_mean_price")
    return out


def margin(f: dict) -> Fraction | None:
    """Point 4's margin: the share won less the mean price paid a share, exact (None with no such position)."""
    n = f["held_resolved"]
    return Fraction(f["win_sum_micro"] - f["price_sum_micro"], n * MICRO) if n else None


def rank_key(f: dict):
    """An exactly comparable key, higher is better, for mean / population standard deviation of the counted months:
    the sign and the squared ratio mean^2 / variance, kept as integers and a Fraction; no spread (variance zero) puts a
    positive mean above every finite ratio and a negative one below."""
    n = f["counted_months"]
    if n == 0:
        return (-2, Fraction(0))
    total, sq = f["counted_profit_sum_micro"], f["counted_profit_sumsq_micro"]
    var_n2 = n * sq - total * total                       # n^2 x the population variance
    if var_n2 == 0:
        return (3, Fraction(0)) if total > 0 else ((-1, Fraction(0)) if total < 0 else (1, Fraction(0)))
    squared = Fraction(total * total, var_n2)             # (mean / sd)^2
    if total > 0:
        return (2, squared)
    return (1, Fraction(0)) if total == 0 else (0, -squared)


def _ratio(f: dict):
    """The rank's ratio for display: a float, or the word inf / -inf where there is no spread."""
    n = f["counted_months"]
    if n == 0:
        return None
    total, sq = f["counted_profit_sum_micro"], f["counted_profit_sumsq_micro"]
    var_n2 = n * sq - total * total
    if var_n2 == 0:
        return "inf" if total > 0 else ("-inf" if total < 0 else 0.0)
    return math.copysign(math.sqrt(Fraction(total * total, var_n2)), total) if total else 0.0


def _derive(f: dict) -> None:
    f["failed"] = judge(f)
    f["eligible"] = not f["failed"]
    f["rank_ratio"] = _ratio(f)
    f["shares_per_dollar"] = f["shares_micro"] / f["profit_micro"] if f["profit_micro"] > 0 else None
    f["both_share"] = f["markets_both"] / f["markets_traded"] if f["markets_traded"] else None
    n = f["held_resolved"]
    f["win_share"] = f["win_sum_micro"] / (n * MICRO) if n else None
    f["mean_price"] = f["price_sum_micro"] / (n * MICRO) if n else None
    m = margin(f)
    f["margin"] = float(m) if m is not None else None
    f["margin_exact"] = str(m) if m is not None else None


def pick(figures) -> list:
    """The eligible wallets in rank order (highest ratio first, ties by the larger margin, then the wallet name),
    at most SET_SIZE of them."""
    return ranking(figures)[:SET_SIZE]


def ranking(figures) -> list:
    """Every eligible wallet in rank order: the highest ratio first, ties by the larger margin, then the wallet name."""
    eligible = sorted((f for f in figures if f["eligible"]), key=lambda f: f["wallet"])
    eligible.sort(key=lambda f: (rank_key(f), margin(f)), reverse=True)     # stable: equal keys keep the name order
    return eligible


# ---------------------------------------------------------------- the store, read only
def _open_ro(path):
    """The copy-leaders store's read methods over a connection that cannot write (`mode=ro`, and query_only)."""
    s = cl.Store.__new__(cl.Store)
    s.path = Path(path)
    s.db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=60)
    s.db.execute("PRAGMA query_only = ON")
    return s


def _identity(path: Path) -> dict:
    st = os.stat(path)
    return {"path": str(path.resolve()), "size_bytes": st.st_size, "mtime_ns": st.st_mtime_ns,
            "mtime_utc": datetime.fromtimestamp(st.st_mtime, UTC).isoformat(timespec="seconds")}


def _dump(doc: dict) -> bytes:
    return (json.dumps(doc, sort_keys=True, indent=1) + "\n").encode()


def _check_fresh(out: Path) -> None:
    for name in (SET_FILE, REPORT_FILE):
        if (out / name).exists():
            raise Refused("%s already exists in %s: the screen is run once, by design, and this folder holds its "
                          "result or a run that stopped after writing it (nothing read, nothing written)" % (name, out))


def _write_new(path: Path, raw: bytes) -> None:
    """Make `path` with exactly `raw`, refusing if it exists (the file is linked into place complete)."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(raw)
    try:
        os.link(tmp, path)
    except FileExistsError:
        raise Refused("%s already exists: the screen is run once, by design (nothing written)" % path) from None
    finally:
        tmp.unlink()


def code_sha256() -> str:
    h = hashlib.sha256(Path(__file__).read_bytes())
    h.update(Path(cl.__file__).read_bytes())
    return h.hexdigest()


# ---------------------------------------------------------------- the run
def run_screen(*, store, out_dir, run_day: date | None = None, today: date | None = None, cutoff_month=None,
               cutoff_override: bool = False, max_memory_mb: float | None = cl.MAX_MEMORY_MB, say=None,
               memory=None) -> dict:
    say = say or _say_err
    today = today or _today()
    run_day = run_day or today
    cut = _cutoff(run_day, today, cutoff_month, cutoff_override)
    out = Path(out_dir)
    _check_fresh(out)
    path = Path(store)
    with cl._readable(path):
        doc = _screen(path, out, cut, max_memory_mb, say, memory)
    return doc


def _screen(path: Path, out: Path, cut: dict, max_memory_mb, say, memory) -> dict:
    last = cut.pop("_last")
    window, end = window_of(last), cutoff_ts(last)
    before = _identity(path)
    s = _open_ro(path)
    try:
        sid = s.checked_id()
        problems = cl.reading_problems(s, plan=False)
        if problems:
            raise Refused("%s (nothing written)" % "; ".join(problems))
        cands = s.candidates()
        if not cands:
            raise Refused("the store holds no candidate set (nothing written)")
        done = s.finished_keys("trades")
        limited = s.page_limited("trades")
        other = [w for w in cands if w not in done and w not in limited]
        if other:
            raise Refused("the pull is unfinished for %d candidate wallets (%s): nothing is screened on an incomplete "
                          "pull (nothing written)" % (len(other), ", ".join(other[:10])))
        not_screened = [{"wallet": w, "reason": "trade walk ended at the page limit (%d pages)" % limited[w]}
                        for w in cands if w in limited]
        screened = [w for w in cands if w in done]
        counts: Counter = Counter()
        figures, peak, t0 = [], 0.0, time.monotonic()
        for i, w in enumerate(screened, 1):
            f = screen_wallet(s, w, window=window, end=end, counts=counts)
            figures.append(f)
            mb = (memory or cl.rss_mb)()
            peak = max(peak, mb)
            say("screen: wallet %d of %d (%s): %s rows read, %d s, memory %d MB" % (
                i, len(screened), w[:10], format(f["trade_rows_read"], ","), time.monotonic() - t0, mb))
            if max_memory_mb is not None and mb > max_memory_mb:
                raise cl.MemoryStopped("stopped: resident memory %d MB is above the limit of %d MB (--max-memory-mb) at "
                                       "wallet %d of %d of the screen; nothing written" % (mb, max_memory_mb, i,
                                                                                            len(screened)))
        rejects = {k[len("trade_"):]: v for k, v in counts.items() if k.startswith("trade_")}
        rows_read = {"wallets_screened": len(screened), "trade_passes": 2,
                     "trade_rows_per_pass": sum(f["trade_rows_read"] for f in figures),
                     "trade_rows_set_aside_by_reason": dict(sorted(rejects.items())),
                     "resolution_rows_read": counts["resolution_rows_read"]}
    finally:
        s.db.close()
    after = _identity(path)
    if (after["size_bytes"], after["mtime_ns"]) != (before["size_bytes"], before["mtime_ns"]):
        raise Refused("the store changed while the screen read it (size or time differs before and after): a pull "
                      "or another writer touched it; nothing written")
    for f in figures:
        f.pop("trade_rows_read")
    figures.sort(key=lambda f: f["wallet"])
    ranked = ranking(figures)
    chosen = ranked[:SET_SIZE]
    starts = len(ranked) >= MIN_SET
    verdict = ("%d eligible of %d screened; the set is the first %d" % (len(ranked), len(figures), len(chosen))
               if starts else "%d eligible of %d screened: fewer than %d, the test does not start"
               % (len(ranked), len(figures), MIN_SET))
    doc = {"test": TEST_DOC, "program": {"module": "valedia_trader.research_loop.copy_screen",
                                         "code_sha256": code_sha256()},
           "cutoff": cut, "window": [cl.month_name(ym) for ym in window],
           "store": {**before, "store_id": sid, "rows_read": rows_read},
           "thresholds": {"counted_month_closed_positions": MIN_CLOSED_IN_MONTH, "counted_months": MIN_COUNTED_MONTHS,
                          "closed_positions": MIN_CLOSED, "shares_per_dollar_below": MAX_SHARES_PER_DOLLAR,
                          "both_outcomes_percent_at_most": BOTH_PERCENT, "held_to_resolution": MIN_HELD_RESOLVED,
                          "set_size": SET_SIZE, "smallest_set": MIN_SET},
           "readings": list(READINGS), "candidates": len(cands), "not_screened": not_screened,
           "wallets": figures, "ranking": ranked, "set": [f["wallet"] for f in chosen] if starts else [],
           "test_starts": starts,
           "verdict": verdict}
    _write(out, doc, chosen if starts else [], starts)
    doc["peak_memory_mb"] = max(peak, cl._peak_mb())
    return doc


def _write(out: Path, doc: dict, chosen, starts: bool) -> None:
    """The set file (checked with the record's own reader first), then the report, each with its hash beside it."""
    out.mkdir(parents=True, exist_ok=True)
    _check_fresh(out)
    files: dict = {}
    if starts:
        bad = [f["wallet"] for f in chosen if not lm.WALLET_RE.fullmatch(f["wallet"])]
        if bad:
            raise Refused("a picked wallet is not a well-formed address the record can take (%s): nothing written; the "
                          "lead rules" % ", ".join(w[:10] for w in bad))
        raw = "".join(f["wallet"] + "\n" for f in chosen).encode()
        probe = out / (SET_FILE + ".probe")
        probe.write_bytes(raw)
        try:
            got, why = lm.read_wallets_file(probe)
        finally:
            probe.unlink()
        if why or got[0] != [f["wallet"] for f in chosen] or got[1] != hashlib.sha256(raw).hexdigest():
            raise Refused("the leaders' record's reader does not take the set as written (%s): nothing written" % why)
        files["set"] = {"path": str(out / SET_FILE), "sha256": hashlib.sha256(raw).hexdigest(), "wallets": len(chosen)}
    report = {k: v for k, v in doc.items() if k != "files"}
    report["files"] = {"set": files.get("set")}
    rraw = _dump(report)
    rhash = hashlib.sha256(rraw).hexdigest()
    _write_new(out / REPORT_FILE, rraw)
    _write_new(out / (REPORT_FILE + ".sha256"), (rhash + "\n").encode())
    files["report"] = {"path": str(out / REPORT_FILE), "sha256": rhash}
    if starts:
        _write_new(out / SET_FILE, raw)
        _write_new(out / (SET_FILE + ".sha256"), (files["set"]["sha256"] + "\n").encode())
    doc["files"] = files


# ---------------------------------------------------------------- the command
def format_summary(doc: dict) -> str:
    c, st = doc["cutoff"], doc["store"]
    lines = ["copy screen (T55a): %s" % doc["test"],
             "cutoff: resolutions known before %s (end of %s), %s; run day %s%s" % (
                 datetime.fromtimestamp(c["resolutions_before_ts"], UTC).strftime("%Y-%m-%d %H:%M:%S"), c["last_month"],
                 "derived from the run day" if c["derived_from_run_day"] and not c["override"]
                 else "NOT derived from the run day (override flag named)", c["run_day"],
                 "" if not c["override"] else " (derived would be %s)" % c["derived_last_month"]),
             "window W: %s to %s (12 complete months)" % (doc["window"][0], doc["window"][-1]),
             "store: %s; %s bytes, mtime %s, id %s" % (st["path"], st["size_bytes"], st["mtime_utc"], st["store_id"]),
             "rows read: %s" % json.dumps(st["rows_read"], sort_keys=True),
             "candidates %d, screened %d, not screened %d" % (doc["candidates"], len(doc["wallets"]),
                                                              len(doc["not_screened"])),
             "eligible %d; %s" % (len(doc["ranking"]), doc["verdict"])]
    for i, f in enumerate(doc["set"], 1):
        lines.append("  %2d. %s" % (i, f[:10]))
    if "set" in doc["files"] and doc["files"]["set"]:
        lines.append("set file: %s sha256 %s" % (doc["files"]["set"]["path"], doc["files"]["set"]["sha256"]))
    else:
        lines.append("set file: none written (the test does not start)")
    lines.append("report: %s sha256 %s" % (doc["files"]["report"]["path"], doc["files"]["report"]["sha256"]))
    lines.append("peak resident memory %d MB" % doc["peak_memory_mb"])
    return "\n".join(lines)


def _month_arg(text: str):
    try:
        y, m = text.split("-")
        y, m = int(y), int(m)
        if not 1 <= m <= 12:
            raise ValueError
        return y, m
    except ValueError:
        raise argparse.ArgumentTypeError("%r is not a month as YYYY-MM" % text) from None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="copy_screen", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run", help="the screen, once")
    p.add_argument("--store", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--run-day", type=date.fromisoformat, default=None)
    p.add_argument("--cutoff-month", type=_month_arg, default=None)
    p.add_argument(OVERRIDE_FLAG, dest="override", action="store_true")
    p.add_argument("--max-memory-mb", type=int, default=cl.MAX_MEMORY_MB)
    a = ap.parse_args(argv)
    try:
        doc = run_screen(store=a.store, out_dir=a.out_dir, run_day=a.run_day, cutoff_month=a.cutoff_month,
                         cutoff_override=a.override, max_memory_mb=a.max_memory_mb)
    except Refused as e:
        print("REFUSED: %s" % e, file=sys.stderr)
        return 2
    except cl.PullStopped as e:
        print("STOPPED: %s" % e, file=sys.stderr)
        return 4
    print(format_summary(doc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
