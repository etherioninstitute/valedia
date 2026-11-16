"""Revising a failed idea, with the brakes on.

When a development test fails, the loop may explain where the strategy loses money and try to
improve it. That is the dangerous part of any self-improving system: rewrite until it passes and
something will always pass. So a revision here is a Harness2 Propose-Probe-Compose step whose every
probe is a counted variation, the versions are layers in a Meta-N archive that never breeds from a
regression, and the ledger's cap on revisions (3 to 5 per idea) ends the search. The sealed period
is never involved.

    engine = RevisionEngine(gauntlet, ledger, paths)
    result = engine.revise(hypothesis_id, strategy)      # stops at a pass, at the cap, or when out of edits
"""
from __future__ import annotations

import json
import math
import random
from pathlib import Path

from valedia_trader.agents import harness2 as h2
from valedia_trader.agents import meta_n as mn
from valedia_trader.research_loop import LoopPaths
from valedia_trader.research_loop import strategy as st
from valedia_trader.research_loop.gauntlet import Gauntlet, GauntletError, RevisionCapReached
from valedia_trader.research_loop.ledger import HypothesisLedger, SealedPeriodUsed

__all__ = ["SpecProposer", "RevisionEngine", "evidence_from", "PRIMARY_LOOKBACK"]

PRIMARY_LOOKBACK = {"momentum": "lookback", "breakout": "lookback", "reversion": "lookback", "ma_cross": "slow", "drift": "window"}
_SELECTIVE_STEP = {"momentum": 0.02, "reversion": 0.5, "ma_cross": 0.01, "drift": 0.05}


def evidence_from(spec: dict, backtest: dict) -> dict:
    """The behaviour of a tested version, in the shape Harness2 and Meta-N work with."""
    result = backtest.get("result") or {}
    def num(x):
        return float(x) if isinstance(x, (int, float)) and math.isfinite(x) else float("nan")
    return {"score": num(result.get("sharpe")), "drawdown": num(result.get("max_drawdown")), "trades": result.get("trades", 0),
            "turnover": num(result.get("annual_turnover")),
            "tasks": {f"w{i + 1}": num(w.get("sharpe")) for i, w in enumerate(backtest.get("walk_forward") or [])},
            "exits": backtest.get("exits") or {}, "worst_symbols": (backtest.get("where_it_loses") or {}).get("worst_symbols") or {},
            "failed_checks": [k for k, c in (backtest.get("checks") or {}).items() if not c.get("passed")], "spec": spec}


def _set(path: str, value) -> dict:
    return {"op": "set", "path": path, "value": value}


class SpecProposer:
    """Edits for a rule-library strategy, read off its test report. INV edits change how positions
    are managed; DOM edits change the signal or where it is applied. Each kind is proposed at most
    once per parent, so a second round on the same version tries something else."""

    def __init__(self) -> None:
        self.tried: set[tuple[str, str]] = set()

    def propose(self, evidence: dict, k: int, library: h2.EditLibrary) -> list[h2.Edit]:
        spec = evidence["spec"]
        entry, risk, sizing = spec["entry"], spec.get("risk", {}), spec.get("sizing", {"rule": "equal"})
        exits, failed = evidence.get("exits", {}), set(evidence.get("failed_checks", []))
        key = json.dumps(spec, sort_keys=True)
        out: list[h2.Edit] = []

        def add(dim, kind, description, *ops):
            if (key, kind) not in self.tried:
                out.append(h2.Edit(f"{dim.lower()}_{kind}", dim, kind, description, mn.Patch(tuple(ops), description)))

        # ---- INV: how positions are managed
        stop, stops = risk.get("stop_loss_pct"), exits.get("stop loss", {})
        total_exits = sum(v["count"] for v in exits.values()) or 1
        if stop is not None and stops.get("count", 0) / total_exits >= 0.25:
            add("INV", "widen_stop", f"widen the stop from {stop:.0%} to {min(stop * 1.5, 0.5):.0%}: {stops['count']} of {total_exits} exits were stops",
                _set("risk.stop_loss_pct", round(min(stop * 1.5, 0.5), 4)))
        if stop is None and ({"prediction_failure_terms", "worst_periods"} & failed):
            add("INV", "add_stop", "add a 10% stop: the drawdown terms failed", _set("risk.stop_loss_pct", 0.10))
        if risk.get("max_holding_days") is not None and exits.get("time limit", {}).get("mean_pnl_pct", 0.0) > 0:
            add("INV", "remove_time_limit", "drop the time limit: positions closed by it were winning on average", _set("risk.max_holding_days", None))
        gap = entry["enter"] - entry["exit"]
        looser = round(entry["exit"] - max(gap, abs(entry["enter"]) * 0.5, 0.01), 6)
        if entry.get("direction", "long") == "long" or looser >= -entry["enter"]:
            add("INV", "more_hysteresis", f"hold longer: lower the exit level from {entry['exit']} to {looser}", _set("entry.exit", looser))
        if sizing.get("rule") == "equal":
            add("INV", "inverse_vol_sizing", "size positions by inverse volatility", _set("sizing", {"rule": "inverse_vol", "position_vol": 0.02}))
        if risk.get("take_profit_pct") is not None:
            add("INV", "remove_take_profit", "let winners run: remove the take-profit", _set("risk.take_profit_pct", None))
        if stop is not None:
            add("INV", "remove_stop", "remove the stop and rely on the exit rule", _set("risk.stop_loss_pct", None))

        # ---- DOM: the signal and where it is applied
        rule = entry["rule"]
        param = PRIMARY_LOOKBACK[rule]
        lo, hi = st._SCORE_PARAMS[rule][param]
        current = entry.get(param, {"momentum": 126, "breakout": 55, "reversion": 5, "ma_cross": 200, "drift": 250}[rule])
        longer, shorter = int(min(hi, round(current * 1.5))), int(max(lo, round(current / 1.5)))
        if rule == "ma_cross":
            shorter = max(shorter, entry.get("fast", 50) + 1)
        if longer != current:
            add("DOM", "longer_lookback", f"slow the signal: {param} {current} -> {longer}", _set(f"entry.{param}", longer))
        if shorter != current:
            add("DOM", "shorter_lookback", f"speed up the signal: {param} {current} -> {shorter}", _set(f"entry.{param}", shorter))
        if rule in _SELECTIVE_STEP:
            stricter = round(entry["enter"] + _SELECTIVE_STEP[rule], 6)
            add("DOM", "more_selective", f"enter only on a stronger signal: {entry['enter']} -> {stricter}", _set("entry.enter", stricter))
        worst = [(s, v) for s, v in evidence.get("worst_symbols", {}).items() if v < 0 and s in spec["market"]]
        if worst and len(spec["market"]) > 1:
            symbol = min(worst, key=lambda x: x[1])[0]
            add("DOM", "drop_worst_symbol", f"stop trading {symbol}, the largest loser", {"op": "remove", "path": "market", "value": symbol})
        if entry.get("direction") == "both":
            add("DOM", "long_only", "trade the long side only", _set("entry.direction", "long"))

        chosen = [e for dim in h2.DIMENSIONS for e in [x for x in library.rank(out) if x.dimension == dim][:k]]
        self.tried |= {(key, e.kind) for e in chosen}
        return chosen


class RevisionEngine:
    def __init__(self, gauntlet: Gauntlet, ledger: HypothesisLedger, paths: LoopPaths, proposer=None,
                 library: h2.EditLibrary | None = None, k: int = 1, margin: float = 0.05, seed: int = 0) -> None:
        self.gauntlet, self.ledger, self.paths, self.k, self.margin = gauntlet, ledger, paths, k, margin
        self.proposer = proposer or SpecProposer()
        self.library = library or h2.EditLibrary(paths.memory / "edit_library.json")
        self.rng = random.Random(seed)

    def revise(self, hypothesis_id: int, strategy: st.RuleStrategy, max_rounds: int | None = None) -> dict:
        """Try to repair an idea whose development test failed. Returns what was tried, the archive
        and the final verdict. On a pass the strategy file holds the passing version; otherwise it
        holds the best version seen, and the idea stays failed."""
        if not isinstance(strategy, st.RuleStrategy):
            raise st.SpecError("only rule strategies can be revised")
        ledger, g = self.ledger, self.gauntlet
        row = ledger.get(hypothesis_id)
        if ledger.sealed_used(hypothesis_id):
            raise SealedPeriodUsed(f"hypothesis {hypothesis_id} has seen its sealed result; it is not revised any more")
        backtest = row["backtest_result"]
        if not backtest or row["strategy_version"] != strategy.version:
            raise GauntletError("run the development test of this version before revising it")
        saved = sorted(self.paths.backtests.glob(f"{strategy.name}-v{strategy.version}-attempt*.json"))
        if saved:                                         # the saved report has the detail the ledger row may lack
            backtest = {**json.loads(saved[-1].read_text()), **backtest}
        base_spec = strategy.to_spec()
        identity = {k: base_spec[k] for k in ("name", "hypothesis")}
        archive = mn.Archive()
        seed = self._candidate(f"v{strategy.version}", evidence_from(base_spec, backtest))
        archive.add(seed)
        evidence = {seed.candidate_id: evidence_from(base_spec, backtest)}
        verdicts = {seed.candidate_id: backtest.get("verdict", "fail")}
        version, rounds, stop, last = strategy.version, [], "revision cap reached", seed.candidate_id
        cap = g.criteria.max_revisions
        while True:
            if str(verdicts[archive.best().candidate_id]).startswith("pass"):
                stop = "passed the development test"
                break
            if ledger.revisions(hypothesis_id) >= cap:
                break
            if max_rounds is not None and len(rounds) >= max_rounds:
                stop = "round limit reached"
                break
            parent = archive.select_parents(1, self.rng, archive.breedable_pool(max_depth=cap + 1))[0]
            parent_spec = evidence[parent.candidate_id]["spec"]
            version = st.bump_version(max((r["version"] for r in rounds), default=strategy.version, key=_version_key))

            def probe(edits, parent_spec=parent_spec, version=version):
                candidates = {e.label: st.RuleStrategy.from_spec({**mn.apply_chain(parent_spec, [e.patch]), **identity, "version": version})
                              for e in edits if self._valid(parent_spec, e)}
                return g.probe(hypothesis_id, version, candidates) if candidates else {}

            try:
                step = h2.ppc_step(f"h{hypothesis_id}", evidence[parent.candidate_id], self.proposer, probe, self.k, self.library,
                                   margin=self.margin, task_tolerance=None, guards={"drawdown": 0.03})
            except RevisionCapReached:
                break
            record = {"version": version, "parent": parent.candidate_id, "probed": [h2.edit_summary(p.edit) | {"contrast": step.contrasts[p.edit.label]}
                                                                                      for p in step.probes],
                      "decision": list(step.composition.reasons)}
            if not step.edits:
                stop = "no further edits to try"
                break
            rounds.append(record)
            if step.composition.keep_base:
                ledger.add_lesson(hypothesis_id, f"Revision v{version} of v{parent.candidate_id[1:]}: " + "; ".join(step.composition.reasons), "revision")
                continue
            layer = step.composition.patch(parent.depth + 1)
            spec = {**mn.apply_chain(parent_spec, [layer]), **identity, "version": version}
            composed = st.RuleStrategy.from_spec(spec)
            report = g.develop(hypothesis_id, composed, str(st.save_spec(composed, self.paths.strategies).relative_to(self.paths.root)))
            child = self._candidate(f"v{version}", evidence_from(spec, report), parent, layer, len(rounds))
            archive.add(child)
            last = child.candidate_id
            evidence[child.candidate_id], verdicts[child.candidate_id] = evidence_from(spec, report), report["verdict"]
            record.update({"composed": layer.rationale, "sharpe": report["result"].get("sharpe"), "verdict": report["verdict"],
                           "deflated_sharpe": report["deflated_sharpe"]["value"], "regressed": archive.regressed(child.candidate_id)})
            ledger.add_lesson(hypothesis_id, f"Revision v{version}: {layer.rationale}. Sharpe {evidence[parent.candidate_id]['score']:.2f} -> "
                              f"{report['result'].get('sharpe', float('nan')):.2f}, Deflated Sharpe {report['deflated_sharpe']['value']:.3f} after "
                              f"{report['deflated_sharpe']['variations_of_idea']} variations."
                              + (" It regressed and will not be built on." if record["regressed"] else ""), "revision")
        best = archive.best()
        passed = str(verdicts[best.candidate_id]).startswith("pass")
        if best.candidate_id != last:
            ledger.add_lesson(hypothesis_id, f"Rolled back to {best.candidate_id}: the later rewrite {last} scored lower.", "revision")
        if not passed and ledger.revisions(hypothesis_id) >= cap:
            ledger.set_status(hypothesis_id, "failed", f"still failing after {cap} revisions and {ledger.variations(hypothesis_id)} counted "
                              "variations; the idea is closed", "revision")
        best_strategy = st.RuleStrategy.from_spec(evidence[best.candidate_id]["spec"])
        file = st.save_spec(best_strategy, self.paths.strategies)          # the file ends on the best version, not the last tried
        out = {"hypothesis_id": hypothesis_id, "stopped": stop, "passed": passed, "best_version": best_strategy.version,
               "best_sharpe": best.mean_score, "variations_counted": ledger.variations(hypothesis_id),
               "revisions_used": ledger.revisions(hypothesis_id), "rounds": rounds, "archive": archive.to_dict(),
               "strategy_file": str(file.relative_to(self.paths.root))}
        Path(self.paths.backtests / f"{strategy.name}-revisions.json").write_text(json.dumps(out, indent=2, default=str))
        return out

    @staticmethod
    def _valid(parent_spec: dict, edit: h2.Edit) -> bool:
        try:
            st.RuleStrategy.from_spec(mn.apply_chain(parent_spec, [edit.patch]))
            return True
        except (st.SpecError, mn.PatchError):
            return False

    @staticmethod
    def _candidate(cid: str, ev: dict, parent: mn.Candidate | None = None, patch: mn.Patch | None = None, iteration: int = 0) -> mn.Candidate:
        traces = [mn.Trace(t, s) for t, s in ev["tasks"].items()]
        return mn.Candidate.evaluated(cid, traces, parent, patch, iteration, mean_score=ev["score"])


def _version_key(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split("."))
