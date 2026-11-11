"""Where each of the sheet's 50 formulas is used by the trader.

The engine implements every formula; this module answers the other question: is it part of the trading
path, and where. A formula is "wired" when code outside the quant engine imports its level module and
calls one of its functions. The few that cannot apply to this trader are listed with the reason, and a
test fails if a formula is neither wired nor listed, or is listed but used.

    python -m valedia_trader.quant_engine            the table
"""
from __future__ import annotations

import re
from pathlib import Path

from valedia_trader.quant_engine import LEVELS, formula_registry

__all__ = ["NOT_APPLICABLE", "wiring", "summary", "render"]

_NO_OPTIONS = "the trader holds no options; this becomes usable when an options venue is added"
NOT_APPLICABLE: dict[str, str] = {
    "4.1": "option P&L from delta, gamma and theta: " + _NO_OPTIONS,
    "6.4": "Ito's lemma for option and binary-contract prices: no options or binary contracts are traded",
    "7.1": "Black-Scholes pricing: " + _NO_OPTIONS,
    "7.2": "put-call parity: " + _NO_OPTIONS,
    "7.3": "the Greeks: " + _NO_OPTIONS,
    "7.4": "implied volatility: " + _NO_OPTIONS,
    "7.5": "gamma P&L: " + _NO_OPTIONS,
    "9.2": "order-book imbalance: the trader works from daily bars and has no order-book feed",
}


def _sources(root: Path) -> dict[str, str]:
    engine = root / "quant_engine"
    return {str(p.relative_to(root)): p.read_text() for p in sorted(root.rglob("*.py")) if engine not in p.parents}


def wiring(package_root: str | Path | None = None) -> list[dict]:
    """One row per formula id: its functions, the files that use them, and its status."""
    root = Path(package_root) if package_root else Path(__file__).resolve().parents[1]
    sources = _sources(root)
    rows = []
    for fid, (module, names) in sorted(formula_registry().items(), key=lambda kv: tuple(int(x) for x in kv[0].split("."))):
        users = {}
        for path, text in sources.items():
            if module not in text:                       # the file must import that level's module
                continue
            hit = sorted(n for n in names if re.search(r"\b" + re.escape(n) + r"\b", text))
            if hit:
                users[path] = hit
        status = "wired" if users else ("not applicable" if fid in NOT_APPLICABLE else "unwired")
        rows.append({"formula": fid, "level": int(fid.split(".")[0]), "module": module, "functions": list(names),
                     "used_in": users, "status": status, "reason": NOT_APPLICABLE.get(fid, "") if not users else ""})
    return rows


def summary(rows: list[dict] | None = None) -> dict:
    rows = wiring() if rows is None else rows
    count = lambda s: sum(r["status"] == s for r in rows)
    return {"formulas": len(rows), "wired": count("wired"), "not_applicable": count("not applicable"), "unwired": count("unwired"),
            "unwired_ids": [r["formula"] for r in rows if r["status"] == "unwired"],
            "by_level": {level: f"{sum(r['status'] == 'wired' for r in rows if r['level'] == level)}/{sum(r['level'] == level for r in rows)}"
                         for level in sorted(LEVELS)}}


def render(rows: list[dict] | None = None) -> str:
    rows = wiring() if rows is None else rows
    lines = []
    for r in rows:
        where = ", ".join(sorted(p.rsplit("/", 1)[-1].removesuffix(".py") for p in r["used_in"])) if r["used_in"] else r["reason"]
        lines.append(f"{r['formula']:>5}  {r['status']:<14}  {r['functions'][0]:<34}  {where}")
    s = summary(rows)
    lines.append(f"\n{s['wired']} of {s['formulas']} wired, {s['not_applicable']} not applicable to this trader, {s['unwired']} unwired")
    return "\n".join(lines)
