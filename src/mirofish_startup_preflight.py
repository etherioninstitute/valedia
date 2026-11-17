"""The observer side of the startup preflight (TMF5 addendum 2).

The launcher's `--preflight` runs this file under the observer's sandbox profile, from the staged observer entry, beside the
staged run script. It loads, unloads and calls no model and needs no gate. Using the run's own code (imported, never copied)
it checks, in the order the run does before it loads a model:
  confinement  the observer cannot read the original source (run.require_confined);
  pins         every file the candidate manifest pins has its pinned hash (run.verify_candidate);
  plan         a plan candidate's manifest binds the staged selector's plan (run.require_plan_binding);
  start        the host's signed resource capsule verifies and its latest sample passes the run's START rule: the free
               reading against the staged threshold, pressure level 1, fresh and valid (run.resource_sample(pre=True)).
Not called, because they act: the gate check (it writes the one-attempt claim), the host children's bind, the SDK helper.
It prints ONE JSON line and exits 0 only when every check holds, else 3. It never prints a command line or a key.
"""
import json
import os
import sys
from pathlib import Path

FIELDS = ("free_percent", "pressure_level", "swap_used_bytes", "co_resident_rows")


def main(work=None, confined=None):
    work = Path(work) if work is not None else Path(__file__).absolute().parents[2]
    result = {"ok": False, "reason": "", "free_percent": None, "start_threshold": None, "pressure_level": None,
              "swap_used_bytes": None, "co_resident_rows": None, "checks": []}
    try:
        import mirofish_native_run as run
        from mirofish_native_model import MODEL_PLAN
        result["start_threshold"] = run.START_FREE_PERCENT
        (confined or run.require_confined)()
        result["checks"].append("confinement: the observer cannot read the original source")
        candidate, _ = run.verify_candidate(work)
        result["checks"].append("pins: %d pinned files match" % (len(candidate["files"]) + len(candidate["model_files"])))
        run.require_plan_binding(candidate, MODEL_PLAN)
        result["checks"].append("plan: " + ("the manifest binds the selector's plan" if MODEL_PLAN is not None else "one model, nothing to bind"))
        sample = None
        try:
            sample = run.resource_sample(True)
        except Exception as exc:
            sample = getattr(exc, "resource_sample", None)
            raise
        finally:
            if type(sample) is dict:
                result.update({k: sample.get(k) for k in FIELDS})
        result["checks"].append("start: the latest sample passes the run's start rule")
        result["ok"] = True
    except Exception as exc:
        reason = type(exc).__name__ + ": " + str(exc)
        key = os.environ.get("MIROFISH_RESOURCE_KEY")
        if key:
            reason = reason.replace(key, "[REDACTED]")
        result["reason"] = reason[:500]
    sys.stdout.write(json.dumps(result) + "\n"); sys.stdout.flush()
    return 0 if result["ok"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
