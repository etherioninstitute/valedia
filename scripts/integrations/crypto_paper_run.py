#!/usr/bin/env python3
"""CLI: crypto paper simulation ledger. Fixture default. --live-data = public klines.

Never keys. Never create_order. Not a live trading bot.
  python3 crypto_paper_run.py
  python3 crypto_paper_run.py --safety
  python3 crypto_paper_run.py --tick
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
import crypto_paper_indicators as ind  # noqa: E402
import crypto_paper_safety as safety  # noqa: E402
import valedia_config as vc  # noqa: E402

QWEN_MODEL = "qwen/qwen3.8-27b"
QWEN_ROUTE = "litellm:4000 alias qwen/qwen3.8-27b"


def _print_safety(dec):
    fill = dec.get("would_have_fill") or None
    body = {
        "ok": True,
        "pass": dec["pass"],
        "reason": dec["reason"],
        "bias": dec["bias"],
        "failed_conditions": dec["failed_conditions"],
        "model": dec["model"],
        "route": dec["route"],
        "n_fills": 1 if fill else 0,
        "would_have": bool(fill),
        "create_order": False,
        "live": False,
        "log": dec.get("log_path"),
        "valedia_version": dec["valedia_version"],
    }
    if dec.get("decision_path"):
        body["decision_log"] = dec["decision_path"]
    assist = dec.get("qwen_assist")
    if assist:
        body["tick"] = True
        body["qwen_assist"] = {
            "ok": assist.get("ok"),
            "reason": assist.get("reason"),
            "picked": assist.get("picked"),
            "model": assist.get("model"),
        }
    print(json.dumps(body, indent=2))


def qwen_assist(rules, snaps, *, post_fn=None, url=None, timeout=8):
    """Rank the watchlist. Fail-open. Never a fill and never the safety model."""
    watch = [cp.norm_pair(p) for p in (rules.get("watchlist") or [])]
    meta = {
        "ok": False, "model": QWEN_MODEL, "route": QWEN_ROUTE,
        "rank": [], "picked": None, "reason": "qwen_down",
    }
    brief = {}
    for p, snap in (snaps or {}).items():
        brief[p] = {k: snap.get(k) for k in ("close", "rsi", "macd_line", "macd_hist")}
    payload = {
        "model": QWEN_MODEL,
        "temperature": 0,
        "max_tokens": 200,
        "messages": [{"role": "user", "content":
            "Rank this PAPER watchlist. Reply with JSON only: "
            "{\"rank\":[\"PAIR\",...]}. Copy pairs from watchlist. "
            "This is not a trade approval.\n"
            + json.dumps({"watchlist": watch, "snapshots": brief}, default=str)}],
    }
    try:
        raw = post_fn(payload) if post_fn else safety._http_post(url or safety.DEFAULT_URL, payload, timeout)
    except Exception as exc:
        if isinstance(exc, AssertionError):
            raise
        return meta
    obj = safety.extract_json(safety._content(raw))
    rank = obj.get("rank") if isinstance(obj, dict) else None
    if not isinstance(rank, list):
        meta["reason"] = "qwen_malformed"
        return meta
    clean = []
    for p in rank:
        n = cp.norm_pair(p)
        if n in watch and n not in clean:
            clean.append(n)
    if not clean:
        meta["reason"] = "qwen_malformed"
        return meta
    meta.update(ok=True, reason="ranked", rank=clean, picked=clean[0])
    return meta


def pick_pair(assist, bars, default):
    """First ranked pair that has bars. Otherwise the CLI pair."""
    default = cp.norm_pair(default)
    if not assist.get("ok"):
        return default
    have = {cp.norm_pair(b.get("pair") or default) for b in bars or []}
    for p in assist.get("rank") or []:
        if p in have:
            assist["picked"] = p
            return p
    assist["ok"] = False
    assist["reason"] = "rank_without_bars"
    assist["picked"] = None
    return default


def _bar_ts(bars, pair):
    pair = cp.norm_pair(pair)
    pb = [b for b in bars or [] if cp.norm_pair(b.get("pair") or pair) == pair]
    return str(pb[-1].get("ts") or "") if pb else ""


def _closed(reason, assist, *, model="none"):
    return {
        "pass": False,
        "allPass": False,
        "reason": reason,
        "bias": "neutral",
        "failed_conditions": [reason],
        "model": model,
        "route": "paper-tick",
        "would_have_fill": None,
        "orderPlaced": False,
        "create_order": False,
        "live": False,
        "tick": True,
        "paperTrading": True,
        "qwen_assist": assist,
        "valedia_version": vc.VALEDIA_VERSION,
    }


def _write_decision(dec, *, path, pair, bar_ts, maker, taker, slip, snap, cash):
    row = cp.decision_row(
        dec, pair=pair, maker=maker, taker=taker, slip=slip,
        bar_ts=bar_ts, snap=snap, cash=cash,
    )
    if dec.get("nimble") is not None:
        row["nimble"] = dec["nimble"]  # verdict beside the Ornith reason in the paper decision row
    cp.append_decision(row, path)
    dec["decision_path"] = path
    return dec


def run_tick(*, try_qwen=True, qwen_post=None, qwen_timeout=8.0,
             pair="BTC-USDT", fixture_path=None, rules_path=None,
             ornith_url=None, decision_path=None, live_data=False, **kw):
    """Paper tick. One flight. Every result appends a fee-true decision row.

    Qwen may rank. Ornith still has to pass before any fill. A second tick
    for a pair+bar that already filled writes duplicate_tick and does not fill.
    """
    path = decision_path or cp.DECISION_LOG
    maker, taker, slip = cp.paper_fees(kw.get("maker"), kw.get("taker"), kw.get("slip"))
    cash = float(kw.get("cash", 10000.0))
    lock = cp.try_tick_lock(path + ".lock")
    if lock is None:
        assist = {
            "ok": False, "reason": "tick_busy", "model": QWEN_MODEL, "route": QWEN_ROUTE,
            "rank": [], "picked": None,
        }
        dec = _closed("tick_busy", assist)
        return _write_decision(
            dec, path=path, pair=pair, bar_ts="", maker=maker, taker=taker, slip=slip,
            snap=None, cash=cash,
        )
    assist = {
        "ok": False, "reason": "tick_error", "model": QWEN_MODEL, "route": QWEN_ROUTE,
        "rank": [], "picked": None,
    }
    chosen, bar_ts, snap = cp.norm_pair(pair), "", None
    try:
        rules = safety.load_rules(rules_path)
        watch = [cp.norm_pair(raw) for raw in (rules.get("watchlist") or list(cp.ALLOWLIST))]
        bars = cp.load_watchlist_bars(
            watch, live_data=live_data, fixture_path=fixture_path or safety.DEFAULT_BARS,
            get_fn=kw.get("get_fn"),
        )
        snaps = {}
        for p in watch:
            pb = [b for b in bars if cp.norm_pair(b.get("pair") or p) == p]
            if pb:
                snaps[p] = ind.snapshot(pb, rules.get("indicators"))
        if try_qwen:
            assist = qwen_assist(rules, snaps, post_fn=qwen_post, url=ornith_url, timeout=qwen_timeout)
        else:
            assist = {
                "ok": False, "reason": "off", "model": QWEN_MODEL, "route": QWEN_ROUTE,
                "rank": [], "picked": None,
            }
        chosen = pick_pair(assist, bars, pair)
        bar_ts = _bar_ts(bars, chosen)
        snap = snaps.get(chosen)
        if cp.already_filled(path, chosen, bar_ts):
            dec = _closed("duplicate_tick", assist)
            return _write_decision(
                dec, path=path, pair=chosen, bar_ts=bar_ts, maker=maker, taker=taker,
                slip=slip, snap=snap, cash=cash,
            )
        dec = safety.run_safety(
            fixture_path=fixture_path, rules_path=rules_path, pair=chosen,
            ornith_url=ornith_url, live_data=live_data,
            extra={"tick": True, "qwen_assist": assist, "live_data": bool(live_data)}, **kw,
        )
        return _write_decision(
            dec, path=path, pair=chosen, bar_ts=bar_ts, maker=maker, taker=taker,
            slip=slip, snap=snap, cash=cash,
        )
    except Exception as exc:
        if isinstance(exc, AssertionError):
            raise
        dec = _closed("tick_error", assist)
        _write_decision(
            dec, path=path, pair=chosen, bar_ts=bar_ts, maker=maker, taker=taker,
            slip=slip, snap=snap, cash=cash,
        )
        raise
    finally:
        cp.release_tick_lock(lock)


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Valedia crypto paper simulation ledger (no live orders)",
    )
    p.add_argument("--live-data", action="store_true",
                   help="public Binance.US klines; no keys/orders; default on with --tick")
    p.add_argument("--fixture", default=None, help="OHLCV JSON (dry default)")
    p.add_argument("--out", default=None, help="equity/PnL JSON path")
    p.add_argument("--pair", default="BTC-USDT")
    p.add_argument("--cash", type=float, default=10000.0)
    p.add_argument("--maker", type=float, default=None)
    p.add_argument("--taker", type=float, default=None)
    p.add_argument("--slip", type=float, default=None)
    p.add_argument("--safety", action="store_true",
                   help="Ornith fail-closed gate, then one paper fill on PASS")
    p.add_argument("--tick", action="store_true",
                   help="paper tick: optional Qwen rank, Ornith gate required")
    p.add_argument("--no-qwen", action="store_true",
                   help="with --tick, skip Qwen; Ornith still required")
    p.add_argument("--qwen-timeout", type=float, default=8.0)
    p.add_argument("--rules", default=None, help="structured rules JSON")
    p.add_argument("--safety-log", default=None, help="safety-check-log.json path")
    p.add_argument("--decision-log", default=None,
                   help="fee-true paper decisions JSONL (default data/crypto-paper/decisions.jsonl)")
    p.add_argument("--ornith-url", default=None,
                   help="LiteLLM chat URL (default :4000 alias ornith-1.5-k-m)")
    p.add_argument("--ornith-timeout", type=float, default=90.0)
    args = p.parse_args(None if argv is None else argv)
    if args.tick or args.safety:
        fn = run_tick if args.tick else safety.run_safety
        # --tick defaults to public Binance.US bars unless --fixture pins a file.
        use_live = bool(args.live_data) or (bool(args.tick) and args.fixture is None)
        extra = {"live_data": use_live}
        if args.tick:
            extra.update({
                "try_qwen": not args.no_qwen,
                "qwen_timeout": args.qwen_timeout,
                "decision_path": args.decision_log,
            })
        dec = fn(
            fixture_path=args.fixture,
            rules_path=args.rules,
            pair=args.pair,
            cash=args.cash,
            out_path=args.out,
            log_path=args.safety_log,
            ornith_url=args.ornith_url,
            timeout=args.ornith_timeout,
            maker=args.maker,
            taker=args.taker,
            slip=args.slip,
            **extra,
        )
        _print_safety(dec)
        return 0
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
