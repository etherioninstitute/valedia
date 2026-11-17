"""One page for the owner: how the trader stands, from the trader's own stores, in one static file.

    python -m valedia_trader.research_loop.owner_page build [--out FILE]

Writes one self-contained HTML file (inline style and inline SVG, no script, no link, no request to anything outside
it; default `artifacts/owner/index.html`). Eight sections, each under its own heading and each saying the time of its
newest data: Strategies, Paper account, Broker (paper), Forecasters, Tax so far, Limits and switches, Research tests in
progress, Health.

The page only reads. Every sqlite file is opened by this module itself, read-only (`mode=ro`) and waiting at most 5
seconds in all for a writer, never through a class that can write, recover or migrate, and is read through its own
module's reading code (the ledger of ideas, the tournament, the order ledger, the order-book collector's `day_status`,
the pick store's and the grading store's `status`, the risk gate, the journal). A source that is missing, cannot be
read, is busy (a live job is writing it) or needs its own program to recover a journal a killed writer left makes that
one section, or that one part of it, say so in plain words ("Unavailable: busy", "Unavailable: needs its own program to
recover it"); its bytes, modification time and side files stay as they were. The one change a read can make: reading a
WAL store (the order-book collector's) creates, when they are absent, an empty `-wal` file and a `-shm` file of 32,768
bytes. Before it reads anything the command registers every value of the trading file with the output guard (into a
private dictionary: nothing goes into the process environment), and reads the Webull environment word from it. A trading file
that is unreadable, shared, over 64 KB or 200 settings, or not UTF-8 text stops the build with exit 2 and nothing written.
Nothing read back from the tool's own files (the alert file, the notification record) is trusted: it is cleaned like a new
text. Known limits of the output guard this rests on (the package's own): a value split by other separators, halves shorter
than 8 characters, a base64 form of a recovery phrase, words of the phrase glued to other letters, and values under ordinary
setting names are not caught. The order-step section shows the unattended run's last results, and apart from them the last
propose run and the last approve run (summaries that carry a `mode`). Every text that comes from a file or from a program's
output is passed through `alerts.clean` (normalised, the package's output guard, ids shortened and digit runs and groups
masked, the guard again, and only then cut to its length) and then HTML-escaped. An order is shown only by the last 4
characters of its tag (a tag the cleaning masks only as `<secret>` or `<number>`), the pick lists only by counts, and no
result of any research test is shown.
"""
from __future__ import annotations

import argparse
import html
import json
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from valedia_trader.core import alerts as al

__all__ = ["build", "render", "main", "SECTIONS"]

FILLS_DAYS = 7
SUMMARIES_SHOWN = 5
COVERAGE_DAYS = 30
CSS = """
:root { --bg: #ffffff; --fg: #1c1c1e; --muted: #6e6e73; --line: #d2d2d7; --card: #f5f5f7; --accent: #0a60c8; --bad: #b3261e; }
@media (prefers-color-scheme: dark) { :root { --bg: #111113; --fg: #f2f2f4; --muted: #9a9aa2; --line: #3a3a3e; --card: #1c1c1f; --accent: #6cb0ff; --bad: #ff8a80; } }
* { box-sizing: border-box; }
body { margin: 0; padding: 16px; background: var(--bg); color: var(--fg); font: 15px/1.45 -apple-system, "Helvetica Neue", Arial, sans-serif; }
main { max-width: 980px; margin: 0 auto; }
h1 { font-size: 1.5rem; margin: 0 0 4px; }
h2 { font-size: 1.15rem; margin: 0 0 4px; }
h3 { font-size: 0.95rem; margin: 14px 0 4px; color: var(--muted); font-weight: 600; }
section { background: var(--card); border: 1px solid var(--line); border-radius: 10px; padding: 14px 16px; margin: 14px 0; overflow-x: auto; }
p { margin: 6px 0; }
.newest, .muted { color: var(--muted); font-size: 0.85rem; }
.note { border-left: 3px solid var(--accent); padding-left: 10px; }
.problem { border-left: 3px solid var(--bad); padding-left: 10px; }
table { border-collapse: collapse; width: 100%; font-size: 0.9rem; }
th, td { text-align: left; padding: 4px 10px 4px 0; border-bottom: 1px solid var(--line); vertical-align: top; }
th { color: var(--muted); font-weight: 600; }
svg { width: 100%; max-width: 640px; height: auto; display: block; }
.line { fill: none; stroke: var(--accent); stroke-width: 2; }
.axis { stroke: var(--line); stroke-width: 1; }
svg text { fill: var(--muted); font-size: 11px; }
""".strip()


# ---------------------------------------------------------------------------------------------- small pieces

def E(x, limit: int = 300) -> str:
    """A text from a file or a program, cleaned by the package's guard and escaped for HTML."""
    return html.escape(al.clean(x, limit), quote=True)


def num(x, nd: int = 2, signed: bool = False) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "n/a"
    if not math.isfinite(v):
        return "n/a"
    return f"{v:+,.{nd}f}" if signed else f"{v:,.{nd}f}"


def pct(x, signed: bool = True) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "n/a"
    return "n/a" if not math.isfinite(v) else (f"{v:+.2%}" if signed else f"{v:.2%}")


def _finite(x) -> bool:
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError):
        return False


def when(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def newest_text(candidates) -> str:
    """The time of the newest data: the largest of epoch seconds, `YYYY-MM-DD` days and ISO times given."""
    best = None
    for c in candidates:
        if c is None:
            continue
        if isinstance(c, (int, float)):
            ts = float(c)
        else:
            try:
                text = str(c).replace("Z", "+00:00")
                d = datetime.fromisoformat(text)
                ts = (d if d.tzinfo else d.replace(tzinfo=timezone.utc)).timestamp()
                if len(str(c)) == 10:
                    ts = (d.replace(tzinfo=timezone.utc)).timestamp()
            except ValueError:
                continue
        if math.isfinite(ts) and (best is None or ts > best[0]):
            best = (ts, c)
    if best is None:
        return "none"
    return str(best[1])[:10] if isinstance(best[1], str) and len(best[1]) == 10 else when(best[0])


def short_id(tag) -> str:
    """An order is shown by one of two things only, never by anything else of its tag: the mask alone (`<secret>`, `<number>`
    or `<held>`, the first one in it) when cleaning the tag puts any mask in it, or else `…` and the tag's last 4 characters
    (`…0007`; a ledger tag `os-20261113-143501-a1b2c3-XLE` is `…-XLE`, its fund shown in its own column)."""
    text = str(tag)
    cleaned = al.clean(text, 400)
    found = [(cleaned.find(mark), mark) for mark in ("<secret>", "<number>", al.HELD) if mark in cleaned]
    return min(found)[1] if found else "…" + text[-4:]


def table(headers: list[str], rows: list[list[str]]) -> str:
    head = "".join(f"<th>{E(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def para(text_html: str, kind: str = "") -> str:
    return f'<p class="{kind}">{text_html}</p>' if kind else f"<p>{text_html}</p>"


def problem(exc: BaseException, what: str = "") -> str:
    """The plain-words line for a part whose source is missing, busy or cannot be read."""
    label = f" ({E(what)})" if what else ""
    if isinstance(exc, al.SourceMissing):
        return para(f"Not available{label}: {E(exc)}.", "problem")
    if al.is_busy(exc):
        return para(f"Unavailable{label}: busy. Another process is writing the file just now; build the page again in a moment.",
                    "problem")
    if al.needs_recovery(exc):
        return para(f"Unavailable{label}: needs its own program to recover it. A writer was stopped part-way; the page does "
                    "not touch the file.", "problem")
    return para(f"Unavailable{label}: {E(al.describe(exc))}.", "problem")


def guarded(fn, what: str = "") -> tuple[list, str]:
    """(the times of newest data the part found, its html), or no times and the problem line when it fails."""
    try:
        return fn()
    except Exception as exc:
        return [], problem(exc, what)


def day_of(e: dict) -> str:
    return str(e.get("day", ""))[:10]


class Context:
    """What the sections share, read once: the whole journal."""

    def __init__(self, env: al.Env) -> None:
        self.env, self._journal = env, None

    def journal(self) -> tuple[list[dict], list[dict]]:
        if self._journal is None:
            from valedia_trader.research_loop.paper import Journal
            folder = self.env.paths.journal
            if not folder.is_dir():
                raise al.SourceMissing("journal/ is not there")
            j = Journal(folder)
            entries = j.read()
            self._journal = (entries, [dict(x) for x in j.skipped_lines])
        return self._journal


# ---------------------------------------------------------------------------------------------- the sections

def ro_ledger_of_ideas(path: Path):
    """The ledger of ideas on a read-only connection (its constructor writes), read by its own methods."""
    from valedia_trader.research_loop.ledger import HypothesisLedger
    led = object.__new__(HypothesisLedger)
    led._db = al.open_readonly(path)
    return led


def paper_stats(entries: list[dict]) -> dict[str, dict]:
    """Per paper strategy, from the journal's day summaries: days, its return and the return of simply holding its funds
    over the same days (both on the capital it had on its first paper day), and its worst fall from a high."""
    seen: dict[str, list[tuple]] = {}
    for e in entries:
        if e.get("type") != "summary" or not isinstance(e.get("strategy_values"), dict):
            continue
        held = e.get("benchmark_values") if isinstance(e.get("benchmark_values"), dict) else {}
        for name, value in e["strategy_values"].items():
            try:
                seen.setdefault(str(name), []).append((day_of(e), float(value), held.get(name), float(e.get("capital_per_strategy") or 0.0)))
            except (TypeError, ValueError):
                continue
    out = {}
    for name, rows in seen.items():
        capital = rows[0][3]
        last = rows[-1]
        ret = last[1] / capital if capital else None
        try:
            hold = float(last[2]) / capital if capital and last[2] is not None else None
        except (TypeError, ValueError):
            hold = None
        fall = None
        if capital:
            curve = [capital] + [capital + r[1] for r in rows]
            peak, fall = curve[0], 0.0
            for c in curve:
                peak = max(peak, c)
                fall = max(fall, 1.0 - c / peak) if peak > 0 else fall
        out[name] = {"days": len(rows), "return": ret, "hold": hold, "fall": fall, "last_day": last[0]}
    return out


def strategies_section(env: al.Env, ctx: Context) -> tuple[list, str]:
    led = ro_ledger_of_ideas(env.paths.ledger)
    try:
        counts = led.summary()
        ideas = [h for s in ("passed paper", "passed backtest only", "paused") for h in led.search(status=s)]
    finally:
        led._db.close()
    from valedia_trader.research_loop.ledger import STATUSES
    total = sum(counts[s] for s in STATUSES)
    tested = total - counts["untested"]
    body = [para(f"{total} ideas in the ledger; {tested} tested, {counts['failed']} failed.")]
    newest: list = []
    try:
        entries, _ = ctx.journal()
        stats = paper_stats(entries)
        names = {e["hypothesis_id"]: e["strategy"] for e in entries if e.get("type") == "decision" and "hypothesis_id" in e
                 and isinstance(e.get("strategy"), str)}
        newest = [e["day"] for e in entries if e.get("type") == "summary" and "day" in e]
        note = ""
    except Exception as exc:
        stats, names, note = {}, {}, problem(exc, "paper figures")
    rows = []
    for h in ideas:
        name = names.get(h["id"])
        st = stats.get(name) if name else None
        rows.append([E(h["idea"], 200), E(name) if name else "none yet", E(h["status"]),
                     str(st["days"]) if st else "no paper days yet",
                     pct(st["return"]) if st else "n/a", pct(st["hold"]) if st else "n/a",
                     pct(-st["fall"]) if st and st["fall"] is not None else "n/a"])
    if rows:
        body.append(table(["Idea", "Strategy", "Status", "Paper days", "Paper return", "Holding its market (same days)", "Worst fall"], rows))
    else:
        body.append(para("No idea is trading or has passed yet."))
    body.append(note or para("Returns are on the capital the strategy had on its first paper day; holding means the same funds, "
                             "equally weighted, with no costs.", "muted"))
    return newest, "".join(body)


def chart(history: list[tuple[str, float]]) -> str:
    """The equity line as inline SVG: no script, no outside file."""
    if not history:
        return para("No paper day has been recorded yet.")
    w, h, pad = 640, 170, 28
    values = [v for _, v in history]
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    n = len(history)
    pts = [(pad + (w - 2 * pad) * (i / (n - 1) if n > 1 else 0.5), h - pad - (h - 2 * pad) * ((v - lo) / span)) for i, (_, v) in enumerate(history)]
    line = (f'<polyline class="line" points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in pts)}"/>' if n > 1
            else f'<circle class="line" cx="{pts[0][0]:.1f}" cy="{pts[0][1]:.1f}" r="3"/>')
    return (f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Paper account equity over time">'
            f'<title>Paper account equity over time</title>'
            f'<line class="axis" x1="{pad}" y1="{h - pad}" x2="{w - pad}" y2="{h - pad}"/>'
            f'{line}'
            f'<text x="{pad}" y="{h - 8}">{E(history[0][0])}</text>'
            f'<text x="{w - pad}" y="{h - 8}" text-anchor="end">{E(history[-1][0])}</text>'
            f'<text x="{pad}" y="14">high {E(num(hi))}</text><text x="{w - pad}" y="14" text-anchor="end">low {E(num(lo))}</text></svg>')


def paper_section(env: al.Env, ctx: Context) -> tuple[list, str]:
    path = env.paths.journal / "paper_state.json"
    if not path.is_file():
        raise al.SourceMissing("paper_state.json is not there")
    state = json.loads(path.read_text())
    if not isinstance(state, dict):
        raise ValueError("paper_state.json is not a record")
    history = [(str(r.get("day"))[:10], float(r["equity"])) for r in state.get("history", [])
               if isinstance(r, dict) and "equity" in r and _finite(r["equity"])]
    positions = [[E(strategy), E(sym), num(lot.get("units"), 4), num(lot.get("cost"))]
                 for strategy, book in sorted((state.get("positions") or {}).items()) if isinstance(book, dict)
                 for sym, lot in sorted(book.items()) if isinstance(lot, dict) and abs(float(lot.get("units") or 0.0)) > 1e-12]
    pending = [[E(o.get("strategy")), E(o.get("symbol")), num(o.get("units"), 4), E(o.get("decided_on")),
                "yes" if o.get("reduces_risk") else "no"] for o in state.get("pending", []) if isinstance(o, dict)]
    realized = state.get("realized") or {}
    total = sum(float(v) for v in realized.values())
    last = history[-1] if history else None
    body = [para(f"Equity {E(num(last[1]))} on {E(last[0])}; started with {E(num(state.get('starting_cash')))}." if last
                 else "No equity recorded yet."),
            chart(history),
            para(f"Cash {E(num(state.get('cash')))}. Realised result {E(num(total, signed=True))} "
                 f"({', '.join(f'{E(k)} {E(num(v, signed=True))}' for k, v in sorted(realized.items())) or 'none yet'})."),
            "<h3>Positions</h3>",
            table(["Strategy", "Fund", "Units", "Cost"], positions) if positions else para("No open positions."),
            "<h3>Pending orders (fill at the next close)</h3>",
            table(["Strategy", "Fund", "Units", "Decided on", "Reduces risk"], pending) if pending else para("No pending orders.")]
    return [state.get("last_day"), last[0] if last else None], "".join(body)


def step_summaries(entries: list[dict]) -> list[dict]:
    return [e for e in entries if e.get("type") == "order_step" and e.get("event") == "summary"]


def step_result(e: dict) -> str:
    """What an order-step summary says, in words: a closed market is ordinary, any other stop names its stage and reason."""
    if al.is_outside_market_hours(e):
        return "outside market hours"
    if e.get("status") == "stopped":
        return f"stopped at stage {E(e.get('stage'))}: {E(e.get('reason'), 240)}"
    return E(e.get("status"))


def step_orders(e: dict) -> str:
    return f"sent {len(e.get('sent') or [])}, refused {len(e.get('refused') or [])}, left over {len(e.get('left_over') or [])}"


def broker_part(path: Path, env: al.Env, ctx: Context) -> tuple[list, str]:
    from valedia_trader.execution_and_risk.order_step import ledger_path_for
    from valedia_trader.execution_and_risk.orders import TERMINAL
    name = path.stem[len("orders-"):]
    led = al.open_order_ledger(path, lambda: env.now)
    try:
        orders = led.orders()
        fills = led.fills()
        last = led.last_reconcile()
    finally:
        led.close()
    open_orders = [o for o in orders if o["state"] not in TERMINAL]
    recent = [f for f in fills if f["time"] >= env.now - FILLS_DAYS * 86400]
    body = [f"<h3>{E(name)}</h3>"]
    body.append(table(["Order (last 4)", "Fund", "Units", "State", "Updated"],
                      [[E(short_id(o["tag"])), E(o["symbol"]), num(o["units"], 4), E(o["state"]), E(when(o["updated"]))]
                       for o in open_orders]) if open_orders else para("No open orders."))
    body.append(f"<h3>Fills of the last {FILLS_DAYS} days</h3>")
    if recent:
        body.append(table(["Order (last 4)", "Fund", "Units", "Price", "Fee", "Time"],
                          [[E(short_id(f["tag"])), E(f["symbol"]), num(f["units"], 4), num(f["price"]), num(f["fee"]),
                            E(when(f["time"]))] for f in recent]))
        body.append(para(f"Fees in these fills: {E(num(sum(float(f['fee'] or 0.0) for f in recent)))}."))
    else:
        body.append(para("No fills in that time."))
    body.append(para(f"Last reconcile: {E(when(last['time']))}, {'clean' if last['clean'] else 'not clean'}." if last
                     else "No reconcile has been recorded.", "muted"))
    try:
        entries, _ = ctx.journal()
        ours = [e for e in step_summaries(entries) if ledger_path_for(Path("."), e.get("venue")).name == path.name]
        mine = [e for e in ours if al.is_unattended(e)][-SUMMARIES_SHOWN:]
        body.append("<h3>Last order steps of the unattended run (from the journal)</h3>")
        if mine:
            body.append(table(["Day", "Result", "Orders"], [[E(day_of(e)), step_result(e), step_orders(e)] for e in reversed(mine)]))
        else:
            body.append(para("No unattended order step is in the journal for this ledger."))
        body.append("<h3>Practice runs of the approval flow</h3>")
        practice = []
        for mode in ("propose", "approve"):
            last_of_mode = [e for e in ours if e.get("mode") == mode and al.is_practice(e)][-1:]
            practice.append([f"Last {mode}", E(day_of(last_of_mode[0])) if last_of_mode else "none yet",
                             step_result(last_of_mode[0]) if last_of_mode else "", step_orders(last_of_mode[0]) if last_of_mode else ""])
        body.append(table(["Run", "Day", "Result", "Orders"], practice))
    except Exception as exc:
        body.append(problem(exc, "order-step summaries"))
    times = [last["time"]] if last else []
    times += [o["updated"] for o in orders] + [f["time"] for f in fills]
    return times, "".join(body)


def broker_section(env: al.Env, ctx: Context) -> tuple[list, str]:
    files = al.order_ledger_files(env.paths.memory)
    if not files:
        return [], para("No order ledger was found (memory/orders-*.sqlite).")
    times, body = [], []
    for path in files:
        t, h = guarded(lambda p=path: broker_part(p, env, ctx), path.stem)
        times += t
        body.append(h)
    return times, "".join(body)


def ro_tournament(path: Path):
    from valedia_trader.research_loop.tournament import TournamentLedger
    led = object.__new__(TournamentLedger)
    led._db = al.open_readonly(path)
    return led


def forecasters_section(env: al.Env, ctx: Context) -> tuple[list, str]:
    from valedia_trader.research_loop import tournament
    led = ro_tournament(env.paths.memory / "tournament.sqlite")
    try:
        rows = tournament.scores(led)
        counts = led.counts()
        times = [led._db.execute("SELECT MAX(ts) FROM forecasts").fetchone()[0], led._db.execute("SELECT MAX(settled_ts) FROM outcomes").fetchone()[0]]
    finally:
        led._db.close()
    body = [para(f"{counts['questions']} questions asked, {counts['settled']} settled. Brier scores: lower is better; skill is 1 minus "
                 "the forecaster's score over its baseline's on the same questions (above 0 beats the baseline).")]
    if rows:
        recorded = lambda v: "not recorded" if v is None else E(v)
        body.append(table(["Forecaster", "Model", "Prompt", "Market", "Kind", "Memory", "Settled", "Brier", "Baseline Brier", "Skill",
                           "Verdict"],
                          [[E(r["forecaster"]), recorded(r.get("model")), recorded(r.get("prompt_sha")), E(r["market"]), E(r["kind"]),
                            E(r.get("memory")), str(r["settled"]), f"{r['brier']:.4f}", f"{r['baseline_brier']:.4f}",
                            f"{r['skill']:+.3f}", E(r["verdict"])] for r in rows]))
    else:
        body.append(para("No question has been settled yet, so nothing is scored."))
    return times, "".join(body)


def tax_section(env: al.Env, ctx: Context) -> tuple[list, str]:
    entries, _ = ctx.journal()
    summaries = [e for e in entries if e.get("type") == "summary"]
    if not summaries:
        return [], para("Unavailable: the journal holds no day summary yet.", "problem")
    last = summaries[-1]
    block = last.get("tax")
    if not isinstance(block, dict):
        return [last.get("day")], para("Unavailable: the paper trader wrote no tax note on its last day "
                                       "(tax notes are off, or the tax rules file is missing).", "problem")
    if "unavailable" in block:
        return [last.get("day")], para(f"Unavailable: {E(block['unavailable'], 400)}", "problem")
    rows = [["Year", E(block.get("year"))], ["Short-term, net", E(num(block.get("short_term_net"), signed=True))],
            ["Long-term, net", E(num(block.get("long_term_net"), signed=True))],
            ["Disallowed loss (wash sales)", E(num(block.get("disallowed_loss")))],
            ["Provisional loss", E(num(block.get("provisional_loss")))], ["As of", E(block.get("as_of"))]]
    unverified = block.get("unverified_rules")
    body = [table(["", "Realised so far"], rows)]
    if unverified:
        body.append(para("Rules still unverified: " + ", ".join(E(u, 80) for u in (unverified if isinstance(unverified, list) else [unverified])) + ".", "muted"))
    body.append(para(E(block.get("note", ""), 400), "muted"))
    return [last.get("day"), block.get("as_of")], "".join(body)


def webull_environment(env: al.Env) -> str:
    """`sandbox` or `not sandbox`, read as the trader reads WEBULL_ENV (default sandbox). Never a key."""
    from valedia_trader.execution_and_risk.order_step import TEST_ENVIRONMENT
    if env.environ is not None:
        word = env.environ.get("WEBULL_ENV")
    else:                                   # as the trader reads it: the environment first, then the trading file; nothing is exported
        word = os.environ.get("WEBULL_ENV") or al.register_trading_file(env).get("WEBULL_ENV")
    return TEST_ENVIRONMENT if (word or TEST_ENVIRONMENT).strip().lower() == TEST_ENVIRONMENT else f"not {TEST_ENVIRONMENT}"


def live_names_in_reach(env: al.Env) -> list[str]:
    """T50: the live key names (secrets.LIVE_NAMES) in reach of the practice runs, read as webull_environment reads the
    settings: the given environment, else the process environment and the trading file (nothing is exported). Names
    only, never a value; the live file itself is never opened."""
    from valedia_trader.core.secrets import live_names_in
    if env.environ is not None:
        return live_names_in(env.environ)
    return live_names_in({**al.register_trading_file(env), **os.environ})


def limits_section(env: al.Env, ctx: Context) -> tuple[list, str]:
    from valedia_trader.research_loop import risk_gate
    times, body = [], []
    try:
        limits = risk_gate.load_limits(env.paths.risk_limits)
        body.append(table(["Limit", "Value"], [[E(k), E(limits[k])] for k in sorted(limits)]))
        times.append(env.paths.risk_limits.stat().st_mtime)
    except Exception as exc:
        body.append(para(f"Unavailable: {E(exc, 400)}. Every order is blocked until the limits file can be read.", "problem"))
    halt = env.halt_file is not None and risk_gate.file_is_there(env.halt_file)
    latch = risk_gate.file_is_there(env.paths.journal / "kill_switch.json")
    rows = [["Operator halt file", "set: all trading is stopped" if halt else "not set"],
            ["Drawdown latch", "set: new risk is blocked until it is reset" if latch else "not set"]]
    try:
        rows.append(["Webull environment", E(webull_environment(env))])
    except Exception as exc:
        rows.append(["Webull environment", f"unknown ({E(al.describe(exc), 100)})"])
    flag = None
    try:
        found = live_names_in_reach(env)
        rows.append(["Webull live key names", f"in reach of the practice runs: {E(' and '.join(found))}" if found
                     else "none in reach of the practice runs"])
        if found:
            flag = (f"{E(' and '.join(found))} {'is' if len(found) == 1 else 'are'} in reach of the practice runs (the "
                    "process environment or the trading file): every practice run refuses to build a venue while it is "
                    "there. The live pair belongs in webull-live.env only.")
    except Exception as exc:
        rows.append(["Webull live key names", f"unknown ({E(al.describe(exc), 100)})"])
    body.append(table(["Switch", "State"], rows))
    if flag is not None:
        body.append(para(flag, "problem"))
    return times, "".join(body)


def orderbook_part(env: al.Env) -> tuple[list, str]:
    from valedia_trader.research_loop import orderbook_loop
    conn = al.open_readonly(env.paths.memory / "orderbook_loop.sqlite")
    try:
        store = object.__new__(orderbook_loop.Store)
        store.path, store.conn = env.paths.memory / "orderbook_loop.sqlite", conn
        days = orderbook_loop.day_status(store, env.now)[-COVERAGE_DAYS:]
        newest = conn.execute("SELECT MAX(ts) FROM heartbeats").fetchone()[0]
    finally:
        conn.close()
    body = ["<h3>Order-book collection</h3>"]
    if days:
        body.append(table(["Day (UTC)", "Slots expected", "Collected", "Coverage", "Market-minutes watched"],
                          [[E(d["day"]), str(d["expected"]), str(d["collected"]), "-" if d["share"] is None else f"{d['share']:.1%}",
                            f"{d['watched_minutes']:.0f} ({d['watched_share']:.1%})"] for d in days]))
    else:
        body.append(para("The collector has recorded no day yet."))
    return [newest], "".join(body)


def picks_part(env: al.Env) -> tuple[list, str]:
    from valedia_trader.data_and_fees import pick_lists
    path = env.paths.memory / "pick_lists.sqlite"
    store = object.__new__(pick_lists.Store)          # the store's own reading code on our own read-only connection: its
    store.path, store.conn = path, al.open_readonly(path, row_factory=False)   # constructor can recover (write) and migrate
    try:
        st = pick_lists.status(store)
        newest = store.latest_time()
    finally:
        store.conn.close()
    rows = [[label, str(st[k])] for label, k in (("Threads", "threads"), ("Comments", "comments"), ("Comment versions", "versions"),
                                                 ("Parsed", "parsed"), ("Not parsed", "unparsed"), ("Set aside", "set_aside"))]
    return [newest], "<h3>Pick lists: collection</h3>" + table(["Count of", "Number"], rows)


def grading_part(env: al.Env) -> tuple[list, str]:
    from valedia_trader.research_loop import pick_grading as pg
    root = env.root / "artifacts" / "pick_grading"
    store = object.__new__(pg.GradingStore)
    store.path, store.conn = root / pg.STORE_FILE, al.open_readonly(root / pg.STORE_FILE)
    kview = object.__new__(pg._KalshiView)
    kview.conn = al.open_readonly(root / "kalshi.sqlite", row_factory=False) if (root / "kalshi.sqlite").is_file() else None
    try:
        layout = store.conn.execute("SELECT version FROM layout WHERE id = 1").fetchone()
        if layout is None or layout[0] != pg.LAYOUT:
            raise pg.Refused("the grading store is of another layout than this code reads")
        kept = store.word_list()
        fp = pg.fingerprint(kept["sha256"] if kept is not None else None)
        frozen = store.frozen()
        if frozen and frozen["fingerprint"] != fp:
            raise pg.Refused("the matching rule is frozen at an earlier fingerprint and the code's has changed")
        st = pg.status(store, fp, kview)
    finally:
        store.conn.close()
        kview.close()
    rows = [["Games on the schedules", str(st["games"])], ["Picks matched to a game", str(st["picks"])],
            ["Errors recorded", str(st["errors"])]]
    gate = "open" if st["gate_open"] else "closed"
    return [], ("<h3>Pick lists: grading</h3>" + table(["Count of", "Number"], rows)
                + para(f"The test gate is {gate}. No result of any test is shown here.", "muted"))


def research_section(env: al.Env, ctx: Context) -> tuple[list, str]:
    times, body = [], []
    for part, what in ((lambda: orderbook_part(env), "order-book collection"), (lambda: picks_part(env), "pick lists"),
                       (lambda: grading_part(env), "pick grading")):
        t, h = guarded(part, what)
        times += t
        body.append(h)
    return times, "".join(body)


def health_section(env: al.Env, ctx: Context) -> tuple[list, str]:
    times, body = [], ["<h3>Scheduled jobs</h3>"]
    try:
        loaded = al.parse_jobs(env.launchctl())
        rows = []
        for label in sorted(set(al.installed_jobs(env.launch_agents_dir)) | set(loaded)):
            job = loaded.get(label)
            if job is None:
                rows.append([E(label, 120), "not loaded", "none"])
            else:
                rows.append([E(label, 120), "loaded", "no exit code yet" if job["status"] == "-" else f"last exit code {E(job['status'], 12)}"])
        body.append(table(["Job", "State", "Last exit"], rows) if rows else para("No com.valedia job is installed or loaded."))
    except Exception as exc:
        body.append(para(f"Could not ask launchd: {E(al.describe(exc))}.", "problem"))
    body.append("<h3>Job logs (last line of each)</h3>")
    logs = []
    try:
        for path in sorted(Path(env.logs_dir).glob("valedia-*.log")):
            lines = al.read_tail(path)
            mtime = path.stat().st_mtime
            times.append(mtime)
            logs.append([E(path.name, 80), E(when(mtime)), E(lines[-1], 200) if lines else "(empty)"])
        body.append(table(["Log", "Changed", "Last line"], logs) if logs else para("No job log was found."))
    except Exception as exc:
        body.append(problem(exc, "job logs"))
    body.append("<h3>Damaged journal lines (last 30 days)</h3>")
    try:
        from valedia_trader.research_loop.paper import describe_skipped
        _, skipped = env.journal()
        body.append(para(f"{len(skipped)} damaged line{'s' if len(skipped) != 1 else ''}: {E(describe_skipped(skipped), 400)}.", "problem")
                    if skipped else para("No damaged journal lines."))
    except Exception as exc:
        body.append(problem(exc, "journal"))
    body.append("<h3>Open alerts of the watchdog</h3>")
    try:
        rows, withheld = al.read_alert_file(al.alerts_file(env.root))
        states = al.read_notifications(al.notifications_file(env.root))
        body.append(table(["Level", "Key", "What", "Since", "Notification"],
                          [[E(r.get("level"), 20), E(r.get("key"), 80), E(r.get("title"), 200), E(r.get("time"), 40),
                            E(al.notification_note(states, r), 120)] for r in rows]) if rows else para("No open alerts."))
        if withheld:
            body.append(para(E(al.withheld_note(withheld), 300) + ".", "muted"))
    except Exception as exc:
        body.append(problem(exc, "alerts"))
    return times, "".join(body)


SECTIONS = [("Strategies", "strategies", strategies_section), ("Paper account", "paper", paper_section),
            ("Broker (paper)", "broker", broker_section), ("Forecasters", "forecasters", forecasters_section),
            ("Tax so far", "tax", tax_section), ("Limits and switches", "limits", limits_section),
            ("Research tests in progress", "research", research_section), ("Health", "health", health_section)]


def render(env: al.Env) -> str:
    al.register_trading_file(env)                     # before anything is read: every value of the trading file known to the guard
    ctx = Context(env)
    parts = []
    for title, ident, fn in SECTIONS:
        times, body = guarded(lambda fn=fn: fn(env, ctx), "")
        parts.append(f'<section id="{ident}"><h2>{E(title)}</h2><p class="newest">Newest data: {E(newest_text(times))}</p>{body}</section>')
    return ("<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>Valedia Trader: owner page</title><style>{CSS}</style></head><body><main>"
            f"<h1>Valedia Trader</h1><p class=\"muted\">Built {E(when(env.now))} from the trader's own files. Paper trading only; "
            "this page reads and never changes anything.</p>" + "".join(parts) + "</main></body></html>\n")


def build(env: al.Env | None = None, out=None) -> Path:
    """Write the page and return its path. The file is written whole (to a side file, then moved into place)."""
    env = env or al.Env.default()
    out = Path(out) if out else env.root / "artifacts" / "owner" / "index.html"
    text = render(env)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, out)
    return out


def main(argv=None, env: al.Env | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m valedia_trader.research_loop.owner_page", description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=None, help="the repository (default: this one)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="write the page")
    b.add_argument("--out", default=None, help="the file to write (default: artifacts/owner/index.html)")
    args = ap.parse_args(argv)
    try:
        path = build(env or al.Env.default(args.root), out=args.out)
    except al.TradingFileError as exc:
        print(f"the page was not built: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"could not write the page: {al.describe(exc)}", file=sys.stderr)
        return 2
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
