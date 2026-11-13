"""The live decay monitor: pause a strategy whose paper results have fallen away from its backtest.

    python -m valedia_trader.research_loop.decay check            report only
    python -m valedia_trader.research_loop.decay check --apply    pause what should be paused, journal the check

A daily check that can only make the trader more careful. For every strategy in the paper book it computes,
from the paper record alone, the strategy's own daily returns (its change in value over the capital the
paper trader gave it that day, the critic's convention), their annualised Sharpe and their worst fall from
a peak, and sets them against the backtest Sharpe and worst drawdown the gauntlet stored in the idea ledger.

    Rule 1, from the first paper day:   worst fall > 1.5 x the backtest's worst drawdown      -> pause
    Rule 2, from MIN_DAYS_FOR_SHARPE:   Sharpe < half the backtest Sharpe                      -> pause
            (never for a record whose every daily return is zero: it has not traded, its Sharpe is not judged)

The Sharpe is computed exactly as the gauntlet's headline figure (`performance_report`): mean over sample
standard deviation (ddof 1) of the daily returns, times the square root of 252, no risk-free rate, 0 when
the returns do not vary. The worst fall uses the same `max_drawdown`, from the starting capital.

It never un-pauses, promotes or changes anything else: the only write to the ledger is
`set_status(id, "paused", reason, "decay")`, for a strategy still trading. A strategy whose figures cannot be
computed (no backtest figures, a damaged journal line in the stretch it needs, fewer than 2 paper days) is
reported as `cannot judge` and left as it is. A damaged line is named by file and line, never by its text.

Exit codes: 0 nothing to pause, 1 at least one strategy paused or to pause, 2 the check could not run.
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from valedia_trader.quant_engine import level8_portfolio_risk as risk
from valedia_trader.research_loop import LoopPaths
from valedia_trader.research_loop.ledger import HypothesisLedger, LedgerError
from valedia_trader.research_loop.paper import Journal, JournalDamaged, describe_skipped

__all__ = ["MIN_DAYS_FOR_SHARPE", "DRAWDOWN_MULTIPLE", "SHARPE_FRACTION", "annualised_sharpe", "worst_fall", "judge",
           "has_traded", "paper_returns", "check", "main"]

MIN_DAYS_FOR_SHARPE = 60          # paper trading days before the Sharpe rule decides anything
DRAWDOWN_MULTIPLE = 1.5           # pause when the paper fall is more than this times the backtest's worst
SHARPE_FRACTION = 0.5             # pause when the paper Sharpe is below this fraction of the backtest's
MIN_DAYS = 2                      # fewer paper days than this: nothing can be said
TRADING = ("passed backtest only", "passed paper")       # the statuses the paper trader trades (cli.PAPER_STATUSES)
SOURCE = "decay"
PERIODS_PER_YEAR = 252.0


class _CannotJudge(Exception):
    pass


ZERO = 1e-15                      # the Sharpe convention's zero (performance_report's threshold on the sd)


def annualised_sharpe(returns) -> float:
    """The gauntlet's convention (`performance_report`): mean / sample sd (ddof 1) x sqrt(252), 0 for flat returns."""
    r = np.asarray(returns, dtype=float).ravel()
    sd = float(r.std(ddof=1)) if r.size >= 2 else 0.0
    return float(r.mean() / sd * math.sqrt(PERIODS_PER_YEAR)) if sd > ZERO else 0.0


def has_traded(returns) -> bool:
    """False when every daily return is zero (within ZERO): the strategy has never held anything, e.g. a trend rule
    sitting in cash. Its Sharpe is then not judged at any number of days."""
    r = np.asarray(returns, dtype=float).ravel()
    return bool(np.any(np.abs(r) > ZERO))


def worst_fall(returns) -> float:
    """Worst fall from a peak of the compounded curve that starts at the strategy's capital, as `performance_report`."""
    r = np.asarray(returns, dtype=float).ravel()
    return float(risk.max_drawdown(np.clip(r, -0.999999, None)).depth)


def _finite(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def judge(live_sharpe: float, live_drawdown: float, days: int, backtest_sharpe: float | None,
          backtest_drawdown: float | None, traded: bool = True) -> dict:
    """Both rules on figures already computed. `decision` is pause, keep or cannot judge; a rule that can be applied
    and fires pauses even when the other rule has no backtest figure (that only makes the trader more careful).
    `traded` False (every daily return zero): the Sharpe rule decides nothing, at any number of days."""
    reasons: list[str] = []
    missing: list[str] = []
    dd_rule = {"limit": None, "fires": False, "decides": False}
    if backtest_drawdown is None:
        missing.append("worst drawdown")
    else:
        limit = DRAWDOWN_MULTIPLE * backtest_drawdown
        dd_rule.update(limit=limit, decides=True, fires=bool(live_drawdown > limit))
        if dd_rule["fires"]:
            reasons.append(f"paper worst fall {live_drawdown:.2%} after {days} paper days is more than {DRAWDOWN_MULTIPLE:g} times "
                           f"the backtest's worst drawdown {backtest_drawdown:.2%} (limit {limit:.2%})")
    sr_rule = {"limit": None, "fires": False, "decides": False}
    if backtest_sharpe is None:
        missing.append("Sharpe")
    else:
        limit = SHARPE_FRACTION * backtest_sharpe
        sr_rule.update(limit=limit, decides=bool(traded and days >= MIN_DAYS_FOR_SHARPE))
        sr_rule["fires"] = bool(sr_rule["decides"] and live_sharpe < limit)
        if sr_rule["fires"]:
            reasons.append(f"paper Sharpe {live_sharpe:.2f} over {days} paper days is below half the backtest Sharpe "
                           f"{backtest_sharpe:.2f} (limit {limit:.2f})")
    if reasons:
        decision = "pause"
    elif missing:
        decision = "cannot judge"
        reasons.append(f"the idea ledger has no backtest {' or '.join(missing)} for it")
    else:
        decision = "keep"
        sharpe_note = (f"Sharpe {live_sharpe:.2f} against a limit of {sr_rule['limit']:.2f}" if sr_rule["decides"] else
                       f"Sharpe {live_sharpe:.2f} reported only ({days} of {MIN_DAYS_FOR_SHARPE} days before it decides)")
        if not traded:
            reasons.append("has not traded: Sharpe not judged")
            sharpe_note = f"no daily return of its {days} paper days is non-zero"
        reasons.append(f"worst fall {live_drawdown:.2%} against a limit of {dd_rule['limit']:.2%}; {sharpe_note}")
    return {"decision": decision, "rules": {"drawdown": dd_rule, "sharpe": sr_rule}, "reasons": reasons}


def paper_returns(entries: list[dict], name: str) -> tuple[list[str], list[float]]:
    """The strategy's own daily returns from the day summaries: its change in value over the capital it was given
    that day (the critic's convention). Raises _CannotJudge, naming the day, on a figure that is missing or unusable."""
    days, out = [], []
    for e in entries:
        if e.get("type") != "summary" or not isinstance(e.get("strategy_value_change"), dict) or name not in e["strategy_value_change"]:
            continue
        change, capital = _finite(e["strategy_value_change"][name]), _finite(e.get("capital_per_strategy"))
        if change is None or capital is None or capital <= 0:
            raise _CannotJudge(f"the day summary of {e.get('day')} has no usable change or capital for it")
        r = change / capital
        if r <= -1.0:
            raise _CannotJudge(f"the day summary of {e.get('day')} shows its whole capital lost")
        days.append(str(e.get("day")))
        out.append(r)
    return days, out


def _mentions(e: dict, name: str) -> bool:
    if e.get("type") == "summary":
        return isinstance(e.get("strategy_value_change"), dict) and name in e["strategy_value_change"]
    return e.get("type") in ("decision", "fill") and e.get("strategy") == name


def _clean(x):
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if isinstance(x, float) and not math.isfinite(x):
        return None
    return x


def check(ledger: HypothesisLedger, journal: Journal, as_of=None, apply: bool = False,
          strategies: dict[int, str] | None = None) -> dict:
    """Judge every strategy in the paper book as of `as_of` (default: the last day summary in the journal).
    `strategies` maps hypothesis id to strategy name; by default it is read from the journal's decisions, together
    with every idea the ledger says is trading. With `apply`, pauses what should be paused and journals the check."""
    if as_of is None:
        found = [e["day"] for e in journal.read() if e.get("type") == "summary" and "day" in e]
        as_of = max(found) if found else str(pd.Timestamp.today().date())
    as_of = str(pd.Timestamp(as_of).date())
    readable = journal.read(until=as_of)                  # skips damage: used only to find who traded and from when
    if strategies is None:
        names: dict[int, str | None] = {}
        for e in readable:
            if e.get("type") == "decision" and e.get("hypothesis_id") is not None and e.get("strategy"):
                names[int(e["hypothesis_id"])] = str(e["strategy"])
        for status in TRADING:
            for h in ledger.search(status=status):
                names.setdefault(int(h["id"]), None)
    else:
        names = {int(k): v for k, v in strategies.items()}

    rows = []
    for hid in sorted(names):
        name = names[hid]
        row = {"hypothesis_id": hid, "strategy": name, "status": None, "days": 0, "first_day": None, "live_sharpe": None,
               "live_max_drawdown": None, "backtest_sharpe": None, "backtest_max_drawdown": None, "drawdown_limit": None,
               "sharpe_limit": None, "sharpe_decides": False, "traded": None, "decision": None, "reason": ""}
        rows.append(row)
        try:
            h = ledger.get(hid)
        except LedgerError:
            row.update(decision="cannot judge", reason=f"hypothesis {hid} is not in the idea ledger")
            continue
        row["status"] = h["status"]
        bt = ((h.get("backtest_result") or {}).get("result") or {})
        row["backtest_sharpe"], row["backtest_max_drawdown"] = _finite(bt.get("sharpe")), _finite(bt.get("max_drawdown"))
        problem = None
        try:
            first = min((e["day"] for e in readable if name and _mentions(e, name) and "day" in e), default=None)
            if first is None:
                raise _CannotJudge(f"0 paper days; at least {MIN_DAYS} are needed")
            row["first_day"] = first
            try:
                stretch = journal.read_complete(since=first, until=as_of)
            except JournalDamaged as exc:
                raise _CannotJudge(f"the journal is damaged in the stretch it needs ({describe_skipped(exc.skipped)})") from None
            _, returns = paper_returns(stretch, name)
            row["days"] = len(returns)
            if len(returns) < MIN_DAYS:
                raise _CannotJudge(f"{len(returns)} paper day{'s' if len(returns) != 1 else ''}; at least {MIN_DAYS} are needed")
            row["live_sharpe"], row["live_max_drawdown"] = annualised_sharpe(returns), worst_fall(returns)
            row["traded"] = has_traded(returns)
        except _CannotJudge as exc:
            problem = str(exc)
        if h["status"] not in TRADING:
            row.update(decision="left alone", reason=f"its status is {h['status']}; this monitor never changes it"
                       + (f" ({problem})" if problem else ""))
            continue
        if problem:
            row.update(decision="cannot judge", reason=problem)
            continue
        verdict = judge(row["live_sharpe"], row["live_max_drawdown"], row["days"], row["backtest_sharpe"],
                        row["backtest_max_drawdown"], traded=row["traded"])
        row.update(decision=verdict["decision"], reason="; ".join(verdict["reasons"]),
                   drawdown_limit=verdict["rules"]["drawdown"]["limit"], sharpe_limit=verdict["rules"]["sharpe"]["limit"],
                   sharpe_decides=verdict["rules"]["sharpe"]["decides"])

    to_pause = [r["hypothesis_id"] for r in rows if r["decision"] == "pause"]
    paused: list[int] = []
    written = False
    if apply:
        for r in rows:
            if r["decision"] == "pause" and ledger.get(r["hypothesis_id"])["status"] in TRADING:
                ledger.set_status(r["hypothesis_id"], "paused", f"decay monitor, {as_of}: {r['reason']}", SOURCE)
                r.update(decision="paused", status="paused")
                paused.append(r["hypothesis_id"])
        already = any(e.get("type") == "decay_check" for e in journal.read(since=as_of, until=as_of))
        if paused or not already:                         # once a day; again only if this run paused something new
            journal.write(as_of, _clean({"type": "decay_check", "as_of": as_of,
                                         "rules": {"drawdown_multiple": DRAWDOWN_MULTIPLE, "sharpe_fraction": SHARPE_FRACTION,
                                                   "min_days_for_sharpe": MIN_DAYS_FOR_SHARPE},
                                         "strategies": rows, "paused": paused}))
            written = True
    return _clean({"as_of": as_of, "strategies": rows, "to_pause": to_pause, "paused": paused, "journal_entry_written": written,
                   "exit_code": 1 if (to_pause or paused) else 0})


def _line(r: dict) -> str:
    def pct(x):
        return "n/a" if x is None else f"{x:.2%}"

    def num(x):
        return "n/a" if x is None else f"{x:.2f}"
    return (f"h{r['hypothesis_id']} {r['strategy'] or '(no paper record)'} [{r['status']}]: {r['decision'].upper()}; "
            f"{r['days']} paper days, Sharpe {num(r['live_sharpe'])} (backtest {num(r['backtest_sharpe'])}), "
            f"worst fall {pct(r['live_max_drawdown'])} (backtest {pct(r['backtest_max_drawdown'])}); {r['reason']}")


def main(argv=None, paths: LoopPaths | None = None) -> int:
    ap = argparse.ArgumentParser(prog="decay", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="judge every paper strategy against its backtest")
    c.add_argument("--apply", action="store_true", help="pause what should be paused and journal the check")
    args = ap.parse_args(argv)
    paths = paths or LoopPaths()
    ledger_path, journal_dir = Path(paths.ledger), Path(paths.journal)
    if not ledger_path.is_file():
        print(f"the check could not run: there is no idea ledger at {ledger_path}", file=sys.stderr)
        return 2
    if not journal_dir.is_dir():
        print(f"the check could not run: there is no journal folder at {journal_dir}", file=sys.stderr)
        return 2
    ledger = None
    try:
        ledger = HypothesisLedger(ledger_path)
        out = check(ledger, Journal(journal_dir), apply=args.apply)
    except Exception as exc:  # anything that stops the check itself is exit 2, never a silent pass
        print(f"the check could not run: {type(exc).__name__}: {str(exc)[:300]}", file=sys.stderr)
        return 2
    finally:
        if ledger is not None:
            ledger.close()
    print(f"decay check as of {out['as_of']}" + (" (applied)" if args.apply else " (report only)"))
    if not out["strategies"]:
        print("no strategy is in the paper book")
    for r in out["strategies"]:
        print(_line(r))
    if out["paused"]:
        print("paused: " + ", ".join(f"h{h}" for h in out["paused"]))
    elif out["to_pause"]:
        print("to pause (run with --apply): " + ", ".join(f"h{h}" for h in out["to_pause"]))
    return int(out["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
