#!/usr/bin/env python3
"""Ornith ensure-gate — thin read-only admission check for Paperclip (Option A).

Wraps ornith_load_policy. NEVER unloads or reloads from this gate.
Suggested `lms load ...` is printed in JSON only; apply stays on the policy
script (`recommend --apply` + ORNITH_LOAD_APPLY=1) — prefer print-only.

CLI:
  python ornith_ensure_gate.py status
  python ornith_ensure_gate.py preflight --purpose desk|light|heavy

Exit codes:
  0 — ok vs floor; if purpose given, also ok vs purpose target
  2 — drift (loaded_ctx < floor, or purpose target mismatch)
  1 — tool / probe error
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Optional

# Same-dir import (canonical + accel symlink resolve to this file's sibling).
from ornith_load_policy import (
    DEFAULT_CTX,
    FLOOR_CTX,
    PARALLEL_DEFAULT,
    build_load_cmd,
    choose_ctx,
    status as policy_status,
)


def _gate_payload(
    purpose: Optional[str],
) -> tuple[dict[str, Any], int]:
    """Build admission JSON and exit code. Never mutates LMS load state."""
    try:
        st = policy_status(purpose=purpose)
    except Exception as e:  # noqa: BLE001 — surface as tool error
        err = {
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "loaded": None,
            "loaded_ctx": None,
            "parallel": None,
            "purpose": purpose,
            "target_ctx": None,
            "recommendation": "error",
            "lms_load_cmd": None,
        }
        return err, 1

    target_ctx: Optional[int] = None
    if purpose:
        try:
            target_ctx = choose_ctx(purpose, max_ctx=st.max_ctx)
        except ValueError as e:
            err = {
                "ok": False,
                "error": str(e),
                "loaded": st.loaded,
                "loaded_ctx": st.loaded_ctx,
                "parallel": st.parallel,
                "purpose": purpose,
                "target_ctx": None,
                "recommendation": "error",
                "lms_load_cmd": None,
            }
            return err, 1

    # Floor check: only when loaded (not-loaded is N/A for floor, not a breach).
    below_floor = bool(
        st.loaded and st.loaded_ctx is not None and st.loaded_ctx < FLOOR_CTX
    )
    # Purpose target: loaded_ctx must be >= target (never force-down).
    purpose_mismatch = False
    if purpose and target_ctx is not None:
        if not st.loaded or st.loaded_ctx is None or st.loaded_ctx < target_ctx:
            purpose_mismatch = True

    if purpose:
        ok = bool(st.loaded and st.loaded_ctx is not None and st.loaded_ctx >= target_ctx)
    else:
        # Floor-only: ok when not loaded (N/A) or loaded_ctx >= floor.
        ok = not below_floor

    # Suggested load cmd when not ok (print-only; gate never executes).
    lms_load_cmd: Optional[str] = None
    if not ok:
        if purpose and target_ctx is not None:
            want = target_ctx
        elif below_floor:
            want = FLOOR_CTX
        else:
            # not loaded / no purpose → desk default for ops visibility
            want = DEFAULT_CTX
        lms_load_cmd = build_load_cmd(want, parallel=PARALLEL_DEFAULT)

    payload: dict[str, Any] = {
        "ok": ok,
        "loaded": st.loaded,
        "loaded_ctx": st.loaded_ctx,
        "parallel": st.parallel,
        "purpose": purpose,
        "target_ctx": target_ctx,
        "recommendation": st.recommendation,
        "lms_load_cmd": lms_load_cmd,
        # extras for operators (stable; Paperclip may ignore)
        "floor_ctx": FLOOR_CTX,
        "ok_vs_floor": st.ok_vs_floor,
        "model_id": st.model_id,
        "source": st.source,
        "detail": st.detail,
        "drift": (not ok),
        "below_floor": below_floor,
        "purpose_mismatch": purpose_mismatch,
    }
    return payload, (0 if ok else 2)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Ornith ensure-gate (read-only Paperclip admission; no auto-unload)"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_status = sub.add_parser(
        "status",
        help="Floor-only admission check (purpose omitted)",
    )
    p_status.add_argument(
        "--purpose",
        choices=("desk", "light", "heavy", "paperclip"),
        default=None,
        help="Optional; if set, also require purpose target",
    )

    p_pre = sub.add_parser(
        "preflight",
        help="Admission check vs floor + purpose target",
    )
    p_pre.add_argument(
        "--purpose",
        choices=("desk", "light", "heavy", "paperclip"),
        required=True,
    )

    args = ap.parse_args(argv)
    purpose = getattr(args, "purpose", None)
    if purpose == "paperclip":
        purpose = "desk"

    # Fence: gate never applies loads. Print-only suggested cmd in JSON.
    # (ORNITH_LOAD_APPLY=1 is honored only by ornith_load_policy recommend --apply.)
    try:
        payload, code = _gate_payload(purpose)
    except Exception as e:  # noqa: BLE001
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": f"{type(e).__name__}: {e}",
                    "loaded": None,
                    "loaded_ctx": None,
                    "parallel": None,
                    "purpose": purpose,
                    "target_ctx": None,
                    "recommendation": "error",
                    "lms_load_cmd": None,
                },
                indent=2,
            ),
            file=sys.stderr,
        )
        return 1

    print(json.dumps(payload, indent=2))
    return code


if __name__ == "__main__":
    sys.exit(main())
