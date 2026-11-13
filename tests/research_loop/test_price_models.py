import json
import sys

import numpy as np
import pandas as pd
import pytest

from valedia_trader.research_loop import price_models as pm
from valedia_trader.research_loop import tournament as tm

FAKE_WORKER = r'''
import json, os, sys
log, mode, deltas = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])
with open(log + ".starts", "a") as fh:
    fh.write("start\n")
if mode == "never_ready" or (mode == "ready_once" and os.path.exists(log + ".started")):
    print("cannot load the model", file=sys.stderr, flush=True)
    sys.exit(5)
open(log + ".started", "w").close()
print("some library chatter on stdout", flush=True)
print(json.dumps({"ready": True, "device": "fake"}), flush=True)
for line in sys.stdin:
    with open(log, "a") as fh:
        fh.write(line)
    req = json.loads(line)
    if mode == "always_die" or (mode == "die_once" and not os.path.exists(log + ".died")):
        open(log + ".died", "w").close()
        sys.exit(3)
    if mode == "error":
        print(json.dumps({"id": req["id"], "error": "the model fell over"}), flush=True)
        continue
    last = req["close"][-1]
    finals = [last * (1.0 + deltas[i % len(deltas)]) for i in range(req["paths"])]
    print(json.dumps({"id": req["id"], "final_closes": finals}), flush=True)
'''


def fake_forecaster(tmp_path, mode="ok", deltas=(0.01, -0.01, 0.0, 0.02), paths=4, name="fake-kronos"):
    script = tmp_path / "fake_worker.py"
    script.write_text(FAKE_WORKER)
    log = tmp_path / f"requests-{name}.log"
    fc = pm.KronosForecaster(name=name, paths=paths, context=300,
                             worker_argv=[sys.executable, str(script), str(log), mode, json.dumps(list(deltas))])
    return fc, log


def requests(log):
    return log.read_text().splitlines() if log.exists() else []


def market_ohlc(n=700, seed=1, symbols=("UPP", "DWN", "FLT")):
    """Daily bars on business days for three assets: open, high, low, close and volume."""
    rng = np.random.default_rng(seed)
    days = pd.bdate_range("2022-01-03", periods=n)
    close = pd.DataFrame({s: 100 * np.exp(np.cumsum(d + 0.01 * rng.standard_normal(n))) for s, d in zip(symbols, (0.002, -0.002, 0.0))},
                         index=days)
    return {"open": close * 0.999, "high": close * 1.01, "low": close * 0.99, "close": close,
            "volume": pd.DataFrame(1e6, index=days, columns=close.columns)}


def later_changed(ohlc, after, factor=3.0):
    out = {k: v.copy() for k, v in ohlc.items()}
    for v in out.values():
        v.loc[v.index > pd.Timestamp(after)] *= factor
    return out


def ledger_with_questions(tmp_path, ohlc, days, symbols=("UPP", "DWN"), horizon=5, name="t.sqlite"):
    led = tm.TournamentLedger(tmp_path / name)
    for d in days:
        led.add(tm.direction_questions(ohlc["close"], list(symbols), d, horizon, "funds", blind=True))
    return led


def test_the_request_holds_no_bar_dated_after_the_question_day(tmp_path):
    ohlc = market_ohlc()
    day = ohlc["close"].index[500]
    sent = []
    for k, data in enumerate((ohlc, later_changed(ohlc, day))):
        led = ledger_with_questions(tmp_path, data, [day], name=f"t{k}.sqlite")
        fc, log = fake_forecaster(tmp_path, name=f"run{k}")
        out = pm.ask_price_model(led, fc, data, not_before="2020-01-01")
        fc.close()
        assert out == {"asked": 2, "answered": 2, "skipped_before_cutoff": 0}
        sent.append(requests(log))
        led.close()
    assert sent[0] == sent[1] and len(sent[0]) == 2                     # every later price changed; the requests did not
    req = json.loads(sent[0][0])
    assert max(pd.Timestamp(t) for t in req["timestamps"]) == day and len(req["timestamps"]) == 300
    assert all(pd.Timestamp(t) > day for t in req["future_timestamps"]) and len(req["future_timestamps"]) == 5
    assert set(req) == {"id", "timestamps", "open", "high", "low", "close", "volume", "future_timestamps", "paths", "seed"}


def test_the_window_is_the_last_rows_up_to_the_day_with_missing_rows_dropped():
    ohlc = market_ohlc()
    idx = ohlc["close"].index
    ohlc["high"].loc[idx[450], "UPP"] = np.nan
    win = pm.window_for(ohlc, "UPP", idx[500], 400)
    assert list(win.columns) == ["open", "high", "low", "close", "volume"] and len(win) == 400
    assert win.index[-1] == idx[500] and idx[450] not in win.index and win.index[0] == idx[100]
    assert pm.window_for(ohlc, "UPP", idx[150], 400) is None                  # fewer than 200 rows
    ohlc["close"].loc[idx[500], "UPP"] = np.nan
    assert pm.window_for(ohlc, "UPP", idx[500], 400) is None                  # no bar on the day itself
    assert pm.window_for(ohlc, "NOPE", idx[500], 400) is None


def test_future_days_use_dates_only_and_fall_back_to_business_days():
    ohlc = market_ohlc(n=520)
    idx = ohlc["close"].index
    got = pm.future_days(ohlc, "UPP", idx[500], 5)
    assert list(got) == list(idx[501:506])
    assert list(pm.future_days(later_changed(ohlc, idx[500]), "UPP", idx[500], 5)) == list(got)
    tail = pm.future_days(ohlc, "UPP", idx[517], 5)                           # two dates in the data, three beyond it
    assert list(tail[:2]) == list(idx[518:520]) and list(tail[2:]) == list(pd.bdate_range(idx[519] + pd.Timedelta(days=1), periods=3))
    btc = {k: v.rename(columns={"UPP": "BTC-USD"}) for k, v in ohlc.items()}
    beyond = pm.future_days(btc, "BTC-USD", idx[-1], 3)
    assert list(beyond) == list(pd.date_range(idx[-1] + pd.Timedelta(days=1), periods=3))   # coins trade every day


def test_probability_is_the_share_of_paths_above_the_last_close_with_ties_one_half(tmp_path):
    ohlc = market_ohlc()
    day = ohlc["close"].index[500]
    fc, _ = fake_forecaster(tmp_path, deltas=(0.01, -0.01, 0.0, 0.02))
    p = fc.probability_up(pm.window_for(ohlc, "UPP", day, 300), pm.future_days(ohlc, "UPP", day, 5), seed=7)
    assert p == pytest.approx((2 + 0.5) / 4) and fc.device == "fake"
    fc.close()


def test_the_same_seed_gives_the_same_request(tmp_path):
    ohlc = market_ohlc()
    day = ohlc["close"].index[500]
    win, fut = pm.window_for(ohlc, "UPP", day, 300), pm.future_days(ohlc, "UPP", day, 5)
    fc, log = fake_forecaster(tmp_path)
    fc.probability_up(win, fut, seed=11)
    fc.probability_up(win, fut, seed=11)
    fc.probability_up(win, fut, seed=12)
    fc.close()
    a, b, c = requests(log)
    assert a == b and a != c and json.loads(c)["seed"] == 12


def test_a_question_before_the_cutoff_is_never_asked(tmp_path):
    ohlc = market_ohlc()
    idx = ohlc["close"].index
    led = ledger_with_questions(tmp_path, ohlc, [idx[400], idx[500]])
    fc, log = fake_forecaster(tmp_path)
    out = pm.ask_price_model(led, fc, ohlc, not_before=str(idx[450].date()), limit=100)
    assert out == {"asked": 2, "answered": 2, "skipped_before_cutoff": 2}
    assert all(json.loads(r)["timestamps"][-1].startswith(str(idx[500].date())) for r in requests(log))
    with pytest.raises(ValueError):
        pm.ask_price_model(led, fc, ohlc, not_before=None)
    fc.close()
    led.close()


def test_a_forecaster_is_never_asked_the_same_question_twice(tmp_path):
    ohlc = market_ohlc()
    led = ledger_with_questions(tmp_path, ohlc, [ohlc["close"].index[500]])
    fc, log = fake_forecaster(tmp_path)
    assert pm.ask_price_model(led, fc, ohlc, not_before="2020-01-01")["answered"] == 2
    assert pm.ask_price_model(led, fc, ohlc, not_before="2020-01-01") == {"asked": 0, "answered": 0, "skipped_before_cutoff": 0}
    assert len(requests(log)) == 2 and led.counts()["answers"] == {"fake-kronos": 2}
    fc.close()
    led.close()


def test_filters_pick_the_market_and_horizon_and_limit_caps_the_run(tmp_path):
    ohlc = market_ohlc()
    idx = ohlc["close"].index
    led = ledger_with_questions(tmp_path, ohlc, [idx[500], idx[510]])
    led.add(tm.direction_questions(ohlc["close"], ["UPP"], idx[520], 10, "funds", blind=True))
    led.add(tm.direction_questions(ohlc["close"], ["UPP"], idx[530], 5, "stocks", blind=True))
    fc, _ = fake_forecaster(tmp_path)
    assert pm.ask_price_model(led, fc, ohlc, "2020-01-01", markets=["funds"], horizon=5, limit=3)["asked"] == 3
    assert pm.ask_price_model(led, fc, ohlc, "2020-01-01", markets=["funds"], horizon=5)["asked"] == 1
    assert pm.ask_price_model(led, fc, ohlc, "2020-01-01", markets=["stocks"])["asked"] == 1
    assert pm.ask_price_model(led, fc, ohlc, "2020-01-01")["asked"] == 1                         # the 10-day one is all that is left
    fc.close()
    led.close()


def test_a_missing_window_and_a_worker_error_are_recorded_and_do_not_stop_the_run(tmp_path):
    ohlc = market_ohlc()
    day = ohlc["close"].index[500]
    led = ledger_with_questions(tmp_path, ohlc, [day], symbols=("UPP", "DWN", "FLT"))
    holed = {k: v.copy() for k, v in ohlc.items()}
    holed["open"].loc[day, "DWN"] = np.nan                                   # no full bar on the day for DWN
    fc, _ = fake_forecaster(tmp_path)
    out = pm.ask_price_model(led, fc, holed, not_before="2020-01-01")
    assert out == {"asked": 3, "answered": 2, "skipped_before_cutoff": 0}
    assert led.counts()["failed_calls"] == {"fake-kronos": 1}
    fc.close()
    bad, _ = fake_forecaster(tmp_path, mode="error", name="broken")
    assert pm.ask_price_model(led, bad, ohlc, not_before="2020-01-01") == {"asked": 3, "answered": 0, "skipped_before_cutoff": 0}
    assert led.counts()["failed_calls"]["broken"] == 3
    bad.close()
    led.close()


def test_a_dead_worker_is_restarted_once(tmp_path):
    ohlc = market_ohlc()
    day = ohlc["close"].index[500]
    win, fut = pm.window_for(ohlc, "UPP", day, 300), pm.future_days(ohlc, "UPP", day, 5)
    fc, log = fake_forecaster(tmp_path, mode="die_once")
    assert fc.probability_up(win, fut, seed=1) == pytest.approx(0.625)       # died on the first try, answered on the second
    assert len(requests(log)) == 2
    fc.close()
    dead, dlog = fake_forecaster(tmp_path, mode="always_die", name="dead")
    with pytest.raises(RuntimeError, match="worker"):
        dead.probability_up(win, fut, seed=1)
    assert len(requests(dlog)) == 2                                          # one restart, not a loop
    led = ledger_with_questions(tmp_path, ohlc, [day])
    assert pm.ask_price_model(led, dead, ohlc, not_before="2020-01-01")["answered"] == 0
    dead.close()
    led.close()


def test_dense_questions_never_overlap_one_symbols_windows_and_sampling_is_reproducible():
    close = market_ohlc()["close"]
    idx = close.index
    qs = pm.dense_questions(close, ["UPP", "DWN", "FLT"], idx[300], idx[600], horizon=5, market="funds")
    assert qs and all(q.kind == "blind history" and q.horizon == 5 for q in qs)
    for sym in ("UPP", "DWN", "FLT"):
        pos = sorted(idx.get_loc(pd.Timestamp(q.asked_on)) for q in qs if q.symbol == sym)
        assert pos[0] == 300 and pos[-1] <= 600 and all(b - a >= 5 for a, b in zip(pos, pos[1:]))
    one = pm.dense_questions(close, ["UPP", "DWN", "FLT"], idx[300], idx[600], 5, "funds", per_day=1, seed=3)
    again = pm.dense_questions(close, ["UPP", "DWN", "FLT"], idx[300], idx[600], 5, "funds", per_day=1, seed=3)
    assert [q.to_row() for q in one] == [q.to_row() for q in again]
    assert len(one) == len({q.asked_on for q in qs}) and len({q.asked_on for q in one}) == len(one)


def test_a_worker_that_cannot_start_stops_the_run_after_two_failed_starts(tmp_path):
    ohlc = market_ohlc()
    idx = ohlc["close"].index
    led = ledger_with_questions(tmp_path, ohlc, [idx[500], idx[510]])          # four questions
    fc, log = fake_forecaster(tmp_path, mode="ready_once")
    assert pm.ask_price_model(led, fc, ohlc, "2020-01-01", limit=1)["answered"] == 1
    fc.close()                                                                # every later start fails
    with pytest.raises(pm.WorkerUnavailable, match="start"):
        pm.ask_price_model(led, fc, ohlc, "2020-01-01")
    assert len((tmp_path / "requests-fake-kronos.log.starts").read_text().splitlines()) == 3   # one good start, two failed
    assert led.counts()["answers"] == {"fake-kronos": 1} and led.counts()["failed_calls"] == {}  # the rest left unasked
    never, _ = fake_forecaster(tmp_path, mode="never_ready", name="never")
    with pytest.raises(pm.WorkerUnavailable, match="cannot load the model"):
        pm.ask_price_model(led, never, ohlc, "2020-01-01")
    assert len((tmp_path / "requests-never.log.starts").read_text().splitlines()) == 2
    assert "never" not in led.counts()["answers"] and "never" not in led.counts()["failed_calls"]
    led.close()


def test_ask_kronos_settles_only_the_questions_of_its_own_market(tmp_path, monkeypatch):
    days = pd.date_range("2023-01-01", periods=800)                           # coins trade every calendar day
    rng = np.random.default_rng(4)
    btc = 100 * np.exp(np.cumsum(0.02 * rng.standard_normal(len(days))))
    spy = pd.Series(100 * np.exp(np.cumsum(0.01 * rng.standard_normal(len(days)))), index=days).where(days.dayofweek < 5)
    close = pd.DataFrame({"BTC-USD": btc, "SPY": spy}, index=days)
    bars = {"open": close * 0.999, "high": close * 1.01, "low": close * 0.99, "close": close, "volume": close * 0 + 1e6}
    folder = tmp_path / "artifacts" / "ohlc_funds"
    folder.mkdir(parents=True)
    for f, frame in bars.items():
        frame.to_parquet(folder / f"{f}.parquet")
    day = next(d for d in days[-40:] if d.dayofweek == 0)
    ledger_file = tmp_path / "t.sqlite"
    led = tm.TournamentLedger(ledger_file)
    assert led.add(tm.direction_questions(close, ["BTC-USD"], day, 5, "crypto", blind=True)) == 1
    led.close()
    fc, _ = fake_forecaster(tmp_path)
    monkeypatch.setattr(pm, "KronosForecaster", lambda **kw: fc)
    assert pm.main(["--root", str(tmp_path), "ask-kronos", "--universe", "funds", "--horizon", "5", "--not-before", str(day.date()),
                    "--ledger", str(ledger_file)]) == 0
    led = tm.TournamentLedger(ledger_file)
    on_day = {(q["market"], q["symbol"]) for q in led.questions() if q["asked_on"] == str(day.date())}
    settled = {(r["market"], r["symbol"]) for r in led.settled() if r["asked_on"] == str(day.date())}
    assert {("funds", "BTC-USD"), ("crypto", "BTC-USD")} <= on_day
    assert ("funds", "BTC-USD") in settled and ("crypto", "BTC-USD") not in settled    # five calendar days, not five weekdays
    led.close()
