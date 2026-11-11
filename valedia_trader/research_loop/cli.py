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
    python -m valedia_trader.research_loop cycle                the whole loop once: research, test, revise, seal, paper, critic
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


def _account_limits(paths: LoopPaths) -> dict | None:
    """The kill switch's limits for the research agents to design within; None if the file is unusable
    (in which case the kill switch blocks every order anyway)."""
    try:
        return risk_gate.load_limits(paths.risk_limits)
    except risk_gate.RiskConfigError as exc:
        print(f"warning: {exc}", file=sys.stderr)
        return None


def _asker(spec: str, timeout: float | None = None):
    """An agent backend. `timeout` (seconds) is the one knob for how long an agent may take; None waits."""
    kind, _, rest = spec.partition(":")
    if kind == "hermes":
        return research_mod.hermes_asker(rest, timeout=timeout)
    if kind == "chat":
        model, _, url = rest.partition("@")              # chat:<model>@<base-url>
        return research_mod.chat_asker(url, model, **({"timeout": timeout} if timeout else {}))
    raise SystemExit(f"backend must be hermes:<profile> or chat:<model>@<base-url>, not {spec}")


def paper_catch_up(trader: PaperTrader, prices: pd.DataFrame, active: dict[int, Strategy]) -> list[dict]:
    """Step the paper book through every trading day it has not processed yet, oldest first, each
    with prices up to that day only. A first run starts at the latest day: nothing is backfilled.
    This makes a weekly run produce the same book a daily run would, because a decision at one
    close is filled at the next close either way. It is for the simulated book only; orders cannot
    be mirrored to a broker in the past."""
    last = trader.state["last_day"]
    days = [prices.index[-1]] if last is None else [d for d in prices.index if str(d.date()) > last]
    return [trader.step(prices.loc[:day], active) for day in days]


def panel_settings(paths: LoopPaths) -> dict:
    """How the decision panel takes part in paper trading: config/panel.json, or off when the file is absent."""
    path = paths.config / "panel.json"
    cfg = {"mode": "off", "horizon_days": 5}
    if path.is_file():
        cfg.update(json.loads(path.read_text()))
    if cfg["mode"] not in PaperTrader.PANEL_MODES:
        raise risk_gate.RiskConfigError(f"config/panel.json: mode must be one of {PaperTrader.PANEL_MODES}")
    return cfg


def paper_trader(paths: LoopPaths, venue=None) -> PaperTrader:
    """The paper trader with the decision panel attached as config/panel.json says."""
    cfg = panel_settings(paths)
    if cfg["mode"] == "off":
        return PaperTrader(paths.journal, paths.risk_limits, venue=venue)
    from valedia_trader.agents.panel import build_panel
    panel = build_panel(ledger_path=str(paths.memory / "panel_forecasts.db"))
    return PaperTrader(paths.journal, paths.risk_limits, venue=venue, panel=panel, panel_mode=cfg["mode"],
                       panel_horizon_days=int(cfg.get("horizon_days", 5)))


def run_cycle(args, paths: LoopPaths, ledger: HypothesisLedger) -> dict:
    """One pass of the loop. Each stage is the same code the single commands run, in the order the
    rules require: research sees the development period only; every untested idea gets its
    development test; failures are revised within the cap; an idea that passes gets its one sealed
    test; whatever has passed that is paper traded for the day; the critic reports on Sundays."""
    from valedia_trader.research_loop.paper import Journal
    from valedia_trader.research_loop.revision import RevisionEngine
    out: dict = {"date": str(pd.Timestamp.today().date())}
    if args.refresh:
        from valedia_trader.data_and_fees import prices as price_data
        try:
            out["prices"] = price_data.refresh(args.data)
        except Exception as exc:  # a failed download must not stop the week; the stored prices are used
            out["prices"] = {"error": f"{type(exc).__name__}: {exc}", "note": "continuing with the stored prices"}
    prices, dollar, cost = _market(args)
    g = Gauntlet(prices, dollar, cost, ledger, paths, Criteria())
    if not args.no_research:
        roles = {role: _asker(args.backend, args.agent_timeout) for role in ("price-analyst", "macro-watcher", "fundamentals-reader")}
        res = research_mod.run_research(ledger, paths, g.dev_prices, g.dev_dollar_volume, MARKET, roles,
                                        _asker(args.skeptic, args.agent_timeout) if args.skeptic else None,
                                        account_limits=_account_limits(paths))
        out["research"] = {k: v.get("accepted", v.get("objections")) for k, v in res["roles"].items()}
    tested = {}
    for h in reversed(ledger.search(market=MARKET, status="untested")):
        if h["variations_tried"] or ledger.prediction(h["id"]) is None:
            continue                                        # already in progress, or has no prediction and cannot be tested
        try:
            strategy, file = _strategy_for(paths, ledger, h["id"], None)
            report = g.develop(h["id"], strategy, file)
            verdict = report["verdict"]
            if verdict == "fail" and hasattr(strategy, "to_spec"):
                revised = RevisionEngine(g, ledger, paths).revise(h["id"], strategy)
                verdict = f"{'pass' if revised['passed'] else 'fail'} after {revised['revisions_used']} revisions"
                strategy, file = _strategy_for(paths, ledger, h["id"], None)
            if verdict.startswith("pass"):
                verdict = "sealed test: " + g.seal(h["id"], strategy, file)["verdict"]
            tested[h["id"]] = verdict
        except (GauntletError, LedgerError, SpecError) as exc:
            tested[h["id"]] = f"refused: {exc}"
    out["tested"] = tested
    active = {h["id"]: _strategy_for(paths, ledger, h["id"], None)[0] for s in PAPER_STATUSES for h in ledger.search(status=s)}
    journal = Journal(paths.journal)
    if active:
        steps = paper_catch_up(paper_trader(paths), prices, active)
        out["paper"] = {"days_processed": len(steps), "last": steps[-1] if steps else "already up to date"}
    else:
        out["paper"] = "nothing has passed its backtest and sealed test"
    if args.critic or pd.Timestamp.today().dayofweek == 6:
        names = {hid: s.name for hid, s in active.items()}
        last = max((e["day"] for e in journal.read()), default=out["date"])
        out["critic"] = critic_mod.weekly_review(ledger, journal, last, names, paths.risk_limits)["path"]
    out["ledger"] = ledger.summary()
    return out


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
    p = sub.add_parser("cycle", help="one full pass of the loop")
    p.add_argument("--refresh", action="store_true", help="download the latest prices first")
    p.add_argument("--no-research", action="store_true", help="skip the research round (test what is already untested)")
    p.add_argument("--backend", default="hermes:valedia-trading-decider")
    p.add_argument("--skeptic", default="hermes:valedia-trading-auditor")
    p.add_argument("--critic", action="store_true", help="run the weekly critic at the end (it always runs on Sundays)")
    p.add_argument("--agent-timeout", type=float, default=None, help="seconds one research agent may take (default: no limit)")
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
                                            _asker(args.skeptic) if args.skeptic else None, account_limits=_account_limits(paths))
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
            trader = paper_trader(paths, venue=venue)
            if not active:
                print("no strategy has passed its backtest and sealed test; nothing to paper trade")
            elif venue is not None:
                print(json.dumps(trader.step(prices, active), indent=2))      # a broker mirror cannot trade in the past
            else:
                steps = paper_catch_up(trader, prices, active)
                print(json.dumps({"days_processed": len(steps), "last": steps[-1] if steps else "already up to date"}, indent=2))
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
        elif args.cmd == "cycle":
            print(json.dumps(run_cycle(args, paths, ledger), indent=2, default=str))
        elif args.cmd == "reset-kill-switch":
            trader = PaperTrader(paths.journal, paths.risk_limits)
            print("latch cleared" if trader.reset_latch(args.who, pd.Timestamp.today()) else "the latch was not set")
    except (GauntletError, LedgerError, SpecError, risk_gate.RiskConfigError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    finally:
        ledger.close()
    return 0
