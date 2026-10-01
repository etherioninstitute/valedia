import json
from datetime import datetime, timezone

from fakes import MEME, POOL, QUOTE, RUG_POOL, FakeRpc, standard_scenario
from rh_meme_bot.app import Bot, build_bot, main
from rh_meme_bot.chain import ChainReader
from rh_meme_bot.config import load_config
from rh_meme_bot.dashboard import Dashboard, PlainSink
from rh_meme_bot.decision import load_engine
from rh_meme_bot.executor import PaperExecutor
from rh_meme_bot.rpc import RpcClient

FIXED = lambda: datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)  # noqa: E731


def make_bot(tmp_path, engine="", rpc=None, **env):
    rpc = rpc or standard_scenario()
    base = {"LOOKBACK_BLOCKS": "50", "MIN_LIQUIDITY": "1000", "QUOTE_TOKENS": QUOTE,
            "POSITIONS_FILE": str(tmp_path / "pos.jsonl"), "DECISION_ENGINE": engine}
    cfg = load_config(env={**base, **env})
    lines: list = []
    reader = ChainReader(RpcClient(cfg.rpc_url, transport=rpc, sleep=lambda s: None),
                         cfg.factory_address, cfg.chain_id)
    bot = Bot(cfg, reader, PaperExecutor(cfg.positions_file), load_engine(cfg.decision_engine),
              PlainSink(lines.append), clock=FIXED)
    return bot, lines, rpc


def test_headless_one_iteration_stub_engine_skips(tmp_path):
    bot, lines, _ = make_bot(tmp_path)
    out = bot.run_once()
    assert len(out) == 2 and all(o["final_action"] == "skip" for o in out)
    assert not (tmp_path / "pos.jsonl").exists()
    assert any(ln.startswith("[launch]") and POOL in ln and "price=4" in ln for ln in lines)
    assert any(ln.startswith("[decision]") and "engine=skip@0.00" in ln for ln in lines)


def test_end_to_end_buy_is_paper_only_and_gates_apply(tmp_path):
    bot, lines, rpc = make_bot(tmp_path, engine="plugin_engine:always_buy")
    out = bot.run_once()
    by_pool = {o["state"]["pool"]: o for o in out}
    good, rug = by_pool[POOL], by_pool[RUG_POOL]
    assert good["final_action"] == "buy" and good["position"].simulated
    assert good["state"]["risk_score"] == 0.0
    assert good["position"].entry_price == "4"  # 4 QUOTE per MEME (MEME is token0)
    assert rug["final_action"] == "skip" and "gated" in rug["reason"]
    rows = [json.loads(x) for x in (tmp_path / "pos.jsonl").read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["token"] == MEME and rows[0]["simulated"] is True
    assert any("PAPER BUY #1" in ln for ln in lines)
    # only read-only RPC methods were used
    assert {r["method"] for r in rpc.requests} <= {
        "eth_chainId", "eth_blockNumber", "eth_getLogs", "eth_getCode", "eth_call"}
    # second poll: nothing new, no duplicate positions
    assert bot.run_once() == [] and len(bot.executor.positions) == 1


def test_entry_price_inverts_when_quote_is_token0(tmp_path):
    bot, _, _ = make_bot(tmp_path, engine="plugin_engine:always_buy", QUOTE_TOKENS=MEME)
    # MEME declared as the quote token -> QUOTE is the target; price is MEME per QUOTE = 1/4
    state, entry = bot.build_state(bot.reader.fetch_pool_created(900, 1000)[0])
    assert state["target_token"] == QUOTE and str(entry) == "0.25"


def test_bad_engine_output_and_crash_do_not_stop_loop(tmp_path):
    for eng in ("plugin_engine:bad_output", "plugin_engine:explode"):
        bot, lines, _ = make_bot(tmp_path, engine=eng)
        out = bot.run_once()
        assert len(out) == 2 and all(o["final_action"] == "skip" for o in out)
        assert any("[decision]" in ln and "final=skip" in ln for ln in lines)


def test_rpc_outage_is_reported_not_raised(tmp_path):
    rpc = FakeRpc()
    rpc.fail_next = [ConnectionError("down")] * 20
    bot, lines, _ = make_bot(tmp_path, rpc=rpc)
    assert bot.run_once() == [] and any("RPC error" in ln for ln in lines)


def test_main_headless_once_and_chain_mismatch(tmp_path, capsys):
    envfile = tmp_path / "e.env"
    envfile.write_text(f"LOOKBACK_BLOCKS=50\nQUOTE_TOKENS={QUOTE}\nMIN_LIQUIDITY=1000\n"
                       f"POSITIONS_FILE={tmp_path / 'p.jsonl'}\n")
    rc = main(["--headless", "--once", "--env-file", str(envfile)], transport=standard_scenario())
    out = capsys.readouterr().out
    assert rc == 0 and "[launch]" in out and "[decision]" in out and "[status]" in out
    rc = main(["--headless", "--once", "--env-file", str(envfile)],
              transport=FakeRpc(chain_id=1))
    assert rc == 2 and "chain id 1" in capsys.readouterr().err


def test_dashboard_renders_three_panels(tmp_path):
    from rich.console import Console
    dash = Dashboard(Console(width=160, record=True, force_terminal=False))
    bot, _, _ = make_bot(tmp_path, engine="plugin_engine:always_buy")
    bot.sink = dash
    bot.run_once()
    dash.console.print(dash.render())
    text = dash.console.export_text()
    assert "New pool launches" in text and "Decisions" in text and "Action log" in text
    assert "PAPER BUY" in text and "PAPER ONLY" in text
