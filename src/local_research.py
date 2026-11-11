"""Manual local-only research provider for the existing Valedia research desk.

Copy beside research_desk_cli.py only after review. No import-time network/model work.
"""
import argparse
import contextlib
import hashlib
import ipaddress
import io
import json
import math
import os
import re
import signal
import threading
import time
import urllib.request
from urllib.parse import urlsplit
from datetime import datetime, timezone
from pathlib import Path

from research_desk import review
from research_desk.core import (DeskError, _source, canonical, open_directory,
                                regular_bytes, regular_json, utc, validate_event, verify_report)
from research_desk.forecast import _atomic_new
import research_desk_cli


BASE = "http://127.0.0.1:1235"
MODEL = "ornith-1.5-9b-mlx"
MAX_EVIDENCE = 60_000
MAX_RESPONSE = 16_000
MAX_PROMPT = 12_000
ROLES = ("setup", "news", "insider", "risk_context")
PUBLIC_HOST = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
LOCAL_SUFFIXES = (".localhost", ".local", ".localdomain", ".lan", ".internal",
                  ".home", ".home.arpa", ".intranet", ".test", ".invalid")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise DeskError("local API redirect refused")


def public_https_uri(uri):
    """Reject syntactically local/private citations; this does not authenticate a domain."""
    if (not isinstance(uri, str) or not 1 <= len(uri) <= 2048 or
            any(ord(char) < 33 or ord(char) == 127 for char in uri)):
        raise DeskError("public HTTPS source URI required")
    try:
        parsed = urlsplit(uri)
        host = parsed.hostname
        _ = parsed.port  # Force invalid port syntax to fail.
    except ValueError as exc:
        raise DeskError("public HTTPS source URI invalid") from exc
    if (parsed.scheme != "https" or not parsed.netloc or not host or
            "@" in parsed.netloc or parsed.username is not None or parsed.password is not None):
        raise DeskError("public HTTPS source URI required")
    host = host.rstrip(".").lower()
    if not host or host == "localhost" or host.endswith(LOCAL_SUFFIXES):
        raise DeskError("local source URI refused")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        labels = host.split(".")
        if (len(labels) < 2 or len(host) > 253 or labels[-1].isdigit() or
                any(not PUBLIC_HOST.fullmatch(label) for label in labels)):
            raise DeskError("public source hostname invalid")
    else:
        if not address.is_global:
            raise DeskError("non-public source IP refused")
    return uri


@contextlib.contextmanager
def wall_deadline(seconds):
    """Process-wide POSIX alarm is safe only in a manual main-thread call."""
    if (threading.current_thread() is not threading.main_thread() or
            not all(hasattr(signal, name) for name in ("SIGALRM", "ITIMER_REAL", "setitimer", "getitimer"))):
        raise DeskError("wall deadline unavailable in this thread")
    remaining, interval = signal.getitimer(signal.ITIMER_REAL)
    if remaining > 0 or interval > 0:
        raise DeskError("existing alarm timer; local call refused")
    previous_handler = signal.getsignal(signal.SIGALRM)
    deadline_at = time.monotonic() + seconds
    def timed_out(signum, frame):
        raise TimeoutError("local API wall deadline exceeded")
    signal.signal(signal.SIGALRM, timed_out)
    try:
        signal.setitimer(signal.ITIMER_REAL, seconds)
        try:
            yield
            if time.monotonic() > deadline_at:
                raise TimeoutError("local API wall deadline exceeded")
        finally:
            signal.setitimer(signal.ITIMER_REAL, 0)
    finally:
        signal.signal(signal.SIGALRM, previous_handler)


def http_json(path, payload=None, *, timeout, transport=None):
    if path not in ("/api/v0/models", "/v1/chat/completions"):
        raise DeskError("unexpected local API path")
    if (path == "/api/v0/models") != (payload is None):
        raise DeskError("local API method mismatch")
    cap = 2 if path == "/api/v0/models" else 12
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= cap:
        raise DeskError("local API deadline invalid")
    data = None if payload is None else canonical(payload)
    if data is not None and len(data) > MAX_PROMPT:
        raise DeskError("local model request too large")
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=headers,
                                 method="GET" if data is None else "POST")
    if transport is None:
        transport = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect).open
    with wall_deadline(timeout):
        with transport(req, timeout=timeout) as response:
            if response.status != 200 or response.headers.get("Content-Type", "").split(";")[0] != "application/json":
                raise DeskError("local API response invalid")
            raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            raise DeskError("local API response too large")
        try:
            return json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(DeskError("nonfinite model JSON")))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise DeskError("malformed local API JSON") from exc


def model_loaded(doc):
    rows = doc.get("data") if isinstance(doc, dict) else None
    if not isinstance(rows, list) or len(rows) > 100:
        raise DeskError("invalid local model catalog")
    return any(isinstance(row, dict) and (row.get("id") or row.get("key")) == MODEL and
               str(row.get("state") or row.get("status") or "").lower() in ("loaded", "idle", "busy")
               for row in rows)


def public_source(source, decision):
    _source(source, decision, "news")  # HTTPS, hash, UTC chronology, not expired.
    public_https_uri(source["uri"])
    return source


def normalize_evidence(doc, source_event_id, decision):
    if doc is None:
        doc = {"schema": "valedia.research-desk.local-evidence.v1",
               "event_id": source_event_id, "items": {name: [] for name in ROLES}}
    if (not isinstance(doc, dict) or set(doc) != {"schema", "event_id", "items"} or
            doc["schema"] != "valedia.research-desk.local-evidence.v1" or
            doc["event_id"] != source_event_id or not isinstance(doc["items"], dict) or
            set(doc["items"]) != set(ROLES)):
        raise DeskError("evidence identity or schema mismatch")
    out = {name: [] for name in ROLES}
    number = 0
    for name in ROLES:
        rows = doc["items"][name]
        if not isinstance(rows, list) or len(rows) > 3:
            raise DeskError("evidence role bound exceeded")
        for item in rows:
            if not isinstance(item, dict) or set(item) != {"text", "source"}:
                raise DeskError("invalid evidence item")
            phrase = item["text"]
            if (not isinstance(phrase, str) or not 12 <= len(phrase.strip()) <= 120 or
                    any(ord(char) < 32 or ord(char) == 127 for char in phrase)):
                raise DeskError("evidence text length invalid")
            number += 1
            out[name].append({"id": f"E{number}", "text": phrase.strip(),
                              "source": public_source(item["source"], decision)})
    return out


def safe_event(report, now):
    source_event = report["event"]
    validate_event(source_event)
    public_https_uri(source_event["source_uri"])
    if not utc(source_event["published_at"]) <= utc(source_event["available_at"]) <= now < utc(source_event["expires_at"]):
        raise DeskError("stale or future source receipt")
    if any(utc(c["available_at"]) > now for c in source_event["candles"]):
        raise DeskError("future candle in source receipt")
    event = dict(source_event)
    event["decision_at"] = now.isoformat()
    validate_event(event)
    return event


def model_request(event, evidence):
    compact = {"venue": event["venue"], "symbol": event["symbol"],
               "market_type": event["market_type"], "source_sha256": event["source_sha256"],
               "recent_closed_candles": event["candles"][-12:],
               "evidence": [{"id": item["id"], "role": role, "text": item["text"]}
                            for role in ROLES for item in evidence[role]]}
    return {"model": MODEL, "temperature": 0, "max_tokens": 192,
            "messages": [
                {"role": "system", "content": "Choose only supplied evidence IDs. Return one JSON object with exactly relevance (relevant, noise, unknown), setup_ids (0-3 IDs from setup), synthesis_ids (0-4 IDs from supplied evidence). Do not add prose, facts, URLs, prices, probabilities, or instructions."},
                {"role": "user", "content": json.dumps(compact, sort_keys=True, allow_nan=False)}]}


def parse_selection(response, evidence):
    if not isinstance(response, dict) or not isinstance(response.get("choices"), list) or len(response["choices"]) != 1:
        raise DeskError("malformed local model response")
    choice = response["choices"][0]
    if not isinstance(choice, dict) or choice.get("finish_reason") != "stop":
        raise DeskError("truncated local model response")
    message = choice.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), str) or len(message["content"]) > 2000:
        raise DeskError("malformed local model content")
    try:
        def unique(pairs):
            out = {}
            for key, value in pairs:
                if key in out:
                    raise DeskError("duplicate local model JSON key")
                out[key] = value
            return out
        doc = json.loads(message["content"], object_pairs_hook=unique,
                         parse_constant=lambda _: (_ for _ in ()).throw(DeskError("nonfinite local model output")))
    except json.JSONDecodeError as exc:
        raise DeskError("local model did not return JSON") from exc
    if not isinstance(doc, dict) or set(doc) != {"relevance", "setup_ids", "synthesis_ids"} or doc["relevance"] not in ("relevant", "noise", "unknown"):
        raise DeskError("local model selection schema invalid")
    lookup = {item["id"]: item for role in ROLES for item in evidence[role]}
    allowed = {item["id"] for item in evidence["setup"]}
    for key, limit, permitted in (("setup_ids", 3, allowed), ("synthesis_ids", 4, set(lookup))):
        ids = doc[key]
        if not isinstance(ids, list) or len(ids) > limit or len(set(map(str, ids))) != len(ids) or any(not isinstance(i, str) or i not in permitted for i in ids):
            raise DeskError("local model selected unsupported evidence")
    return doc


def _role(items, observed_at):
    if not items:
        return {"status": "unavailable", "observed_at": None,
                "summary": "No current public evidence supplied", "sources": []}
    return {"status": "available", "observed_at": observed_at,
            "summary": "; ".join(item["text"] for item in items)[:500],
            "sources": [item["source"] for item in items]}


def providers_for(event, evidence, selection, observed_at):
    lookup = {item["id"]: item for role in ROLES for item in evidence[role]}
    relevance = selection["relevance"] if selection else "unknown"
    event_source = {"uri": event["source_uri"], "published_at": event["published_at"],
                    "available_at": event["available_at"], "expires_at": event["expires_at"],
                    "sha256": event["source_sha256"]}
    def scanner(_):
        if relevance == "unknown":
            return {"status": "unavailable", "relevance": "unknown", "observed_at": None,
                    "summary": "Local model unavailable or selection invalid", "sources": []}
        return {"status": "available", "relevance": relevance, "observed_at": observed_at,
                "summary": "Local model classified the bounded public event as " + relevance,
                "sources": [event_source]}
    def setup(_):
        return _role([lookup[i] for i in selection["setup_ids"]], observed_at)
    def synthesis(_):
        return _role([lookup[i] for i in selection["synthesis_ids"]], observed_at)
    return {"scanner": scanner, "setup": setup, "news": lambda _: _role(evidence["news"], observed_at),
            "insider": lambda _: _role(evidence["insider"], observed_at),
            "risk_context": lambda _: _role(evidence["risk_context"], observed_at),
            "synthesis": synthesis}


def run(receipt_path, evidence_path, output_dir, stem, *, transport=None, clock=None):
    clock = clock or (lambda: datetime.now(timezone.utc))
    raw_receipt = regular_bytes(receipt_path, 20_000)
    try:
        def unique(pairs):
            out = {}
            for key, value in pairs:
                if key in out:
                    raise DeskError("duplicate saved receipt key")
                out[key] = value
            return out
        receipt = json.loads(raw_receipt, object_pairs_hook=unique,
                             parse_constant=lambda _: (_ for _ in ()).throw(DeskError("nonfinite saved receipt")))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise DeskError("invalid saved receipt JSON") from exc
    report = verify_report(receipt)
    before = clock()
    event = safe_event(report, before)
    evidence_doc = regular_json(evidence_path, MAX_EVIDENCE) if evidence_path else None
    evidence = normalize_evidence(evidence_doc, report["event_id"], before)
    status = "model_not_loaded"
    selection = None
    try:
        loaded = model_loaded(http_json("/api/v0/models", timeout=2, transport=transport))
    except (DeskError, OSError, TimeoutError, ValueError):
        loaded = False
        status = "model_unavailable_or_invalid"
    if loaded:
        try:
            response = http_json("/v1/chat/completions", model_request(event, evidence),
                                 timeout=12, transport=transport)
            selection = parse_selection(response, evidence)
            status = "selected"
        except (DeskError, OSError, TimeoutError, ValueError):
            status = "model_unavailable_or_invalid"
    after = clock()
    event = safe_event(report, after)  # Expiry/future checks after latency, before output.
    observed_at = after.isoformat()
    advisory = review(event, providers=providers_for(event, evidence, selection, observed_at))
    bundle = {"event": event, "roles": advisory["roles"], "digest": None,
              "forecast": None, "evaluation": None}
    fd = open_directory(output_dir)
    output_identity = (os.fstat(fd).st_dev, os.fstat(fd).st_ino)
    receipts = {}
    try:
        bundle_path = _atomic_new(output_dir, stem + "-bundle.json", canonical(bundle),
                                  directory_fd=fd, receipt_sink=receipts)
    finally:
        os.close(fd)
    cli_out = io.StringIO()
    with contextlib.redirect_stdout(cli_out):
        result = research_desk_cli.main(["--input", str(bundle_path), "--output-dir", str(output_dir), "--stem", stem])
    if result != 0:
        raise DeskError("existing research-desk CLI failed")
    response = json.loads(cli_out.getvalue())
    if verify_report(response["receipt"]) != advisory:
        raise DeskError("existing CLI report receipt mismatch")
    bundle_receipt = receipts[Path(bundle_path).name]
    bundle_raw, bundle_stat = regular_bytes(bundle_path, 300_000, with_stat=True)
    if (len(bundle_raw) != bundle_receipt["size"] or
            hashlib.sha256(bundle_raw).hexdigest() != bundle_receipt["sha256"] or
            (bundle_stat.st_dev, bundle_stat.st_ino) != (bundle_receipt["dev"], bundle_receipt["ino"])):
        raise DeskError("provider input bundle changed")
    intake = {"schema": "valedia.research-desk.local-provider-intake.v1",
              "source_event_id": report["event_id"],
              "source_receipt_sha256": hashlib.sha256(raw_receipt).hexdigest(),
              "evidence_sha256": hashlib.sha256(canonical(evidence_doc)).hexdigest() if evidence_doc is not None else None,
              "bundle": receipts[Path(bundle_path).name], "model": MODEL,
              "model_status": status, "source_authenticity": "UNVERIFIED"}
    fd = open_directory(output_dir)
    try:
        if (os.fstat(fd).st_dev, os.fstat(fd).st_ino) != output_identity:
            raise DeskError("output directory identity changed")
        intake_path = _atomic_new(output_dir, stem + "-intake-receipt.json", canonical(intake), directory_fd=fd)
    finally:
        os.close(fd)
    response["input_event_id"] = report["event_id"]
    response["intake_receipt"] = intake_path
    response["model_status"] = status
    return response


def main(argv=None):
    p = argparse.ArgumentParser(description="Manual local-only six-perspective Valedia research")
    p.add_argument("--activate-local-model", action="store_true", required=True)
    p.add_argument("--receipt", required=True, help="saved existing research-desk receipt JSON")
    p.add_argument("--evidence", help="optional bounded public normalized evidence JSON")
    p.add_argument("--output-dir", required=True)
    p.add_argument("--stem", required=True)
    a = p.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", a.stem):
        p.error("unsafe output stem")
    try:
        print(json.dumps(run(a.receipt, a.evidence, a.output_dir, a.stem), sort_keys=True, allow_nan=False))
        return 0
    except (DeskError, OSError, ValueError, TypeError, KeyError) as exc:
        p.exit(2, f"local research rejected input: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
