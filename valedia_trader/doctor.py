"""One check of the whole trader: is every part present, consistent with the others, and ready.

    python -m valedia_trader.doctor            the table (exit 1 if anything fails)
    python -m valedia_trader.doctor --json
    python -m valedia_trader.doctor --live     also ask every panel member one question

Each check looks at one part through the same code path the trader uses, so a pass here means the parts
agree with each other: the ledger the research loop writes is the one the paper trader reads, the model
the panel pins is the one the local server reports, the limits the kill switch enforces are the file on
disk. `ok` is fine, `attention` works but needs a decision or an input from the owner, `fail` is broken.
No secret value is ever printed.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Callable

import pandas as pd

from valedia_trader.research_loop import LoopPaths, risk_gate

REPO = Path(__file__).resolve().parents[1]
OK, ATTENTION, FAIL = "ok", "attention", "fail"


def _get(url: str, timeout: float = 3.0) -> dict:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read())


def check_data(paths: LoopPaths) -> tuple[str, str]:
    from valedia_trader.data_and_fees.prices import UNIVERSE
    f = paths.root / "artifacts" / "data" / "close.parquet"
    if not f.is_file():
        return FAIL, "no price history; run the cycle with --refresh"
    close = pd.read_parquet(f)
    missing = [s for s in UNIVERSE if s not in close.columns]
    age = (pd.Timestamp.now().normalize() - close.index[-1].normalize()).days
    detail = f"{close.shape[1]} assets, {close.index[0].date()} to {close.index[-1].date()} ({age} days old)"
    if missing:
        return FAIL, detail + f"; missing {missing}"
    return (OK if age <= 4 else ATTENTION), detail + ("" if age <= 4 else "; refresh before trading")


def check_ledger(paths: LoopPaths) -> tuple[str, str]:
    from valedia_trader.research_loop.ledger import STATUSES, HypothesisLedger
    if not paths.ledger.is_file():
        return FAIL, "no hypothesis ledger; run `python -m valedia_trader.research_loop init`"
    ledger = HypothesisLedger(paths.ledger)
    try:
        counts = {s: len(ledger.search(status=s)) for s in STATUSES}
        attempts = int(ledger.attempt_sharpes().size)
    finally:
        ledger.close()
    live = counts["passed backtest only"] + counts["passed paper"]
    text = f"{sum(counts.values())} ideas ({counts['failed']} failed, {counts['untested']} untested, {live} in paper trading), {attempts} counted variations"
    return (OK if live else ATTENTION), text + ("" if live else "; nothing has passed the gate, so nothing trades")


def check_risk_limits(paths: LoopPaths) -> tuple[str, str]:
    try:
        limits = risk_gate.load_limits(paths.risk_limits)
    except risk_gate.RiskConfigError as exc:
        return FAIL, f"{exc}; every order is blocked until this is fixed"
    text = (f"daily loss {limits['max_daily_loss_pct']}%, drawdown {limits['max_drawdown_pct']}%, position {limits['max_position_pct']:.0%}, "
            f"{limits['max_open_positions']} positions")
    if all(limits.get(k) == v for k, v in risk_gate.DEFAULT_LIMITS.items()):
        return ATTENTION, text + "; these are still the guide's example limits, not yours"
    return OK, text


def check_kill_switch(paths: LoopPaths) -> tuple[str, str]:
    latch, halt = paths.journal / "kill_switch.json", risk_gate.HALT_FILE
    if halt.exists():
        return ATTENTION, f"operator halt file present ({halt}); all trading is stopped"
    if latch.exists():
        return ATTENTION, "drawdown latch is set; new risk is blocked until `reset-kill-switch --who NAME`"
    blocked, _ = risk_gate.check_order({"symbol": "X", "side": "buy", "value": 1.0, "reduces_risk": False, "position_value_after": 1.0},
                                       {"equity": 100.0, "daily_loss_pct": 0.0, "drawdown_pct": 0.0, "open_positions": 0}, None)
    return (OK, "armed; an order with no limits supplied is refused") if blocked is False else (FAIL, "an order with no limits was allowed")


def check_quant_sheet(paths: LoopPaths) -> tuple[str, str]:
    from valedia_trader.quant_engine.wiring import summary
    s = summary()
    text = f"{s['wired']} of {s['formulas']} formulas in the trading path, {s['not_applicable']} not applicable"
    return (OK, text) if not s["unwired_ids"] else (FAIL, text + f"; unused and unexplained: {s['unwired_ids']}")


def check_panel(paths: LoopPaths, live: bool = False) -> tuple[str, str]:
    from valedia_trader.agents import panel as P
    from valedia_trader.research_loop.cli import panel_settings
    mode = panel_settings(paths)["mode"]
    roster = P.load_roster()
    on = [m for m in roster["members"] if m.get("enabled", True)]
    deciders, vetters = sum(m["role"] == "decider" for m in on), sum(m["role"] == "vetter" for m in on)
    text = f"mode {mode}; {deciders} deciders and {vetters} vetters enabled"
    if deciders < roster.get("min_deciders", 2) or vetters < roster.get("min_vetters", 1):
        return FAIL, text + "; below the roster's minimum"
    if not live:
        return OK, text
    pan = P.build_panel(roster)
    case = {"thesis": "SPY closes higher over the next 5 trading days", "facts": {"momentum_20d_pct": 1.0, "risk_limits_breached": False}}
    answers = {m.name: m.ask(case) for m in pan.members}
    bad = [n for n, v in answers.items() if v.error is not None]
    return (OK, text + f"; all {len(answers)} members answered") if not bad else (FAIL, text + f"; no answer from {bad}")


def check_models(paths: LoopPaths) -> tuple[str, str]:
    from valedia_trader.core import legacy
    notes, status = [], OK
    try:
        v3 = legacy.load("systemone_v3_candidate")
        tags = _get("http://127.0.0.1:11434/api/tags"); version = _get("http://127.0.0.1:11434/api/version").get("version")
        pinned = v3.pinned_tag(tags, version) == v3.TAG
        notes.append(f"Ollama {version}: Nimble " + ("matches its pinned manifest" if pinned else "MISSING or changed"))
        status = OK if pinned else FAIL
    except Exception as exc:
        notes.append(f"Ollama not reachable ({type(exc).__name__})"); status = FAIL
    try:
        ids = {m.get("id") for m in _get("http://127.0.0.1:1235/v1/models").get("data", [])}
        notes.append("Bionic: 27B " + ("listed" if "qwen/qwen3.8-27b" in ids else "not listed (only the optional 27B members need it)"))
    except Exception as exc:
        notes.append(f"Bionic not reachable ({type(exc).__name__}); only the optional 27B members need it")
    return status, "; ".join(notes)


def check_venues(paths: LoopPaths) -> tuple[str, str]:
    from valedia_trader.core.secrets import SecretError, load_env, secret
    try:
        load_env()
    except Exception:
        pass
    notes, ready = [], 0
    for label, key, good in (("Alpaca paper", "ALPACA_API_KEY", lambda v: v.startswith("PK")),
                             ("Webull", "WEBULL_APP_KEY", lambda v: len(v) >= 8)):
        try:
            value = secret(key)
        except SecretError:
            notes.append(f"{label}: no key on file"); continue
        if good(value):
            notes.append(f"{label}: key on file"); ready += 1
        else:
            notes.append(f"{label}: a key is on file but it is not a paper key; the paper venue refuses it")
    try:
        from valedia_trader.venues.webull import LIVE_CONFIRMATION, load_live_confirmation
        import os
        if os.environ.get("WEBULL_APP_KEY"):
            env = (os.environ.get("WEBULL_ENV") or "sandbox").lower()
            live = load_live_confirmation(LIVE_CONFIRMATION) is not None
            notes.append(f"Webull environment {env}; real-money orders " + ("CONFIRMED in config/webull_live.json" if live and env == "production" else "off"))
    except Exception:
        pass
    notes.append("the built-in paper book needs no key")
    return (OK if ready else ATTENTION), "; ".join(notes)


def check_schedule(paths: LoopPaths) -> tuple[str, str]:
    jobs = (("com.valedia.research-loop.weekly", "weekly cycle", "valedia-research-loop.log", "Sundays 18:00"),
            ("com.valedia.paper.daily", "daily paper step", "valedia-paper-daily.log", "weekdays 14:30"))
    try:
        out = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=10).stdout
    except Exception as exc:
        return ATTENTION, f"could not ask launchd ({type(exc).__name__})"
    notes, status = [], OK
    for label, name, log, when in jobs:
        line = next((ln for ln in out.splitlines() if ln.split()[-1:] == [label]), None)
        if line is None:
            notes.append(f"the {name} is not loaded (see tools/launchd/)"); status = ATTENTION if status == OK else status
            continue
        code = line.split()[1]
        ran = f"last exit code {code}" if (Path.home() / "Library" / "Logs" / log).exists() else f"it has not run yet ({when})"
        notes.append(f"{name} loaded; {ran}")
        if code not in ("0", "-"):
            status = FAIL
    return status, "; ".join(notes)


def check_mirofish(paths: LoopPaths) -> tuple[str, str]:
    from valedia_trader.agents import mirofish_models as mm
    try:
        profile = mm.active()
        report = mm.verify(profile, probe_servers=False)
    except Exception as exc:
        return FAIL, f"{type(exc).__name__}: {exc}"
    if not report.get("ready"):
        return FAIL, f"model profile {profile.name} NOT ready"
    decided = (paths.root / "tools" / "mirofish_acknowledged_revisions.json").is_file()
    if not decided:
        return ATTENTION, f"model profile {profile.name} ready; staging is refused until the owner decides on the staging guard"
    return OK, (f"model profile {profile.name} ready; the staging guard carries the owner's decision; each run still needs its own staged "
                "folder and one-use gate (docs/mirofish-swarm.md)")


def check_desk(paths: LoopPaths) -> tuple[str, str]:
    import os
    from valedia_trader.agents import desk as D
    roster = D.load_roster()
    home = Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    missing = [f"{role} ({roster[role]['profile']})" for role in D.ROLES if not (home / "profiles" / roster[role]["profile"] / "config.yaml").is_file()]
    if missing:
        return FAIL, "no Hermes profile for the " + ", ".join(missing)
    d = D.Desk(paths, roster)
    waiting = [s["id"] for s in d.rooms_list() if s["status"] in ("awaiting owner", "stopped at the cap")]
    text = f"4 seats, room capped at {roster['max_bot_turns']} turns; {len(d.index())} approved notes in the library"
    return (ATTENTION, text + f"; {len(waiting)} report(s) wait for your decision (`python -m valedia_trader.agents.desk list`)") if waiting else (OK, text)


def check_team(paths: LoopPaths) -> tuple[str, str]:
    from valedia_trader.agents import team as T
    team = T.load_team()
    problems = T.check(team)
    seats = T.table(team)
    text = f"{len(team['bots'])} bots fill {len(seats)} seats across the desk, the research round, the panel and the weekly review"
    if not problems:
        return OK, text + "; profiles, models and role files agree with team.json"
    worst = FAIL if any(p["level"] == "fail" for p in problems) else ATTENTION
    return worst, text + "; " + "; ".join(p["problem"] for p in problems[:3]) + (f"; and {len(problems) - 3} more" if len(problems) > 3 else "")


def check_repo(paths: LoopPaths) -> tuple[str, str]:
    try:
        dirty = subprocess.run(["git", "-C", str(paths.root), "status", "--porcelain"], capture_output=True, text=True, timeout=20).stdout.strip()
        head = subprocess.run(["git", "-C", str(paths.root), "log", "--oneline", "-1"], capture_output=True, text=True, timeout=20).stdout.strip()
    except Exception as exc:
        return ATTENTION, f"git not available ({type(exc).__name__})"
    return (OK, head[:70]) if not dirty else (ATTENTION, f"{len(dirty.splitlines())} uncommitted changes; {head[:50]}")


CHECKS: dict[str, Callable[..., tuple[str, str]]] = {
    "price history": check_data, "hypothesis ledger": check_ledger, "risk limits": check_risk_limits, "kill switch": check_kill_switch,
    "quant sheet": check_quant_sheet, "team": check_team, "decision panel": check_panel, "local models": check_models, "venues": check_venues,
    "research desk": check_desk, "schedules": check_schedule, "MiroFish": check_mirofish, "repository": check_repo,
}


def run(root: str | Path | None = None, live: bool = False, only: tuple[str, ...] = ()) -> list[dict]:
    paths = LoopPaths(Path(root).resolve() if root else REPO)
    rows = []
    for name, fn in CHECKS.items():
        if only and name not in only:
            continue
        try:
            status, detail = fn(paths, live) if fn is check_panel else fn(paths)
        except Exception as exc:                               # one broken check must not hide the others
            status, detail = FAIL, f"the check itself failed: {type(exc).__name__}: {str(exc)[:140]}"
        rows.append({"check": name, "status": status, "detail": detail})
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m valedia_trader.doctor", description=__doc__.split("\n")[0])
    ap.add_argument("--root", default=None); ap.add_argument("--json", action="store_true")
    ap.add_argument("--live", action="store_true", help="also ask every enabled panel member one question")
    args = ap.parse_args(argv)
    rows = run(args.root, args.live)
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        for r in rows:
            print(f"{r['status']:<10} {r['check']:<18} {r['detail']}")
        n = {s: sum(r["status"] == s for r in rows) for s in (OK, ATTENTION, FAIL)}
        print(f"\n{n[OK]} ok, {n[ATTENTION]} need your attention, {n[FAIL]} failing")
    return 1 if any(r["status"] == FAIL for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
