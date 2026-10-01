#!/usr/bin/env python3
"""Calibration map for the Nimble veto: calibrate(source, p) -> calibrated probability.

Moved out of calibration_ledger.py (which re-exports every name here); the scored items
still come from calibration_ledger._all_items, read lazily (no import cycle).

calibrate(source, p): identity until `source` has >= MIN_N (calibration_ledger.MIN_N, 30)
scorable (p, y) items; then a monotone map fitted on them: pool-adjacent-violators
isotonic regression whose block value is Laplace smoothed, (sum_y + 1) / (n + 2), pooling
adjacent blocks until the values are non-decreasing (ties in p start in one block).
Between block centroids p is interpolated linearly, flat outside them, clamped to [0, 1].
Deterministic for fixed files; items are read once and each source is fitted once per
process. Never raises: any failure -> identity. Read-only: no network, no writes.
(c) GOATWORTHY Post LLC.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

_FITS = {}
_ITEMS = None


def _ledger():
    import calibration_ledger as cl
    return cl


def _reset_cache(items=None):
    """Drop fitted maps. items: a preloaded item list used instead of the files (tests)."""
    global _ITEMS
    _FITS.clear()
    _ITEMS = items


def _scored_pairs(source):
    global _ITEMS
    if _ITEMS is None:
        try:
            cl = _ledger()
            d = cl.default_paths()
            _ITEMS = cl._all_items(cl._rows(d["decisions"]), cl._rows(d["ftl"]),
                                   cl._rows(d["mirofish"]), datetime.now(timezone.utc))
        except Exception:
            _ITEMS = []
    return [(it["p"], it["y"]) for it in _ITEMS
            if it.get("source") == source and it.get("p") is not None and it.get("y") is not None]


def isotonic_fit(pairs):
    """[(p, y)] -> knots [(x, v)]: x strictly increasing, v non-decreasing in (0, 1). [] if empty."""
    groups = {}
    for p, y in pairs:
        g = groups.setdefault(float(p), [0.0, 0])
        g[0], g[1] = g[0] + float(y), g[1] + 1
    val = lambda b: (b[1] + 1.0) / (b[2] + 2.0)  # noqa: E731  Laplace-smoothed block value
    st = []  # blocks [sum_p, sum_y, n]
    for p in sorted(groups):
        st.append([p * groups[p][1], groups[p][0], groups[p][1]])
        while len(st) > 1 and val(st[-2]) > val(st[-1]):
            b = st.pop()
            st[-1] = [st[-1][0] + b[0], st[-1][1] + b[1], st[-1][2] + b[2]]
    return [(b[0] / b[2], val(b)) for b in st]


def _interp(knots, p):
    if p <= knots[0][0]:
        return knots[0][1]
    for (x0, v0), (x1, v1) in zip(knots, knots[1:]):
        if p <= x1:
            return v0 + (v1 - v0) * (p - x0) / (x1 - x0)
    return knots[-1][1]


def calibrate_ex(source, p, *, pairs=None, min_n=None):
    """{"p_cal", "calibration": "identity"|"isotonic", "n"}. pairs: fit on these (no cache).
    min_n None -> calibration_ledger.MIN_N."""
    try:
        cl = _ledger()
        q = cl._num(p)
        min_n = cl.MIN_N if min_n is None else min_n
    except Exception:
        return {"p_cal": None, "calibration": "identity", "n": 0}
    if q is None:
        return {"p_cal": None, "calibration": "identity", "n": 0}
    q = min(max(q, 0.0), 1.0)
    try:
        if pairs is None:
            key = (str(source), int(min_n))
            if key not in _FITS:
                sc = _scored_pairs(str(source))
                _FITS[key] = (len(sc), isotonic_fit(sc) if len(sc) >= min_n else [])
            n, knots = _FITS[key]
        else:
            n = len(pairs)
            knots = isotonic_fit(pairs) if n >= min_n else []
        if not knots:
            return {"p_cal": q, "calibration": "identity", "n": n}
        return {"p_cal": round(min(max(_interp(knots, q), 0.0), 1.0), 6),
                "calibration": "isotonic", "n": n}
    except Exception:
        return {"p_cal": q, "calibration": "identity", "n": 0}


def calibrate(source, p, **kw):
    """Calibrated probability for `source` (rule above). Never raises."""
    return calibrate_ex(source, p, **kw)["p_cal"]
