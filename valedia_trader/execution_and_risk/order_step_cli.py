"""The order step's own entry point: the `order-step` command, its arguments and everything it runs before
execution_and_risk/order_step.py takes over (the venue's refusals and build, the broker book file, the paper book's
whole-book refusal, live mode's flags and locks, the owner's entry and the live repair).

    python -m valedia_trader.execution_and_risk.order_step_cli [--root R] [ORDER-STEP ARGUMENTS]

takes exactly the arguments of `python -m valedia_trader.research_loop [--root R] order-step [ORDER-STEP ARGUMENTS]`
(`--data` too, which the order step never reads) and runs the same code: `research_loop order-step` reads its
arguments with `add_arguments` and hands them to `run`, the one code path of both entries. Same refusals, same checks
before any venue is built, same summary, same exit codes.

This module imports nothing of the research desk, the decision panel, the research round, the gauntlet, the critic or
the price data, so the order path's pinned files (valedia-deploy/accepted-orderpath-files: this module's import closure,
at import time and inside its functions) are the files the order step runs and no others.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

from valedia_trader.research_loop import LoopPaths, risk_gate
from valedia_trader.research_loop.ledger import HypothesisLedger, LedgerError
from valedia_trader.research_loop.paper import LatchResetError, PaperBookNoPrice, PaperTrader
from valedia_trader.research_loop.strategy import SpecError, Strategy, can_hold_shorts, load_strategy

PAPER_STATUSES = ("passed backtest only", "passed paper")


def _strategy_for(paths: LoopPaths, ledger: HypothesisLedger, hid: int, file: str | None) -> tuple[Strategy, str]:
    candidates = [Path(file)] if file else []
    row = ledger.get(hid)
    if row["strategy_file"]:
        candidates.append(paths.root / row["strategy_file"])
    candidates += sorted(paths.strategies.glob(f"*_h{hid}.json")) + sorted(paths.strategies.glob(f"*_h{hid}.py"))
    for c in candidates:
        if c.is_file():
            return load_strategy(c), str(c.resolve().relative_to(paths.root)) if c.resolve().is_relative_to(paths.root) else str(c)
    raise SpecError(f"no strategy file found for hypothesis {hid}; pass --file")



_ZERO = 1e-12                                          # the paper trader's fill sets |units| below this to exactly 0


def _units(value) -> float | None:
    """A lot's or an order's units as a float when they are a real, finite number; None otherwise."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def _kind(value) -> str:
    """What a value of the state is, in words, for a message about a state that cannot be read."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true or false"
    for kind, words in ((dict, "a mapping"), (list, "a list"), (str, "text"), ((int, float), "a number")):
        if isinstance(value, kind):
            return words
    return f"a {type(value).__name__}"


def judge_paper_book(state, left_out=(), only=None) -> tuple[str, str, str] | None:
    """The paper book's one invariant (T38-1b addendum 2), over the parts of its durable state that act on a later step:
    `positions` ({strategy name: {symbol: lot}}: lots are marked, traded and stopped only through their strategy) and
    `pending` (orders, each with its strategy's name: filled at the next step whatever the strategy). `realized`,
    `history`, `benchmarks`, `panel_cases` and `journal_outbox` carry strategy names but never trade, fill or settle a
    position. None when the book holds, for the names in `only` (every name when None):
      B. under a name in `left_out` (strategies that can hold a short and are not stepped), no lot whose units are not
         exactly zero and no pending order of either sign;
      C. under any name, no lot below zero, and no pending orders that, filled in their order with the paper fill's own
         arithmetic (paper.py `_fill_pending`: each fill adds the order's units to the lot and sets a result below 1e-12
         in size to exactly 0), would take the lot below zero; a lot or an order whose units are not a finite number is
         refused too, since it cannot be told from a short.
    Otherwise (rule, strategy name, symbol) of the first breach found, rule "lot" or "pending" (B) or "short" (C); or
    ("unreadable", what is wrong in words, "") when `positions` is present but not a mapping, `pending` is present but
    not a list, or an entry in them cannot be read (T38-1b addendum 3). Pure: reads `state` only."""
    if not isinstance(state, dict):
        return ("unreadable", f"it is {_kind(state)} where a mapping belongs", "")
    positions, pending = state.get("positions", {}), state.get("pending", [])
    if not isinstance(positions, dict):
        return ("unreadable", f"its positions are {_kind(positions)} where a mapping of strategies to their lots belongs",
                "")
    if not isinstance(pending, list):
        return ("unreadable", f"its pending orders are {_kind(pending)} where a list of orders belongs", "")

    def not_a_book(name, book):
        return ("unreadable", f"its lots under {name} are {_kind(book)} where a mapping of symbols to lots belongs", "")

    asked = (lambda name: True) if only is None else (lambda name: name in only)
    for name in left_out:                                                       # B
        if not isinstance(name, str) or not asked(name):
            continue
        book = positions.get(name, {})
        if not isinstance(book, dict):
            return not_a_book(name, book)
        for sym, lot in book.items():
            if _units(lot.get("units") if isinstance(lot, dict) else None) != 0.0:
                return ("lot", name, str(sym))
        for o in pending:
            if isinstance(o, dict) and o.get("strategy") == name:
                return ("pending", name, str(o.get("symbol")))
    held: dict[tuple, float] = {}
    for name, book in positions.items():                                       # C: lots
        if not asked(name):
            continue
        if not isinstance(book, dict):
            return not_a_book(name, book)
        for sym, lot in book.items():
            units = _units(lot.get("units") if isinstance(lot, dict) else None)
            if units is None:
                return ("unreadable", f"it holds a lot of {name} ({sym}) whose units are not a number", "")
            if units < 0:
                return ("short", str(name), str(sym))
            held[(name, sym)] = units
    for o in pending:                                                           # C: pending orders, in fill order
        what = ("a pending entry that is not an order" if not isinstance(o, dict) else
                "a pending order with no strategy name" if not isinstance(o.get("strategy"), str) else
                "a pending order with no symbol" if not isinstance(o.get("symbol"), str) else None)
        if what is not None:
            if only is None:
                return ("unreadable", f"it holds {what}", "")
            continue
        name, sym = o["strategy"], o["symbol"]
        if not asked(name):
            continue
        units = _units(o.get("units"))
        if units is None:
            return ("unreadable", f"it holds a pending order of {name} for {sym} whose units are not a number", "")
        after = held.get((name, sym), 0.0) + units                            # the fill's own arithmetic
        held[(name, sym)] = 0.0 if abs(after) < _ZERO else after
        if held[(name, sym)] < 0:
            return ("short", name, sym)
    return None


def paper_book_words(found: tuple[str, str, str], tail: str) -> str:
    """judge_paper_book's finding in words, ending with `tail` (what the command did not do)."""
    rule, name, sym = found
    if rule == "lot":
        core = f"the paper book holds a lot of {name} ({sym}), which can hold a short and is no longer stepped"
    elif rule == "pending":
        core = f"the paper book holds a pending order of {name} ({sym}), which can hold a short and is no longer stepped"
    elif rule == "short":
        core = (f"the paper book holds a short position (or an order that would open one) under {name}, {sym}; the "
                "paper book and its tax ledger do not handle shorts")
    else:
        core = f"the paper book's state cannot be read: {name}"
    return f"{core}; {tail}"


def _book_state(paths: LoopPaths):
    """The paper book's state as the paper trader reads it (paper_state.json; a missing file is a book not started),
    or None when the file cannot be read or parsed (the paper trader then refuses it itself, where it always has).
    Writes and creates nothing."""
    try:
        raw = (paths.journal / "paper_state.json").read_bytes()
    except FileNotFoundError:
        return {"positions": {}, "pending": []}
    except Exception:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


class PaperBookRefused(Exception):
    """The paper book breaks its invariant (judge_paper_book): the run steps nothing."""



def _left_out_names(paths: LoopPaths, ledger: HypothesisLedger | None) -> list[str]:
    """The names of the paper ideas' strategies that can hold a short, read idea by idea in the paper command's order
    (T38-1b addendum 3): an idea whose strategy file cannot be loaded names no strategy here (its error surfaces later,
    exactly where it always has), and every other is still named. None of them when there is no ledger. The one
    reading of the early checks and of the order step's whole-book refusal."""
    names = []
    for s in PAPER_STATUSES if ledger is not None else ():
        for h in ledger.search(status=s):
            try:
                strategy = _strategy_for(paths, ledger, h["id"], None)[0]
                name = strategy.name
            except Exception:
                continue
            if can_hold_shorts(strategy):
                names.append(name)
    return names


MIRROR_TAIL = "the order step does not mirror such a book"


def _whole_book_refusal(paths: LoopPaths, ledger: HypothesisLedger | None, trader):
    """For the order step with no broker book file outside live mode (the whole book): None when judge_paper_book
    finds nothing in the book the order step would mirror (`trader`'s state, else the state on disk), the left-out
    strategies read idea by idea through the research ledger (none when it has no file; an idea whose strategy cannot
    be loaded names no strategy, as before, and rule C still covers every name); otherwise an order_step.Follow
    refusal in its words."""
    from valedia_trader.execution_and_risk import order_step
    state = trader.state if trader is not None else _book_state(paths)
    if state is None:
        return None                                     # unreadable: the order step's own reading refuses it
    own = None
    try:
        if ledger is None and paths.ledger.is_file():
            ledger = own = HypothesisLedger(paths.ledger)
        left = _left_out_names(paths, ledger)
    finally:
        if own is not None:
            own.close()
    found = judge_paper_book(state, left)
    return None if found is None else order_step.Follow(refused=paper_book_words(found, MIRROR_TAIL))



ORDER_STEP_ACCOUNT_CLASSES = ("INDIVIDUAL_CASH", "INDIVIDUAL_MARGIN")


class _AccountClassNotNamed(Exception):
    """WEBULL_ACCOUNT_CLASS is missing or not a stock account class; says nothing of the value."""


def _order_step_account_class_named(environ=None) -> None:
    """Raises _AccountClassNotNamed unless WEBULL_ACCOUNT_CLASS is INDIVIDUAL_CASH or INDIVIDUAL_MARGIN, read as
    WebullVenue.from_secrets reads it (stripped, upper-cased; the secrets file is loaded first when no `environ` is
    given). With the account chosen by WEBULL_ACCOUNT_ID alone an Events account would take the Cash venue's name and
    be refused only inside the session, after files were made."""
    import os
    if environ is None:
        from valedia_trader.core.secrets import load_env
        load_env()
        environ = os.environ
    if (environ.get("WEBULL_ACCOUNT_CLASS") or "").strip().upper() not in ORDER_STEP_ACCOUNT_CLASSES:
        raise _AccountClassNotNamed()


def _order_step_venue():
    """Webull's venue from the secrets file, after the account class is found named there (nothing is built before).
    The order step itself refuses anything but the test environment, before any call to it."""
    _order_step_account_class_named()
    from valedia_trader.venues.webull import WebullVenue
    return WebullVenue.from_secrets()


def _order_step_environment(environ=None) -> str:
    """The Webull environment the venue would be built for, read as WebullVenue.from_secrets reads it (WEBULL_ENV,
    default sandbox). With no `environ`, the secrets file is loaded first, as from_secrets does."""
    import os
    if environ is None:
        from valedia_trader.core.secrets import load_env
        load_env()
        environ = os.environ
    return (environ.get("WEBULL_ENV") or "sandbox").strip().lower()


def _order_step_live_names(environ=None) -> list[str]:
    """T50: the live names (secrets.LIVE_NAMES) in reach of the practice order step: in `environ`, or, with none
    given, in the process environment after the ordinary secrets file is loaded into it (so a name in the file counts).
    Never opens the live file."""
    import os
    from valedia_trader.core.secrets import live_names_in, load_env
    if environ is None:
        load_env()
        environ = os.environ
    return live_names_in(environ)


def _order_step_refused() -> bool:
    """True, with one line on stderr, when a live name is in reach (T50), when the configured environment is not the
    test environment, or when the secrets file cannot be loaded (the line names the error's class only, nothing from
    the file or its path; for a live name, the name only)."""
    from valedia_trader.core.secrets import LiveNameInReach
    from valedia_trader.execution_and_risk.order_step import TEST_ENVIRONMENT, _venue_error
    try:
        configured = _order_step_environment()
        found = _order_step_live_names()
    except LiveNameInReach as exc:                       # the loader refused the file and set nothing (names, no value)
        print(f"refused: {exc}; the practice order step never runs with a live name in reach", file=sys.stderr)
        return True
    except Exception as exc:
        print(f"refused: the trading secrets file could not be loaded ({_venue_error(exc)}); it must be readable by its "
              "owner only (chmod 600) and hold KEY=VALUE lines", file=sys.stderr)
        return True
    if found:
        print(f"refused: {' and '.join(found)} {'is' if len(found) == 1 else 'are'} in reach of the practice order step "
              "(the process environment or the trading secrets file); the live pair belongs in webull-live.env only, "
              "read by the attended order-step --live", file=sys.stderr)
        return True
    if configured == TEST_ENVIRONMENT:
        return False
    print(f"refused: the order step runs against Webull's test environment ({TEST_ENVIRONMENT}) only and WEBULL_ENV "
          "names another; live orders run only through the attended order-step --live, from its own key file",
          file=sys.stderr)
    return True


def _order_step_build():
    """The venue for the order step, or None after one plain line on stderr: when the configured environment is not
    the test environment, or the secrets file cannot be loaded, or building the venue raises WebullError, the
    secrets loader's SecretError or an OSError. The line names the error's class (and a plain code) only, never
    anything from the secrets file or a path inside it. Every other exception is a programming error and goes
    through."""
    from valedia_trader.core.secrets import SecretError
    from valedia_trader.execution_and_risk.order_step import _venue_error
    from valedia_trader.venues.webull import WebullError
    if _order_step_refused():
        return None
    try:
        venue = _order_step_venue()
    except _AccountClassNotNamed:
        print("refused: the order step needs the account class named: set WEBULL_ACCOUNT_CLASS to INDIVIDUAL_CASH or "
              "INDIVIDUAL_MARGIN (WEBULL_ACCOUNT_ID alone does not say which kind of account it is)", file=sys.stderr)
        return None
    except (WebullError, SecretError, OSError) as exc:
        print(f"refused: the Webull venue could not be set up ({_venue_error(exc)}); check the trading secrets file "
              "and its WEBULL_ settings", file=sys.stderr)
        return None
    return None if _order_step_events(venue) else venue


def _order_step_events(venue) -> bool:
    """True, with one line on stderr, when the venue is an Events venue (order_step.events_refusal: its name, or its
    account class without a network read). The line is the order step's own words, nothing from the secrets file."""
    from valedia_trader.execution_and_risk.order_step import events_refusal
    why = events_refusal(venue)
    if why is not None:
        print(f"refused: {why}", file=sys.stderr)
        return True
    return False


BROKER_BOOK_SHOWN = "config/broker_book.json"


def broker_book_follow(paths: LoopPaths, ledger: HypothesisLedger | None = None, *, live: bool = False, trader=None):
    """config/broker_book.json (T32): which strategies of the paper book the broker book follows, as the order step
    is given it. None when the file is missing outside live mode (the whole book, as before). Otherwise an
    order_step.Follow: the hypothesis ids, their strategies' names (through the ledger and the strategy files, as
    the paper command names them: _strategy_for(...)[0].name) and a digest of the file's value and the names; or
    `refused`, why the file or an id cannot be followed, in words a person can act on. Read through
    webull.read_owner_file (a link, a second name, a file writable by others, a repeated key, NaN and the rest are
    refused). In live mode the file is the repository's own (webull.LIVE_REPO) and a missing file is refused: live
    mode never follows the whole book by default. Each id must be a hypothesis the paper command steps (its ledger
    status one of PAPER_STATUSES, T32 rev 2). With `trader` (a PaperTrader) each name must also be among the
    strategies of the paper book's latest day, and `every` says whether the names are all of them (the broker book
    is then the whole book, on the paper equity). `ledger` None: the research ledger is opened only when its file is
    there (a missing one is never made). Never raises; writes, creates or repairs nothing."""
    import hashlib
    import os

    from valedia_trader.execution_and_risk import order_step
    from valedia_trader.venues import webull
    shown = BROKER_BOOK_SHOWN
    Follow = order_step.Follow
    try:
        if live:
            paths = LoopPaths(Path(os.path.abspath(os.fspath(webull.LIVE_REPO))))
        raw, why = webull.read_owner_file(paths.config / order_step.BROKER_BOOK_FILE, paths.root)
        if why == "is missing" and not live:
            return _whole_book_refusal(paths, ledger, trader)   # T38-1b addendum 2 E: None, or the book refused
        if why == "is missing":
            return Follow(refused=f"{shown} is missing: live mode follows only the strategies that file names (such as "
                                  '{"follow": [26]}) and never follows the whole book by default')
        if why is not None:
            return Follow(refused=f"{shown} {why}")
        ids, why = order_step.judge_follow(raw)
        if why is not None:
            return Follow(refused=f"{shown} {why}")
        own = None
        if ledger is None:
            if not paths.ledger.is_file():
                return Follow(refused=f"the research ledger (memory/ledger.db) is missing, so the hypotheses {shown} "
                                      "names cannot be found")
            ledger = own = HypothesisLedger(paths.ledger)
        try:
            names: list[str] = []
            for hid in ids:
                try:
                    status = ledger.get(hid).get("status")
                except LedgerError:
                    return Follow(refused=f"{shown} names hypothesis {hid}, which the research ledger does not hold")
                if status not in PAPER_STATUSES:            # rev 2: only a hypothesis the paper command steps today
                    from valedia_trader.research_loop.ledger import STATUSES
                    said = status if status in STATUSES else "not one it knows"
                    return Follow(refused=f"{shown} names hypothesis {hid}, whose status in the research ledger is "
                                          f"{said}; the paper command steps only {' and '.join(PAPER_STATUSES)}")
                try:
                    strategy = _strategy_for(paths, ledger, hid, None)[0]
                    name = strategy.name
                except Exception as exc:
                    return Follow(refused=f"{shown} names hypothesis {hid}, whose strategy cannot be loaded "
                                          f"({order_step._describe(exc)})")
                if type(name) is not str or not order_step._plain(order_step._NAME, name):
                    return Follow(refused=f"{shown} names hypothesis {hid}, whose strategy has no plain name")
                if can_hold_shorts(strategy):               # T38-1b: live and not live
                    return Follow(refused=f"{shown} names hypothesis {hid}, whose strategy can hold a short position; "
                                          "the order step sells only what the broker book owns, so it cannot be "
                                          "followed until shorts are built")
                if name in names:
                    return Follow(refused=f"{shown} names hypotheses {ids[names.index(name)]} and {hid}, whose strategies "
                                          f"share the name {name}; follow it once")
                names.append(name)
        finally:
            if own is not None:
                own.close()
        state = trader.state if trader is not None else _book_state(paths)    # T38-1b addendum 2: C for these names
        found = None if state is None else judge_paper_book(state, (), only=set(names))
        if found is not None:
            return Follow(refused=paper_book_words(found, MIRROR_TAIL))
        if trader is not None:
            day, latest = order_step.paper_strategies(trader)
            if latest is None:
                return Follow(refused=f"the paper book's latest day ({day}) does not list its strategies, so what {shown} "
                                      "names cannot be found in it")
            for hid, name in zip(ids, names):
                if name not in latest:
                    return Follow(refused=f"{shown} names hypothesis {hid} ({name}), which is not among the strategies of "
                                          f"the paper book's latest day ({day}: {', '.join(sorted(latest)) or 'none'})")
        digest = hashlib.sha256(json.dumps({"file": raw, "ids": list(ids), "names": names}, sort_keys=True,
                                           ensure_ascii=True, allow_nan=False).encode()).hexdigest()
        every = trader is not None and set(names) == set(latest)     # rev 2: the whole latest day is followed
        return Follow(ids=tuple(ids), names=tuple(names), digest=digest, every=every)
    except Exception as exc:                             # nothing about the broker book file may raise
        return Follow(refused=f"{shown} could not be judged ({type(exc).__name__})")


def order_step_command(args, paths: LoopPaths, venue=None, ledger: HypothesisLedger | None = None) -> int:
    """One order step against the venue `_order_step_venue` gives (built here when not given); prints the summary.
    0 when the run went through, 2 when it stopped. The command line builds the venue before any folder or file is
    made, and refuses a configured environment other than the test environment before building it. With --propose
    the run writes a ticket and sends nothing; its number is printed once, on a line of its own after the summary
    (it is never journaled). With --approve NUMBER the run sends only that ticket's orders, each after fresh checks.
    Without either flag the run is called exactly as before. With config/broker_book.json (T32) the run follows only
    the strategies it names (broker_book_follow; `ledger` is the research ledger, opened when not given); without
    it, the whole book, called exactly as before."""
    from valedia_trader.execution_and_risk import order_step
    if venue is None:
        venue = _order_step_build()
        if venue is None:
            return 2
    elif _order_step_events(venue):                     # before the paper trader, the ledger or any file
        return 2
    ticket = {}                                          # nothing new is passed to a run without the flags
    if getattr(args, "propose", False):
        ticket["propose"] = True
    elif getattr(args, "approve", None) is not None:
        ticket["approve"] = args.approve
    trader = PaperTrader(paths.journal, paths.risk_limits)
    follow = broker_book_follow(paths, ledger, trader=trader)
    followed = {} if follow is None else {"follow": follow}   # nothing new is passed without the file
    ledger_path = order_step.ledger_path_for(paths.memory, venue.name)
    orders = order_step.open_ledger(ledger_path, clock=order_step.wall_clock)
    try:
        out = order_step.run_order_step(
            ledger=orders, venue=venue, paper=lambda: order_step.paper_targets(trader), quotes=order_step.minute_quotes,
            journal=trader.journal, limits_file=paths.risk_limits, state=order_step.state_path_for(ledger_path, venue.name),
            clock=order_step.wall_clock, halt_file=risk_gate.HALT_FILE, latch_file=trader.latch_file, capital=args.capital,
            fractional=args.fractional, **ticket, **followed, **({"shared": True} if getattr(args, "shared", False) else {}))
    finally:
        orders.close()
    number = out.pop("ticket_number", None) if ticket else None
    print(json.dumps(out, indent=2, default=str))
    if number is not None:
        again = "".join([f" --capital {args.capital!r}" if args.capital is not None else "",
                         " --fractional" if args.fractional else "",
                         " --shared" if getattr(args, "shared", False) else ""])
        print(f"ticket number: {number} (approve within {order_step.TICKET_LIFE_SECONDS // 60} minutes with: "
              f"order-step --approve NUMBER{again})")
    return 0 if out["status"] == "done" else 2


LIVE_CLASS = "INDIVIDUAL_CASH"                          # live mode's only account class (stocks and funds)


class _LiveRefused(Exception):
    """Live mode cannot go on; the message is the command's own words, nothing from the secrets file."""


def _live_args_refusal(args) -> str | None:
    """Why the flags given cannot run (checked before anything is read), or None."""
    import math
    live, check = getattr(args, "live", False), getattr(args, "check", False)
    if check and not live:
        return "--check is part of live mode: order-step --live --check"
    shared = getattr(args, "shared", False)
    if shared and check:
        return "--check judges the locks and sends nothing: it takes no --shared (a live run is --live --shared --propose)"
    if check and getattr(args, "fractional", False) and live:
        return "live mode trades whole shares only (no --fractional)"      # 4db3192's own words, for a check
    if not live:
        return None
    capital = getattr(args, "capital", None)
    if check:
        return None if capital is None or (math.isfinite(capital) and capital > 0) else "--capital must be a number above 0"
    if not getattr(args, "propose", False) and getattr(args, "approve", None) is None:
        return ("live mode runs only as a ticket: order-step --live --shared --propose, then order-step --live --shared "
                "--approve NUMBER (or order-step --live --check)")
    if not shared:                                      # T51c S4d (addendum 7): live is the shared account's book, always
        return ("a live run is always the shared account's book (the owner also trades that account): add --shared, as in "
                "order-step --live --shared --propose; it is not implied, so that the command says what it runs")
    if capital is not None:                             # R26: the capital is the book's own value; lock 1 binds it
        return ("--capital is refused in the shared mode: the capital is the book's own value (the cautious bound), so a "
                "stake is changed by the owner's entry and never by a figure typed to a run; the confirmation's "
                "max_capital still binds that value")
    return None


def _live_environ():
    """The environment the command runs with: the trading secrets file loaded into it (for the second price sources;
    T50: the live pair is never read from here, and _live_environment_refusal judges what was loaded)."""
    import os
    from valedia_trader.core.secrets import load_env
    load_env()
    return os.environ


def _attended() -> bool:
    """T50: a person at a terminal: standard input is a terminal. A job started by launchd, cron or a pipe has none,
    so live mode refuses it. This stops an accident only: a program that fakes a terminal is not caught here (the
    doctor's jobs check is the other half: no scheduled job may name live mode or a live key name)."""
    import os
    try:
        return bool(os.isatty(sys.stdin.fileno()))
    except Exception:
        return False


def _live_environment_refusal(environ) -> str | None:
    """T50: why live mode cannot run with the ordinary environment it loaded, or None. The ordinary secrets file (and
    the process environment, which load_env makes agree with it) must stay a practice file: WEBULL_ENV the practice
    value or absent, and no live name (secrets.LIVE_NAMES) in it, because the live pair is read from webull-live.env
    only."""
    from valedia_trader.core.secrets import is_practice_environment, live_names_in
    found = live_names_in(environ)
    if found:
        return (f"live mode reads the live pair from webull-live.env only, and {' and '.join(found)} "
                f"{'is' if len(found) == 1 else 'are'} in the trading secrets file or the process environment: remove "
                f"{'it' if len(found) == 1 else 'them'} from there")
    if not is_practice_environment(environ):
        return ("live mode needs WEBULL_ENV in the trading secrets file and the process environment to be sandbox (or "
                "absent): the ordinary file stays a practice file, and the live entry fixes production in code")
    return None


def _live_files(paths: LoopPaths, environ):
    """What live mode's locks are judged with: the environment. Where they are read is the repository's own config/
    and journal/ (go_live.LiveFiles' properties, under webull.LIVE_REPO), never `paths`: the caller has already
    refused any other root (_live_root_refusal)."""
    from valedia_trader.execution_and_risk import go_live
    return go_live.LiveFiles(environ=environ)


def _live_root_refusal(paths: LoopPaths) -> str | None:
    """Live mode exists only for the repository the package lives in (webull.LIVE_REPO): a root that resolves (links
    resolved on both sides) to any other folder is refused before anything is read. Why, or None."""
    import os
    from valedia_trader.venues import webull
    if os.path.realpath(os.fspath(paths.root)) != os.path.realpath(os.fspath(webull.LIVE_REPO)):
        return "live mode runs only from the repository itself (--root names another folder)"
    return None


def _order_step_live_venue(files, environ, live_file=None):
    """Webull's production venue (T50): the pair from webull-live.env only (`live_file` is for tests), production and
    its host fixed in code (WebullVenue.from_live_file), no account id, the account class from the owner's
    confirmation (the repository's own config/webull_live.json, judged again here), and that confirmation as the file
    the adapter's own lock reads. Nothing in `environ` is read: not WEBULL_ENV, not a key, not WEBULL_ACCOUNT_ID or
    WEBULL_ACCOUNT_CLASS. Raises _LiveRefused when the confirmation cannot be used."""
    from valedia_trader.venues import webull
    own = webull.own_confirmation()
    confirmation, why = webull.live_confirmation(own)
    if confirmation is None:
        raise _LiveRefused(f"the owner's confirmation (config/webull_live.json) {why}; no live venue is built")
    return webull.WebullVenue.from_live_file(live_file, account_class=confirmation["account_class"], confirmation_file=own)


class _LockStub:
    """What an approve whose locks are unmet is run against, so that the order step marks the refusal on the ticket
    file: the production venue's name, environment and host as plain attributes and nothing else (any other use
    raises; nothing is read from any account)."""
    environment = "production"
    host = "api.webull.com"
    account_class = LIVE_CLASS

    def __init__(self) -> None:
        self.name = "webull-production"

    def __getattr__(self, name):
        raise AttributeError(f"{name}: nothing is read while a lock is unmet")


def _build_live_venue(build_venue, files, environ, live_file=None):
    """The live venue, or (None, why) in the command's own words (for the live file, its fault in the reader's own
    words, which never hold a value)."""
    from valedia_trader.core.secrets import LiveFileError, SecretError
    from valedia_trader.execution_and_risk.order_step import _venue_error
    from valedia_trader.venues.webull import WebullError
    try:
        if build_venue is None and live_file is not None:
            return _order_step_live_venue(files, environ, live_file=live_file), None
        return (build_venue or _order_step_live_venue)(files, environ), None
    except _LiveRefused as exc:
        return None, str(exc)
    except LiveFileError as exc:
        return None, f"the live key file cannot be used: {exc}"
    except (WebullError, SecretError, OSError) as exc:
        return None, (f"the Webull venue could not be set up ({_venue_error(exc)}); check webull-live.env (the live "
                      "pair) and the owner's confirmation")


def _live_account_report(venue, confirmation) -> tuple[dict | str, bool]:
    """The account the venue will trade, as `check` prints it: its class and the last 4 characters of its id only."""
    import re
    from valedia_trader.execution_and_risk.order_step import _venue_error
    try:
        cls = venue.account_class
        account = venue.account_id
    except Exception as exc:
        return f"could not be read: {_venue_error(exc)}", False
    ends = account[-4:] if type(account) is str and len(account) >= 4 else None
    shown = cls if type(cls) is str and re.fullmatch(r"[A-Z_]{1,40}", cls) else None
    matches = shown == confirmation["account_class"] and ends == confirmation["account_ends"]
    return {"account_class": shown, "account_ends": ends, "matches_the_confirmation": matches}, matches


def _run_live(args, paths: LoopPaths, venue, files, second_opinion) -> int:
    from valedia_trader.execution_and_risk import go_live, order_step
    ticket = {"propose": True} if args.propose else {"approve": args.approve}
    trader = PaperTrader(paths.journal, paths.risk_limits)
    follow = broker_book_follow(paths, live=True, trader=trader)   # T32: read again for this run, with its paper book
    ledger_path = order_step.ledger_path_for(paths.memory, venue.name)
    orders = order_step.open_ledger(ledger_path, clock=order_step.wall_clock)
    try:
        out = order_step.run_order_step(
            ledger=orders, venue=venue, paper=lambda: order_step.paper_targets(trader), quotes=order_step.minute_quotes,
            journal=trader.journal, limits_file=paths.risk_limits, state=order_step.state_path_for(ledger_path, venue.name),
            clock=order_step.wall_clock, halt_file=risk_gate.HALT_FILE, latch_file=trader.latch_file, capital=None,
            fractional=bool(getattr(args, "fractional", False)), live=files, second_opinion=second_opinion, follow=follow,
            shared=True, practice_ledger=order_step.ledger_path_for(paths.memory, go_live.PAPER_VENUE), **ticket)
    finally:
        orders.close()
    number = out.pop("ticket_number", None)
    print(json.dumps(out, indent=2, default=str))
    if number is not None:
        opened = isinstance(out.get("live_fractions"), dict) and out["live_fractions"].get("open") is True
        again = " --fractional" if getattr(args, "fractional", False) and opened else ""   # shut: what the run without it prints
        print(f"ticket number: {number} (approve within {order_step.TICKET_LIFE_SECONDS // 60} minutes with: "
              f"order-step --live --shared{again} --approve NUMBER)")
    return 0 if out["status"] == "done" else 2


def live_order_step_command(args, paths: LoopPaths, environ=None, build_venue=None, second_opinion=None, today=None,
                            attended=None, live_file=None) -> int:
    """`order-step --live`: the go-live gate (docs/specs/2026-11-13-go-live-gate-design.md). The flags are checked
    first, before anything is read; then the four locks from the owner's files and the paper record, before any
    venue is built. --check prints each lock (met or not, the figures and the owner's numbers) and, only when locks 1
    to 3 are met, the account the venue will trade (its class and last 4); it sends nothing. --propose and --approve
    run the order step in live mode once every lock is met; with a lock unmet they say which and by how much and
    exit 2 (an approve is still marked refused on the ticket file, when there is one, without any account read).
    Then config/broker_book.json (T32, broker_book_follow): missing or refused, the run is refused the same way,
    before any venue is built; live mode never follows the whole book. `environ` (default: the secrets file loaded into the process environment), `build_venue`, `second_opinion` and
    `today` are for tests. Nothing here writes, creates or repairs an owner's file.

    T50: every live command, --check included, needs a person at a terminal (_attended; `attended` is for tests),
    checked right after the flags and before anything is read; then the ordinary environment must be a practice one
    (_live_environment_refusal: WEBULL_ENV sandbox or absent, no live name in it). The venue's pair comes from
    webull-live.env only (`live_file` is for tests), its environment and host are fixed in code."""
    from valedia_trader.core.secrets import LiveNameInReach
    from valedia_trader.execution_and_risk import go_live, order_step
    from valedia_trader.venues.webull import ny_date
    why = _live_args_refusal(args) or _live_root_refusal(paths)
    if why is None and not (_attended() if attended is None else attended):
        why = ("live mode needs a person at a terminal (standard input is not a terminal); it never runs from a job, a "
               "pipe or a schedule")
    if why is not None:
        print(f"refused: {why}", file=sys.stderr)
        return 2
    import os
    from valedia_trader.venues import webull
    paths = LoopPaths(Path(os.path.abspath(os.fspath(webull.LIVE_REPO))))   # the repository's folders, nothing else
    if environ is None:
        try:
            environ = _live_environ()
        except LiveNameInReach as exc:                   # the loader refused the file and set nothing (names, no value)
            print(f"refused: {exc}; live mode reads the live pair from webull-live.env only; nothing was read from the "
                  "account", file=sys.stderr)
            return 2
        except Exception as exc:
            print(f"refused: the trading secrets file could not be loaded ({order_step._venue_error(exc)}); it must be "
                  "readable by its owner only (chmod 600) and hold KEY=VALUE lines", file=sys.stderr)
            return 2
    why = _live_environment_refusal(environ)
    if why is not None:
        print(f"refused: {why}; nothing was read from the account", file=sys.stderr)
        return 2
    files = _live_files(paths, environ)
    state = go_live.judge_locks(files, ny_date() if today is None else today, args.capital)
    first_three = all(lock["met"] for lock in state.locks[:3])
    if args.check:
        report: dict = {"locks": state.locks, "account": "not read: locks 1 to 3 are not all met"}
        ok = first_three
        if first_three:
            venue, why = _build_live_venue(build_venue, files, environ, live_file)
            if venue is None:
                report["account"], ok = f"not read: {why}", False
            else:
                report["account"], ok = _live_account_report(venue, state.confirmation)
        report["all_met"] = bool(ok and state.met)
        print(json.dumps(report, indent=2, default=str))
        return 0 if report["all_met"] else 2
    if not state.met:
        print(f"refused: {state.unmet()}; nothing was read from the account and no quote was asked", file=sys.stderr)
        if args.approve is not None:                    # the refused approve is marked, as step 1 marks one
            ledger_path = order_step.ledger_path_for(paths.memory, go_live.LIVE_VENUE)
            if Path(order_step.ticket_path_for(order_step.state_path_for(ledger_path, go_live.LIVE_VENUE))).exists():
                _run_live(args, paths, _LockStub(), files, second_opinion)
        return 2
    trader = PaperTrader(paths.journal, paths.risk_limits)   # rev 2: judged in full (latest day too) before any venue
    follow = broker_book_follow(paths, live=True, trader=trader)   # T32: before any venue is built; never the whole book
    if follow.refused is not None:
        print(f"refused: {follow.refused}; nothing was read from the account and no quote was asked", file=sys.stderr)
        if args.approve is not None:                    # the refused approve is marked, as for a lock unmet
            ledger_path = order_step.ledger_path_for(paths.memory, go_live.LIVE_VENUE)
            if Path(order_step.ticket_path_for(order_step.state_path_for(ledger_path, go_live.LIVE_VENUE))).exists():
                _run_live(args, paths, _LockStub(), files, second_opinion)
        return 2
    venue, why = _build_live_venue(build_venue, files, environ, live_file)
    if venue is None:
        print(f"refused: {why}", file=sys.stderr)
        return 2
    paths.ensure()
    return _run_live(args, paths, venue, files, second_opinion)



def add_arguments(p: argparse.ArgumentParser) -> None:
    """The `order-step` command's arguments, on `p`: the one definition, for this module's parser and for
    research_loop's `order-step` subcommand."""
    p.add_argument("--capital", type=float, default=None,
                   help="the broker capital the paper book is scaled to (default: the paper equity, a scale of 1)")
    p.add_argument("--fractional", action="store_true",
                   help="fractional units (5 decimals) instead of whole shares; with --live only when the practice proof, "
                        "the owner's consent and the shared book all hold (then the whole legs follow the split and the "
                        "remainders under one share go as MARKET orders, one per run until the first has reconciled; "
                        "docs/WEBULL.md, Fractions on live); otherwise the run is exactly the live run without --fractional")
    p.add_argument("--shared", action="store_true",
                   help="T51c S4c: the account is shared with its owner and the fund book is judged on its own "
                        "figures (its own value is the capital, so --capital is refused; needs the owner's opening entry); "
                        "required with --live (S4d: a live run is always the shared account's book)")
    ticket = p.add_mutually_exclusive_group()
    ticket.add_argument("--propose", action="store_true",
                        help="write a ticket of the orders instead of sending them; nothing is sent, the number is printed")
    ticket.add_argument("--approve", metavar="NUMBER", default=None,
                        help="send only the orders of this ticket (six digits, within 15 minutes), each after fresh checks")
    ticket.add_argument("--check", action="store_true",
                        help="with --live: print each lock of the go-live gate and, when locks 1 to 3 are met, the account")
    p.add_argument("--live", action="store_true",
                   help="real money: only with --shared and --propose or --approve (never --capital: the book's own value "
                        "is the capital, at most the confirmation's max_capital), under the go-live gate's four locks; "
                        "with --owner-entry, the live venue's book (a production venue's name needs it)")
    owner = p.add_argument_group("the owner's entry (T51c; a shared run reads it)")
    owner.add_argument("--owner-entry", choices=("cash_in", "cash_out", "units_out", "split", "mark"), default=None,
                       help="write one entry of the owner's into the venue's state file instead of running; every "
                            "figure is his own and none is defaulted; `mark` (with --symbol alone) takes the fund's "
                            "present quote, as the order step's own quote path gives it, as its new mark (no cash and "
                            "no units change): it clears a stop at stage `marks` for that fund")
    owner.add_argument("--venue", default=None, help="the venue whose book it is, as in its state file's name")
    owner.add_argument("--amount", default=None, help="cash_in, cash_out: the amount, digits and a decimal part")
    owner.add_argument("--symbol", default=None, help="units_out, split, mark: the symbol")
    owner.add_argument("--units", default=None, help="units_out: the shares taken that the book counted as its own")
    owner.add_argument("--ratio", default=None, help="split: new units per old unit")
    owner.add_argument("--mark", action="append", default=None, metavar="SYMBOL=PRICE",
                       help="not taken (T51c S4b): marks come from the order step's own quotes; given, it is refused")
    owner.add_argument("--who", default=None, help="who writes the entry")
    owner.add_argument("--note", default=None, help="why")
    owner.add_argument("--confirm", default=None, metavar="CODE",
                       help="write the entry the preview showed: the same command with the code it printed, within a "
                            "ticket's life; without it the command only previews and writes no entry")
    owner.add_argument("--owner-entry-rejournal", default=None, metavar="N",
                       help="repair: write the missing journal line of the LAST entry N (with --hash, --who, --note)")
    owner.add_argument("--hash", default=None, help="with --owner-entry-rejournal: the entry's hash, as printed")
    owner.add_argument("--accept-history", default=None, metavar="DAY",
                       help="live repair (T51c S4d): accept the state file's latest book_nav row of DAY (YYYY-MM-DD) as the "
                            "day's figure when it differs from the journal's last figure of the day and is NOT BELOW it "
                            "(it only tightens; a lower row has no route here); with --live --venue --who --note, a "
                            "preview first, then --confirm CODE")


def run(args) -> int:
    """The `order-step` command with its arguments read (`args`: add_arguments' flags and `root`): the one code path of
    this module's `main` and of `research_loop order-step`. The repair, the owner's entry and live mode come first, each
    before any venue is built; then the practice venue is built before any folder or file is made; then the folders,
    the research ledger and the run. A refusal the command has always said in one line is said here in the same words
    and exits 2 (the research loop's other refusals, GauntletError and ShortAfterStep, cannot come from this code path:
    no module the order path imports raises them)."""
    if args.accept_history is not None:                 # T51c S4d: the live book's repair
        from valedia_trader.execution_and_risk import order_step
        return order_step.accept_history_command(args, LoopPaths(Path(args.root).resolve()) if args.root else LoopPaths())
    if (args.owner_entry is not None or args.owner_entry_rejournal is not None
            or args.confirm is not None or args.hash is not None):
        # T51c S3: the owner's entry; no venue is built (S4b: its preview, its confirm and its repair too)
        from valedia_trader.execution_and_risk import order_step
        return order_step.owner_entry_command(args, LoopPaths(Path(args.root).resolve()) if args.root else LoopPaths())
    if args.live or args.check:                         # live mode: its flags and locks before anything else
        from valedia_trader.venues import webull
        return live_order_step_command(args, LoopPaths(Path(args.root).resolve()) if args.root else LoopPaths(webull.LIVE_REPO))
    venue = _order_step_build()                         # before any folder or file is made
    if venue is None:
        return 2
    paths = LoopPaths(Path(args.root).resolve()) if args.root else LoopPaths()
    paths.ensure()
    ledger = HypothesisLedger(paths.ledger)
    try:
        return order_step_command(args, paths, venue=venue, ledger=ledger)
    except (LedgerError, SpecError, risk_gate.RiskConfigError, LatchResetError, PaperBookNoPrice, PaperBookRefused) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    finally:
        ledger.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="order_step_cli", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 allow_abbrev=False)   # T50: --liv, --li, --l and every other prefix are errors, not --live
    ap.add_argument("--root", default=None, help="project root (default: the repository)")
    ap.add_argument("--data", default="artifacts/data", help="taken as research_loop takes it; the order step reads no price data")
    add_arguments(ap)
    return run(ap.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
