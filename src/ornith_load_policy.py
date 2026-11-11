#!/usr/bin/env python3
"""Ornith LMS load fluid policy (paper/ops).

Adaptive context sizing — NOT a hard global 131k forever.
Floor = Hermes min (64k). Default desk target = 131k. Heavy = up to model max.

Fence (AGENT-INVENTORY / no auto-unload):
  - status / recommend never unload or reload.
  - --apply prints the exact `lms load ...` command by default.
  - Actual load only when ORNITH_LOAD_APPLY=1 AND --purpose is set.
  - Never unloads a different model; never swaps without explicit apply path.

CLI:
  python ornith_load_policy.py status
  python ornith_load_policy.py recommend --purpose desk|light|heavy
  python ornith_load_policy.py recommend --purpose desk --apply   # print-only unless env
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any, Optional

FLOOR_CTX = 65536  # Hermes min
DEFAULT_CTX = 131072  # desk / paperclip target (not a hard global)
PARALLEL_DEFAULT = 2
MODEL_ID = os.environ.get("ORNITH_MODEL_ID", "ornith-1.5-35b-a3b-mlx")
LMS_API = os.environ.get("ORNITH_LMS_API", "http://127.0.0.1:1234/api/v0/models")
LMS_BIN = os.environ.get("ORNITH_LMS_BIN", "") or (
    shutil.which("lms") or os.path.expanduser("~/.lmstudio/bin/lms")
)


@dataclass
class OrnithStatus:
    model_id: str
    loaded: bool
    loaded_ctx: Optional[int]
    max_ctx: Optional[int]
    parallel: Optional[int]
    ttl: Optional[str]
    ok_vs_floor: bool
    recommendation: str  # ok | bump_to_floor | align_hermes | leave_alone
    source: str
    detail: str = ""


def _http_json(url: str, timeout: float = 5.0) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _parse_lms_ps() -> Optional[dict[str, Any]]:
    """Parse `lms ps` for MODEL_ID row. Returns dict or None if unavailable."""
    if not LMS_BIN or not os.path.isfile(LMS_BIN):
        return None
    try:
        out = subprocess.check_output(
            [LMS_BIN, "ps"], stderr=subprocess.STDOUT, text=True, timeout=8
        )
    except (OSError, subprocess.SubprocessError):
        return None
    lines = [ln.rstrip() for ln in out.splitlines() if ln.strip()]
    # Header: IDENTIFIER MODEL STATUS SIZE CONTEXT PARALLEL DEVICE TTL
    for ln in lines[1:]:
        # Split on 2+ spaces to keep identifier intact
        parts = re.split(r"\s{2,}", ln.strip())
        if len(parts) < 5:
            # fallback whitespace split
            parts = ln.split()
        if not parts:
            continue
        ident = parts[0]
        if ident != MODEL_ID and (len(parts) < 2 or parts[1] != MODEL_ID):
            continue
        # Try structured columns from header-aligned split
        ctx = parallel = ttl = None
        status_tok = ""
        try:
            # IDENTIFIER MODEL STATUS SIZE CONTEXT PARALLEL DEVICE [TTL]
            if len(parts) >= 6 and parts[0] == MODEL_ID:
                status_tok = parts[2]
                ctx = int(parts[4].replace(",", ""))
                parallel = int(parts[5])
                ttl = parts[7] if len(parts) > 7 else ""
            else:
                # looser: find CONTEXT-looking int near end
                nums = [int(x) for x in re.findall(r"\b\d+\b", ln)]
                # typical: size-ish skipped; context then parallel
                # From sample: ... 22.62 GB 131072 2 Local
                m = re.search(
                    r"\b(IDLE|BUSY|LOADING|loaded|idle|busy)\b.*?(\d{4,})\s+(\d+)\s+",
                    ln,
                    re.I,
                )
                if m:
                    status_tok = m.group(1)
                    ctx = int(m.group(2))
                    parallel = int(m.group(3))
                elif len(nums) >= 2:
                    ctx, parallel = nums[-2], nums[-1]
                ttl_m = re.search(r"TTL\s*[:=]?\s*(\S+)", ln, re.I)
                ttl = ttl_m.group(1) if ttl_m else ""
        except (ValueError, IndexError):
            pass
        loaded = status_tok.upper() in ("IDLE", "BUSY", "LOADING", "LOADED") or (
            ctx is not None
        )
        return {
            "loaded": bool(loaded),
            "loaded_ctx": ctx,
            "parallel": parallel,
            "ttl": ttl or "",
            "raw": ln,
        }
    return {"loaded": False, "loaded_ctx": None, "parallel": None, "ttl": "", "raw": ""}


def _from_api() -> Optional[dict[str, Any]]:
    try:
        doc = _http_json(LMS_API)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError):
        return None
    models = doc.get("data") if isinstance(doc, dict) else doc
    if not isinstance(models, list):
        return None
    for m in models:
        if not isinstance(m, dict):
            continue
        if m.get("id") != MODEL_ID:
            continue
        state = (m.get("state") or "").lower()
        return {
            "loaded": state == "loaded",
            "loaded_ctx": m.get("loaded_context_length"),
            "max_ctx": m.get("max_context_length"),
            "state": state,
            "raw": m,
        }
    return {
        "loaded": False,
        "loaded_ctx": None,
        "max_ctx": None,
        "state": "missing",
        "raw": None,
    }


def _recommend(
    loaded: bool,
    loaded_ctx: Optional[int],
    purpose: Optional[str] = None,
) -> str:
    """ok | bump_to_floor | align_hermes | leave_alone"""
    if not loaded:
        return "leave_alone"  # do not auto-load; ops decide
    if loaded_ctx is None:
        return "align_hermes"
    if loaded_ctx < FLOOR_CTX:
        return "bump_to_floor"
    if purpose:
        target = choose_ctx(purpose)
        if loaded_ctx < target:
            return "align_hermes"  # below purpose target but >= floor
        if loaded_ctx > target and purpose == "light":
            return "leave_alone"  # never force-down without policy+apply
    return "ok"


def status(purpose: Optional[str] = None) -> OrnithStatus:
    """Live LMS read via API and/or `lms ps`. Never mutates load state."""
    api = _from_api()
    ps = _parse_lms_ps()
    sources: list[str] = []

    loaded = False
    loaded_ctx: Optional[int] = None
    max_ctx: Optional[int] = None
    parallel: Optional[int] = None
    ttl: Optional[str] = None
    detail_parts: list[str] = []

    if api is not None:
        sources.append("api")
        loaded = bool(api.get("loaded"))
        if api.get("loaded_ctx") is not None:
            try:
                loaded_ctx = int(api["loaded_ctx"])
            except (TypeError, ValueError):
                loaded_ctx = None
        if api.get("max_ctx") is not None:
            try:
                max_ctx = int(api["max_ctx"])
            except (TypeError, ValueError):
                max_ctx = None
        detail_parts.append(f"api_state={api.get('state')}")
    if ps is not None:
        sources.append("lms_ps")
        if ps.get("loaded"):
            loaded = True
        if ps.get("loaded_ctx") is not None:
            try:
                # prefer ps ctx if api missing; else keep api if both agree-ish
                ps_ctx = int(ps["loaded_ctx"])
                if loaded_ctx is None:
                    loaded_ctx = ps_ctx
                elif ps_ctx != loaded_ctx:
                    detail_parts.append(f"ps_ctx={ps_ctx}≠api_ctx={loaded_ctx}")
                    # prefer higher of the two when both present (safer vs floor)
                    loaded_ctx = max(loaded_ctx, ps_ctx)
            except (TypeError, ValueError):
                pass
        if ps.get("parallel") is not None:
            try:
                parallel = int(ps["parallel"])
            except (TypeError, ValueError):
                parallel = None
        ttl = ps.get("ttl") or ttl

    if not sources:
        return OrnithStatus(
            model_id=MODEL_ID,
            loaded=False,
            loaded_ctx=None,
            max_ctx=None,
            parallel=None,
            ttl=None,
            ok_vs_floor=False,
            recommendation="leave_alone",
            source="unreachable",
            detail="LMS API and lms ps both unavailable",
        )

    ok_vs_floor = bool(loaded and loaded_ctx is not None and loaded_ctx >= FLOOR_CTX)
    if not loaded:
        ok_vs_floor = True  # N/A — not loaded is not a floor breach
    rec = _recommend(loaded, loaded_ctx, purpose)
    if parallel is not None and parallel != PARALLEL_DEFAULT:
        detail_parts.append(f"parallel={parallel}≠default={PARALLEL_DEFAULT} (info only)")

    return OrnithStatus(
        model_id=MODEL_ID,
        loaded=loaded,
        loaded_ctx=loaded_ctx,
        max_ctx=max_ctx,
        parallel=parallel,
        ttl=ttl or "",
        ok_vs_floor=ok_vs_floor,
        recommendation=rec,
        source="+".join(sources),
        detail="; ".join(detail_parts),
    )


def choose_ctx(purpose: str, max_ctx: Optional[int] = None) -> int:
    """Purpose heuristic — NEVER force one number globally.

    light        → FLOOR_CTX (65536)
    desk/paperclip → DEFAULT_CTX (131072)
    heavy        → min(262144, max_ctx or 262144)
    """
    p = (purpose or "").strip().lower()
    if p in ("light", "min", "floor"):
        return FLOOR_CTX
    if p in ("desk", "paperclip", "default", "hermes"):
        return DEFAULT_CTX
    if p in ("heavy", "max", "long"):
        cap = max_ctx if (max_ctx and max_ctx > 0) else 262144
        return min(262144, cap)
    raise ValueError(f"unknown purpose={purpose!r}; use desk|light|heavy")


def build_load_cmd(
    ctx: int,
    parallel: int = PARALLEL_DEFAULT,
    gpu: str = "max",
) -> str:
    """Exact lms load command string (print-only unless apply env)."""
    return (
        f"{LMS_BIN or 'lms'} load {MODEL_ID} -c {ctx} --parallel {parallel} "
        f"--gpu {gpu} --yes"
    )


def maybe_apply(cmd: str, purpose: str) -> int:
    """Run load only when ORNITH_LOAD_APPLY=1 and purpose set. Else print-only."""
    apply_env = os.environ.get("ORNITH_LOAD_APPLY", "").strip() == "1"
    if not apply_env:
        print("APPLY: print-only (set ORNITH_LOAD_APPLY=1 to execute)")
        print(f"CMD: {cmd}")
        return 0
    if not purpose:
        print("APPLY blocked: purpose required when ORNITH_LOAD_APPLY=1", file=sys.stderr)
        print(f"CMD (not run): {cmd}")
        return 2
    print(f"APPLY: executing (ORNITH_LOAD_APPLY=1 purpose={purpose})")
    print(f"CMD: {cmd}")
    if any(ch in cmd for ch in ";|&<>$`\n"):
        print("APPLY blocked: shell metacharacters in command", file=sys.stderr)
        return 2
    try:
        subprocess.check_call(shlex.split(cmd))
    except subprocess.CalledProcessError as e:
        print(f"APPLY failed: exit={e.returncode}", file=sys.stderr)
        return e.returncode or 1
    return 0


def _print_status(st: OrnithStatus) -> None:
    print(json.dumps(asdict(st), indent=2))
    print(
        f"\nsummary: loaded={st.loaded} ctx={st.loaded_ctx} parallel={st.parallel} "
        f"ok_vs_floor={st.ok_vs_floor} rec={st.recommendation} src={st.source}"
    )


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Ornith fluid load policy (no auto-unload)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_status = sub.add_parser("status", help="Live LMS status + recommendation")
    p_status.add_argument(
        "--purpose",
        choices=("desk", "light", "heavy", "paperclip"),
        default=None,
        help="Optional purpose to refine recommendation",
    )

    p_rec = sub.add_parser("recommend", help="Choose ctx for purpose + optional apply print")
    p_rec.add_argument(
        "--purpose",
        choices=("desk", "light", "heavy", "paperclip"),
        required=True,
    )
    p_rec.add_argument(
        "--apply",
        action="store_true",
        help="Print lms load cmd; execute only if ORNITH_LOAD_APPLY=1",
    )
    p_rec.add_argument("--parallel", type=int, default=PARALLEL_DEFAULT)
    p_rec.add_argument("--gpu", default="max")

    args = ap.parse_args(argv)

    if args.cmd == "status":
        st = status(purpose=getattr(args, "purpose", None))
        _print_status(st)
        return 0

    if args.cmd == "recommend":
        purpose = "desk" if args.purpose == "paperclip" else args.purpose
        st = status(purpose=purpose)
        target = choose_ctx(purpose, max_ctx=st.max_ctx)
        print(
            json.dumps(
                {
                    "purpose": purpose,
                    "target_ctx": target,
                    "floor_ctx": FLOOR_CTX,
                    "default_ctx": DEFAULT_CTX,
                    "parallel_default": PARALLEL_DEFAULT,
                    "live": asdict(st),
                },
                indent=2,
            )
        )
        cmd = build_load_cmd(target, parallel=args.parallel, gpu=args.gpu)
        if args.apply:
            return maybe_apply(cmd, purpose=purpose)
        print(f"\n(suggested, not applied) CMD: {cmd}")
        print("Hint: pass --apply to print apply path; ORNITH_LOAD_APPLY=1 to execute.")
        return 0

    ap.error(f"unknown cmd {args.cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
