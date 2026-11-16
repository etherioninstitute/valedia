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
        try:                                              # T38-1b: what the paper command leaves out (can hold a short)
            from valedia_trader.research_loop import cli
            left = len(cli.strategies_to_step(paths, ledger).left_out)
        except Exception:                                 # a strategy that cannot be loaded: counted as before
            left = 0
    finally:
        ledger.close()
    live = counts["passed backtest only"] + counts["passed paper"] - left
    out = f" ({left} not stepped: can hold a short)" if left else ""
    text = f"{sum(counts.values())} ideas ({counts['failed']} failed, {counts['untested']} untested, {live} in paper trading{out}), {attempts} counted variations"
    return (OK if live else ATTENTION), text + ("" if live else "; nothing is stepped, so nothing trades" if left else "; nothing has passed the gate, so nothing trades")


# T32 rev 2: the range in which each limit the row shows is a limit (the risk gate's own ranges; the gate sets no top
# to the count of positions, the row does: a longer figure there is more likely an account number typed by mistake)
LIMIT_RANGES = {"max_daily_loss_pct": (0.0, 100.0), "max_drawdown_pct": (0.0, 100.0), "max_position_pct": (0.0, 1.0),
                "max_open_positions": (0, 1000)}         # a whole number from 1 (checked below) to 1,000
_LIMIT_LABELS = {"max_daily_loss_pct": "daily loss", "max_drawdown_pct": "drawdown", "max_position_pct": "position",
                 "max_open_positions": "positions"}


def _limit_in_range(key: str, value) -> bool:
    low, high = LIMIT_RANGES[key]
    if type(value) not in (int, float) or not low < value <= high or value != value:
        return False
    return key != "max_open_positions" or value >= 1 and int(value) == value


def _limits_described(path) -> str:
    """What is wrong with the limits file, in the doctor's words, with nothing from inside it (T32 A1, rev 2):
    unknown keys counted, a known limit that is not a number or out of its range named as such; "" when none of
    that can be said."""
    try:
        data = json.loads(Path(path).read_text())
    except Exception:
        return ""
    if not isinstance(data, dict):
        return ""
    said = []
    unknown = sum(1 for k in data if k not in risk_gate.DEFAULT_LIMITS and k not in risk_gate.OPTIONAL_LIMITS)
    if unknown:
        said.append(f"it holds {unknown} key{'s' if unknown != 1 else ''} that {'is' if unknown == 1 else 'are'} "
                    "not a known limit")
    for key in LIMIT_RANGES:
        if key in data:
            v = data[key]
            if type(v) not in (int, float) or v != v or v in (float("inf"), float("-inf")):
                said.append(f"{key} is not a number")
            elif not _limit_in_range(key, v):
                said.append(f"{key} is out of range")
    return "; ".join(said)


def check_risk_limits(paths: LoopPaths) -> tuple[str, str]:
    try:
        limits = risk_gate.load_limits(paths.risk_limits)
    except risk_gate.RiskConfigError as exc:
        said = _limits_described(paths.risk_limits)
        return FAIL, f"{exc}{f' ({said})' if said else ''}; every order is blocked until this is fixed"
    # T32 rev 2: every limit inside its range is shown as b8fcd2d showed it; one outside is said to be out of range
    # and not quoted (A1: no figure from inside the file that is not a limit)
    parts = {"max_daily_loss_pct": f"daily loss {limits['max_daily_loss_pct']}%",
             "max_drawdown_pct": f"drawdown {limits['max_drawdown_pct']}%",
             "max_position_pct": f"position {limits['max_position_pct']:.0%}",
             "max_open_positions": f"{limits['max_open_positions']} positions"}
    text = ", ".join(parts[k] if _limit_in_range(k, limits[k]) else f"{_LIMIT_LABELS[k]} out of range" for k in parts)
    if all(limits.get(k) == v for k, v in risk_gate.DEFAULT_LIMITS.items()):
        return ATTENTION, text + "; these are still the guide's example limits, not yours"
    return OK, text


def check_kill_switch(paths: LoopPaths) -> tuple[str, str]:
    latch, halt = paths.journal / "kill_switch.json", risk_gate.HALT_FILE
    if risk_gate.file_is_there(halt):
        return ATTENTION, f"operator halt file present ({halt}); all trading is stopped"
    if risk_gate.file_is_there(latch):
        return ATTENTION, "drawdown latch is set; new risk is blocked until `reset-kill-switch --who NAME`"
    blocked, _ = risk_gate.check_order({"symbol": "X", "side": "buy", "value": 1.0, "reduces_risk": False, "position_value_after": 1.0},
                                       {"equity": 100.0, "daily_loss_pct": 0.0, "drawdown_pct": 0.0, "open_positions": 0}, None)
    return (OK, "armed; an order with no limits supplied is refused") if blocked is False else (FAIL, "an order with no limits was allowed")


def check_journal(paths: LoopPaths) -> tuple[str, str]:
    """Lines of the journal files of the last 30 days that are not complete entries (a crash in the middle of a write, or
    damage): the tax notes and the weekly review stop trusting a journal with such a line. Nothing is created."""
    from valedia_trader.research_loop.paper import DAMAGE_WINDOW_DAYS, Journal, describe_skipped
    if not paths.journal.is_dir():
        return OK, "no journal yet"
    journal = Journal(paths.journal)
    journal.read(since=pd.Timestamp.now().normalize() - pd.Timedelta(days=DAMAGE_WINDOW_DAYS))
    skipped = journal.skipped_lines
    if not skipped:
        return OK, f"no damaged lines in the last {DAMAGE_WINDOW_DAYS} days"
    return ATTENTION, (f"{len(skipped)} damaged line{'s' if len(skipped) != 1 else ''} in the last {DAMAGE_WINDOW_DAYS} days "
                       f"({describe_skipped(skipped)}); tax figures are unavailable and the weekly review is marked incomplete "
                       "until a person repairs or removes them")


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
        import os
        if os.environ.get("WEBULL_APP_KEY"):
            notes.append(f"Webull environment {(os.environ.get('WEBULL_ENV') or 'sandbox').lower()}")
    except Exception:
        pass
    notes.append(live_mode_note(paths))
    notes.append("the built-in paper book needs no key")
    return (OK if ready else ATTENTION), "; ".join(notes)


LIVE_ONLY_AT_A_PROPOSAL = ("judged only when a batch is proposed and again when it is approved: lock 4 (the ticket), the "
                           "capital given with --capital against the confirmation's max_capital, the money at work (what "
                           "the account holds plus the batch's buys) against max_capital, the account the venue will "
                           "trade against the confirmation's class and last 4 characters, each order's value against "
                           "max_order_value, and both second price sources agreeing on each price; WEBULL_ENV and "
                           "WEBULL_ACCOUNT_CLASS are read when the venue is built, not here")

# T32 A1: the lock judges' reasons the doctor prints as they are, because they are made of the code's own words and
# nothing from inside the file (after "config/<file> "); any other reason is said in the doctor's own words, or not
# shown. The order step still gets the judges' reasons whole.
_READ_WHYS = ("is missing", "is a symbolic link", "has a symbolic link", "cannot be opened", "is not a regular file",
              "has more than one name", "is writable by", "is larger than", "is not UTF-8", "is not plain JSON",
              "cannot be read")
_PLAIN_REASONS = {
    1: ("config/webull_live.json", _READ_WHYS + ("is not a JSON object", "is in the old form", "lacks ",
                                                 "account_class must be", "account_ends must be", "confirmed_by must be",
                                                 "max_order_value must be", "max_capital must be",
                                                 "max_order_value is above", "valid_until must be",
                                                 "valid_until is more than", "i_understand_this_trades_real_money must")),
    2: ("config/go_live.json", _READ_WHYS + ("is not a JSON object", "lacks ", "confirmed_by must be", "min_paper_broker_days must",
                                             "min_reconciled_fills must", "min_days_since_a_stopped_run must",
                                             "fills_checked_by_hand must", "min_fills_checked_by_hand must")),
    3: ("config/risk.json", _READ_WHYS + ("is not a JSON object", "must hold max_orders_per_day",
                                          "is not a limits file the risk gate accepts")),
}
_RECORD_WHYS = ("there is no paper record", "the journal is damaged", "the journal cannot be read",
                "the paper record cannot be", "a stopped paper run in the journal carries no date")
_SHORT = (("paper trading days on which the order step ended done", "paper_broker_days"),
          ("fills journaled in paper runs with a clean reconcile", "reconciled_fills"),
          ("min_days_since_a_stopped_run", None), ("fills checked by hand", None))


def _unknown_keys(n: int) -> int | None:
    """How many keys of lock n's file are not part of it, read again through the locks' own reading function; None
    when that cannot be told."""
    try:
        from valedia_trader.execution_and_risk import go_live
        from valedia_trader.venues import webull
        files = go_live.LiveFiles()
        path, known = (files.confirmation, webull.LIVE_KEYS) if n == 1 else (files.checklist, go_live.CHECKLIST_KEYS)
        data, why = webull.read_owner_file(path, files.root)
        return sum(1 for k in data if k not in known) if why is None and isinstance(data, dict) else None
    except Exception:
        return None


def plain_lock_reason(lock: dict) -> str:
    """A lock's reason with nothing taken from inside the owner's file (T32 A1): an unknown key is counted, not
    named; a lapsed confirmation's date and the checklist's own figures are not shown; a reason not known to be the
    code's own words is not shown."""
    n, reason, figures = lock.get("lock"), lock.get("reason"), lock.get("figures") or {}
    if lock.get("met") is True:
        return "met"
    shown, known = _PLAIN_REASONS.get(n, ("its file", ()))
    if not isinstance(reason, str):
        return "not met"
    rest = reason[len(shown) + 1:] if reason.startswith(shown + " ") else None
    if rest is not None and rest.startswith("has keys that are not part of"):
        count = _unknown_keys(n)
        what = "a confirmation" if n == 1 else "the checklist"
        said = "one or more unknown keys" if not count else f"{count} unknown key{'s' if count != 1 else ''}"
        return f"{shown} holds {said} (not part of {what}; which is not shown here)"
    if n == 1 and rest is not None and rest.startswith("lapsed on "):
        return f"{shown} has lapsed (its valid_until is before today, New York date): the owner renews it"
    if n == 2 and isinstance(figures.get("owner"), dict) and isinstance(figures.get("record"), dict):
        record, short = figures["record"], []
        for words, figure in _SHORT:                    # the record's figures are the journal's, not the file's
            if words not in reason:
                continue
            if figure is not None:
                short.append(f"{words} ({record.get(figure)} so far)")
            elif words == "min_days_since_a_stopped_run":
                short.append(f"days since a stopped paper run (the last was {record.get('days_since_a_stopped_run')} "
                             f"days ago, {record.get('last_stopped_run')})")
            else:
                short.append(words)
        if short:
            return f"the paper record is short of {shown} on: {'; '.join(short)} (the file's own figures are not shown here)"
    if n == 2 and "owner" in figures and reason.startswith(_RECORD_WHYS):
        return reason
    if rest is not None and rest.startswith(known):
        return reason
    if n == 3 and reason.startswith("the second price sources are not configured: "):
        return reason
    return f"{shown} is not as live mode needs it (the reason is not shown here: it may quote the file; the order step gives it)"


def live_mode_note(paths: LoopPaths) -> str:
    """What can be said of live mode (real money) without the venue, a quote or any network call, through the
    functions live mode itself uses: the command line's root rule (research_loop.cli._live_root_refusal) and the
    locks as go_live.judge_locks reads and judges them (the owner's files through webull.read_owner_file, the paper
    record from the journal), on today's New York date. Each of locks 1 to 3 is met or not met with its reason; what
    is judged only when a batch is proposed is named as such. "real-money orders off" unless every lock judged here
    is met, and even then every batch still needs a proposal and the owner's approval. Never raises; prints no
    account id (not even its last 4), key or token, and nothing taken from inside an owner's file (T32 A1: the
    reasons go through plain_lock_reason). Also which strategies the broker book follows (config/broker_book.json,
    T32), through the reading function the order step is given (research_loop.cli.broker_book_follow), or why it
    would not be followed and that live mode would stop for that."""
    try:
        import os

        from valedia_trader.execution_and_risk import go_live
        from valedia_trader.research_loop.cli import _live_root_refusal
        from valedia_trader.venues import webull
        why = _live_root_refusal(paths)
        if why is not None:
            return f"real-money orders off: {why}; the locks were not read"
        state = go_live.judge_locks(go_live.LiveFiles(environ=os.environ), webull.ny_date())
        locks = state.locks[:3]
        told = [f"lock {lock['lock']} ({lock['name']}) " + ("met" if lock["met"] is True else
                                                            f"not met: {plain_lock_reason(lock)}")
                for lock in locks]
    except Exception as exc:
        return f"real-money orders off: the locks could not be judged ({type(exc).__name__})"
    if all(lock["met"] is True for lock in locks):
        head = ("live mode: locks 1 to 3 met as far as they can be judged here; every batch still needs a proposal and "
                "the owner's approval of its ticket number (order-step --live --propose, then --approve)")
    else:
        head = "real-money orders off"
    return "; ".join([head, *told, broker_book_note(paths), LIVE_ONLY_AT_A_PROPOSAL])


def broker_book_note(paths: LoopPaths) -> str:
    """Which strategies the broker book follows in live mode, read as live mode reads it
    (research_loop.cli.broker_book_follow: the repository's config/broker_book.json, the ledger, the strategy files);
    whether each is in the paper book's latest day is judged at each run. Nothing taken from inside the file is
    shown (T32 A1): the strategies are named by their names (from the strategy files), not by the file's ids, and a
    refusal is said in the doctor's own words (plain_follow_refusal). Never raises."""
    try:
        from valedia_trader.research_loop import cli
        follow = cli.broker_book_follow(paths, live=True)
    except Exception as exc:
        return f"broker book: could not be judged ({type(exc).__name__}); live mode would stop for that"
    if follow.refused is not None:
        shown = cli.BROKER_BOOK_SHOWN
        if follow.refused.startswith(f"{shown} is missing: "):
            return (f"broker book: {shown} is missing: live mode follows only the strategies that file names and never "
                    "follows the whole book by default; live mode would stop for that")
        return f"broker book: not followed: {plain_follow_refusal(follow.refused, shown)}; live mode would stop for that"
    n = len(follow.names)
    return (f"broker book: follows {n} strateg{'y' if n == 1 else 'ies'}: {', '.join(follow.names)} (each must be in "
            "the paper book's latest day; that is judged at each run)")


_FOLLOW_WORDS = (('must be a JSON object such as ', 'is not a JSON object such as {"follow": [26]}'),
                 ('has no "follow" list', 'has no "follow" list of hypothesis ids (such as {"follow": [26]})'),
                 ('"follow" must be a list', '"follow" is not a list of hypothesis ids (such as {"follow": [26]})'),
                 ('"follow" names no hypothesis', '"follow" names no hypothesis: at least one id is needed'),
                 ('"follow" holds true or false', '"follow" holds true or false where a hypothesis id is needed'),
                 ('"follow" holds ', '"follow" holds something that is not a hypothesis id (a whole number from 1)'))


def plain_follow_refusal(refused: str, shown: str) -> str:
    """Why the broker book file is not followed, with nothing taken from inside it (T32 A1): no id, no value."""
    if refused.startswith("the research ledger (memory/ledger.db) is missing"):
        return f"the research ledger (memory/ledger.db) is missing, so the hypotheses {shown} names cannot be found"
    if not refused.startswith(shown + " "):
        return f"{shown} cannot be followed (the reason is not shown here: it may quote the file)"
    rest = refused[len(shown) + 1:]
    if rest.startswith(_READ_WHYS) or rest.startswith("could not be judged ("):
        return refused                                  # the reading function's own words, or an error's type
    if rest.startswith("names hypothes"):
        marker = ", whose status in the research ledger is "
        if marker in rest:                              # rev 2: a hypothesis the paper command does not step
            from valedia_trader.research_loop.ledger import STATUSES
            status = rest.split(marker, 1)[1].split(";", 1)[0]
            said = status if status in STATUSES else "one the paper command does not step"
            return f"{shown} names a hypothesis whose status in the research ledger is {said}"
        for end, words in ((", which the research ledger does not hold", "names a hypothesis the research ledger does not hold"),
                           ("whose strategy has no plain name", "names a hypothesis whose strategy has no plain name"),
                           ("; follow it once", "names two hypotheses whose strategies share one name")):
            if rest.endswith(end):
                return f"{shown} {words}"
        if ", whose strategy cannot be loaded (" in rest:
            return f"{shown} names a hypothesis whose strategy cannot be loaded"
        if ", whose strategy can hold a short position; " in rest:      # T38-1b
            return (f"{shown} names a hypothesis whose strategy can hold a short position, so it cannot be followed "
                    "until shorts are built")
        return f"{shown} names a hypothesis that cannot be followed"
    if rest.endswith("more than once"):
        return f'{shown} "follow" names a hypothesis more than once'
    for start, words in _FOLLOW_WORDS:
        if rest.startswith(start):
            return f"{shown} {words}"
    return f"{shown} cannot be followed (the reason is not shown here: it may quote the file)"


def check_schedule(paths: LoopPaths) -> tuple[str, str]:
    jobs = (("com.valedia.research-loop.weekly", "weekly cycle", "valedia-research-loop.log", "Sundays 18:00"),
            ("com.valedia.paper.daily", "daily paper step", "valedia-paper-daily.log", "weekdays 14:30"))
    try:
        from valedia_trader.core.secrets import child_env
        out = subprocess.run(["launchctl", "list"], capture_output=True, text=True, timeout=10, env=child_env()).stdout
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
        from valedia_trader.core.secrets import child_env
        dirty = subprocess.run(["git", "-C", str(paths.root), "status", "--porcelain"], capture_output=True, text=True, timeout=20,
                               env=child_env()).stdout.strip()
        head = subprocess.run(["git", "-C", str(paths.root), "log", "--oneline", "-1"], capture_output=True, text=True, timeout=20,
                              env=child_env()).stdout.strip()
    except Exception as exc:
        return ATTENTION, f"git not available ({type(exc).__name__})"
    return (OK, head[:70]) if not dirty else (ATTENTION, f"{len(dirty.splitlines())} uncommitted changes; {head[:50]}")


CHECKS: dict[str, Callable[..., tuple[str, str]]] = {
    "price history": check_data, "hypothesis ledger": check_ledger, "risk limits": check_risk_limits, "kill switch": check_kill_switch,
    "journal": check_journal, "quant sheet": check_quant_sheet, "team": check_team, "decision panel": check_panel, "local models": check_models, "venues": check_venues,
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
