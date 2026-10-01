"""Wiring: new pool -> state -> decide -> hard gates -> paper buy -> log EVERY decision."""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timezone
from typing import Callable, List, Optional

from .chain import ChainMismatchError, ChainReader, PoolCreatedEvent, PoolWatcher
from .config import Config, ConfigError, load_config
from .dashboard import Dashboard, PlainSink
from .decision import Engine, load_engine, run_engine, validate_decision
from .decision_engine import sanitize
from .decision_log import DecisionLog, read_records, review_main
from .executor import DuplicatePoolError, Executor, PaperExecutor
from .gates import evaluate_gates, first_failure
from .state import QuoteUsd, build_state, split_roles
from .stats import LatencyStats
from .rpc import RpcClient, RpcError


class Bot:
    def __init__(self, cfg: Config, reader: ChainReader, executor: Executor, engine: Engine,
                 sink, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 decision_log: Optional[DecisionLog] = None,
                 perf: Callable[[], float] = time.perf_counter, redecide: bool = False):
        self.cfg, self.reader, self.executor = cfg, reader, executor
        self.engine, self.sink, self._clock, self._perf = engine, sink, clock, perf
        self.log = decision_log
        self.redecide = redecide
        # Pools already decided (in an earlier run) are not decided again after a restart
        # inside the lookback window, so the log keeps one line per decision. Records that
        # ended in an engine error do not count: those are retried.
        self.seen_pools = set() if (redecide or decision_log is None) else {
            r["pool"] for r in read_records(decision_log.path)
            if r.get("pool") and not (r.get("decision") or {}).get("error")}
        self.stats = LatencyStats(cfg.decision_latency_budget_ms)
        self.quote_usd = QuoteUsd(reader, cfg.quote_tokens)
        self.watcher = PoolWatcher(reader, cfg.lookback_blocks, cfg.max_block_range,
                                   on_error=lambda m: sink.action(f"RPC error: {m}"))

    @property
    def model_name(self) -> str:
        return getattr(self.engine, "model", None) or getattr(self.engine, "__name__", "plugin")

    def _emit_engine(self) -> None:
        self.sink.engine(self.stats.snapshot(self.model_name))

    def start(self) -> None:
        """Warm the model once (resident via keep_alive) and report its latency."""
        warm = getattr(self.engine, "warmup", None)
        if warm is not None:
            res = warm()
            self.stats.warmup_ms, self.stats.warm = res.get("latency_ms"), not res.get("error")
            self.stats.last_error = res.get("error")
            self.sink.action(f"warmup {res.get('latency_ms')}ms "
                             + ("ok" if self.stats.warm else f"FAILED: {res.get('error')}"))
        self._emit_engine()

    # --- one pool ---------------------------------------------------------
    def process_event(self, ev: PoolCreatedEvent) -> dict:
        ts = self._clock().isoformat(timespec="seconds")
        meme, _ = split_roles(ev, self.cfg.quote_tokens)
        entry, build_err = None, None
        try:
            state, entry = build_state(self.reader, self.cfg, ev, self.quote_usd)
        except Exception as e:  # noqa: BLE001 - unreadable pool is logged as a skip
            state = {"token_address": meme, "pool_address": ev.pool, "symbol": None,
                     "fee_tier": ev.fee, "liquidity_usd": None}
            build_err = f"state_error: {type(e).__name__}: {e}"
        self.sink.launch({"time": self._clock().strftime("%H:%M:%S"), "pool": ev.pool,
                          "token": meme, "symbol": state.get("symbol"), "fee": ev.fee,
                          "price": state.get("price_in_quote"),
                          "liquidity_usd": state.get("liquidity_usd"),
                          "owner_status": state.get("owner_status"),
                          "code": state.get("contract_has_code")})
        if build_err:
            decision, _ = validate_decision(None)
            decision["error"], latency, within = build_err, None, False
        else:
            t0 = self._perf()
            decision, problem = run_engine(self.engine, state)
            wall = (self._perf() - t0) * 1000.0
            if problem:
                decision["error"] = decision["error"] or problem
            latency = decision["latency_ms"] if decision["latency_ms"] is not None else wall
            within = self.stats.record(latency)
            self.stats.warm, self.stats.last_error = not decision["error"], decision["error"]
        final, gates = evaluate_gates(decision, state, self.cfg)
        position = None
        has_pos = getattr(self.executor, "has_position", lambda pool: False)
        if final == "buy" and has_pos(ev.pool):
            final = "skip"
            gates.append({"gate": "duplicate_pool", "value": ev.pool,
                          "threshold": "no open position", "passed": False})
        if final == "buy":
            try:
                position = self.executor.buy(ev.pool, state["token_address"],
                                             self.cfg.paper_size, entry)
            except Exception as e:  # noqa: BLE001 - executor failure must not kill the loop
                final = "skip"
                gate = "duplicate_pool" if isinstance(e, DuplicatePoolError) else "executor"
                gates.append({"gate": gate, "value": f"{type(e).__name__}: {e}",
                              "threshold": "no error", "passed": False})
        self.seen_pools.add(ev.pool)
        self._log(ts, ev, state, decision, latency, within, gates, final, position)
        self.sink.decision({
            "pool": ev.pool, "action": decision["action"], "confidence": decision["confidence"],
            "risk_score": decision["risk_score"], "latency_ms": latency, "within_budget": within,
            "final_action": final, "first_fail": first_failure(gates),
            "error": decision["error"]})
        if position:
            self.sink.action(f"PAPER BUY #{position.id} {state.get('symbol') or position.token} "
                             f"size={position.size} @ {position.entry_price} "
                             f"(simulated, pool {ev.pool})")
        else:
            self.sink.action(f"{final.upper()} pool {ev.pool} - {first_failure(gates)}")
        self._emit_engine()
        return {"state": state, "decision": decision, "final_action": final, "gates": gates,
                "position": position, "latency_ms": latency, "within_budget": within}

    def _log(self, ts, ev, state, decision, latency, within, gates, final, position) -> None:
        if self.log is None:
            return
        rec = {"ts": ts, "pool": ev.pool, "block_number": ev.block_number,
               "tx_hash": ev.tx_hash, "state": state, "raw": decision["raw"],
               "decision": {k: decision[k] for k in
                            ("action", "confidence", "risk_score", "risk_expected",
                             "model", "error")},
               "latency_ms": latency, "within_budget": within, "gates": gates,
               "final_action": final, "position_id": position.id if position else None}
        try:
            self.log.append(sanitize(rec))
        except Exception as e:  # noqa: BLE001 - disk trouble must be visible, not fatal
            self.sink.action(f"DECISION LOG WRITE FAILED: {type(e).__name__}: {e}")

    # --- loop ---------------------------------------------------------------
    def run_once(self) -> List[dict]:
        """One poll iteration. RPC failures are reported, never raised."""
        outcomes: List[dict] = []
        try:
            events = self.watcher.poll()
        except RpcError as e:
            self.sink.action(f"RPC error: {e}")
            return outcomes
        self.sink.status(f"head={self.watcher.last_head} scanned_to={self.watcher.last_block} "
                         f"new_pools={len(events)}")
        for ev in events:
            if ev.pool in self.seen_pools:
                self.sink.action(f"already decided, skipping pool {ev.pool} (use --redecide)")
                continue
            try:
                outcomes.append(self.process_event(ev))
            except Exception as e:  # noqa: BLE001 - one bad pool must not stop the loop
                self.sink.action(f"error processing pool {ev.pool}: {type(e).__name__}: {e}")
        return outcomes

    def run_forever(self, sleep: Callable[[float], None] = time.sleep,
                    should_stop: Callable[[], bool] = lambda: False) -> None:
        while not should_stop():
            self.run_once()
            sleep(self.cfg.poll_interval_seconds)


def build_bot(cfg: Config, sink, transport=None, nimble_transport=None,
              redecide: bool = False) -> Bot:
    reader = ChainReader(RpcClient(cfg.rpc_url, transport=transport), cfg.factory_address,
                         cfg.chain_id)
    engine = load_engine(cfg.decision_engine, cfg, nimble_transport)
    problem = getattr(engine, "config_problem", lambda: None)()
    if problem:
        raise ValueError(problem)
    return Bot(cfg, reader, PaperExecutor(cfg.positions_file), engine, sink,
               decision_log=DecisionLog(cfg.decisions_log), redecide=redecide)


def _review(argv: List[str]) -> int:
    try:
        path = load_config().decisions_log
    except ConfigError:
        path = os.environ.get("DECISIONS_LOG", "decisions.jsonl")
    return review_main(argv, path)


def main(argv: Optional[List[str]] = None, transport=None, nimble_transport=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] == "review":
        return _review(argv[1:])
    ap = argparse.ArgumentParser(prog="rh-meme-bot", description=(
        "Robinhood Chain memecoin monitor. PAPER TRADING ONLY: no keys, no real swaps. "
        "Subcommand: 'review' reads the decision log."))
    ap.add_argument("--headless", action="store_true", help="plain lines instead of the TUI")
    ap.add_argument("--once", action="store_true", help="run a single poll iteration and exit")
    ap.add_argument("--redecide", action="store_true",
                    help="decide again on pools already in the decision log")
    ap.add_argument("--env-file", default=".env", help="path to .env (default: ./.env)")
    args = ap.parse_args(argv)
    try:
        cfg = load_config(env_file=args.env_file)
        sink = PlainSink() if args.headless else Dashboard()
        bot = build_bot(cfg, sink, transport, nimble_transport, args.redecide)
        bot.reader.verify_chain()
    except (ConfigError, ValueError, ChainMismatchError, RpcError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    try:
        if isinstance(sink, Dashboard):
            with sink:  # Live prints its final frame on exit, so --once renders exactly once
                bot.start()
                bot.run_once() if args.once else bot.run_forever()
        else:
            bot.start()
            bot.run_once() if args.once else bot.run_forever()
    except KeyboardInterrupt:
        print("stopped", file=sys.stderr)
    return 0
