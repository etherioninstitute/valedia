"""MiroFish live intel: dated archive, then atomic replace. FAIL keeps last-good.

Allocators call load_for_fv. A dated archive is not create fuel.
Near-neutral overall_bias is not create fuel. No venue IO.
"""
from __future__ import annotations

import json
import math
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
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def _reject_constant(value):
    raise ValueError("nonfinite JSON constant: " + value)


def _read(path):
    try:
        with open(path) as f:
            doc = json.load(f, parse_constant=_reject_constant)
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _read_marker(path):
    """None is an absent optional marker; malformed/present is never absent."""
    try:
        with open(path) as f:
            marker = json.load(f, parse_constant=_reject_constant)
    except FileNotFoundError:
        # A dangling sidecar symlink is present but unreadable, not absent.
        return False if os.path.lexists(path) else None
    except (OSError, ValueError):
        return False
    # JSON null also means malformed here, not an absent sidecar.
    return marker if isinstance(marker, dict) else False


def _bounded_number(value, lower, upper):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value) and lower <= value <= upper
    except (ValueError, OverflowError):
        return False


def _input_issues(doc, now):
    """Validate supplied fields without inventing report/event provenance."""
    if not isinstance(doc, dict):
        return ["document_not_object"]
    issues = []
    if doc.get("source") != "mirofish_swarm":
        issues.append("source_not_swarm")
    if not _bounded_number(doc.get("overall_bias"), 0, 100):
        issues.append("invalid_overall_bias")
    if "status" in doc and doc["status"] != "active":
        issues.append("inactive_status")
    if "stale" in doc and type(doc["stale"]) is not bool:
        issues.append("invalid_stale")
    elif doc.get("stale") is True:
        issues.append("stale_document")
    if "archive" in doc and type(doc["archive"]) is not bool:
        issues.append("invalid_archive")
    if doc.get("role") == "archive" or doc.get("archive") is True:
        issues.append("archived")
    for key in ("as_of", "ts"):
        if key in doc:
            dt = _parse(doc[key])
            if dt is None:
                issues.append("invalid_" + key)
            elif (now - dt).total_seconds() < 0:
                issues.append("future_" + key)
    if "signals" in doc:
        signals = doc["signals"]
        if not isinstance(signals, dict):
            issues.append("signals_not_object")
        else:
            for signal in signals.values():
                if not isinstance(signal, dict):
                    issues.append("signal_not_object")
                elif not _bounded_number(signal.get("score"), 0, 100):
                    issues.append("invalid_signal_score")
                elif "confidence" in signal and not _bounded_number(signal["confidence"], 0, 1):
                    issues.append("invalid_signal_confidence")
    return list(dict.fromkeys(issues))


def _atomic(path, doc):
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".mf-", suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(doc, f, indent=2, allow_nan=False)
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
    """Fresh, finite heuristic evidence only. An absent marker is compatible."""
    try:
        now = _utc(now)
    except (AttributeError, TypeError, ValueError, OverflowError):
        return {"as_of": None, "stale": True, "age_s": None,
                "usable_for_fv": False, "reasons": ["invalid_now"]}
    issues = _input_issues(doc, now)
    doc = doc if isinstance(doc, dict) else {}
    marker_issues = []
    if marker is not None and not isinstance(marker, dict):
        marker_issues.append("malformed_marker")
    else:
        marker = marker or {}
        if "stale" in marker and type(marker["stale"]) is not bool:
            marker_issues.append("invalid_marker_stale")
        elif marker.get("stale") is True:
            marker_issues.append("stale_marker")
    as_of = _parse(doc.get("as_of") or doc.get("ts"))
    elapsed = None if as_of is None else (now - as_of).total_seconds()
    # Check signed duration before truncation: even a subsecond future is invalid.
    age = None if elapsed is None or elapsed < 0 else int(elapsed)
    time_stale = as_of is None or elapsed < 0 or elapsed >= MAX_AGE_S
    if as_of is None:
        issues.append("missing_timestamp")
    elif elapsed >= MAX_AGE_S:
        issues.append("expired")
    near = _bounded_number(doc.get("overall_bias"), 0, 100) and abs(doc["overall_bias"] - 50.0) <= NEUTRAL_BAND
    if near:
        issues.append("near_neutral")
    reasons = list(dict.fromkeys(issues + marker_issues))
    stale = time_stale or bool(marker_issues) or bool(doc.get("stale"))
    return {
        "as_of": None if as_of is None else as_of.isoformat(),
        "stale": stale,
        "age_s": age,
        "usable_for_fv": not reasons,
        "reasons": reasons,
    }


def freshness_of(live_path=None, now=None):
    path = live_path or DEFAULT_LIVE
    doc = _read(path) or {}
    marker = _read_marker(marker_path(path))
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
    if not evaluate(doc, _read_marker(marker_path(live_path)), now)["usable_for_fv"]:
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
    issues = _input_issues(doc, now)
    if issues:
        raise ValueError("invalid MiroFish publication: " + ", ".join(issues))
    # Preflight the entire body before creating even an archive/temp file.
    json.dumps(doc, allow_nan=False)
    body = dict(doc)
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
