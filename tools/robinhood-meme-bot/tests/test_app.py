import json
from datetime import datetime, timezone

import pytest

from fakes import (MEME, POOL, QUOTE_ENV, RUG_POOL, FakeClock, FakeNimble, FakeRpc,
                   nimble_body, standard_scenario)
from rh_meme_bot import app as app_mod
from rh_meme_bot.app import Bot, main
from rh_meme_bot.chain import ChainReader
from rh_meme_bot.config import load_config
from rh_meme_bot.dashboard import Dashboard, PlainSink
from rh_meme_bot.decision import load_engine
from rh_meme_bot.decision_engine import engine_from_config
from rh_meme_bot.decision_log import DecisionLog, read_records
from rh_meme_bot.executor import PaperExecutor
from rh_meme_bot.rpc import RpcClient

FIXED = lambda: datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)  # noqa: E731


def make_bot(tmp_path, engine="", rpc=None, nimble=None, step_ms=120.0, **env):
    rpc = rpc or standard_scenario()
    clock = FakeClock()
    nimble = nimble or FakeNimble(clock=clock, step_ms=step_ms)
    cfg = load_config(env={"LOOKBACK_BLOCKS": "50", "QUOTE_TOKENS": QUOTE_ENV,
                           "POSITIONS_FILE": str(tmp_path / "pos.jsonl"),
                           "DECISIONS_LOG": str(tmp_path / "dec.jsonl"),
                           "DECISION_ENGINE": engine, **env})
    eng = engine_from_config(cfg, nimble, clock) if not engine else load_engine(engine, cfg)
    lines: list = []
    reader = ChainReader(RpcClient(cfg.rpc_url, transport=rpc, sleep=lambda s: None),
                         cfg.factory_address, cfg.chain_id)
    bot = Bot(cfg, reader, PaperExecutor(cfg.positions_file), eng, PlainSink(lines.append),
              clock=FIXED, decision_log=DecisionLog(cfg.decisions_log), perf=clock)
    bot.nimble = nimble
    return bot, lines, rpc


def test_end_to_end_good_pool_buys_bad_pool_skips_and_both_logged(tmp_path):
    bot, lines, rpc = make_bot(tmp_path)
    out = bot.run_once()
    by_pool = {o["state"]["pool_address"]: o for o in out}
    good, rug = by_pool[POOL], by_pool[RUG_POOL]
    assert good["final_action"] == "buy" and good["position"].simulated
    assert good["position"].entry_price == "0.0005"
    assert good["decision"]["risk_score"] == 2
    assert rug["final_action"] == "skip" and rug["position"] is None
    failing = [g["gate"] for g in rug["gates"] if not g["passed"]]
    assert {"model_action", "risk_score", "liquidity_usd", "contract_has_code",
            "owner_renounced"} <= set(failing)
    rows = [json.loads(x) for x in (tmp_path / "pos.jsonl").read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["token"] == MEME and rows[0]["simulated"] is True
    assert any("PAPER BUY #1" in ln for ln in lines)
    assert any(ln.startswith("[decision]") and "final=buy BUY *" in ln for ln in lines)
    assert any("first_fail=model_action=skip" in ln for ln in lines)
    # only read-only RPC methods were used
    assert {r["method"] for r in rpc.requests} <= {
        "eth_chainId", "eth_blockNumber", "eth_getLogs", "eth_getCode", "eth_call"}
    assert bot.run_once() == [] and len(bot.executor.positions) == 1  # no duplicates


def test_every_pool_logged_with_raw_response(tmp_path):
    bot, _, _ = make_bot(tmp_path)
    bot.run_once()
    recs = read_records(str(tmp_path / "dec.jsonl"))
    assert len(recs) == 2  # the malformed log is skipped by the watcher, not a pool
    good = next(r for r in recs if r["pool"] == POOL)
    assert good["final_action"] == "buy" and good["position_id"] == 1
    assert good["raw"]["answers"]["action"]["choice"] == "buy"
    assert good["decision"]["action"] == "buy" and good["decision"]["risk_score"] == 2
    assert good["state"]["token_address"] == MEME and good["within_budget"] is True
    assert good["latency_ms"] == pytest.approx(120.0) and len(good["gates"]) == 7
    bad = next(r for r in recs if r["pool"] == RUG_POOL)
    assert bad["final_action"] == "skip" and bad["position_id"] is None and bad["raw"]


def test_nimble_down_fails_closed_and_is_logged(tmp_path):
    fake = FakeNimble()
    fake.fail = ConnectionError("ollama not running")
    bot, lines, _ = make_bot(tmp_path, nimble=fake)
    out = bot.run_once()
    assert len(out) == 2 and all(o["final_action"] == "skip" for o in out)
    assert not (tmp_path / "pos.jsonl").exists()
    recs = read_records(str(tmp_path / "dec.jsonl"))
    assert len(recs) == 2 and all("ConnectionError" in r["decision"]["error"] for r in recs)
    assert all(r["raw"] is None for r in recs)
    assert any("error=ConnectionError" in ln for ln in lines)


def test_latency_budget_flag_and_hit_rate(tmp_path):
    bot, lines, _ = make_bot(tmp_path, step_ms=800.0)
    out = bot.run_once()
    assert all(o["within_budget"] is False for o in out)
    assert any("800ms!" in ln for ln in lines)
    snap = bot.stats.snapshot("nimble2")
    assert snap["hit_rate"] == 0.0 and snap["p50"] == pytest.approx(800.0) and snap["n"] == 2
    bot2, _, _ = make_bot(tmp_path / "b", step_ms=100.0, DECISION_LATENCY_BUDGET_MS="100")
    assert all(o["within_budget"] for o in bot2.run_once())  # boundary: 100 <= 100


def test_warmup_reports_latency_and_engine_status(tmp_path):
    bot, lines, _ = make_bot(tmp_path, step_ms=230.0)
    assert bot.stats.snapshot("m")["warm"] is False  # cold until warmed
    bot.start()
    assert any("warmup 230.0ms ok" in ln for ln in lines)
    assert any(ln.startswith("[engine]") and "warm" in ln and "nimble2" in ln for ln in lines)
    assert len(bot.nimble.requests) == 1 and bot.stats.warm
    assert bot.stats.samples == []  # warmup latency is not counted in decision stats
    bot.nimble.fail = TimeoutError("slow")
    bot.start()
    assert any("warmup" in ln and "FAILED" in ln for ln in lines) and not bot.stats.warm


def test_stub_engine_skips_everything_but_still_logs(tmp_path):
    bot, _, _ = make_bot(tmp_path, engine="stub")
    out = bot.run_once()
    assert all(o["final_action"] == "skip" for o in out)
    recs = read_records(str(tmp_path / "dec.jsonl"))
    assert len(recs) == 2 and all(r["raw"] is None for r in recs)


def test_plugin_without_risk_score_cannot_buy(tmp_path):
    bot, _, _ = make_bot(tmp_path, engine="plugin_engine:always_buy")
    out = bot.run_once()
    good = next(o for o in out if o["state"]["pool_address"] == POOL)
    assert good["final_action"] == "skip"
    assert [g["gate"] for g in good["gates"] if not g["passed"]] == ["risk_score"]
    for eng in ("plugin_engine:bad_output", "plugin_engine:explode"):
        b, lines, _ = make_bot(tmp_path / eng.split(":")[1], engine=eng)
        assert all(o["final_action"] == "skip" for o in b.run_once())


def test_state_build_failure_is_logged_as_skip(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("rpc exploded")
    monkeypatch.setattr(app_mod, "build_state", boom)
    bot, _, _ = make_bot(tmp_path)
    out = bot.run_once()
    assert len(out) == 2 and all(o["final_action"] == "skip" for o in out)
    recs = read_records(str(tmp_path / "dec.jsonl"))
    assert len(recs) == 2 and "state_error" in recs[0]["decision"]["error"]
    assert bot.nimble.requests == []  # no model call without facts


def test_executor_failure_does_not_crash(tmp_path):
    bot, _, _ = make_bot(tmp_path)

    class Broken:
        def buy(self, *a):
            raise OSError("disk full")
    bot.executor = Broken()
    out = bot.run_once()
    good = next(o for o in out if o["state"]["pool_address"] == POOL)
    assert good["final_action"] == "skip" and good["gates"][-1]["gate"] == "executor"
    assert read_records(str(tmp_path / "dec.jsonl"))[0]["position_id"] is None


def test_rpc_outage_is_reported_not_raised(tmp_path):
    rpc = FakeRpc()
    rpc.fail_next = [ConnectionError("down")] * 20
    bot, lines, _ = make_bot(tmp_path, rpc=rpc)
    assert bot.run_once() == [] and any("RPC error" in ln for ln in lines)


def _envfile(tmp_path, extra=""):
    f = tmp_path / "e.env"
    f.write_text(f"LOOKBACK_BLOCKS=50\nQUOTE_TOKENS={QUOTE_ENV}\n"
                 f"POSITIONS_FILE={tmp_path / 'p.jsonl'}\nDECISIONS_LOG={tmp_path / 'd.jsonl'}\n"
                 + extra)
    return str(f)


def test_main_headless_once_and_failures(tmp_path, capsys):
    env = _envfile(tmp_path)
    rc = main(["--headless", "--once", "--env-file", env], transport=standard_scenario(),
              nimble_transport=FakeNimble())
    out = capsys.readouterr().out
    assert rc == 0 and "[launch]" in out and "BUY *" in out and "[engine]" in out
    assert "warmup" in out and len(read_records(str(tmp_path / "d.jsonl"))) == 2
    assert main(["--headless", "--once", "--env-file", env], transport=FakeRpc(chain_id=1),
                nimble_transport=FakeNimble()) == 2
    assert "chain id 1" in capsys.readouterr().err
    (tmp_path / "x").mkdir()
    env2 = _envfile(tmp_path / "x", "NIMBLE_MODEL=nimble\n")
    assert main(["--headless", "--once", "--env-file", env2], transport=standard_scenario(),
                nimble_transport=FakeNimble()) == 2
    assert "not a Nimble 2 tag" in capsys.readouterr().err


def test_dashboard_renders_panels_buy_highlight_and_engine_status(tmp_path):
    from rich.console import Console
    dash = Dashboard(Console(width=200, record=True, force_terminal=False))
    bot, _, _ = make_bot(tmp_path)
    bot.sink = dash
    bot.start()
    bot.run_once()
    dash.console.print(dash.render())
    text = dash.console.export_text()
    assert "New pool launches" in text and "Decisions" in text and "Action log" in text
    assert "BUY ★" in text and "PAPER ONLY" in text and "PAPER BUY" in text
    assert "$60,000" in text and "owner:R code:Y" in text and "owner:N code:N" in text
    assert "p50" in text and "budget 500ms" in text and "warm" in text
    assert "model_action=skip" in text  # first failing gate shown


def _ids(path):
    return [json.loads(x)["id"] for x in path.read_text().splitlines()]


def test_restart_inside_lookback_does_not_rebuy_or_redecide(tmp_path):
    make_bot(tmp_path)[0].run_once()
    bot2, lines, _ = make_bot(tmp_path)  # same files, fresh rpc/engine = restart
    assert bot2.run_once() == []
    assert any("already decided" in ln for ln in lines) and bot2.nimble.requests == []
    assert len(read_records(str(tmp_path / "dec.jsonl"))) == 2  # still one line per decision
    assert _ids(tmp_path / "pos.jsonl") == [1]


def test_redecide_hits_duplicate_pool_gate_and_logs_it(tmp_path):
    make_bot(tmp_path)[0].run_once()
    bot2, _, _ = make_bot(tmp_path)
    bot2.redecide, bot2.seen_pools = True, set()
    out = bot2.run_once()
    good = next(o for o in out if o["state"]["pool_address"] == POOL)
    assert good["final_action"] == "skip" and good["position"] is None
    assert good["gates"][-1]["gate"] == "duplicate_pool" and not good["gates"][-1]["passed"]
    recs = read_records(str(tmp_path / "dec.jsonl"))
    assert len(recs) == 4
    dup = [r for r in recs if any(g["gate"] == "duplicate_pool" for g in r["gates"])]
    assert len(dup) == 1 and dup[0]["final_action"] == "skip" and dup[0]["position_id"] is None
    assert _ids(tmp_path / "pos.jsonl") == [1]  # never [1, 1]


def test_main_redecide_flag_is_wired(tmp_path, capsys):
    env = _envfile(tmp_path)
    args = ["--headless", "--once", "--env-file", env]
    main(args, transport=standard_scenario(), nimble_transport=FakeNimble())
    capsys.readouterr()
    main(args, transport=standard_scenario(), nimble_transport=FakeNimble())
    assert "already decided" in capsys.readouterr().out
    main(args + ["--redecide"], transport=standard_scenario(), nimble_transport=FakeNimble())
    assert "[decision]" in capsys.readouterr().out
    assert len(read_records(str(tmp_path / "d.jsonl"))) == 4
    assert _ids(tmp_path / "p.jsonl") == [1]


def test_errored_decisions_are_retried_after_restart(tmp_path):
    down = FakeNimble()
    down.fail = ConnectionError("ollama not running")
    make_bot(tmp_path, nimble=down)[0].run_once()
    out = make_bot(tmp_path)[0].run_once()  # Ollama is back
    assert any(o["final_action"] == "buy" for o in out)


def test_once_without_headless_renders_dashboard_once(tmp_path, capsys):
    rc = main(["--once", "--env-file", _envfile(tmp_path)], transport=standard_scenario(),
              nimble_transport=FakeNimble())
    out = capsys.readouterr().out
    assert rc == 0 and out.count("New pool launches") == 1 and "BUY" in out


def test_non_json_body_logged_as_raw_text(tmp_path):
    bot, _, _ = make_bot(tmp_path, nimble=FakeNimble(respond=lambda p: ["not", "a", "dict"]))
    bot.run_once()
    recs = read_records(str(tmp_path / "dec.jsonl"))
    assert recs and all(r["raw"] == '["not", "a", "dict"]' for r in recs)
