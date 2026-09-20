#!/usr/bin/env python3
"""CLI: crypto paper simulation ledger. Fixture default. --live-data = public klines.

Never keys. Never create_order. Not a live trading bot.
  python3 crypto_paper_run.py
  python3 crypto_paper_run.py --live-data
(c) GOATWORTHY Post LLC.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import crypto_paper as cp  # noqa: E402


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Valedia crypto paper simulation ledger (no live orders)",
    )
    p.add_argument("--live-data", action="store_true",
                   help="public REST klines only; no keys, no orders")
    p.add_argument("--fixture", default=None, help="OHLCV JSON (dry default)")
    p.add_argument("--out", default=None, help="equity/PnL JSON path")
    p.add_argument("--pair", default="BTC-USDT")
    p.add_argument("--cash", type=float, default=10000.0)
    p.add_argument("--maker", type=float, default=None)
    p.add_argument("--taker", type=float, default=None)
    p.add_argument("--slip", type=float, default=None)
    args = p.parse_args(None if argv is None else argv)
    doc = cp.run(
        fixture_path=args.fixture,
        live_data=args.live_data,
        pair=args.pair,
        cash=args.cash,
        maker=args.maker,
        taker=args.taker,
        slip=args.slip,
        out_path=args.out,
    )
    print(json.dumps({
        "ok": True,
        "status": doc["status"],
        "source": doc["source"],
        "path": doc["path"],
        "pair": doc["pair"],
        "starting_equity": doc["starting_equity"],
        "ending_equity": doc["ending_equity"],
        "would_have_pnl": doc["would_have_pnl"],
        "n_fills": doc["n_fills"],
        "fees_paid": doc["fees_paid"],
        "create_order": False,
        "live": False,
        "valedia_version": doc["valedia_version"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
