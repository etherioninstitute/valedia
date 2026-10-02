#!/usr/bin/env python3
"""Single ERE state reader/writer. I/O and wall clock are OUTSIDE the pure core.

PAPER_LOCKED (default) prohibits new live risk. HEALTH_ONLY may be chosen only
following migration acceptance; it binds data/operational faults, NOT the failed
statistical candidate. No mode in this release arms the statistical gate.
Network fetching is optional and not validated against live venues in this study.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict, replace
from datetime import datetime, timezone, timedelta
from pathlib import Path
import hashlib
import json
import logging
import os
import tempfile
import fcntl
import numpy as np
from etherion_risk import (EREConfig, Baseline, EREResult, assess, update_baseline,
                           utc, mult_for, level_for, failure, DataError)

LOG = logging.getLogger("ere")
DEFAULT_MODELS = Path(__file__).resolve().parents[2] / "models"


def digest(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


def core_digest():
    return hashlib.sha256(Path(__file__).with_name("etherion_risk.py").read_bytes()).hexdigest()


def read_json(path):
    def bad_constant(value):
        raise ValueError("Non-finite JSON constant: " + value)
    with Path(path).open() as stream:
        return json.load(stream, parse_constant=bad_constant)


def settings(models):
    obj = read_json(Path(models) / "ere_runtime_config.json")
    if obj.get("mode") not in ("PAPER_LOCKED", "HEALTH_ONLY"):
        raise ValueError("No statistical LIVE mode is approved in this release")
    core = dict(obj.get("core", {}))
    core["assets"] = tuple(core.get("assets", ()))
    cfg = EREConfig(**core)
    universe = tuple(obj["universe"])
    if not universe or len(set(universe)) != len(universe):
        raise ValueError("Missing/duplicate universe")
    if any(not isinstance(t, str) or not t or t in ("VIX", "^VIX", "BOOK", "KALSHI") for t in universe):
        raise ValueError("Invalid tradable ticker")
    if obj["mode"] == "HEALTH_ONLY" and obj.get("all_risk_increasing_readers_migrated") is not True:
        raise ValueError("Reader and execution-fence migration is not acknowledged")
    return obj, cfg


def positions(models, cfg, now):
    obj = read_json(Path(models) / "ere_positions.json")
    age = (utc(now) - utc(obj["asof"])).total_seconds()
    if obj["schema"] != "ere-positions/1" or not 0 <= age <= cfg.position_ttl_s:
        raise DataError("STALE", "Stale/missing holdings snapshot")
    if obj.get("complete") is not True or not obj.get("position_version"):
        raise ValueError("Incomplete holdings reconciliation")
    if not np.isfinite(obj["nav"]) or obj["nav"] <= 0:
        raise ValueError("Invalid reconciled NAV")
    w = obj["weights"]
    if not isinstance(w, dict) or any(not np.isfinite(x) or x < 0 for x in w.values()):
        raise ValueError("Runtime scope is long-only spot/US cash securities")
    if sum(w.values()) > cfg.max_gross + cfg.numeric_tol:
        raise ValueError("Gross exposure outside cash-book scope")
    if not isinstance(obj.get("binary_positions"), list):
        raise ValueError("Binary inventory must be explicitly reconciled, even when empty")
    return obj


def result_dict(multiplier, asof, reason, cfg, quality="OK", assets=()):
    return asdict(EREResult("ere/2", asof, float(multiplier), level_for(multiplier, cfg),
                 multiplier == 0, {}, (reason,), quality, cfg.version, cfg.config_id, tuple(assets)))


def read_gate(key="BOOK", models_dir=DEFAULT_MODELS, *, now=None):
    """Every risk-increasing consumer uses this. File mtime is NEVER freshness.

    The holdings digest must change on every fill/cancel/reconciliation update.
    This check and the executor's own account lock must be repeated immediately
    before sending an order; a file read cannot remove an order/fill race.
    """
    now = now or datetime.now(timezone.utc)
    cfg = EREConfig()
    try:
        policy, cfg = settings(models_dir)
        pos = positions(models_dir, cfg, now)
        state = read_json(Path(models_dir) / "ere_state.json")
        if (state["schema"] != "ere/2" or state["version"] != cfg.version
                or state["config_digest"] != digest(policy)
                or state["core_sha256"] != core_digest()
                or state["holdings_digest"] != digest(pos)
                or state["mode"] != policy["mode"]
                or state["heartbeat"]["status"] != "OK"):
            raise DataError("MISALIGNED", "Identity, holdings or producer health mismatch")
        age = (utc(now) - utc(state["generated_at"])).total_seconds()
        if not 0 <= age <= cfg.heartbeat_ttl_s or utc(now) > utc(state["expires_at"]):
            raise DataError("STALE", "Expired ERE heartbeat")
        if utc(state["expires_at"]) - utc(state["generated_at"]) > timedelta(seconds=cfg.heartbeat_ttl_s):
            raise ValueError("Unapproved heartbeat TTL")
        book = state["results"]["BOOK"]
        for field in ("asof", "multiplier", "level", "halt", "data_quality", "version", "config_id"):
            if state[field] != book[field]:
                raise ValueError("Inconsistent top-level BOOK result")
        if state["risk_level"] != book["level"]:
            raise ValueError("Inconsistent legacy vocabulary alias")
        requested = state["results"][key]
        for item in (book, requested):
            if item["config_id"] != cfg.config_id:
                raise ValueError("Result configuration mismatch")
            bar_age = (utc(now) - utc(item["asof"])).total_seconds()
            if not 0 <= bar_age <= cfg.max_bar_age_s:
                raise DataError("STALE", "Stale/future result bar")
            checked = mult_for(item, cfg.max_bar_age_s, now=now, cfg=cfg)
            if item["data_quality"] == "OK" and checked != item["multiplier"]:
                raise ValueError("Malformed result contract")
        m = min(mult_for(book, cfg.max_bar_age_s, now=now, cfg=cfg),
                mult_for(requested, cfg.max_bar_age_s, now=now, cfg=cfg))
        out = dict(requested)
        out.update(multiplier=m, level=level_for(m, cfg), halt=m == 0,
                   risk_level=level_for(m, cfg), safe=m > 0, status=level_for(m, cfg))
        return out
    except Exception as exc:
        # Catch at the I/O boundary, log loudly, NEVER turn an error into size 1.
        LOG.exception("ERE reader CLOSED: %s", exc)
        out = asdict(failure(getattr(exc, "quality", "INSUFFICIENT"), "Reader closed: " + str(exc), cfg=cfg))
        out.update(risk_level="HALT", safe=False, status="HALT")
        return out


def atomic_write(path, obj):
    """Same-filesystem replace; a process killed mid-write leaves the old TTL."""
    path = Path(path)
    payload = json.dumps(obj, indent=2, allow_nan=False)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(payload + "\n"); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def get_prices(policy, cfg, csv_path=None):
    import pandas as pd
    if csv_path:
        df = pd.read_csv(csv_path, parse_dates=["date"])
    else:
        import yfinance as yf  # Optional I/O dependency; never imported by the core/reader.
        chunks = []
        for ticker in policy["universe"]:
            h = yf.Ticker(ticker).history(start=policy.get("history_start", "2017-01-01"),
                    interval="1d", auto_adjust=False, repair=False, keepna=True,
                    timeout=cfg.fetch_timeout_s, raise_errors=True)
            if h.empty:
                raise ValueError("Empty download for " + ticker)
            h = h.reset_index()
            h.columns = [str(c).lower().replace(" ", "_") for c in h.columns]
            h["date"] = pd.to_datetime(h["date"]).dt.tz_localize(None).dt.normalize()
            h["ticker"] = ticker
            chunks.append(h)
        df = pd.concat(chunks, ignore_index=True)
    required = {"ticker", "date", "adj_close", "close", "open", "high", "low", "volume"}
    if not required.issubset(df) or df.duplicated(["ticker", "date"]).any():
        raise ValueError("Missing columns or duplicate prices")
    df = df[df.ticker.isin(policy["universe"])].copy()
    df["date"] = pd.to_datetime(df.date).dt.normalize()
    # Explicit audited exclusions, not automatic outlier clipping or gap filling.
    for ticker, start in policy.get("price_start_dates", {}).items():
        df = df[~((df.ticker == ticker) & (df.date < pd.Timestamp(start)))]
    return df


def completed_inputs(df, assets, cfg, now):
    """Strict native-calendar panel with real completed-session endpoints."""
    import pandas as pd
    import exchange_calendars as xc
    crypto = [t.endswith("-USD") for t in assets]
    if any(crypto) and not all(crypto):
        raise ValueError("Cannot join US closes to crypto UTC closes")
    part = df[df.ticker.isin(assets)]
    if set(part.ticker) != set(assets):
        raise ValueError("Missing requested price series")
    px = part.pivot(index="date", columns="ticker", values="adj_close").reindex(columns=assets).sort_index()
    start = max(px[c].first_valid_index() for c in assets)
    ready = pd.Timestamp(utc(now) - timedelta(seconds=cfg.feed_grace_s))
    if all(crypto):
        last = ready.tz_convert("UTC").normalize().tz_localize(None) - pd.Timedelta(days=1)
        labels = pd.date_range(start, last, freq="D")
        ends = (labels.tz_localize("UTC") + pd.Timedelta(days=1))
    else:
        cal = xc.get_calendar("XNYS", start=str(start.date()), end=str(ready.date()))
        schedule = cal.schedule
        schedule = schedule[schedule["close"] <= ready]
        labels, ends = schedule.index, pd.DatetimeIndex(schedule["close"])
    if not len(labels):
        raise ValueError("No finalized sessions")
    px = px.reindex(labels)
    if not np.isfinite(px.to_numpy()).all() or (px.to_numpy() <= 0).any():
        raise ValueError("Missing or invalid expected completed bar")
    r = px.pct_change(fill_method=None).iloc[1:].to_numpy()
    stamps = tuple(x.isoformat() for x in ends[1:])
    if len(r) < cfg.reference + cfg.short:
        raise ValueError("Insufficient completed history")
    return r, stamps, labels[1:]


def measure_context(key, assets, weights, df, cfg, now, cache):
    cfg = replace(cfg, assets=tuple(assets))
    r, stamps, labels = completed_inputs(df, assets, cfg, now)
    year = utc(now).year
    start = labels.searchsorted(f"{year}-01-01")
    end = start - cfg.baseline_embargo
    if end <= 0:
        raise ValueError("No embargoed annual baseline")
    prior = cache.get(key)
    if prior is not None:
        raw = prior["baseline"]
        b = Baseline(raw["schema"], tuple(raw["assets"]), tuple(raw["feature_key"]),
                     tuple(raw["dates"]), tuple(tuple(x) for x in raw["returns"]))
        if b.assets != cfg.assets or b.feature_key != cfg.feature_key:
            # Universe/feature changes need a new context, not positional reuse.
            b = None
        elif prior["year"] > year:
            raise ValueError("Future baseline cache")
    else:
        b = None
    # Replays overlap for revision detection; no sample comes from the test year.
    b = update_baseline(b, r[:end][-cfg.max_history:], stamps[:end][-cfg.max_history:], cfg)
    out = assess(r, stamps, weights, b, cfg, now=now)
    cache[key] = {"year": year, "baseline": asdict(b)}
    return asdict(out)


def publish(models_dir, csv_path=None, *, now=None):
    now = utc(now or datetime.now(timezone.utc))
    models = Path(models_dir); models.mkdir(parents=True, exist_ok=True)
    cfg = EREConfig(); policy = {}; pos = {}; state = {}; error = None
    with (models / "ere_writer.lock").open("a") as lock:
        # The cron should not launch another writer while a slow download runs.
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            policy, cfg = settings(models)
            pos = positions(models, cfg, now)
            held = {t:w for t,w in pos["weights"].items() if w > 0}
            if not set(held).issubset(policy["universe"]):
                raise ValueError("Holdings outside declared universe")
            previous_path = models / "ere_baselines.json"
            cache = read_json(previous_path) if previous_path.exists() else {}
            df = get_prices(policy, cfg, csv_path)
            candidates = {}; results = {}
            for ticker in policy["universe"]:
                try:
                    candidate = measure_context(ticker, [ticker], [1.0], df, cfg, now, cache)
                except Exception as exc:
                    LOG.exception("ERE price context failed: %s", ticker)
                    candidate = asdict(failure("INSUFFICIENT", str(exc), cfg=replace(cfg, assets=(ticker,))))
                candidates[ticker] = candidate
            sleeve_results = []
            for crypto, name in ((True, "HELD_CRYPTO"), (False, "HELD_EQUITY")):
                group = {t:w for t,w in held.items() if t.endswith("-USD") == crypto}
                if group:
                    candidates[name] = measure_context(name, list(group), list(group.values()), df, cfg, now, cache)
                    sleeve_results.append(candidates[name])
            # A cash-only book still needs quality-checked prospective asset inputs.
            if not sleeve_results:
                sleeve_results = list(candidates.values())
            for ticker, candidate in candidates.items():
                quality = candidate["data_quality"]
                m = float(quality == "OK" and policy["mode"] == "HEALTH_ONLY")
                results[ticker] = result_dict(m, candidate["asof"],
                    "Operational health only; statistical candidate unapproved" if m else "Paper lock or input fault",
                    cfg, quality, candidate["assets"])
            quality = "OK" if all(x["data_quality"] == "OK" for x in sleeve_results) else "INSUFFICIENT"
            dated = [x["asof"] for x in sleeve_results if x["asof"]]
            asof = min(dated, key=utc) if dated else ""
            m = float(quality == "OK" and policy["mode"] == "HEALTH_ONLY")
            reason = "Operational health only; mixed sleeves NOT a joint covariance model"
            if pos["binary_positions"]:
                quality, m, reason = "INSUFFICIENT", 0.0, "Binary exposure lacks validated joint-scenario adapter"
            results["BOOK"] = result_dict(m, asof, reason, cfg, quality, tuple(held))
            results["KALSHI"] = result_dict(0, asof, "New binary risk disabled pending scenario adapter", cfg)
            state = dict(results["BOOK"])
            state.update(results=results, candidate_results=candidates,
                         context_errors={k:v["reasons"] for k,v in candidates.items() if v["data_quality"] != "OK"},
                         asof_semantics="Oldest required context; no fictitious synchronized mixed-book bar")
            atomic_write(previous_path, cache)
        except Exception as exc:
            LOG.exception("ERE producer CLOSED")
            error = type(exc).__name__ + ": " + str(exc)
            state = asdict(failure("INSUFFICIENT", error, cfg=cfg))
            state["results"] = {"BOOK": dict(state)}
        state.update(generated_at=now.isoformat(), expires_at=(now+timedelta(seconds=cfg.heartbeat_ttl_s)).isoformat(),
                     risk_level=state["level"], heartbeat={"status":"ERROR" if error else "OK", "error":error},
                     source="ere_runtime single writer", config_digest=digest(policy), holdings_digest=digest(pos),
                     core_sha256=core_digest(), mode=policy.get("mode", "PAPER_LOCKED"), live_approved=False)
        atomic_write(models / "ere_state.json", state)
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", default=str(DEFAULT_MODELS))
    parser.add_argument("--csv", help="Offline producer exercise instead of fetching")
    parser.add_argument("--now", help="Offline clock injection; NEVER set this in live cron")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    state = publish(args.models, args.csv, now=args.now)
    print(json.dumps({k:state[k] for k in ("version","asof","multiplier","risk_level","data_quality","mode","heartbeat")}))
    return int(state["heartbeat"]["status"] != "OK")


if __name__ == "__main__":
    raise SystemExit(main())
