import json

import numpy as np
import pandas as pd
import pytest

from valedia_trader.agents import harness2 as h2
from valedia_trader.backtest.engine import CostModel
from valedia_trader.research_loop import LoopPaths
from valedia_trader.research_loop import gauntlet as gt
from valedia_trader.research_loop import ledger as lg
from valedia_trader.research_loop import revision as rv
from valedia_trader.research_loop import strategy as st

COST = CostModel(fees_bps=1.0, slippage_bps=1.0, borrow_annual=0.02, funding_annual=0.0)
PRED = gt.Prediction(annual_return=0.05, win_rate=0.5, sharpe=0.8, fail_if_sharpe_below=0.3, fail_if_drawdown_above=0.25)


def market(seed=0, drift=(0.0009, 0.0008, 0.0007, -0.0007, -0.0008, -0.0009), n=2400):
    rng = np.random.default_rng(seed)
    r = np.array(drift) + 0.01 * rng.standard_normal((n, len(drift)))
    return pd.DataFrame(100 * np.exp(np.cumsum(r, axis=0)), index=pd.bdate_range("2012-01-02", periods=n), columns=list("ABCDEF"))


def crippled(version="1.0.0"):
    """A sound trend rule that trades too often: a 1.5% stop and no hysteresis. Where trading is
    expensive it fails the breaker; holding longer on a slower signal repairs it."""
    return st.RuleStrategy.from_spec({
        "name": "trend_fix", "version": version, "hypothesis": "Assets with a persistent drift keep drifting the same way",
        "market": list("ABCDEF"), "entry": {"rule": "momentum", "lookback": 120, "enter": 0.02, "exit": 0.0, "direction": "both"},
        "sizing": {"rule": "equal"}, "risk": {"max_position_pct": 0.15, "max_open_positions": 6, "stop_loss_pct": 0.015}})


EXPENSIVE = CostModel(fees_bps=25.0, slippage_bps=25.0, borrow_annual=0.02, funding_annual=0.0)


def desk(tmp_path, prices, criteria=None, cost=COST):
    paths = LoopPaths(tmp_path).ensure()
    ledger = lg.HypothesisLedger(paths.ledger)
    g = gt.Gauntlet(prices, None, cost, ledger, paths, criteria)
    hid = ledger.propose("Assets with a persistent drift keep drifting the same way", "price-analyst", "synthetic")
    gt.write_prediction(ledger, paths, hid, "trend_fix", PRED)
    return paths, ledger, g, hid


def test_proposer_reads_the_report_and_separates_how_from_what():
    spec = crippled().to_spec()
    evidence = {"score": 0.1, "spec": spec, "failed_checks": ["deflated_sharpe"], "worst_symbols": {"D": -0.04, "A": 0.01},
                "exits": {"stop loss": {"count": 80, "mean_pnl_pct": -0.02}, "exit rule": {"count": 20, "mean_pnl_pct": 0.05}}}
    proposer, library = rv.SpecProposer(), h2.EditLibrary()
    first = proposer.propose(evidence, 2, library)
    kinds = {e.kind: e for e in first}
    assert [e.dimension for e in first] == ["INV", "INV", "DOM", "DOM"]
    assert "widen_stop" in kinds and kinds["widen_stop"].patch.ops == ({"op": "set", "path": "risk.stop_loss_pct", "value": 0.0225},)
    assert "80 of 100 exits were stops" in kinds["widen_stop"].description
    everything = first + proposer.propose(evidence, 9, library)
    kinds = {e.kind: e for e in everything}
    assert {"widen_stop", "more_hysteresis", "inverse_vol_sizing", "remove_stop"} <= {k for k, e in kinds.items() if e.dimension == "INV"}
    assert {"longer_lookback", "shorter_lookback", "more_selective", "drop_worst_symbol", "long_only"} == {k for k, e in kinds.items() if e.dimension == "DOM"}
    assert kinds["drop_worst_symbol"].patch.ops == ({"op": "remove", "path": "market", "value": "D"},)
    assert kinds["longer_lookback"].patch.ops[0]["value"] == 180 and kinds["shorter_lookback"].patch.ops[0]["value"] == 80
    assert len({e.kind for e in everything}) == len(everything)          # nothing is proposed twice for the same version
    assert proposer.propose(evidence, 9, library) == []
    for e in everything:                                                 # every edit yields a valid strategy
        st.RuleStrategy.from_spec(__import__("valedia_trader.agents.meta_n", fromlist=["x"]).apply_chain(spec, [e.patch]))


def test_a_repairable_strategy_is_repaired_and_every_probe_is_counted(tmp_path):
    paths, ledger, g, hid = desk(tmp_path, market(), cost=EXPENSIVE)
    base = crippled()
    first = g.develop(hid, base, "strategies/trend_fix.json")
    assert first["verdict"] == "fail" and first["exits"]["stop loss"]["count"] > 0
    out = rv.RevisionEngine(g, ledger, paths, k=2).revise(hid, base)
    assert out["passed"] and out["stopped"] == "passed the development test" and out["best_sharpe"] > first["result"]["sharpe"]
    assert 1 <= out["revisions_used"] <= g.criteria.max_revisions
    probes = sum(len(r["probed"]) for r in out["rounds"])
    composed = sum("verdict" in r for r in out["rounds"])
    assert out["variations_counted"] == 1 + probes + composed == ledger.variations(hid)     # nothing was tried off the books
    fixed = st.load_strategy(paths.root / out["strategy_file"])
    assert fixed.version == out["best_version"] != "1.0.0" and ledger.get(hid)["status"] == "untested"
    assert "Revision v" in ledger.get(hid)["lessons_learned"]
    saved = json.loads((paths.backtests / "trend_fix-revisions.json").read_text())
    assert saved["archive"]["best"] == f"v{out['best_version']}" and saved["archive"]["candidates"][0]["id"] == "v1.0.0"
    library = h2.EditLibrary(paths.memory / "edit_library.json")
    assert len(library.entries) == probes and any(e["adopted"] for e in library.entries)
    sealed = g.seal(hid, fixed, out["strategy_file"])                    # the repaired version earns its one sealed test
    assert sealed["verdict"] in ("pass", "fail") and ledger.sealed_used(hid)
    with pytest.raises(lg.SealedPeriodUsed):
        rv.RevisionEngine(g, ledger, paths).revise(hid, fixed)


def test_noise_cannot_be_revised_into_a_pass_and_the_cap_closes_the_idea(tmp_path):
    paths, ledger, g, hid = desk(tmp_path, market(seed=1, drift=(0.0,) * 6), gt.Criteria(max_revisions=3))
    base = crippled()
    g.develop(hid, base, "strategies/trend_fix.json")
    out = rv.RevisionEngine(g, ledger, paths, k=1).revise(hid, base)
    assert not out["passed"] and out["stopped"] == "revision cap reached" and out["revisions_used"] == 3
    h = ledger.get(hid)
    assert h["status"] == "failed" and "still failing after 3 revisions" in h["lessons_learned"]
    assert out["variations_counted"] >= 4 and ledger.variations(hid) == out["variations_counted"]
    before = ledger.variations(hid)
    again = rv.RevisionEngine(g, ledger, paths, k=1).revise(hid, st.load_strategy(paths.root / out["strategy_file"])) \
        if ledger.get(hid)["strategy_version"] == out["best_version"] else None
    assert ledger.variations(hid) == before and (again is None or again["rounds"] == [])   # the cap holds across calls
    with pytest.raises(gt.RevisionCapReached):
        g.probe(hid, "9.9.9", {"x": crippled("9.9.9")})


def test_revision_needs_a_tested_version_and_respects_a_round_limit(tmp_path):
    paths, ledger, g, hid = desk(tmp_path, market(seed=1, drift=(0.0,) * 6))
    base = crippled()
    with pytest.raises(gt.GauntletError, match="development test"):
        rv.RevisionEngine(g, ledger, paths).revise(hid, base)
    g.develop(hid, base, "strategies/trend_fix.json")
    out = rv.RevisionEngine(g, ledger, paths, k=1).revise(hid, base, max_rounds=1)
    assert out["stopped"] == "round limit reached" and len(out["rounds"]) == 1 and out["revisions_used"] == 1
    assert ledger.get(hid)["status"] == "failed"                         # still the failed development verdict
    assert set(out["rounds"][0]["probed"][0]) >= {"label", "dimension", "kind", "description", "ops", "contrast"}


def test_a_pairs_strategy_is_refused_cleanly_and_nothing_is_counted(tmp_path):
    from valedia_trader.research_loop.pairs import PairsStrategy
    paths, ledger, g, hid = desk(tmp_path, market(seed=1, drift=(0.0,) * 6))
    s = PairsStrategy.from_spec({
        "kind": "pairs", "name": "trend_fix", "version": "1.0.0", "hypothesis": "Two related prices drift back together after they part",
        "pairs": [["A", "B"], ["C", "D"]], "signal": {"hedge": "ratio", "window": 20, "enter": 1.5, "exit": 0.5}})
    assert g.develop(hid, s, "strategies/trend_fix.json")["verdict"] == "fail"      # a failed idea: the loop would revise it
    before = ledger.variations(hid)
    with pytest.raises(st.SpecError, match="only rule strategies can be revised"):
        rv.RevisionEngine(g, ledger, paths).revise(hid, s)
    assert ledger.variations(hid) == before and not (paths.backtests / "trend_fix-revisions.json").exists()
