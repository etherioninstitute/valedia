"""Restart safety, duplicate protection, corrupt-log tolerance, plugin cap."""
import json

from fakes import POOL, FakeNimble, standard_scenario
from rh_meme_bot.app import main
from rh_meme_bot.decision_log import read_records
from test_app import _envfile, make_bot


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


# ---- final hardening round ------------------------------------------------------------
def test_corrupt_decision_log_records_do_not_crash_startup(tmp_path):
    good_pool = "0x" + "12" * 20
    (tmp_path / "dec.jsonl").write_text("\n".join([
        json.dumps({"pool": POOL, "decision": "buy"}),  # decision is a string
        json.dumps({"pool": None, "decision": {}}), json.dumps({"decision": {}}),
        json.dumps({"pool": good_pool.upper().replace("0X", "0x"), "decision": {"error": None}}),
        "{torn", ""]))
    bot, lines, _ = make_bot(tmp_path)  # must not raise
    assert any("WARNING: skipped 3 unreadable record" in ln for ln in lines)
    assert bot.seen_pools == {good_pool}  # POOL is NOT seen -> it will be decided again
    out = bot.run_once()
    assert any(o["state"]["pool_address"] == POOL for o in out)


def test_main_survives_corrupt_log_and_reports_startup_errors(tmp_path, capsys):
    env = _envfile(tmp_path)
    (tmp_path / "d.jsonl").write_text(json.dumps({"pool": POOL, "decision": "buy"}) + "\n")
    rc = main(["--headless", "--once", "--env-file", env], transport=standard_scenario(),
              nimble_transport=FakeNimble())
    assert rc == 0 and "WARNING: skipped 1 unreadable" in capsys.readouterr().out
    (tmp_path / "p.jsonl").unlink(missing_ok=True)
    (tmp_path / "p.jsonl").mkdir()  # POSITIONS_FILE is a directory: executor cannot load it
    rc = main(["--headless", "--once", "--env-file", env], transport=standard_scenario(),
              nimble_transport=FakeNimble())
    err = capsys.readouterr().err
    assert rc == 2 and err.startswith("error: startup failed:") and "Traceback" not in err


def test_plugin_engine_over_cap_is_skipped_post_hoc(tmp_path):
    bot, _, _ = make_bot(tmp_path, DECISION_TIMEOUT_S="10")

    def slow(state):
        bot._perf.advance_ms(11000)  # engine "blocks" 11s of fake time
        return {"action": "buy", "confidence": 0.99, "risk_score": 1}

    bot.engine = slow
    out = bot.run_once()
    assert all(o["final_action"] == "skip" for o in out)
    assert all("timeout" in o["decision"]["error"] for o in out)
    assert any("timeout" in r["decision"]["error"]
               for r in read_records(str(tmp_path / "dec.jsonl")))
    bot2, _, _ = make_bot(tmp_path / "b", DECISION_TIMEOUT_S="10")

    def fast(state):
        bot2._perf.advance_ms(9000)
        return {"action": "buy", "confidence": 0.99, "risk_score": 1}

    bot2.engine = fast
    assert any(o["final_action"] == "buy" for o in bot2.run_once())  # 9s < 10s cap
