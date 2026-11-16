"""Pairs as a second kind of strategy (T38-1).

The hand-built series below are the ticket's rulings made concrete; `oracle` is a slow single-pair walker
written from the same rulings with plain loops and nothing from `pairs.py`."""
import copy
import json
import math

import numpy as np
import pandas as pd
import pytest

from valedia_trader.research_loop import pairs as pr
from valedia_trader.research_loop import strategy as st

DROP = object()
BASE = [100, 101, 99, 100, 101, 100, 99, 100]          # eight quiet bars; bar 8 is the first one a test moves


def frame(**cols):
    n = len(next(iter(cols.values())))
    return pd.DataFrame({k: np.asarray(v, dtype=float) for k, v in cols.items()}, index=pd.bdate_range("2024-01-01", periods=n))


def spec(**over):
    base = {"kind": "pairs", "name": "pairs_test", "version": "1.0.0",
            "hypothesis": "Two related prices drift back together after they part",
            "pairs": [["A", "B"]], "signal": {"hedge": "ratio", "window": 5, "enter": 1.5, "exit": 0.5, "stop_z": None},
            "legs": "both", "sizing": {"pair_weight": 0.10, "max_open_pairs": 3},
            "risk": {"max_position_pct": 0.10, "max_gross": 1.0, "stop_loss_pct": None, "take_profit_pct": None,
                     "max_holding_days": None}}
    for k, v in over.items():
        if k in ("signal", "sizing", "risk") and isinstance(v, dict):
            base[k] = {**base[k], **v}
        else:
            base[k] = v
    for part in (base, base.get("signal"), base.get("sizing"), base.get("risk")):
        if isinstance(part, dict):
            for k in [k for k, v in part.items() if v is DROP]:
                del part[k]
    return base


def build(**over):
    return pr.PairsStrategy.from_spec(spec(**over))


def z_ratio(a, b, window=5):
    """The z-score of A/B by pandas, independent of pairs.py: for reading the hand-built series."""
    s = pd.Series(np.asarray(a, float) / np.asarray(b, float))
    return ((s - s.rolling(window).mean()) / s.rolling(window).std()).to_numpy()


def trades(events):
    return [e for e in events if e["action"] == "exit"]


# ---- the slow oracle


MISSING = 5                       # consecutive bars with a leg unpriced that close an open pair (addendum 2, ruling 2)


def _ok(x):
    return isinstance(x, float) and math.isfinite(x) and x > 0


def _bar_signal(a, b, signal):
    """Each bar's own z and beta for one pair, plain loops."""
    hedge, w, hw = signal["hedge"], signal["window"], signal.get("hedge_window")
    n = len(a)
    z, beta = [None] * n, [None] * n
    for t in range(n):
        if hedge == "ols":
            if t < hw - 1:
                continue
            idx = list(range(t - hw + 1, t + 1))
            if not all(_ok(a[u]) and _ok(b[u]) for u in idx):
                continue
            ys, xs = [math.log(a[u]) for u in idx], [math.log(b[u]) for u in idx]
            if max(xs) - min(xs) == 0.0:
                continue
            xm, ym = math.fsum(xs) / hw, math.fsum(ys) / hw
            bt = math.fsum((x - xm) * (y - ym) for x, y in zip(xs, ys)) / math.fsum((x - xm) ** 2 for x in xs)
            if not math.isfinite(bt):
                continue
            al = ym - bt * xm
            beta[t] = bt
            s = [math.log(a[u]) - bt * math.log(b[u]) - al for u in range(t - w + 1, t + 1)]
        else:
            if t < w - 1:
                continue
            idx = list(range(t - w + 1, t + 1))
            if not all(_ok(a[u]) and _ok(b[u]) for u in idx):
                continue
            s = [a[u] / b[u] if hedge == "ratio" else math.log(a[u]) - math.log(b[u]) for u in idx]
        z[t] = _z_of(s, 0.0 if hedge == "ratio" else 1.0)     # a ratio may be tiny (addendum 1, ruling 3)
    return z, beta


def _z_of(s, floor):
    m = math.fsum(s) / len(s)
    sd = math.sqrt(math.fsum((x - m) ** 2 for x in s) / (len(s) - 1))
    if not math.isfinite(sd) or sd <= 1e-12 * max(floor, max(abs(x) for x in s)):
        return None
    return (s[-1] - m) / sd


def _held_z(a, b, beta0, t, w):
    """An open ols pair's z: the spread it holds, ln A - beta_0 ln B, over the last `window` bars (addendum 2, ruling 3)."""
    if t < w - 1 or not all(_ok(a[u]) and _ok(b[u]) for u in range(t - w + 1, t + 1)):
        return None
    return _z_of([math.log(a[u]) - beta0 * math.log(b[u]) for u in range(t - w + 1, t + 1)], 1.0)


def oracle_book(px, pairs, signal, legs="both", sizing=None, risk=None):
    """Any number of pairs, plain loops, written from the rulings (ticket and addenda 1 to 3), nothing from pairs.py.
    Returns (rows: one {symbol: net weight} per bar, closed trades (bar, pair index, reason word, pair result),
    per pair (z, beta), counts of what happened)."""
    sizing, risk = dict(sizing or {}), dict(risk or {})
    hedge, w, en, ex = signal["hedge"], signal["window"], signal["enter"], signal["exit"]
    sz = signal.get("stop_z")
    pw, mop = sizing.get("pair_weight", 0.10), sizing.get("max_open_pairs", 3)
    mpp, mg = risk.get("max_position_pct", 0.10), risk.get("max_gross", 1.0)
    sl = risk["stop_loss_pct"] if "stop_loss_pct" in risk else 0.10
    tp, mh = risk.get("take_profit_pct"), risk.get("max_holding_days")
    px = {k: [float(x) for x in v] for k, v in px.items()}
    syms = list(dict.fromkeys(s for p in pairs for s in p))
    n = len(px[syms[0]])
    sig = [_bar_signal(px[a], px[b], signal) for a, b in pairs]
    st_ = [{"side": 0, "wa": 0.0, "wb": 0.0, "pa": None, "pb": None, "held": 0, "miss": 0, "armed": True, "cside": 0,
            "beta0": None} for _ in pairs]
    last = {s: None for s in syms}
    rows, closed_trades, counts = [], [], {"position skip": 0, "gross skip": 0, "scaled": 0}
    for t in range(n):
        for s in syms:
            if _ok(px[s][t]):
                last[s] = px[s][t]
        closed = set()
        for i, (a, b) in enumerate(pairs):
            p = st_[i]
            if p["side"] == 0:
                continue
            both = _ok(px[a][t]) and _ok(px[b][t])
            p["held"] += 1                                           # the clock runs on every bar (addendum 2, ruling 2)
            p["miss"] = 0 if both else p["miss"] + 1
            r = (p["wa"] * (last[a] / p["pa"] - 1.0) + p["wb"] * (last[b] / p["pb"] - 1.0)) / (abs(p["wa"]) + abs(p["wb"]))
            zt = None
            if both:
                zt = _held_z(px[a], px[b], p["beta0"], t, w) if hedge == "ols" else sig[i][0][t]
            why = None
            if sl is not None and r <= -sl:
                why = "stop loss"
            elif tp is not None and r >= tp:
                why = "take profit"
            elif mh is not None and p["held"] >= mh:
                why = "time limit"
            elif p["miss"] >= MISSING:
                why = "missing leg"
            elif sz is not None and zt is not None and ((p["side"] > 0 and zt <= -sz) or (p["side"] < 0 and zt >= sz)):
                why = "z stop"
            elif zt is not None and ((p["side"] > 0 and zt >= -ex) or (p["side"] < 0 and zt <= ex)):
                why = "exit rule"
            if why:
                closed_trades.append((t, i, why, r))
                counts[why] = counts.get(why, 0) + 1
                p.update(cside=p["side"], side=0, wa=0.0, wb=0.0, pa=None, pb=None, held=0, miss=0, armed=why == "exit rule")
                closed.add(i)
        cands = []
        for i, (a, b) in enumerate(pairs):
            p, zt = st_[i], sig[i][0][t]
            if p["side"] != 0 or i in closed or zt is None:
                continue
            if not p["armed"]:                                       # re-arm on the closed trade's own exit condition
                if (p["cside"] > 0 and zt >= -ex) or (p["cside"] < 0 and zt <= ex):
                    p["armed"] = True
                continue                                             # and the re-arm bar opens nothing
            if not (_ok(px[a][t]) and _ok(px[b][t])) or abs(zt) < en or (sz is not None and abs(zt) >= sz):
                continue
            if hedge == "ols" and not (sig[i][1][t] is not None and 0.1 <= sig[i][1][t] <= 10.0):
                continue
            cands.append(i)
        free = mop - sum(1 for p in st_ if p["side"] != 0)
        legsum = {s: 0.0 for s in syms}
        for (a, b), p in zip(pairs, st_):
            if p["side"] != 0:
                legsum[a] += abs(p["wa"])
                legsum[b] += abs(p["wb"])
        book = sum(legsum.values())
        for i in sorted(cands, key=lambda j: (-abs(sig[j][0][t]), j)):
            if free == 0:
                break
            a, b = pairs[i]
            zt, bt = sig[i][0][t], sig[i][1][t]
            new_side = 1 if zt <= -en else -1
            ga, gb = (pw / (1.0 + bt), pw * bt / (1.0 + bt)) if hedge == "ols" else (pw / 2.0, pw / 2.0)
            big = max(ga, gb)
            if big > mpp:
                ga, gb = ga * mpp / big, gb * mpp / big
            na, nb = (ga, -gb) if new_side > 0 else (-ga, gb)
            if legs == "long":
                na, nb = max(na, 0.0), max(nb, 0.0)
            if legsum[a] + abs(na) > mpp + 1e-12 or legsum[b] + abs(nb) > mpp + 1e-12:
                counts["position skip"] += 1                         # leg weights, no credit for netting (addendum 3)
                continue
            if book + abs(na) + abs(nb) > mg + 1e-12:
                counts["gross skip"] += 1
                continue
            st_[i].update(side=new_side, wa=na, wb=nb, pa=px[a][t], pb=px[b][t], held=0, miss=0, beta0=bt)
            legsum[a] += abs(na)
            legsum[b] += abs(nb)
            book += abs(na) + abs(nb)
            free -= 1
        net = {s: 0.0 for s in syms}
        for (a, b), p in zip(pairs, st_):
            if p["side"] != 0:
                net[a] += p["wa"]
                net[b] += p["wb"]
        gross = sum(abs(v) for v in net.values())
        if gross > mg:
            counts["scaled"] += 1
            net = {s: v * mg / gross for s, v in net.items()}
        rows.append(net)
    return rows, closed_trades, sig, counts


def oracle(a, b, signal, legs="both", sizing=None, risk=None):
    """One pair: (rows of (weight A, weight B), closed trades (bar, reason word, pair result), z, beta)."""
    rows, closed, sig, _ = oracle_book({"A": a, "B": b}, [("A", "B")], signal, legs, sizing, risk)
    return [(r["A"], r["B"]) for r in rows], [(t, why, r) for t, _, why, r in closed], sig[0][0], sig[0][1]


def leg_invariant(strategy, w, events):
    """Rebuild the held legs bar by bar from the events and check addendum 3's invariant on every bar: per symbol
    the sum of absolute leg weights is within max_position_pct, the book's within max_gross, and the output row is
    exactly the net of the legs (the final scaling never acted). Returns (bars, largest symbol sum / limit, largest
    book sum / limit)."""
    held, by_date = {}, {}
    for e in events:
        by_date.setdefault(e["date"], []).append(e)
    worst_sym = worst_book = 0.0
    mpp, mg = strategy.risk.max_position_pct, strategy.risk.max_gross
    for d in w.index:
        for e in by_date.get(d, []):
            if e["action"] in ("exit", "exit_leg"):
                del held[(e["pair"], e["symbol"])]
            else:
                held[(e["pair"], e["symbol"])] = e["side"] * e["weight"]
        sums, net = {}, {}
        for (_, sym), wl in held.items():
            sums[sym] = sums.get(sym, 0.0) + abs(wl)
            net[sym] = net.get(sym, 0.0) + wl
        book = sum(sums.values())
        assert all(v <= mpp + 1e-12 for v in sums.values()) and book <= mg + 1e-12, d
        assert all(abs(w.at[d, c] - net.get(c, 0.0)) <= 1e-14 for c in w.columns), d      # never rescaled
        worst_sym = max([worst_sym] + [v / mpp for v in sums.values()])
        worst_book = max(worst_book, book / mg)
    return len(w), worst_sym, worst_book


# ---- specs


def _bad_specs():
    many = [[f"S{i}", f"T{i}"] for i in range(41)]
    out = {
        "not an object": ["pairs"],
        "missing pairs": spec(pairs=DROP),
        "missing signal": spec(signal=DROP),
        "missing hypothesis": spec(hypothesis=DROP),
        "unknown top key": spec(market=["A", "B"]),
        "unknown signal key": spec(signal={"lookback": 5}),
        "unknown sizing key": spec(sizing={"rule": "equal"}),
        "unknown risk key": spec(risk={"leverage": 2}),
        "risk sets max_open_positions": spec(risk={"max_open_positions": 4}),
        "wrong kind": spec(kind="rule"),
        "bad name": spec(name="Pairs Test"),
        "bad version": spec(version="1.0"),
        "short hypothesis": spec(hypothesis="pairs revert"),
        "no pairs": spec(pairs=[]),
        "41 pairs": spec(pairs=many),
        "pair of one symbol twice": spec(pairs=[["A", "A"]]),
        "pair of three": spec(pairs=[["A", "B", "C"]]),
        "pair as a string": spec(pairs=["AB"]),
        "empty symbol": spec(pairs=[["A", ""]]),
        "symbol not a string": spec(pairs=[["A", 7]]),
        "same pair twice": spec(pairs=[["A", "B"], ["A", "B"]]),
        "same pair reversed": spec(pairs=[["A", "B"], ["B", "A"]]),
        "pairs not a list": spec(pairs={"A": "B"}),
        "signal not an object": spec(signal=[1]),
        "unknown hedge": spec(signal={"hedge": "kalman"}),
        "no hedge": spec(signal={"hedge": DROP}),
        "no window": spec(signal={"window": DROP}),
        "no enter": spec(signal={"enter": DROP}),
        "no exit": spec(signal={"exit": DROP}),
        "window 4": spec(signal={"window": 4}),
        "window 505": spec(signal={"window": 505}),
        "window not whole": spec(signal={"window": 20.5}),
        "window a bool": spec(signal={"window": True}),
        "window a string": spec(signal={"window": "20"}),
        "enter 0": spec(signal={"enter": 0}),
        "enter above 10": spec(signal={"enter": 10.5}),
        "enter infinite": spec(signal={"enter": float("inf")}),
        "exit equal to enter": spec(signal={"exit": 1.5}),
        "exit negative": spec(signal={"exit": -0.1}),
        "stop_z equal to enter": spec(signal={"stop_z": 1.5}),
        "stop_z above 20": spec(signal={"stop_z": 21}),
        "hedge_window with ratio": spec(signal={"hedge_window": 60}),
        "hedge_window with log_ratio": spec(signal={"hedge": "log_ratio", "hedge_window": 60}),
        "ols without hedge_window": spec(signal={"hedge": "ols"}),
        "ols hedge_window below window": spec(signal={"hedge": "ols", "window": 30, "hedge_window": 25}),
        "ols hedge_window 19": spec(signal={"hedge": "ols", "hedge_window": 19}),
        "ols hedge_window 1261": spec(signal={"hedge": "ols", "hedge_window": 1261}),
        "ols hedge_window not whole": spec(signal={"hedge": "ols", "hedge_window": 60.5}),
        "legs short": spec(legs="short"),
        "crypto with both legs": spec(pairs=[["BTC-USD", "ETH-USD"]]),
        "crypto with both legs (default)": spec(pairs=[["SOL-USD", "AAA"]], legs=DROP),
        "missing legs": spec(legs=DROP),
        "pair_weight 0": spec(sizing={"pair_weight": 0}),
        "pair_weight above 1": spec(sizing={"pair_weight": 1.5}),
        "max_open_pairs 0": spec(sizing={"max_open_pairs": 0}),
        "max_open_pairs 41": spec(sizing={"max_open_pairs": 41}),
        "max_open_pairs not whole": spec(sizing={"max_open_pairs": 2.5}),
        "sizing not an object": spec(sizing=0.1),
        "risk not an object": spec(risk=0.1),
        "stop_loss 1": spec(risk={"stop_loss_pct": 1.0}),
        "max_position_pct a bool": spec(risk={"max_position_pct": True}),
        "max_gross a string": spec(risk={"max_gross": "1"}),
        "max_holding_days not whole": spec(risk={"max_holding_days": 2.5}),
        "max_holding_days 0": spec(risk={"max_holding_days": 0}),
    }
    return list(out.items())


@pytest.mark.parametrize("label,bad", _bad_specs(), ids=[k for k, _ in _bad_specs()])
def test_bad_specs_are_refused(label, bad):
    with pytest.raises(st.SpecError):
        pr.PairsStrategy.from_spec(bad)


def test_good_specs_are_accepted_with_their_defaults():
    s = pr.PairsStrategy.from_spec({k: v for k, v in spec().items() if k not in ("sizing", "risk")})
    assert s.legs == "both" and s.holds_shorts and not build(legs="long").holds_shorts
    with pytest.raises(AttributeError):
        s.holds_shorts = False                                                   # read-only
    assert s.to_spec()["sizing"] == {"pair_weight": 0.10, "max_open_pairs": 3}
    assert s.risk == st.StrategyRisk(max_open_positions=6)                    # two legs per open pair, three pairs
    crypto = build(pairs=[["BTC-USD", "ETH-USD"], ["ETH-USD", "SOL-USD"]], legs="long", sizing={"max_open_pairs": 2})
    assert crypto.market == ("BTC-USD", "ETH-USD", "SOL-USD") and crypto.risk.max_open_positions == 2
    ols = build(signal={"hedge": "ols", "window": 20, "hedge_window": 20, "stop_z": 3.0})
    assert ols.to_spec()["signal"] == {"hedge": "ols", "window": 20, "enter": 1.5, "exit": 0.5, "stop_z": 3.0, "hedge_window": 20}
    assert build(pairs=[["XOM", "CVX"], ["KO", "PEP"], ["CVX", "KO"]]).market == ("XOM", "CVX", "KO", "PEP")
    assert build(signal={"exit": 0, "enter": 10, "stop_z": 20}).to_spec()["signal"]["stop_z"] == 20


def test_a_pairs_spec_round_trips_through_to_spec_and_save_spec(tmp_path):
    for hedge in ("ratio", "log_ratio", "ols"):
        extra = {"hedge_window": 60} if hedge == "ols" else {}
        full = spec(name=f"pairs_{hedge}", pairs=[["KO", "PEP"], ["XOM", "CVX"]],
                    signal={"hedge": hedge, "window": 20, "enter": 1.5, "exit": 0.5, "stop_z": 3.5, **extra},
                    legs="long", sizing={"pair_weight": 0.2, "max_open_pairs": 2},
                    risk={"max_position_pct": 0.08, "max_gross": 0.9, "stop_loss_pct": 0.05, "take_profit_pct": 0.1,
                          "max_holding_days": 20})
        s = pr.PairsStrategy.from_spec(copy.deepcopy(full))
        assert s.to_spec() == full and s.to_spec()["kind"] == "pairs"
        assert pr.PairsStrategy.from_spec(s.to_spec()).to_spec() == s.to_spec()
        assert "max_open_positions" not in s.to_spec()["risk"] and s.risk.max_open_positions == 2
        assert s.rebalance_days is None and s.market == ("KO", "PEP", "XOM", "CVX")
        assert s.describe()["risk"]["max_open_positions"] == 2 and s.describe()["market"] == ["KO", "PEP", "XOM", "CVX"]
        path = st.save_spec(s, tmp_path)
        assert json.loads(path.read_text()) == full and pr.PairsStrategy.from_spec(json.loads(path.read_text())) == s
    assert build().risk.max_open_positions == 6 and build(legs="long").risk.max_open_positions == 3


def test_a_pairs_strategy_has_no_per_symbol_rules():
    s, px = build(), frame(A=BASE, B=BASE)
    for call in (lambda: s.entry(px), lambda: s.exit(px, 1), lambda: s.size(px), lambda: s.strength(px)):
        with pytest.raises(st.SpecError, match="walked as pairs"):
            call()


# ---- the walk on hand-built series


def test_entries_and_exits_land_on_the_exact_bars_for_both_sides():
    b = [50.0 * (1 + 0.003 * k) for k in range(18)]                     # B moves; the ratio is what the test sets
    down = BASE + [94, 95, 97, 99, 100, 101, 100, 99, 101, 100]
    up = BASE + [106, 105, 103, 101, 100, 99, 100, 101, 99, 100]
    for path, side, want_z in ((down, 1, -1.73), (up, -1, 1.73)):
        a = [r / 100.0 * x for r, x in zip(path, b)]
        z = z_ratio(a, b)
        assert z[8] == pytest.approx(want_z, abs=1e-3) and abs(z[9]) > 0.5 and abs(z[10]) <= 0.5
        w, events = build().walk(frame(A=a, B=b))
        assert [(e["action"], e["symbol"], e["side"]) for e in events] == [
            ("enter", "A", side), ("enter_leg", "B", -side), ("exit", "A", side), ("exit_leg", "B", -side)]
        assert [events[i]["date"] for i in range(4)] == [w.index[8]] * 2 + [w.index[10]] * 2
        assert events[0]["reason"].startswith("entry rule") and events[2]["reason"].startswith("exit rule")
        assert events[0]["pair"] == "A/B" and events[0]["hedge"] is None and events[0]["z"] == pytest.approx(z[8])
        assert events[0]["weight"] == pytest.approx(0.05) and events[1]["weight"] == pytest.approx(0.05)
        r = (side * 0.05 * (a[10] / a[8] - 1) - side * 0.05 * (b[10] / b[8] - 1)) / 0.10
        assert events[2]["pnl_pct"] == pytest.approx(r) and events[3]["pnl_pct"] == pytest.approx(r)
        assert events[2]["price"] == a[10] and events[3]["price"] == b[10]
        assert (w.iloc[:8] == 0).all().all() and (w.iloc[10:] == 0).all().all()
        assert list(w["A"].iloc[8:10]) == [0.05 * side] * 2 and list(w["B"].iloc[8:10]) == [-0.05 * side] * 2


def test_both_legs_are_dollar_neutral_at_entry_and_held_constant():
    rng = np.random.default_rng(3)
    b = 80 * np.exp(np.cumsum(0.02 * rng.standard_normal(30)))
    ratio = np.array(BASE + [94, 93, 92, 91, 92, 93, 94, 95, 96, 98, 99, 100, 100, 101, 100, 99, 100, 101, 100, 100, 99, 100]) / 100
    a = ratio * b
    w, events = build(signal={"exit": 0.0}).walk(frame(A=a, B=b))
    held = w[(w["A"] != 0)]
    assert len(held) >= 3 and (held["A"] == 0.05).all() and (held["B"] == -0.05).all()
    assert (held.sum(axis=1).abs() < 1e-15).all()                          # dollar-neutral every held bar, prices moving
    assert events[0]["action"] == "enter" and events[0]["date"] == w.index[8]


def test_the_long_leg_only_takes_the_cheap_side_and_crypto_needs_it():
    b = [100.0] * 18
    down = BASE + [94, 95, 97, 99, 100, 101, 100, 99, 101, 100]
    up = BASE + [106, 105, 103, 101, 100, 99, 100, 101, 99, 100]
    w, events = build(legs="long").walk(frame(A=down, B=b))
    assert list(w["A"].iloc[8:10]) == [0.05, 0.05] and (w["B"] == 0).all()        # the weight it would have had
    assert [(e["action"], e["symbol"], e["side"]) for e in events] == [("enter", "A", 1), ("exit", "A", 1)]
    assert events[1]["pnl_pct"] == pytest.approx(97 / 94 - 1)                     # the pair's result is the held leg's
    w, events = build(legs="long").walk(frame(A=up, B=b))
    assert (w["A"] == 0).all() and list(w["B"].iloc[8:10]) == [0.05, 0.05]       # short A is not taken; long B is
    assert [(e["action"], e["symbol"], e["side"]) for e in events] == [("enter", "B", 1), ("exit", "B", 1)]
    assert events[1]["pnl_pct"] == 0.0
    with pytest.raises(st.SpecError, match="long"):
        build(pairs=[["BTC-USD", "ETH-USD"]], legs="both")
    w, _ = build(pairs=[["BTC-USD", "ETH-USD"]], legs="long").walk(frame(**{"BTC-USD": up, "ETH-USD": b}))
    assert (w["BTC-USD"] >= 0).all() and w["ETH-USD"].max() == 0.05


def test_pair_stop_loss_take_profit_time_limit_and_z_stop():
    b = [100.0] * 30
    cases = [  # tail after BASE, signal and risk overrides, expected exit bar, reason, result
        ([94, 90, 89], {}, {"stop_loss_pct": 0.02}, 9, "stop loss", 0.5 * (90 / 94 - 1)),
        ([94, 99, 99, 100], {"exit": 0.0}, {"take_profit_pct": 0.02}, 9, "take profit", 0.5 * (99 / 94 - 1)),
        ([94, 94.5, 94.2, 94.4, 94.3], {}, {"max_holding_days": 3}, 11, "time limit", 0.5 * (94.4 / 94 - 1)),
        ([97, 85, 86], {"stop_z": 1.6}, {}, 9, "z stop", 0.5 * (85 / 97 - 1)),     # entered at |z| 1.58, below the z stop
    ]
    for tail, sig, risk, bar, reason, r in cases:
        a = BASE + tail
        w, events = build(signal=sig, risk=risk).walk(frame(A=a, B=b[: len(a)]))
        exits = trades(events)
        assert len(exits) == 1 and exits[0]["date"] == w.index[bar], reason
        assert exits[0]["reason"].startswith(reason) and exits[0]["pnl_pct"] == pytest.approx(r)
        assert [e["action"] for e in events if e["date"] == w.index[bar]] == ["exit", "exit_leg"]
        assert (w.iloc[bar:] == 0).all().all() and w["A"].iloc[bar - 1] == 0.05
    assert z_ratio(BASE + [97, 85, 86], b[:11])[9] == pytest.approx(-1.756, abs=1e-3)   # beyond the z stop


def test_a_pair_closed_by_a_stop_waits_for_the_spread_to_come_back_before_it_opens_again():
    a = BASE + [94, 90, 86, 82, 78, 85, 95, 100, 100.5, 100, 99.5, 100, 94, 95, 99, 100]
    b = [100.0] * len(a)
    z = z_ratio(a, b)
    assert all(z[k] <= -1.2 for k in (9, 10, 11, 12)) and z[13] == pytest.approx(0.178, abs=1e-3)
    sig = {"enter": 1.2, "exit": 0.3}
    w, events = build(signal=sig, risk={"stop_loss_pct": 0.02}).walk(frame(A=a, B=b))
    got = [(w.index.get_loc(e["date"]), e["action"], e["reason"].split(":")[0]) for e in events if e["symbol"] == "A"]
    assert got == [(8, "enter", "entry rule"), (9, "exit", "stop loss"),          # bars 10 to 12 are past enter: disarmed
                   (14, "enter", "entry rule"), (15, "exit", "stop loss"),        # bar 13 re-armed (|z| <= exit), 14 opened short
                   (20, "enter", "entry rule"), (22, "exit", "exit rule")]        # 18 re-armed, 20 opened long
    assert w["A"].iloc[14] == -0.05 and w["A"].iloc[20] == 0.05
    w, events = build(signal=sig).walk(frame(A=a, B=b))                           # no stop: the exit rule never disarms
    got = [(w.index.get_loc(e["date"]), e["action"]) for e in events if e["symbol"] == "A"]
    assert got[:3] == [(8, "enter"), (13, "exit"), (14, "enter")]


def test_entries_go_to_free_slots_by_the_size_of_z_then_the_order_of_the_pairs():
    q = [100.0] * 9
    cols = {"A": BASE + [97], "P": q, "C": BASE + [90], "Q": q, "D": BASE + [94], "R": q}
    s = build(pairs=[["A", "P"], ["C", "Q"], ["D", "R"]], sizing={"max_open_pairs": 2})
    w, events = s.walk(frame(**cols))
    assert [e["pair"] for e in events if e["action"] == "enter"] == ["C/Q", "D/R"]          # |z| 1.77 and 1.73 beat 1.58
    assert w["A"].iloc[-1] == 0 and w["C"].iloc[-1] == 0.05 and w["D"].iloc[-1] == 0.05
    tie = {"A": BASE + [94], "P": q, "C": BASE + [94], "Q": q}
    _, events = build(pairs=[["C", "Q"], ["A", "P"]], sizing={"max_open_pairs": 1}).walk(frame(**tie))
    assert [e["pair"] for e in events if e["action"] == "enter"] == ["C/Q"]                  # a tie goes to the earlier pair


def test_a_symbol_in_two_open_pairs_is_netted():
    q = [100.0] * 9
    same = build(pairs=[["A", "B"], ["A", "C"]]).walk(frame(A=BASE + [94], B=q, C=q))[0]
    assert same["A"].iloc[-1] == pytest.approx(0.10) and same["B"].iloc[-1] == -0.05 and same["C"].iloc[-1] == -0.05
    # B jumps (A/B falls: long A) while C falls (A/C rises: short A) on the same bar
    w, events = build(pairs=[["A", "B"], ["A", "C"]]).walk(frame(A=q, B=[100.0] * 8 + [106.4], C=[100.0] * 8 + [94.0]))
    assert len([e for e in events if e["action"] == "enter"]) == 2
    assert w["A"].iloc[-1] == 0.0 and w["B"].iloc[-1] == -0.05 and w["C"].iloc[-1] == 0.05


def test_the_leg_cap_and_the_gross_limit():
    q = [100.0] * 9
    w, events = build(sizing={"pair_weight": 0.5}, risk={"max_position_pct": 0.1}).walk(frame(A=BASE + [94], B=q))
    assert w["A"].iloc[-1] == pytest.approx(0.1) and w["B"].iloc[-1] == pytest.approx(-0.1) and events[0]["weight"] == pytest.approx(0.1)
    two = build(pairs=[["A", "B"], ["C", "D"]], risk={"max_gross": 0.1})
    w, events = two.walk(frame(A=BASE + [94], B=q, C=BASE + [94], D=q))
    assert [e["pair"] for e in events if e["action"] == "enter"] == ["A/B"]        # C/D would take the book to 0.2: refused
    assert w.iloc[-1].abs().sum() == pytest.approx(0.1) and w["A"].iloc[-1] == 0.05 and (w["C"] == 0).all()
    leg_invariant(two, w, events)


def test_a_missing_leg_keeps_the_clock_running_and_closes_the_pair_after_five_sessions():
    a = BASE + [94, 94.5, 94.2, 94.4, 94.3, 94.1, 94.2, 94.0]
    b = [100.0] * len(a)

    def gap(series, bars):
        out = list(series)
        for k in bars:
            out[k] = float("nan")
        return out

    w, events = build().walk(frame(A=a, B=gap(b, range(9, 14))))                # B unpriced on bars 9 to 13
    ex = trades(events)
    assert len(ex) == 1 and ex[0]["date"] == w.index[13] and ex[0]["reason"] == "missing leg: no price for 5 sessions"
    legs = [e for e in events if e["date"] == w.index[13]]
    assert [(e["action"], e["symbol"], e["price"], e.get("stale")) for e in legs] == [("exit", "A", a[13], None), ("exit_leg", "B", 100.0, True)]
    assert ex[0]["pnl_pct"] == pytest.approx(0.5 * (a[13] / 94 - 1))
    assert list(w["A"].iloc[8:13]) == [0.05] * 5 and (w.iloc[13:] == 0).all().all()
    w, events = build(risk={"max_holding_days": 3}).walk(frame(A=a, B=gap(b, [9, 10])))
    assert trades(events)[0]["date"] == w.index[11] and trades(events)[0]["reason"] == "time limit: held 3 days"    # the clock ran
    assert "stale" not in [e for e in events if e["action"] == "exit_leg"][0]
    w, events = build(risk={"max_holding_days": 3}).walk(frame(A=a, B=gap(b, [10, 11])))
    assert [(e["symbol"], e.get("stale"), e["price"]) for e in events if e["date"] == w.index[11]] == [("A", None, a[11]), ("B", True, 100.0)]
    a_fall = BASE + [94, 90, 91, 92, 93, 94]
    w, events = build(risk={"stop_loss_pct": 0.02}).walk(frame(A=a_fall, B=gap(b[:14], range(9, 13))))
    assert trades(events)[0]["date"] == w.index[9] and trades(events)[0]["reason"].startswith("stop loss")   # a stop while B is unpriced
    assert trades(events)[0]["pnl_pct"] == pytest.approx(0.5 * (90 / 94 - 1))
    w, events = build(risk={"max_holding_days": 5}).walk(frame(A=a, B=gap(b, range(9, 14))))
    assert trades(events)[0]["reason"] == "time limit: held 5 days"                # the time limit is checked first
    w, events = build(legs="long").walk(frame(A=a, B=gap(b, range(9, 14))))         # the leg not held counts too
    assert [(e["action"], e["symbol"], e.get("stale")) for e in events] == [("enter", "A", None), ("exit", "A", None)]
    assert events[1]["reason"].startswith("missing leg") and events[1]["date"] == w.index[13]
    w, events = build().walk(frame(A=gap(a, range(9, 14)), B=gap(b, range(9, 14))))
    assert [(e["price"], e["stale"]) for e in trades(events) + [e for e in events if e["action"] == "exit_leg"]] == [(94.0, True), (100.0, True)]
    assert trades(events)[0]["pnl_pct"] == 0.0
    w, events = build().walk(frame(A=a, B=gap(b, [9, 10, 11, 12, 14])))            # four, then a priced bar: the count restarts
    assert all(not e["reason"].startswith("missing leg") for e in trades(events))
    w, events = build().walk(frame(A=BASE + [94, 94], B=gap(b[:10], [8])))
    assert events == [] and (w == 0).all().all()                                 # no entry on a bar a leg has no price
    w, events = build(risk={"stop_loss_pct": 0.05}).walk(frame(A=BASE + [94, 0.0, 95, 99, 100], B=b[:13]))
    assert list(w["A"].iloc[8:]) == [0.05] * 5 and trades(events) == []        # a price of zero is no price: no stop,
    assert all(e["date"] == w.index[8] for e in events)                         # and no z while it is in the window


def _ols_pair(beta, n=320, seed=11):
    rng = np.random.default_rng(seed)
    lb = np.log(100.0) + np.cumsum(0.015 * rng.standard_normal(n))
    e = np.zeros(n)
    for t in range(1, n):
        e[t] = 0.7 * e[t - 1] + 0.004 * rng.standard_normal()
    la = beta * lb + (1 - beta) * np.log(100.0) + 0.3 + e
    return np.exp(la), np.exp(lb)


def test_ols_recovers_a_known_beta_sets_the_weights_from_it_and_refuses_a_beta_out_of_range():
    from valedia_trader.quant_engine.level5_timeseries import hedge_ratio_ols
    sig = {"hedge": "ols", "window": 20, "hedge_window": 120, "enter": 1.5, "exit": 0.3}
    a, b = _ols_pair(1.5)
    s = build(signal=sig, sizing={"pair_weight": 0.2}, risk={"max_position_pct": 0.5})
    w, events = s.walk(frame(A=a, B=b))
    entries = [e for e in events if e["action"] == "enter"]
    assert len(entries) >= 3
    for e in entries:
        t = w.index.get_loc(e["date"])
        beta, _ = hedge_ratio_ols(np.log(a[t - 119: t + 1]), np.log(b[t - 119: t + 1]))
        assert e["hedge"] == pytest.approx(beta, rel=1e-10) and abs(beta - 1.5) < 0.1
        side = e["side"]
        assert w["A"].iloc[t] == pytest.approx(side * 0.2 / (1 + beta)) and w["B"].iloc[t] == pytest.approx(-side * 0.2 * beta / (1 + beta))
    ex = trades(events)[0]
    t0, t1 = w.index.get_loc(entries[0]["date"]), w.index.get_loc(ex["date"])
    assert (w["A"].iloc[t0:t1] == w["A"].iloc[t0]).all()                         # weights fixed at entry
    for beta in (0.02, 15.0):
        a2, b2 = _ols_pair(beta)
        _, _, z2, betas = oracle(a2, b2, sig)
        assert sum(abs(v) >= 1.5 for v in z2 if v is not None) >= 3                # the spread did stretch ...
        assert all(not 0.1 <= v <= 10 for v in betas if v is not None)
        w2, events2 = build(signal=sig).walk(frame(A=a2, B=b2))
        assert events2 == [] and (w2 == 0).all().all()                           # ... but the hedge was out of range


# ---- the oracle


def _random_case(rng, hedge):
    n = int(rng.integers(90, 160))
    window = int(rng.integers(5, 15))
    sig = {"hedge": hedge, "window": window, "enter": float(rng.uniform(0.8, 2.2))}
    sig["exit"] = float(rng.uniform(0.0, 0.9) * sig["enter"])
    sig["stop_z"] = None if rng.random() < 0.5 else float(sig["enter"] + rng.uniform(0.2, 1.2))
    if hedge == "ols":
        sig["hedge_window"] = int(max(20, window + rng.integers(0, 30)))
    risk = {"max_position_pct": float(rng.uniform(0.05, 1.0)), "max_gross": float(rng.uniform(0.05, 1.2)),
            "stop_loss_pct": None if rng.random() < 0.4 else float(rng.uniform(0.003, 0.04)),
            "take_profit_pct": None if rng.random() < 0.5 else float(rng.uniform(0.003, 0.04)),
            "max_holding_days": None if rng.random() < 0.5 else int(rng.integers(1, 12))}
    sizing = {"pair_weight": float(rng.uniform(0.05, 1.0)), "max_open_pairs": int(rng.integers(1, 4))}
    legs = "both" if rng.random() < 0.6 else "long"
    lb = np.log(100.0) + np.cumsum(0.015 * rng.standard_normal(n))
    e = np.zeros(n)
    for t in range(1, n):
        e[t] = 0.75 * e[t - 1] + 0.01 * rng.standard_normal()
    beta = float(rng.choice([rng.uniform(0.4, 2.5), 0.05, 12.0], p=[0.8, 0.1, 0.1])) if hedge == "ols" else 1.0
    a, b = np.exp(beta * lb + (1 - beta) * np.log(100.0) + e), np.exp(lb)
    if hedge == "ratio" and rng.random() < 0.25:
        a, b = a * 1e-11, b * 1e3                                   # a ratio near 1e-14 must still trade (ruling 3)
    _holes(rng, a, b)
    return a, b, sig, legs, sizing, risk


def _holes(rng, *arrays):
    """Scattered missing and zero prices, and now and then a stretch of 3 to 8 unpriced bars (a halt)."""
    for arr in arrays:
        n = len(arr)
        arr[rng.random(n) < 0.03] = np.nan
        arr[rng.random(n) < 0.004] = 0.0
        if rng.random() < 0.35:
            g0 = int(rng.integers(20, n - 10))
            arr[g0: g0 + int(rng.integers(3, 9))] = np.nan


KINDS = {"stop loss", "take profit", "time limit", "missing leg", "z stop", "exit rule"}


@pytest.mark.parametrize("hedge", ["ratio", "log_ratio", "ols"])
def test_walk_agrees_with_a_slow_walker_written_from_the_rulings(hedge):
    rng = np.random.default_rng({"ratio": 101, "log_ratio": 202, "ols": 303}[hedge])
    total, kinds, skips = 0, set(), 0
    for i in range(200):
        a, b, sig, legs, sizing, risk = _random_case(rng, hedge)
        rows, closed, _, counts = oracle_book({"A": a, "B": b}, [("A", "B")], sig, legs, sizing, risk)
        s = build(signal={**{"stop_z": None}, **sig}, legs=legs, sizing=sizing, risk=risk)
        w, events = s.walk(frame(A=a, B=b))
        np.testing.assert_allclose(w[["A", "B"]].to_numpy(), np.array([(r["A"], r["B"]) for r in rows]), rtol=0, atol=1e-12,
                                   err_msg=f"case {i}")
        got = [(w.index.get_loc(e["date"]), e["reason"].split(":")[0], e["pnl_pct"]) for e in trades(events)]
        assert [g[:2] for g in got] == [(c[0], c[2]) for c in closed], f"case {i}"
        assert all(math.isclose(g[2], c[3], rel_tol=1e-9, abs_tol=1e-12) for g, c in zip(got, closed))
        assert counts["scaled"] == 0
        leg_invariant(s, w, events)
        total += len(closed)
        kinds |= {c[2] for c in closed}
        skips += counts["gross skip"]
    assert total >= 200 and kinds == KINDS and skips >= 20


def _random_book(rng, hedge):
    """Six symbols on three common factors, three to six pairs among them (shared symbols included)."""
    n = int(rng.integers(120, 200))
    window = int(rng.integers(5, 15))
    sig = {"hedge": hedge, "window": window, "enter": float(rng.uniform(0.8, 2.0))}
    sig["exit"] = float(rng.uniform(0.0, 0.9) * sig["enter"])
    sig["stop_z"] = None if rng.random() < 0.5 else float(sig["enter"] + rng.uniform(0.2, 1.2))
    if hedge == "ols":
        sig["hedge_window"] = int(max(20, window + rng.integers(0, 30)))
    factors = np.log(100.0) + np.cumsum(0.015 * rng.standard_normal((n, 3)), axis=0)
    px = {}
    for j in range(6):
        e = np.zeros(n)
        for t in range(1, n):
            e[t] = 0.75 * e[t - 1] + 0.01 * rng.standard_normal()
        beta = float(rng.uniform(0.5, 2.0)) if hedge == "ols" else 1.0
        px[f"S{j}"] = np.exp(beta * factors[:, j % 3] + (1 - beta) * np.log(100.0) + e)
    _holes(rng, *px.values())
    combos = [(f"S{i}", f"S{j}") for i in range(6) for j in range(i + 1, 6)]
    pairs = [combos[k] if rng.random() < 0.5 else combos[k][::-1] for k in rng.choice(len(combos), int(rng.integers(3, 7)), replace=False)]
    sizing = {"pair_weight": float(rng.uniform(0.05, 0.6)), "max_open_pairs": int(rng.integers(1, 5))}
    risk = {"max_position_pct": float(rng.uniform(0.03, 0.4)), "max_gross": float(rng.uniform(0.05, 1.0)),
            "stop_loss_pct": None if rng.random() < 0.4 else float(rng.uniform(0.003, 0.04)),
            "take_profit_pct": None if rng.random() < 0.5 else float(rng.uniform(0.003, 0.04)),
            "max_holding_days": None if rng.random() < 0.5 else int(rng.integers(1, 12))}
    return px, pairs, sig, ("both" if rng.random() < 0.6 else "long"), sizing, risk


@pytest.mark.parametrize("hedge", ["ratio", "log_ratio", "ols"])
def test_walk_agrees_with_the_slow_walker_on_universes_of_several_pairs(hedge):
    rng = np.random.default_rng({"ratio": 404, "log_ratio": 505, "ols": 606}[hedge])
    totals = {"trades": 0, "gross skip": 0, "position skip": 0, "missing leg": 0}
    kinds = set()
    for i in range(80):
        px, pairs, sig, legs, sizing, risk = _random_book(rng, hedge)
        rows, closed, _, counts = oracle_book(px, pairs, sig, legs, sizing, risk)
        s = build(pairs=[list(p) for p in pairs], signal={**{"stop_z": None}, **sig}, legs=legs, sizing=sizing, risk=risk)
        w, events = s.walk(frame(**px))
        np.testing.assert_allclose(w[list(px)].to_numpy(), np.array([[r[c] for c in px] for r in rows]), rtol=0, atol=1e-12,
                                   err_msg=f"case {i}")
        got = [(w.index.get_loc(e["date"]), e["pair"], e["reason"].split(":")[0]) for e in trades(events)]
        assert got == [(c[0], "/".join(pairs[c[1]]), c[2]) for c in closed], f"case {i}"
        assert counts["scaled"] == 0
        leg_invariant(s, w, events)
        totals["trades"] += len(closed)
        totals["gross skip"] += counts["gross skip"]
        totals["position skip"] += counts["position skip"]
        totals["missing leg"] += counts.get("missing leg", 0)
        kinds |= {c[2] for c in closed}
    assert kinds == KINDS and totals["trades"] >= 300 and min(totals.values()) >= 5, totals


# ---- through the hooks and the real consumers


def test_a_pairs_spec_round_trips_through_save_spec_and_load_strategy(tmp_path):
    for hedge in ("ratio", "log_ratio", "ols"):
        s = build(name=f"pairs_{hedge}", signal={"hedge": hedge, **({"hedge_window": 60} if hedge == "ols" else {})})
        path = st.save_spec(s, tmp_path)
        loaded = st.load_strategy(path)
        assert isinstance(loaded, pr.PairsStrategy) and loaded == s and loaded.to_spec() == json.loads(path.read_text())


def _universe(n=320, seed=5, gaps=0.02, halts=True):
    rng = np.random.default_rng(seed)
    base = np.log(100.0) + np.cumsum(0.015 * rng.standard_normal((n, 3)), axis=0)
    e = np.zeros((n, 3))
    for t in range(1, n):
        e[t] = 0.8 * e[t - 1] + 0.01 * rng.standard_normal(3)
    cols = {"A": base[:, 0] + e[:, 0], "B": base[:, 0], "C": 1.3 * base[:, 1] + e[:, 1] - 1.0, "D": base[:, 1],
            "E": base[:, 2] + e[:, 2], "F": base[:, 2]}
    px = pd.DataFrame({k: np.exp(v) for k, v in cols.items()}, index=pd.bdate_range("2020-01-01", periods=n))
    px = px.mask(rng.random(px.shape) < gaps)
    if halts:
        for col, start, length in (("B", 60, 6), ("D", 140, 7), ("F", 200, 5), ("C", 250, 8), ("A", 290, 6)):
            px.iloc[start: start + length, px.columns.get_loc(col)] = np.nan
    return px


def _universe_strategy(hedge):
    return build(name=f"universe_{hedge}", pairs=[["A", "B"], ["C", "D"], ["E", "F"], ["A", "C"]],
                 signal={"hedge": hedge, "window": 10, "enter": 1.4, "exit": 0.3, "stop_z": 3.0,
                         **({"hedge_window": 40} if hedge == "ols" else {})},
                 sizing={"pair_weight": 0.2, "max_open_pairs": 2},
                 risk={"max_position_pct": 0.08, "max_gross": 0.35, "stop_loss_pct": 0.03, "take_profit_pct": 0.03,
                       "max_holding_days": 12})


@pytest.mark.parametrize("hedge", ["ratio", "log_ratio", "ols"])
def test_the_walk_is_causal(hedge):
    from valedia_trader.quant_engine.level10_ml_research import detect_lookahead
    px, s = _universe(), _universe_strategy(hedge)
    assert detect_lookahead(s.target_weights, px) == [] and detect_lookahead(s.target_weights, px, n_checks=40) == []
    full_w, full_ev = s.walk(px)
    assert len(trades(full_ev)) >= 10
    leg_invariant(s, full_w, full_ev)
    rng = np.random.default_rng(7)
    halted = [k for k in range(len(px)) if px.iloc[k].isna().any() and k >= 60]
    missing_exits = [px.index.get_loc(e["date"]) for e in trades(full_ev) if e["reason"].startswith("missing leg")]
    assert missing_exits                                                          # a missing-leg exit to cut on
    cuts = sorted(set(rng.choice(np.arange(20, len(px) - 1), size=50, replace=False)) | set(halted[::2]) | set(missing_exits))
    assert len(cuts) >= 70
    for cut in cuts:
        part_w = s.target_weights(px.iloc[: cut + 1])
        pd.testing.assert_frame_equal(part_w, full_w.iloc[: cut + 1], check_exact=True)
        _, part_ev = s.walk(px.iloc[: cut + 1])
        assert part_ev == [e for e in full_ev if e["date"] <= px.index[cut]]


def test_run_rules_hands_over_to_walk_and_the_backtester_takes_the_weights():
    from valedia_trader.backtest.engine import CostModel, run_backtest
    px = _universe(gaps=0.0, halts=False)
    px["X"] = 50.0                                                                # a column outside the market
    for hedge in ("ratio", "log_ratio", "ols"):
        s = _universe_strategy(hedge)
        w, events = st.run_rules(s, px)
        ww, ee = s.walk(px)
        pd.testing.assert_frame_equal(w, ww, check_exact=True)
        assert events == ee and list(w.columns) == list(px.columns) and (w["X"] == 0).all()
        assert (w.abs().sum(axis=1) <= 0.35 + 1e-12).all() and (w.abs().sum(axis=1) > 0).any()
        assert all(e["weight"] <= 0.08 + 1e-12 for e in events if "weight" in e)
        leg_invariant(s, w, events)
        res = run_backtest(px, w, CostModel(fees_bps=1.0, slippage_bps=1.0, borrow_annual=0.02, funding_annual=0.0))
        assert np.isfinite(res.net.to_numpy()).all() and res.turnover.sum() > 0
    with pytest.raises(st.SpecError, match="price data"):
        st.run_rules(_universe_strategy("ratio"), px[["X"]])


def test_a_spread_that_does_not_move_gives_no_z_even_through_rounding():
    """An exact hedge leaves a residual of rounding noise (about 1e-16). Its deviation counts as zero: no z, no trade."""
    rng = np.random.default_rng(2)
    lb = np.log(100.0) + np.cumsum(0.02 * rng.standard_normal(200))
    a, b = np.exp(1.5 * lb + 0.3), np.exp(lb)
    sig = {"hedge": "ols", "window": 20, "hedge_window": 60, "enter": 1.5, "exit": 0.3}
    _, _, z, betas = oracle(a, b, sig)
    assert all(v is None for v in z) and all(abs(v - 1.5) < 1e-9 for v in betas if v is not None)
    w, events = build(signal=sig).walk(frame(A=a, B=b))
    assert events == [] and (w == 0).all().all()
    w, events = build().walk(frame(A=[100.0] * 30, B=[50.0] * 30))                  # a ratio that never moves
    assert events == [] and (w == 0).all().all()


# ---- addendum 1


def test_an_entry_that_would_take_a_symbols_net_weight_past_the_limit_is_skipped_for_the_next_candidate():
    q = [100.0] * 9
    cols = {"A": BASE + [90], "B": q, "C": q, "D": BASE + [97], "E": q}
    s = build(pairs=[["A", "B"], ["A", "C"], ["D", "E"]], sizing={"pair_weight": 0.2, "max_open_pairs": 2},
              risk={"max_position_pct": 0.10})
    z = z_ratio(cols["A"], q)[8], z_ratio(cols["D"], q)[8]
    assert z[0] < z[1] <= -1.5                                                    # A/B and A/C tie at the largest |z|
    w, events = s.walk(frame(**cols))
    assert [e["pair"] for e in events if e["action"] == "enter"] == ["A/B", "D/E"]   # A/C would put A at 0.20: skipped
    assert w["A"].iloc[-1] == pytest.approx(0.10) and w["D"].iloc[-1] == pytest.approx(0.10)
    assert (w.abs() <= 0.10 + 1e-12).all().all()
    alone = build(pairs=[["A", "C"]], sizing={"pair_weight": 0.2}, risk={"max_position_pct": 0.10}).walk(frame(A=cols["A"], C=q))[0]
    assert alone["A"].iloc[-1] == pytest.approx(0.10)                             # it fits on its own
    _, events = build(pairs=[["A", "B"], ["A", "C"]]).walk(frame(A=BASE + [90], B=q, C=q))
    assert len([e for e in events if e["action"] == "enter"]) == 2                # 0.05 + 0.05 = 0.10 is at the limit: allowed


def test_nothing_opens_beyond_the_z_stop_on_either_side():
    b = [100.0] * 9
    for tail, side in (([94], 1), ([106], -1)):                                   # |z| 1.73 on bar 8
        a = BASE + tail
        assert abs(z_ratio(a, b)[8]) == pytest.approx(1.73, abs=1e-2)
        w, events = build(signal={"stop_z": 1.7}).walk(frame(A=a, B=b))
        assert events == [] and (w == 0).all().all()
        w, events = build(signal={"stop_z": 1.8}).walk(frame(A=a, B=b))
        assert events[0]["action"] == "enter" and events[0]["side"] == side and w["A"].iloc[8] == 0.05 * side


def test_a_ratio_near_1e_minus_14_trades_as_the_same_series_at_ordinary_prices():
    a = BASE + [94, 90, 86, 82, 78, 85, 95, 100, 100.5, 100, 99.5, 100, 94, 95, 99, 100]
    b = [100.0 * (1 + 0.001 * k) for k in range(len(a))]
    sig, risk = {"enter": 1.2, "exit": 0.3}, {"stop_loss_pct": 0.02}
    _, ordinary = build(signal=sig, risk=risk).walk(frame(A=a, B=b))
    tiny_a, big_b = [x * 1e-11 for x in a], [x * 1e3 for x in b]                  # about 1e-9 and 1e5
    assert max(x / y for x, y in zip(tiny_a, big_b)) < 2e-14
    _, tiny = build(signal=sig, risk=risk).walk(frame(A=tiny_a, B=big_b))
    key = [(e["date"], e["action"], e["reason"].split(":")[0], e["side"]) for e in ordinary]
    assert len(key) >= 6 and key == [(e["date"], e["action"], e["reason"].split(":")[0], e["side"]) for e in tiny]
    w, events = build().walk(frame(A=[3e-9] * 30, B=[1e5] * 30))                  # a tiny ratio that never moves: no z
    assert events == [] and (w == 0).all().all()


def test_every_event_says_which_way_the_spread_was_traded():
    b = [100.0] * 18
    up = BASE + [106, 105, 103, 101, 100, 99, 100, 101, 99, 100]
    _, events = build(legs="long").walk(frame(A=up, B=b))                           # short the spread: only long B is held
    assert [(e["action"], e["symbol"], e["side"], e["spread_side"]) for e in events] == [("enter", "B", 1, -1), ("exit", "B", 1, -1)]
    _, events = build().walk(frame(A=up, B=b))
    assert [(e["symbol"], e["side"], e["spread_side"]) for e in events] == [("A", -1, -1), ("B", 1, -1)] * 2
    down = BASE + [94, 95, 97, 99, 100, 101, 100, 99, 101, 100]
    _, events = build().walk(frame(A=down, B=b))
    assert all(e["spread_side"] == 1 for e in events) and len(events) == 4


# ---- addendum 2 and 3


def test_an_open_ols_pair_is_judged_on_the_spread_it_holds():
    sig = {"hedge": "ols", "window": 20, "hedge_window": 120, "enter": 1.5, "exit": 0.3, "stop_z": 3.0}
    a, b = _ols_pair(1.5, n=600, seed=13)
    w, events = build(signal=sig, risk={"stop_loss_pct": None}).walk(frame(A=a, B=b))
    entries, exits = [e for e in events if e["action"] == "enter"], trades(events)
    assert len(exits) >= 4
    _, beta_bar = _bar_signal(a, b, sig)
    differ = 0
    for en, ex in zip(entries, exits):
        t0, t1 = w.index.get_loc(en["date"]), w.index.get_loc(ex["date"])
        beta0 = en["hedge"]
        assert ex["hedge"] == beta0 and beta0 == pytest.approx(beta_bar[t0], rel=1e-10)
        held = [_held_z(a, b, beta0, t, 20) for t in range(t0 + 1, t1 + 1)]
        side = en["spread_side"]
        fires = [z is not None and ((side > 0 and (z >= -0.3 or z <= -3.0)) or (side < 0 and (z <= 0.3 or z >= 3.0))) for z in held]
        assert fires[-1] and not any(fires[:-1])                                  # closes on the first bar its held z says so
        assert ex["z"] == pytest.approx(held[-1], rel=1e-9, abs=1e-12)
        bar_z = _bar_signal(a, b, sig)[0][t1]
        differ += bar_z is not None and abs(bar_z - held[-1]) > 1e-6
    assert differ >= 2                                                            # the bar's own fit would read differently


def test_re_arming_follows_the_closed_trades_own_exit_condition():
    b = [100.0] * 13
    sig, risk = {"enter": 1.2, "exit": 0.0}, {"stop_loss_pct": 0.02}
    a = BASE + [94, 90, 110, 125, 126]                       # z: -1.73 (enter long), -1.48 (stop), +1.51, +1.51, +1.01
    w, events = build(signal=sig, risk=risk).walk(frame(A=a, B=b))
    got = [(w.index.get_loc(e["date"]), e["action"], e["spread_side"]) for e in events if e["symbol"] == "A"]
    assert got == [(8, "enter", 1), (9, "exit", 1), (11, "enter", -1)]        # re-armed on bar 10 (z >= -exit), which opens nothing
    a = BASE + [94, 90, 95, 99, 100]                         # z after the stop: -0.15 (not yet >= 0), +0.84 (re-arm), +1.09
    w, events = build(signal={**sig, "enter": 1.0}, risk=risk).walk(frame(A=a, B=b))
    assert [(w.index.get_loc(e["date"]), e["action"]) for e in events if e["symbol"] == "A"] == [(8, "enter"), (9, "exit"), (12, "enter")]


def test_worked_case_one_an_offsetting_pair_is_refused_at_entry():
    q, n = [100.0] * 8, 12                                  # flat before bar 8, so the signals tie and the pair order decides
    px = frame(A=[100.0] * n, B=q + [106] * 4, C=q + [94, 94, 100, 100], D=q + [100, 106, 106, 106])
    s = build(pairs=[["A", "B"], ["A", "C"], ["A", "D"]], sizing={"pair_weight": 0.2, "max_open_pairs": 3},
              risk={"max_position_pct": 0.1, "max_gross": 0.3})
    w, events = s.walk(px)
    assert [e["pair"] for e in events if e["action"] == "enter" and e["date"] <= px.index[9]] == ["A/B"]
    # A/C (it would offset A/B) on bar 8 and A/D on bar 9 are refused at entry: each would put 0.2 of legs on A
    assert w["A"].abs().max() <= 0.1 + 1e-12
    leg_invariant(s, w, events)


def test_worked_case_two_the_book_limit_counts_legs_not_nets():
    q, n = [100.0] * 8, 12
    px = frame(A=[100.0] * n, B=q + [106] * 4, C=q + [94, 94, 100, 100], D=q + [88] * 4)
    s = build(pairs=[["A", "C"], ["A", "B"], ["C", "D"]], sizing={"pair_weight": 0.2, "max_open_pairs": 3},
              risk={"max_position_pct": 0.5, "max_gross": 0.3})
    w, events = s.walk(px)
    assert [e["pair"] for e in events if e["action"] == "enter" and e["date"] == px.index[8]] == ["A/C"]
    # A/B and C/D signal on bar 8 too; each would take the book's legs to 0.4 against 0.3
    assert trades(events) and (w.abs().sum(axis=1) <= 0.3 + 1e-12).all()
    leg_invariant(s, w, events)


def test_the_position_limit_counts_only_legs_actually_held():
    q = [100.0] * 9
    cols = {"A": BASE[:8] + [90.0], "B": q, "C": BASE[:8] + [80.0]}             # A cheap against B, dear against C
    long_only = build(pairs=[["A", "B"], ["A", "C"]], legs="long", sizing={"pair_weight": 0.2}, risk={"max_position_pct": 0.1})
    w, events = long_only.walk(frame(**cols))
    assert [(e["pair"], e["symbol"]) for e in events] == [("A/B", "A"), ("A/C", "C")] or \
        [(e["pair"], e["symbol"]) for e in events] == [("A/C", "C"), ("A/B", "A")]   # short A is not taken: no weight on A from A/C
    both = build(pairs=[["A", "B"], ["A", "C"]], sizing={"pair_weight": 0.2}, risk={"max_position_pct": 0.1})
    w, events = both.walk(frame(**cols))
    assert len([e for e in events if e["action"] == "enter"]) == 1                # with both legs A would carry 0.2 of legs
    leg_invariant(both, w, events)


def test_the_position_limit_passes_the_slot_on_after_two_fit():
    q = [100.0] * 9
    cols = {"A": BASE + [90], "B": q, "C": q, "F": q, "D": BASE + [97], "E": q}
    s = build(pairs=[["A", "B"], ["A", "C"], ["A", "F"], ["D", "E"]], sizing={"pair_weight": 0.08, "max_open_pairs": 3},
              risk={"max_position_pct": 0.1})
    w, events = s.walk(frame(**cols))
    assert [e["pair"] for e in events if e["action"] == "enter"] == ["A/B", "A/C", "D/E"]   # A/F would make A's legs 0.12
    assert w["A"].iloc[-1] == pytest.approx(0.08)
    leg_invariant(s, w, events)


def test_the_one_leg_cap_with_an_ols_hedge_keeps_the_hedge_proportion():
    sig = {"hedge": "ols", "window": 20, "hedge_window": 120, "enter": 1.5, "exit": 0.3}
    a, b = _ols_pair(3.0)
    s = build(signal=sig, sizing={"pair_weight": 0.4}, risk={"max_position_pct": 0.1})
    w, events = s.walk(frame(A=a, B=b))
    entries = [e for e in events if e["action"] == "enter"]
    assert entries
    for e in entries:
        t, beta0 = w.index.get_loc(e["date"]), e["hedge"]
        assert abs(beta0 - 3.0) < 0.3 and abs(w["B"].iloc[t]) == pytest.approx(0.1) and abs(w["A"].iloc[t]) == pytest.approx(0.1 / beta0)


def test_the_one_leg_cap_on_the_long_leg_and_at_its_boundary():
    q = [100.0] * 9
    w, events = build(legs="long", sizing={"pair_weight": 0.5}, risk={"max_position_pct": 0.1}).walk(frame(A=BASE + [94], B=q))
    assert w["A"].iloc[-1] == pytest.approx(0.1) and (w["B"] == 0).all() and events[0]["weight"] == pytest.approx(0.1)
    w, _ = build(sizing={"pair_weight": 0.2}, risk={"max_position_pct": 0.1}).walk(frame(A=BASE + [94], B=q))
    assert w["A"].iloc[-1] == 0.1 and w["B"].iloc[-1] == -0.1                     # exactly at the cap: not scaled
    w, _ = build(sizing={"pair_weight": 0.2}, risk={"max_position_pct": 0.09}).walk(frame(A=BASE + [94], B=q))
    assert w["A"].iloc[-1] == pytest.approx(0.09) and w["B"].iloc[-1] == pytest.approx(-0.09)


def test_a_price_column_holding_text_raises_as_run_rules_does():
    px = frame(A=BASE * 3, B=BASE * 3).astype(object)
    px.iloc[5, 0] = "halted"
    rule = st.RuleStrategy.from_spec({"name": "hold_text", "version": "1.0.0", "hypothesis": "Holding every fund all the time",
                                      "market": ["A", "B"], "entry": {"rule": "hold", "enter": 1, "exit": 0}})
    with pytest.raises(ValueError, match="could not convert string to float"):
        st.run_rules(rule, px)
    with pytest.raises(ValueError, match="could not convert string to float"):
        build().walk(px)
