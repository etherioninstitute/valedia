#!/usr/bin/env python3
"""Offline walk-forward/calibration/ablation harness for ERE v3 (ere/2).

Requires numpy, scipy, pandas. No network. See PROTOCOL.md and --help.
The supplied OHLCV alone does NOT reproduce Valedia's strategy or fills.
"""
from __future__ import annotations
import argparse
from dataclasses import dataclass, asdict, replace
from pathlib import Path
import hashlib
import json
import math
import platform
import sys
import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view as swv
from etherion_risk import EREConfig, score_series, update_baseline, assess


@dataclass(frozen=True)
class Plan:
    # Frozen ex ante experimental design; not fitted to holdout.
    train_start: str = "2017-01-01"
    dev_start: str = "2020-01-01"
    test_start: str = "2024-01-01"
    last_date: str = "2026-09-30"
    short: int = 10
    reference: int = 120
    half_life: float = 5.0
    dynamics_window: int = 60
    propagation_horizon: int = 5
    ridge: float = 0.1
    min_reference_scores: int = 252
    max_history: int = 4096
    cut_grid: tuple = (0.90, 0.95, 0.975, 0.99, 0.995)
    halt_grid: tuple = (0.995, 0.999, 1.0)
    false_cut_budget: float = 0.10
    false_halt_budget: float = 0.01
    null_seed: int = 841131
    null_replications: int = 20
    null_baseline_n: int = 4096
    null_test_n: int = 5000
    null_cut_grid: tuple = (0.90, 0.95, 0.975)
    null_margin: float = 0.5  # independent pre-holdout synthetic screen; reserve half cut budget
    event_lead_bars: int = 10
    correlation_cap: float = 0.5
    capital: float = 500.0
    crypto_cost: float = 0.0015  # attached CE-3: 10bp fee + 5bp slippage, per side
    equity_cost: float = 0.0002  # assumption: fee_engine.py not supplied
    execution_lag_bars: int = 1  # next open; also report an extra full-bar latency sensitivity
    min_order: float = 1.0       # experimental friction, not verified venue minimum
    currency_quantum: float = 0.01
    cvar_level: float = 0.95
    bootstrap_draws: int = 1000
    bootstrap_blocks: tuple = (5, 20, 60)
    confidence: float = 0.95
    seed: int = 20261001
    tol: float = 1e-12


EVENTS = {
    "COVID": ("2020-02-19", "2020-04-07"),
    "LUNA": ("2022-05-05", "2022-05-20"),
    "FTX": ("2022-11-06", "2022-11-21"),
    "CARRY": ("2024-07-31", "2024-08-09"),
}
BOOKS = {
    "CRYPTO": {"BTC-USD": .40, "ETH-USD": .30, "XRP-USD": .15, "LINK-USD": .10, "ADA-USD": .05},
    "EQUITY": {"SPY": .35, "QQQ": .20, "IWM": .15, "TLT": .15, "GLD": .10, "XLE": .05},
}
CANDIDATES = ("downside_mean", "downside_rms", "tail_energy", "covariance_amplification",
              "finite_horizon_var", "variance_growth", "spectral_roughness", "loss_innovation")
COMPARATORS = ("vol_target", "correlation_cap", "drawdown_stop")
METRICS = ("log_growth_ann", "sharpe", "max_drawdown", "cvar95", "turnover_ann", "time_in_cut")


def dump(path, obj):
    def encode(x):
        if isinstance(x, np.generic):
            return x.item()
        if isinstance(x, (np.ndarray, pd.Index)):
            return x.tolist()
        if isinstance(x, (pd.Timestamp, Path)):
            return str(x)
        raise TypeError(type(x).__name__)
    Path(path).write_text(json.dumps(obj, indent=2, default=encode, allow_nan=False) + "\n")


def load_prices(path, out):
    df = pd.read_csv(path, parse_dates=["date"])
    required = {"ticker", "date", "open", "high", "low", "close", "adj_close", "volume"}
    if not required.issubset(df.columns) or df.duplicated(["ticker", "date"]).any():
        raise ValueError("Missing columns or duplicate ticker/date rows")
    nums = df[list(required - {"ticker", "date"})].to_numpy(float)
    if not np.isfinite(nums).all() or (df[["open", "high", "low", "close", "adj_close"]] <= 0).any().any():
        raise ValueError("Non-finite/nonpositive prices; no silent cleaning")
    df = df.sort_values(["ticker", "date"])
    ret = df.groupby("ticker").adj_close.pct_change(fill_method=None)
    jumps = df.loc[ret.abs() > 1, ["ticker", "date", "close", "adj_close"]].copy()
    jumps["simple_return"] = ret[ret.abs() > 1]
    jumps.to_csv(out / "data_jumps.csv", index=False)
    excluded = df.ticker.isin(["ADA-USD", "MARA"]) & (df.date < "2018-01-01")
    info = {"input_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
            "rows": len(df), "tickers": sorted(df.ticker.unique()), "ticker_count": df.ticker.nunique(),
            "start": str(df.date.min().date()), "end": str(df.date.max().date()),
            "duplicates": 0, "missing_numeric": 0, "excluded_rows": int(excluded.sum()),
            "excluded_policy": "ADA-USD and MARA through 2017-12-31; no winsorization",
            "large_jump_policy": "Other >100% jumps retained and reported; not independently verified",
            "VIX_policy": "Both VIX and ^VIX are nontradable and excluded from capital allocation"}
    dump(out / "data_audit.json", info)
    return df.loc[~excluded].copy(), info


def panel(df, book, name, plan):
    assets = list(book)
    crypto = [x.endswith("-USD") for x in assets]
    if any(crypto) and not all(crypto):
        raise ValueError("Mixed UTC/US-close panel prohibited; synchronize intraday data first")
    prices = df[df.ticker.isin(assets)].copy()
    prices["adj_open"] = prices.open * prices.adj_close / prices.close
    close = prices.pivot(index="date", columns="ticker", values="adj_close").reindex(columns=assets)
    op = prices.pivot(index="date", columns="ticker", values="adj_open").reindex(columns=assets)
    if close.empty or close.notna().sum().min() == 0:
        raise ValueError("Missing required asset")
    begin = max(close[c].first_valid_index() for c in assets)
    finish = min(close[c].last_valid_index() for c in assets)
    if all(crypto):
        expected = pd.date_range(begin, finish, freq="D")
        calendar = None
    else:
        import exchange_calendars as xc
        calendar = xc.get_calendar("XNYS", start=str(begin.date()), end=str(finish.date()))
        expected = calendar.sessions
    close, op = close.reindex(expected), op.reindex(expected)
    missing = close.isna().any(axis=1) | op.isna().any(axis=1)
    # A missing historical price cannot be booked as zero PnL. Use only the
    # uninterrupted terminal segment, and disclose precisely what was discarded.
    discarded_until = None
    if missing.any():
        last_gap = np.flatnonzero(missing.to_numpy())[-1]
        discarded_until = str(expected[last_gap].date())
        close, op = close.iloc[last_gap+1:], op.iloc[last_gap+1:]
    if len(close) < plan.reference + plan.short + plan.min_reference_scores:
        raise ValueError("Insufficient uninterrupted history")
    r = close.pct_change(fill_method=None).to_numpy()[1:]
    assert np.isfinite(r).all()
    ann = 365.25 if all(crypto) else 252.0
    rates = np.array([plan.crypto_cost if x else plan.equity_cost for x in crypto])
    cfg = EREConfig(assets=tuple(assets), short=plan.short, reference=plan.reference,
                    half_life=plan.half_life, min_scores=plan.min_reference_scores)
    dates = close.index
    # Actual bar endpoint: Yahoo crypto row labels START of UTC day; equities
    # have a 16:00 America/New_York endpoint (including daylight-saving changes).
    if all(crypto):
        stamps = (dates.tz_localize("UTC") + pd.Timedelta(days=1))
    else:
        stamps = pd.DatetimeIndex(calendar.schedule.loc[dates, "close"])
    return {"name": name, "assets": assets, "weights": np.array(list(book.values())),
            "close": close.to_numpy(), "open": op.to_numpy(), "returns": r,
            "dates": dates, "stamps": [x.isoformat() for x in stamps], "ann": ann,
            "rates": rates, "cfg": cfg, "discarded_until": discarded_until,
            "missing_rows": int(missing.sum())}


def features(pan, p):
    r, w = pan["returns"], pan["weights"]
    port = r @ w
    n, h, ref = len(port), p.short, p.reference
    total = h + ref
    x = swv(port, total)
    s = x[:, ref:]
    old = x[:, :ref]
    sd = np.sqrt(np.mean(old**2, axis=1))
    a = np.exp2(-np.arange(h-1, -1, -1) / p.half_life); a /= a.sum()
    loss = np.maximum(-s, 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        raw = {
            "downside_mean": score_series(r, w, pan["cfg"]),
            "downside_rms": np.sqrt(loss**2 @ a) / sd,
            "vol_target": np.sqrt(s**2 @ a) / sd,
            "tail_energy": np.max(loss**2, axis=1) / np.mean(s**2, axis=1),
            "variance_growth": np.log(np.mean(s**2, axis=1) / np.mean(x[:, ref-h:ref]**2, axis=1)),
            "loss_innovation": loss[:, -1]**2 / sd**2,
        }
    full = {k: np.r_[np.full(total-1, np.nan), v] for k, v in raw.items()}
    for k in ("covariance_amplification", "finite_horizon_var", "spectral_roughness", "correlation_cap", "drawdown_stop"):
        full[k] = np.full(n, np.nan)
    for t in range(total-1, n):
        z = r[t-h+1:t+1]
        cov = np.atleast_2d(np.cov(z, rowvar=False))
        var = np.diag(cov)
        diag = float(np.sum(w*w*var))
        full["covariance_amplification"][t] = float(w @ cov @ w / diag) if diag > 0 else np.nan
        if len(w) > 1 and np.all(var > 0):
            corr = cov / np.sqrt(np.outer(var, var))
            pair = np.outer(w, w); np.fill_diagonal(pair, 0)
            full["correlation_cap"][t] = np.sum(pair*corr) / np.sum(np.abs(pair))
        else:
            full["correlation_cap"][t] = 0.0  # N=1: explicitly not applicable
        block = r[t-p.dynamics_window+1:t+1]
        sigma = block.std(axis=0)
        if np.all(sigma > 0):
            z = (block - block.mean(axis=0)) / sigma
            x0, y0 = z[:-1], z[1:]
            A = np.linalg.solve(x0.T@x0 / len(x0) + p.ridge*np.eye(len(w)), x0.T@y0 / len(x0)).T
            gain = np.linalg.norm(np.linalg.matrix_power(A, p.propagation_horizon), ord=2)
            full["finite_horizon_var"][t] = np.log(max(gain, p.tol))/p.propagation_horizon
        q = port[t-p.dynamics_window+1:t+1]
        f = np.fft.rfft((q-q.mean()) * np.hanning(len(q)))[1:]
        power = np.abs(f)**2
        full["spectral_roughness"][t] = power[len(power)//2:].sum()/power.sum() if power.sum() else np.nan
        if np.all(q > -1):
            path = np.r_[0, np.cumsum(np.log1p(q))]
            full["drawdown_stop"][t] = (path.max()-path[-1])/(sd[t-total+1]*np.sqrt(len(q)))
    # Return features aligned to PRICE rows (first price has no return).
    return {k: np.r_[np.nan, v] for k, v in full.items()}


def event_labels(dates, plan):
    labels = np.zeros(len(dates), dtype=bool)
    for start, end in EVENTS.values():
        a = dates.searchsorted(pd.Timestamp(start))
        b = dates.searchsorted(pd.Timestamp(end), side="right")
        labels[max(0, a-plan.event_lead_bars):b] = True
    return labels


def annual_references(pan, feat, plan):
    refs = {}
    dates = pan["dates"]
    # Embargo maximum tested recent horizon, stronger than the core's 10-bar
    # exclusion, to cover the 60-bar drawdown/VAR/spectral comparators as well.
    embargo = max(plan.short, plan.dynamics_window)
    for year in range(pd.Timestamp(plan.dev_start).year, pd.Timestamp(plan.last_date).year+1):
        start = dates.searchsorted(pd.Timestamp(f"{year}-01-01"))
        end = start - embargo
        raw_start = max(0, end-1-plan.max_history)
        lo = raw_start + plan.reference + plan.short
        if end <= lo:
            continue
        mask = dates.year == year
        for name, vals in feat.items():
            sample = vals[lo:end]
            sample = sample[np.isfinite(sample)]
            if len(sample) >= plan.min_reference_scores:
                refs[year, name] = sample
    return refs


def gate_path(pan, feat, refs, feature, cut_q, halt_q, plan, cost_mult=1.0):
    dates = pan["dates"]
    value = feat[feature]
    m = np.full(len(dates), np.nan)
    lows = np.full(len(dates), np.nan)
    highs = np.full(len(dates), np.nan)
    for year in np.unique(dates.year):
        sample = refs.get((year, feature))
        if sample is None:
            continue
        low = float(np.quantile(sample, cut_q)); high = float(np.quantile(sample, halt_q))
        mask = (dates.year == year) & np.isfinite(value)
        v = value[mask]
        if feature == "vol_target":
            mm = np.minimum(1, low/np.maximum(v, plan.tol))
        elif feature == "correlation_cap":
            mm = np.where(v > low, plan.correlation_cap, 1.0)
        elif feature == "drawdown_stop":
            mm = np.where(v > low, 0.0, 1.0)
        elif high-low <= plan.tol:
            mm = np.ones(len(v))  # constant N=1 covariance proxy is not applicable
        else:
            mm = np.clip((high-v)/(high-low), 0, 1)
        m[mask] = mm; lows[mask] = low; highs[mask] = high
    return m, lows, highs


def null_calibration(plan):
    """Reproduce the independent pre-holdout null screen, including RNG order."""
    rng = np.random.default_rng(plan.null_seed)
    rows = []
    for kind in ("gaussian", "t3"):
        for q in plan.null_cut_grid:
            counts = np.zeros(3)
            for replication in range(plan.null_replications):
                cfg = EREConfig(cut_q=q, halt_q=1.0, short=plan.short,
                                reference=plan.reference, half_life=plan.half_life)
                draw = ((lambda n: rng.normal(0,.01,n)) if kind == "gaussian" else
                        (lambda n: rng.standard_t(3,n)*.005))
                b, x = draw(plan.null_baseline_n), draw(plan.null_test_n)
                past, test = score_series(b,[1.],cfg), score_series(x,[1.],cfg)
                low, high = np.quantile(past,[q,1.])
                counts += ((test>low).sum(), (test>=high).sum(), len(test))
            rows.append(dict(distribution=kind, cut_q=q, halt_q=1.0,
                cuts=int(counts[0]), halts=int(counts[1]), observations=int(counts[2]),
                false_cut=counts[0]/counts[2], false_halt=counts[1]/counts[2]))
    return pd.DataFrame(rows)


def calibrate(panels, feats, refs, plan):
    table, selected = [], {}
    for name in CANDIDATES + COMPARATORS:
        trials = []
        high_grid = plan.halt_grid if name in CANDIDATES else (1.0,)
        for q in plan.cut_grid + ((.999,) if name == "drawdown_stop" else ()):
            for hq in high_grid:
                if q >= hq:
                    continue
                counts = np.zeros(6)  # normal cut, normal halt, normal n, true cut, positive n, all cut
                per_book = []
                for key, pan in panels.items():
                    mm, _, _ = gate_path(pan, feats[key], refs[key], name, q, hq, plan)
                    valid = (pan["dates"] >= plan.dev_start) & (pan["dates"] < plan.test_start) & np.isfinite(mm)
                    y = event_labels(pan["dates"], plan)
                    normal, positive = valid & ~y, valid & y
                    cuts, halts = mm < 1-plan.tol, mm <= plan.tol
                    c = np.array([np.sum(cuts & normal), np.sum(halts & normal), normal.sum(),
                                  np.sum(cuts & positive), positive.sum(), np.sum(cuts & valid)],float)
                    counts += c
                    per_book.append((key, c[0]/max(c[2],1), c[1]/max(c[2],1)))
                fp, fh = counts[0]/counts[2], counts[1]/counts[2]
                recall = counts[3]/max(counts[4],1)
                feasible = (q >= 1 - plan.false_cut_budget * plan.null_margin and
                            all(fc <= plan.false_cut_budget and hh <= plan.false_halt_budget for _,fc,hh in per_book))
                row = dict(component=name, cut_q=q, halt_q=hq, false_cut=fp, false_halt=fh,
                           recall=recall, feasible=feasible, positive_n=int(counts[4]),
                           normal_n=int(counts[2]), worst_book_false_cut=max(c[1] for c in per_book),
                           worst_book_false_halt=max(c[2] for c in per_book))
                table.append(row); trials.append(row)
        feasible = [x for x in trials if x["feasible"]]
        pool = feasible or trials
        best = sorted(pool, key=lambda x: (-x["recall"],x["false_cut"],x["false_halt"],-x["halt_q"]))[0] if feasible else sorted(pool,key=lambda x:(x["false_halt"],x["false_cut"],-x["recall"]))[0]
        selected[name] = {**best, "authorized_by_roc": bool(feasible),
                         "calibration_start":plan.dev_start,"calibration_end":"2023-12-31",
                         "provenance":"Chronological past-only annual reference; development labels only"}
    return pd.DataFrame(table), selected


def simulate(pan, m, start, end, plan, cost_multiplier=1.0, signal_weights=None):
    """Long-only, actual self-financing cash/holding ledger; next-open execution.

    Optional T-by-N signal_weights are desired *ungated* NAV fractions known
    after each close. This supports strategy replay without fabricating alpha.
    They must be generated causally outside this harness. It is still a target
    overlay, NOT equivalent to Valedia's per-new-trade Kelly ceiling.
    """
    dates, op, w = pan["dates"], pan["open"], pan["weights"]
    rate = pan["rates"] * cost_multiplier
    if (rate >= 1).any() or (rate < 0).any():
        raise ValueError("Invalid one-way friction")
    eligible = np.flatnonzero((dates >= start) & (dates <= end) & np.isfinite(m))
    lag = plan.execution_lag_bars
    if type(lag) is not int or lag < 1:
        raise ValueError("Execution must lag the signal by at least one price row")
    eligible = eligible[eligible < len(dates)-lag-1]
    if not len(eligible):
        raise ValueError("No evaluable signals")
    # No bridging missing gate days: the caller must select a complete segment.
    if not np.all(np.diff(eligible) == 1):
        raise ValueError("Missing gate observations inside evaluation")
    holding = np.zeros(len(w)); cash = plan.capital
    rows = []
    for s in eligible:
        equity = cash + holding.sum()
        base = w if signal_weights is None else np.asarray(signal_weights[s])
        if not np.isfinite(base).all() or np.any(base < 0) or base.sum() > 1+plan.tol:
            raise ValueError("Replay targets outside long-only unlevered scope")
        target = base * m[s] * equity
        delta = target - holding
        traded = 0.0; fees = 0.0
        # Sales first; all risk-reducing liquidation at HALT is allowed, even dust.
        sell = np.maximum(-delta,0)
        sell[(sell < plan.min_order) & (m[s] > 0)] = 0
        sell = np.minimum(sell,holding)
        selling_cost = sell @ rate
        holding -= sell; cash += sell.sum()-selling_cost
        fees += selling_cost; traded += sell.sum()
        buy = np.maximum(target-holding,0)
        buy[buy < plan.min_order] = 0
        required = np.sum(buy*(1+rate))
        if required > cash and required > 0:
            buy *= max(cash,0)/required
        buy = np.floor(buy/plan.currency_quantum)*plan.currency_quantum
        buy[buy < plan.min_order] = 0
        buying_cost = buy @ rate
        holding += buy; cash -= buy.sum()+buying_cost
        fees += buying_cost; traded += buy.sum()
        if cash < -plan.tol or (holding < -plan.tol).any():
            raise AssertionError("Ledger not self-financing")
        holding *= op[s+lag+1]/op[s+lag]
        end_equity = cash+holding.sum()
        rows.append([str(dates[s].date()),str(dates[s+lag].date()),str(dates[s+lag+1].date()),
                     end_equity/equity-1,traded/equity,m[s],fees,equity,end_equity])
    # Liquidate at final mark, not at the prior signal's close. Include exit fees.
    liquidation_fee = holding @ rate
    rows[-1][3] = (rows[-1][8]-liquidation_fee)/rows[-1][7]-1
    rows[-1][4] += holding.sum()/rows[-1][7]
    rows[-1][6] += liquidation_fee; rows[-1][8] -= liquidation_fee
    result = pd.DataFrame(rows,columns=["signal_date","execution_date","mark_date","net_return",
                          "turnover","multiplier","cost_dollars","starting_equity","ending_equity"])
    if (result.net_return <= -1).any():
        raise ValueError("Bankruptcy in evaluation")
    return result


def metrics(record, ann, plan):
    if isinstance(record,pd.DataFrame):
        a = record[["net_return","turnover","multiplier"]].to_numpy()
    else:
        a = np.asarray(record)
    r, turn, m = a.T
    logs = np.log1p(r)
    path = np.r_[0,np.cumsum(logs)]
    dd = 1-np.exp(path-np.maximum.accumulate(path))
    losses = np.sort(-r)[::-1]
    mass = len(r)*(1-plan.cvar_level)
    k = int(np.floor(mass)); fraction = mass-k
    cvar = (losses[:k].sum()+(fraction*losses[k] if k<len(r) else 0))/mass
    return {"log_growth_ann":float(logs.mean()*ann),
            "sharpe":float(r.mean()/r.std(ddof=1)*np.sqrt(ann)) if r.std() else 0.0,
            "max_drawdown":float(dd.max()),"cvar95":float(cvar),
            "turnover_ann":float(turn.mean()*ann),"time_in_cut":float(np.mean(m<1-plan.tol)),
            "halt_rate":float(np.mean(m<=plan.tol)),"n_bars":len(r)}


def bootstrap(records, ann, plan, out, book, phase):
    rng = np.random.default_rng(plan.seed)
    names = list(records)
    a = np.stack([records[n][["net_return","turnover","multiplier"]].to_numpy() for n in names])
    n = a.shape[1]
    points = {name:metrics(a[j],ann,plan) for j,name in enumerate(names)}
    rows = []
    for block in plan.bootstrap_blocks:
        b = np.empty((plan.bootstrap_draws,len(names),len(METRICS)))
        for i in range(plan.bootstrap_draws):
            starts = rng.integers(0,n,size=math.ceil(n/block))
            index = ((starts[:,None]+np.arange(block))%n).ravel()[:n]
            for j in range(len(names)):
                met = metrics(a[j,index],ann,plan)
                b[i,j] = [met[k] for k in METRICS]
        alpha = (1-plan.confidence)/2
        for j,name in enumerate(names):
            for k,metric in enumerate(METRICS):
                lo,hi = np.quantile(b[:,j,k],(alpha,1-alpha))
                rows.append(dict(book=book,phase=phase,block=block,method=name,metric=metric,
                                 comparison="absolute",estimate=points[name][metric],lower=lo,upper=hi))
                for refname in names:
                    if name != "ERE" or refname == "ERE":
                        continue
                    jj=names.index(refname)
                    lo,hi=np.quantile(b[:,j,k]-b[:,jj,k],(alpha,1-alpha))
                    rows.append(dict(book=book,phase=phase,block=block,method=name,metric=metric,
                        comparison=refname,estimate=points[name][metric]-points[refname][metric],lower=lo,upper=hi))
    frame=pd.DataFrame(rows)
    frame.to_csv(out/f"ci_{book}_{phase}.csv",index=False)
    return frame


def lead_lag(pan, paths, plan):
    rows=[]; dates=pan["dates"]
    for event,(start,end) in EVENTS.items():
        i=dates.searchsorted(pd.Timestamp(start)); j=dates.searchsorted(pd.Timestamp(end),side="right")
        for name,m in paths.items():
            lo=max(0,i-plan.event_lead_bars)
            hit=np.flatnonzero((m[lo:j]<1-plan.tol)&np.isfinite(m[lo:j]))+lo
            first=int(hit[0]) if len(hit) else None
            rows.append(dict(book=pan["name"],event=event,component=name,onset=start,end=end,
                 phase="holdout" if start>=plan.test_start else "development/in-sample-thresholds",
                 first_signal_date=str(dates[first].date()) if first is not None else "NONE",
                 lag_calendar_days=(dates[first]-pd.Timestamp(start)).days if first is not None else None,
                 lag_observed_bars=first-i if first is not None else None,
                 signal_available_at=pan["stamps"][first] if first is not None else "NONE",
                 execution_session=str(dates[first+1].date()) if first is not None and first+1<len(dates) else "NONE",
                 lag_note="Session-label lag; crypto signal is available next UTC midnight, equities after session close",
                 left_censored=bool(first==lo) if first is not None else False,
                 event_cut_fraction=float(np.mean(m[i:j]<1-plan.tol)) if j>i else None))
    return rows


def core_parity(pan, feats, refs, selected, plan, out):
    # Ensures the harness's downside_mean gate is not a silently different engine.
    chosen=selected["downside_mean"]
    cfg=replace(pan["cfg"],cut_q=chosen["cut_q"],halt_q=chosen["halt_q"])
    m,_,_=gate_path(pan,feats,refs,"downside_mean",cfg.cut_q,cfg.halt_q,plan)
    rows=[]
    for year in range(2020,2027):
        ids=np.flatnonzero((pan["dates"].year==year)&np.isfinite(m))
        if not len(ids): continue
        start=pan["dates"].searchsorted(pd.Timestamp(f"{year}-01-01"))
        end=start-max(plan.short,plan.dynamics_window)
        # features price row t uses return row t-1; baseline ends at price row end-1
        lo=max(0,end-1-plan.max_history)
        b=update_baseline(None,pan["returns"][lo:end-1],pan["stamps"][lo+1:end],cfg)
        for t in ids[::max(1,len(ids)//12)]:
            res=assess(pan["returns"][:t],pan["stamps"][1:t+1],pan["weights"],b,cfg)
            error=abs(res.multiplier-m[t])
            rows.append(dict(year=year,date=str(pan["dates"][t].date()),quality=res.data_quality,error=error))
            if res.data_quality!="OK" or error>1e-10:
                raise AssertionError((pan["name"],year,t,res,m[t],error))
    pd.DataFrame(rows).to_csv(out/f"core_parity_{pan['name']}.csv",index=False)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv",required=True);ap.add_argument("--out",default="ere_validation")
    ap.add_argument("--phase",choices=["develop","all"],default="all")
    ap.add_argument("--bootstrap",type=int,default=1000)
    ap.add_argument("--single-assets",action="store_true")
    ap.add_argument("--fees-json",help='Optional {"crypto_cost":.0015,"equity_cost":.0002,"min_order":1}')
    args=ap.parse_args();out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    plan=replace(Plan(),bootstrap_draws=args.bootstrap)
    if args.fees_json:
        fees=json.loads(Path(args.fees_json).read_text());plan=replace(plan,**fees)
    dump(out/"plan.json",asdict(plan))
    null_calibration(plan).to_csv(out/"null_calibration.csv",index=False)
    data,audit=load_prices(args.csv,out)
    pans={k:panel(data,b,k,plan) for k,b in BOOKS.items()}
    feat={k:features(v,plan) for k,v in pans.items()}
    refs={k:annual_references(v,feat[k],plan) for k,v in pans.items()}
    roc,selected=calibrate(pans,feat,refs,plan)
    roc.to_csv(out/"roc_grid.csv",index=False);dump(out/"frozen_thresholds.json",selected)
    paths={}
    ablations=[]
    for key,pan in pans.items():
        paths[key]={}
        for name,s in selected.items():
            m,low,high=gate_path(pan,feat[key],refs[key],name,s["cut_q"],s["halt_q"],plan)
            paths[key][name]=m
            rec=simulate(pan,m,plan.dev_start,"2023-12-31",plan)
            ablations.append(dict(book=key,method=name,authorized_by_roc=s["authorized_by_roc"],**metrics(rec,pan["ann"],plan)))
        no=np.where(np.isfinite(paths[key]["downside_mean"]),1.,np.nan)
        rec=simulate(pan,no,plan.dev_start,"2023-12-31",plan)
        ablations.append(dict(book=key,method="no_gate",authorized_by_roc=True,**metrics(rec,pan["ann"],plan)))
        core_parity(pan,feat[key],refs[key],selected,plan,out)
    abl=pd.DataFrame(ablations);abl.to_csv(out/"development_ablation.csv",index=False)
    # Predeclared strict cross-book incremental-value screen. Do not silently
    # relax it after seeing holdout. Best candidate remains research-only if none pass.
    passed=[]
    for name in CANDIDATES:
        ok=selected[name]["authorized_by_roc"]
        for key in pans:
            sub=abl[abl.book==key].set_index("method")
            a=sub.loc[name];no=sub.loc["no_gate"]
            ok &= (a.log_growth_ann>sub.loc[list(COMPARATORS)+["no_gate"],"log_growth_ann"].max()
                   and a.max_drawdown<no.max_drawdown and a.cvar95<no.cvar95)
        if ok:passed.append(name)
    ranked=abl[abl.method.isin(CANDIDATES)].groupby("method").log_growth_ann.mean().sort_values(ascending=False)
    research_choice=ranked.index[0]
    decision={"economic_components_approved":passed,"research_winner":research_choice,
              "core_candidate":"downside_mean","live_approved":False,
              "reason":"Actual Valedia strategy/intent/fill replay and prospective acceptance required",
              "holdout_not_used_for_selection":True}
    dump(out/"selection.json",decision)
    print(abl.to_string(index=False));print(json.dumps(decision,indent=2))
    if args.phase=="develop":return
    # The shipped core is one fixed standard downside-mean candidate. The output
    # never substitutes a richer retrospectively winning feature into ERE.
    all_rows=[];annual=[];leads=[];sensitivity=[];civals=[];holdout_ablations=[];latency=[]
    for key,pan in pans.items():
        for name in CANDIDATES:
            rec=simulate(pan,paths[key][name],plan.test_start,plan.last_date,plan)
            holdout_ablations.append(dict(book=key,method=name,**metrics(rec,pan["ann"],plan)))
        no=np.where(np.isfinite(paths[key]["downside_mean"]),1.,np.nan)
        primary={"no_gate":no,"ERE":paths[key]["downside_mean"],
                 **{n:paths[key][n] for n in COMPARATORS}}
        leads.extend(lead_lag(pan,{**paths[key],"ERE":primary["ERE"]},plan))
        for phase,start,end in (("development",plan.dev_start,"2023-12-31"),
                                ("holdout",plan.test_start,plan.last_date)):
            records={}
            for name,m in primary.items():
                rec=simulate(pan,m,start,end,plan);records[name]=rec
                rec.to_csv(out/f"ledger_{key}_{phase}_{name}.csv",index=False)
                all_rows.append(dict(book=key,phase=phase,method=name,**metrics(rec,pan["ann"],plan),
                    cost_dollars=rec.cost_dollars.sum(),ending_equity=rec.ending_equity.iloc[-1]))
                for year,g in rec.groupby(pd.to_datetime(rec.signal_date).dt.year):
                    annual.append(dict(book=key,phase=phase,year=year,method=name,**metrics(g,pan["ann"],plan)))
            if plan.bootstrap_draws:
                civals.append(bootstrap(records,pan["ann"],plan,out,key,phase))
        for name,m in primary.items():
            rec=simulate(pan,m,plan.test_start,plan.last_date,replace(plan,execution_lag_bars=2))
            latency.append(dict(book=key,method=name,execution_lag_bars=2,**metrics(rec,pan["ann"],plan)))
        for cm in (.5,1.,2.):
            for name,m in primary.items():
                rec=simulate(pan,m,plan.test_start,plan.last_date,plan,cost_multiplier=cm)
                sensitivity.append(dict(book=key,method=name,cost_multiplier=cm,**metrics(rec,pan["ann"],plan)))
    pd.DataFrame(latency).to_csv(out/"latency_sensitivity.csv",index=False)
    pd.DataFrame(holdout_ablations).to_csv(out/"holdout_ablation.csv",index=False)
    pd.DataFrame(all_rows).to_csv(out/"summary.csv",index=False)
    pd.DataFrame(annual).to_csv(out/"annual_folds.csv",index=False)
    pd.DataFrame(leads).to_csv(out/"event_lead_lag.csv",index=False)
    pd.DataFrame(sensitivity).to_csv(out/"fee_sensitivity.csv",index=False)
    if civals:pd.concat(civals,ignore_index=True).to_csv(out/"confidence_intervals.csv",index=False)
    if args.single_assets:
        singles=[];failures=[]
        for ticker in sorted(set(data.ticker)-{"VIX","^VIX"}):
            try:
                pan=panel(data,{ticker:1.0},ticker,plan)
                ff=features(pan,plan);rr=annual_references(pan,ff,plan)
                pp={}
                for name in ("downside_mean",)+COMPARATORS:
                    s=selected[name];pp["ERE" if name=="downside_mean" else name]=gate_path(pan,ff,rr,name,s["cut_q"],s["halt_q"],plan)[0]
                pp["no_gate"]=np.where(np.isfinite(pp["ERE"]),1.,np.nan)
                for name,m in pp.items():
                    rec=simulate(pan,m,plan.test_start,plan.last_date,plan)
                    singles.append(dict(ticker=ticker,method=name,discarded_until=pan["discarded_until"],**metrics(rec,pan["ann"],plan)))
            except (ValueError,AssertionError) as e:
                failures.append({"ticker":ticker,"error":str(e)})
        pd.DataFrame(singles).to_csv(out/"single_asset_holdout.csv",index=False)
        dump(out/"single_asset_exclusions.json",failures)
    dump(out/"runtime.json",{"python":sys.version,"numpy":np.__version__,"pandas":pd.__version__,
         "platform":platform.platform(),"core_sha256":hashlib.sha256(Path(__file__).with_name("etherion_risk.py").read_bytes()).hexdigest(),
         "harness_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})
    print(pd.DataFrame(all_rows).to_string(index=False))


if __name__=="__main__":
    main()
