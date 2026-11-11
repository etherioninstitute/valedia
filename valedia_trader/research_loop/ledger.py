"""The hypothesis ledger: every idea the system has tested, what happened and in what market.

One table, `hypotheses`, with the record of each idea; failed ideas stay in it for good. Three
supporting tables hold what must not be rewritten: `attempts` (one row per variation backtested,
which is the count the Deflated Sharpe uses), `predictions` (written before the backtest; the
database refuses to change or delete them) and `sealed_uses` (the sealed test period may be used
once per idea; a second use is refused by a primary key, not by a convention).
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

__all__ = ["STATUSES", "REGIMES", "HypothesisLedger", "LedgerError", "DuplicateIdea", "SealedPeriodUsed",
           "PredictionExists", "market_regime", "idea_similarity"]

STATUSES = ("untested", "failed", "passed backtest only", "passed paper", "paused", "retired")
REGIMES = ("trending up", "trending down", "choppy", "high volatility")
_STOP = frozenset("a an the of to in on for and or with is are be that this it its by as at from when than then over under "
                  "after before if will may might can should each any all more less not no into out up down".split())


class LedgerError(RuntimeError):
    pass


class DuplicateIdea(LedgerError):
    """The idea was already tested on this market and failed."""


class SealedPeriodUsed(LedgerError):
    """The sealed test period has already been used for this idea."""


class PredictionExists(LedgerError):
    """A prediction is written once and never edited."""


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", str(text).lower())
    return {w[:-1] if len(w) > 4 and w.endswith("s") else w for w in words if w not in _STOP and len(w) > 1}


def idea_similarity(a: str, b: str) -> float:
    """Jaccard overlap of the content words of two one-sentence ideas (0 = unrelated, 1 = the same)."""
    ta, tb = _tokens(a), _tokens(b)
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


def market_regime(prices: pd.DataFrame | pd.Series, reference: pd.DataFrame | pd.Series | None = None) -> str:
    """Label a test window as one of REGIMES from the equal-weight basket of `prices`.

    High volatility when the window's volatility is at least 1.3 times that of `reference` (the full
    history, default the window itself, which never triggers). Otherwise trending up or down when the
    t-statistic of the mean daily return is beyond +/-1, else choppy."""
    def basket(p):
        frame = p.to_frame() if isinstance(p, pd.Series) else p
        return np.log(frame.where(frame > 0)).diff().mean(axis=1).dropna()
    r = basket(prices)
    if len(r) < 20:
        return "choppy"
    ref = basket(reference) if reference is not None else r
    if ref.std() > 0 and r.std() / ref.std() >= 1.3:
        return "high volatility"
    t = float(r.mean() / r.std() * np.sqrt(len(r))) if r.std() > 0 else 0.0
    return "trending up" if t >= 1.0 else "trending down" if t <= -1.0 else "choppy"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class HypothesisLedger:
    def __init__(self, path: str | Path = ":memory:") -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path))
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA foreign_keys = ON")
        statuses, regimes = ", ".join(f"'{s}'" for s in STATUSES), ", ".join(f"'{r}'" for r in REGIMES)
        self._db.executescript(f"""
        CREATE TABLE IF NOT EXISTS hypotheses (
            id INTEGER PRIMARY KEY, idea TEXT NOT NULL, source_agent TEXT NOT NULL, market TEXT NOT NULL,
            date_tested TEXT, strategy_file TEXT, strategy_version TEXT, variations_tried INTEGER NOT NULL DEFAULT 0,
            market_regime TEXT CHECK (market_regime IS NULL OR market_regime IN ({regimes})),
            backtest_result TEXT, sealed_test_result TEXT, paper_trading_result TEXT,
            status TEXT NOT NULL DEFAULT 'untested' CHECK (status IN ({statuses})),
            lessons_learned TEXT NOT NULL DEFAULT '', rationale TEXT NOT NULL DEFAULT '',
            conditions TEXT NOT NULL DEFAULT '', falsifier TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS attempts (
            id INTEGER PRIMARY KEY, hypothesis_id INTEGER NOT NULL REFERENCES hypotheses(id), kind TEXT NOT NULL,
            strategy_version TEXT, params TEXT NOT NULL DEFAULT '{{}}', sharpe REAL, n_obs INTEGER, recorded_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS predictions (
            hypothesis_id INTEGER PRIMARY KEY REFERENCES hypotheses(id), body TEXT NOT NULL, sha256 TEXT NOT NULL,
            expected TEXT NOT NULL, recorded_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sealed_uses (
            hypothesis_id INTEGER PRIMARY KEY REFERENCES hypotheses(id), strategy_version TEXT, used_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS findings (
            id INTEGER PRIMARY KEY, hypothesis_id INTEGER REFERENCES hypotheses(id), source TEXT NOT NULL,
            finding TEXT NOT NULL, recorded_at TEXT NOT NULL);
        CREATE TRIGGER IF NOT EXISTS predictions_no_update BEFORE UPDATE ON predictions
            BEGIN SELECT RAISE(ABORT, 'a prediction is never edited'); END;
        CREATE TRIGGER IF NOT EXISTS predictions_no_delete BEFORE DELETE ON predictions
            BEGIN SELECT RAISE(ABORT, 'a prediction is never deleted'); END;
        CREATE TRIGGER IF NOT EXISTS sealed_no_update BEFORE UPDATE ON sealed_uses
            BEGIN SELECT RAISE(ABORT, 'a sealed-period use is never edited'); END;
        CREATE TRIGGER IF NOT EXISTS sealed_no_delete BEFORE DELETE ON sealed_uses
            BEGIN SELECT RAISE(ABORT, 'a sealed-period use is never deleted'); END;
        CREATE TRIGGER IF NOT EXISTS attempts_no_delete BEFORE DELETE ON attempts
            BEGIN SELECT RAISE(ABORT, 'attempts are never deleted'); END;
        CREATE TRIGGER IF NOT EXISTS hypotheses_no_delete BEFORE DELETE ON hypotheses
            BEGIN SELECT RAISE(ABORT, 'hypotheses are never deleted, failed ones least of all'); END;
        """)
        self._db.commit()

    # ---- ideas
    def get(self, hypothesis_id: int) -> dict:
        row = self._db.execute("SELECT * FROM hypotheses WHERE id = ?", (int(hypothesis_id),)).fetchone()
        if row is None:
            raise LedgerError(f"no hypothesis {hypothesis_id}")
        out = dict(row)
        for k in ("backtest_result", "sealed_test_result", "paper_trading_result"):
            out[k] = json.loads(out[k]) if out[k] else None
        return out

    def search(self, market: str | None = None, regime: str | None = None, status: str | None = None,
               text: str | None = None) -> list[dict]:
        """Past ideas by market, regime or status (any combination), newest first."""
        if status is not None and status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
        if regime is not None and regime not in REGIMES:
            raise ValueError(f"regime must be one of {REGIMES}")
        where, args = [], []
        for col, val in (("market", market), ("market_regime", regime), ("status", status)):
            if val is not None:
                where.append(f"{col} = ?")
                args.append(val)
        if text:
            where.append("(idea LIKE ? OR lessons_learned LIKE ?)")
            args += [f"%{text}%", f"%{text}%"]
        sql = "SELECT id FROM hypotheses" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC"
        return [self.get(r[0]) for r in self._db.execute(sql, args)]

    def similar(self, idea: str, market: str, threshold: float = 0.6) -> list[dict]:
        """Ideas on the same market that say much the same thing, most similar first."""
        scored = [(idea_similarity(idea, h["idea"]), h) for h in self.search(market=market)]
        return [dict(h, similarity=round(s, 3)) for s, h in sorted(scored, key=lambda x: -x[0]) if s >= threshold]

    def propose(self, idea: str, source_agent: str, market: str, rationale: str = "", conditions: str = "",
                falsifier: str = "", conditions_changed: bool = False, threshold: float = 0.6) -> int:
        """Record a new idea as untested. An idea that matches one which already failed on this market
        is refused unless the proposer states that market conditions have clearly changed."""
        idea = " ".join(str(idea).split())
        if not idea or not source_agent or not market:
            raise ValueError("an idea needs a sentence, a source agent and a market")
        failed = [h for h in self.similar(idea, market, threshold) if h["status"] in ("failed", "retired")]
        if failed and not conditions_changed:
            raise DuplicateIdea(f"already tested and failed as hypothesis {failed[0]['id']}: {failed[0]['idea']}")
        cur = self._db.execute(
            "INSERT INTO hypotheses (idea, source_agent, market, rationale, conditions, falsifier, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)", (idea, source_agent, market, rationale, conditions, falsifier, _now()))
        self._db.commit()
        return int(cur.lastrowid)

    def set_status(self, hypothesis_id: int, status: str, reason: str = "", source: str = "system") -> None:
        if status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
        self.get(hypothesis_id)
        self._db.execute("UPDATE hypotheses SET status = ? WHERE id = ?", (status, int(hypothesis_id)))
        self._db.commit()
        if reason:
            self.add_lesson(hypothesis_id, f"status -> {status}: {reason}", source)

    def add_lesson(self, hypothesis_id: int | None, lesson: str, source: str = "critic") -> None:
        """Append a dated lesson to the idea (and to `findings`). Lessons are only ever added."""
        lesson = " ".join(str(lesson).split())
        if not lesson:
            return
        stamp = _now()
        self._db.execute("INSERT INTO findings (hypothesis_id, source, finding, recorded_at) VALUES (?, ?, ?, ?)",
                         (hypothesis_id, source, lesson, stamp))
        if hypothesis_id is not None:
            self._db.execute("UPDATE hypotheses SET lessons_learned = lessons_learned || ? WHERE id = ?",
                             (f"[{stamp[:10]} {source}] {lesson}\n", int(hypothesis_id)))
        self._db.commit()

    def findings(self, hypothesis_id: int | None = None) -> list[dict]:
        sql, args = "SELECT * FROM findings", ()
        if hypothesis_id is not None:
            sql, args = sql + " WHERE hypothesis_id = ?", (int(hypothesis_id),)
        return [dict(r) for r in self._db.execute(sql + " ORDER BY id", args)]

    # ---- predictions: written first, never edited
    def register_prediction(self, hypothesis_id: int, body: str, expected: dict[str, Any]) -> str:
        self.get(hypothesis_id)
        if self._db.execute("SELECT 1 FROM attempts WHERE hypothesis_id = ?", (int(hypothesis_id),)).fetchone():
            raise LedgerError("the prediction must be written before the first backtest of the idea")
        sha = hashlib.sha256(body.encode()).hexdigest()
        try:
            self._db.execute("INSERT INTO predictions VALUES (?, ?, ?, ?, ?)",
                             (int(hypothesis_id), body, sha, json.dumps(expected, sort_keys=True), _now()))
        except sqlite3.IntegrityError as exc:
            raise PredictionExists(f"hypothesis {hypothesis_id} already has a prediction") from exc
        self._db.commit()
        return sha

    def prediction(self, hypothesis_id: int) -> dict | None:
        row = self._db.execute("SELECT * FROM predictions WHERE hypothesis_id = ?", (int(hypothesis_id),)).fetchone()
        return None if row is None else dict(row, expected=json.loads(row["expected"]))

    # ---- attempts: every variation is counted
    def record_attempt(self, hypothesis_id: int, kind: str, sharpe: float | None, n_obs: int | None,
                       strategy_version: str | None = None, params: dict | None = None) -> int:
        """Count one backtested variation of the idea. Returns the idea's total so far."""
        self.get(hypothesis_id)
        s = None if sharpe is None or not np.isfinite(sharpe) else float(sharpe)
        self._db.execute("INSERT INTO attempts (hypothesis_id, kind, strategy_version, params, sharpe, n_obs, recorded_at) "
                         "VALUES (?, ?, ?, ?, ?, ?, ?)", (int(hypothesis_id), kind, strategy_version,
                                                          json.dumps(params or {}, sort_keys=True, default=str), s, n_obs, _now()))
        self._db.execute("UPDATE hypotheses SET variations_tried = variations_tried + 1 WHERE id = ?", (int(hypothesis_id),))
        self._db.commit()
        return self.variations(hypothesis_id)

    def variations(self, hypothesis_id: int) -> int:
        return int(self.get(hypothesis_id)["variations_tried"])

    def revisions(self, hypothesis_id: int) -> int:
        """Distinct strategy versions backtested for the idea, minus the first."""
        n = self._db.execute("SELECT COUNT(DISTINCT strategy_version) FROM attempts WHERE hypothesis_id = ?",
                             (int(hypothesis_id),)).fetchone()[0]
        return max(0, int(n) - 1)

    def attempts(self, hypothesis_id: int) -> list[dict]:
        """Every counted variation of the idea, oldest first."""
        rows = self._db.execute("SELECT * FROM attempts WHERE hypothesis_id = ? ORDER BY id", (int(hypothesis_id),))
        return [dict(r, params=json.loads(r["params"])) for r in rows]

    def attempt_sharpes(self, market: str | None = None, hypothesis_id: int | None = None) -> np.ndarray:
        """Per-period Sharpe of every counted variation, for one idea or for a whole market."""
        sql = ("SELECT a.sharpe FROM attempts a JOIN hypotheses h ON h.id = a.hypothesis_id "
               "WHERE a.sharpe IS NOT NULL")
        args: list = []
        if market is not None:
            sql, args = sql + " AND h.market = ?", args + [market]
        if hypothesis_id is not None:
            sql, args = sql + " AND h.id = ?", args + [int(hypothesis_id)]
        return np.array([r[0] for r in self._db.execute(sql + " ORDER BY a.id", args)], dtype=float)

    # ---- results
    def record_backtest(self, hypothesis_id: int, result: dict, regime: str, strategy_file: str, strategy_version: str,
                        status: str) -> None:
        if regime not in REGIMES or status not in STATUSES:
            raise ValueError("unknown regime or status")
        self._db.execute("UPDATE hypotheses SET backtest_result = ?, market_regime = ?, strategy_file = ?, "
                         "strategy_version = ?, date_tested = ?, status = ? WHERE id = ?",
                         (json.dumps(result, default=str), regime, strategy_file, strategy_version, _now()[:10], status,
                          int(hypothesis_id)))
        self._db.commit()

    def use_sealed(self, hypothesis_id: int, strategy_version: str) -> None:
        """Claim the idea's single use of the sealed period. Call this before looking at the data."""
        self.get(hypothesis_id)
        try:
            self._db.execute("INSERT INTO sealed_uses VALUES (?, ?, ?)", (int(hypothesis_id), strategy_version, _now()))
        except sqlite3.IntegrityError as exc:
            raise SealedPeriodUsed(f"hypothesis {hypothesis_id} has already used the sealed period") from exc
        self._db.commit()

    def sealed_used(self, hypothesis_id: int) -> bool:
        return self._db.execute("SELECT 1 FROM sealed_uses WHERE hypothesis_id = ?", (int(hypothesis_id),)).fetchone() is not None

    def record_sealed(self, hypothesis_id: int, result: dict, status: str) -> None:
        if status not in STATUSES:
            raise ValueError("unknown status")
        self._db.execute("UPDATE hypotheses SET sealed_test_result = ?, status = ? WHERE id = ?",
                         (json.dumps(result, default=str), status, int(hypothesis_id)))
        self._db.commit()

    def record_paper(self, hypothesis_id: int, result: dict, status: str | None = None) -> None:
        if status is not None and status not in STATUSES:
            raise ValueError("unknown status")
        self._db.execute("UPDATE hypotheses SET paper_trading_result = ? WHERE id = ?",
                         (json.dumps(result, default=str), int(hypothesis_id)))
        if status is not None:
            self._db.execute("UPDATE hypotheses SET status = ? WHERE id = ?", (status, int(hypothesis_id)))
        self._db.commit()

    def summary(self) -> dict[str, int]:
        counts = {s: 0 for s in STATUSES}
        for status, n in self._db.execute("SELECT status, COUNT(*) FROM hypotheses GROUP BY status"):
            counts[status] = int(n)
        counts["variations"] = int(self._db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0])
        return counts

    def close(self) -> None:
        self._db.close()
