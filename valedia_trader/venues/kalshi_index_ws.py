"""A read-only client for Kalshi's CF Benchmarks index websocket (BRTI), recording each tick to a JSONL file.

    python -m valedia_trader.venues.kalshi_index_ws stream --out-dir DIR [--index BRTI ...] [--5hz] [--seconds N]

What it does: it opens one websocket, subscribes to the `cfbenchmarks_value` channel (or `cfbenchmarks_value_5hz`) for
the named index ids, parses every value message into an `IndexTick` and appends it to
`<folder>/kalshi_index_ticks-YYYY-MM-DD.jsonl` (the UTC date of the tick's `received_at_ms`).

What it never does: it makes no REST call, sends no trade instruction and subscribes to no account channel. Every
outgoing frame goes through `guard_frame`, which allows only a subscribe to the two index channels, an
`update_subscription` with an index action, and an `unsubscribe`.

Secrets: the key id and the key file are read only when a live connection is about to open (after the date gate), and
neither they nor the signature appear in any log line, error text, repr, summary or JSONL line.

The date gate is the shared `ENABLED_FROM` of the research loop's logger: before that instant `stream` refuses
before it reads a credential or opens a connection.

Keep-alive: the server pings after 10 s without data and the `websockets` library answers with pongs by itself; it also
closes a connection whose peer stops answering. So a connection is treated as idle only when neither messages nor pings
arrive, and the client reconnects when it is closed. The reader never blocks on anything but the next frame, so the
server never sees a slow reader.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import inspect
import json
import logging
import os
import random
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

from valedia_trader.research_loop.btc_updown_logger import ENABLED_FROM, NaiveClock, NotYetEnabled

WS_URL = "wss://external-api-ws.kalshi.com/trade-api/ws/v2"
WS_PATH = "/trade-api/ws/v2"

KEY_ID_ENV = "KALSHI_API_KEY_ID"
KEY_PATH_ENV = "KALSHI_PRIVATE_KEY_PATH"
DEFAULT_KEY_PATH = "~/.hermes/secrets/kalshi_private_key.pem"

CHANNEL_VALUE = "cfbenchmarks_value"
CHANNEL_VALUE_5HZ = "cfbenchmarks_value_5hz"
ALLOWED_CHANNELS = frozenset({CHANNEL_VALUE, CHANNEL_VALUE_5HZ})
ALLOWED_ACTIONS = frozenset({"subscribe_indices", "unsubscribe_indices", "indexlist"})
_ACTIONS_WITH_INDICES = frozenset({"subscribe_indices", "unsubscribe_indices"})
_VALUE_TYPES = frozenset({CHANNEL_VALUE, CHANNEL_VALUE_5HZ})
_INDEXLIST_TYPES = frozenset({CHANNEL_VALUE + "_indexlist", CHANNEL_VALUE_5HZ + "_indexlist"})
_INDEX_RE = re.compile(r"[A-Z0-9_]{2,32}")        # matched with fullmatch: `^[A-Z0-9_]{2,32}$` without the newline slip

# websockets logs every handshake header (the key id and the signature) at DEBUG when its logger is enabled for it. The
# live connection gets this logger instead: its own level is pinned above DEBUG, so no host-wide DEBUG setting reaches it.
_QUIET_LOGGER = logging.getLogger(__name__ + ".websocket")
_QUIET_LOGGER.setLevel(logging.WARNING)

STORE_PREFIX = "kalshi_index_ticks-"
_REPO_ROOT = Path(__file__).resolve().parents[2]   # the folder that holds the `valedia_trader` package

BACKOFF_START_S = 1.0
BACKOFF_CAP_S = 60.0
JITTER_FRACTION = 0.25      # a delay is shortened by up to this share, so the doubling never passes the cap
POLL_S = 1.0                # the longest the reader waits for one frame before it re-checks its deadline
MAX_ERRORS_KEPT = 100


# ------------------------------------------------------------------ errors

class CredentialError(RuntimeError):
    """A credential is missing or unusable. The text names the variable, never its value."""


class SendRefused(ValueError):
    """An outgoing frame is not one of the read-only index-stream frames."""


class MalformedFrame(ValueError):
    """An incoming frame is not a value message this module understands."""


class StoreError(RuntimeError):
    """A tick could not be written to the store."""


# ------------------------------------------------------------------ signing and credentials

def sign(private_key: Any, timestamp_ms: int | str, path: str) -> str:
    """Base64 signature of `timestamp + "GET" + path`, with any query string stripped from the path.

    RSA keys: RSA-PSS, SHA-256, MGF1(SHA-256), salt length = the digest length (32). Ed25519 keys sign the bytes directly.
    """
    clean_path = str(path).split("?", 1)[0]
    message = f"{timestamp_ms}GET{clean_path}".encode("utf-8")
    if isinstance(private_key, RSAPrivateKey):
        signature = private_key.sign(
            message,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
    elif isinstance(private_key, Ed25519PrivateKey):
        signature = private_key.sign(message)
    else:
        raise TypeError("private key must be an RSA or Ed25519 key object")
    return base64.b64encode(signature).decode("ascii")


def build_headers(key_id: str, private_key: Any, timestamp_ms: int, path: str = WS_PATH) -> dict[str, str]:
    """The three handshake headers; the timestamp is unix milliseconds as a string."""
    return {
        "KALSHI-ACCESS-KEY": key_id,
        "KALSHI-ACCESS-SIGNATURE": sign(private_key, timestamp_ms, path),
        "KALSHI-ACCESS-TIMESTAMP": str(int(timestamp_ms)),
    }


@dataclass(frozen=True, repr=False)
class Credentials:
    """A key id and a private-key object. Its repr shows neither."""
    key_id: str
    private_key: Any

    def __repr__(self) -> str:
        return "Credentials(<redacted>)"


def load_credentials(environ: Mapping[str, str] | None = None) -> Credentials:
    """Read the key id and the key file named by the environment. Called only when a live connection is about to open."""
    env = os.environ if environ is None else environ
    key_id = (env.get(KEY_ID_ENV) or "").strip()
    if not key_id:
        raise CredentialError(f"{KEY_ID_ENV} is not set")
    key_path = env.get(KEY_PATH_ENV) or DEFAULT_KEY_PATH
    try:
        pem = Path(key_path).expanduser().read_bytes()
    except (OSError, ValueError):
        raise CredentialError(f"the key file named by {KEY_PATH_ENV} (or its default) cannot be read") from None
    try:
        key = serialization.load_pem_private_key(pem, password=None)
    except Exception:       # noqa: BLE001 - whatever the parser says could quote key material; say only which input
        raise CredentialError(f"the file named by {KEY_PATH_ENV} is not a usable private key") from None
    if not isinstance(key, (RSAPrivateKey, Ed25519PrivateKey)):
        raise CredentialError(f"the file named by {KEY_PATH_ENV} is not an RSA or Ed25519 key")
    return Credentials(key_id=key_id, private_key=key)


def _resolve_credentials(source: Any) -> Credentials:
    got = load_credentials() if source is None else (source() if callable(source) else source)
    if (not isinstance(got, Credentials) or not isinstance(got.key_id, str) or not got.key_id
            or not isinstance(got.private_key, (RSAPrivateKey, Ed25519PrivateKey))):
        raise CredentialError("credentials must be a Credentials with a key id and an RSA or Ed25519 key")
    return got


# ------------------------------------------------------------------ the date gate

def _gate(clock: Callable[[], datetime]) -> datetime:
    """ONE clock reading, checked against ENABLED_FROM and returned. Runs before any credential or connect."""
    now = clock()
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise NaiveClock("the clock must return a timezone-aware datetime")
    if now.timestamp() < ENABLED_FROM.timestamp():
        raise NotYetEnabled(f"disabled until {ENABLED_FROM.isoformat()}")
    return now


# ------------------------------------------------------------------ the send guard

def _check_index_ids(ids: Any) -> list[str]:
    if isinstance(ids, (str, bytes)) or not isinstance(ids, (list, tuple)) or not ids:
        raise SendRefused("index_ids must be a non-empty list")
    for one in ids:
        if not isinstance(one, str) or not (one == "all" or _INDEX_RE.fullmatch(one)):
            raise SendRefused("an index id must match [A-Z0-9_]{2,32} (or be \"all\")")
    return list(ids)


def _is_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def guard_frame(frame: Any) -> str:
    """The one check every outgoing frame passes. Returns the JSON text to send, or raises SendRefused.

    Allowed: `subscribe` (channels within the two index channels), `update_subscription` (action within
    subscribe_indices / unsubscribe_indices / indexlist) and `unsubscribe`. Nothing else.
    """
    if not isinstance(frame, dict) or set(frame) - {"id", "cmd", "params"} or not {"id", "cmd", "params"} <= set(frame):
        raise SendRefused("a frame must be {id, cmd, params}")
    if not _is_int(frame["id"]) or frame["id"] < 1:
        raise SendRefused("a frame id must be a positive whole number")
    cmd, params = frame["cmd"], frame["params"]
    if not isinstance(params, dict):
        raise SendRefused("params must be an object")
    if cmd == "subscribe":
        if set(params) != {"channels", "index_ids"}:
            raise SendRefused("subscribe takes channels and index_ids only")
        channels = params["channels"]
        if (not isinstance(channels, (list, tuple)) or not channels
                or any(not isinstance(c, str) or c not in ALLOWED_CHANNELS for c in channels)):
            raise SendRefused(f"subscribe is allowed only for {sorted(ALLOWED_CHANNELS)}")
        _check_index_ids(params["index_ids"])
    elif cmd == "update_subscription":
        action = params.get("action")
        if not isinstance(action, str) or action not in ALLOWED_ACTIONS:
            raise SendRefused(f"update_subscription is allowed only for {sorted(ALLOWED_ACTIONS)}")
        extra = {"sid", "action", "index_ids"} if action in _ACTIONS_WITH_INDICES else {"sid", "action"}
        if not _is_int(params.get("sid")) or not {"sid", "action"} <= set(params) <= extra:
            raise SendRefused("update_subscription takes sid, action and (for index actions) index_ids only")
        if action in _ACTIONS_WITH_INDICES:
            if "index_ids" not in params:
                raise SendRefused("this action needs index_ids")
            _check_index_ids(params["index_ids"])
    elif cmd == "unsubscribe":
        sids = params.get("sids")
        if set(params) != {"sids"} or not isinstance(sids, (list, tuple)) or not sids or not all(_is_int(s) for s in sids):
            raise SendRefused("unsubscribe takes sids (whole numbers) only")
    else:
        raise SendRefused("this command is not allowed on the index stream")
    return json.dumps(frame, separators=(",", ":"), sort_keys=True)


async def send_frame(ws: Any, frame: Any) -> None:
    """The only place a frame leaves this module."""
    text = guard_frame(frame)
    await ws.send(text)


def subscribe_frame(channel: str, index_ids: Sequence[str], frame_id: int = 1) -> dict:
    return {"id": frame_id, "cmd": "subscribe", "params": {"channels": [channel], "index_ids": list(index_ids)}}


# ------------------------------------------------------------------ the parser

@dataclass(frozen=True)
class WindowAvg:
    value: Decimal
    window_size: int
    window_start_ts_ms: int
    window_end_ts_exclusive: int

    def as_json(self) -> dict:
        return {"value": _dec_str(self.value), "window_size": self.window_size,
                "window_start_ts_ms": self.window_start_ts_ms, "window_end_ts_exclusive": self.window_end_ts_exclusive}


@dataclass(frozen=True)
class IndexTick:
    index_id: str
    value: Decimal
    source_time_ms: int
    received_at_ms: int
    sending_ts_ms: int | None
    sid: int
    seq: int
    avg_60s: WindowAvg | None
    last_15min_avg: WindowAvg | None
    data: str

    def as_json(self, channel: str | None = None) -> dict:
        row = {"index_id": self.index_id, "value": _dec_str(self.value), "source_time_ms": self.source_time_ms,
               "received_at_ms": self.received_at_ms, "sending_ts_ms": self.sending_ts_ms, "sid": self.sid,
               "seq": self.seq, "avg_60s": self.avg_60s.as_json() if self.avg_60s else None,
               "last_15min_avg": self.last_15min_avg.as_json() if self.last_15min_avg else None, "data": self.data}
        if channel:
            row["channel"] = channel
        return row


def _dec_str(d: Decimal) -> str:
    return format(d, "f")


def _to_int(x: Any) -> int:
    if isinstance(x, bool):
        raise MalformedFrame("a whole number was expected")
    if isinstance(x, int):
        return x
    if isinstance(x, str) and re.fullmatch(r"-?[0-9]{1,18}", x):
        return int(x)
    raise MalformedFrame("a whole number was expected")


def _to_decimal(x: Any) -> Decimal:
    if isinstance(x, bool) or not isinstance(x, (str, int, Decimal)):
        raise MalformedFrame("a decimal string was expected")     # a float is refused: it has already lost digits
    if isinstance(x, str) and len(x) > 64:
        raise MalformedFrame("a decimal string is too long")
    try:
        d = Decimal(x)
    except InvalidOperation:
        raise MalformedFrame("not a decimal number") from None
    if not d.is_finite() or abs(d.adjusted()) > 30:
        raise MalformedFrame("not a usable finite number")
    return d


def _parse_window(node: Any) -> WindowAvg:
    if not isinstance(node, dict):
        raise MalformedFrame("a window average must be an object")
    return WindowAvg(value=_to_decimal(node.get("value")), window_size=_to_int(node.get("window_size")),
                     window_start_ts_ms=_to_int(node.get("window_start_ts_ms")),
                     window_end_ts_exclusive=_to_int(node.get("window_end_ts_exclusive")))


def _check_ms(ms: int) -> int:
    try:
        datetime.fromtimestamp(ms // 1000, timezone.utc)
    except (OverflowError, OSError, ValueError):
        raise MalformedFrame("a timestamp is out of range") from None
    return ms


def parse_value_message(obj: Any) -> IndexTick:
    """A decoded `cfbenchmarks_value` (or `_5hz`) frame to an IndexTick; MalformedFrame when it is not one."""
    if not isinstance(obj, dict) or obj.get("type") not in _VALUE_TYPES:
        raise MalformedFrame("not a value message")
    msg = obj.get("msg")
    if not isinstance(msg, dict):
        raise MalformedFrame("msg must be an object")
    index_id = msg.get("index_id")
    if not isinstance(index_id, str) or not _INDEX_RE.fullmatch(index_id):
        raise MalformedFrame("bad index id")
    data = msg.get("data")
    if not isinstance(data, str):
        raise MalformedFrame("data must be the raw string")
    try:
        raw = json.loads(data)
    except ValueError:
        raise MalformedFrame("data is not JSON") from None
    if not isinstance(raw, dict):
        raise MalformedFrame("data must be an object")
    sending = obj.get("sending_ts_ms")
    avg_60 = msg.get("avg_60s_data")
    last_15 = msg.get("last_60s_windowed_average_15min")
    if obj["type"] == CHANNEL_VALUE and avg_60 is None:
        raise MalformedFrame("avg_60s_data is always present on this channel")     # the 5 Hz message has none
    return IndexTick(
        index_id=index_id,
        value=_to_decimal(raw.get("value")),
        source_time_ms=_check_ms(_to_int(raw.get("time"))),
        received_at_ms=_check_ms(_to_int(msg.get("received_at"))),
        sending_ts_ms=None if sending is None else _to_int(sending),
        sid=_to_int(obj.get("sid")),
        seq=_to_int(obj.get("seq")),
        avg_60s=None if avg_60 is None else _parse_window(avg_60),
        last_15min_avg=None if last_15 is None else _parse_window(last_15),
        data=data,
    )


# ------------------------------------------------------------------ the store

def _check_folder(folder: Any) -> Path:
    """The store folder must be an absolute path outside the repository (the folder that holds this package)."""
    if not isinstance(folder, (str, os.PathLike)):
        raise ValueError("folder must name a directory")
    raw = os.fspath(folder)
    if not isinstance(raw, str) or not raw or not os.path.isabs(raw):
        raise ValueError("folder must be an absolute path (not empty, not relative)")
    resolved = Path(os.path.realpath(raw))
    root = Path(os.path.realpath(_REPO_ROOT))
    if resolved == root or root in resolved.parents:
        raise ValueError("folder must be outside the repository")
    for candidate in (resolved, *resolved.parents):
        if candidate.exists() and os.path.samefile(candidate, root):
            raise ValueError("folder must be outside the repository")
    return resolved


class TickStore:
    """`<folder>/kalshi_index_ticks-YYYY-MM-DD.jsonl`, one line per tick, dated by the UTC day of `received_at_ms`."""

    def __init__(self, folder: Any):
        self.folder = _check_folder(folder)

    def path_for(self, received_at_ms: int) -> Path:
        day = datetime.fromtimestamp(received_at_ms // 1000, timezone.utc).strftime("%Y-%m-%d")
        return self.folder / f"{STORE_PREFIX}{day}.jsonl"

    def append(self, tick: IndexTick, channel: str | None = None) -> Path:
        path = self.path_for(tick.received_at_ms)
        line = json.dumps(tick.as_json(channel), separators=(",", ":"), sort_keys=True)
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError as exc:
            raise StoreError(f"cannot write a tick ({type(exc).__name__})") from None
        return path


# ------------------------------------------------------------------ the stream

def backoff_delay(failures: int) -> float:
    """Seconds to wait after the `failures`-th failure in a row (1-based): 1, 2, 4 ... capped at 60, less up to 25% jitter."""
    base = min(BACKOFF_CAP_S, BACKOFF_START_S * (2 ** max(0, failures - 1)))
    return base * (1.0 - JITTER_FRACTION * _jitter())


def _jitter() -> float:
    return random.random()


async def _maybe_await(x: Any) -> Any:
    return await x if inspect.isawaitable(x) else x


class _Run:
    def __init__(self, store: TickStore, index_ids: Sequence[str], channel: str, clock: Callable[[], datetime],
                 connect: Callable[..., Any], creds: Credentials, max_seconds: float | None,
                 sleep: Callable[[float], Any], start: datetime):
        self.store, self.index_ids, self.channel, self.clock = store, list(index_ids), channel, clock
        self.connect, self.creds, self.max_seconds, self.sleep, self.start = connect, creds, max_seconds, sleep, start
        self.ticks = self.gaps = self.missed = self.malformed = self.reconnects = 0
        self.errors: list[dict] = []
        self.last_seq: dict[int, int] = {}
        self.signatures: set[str] = set()
        self.done = False
        self._got_data = False      # did the current (or latest) connection store at least one tick?

    # -- bookkeeping
    def summary(self) -> dict:
        return {"ticks": self.ticks, "gaps": self.gaps, "missed": self.missed, "malformed": self.malformed,
                "reconnects": self.reconnects, "errors": list(self.errors)}

    def _scrub(self, text: str) -> str:
        for secret in (self.creds.key_id, *self.signatures):
            if secret:
                text = text.replace(secret, "<redacted>")
        return text[:200]

    def _record_error(self, kind: str, **fields: Any) -> None:
        if len(self.errors) < MAX_ERRORS_KEPT:
            row = {"kind": kind}
            for k, v in fields.items():
                row[k] = self._scrub(v) if isinstance(v, str) else v
            self.errors.append(row)

    def _record_exception(self, exc: BaseException) -> None:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if _is_int(status):
            self._record_error(f"handshake_{status}", exception=type(exc).__name__)
        elif type(exc).__name__.startswith("ConnectionClosed"):
            code = getattr(getattr(exc, "rcvd", None), "code", None)
            self._record_error("connection_closed", exception=type(exc).__name__, code=code if _is_int(code) else None)
        else:
            self._record_error("connection_failed", exception=type(exc).__name__, detail=str(exc))

    def _remaining(self) -> float | None:
        if self.max_seconds is None:
            return None
        now = self.clock()
        if not isinstance(now, datetime) or now.tzinfo is None:
            raise NaiveClock("the clock must return a timezone-aware datetime")
        return self.max_seconds - (now - self.start).total_seconds()

    # -- one frame in
    def handle(self, raw: Any) -> None:
        try:
            text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw
            obj = json.loads(text)
        except (ValueError, TypeError, AttributeError):
            self.malformed += 1
            return
        kind = obj.get("type") if isinstance(obj, dict) else None
        if kind in _VALUE_TYPES:
            try:
                tick = parse_value_message(obj)
            except MalformedFrame:
                self.malformed += 1
                return
            self._count_seq(tick.sid, tick.seq)
            self.store.append(tick, kind)
            self.ticks += 1
            self._got_data = True       # only a stored tick restarts the backoff, not a reply or an error
        elif kind in _INDEXLIST_TYPES:
            pass        # the reply to an `indexlist` request: it carries no tick
        elif kind == "error":
            body = obj.get("msg") if isinstance(obj.get("msg"), dict) else {}
            code = body.get("code")
            message = body.get("msg", body.get("message"))
            self._record_error("server_error", code=code if _is_int(code) or isinstance(code, str) else None,
                               message=message if isinstance(message, str) else None)
        elif not isinstance(kind, str):
            self.malformed += 1
        # `subscribed`, `ok` and any other type carry no tick: nothing to record

    def _count_seq(self, sid: int, seq: int) -> None:
        last = self.last_seq.get(sid)
        if last is not None and seq > last + 1:
            self.gaps += 1
            self.missed += seq - last - 1
        if last is None or seq > last:
            self.last_seq[sid] = seq

    # -- one connection
    async def _read(self, ws: Any) -> None:
        while True:
            remaining = self._remaining()
            if remaining is not None and remaining <= 0:
                self.done = True
                return
            try:
                async with asyncio.timeout(POLL_S if remaining is None else min(POLL_S, remaining)):
                    raw = await ws.recv()
            except TimeoutError:
                continue
            self.handle(raw)

    async def _one_connection(self) -> None:
        self._got_data = False
        now = self.clock()
        headers = build_headers(self.creds.key_id, self.creds.private_key, int(now.timestamp() * 1000))
        self.signatures.add(headers["KALSHI-ACCESS-SIGNATURE"])
        ws = None
        try:
            ws = await _maybe_await(self.connect(WS_URL, additional_headers=headers))
            self.last_seq = {}
            await send_frame(ws, subscribe_frame(self.channel, self.index_ids))
            await self._read(ws)
        finally:
            if ws is not None:
                try:
                    await _maybe_await(ws.close())
                except Exception:       # noqa: BLE001 - a close that fails changes nothing
                    pass

    async def main(self) -> None:
        failures = 0
        first = True
        while not self.done:
            remaining = self._remaining()
            if remaining is not None and remaining <= 0:
                break
            if not first:
                self.reconnects += 1
            first = False
            try:
                await self._one_connection()
            except (KeyboardInterrupt, asyncio.CancelledError):
                break           # Ctrl-C reaches asyncio.run as a cancellation; the socket is already closed
            except StoreError:
                raise
            except Exception as exc:    # noqa: BLE001 - a close, a refused handshake or a network error: back off and retry
                self._record_exception(exc)
            if self.done:
                break
            failures = 1 if self._got_data else failures + 1      # data on this connection restarts the backoff
            delay = backoff_delay(failures)
            remaining = self._remaining()
            if remaining is not None:
                if remaining <= 0:
                    break
                delay = min(delay, remaining)
            try:
                await _maybe_await(self.sleep(delay))
            except KeyboardInterrupt:
                break


def _default_connect(url: str, **kwargs: Any) -> Any:
    import websockets       # imported only for a live connection
    return websockets.connect(url, logger=_QUIET_LOGGER, **kwargs)


def stream(folder: Any, *, index_ids: Sequence[str] = ("BRTI",), channel: str = CHANNEL_VALUE,
           clock: Callable[[], datetime], connect: Callable[..., Any] | None = None,
           max_seconds: float | None = None, credentials: Any = None,
           sleep: Callable[[float], Any] | None = None) -> dict:
    """Stream the index channel into the store until `max_seconds` or Ctrl-C; return the run's summary.

    Sequence: the date gate, the folder check and the frame check, then the credentials, then connect with signed
    headers and the guarded subscribe. `connect(url, additional_headers=...)` returns a connection (or an awaitable of
    one); `sleep(seconds)` may be a plain function or a coroutine function.
    """
    start = _gate(clock)
    store = TickStore(folder)
    ids = list(index_ids) if isinstance(index_ids, (list, tuple)) else index_ids
    guard_frame(subscribe_frame(channel, ids))          # a bad channel or index id refuses before any secret is read
    if max_seconds is not None and (isinstance(max_seconds, bool) or not isinstance(max_seconds, (int, float))
                                    or not max_seconds > 0):
        raise ValueError("max_seconds must be a positive number")
    creds = _resolve_credentials(credentials)
    run = _Run(store, ids, channel, clock, connect or _default_connect, creds, max_seconds,
               sleep or asyncio.sleep, start)
    try:
        asyncio.run(run.main())
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    return run.summary()


# ------------------------------------------------------------------ the command line

def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kalshi_index_ws", description="Read-only Kalshi index (BRTI) tick logger.")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("stream", help="stream ticks into a folder outside the repository")
    p.add_argument("--out-dir", required=True, help="absolute folder outside the repository")
    p.add_argument("--index", action="append", dest="indices", help="an index id (repeatable; default BRTI)")
    p.add_argument("--5hz", dest="five_hz", action="store_true", help="use the 5 Hz channel")
    p.add_argument("--seconds", type=float, default=None, help="stop after this many seconds")
    args = parser.parse_args(argv)
    try:
        summary = stream(args.out_dir, index_ids=tuple(args.indices or ("BRTI",)),
                         channel=CHANNEL_VALUE_5HZ if args.five_hz else CHANNEL_VALUE,
                         clock=_utc_now, max_seconds=args.seconds)
    except (NotYetEnabled, NaiveClock, CredentialError, StoreError, ValueError) as exc:
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
