"""Ten-level quantitative engine. Each level module exposes FORMULAS, mapping a formula id
("1.1" .. "10.5") to the public callables that implement it."""
from __future__ import annotations

import importlib

LEVELS = {
    1: "level1_probability", 2: "level2_statistics", 3: "level3_linalg", 4: "level4_optimization",
    5: "level5_timeseries", 6: "level6_stochastic", 7: "level7_derivatives", 8: "level8_portfolio_risk",
    9: "level9_microstructure", 10: "level10_ml_research",
}


def formula_registry() -> dict[str, tuple[str, tuple[str, ...]]]:
    """Formula id -> (module name, implementing callables). Imports the level modules on demand."""
    out: dict[str, tuple[str, tuple[str, ...]]] = {}
    for module in LEVELS.values():
        mod = importlib.import_module(f"{__name__}.{module}")
        for fid, names in mod.FORMULAS.items():
            out[fid] = (module, tuple(names))
    return out
