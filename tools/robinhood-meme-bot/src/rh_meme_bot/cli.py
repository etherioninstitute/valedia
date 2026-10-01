"""Command line: build the bot from config and run it (dashboard or headless), or review."""
from __future__ import annotations

import argparse
import os
import sys
from typing import List, Optional

from .chain import ChainMismatchError, ChainReader
from .config import Config, ConfigError, load_config
from .dashboard import Dashboard, PlainSink
from .decision import load_engine
from .decision_log import DecisionLog, review_main
from .executor import PaperExecutor
from .rpc import RpcClient, RpcError
from .v4 import V4Reader


def build_bot(cfg: Config, sink, transport=None, nimble_transport=None,
              redecide: bool = False):
    from .app import Bot  # app re-exports this module's entry points

    rpc = RpcClient(cfg.rpc_url, transport=transport)
    reader = ChainReader(rpc, cfg.factory_address, cfg.chain_id)
    v4 = (V4Reader(rpc, cfg.v4_pool_manager, cfg.v4_state_view, cfg.max_block_range)
          if cfg.v4_pool_manager and cfg.v4_state_view else None)
    engine = load_engine(cfg.decision_engine, cfg, nimble_transport)
    problem = getattr(engine, "config_problem", lambda: None)()
    if problem:
        raise ValueError(problem)
    return Bot(cfg, reader, PaperExecutor(cfg.positions_file), engine, sink,
               decision_log=DecisionLog(cfg.decisions_log), redecide=redecide, v4=v4)


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
        "Robinhood Chain memecoin monitor (Uniswap v3 + v4). PAPER TRADING ONLY: no keys, "
        "no real swaps. Subcommand: 'review' reads the decision log."))
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
    except Exception as e:  # noqa: BLE001 - e.g. unreadable positions file: clear message, not a traceback
        print(f"error: startup failed: {type(e).__name__}: {e}", file=sys.stderr)
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
