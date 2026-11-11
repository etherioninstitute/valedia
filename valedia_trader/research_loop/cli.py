"""Command line for the research loop.

    python -m valedia_trader.research_loop init                 folders, risk limits, ledger; record what was already tested
    python -m valedia_trader.research_loop status               the ledger at a glance
    python -m valedia_trader.research_loop research             one research round (agents check the ledger first)
    python -m valedia_trader.research_loop backtest <id>        development test of an idea's strategy (counted in the ledger)
    python -m valedia_trader.research_loop revise <id>          repair a failed idea: propose, probe, compose, within the cap
    python -m valedia_trader.research_loop seal <id>            the idea's single sealed test
    python -m valedia_trader.research_loop paper-step           one paper-trading day for the strategies that passed
    python -m valedia_trader.research_loop critic               the weekly post-mortem
    python -m valedia_trader.research_loop reset-kill-switch --who NAME
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd

from valedia_trader.research_loop import LoopPaths, risk_gate
from valedia_trader.research_loop import critic as critic_mod
from valedia_trader.research_loop import research as research_mod
from valedia_trader.research_loop.gauntlet import Criteria, Gauntlet, GauntletError
from valedia_trader.research_loop.ledger import HypothesisLedger, LedgerError
from valedia_trader.research_loop.paper import AlpacaPaperVenue, PaperTrader
from valedia_trader.research_loop.strategy import SpecError, Strategy, load_strategy

MARKET = "us-etf+crypto"
PAPER_STATUSES = ("passed backtest only", "passed paper")

# What the 2026-11-10 walk-forward already tried on this market, in one sentence each. These go into
# the ledger so the research agents do not rediscover them and the Deflated Sharpe counts them.
_PRIOR = {
    "sleeve:trend": "Go long assets whose six-month return is positive and short the rest, sized by inverse volatility",
    "sleeve:xs_momentum": "Hold the top third of assets by six-month return and short the bottom third",
    "sleeve:reversal": "Fade five-day moves larger than one standard deviation",
    "sleeve:risk_parity": "Hold every asset long in inverse proportion to its volatility",
    "equal_weight_sleeves": "Blend trend, cross-sectional momentum, reversal and risk parity sleeves in equal parts",
    "darwinian": "Blend the four sleeves with weights that grow for recent winners and shrink for recent losers",
    "darwinian+kelly+cro": "Add fractional Kelly sizing and a risk officer on top of the adaptive sleeve blend",
    "darwinian+kelly+cro+regimes": "Scale the Kelly-sized sleeve blend by a two-state volatility regime model",
    "darwinian+kelly+cro+reflexivity": "Cut the Kelly-sized sleeve blend when its own profit pattern signals a reversal",
    "darwinian+buffer": "Trade the adaptive sleeve blend only when a position drifts more than ten percent from target",
    "darwinian+kelly+cro+autoresearch": "Retune one sleeve parameter at a time and keep changes that raise the recent Sharpe",
    "sleeve:timesfm": "Follow the five-day direction forecast of the TimesFM 2.5 foundation model on daily prices",
    "sleeve:naive_drift": "Follow the trailing one-year drift of each asset scaled by its volatility, rebalanced weekly",
    "darwinian+timesfm+buffer": "Add the TimesFM forecast sleeve to the buffered adaptive sleeve blend",
}
_PRIOR_LESSONS = {
    "sleeve:timesfm": "Zero-shot TimesFM 2.5 showed no directional skill at a 5-day horizon on daily ETF and crypto prices "
                      "(rank IC about 0, hit rate about 50%); its 80% forecast band was calibrated. Do not use it for direction.",
    "darwinian+timesfm+buffer": "Adding the TimesFM sleeve lowered the blend's Sharpe.",
    "darwinian+kelly+cro": "The Kelly and risk-officer overlay raised return but lowered the Sharpe ratio against the plain blend.",
    "sleeve:naive_drift": "As a control it beat the model it was a control for. It was looked at on 2020-2026 data, so that "
                          "period is not a clean test for any drift idea.",
}


def seed_prior_trials(ledger: HypothesisLedger, trials_db: Path, report: Path | None = None) -> int:
    """Record the earlier walk-forward's distinct configurations as tested ideas. Runs once."""
    if not trials_db.exists() or ledger.search(market=MARKET, text="Recorded from the 2026-11-10 walk-forward"):
        return 0
    rows = sqlite3.connect(trials_db).execute(
        "SELECT strategy, sharpe, n_obs, params FROM trials WHERE id IN (SELECT MAX(id) FROM trials GROUP BY strategy, params) ORDER BY id").fetchall()
    details = json.loads(report.read_text()).get("strategies", {}) if report and report.exists() else {}
    autoresearch_id, added = None, 0
    for name, sharpe, n_obs, params in rows:
        if name.startswith("autoresearch:"):                       # variations of one idea, not ideas of their own
            if autoresearch_id is None:
                autoresearch_id = ledger.propose("Mutate one parameter of the cross-sectional momentum sleeve and keep it if the "
                                                 "recent Sharpe improves", "walk-forward", MARKET)
                ledger.set_status(autoresearch_id, "failed", "Recorded from the 2026-11-10 walk-forward: all three mutations were rejected.", "seed")
                ledger.use_sealed(autoresearch_id, "walk-forward")
                added += 1
            ledger.record_attempt(autoresearch_id, "search", sharpe, n_obs, name, json.loads(params or "{}"))
            continue
        if name not in _PRIOR:
            continue
        hid = ledger.propose(_PRIOR[name], "walk-forward", MARKET, threshold=1.01)
        ledger.record_attempt(hid, "backtest", sharpe, n_obs, name)
        holdout = (details.get(name) or {}).get("holdout") or {}
        note = (f"Recorded from the 2026-11-10 walk-forward as '{name}': holdout Sharpe {holdout.get('sharpe', float('nan')):.2f}, "
                f"Deflated Sharpe {holdout.get('deflated_sharpe', float('nan')):.2f} against a 0.95 gate. That holdout (2020 onward) was "
                "looked at for every configuration, so this idea has no clean sealed test left.")
        ledger.set_status(hid, "failed", note, "seed")
        if name in _PRIOR_LESSONS:
            ledger.add_lesson(hid, _PRIOR_LESSONS[name], "seed")
        ledger.use_sealed(hid, "walk-forward")
        added += 1
    return added


def _market(args):
    from valedia_trader.backtest.walkforward import build_cost_model, load_universe
    from valedia_trader.data_and_fees.venue_registry import VenueRegistry
    prices, dollar = load_universe(args.data)
    registry = VenueRegistry()
    cost = build_cost_model(prices.columns, registry, as_of=prices.index[-1])
    registry.close()
    return prices, dollar, cost


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


def _asker(spec: str):
    kind, _, rest = spec.partition(":")
    if kind == "hermes":
        return research_mod.hermes_asker(rest)
    if kind == "chat":
        url, _, model = rest.rpartition("@")
        return research_mod.chat_asker(url, model)
    raise SystemExit(f"backend must be hermes:<profile> or chat:<model>@<base-url>, not {spec}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="research_loop", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=None, help="project root (default: the repository)")
    ap.add_argument("--data", default="artifacts/data")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    sub.add_parser("status")
    p = sub.add_parser("research")
    p.add_argument("--roles", nargs="+", default=["price-analyst", "macro-watcher", "fundamentals-reader"])
    p.add_argument("--backend", default="hermes:valedia-trading-decider")
    p.add_argument("--skeptic", default="hermes:valedia-trading-auditor")
    for name in ("backtest", "seal"):
        p = sub.add_parser(name)
        p.add_argument("id", type=int)
        p.add_argument("--file")
    p = sub.add_parser("revise", help="try to repair a failed idea within the revision cap")
    p.add_argument("id", type=int)
    p.add_argument("--file")
    p.add_argument("--k", type=int, default=1, help="edits probed per dimension in each round")
    p = sub.add_parser("paper-step")
    p.add_argument("--refresh", action="store_true", help="download the latest prices first")
    p.add_argument("--alpaca", action="store_true", help="also mirror orders to the Alpaca paper account")
    p = sub.add_parser("critic")
    p.add_argument("--narrate", help="backend that puts the findings into words, e.g. hermes:valedia-trading-auditor")
    p.add_argument("--days", type=int, default=7)
    p = sub.add_parser("reset-kill-switch")
    p.add_argument("--who", required=True)
    args = ap.parse_args(argv)

    paths = LoopPaths(Path(args.root).resolve()) if args.root else LoopPaths()
    paths.ensure()
    data = Path(args.data) if Path(args.data).is_absolute() else paths.root / args.data
    args.data = str(data)
    ledger = HypothesisLedger(paths.ledger)
    try:
        if args.cmd == "init":
            created = risk_gate.write_default_limits(paths.risk_limits)
            seeded = seed_prior_trials(ledger, paths.root / "artifacts" / "trials.sqlite", paths.root / "artifacts" / "walkforward_report.json")
            print(f"folders ready under {paths.root}")
            print(f"risk limits: {paths.risk_limits} ({'created with the example numbers; set your own' if created else 'kept as it was'})")
            print(f"ledger: {paths.ledger} ({seeded} earlier ideas recorded)" if seeded else f"ledger: {paths.ledger}")
        elif args.cmd == "status":
            print(json.dumps(ledger.summary(), indent=2))
            for h in reversed(ledger.search()):
                print(f"#{h['id']:3d} {h['status']:22s} x{h['variations_tried']:<3d} {h['source_agent']:16s} {h['idea'][:92]}")
        elif args.cmd == "research":
            prices, dollar, cost = _market(args)
            g = Gauntlet(prices, dollar, cost, ledger, paths)
            askers = {role: _asker(args.backend) for role in args.roles}
            out = research_mod.run_research(ledger, paths, g.dev_prices, g.dev_dollar_volume, MARKET, askers,
                                            _asker(args.skeptic) if args.skeptic else None)
            print(json.dumps({k: v for k, v in out.items() if k != "accepted"}, indent=2))
            for r in out["accepted"]:
                print(f"accepted #{r['hypothesis_id']}: {r['idea']}  ->  {r['strategy_file']}")
        elif args.cmd in ("backtest", "seal"):
            prices, dollar, cost = _market(args)
            g = Gauntlet(prices, dollar, cost, ledger, paths, Criteria())
            strategy, file = _strategy_for(paths, ledger, args.id, args.file)
            rep = g.develop(args.id, strategy, file) if args.cmd == "backtest" else g.seal(args.id, strategy, file)
            r = rep["result"]
            print(f"{strategy.name} v{strategy.version}: {rep['verdict']}")
            print(f"  Sharpe {r.get('sharpe', float('nan')):.2f}, return {r.get('total_return', float('nan')):+.1%}, max drawdown "
                  f"{r.get('max_drawdown', float('nan')):.1%}, {r.get('trades', 0)} trades, win rate {r.get('trade_win_rate', float('nan')):.0%}")
            for name, c in rep["checks"].items():
                print(f"  [{'pass' if c['passed'] else 'FAIL'}] {name}: {c['detail']}")
        elif args.cmd == "revise":
            from valedia_trader.research_loop.revision import RevisionEngine
            prices, dollar, cost = _market(args)
            g = Gauntlet(prices, dollar, cost, ledger, paths, Criteria())
            strategy, _ = _strategy_for(paths, ledger, args.id, args.file)
            out = RevisionEngine(g, ledger, paths, k=args.k).revise(args.id, strategy)
            for r in out["rounds"]:
                print(f"v{r['version']} (from {r['parent']}): " + " | ".join(r["decision"]))
                if "verdict" in r:
                    print(f"    -> Sharpe {r['sharpe']:.2f}, Deflated Sharpe {r['deflated_sharpe']:.3f}, {r['verdict']}")
            print(f"stopped: {out['stopped']}; best v{out['best_version']} (Sharpe {out['best_sharpe']:.2f}); "
                  f"{out['variations_counted']} variations counted, {out['revisions_used']} revisions used; passed: {out['passed']}")
        elif args.cmd == "paper-step":
            if args.refresh:
                from valedia_trader.data_and_fees import prices as price_data
                print("prices:", price_data.refresh(args.data))
            prices, _, _ = _market(args)
            active = {h["id"]: _strategy_for(paths, ledger, h["id"], None)[0] for s in PAPER_STATUSES for h in ledger.search(status=s)}
            venue = None
            if args.alpaca:
                from valedia_trader.core.secrets import load_env, secret
                load_env()
                venue = AlpacaPaperVenue(secret("ALPACA_API_KEY"), secret("ALPACA_SECRET_KEY"))
            trader = PaperTrader(paths.journal, paths.risk_limits, venue=venue)
            if not active:
                print("no strategy has passed its backtest and sealed test; nothing to paper trade")
            else:
                print(json.dumps(trader.step(prices, active), indent=2))
        elif args.cmd == "critic":
            from valedia_trader.research_loop.paper import Journal
            names = {h["id"]: _strategy_for(paths, ledger, h["id"], None)[0].name
                     for s in (*PAPER_STATUSES, "paused") for h in ledger.search(status=s) if h["paper_trading_result"] or s != "paused"}
            journal = Journal(paths.journal)
            last = max((e["day"] for e in journal.read()), default=str(pd.Timestamp.today().date()))
            out = critic_mod.weekly_review(ledger, journal, last, names, paths.risk_limits, days=args.days,
                                           narrator=_asker(args.narrate) if args.narrate else None)
            print(out["path"])
            for f in out["findings"]:
                print(" -", f)
        elif args.cmd == "reset-kill-switch":
            trader = PaperTrader(paths.journal, paths.risk_limits)
            print("latch cleared" if trader.reset_latch(args.who, pd.Timestamp.today()) else "the latch was not set")
    except (GauntletError, LedgerError, SpecError, risk_gate.RiskConfigError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    finally:
        ledger.close()
    return 0
