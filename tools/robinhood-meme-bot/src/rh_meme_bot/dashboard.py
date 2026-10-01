"""Output sinks: a rich live dashboard (3 panels) and a plain-line printer for headless runs."""
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from typing import Callable, Deque, Optional

from rich.console import Console
from rich.layout import Layout
from rich.panel import Panel
from rich.table import Table


def short(addr: Optional[str]) -> str:
    return "-" if not addr else (addr if len(addr) <= 12 else f"{addr[:6]}..{addr[-4:]}")


def fmt_price(p: Optional[float]) -> str:
    return "-" if p is None else f"{p:.6g}"


def now_hms() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


class PlainSink:
    """Headless mode: one plain line per event (CI / tests friendly)."""

    def __init__(self, write: Callable[[str], None] = print):
        self._w = write

    def status(self, text: str) -> None:
        self._w(f"[status] {text}")

    def launch(self, r: dict) -> None:
        self._w(f"[launch] {r['time']} pool={r['pool']} token0={r['token0']} token1={r['token1']} "
                f"sym={r['symbol'] or '-'} fee={r['fee']} price={fmt_price(r['price'])} "
                f"liq={r['liquidity']} risk={r['risk']:.2f}")

    def decision(self, r: dict) -> None:
        gated = f" ({r['reason']})" if r.get("reason") else ""
        self._w(f"[decision] pool={r['pool']} engine={r['action']}@{r['confidence']:.2f} "
                f"final={r['final_action']}{gated}")

    def action(self, text: str) -> None:
        self._w(f"[action] {text}")


class Dashboard:
    """Rich Live layout: launches | decisions | action log."""

    def __init__(self, console: Optional[Console] = None, rows: int = 12):
        self.console = console or Console()
        self.launches: Deque[dict] = deque(maxlen=rows)
        self.decisions: Deque[dict] = deque(maxlen=rows)
        self.actions: Deque[str] = deque(maxlen=rows * 2)
        self._status = "starting..."
        self._live = None

    # --- sink interface ---
    def status(self, text: str) -> None:
        self._status = text
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
        for col in ("time", "pool", "tokens", "fee", "price", "liquidity", "risk"):
            t.add_column(col, overflow="fold")
        for r in self.launches:
            t.add_row(r["time"], short(r["pool"]),
                      r["symbol"] or f"{short(r['token0'])}/{short(r['token1'])}",
                      str(r["fee"]), fmt_price(r["price"]), str(r["liquidity"]),
                      f"{r['risk']:.2f}")
        d = Table(expand=True, show_edge=False)
        for col in ("pool", "engine", "conf", "final", "reason"):
            d.add_column(col, overflow="fold")
        for r in self.decisions:
            style = "green" if r["final_action"] == "buy" else "dim"
            d.add_row(short(r["pool"]), r["action"], f"{r['confidence']:.2f}",
                      f"[{style}]{r['final_action']}[/{style}]", r.get("reason") or "")
        layout = Layout()
        layout.split_column(Layout(name="top", ratio=2), Layout(name="log", ratio=1, minimum_size=5))
        layout["top"].split_row(
            Layout(Panel(t, title="New pool launches")),
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
