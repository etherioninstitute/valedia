"""Disabled-by-default, fixture-injectable Polymarket US paper runner.

No default network, inference, broker, scheduler, or credential access. The
opt-in public-data bridge is injectable; forecasts and verified fee remain separate.
"""
from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from poly_guide_paper import VENUE, _contract, _evidence, _time


@dataclass(frozen=True)
class RunnerConfig:
    cadence_seconds: int = 900
    max_depth_levels: int = 1
    candidate_limit: int = 5
    enabled: bool = False

    def __post_init__(self):
        if self.cadence_seconds < 60 or self.candidate_limit != 5 or self.max_depth_levels != 1:
            raise ValueError("cadence >=60; exactly five candidates; top-of-book depth only")


def validate_forecast_json(raw, market, now):
    """Parse JSON forecast and enforce the paper desk's evidence/provenance contract."""
    if isinstance(raw, str):
        try:
            obj = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            raise ValueError("invalid forecast JSON") from None
    elif isinstance(raw, dict):
        obj = dict(raw)
    else:
        raise ValueError("forecast JSON object required")
    _evidence(obj, market, _time(now))
    stamp = _time(obj.get("as_of"))
    if stamp > _time(now) or _time(now) - stamp > timedelta(hours=48):
        raise ValueError("stale forecast evidence")
    return obj


class PaperRunner:
    def __init__(self, desk, *, market_transport=None, bbo_transport=None,
                 inference_transport=None, us_transport=None, fee_per_share=None,
                 output_dir=".", clock=None, config=None):
        self.desk = desk
        self.market_transport = market_transport or (us_transport.markets if us_transport is not None else None)
        self.bbo_transport = bbo_transport or (us_transport.bbo if us_transport is not None else None)
        self.inference_transport = inference_transport
        self.us_transport = us_transport
        self.fee_per_share = fee_per_share
        self.output_dir = Path(output_dir)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.config = config or RunnerConfig()
        self._last_run = None
        self._cadence_db = sqlite3.connect(str(self.desk.path), isolation_level=None)
        self._cadence_db.execute("CREATE TABLE IF NOT EXISTS poly_guide_runner_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        self._cadence_db.execute("""CREATE TABLE IF NOT EXISTS inference_attempts (
            id INTEGER PRIMARY KEY, at TEXT NOT NULL, slug TEXT NOT NULL,
            status TEXT, cost_usd REAL, error TEXT)""")

    def record_inference_cost(self, at, usd, kind):
        if isinstance(usd, bool) or not isinstance(usd, (int, float)) or not math.isfinite(usd) or usd < 0:
            raise ValueError("measured nonnegative USD cost required")
        self.desk.record_cost(at, usd, kind)

    def close(self):
        self._cadence_db.close()

    def run_once(self, *, enabled=None, stop_dir="."):
        active = self.config.enabled if enabled is None else bool(enabled)
        if not active:
            return {"enabled": False, "processed": 0}
        instant = self.clock()
        if instant.tzinfo is None:
            raise ValueError("clock must return timezone-aware time")
        now = instant.astimezone(timezone.utc).isoformat()
        prior = self._cadence_db.execute("SELECT value FROM poly_guide_runner_state WHERE key='last_cycle'").fetchone()
        if prior and instant - _time(prior[0]) < timedelta(seconds=self.config.cadence_seconds):
            return {"enabled": True, "processed": 0, "reason": "cadence"}
        unknown = self._cadence_db.execute(
            "SELECT id FROM inference_attempts WHERE cost_usd IS NULL LIMIT 1").fetchone()
        if unknown:
            return self._write_report(instant, now, [self._blocked(
                now, "", f"inference cost measurement unavailable for attempt {unknown[0]}; proposals halted")],
                completed=False)
        startup_failed = False
        decisions = []
        try:
            if not all(callable(x) for x in (self.market_transport, self.bbo_transport, self.inference_transport)):
                raise RuntimeError("market, BBO and local inference transports must be explicitly injected")
            markets = self.market_transport(self.config.candidate_limit)
            if not isinstance(markets, list):
                raise ValueError("market transport must return a list")
        except Exception as exc:
            startup_failed = True
            markets = []
            decisions.append(self._blocked(now, "", f"market transport failure: {exc}"))
        # Filter to structurally verifiable US binary markets before truncating.
        eligible = [m for m in markets if isinstance(m, dict) and m.get("venue") == VENUE
                    and m.get("schema") == "us_binary_v1" and m.get("tradeable")
                    and m.get("slug") and m.get("rules_digest")]
        for m in eligible[:self.config.candidate_limit]:
            try:
                bbo = self.bbo_transport(m)
                _contract(m, bbo, _time(now))
                if (isinstance(self.fee_per_share, bool) or
                        not isinstance(self.fee_per_share, (int, float)) or
                        not math.isfinite(self.fee_per_share) or self.fee_per_share <= 0):
                    raise ValueError("explicit positive US round-trip fee estimate required")
                attempt = self._cadence_db.execute(
                    "INSERT INTO inference_attempts(at,slug,status,cost_usd,error) VALUES(?,?,NULL,NULL,NULL)",
                    (now, m["slug"])).lastrowid
                try:
                    measured = self.inference_transport(m)
                except Exception as exc:
                    observed = getattr(exc, "cost_usd", None)
                    if isinstance(observed, bool) or not isinstance(observed, (int, float)) or not math.isfinite(observed) or observed <= 0:
                        observed = None
                    if observed is not None:
                        self.record_inference_cost(now, observed, "forecast_attempt")
                    self._cadence_db.execute("UPDATE inference_attempts SET status='FAILED',cost_usd=?,error=? WHERE id=?",
                                             (observed, str(exc), attempt))
                    raise ValueError(f"inference failed; cost measurement unavailable: {exc}") from exc
                cost_value = measured.get("cost_usd") if isinstance(measured, dict) else None
                if isinstance(cost_value, bool) or not isinstance(cost_value, (int, float)) or not math.isfinite(cost_value) or cost_value < 0:
                    self._cadence_db.execute("UPDATE inference_attempts SET status='FAILED',error=? WHERE id=?",
                                             ("cost measurement unavailable", attempt))
                    raise ValueError("explicit measured inference cost required; measurement unavailable")
                self.record_inference_cost(now, cost_value, "forecast_attempt")
                error = measured.get("error") or (None if "forecast" in measured else "forecast unavailable")
                self._cadence_db.execute("UPDATE inference_attempts SET status=?,cost_usd=?,error=? WHERE id=?",
                                         ("FAILED" if error else "SUCCESS", cost_value, str(error) if error else None, attempt))
                if error:
                    raise ValueError(f"inference failed: {error}")
                f = validate_forecast_json(measured["forecast"], m, now)
                md = bbo.get("book", {}).get("marketData", {}) if isinstance(bbo, dict) else {}
                ask = md.get("bestAsk", {})
                bid = md.get("bestBid", {})
                ask_qty = ask.get("quantity") if isinstance(ask, dict) else None
                bid_qty = bid.get("quantity") if isinstance(bid, dict) else None
                result = self.desk.evaluate(m, bbo, f, fee_per_share=self.fee_per_share,
                                            now=now, stop_dir=stop_dir,
                                            depth_caps={"YES": ask_qty, "NO": bid_qty})
                result["provenance"] = {"model_id": f["model_id"], "as_of": f["as_of"],
                                        "rules_digest": f["rules_digest"],
                                        "evidence": f["evidence"], "could_be_wrong": f["could_be_wrong"]}
            except Exception as exc:
                result = self._blocked(now, m.get("slug", ""), str(exc))
            decisions.append(result)
            if self._cadence_db.execute("SELECT 1 FROM inference_attempts WHERE cost_usd IS NULL LIMIT 1").fetchone():
                break
        return self._write_report(instant, now, decisions, completed=not startup_failed)

    def _write_report(self, instant, now, decisions, *, completed):
        report = {"date": instant.date().isoformat(), "generated_at": now,
                  "venue": VENUE, "mode": "paper_only", "depth_levels": 1,
                  "decisions": decisions}
        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / f"{instant.date().isoformat()}.json"
        if path.exists():
            previous = json.loads(path.read_text(encoding="utf-8"))
            cycles = previous.get("cycles", [previous])
        else:
            cycles = []
        cycles.append(report)
        path.write_text(json.dumps({"date": report["date"], "cycles": cycles,
                                    "decisions": [d for c in cycles for d in c["decisions"]]},
                                   sort_keys=True, indent=2) + "\n", encoding="utf-8")
        if completed:
            self._cadence_db.execute("INSERT INTO poly_guide_runner_state(key,value) VALUES('last_cycle',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (now,))
            self._last_run = instant
        return {"enabled": True, "processed": len(decisions), "output": str(path)}

    def _blocked(self, at, slug, reason):
        cursor = self.desk.db.execute("""INSERT INTO decisions(at,strategy,venue,slug,status,reason)
            VALUES(?,?,?,?,?,?)""", (at, "poly_guide_paper", VENUE, slug, "BLOCKED", reason))
        return {"id": cursor.lastrowid, "status": "BLOCKED", "reason": reason,
                "venue": VENUE, "slug": slug, "provenance": None}
