"""MiroFish live intel: dated archive, then atomic replace. FAIL keeps last-good.

Allocators call load_for_fv. A dated archive is not create fuel.
Near-neutral overall_bias is not create fuel. No venue IO.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
DEFAULT_LIVE = os.path.expanduser("~/.openclaw/workspace/models/mirofish_signal.json")
DEFAULT_ARCHIVE = os.path.join(_ROOT, "data", "mirofish")
MARKER_NAME = "mirofish_freshness.json"
# ponytail: same 24h window intel_refresh already uses for a fresh swarm.
MAX_AGE_S = 24 * 3600
# ponytail: |bias-50| <= 5 is hold, not a fair value. Widen if a scored edge needs it.
NEUTRAL_BAND = 5.0
# ponytail: filename scan, midpoint of the 30–90 night window.
RETAIN_NIGHTS = 60
_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})(?:\.|$)")


def _utc(now):
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _parse(ts):
    if not isinstance(ts, str) or not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _read(path):
    try:
        with open(path) as f:
            doc = json.load(f)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _atomic(path, doc):
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".mf-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(doc, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def marker_path(live_path):
    return os.path.join(os.path.dirname(os.path.abspath(live_path)), MARKER_NAME)


def resolve_archive_dir(live_path, archive_dir=None):
    if archive_dir:
        return archive_dir
    if os.path.abspath(live_path) == os.path.abspath(DEFAULT_LIVE):
        return DEFAULT_ARCHIVE
    return os.path.join(os.path.dirname(os.path.abspath(live_path)), "mirofish-archive")


def _is_archive_path(path):
    ap = os.path.abspath(path)
    parent = os.path.basename(os.path.dirname(ap))
    base = os.path.basename(ap)
    if parent == "mirofish-archive":
        return True
    if parent == "mirofish" and base != "mirofish_signal.json":
        return True
    return bool(_DATE.match(base))


def _safe_id(run_id):
    return re.sub(r"[^A-Za-z0-9._-]+", "", str(run_id or ""))[:64]


def evaluate(doc, marker=None, now=None):
    """as_of / stale / age_s / usable_for_fv. Sidecar stale wins. Archive is never fuel."""
    now = _utc(now)
    doc = doc if isinstance(doc, dict) else {}
    marker = marker if isinstance(marker, dict) else {}
    as_of = _parse(doc.get("as_of") or doc.get("ts"))
    age = None if as_of is None else max(0, int((now - as_of).total_seconds()))
    try:
        bias = float(doc.get("overall_bias"))
    except (TypeError, ValueError):
        bias = 50.0
    near = abs(bias - 50.0) <= NEUTRAL_BAND
    archived = doc.get("role") == "archive" or doc.get("archive") is True
    stale = bool(marker.get("stale")) or bool(doc.get("stale")) or as_of is None or age >= MAX_AGE_S
    usable = (
        not stale
        and not archived
        and not near
        and doc.get("source") == "mirofish_swarm"
    )
    return {
        "as_of": None if as_of is None else as_of.isoformat(),
        "stale": stale,
        "age_s": age,
        "usable_for_fv": usable,
    }


def freshness_of(live_path=None, now=None):
    path = live_path or DEFAULT_LIVE
    doc = _read(path) or {}
    marker = _read(marker_path(path)) or {}
    if _is_archive_path(path):
        view = evaluate(doc, marker, now)
        view["stale"] = True
        view["usable_for_fv"] = False
        return view
    if not doc and not marker:
        return {"as_of": None, "stale": True, "age_s": None, "usable_for_fv": False}
    return evaluate(doc, marker, now)


def load_for_fv(live_path, now=None):
    """Live body only when fresh, not stale, and not near-neutral. Else None."""
    if not live_path or _is_archive_path(live_path):
        return None
    doc = _read(live_path)
    if not doc:
        return None
    if not evaluate(doc, _read(marker_path(live_path)), now)["usable_for_fv"]:
        return None
    return doc


def prune(archive_dir, now=None, keep_nights=RETAIN_NIGHTS):
    if not archive_dir or not os.path.isdir(archive_dir):
        return 0
    cutoff = _utc(now).date() - timedelta(days=int(keep_nights))
    removed = 0
    for name in os.listdir(archive_dir):
        m = _DATE.match(name)
        if not m or not name.endswith(".json"):
            continue
        try:
            day = datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except ValueError:
            continue
        if day < cutoff:
            os.remove(os.path.join(archive_dir, name))
            removed += 1
    return removed


def publish_pass(doc, live_path=None, archive_dir=None, run_id=None, now=None):
    """Write the dated archive (and run-id sibling), then atomic-replace live."""
    now = _utc(now)
    live_path = live_path or DEFAULT_LIVE
    archive_dir = resolve_archive_dir(live_path, archive_dir)
    body = dict(doc or {})
    body.pop("role", None)
    body.pop("archive", None)
    body["as_of"] = now.isoformat()
    body["stale"] = False
    view = evaluate(body, {"stale": False}, now)
    body["age_s"] = 0
    body["usable_for_fv"] = view["usable_for_fv"]
    archived = dict(body)
    archived["role"] = "archive"
    archived["archive"] = True
    archived["usable_for_fv"] = False
    day = now.strftime("%Y-%m-%d")
    _atomic(os.path.join(archive_dir, day + ".json"), archived)
    safe = _safe_id(run_id)
    if safe:
        _atomic(os.path.join(archive_dir, "%s.%s.json" % (day, safe)), archived)
    _atomic(live_path, body)
    _atomic(marker_path(live_path), {
        "as_of": body["as_of"],
        "stale": False,
        "age_s": 0,
        "usable_for_fv": body["usable_for_fv"],
        "reason": "pass",
    })
    prune(archive_dir, now)
    return body


def publish_fail(live_path=None, reason="fail", now=None):
    """Stale/age marker only. Does not open the live signal for write."""
    now = _utc(now)
    live_path = live_path or DEFAULT_LIVE
    doc = _read(live_path) or {}
    as_of = _parse(doc.get("as_of") or doc.get("ts"))
    age = None if as_of is None else max(0, int((now - as_of).total_seconds()))
    _atomic(marker_path(live_path), {
        "as_of": None if as_of is None else as_of.isoformat(),
        "stale": True,
        "age_s": age,
        "usable_for_fv": False,
        "reason": str(reason or "fail")[:80],
    })
    return {"stale": True, "usable_for_fv": False, "age_s": age}
