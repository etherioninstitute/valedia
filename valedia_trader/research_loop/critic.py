"""The weekly post-mortem. A critic reads the journal and says what happened.

Every number is computed here from the journal and the ledger: how each strategy's paper results
compare with its backtest and its written prediction, what the losing trades had in common, which
mistake keeps repeating, and whether a strategy should be paused. A model may be asked to put the
findings into words (`narrator`), but it is given the numbers and cannot change them. The review is
saved to `journal/review-<date>.md` and every finding is written to the ledger.

The critic also audits the kill switch: it re-runs `check_order` on every order the journal says
was placed. An order that should have been refused and was not is the first thing reported.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from valedia_trader.research_loop import risk_gate
from valedia_trader.research_loop.ledger import HypothesisLedger
from valedia_trader.research_loop.paper import Journal, describe_skipped

__all__ = ["CriticRules", "weekly_review"]


@dataclass(frozen=True)
class CriticRules:
    min_days_to_judge: int = 20        # paper results are judged over weeks, not days
    pause_z: float = -2.0              # pause when live returns sit this many standard errors below the backtest
    repeat: int = 3                    # the same thing three times is a pattern
    promote_days: int = 20             # trading days of paper results before 'passed paper' is possible
    promote_min_z: float = -1.0


def _fmt(x: float, pct: bool = True) -> str:
    if x is None or not math.isfinite(x):
        return "n/a"
    return f"{x:+.2%}" if pct else f"{x:.2f}"


def panel_scorecard(entries: list[dict]) -> dict | None:
    """How the decision panel's verdicts have turned out, from the journal alone. None until a case is settled."""
    verdict = {}
    for e in entries:
        if e.get("type") == "decision" and isinstance(e.get("panel"), dict) and e["panel"].get("available"):
            verdict[f"{e['strategy']}:{e['symbol']}:{e['day']}"] = bool(e["panel"]["approve"])
    settled = [(verdict[e["case_id"]], bool(e["thesis_right"])) for e in entries
               if e.get("type") == "panel_settled" and "thesis_right" in e and e.get("case_id") in verdict]
    if not settled:
        return None
    approved = [right for ok, right in settled if ok]; refused = [right for ok, right in settled if not ok]
    rate = lambda xs: (sum(xs) / len(xs)) if xs else None
    return {"settled": len(settled), "approved": len(approved), "refused": len(refused),
            "approved_came_true": rate(approved), "refused_came_true": rate(refused)}


def benchmark_comparison(entries: list[dict]) -> list[dict]:
    """Each paper strategy against simply holding its own funds, from the journal's day summaries. `days` is the number
    of days both were recorded, so the reader can see how little or how much the difference rests on."""
    seen: dict[str, list[dict]] = {}
    for e in last_summary_of_each_day(entries):
        if e.get("type") == "summary" and isinstance(e.get("benchmark_values"), dict):
            for name in e["benchmark_values"]:
                if name in e.get("strategy_values", {}):
                    seen.setdefault(name, []).append(e)
    rows = []
    for name, days in sorted(seen.items()):
        last = max(days, key=lambda e: e["day"])
        mine, held, capital = float(last["strategy_values"][name]), float(last["benchmark_values"][name]), float(last.get("capital_per_strategy") or 0.0)
        rows.append({"strategy": name, "days": len(days), "strategy_value": mine, "benchmark_value": held, "difference": mine - held,
                     "difference_pct_of_capital": (mine - held) / capital * 100.0 if capital else None, "as_of": last["day"]})
    return rows


def last_summary_of_each_day(entries: list[dict]) -> list[dict]:
    """`entries` with every day summary but the last of its day left out (a day run again writes its summary again; the
    last one is the day's truth). Everything else is kept, in order."""
    last = {e.get("day"): k for k, e in enumerate(entries) if e.get("type") == "summary"}
    keep = set(last.values())
    return [e for k, e in enumerate(entries) if e.get("type") != "summary" or k in keep]


def _price_field(e: dict, key: str) -> set | None:
    """The funds a summary names under `key` (`stale_prices` or `suspect_prices`): a mapping's keys, or a list of fund
    names; an empty set when the field is absent; None when it is there but cannot be read (a number, a string, null, a
    list of anything but names)."""
    if key not in e:
        return set()
    v = e[key]
    if isinstance(v, dict):
        return set(v)
    if isinstance(v, list) and all(isinstance(x, str) for x in v):
        return set(v)
    return None


def unreadable_days(entries: list[dict], name: str) -> list[bool]:
    """For each of the strategy's day summaries in `entries`, in order (as `carried_days`): whether its `stale_prices` or
    `suspect_prices` field is there but cannot be read."""
    return [_price_field(e, "stale_prices") is None or _price_field(e, "suspect_prices") is None
            for e in entries if e.get("type") == "summary" and name in e.get("strategy_value_change", {})]


def carried_days(entries: list[dict], name: str) -> tuple[list[bool], list[str]]:
    """For each of the strategy's day summaries in `entries` (those whose `strategy_value_change` names it, in order):
    whether the strategy held a fund that day whose price was carried (the summary's `stale_prices`), and the funds that
    made any day carried. What the strategy held comes from its fills in the same entries, up to and including the day."""
    held: dict[str, float] = {}
    marks, funds = [], set()
    for e in entries:
        if e.get("type") == "fill" and e.get("strategy") == name:
            held[e["symbol"]] = held.get(e["symbol"], 0.0) + float(e.get("units") or 0.0)
        elif e.get("type") == "summary" and name in e.get("strategy_value_change", {}):
            stale = _price_field(e, "stale_prices") or set()           # an unreadable field: see `unreadable_days`
            hit = {sym for sym in stale if abs(held.get(sym, 0.0)) > 1e-12}
            marks.append(bool(hit))
            funds |= hit
    return marks, sorted(funds)


def weekly_review(ledger: HypothesisLedger, journal: Journal, as_of, strategies: dict[int, str], limits_file: str | Path,
                  days: int = 7, rules: CriticRules | None = None, apply: bool = True,
                  narrator: Callable[[str], str] | None = None) -> dict:
    """Review the `days` up to `as_of`. `strategies` maps hypothesis id to strategy name. With
    `apply`, a strategy that should be paused is paused in the ledger. Returns the findings; the
    markdown review is written next to the journal."""
    rules = rules or CriticRules()
    as_of = pd.Timestamp(as_of)
    since = as_of - pd.Timedelta(days=days - 1)
    everything = last_summary_of_each_day(journal.read(until=as_of))
    skipped = [dict(s) for s in getattr(journal, "skipped_lines", [])]
    incomplete = bool(skipped)
    week = [e for e in everything if e["day"] >= str(since.date())]
    findings: list[tuple[int | None, str]] = []
    incomplete_text = (f"INCOMPLETE REVIEW: {len(skipped)} journal line{'s' if len(skipped) != 1 else ''} could not be read "
                       f"({describe_skipped(skipped)}). The figures below leave out whatever was on them: no strategy is promoted "
                       "and none is called free of reason to pause; a pause the readable entries justify is still ordered.")
    if incomplete:
        findings.append((None, incomplete_text))

    # ---- audit of the kill switch
    try:
        limits = risk_gate.load_limits(limits_file)
    except risk_gate.RiskConfigError as exc:
        limits = None
        findings.append((None, f"The risk limits file could not be read ({exc}); every order is blocked until it is fixed."))
    escaped = []
    if limits is not None:
        for e in week:
            if e["type"] == "decision" and e.get("decision") == "placed" and "account" in e:
                ok, why = risk_gate.check_order(e["order"], e["account"], limits, halt_file=None)
                if not ok:
                    escaped.append((e, why))
    for e, why in escaped:
        findings.append((e.get("hypothesis_id"), f"KILL SWITCH FAILURE: an order for {e['symbol']} on {e['day']} was placed although the "
                         f"limits refuse it ({why}). The check is not running before every order. Fix this before anything else."))
    blocks = Counter(e["risk_check"] for e in week if e["type"] == "decision" and e.get("decision") == "blocked")
    for reason, n in blocks.items():
        if n >= rules.repeat:
            findings.append((None, f"The kill switch refused {n} orders this week for the same reason: {reason}"))
    for e in week:
        if e["type"] == "kill_switch":
            findings.append((None, f"Kill switch {e['decision']} on {e['day']}: {e['reasoning']}"))

    # ---- the decision panel against what happened
    card = panel_scorecard(everything)
    if card:
        a, r = card["approved_came_true"], card["refused_came_true"]
        text = (f"Decision panel: {card['settled']} judged orders have reached their horizon. It approved {card['approved']}"
                + (f" ({a:.0%} went the predicted way)" if a is not None else "") + f" and refused {card['refused']}"
                + (f" ({r:.0%} went the predicted way)" if r is not None else "") + ".")
        if card["refused"] >= 10 and a is not None and r is not None:
            text += (" Its refusals picked out worse orders; veto mode is worth considering." if r < a - 0.10
                     else " Its refusals did no better than its approvals; keep it in shadow mode.")
        else:
            text += " Too few refusals to judge whether a veto would help."
        findings.append((None, text))

    # ---- each strategy against simply holding its funds
    for row in benchmark_comparison(everything):
        text = (f"{row['strategy']}: {row['strategy_value']:+,.0f} in paper against {row['benchmark_value']:+,.0f} for simply holding its funds "
                f"over {row['days']} days")
        if row["difference_pct_of_capital"] is not None:
            text += f" ({row['difference_pct_of_capital']:+.2f}% of its capital)"
        text += (". That is too few days to mean anything." if row["days"] < rules.min_days_to_judge else
                 ". It is behind holding." if row["difference"] < 0 else ". It is ahead of holding.")
        findings.append((None, text))

    # ---- each strategy against its backtest and its prediction
    per_strategy = {}
    for hid, name in strategies.items():
        h = ledger.get(hid)
        summaries = [e for e in everything if e["type"] == "summary" and name in e.get("strategy_value_change", {})]
        # a day's change was earned on the capital the day started with: the capital per strategy of the day before
        # (a strategy's first day: its own first capital). The day's own capital shrinks when strategies join.
        started = [e["capital_per_strategy"] for e in summaries[:1]] + [e["capital_per_strategy"] for e in summaries[:-1]]
        returns = np.array([e["strategy_value_change"][name] / cap for e, cap in zip(summaries, started)], dtype=float)
        in_week = np.array([e["strategy_value_change"][name] / cap for e, cap in zip(summaries, started)
                            if e["day"] >= str(since.date())], dtype=float)
        # a day on which the strategy held a fund whose price was carried is no evidence of holding up: it is left out of
        # the days that count toward passing paper (and nothing else; the pause rules use every day)
        # so is a day whose carried- or suspect-price field cannot be read: it cannot say what the day's prices were
        carried, carried_funds = carried_days(everything, name)
        unreadable = unreadable_days(everything, name)
        n_carried, n_unreadable = sum(carried), sum(unreadable)
        out_of_count = np.array(carried, dtype=bool) | np.array(unreadable, dtype=bool)
        kept = returns[~out_of_count] if len(carried) == len(returns) else returns
        closed = [e for e in everything if e["type"] == "fill" and e["strategy"] == name and e.get("realized_pnl") is not None]
        losers = [e for e in closed if e["realized_pnl"] < 0 and e["day"] >= str(since.date())]
        bt = ((h["backtest_result"] or {}).get("result") or {})
        prediction = (ledger.prediction(hid) or {}).get("expected", {})
        n = len(returns)
        live_total = float(np.prod(1.0 + returns) - 1.0) if n else float("nan")
        live_annual = float(np.mean(returns) * 252.0) if n else float("nan")
        curve = np.concatenate([[1.0], np.cumprod(1.0 + returns)])        # drawdown is measured from the starting capital
        live_dd = float(np.max(1.0 - curve / np.maximum.accumulate(curve))) if n else 0.0
        bt_mu, bt_sd = bt.get("cagr"), bt.get("annual_vol")
        z = float("nan")
        if n >= 2 and bt_mu is not None and bt_sd:
            z = (float(np.mean(returns)) - bt_mu / 252.0) / (bt_sd / math.sqrt(252.0) / math.sqrt(n))
        win = float(np.mean([e["realized_pnl"] > 0 for e in closed])) if closed else float("nan")
        result = {"days": n, "week_return": float(np.prod(1.0 + in_week) - 1.0) if len(in_week) else float("nan"),
                  "total_return": live_total, "annualised_return": live_annual, "max_drawdown": live_dd,
                  "closed_trades": len(closed), "trade_win_rate": win, "z_vs_backtest": z,
                  "backtest_annual_return": bt_mu, "backtest_max_drawdown": bt.get("max_drawdown"),
                  "predicted_annual_return": prediction.get("annual_return"), "predicted_win_rate": prediction.get("win_rate")}
        n_kept, total_kept, z_kept = len(kept), float(np.prod(1.0 + kept) - 1.0) if len(kept) else float("nan"), float("nan")
        if n_kept >= 2 and bt_mu is not None and bt_sd:
            z_kept = (float(np.mean(kept)) - bt_mu / 252.0) / (bt_sd / math.sqrt(252.0) / math.sqrt(n_kept))
        if n_carried:
            result.update({"carried_price_days": n_carried, "carried_price_funds": carried_funds})
        if n_unreadable:
            result["unreadable_price_days"] = n_unreadable
        notes = []
        if n_carried:
            notes.append(f"{name}: {n_carried} paper day{'s' if n_carried != 1 else ''} with a carried price ({', '.join(carried_funds)}) "
                         f"{'are' if n_carried != 1 else 'is'} left out of the days that count toward passing paper; "
                         f"{n_kept} of its {n} days count.")
        if n_unreadable:
            notes.append(f"{name}: {n_unreadable} paper day{'s' if n_unreadable != 1 else ''} whose carried-price field could not be read "
                         f"{'are' if n_unreadable != 1 else 'is'} left out of the days that count toward passing paper; "
                         f"{n_kept} of its {n} days count.")
        if n and bt_mu is not None and math.isfinite(live_annual) and live_annual < bt_mu:
            notes.append(f"{name} is running at {_fmt(live_annual)} a year on paper against {_fmt(bt_mu)} in its backtest, "
                         f"{_fmt(bt_mu - live_annual)} worse ({n} days, z {_fmt(z, False)}).")
        if prediction and closed and math.isfinite(win) and win < prediction.get("win_rate", 0.0):
            notes.append(f"{name} wins {win:.0%} of its trades on paper; the prediction said {prediction['win_rate']:.0%}.")
        regimes = Counter((e.get("entry_conditions") or {}).get("regime", "unknown") for e in losers)
        if losers:
            regime, count = regimes.most_common(1)[0]
            if count >= 2:
                notes.append(f"{count} of {name}'s {len(losers)} losing trades this week were opened in a {regime} market.")
        by_symbol = Counter(e["symbol"] for e in losers)
        by_reason = Counter(str(e.get("reason", "")).split(";")[0] for e in losers)
        for label, counter in (("on", by_symbol), ("with the same exit", by_reason)):
            for key, count in counter.items():
                if count >= rules.repeat:
                    notes.append(f"Repeating mistake: {name} lost {count} times this week {label} {key}.")
        pause = None
        if n >= rules.min_days_to_judge and math.isfinite(z) and z <= rules.pause_z:
            pause = f"paper returns are {abs(z):.1f} standard errors below the backtest after {n} days: the backtest was overfit"
        elif bt.get("max_drawdown") is not None and live_dd > bt["max_drawdown"] and live_dd > 0:
            pause = f"paper drawdown {live_dd:.1%} is already deeper than the worst drawdown of the whole backtest ({bt['max_drawdown']:.1%})"
        elif any(e.get("hypothesis_id") == hid for e, _ in escaped):
            pause = "one of its orders got past the kill switch"
        status = h["status"]
        if pause:
            notes.append(f"PAUSE {name}: {pause}.")
            if apply and status not in ("paused", "failed", "retired"):
                ledger.set_status(hid, "paused", pause, "critic")
                status = "paused"
        elif (not incomplete and status == "passed backtest only" and n_kept >= rules.promote_days and math.isfinite(z_kept)
              and z_kept >= rules.promote_min_z and total_kept > 0):
            notes.append(f"{name} has held up for {n_kept} paper days (z {_fmt(z_kept, False)} against its backtest).")
            if apply:
                ledger.set_status(hid, "passed paper", f"{n_kept} paper days, return {_fmt(total_kept)}, z {z_kept:.2f} against the backtest",
                                  "critic")
                status = "passed paper"
        elif n < rules.min_days_to_judge:
            notes.append(f"{name} has {n} paper days. That is too few to judge; the bar is {rules.min_days_to_judge}.")
        if incomplete and not pause:
            notes.append(f"{name}: the journal is missing entries, so this review cannot say there is no reason to pause it.")
        result["status"] = status
        if incomplete:
            result["incomplete"] = True
        ledger.record_paper(hid, {**result, "as_of": str(as_of.date())})
        for note in notes:
            findings.append((hid, note))
        per_strategy[name] = {"result": result, "notes": notes}

    for hid, text in findings:
        ledger.add_lesson(hid, text, "critic")

    lines = ([f"**{incomplete_text}**", ""] if incomplete else []) + [f"# Critic's review, {since.date()} to {as_of.date()}", ""]
    urgent = [t for _, t in findings if t.startswith("KILL SWITCH FAILURE")]
    if urgent:
        lines += ["## Fix first", ""] + [f"- {t}" for t in urgent] + [""]
    lines += ["## Strategies", ""]
    if not per_strategy:
        lines += ["No strategy is in paper trading.", ""]
    for name, item in per_strategy.items():
        r = item["result"]
        lines += [f"### {name} ({r['status']})", "",
                  f"- Paper: {_fmt(r['week_return'])} this week, {_fmt(r['total_return'])} over {r['days']} days, "
                  f"worst drawdown {r['max_drawdown']:.1%}, {r['closed_trades']} closed trades",
                  f"- Backtest: {_fmt(r['backtest_annual_return'])} a year; prediction: {_fmt(r['predicted_annual_return'])} a year"]
        lines += [f"- {n}" for n in item["notes"]] + [""]
    account_notes = [t for hid, t in findings if hid is None]
    lines += ["## Account and kill switch", ""] + ([f"- {t}" for t in account_notes] or ["- Nothing to report."]) + [""]
    body = "\n".join(lines)
    if narrator is not None:
        try:
            said = narrator("You are the Critic of a paper-trading desk. Be blunt. Using only the facts below, say in at most "
                            "150 words what went wrong, what keeps repeating and what should be paused. Do not add numbers.\n\n" + body)
            body += "\n## The critic's read (written by a model from the numbers above)\n\n" + str(said).strip() + "\n"
        except Exception as exc:  # the review stands without the narration
            body += f"\n(The narrator failed: {type(exc).__name__}.)\n"
    path = journal.dir / f"review-{as_of.date()}.md"
    path.write_text(body)
    return {"path": str(path), "findings": [t for _, t in findings], "strategies": per_strategy, "kill_switch_failures": len(escaped),
            "incomplete": incomplete, "skipped_lines": skipped}
