"""The research agents: several specialists, each looking at the market from one angle.

Two rules, kept by the code and not by the prompt:

* They work separately. Every proposer is asked before any memo is written, and each prompt holds
  only that agent's own angle, so none can anchor on another's opinion.
* The ledger is checked first. What was already tested on the market goes into the prompt, and an
  idea that repeats a failed one is refused by the ledger itself unless the agent states that
  conditions have changed.

An agent proposes at most three ideas. Each must say the idea in one sentence, why it might work,
the market conditions it needs and what result would prove it wrong, and it must come with a
strategy built from the rule library and a prediction. Nothing an agent writes is run as code. The
prediction is written to the ledger at once, which is before any backtest can happen.

The agents see the development period only. Showing them the sealed period would spend it.
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

import numpy as np
import pandas as pd

from valedia_trader.research_loop import LoopPaths
from valedia_trader.research_loop import strategy as st
from valedia_trader.research_loop.gauntlet import Prediction, write_prediction
from valedia_trader.research_loop.ledger import DuplicateIdea, HypothesisLedger

__all__ = ["Role", "ROLES", "MAX_IDEAS", "price_table", "macro_table", "PRICE_COLUMNS", "MACRO_COLUMNS", "build_prompt", "parse_ideas", "run_research",
           "hermes_asker", "chat_asker", "RULE_LIBRARY"]

MAX_IDEAS = 3
Asker = Callable[[str], str]


@dataclass(frozen=True)
class Role:
    key: str
    title: str
    angle: str
    table: Callable[[pd.DataFrame, pd.DataFrame | None], str] | None
    unavailable: str = ""


def _fmt(df: pd.DataFrame) -> str:
    return df.round(3).to_csv()


SESSIONS_PER_YEAR = 252
_CALENDAR_TOLERANCE = 0.05
CORRELATION_WINDOW = 60


@dataclass(frozen=True)
class ColumnDef:
    """What one table column is. `window` is the number of sessions the column looks over (for a move, how far back it
    compares); `baseline_window` is the number of sessions of the second statistic it is set against, when there is one."""
    unit: str
    window: int
    sentence: str
    baseline_window: int | None = None


# The one place a column's unit, window and meaning are written. The tables take their windows from here and the legend in the
# prompt is generated from here, so a number printed in a table and the words that describe it cannot drift apart.
PRICE_COLUMNS: dict[str, ColumnDef] = {
    "ret_1m": ColumnDef("fraction", 21, "Last close divided by the close 21 sessions earlier, minus 1."),
    "ret_3m": ColumnDef("fraction", 63, "Last close divided by the close 63 sessions earlier, minus 1."),
    "ret_12m": ColumnDef("fraction", 252, "Last close divided by the close 252 sessions earlier, minus 1."),
    "vol_20d": ColumnDef("fraction", 20, "Standard deviation of the last 20 simple daily returns, times the square root of 252."),
    "vol_1y": ColumnDef("fraction", 252, "Standard deviation of the last 252 simple daily returns, times the square root of 252."),
    "below_1y_high": ColumnDef("fraction", 252, "Last close divided by the highest close of the last 252 sessions, minus 1 (0 means at the high)."),
    "vs_200d_avg": ColumnDef("fraction", 200, "Last close divided by the average close of the last 200 sessions, minus 1."),
    "move_5d_in_sd": ColumnDef("standard deviations", 5, "The 5-session change (last close over the close 5 sessions earlier, minus 1) "
                                                         "divided by the standard deviation of the last 60 daily returns times the "
                                                         "square root of 5.", baseline_window=60),
    "volume_20d_vs_1y": ColumnDef("fraction", 20, "Average dollar volume of the last 20 sessions divided by the average of the last "
                                                  "252 sessions, minus 1.", baseline_window=252),
}
MACRO_COLUMNS: dict[str, ColumnDef] = {
    "chg_3m": ColumnDef("fraction", 63, "The price ratio of the pair now divided by the ratio 63 sessions earlier, minus 1."),
    "chg_12m": ColumnDef("fraction", 252, "The price ratio of the pair now divided by the ratio 252 sessions earlier, minus 1."),
    "vs_200d_avg": ColumnDef("fraction", 200, "The price ratio of the pair now divided by its average over the last 200 sessions, minus 1."),
}
_PRICE = {k: v.window for k, v in PRICE_COLUMNS.items()}
_PRICE_BASE = {k: v.baseline_window for k, v in PRICE_COLUMNS.items()}


def _legend(defs: dict[str, ColumnDef], columns) -> str:
    """The text that goes above a table: how to read the values, then one line per column the table has. A column with no
    definition is refused, so a new column cannot reach a prompt unlabelled. No columns, no legend."""
    columns = list(columns)
    unknown = [c for c in columns if c not in defs]
    if unknown:
        raise ValueError(f"table column(s) with no definition: {', '.join(map(str, unknown))}")
    if not columns:
        return ""
    lines = [f"How to read the table below. Values are fractions unless the unit says otherwise (0.052 means 5.2%). Windows count "
             f"trading sessions of this data (1m = 21 sessions, 3m = 63, 12m and 1y = {SESSIONS_PER_YEAR}). Annual figures use "
             f"{SESSIONS_PER_YEAR} sessions a year. Values are rounded to 3 decimals. Columns:"]
    for name in columns:
        d = defs[name]
        span = f"window {d.window} sessions" + (f", baseline {d.baseline_window} sessions" if d.baseline_window else "")
        lines.append(f"- {name} [{d.unit}; {span}]: {d.sentence}")
    return "\n".join(lines) + "\n"


def _check_calendar(prices: pd.DataFrame) -> None:
    """The legend says 21 rows are one month, so refuse a frame whose rows are not on a 252-sessions-a-year calendar (a seven-day
    crypto calendar, say). The rate is measured over the whole frame: rows divided by the years the index spans. A frame spanning
    less than one year is not judged: its rate is too noisy to measure, and its tables cannot fill a 12-month window anyway."""
    index = pd.DatetimeIndex(prices.index)
    if len(index) < 2:
        return
    years = (index.max() - index.min()).days / 365.25
    if years < 1.0:
        return
    rate = len(index) / years
    if abs(rate - SESSIONS_PER_YEAR) > _CALENDAR_TOLERANCE * SESSIONS_PER_YEAR:
        raise ValueError(f"the price frame has {rate:.1f} rows a year, not {SESSIONS_PER_YEAR} sessions a year (within 5%); the "
                         "tables count rows as sessions, so they are not built for this calendar")


def price_table(prices: pd.DataFrame, dollar_volume: pd.DataFrame | None = None) -> str:
    """Price and volume facts per symbol at the last row of `prices`, under a legend generated from PRICE_COLUMNS."""
    _check_calendar(prices)
    w, b, last = _PRICE, _PRICE_BASE, prices.iloc[-1]
    r = prices.pct_change()
    out = pd.DataFrame({
        "ret_1m": last / prices.shift(w["ret_1m"]).iloc[-1] - 1, "ret_3m": last / prices.shift(w["ret_3m"]).iloc[-1] - 1,
        "ret_12m": last / prices.shift(w["ret_12m"]).iloc[-1] - 1,
        "vol_20d": r.tail(w["vol_20d"]).std() * np.sqrt(SESSIONS_PER_YEAR),
        "vol_1y": r.tail(w["vol_1y"]).std() * np.sqrt(SESSIONS_PER_YEAR),
        "below_1y_high": last / prices.tail(w["below_1y_high"]).max() - 1,
        "vs_200d_avg": last / prices.tail(w["vs_200d_avg"]).mean() - 1,
        "move_5d_in_sd": (last / prices.shift(w["move_5d_in_sd"]).iloc[-1] - 1)
                         / (r.tail(b["move_5d_in_sd"]).std() * np.sqrt(w["move_5d_in_sd"])),
    })
    if dollar_volume is not None:
        out["volume_20d_vs_1y"] = (dollar_volume.tail(w["volume_20d_vs_1y"]).mean()
                                   / dollar_volume.tail(b["volume_20d_vs_1y"]).mean() - 1)
    return _legend(PRICE_COLUMNS, out.columns) + _fmt(out)


_PAIRS = (("TLT", "IEF", "long bonds vs intermediate (curve)"), ("LQD", "IEF", "credit vs treasuries"),
          ("XLK", "XLU", "tech vs utilities (risk appetite)"), ("EEM", "EFA", "emerging vs developed"),
          ("DBC", "GLD", "commodities vs gold"), ("XLE", "SPY", "energy vs market"), ("XLF", "SPY", "financials vs market"))


def macro_table(prices: pd.DataFrame, dollar_volume: pd.DataFrame | None = None) -> str:
    """Rates, credit, commodity and sector trends as far as the traded instruments show them, under a legend generated from
    MACRO_COLUMNS. No economic data feed is connected; this table is cross-asset prices only. The SPY-TLT correlation is not a
    row of the table (it is not a change in a ratio): it is its own line after it, when both funds are in the frame."""
    _check_calendar(prices)
    c3, c12, avg = (MACRO_COLUMNS[k].window for k in ("chg_3m", "chg_12m", "vs_200d_avg"))
    rows = {}
    for a, b, label in _PAIRS:
        if a in prices and b in prices:
            ratio = (prices[a] / prices[b]).dropna()
            if len(ratio) > c12:
                rows[f"{a}/{b} {label}"] = {"chg_3m": ratio.iloc[-1] / ratio.iloc[-1 - c3] - 1,
                                            "chg_12m": ratio.iloc[-1] / ratio.iloc[-1 - c12] - 1,
                                            "vs_200d_avg": ratio.iloc[-1] / ratio.tail(avg).mean() - 1}
    out = pd.DataFrame(rows).T
    text = _legend(MACRO_COLUMNS, out.columns) + _fmt(out)
    r = prices.pct_change()
    if "SPY" in r and "TLT" in r:
        corr = r["SPY"].tail(CORRELATION_WINDOW).corr(r["TLT"].tail(CORRELATION_WINDOW))
        if pd.notna(corr):
            text += f"SPY-TLT correlation of daily returns, last {CORRELATION_WINDOW} sessions: {corr:.3f}\n"
    return text


ROLES: dict[str, Role] = {
    "price-analyst": Role("price-analyst", "Price Analyst", "price and volume patterns", price_table),
    "fundamentals-reader": Role("fundamentals-reader", "Fundamentals Reader", "earnings reports and company filings", None,
                                "no earnings or filings source is connected, and the universe is funds and crypto"),
    "macro-watcher": Role("macro-watcher", "Macro Watcher",
                          "interest rates, credit, commodities and sector trends, as the cross-asset prices show them", macro_table),
}
SKEPTIC = Role("skeptic", "Skeptic", "why each idea will fail", None)


def _library_help() -> str:
    lines = ["entry rules (a score; a long opens when score >= enter and closes when score <= exit; shorts mirror it):"]
    for name, fn in st.SCORES.items():
        params = ", ".join(f"{k} in [{lo}, {hi}]" for k, (lo, hi) in st._SCORE_PARAMS[name].items()) or "none"
        lines.append(f"  {name}: {fn.__doc__.strip().splitlines()[0]} Parameters: {params}.")
    lines.append('  every entry also needs "enter", "exit" (exit <= enter) and "direction": "long" | "short" | "both"')
    lines.append("sizing rules:")
    for name in st.SIZING:
        params = ", ".join(f"{k} in [{lo}, {hi}]" for k, (lo, hi) in st._SIZING_PARAMS[name].items()) or "none"
        lines.append(f"  {name}: parameters {params}")
    lines.append("risk: max_position_pct (0-1], max_open_positions >= 1, max_gross (0-2], stop_loss_pct, take_profit_pct, "
                 "max_holding_days (the last three may be null)")
    return "\n".join(lines)


RULE_LIBRARY = _library_help()

_REPLY_FORMAT = """Reply with JSON only, in this shape:
{"ideas": [{"idea": "<one sentence>", "why": "<why it might work>", "conditions": "<the exact market conditions it needs>",
  "falsifier": "<what result would prove it wrong>", "conditions_changed": false, "symbols": ["<from the list above>"],
  "strategy": {"entry": {"rule": "...", "enter": 0.0, "exit": 0.0, "direction": "long"}, "sizing": {"rule": "equal"},
               "risk": {"max_position_pct": 0.1, "max_open_positions": 5, "stop_loss_pct": 0.1}},
  "prediction": {"annual_return": 0.04, "win_rate": 0.5, "sharpe": 0.5, "fail_if_sharpe_below": 0.2, "fail_if_drawdown_above": 0.15}}]}
The prediction is what you expect a backtest net of costs to show and what would count as failure. It is saved before
any backtest and can never be edited, so state what you actually believe."""


def _limits_line(account_limits: dict | None) -> str:
    if not account_limits:
        return ""
    return (f"The account's kill switch refuses any position above {account_limits['max_position_pct']:.0%} of capital and any new "
            f"position once {int(account_limits['max_open_positions'])} are open. Design within that: a strategy's max_position_pct and "
            "max_open_positions are cut down to these limits before it is tested.\n\n")


def _reading_block(reading: list[dict] | None) -> str:
    """Owner-approved notes from the research desk's library. Background for ideas, never a result."""
    if not reading:
        return ""
    lines = "\n".join(f"- {' '.join(str(n.get('title', '')).split())[:120]}: {' '.join(str(n.get('takeaway', '')).split())[:320]}" for n in reading)
    return ("Outside reading the owner approved (published work, summarised by the research desk). It is background, not "
            f"evidence for this market, and it may describe periods you cannot see; an idea drawn from it is tested like any other:\n{lines}\n\n")


def build_prompt(role: Role, market: str, symbols: list[str], as_of, table: str, tested: list[dict],
                 account_limits: dict | None = None, reading: list[dict] | None = None) -> str:
    seen = "\n".join(f"- #{h['id']} [{h['status']}] {h['idea']}" + (f" (lesson: {h['lessons_learned'].strip().splitlines()[-1][:160]})"
                                                                   if h["lessons_learned"].strip() else "") for h in tested) or "- nothing yet"
    return (f"You are the {role.title}. Look only at {role.angle} for {market} ({', '.join(symbols)}).\n\n"
            f"Ideas already tested on this market, from the hypothesis ledger. Do not repeat any that failed unless market "
            f"conditions have clearly changed (then set conditions_changed to true and say what changed):\n{seen}\n\n"
            f"Your data, as of {pd.Timestamp(as_of).date()} (nothing later exists for you):\n{table}\n"
            f"{_limits_line(account_limits)}{_reading_block(reading)}"
            f"Propose at most {MAX_IDEAS} testable trading ideas. For each, state the idea in one sentence, why it might work, "
            f"the exact market conditions it needs, and what result would prove it wrong. Each idea must be expressed with "
            f"this rule library, because only these rules can be tested:\n{RULE_LIBRARY}\n\n{_REPLY_FORMAT}")


def parse_ideas(text: str) -> list[dict]:
    """The list under "ideas" in a model reply (code fences and surrounding prose are tolerated)."""
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        raise ValueError("no JSON object in the reply")
    ideas = json.loads(match.group(0)).get("ideas")
    if not isinstance(ideas, list):
        raise ValueError('the reply has no "ideas" list')
    return [i for i in ideas if isinstance(i, dict)]


def _slug(role_key: str, idea: str, hypothesis_id: int) -> str:
    words = [w for w in re.findall(r"[a-z]+", idea.lower()) if len(w) > 3][:3]
    prefix = "".join(p[0] for p in role_key.split("-"))
    return ("_".join([prefix, *words])[:32].rstrip("_") + f"_h{hypothesis_id}")


def _accept(raw: dict, role: Role, market: str, universe: list[str], ledger: HypothesisLedger, paths: LoopPaths,
            account_limits: dict | None = None) -> dict:
    """Validate one proposed idea and, if it stands, record it with its strategy and prediction. The
    strategy's own position limits are cut down to the account's, so what is tested is what the kill
    switch will let trade."""
    text = {k: " ".join(str(raw.get(k, "")).split()) for k in ("idea", "why", "conditions", "falsifier")}
    out: dict[str, Any] = {**text, "accepted": False}
    missing = [k for k, v in text.items() if len(v.split()) < 3]
    if missing:
        return {**out, "reason": f"incomplete: {', '.join(missing)} not stated"}
    symbols = [s for s in raw.get("symbols") or [] if s in universe] or list(universe)
    proposed = raw.get("strategy") if isinstance(raw.get("strategy"), dict) else {}
    draft = {"name": "draft_strategy", "version": "1.0.0", "hypothesis": text["idea"], "market": symbols,
             **{k: proposed[k] for k in ("entry", "sizing", "risk") if proposed.get(k) is not None}}
    if account_limits and isinstance(draft.get("risk", {}), dict):
        risk, defaults = dict(draft.get("risk", {})), st.StrategyRisk()
        for key, cap in (("max_position_pct", float(account_limits["max_position_pct"])),
                         ("max_open_positions", int(account_limits["max_open_positions"]))):
            wanted = risk.get(key, getattr(defaults, key))
            if isinstance(wanted, (int, float)) and not isinstance(wanted, bool) and wanted > cap:
                risk[key] = cap
                out.setdefault("cut_to_account_limits", []).append(f"{key} {wanted} -> {cap}")
        draft["risk"] = risk
    try:
        st.RuleStrategy.from_spec(draft)
        prediction = Prediction(**{k: float(v) for k, v in (raw.get("prediction") or {}).items() if k != "notes"})
    except (st.SpecError, ValueError, TypeError) as exc:
        return {**out, "reason": f"not testable as written: {exc}"}
    try:
        hid = ledger.propose(text["idea"], role.key, market, text["why"], text["conditions"], text["falsifier"],
                             conditions_changed=raw.get("conditions_changed") is True)
    except DuplicateIdea as exc:
        return {**out, "reason": str(exc)}
    strategy = st.RuleStrategy.from_spec({**draft, "name": _slug(role.key, text["idea"], hid)})
    path = st.save_spec(strategy, paths.strategies)
    write_prediction(ledger, paths, hid, strategy.name, prediction)
    return {**out, "accepted": True, "hypothesis_id": hid, "strategy": strategy.name, "strategy_file": str(path.relative_to(paths.root)),
            "spec": strategy.to_spec(), "prediction": prediction.__dict__}


def _memo(role: Role, today: str, market: str, as_of, tested: list[dict], results: list[dict], note: str = "") -> str:
    lines = [f"# {role.title} memo, {today}", "", f"Market: {market}. Data as of {pd.Timestamp(as_of).date()} (development period only).", "",
             f"Ledger check: {len(tested)} ideas were already on record for this market"
             + (": " + "; ".join(f"#{h['id']} {h['status']}" for h in tested[:12]) if tested else "") + ".", ""]
    if note:
        lines += [note, ""]
    for i, r in enumerate(results, start=1):
        lines += [f"## Idea {i}: {r.get('idea') or '(no idea stated)'}", "", f"- Why it might work: {r.get('why', '')}",
                  f"- Conditions it needs: {r.get('conditions', '')}", f"- What would prove it wrong: {r.get('falsifier', '')}"]
        if r["accepted"]:
            p = r["prediction"]
            if r.get("cut_to_account_limits"):
                lines.append("- Cut to the account's limits: " + ", ".join(r["cut_to_account_limits"]))
            lines += [f"- Recorded as hypothesis #{r['hypothesis_id']}, strategy `{r['strategy_file']}`",
                      f"- Prediction (now locked): {p['annual_return']:+.1%} a year, {p['win_rate']:.0%} winning trades, Sharpe "
                      f"{p['sharpe']:.2f}; failed if Sharpe is below {p['fail_if_sharpe_below']:.2f} or drawdown exceeds "
                      f"{p['fail_if_drawdown_above']:.0%}"]
        else:
            lines.append(f"- Not recorded: {r['reason']}")
        lines.append("")
    return "\n".join(lines)


def run_research(ledger: HypothesisLedger, paths: LoopPaths, dev_prices: pd.DataFrame, dev_dollar_volume: pd.DataFrame | None,
                 market: str, askers: dict[str, Asker], skeptic: Asker | None = None, today: str | None = None,
                 account_limits: dict | None = None, reading: list[dict] | None = None) -> dict:
    """One research round. `reading` is the desk library's owner-approved notes (title and takeaway). `askers` maps role keys to a function that sends a prompt to that agent
    and returns its reply. Pass `gauntlet.dev_prices`: the agents must never see the sealed period.
    `account_limits` are the kill switch's limits (config/risk.json); strategies are held to them."""
    today = today or str(date.today())
    unknown = set(askers) - set(ROLES)
    if unknown:
        raise ValueError(f"unknown research roles: {sorted(unknown)}")
    universe, as_of = list(dev_prices.columns), dev_prices.index[-1]
    tested = ledger.search(market=market)
    # 1. every proposer answers before anything is written, each from its own angle only
    replies: dict[str, tuple[str | None, str]] = {}
    for key, ask in askers.items():
        role = ROLES[key]
        if role.table is None:
            replies[key] = (None, f"Skipped: {role.unavailable}.")
            continue
        try:
            replies[key] = (ask(build_prompt(role, market, universe, as_of, role.table(dev_prices, dev_dollar_volume), tested,
                                             account_limits, reading)), "")
        except Exception as exc:  # one agent failing does not stop the others
            replies[key] = (None, f"The agent did not answer ({type(exc).__name__}: {exc}).")
    # 2. validate, record and write the memos
    summary: dict[str, Any] = {"date": today, "market": market, "roles": {}, "accepted": []}
    for key, (reply, note) in replies.items():
        role, results = ROLES[key], []
        if reply is not None:
            try:
                ideas = parse_ideas(reply)
                if len(ideas) > MAX_IDEAS:
                    note = f"The agent proposed {len(ideas)} ideas; only the first {MAX_IDEAS} were considered."
                results = [_accept(raw, role, market, universe, ledger, paths, account_limits) for raw in ideas[:MAX_IDEAS]]
            except (ValueError, json.JSONDecodeError) as exc:
                note = f"The reply could not be read ({exc})."
        memo = paths.research / f"{key}-{today}.md"
        memo.parent.mkdir(parents=True, exist_ok=True)
        memo.write_text(_memo(role, today, market, as_of, tested, results, note))
        summary["roles"][key] = {"memo": str(memo.relative_to(paths.root)), "proposed": len(results),
                                 "accepted": sum(r["accepted"] for r in results), "note": note,
                                 "rejected": [r["reason"] for r in results if not r["accepted"]]}
        summary["accepted"] += [r for r in results if r["accepted"]]
    # 3. the skeptic argues against what was accepted
    if skeptic is not None and summary["accepted"]:
        listing = "\n".join(f"#{r['hypothesis_id']}: {r['idea']} Why: {r['why']} Needs: {r['conditions']}" for r in summary["accepted"])
        failures = "\n".join(f"- {h['idea']}" for h in tested if h["status"] == "failed") or "- none on record"
        prompt = (f"You are the Skeptic. Your only job is to argue why each idea below will fail on {market}.\n\nIdeas:\n{listing}\n\n"
                  f"Ideas that already failed on this market:\n{failures}\n\nFor each idea give the strongest reason it will fail and "
                  'the test most likely to break it. Reply with JSON only: {"objections": [{"hypothesis_id": 0, '
                  '"why_it_fails": "...", "kill_test": "..."}]}')
        lines, count = [f"# Skeptic memo, {today}", ""], 0
        try:
            match = re.search(r"\{.*\}", skeptic(prompt) or "", re.S)
            objections = json.loads(match.group(0)).get("objections", []) if match else []
            valid = {r["hypothesis_id"] for r in summary["accepted"]}
            for o in objections if isinstance(objections, list) else []:
                if isinstance(o, dict) and o.get("hypothesis_id") in valid and str(o.get("why_it_fails", "")).strip():
                    count += 1
                    lines += [f"## Hypothesis #{o['hypothesis_id']}", "", f"- Why it will fail: {o['why_it_fails']}",
                              f"- Test most likely to break it: {o.get('kill_test', '')}", ""]
                    ledger.add_lesson(o["hypothesis_id"], f"Before testing, the skeptic argued: {o['why_it_fails']} "
                                      f"Breaking test: {o.get('kill_test', '')}", "skeptic")
            if not count:
                lines.append("The skeptic returned no usable objection.")
        except Exception as exc:
            lines.append(f"The skeptic did not answer ({type(exc).__name__}: {exc}).")
        memo = paths.research / f"skeptic-{today}.md"
        memo.write_text("\n".join(lines))
        summary["roles"]["skeptic"] = {"memo": str(memo.relative_to(paths.root)), "objections": count}
    return summary


def hermes_asker(profile: str, timeout: float | None = None, model: str | None = None, toolsets: str = "clarify") -> Asker:
    """Ask a Hermes profile one-shot (`hermes -p <profile> -z <prompt> -t <toolsets>`). No timeout by default: a
    research memo may take a while, and the caller decides when to give up.

    The call carries no tools (`clarify` alone means none). A research agent is given everything it may know in
    its prompt; with file tools it could open the stored prices, which include the sealed period, or another
    agent's memo, and both rules at the top of this module would then rest on the agent's good behaviour."""
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", profile):
        raise ValueError("invalid profile name")
    if not re.fullmatch(r"[a-z0-9_-]+(,[a-z0-9_-]+)*", toolsets):
        raise ValueError("invalid toolsets")

    def ask(prompt: str) -> str:
        argv = ["hermes", "-p", profile, "-z", prompt, "-t", toolsets] + (["-m", model] if model else [])
        from valedia_trader.core.secrets import child_env
        out = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL, env=child_env())
        if out.returncode != 0:
            raise RuntimeError(f"hermes exited {out.returncode}")
        return out.stdout

    return ask


def chat_asker(base_url: str, model: str, timeout: float = 900.0, max_tokens: int = 2500) -> Asker:
    """Ask a local OpenAI-compatible model."""
    from valedia_trader.agents import panel
    if not panel._loopback(base_url):
        raise ValueError("research agents on a chat endpoint must be local")

    def ask(prompt: str) -> str:
        body = panel._http_json(base_url.rstrip("/") + "/chat/completions",
                                {"model": model, "temperature": 0.7, "max_tokens": max_tokens,
                                 "messages": [{"role": "user", "content": prompt}]}, timeout)
        return body["choices"][0]["message"]["content"]

    return ask
