#!/usr/bin/env python3
"""calibration_ledger: synthetic decisions + fee-true settlements. No network.

Run: python3 test_calibration_ledger.py   (or pytest)
"""
import contextlib
import io
import json
import math
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ["OPENCLAW_SKIP_ENV"] = "1"

import calibration_ledger as cl  # noqa: E402

T = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
NOW = T + timedelta(days=3)


def iso(dt):
    return dt.isoformat()


def close(a, b, tol=1e-6):
    return a is not None and abs(a - b) <= tol


def write(path, rows):
    with open(path, "w") as f:
        for r in rows:
            f.write(r if isinstance(r, str) else json.dumps(r))
            f.write("\n")
    return path


def tmpdir():
    return tempfile.mkdtemp(prefix="calib_")


def entry(eid, ticker, side, ts, source="live", **kw):
    return {"type": "entry", "id": eid, "ticker": ticker, "side": side, "ts": iso(ts),
            "strategy": "S", "venue": "kalshi", "source": source, **kw}


def sett(eid, ticker, result, net, logged, source="live"):
    return {"type": "settlement", "entry_id": eid, "ticker": ticker, "result": result,
            "realized_pnl_net": net, "won": net > 0, "logged_at": iso(logged), "source": source}


def dec(ts, symbol, side, probs=None, action="buy", conf=None, error=None, src="nimble_decide:alloc"):
    ans = None if error else {"action": action, "confidence": conf, "action_probabilities": probs}
    return {"ts": iso(ts), "source": src, "state": {"symbol": symbol, "side": side},
            "answers": ans, "error": error, "label": {"code": "x"}}


def fv_rows(specs, src="nws", start=T):
    """specs: [(fv, result)] -> real entry + settlement pairs carrying fv/fv_source."""
    rows = []
    for i, (fv, res) in enumerate(specs):
        eid = "fv_%s_%d" % (src, i)
        rows.append(entry(eid, "KXW-%d" % i, "yes", start + timedelta(minutes=i),
                          fv=fv, fv_source=src))
        rows.append(sett(eid, "KXW-%d" % i, res, 0.1, start + timedelta(days=1)))
    return rows


def run_score(d, dec_rows=(), ftl_rows=(), mf_rows=(), **kw):
    return cl.score(decisions_path=write(os.path.join(d, "dec.jsonl"), dec_rows),
                    ftl_path=write(os.path.join(d, "ftl.jsonl"), ftl_rows),
                    mirofish_path=write(os.path.join(d, "mf.jsonl"), mf_rows), now=NOW, **kw)


def test_fv_exact_brier_logloss_and_unscorable():
    rows = fv_rows([(0.8, "yes"), (0.3, "no"), (60, "no")])  # 60 -> cents -> 0.6
    rows += [entry("u1", "KXW-U", "yes", T, fv=0.5, fv_source="nws"),             # unsettled
             entry("b1", "KXW-B", "yes", T, source="backfill:pnl_tracker", fv=0.5, fv_source="nws"),
             sett("b1", "KXW-B", "yes", 1.0, T + timedelta(days=1), source="backfill:pnl_tracker"),
             entry("x1", "BTC-USD", "buy", T, fv=0.5, fv_source="nws"),
             {"type": "settlement", "entry_id": "x1", "ticker": "BTC-USD", "result": "exit",
              "realized_pnl_net": 1.0, "logged_at": iso(T)},
             entry("n1", "KXW-N", "yes", T)]                                      # no fv: not a source
    st = run_score(tmpdir(), ftl_rows=rows)["fv:nws"]
    assert st["n"] == 6 and st["n_scorable"] == 3, st
    assert close(st["brier"], (0.04 + 0.09 + 0.36) / 3), st
    assert close(st["log_loss"], -(math.log(0.8) + math.log(0.7) + math.log(0.4)) / 3), st
    assert close(st["hit_rate"], 2 / 3), st
    assert st["unscorable"] == {"no_market_outcome": 1, "non_real_source": 1, "unsettled": 1}, st
    assert st["n_unscorable"] == 3


def test_reliability_bins():
    rows = fv_rows([(0.05, "no"), (0.15, "no"), (0.12, "yes"), (1.0, "yes"), (0.95, "yes")])
    st = run_score(tmpdir(), ftl_rows=rows)["fv:nws"]
    bins = st["reliability_bins"]
    assert [(b["p_lo"], b["p_hi"], b["n"]) for b in bins] == [(0.0, 0.1, 1), (0.1, 0.2, 2), (0.9, 1.0, 2)], bins
    assert close(bins[1]["avg_p"], 0.135) and close(bins[1]["freq"], 0.5), bins
    assert close(bins[2]["avg_p"], 0.975) and close(bins[2]["freq"], 1.0), bins


def test_nimble_matching_dedupe_and_malformed():
    ftl = [entry("t1", "KX-A", "yes", T + timedelta(seconds=60)),
           sett("t1", "KX-A", "yes", 0.5, T + timedelta(days=1)),
           # Market outcome for KX-B: another strategy's real position, a day earlier.
           entry("t2", "KX-B", "yes", T - timedelta(days=1)),
           sett("t2", "KX-B", "no", -0.4, T + timedelta(days=1)),
           # A backfill trade never counts, as trade or as market outcome.
           entry("t3", "KX-D", "yes", T + timedelta(seconds=30), source="backfill:pnl_tracker"),
           sett("t3", "KX-D", "yes", 0.9, T + timedelta(days=1), source="backfill:pnl_tracker"),
           # A real KX-E trade 7h after the decision: outside the trade window.
           entry("t4", "KX-E", "yes", T + timedelta(hours=7)),
           sett("t4", "KX-E", "yes", -0.2, T + timedelta(hours=8)),
           "{not json", "[1, 2]", ""]
    decs = [dec(T, "KX-A", "yes", {"buy": 0.7, "hold": 0.2, "skip": 0.1}),                  # trade, y=1
            dec(T - timedelta(seconds=60), "KX-B", "no", {"buy": 0.4, "hold": 0.3, "skip": 0.3}),  # superseded
            dec(T, "KX-B", "no", {"buy": 0.2, "hold": 0.3, "skip": 0.5}, action="skip"),       # market, y=1
            dec(T, "KX-A", "yes", None, action="hold", conf=0.9),                               # no_p_buy
            dec(T, "KX-A", "yes", error="timeout"),                                             # no_answer
            dec(T, "KX-C", "yes", {"buy": 1.2, "hold": 0.8}),                                    # p=.6, no outcome
            dec(T, "KX-D", "yes", {"buy": 0.9, "hold": 0.1}),                                    # backfill only
            "garbage line", "null",
            {"ts": iso(T), "source": "movers_scan", "answers": {"action": "buy"}}]             # not Nimble
    res = run_score(tmpdir(), dec_rows=decs, ftl_rows=ftl)
    assert list(res) == ["nimble_decide:alloc"], res
    st = res["nimble_decide:alloc"]
    assert st["n"] == 7 and st["n_scorable"] == 2 and st["n_superseded"] == 1, st
    assert close(st["brier"], (0.09 + 0.64) / 2), st
    assert close(st["log_loss"], -(math.log(0.7) + math.log(0.2)) / 2), st
    assert close(st["hit_rate"], 0.5), st
    assert st["unscorable"] == {"no_answer": 1, "no_p_buy": 1, "no_trade_no_market_outcome": 2}, st
    assert st["last_ts"] == iso(T), st
    # The out-of-window real KX-E trade is not this decision's trade; its market result is.
    st = run_score(tmpdir(), dec_rows=[dec(T, "KX-E", "yes", {"buy": 0.3, "skip": 0.7}, action="skip")],
                   ftl_rows=ftl)["nimble_decide:alloc"]
    assert st["n_scorable"] == 1 and close(st["brier"], 0.49), st   # market yes, side yes -> y=1


def test_nimble_probabilities_not_confidence():
    """Burton's live Mac sample: confidence is 1 - normalised entropy, not P(chosen)."""
    raw = {"answers": {"a": {"type": "choice", "choice": "skip",
                             "probabilities": {"buy": 0.4765, "skip": 0.5235},
                             "confidence": 0.00159}}}
    a = raw["answers"]["a"]
    ent = -sum(p * math.log(p) for p in a["probabilities"].values()) / math.log(2)
    assert close(1 - ent, a["confidence"], 1e-5)                 # it is sharpness, as reported
    ftl = [entry("k1", "KX-S", "yes", T - timedelta(days=1)),
           sett("k1", "KX-S", "no", -0.3, T + timedelta(days=1))]
    rec = dec(T, "KX-S", "no", a["probabilities"], action=a["choice"], conf=a["confidence"])
    rec["response"] = raw
    no_probs = dec(T, "KX-T", "yes", None, action="buy", conf=0.9)   # buy + confidence only
    st = run_score(tmpdir(), dec_rows=[rec, no_probs], ftl_rows=ftl)["nimble_decide:alloc"]
    # Forecast is P(buy) = 0.4765 (side no; market no -> y = 1), not 0.00159 nor 1 - 0.00159.
    assert st["n_scorable"] == 1 and close(st["brier"], (0.4765 - 1) ** 2), st
    assert close(st["log_loss"], -math.log(0.4765)), st
    assert close(st["avg_confidence"], 0.00159), st
    assert st["unscorable"] == {"no_p_buy": 1}, st


def test_mirofish_reuses_fee_true_matching():
    ftl = [entry("m1", "BTC-USD", "buy", T + timedelta(days=1), venue="coinbase"),
           sett("m1", "BTC-USD", "exit", 2.0, T + timedelta(days=2))]
    due = iso(T + timedelta(days=5))
    mf = [{"type": "prediction", "pred_id": "p1", "ts": iso(T), "asset": "BTC-USD",
           "predicted_direction": "BUY", "score": 70, "score_after": iso(T + timedelta(days=2))},
          {"type": "prediction", "pred_id": "p2", "ts": iso(T), "asset": "ETH-USD",
           "predicted_direction": "SELL", "score": 40, "score_after": due},
          {"type": "result", "pred_id": "p2", "realized": "DOWN", "fee_true": True},
          {"type": "prediction", "pred_id": "p3", "ts": iso(T), "asset": "SOL-USD",
           "predicted_direction": "BUY", "score": 55, "score_after": due},          # NOW < due: pending
          {"type": "prediction", "pred_id": "p4", "ts": iso(T), "asset": "XRP-USD",
           "predicted_direction": "BUY", "score_after": iso(T)},                     # no score
          {"type": "prediction", "pred_id": "p5", "ts": iso(T), "asset": "ADA-USD",
           "predicted_direction": "BUY", "score": 60, "score_after": iso(T + timedelta(days=1))},
          "broken"]
    st = run_score(tmpdir(), ftl_rows=ftl, mf_rows=mf)["mirofish"]
    assert st["n"] == 5 and st["n_scorable"] == 2, st
    assert close(st["brier"], (0.09 + 0.16) / 2), st
    assert st["unscorable"] == {"no_fee_true_outcome": 1, "no_score": 1, "pending": 1}, st


def test_thin_sample_unproven_weight():
    rows = fv_rows([(0.9, "yes"), (0.1, "no")] * 3)
    d = tmpdir()
    st = run_score(d, ftl_rows=rows)["fv:nws"]
    assert st["n_scorable"] == 6
    assert close(cl.earned_weight("fv:nws", 0.10, 0.30, stats=st), 0.05)
    # Missing source / missing files -> unproven, never raises.
    missing = os.path.join(d, "nope.jsonl")
    assert close(cl.earned_weight("ghost", 0.10, 0.30, decisions_path=missing, ftl_path=missing,
                                  mirofish_path=missing, now=NOW), 0.05)
    assert cl.earned_weight("x", "junk", 0.3) == 0.0
    assert close(cl.earned_weight("x", 0.10, 0.30, stats={"n_scorable": "bad"}), 0.05)
    assert close(cl.earned_weight("x", 0.10, 0.02, stats={}), 0.02)            # shrink still capped
    assert close(cl.earned_weight("x", 0.10, 0.30, stats={"n_scorable": 40, "bss": None}), 0.05)


def test_skilled_beats_unskilled():
    outs = ["yes", "no"] * 20
    rows = fv_rows([(0.8 if o == "yes" else 0.2, o) for o in outs], src="good")
    rows += fv_rows([(0.6 if o == "yes" else 0.4, o) for o in outs], src="meh")
    rows += fv_rows([(0.5, o) for o in outs], src="flat")
    rows += fv_rows([(0.2 if o == "yes" else 0.8, o) for o in outs], src="bad")
    res = run_score(tmpdir(), ftl_rows=rows, source_filter="fv")
    assert sorted(res) == ["fv:bad", "fv:flat", "fv:good", "fv:meh"], res
    assert close(res["fv:good"]["bss"], 0.84) and close(res["fv:meh"]["bss"], 0.36)
    assert close(res["fv:flat"]["bss"], 0.0) and res["fv:flat"]["hit_rate"] is None
    w = {s: cl.earned_weight(s, 0.10, 1.0, stats=res[s]) for s in res}
    assert close(w["fv:good"], 0.84) and close(w["fv:meh"], 0.36), w
    assert w["fv:flat"] == 0.0 and w["fv:bad"] == 0.0, w
    assert close(cl.earned_weight("fv:good", 0.10, 0.30, stats=res["fv:good"]), 0.30)  # capped
    # Determinism: same inputs, same answer.
    assert res == run_score(tmpdir(), ftl_rows=rows, source_filter="fv")


def test_filters_and_cli():
    d = tmpdir()
    rows = fv_rows([(0.7, "yes")], src="nws") + fv_rows([(0.4, "no")], src="nymex",
                                                       start=T + timedelta(days=2))
    ftl = write(os.path.join(d, "ftl.jsonl"), rows)
    empty = write(os.path.join(d, "e.jsonl"), [])
    kw = dict(decisions_path=empty, ftl_path=ftl, mirofish_path=empty, now=NOW)
    assert list(cl.score("fv:nws", **kw)) == ["fv:nws"]
    assert list(cl.score("fv:nw", **kw)) == []                       # no partial-name match
    assert list(cl.score(since=iso(T + timedelta(days=1)), **kw)) == ["fv:nymex"]
    out = os.path.join(d, "calib", "summary.json")
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert cl.main(["--ftl", ftl, "--decisions", empty, "--mirofish", empty, "--out", out]) == 0
    txt = buf.getvalue()
    assert "fv:nws" in txt and "fv:nymex" in txt and "wrote " + out in txt, txt
    doc = json.load(open(out))
    assert doc["earned_weight_applied"] is False and set(doc["sources"]) == {"fv:nws", "fv:nymex"}
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        cl.main(["--ftl", ftl, "--decisions", empty, "--mirofish", empty, "--source", "fv:nymex",
                 "--json", "--no-write"])
    assert list(json.loads(buf.getvalue())["sources"]) == ["fv:nymex"]


def _synthetic(n, seed):
    """Overconfident source: it says p, the true P(y=1) is 0.3 + 0.4 * p (squashed to the middle)."""
    import random
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        p = rng.random()
        true = 0.3 + 0.4 * p  # the source says p, reality is much closer to 0.5
        out.append((round(p, 4), 1 if rng.random() < true else 0))
    return out


def test_calibrate_identity_under_min_n():
    pairs = _synthetic(cl.MIN_N - 1, 1)
    r = cl.calibrate_ex("nimble_decide:x", 0.8, pairs=pairs)
    assert r == {"p_cal": 0.8, "calibration": "identity", "n": cl.MIN_N - 1}, r
    assert cl.calibrate("nimble_decide:x", None, pairs=pairs) is None
    assert cl.calibrate("nimble_decide:x", 1.7, pairs=pairs) == 1.0  # clamped
    assert cl.calibrate("nimble_decide:x", "junk") is None
    # cached per process from the files; with no record → identity, never raises
    cl._reset_cache(items=[])
    assert cl.calibrate_ex("nimble_decide:y", 0.7) == {"p_cal": 0.7, "calibration": "identity", "n": 0}
    cl._reset_cache(items=[{"source": "s", "p": "bad", "y": 1}] * 40)  # malformed → identity
    assert cl.calibrate_ex("s", 0.7)["calibration"] == "identity"
    cl._reset_cache()


def test_calibrate_isotonic_monotone_and_improves_brier():
    train, test = _synthetic(2000, 7), _synthetic(2000, 8)
    knots = cl.isotonic_fit(train)
    xs = [k[0] for k in knots]
    vs = [k[1] for k in knots]
    assert all(a < b for a, b in zip(xs, xs[1:])) and all(a <= b for a, b in zip(vs, vs[1:]))
    assert all(0.0 < v < 1.0 for v in vs)
    grid = [i / 100 for i in range(101)]
    cal = [cl.calibrate("src", g, pairs=train) for g in grid]
    assert all(a <= b for a, b in zip(cal, cal[1:])), "monotone"
    assert all(0.0 <= c <= 1.0 for c in cal)
    assert cl.calibrate_ex("src", 0.9, pairs=train)["calibration"] == "isotonic"
    assert cl.calibrate("src", 0.9, pairs=train) < 0.8  # overconfident 0.9 pulled toward 0.66
    brier = lambda f: sum((f(p) - y) ** 2 for p, y in test) / len(test)  # noqa: E731
    raw = brier(lambda p: p)
    fit = brier(lambda p: cl.calibrate("src", p, pairs=train))
    assert fit < raw - 0.01, (raw, fit)
    # deterministic; the process cache fits once from the preloaded items
    assert cl.isotonic_fit(train) == knots
    cl._reset_cache(items=[{"source": "src", "p": p, "y": y} for p, y in train])
    a = cl.calibrate_ex("src", 0.9)
    assert a["calibration"] == "isotonic" and a["n"] == 2000
    assert a["p_cal"] == cl.calibrate("src", 0.9, pairs=train)
    assert ("src", cl.MIN_N) in cl._FITS
    assert cl.calibrate_ex("other", 0.9)["calibration"] == "identity"
    cl._reset_cache()
    # pooling: a violating pair is merged, ties start in one block
    assert cl.isotonic_fit([(0.2, 1), (0.8, 0)]) == [(0.5, 0.5)]
    assert cl.isotonic_fit([(0.5, 1), (0.5, 0)]) == [(0.5, 0.5)]
    assert cl.isotonic_fit([]) == []


def _keyed(tag, src, n=40, model=None, start=T):
    """n well-scored traded decisions under one source key: p_buy .9 when the trade won, .1 when it lost."""
    decs, ftl = [], []
    for i in range(n):
        won = i % 2 == 0
        ts, tick, eid = start + timedelta(minutes=i), "KX%s%03d" % (tag, i), "%s_%d" % (tag, i)
        p = 0.9 if won else 0.1
        row = dec(ts, tick, "yes", {"buy": p, "hold": (1 - p) / 2, "skip": (1 - p) / 2}, src=src)
        if model is not None:
            row["model"] = model
        decs.append(row)
        ftl += [entry(eid, tick, "yes", ts + timedelta(seconds=60)),
                sett(eid, tick, "yes" if won else "no", 0.5 if won else -0.5, ts + timedelta(days=1))]
    return decs, ftl


def _store(d, decs, ftl):
    """Score from files in d and preload the calibration cache with the same items (no default paths)."""
    paths = dict(decisions_path=write(os.path.join(d, "dec.jsonl"), decs),
                 ftl_path=write(os.path.join(d, "ftl.jsonl"), ftl),
                 mirofish_path=write(os.path.join(d, "mf.jsonl"), []), now=NOW)
    cl._reset_cache(items=cl._all_items(decs, ftl, [], NOW))
    return cl.score(**paths), paths


def test_new_key_held_with_old_rows_exactly_as_empty_store():
    """Ruling 5 (two-way): rows under old keys never calibrate, weight or enter the new key's fit."""
    import nimble_decide as nd
    import systemone_pin as pin
    import systemone_v3_candidate as v3
    new = nd._cal_source("pt")
    assert new == "decide:%s@%s:pt" % (pin.dm.INSTANCE, pin.dm.QUANT.lower()) and new.startswith(pin.SOURCE_PREFIX)
    old, old_v3 = "nimble_decide:pt", "nimble_decide:v3:%s:pt" % v3.MANIFEST_SHA256
    try:
        # empty store: the reference answers
        res0, paths0 = _store(tmpdir(), [], [])
        assert res0 == {}
        held0 = nd._calibrate(new, 0.9)
        assert held0 == {"p_cal": None, "calibration": "untrained_hold", "n": 0}, held0
        assert cl.calibrate_ex(new, 0.9) == {"p_cal": 0.9, "calibration": "identity", "n": 0}
        w0 = cl.earned_weight(new, 0.10, 0.30, **paths0)
        assert close(w0, 0.05), w0                                      # the unproven fallback
        # many well-scored rows under the old keys only
        d_old, f_old = _keyed("O", old)
        d_v3, f_v3 = _keyed("V", old_v3, model=v3.TAG)
        res1, paths1 = _store(tmpdir(), d_old + d_v3, f_old + f_v3)
        assert sorted(res1) == sorted([old, old_v3]), res1             # the new key has no rows
        assert res1[old]["n_scorable"] == 40 and res1[old_v3]["n_scorable"] == 40, res1
        # the other side: these rows do fit, and do earn weight, under their own keys
        assert cl.calibrate_ex(old, 0.9)["calibration"] == "isotonic"
        assert cl.calibrate_ex(old_v3, 0.9)["calibration"] == "isotonic"
        assert close(cl.earned_weight(old, 0.10, 0.30, **paths1), 0.30)
        # ...and the new key is held exactly as with the empty store
        assert nd._calibrate(new, 0.9) == held0
        assert nd._calibrate(new, 0.1) == nd._calibrate(new, 0.1) == held0
        assert cl.calibrate_ex(new, 0.9) == {"p_cal": 0.9, "calibration": "identity", "n": 0}
        assert cl.earned_weight(new, 0.10, 0.30, **paths1) == w0
        # new-key rows (model = the instance) are admitted to the new key's fit; old rows are not
        d_new, f_new = _keyed("N", new, model=pin.dm.INSTANCE)
        res2, paths2 = _store(tmpdir(), d_old + d_v3 + d_new, f_old + f_v3 + f_new)
        assert res2[new]["n_scorable"] == 40 and res2[new]["n"] == 40, res2[new]
        assert res2[old] == res1[old] and res2[old_v3] == res1[old_v3]  # old keys unchanged
        fit = cl.calibrate_ex(new, 0.9)
        assert fit["calibration"] == "isotonic" and fit["n"] == 40, fit  # 40, not 120
        got = nd._calibrate(new, 0.9)
        assert got["calibration"] == "isotonic" and got["p_cal"] is not None and got["n"] == 40, got
        assert close(cl.earned_weight(new, 0.10, 0.30, **paths2), 0.30)
    finally:
        cl._reset_cache()


def test_new_key_admits_only_rows_naming_the_instance():
    """Ruling 7: the new prefix is admitted only with model == the instance; ruling 8: the v3 gate stays."""
    import nimble_decide as nd
    import systemone_pin as pin
    import systemone_v3_candidate as v3
    new = nd._cal_source("pt")
    try:
        bad = []
        for tag, model in (("W", v3.TAG), ("X", None), ("Y", pin.dm.INSTANCE.upper()), ("Z", "")):
            bad += [_keyed(tag, new, model=model)]
        bad += [_keyed("Q", "decide:other@x:pt", model=pin.dm.INSTANCE),
                _keyed("R", "decide:%s@%s:pt" % (pin.dm.INSTANCE, "q4_k_m"), model=pin.dm.INSTANCE),
                _keyed("S", "nimble_decide:v3:%s:pt" % v3.MANIFEST_SHA256, model=None),
                _keyed("U", "nimble_decide:v3:%s:pt" % ("0" * len(v3.MANIFEST_SHA256)), model=v3.TAG)]
        decs = [r for ds, _ in bad for r in ds]
        ftl = [r for _, fs in bad for r in fs]
        res, _paths = _store(tmpdir(), decs, ftl)
        assert res == {}, sorted(res)
        assert nd._calibrate(new, 0.9) == {"p_cal": None, "calibration": "untrained_hold", "n": 0}
        # the same rows with the right model are admitted
        ok_decs, ok_ftl = _keyed("W", new, model=pin.dm.INSTANCE)
        res, _paths = _store(tmpdir(), ok_decs, ok_ftl)
        assert list(res) == [new] and res[new]["n_scorable"] == 40, res
    finally:
        cl._reset_cache()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("PASS test_calibration_ledger")
