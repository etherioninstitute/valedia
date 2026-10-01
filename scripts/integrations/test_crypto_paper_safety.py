#!/usr/bin/env python3
"""CE-1 — Ornith fail-closed gate on the crypto paper desk. Fixture only.

Run: python3 test_crypto_paper_safety.py
"""
import json
import os
import subprocess
import sys
import tempfile
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)

# Unit tests assert git-default OFF without Burton's ~/.openclaw/.env flips.
os.environ["OPENCLAW_SKIP_ENV"] = "1"

import crypto_paper as cp
import crypto_paper_indicators as ind
import crypto_paper_run as cpr
import crypto_paper_safety as safety
import forward_test_ledger as ftl
import valedia_config as vc

RULES = os.path.join(ROOT, "data", "crypto-paper", "rules.json")
BARS = os.path.join(ROOT, "data", "crypto-paper", "bars_safety_pass.json")


def _banned(src):
    for s in (
        "create_order(", "cancel_order(", "withdraw(",
        "import ccxt", "from ccxt", "import requests",
        "11435", "privateKey", "api_secret",
    ):
        assert s not in src, s
    assert "--live\"" not in src
    assert "add_argument(\"--live\")" not in src


def _echo(payload, *, ok=True, model="ornith-1.5-k-m", drop=()):
    text = payload["messages"][0]["content"]
    blob = json.loads(text[text.rfind("\n") + 1:])
    ev = {}
    failed = []
    for cond in blob["conditions"]:
        if cond["id"] in drop:
            continue
        ev[cond["id"]] = {"pass": bool(ok), "value": cond["actual"]}
        if not ok:
            failed.append(cond["id"])
    return json.dumps({
        "pass": bool(ok) and not drop,
        "bias": blob["bias"],
        "failed_conditions": failed,
        "evidence": ev,
        "model": model,
    })


def test_indicators_and_rules():
    assert ind.ema([1, 2, 3, 4], 3) == 3.0
    assert ind.sma([1, 2, 3, 4, 5], 5) == 3.0
    up = list(range(1, 20))
    assert ind.rsi(up, 14) == 100.0
    assert ind.vwap([{"high": 3, "low": 1, "close": 2, "volume": 10}], 1) == 2.0
    rules = safety.load_rules(RULES)
    assert rules["mode"] == "PAPER/SHADOW"
    assert rules["watchlist"] == ["BTC-USDT", "ETH-USDT"]
    assert rules["model"] == "ornith-1.5-k-m"
    bars = cp.load_fixture(BARS)
    snap = ind.snapshot(bars, rules["indicators"])
    assert snap["ema_fast"] > snap["ema_slow"]
    assert snap["close"] > snap["sma"]
    assert snap["close"] > snap["vwap"]
    assert snap["rsi"] < 70
    flat = ind.macd(list(range(1, 11)), fast=3, slow=5, signal=3)
    assert flat["line"] == flat["signal"] and flat["hist"] == flat["line"] - flat["signal"]
    assert abs(flat["line"] - 1.0) < 1e-9 and flat["hist"] == 0.0
    assert ind.macd(list(range(1, 7)), fast=3, slow=5, signal=3) is None
    assert snap["macd_line"] > 0
    assert snap["macd_hist"] > 0
    assert snap["macd_line"] > snap["macd_signal"]
    assert any(s.get("fn") == "macd" for s in rules["indicators"])
    assert rules["tick_assist"] == "qwen/qwen3.8-27b"
    assert rules["model"] == "ornith-1.5-k-m"
    for name in ("crypto_paper_indicators.py", "crypto_paper_safety.py", "crypto_paper_run.py"):
        _banned(open(os.path.join(HERE, name)).read())
    assert "ornith-1.5-k-m" in open(os.path.join(HERE, "crypto_paper_safety.py")).read()
    assert vc.VALEDIA_VERSION == "0.74.1"
    assert vc.CRYPTO_PAPER_MODE == "PAPER/SHADOW"
    assert vc.CRYPTO_PAPER_SAFETY == "PAPER/SHADOW+ORNITH"
    assert vc.WEATHER_LIVE is False
    assert "OIL_BRACKET" in vc.LIVE_EXEC_FENCED
    row = {r["name"]: r for r in vc.controls_snapshot()["items"]}["CRYPTO_PAPER_SAFETY"]
    assert row["value"] == "PAPER/SHADOW+ORNITH"
    assert row["source"] == "const"


def _run(post_fn, bars=BARS):
    d = tempfile.mkdtemp(prefix="ce1_")
    log = os.path.join(d, "safety-check-log.json")
    out = os.path.join(d, "equity.json")
    dec = safety.run_safety(
        fixture_path=bars, rules_path=RULES, log_path=log, out_path=out, post_fn=post_fn,
    )
    return dec, log, out


def test_pass_paper_fill_only():
    dec, log, out = _run(lambda p: _echo(p, ok=True))
    assert dec["pass"] is True
    assert dec["reason"] == "pass"
    assert dec["model"] == "ornith-1.5-k-m"
    assert dec["create_order"] is False
    assert dec["live"] is False
    assert dec["orderPlaced"] is False
    fill = dec["would_have_fill"]
    assert fill["source"] == "paper" and fill["side"] == "buy" and fill["qty"] > 0
    saved = json.load(open(out))
    assert saved["n_fills"] == 1 and saved["create_order"] is False and saved["live"] is False
    row = json.load(open(log))["trades"][-1]
    assert row["pass"] is True and row["would_have_fill"]["source"] == "paper"
    assert row["create_order"] is False


def test_ornith_refuse_and_disagree():
    dec, _log, out = _run(lambda p: _echo(p, ok=False))
    assert dec["pass"] is False and dec["reason"] == "ornith_refuse"
    assert dec["would_have_fill"] is None and not os.path.exists(out)

    def up_bars():
        d = tempfile.mkdtemp(prefix="ce1_up_")
        path = os.path.join(d, "up.json")
        json.dump({"pair": "BTC-USDT", "bars": cp.synthetic_bars(n_down=0, n_up=40, n_down2=0)}, open(path, "w"))
        return path

    dec, _log, out = _run(lambda p: _echo(p, ok=True), bars=up_bars())
    assert dec["pass"] is False and dec["reason"] == "local_disagree"
    assert dec["would_have_fill"] is None and not os.path.exists(out)
    assert "rsi_not_chase" in dec["failed_conditions"]


def test_ornith_down_malformed_qwen_missing():
    def boom(_payload):
        raise urllib.error.URLError("down")

    dec, _log, out = _run(boom)
    assert dec["pass"] is False and dec["reason"] == "ornith_down" and dec["model"] == "down"
    assert dec["would_have_fill"] is None and not os.path.exists(out)

    dec, _log, _out = _run(lambda _p: "not json")
    assert dec["pass"] is False and dec["reason"] == "malformed"

    dec, _log, _out = _run(lambda p: _echo(p, ok=True, model="qwen/qwen3.8-27b"))
    assert dec["pass"] is False and dec["reason"] == "model_rejected"
    assert dec["would_have_fill"] is None

    dec, _log, _out = _run(lambda p: _echo(p, ok=True, drop=("rsi_not_chase",)))
    assert dec["pass"] is False and dec["reason"] == "missing_condition"


def test_no_entry_does_not_fill():
    called = []

    def post(payload):
        called.append(1)
        return _echo(payload, ok=True)

    d = tempfile.mkdtemp(prefix="ce1_down_")
    path = os.path.join(d, "down.json")
    json.dump({"pair": "BTC-USDT", "bars": cp.synthetic_bars(n_down=40, n_up=0, n_down2=0, start_px=100)}, open(path, "w"))
    dec, _log, out = _run(post, bars=path)
    assert called == []
    assert dec["pass"] is False and dec["reason"] == "no_entry"
    assert dec["would_have_fill"] is None and not os.path.exists(out)


def _tick(qwen_post, ornith_post, **kw):
    d = kw.get("dir") or tempfile.mkdtemp(prefix="ce2_")
    log = os.path.join(d, "tick-check-log.json")
    out = os.path.join(d, "equity.json")
    decisions = kw.get("decisions") or os.path.join(d, "decisions.jsonl")
    dec = cpr.run_tick(
        fixture_path=kw.get("bars", BARS), rules_path=RULES, log_path=log, out_path=out,
        post_fn=ornith_post, qwen_post=qwen_post, try_qwen=kw.get("try_qwen", True),
        decision_path=decisions,
    )
    return dec, log, out


def test_tick_pass_fail_and_qwen_down():
    def rank_eth_first(_payload):
        return json.dumps({"pass": True, "model": "qwen/qwen3.8-27b", "rank": ["ETH-USDT", "BTC-USDT"]})

    calls = []

    def ornith(payload):
        calls.append(payload["model"])
        return _echo(payload, ok=True)

    dec, log, out = _tick(rank_eth_first, ornith)
    assert calls == ["ornith-1.5-k-m"]
    assert dec["pass"] is True and dec["reason"] == "pass"
    assert dec["model"] == "ornith-1.5-k-m"
    assert "qwen" not in dec["model"].lower()
    assert dec["qwen_assist"]["ok"] is True and dec["qwen_assist"]["picked"] == "BTC-USDT"
    assert dec["qwen_assist"]["reason"] == "ranked"
    fill = dec["would_have_fill"]
    assert fill["source"] == "paper" and fill["side"] == "buy"
    assert dec["create_order"] is False and dec["live"] is False and dec["orderPlaced"] is False
    row = json.load(open(log))["trades"][-1]
    assert row["tick"] is True and row["would_have_fill"]["source"] == "paper"
    assert row["qwen_assist"]["model"] == "qwen/qwen3.8-27b"
    assert json.load(open(out))["n_fills"] == 1 and json.load(open(out))["create_order"] is False

    dec, _log, out = _tick(rank_eth_first, lambda p: _echo(p, ok=False))
    assert dec["pass"] is False and dec["reason"] == "ornith_refuse"
    assert dec["would_have_fill"] is None and not os.path.exists(out)

    calls.clear()

    def qwen_down(_payload):
        raise urllib.error.URLError("qwen down")

    dec, _log, out = _tick(qwen_down, ornith)
    assert calls == ["ornith-1.5-k-m"]
    assert dec["qwen_assist"]["ok"] is False and dec["qwen_assist"]["reason"] == "qwen_down"
    assert dec["pass"] is True and dec["model"] == "ornith-1.5-k-m"
    assert dec["would_have_fill"]["source"] == "paper"

    def qwen_says_pass(_payload):
        return json.dumps({"pass": True, "model": "qwen/qwen3.8-27b"})

    dec, _log, out = _tick(qwen_says_pass, lambda p: _echo(p, ok=False))
    assert dec["pass"] is False and dec["reason"] == "ornith_refuse"
    assert dec["qwen_assist"]["reason"] == "qwen_malformed"
    assert dec["would_have_fill"] is None and not os.path.exists(out)


def _fee_fields(row):
    for key in ("fee_table", "fee_pct", "slip_pct", "entry_fee", "side", "qty", "source", "reason"):
        assert key in row, key
    assert row["fee_table"] == "CRYPTO_FEE_PCT"
    assert row["fee_pct"] == vc.CRYPTO_FEE_PCT
    assert row["slip_pct"] == vc.CRYPTO_SLIPPAGE_PCT
    assert row["source"] == "crypto_paper"
    assert row["type"] == "decision"
    assert row["create_order"] is False
    assert row["live"] is False
    assert row["counts_toward_kelly"] is False
    assert ftl._is_non_real_source(row["source"])


def test_cli_tick_dead_ornith():
    d = tempfile.mkdtemp(prefix="ce2_cli_")
    log = os.path.join(d, "tick-check-log.json")
    out = os.path.join(d, "equity.json")
    decisions = os.path.join(d, "decisions.jsonl")
    rc = cpr.main([
        "--tick", "--no-qwen", "--fixture", BARS, "--rules", RULES,
        "--safety-log", log, "--out", out, "--decision-log", decisions,
        "--ornith-url", "http://127.0.0.1:9/v1/chat/completions",
        "--ornith-timeout", "2",
    ])
    assert rc == 0
    row = json.load(open(log))["trades"][-1]
    assert row["tick"] is True and row["reason"] == "ornith_down" and row["pass"] is False
    assert row["qwen_assist"]["reason"] == "off"
    assert row["create_order"] is False and row["would_have_fill"] is None
    assert row["model"] == "down"
    assert not os.path.exists(out)
    logged = cp.read_decisions(decisions)
    assert len(logged) == 1
    _fee_fields(logged[0])
    assert logged[0]["reason"] == "ornith_down"
    assert logged[0]["filled"] is False
    assert logged[0]["would_have_pnl"] is None
    assert logged[0]["entry_fee"] > 0
    assert logged[0]["qty"] > 0
    assert logged[0]["side"] == "buy"


def test_cli_dead_ornith():
    d = tempfile.mkdtemp(prefix="ce1_cli_")
    log = os.path.join(d, "safety-check-log.json")
    out = os.path.join(d, "equity.json")
    rc = cpr.main([
        "--safety", "--fixture", BARS, "--rules", RULES,
        "--safety-log", log, "--out", out,
        "--ornith-url", "http://127.0.0.1:9/v1/chat/completions",
        "--ornith-timeout", "2",
    ])
    assert rc == 0
    row = json.load(open(log))["trades"][-1]
    assert row["reason"] == "ornith_down" and row["pass"] is False
    assert row["create_order"] is False and row["would_have_fill"] is None
    assert json.load(open(log))["create_order"] is False
    assert not os.path.exists(out)


def test_decisions_fee_kelly_lock_retention():
    calls = []

    def ornith(payload):
        calls.append(payload["model"])
        return _echo(payload, ok=True)

    d = tempfile.mkdtemp(prefix="ce3_")
    decisions = os.path.join(d, "decisions.jsonl")
    dec, _log, out = _tick(None, ornith, try_qwen=False, dir=d, decisions=decisions)
    assert dec["pass"] is True and dec["reason"] == "pass"
    assert dec["create_order"] is False
    row = cp.read_decisions(decisions)[-1]
    _fee_fields(row)
    assert row["filled"] is True and row["pass"] is True
    assert row["entry_fee"] > 0 and row["would_have_pnl"] is not None
    assert row["would_have_pnl"] < 0
    assert json.load(open(out))["n_fills"] == 1

    dec2, _log, out2 = _tick(None, ornith, try_qwen=False, dir=d, decisions=decisions)
    assert calls == ["ornith-1.5-k-m"]
    assert dec2["reason"] == "duplicate_tick" and dec2["pass"] is False
    assert dec2["would_have_fill"] is None and dec2["create_order"] is False
    dup = cp.read_decisions(decisions)[-1]
    _fee_fields(dup)
    assert dup["filled"] is False and dup["entry_fee"] > 0 and dup["would_have_pnl"] is None
    assert json.load(open(out2))["n_fills"] == 1

    d_fail = tempfile.mkdtemp(prefix="ce3_fail_")
    decf, _log, outf = _tick(None, lambda p: _echo(p, ok=False), try_qwen=False, dir=d_fail)
    refused = cp.read_decisions(decf["decision_path"])[-1]
    _fee_fields(refused)
    assert refused["reason"] == "ornith_refuse" and refused["filled"] is False
    assert refused["entry_fee"] > 0 and refused["would_have_pnl"] is None
    assert not os.path.exists(outf)

    fd, led = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    orig = ftl.LEDGER
    ftl.LEDGER = led
    try:
        eid = ftl.record_entry(
            "CRYPTO_PAPER", row["ticker"], row["side"], row["entry_price"], row["qty"],
            venue="paper", source=row["source"], entry_fee=row["entry_fee"],
        )
        ftl._append({
            "type": "settlement", "entry_id": eid, "ticker": row["ticker"],
            "strategy": "CRYPTO_PAPER", "result": "WON", "won": True,
            "filled_contracts": row["qty"], "realized_pnl_net": 500.0,
            "source": row["source"],
        })
        ftl._append(dict(row))
        tr = ftl.real_track_record()
        assert tr["n"] == 0 and tr["net"] == 0.0, tr
        stats = ftl.category_stats()
        assert stats.get("CRYPTO_PAPER", {}).get("n", 0) == 0
        assert vc._track_record_mult() == vc.KELLY_UNPROVEN_MULT
    finally:
        ftl.LEDGER = orig
        os.remove(led)

    paper_src = open(os.path.join(HERE, "crypto_paper.py")).read()
    run_src = open(os.path.join(HERE, "crypto_paper_run.py")).read()
    assert "forward_test_ledger" not in paper_src
    assert "forward_test_ledger" not in run_src
    assert "create_order(" not in paper_src
    assert "create_order(" not in run_src
    assert "import requests" not in paper_src and "import requests" not in run_src

    kept = os.path.join(d, "keep.jsonl")
    for i in range(5):
        cp.append_decision({"n": i, "reason": "keep", "fee_table": "CRYPTO_FEE_PCT",
                            "fee_pct": vc.CRYPTO_FEE_PCT, "entry_fee": 0, "side": "buy",
                            "qty": 0}, kept, keep=3)
    tail = cp.read_decisions(kept)
    assert [r["n"] for r in tail] == [2, 3, 4]
    assert all(r["source"] == "crypto_paper" and r["create_order"] is False for r in tail)

    busy_dir = tempfile.mkdtemp(prefix="ce3_busy_")
    busy_log = os.path.join(busy_dir, "decisions.jsonl")
    proc = subprocess.Popen(
        [sys.executable, "-c",
         "import fcntl,sys,time\n"
         "fh=open(sys.argv[1],'a+')\n"
         "fcntl.flock(fh.fileno(), fcntl.LOCK_EX)\n"
         "sys.stdout.write('held\\n'); sys.stdout.flush()\n"
         "time.sleep(30)\n",
         busy_log + ".lock"],
        stdout=subprocess.PIPE,
    )
    try:
        assert proc.stdout.readline().strip() == b"held"
        called = []
        decb = cpr.run_tick(
            fixture_path=BARS, rules_path=RULES, try_qwen=False,
            decision_path=busy_log, post_fn=lambda p: called.append(p) or _echo(p, ok=True),
            log_path=os.path.join(busy_dir, "skip.json"),
            out_path=os.path.join(busy_dir, "eq.json"),
        )
        assert called == []
        assert decb["reason"] == "tick_busy" and decb["pass"] is False
        assert decb["create_order"] is False and decb["would_have_fill"] is None
        brow = cp.read_decisions(busy_log)[-1]
        _fee_fields(brow)
        assert brow["reason"] == "tick_busy" and brow["filled"] is False
        assert not os.path.exists(os.path.join(busy_dir, "eq.json"))
    finally:
        proc.kill()
        proc.wait(timeout=5)

    got = {r["name"]: r for r in vc.controls_snapshot()["items"]}
    assert got["CRYPTO_PAPER_TICK_AGE_S"]["source"] == "file"
    assert got["CRYPTO_PAPER_LAST_ORNITH"]["source"] == "file"
    assert got["CRYPTO_PAPER_TICK_STALE"]["source"] == "file"
    age = got["CRYPTO_PAPER_TICK_AGE_S"]["value"]
    assert age == "none" or isinstance(age, int)
    assert isinstance(got["CRYPTO_PAPER_LAST_ORNITH"]["value"], str)
    assert got["CRYPTO_PAPER_LAST_ORNITH"]["value"]
    assert isinstance(got["CRYPTO_PAPER_TICK_STALE"]["value"], bool)
    assert got["CRYPTO_PAPER_MODE"]["value"] == "PAPER/SHADOW"
    assert vc.WEATHER_LIVE is False
    assert "OIL_BRACKET" in vc.LIVE_EXEC_FENCED


if __name__ == "__main__":
    test_indicators_and_rules()
    test_pass_paper_fill_only()
    test_ornith_refuse_and_disagree()
    test_ornith_down_malformed_qwen_missing()
    test_no_entry_does_not_fill()
    test_cli_dead_ornith()
    test_tick_pass_fail_and_qwen_down()
    test_cli_tick_dead_ornith()
    test_decisions_fee_kelly_lock_retention()
    print("CE-1+CE-2+CE-3 crypto paper safety/tick: PASS")
