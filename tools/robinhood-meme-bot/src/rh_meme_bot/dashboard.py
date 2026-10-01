"""Output sinks: a rich live dashboard (3 panels) and a plain-line printer for headless runs.

Sink interface: status(text), engine(snapshot), launch(row), decision(row), action(text).
"""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from typing import Callable, Deque, Optional

from rich.console import Console
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .stats import format_status


def short(addr: Optional[str]) -> str:
    return "-" if not addr else (addr if len(addr) <= 12 else f"{addr[:6]}..{addr[-4:]}")


def fmt_num(v: Optional[float]) -> str:
    return "-" if v is None else f"{v:.6g}"


def fmt_usd(v: Optional[float]) -> str:
    return "-" if v is None else f"${v:,.0f}"


def flags(r: dict) -> str:
    """Compact flags: owner R=renounced/O=owned/N=no owner(), code Y/N, v4 hooks Y/N,
    'dynfee' for a dynamic-fee v4 pool."""
    owner = {"renounced": "R", "no_owner_fn": "N"}.get(r.get("owner_status"), "O")
    out = f"owner:{owner} code:{'Y' if r.get('code') else 'N'}"
    if r.get("version") == "v4":
        out += f" hooks:{'Y' if r.get('has_hooks') else 'N'}"
        out += " dynfee" if r.get("dynamic_fee") else ""
    return out


def fee_text(r: dict) -> str:
    return "dyn" if r.get("dynamic_fee") else str(r.get("fee"))


def now_hms() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def _lat(r: dict) -> str:
    ms = r.get("latency_ms")
    return "-" if ms is None else f"{ms:.0f}ms{'' if r.get('within_budget') else '!'}"


class PlainSink:
    """Headless mode: one plain line per event (CI / tests friendly)."""

    def __init__(self, write: Callable[[str], None] = print):
        self._w = write

    def status(self, text: str) -> None:
        self._w(f"[status] {text}")

    def engine(self, snap: dict) -> None:
        self._w(f"[engine] {format_status(snap)}")

    def launch(self, r: dict) -> None:
        self._w(f"[launch] {r['time']} {r.get('version', 'v3')} pool={r['pool']} "
                f"sym={r['symbol'] or '-'} fee={fee_text(r)} "
                f"price={fmt_num(r['price'])} liq={fmt_usd(r['liquidity_usd'])} {flags(r)}")

    def decision(self, r: dict) -> None:
        why = f" first_fail={r['first_fail']}" if r.get("first_fail") else ""
        err = f" error={r['error']}" if r.get("error") else ""
        star = " BUY *" if r["final_action"] == "buy" else ""
        self._w(f"[decision] {r.get('version', 'v3')} pool={r['pool']} model={r['action']}@{r['confidence']:.2f} "
                f"risk={r['risk_score']} {_lat(r)} final={r['final_action']}{star}{why}{err}")

    def action(self, text: str) -> None:
        self._w(f"[action] {text}")


class Dashboard:
    """Rich Live layout: launches | decisions | action log, plus an engine status line."""

    def __init__(self, console: Optional[Console] = None, rows: int = 12):
        self.console = console or Console()
        self.launches: Deque[dict] = deque(maxlen=rows)
        self.decisions: Deque[dict] = deque(maxlen=rows)
        self.actions: Deque[str] = deque(maxlen=rows * 2)
        self._status = "starting..."
        self._engine = "engine: starting"
        self._live = None

    # --- sink interface ---
    def status(self, text: str) -> None:
        self._status = text
        self._refresh()

    def engine(self, snap: dict) -> None:
        self._engine = format_status(snap)
        self._refresh()

    def launch(self, r: dict) -> None:
        self.launches.appendleft(r)
        self._refresh()

    def decision(self, r: dict) -> None:
        self.decisions.appendleft(r)
        self._refresh()

    def action(self, text: str) -> None:
        self.actions.appendleft(f"{now_hms()} {text}")
        self._refresh()

    # --- rendering ---
    def render(self) -> Layout:
        t = Table(expand=True, show_edge=False)
        for col in ("time", "ver", "pool", "token", "fee", "price", "liquidity", "flags"):
            t.add_column(col, overflow="fold")
        for r in self.launches:
            t.add_row(r["time"], r.get("version", "v3"), short(r["pool"]),
                      r["symbol"] or short(r.get("token")), fee_text(r), fmt_num(r["price"]),
                      fmt_usd(r["liquidity_usd"]), flags(r))
        d = Table(expand=True, show_edge=False)
        for col in ("pool", "model", "conf", "risk", "latency", "final", "first failing gate"):
            d.add_column(col, overflow="fold")
        for r in self.decisions:
            buy = r["final_action"] == "buy"
            final = "[bold green]BUY ★[/]" if buy else f"[dim]{r['final_action']}[/]"
            style = "bold green" if buy else None
            d.add_row(short(r["pool"]), r["action"], f"{r['confidence']:.2f}",
                      str(r["risk_score"]), _lat(r), final,
                      r.get("first_fail") or r.get("error") or "", style=style)
        layout = Layout()
        layout.split_column(Layout(Text(self._engine, style="cyan"), name="engine", size=1),
                            Layout(name="top", ratio=2),
                            Layout(name="log", ratio=1, minimum_size=5))
        layout["top"].split_row(Layout(Panel(t, title="New pool launches")),
                                Layout(Panel(d, title="Decisions")))
        body = "\n".join(self.actions) or "(no actions yet)"
        layout["log"].update(Panel(body, title=f"Action log  |  {self._status}  |  PAPER ONLY"))
        return layout

    def _refresh(self) -> None:
        if self._live is not None:
            self._live.update(self.render())

    def __enter__(self) -> "Dashboard":
        from rich.live import Live
        self._live = Live(self.render(), console=self.console, screen=False,
                          refresh_per_second=4)
        self._live.__enter__()
        return self

    def __exit__(self, *exc) -> None:
        if self._live is not None:
            self._live.__exit__(*exc)
            self._live = None
