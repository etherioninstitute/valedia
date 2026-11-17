"""Bounded local transport for a reviewed native MiroFish qualification.

Importing this module starts nothing. Model and template admission are supplied by
an independently checked owner; the gateway never discovers or loads a model.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import os
from pathlib import Path
import select
import socket
import threading
import time
import uuid


class NativeRefused(RuntimeError):
    pass


class UpstreamIOFailure(NativeRefused):
    def __init__(self, reason, *, partial_response=None, status=None):
        super().__init__(reason)
        self.partial_response = partial_response
        self.status = status


def decode(raw):
    def pairs(rows):
        value = {}
        for key, item in rows:
            if key in value:
                raise NativeRefused("duplicate JSON field")
            value[key] = item
        return value
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda x: (_ for _ in ()).throw(NativeRefused("nonfinite JSON")))
    except (ValueError, UnicodeError) as exc:
        raise NativeRefused("invalid JSON") from exc


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode()


def atomic(path, value):
    path = Path(path)
    if path.is_symlink() or any(x.is_symlink() for x in path.parents):
        raise NativeRefused("symlink evidence path")
    temp = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex)
    with temp.open("xb") as stream:
        stream.write(encoded(value)); stream.flush(); os.fsync(stream.fileno())
    os.replace(temp, path)


@dataclass(frozen=True)
class Limits:
    chat_model: str
    embedding_model: str = "ym-research-embedding"
    context: int = 32768
    embedding_context: int = 2048
    chat_attempts: int = 40
    output_total: int = 60000
    output_per_call: int = 4096
    embedding_attempts: int = 64
    embedding_texts: int = 256
    embedding_batch: int = 16
    embedding_text_bytes: int = 1536
    embedding_total_bytes: int = 131072
    vector_dimension: int = 768
    wall_seconds: int = 2400
    cleanup_seconds: int = 120
    client_seconds: int = 175
    queue_seconds: int = 170
    request_bytes: int = 131072
    response_bytes: int = 2097152

    def __post_init__(self):
        if not self.chat_model or self.embedding_model != "ym-research-embedding":
            raise NativeRefused("fixed model identities required")
        maxima = {"context": 32768, "embedding_context": 2048, "chat_attempts": 40,
                  "output_total": 60000, "output_per_call": 4096,
                  "embedding_attempts": 64, "embedding_texts": 256,
                  "embedding_batch": 16, "embedding_text_bytes": 2048,
                  "embedding_total_bytes": 131072, "wall_seconds": 2400,
                  "client_seconds": 175, "queue_seconds": 170}
        for key, maximum in maxima.items():
            if type(getattr(self, key)) is not int or not 1 <= getattr(self, key) <= maximum:
                raise NativeRefused("limit outside reviewed maximum: " + key)
        if self.cleanup_seconds < 120 or self.cleanup_seconds >= self.wall_seconds or self.vector_dimension != 768:
            raise NativeRefused("cleanup/vector contract changed")


CHAT_FIELDS = {"model", "messages", "max_tokens", "temperature", "top_p", "n",
               "stream", "tools", "tool_choice", "parallel_tool_calls", "reasoning_effort",
               "response_format", "stop", "presence_penalty", "frequency_penalty", "seed"}
MESSAGE_FIELDS = {"role", "content", "name", "tool_calls", "tool_call_id"}


def validate_chat(body, limits):
    if type(body) is not dict or set(body) - CHAT_FIELDS or body.get("model") != limits.chat_model:
        raise NativeRefused("chat model/fields")
    if body.get("stream", False) is not False or body.get("n", 1) != 1:
        raise NativeRefused("only one nonstreaming choice")
    if body.get("parallel_tool_calls", False) is not False:
        raise NativeRefused("parallel tools unavailable")
    maximum = body.get("max_tokens")
    if type(maximum) is not int or not 1 <= maximum <= limits.output_per_call:
        raise NativeRefused("explicit bounded max_tokens required")
    messages = body.get("messages")
    if type(messages) is not list or not 1 <= len(messages) <= 128:
        raise NativeRefused("bounded message list required")
    for message in messages:
        if type(message) is not dict or set(message) - MESSAGE_FIELDS or message.get("role") not in {"system", "user", "assistant", "tool"}:
            raise NativeRefused("message fields/role")
        if message.get("content") is not None and type(message.get("content")) is not str:
            raise NativeRefused("text-only native messages required")
        if "tool_calls" in message:
            if message["role"] != "assistant" or type(message["tool_calls"]) is not list or not 1 <= len(message["tool_calls"]) <= 16:
                raise NativeRefused("tool call message shape")
            for call in message["tool_calls"]:
                if type(call) is not dict or set(call) != {"id", "type", "function"} or call["type"] != "function":
                    raise NativeRefused("tool call shape")
                function = call["function"]
                if type(call["id"]) is not str or type(function) is not dict or set(function) != {"name", "arguments"} or type(function["name"]) is not str or type(function["arguments"]) is not str:
                    raise NativeRefused("tool function shape")
                if type(decode(function["arguments"])) is not dict:
                    raise NativeRefused("object function arguments required")
        if message["role"] == "tool" and not isinstance(message.get("tool_call_id"), str):
            raise NativeRefused("tool result identity required")
    tools = body.get("tools", [])
    if type(tools) is not list or len(tools) > 32:
        raise NativeRefused("bounded tools required")
    for tool in tools:
        if type(tool) is not dict or set(tool) != {"type", "function"} or tool["type"] != "function" or type(tool["function"]) is not dict:
            raise NativeRefused("native function schema required")
        if set(tool["function"]) - {"name", "description", "parameters", "strict"} or not isinstance(tool["function"].get("name"), str):
            raise NativeRefused("function schema fields")
    # One explicit request policy applies to preparation, native CAMEL, report and probes.
    # No model response, tool definition, message or requested output is rewritten.
    if body.get("reasoning_effort", "none") != "none":
        raise NativeRefused("no-thinking policy required")
    result = dict(body)
    result["reasoning_effort"] = "none"
    return result


def validate_embedding(body, limits):
    if type(body) is not dict or set(body) - {"model", "input", "encoding_format"} or body.get("model") != limits.embedding_model:
        raise NativeRefused("embedding model/fields")
    if body.get("encoding_format", "float") != "float":
        raise NativeRefused("float embeddings required")
    texts = body.get("input")
    if isinstance(texts, str):
        texts = [texts]
    if type(texts) is not list or not 1 <= len(texts) <= limits.embedding_batch:
        raise NativeRefused("embedding batch limit")
    if any(type(t) is not str or not t or len(t.encode()) > limits.embedding_text_bytes for t in texts):
        raise NativeRefused("embedding text byte limit")
    return dict(body), len(texts), sum(len(t.encode()) for t in texts)


class Gateway:
    """Shared serial admission/dispatch ledger; failed reservations are never refunded."""
    def __init__(self, limits, secret, ledger_path, context_admission, *,
                 resource_check=lambda: None, clock=time.monotonic, dispatch=None, deadline=None, calls_path=None):
        if not isinstance(secret, str) or len(secret) < 32:
            raise NativeRefused("owned local authentication required")
        self.limits, self.secret, self.path = limits, secret, Path(ledger_path)
        self.admit_context, self.resource_check, self.clock = context_admission, resource_check, clock
        self.calls_path = Path(calls_path) if calls_path is not None else None
        if self.calls_path is not None:
            self.calls_path.mkdir(parents=True, exist_ok=False, mode=0o700)
        self.connections = set(); self.connection_lock = threading.Lock()
        self.started = clock(); self.deadline = min(self.started + limits.wall_seconds, deadline if deadline is not None else float("inf"))
        if self.deadline - self.started <= limits.cleanup_seconds:
            raise NativeRefused("whole-task cleanup headroom unavailable")
        self.active_deadline = self.deadline - limits.cleanup_seconds
        self.cancelled = threading.Event(); self.lock = threading.Lock(); self.serial = threading.Lock()
        self.dispatch = dispatch or self._http
        self.state = {"schema": 1, "chat_attempts": 0, "reserved_output": 0,
                      "embedding_attempts": 0, "embedding_texts": 0,
                      "embedding_bytes": 0, "calls": [], "cancelled": False,
                      "refusals": [], "refusal_count": 0}  # TMF5: every HTTP request answered with a refusal
        if self.path.exists():
            raise NativeRefused("fresh gateway ledger required; no budget reset/replay")
        atomic(self.path, self.state)

    def cancel(self):
        self.cancelled.set()
        with self.connection_lock:
            for connection in tuple(self.connections):
                abort_socket = getattr(connection, "_native_abort_socket", connection.sock)
                if abort_socket is not None:
                    try: abort_socket.shutdown(socket.SHUT_RDWR)
                    except OSError: pass
                connection.close()
        with self.lock:
            self.state["cancelled"] = True; atomic(self.path, self.state)

    def record_refusal(self, route, exc):
        """TMF5: a refused HTTP request is recorded durably (route, exception type and reason, never a body), whether it was
        refused before or after its reservation, so the run can fail on it instead of the producer degrading silently."""
        with self.lock:
            self.state["refusal_count"] += 1
            if len(self.state["refusals"]) < 256:
                reason = str(exc).replace(self.secret, "[REDACTED_LOCAL_AUTH]")[:512]
                self.state["refusals"].append({"route": str(route)[:64], "failure": type(exc).__name__, "reason": reason})
            atomic(self.path, self.state)

    def remaining(self, end):
        value = min(end, self.active_deadline) - self.clock()
        if self.cancelled.is_set() or value <= 0:
            raise NativeRefused("cancelled or active deadline expired")
        return value

    def _reserve(self, kind, body, texts, byte_count):
        with self.lock:
            self.remaining(self.active_deadline)
            if kind == "chat":
                maximum = body["max_tokens"]
                if self.state["chat_attempts"] >= self.limits.chat_attempts or self.state["reserved_output"] + maximum > self.limits.output_total:
                    raise NativeRefused("shared chat/output budget exhausted")
                self.state["chat_attempts"] += 1; self.state["reserved_output"] += maximum
            else:
                if (self.state["embedding_attempts"] >= self.limits.embedding_attempts or
                    self.state["embedding_texts"] + texts > self.limits.embedding_texts or
                    self.state["embedding_bytes"] + byte_count > self.limits.embedding_total_bytes):
                    raise NativeRefused("shared embedding budget exhausted")
                self.state["embedding_attempts"] += 1; self.state["embedding_texts"] += texts; self.state["embedding_bytes"] += byte_count
            call = {"id": uuid.uuid4().hex, "kind": kind, "state": "reserved",
                    "requested_output": body.get("max_tokens"), "payload_sha256": hashlib.sha256(encoded(body)).hexdigest(),
                    "dispatched": False, "usage": None, "usage_status": "unknown"}
            self.state["calls"].append(call); atomic(self.path, self.state)
            return call

    def _save(self, call, **values):
        with self.lock:
            call.update(values); atomic(self.path, self.state)

    def _capture(self, call, name, raw):
        if self.calls_path is None:
            return
        root = self.calls_path / call["id"]
        root.mkdir(mode=0o700, exist_ok=True)
        path = root / name
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())

    def handle(self, route, body, authorization, *, disconnected=lambda: False, arrived=None, deadline=None, query_cut_from=None):
        if not hmac.compare_digest(authorization or "", "Bearer " + self.secret):
            raise NativeRefused("local authentication refused")
        if route == "/v1/chat/completions":
            body = validate_chat(body, self.limits); kind = "chat"; texts = byte_count = 0
        elif route == "/v1/embeddings":
            body, texts, byte_count = validate_embedding(body, self.limits); kind = "embedding"
        else:
            raise NativeRefused("route refused")
        # TMF5 (c): the producer cut one search query to the text limit; the original size is recorded on the call.
        if query_cut_from is not None and not (kind == "embedding" and texts == 1 and type(query_cut_from) is int
                                               and query_cut_from > self.limits.embedding_text_bytes):
            raise NativeRefused("query cut record malformed")
        end = min(self.active_deadline, (self.clock() if arrived is None else arrived) + self.limits.client_seconds, deadline if deadline is not None else float("inf"))
        self.remaining(end)
        if disconnected():
            raise NativeRefused("client disconnected before reservation")
        call = self._reserve(kind, body, texts, byte_count)
        locked = False;stage='request_capture'
        try:
            if query_cut_from is not None:
                self._save(call, query_truncated_from_bytes=query_cut_from)
            captured=encoded(body).replace(self.secret.encode(),b'[REDACTED_LOCAL_AUTH]')
            self._capture(call, "request.json", captured)
            self._save(call,request_capture_sha256=hashlib.sha256(captured).hexdigest(),request_capture_redacted=captured!=encoded(body))
            stage='queue'
            locked = self.serial.acquire(timeout=min(self.limits.queue_seconds, self.remaining(end)))
            if not locked:
                raise NativeRefused("serial queue deadline")
            self.remaining(end)
            if disconnected():
                raise NativeRefused("client disconnected before admission")
            stage='resource_guard'
            self.resource_check()
            stage='context_admission'
            context = self.admit_context(kind, body, min(end, self.clock()+10.0))
            if (type(context) is not dict or context.get("verified") is not True or
                context.get("model") != body["model"] or type(context.get("tokens")) is not int or context["tokens"] < 1):
                raise NativeRefused("verified complete template/tokenizer admission unavailable")
            cap = self.limits.context if kind == "chat" else self.limits.embedding_context
            if context["tokens"] + (body["max_tokens"] if kind == "chat" else 0) > cap:
                raise NativeRefused("full native context plus output exceeds model context")
            self._save(call, context=context, arrival_deadline=end)
            timeout = self.remaining(end)
            if disconnected():
                raise NativeRefused("client disconnected before dispatch")
            # The durable dispatch intent is committed immediately before the sole fixed upstream call.
            self._save(call, state="inflight", dispatched=True)
            self.remaining(end)
            if disconnected():
                raise NativeRefused("client disconnected at dispatch boundary")
            stage='upstream_dispatch'
            status, raw = self.dispatch(route, body, end)
            if isinstance(raw, bytes):
                self._capture(call, "response.bin" if len(raw) <= self.limits.response_bytes else "response-prefix-over-limit.bin", raw[:self.limits.response_bytes])
            self._save(call, upstream_status=status)
            self.remaining(end)
            if disconnected():
                raise NativeRefused("client disconnected with upstream outcome unknown")
            if type(status) is not int or status != 200 or not isinstance(raw, bytes) or len(raw) > self.limits.response_bytes:
                raise NativeRefused("bounded successful upstream response required")
            response = decode(raw)
            if kind == "embedding":
                rows = response.get("data") if isinstance(response, dict) else None
                if not isinstance(rows, list) or len(rows) != texts or sorted(row.get("index") for row in rows) != list(range(texts)):
                    raise NativeRefused("embedding cardinality/index drift")
                for row in rows:
                    vector = row.get("embedding")
                    if not isinstance(vector, list) or len(vector) != self.limits.vector_dimension or any(type(x) not in (int, float) or not math.isfinite(x) for x in vector):
                        raise NativeRefused("finite 768-dimensional native embeddings required")
            else:
                if not isinstance(response, dict) or not isinstance(response.get("choices"), list) or len(response["choices"]) != 1:
                    raise NativeRefused("single native chat response required")
                choice=response['choices'][0]
                if type(choice) is not dict or type(choice.get('message')) is not dict:
                    raise NativeRefused('native chat choice/message shape required')
                finish=choice.get('finish_reason')
                if finish=='tool_calls':
                    calls=choice['message'].get('tool_calls')
                    if type(calls) is not list or not calls:
                        raise NativeRefused('valid nonempty tool-call completion required')
                    for tool in calls:
                        function=tool.get('function') if type(tool) is dict else None
                        if tool.get('type')!='function' or type(function) is not dict or type(function.get('name')) is not str or not function['name'] or type(function.get('arguments')) is not str or type(decode(function['arguments'])) is not dict:
                            raise NativeRefused('valid function tool-call completion required')
                elif finish!='stop':
                    raise NativeRefused('native chat finish_reason refused: '+str(finish))
            usage = response.get("usage") if isinstance(response, dict) else None
            if usage is not None and (not isinstance(usage, dict) or any(type(v) is not int or v < 0 for k,v in usage.items() if k in {"prompt_tokens", "completion_tokens", "total_tokens"})):
                raise NativeRefused("invalid upstream usage")
            if kind == "chat" and usage is not None and usage.get("completion_tokens", 0) > body["max_tokens"]:
                self.cancel(); raise NativeRefused("upstream output cap breach")
            self._save(call, state="complete", usage=usage, usage_status="reported" if isinstance(usage, dict) and {"prompt_tokens", "completion_tokens", "total_tokens"}.issubset(usage) else "partial" if usage else "unknown", response_sha256=hashlib.sha256(raw).hexdigest())
            return raw
        except BaseException as exc:
            if call["dispatched"]:
                # The peer may continue after our socket closes. Freeze every
                # route before serial release; an available budget is not proof
                # the unknown operation has finished. No retry/reset is allowed.
                self.cancel()
                partial = getattr(exc, "partial_response", None)
                if partial:
                    self._capture(call, "response-partial-unknown.bin", partial[:self.limits.response_bytes])
            self._save(call, state="unknown" if call["dispatched"] else "refused_before_forward", failure=type(exc).__name__,failure_reason=str(exc).replace(self.secret,"[REDACTED_LOCAL_AUTH]")[:2048],failure_stage=stage)
            raise
        finally:
            try:
                if self.calls_path is not None:
                    root = self.calls_path / call["id"]; root.mkdir(mode=0o700, exist_ok=True)
                    atomic(root / "outcome.json", call)
            except BaseException:
                self.cancelled.set()
                raise
            finally:
                if locked:
                    self.serial.release()

    def _http(self, route, body, deadline):
        # One absolute end, supplied by handle after queue/context/persistence.
        # A watchdog interrupts the actual TCP socket, including header parsing
        # and slow-drip body reads. It does not leave a reader future running.
        end = min(deadline, self.active_deadline)
        connection = http.client.HTTPConnection("127.0.0.1", 1235, timeout=self.remaining(end))
        expired = threading.Event()
        received = bytearray()
        status = None
        def expire():
            expired.set()
            self.cancelled.set()  # No queued/future admission can race release.
            with self.connection_lock:
                abort_socket = getattr(connection, "_native_abort_socket", None)
                if abort_socket is not None:
                    try: abort_socket.shutdown(socket.SHUT_RDWR)
                    except OSError: pass
                connection.close()
        timer = threading.Timer(max(0, end-self.clock()), expire)
        timer.name = "mirofish-http-deadline-" + uuid.uuid4().hex
        timer.daemon = True
        with self.connection_lock:
            self.connections.add(connection)
        timer.start()
        try:
            connection.connect()
            # HTTPConnection may relinquish .sock after Connection: close.
            # Retain our own duplicate so shutdown still interrupts the same
            # real connection while HTTPResponse owns its makefile reader.
            with self.connection_lock:
                connection._native_abort_socket = connection.sock.dup()
            connection.sock.settimeout(self.remaining(end))
            self.remaining(end)
            connection.request("POST", route, encoded(body), {"Content-Type": "application/json", "Authorization": "Bearer local-transport-only"})
            response = connection.getresponse()
            status = response.status
            self.remaining(end)
            while True:
                self.remaining(end)
                block = response.read1(min(65536, self.limits.response_bytes+1-len(received)))
                if not block:
                    if response.length not in (None, 0):
                        raise NativeRefused("truncated upstream body")
                    break
                received.extend(block)
                if len(received) > self.limits.response_bytes:
                    raise NativeRefused("upstream response byte limit")
            self.remaining(end)
            if expired.is_set():
                raise NativeRefused("absolute upstream deadline expired")
            return status, bytes(received)
        except BaseException as exc:
            raise UpstreamIOFailure("upstream I/O unknown: " + type(exc).__name__,
                                    partial_response=bytes(received) if received else None,
                                    status=status) from exc
        finally:
            timer.cancel()
            with self.connection_lock:
                self.connections.discard(connection)
                abort_socket = getattr(connection, "_native_abort_socket", None)
                if abort_socket is not None:
                    abort_socket.close()
                connection.close()
            timer.join(.25)
            if timer.is_alive():
                self.cancelled.set()
                raise UpstreamIOFailure("deadline watchdog cleanup uncertain",
                                        partial_response=bytes(received) if received else None,
                                        status=status)


def make_server(gateway, port=0):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            arrived = gateway.clock()
            try:
                if self.headers.get("Transfer-Encoding") or self.headers.get("Content-Encoding"):
                    raise NativeRefused("encoded/chunked requests refused")
                size = int(self.headers.get("Content-Length", "-1"))
                if not 0 < size <= gateway.limits.request_bytes:
                    raise NativeRefused("request byte limit")
                self.connection.settimeout(5)
                raw = self.rfile.read(size)
                if len(raw) != size:
                    raise NativeRefused("partial request")
                def disconnected():
                    readable, _, _ = select.select([self.connection], [], [], 0)
                    return bool(readable) and self.connection.recv(1, 2) == b""
                cut = self.headers.get("X-Native-Query-Cut-From")
                if cut is not None and not (cut.isascii() and cut.isdigit() and len(cut) <= 9):
                    raise NativeRefused("query cut record malformed")
                result = gateway.handle(self.path, decode(raw), self.headers.get("Authorization"), disconnected=disconnected, arrived=arrived,
                                        query_cut_from=None if cut is None else int(cut))
                self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(result))); self.end_headers(); self.wfile.write(result)
            except Exception as exc:
                try:
                    gateway.record_refusal(self.path, exc)
                except Exception:
                    gateway.cancelled.set()  # a refusal that cannot be recorded freezes the gateway
                try:
                    result = b'{"error":"owned native gateway refused"}'
                    self.send_response(409); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(result))); self.end_headers(); self.wfile.write(result)
                except OSError:
                    pass
        def do_GET(self):
            self.send_error(405)
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server
