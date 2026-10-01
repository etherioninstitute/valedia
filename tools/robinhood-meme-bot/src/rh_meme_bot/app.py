"""Application wiring: watch pools -> gather facts -> decide -> gate -> paper execute."""
from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, List, Optional

from .chain import (ChainMismatchError, ChainReader, PoolCreatedEvent, PoolWatcher,
                    price_from_sqrt)
from .config import Config, ConfigError, load_config
from .dashboard import Dashboard, PlainSink
from .decision import Engine, apply_gates, load_engine, run_engine
from .executor import Executor, PaperExecutor
from .risk import compute_risk
from .rpc import RpcClient, RpcError


def _float(d: Optional[Decimal]) -> Optional[float]:
    if d is None:
        return None
    try:
        f = float(d)
    except (OverflowError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


class Bot:
    def __init__(self, cfg: Config, reader: ChainReader, executor: Executor, engine: Engine,
                 sink, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.cfg, self.reader, self.executor = cfg, reader, executor
        self.engine, self.sink, self._clock = engine, sink, clock
        self.watcher = PoolWatcher(reader, cfg.lookback_blocks, cfg.max_block_range,
                                   on_error=lambda m: sink.action(f"RPC error: {m}"))

    # --- fact gathering -------------------------------------------------
    def build_state(self, ev: PoolCreatedEvent) -> tuple:
        """Return (state, entry_price Decimal|None). Partial facts on RPC trouble."""
        quotes = set(self.cfg.quote_tokens)
        t0_is_quote = ev.token0 in quotes and ev.token1 not in quotes
        target = ev.token1 if t0_is_quote else ev.token0
        quote = ev.token0 if t0_is_quote else ev.token1
        info = self.reader.token_info(target)
        qinfo = self.reader.token_info(quote)
        pool, liq = ev.pool, None
        slot0 = price = None
        try:
            liq = self.reader.pool_liquidity(pool)
            slot0 = self.reader.pool_slot0(pool)
        except RpcError as e:
            self.sink.action(f"pool read failed {pool}: {e}")
        d_t, d_q = info.decimals, qinfo.decimals
        if slot0 and d_t is not None and d_q is not None:
            d0, d1 = (d_q, d_t) if t0_is_quote else (d_t, d_q)
            price = price_from_sqrt(slot0.sqrt_price_x96, d0, d1)
        # entry price = quote units per 1 target token
        entry = None if price is None else (price[0] if not t0_is_quote else price[1])
        state = {
            "pool": pool, "token0": ev.token0, "token1": ev.token1, "fee": ev.fee,
            "tick_spacing": ev.tick_spacing, "block_number": ev.block_number,
            "tx_hash": ev.tx_hash, "detected_at": self._clock().isoformat(timespec="seconds"),
            "target_token": target, "quote_token": quote, "target_is_token0": not t0_is_quote,
            "symbol": info.symbol, "decimals": info.decimals, "total_supply": info.total_supply,
            "has_bytecode": info.has_bytecode, "owner_status": info.owner_status,
            "liquidity": liq, "tick": slot0.tick if slot0 else None,
            "price_token1_per_token0": _float(price[0]) if price else None,
            "price_token0_per_token1": _float(price[1]) if price else None,
            "target_price_in_quote": _float(entry),
        }
        risk = compute_risk(state, self.cfg.min_liquidity)
        state["risk_score"], state["risk_reasons"] = risk.score, risk.reasons
        return state, entry

    # --- one pool -------------------------------------------------------
    def process_event(self, ev: PoolCreatedEvent) -> dict:
        state, entry = self.build_state(ev)
        self.sink.launch({
            "time": self._clock().strftime("%H:%M:%S"), "pool": ev.pool, "token0": ev.token0,
            "token1": ev.token1, "symbol": state["symbol"], "fee": ev.fee,
            "price": state["price_token1_per_token0"], "liquidity": state["liquidity"],
            "risk": state["risk_score"]})
        decision, problem = run_engine(self.engine, state)
        final, reason = apply_gates(
            decision, state, min_confidence=self.cfg.min_confidence,
            max_risk_score=self.cfg.max_risk_score, min_liquidity=self.cfg.min_liquidity)
        if problem:
            reason = problem
        position = None
        if final == "buy":
            if entry is None or entry <= 0:
                final, reason = "skip", "gated: no usable entry price"
            else:
                try:
                    position = self.executor.buy(ev.pool, state["target_token"],
                                                 self.cfg.paper_size, entry)
                except Exception as e:  # noqa: BLE001 - executor failure must not kill the loop
                    final, reason = "skip", f"executor error: {type(e).__name__}: {e}"
        self.sink.decision({"pool": ev.pool, "action": decision["action"],
                            "confidence": decision["confidence"], "final_action": final,
                            "reason": reason})
        if position:
            self.sink.action(f"PAPER BUY #{position.id} {state['symbol'] or position.token} "
                             f"size={position.size} @ {position.entry_price} "
                             f"(simulated, pool {ev.pool})")
        else:
            self.sink.action(f"{final.upper()} pool {ev.pool}" + (f" - {reason}" if reason else ""))
        return {"state": state, "decision": decision, "final_action": final,
                "reason": reason, "position": position}

    # --- loop -----------------------------------------------------------
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


def build_bot(cfg: Config, sink, transport=None) -> Bot:
    reader = ChainReader(RpcClient(cfg.rpc_url, transport=transport), cfg.factory_address,
                         cfg.chain_id)
    return Bot(cfg, reader, PaperExecutor(cfg.positions_file), load_engine(cfg.decision_engine),
               sink)


def main(argv: Optional[List[str]] = None, transport=None) -> int:
    ap = argparse.ArgumentParser(prog="rh-meme-bot", description=(
        "Robinhood Chain memecoin monitor. PAPER TRADING ONLY: no keys, no real swaps."))
    ap.add_argument("--headless", action="store_true", help="plain lines instead of the TUI")
    ap.add_argument("--once", action="store_true", help="run a single poll iteration and exit")
    ap.add_argument("--env-file", default=".env", help="path to .env (default: ./.env)")
    args = ap.parse_args(argv)
    try:
        cfg = load_config(env_file=args.env_file)
        sink = PlainSink() if args.headless else Dashboard()
        bot = build_bot(cfg, sink, transport)
        bot.reader.verify_chain()
    except (ConfigError, ValueError, ChainMismatchError, RpcError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    try:
        if isinstance(sink, Dashboard):
            with sink:
                bot.run_once() if args.once else bot.run_forever()
            if args.once:
                sink.console.print(sink.render())
        elif args.once:
            bot.run_once()
        else:
            bot.run_forever()
    except KeyboardInterrupt:
        print("stopped", file=sys.stderr)
    return 0
