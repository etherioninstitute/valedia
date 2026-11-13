"""Price-forecast models as forecasters in the forecast tournament, starting with Kronos.

Kronos (Kronos-small, a model trained on price bars) answers the tournament's direction questions
("does this asset close higher over the next N trading days?") from the price bars alone. It draws
`paths` possible futures and its probability is the share of them that end above the day's close.

What it sees: the last `context` daily bars (open, high, low, close, volume) of the asset dated on or
before the question's day, and the DATES of the next N sessions. Never a price dated after the day.

Its training data runs to June 2024 by its authors' account and the weights were published on 30 June
2025, so only questions after that can count. `ask_price_model` asks nothing dated before `not_before`;
choosing that date is the caller's job, so the command line has no default for it.

Kronos runs in a worker process (data_and_fees/kronos_worker.py) under the trading-lab interpreter, which
has torch and every Kronos dependency; the trader's own environment lacks one. Nothing is downloaded.

    python -m valedia_trader.research_loop.price_models refresh-ohlc --universe funds
    python -m valedia_trader.research_loop.price_models ask-kronos --universe funds --horizon 5 --not-before 2025-07-01 --with-rules
    python -m valedia_trader.research_loop.tournament score
"""
from __future__ import annotations

import argparse
import atexit
import hashlib
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

from valedia_trader.data_and_fees import ohlc as ohlc_data
from valedia_trader.research_loop import LoopPaths
from valedia_trader.research_loop import tournament as tm

__all__ = ["KronosForecaster", "window_for", "future_days", "dense_questions", "ask_price_model", "ohlc_universe", "main"]

TRADING_LAB = Path(__file__).resolve().parents[3] / "trading-lab"
KRONOS_PYTHON = TRADING_LAB / "runtime" / "venv" / "bin" / "python"
KRONOS_SOURCE = TRADING_LAB / "upstream" / "Kronos-67b630e67f6a18c9e9be918d9b4337c960db1e9a"
KRONOS_MODEL = TRADING_LAB / "models" / "Kronos-small"
KRONOS_TOKENIZER = TRADING_LAB / "models" / "Kronos-Tokenizer-base"
WORKER = Path(__file__).resolve().parents[1] / "data_and_fees" / "kronos_worker.py"

FIELDS = ohlc_data.FIELDS
MIN_WINDOW = 200


def _is_coin(symbol: str) -> bool:
    return symbol.endswith("-USD")


class KronosForecaster:
    """Kronos as a tournament forecaster. The worker starts on the first question, is restarted once if it
    dies during a request, and is closed at exit. `worker_argv` replaces the worker's command (for tests)."""

    def __init__(self, name: str = "kronos-small", python=KRONOS_PYTHON, source=KRONOS_SOURCE, model=KRONOS_MODEL,
                 tokenizer=KRONOS_TOKENIZER, paths: int = 30, context: int = 400, worker_argv: Sequence[str] | None = None):
        self.name, self.paths, self.context = name, int(paths), int(context)
        self.argv = list(worker_argv) if worker_argv else [str(python), str(WORKER), str(source), str(model), str(tokenizer)]
        self.device: str | None = None
        self._proc: subprocess.Popen | None = None
        self._log = None
        atexit.register(self.close)

    def _start(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            return
        self.close()
        self._log = tempfile.TemporaryFile(mode="w+")
        env = {**os.environ, "HF_HUB_OFFLINE": "1", "PYTHONUNBUFFERED": "1"}
        self._proc = subprocess.Popen(self.argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._log, text=True,
                                      bufsize=1, cwd=tempfile.gettempdir(), env=env)
        ready = self._read(lambda msg: msg.get("ready") is True)
        self.device = ready.get("device")

    def _log_tail(self) -> str:
        try:
            self._log.seek(0)
            return self._log.read()[-600:].strip()
        except (OSError, ValueError, AttributeError):
            return ""

    def _read(self, wanted) -> dict:
        """The next JSON line from the worker that `wanted` accepts. Other lines are library chatter.
        No timeout: a forecast waits as long as the worker is alive; a dead worker raises."""
        while True:
            line = self._proc.stdout.readline()
            if not line:
                self._proc.wait()
                raise _WorkerDied(f"Kronos worker exited with code {self._proc.returncode}: {self._log_tail()}")
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if isinstance(msg, dict) and wanted(msg):
                return msg

    def _request(self, payload: dict) -> dict:
        line = json.dumps(payload, sort_keys=True)
        for attempt in (1, 2):                              # a worker that dies is restarted once
            try:
                self._start()
                self._proc.stdin.write(line + "\n")
                self._proc.stdin.flush()
                return self._read(lambda msg: msg.get("id") == payload["id"])
            except (_WorkerDied, BrokenPipeError, OSError) as exc:
                self.close()
                if attempt == 2:
                    raise RuntimeError(f"the Kronos worker died twice on one request: {exc}") from exc
        raise AssertionError("unreachable")

    def request_for(self, window: pd.DataFrame, future_timestamps, seed: int) -> dict:
        """The request the worker is sent: the window's bars, the future dates and the seed, and nothing else."""
        days = [str(pd.Timestamp(t).date()) for t in window.index]
        body = {"timestamps": days, **{f: [float(v) for v in window[f].to_numpy()] for f in FIELDS},
                "future_timestamps": [str(pd.Timestamp(t).date()) for t in future_timestamps], "paths": self.paths,
                "seed": int(seed)}
        body["id"] = hashlib.sha1(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]
        return body

    def probability_up(self, window: pd.DataFrame, future_timestamps, seed: int) -> float:
        """Share of the sampled paths whose final close is above the window's last close; a path that ends
        exactly level counts one half."""
        if len(future_timestamps) == 0 or min(pd.Timestamp(t) for t in future_timestamps) <= window.index[-1]:
            raise ValueError("the future dates must all come after the window's last bar")
        reply = self._request(self.request_for(window, future_timestamps, seed))
        if reply.get("error"):
            raise RuntimeError(f"Kronos worker failed: {reply['error']}")
        finals = np.asarray(reply.get("final_closes") or [], dtype=float)
        if len(finals) != self.paths or not np.all(np.isfinite(finals)):
            raise RuntimeError(f"Kronos worker gave {len(finals)} usable paths, wanted {self.paths}")
        last = float(window["close"].iloc[-1])
        return float((np.sum(finals > last) + 0.5 * np.sum(finals == last)) / len(finals))

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is not None:
            try:
                proc.stdin.close()
            except OSError:
                pass
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            proc.stdout.close()
        log, self._log = self._log, None
        if log is not None:
            log.close()


class _WorkerDied(RuntimeError):
    pass


def window_for(ohlc: dict[str, pd.DataFrame], symbol: str, asked_on, context: int) -> pd.DataFrame | None:
    """The last `context` daily bars of `symbol` dated on or before `asked_on`, all five fields, rows with any
    missing value dropped. None when fewer than 200 rows remain or the symbol has no bar on `asked_on`."""
    day = pd.Timestamp(asked_on)
    if symbol not in ohlc["close"].columns:
        return None
    bars = pd.DataFrame({f: ohlc[f][symbol] for f in FIELDS}).loc[:day].dropna()
    if bars.empty or bars.index[-1] != day:
        return None
    bars = bars.iloc[-int(context):]
    return bars if len(bars) >= MIN_WINDOW else None


def future_days(ohlc: dict[str, pd.DataFrame], symbol: str, asked_on, horizon: int) -> pd.DatetimeIndex:
    """The next `horizon` dates `symbol` trades after `asked_on`: from the data's own dates while it has them
    (which dates had a bar, never what the bar was), then business days beyond them: weekdays for a
    security, every calendar day for a coin (a symbol ending "-USD")."""
    day, horizon = pd.Timestamp(asked_on), int(horizon)
    col = ohlc["close"][symbol] if symbol in ohlc["close"].columns else pd.Series(dtype=float)
    known = col.index[col.notna().to_numpy() & (col.index > day)][:horizon]
    if len(known) == horizon:
        return pd.DatetimeIndex(known)
    after = (known[-1] if len(known) else day) + pd.Timedelta(days=1)
    more = (pd.date_range if _is_coin(symbol) else pd.bdate_range)(after, periods=horizon - len(known))
    return pd.DatetimeIndex(list(known) + list(more))


def dense_questions(close: pd.DataFrame, symbols: Sequence[str], start, end, horizon: int, market: str,
                    per_day: int | None = None, seed: int = 0) -> list[tm.Question]:
    """Blind-history direction questions for every symbol on every `horizon`-th session from `start` to `end`,
    so one symbol's windows never overlap. With `per_day`, at most that many symbols a day, chosen at random
    from those with a price that day (seeded, so the same call gives the same questions)."""
    sessions = close.index[(close.index >= pd.Timestamp(start)) & (close.index <= pd.Timestamp(end))][::int(horizon)]
    rng = np.random.default_rng(seed)
    out: list[tm.Question] = []
    for day in sessions:
        todays = [s for s in symbols if s in close.columns and math.isfinite(float(close.at[day, s]))]
        if per_day is not None and len(todays) > per_day:
            todays = [todays[i] for i in sorted(rng.choice(len(todays), size=int(per_day), replace=False))]
        out += tm.direction_questions(close, todays, day, horizon, market, blind=True)
    return out


def _seed(question_id: str) -> int:
    return int(hashlib.sha1(question_id.encode()).hexdigest()[:8], 16)


def ask_price_model(ledger: tm.TournamentLedger, forecaster: KronosForecaster, ohlc: dict[str, pd.DataFrame], not_before,
                    markets: Sequence[str] | None = None, horizon: int | None = None, limit: int | None = None,
                    progress=None) -> dict[str, int]:
    """Put every question this forecaster has not answered, dated on or after `not_before` (and in `markets`,
    of `horizon`, when given), to the price model. A question with no price window, or one the worker fails
    on, is recorded as an error and the run goes on. A question dated before `not_before` is never asked."""
    if not_before is None:
        raise ValueError("not_before is required: the first day the model cannot have trained on")
    cutoff = pd.Timestamp(not_before)
    have = ledger.answered(forecaster.name)
    open_ = [q for q in ledger.questions() if q["id"] not in have and (markets is None or q["market"] in markets)
             and (horizon is None or int(q["horizon"]) == int(horizon))]
    todo = [q for q in open_ if pd.Timestamp(q["asked_on"]) >= cutoff]
    skipped = len(open_) - len(todo)
    todo = todo[:limit] if limit else todo
    answered = 0
    for n, q in enumerate(todo, start=1):
        started = time.perf_counter()
        window = window_for(ohlc, q["symbol"], q["asked_on"], forecaster.context)
        p, error = None, None
        if window is None:
            error = f"no price window of {MIN_WINDOW}+ full bars ending on the question's day"
        else:
            try:
                p = forecaster.probability_up(window, future_days(ohlc, q["symbol"], q["asked_on"], q["horizon"]), _seed(q["id"]))
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"[:500]
        ledger.record(q["id"], forecaster.name, p, error, (time.perf_counter() - started) * 1000.0)
        answered += int(error is None)
        if progress and (n % 10 == 0 or n == len(todo)):
            progress(f"{forecaster.name}: {n}/{len(todo)} asked, {answered} answered")
    return {"asked": len(todo), "answered": answered, "skipped_before_cutoff": skipped}


def ohlc_universe(name: str) -> dict:
    """Where a universe's bars live: funds (the research funds from 2012), or stocks and crypto (universes.json)."""
    from valedia_trader.data_and_fees import prices
    if name == "funds":
        return {"folder": "artifacts/ohlc_funds", "symbols": list(prices.UNIVERSE), "start": prices.START}
    u = tm.universe(name)
    return {"folder": f"artifacts/ohlc_{name}", "symbols": list(u["symbols"]), "start": u["start"]}


def _on_sessions(ohlc: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    """The bars on the market's own sessions: any day a security traded, or every day for a universe of coins only.
    Coins in a universe of funds are then sampled on the funds' sessions, as the tournament's price folders are."""
    close = ohlc["close"]
    listed = [c for c in close.columns if not _is_coin(c)]
    days = close[listed].dropna(how="all").index if listed else close.dropna(how="all").index
    return {f: v.loc[days] for f, v in ohlc.items()}


class _Only:
    """A view of the ledger that shows only some questions, so other forecasters answer exactly those."""

    def __init__(self, ledger: tm.TournamentLedger, ids: set[str]):
        self._ledger, self._ids = ledger, ids

    def questions(self, unsettled_only: bool = False) -> list[dict]:
        return [q for q in self._ledger.questions(unsettled_only) if q["id"] in self._ids]

    def answered(self, forecaster: str) -> set[str]:
        return self._ledger.answered(forecaster)

    def record(self, *args) -> None:
        self._ledger.record(*args)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="price_models", description="Ask price-forecast models the tournament's direction questions.")
    ap.add_argument("--root", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("refresh-ohlc").add_argument("--universe", required=True, help="funds, stocks or crypto")
    p = sub.add_parser("ask-kronos")
    p.add_argument("--universe", required=True, help="funds, stocks or crypto (the market label too)")
    p.add_argument("--horizon", type=int, required=True)
    p.add_argument("--not-before", required=True, help="the first question day that can count (YYYY-MM-DD); no default")
    p.add_argument("--end", default=None, help="the last question day (default: the last day whose horizon has passed)")
    p.add_argument("--per-day", type=int, default=None, help="at most this many symbols a day")
    p.add_argument("--seed", type=int, default=0, help="which symbols --per-day picks")
    p.add_argument("--paths", type=int, default=30)
    p.add_argument("--limit", type=int, default=None, help="at most this many questions in this run")
    p.add_argument("--ledger", default=None, help="the tournament file (default: memory/tournament.sqlite)")
    p.add_argument("--with-rules", action="store_true", help="also ask the two rule forecasters on the same questions")
    args = ap.parse_args(argv)
    paths = LoopPaths(Path(args.root)) if args.root else LoopPaths()
    spec = ohlc_universe(args.universe)
    folder = paths.root / spec["folder"]
    if args.cmd == "refresh-ohlc":
        print(json.dumps(ohlc_data.refresh_ohlc(folder, spec["symbols"], spec["start"]), indent=1))
        return 0
    bars = _on_sessions(ohlc_data.load_ohlc(folder))
    close = bars["close"]
    end = pd.Timestamp(args.end) if args.end else close.index[-1 - args.horizon]
    ledger = tm.TournamentLedger(args.ledger or paths.memory / "tournament.sqlite")
    forecaster = KronosForecaster(paths=args.paths)
    log = lambda s: print(s, file=sys.stderr, flush=True)
    try:
        qs = dense_questions(close, list(close.columns), args.not_before, end, args.horizon, args.universe, args.per_day, args.seed)
        added, settled = ledger.add(qs), tm.settle_directions(ledger, close)
        before = ledger.answered(forecaster.name)
        started = time.perf_counter()
        asked = ask_price_model(ledger, forecaster, bars, args.not_before, markets=[args.universe], horizon=args.horizon,
                                limit=args.limit, progress=log)
        seconds = time.perf_counter() - started
        out = {"questions_added": added, "settled_now": settled, "asked": {forecaster.name: asked}, "device": forecaster.device,
               "paths": args.paths, "seconds_per_question": round(seconds / asked["asked"], 3) if asked["asked"] else None}
        if args.with_rules:
            ids = ledger.answered(forecaster.name) - before          # the questions Kronos answered in this run
            out["asked"].update(tm.ask(_Only(ledger, ids), tm.reference_forecasters(), progress=log))
        out["counts"] = ledger.counts()
        print(json.dumps(out, indent=1))
        return 0
    finally:
        forecaster.close()
        ledger.close()


if __name__ == "__main__":
    raise SystemExit(main())
