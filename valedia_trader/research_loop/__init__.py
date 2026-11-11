"""Self-improving research loop: research, code, backtest, paper trade, post-mortem, remember.

The loop every idea goes through, with its rules held in code and not in prompts:

    research   independent specialist agents propose ideas after checking the ledger   research.py
    code       every strategy has the same shape: entry, exit, sizing, risk limits     strategy.py
    backtest   prediction written first, sealed test period, walk-forward, breaker,    gauntlet.py
               Deflated Sharpe from the ledger's count of variations
    paper      every order passes check_order; every decision is journaled             risk_gate.py, paper.py
    critic     weekly comparison of paper results with backtest and prediction         critic.py
    remember   the hypothesis ledger, failures included                                ledger.py

Paper trading only. Nothing in this package can reach a live-money account.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class LoopPaths:
    """Where the loop keeps its files. The default is the repository root."""
    root: Path = REPO

    @property
    def research(self) -> Path: return self.root / "research"
    @property
    def strategies(self) -> Path: return self.root / "strategies"
    @property
    def backtests(self) -> Path: return self.root / "backtests"
    @property
    def journal(self) -> Path: return self.root / "journal"
    @property
    def memory(self) -> Path: return self.root / "memory"
    @property
    def config(self) -> Path: return self.root / "config"
    @property
    def ledger(self) -> Path: return self.memory / "ledger.db"
    @property
    def risk_limits(self) -> Path: return self.config / "risk.json"

    def ensure(self) -> "LoopPaths":
        for d in (self.research, self.strategies, self.backtests, self.journal, self.memory, self.config):
            d.mkdir(parents=True, exist_ok=True)
        return self


__all__ = ["LoopPaths", "REPO"]
