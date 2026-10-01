#!/usr/bin/env python3
"""UGFT paper walk-forward: kernel v16e + Etherion risk gates.

PAPER / fee-true / not-live. No orders, no --live, no venue sends, no Kelly.

Alpha is kernel_predictor_v16e only. QEIDrawdownGuard, VarianceCascade
(EnergyLadderCascade), and ExecutionErrorEstimator may only cut or zero
that size. Etherion is not an alpha. Mean-reversion is not consulted.

Nimble 2 (the only model; the retired AUTONOMY-2 shadow it replaced) is
annotate-only. It is not a bar bulk backtester. This process does not call it.

Fee book is CE-3 (valedia_config CRYPTO_FEE_PCT + CRYPTO_SLIPPAGE_PCT)
charged on turnover. Not the kernel helper's 8 bps default.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import yfinance as yf

from Etherion_Risk_Engine import (
    ExecutionErrorEstimator,
    QEIDrawdownGuard,
    VarianceCascadeEngine,
)
from kernel_predictor_v16e import KernelPredictor, backtest as kernel_backtest
from valedia_config import CRYPTO_FEE_PCT, CRYPTO_SLIPPAGE_PCT

ROOT = Path(__file__).resolve().parents[2]
OUT_STEM = "UGFT-KERNEL-ETHERION-BACKTEST-2026-09-29"
WARMUP = 80
TURN_MIN = 0.05
# ExecutionErrorEstimator.slippage_pct == daily vol in percent at freq=1
# (Kato-Ponce bound). These cut/halt levels are this runner's policy.
# They do not replace the CE-3 booked cost.
EEE_CUT_PCT = 5.0
EEE_HALT_PCT = 8.0
TICKERS = ("ETH-USD", "BTC-USD", "SPY", "AAPL")


def one_way_cost():
    return float(CRYPTO_FEE_PCT) + float(CRYPTO_SLIPPAGE_PCT)


def fetch(ticker, start):
    hist = yf.Ticker(ticker).history(start=start, auto_adjust=True)
    if hist is None or len(hist) == 0 or "Close" not in hist.columns:
        raise RuntimeError("no Close history")
    close = hist["Close"].astype(float)
    close = close[np.isfinite(close.to_numpy()) & (close.to_numpy() > 0)]
    if len(close) < WARMUP + 30:
        raise RuntimeError(f"only {len(close)} closes")
    dates = [ts.date().isoformat() for ts in close.index]
    return close.to_numpy(dtype=float), dates


def etherion_factor(prices_now):
    """Return a multiplier in [0, 1] and which gates fired. Never a sign flip."""
    fired = {
        "qei_cut": 0, "qei_halt": 0, "qei_nonfinite": 0,
        "cascade_cut": 0, "eee_cut": 0, "eee_halt": 0,
    }
    factor = 1.0
    halt = False
    px = np.asarray(prices_now, dtype=float)

    if len(px) >= 40:
        est = ExecutionErrorEstimator.estimate_slippage(px[-80:], rebalance_freq=1)
        bound = float(est.get("slippage_pct") or 0.0)
        if bound >= EEE_HALT_PCT:
            halt = True
            fired["eee_halt"] = 1
        elif bound >= EEE_CUT_PCT:
            factor *= 0.5
            fired["eee_cut"] = 1

    if len(px) >= 25:
        window = np.diff(np.log(np.clip(px[-61:], 1e-12, None)))
        if len(window) >= 20:
            # energy_ladder_check → kotecky_preiss_criterion crashes on one
            # column (np.corrcoef of a single series is a scalar). KP is the
            # cross-asset criterion. Univariate cut uses the class's own
            # ladder rule: Gronwall_ratio >= 2.
            ladder = VarianceCascadeEngine().compute_sobolev_ladder(window)
            if float(ladder.get("Gronwall_ratio") or 0.0) >= 2.0:
                factor *= 0.5
                fired["cascade_cut"] = 1

    asset_ret = np.diff(np.log(np.clip(px, 1e-12, None)))
    # Score the asset log-return window, not strategy pnl. A sparse pnl
    # series makes kurtosis explode, and check_guard then else-branches
    # into HALT_ALL. φ₀ is the prior 120 asset days; the check is the
    # last 60. Both are known at this close.
    if len(asset_ret) >= 180:
        guard = QEIDrawdownGuard(initial_capital=1.0)
        guard.adaptive_threshold(asset_ret[-180:-60])
        check = guard.check_guard(asset_ret[-60:])
        action = check.get("action")
        ratio = check.get("ratio")
        try:
            ratio_f = float(ratio)
        except (TypeError, ValueError):
            ratio_f = float("nan")
        if not np.isfinite(ratio_f):
            fired["qei_nonfinite"] = 1
        elif action == "HALT_ALL":
            halt = True
            fired["qei_halt"] = 1
        elif action == "REDUCE_50%":
            factor *= 0.5
            fired["qei_cut"] = 1

    if halt:
        factor = 0.0
    return float(np.clip(factor, 0.0, 1.0)), fired


def _metrics(eq, bh0, bh1, wins, losses, trades, fees_units):
    eq = np.asarray(eq, dtype=float)
    ret = (eq[-1] / eq[0] - 1.0) * 100.0
    bh = ((bh1 / bh0) - 1.0) * 100.0 if bh0 else 0.0
    dr = np.diff(np.log(np.maximum(eq, 1e-10)))
    sharpe = float(np.mean(dr) / (np.std(dr) + 1e-20) * np.sqrt(252)) if len(dr) > 1 else 0.0
    peak = np.maximum.accumulate(eq)
    maxdd = float(np.max((peak - eq) / np.maximum(peak, 1e-20)) * 100.0)
    denom = wins + losses
    hit = (wins / denom * 100.0) if denom else 0.0
    return {
        "ret_pct": ret,
        "bh_pct": bh,
        "alpha_pct": ret - bh,
        "sharpe": sharpe,
        "maxdd_pct": maxdd,
        "hit_pct": hit,
        "trades": int(trades),
        "fees_paid": float(fees_units),
        "fees_paid_pct_of_start": float(fees_units) * 100.0,
        "bars": int(len(eq) - 1),
    }


def replay(prices, dates, ac, use_gates, cost):
    pred = KernelPredictor(ac)
    ret = np.diff(np.log(prices))
    n = len(ret)
    eq = [1.0]
    eq_dates = [dates[WARMUP]]
    pos = 0.0
    wins = losses = trades = 0
    fees_units = 0.0
    gates = {k: 0 for k in (
        "kernel_halt", "qei_cut", "qei_halt", "qei_nonfinite", "cascade_cut", "eee_cut", "eee_halt",
        "bars_halt", "bars_cut",
    )}
    bar_win = []
    bar_trade = []
    bar_fee = []
    bar_gates = []

    for i in range(WARMUP, n):
        sig = pred.predict(prices[: i + 1])
        raw = float(sig.get("size") or 0.0)
        if sig.get("direction") == "HALT" or not np.isfinite(raw):
            raw = 0.0
            gates["kernel_halt"] += 1
        raw = float(np.clip(raw, -1.0, 1.0)) if np.isfinite(raw) else 0.0

        fired = {k: 0 for k in (
            "qei_cut", "qei_halt", "qei_nonfinite", "cascade_cut", "eee_cut", "eee_halt",
        )}
        tgt = raw
        if use_gates and abs(raw) > 1e-9:
            factor, fired = etherion_factor(prices[: i + 1])
            tgt = float(np.clip(raw * factor, -1.0, 1.0))
            if abs(tgt) > abs(raw) + 1e-12 or tgt * raw < 0:
                tgt = 0.0 if tgt * raw < 0 else raw
            if factor <= 0.0:
                gates["bars_halt"] += 1
            elif factor < 1.0 - 1e-12:
                gates["bars_cut"] += 1
            for k, v in fired.items():
                gates[k] += int(v)

        chg = abs(tgt - pos)
        fee = chg * cost if chg > TURN_MIN else 0.0
        traded = chg > TURN_MIN
        if traded:
            trades += 1
        eq_before = eq[-1]
        bar = pos * ret[i] - fee
        eq.append(eq_before * (1.0 + bar))
        fees_units += fee * eq_before
        win = None
        if abs(pos) > 0.01:
            win = bool(pos * ret[i] > 0.0)
            if win:
                wins += 1
            else:
                losses += 1
        bar_win.append(win)
        bar_trade.append(traded)
        bar_fee.append(fee * eq_before)
        bar_gates.append(fired)
        pos = tgt
        eq_dates.append(dates[i + 1])

    full = _metrics(eq, prices[WARMUP], prices[-1], wins, losses, trades, fees_units)
    years = _years(eq, eq_dates, prices, dates, bar_win, bar_trade, bar_fee, bar_gates)
    return full, gates, years


def _years(eq, eq_dates, prices, dates, bar_win, bar_trade, bar_fee, bar_gates):
    years = sorted({d[:4] for d in eq_dates})
    rows = []
    empty = {
        "qei_cut": 0, "qei_halt": 0, "qei_nonfinite": 0,
        "cascade_cut": 0, "eee_cut": 0, "eee_halt": 0,
    }
    for year in years:
        in_year = [k for k, d in enumerate(eq_dates) if d[:4] == year]
        if len(in_year) < 2:
            continue
        first = in_year[0]
        prev = first - 1 if first > 0 else first
        last = in_year[-1]
        if last <= prev:
            continue
        wins = losses = trades = 0
        fees = 0.0
        g = dict(empty)
        for j in range(prev, last):
            # bar j earns eq[j] -> eq[j+1], dated eq_dates[j+1]
            if eq_dates[j + 1][:4] != year:
                continue
            fees += bar_fee[j]
            if bar_trade[j]:
                trades += 1
            if bar_win[j] is True:
                wins += 1
            elif bar_win[j] is False:
                losses += 1
            for k in g:
                g[k] += int(bar_gates[j].get(k, 0))
        px0 = prices[WARMUP + prev]
        px1 = prices[WARMUP + last]
        m = _metrics(eq[prev: last + 1], px0, px1, wins, losses, trades, fees)
        m["year"] = year
        m["gates"] = g
        rows.append(m)
    return rows


def parity(prices, ac, cost, kernel_only):
    ref = kernel_backtest(prices, ac=ac, warmup=WARMUP, fee=cost)
    d_ret = abs(kernel_only["ret_pct"] - float(ref["ret"]))
    d_bh = abs(kernel_only["bh_pct"] - float(ref["bh"]))
    d_hit = abs(kernel_only["hit_pct"] - float(ref["hit"]))
    trades_ok = int(kernel_only["trades"]) == int(ref["trades"])
    ok = d_ret < 0.05 and d_bh < 0.05 and d_hit < 0.05 and trades_ok
    return {
        "ok": bool(ok),
        "ref_fee": cost,
        "ref_ret_pct": float(ref["ret"]),
        "ref_bh_pct": float(ref["bh"]),
        "ref_alpha_pct": float(ref["alpha"]),
        "ref_sharpe": float(ref["sh"]),
        "ref_hit_pct": float(ref["hit"]),
        "ref_trades": int(ref["trades"]),
        "ret_abs_diff": d_ret,
        "trades_match": trades_ok,
        "note": "Same CE-3 one-way cost passed into kernel backtest(). Kernel default 8 bps was not used.",
    }


def run_ticker(ticker, start, cost):
    ac = "equity" if "-" not in ticker else "crypto"
    prices, dates = fetch(ticker, start)
    ugft, ugft_gates, ugft_years = replay(prices, dates, ac, True, cost)
    kern, kern_gates, kern_years = replay(prices, dates, ac, False, cost)
    check = parity(prices, ac, cost, kern)
    return {
        "ticker": ticker,
        "asset_class": ac,
        "fee_book": "CE-3 CRYPTO_FEE_PCT+CRYPTO_SLIPPAGE_PCT on |delta weight|",
        "start": dates[0],
        "end": dates[-1],
        "closes": int(len(prices)),
        "ugft": ugft,
        "ugft_gates": ugft_gates,
        "ugft_years": ugft_years,
        "kernel_only_same_fees": kern,
        "kernel_only_gates": {"kernel_halt": kern_gates["kernel_halt"]},
        "kernel_parity_same_fees": check,
    }


def _n(x, nd=2):
    return f"{float(x):.{nd}f}"


def _row(name, book, m, gates=None):
    g = gates or {}
    return (
        f"| {name} | {book} | {_n(m['ret_pct'])} | {_n(m['bh_pct'])} | {_n(m['alpha_pct'])} | "
        f"{_n(m['sharpe'], 3)} | {_n(m['maxdd_pct'])} | {_n(m['hit_pct'], 1)} | {m['trades']} | "
        f"{_n(m['fees_paid_pct_of_start'], 3)} | "
        f"{g.get('qei_cut', '—')} | {g.get('qei_halt', '—')} | {g.get('qei_nonfinite', '—')} | {g.get('cascade_cut', '—')} | "
        f"{g.get('eee_cut', '—')} | {g.get('eee_halt', '—')} | {g.get('kernel_halt', '—')} |"
    )


def render_md(doc):
    lines = [
        "# UGFT kernel + Etherion gates — paper backtest",
        "",
        f"Updated: {doc['generated_at']}",
        "",
        "**PAPER / fee-true / not-live.** No orders. No `--live`. No venue sends. No Kelly.",
        "",
        "Nimble 2 is annotate-only (it replaced the retired AUTONOMY-2 shadow). It is not a bar bulk backtester. Nimble 2 did not run these bars. Numbers below are a Python causal replay of `kernel_predictor_v16e` with Etherion risk gates.",
        "",
        "This is not the TradingView SuperTrend test. SuperTrend Pine stays `//@version=6` (no bump): Desktop `tv-pine-profiler-2026-09-29/GROK-SuperTrend-Strategy-paper.pine`, `recommendations/SUPERTREND-STRATEGY-PAPER.pine`, and the accel-ornith twin.",
        "",
        "## Book",
        "",
        "| Item | Value |",
        "| --- | --- |",
        "| Name | UGFT = kernel_predictor_v16e + Etherion risk gates |",
        "| Alpha | `KernelPredictor.predict` size only |",
        "| Gates | `QEIDrawdownGuard` finite HALT_ALL→flat, REDUCE_50%→×0.5. A NaN ratio (flat window) is not a halt. `VarianceCascade` univariate `compute_sobolev_ladder` Gronwall_ratio≥2→×0.5 (KP crashes on one column). `ExecutionErrorEstimator` vol-bound ≥8%→flat, ≥5%→×0.5 |",
        "| Not used | Etherion as alpha, MeanReversionEngine, Kelly, kernel 8 bps, SuperTrend |",
        f"| Fees | `CRYPTO_FEE_PCT` {doc['fees']['CRYPTO_FEE_PCT']} + `CRYPTO_SLIPPAGE_PCT` {doc['fees']['CRYPTO_SLIPPAGE_PCT']} = {doc['fees']['one_way']} one-way on \\|Δweight\\| when \\|Δ\\|>0.05 |",
        "| Clock | Daily close. Signal at t earns t→t+1. Warmup 80. No lookahead, no fit. |",
        "| QEI arm | φ₀ from the prior 120 asset log-returns; checked on the last 60 asset log-returns. Not strategy pnl (a flat book blows up kurtosis and the class else-branch halts). Non-finite ratio is not a halt. |",
        "| Equities | SPY and AAPL pay the same crypto book (conservative versus cash equities). |",
        "| maxDD% | Peak-to-trough on the window. Not the kernel helper's max-drop / global-max. |",
        "| fees_paid | Sum of turnover costs in equity units with start equity 1, shown as % of start. |",
        "",
        "## Full sample",
        "",
        "| Asset | Book | ret% | B&H% | alpha | Sharpe | maxDD% | hit% | trades | fees_paid% | qei_cut | qei_halt | qei_nan | cascade_cut | eee_cut | eee_halt | kernel_halt |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in doc["assets"]:
        if row.get("error"):
            lines.append(f"| {row['ticker']} | ERROR | {row['error']} | | | | | | | | | | | | | | |")
            continue
        g = dict(row["ugft_gates"])
        lines.append(_row(row["ticker"], "UGFT", row["ugft"], g))
        kg = {"kernel_halt": row["kernel_only_gates"]["kernel_halt"]}
        lines.append(_row(row["ticker"], "kernel same fees", row["kernel_only_same_fees"], kg))
        span = f"{row['start']} → {row['end']} ({row['closes']} closes, {row['asset_class']})"
        lines.append(f"| {row['ticker']} | span | {span} | | | | | | | | | | | | | | |")
    lines += [
        "",
        "UGFT alpha minus kernel-only alpha is the fee-true effect of the gates on the same path. Kernel-only still uses CE-3 costs, not 8 bps.",
        "",
        "## Walk-forward years (UGFT)",
        "",
        "Calendar slices of the one causal path. The kernel has no fitted weights to roll.",
        "",
        "| Asset | Year | ret% | B&H% | alpha | Sharpe | maxDD% | hit% | trades | fees_paid% | qei c/h/nan | cascade_cut | eee c/h |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in doc["assets"]:
        if row.get("error"):
            continue
        for y in row["ugft_years"]:
            g = y["gates"]
            lines.append(
                f"| {row['ticker']} | {y['year']} | {_n(y['ret_pct'])} | {_n(y['bh_pct'])} | {_n(y['alpha_pct'])} | "
                f"{_n(y['sharpe'], 3)} | {_n(y['maxdd_pct'])} | {_n(y['hit_pct'], 1)} | {y['trades']} | "
                f"{_n(y['fees_paid_pct_of_start'], 3)} | {g['qei_cut']}/{g['qei_halt']}/{g['qei_nonfinite']} | {g['cascade_cut']} | "
                f"{g['eee_cut']}/{g['eee_halt']} |"
            )
    lines += ["", "## Kernel parity (same CE-3 cost, gates off)", ""]
    for row in doc["assets"]:
        if row.get("error"):
            lines.append(f"- {row['ticker']}: fetch failed ({row['error']})")
            continue
        p = row["kernel_parity_same_fees"]
        flag = "PASS" if p["ok"] else "FAIL"
        lines.append(
            f"- {row['ticker']}: {flag} retΔ={p['ret_abs_diff']:.4f} trades_match={p['trades_match']} "
            f"ref_ret={p['ref_ret_pct']:.2f} ref_trades={p['ref_trades']}"
        )
    lines += [
        "",
        "## Fences",
        "",
        "No orders. No `--live`. No venue sends. No `create_order`. No Kelly. Etherion did not supply a direction. Nimble 2 did not run bars.",
        "",
        f"Label: {doc['label']}",
        "",
    ]
    return "\n".join(lines) + "\n"


def main(argv):
    start = "2020-01-01"
    if "--start" in argv:
        start = argv[argv.index("--start") + 1]
    cost = one_way_cost()
    if abs(float(CRYPTO_FEE_PCT) - 0.001) > 1e-15 or abs(float(CRYPTO_SLIPPAGE_PCT) - 0.0005) > 1e-15:
        print("FEE BOOK DRIFT", CRYPTO_FEE_PCT, CRYPTO_SLIPPAGE_PCT, file=sys.stderr)
        return 2
    now = datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d %H:%M:%S %Z")
    assets = []
    for ticker in TICKERS:
        print(f"RUN {ticker}", flush=True)
        try:
            assets.append(run_ticker(ticker, start, cost))
        except Exception as exc:
            if ticker in ("ETH-USD", "BTC-USD"):
                print(f"FAIL {ticker}: {exc}", file=sys.stderr)
                return 1
            assets.append({"ticker": ticker, "error": str(exc)})
            print(f"SKIP {ticker}: {exc}", flush=True)
    doc = {
        "name": "UGFT-KERNEL-ETHERION-BACKTEST",
        "label": "PAPER/fee-true/not-live",
        "generated_at": now,
        "nimble2": "Nimble 2 is annotate-only (replaced the retired AUTONOMY-2 shadow). Not a bar bulk backtester. Nimble 2 did not run these bars.",
        "alpha": "kernel_predictor_v16e.KernelPredictor",
        "gates": [
            "QEIDrawdownGuard",
            "VarianceCascade",
            "ExecutionErrorEstimator",
        ],
        "gate_policy": {
            "qei": "Finite HALT_ALL flattens; REDUCE_50% multiplies size by 0.5. phi_0 from the prior 120 asset log-returns via adaptive_threshold. check_guard runs on the last 60 asset log-returns, not on strategy pnl. A non-finite ratio is qei_nonfinite and is not a halt.",
            "variance_cascade": "VarianceCascadeEngine.compute_sobolev_ladder. Gronwall_ratio >= 2 multiplies size by 0.5. energy_ladder_check is not called: kotecky_preiss_criterion IndexErrors on a single column because np.corrcoef returns a scalar. KP is cross-asset and is not a signal here.",
            "execution_error": f"estimate_slippage rebalance_freq=1. slippage_pct >= {EEE_HALT_PCT} flattens; >= {EEE_CUT_PCT} multiplies by 0.5. Booked cost stays CE-3.",
            "sign": "Gates never increase |size| and never flip sign.",
        },
        "fees": {
            "CRYPTO_FEE_PCT": float(CRYPTO_FEE_PCT),
            "CRYPTO_SLIPPAGE_PCT": float(CRYPTO_SLIPPAGE_PCT),
            "one_way": cost,
            "not": "kernel backtest default fee 0.0008",
            "turn_min": TURN_MIN,
        },
        "not": [
            "orders",
            "--live",
            "venue sends",
            "create_order",
            "Kelly",
            "Etherion-as-alpha",
            "SuperTrend-only TV",
            "Nimble 2 bar replay",
        ],
        "assets": assets,
    }
    out_dir = ROOT / "recommendations"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{OUT_STEM}.json"
    md_path = out_dir / f"{OUT_STEM}.md"
    json_path.write_text(json.dumps(doc, indent=2) + "\n")
    md_path.write_text(render_md(doc))
    print(render_md(doc))
    print(f"WROTE {md_path}")
    print(f"WROTE {json_path}")
    required = [a for a in assets if a["ticker"] in ("ETH-USD", "BTC-USD")]
    if any(a.get("error") for a in required):
        return 1
    if any(not a.get("kernel_parity_same_fees", {}).get("ok") for a in assets if not a.get("error")):
        print("PARITY FAIL", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
