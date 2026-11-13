"""Credential loading. Secrets live in one owner-only file outside the repository and outside the
general Hermes environment, so only the trading code that asks for them can read them.

Default file: ~/.hermes/secrets/trading.env (override with VALEDIA_ENV_FILE). Values are never
logged, printed or included in exceptions.

A tripwire guards the exits as a second line of defence. Every credential this module hands out
through `secret`, every value handed to `remember`, and every value `load_env` finds under a
credential or account name (loaded now or already in the environment) is remembered, when it is at
least 8 characters long. `load_env` never remembers a path or a URL, a setting (a name ending in
_DIR, _PATH, _FILE, _URL, _HOST, _PORT, _TYPE, _ENV, _MODE, _NAME or _REGION), a value holding
whitespace, or an account value with no digit. `redact` and `redact_json` replace any piece of a
remembered secret in a text or a JSON-like record, and the first time a secret is remembered an
output guard wraps standard output, standard error and every logging handler so that nothing printed
or logged carries one. `child_env` is the environment to give a child process: without the trading
file's variables. `tripped` says how many replacements have been made; nothing else about them is
ever exposed.

A piece made only of the characters 0-9 and a-f counts from 12 characters, every other piece from
8, so ordinary numbers, prices and hashes are never mistaken for a secret. A remembered value of 8
to 11 such characters (a short account number) is caught only whole and standing alone, not inside
a longer run of them. What this gives up: a piece of 8 to 11 characters of a digit-only or hex-only
secret, or of the hex form of any secret, passes.

A secret of 4 words or more (a wallet recovery phrase) is matched as words: any 4 consecutive words
of it, in any letter case, with whitespace or a single comma, hyphen or underscore between them,
are replaced. A word of it with 8 characters or more that is not purely letters also counts alone.
What this gives up: fewer than 4 consecutive words of a phrase pass.

The guard re-checks standard output and standard error at every call to `secret`, `load_env`,
`remember`, `redact`, `redact_json` (so every journal write) and `child_env`, and wraps a stream
that was replaced since; `ensure_output_guard` does the same on demand. A stream replaced between
two such calls is unguarded in between.

Still not covered: writes made straight to a file descriptor (os.write, sys.stdout.buffer,
sys.__stdout__, sys.__stderr__); files other than the journal (paper_state.json, the desk's files,
ledgers, backtest reports); a logging handler that builds its text without `Handler.format`; child
processes' own output; log handlers that send the raw record rather than formatted text (socket,
datagram and HTTP handlers send `extra=` fields as they are); a secret split across two writes
inside one line longer than 64 KB; and the forms that still pass: JSON `\\/` escaping of a slash,
text in which JSON has escaped the secret's own characters (`\\uXXXX`, `\\"`, `\\\\`), account
numbers written grouped or with dashes, a second layer of encoding (base64 of hex, and so on)."""
from __future__ import annotations

import atexit
import base64
import hashlib
import logging
import math
import os
import re
import stat
import sys
import threading
from pathlib import Path
from urllib.parse import quote, quote_plus, unquote

__all__ = ["env_file_path", "load_env", "secret", "SecretError", "DEFAULT_ENV_FILE", "SECRETS_DIR",
           "redact", "redact_json", "tripped", "remember", "child_env", "install_output_guard",
           "remove_output_guard", "ensure_output_guard"]

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
    group or others is refused. A missing file loads nothing (and returns an empty list). A name
    already set is left alone (unless `override`), but its value is remembered all the same."""
    ensure_output_guard()
    env = os.environ if environ is None else environ
    p = Path(path).expanduser() if path else env_file_path(env)
    if not p.is_file():
        return []
    if stat.S_IMODE(p.stat().st_mode) & 0o077:
        raise SecretError(f"{p} is readable by group or others; run chmod 600 on it")
    loaded = []
    for key, value in parse_env(p.read_text()).items():
        _hidden_names.add(key)
        if override or key not in env:
            env[key] = value
            loaded.append(key)
        current = env.get(key, "")
        if _worth_remembering(key, current):
            remember(current)
    return loaded


def secret(name: str, environ=None) -> str:
    """Strict lookup of one credential from the environment."""
    ensure_output_guard()
    env = os.environ if environ is None else environ
    value = env.get(name, "")
    if not value.strip():
        raise SecretError(f"credential {name} is not set (expected in {env_file_path(env)})")
    _hidden_names.add(name)
    remember(value)
    return value


# ---- the tripwire: remembered secrets, redaction and the output guard --------------------------

_RUN = 8                                   # the shortest piece of a secret that counts as a leak
_HEX_RUN = 12                              # ... when the piece is made only of 0-9 and a-f
_HEX_CHARS = "0123456789abcdef"
_SHORT_HEX = re.compile(r"(?<![0-9a-f])[0-9a-f]{8,11}(?![0-9a-f])")
_MASK = "<secret>"
_CREDENTIAL_WORDS = ("KEY", "SECRET", "TOKEN", "PASSWORD", "PASS", "MNEMONIC", "SEED_PHRASE", "RECOVERY_PHRASE", "ACCOUNT")
_SETTING_ENDINGS = ("_DIR", "_PATH", "_FILE", "_URL", "_HOST", "_PORT", "_TYPE", "_ENV", "_MODE", "_NAME", "_REGION")
_QUOTES = "\"'`"
_TOKEN = re.compile(r"[^\s\"'`]+")
_PHRASE_WORDS = 4                          # a secret of this many words or more is matched as words
_WORD = re.compile(r"[^\W_]+")
_WORD_GAP = re.compile(r"\s+|\s*[,_-]\s*")

_lock = threading.RLock()
_seen: set[str] = set()                    # a digest of each remembered secret, so each is processed once
_hidden_names: set[str] = set()            # names from the trading file or served by `secret`: kept from children
_NOTHING: tuple = (frozenset(), frozenset(), frozenset(), frozenset(), None)
_pieces = _NOTHING                         # (8-character runs holding a non-hex character, 12-character
                                           #  hex-only runs, short hex-only values, 4-word runs of phrases,
                                           #  a pattern finding the phrases' words), all folded
_count = 0                                 # replacements made in this process


def _looks_like_credential(name: str) -> bool:
    """A name whose value is remembered: it contains KEY, SECRET, TOKEN, PASSWORD or PASS, or
    ACCOUNT (an account number is not a credential, but it is the owner's and stays private)."""
    upper = name.upper()
    return any(word in upper for word in _CREDENTIAL_WORDS)


def _looks_like_a_place(value: str) -> bool:
    return value.startswith(("/", "~", "./", "../")) or "://" in value


def _worth_remembering(name: str, value: str) -> bool:
    """What `load_env` remembers: a value under a credential or account name that is not a path, a
    URL, a setting, text with whitespace and fewer than 4 words, or (under an account-only name) a
    value with no digit."""
    upper = name.upper()
    if not _looks_like_credential(name) or upper.endswith(_SETTING_ENDINGS):
        return False
    if _looks_like_a_place(value) or (any(c.isspace() for c in value) and len(value.split()) < _PHRASE_WORDS):
        return False
    account_only = not any(word in upper for word in _CREDENTIAL_WORDS if word != "ACCOUNT")
    return not (account_only and not any(c.isdigit() for c in value))


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


def _pieces_of(value: str) -> tuple[set, set, set, set]:
    """The 8-character runs that hold a non-hex character, the 12-character hex-only runs, the value
    itself when it is a short (8 to 11) hex-only value, and, for a phrase of 4 words or more, its
    runs of 4 consecutive words (then only its words of 8 characters or more that are not purely
    letters give ordinary pieces)."""
    if len(value.split()) >= _PHRASE_WORDS:
        words = _WORD.findall(_fold(value))
        grams = {tuple(words[i:i + _PHRASE_WORDS]) for i in range(len(words) - _PHRASE_WORDS + 1)}
        parts: tuple[set, set, set, set] = (set(), set(), set(), grams)
        for word in value.split():
            word = word.strip(",.;:_-")
            if len(word) >= _RUN and not word.isalpha():
                for whole, piece in zip(parts, _pieces_of(word)):
                    whole.update(piece)
        return parts
    mixed: set[str] = set()
    hexed: set[str] = set()
    for form in _forms(value):
        folded = _fold(form)
        mixed.update(run for run in _windows(folded, _RUN) if not _is_hex(run) and not any(c.isspace() for c in run))
        hexed.update(run for run in _windows(folded, _HEX_RUN) if _is_hex(run))
    folded = _fold(value)
    short = {folded} if len(folded) < _HEX_RUN and _is_hex(folded) else set()
    return mixed, hexed, short, set()


def _word_pattern(grams) -> re.Pattern | None:
    words = sorted({w for gram in grams for w in gram}, key=len, reverse=True)
    return re.compile("(?:" + "|".join(map(re.escape, words)) + r")(?![^\W_])") if words else None


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()


def remember(value) -> None:
    """Hand the tripwire a value to keep out of every output (for example an account id read from a
    broker). A number is remembered as its plain decimal text; anything that is neither a string nor
    a number is ignored, and so are values shorter than 8 characters. Turns the output guard on."""
    global _pieces
    ensure_output_guard()
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        value = _plain_number(value)
    if not isinstance(value, str) or len(value) < _RUN:
        return
    digest = _digest(value)
    with _lock:
        if digest not in _seen:
            _seen.add(digest)
            joined = tuple(old | frozenset(new) for old, new in zip(_pieces[:4], _pieces_of(value)))
            _pieces = joined + (_word_pattern(joined[3]),)
    install_output_guard()


def _forget_all() -> None:
    """For tests only: forget every remembered secret and name. The count of replacements is kept."""
    global _pieces
    with _lock:
        _seen.clear()
        _hidden_names.clear()
        _pieces = _NOTHING


def child_env(extra=None) -> dict:
    """The environment for a child process: this process's environment merged with `extra`, without
    every variable loaded from the trading file or served by `secret` (by name) and without any
    variable whose value is, or holds, a remembered secret. Nothing else is removed."""
    ensure_output_guard()
    pieces = _pieces
    out = {}
    for name, value in {**os.environ, **(extra or {})}.items():
        if name in _hidden_names:
            continue
        if pieces is not _NOTHING and isinstance(value, str) and len(value) >= _RUN and \
                (_digest(value) in _seen or _spans(value, pieces)):
            continue
        out[name] = value
    return out


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


def _phrase_hits(folded: str, grams, pattern) -> list[tuple[int, int]]:
    """Runs of 4 consecutive words of a remembered phrase, with whitespace or a single comma, hyphen
    or underscore between them."""
    found, run = [], []
    for m in pattern.finditer(folded):
        start = m.start()
        if start and folded[start - 1].isalnum():          # the end of a longer word
            run = []
            continue
        if run and not _WORD_GAP.fullmatch(folded, run[-1][1], start):
            run = []
        run.append((start, m.end(), m.group()))
        if len(run) >= _PHRASE_WORDS and tuple(w for _, _, w in run[-_PHRASE_WORDS:]) in grams:
            found.append((run[-_PHRASE_WORDS][0], m.end()))
        if len(run) > 2 * _PHRASE_WORDS:
            del run[:-_PHRASE_WORDS]
    return found


def _hits(folded: str, pieces) -> list[tuple[int, int]]:
    mixed, hexed, short, grams, pattern = pieces
    found = [(i, i + _RUN) for i in range(len(folded) - _RUN + 1) if folded[i:i + _RUN] in mixed] if mixed else []
    if hexed:
        found += [(i, i + _HEX_RUN) for i in range(len(folded) - _HEX_RUN + 1) if folded[i:i + _HEX_RUN] in hexed]
    if short:
        found += [m.span() for m in _SHORT_HEX.finditer(folded) if m.group() in short]
    if grams:
        found += _phrase_hits(folded, grams, pattern)
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
    ensure_output_guard()
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
    """`redact` over every string inside a JSON-like structure, keys included. A number (value or
    key) whose plain decimal text `redact` would replace becomes the string `<secret>`; every other
    number is left as it is. Other values are checked as the text the journal would write for them
    (`str`). Returns the structure and the number of replacements; when nothing is replaced the very
    same object comes back."""
    ensure_output_guard()
    if _pieces is _NOTHING:
        return obj, 0
    return _redact_any(obj)


def _plain_number(x) -> str | None:
    """The plain decimal text of a number: digits for an integer (and an integral float), else repr."""
    try:
        if isinstance(x, float):
            if not math.isfinite(x):
                return None
            return str(int(x)) if x.is_integer() and abs(x) < 1e18 else repr(x)
        return str(x)
    except (ValueError, OverflowError):
        return None


def _redact_number(x):
    text = _plain_number(x)
    if text is None or len(text) < _RUN:
        return x, 0
    _, n = redact(text)
    return (_MASK, n) if n else (x, 0)


def _redact_any(obj):
    if isinstance(obj, str):
        return redact(obj)
    if obj is None or isinstance(obj, bool):
        return obj, 0
    if isinstance(obj, (int, float)):
        return _redact_number(obj)
    if isinstance(obj, dict):
        out, total = {}, 0
        for key, value in obj.items():
            if isinstance(key, str):
                key2, a = _redact_any(key)
            elif isinstance(key, (int, float)) and not isinstance(key, bool):
                key2, a = _redact_number(key)
            else:
                key2, a = key, 0
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
    newline or carriage return, on `flush()`, on `close()` and at exit. Everything else (attributes,
    `with`, iteration) behaves as the wrapped stream does."""

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
            cut = max(data.rfind("\n"), data.rfind("\r")) + 1
            if not cut and len(data) > self._HOLD_LIMIT:
                cut = max(data.rfind(" "), data.rfind("\t")) + 1 or len(data)
            self._pending = data[cut:]
            if cut:
                self._stream.write(redact(data[:cut])[0])
        return len(text)

    def writelines(self, lines) -> None:
        for line in lines:
            self.write(line)

    def _emit_pending(self) -> None:
        with self._write_lock:
            if self._pending and not getattr(self._stream, "closed", False):
                pending, self._pending = self._pending, ""
                self._stream.write(redact(pending)[0])

    def flush(self) -> None:
        self._emit_pending()
        flush = getattr(self._stream, "flush", None)
        if flush is not None:
            flush()

    def close(self) -> None:
        self._emit_pending()
        self._stream.close()

    def __enter__(self):
        enter = getattr(self._stream, "__enter__", None)
        if enter is not None:
            enter()                                # the wrapped stream's own checks (a closed stream refuses)
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._stream)

    def __getattr__(self, name):
        if name == "_stream":
            raise AttributeError(name)
        return getattr(self._stream, name)


# ---- logging: every record, through every handler -------------------------------------------------

_logging_on = False
_format_hook = None                        # what Handler.format is while the guard is on
_factory_hook = None                       # the record factory while the guard is on
_saved_format = None                       # what was there before
_saved_factory = None


def _redact_record(record: logging.LogRecord) -> None:
    """Replace secrets in a new record's message (with its arguments), exception text and stack text."""
    try:
        clean, n = redact(record.getMessage())
        if n:
            record.msg, record.args = clean, None
        if record.exc_info and record.exc_info[0] is not None and not record.exc_text:
            clean, n = redact(logging.Formatter().formatException(record.exc_info))
            if n:
                record.exc_text = clean
        if record.stack_info:
            clean, n = redact(record.stack_info)
            if n:
                record.stack_info = clean
    except Exception:
        pass                                   # logging reports its own formatting errors later


def _make_format_hook(previous):
    def format_hook(handler, record):
        text = previous(handler, record)
        return redact(text)[0] if _logging_on else text
    return format_hook


def _make_factory_hook(previous):
    def factory_hook(*args, **kwargs):
        record = previous(*args, **kwargs)
        if _logging_on:
            _redact_record(record)
        return record
    return factory_hook


def _install_logging() -> None:
    global _logging_on, _format_hook, _factory_hook, _saved_format, _saved_factory
    if _format_hook is None or logging.Handler.format is not _format_hook:
        _saved_format = logging.Handler.format
        _format_hook = _make_format_hook(_saved_format)
        logging.Handler.format = _format_hook
    if _factory_hook is None or logging.getLogRecordFactory() is not _factory_hook:
        _saved_factory = logging.getLogRecordFactory()
        _factory_hook = _make_factory_hook(_saved_factory)
        logging.setLogRecordFactory(_factory_hook)
    _logging_on = True


def _remove_logging() -> None:
    global _logging_on, _format_hook, _factory_hook
    _logging_on = False                        # a hook someone else has wrapped since now passes text through
    if _format_hook is not None and logging.Handler.format is _format_hook:
        logging.Handler.format = _saved_format
    if _factory_hook is not None and logging.getLogRecordFactory() is _factory_hook:
        logging.setLogRecordFactory(_saved_factory)
    _format_hook = _factory_hook = None


_at_exit_registered = False
_guard_wanted = False                      # on from the first install until remove_output_guard


def _wrap_streams() -> bool:
    wrapped = False
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and not isinstance(stream, _GuardedStream):
            setattr(sys, name, _GuardedStream(stream))
            wrapped = True
    return wrapped


def ensure_output_guard() -> bool:
    """While any secret is remembered and the guard is on, wrap sys.stdout or sys.stderr again if
    either was replaced since (and put the logging hooks back if they were lost). Returns whether a
    stream had to be wrapped. A stream replaced between two calls is unguarded in between."""
    if not _guard_wanted or _pieces is _NOTHING:
        return False
    out, err = getattr(sys, "stdout", None), getattr(sys, "stderr", None)
    if (out is None or isinstance(out, _GuardedStream)) and (err is None or isinstance(err, _GuardedStream)) and _logging_on:
        return False
    with _lock:
        wrapped = _wrap_streams()
        if not _logging_on:
            _install_logging()
    return wrapped


def install_output_guard() -> None:
    """Wrap sys.stdout and sys.stderr in a redacting writer, and redact every log record from any
    logger through any handler, created before or after: the record factory replaces secrets in the
    message, its arguments, the exception text and the stack text, and `logging.Handler.format`
    (which every standard handler uses to make its text) passes its result through `redact`. Called
    by itself the first time a secret is remembered; calling it again is harmless (nothing is
    wrapped twice). Text holding no secret passes unchanged, only an unfinished line waits for its
    newline, carriage return or a flush.

    Not covered: writes made straight to a file descriptor (os.write, sys.stdout.buffer,
    sys.__stdout__, sys.__stderr__, a handler's own file written without `Handler.format`), a
    handler that formats without the standard path, other files, and child processes, which have
    streams of their own (give them `child_env()`)."""
    global _at_exit_registered, _guard_wanted
    with _lock:
        _guard_wanted = True
        _wrap_streams()
        _install_logging()
        if not _at_exit_registered:
            atexit.register(_flush_guarded)
            _at_exit_registered = True


def remove_output_guard() -> None:
    """Undo `install_output_guard`: flush what is held back, put the original streams back and
    restore logging exactly as it was. Harmless when the guard is not installed."""
    global _guard_wanted
    with _lock:
        _guard_wanted = False
        _flush_guarded()
        for name in ("stdout", "stderr"):
            stream = getattr(sys, name, None)
            if isinstance(stream, _GuardedStream):
                setattr(sys, name, stream._stream)
        _remove_logging()


def _flush_guarded() -> None:
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if isinstance(stream, _GuardedStream):
            try:
                stream.flush()
            except Exception:
                pass
