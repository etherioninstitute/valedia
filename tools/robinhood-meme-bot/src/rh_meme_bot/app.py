"""Wiring: new pool -> state -> decide -> hard gates -> paper buy -> log EVERY decision."""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Callable, List, Optional

from .chain import ChainReader, PoolCreatedEvent, PoolWatcher
from .cli import build_bot, main  # noqa: F401 - entry points stay importable from app
from .config import Config
from .decision import Engine, run_engine, validate_decision
from .decision_engine import sanitize
from .backoff import ENGINE_COOLDOWN, EngineBreaker
from .decision_log import DecisionLog, read_records
from .executor import DuplicatePoolError, Executor
from .gates import evaluate_gates, first_failure
from .profiles import profile_for
from .quotes import QuoteUsd
from .recheck import RecheckBook, fingerprint
from .rpc import RpcError
from .state import build_state, fallback_state, split_roles
from .stats import LatencyStats

log = logging.getLogger(__name__)


class Bot:
    def __init__(self, cfg: Config, reader: ChainReader, executor: Executor, engine: Engine,
                 sink, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 decision_log: Optional[DecisionLog] = None,
                 perf: Callable[[], float] = time.perf_counter, redecide: bool = False,
                 v4=None, mono: Callable[[], float] = time.monotonic):
        self.cfg, self.reader, self.executor = cfg, reader, executor
        self.engine, self.sink, self._clock, self._perf = engine, sink, clock, perf
        self.log = decision_log
        self.redecide = redecide
        # Pools already decided (in an earlier run) are not decided again after a restart
        # inside the lookback window, so the log keeps one line per decision. Records that
        # ended in an engine error do not count: those are retried.
        self.seen_pools = set() if (redecide or decision_log is None) \
            else self._load_seen(decision_log.path)
        self.stats = LatencyStats(cfg.decision_latency_budget_ms)
        self.quote_usd = QuoteUsd(reader, cfg.quote_tokens, profile=profile_for(cfg.chain_id))
        self.rechecks = RecheckBook(cfg.recheck_blocks)
        self.breaker = EngineBreaker(cfg.engine_breaker_errors, cfg.engine_cooldown_s, mono)
        self._cooldown_logged: set = set()  # pools already logged engine_cooldown this episode
        self._head: Optional[int] = None  # newest chain head seen by run_once (block clock)
        self.v4 = v4
        self._v4_ready: Optional[bool] = None  # None = not verified yet; False = disabled
        self._errs: dict = {}
        self.health = {"v3": "starting",
                       "v4": "starting" if v4 is not None else "disabled: not configured"}
        self.watchers = {"v3": self._watcher("v3", None)}
        if v4 is not None:
            self.watchers["v4"] = self._watcher("v4", v4.fetch_initialize)

    def _watcher(self, label: str, fetch) -> PoolWatcher:
        def on_error(msg: str) -> None:
            self._errs[label] = msg
            self.sink.action(f"RPC error: {msg}")
        return PoolWatcher(self.reader, self.cfg.lookback_blocks, self.cfg.max_block_range,
                           on_error=on_error, fetch=fetch, label=label)

    @property
    def watcher(self) -> PoolWatcher:
        return self.watchers["v3"]

    def _load_seen(self, path: str) -> set:
        """Pools with a completed (non-error) decision. Corrupt records are skipped, not fatal."""
        seen, bad = set(), 0
        for r in read_records(path):
            dec, pool = r.get("decision"), r.get("pool")
            if not isinstance(pool, str) or not pool or not isinstance(dec, dict):
                bad += 1
                continue
            if not dec.get("error"):
                seen.add(pool.lower())
        if bad:
            self.sink.action(f"WARNING: skipped {bad} unreadable record(s) in {path}")
        return seen

    @property
    def model_name(self) -> str:
        return getattr(self.engine, "model", None) or getattr(self.engine, "__name__", "plugin")

    def _emit_engine(self) -> None:
        snap = self.stats.snapshot(self.model_name)
        snap["watchers"] = f"v3 {self.health['v3']} | v4 {self.health['v4']}"
        self.sink.engine(snap)

    def verify_v4(self) -> None:
        """Check V4_POOL_MANAGER / V4_STATE_VIEW on-chain; failure disables v4 only."""
        if self.v4 is None or self._v4_ready is not None:
            return
        try:
            problem = self.v4.verify()
        except RpcError as e:  # transient: stay unverified and retry next poll
            self.health["v4"] = f"unverified (rpc error, retrying): {e}"
            return
        if problem:
            self._v4_ready = False
            self.health["v4"] = f"DISABLED: {problem}"
            self.watchers.pop("v4", None)
            msg = f"WARNING: v4 watcher disabled: {problem} (v3 keeps running)"
            log.warning(msg)
            self.sink.action(msg)
        else:
            self._v4_ready, self.health["v4"] = True, "ok"
            self.quote_usd.v4 = self.v4  # verified: usable for the ETH/USDG price fallback

    def start(self) -> None:
        """Verify v4 addresses, warm the model once (resident via keep_alive), report."""
        self.verify_v4()
        warm = getattr(self.engine, "warmup", None)
        if warm is not None:
            res = warm()
            self.stats.warmup_ms, self.stats.warm = res.get("latency_ms"), not res.get("error")
            self.stats.last_error = res.get("error")
            self.sink.action(f"warmup {res.get('latency_ms')}ms "
                             + ("ok" if self.stats.warm else f"FAILED: {res.get('error')}"))
        self._emit_engine()

    # --- one pool ---------------------------------------------------------
    def _facts(self, ev: PoolCreatedEvent):
        meme, _ = split_roles(ev, self.cfg.quote_tokens)
        try:
            state, entry = build_state(self.reader, self.cfg, ev, self.quote_usd,
                                       self.v4 if self._v4_ready else None)
            return state, entry, None
        except Exception as e:  # noqa: BLE001 - unreadable pool is logged as a skip
            return fallback_state(ev, meme), None, f"state_error: {type(e).__name__}: {e}"

    def process_event(self, ev: PoolCreatedEvent) -> dict:
        state, entry, build_err = self._facts(ev)
        self.sink.launch({"time": self._clock().strftime("%H:%M:%S"), "pool": ev.pool,
                          "token": state.get("token_address"), "symbol": state.get("symbol"),
                          "fee": ev.fee, "version": ev.version, "has_hooks": ev.has_hooks,
                          "dynamic_fee": ev.dynamic_fee, "price": state.get("price_in_quote"),
                          "liquidity_usd": state.get("liquidity_usd"),
                          "owner_status": state.get("owner_status"),
                          "code": state.get("contract_has_code")})
        return self._evaluate(ev, state, entry, build_err, 0)

    def _evaluate(self, ev, state, entry, build_err, recheck: int) -> dict:
        ts = self._clock().isoformat(timespec="seconds")
        cooldown = False
        if build_err:
            decision, _ = validate_decision(None)
            decision["error"], latency, within = build_err, None, False
        elif self.breaker.is_open():  # cheap skip: no engine call while the breaker is open
            decision, _ = validate_decision(None)
            decision["error"], latency, within, cooldown = ENGINE_COOLDOWN, None, False, True
            self.stats.warm, self.stats.last_error = False, ENGINE_COOLDOWN
            self._cooldown_logged.add(ev.pool.lower())
        else:
            t0 = self._perf()
            decision, problem = run_engine(self.engine, state)
            wall = (self._perf() - t0) * 1000.0
            if problem:
                decision["error"] = decision["error"] or problem
            cap_ms = self.cfg.decision_timeout_s * 1000.0
            if wall > cap_ms and not decision["error"]:  # same post-hoc cap for plugin engines
                decision.update(action="skip", confidence=0.0, risk_score=None,
                                error=f"timeout: engine took {wall:.0f}ms > cap {cap_ms:.0f}ms")
            latency = decision["latency_ms"] if decision["latency_ms"] is not None else wall
            within = self.stats.record(latency)
            self.stats.warm, self.stats.last_error = not decision["error"], decision["error"]
            if self.breaker.record(bool(decision["error"])):
                self.sink.action(
                    f"engine circuit open: {self.breaker.consecutive} consecutive errors; no "
                    f"engine calls for {self.cfg.engine_cooldown_s:g}s (pools log engine_cooldown)")
        final, gates = evaluate_gates(decision, state, self.cfg, ev.hooks)
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
        self.seen_pools.add(ev.pool.lower())
        self._log(ts, ev, state, decision, latency, within, gates, final, position, recheck)
        retry_at = None  # per-pool backoff after a real engine error (a cooldown skip is not one)
        if decision["error"] and not build_err and not cooldown and self.cfg.engine_retry_blocks:
            retry_at = (ev.block_number if self._head is None else self._head) \
                + self.cfg.engine_retry_blocks
        watched = not build_err and self.rechecks.consider(
            ev, state, gates, final, recheck, engine_error=bool(decision["error"]),
            retry_at=retry_at)
        self.sink.decision({
            "pool": ev.pool, "action": decision["action"], "confidence": decision["confidence"],
            "risk_score": decision["risk_score"], "latency_ms": latency, "within_budget": within,
            "final_action": final, "first_fail": first_failure(gates),
            "error": decision["error"], "version": ev.version})
        tag = f" (recheck #{recheck})" if recheck else ""
        if position:
            self.sink.action(f"PAPER BUY #{position.id} {state.get('symbol') or position.token} "
                             f"size={position.size} @ {position.entry_price} "
                             f"(simulated, {ev.version} pool {ev.pool}){tag}")
        else:
            more = (f"; re-checking liquidity until block {ev.block_number + self.rechecks.blocks}"
                    if watched else "")
            self.sink.action(f"{final.upper()} pool {ev.pool} - {first_failure(gates)}{tag}{more}")
        self._emit_engine()
        return {"state": state, "decision": decision, "final_action": final, "gates": gates,
                "position": position, "latency_ms": latency, "within_budget": within,
                "recheck": recheck}

    def _log(self, ts, ev, state, decision, latency, within, gates, final, position,
             recheck: int = 0) -> None:
        if self.log is None:
            return
        rec = {"ts": ts, "pool": ev.pool, "block_number": ev.block_number,
               "tx_hash": ev.tx_hash, "version": ev.version, "recheck": recheck,
               "pool_record": {"version": ev.version, "pool_address": ev.pool_address,
                               "pool_id": ev.pool_id, "hooks": ev.hooks, "fee": ev.fee,
                               "tick_spacing": ev.tick_spacing},
               "state": state, "raw": decision["raw"],
               "decision": {k: decision[k] for k in
                            ("action", "confidence", "risk_score", "risk_expected",
                             "model", "error")},
               "latency_ms": latency, "within_budget": within, "gates": gates,
               "final_action": final, "position_id": position.id if position else None}
        try:
            self.log.append(sanitize(rec))
        except Exception as e:  # noqa: BLE001 - disk trouble must be visible, not fatal
            self.sink.action(f"DECISION LOG WRITE FAILED: {type(e).__name__}: {e}")

    # --- re-check window ------------------------------------------------------
    def run_rechecks(self, head: Optional[int], fresh: frozenset = frozenset()) -> List[dict]:
        """Re-evaluate watched pools (skipping ``fresh`` ones decided in this same poll)."""
        outcomes: List[dict] = []
        if head is None:
            return outcomes
        for p in self.rechecks.expire(head):
            self.sink.action(f"recheck window over for pool {p.ev.pool} (block {p.until_block})")
        for p in self.rechecks.due(head):  # pools backing off after an engine error wait
            if p.ev.pool.lower() in fresh:
                continue
            if self.breaker.is_open() and p.ev.pool.lower() in self._cooldown_logged:
                continue  # already logged engine_cooldown this episode: stay silent
            state, entry, err = self._facts(p.ev)
            if err or fingerprint(state) == p.facts:
                continue  # unreadable now, or nothing changed: no model call, no log line
            outcomes.append(self._evaluate(p.ev, state, entry, None, p.checks + 1))
        return outcomes

    # --- loop ---------------------------------------------------------------
    def run_once(self) -> List[dict]:
        """One poll iteration. RPC failures are reported, never raised."""
        outcomes: List[dict] = []
        self.verify_v4()
        if not self.breaker.is_open():
            self._cooldown_logged.clear()  # a new episode logs each pool once again
        events: List[PoolCreatedEvent] = []
        for label, w in list(self.watchers.items()):
            if label == "v4" and not self._v4_ready:
                continue  # addresses not verified yet (transient RPC trouble): retry next poll
            self._errs[label] = None
            try:
                events.extend(w.poll())
            except RpcError as e:
                self.health[label] = f"error: {e}"
                self.sink.action(f"RPC error ({label}): {e}")
                continue
            self.health[label] = "ok" if self._errs[label] is None else "lagging (getLogs error)"
        heads = [w.last_head for w in self.watchers.values() if w.last_head is not None]
        if not heads:
            self._emit_engine()
            return outcomes
        self._head = max(heads)
        scanned = " ".join(f"{k}_scanned_to={w.last_block}" for k, w in self.watchers.items())
        self.sink.status(f"head={max(heads)} {scanned} new_pools={len(events)}")
        for ev in sorted(events, key=lambda e: (e.block_number, e.log_index)):
            if ev.pool.lower() in self.seen_pools:
                self.sink.action(f"already decided, skipping pool {ev.pool} (use --redecide)")
                continue
            try:
                outcomes.append(self.process_event(ev))
            except Exception as e:  # noqa: BLE001 - one bad pool must not stop the loop
                self.sink.action(f"error processing pool {ev.pool}: {type(e).__name__}: {e}")
        try:
            fresh = frozenset(o["state"].get("pool_id") or o["state"].get("pool_address") or ""
                              for o in outcomes)
            outcomes.extend(self.run_rechecks(max(heads), fresh))
        except Exception as e:  # noqa: BLE001
            self.sink.action(f"recheck error: {type(e).__name__}: {e}")
        self._emit_engine()
        return outcomes

    def run_forever(self, sleep: Callable[[float], None] = time.sleep,
                    should_stop: Callable[[], bool] = lambda: False) -> None:
        while not should_stop():
            self.run_once()
            sleep(self.cfg.poll_interval_seconds)
