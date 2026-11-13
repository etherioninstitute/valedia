"""Credential loading. Secrets live in one owner-only file outside the repository and outside the
general Hermes environment, so only the trading code that asks for them can read them.

Default file: ~/.hermes/secrets/trading.env (override with VALEDIA_ENV_FILE). Values are never
logged, printed or included in exceptions.

A tripwire guards the exits as a second line of defence. Every credential this module hands out
(through `secret`, or loaded by `load_env` under a credential or account name) is remembered, when it is
at least 8 characters long. `redact` and `redact_json` replace any piece of a remembered secret in a
text or a JSON-like record, and the first time a secret is remembered an output guard wraps
standard output, standard error and the root logger so that nothing printed or logged carries one.
`tripped` says how many replacements have been made; nothing else about them is ever exposed.

A piece made only of the characters 0-9 and a-f counts from 12 characters, every other piece from
8, so ordinary numbers, prices and hashes are never mistaken for a secret. A remembered value of 8
to 11 such characters (a short account number) is caught only whole and standing alone, not inside
a longer run of them. What this gives up: a piece of 8 to 11 characters of a digit-only or hex-only
secret, or of the hex form of any secret, passes."""
from __future__ import annotations

import atexit
import base64
import hashlib
import logging
import os
import re
import stat
import sys
import threading
import weakref
from pathlib import Path
from urllib.parse import quote, quote_plus, unquote

__all__ = ["env_file_path", "load_env", "secret", "SecretError", "DEFAULT_ENV_FILE", "SECRETS_DIR",
           "redact", "redact_json", "tripped", "install_output_guard", "remove_output_guard"]

SECRETS_DIR = Path("~/.hermes/secrets").expanduser()
DEFAULT_ENV_FILE = SECRETS_DIR / "trading.env"


class SecretError(RuntimeError):
    """A credential is missing or its file is unsafe. The message names the variable, never the value."""


def env_file_path(environ=None) -> Path:
    env = os.environ if environ is None else environ
    return Path(env.get("VALEDIA_ENV_FILE") or DEFAULT_ENV_FILE).expanduser()


def parse_env(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.replace("export ", "", 1).strip()
        if key.replace("_", "").isalnum() and not key[0].isdigit():
            out[key] = value.strip().strip('"').strip("'")
    return out


def load_env(path: str | Path | None = None, environ=None, override: bool = False) -> list[str]:
    """Load KEY=VALUE pairs into the environment and return the names loaded. A file readable by
    group or others is refused. A missing file loads nothing (and returns an empty list)."""
    env = os.environ if environ is None else environ
    p = Path(path).expanduser() if path else env_file_path(env)
    if not p.is_file():
        return []
    if stat.S_IMODE(p.stat().st_mode) & 0o077:
        raise SecretError(f"{p} is readable by group or others; run chmod 600 on it")
    loaded = []
    for key, value in parse_env(p.read_text()).items():
        if override or key not in env:
            env[key] = value
            loaded.append(key)
            if _looks_like_credential(key):
                _remember(value)
    return loaded


def secret(name: str, environ=None) -> str:
    """Strict lookup of one credential from the environment."""
    env = os.environ if environ is None else environ
    value = env.get(name, "")
    if not value.strip():
        raise SecretError(f"credential {name} is not set (expected in {env_file_path(env)})")
    _remember(value)
    return value


# ---- the tripwire: remembered secrets, redaction and the output guard --------------------------

_RUN = 8                                   # the shortest piece of a secret that counts as a leak
_HEX_RUN = 12                              # ... when the piece is made only of 0-9 and a-f
_HEX_CHARS = "0123456789abcdef"
_SHORT_HEX = re.compile(r"(?<![0-9a-f])[0-9a-f]{8,11}(?![0-9a-f])")
_MASK = "<secret>"
_CREDENTIAL_WORDS = ("KEY", "SECRET", "TOKEN", "PASSWORD", "PASS", "ACCOUNT")
_QUOTES = "\"'`"
_TOKEN = re.compile(r"[^\s\"'`]+")

_lock = threading.RLock()
_seen: set[str] = set()                    # a digest of each remembered secret, so each is processed once
_NOTHING: tuple[frozenset[str], frozenset[str], frozenset[str]] = (frozenset(), frozenset(), frozenset())
_pieces = _NOTHING                         # (8-character runs holding a non-hex character, 12-character
                                           #  hex-only runs, short hex-only values), all folded
_count = 0                                 # replacements made in this process


def _looks_like_credential(name: str) -> bool:
    """A name whose value is remembered: it contains KEY, SECRET, TOKEN, PASSWORD or PASS, or
    ACCOUNT (an account number is not a credential, but it is the owner's and stays private)."""
    upper = name.upper()
    return any(word in upper for word in _CREDENTIAL_WORDS)


def _fold(text: str) -> str:
    """Lower case, one character for one character (so positions in the folded text are positions
    in the original)."""
    low = text.lower()
    if len(low) == len(text):
        return low
    return "".join(c.lower() if len(c.lower()) == 1 else c for c in text)


def _base64_pieces(raw: bytes, encode) -> list[str]:
    """The base64 text of a secret on its own, and, for each of the three places it can sit in a
    longer payload, the characters that depend on the secret's bytes alone."""
    pieces = [encode(raw).decode()]
    for shift in (0, 1, 2):
        text = encode(b"\0" * shift + raw).decode().rstrip("=")
        total = shift + len(raw)
        end = len(text) if total % 3 == 0 else total // 3 * 4
        pieces.append(text[4 if shift else 0:end])
    return pieces


def _forms(value: str) -> list[str]:
    """The secret as written, in lower and upper case, URL-encoded, in base64 (standard and
    URL-safe) and in hex."""
    raw = value.encode("utf-8", "replace")
    return ([value, value.lower(), value.upper(), quote(value, safe=""), quote(value), quote_plus(value), raw.hex()]
            + _base64_pieces(raw, base64.b64encode) + _base64_pieces(raw, base64.urlsafe_b64encode))


def _is_hex(run: str) -> bool:
    return not run.strip(_HEX_CHARS)


def _windows(text: str, n: int):
    return (text[i:i + n] for i in range(len(text) - n + 1))


def _pieces_of(value: str) -> tuple[set[str], set[str], set[str]]:
    """The 8-character runs that hold a non-hex character, the 12-character hex-only runs, and the
    value itself when it is a short (8 to 11) hex-only value."""
    mixed: set[str] = set()
    hexed: set[str] = set()
    for form in _forms(value):
        folded = _fold(form)
        mixed.update(run for run in _windows(folded, _RUN) if not _is_hex(run) and not any(c.isspace() for c in run))
        hexed.update(run for run in _windows(folded, _HEX_RUN) if _is_hex(run))
    folded = _fold(value)
    short = {folded} if len(folded) < _HEX_RUN and _is_hex(folded) else set()
    return mixed, hexed, short


def _remember(value) -> None:
    """Remember a credential (8 characters or more) and make sure the output guard is on."""
    global _pieces
    if not isinstance(value, str) or len(value) < _RUN:
        return
    digest = hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()
    with _lock:
        if digest not in _seen:
            _seen.add(digest)
            _pieces = tuple(old | frozenset(new) for old, new in zip(_pieces, _pieces_of(value)))
    install_output_guard()


def _forget_all() -> None:
    """For tests only: forget every remembered secret. The count of replacements is kept."""
    global _pieces
    with _lock:
        _seen.clear()
        _pieces = _NOTHING


def _is_boundary(c: str) -> bool:
    return c.isspace() or c in _QUOTES


def _merge(spans: list[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for a, b in sorted(spans):
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def _hits(folded: str, pieces) -> list[tuple[int, int]]:
    mixed, hexed, short = pieces
    found = [(i, i + _RUN) for i in range(len(folded) - _RUN + 1) if folded[i:i + _RUN] in mixed] if mixed else []
    if hexed:
        found += [(i, i + _HEX_RUN) for i in range(len(folded) - _HEX_RUN + 1) if folded[i:i + _HEX_RUN] in hexed]
    if short:
        found += [m.span() for m in _SHORT_HEX.finditer(folded) if m.group() in short]
    return found


def _spans(text: str, pieces) -> list[tuple[int, int]]:
    """Where pieces of a secret sit, each widened to its whole token (up to whitespace or a quote).
    URL-encoded tokens are also searched after decoding."""
    found = _hits(_fold(text), pieces)
    if "%" in text:
        for m in _TOKEN.finditer(text):
            token = m.group()
            if "%" not in token:
                continue
            for _ in range(3):                       # decodes double and triple encoding too
                decoded = unquote(token)
                if decoded == token:
                    break
                token = decoded
                if _hits(_fold(token), pieces):
                    found.append(m.span())
                    break
    widened = []
    for a, b in _merge(found):
        while a > 0 and not _is_boundary(text[a - 1]):
            a -= 1
        while b < len(text) and not _is_boundary(text[b]):
            b += 1
        widened.append((a, b))
    return _merge(widened)


def redact(text: str) -> tuple[str, int]:
    """Return the text with every token holding a piece (8 characters or more) of a remembered
    secret replaced by `<secret>`, and how many tokens were replaced. A piece counts in any letter
    case and in its URL-encoded, base64 or hex form; URL-encoded text is also searched decoded. A
    piece made only of 0-9 and a-f counts from 12 characters (see the module docstring)."""
    global _count
    pieces = _pieces
    if pieces is _NOTHING or not isinstance(text, str) or len(text) < _RUN:
        return text, 0
    spans = _spans(text, pieces)
    if not spans:
        return text, 0
    parts, last = [], 0
    for a, b in spans:
        parts += [text[last:a], _MASK]
        last = b
    parts.append(text[last:])
    with _lock:
        _count += len(spans)
    return "".join(parts), len(spans)


def redact_json(obj):
    """`redact` over every string inside a JSON-like structure, keys included. Other values are
    checked as the text the journal would write for them (`str`). Returns the structure and the
    number of replacements; when nothing is replaced the very same object comes back."""
    if _pieces is _NOTHING:
        return obj, 0
    return _redact_any(obj)


def _redact_any(obj):
    if isinstance(obj, str):
        return redact(obj)
    if obj is None or isinstance(obj, (bool, int, float)):
        return obj, 0
    if isinstance(obj, dict):
        out, total = {}, 0
        for key, value in obj.items():
            key2, a = _redact_any(key) if isinstance(key, str) else (key, 0)
            if a:
                key2 = _distinct(key2, out, obj)
            value2, b = _redact_any(value)
            out[key2] = value2
            total += a + b
        return (out, total) if total else (obj, 0)
    if isinstance(obj, (list, tuple)):
        items = [_redact_any(x) for x in obj]
        total = sum(n for _, n in items)
        if not total:
            return obj, 0
        values = [x for x, _ in items]
        return (values if isinstance(obj, list) else tuple(values)), total
    try:
        clean, n = redact(str(obj))
    except Exception:
        return obj, 0
    return (clean, n) if n else (obj, 0)


def _distinct(key: str, taken: dict, original: dict) -> str:
    """A redacted key that collides with one already written (or with another key of the record)
    gets its last `<secret>` numbered: `<secret-2>`, `<secret-3>` and so on, so no value is lost."""
    candidate, number = key, 2
    head, _, tail = key.rpartition(_MASK)
    while candidate in taken or candidate in original:
        candidate = f"{head}<secret-{number}>{tail}"
        number += 1
    return candidate


def tripped() -> int:
    """How many replacements have been made in this process. Nothing else about them is kept."""
    return _count


class _GuardedStream:
    """A thin writer in front of a text stream: every write goes through `redact`. An unfinished
    line is held back so a secret split across two writes is still caught; it goes out on the next
    newline, on `flush()` and at exit. Every other attribute is the wrapped stream's own."""

    _HOLD_LIMIT = 1 << 16                     # a line longer than this goes out at its last space

    def __init__(self, stream) -> None:
        self._stream = stream
        self._pending = ""
        self._write_lock = threading.RLock()

    def write(self, text):
        if not isinstance(text, str):
            return self._stream.write(text)
        with self._write_lock:
            data = self._pending + text
            cut = data.rfind("\n") + 1
            if not cut and len(data) > self._HOLD_LIMIT:
                cut = max(data.rfind(" "), data.rfind("\t")) + 1 or len(data)
            self._pending = data[cut:]
            if cut:
                self._stream.write(redact(data[:cut])[0])
        return len(text)

    def writelines(self, lines) -> None:
        for line in lines:
            self.write(line)

    def flush(self) -> None:
        with self._write_lock:
            if self._pending:
                pending, self._pending = self._pending, ""
                self._stream.write(redact(pending)[0])
        flush = getattr(self._stream, "flush", None)
        if flush is not None:
            flush()

    def __getattr__(self, name):
        if name == "_stream":
            raise AttributeError(name)
        return getattr(self._stream, name)


class _RedactingFilter(logging.Filter):
    """Redacts a log record's final message (and its traceback text) before any handler writes it."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            clean, n = redact(record.getMessage())
            if n:
                record.msg, record.args = clean, None
            if record.exc_info and record.exc_info[0] is not None:
                text = record.exc_text or logging.Formatter().formatException(record.exc_info)
                clean, n = redact(text)
                if n:
                    record.exc_text = clean
            if record.stack_info:
                clean, n = redact(record.stack_info)
                if n:
                    record.stack_info = clean
        except Exception:
            pass
        return True


_FILTER = _RedactingFilter()
_filtered_handlers: "weakref.WeakSet[logging.Handler]" = weakref.WeakSet()
_at_exit_registered = False


def install_output_guard() -> None:
    """Wrap sys.stdout and sys.stderr in a redacting writer, and add a redacting filter to the root
    logger and to each of its handlers. Called by itself the first time a secret is remembered;
    calling it again is harmless (nothing is wrapped twice). Text holding no secret passes
    unchanged, only an unfinished line waits for its newline or a flush.

    Not covered: writes made straight to a file descriptor (os.write, sys.stdout.buffer, a
    handler's own file), handlers added to the root logger later that write somewhere other than
    sys.stderr, and child processes, which have streams of their own."""
    global _at_exit_registered
    with _lock:
        for name in ("stdout", "stderr"):
            stream = getattr(sys, name, None)
            if stream is not None and not isinstance(stream, _GuardedStream):
                setattr(sys, name, _GuardedStream(stream))
        root = logging.getLogger()
        root.addFilter(_FILTER)
        for handler in list(root.handlers):
            if _FILTER not in handler.filters:
                handler.addFilter(_FILTER)
                _filtered_handlers.add(handler)
        if not _at_exit_registered:
            atexit.register(_flush_guarded)
            _at_exit_registered = True


def remove_output_guard() -> None:
    """Undo `install_output_guard`: flush what is held back, put the original streams back and take
    the logging filter off. Harmless when the guard is not installed."""
    with _lock:
        _flush_guarded()
        for name in ("stdout", "stderr"):
            stream = getattr(sys, name, None)
            if isinstance(stream, _GuardedStream):
                setattr(sys, name, stream._stream)
        logging.getLogger().removeFilter(_FILTER)
        for handler in list(_filtered_handlers):
            handler.removeFilter(_FILTER)
        _filtered_handlers.clear()


def _flush_guarded() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if isinstance(stream, _GuardedStream):
            try:
                stream.flush()
            except Exception:
                pass
