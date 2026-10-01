"""Bot-level v3 + v4: dual watchers, StateView verification, hooks gate, re-check window."""
import json
from datetime import datetime, timezone

from fakes import FACTORY, FakeClock, FakeNimble
from fakes_v4 import (HOOKS, MEME3, MEME4, PM, POOL4, SV, FULL_HI, FULL_LO, dual_scenario,
                      install_v4, install_weth_usdg, modify_log, set_v4_pool, v4_meme_pool)
from fakes import FakeRpc
from rh_meme_bot.app import Bot, main
from rh_meme_bot.chain import ChainReader
from rh_meme_bot.config import load_config
from rh_meme_bot.dashboard import PlainSink
from rh_meme_bot.decision_engine import engine_from_config
from rh_meme_bot.decision_log import DecisionLog, read_records
from rh_meme_bot.executor import PaperExecutor
from rh_meme_bot.rpc import RpcClient
from rh_meme_bot.v4 import V4Reader

FIXED = lambda: datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)  # noqa: E731


def make(tmp_path, rpc, **env):
    clock = FakeClock()
    nimble = FakeNimble(clock=clock, step_ms=50)
    cfg = load_config(env={"LOOKBACK_BLOCKS": "50", "POSITIONS_FILE": str(tmp_path / "p.jsonl"),
                           "DECISIONS_LOG": str(tmp_path / "d.jsonl"), **env})
    client = RpcClient(cfg.rpc_url, transport=rpc, sleep=lambda s: None)
    reader = ChainReader(client, cfg.factory_address, cfg.chain_id)
    v4 = V4Reader(client, cfg.v4_pool_manager, cfg.v4_state_view, cfg.max_block_range)
    lines: list = []
    bot = Bot(cfg, reader, PaperExecutor(cfg.positions_file), engine_from_config(cfg, nimble, clock),
              PlainSink(lines.append), clock=FIXED, decision_log=DecisionLog(cfg.decisions_log),
              perf=clock, v4=v4)
    bot.nimble = nimble
    return bot, lines


def by_token(out):
    return {o["state"]["token_address"]: o for o in out}


def test_one_v3_and_one_v4_launch_both_paper_buy(tmp_path):
    rpc = dual_scenario(FACTORY)
    bot, lines = make(tmp_path, rpc)
    bot.start()
    out = by_token(bot.run_once())
    v3, v4 = out[MEME4], out[MEME3]
    assert v3["final_action"] == v4["final_action"] == "buy"
    assert v3["state"]["version"] == "v3" and v3["state"]["pool_address"] == POOL4
    assert v4["state"]["version"] == "v4" and v4["state"]["quote_symbol"] == "ETH"
    assert v4["state"]["liquidity_usd"] > 20000 and v3["state"]["liquidity_usd"] == 60000.0
    assert v4["position"].pool == v4["state"]["pool_id"]
    recs = read_records(str(tmp_path / "d.jsonl"))
    assert sorted(r["version"] for r in recs) == ["v3", "v4"]
    v4rec = next(r for r in recs if r["version"] == "v4")
    assert v4rec["pool_record"]["hooks"] == "0x" + "00" * 20 and v4rec["recheck"] == 0
    assert {g["gate"] for g in v4rec["gates"]} >= {"v4_hooks", "v4_dynamic_fee"}
    assert any("[launch]" in ln and " v4 " in ln and "hooks:N" in ln for ln in lines)
    assert any("watchers: v3 ok | v4 ok" in ln for ln in lines)
    assert {q["method"] for q in rpc.requests} <= {
        "eth_chainId", "eth_blockNumber", "eth_getLogs", "eth_getCode", "eth_call"}


def test_stateview_failure_disables_v4_only(tmp_path):
    rpc = dual_scenario(FACTORY)
    rpc.code.pop(SV)  # StateView address wrong / not deployed
    bot, lines = make(tmp_path, rpc)
    bot.start()
    out = by_token(bot.run_once())
    assert MEME3 not in out and out[MEME4]["final_action"] == "buy"
    assert any("WARNING: v4 watcher disabled" in ln and "V4_STATE_VIEW" in ln for ln in lines)
    assert any("v4 DISABLED" in ln for ln in lines if ln.startswith("[engine]"))
    pm_logs = [q for q in rpc.requests if q["method"] == "eth_getLogs"
               and q["params"][0]["address"] == PM]
    assert pm_logs == []  # the v4 watcher never polled


def test_v4_transient_rpc_error_retries_verification(tmp_path):
    rpc = dual_scenario(FACTORY)
    bot, lines = make(tmp_path, rpc)
    rpc.fail_next = [ConnectionError("blip")] * 4  # every retry of the first getCode fails
    bot.verify_v4()
    assert bot._v4_ready is None and "retrying" in bot.health["v4"]
    rpc.fail_next = [ConnectionError("blip")] * 4  # still down at the next poll
    out = by_token(bot.run_once())
    assert MEME3 not in out and out[MEME4]["final_action"] == "buy"  # v3 unaffected
    assert not any(q["params"][0].get("address") == PM for q in rpc.requests
                   if q["method"] == "eth_getLogs")  # unverified v4 is never polled
    out = by_token(bot.run_once())  # verification retried, then both watchers run
    assert bot.health["v4"] == "ok" and out[MEME3]["final_action"] == "buy"


def test_hooks_pool_is_skipped_by_code_gate_and_not_rechecked(tmp_path):
    rpc = FakeRpc()
    install_v4(rpc)
    install_weth_usdg(rpc, FACTORY)
    v4_meme_pool(rpc, 995, hooks=HOOKS)
    bot, lines = make(tmp_path, rpc)
    out = bot.run_once()
    assert len(out) == 1 and out[0]["final_action"] == "skip"
    assert [g["gate"] for g in out[0]["gates"] if not g["passed"]] == ["v4_hooks"]
    assert out[0]["state"]["has_hooks"] is True and bot.rechecks.pending == {}
    assert any("hooks:Y" in ln for ln in lines)
    bot2, _ = make(tmp_path / "allow", rpc, ALLOWED_V4_HOOKS=HOOKS)
    assert bot2.run_once()[0]["final_action"] == "buy"


def test_recheck_turns_liquidity_skip_into_buy_and_logs_both(tmp_path):
    rpc = FakeRpc(head=1000)
    install_v4(rpc)
    install_weth_usdg(rpc, FACTORY)
    pid, liq, sqrt, tick = v4_meme_pool(rpc, 995, with_liquidity=False)
    bot, lines = make(tmp_path, rpc)
    first = bot.run_once()
    assert len(first) == 1 and first[0]["final_action"] == "skip"
    assert first[0]["state"]["liquidity_usd"] == 0.0
    assert [g["gate"] for g in first[0]["gates"] if not g["passed"]] == ["liquidity_usd"]
    assert pid in bot.rechecks.pending and any("re-checking liquidity" in ln for ln in lines)
    calls = len(bot.nimble.requests)
    rpc.head = 1003
    assert bot.run_once() == [] and len(bot.nimble.requests) == calls  # facts unchanged
    rpc.logs.append(modify_log(pid, FULL_LO, FULL_HI, liq, 1005))  # liquidity arrives
    set_v4_pool(rpc, pid, sqrt, tick, liq)
    rpc.head = 1006
    second = bot.run_once()
    assert len(second) == 1 and second[0]["final_action"] == "buy"
    assert second[0]["recheck"] == 1 and len(bot.nimble.requests) == calls + 1
    recs = [r for r in read_records(str(tmp_path / "d.jsonl")) if r["pool"] == pid]
    assert [(r["final_action"], r["recheck"]) for r in recs] == [("skip", 0), ("buy", 1)]
    assert bot.rechecks.pending == {} and any("(recheck #1)" in ln for ln in lines)
    rpc.head = 1010
    assert bot.run_once() == []  # never re-bought
    rows = [json.loads(x) for x in (tmp_path / "p.jsonl").read_text().splitlines()]
    assert [r["pool"] for r in rows] == [pid]


def test_recheck_window_expires_and_can_be_disabled(tmp_path):
    rpc = FakeRpc(head=1000)
    install_v4(rpc)
    install_weth_usdg(rpc, FACTORY)
    pid, *_ = v4_meme_pool(rpc, 995, with_liquidity=False)
    bot, lines = make(tmp_path, rpc, RECHECK_BLOCKS="10")
    bot.run_once()
    assert bot.rechecks.pending[pid].until_block == 1005
    rpc.head = 1006
    bot.run_once()
    assert bot.rechecks.pending == {} and any("recheck window over" in ln for ln in lines)
    bot2, _ = make(tmp_path / "off", rpc, RECHECK_BLOCKS="0")
    bot2.run_once()
    assert bot2.rechecks.pending == {}


def test_main_headless_one_v3_one_v4(tmp_path, capsys):
    env = tmp_path / "e.env"
    env.write_text(f"LOOKBACK_BLOCKS=50\nPOSITIONS_FILE={tmp_path / 'p.jsonl'}\n"
                   f"DECISIONS_LOG={tmp_path / 'd.jsonl'}\n")
    rc = main(["--headless", "--once", "--env-file", str(env)],
              transport=dual_scenario(FACTORY), nimble_transport=FakeNimble())
    out = capsys.readouterr().out
    assert rc == 0 and out.count("BUY *") == 2
    assert "[launch]" in out and " v3 pool=" in out and " v4 pool=" in out
    assert "watchers: v3 ok | v4 ok" in out and "warmup" in out and "FAILED" not in out


def test_dashboard_shows_version_column_hooks_flag_and_watcher_health(tmp_path):
    from rich.console import Console
    from rh_meme_bot.dashboard import Dashboard
    rpc = dual_scenario(FACTORY)
    bot, _ = make(tmp_path, rpc)
    dash = Dashboard(Console(width=220, record=True, force_terminal=False))
    bot.sink = dash
    bot.start()
    bot.run_once()
    dash.console.print(dash.render())
    text = dash.console.export_text()
    assert "ver" in text and " v3 " in text and " v4 " in text
    assert "hooks:N" in text and "watchers: v3 ok | v4 ok" in text and "BUY ★" in text


def test_recheck_engine_error_keeps_watch_and_retries(tmp_path):
    rpc = FakeRpc(head=1000)
    install_v4(rpc)
    install_weth_usdg(rpc, FACTORY)
    pid, liq, sqrt, tick = v4_meme_pool(rpc, 995, with_liquidity=False)
    bot, _ = make(tmp_path, rpc, ENGINE_RETRY_BLOCKS="1")  # error at 1003 -> re-ask from 1004
    assert bot.run_once()[0]["final_action"] == "skip"
    rpc.logs.append(modify_log(pid, FULL_LO, FULL_HI, liq, 1002))  # liquidity arrives...
    set_v4_pool(rpc, pid, sqrt, tick, liq)
    rpc.head = 1003
    bot.nimble.fail = ConnectionError("ollama restarting")  # ...but the model call fails
    out = bot.run_once()
    assert out[0]["final_action"] == "skip" and "ConnectionError" in out[0]["decision"]["error"]
    assert pid in bot.rechecks.pending  # not dropped: an engine error is not a verdict
    bot.nimble.fail = None
    rpc.head = 1004  # facts unchanged, model back: re-asked anyway
    out = bot.run_once()
    assert out[0]["final_action"] == "buy" and out[0]["recheck"] == 2
    recs = [r for r in read_records(str(tmp_path / "d.jsonl")) if r["pool"] == pid]
    assert [(r["final_action"], r["recheck"]) for r in recs] == [
        ("skip", 0), ("skip", 1), ("buy", 2)]
    assert bot.rechecks.pending == {}
    rpc.head = 1006
    assert bot.run_once() == []


def test_engine_error_watch_ends_with_window(tmp_path):
    rpc = FakeRpc(head=1000)
    install_v4(rpc)
    install_weth_usdg(rpc, FACTORY)
    pid, *_ = v4_meme_pool(rpc, 995)  # liquid pool, but Nimble is down at detection
    bot, _ = make(tmp_path, rpc, RECHECK_BLOCKS="8", ENGINE_RETRY_BLOCKS="2")
    bot.nimble.fail = ConnectionError("down")
    assert bot.run_once()[0]["decision"]["error"]
    assert pid in bot.rechecks.pending
    rpc.head = 1002  # window ends at 995 + 8 = 1003
    assert bot.run_once()[0]["recheck"] == 1  # retried although facts did not change
    rpc.head = 1004
    assert bot.run_once() == [] and bot.rechecks.pending == {}  # window over
