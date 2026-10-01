"""Nimble failure containment: per-pool retry spacing + global circuit breaker.

Fake clock (breaker) and fake chain head (retry blocks); no network, no real Nimble.
"""
from datetime import datetime, timezone

import pytest

from fakes import FACTORY, FakeClock, FakeNimble
from fakes_v4 import dual_scenario
from rh_meme_bot.app import Bot
from rh_meme_bot.backoff import ENGINE_COOLDOWN, EngineBreaker
from rh_meme_bot.chain import ChainReader
from rh_meme_bot.config import ConfigError, load_config
from rh_meme_bot.dashboard import PlainSink
from rh_meme_bot.decision_engine import engine_from_config
from rh_meme_bot.decision_log import DecisionLog, read_records
from rh_meme_bot.executor import PaperExecutor
from rh_meme_bot.rpc import RpcClient
from rh_meme_bot.v4 import V4Reader

FIXED = lambda: datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)  # noqa: E731


class Mono:
    """Fake monotonic clock for the breaker."""

    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def make(tmp_path, rpc, **env):
    perf, mono = FakeClock(), Mono()
    nimble = FakeNimble(clock=perf, step_ms=50)
    cfg = load_config(env={"LOOKBACK_BLOCKS": "50", "POSITIONS_FILE": str(tmp_path / "p.jsonl"),
                           "DECISIONS_LOG": str(tmp_path / "d.jsonl"), **env})
    client = RpcClient(cfg.rpc_url, transport=rpc, sleep=lambda s: None)
    reader = ChainReader(client, cfg.factory_address, cfg.chain_id)
    v4 = V4Reader(client, cfg.v4_pool_manager, cfg.v4_state_view, cfg.max_block_range)
    lines: list = []
    bot = Bot(cfg, reader, PaperExecutor(cfg.positions_file), engine_from_config(cfg, nimble, perf),
              PlainSink(lines.append), clock=FIXED, decision_log=DecisionLog(cfg.decisions_log),
              perf=perf, v4=v4, mono=mono)
    bot.nimble, bot.mono, bot.rpc = nimble, mono, rpc
    return bot, lines


def errors(tmp_path):
    return [(r["version"], r["recheck"], (r["decision"]["error"] or "")[:15], r["final_action"])
            for r in read_records(str(tmp_path / "d.jsonl"))]


# --- config ---------------------------------------------------------------------------------
def test_config_defaults_overrides_and_validation():
    cfg = load_config(env={})
    assert (cfg.engine_retry_blocks, cfg.engine_breaker_errors, cfg.engine_cooldown_s) == (10, 3, 30.0)
    cfg = load_config(env={"ENGINE_RETRY_BLOCKS": "0", "ENGINE_BREAKER_ERRORS": "5",
                           "ENGINE_COOLDOWN_S": "0.5"})
    assert (cfg.engine_retry_blocks, cfg.engine_breaker_errors, cfg.engine_cooldown_s) == (0, 5, 0.5)
    with pytest.raises(ConfigError) as e:
        load_config(env={"ENGINE_RETRY_BLOCKS": "-1", "ENGINE_BREAKER_ERRORS": "x",
                         "ENGINE_COOLDOWN_S": "nan"})
    msg = str(e.value)
    for key in ("ENGINE_RETRY_BLOCKS", "ENGINE_BREAKER_ERRORS", "ENGINE_COOLDOWN_S"):
        assert key in msg
    with pytest.raises(ConfigError):
        load_config(env={"ENGINE_COOLDOWN_S": "-3"})


# --- breaker unit ---------------------------------------------------------------------------
def test_breaker_trips_on_consecutive_errors_and_closes_after_cooldown():
    clock = Mono()
    b = EngineBreaker(3, 30.0, clock)
    assert [b.record(True), b.record(True)] == [False, False] and not b.is_open()
    assert b.record(True) is True and b.is_open() and b.remaining_s() == 30.0
    clock.t += 29.9
    assert b.is_open()
    clock.t += 0.2
    assert not b.is_open() and b.remaining_s() == 0.0
    assert b.record(True) is True and b.is_open() and b.trips == 2  # probe fails: reopen at once
    clock.t += 31
    assert b.record(False) is False and b.consecutive == 0  # success resets
    assert [b.record(True), b.record(True)] == [False, False]  # needs 3 again


def test_breaker_success_in_between_resets_the_count():
    b = EngineBreaker(3, 30.0, Mono())
    b.record(True), b.record(True), b.record(False), b.record(True), b.record(True)
    assert not b.is_open()


def test_breaker_can_be_disabled():
    for args in ((0, 30.0), (3, 0.0)):
        b = EngineBreaker(*args, Mono())
        assert not b.enabled
        assert [b.record(True) for _ in range(10)] == [False] * 10 and not b.is_open()


# --- per-pool retry spacing -------------------------------------------------------------------
def test_engine_error_pool_waits_retry_blocks_before_reask(tmp_path):
    bot, _ = make(tmp_path, dual_scenario(FACTORY), ENGINE_BREAKER_ERRORS="0",
                  ENGINE_RETRY_BLOCKS="10")
    rpc = bot.rpc
    bot.nimble.fail = ConnectionError("ollama down")
    assert len(bot.run_once()) == 2 and len(bot.nimble.requests) == 2  # head 1000: both error
    assert all(p.retry_at == 1010 for p in bot.rechecks.pending.values())
    for head in (1001, 1005, 1009):
        rpc.head = head
        assert bot.run_once() == [] and len(bot.nimble.requests) == 2  # backing off: no calls
    bot.nimble.fail = None
    rpc.head = 1010
    out = bot.run_once()
    assert [o["final_action"] for o in out] == ["buy", "buy"] and len(bot.nimble.requests) == 4
    assert bot.rechecks.pending == {}


def test_retry_blocks_zero_reasks_every_poll_and_error_keeps_new_retry_at(tmp_path):
    bot, _ = make(tmp_path, dual_scenario(FACTORY), ENGINE_BREAKER_ERRORS="0",
                  ENGINE_RETRY_BLOCKS="0")
    rpc = bot.rpc
    bot.nimble.fail = ConnectionError("down")
    bot.run_once()
    for head in (1001, 1002):
        rpc.head = head
        assert len(bot.run_once()) == 2
    assert len(bot.nimble.requests) == 6 and all(p.retry_at is None for p in bot.rechecks.pending.values())
    bot2, _ = make(tmp_path / "b", dual_scenario(FACTORY), ENGINE_BREAKER_ERRORS="0",
                   ENGINE_RETRY_BLOCKS="4")
    bot2.nimble.fail = ConnectionError("down")
    bot2.run_once()
    bot2.rpc.head = 1003  # first error at head 1000 -> due again at 1004
    assert bot2.run_once() == []
    bot2.rpc.head = 1004
    assert len(bot2.run_once()) == 2
    assert all(p.retry_at == 1008 for p in bot2.rechecks.pending.values())  # re-spaced from 1004


# --- circuit breaker through the bot ----------------------------------------------------------
def test_breaker_opens_logs_engine_cooldown_without_calling_and_recovers(tmp_path):
    bot, lines = make(tmp_path, dual_scenario(FACTORY), ENGINE_BREAKER_ERRORS="1",
                      ENGINE_COOLDOWN_S="30", ENGINE_RETRY_BLOCKS="10")
    rpc = bot.rpc
    bot.nimble.fail = ConnectionError("ollama down")
    out = bot.run_once()  # head 1000: v3 pool = real error trips the breaker; v4 pool = no call
    assert len(out) == 2 and len(bot.nimble.requests) == 1
    errs = [o["decision"]["error"] for o in out]
    assert "ConnectionError" in errs[0] and errs[1] == ENGINE_COOLDOWN
    assert [o["final_action"] for o in out] == ["skip", "skip"]
    assert any("engine circuit open" in ln and "30s" in ln for ln in lines)
    assert [e[2] for e in errors(tmp_path)] == ["ConnectionError", ENGINE_COOLDOWN]
    # inside the cooldown: no calls, and no repeat log line for pools already logged
    bot.mono.t += 5
    rpc.head = 1001
    assert bot.run_once() == [] and len(bot.nimble.requests) == 1 and len(errors(tmp_path)) == 2
    # cooldown over, Nimble still down: v4 pool (no backoff set for a cooldown skip) is the
    # probe; its error re-opens the breaker; v3 pool is still backing off (retry_at 1010)
    bot.mono.t += 31
    rpc.head = 1002
    out = bot.run_once()
    assert len(out) == 1 and out[0]["state"]["version"] == "v4" and out[0]["recheck"] == 1
    assert "ConnectionError" in out[0]["decision"]["error"] and len(bot.nimble.requests) == 2
    assert bot.breaker.is_open() and bot.breaker.trips == 2
    # recovered: after the cooldown and the retry blocks both pools are decided normally
    bot.nimble.fail = None
    bot.mono.t += 31
    rpc.head = 1011
    out = bot.run_once()  # v3 due (1010); v4 backing off until 1012
    assert [o["state"]["version"] for o in out] == ["v3"] and out[0]["final_action"] == "buy"
    rpc.head = 1012
    out = bot.run_once()
    assert [o["state"]["version"] for o in out] == ["v4"] and out[0]["final_action"] == "buy"
    assert not bot.breaker.is_open() and bot.breaker.consecutive == 0
    assert bot.rechecks.pending == {} and len(bot.nimble.requests) == 4


def test_default_threshold_is_three_consecutive_errors(tmp_path):
    bot, lines = make(tmp_path, dual_scenario(FACTORY), ENGINE_RETRY_BLOCKS="0")
    rpc = bot.rpc
    bot.nimble.fail = TimeoutError("hung")
    bot.run_once()  # two pools, two errors: not open yet
    assert not bot.breaker.is_open() and len(bot.nimble.requests) == 2
    rpc.head = 1001
    out = bot.run_once()  # third consecutive error opens it; the second pool is not asked
    assert len(bot.nimble.requests) == 3 and bot.breaker.is_open()
    assert [o["decision"]["error"][:7] for o in out] == ["Timeout", ENGINE_COOLDOWN[:7]]
    assert sum("engine circuit open" in ln for ln in lines) == 1


def test_cooldown_new_pool_is_logged_once_and_not_buyable(tmp_path):
    bot, _ = make(tmp_path, dual_scenario(FACTORY), ENGINE_BREAKER_ERRORS="1", ENGINE_COOLDOWN_S="60")
    bot.nimble.fail = ConnectionError("down")
    bot.run_once()
    assert not (tmp_path / "p.jsonl").exists()  # nothing bought while the engine was unavailable
    recs = read_records(str(tmp_path / "d.jsonl"))
    cool = [r for r in recs if r["decision"]["error"] == ENGINE_COOLDOWN]
    assert len(cool) == 1 and cool[0]["decision"]["action"] == "skip"
    assert cool[0]["final_action"] == "skip" and cool[0]["latency_ms"] is None
    assert cool[0]["position_id"] is None
    # a pool that ended in an engine error is retried after a restart (not "already decided")
    bot2, _ = make(tmp_path, dual_scenario(FACTORY))
    assert bot2.seen_pools == set()
