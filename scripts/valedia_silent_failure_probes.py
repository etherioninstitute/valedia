#!/usr/bin/env python3
"""VAL-11 silent-failure fail-closed probes (paper/ops).

Signals wired:
  #10 Presence ≠ readiness — LMS /api/v0/models state must be loaded
  #11 Stub/empty behind green exit — empty jsonl / ledger / fee artifact ≠ PASS
  #14 Null display mask — unhealthy fee artifact / null fee-true claimed PASS ≠ PASS
  #2/#7 Kalshi quote consumer None≠0 — static source + last weather artifact shape
  #5 Weather skip tallies — skipped_non_nws (+ price_stats) on last morning-go artifact
  #16 Age gates — fee-map as_of + Davidd last-row age WARN/FAIL thresholds
  #ornith_ctx — loaded_ctx vs Hermes floor 65536 (WARN below; PASS >=floor or not loaded)

Fence: read-only. No --live, no WEATHER_LIVE flip, no LMS unload, no orders.
Exit 0 only if all checks PASS/WARN/SKIP. Empty artifact ⇒ exit 1 (FAIL).
WARN does not fail exit; documents soft age breaches under FAIL threshold.
"""
from __future__ import annotations

import json
import os
import re
import socket
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, List, Optional, Tuple

HOME = os.path.expanduser("~")
OC = os.path.join(HOME, ".openclaw")
WS = os.path.join(OC, "workspace")
WS2 = os.path.join(OC, "workspace-v2")

DAVIDD_JSONL_CANDIDATES = [
    os.path.join(WS2, "data", "daviddtech-paper", "decisions.jsonl"),
    os.path.join(WS, "data", "daviddtech-paper", "decisions.jsonl"),
]
LEDGER = os.path.join(WS, "models", "mirofish_accuracy_ledger.jsonl")
FEE_MAP_CANDIDATES = [
    os.path.join(WS2, "recommendations", "kalshi-fee-map.json"),
    os.path.join(WS, "recommendations", "kalshi-fee-map.json"),
]
# weather_morning_go.py writes REC/WEATHER-MORNING-GO-LAST.json (ROOT=workspace[-v2])
WEATHER_LAST_CANDIDATES = [
    os.path.join(WS2, "recommendations", "WEATHER-MORNING-GO-LAST.json"),
    os.path.join(WS, "recommendations", "WEATHER-MORNING-GO-LAST.json"),
    os.path.join(OC, "logs", "weather_morning_go.last.json"),  # legacy probe path
]
KALSHI_QUOTE_CANDIDATES = [
    os.path.join(WS2, "scripts", "integrations", "kalshi_quote.py"),
    os.path.join(WS, "scripts", "integrations", "kalshi_quote.py"),
]
WEATHER_GO_CANDIDATES = [
    os.path.join(WS2, "scripts", "integrations", "weather_morning_go.py"),
    os.path.join(WS, "scripts", "integrations", "weather_morning_go.py"),
]

# Ornith-solo seat (VAL-11 CTO). Override: VAL11_REQUIRED_LMS=id1,id2
DEFAULT_REQUIRED_LMS = ["ornith-1.5-35b-a3b-mlx"]

# Age gates (hours). Override via env.
FEE_ASOF_WARN_H = float(os.environ.get("VAL11_FEE_ASOF_WARN_H", "72"))
FEE_ASOF_FAIL_H = float(os.environ.get("VAL11_FEE_ASOF_FAIL_H", "168"))
DAVIDD_AGE_WARN_H = float(os.environ.get("VAL11_DAVIDD_AGE_WARN_H", "36"))
DAVIDD_AGE_FAIL_H = float(os.environ.get("VAL11_DAVIDD_AGE_FAIL_H", "168"))

PASS, FAIL, SKIP, WARN = "PASS", "FAIL", "SKIP", "WARN"
results: List[Tuple[str, str, str]] = []


def rec(level: str, name: str, detail: str = "") -> None:
    results.append((level, name, detail))
    tag = {"PASS": "PASS", "FAIL": "FAIL", "SKIP": "SKIP", "WARN": "WARN"}[level]
    extra = f" — {detail}" if detail else ""
    print(f"[{tag}] {name}{extra}")


def http_json(url: str, timeout: float = 5.0) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def port_open(host: str, port: int, timeout: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def check_lms_loaded() -> None:
    """Signal #10 — listed ≠ loaded."""
    required = [
        x.strip()
        for x in os.environ.get("VAL11_REQUIRED_LMS", ",".join(DEFAULT_REQUIRED_LMS)).split(",")
        if x.strip()
    ]
    if not port_open("127.0.0.1", 1234):
        rec(FAIL, "Signal#10 LMS :1234", "port closed")
        return
    try:
        doc = http_json("http://127.0.0.1:1234/api/v0/models")
    except Exception as e:
        # Fallback: /v1/models alone is Signal #10 trap — do not PASS on list-only
        rec(FAIL, "Signal#10 LMS /api/v0/models", f"unreachable ({e}); refuse list-only PASS")
        return
    models = doc.get("data") if isinstance(doc, dict) else doc
    if not isinstance(models, list):
        rec(FAIL, "Signal#10 LMS models shape", type(models).__name__)
        return
    by_id = {}
    for m in models:
        if isinstance(m, dict) and m.get("id"):
            by_id[m["id"]] = m
    missing = [r for r in required if r not in by_id]
    if missing:
        rec(FAIL, "Signal#10 LMS required missing from catalog", ",".join(missing))
        return
    not_loaded = []
    for r in required:
        st = (by_id[r].get("state") or "").lower()
        if st != "loaded":
            not_loaded.append(f"{r}:{st or 'unknown'}")
    if not_loaded:
        rec(
            FAIL,
            "Signal#10 exists≠loaded",
            "required registered but not loaded: " + ", ".join(not_loaded),
        )
        return
    loaded = [m.get("id") for m in models if isinstance(m, dict) and (m.get("state") or "").lower() == "loaded"]
    rec(PASS, "Signal#10 LMS loaded", f"required={required} loaded_all={loaded}")


def _read_jsonl_rows(path: str) -> List[dict]:
    rows: List[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                rows.append({"_unparsed": True})
    return rows


def check_davidd_jsonl() -> None:
    """Signal #11 — empty decisions.jsonl ≠ PASS."""
    path = next((p for p in DAVIDD_JSONL_CANDIDATES if os.path.isfile(p)), None)
    if path is None:
        rec(FAIL, "Signal#11 Davidd jsonl", "missing decisions.jsonl (empty artifact ≠ PASS)")
        return
    if os.path.getsize(path) == 0:
        rec(FAIL, "Signal#11 Davidd jsonl empty", path)
        return
    rows = _read_jsonl_rows(path)
    usable = [r for r in rows if isinstance(r, dict) and not r.get("_unparsed")]
    if not usable:
        rec(FAIL, "Signal#11 Davidd jsonl no usable rows", f"path={path} raw={len(rows)}")
        return
    # fence sanity (paper only)
    bad = [r for r in usable if r.get("create_order") in (True, "true", 1, "1") or r.get("live") in (True, "true", 1, "1")]
    if bad:
        rec(FAIL, "Signal#11 Davidd fence breach in jsonl", f"n={len(bad)} path={path}")
        return
    rec(PASS, "Signal#11 Davidd jsonl", f"rows={len(usable)} path={path}")


def check_mirofish_ledger() -> None:
    """Signal #11 — empty accuracy ledger ≠ PASS (unless explicit skip marker)."""
    skip = os.path.join(OC, "state", "mirofish_ledger_skip")
    if os.path.isfile(skip):
        reason = open(skip, encoding="utf-8").read().strip() or "marker present"
        rec(SKIP, "Signal#11 MiroFish ledger", f"explicit skip: {reason[:120]}")
        return
    if not os.path.isfile(LEDGER):
        rec(FAIL, "Signal#11 MiroFish ledger missing", LEDGER)
        return
    if os.path.getsize(LEDGER) == 0:
        rec(FAIL, "Signal#11 MiroFish ledger empty file", LEDGER)
        return
    rows = _read_jsonl_rows(LEDGER)
    preds = [r for r in rows if isinstance(r, dict) and r.get("type") == "prediction"]
    if not preds:
        rec(FAIL, "Signal#11 MiroFish ledger no predictions", f"rows={len(rows)} path={LEDGER}")
        return
    rec(PASS, "Signal#11 MiroFish ledger", f"predictions={len(preds)} path={LEDGER}")


def _load_fee_doc() -> Tuple[str | None, dict | None]:
    for p in FEE_MAP_CANDIDATES:
        if os.path.isfile(p):
            try:
                return p, json.load(open(p, encoding="utf-8"))
            except (OSError, ValueError):
                return p, None
    return None, None


def check_fee_map_artifact() -> None:
    """Signal #11 — fee-map missing/empty series ≠ PASS."""
    path, doc = _load_fee_doc()
    if path is None:
        rec(FAIL, "Signal#11 fee-map artifact missing", "kalshi-fee-map.json not found")
        return
    if doc is None or not isinstance(doc, dict):
        rec(FAIL, "Signal#11 fee-map unreadable", path)
        return
    series = doc.get("series") if isinstance(doc.get("series"), dict) else {}
    mode, as_of = doc.get("mode"), doc.get("as_of")
    if mode != "get" or not series or not as_of:
        rec(
            FAIL,
            "Signal#11 fee-map empty/unhealthy shape",
            f"mode={mode!r} series_n={len(series)} as_of={as_of!r} path={path}",
        )
        return
    rec(PASS, "Signal#11 fee-map artifact", f"series_n={len(series)} as_of={as_of} path={path}")


def _load_weather_last() -> Tuple[str | None, dict | None]:
    """Prefer newest existing LAST by mtime across dual-home candidates."""
    existing = [p for p in WEATHER_LAST_CANDIDATES if os.path.isfile(p)]
    if not existing:
        return None, None
    existing.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    for path in existing:
        try:
            return path, json.load(open(path, encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return existing[0], None


def check_fee_null_mask() -> None:
    """Signal #14 — null fee-true / unhealthy artifact must not read as PASS."""
    path, doc = _load_fee_doc()
    if path is None or not isinstance(doc, dict):
        rec(FAIL, "Signal#14 fee-true mask", "no fee-map to validate (null/missing)")
        return
    series = doc.get("series") if isinstance(doc.get("series"), dict) else {}
    healthy = doc.get("mode") == "get" and bool(series) and bool(doc.get("as_of"))
    if not healthy:
        rec(FAIL, "Signal#14 fee artifact unhealthy", f"path={path}")
        return
    # Optional weather last-run: if present, refuse PASS-shaped claim with all-null fee_true
    wpath, last = _load_weather_last()
    if wpath and isinstance(last, dict):
        status = (last.get("status") or last.get("result") or last.get("preflight") or "").upper()
        n_ev = last.get("n_fee_true_ev")
        n_null = last.get("n_price_null")
        if status == "PASS" and n_ev == 0 and (n_null or 0) > 0 and not last.get("skip_reason"):
            rec(
                FAIL,
                "Signal#14 null fee-true masked as PASS",
                f"n_fee_true_ev=0 n_price_null={n_null} file={wpath}",
            )
            return
        rec(PASS, "Signal#14 weather last-run", f"status={status!r} n_ev={n_ev} n_null={n_null} file={wpath}")
        return
    rec(PASS, "Signal#14 fee-true not null-masked", f"fee-map healthy series_n={len(series)}")


def check_quote_none_not_zero() -> None:
    """Signal #2/#7 — None never laundered to 0 for book prices (static + artifact).

    Read-only: no live Kalshi. Missing weather artifact ⇒ SKIP (never invent prices).
    """
    kq = next((p for p in KALSHI_QUOTE_CANDIDATES if os.path.isfile(p)), None)
    wg = next((p for p in WEATHER_GO_CANDIDATES if os.path.isfile(p)), None)
    if kq is None:
        rec(FAIL, "Signal#2/#7 kalshi_quote.py missing", "cannot audit writer")
        return
    src = open(kq, encoding="utf-8").read()
    # Writer contract: zero/missing → None (kalshi_quote.py:18-19, return ask may be None)
    if "if x <= 0.0:" not in src or "return None" not in src:
        rec(FAIL, "Signal#2/#7 kalshi_quote zero→None missing", kq)
        return
    # Consumer must not do yes_price(...) or 0
    if wg is None:
        rec(FAIL, "Signal#2/#7 weather_morning_go.py missing", "cannot audit consumer")
        return
    wsrc = open(wg, encoding="utf-8").read()
    bad_coercions = re.findall(r"yes_price\s*\([^)]*\)\s*or\s*0", wsrc)
    if bad_coercions:
        rec(FAIL, "Signal#2/#7 yes_price or 0 coercion", f"n={len(bad_coercions)} file={wg}")
        return
    # price_stats null path must use `is None` (weather_morning_go.py ~L326)
    if "if r.get(\"price\") is None:" not in wsrc and "if r.get('price') is None:" not in wsrc:
        rec(FAIL, "Signal#2/#7 price_stats None check missing", wg)
        return
    # Residual ticket: pick_admit still uses fee_true_ev or 0 for ranking — WARN only
    residual = "float(r.get(\"fee_true_ev\") or 0)" in wsrc or "float(r.get('fee_true_ev') or 0)" in wsrc
    # Runtime shape on last weather artifact — never call Kalshi
    wpath, last = _load_weather_last()
    if wpath is None or not isinstance(last, dict):
        detail = f"static ok kalshi_quote+price_stats is None; weather artifact missing → SKIP live shape (no invent)"
        if residual:
            detail += "; residual pick_admit fee_true_ev or 0 (ticketed)"
        rec(SKIP, "Signal#2/#7 quote None≠0 (artifact)", detail)
        return
    n_price = last.get("n_price")
    n_null = last.get("n_price_null")
    n_markets = last.get("n_markets")
    if not isinstance(n_price, int) or not isinstance(n_null, int):
        rec(FAIL, "Signal#2/#7 weather artifact price keys", f"n_price={n_price!r} n_price_null={n_null!r} file={wpath}")
        return
    if isinstance(n_markets, int) and n_markets > 0 and (n_price + n_null) == 0:
        rec(FAIL, "Signal#2/#7 price tallies empty with markets", f"n_markets={n_markets} file={wpath}")
        return
    detail = f"n_price={n_price} n_price_null={n_null} file={wpath}"
    if residual:
        detail += "; residual pick_admit fee_true_ev or 0 @L130 (ticketed, not FAIL)"
        rec(WARN, "Signal#2/#7 quote None≠0", detail)
        return
    rec(PASS, "Signal#2/#7 quote None≠0", detail)


def check_weather_skip_tallies() -> None:
    """Signal #5 — skipped_non_nws (+ actionable tallies) present on last morning-go artifact."""
    wpath, last = _load_weather_last()
    if wpath is None:
        rec(SKIP, "Signal#5 weather skip tallies", "WEATHER-MORNING-GO-LAST.json missing (no invent; SKIP)")
        return
    if not isinstance(last, dict):
        rec(FAIL, "Signal#5 weather last unreadable", wpath)
        return
    required = ("skipped_non_nws", "n_price", "n_price_null", "n_fee_true_ev", "n_alert", "admitted")
    missing = [k for k in required if k not in last]
    if missing:
        rec(FAIL, "Signal#5 skip/actionable keys missing", f"missing={missing} file={wpath}")
        return
    # #8-ish: both ceiling (n_alert/eligible) and actionable (admitted) must be reportable
    detail = (
        f"skipped_non_nws={last.get('skipped_non_nws')} n_alert={last.get('n_alert')} "
        f"admitted={last.get('admitted')} n_fee_true_ev={last.get('n_fee_true_ev')} file={wpath}"
    )
    rec(PASS, "Signal#5 weather skip tallies", detail)


def _parse_ts(raw: Any) -> Optional[datetime]:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    # Accept ISO with Z or offset
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def check_age_gates() -> None:
    """Signal #16 — fee-map as_of + Davidd last-row age thresholds."""
    now = datetime.now(timezone.utc)

    # Fee-map as_of
    path, doc = _load_fee_doc()
    if path is None or not isinstance(doc, dict):
        rec(FAIL, "Signal#16 fee as_of", "fee-map missing (empty ≠ PASS)")
    else:
        as_of = _parse_ts(doc.get("as_of"))
        if as_of is None:
            rec(FAIL, "Signal#16 fee as_of unparseable", f"as_of={doc.get('as_of')!r} path={path}")
        else:
            age_h = (now - as_of).total_seconds() / 3600.0
            detail = f"as_of={doc.get('as_of')} age_h={age_h:.1f} warn={FEE_ASOF_WARN_H} fail={FEE_ASOF_FAIL_H} path={path}"
            if age_h >= FEE_ASOF_FAIL_H:
                rec(FAIL, "Signal#16 fee as_of stale", detail)
            elif age_h >= FEE_ASOF_WARN_H:
                rec(WARN, "Signal#16 fee as_of aging", detail)
            else:
                rec(PASS, "Signal#16 fee as_of fresh", detail)

    # Davidd last row
    dpath = next((p for p in DAVIDD_JSONL_CANDIDATES if os.path.isfile(p)), None)
    if dpath is None:
        rec(FAIL, "Signal#16 Davidd last-row age", "decisions.jsonl missing")
        return
    rows = _read_jsonl_rows(dpath)
    usable = [r for r in rows if isinstance(r, dict) and not r.get("_unparsed")]
    if not usable:
        rec(FAIL, "Signal#16 Davidd last-row age", f"no usable rows path={dpath}")
        return
    last = usable[-1]
    ts = _parse_ts(last.get("ts") or last.get("alert_timestamp") or last.get("timestamp"))
    if ts is None:
        rec(FAIL, "Signal#16 Davidd last-row ts missing", f"keys ts/alert_timestamp empty path={dpath}")
        return
    age_h = (now - ts).total_seconds() / 3600.0
    detail = f"ts={ts.isoformat()} age_h={age_h:.1f} warn={DAVIDD_AGE_WARN_H} fail={DAVIDD_AGE_FAIL_H} path={dpath}"
    if age_h >= DAVIDD_AGE_FAIL_H:
        rec(FAIL, "Signal#16 Davidd last-row stale", detail)
    elif age_h >= DAVIDD_AGE_WARN_H:
        rec(WARN, "Signal#16 Davidd last-row aging", detail)
    else:
        rec(PASS, "Signal#16 Davidd last-row fresh", detail)




def check_ornith_ctx() -> None:
    """Probe #ornith_ctx — fluid floor vs hard 131k.

    WARN if Ornith loaded and loaded_ctx < 65536 (Hermes min).
    PASS if loaded_ctx >= floor, or not loaded (INFO).
    parallel≠2 is INFO only — never FAILs the suite alone.
    """
    import re
    import shutil
    import subprocess

    floor = 65536
    model_id = os.environ.get("ORNITH_MODEL_ID", "ornith-1.5-35b-a3b-mlx")
    if not port_open("127.0.0.1", 1234):
        rec(WARN, "#ornith_ctx LMS :1234", "port closed — cannot read loaded_ctx")
        return
    try:
        doc = http_json("http://127.0.0.1:1234/api/v0/models")
    except Exception as e:
        rec(WARN, "#ornith_ctx LMS models", f"unreachable ({e})")
        return
    models = doc.get("data") if isinstance(doc, dict) else doc
    if not isinstance(models, list):
        rec(WARN, "#ornith_ctx LMS shape", type(models).__name__)
        return
    target = None
    for m in models:
        if isinstance(m, dict) and m.get("id") == model_id:
            target = m
            break
    if target is None:
        rec(PASS, "#ornith_ctx not in catalog", f"{model_id} absent (INFO)")
        return
    state = (target.get("state") or "").lower()
    if state != "loaded":
        rec(PASS, "#ornith_ctx not loaded", f"{model_id} state={state or 'unknown'} (INFO)")
        return
    loaded_ctx = target.get("loaded_context_length")
    try:
        loaded_ctx_i = int(loaded_ctx) if loaded_ctx is not None else None
    except (TypeError, ValueError):
        loaded_ctx_i = None
    parallel_note = ""
    try:
        lms = shutil.which("lms") or os.path.expanduser("~/.lmstudio/bin/lms")
        if lms and os.path.isfile(lms):
            out = subprocess.check_output(
                [lms, "ps"], text=True, timeout=8, stderr=subprocess.STDOUT
            )
            for ln in out.splitlines():
                if model_id not in ln:
                    continue
                m2 = re.search(r"(\d{4,})\s+(\d+)\s+", ln)
                if m2:
                    par = int(m2.group(2))
                    if par != 2:
                        parallel_note = f" parallel={par}≠2 (INFO only, not FAIL)"
                break
    except Exception:
        pass
    if loaded_ctx_i is None:
        rec(
            WARN,
            "#ornith_ctx loaded_ctx missing",
            f"{model_id} loaded but no loaded_context_length{parallel_note}",
        )
        return
    detail = f"loaded_ctx={loaded_ctx_i} floor={floor}{parallel_note}"
    if loaded_ctx_i < floor:
        rec(WARN, "#ornith_ctx below Hermes floor", detail)
    else:
        rec(PASS, "#ornith_ctx ok_vs_floor", detail)


def check_ports_smoke() -> None:
    """Supporting surface smoke (not a checklist row alone). Fail-closed on Neo4j if claimed."""
    neo = port_open("127.0.0.1", 7687)
    hermes = {p: port_open("127.0.0.1", p) for p in (8642, 8643, 8644, 8645)}
    pc = port_open("127.0.0.1", 3100)
    if not neo:
        rec(FAIL, "Neo4j :7687", "LISTEN missing (VAL-11 probe surface)")
    else:
        rec(PASS, "Neo4j :7687", "LISTEN")
    down = [str(p) for p, ok in hermes.items() if not ok]
    if down:
        rec(FAIL, "Hermes gateways", "down: " + ",".join(down))
    else:
        rec(PASS, "Hermes :8642-8645", "all LISTEN")
    if not pc:
        rec(FAIL, "Paperclip :3100", "LISTEN missing")
    else:
        try:
            h = http_json("http://127.0.0.1:3100/api/health")
            st = h.get("status") if isinstance(h, dict) else None
            if st != "ok":
                rec(FAIL, "Paperclip /api/health", repr(st))
            else:
                rec(PASS, "Paperclip /api/health", "ok")
        except Exception as e:
            rec(FAIL, "Paperclip /api/health", str(e)[:80])


def main() -> int:
    print(f"VAL-11 silent-failure probes @ {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M %Z')}")
    print("Fail-closed: empty artifact ≠ PASS\n")
    check_lms_loaded()            # #10
    check_ornith_ctx()             # #ornith_ctx
    check_davidd_jsonl()          # #11
    check_mirofish_ledger()       # #11
    check_fee_map_artifact()      # #11
    check_fee_null_mask()         # #14
    check_quote_none_not_zero()   # #2/#7
    check_weather_skip_tallies()  # #5
    check_age_gates()             # #16
    check_ports_smoke()
    n_pass = sum(1 for L, _, _ in results if L == PASS)
    n_fail = sum(1 for L, _, _ in results if L == FAIL)
    n_skip = sum(1 for L, _, _ in results if L == SKIP)
    n_warn = sum(1 for L, _, _ in results if L == WARN)
    print(f"\n=== {n_pass} PASS · {n_warn} WARN · {n_skip} SKIP · {n_fail} FAIL ===")
    print("GROK_EXIT hint: 0 iff FAIL==0 (WARN/SKIP allowed)")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
