"""The forecast tournament: what each of Valedia's forecasters is actually worth, on any market.

Every forecaster (Nimble v3, the local models, the Hermes bots, later a MiroFish swarm) is asked the same
dated yes-or-no questions and scored when they resolve, against a baseline that needs no intelligence.
A forecaster earns a say in trading only by beating that baseline on enough resolved questions.

Two kinds of question:

* forward        asked today about the next few days, with the asset named. Nothing can leak, because
                 the answer does not exist yet. It takes calendar time to score.
* blind history  a past day, with the asset's name and the date removed, so a model cannot recall what
                 happened and has only the facts to go on. It resolves at once, in the thousands.

The baseline for a direction question is the share of the asset's own past windows that rose, taken from
prices before the question's day. Scores are Brier scores (the squared miss of the probability; lower
is better). Questions asked in the same week share the same market weather, so the test of "better than
the baseline" counts each week once.

Two more measures separate a forecaster's ranking from its scaling. `ranking` is the chance that a case that
came true got a higher forecast than one that did not (0.5 is a coin, 1 is perfect), with a 95% range from
resampling whole weeks. The rescaled score refits the forecaster's probabilities on the earlier half of its
questions only, then scores the later half, so a model that says "about 0.1" to everything but orders the
cases well is not lost behind its scale. Two rule forecasters (`--with-rules`) give a model a one-line rule to beat.

    python -m valedia_trader.research_loop.tournament refresh --universe stocks       (or crypto: see universes.json)
    python -m valedia_trader.research_loop.tournament ask-blind --data artifacts/data --market funds --count 300
    python -m valedia_trader.research_loop.tournament ask-forward --data artifacts/data --market funds --with-rules
    python -m valedia_trader.research_loop.tournament settle --data artifacts/data
    python -m valedia_trader.research_loop.tournament score
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import expit, logit
from scipy.stats import rankdata

from valedia_trader.agents import panel as panel_mod
from valedia_trader.research_loop import LoopPaths, diagnostics

__all__ = ["Question", "TournamentLedger", "direction_questions", "settle_directions", "ask", "scores", "base_rate",
           "forecasters", "reference_forecasters", "universe", "refresh_universe", "main"]

MIN_HISTORY = 253                 # a year of prices before an asset can be asked about
HIDDEN_WHEN_BLIND = ("symbol", "as_of", "data_age_days")
KINDS = ("forward", "blind history")


@dataclass(frozen=True)
class Question:
    id: str
    market: str
    kind: str
    asked_on: str
    horizon: int
    symbol: str                    # kept beside the question to settle it; a blind question never shows it
    thesis: str
    facts: dict
    baseline: float

    def to_row(self) -> tuple:
        return (self.id, self.market, self.kind, self.asked_on, int(self.horizon), self.symbol, self.thesis,
                json.dumps(self.facts, sort_keys=True, default=str), float(self.baseline))


def base_rate(history: pd.Series, horizon: int) -> float:
    """Share of the asset's past `horizon`-day windows that rose, from the prices given and nothing later."""
    px = history.dropna().to_numpy(dtype=float)
    if len(px) <= horizon + 100:
        return 0.5
    return float(np.clip(np.mean(px[horizon:] > px[:-horizon]), 0.05, 0.95))


def _rounded(facts: dict) -> dict:
    return {k: (round(v, 3) if isinstance(v, float) and math.isfinite(v) else v) for k, v in facts.items()}


def direction_questions(prices: pd.DataFrame, symbols: Sequence[str], as_of, horizon: int, market: str,
                        blind: bool = False) -> list[Question]:
    """One "closes higher over the next `horizon` trading days" question per asset, from prices up to
    `as_of` only. An asset with no price that day or under a year of history gets no question."""
    as_of = pd.Timestamp(as_of)
    hist = prices.loc[:as_of]
    out = []
    for sym in symbols:
        if sym not in hist.columns:
            continue
        px = hist[sym].dropna()
        if len(px) < MIN_HISTORY or px.index[-1] != as_of:
            continue
        try:
            facts = _rounded(diagnostics.order_facts(hist, sym))
        except Exception:                                  # an asset the fact sheet cannot describe is not asked about
            continue
        day = str(as_of.date())
        if blind:
            facts = {k: v for k, v in facts.items() if k not in HIDDEN_WHEN_BLIND}
            qid = "blind:" + hashlib.sha1(f"{market}|{sym}|{day}|{horizon}".encode()).hexdigest()[:16]
            thesis = f"This asset closes higher over the next {horizon} trading days"
        else:
            qid, thesis = f"{market}:{sym}:{day}:{horizon}d", f"{sym} closes higher over the next {horizon} trading days"
        out.append(Question(qid, market, "blind history" if blind else "forward", day, int(horizon), sym, thesis, facts,
                            base_rate(px, horizon)))
    return out


class TournamentLedger:
    """Questions, every forecaster's answer, and what happened. One SQLite file."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path))
        self._db.row_factory = sqlite3.Row
        self._db.executescript(
            "CREATE TABLE IF NOT EXISTS questions (id TEXT PRIMARY KEY, market TEXT, kind TEXT, asked_on TEXT, horizon INTEGER, "
            "symbol TEXT, thesis TEXT, facts TEXT, baseline REAL);"
            "CREATE TABLE IF NOT EXISTS forecasts (question_id TEXT, forecaster TEXT, probability REAL, error TEXT, latency_ms REAL, "
            "ts REAL, PRIMARY KEY (question_id, forecaster));"
            "CREATE TABLE IF NOT EXISTS outcomes (question_id TEXT PRIMARY KEY, outcome INTEGER, settled_ts REAL);")

    def add(self, questions: Sequence[Question]) -> int:
        """Add the questions not already on file. Returns how many were new."""
        before = self._db.total_changes
        self._db.executemany("INSERT OR IGNORE INTO questions VALUES (?,?,?,?,?,?,?,?,?)", [q.to_row() for q in questions])
        self._db.commit()
        return self._db.total_changes - before

    def questions(self, unsettled_only: bool = False) -> list[dict]:
        sql = "SELECT q.* FROM questions q" + (" LEFT JOIN outcomes o ON o.question_id = q.id WHERE o.question_id IS NULL" if unsettled_only else "")
        return [{**dict(r), "facts": json.loads(r["facts"])} for r in self._db.execute(sql + " ORDER BY q.asked_on, q.id")]

    def answered(self, forecaster: str) -> set[str]:
        """Questions this forecaster has given a usable answer to. A failed call is asked again."""
        return {r[0] for r in self._db.execute("SELECT question_id FROM forecasts WHERE forecaster = ? AND error IS NULL", (forecaster,))}

    def record(self, question_id: str, forecaster: str, probability: float | None, error: str | None, latency_ms: float) -> None:
        self._db.execute("INSERT OR REPLACE INTO forecasts VALUES (?,?,?,?,?,?)",
                         (question_id, forecaster, probability, error, float(latency_ms), time.time()))
        self._db.commit()

    def settle(self, question_id: str, outcome: int) -> bool:
        """Record what happened. A question is settled once; a second call changes nothing."""
        if outcome not in (0, 1):
            raise ValueError("an outcome is 0 (it did not happen) or 1 (it did)")
        cur = self._db.execute("INSERT OR IGNORE INTO outcomes VALUES (?,?,?)", (question_id, int(outcome), time.time()))
        self._db.commit()
        return cur.rowcount == 1

    def settled(self) -> list[dict]:
        return [dict(r) for r in self._db.execute(
            "SELECT q.id, q.market, q.kind, q.asked_on, q.symbol, q.baseline, o.outcome FROM questions q JOIN outcomes o ON o.question_id = q.id")]

    def scored_rows(self) -> list[dict]:
        return [dict(r) for r in self._db.execute(
            "SELECT f.forecaster, f.probability, q.market, q.kind, q.asked_on, q.baseline, o.outcome FROM forecasts f "
            "JOIN questions q ON q.id = f.question_id JOIN outcomes o ON o.question_id = q.id "
            "WHERE f.error IS NULL AND f.probability IS NOT NULL ORDER BY q.asked_on, q.id, f.forecaster")]

    def counts(self) -> dict[str, Any]:
        one = lambda sql: self._db.execute(sql).fetchone()[0]
        return {"questions": one("SELECT COUNT(*) FROM questions"), "settled": one("SELECT COUNT(*) FROM outcomes"),
                "answers": dict(self._db.execute("SELECT forecaster, COUNT(*) FROM forecasts WHERE error IS NULL GROUP BY forecaster").fetchall()),
                "failed_calls": dict(self._db.execute("SELECT forecaster, COUNT(*) FROM forecasts WHERE error IS NOT NULL GROUP BY forecaster").fetchall())}

    def close(self) -> None:
        self._db.close()


def settle_directions(ledger: TournamentLedger, prices: pd.DataFrame) -> int:
    """Settle every open direction question whose horizon has passed in `prices`. Returns how many."""
    done = 0
    for q in ledger.questions(unsettled_only=True):
        day = pd.Timestamp(q["asked_on"])
        if q["symbol"] not in prices.columns or day not in prices.index:
            continue
        i = prices.index.get_loc(day)
        if i + q["horizon"] >= len(prices):
            continue                                       # the horizon has not passed yet
        then, later = float(prices[q["symbol"]].iloc[i]), float(prices[q["symbol"]].iloc[i + q["horizon"]])
        if math.isfinite(then) and math.isfinite(later):
            done += int(ledger.settle(q["id"], int(later > then)))
    return done


def ask(ledger: TournamentLedger, members: Sequence[panel_mod.Member], today=None, max_age_days: int = 3,
        limit: int | None = None, progress=None) -> dict[str, dict[str, int]]:
    """Put every question a forecaster has not answered to it. A forecaster sees the thesis and the
    facts, as the decision panel does, and nothing else. Failures are recorded and asked again next time.

    With `today` given, a forward question is only put while it is at most `max_age_days` old and still
    open: a forecaster that arrives later must not answer it with hindsight."""
    out = {}
    for m in members:
        have = ledger.answered(m.name)
        todo = [q for q in ledger.questions() if q["id"] not in have]
        if today is not None:
            open_ids = {q["id"] for q in ledger.questions(unsettled_only=True)}
            fresh = lambda q: q["id"] in open_ids and (pd.Timestamp(today) - pd.Timestamp(q["asked_on"])).days <= max_age_days
            todo = [q for q in todo if q["kind"] != "forward" or fresh(q)]
        todo = todo[:limit] if limit else todo
        answered = 0
        for n, q in enumerate(todo, start=1):
            v = m.ask({"thesis": q["thesis"], "facts": q["facts"]})
            ok = v.error is None and v.probability is not None
            ledger.record(q["id"], m.name, v.probability if ok else None, None if ok else (v.error or "no probability in the reply"), v.latency_ms)
            answered += int(ok)
            if progress and (n % 25 == 0 or n == len(todo)):
                progress(f"{m.name}: {n}/{len(todo)} asked, {answered} answered")
        out[m.name] = {"asked": len(todo), "answered": answered}
    return out


def _week(day: str) -> tuple[int, int]:
    iso = pd.Timestamp(day).isocalendar()
    return int(iso[0]), int(iso[1])


BOOTSTRAPS = 500


def _t_by_week(rows: list[dict], diff: np.ndarray) -> tuple[float, int]:
    """The t statistic of the per-question differences with each ISO week counted once, and how many weeks."""
    by_week: dict[tuple, list[float]] = {}
    for r, d in zip(rows, diff):
        by_week.setdefault(_week(r["asked_on"]), []).append(float(d))
    weekly = np.array([np.mean(v) for v in by_week.values()])
    spread = float(weekly.std(ddof=1)) if len(weekly) > 1 else 0.0
    return (float(weekly.mean() / (spread / math.sqrt(len(weekly)))) if spread > 0 else 0.0), len(weekly)


def _auc(p: np.ndarray, y: np.ndarray) -> float | None:
    """Share of (came-true, did-not) pairs where the came-true case got the higher forecast; a tie counts one half."""
    pos = int((y == 1).sum())
    neg = len(y) - pos
    if pos == 0 or neg == 0:
        return None
    ranks = rankdata(p)                                    # tied forecasts share their average rank, which is the half
    return float((ranks[y == 1].sum() - pos * (pos + 1) / 2.0) / (pos * neg))


def _ranking(rows: list[dict]) -> tuple[float | None, float | None, float | None]:
    """The ranking and its 95% range from 500 resamples of whole ISO weeks (drawn with replacement, rows pooled).
    A resample missing a whole outcome class is skipped. The same seed every time, so the range is reproducible."""
    p, y = (np.array([r[k] for r in rows], dtype=float) for k in ("probability", "outcome"))
    point = _auc(p, y)
    if point is None:
        return None, None, None
    weeks: dict[tuple, list[int]] = {}
    for i, r in enumerate(rows):
        weeks.setdefault(_week(r["asked_on"]), []).append(i)
    members = [np.array(weeks[k]) for k in sorted(weeks)]          # weeks in calendar order, however the rows were stored
    rng = np.random.default_rng(0)
    draws = []
    for _ in range(BOOTSTRAPS):
        picked = np.concatenate([members[j] for j in rng.integers(0, len(members), len(members))])
        got = _auc(p[picked], y[picked])
        if got is not None:
            draws.append(got)
    if not draws:
        return point, None, None
    low, high = np.percentile(draws, [2.5, 97.5])
    return point, float(low), float(high)


def _log_odds(p: np.ndarray) -> np.ndarray:
    return logit(np.clip(p, 0.01, 0.99))


def _rescaled(rows: list[dict], min_settled: int) -> dict:
    """Fit q = sigmoid(a + b * logit(p)) by log loss on the earlier half of the questions (split at the median
    distinct ask date, a date never straddling the split), then score the later half with it. The slope b is held
    between 0 and 100 and the shift a between -500 and 500: a forecaster that is reliably wrong-way is rescaled to
    a constant, not credited, a timid one (forecasts within 0.02 of one half) gets the large slope it needs, and a
    perfectly separable earlier half still has a finite fit. Empty (None) when
    either half has fewer than `min_settled` rows or the earlier half has only one outcome."""
    empty = {"rescaled_brier": None, "rescaled_baseline_brier": None, "rescaled_skill": None, "rescaled_t_by_week": None,
             "rescaled_settled": None}
    days = sorted({r["asked_on"] for r in rows})
    cut = days[len(days) // 2]
    early = [r for r in rows if r["asked_on"] < cut]
    late = [r for r in rows if r["asked_on"] >= cut]
    if len(early) < min_settled or len(late) < min_settled:
        return empty
    pe, ye = (np.array([r[k] for r in early], dtype=float) for k in ("probability", "outcome"))
    if ye.min() == ye.max():
        return empty
    xe = _log_odds(pe)
    def log_loss(ab):
        z = ab[0] + ab[1] * xe
        return float(np.mean(np.logaddexp(0.0, z) - ye * z))          # -[y log q + (1-y) log(1-q)], q = sigmoid(z)
    a, b = minimize(log_loss, x0=np.array([0.0, 1.0]), method="L-BFGS-B", bounds=[(-500.0, 500.0), (0.0, 100.0)],
                  options={"ftol": 1e-12, "gtol": 1e-10, "maxiter": 1000}).x
    pl, yl, bl = (np.array([r[k] for r in late], dtype=float) for k in ("probability", "outcome", "baseline"))
    mine, base = (expit(a + b * _log_odds(pl)) - yl) ** 2, (bl - yl) ** 2
    t, _ = _t_by_week(late, mine - base)
    return {"rescaled_brier": float(mine.mean()), "rescaled_baseline_brier": float(base.mean()),
            "rescaled_skill": float(1.0 - mine.mean() / base.mean()) if base.mean() > 0 else 0.0, "rescaled_t_by_week": t,
            "rescaled_settled": len(late)}


def scores(ledger: TournamentLedger, min_settled: int = 30) -> list[dict]:
    """Each forecaster against the baseline, per market and kind of question.

    skill is 1 minus the forecaster's Brier score over the baseline's on the same questions: above 0 is
    better than the baseline, 0 is the same, below 0 is worse. t_by_week tests the difference with each
    week of questions counted once; -2 or lower is "beats the baseline", +2 or higher is "worse".

    ranking is the chance a case that came true got a higher forecast than one that did not (ties count
    half; 0.5 is a coin), between ranking_low and ranking_high (95%, resampling whole weeks). The rescaled_*
    keys are the same Brier comparison on the later half of the questions after the forecasts are rescaled
    by a fit to the earlier half alone; None when there are too few questions to split."""
    groups: dict[tuple, list[dict]] = {}
    for r in ledger.scored_rows():
        groups.setdefault((r["forecaster"], r["market"], r["kind"]), []).append(r)
    out = []
    for (who, market, kind), rows in sorted(groups.items()):
        p, y, b = (np.array([r[k] for r in rows], dtype=float) for k in ("probability", "outcome", "baseline"))
        mine, base = (p - y) ** 2, (b - y) ** 2
        t, weeks = _t_by_week(rows, mine - base)
        n = len(rows)
        if n < min_settled:
            verdict = f"too few resolved questions to judge ({n} of {min_settled})"
        elif t <= -2.0:
            verdict = "beats the baseline"
        elif t >= 2.0:
            verdict = "worse than the baseline"
        else:
            verdict = "not distinguishable from the baseline"
        ranking, low, high = _ranking(rows)
        if n < min_settled:
            ranking_verdict = "too few resolved questions to judge"
        elif low is not None and low > 0.5:
            ranking_verdict = "ranks better than chance"
        elif high is not None and high < 0.5:
            ranking_verdict = "ranks worse than chance"
        else:
            ranking_verdict = "ranking not distinguishable from chance"
        out.append({"forecaster": who, "market": market, "kind": kind, "settled": n, "weeks": weeks,
                    "brier": float(mine.mean()), "baseline_brier": float(base.mean()),
                    "skill": float(1.0 - mine.mean() / base.mean()) if base.mean() > 0 else 0.0, "t_by_week": t,
                    "mean_forecast": float(p.mean()), "came_true": float(y.mean()),
                    "right_side": float(np.mean((p >= 0.5) == (y == 1))), "verdict": verdict,
                    "ranking": ranking, "ranking_low": low, "ranking_high": high, "ranking_verdict": ranking_verdict,
                    **_rescaled(rows, min_settled)})
    return out


def reference_forecasters() -> list[panel_mod.Member]:
    """Two forecasters that need no model, so a model can be compared with a one-line rule: `rule-momentum`
    says 0.6 when the last 20 days rose and 0.4 when they did not, `rule-above-200d` says 0.6 above the
    200-day average and 0.4 below it. Each says 0.5 when the fact is missing."""
    def momentum(case):
        m = case["facts"].get("momentum_20d_pct")
        return (0.5 if m is None else 0.6 if m > 0 else 0.4), None, "20-day momentum rule"
    def above(case):
        a = case["facts"].get("above_200d_average")
        return (0.5 if a is None else 0.6 if a else 0.4), None, "200-day average rule"
    return [panel_mod.CallableMember("rule-momentum", "decider", momentum), panel_mod.CallableMember("rule-above-200d", "decider", above)]


def forecasters(names: Sequence[str] | None = None, roster: dict | None = None) -> list[panel_mod.Member]:
    """The panel's deciders as tournament forecasters, built from the panel roster. `names` picks some
    (a member switched off in the roster can still be named here); without it, every enabled decider."""
    roster = panel_mod.load_roster() if roster is None else roster
    specs = [s for s in roster["members"] if s["role"] == "decider" and (s["name"] in names if names else s.get("enabled", True))]
    missing = set(names or ()) - {s["name"] for s in specs}
    if missing:
        raise ValueError(f"not a decider in the panel roster: {sorted(missing)}")
    return list(panel_mod.build_panel({**roster, "members": [{**s, "enabled": True} for s in specs]}).members)


def universe(name: str) -> dict:
    """A named list of assets beyond the funds, and the folder its prices live in (universes.json)."""
    known = {k: v for k, v in json.loads(Path(__file__).with_name("universes.json").read_text()).items() if isinstance(v, dict)}
    if name not in known:
        raise ValueError(f"no universe called '{name}'; there are: {', '.join(sorted(known))}")
    return known[name]


def refresh_universe(name: str, root: str | Path | None = None, download=None) -> dict:
    """Download or update the universe's daily prices into its folder. Fails closed if any symbol comes back empty."""
    from valedia_trader.data_and_fees import prices as price_data
    u = universe(name)
    folder = (Path(root) if root else LoopPaths().root) / u["folder"]
    return price_data.refresh(folder, symbols=tuple(u["symbols"]), start=u["start"], download=download)


def _sample_days(index: pd.DatetimeIndex, count: int, horizon: int, rng: np.random.Generator) -> list[pd.Timestamp]:
    """Past days at least a year in and far enough from the end to have resolved, one per week at most."""
    usable = index[MIN_HISTORY:len(index) - horizon - 1]
    one_per_week = pd.Series(usable, index=usable).groupby([usable.isocalendar().year.to_numpy(), usable.isocalendar().week.to_numpy()]).last()
    picks = rng.choice(len(one_per_week), size=min(count, len(one_per_week)), replace=False)
    return sorted(pd.Timestamp(d) for d in one_per_week.to_numpy()[picks])


def main(argv=None) -> int:
    from valedia_trader.backtest.walkforward import load_universe
    ap = argparse.ArgumentParser(prog="tournament", description="Ask, settle and score Valedia's forecasters.")
    ap.add_argument("--root", default=None)
    ap.add_argument("--ledger", default=None, help="the tournament file (default: memory/tournament.sqlite)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("ask-blind", "ask-forward", "settle"):
        p = sub.add_parser(name)
        p.add_argument("--data", required=True, help="a price folder (close.parquet)")
        if name != "settle":
            p.add_argument("--market", required=True, help="a label for these assets: funds, stocks, crypto")
            p.add_argument("--horizon", type=int, default=5)
            p.add_argument("--forecasters", default="", help="comma-separated panel deciders (default: every enabled one)")
            p.add_argument("--with-rules", action="store_true", help="also ask the two rule forecasters (momentum, above the 200-day average)")
            p.add_argument("--limit", type=int, default=None, help="at most this many questions per forecaster in this run")
        if name == "ask-blind":
            p.add_argument("--count", type=int, default=300, help="how many questions to draw")
            p.add_argument("--seed", type=int, default=0)
    sub.add_parser("score").add_argument("--min-settled", type=int, default=30)
    sub.add_parser("refresh").add_argument("--universe", required=True, help="stocks or crypto: download or update its prices")
    args = ap.parse_args(argv)
    paths = LoopPaths(Path(args.root)) if args.root else LoopPaths()
    ledger = TournamentLedger(args.ledger or paths.memory / "tournament.sqlite")
    try:
        if args.cmd == "score":
            rows = scores(ledger, args.min_settled)
            print(json.dumps({"counts": ledger.counts(), "scores": rows}, indent=1))
            return 0
        if args.cmd == "refresh":
            print(json.dumps(refresh_universe(args.universe, paths.root), indent=1))
            return 0
        data = Path(args.data) if Path(args.data).is_absolute() else paths.root / args.data
        prices, _ = load_universe(data)
        if args.cmd == "settle":
            print(json.dumps({"settled_now": settle_directions(ledger, prices), "counts": ledger.counts()}, indent=1))
            return 0
        if args.cmd == "ask-blind":
            rng = np.random.default_rng(args.seed)
            days = _sample_days(prices.index, args.count, args.horizon, rng)
            symbols = list(prices.columns)
            qs = []
            for d in days:                                  # one asset per day: questions as independent as the data allows
                got = direction_questions(prices, [symbols[int(rng.integers(len(symbols)))]], d, args.horizon, args.market, blind=True)
                qs += got
            added = ledger.add(qs)
            settled = settle_directions(ledger, prices)
            today = None
        else:
            qs = direction_questions(prices, list(prices.columns), prices.index[-1], args.horizon, args.market)
            added, settled, today = ledger.add(qs), 0, pd.Timestamp.now().normalize()
        members = forecasters([n for n in args.forecasters.split(",") if n] or None) + (reference_forecasters() if args.with_rules else [])
        asked = ask(ledger, members, today=today, limit=args.limit, progress=lambda s: print(s, file=sys.stderr, flush=True))
        print(json.dumps({"questions_added": added, "settled_now": settled, "asked": asked, "counts": ledger.counts()}, indent=1))
        return 0
    finally:
        ledger.close()


if __name__ == "__main__":
    raise SystemExit(main())
