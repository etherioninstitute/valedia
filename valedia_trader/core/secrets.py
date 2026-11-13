"""Credential loading. Secrets live in one owner-only file outside the repository and outside the
general Hermes environment, so only the trading code that asks for them can read them.

Default file: ~/.hermes/secrets/trading.env (override with VALEDIA_ENV_FILE). Values are never
logged, printed or included in exceptions.

A tripwire guards the exits as a second line of defence. Every credential this module hands out
through `secret`, every value handed to `remember`, and every value `load_env` finds worth
remembering (loaded now or already in the environment) is remembered, when it is at least 8
characters long. `load_env` remembers, under any name, a value of exactly 12, 15, 18, 21 or 24
words of 3 to 8 lower-case letters (a recovery phrase); under a credential name (KEY, SECRET, TOKEN,
PASSWORD, PASS, ACCOUNT) a value without whitespace (an account value only with a digit) and a
value of 4 groups or more that all hold a digit (a spaced number); and under a phrase name
(MNEMONIC, SEED, PHRASE, RECOVERY, WORDS) without a credential word a value with whitespace of 4
words or groups or more. Plain words under a credential name are never remembered unless they have
the recovery-phrase shape. It never remembers a path or a URL, a setting (a name
ending in _DIR, _PATH, _FILE, _URL, _HOST, _PORT, _TYPE, _ENV, _MODE, _NAME or _REGION), or, because
of a phrase name alone, a value without whitespace (see `_worth_remembering`). `redact` and
`redact_json` replace any piece of a
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

A secret of 4 words or more (a wallet recovery phrase) whose runs of letters all have 3 letters or
more is matched as words, a word being a run of letters: any 4 consecutive words of it, in any letter case, with 0 to 6 characters that are not
letters between each two (whitespace and line breaks, punctuation, digits: so a numbered list and
words written together are caught), are replaced. A word of it with 8 characters or more that is
not purely letters also counts alone. What this gives up: fewer than 4 consecutive words of a
phrase pass, and so do words with 7 or more non-letters between them.

A secret of 4 words or more that holds a digit or is not matched as words (groups of digits, or of
letters and digits, such as a card number written in fours) is matched as a sequence of groups, a
group being a run of letters and digits: any 4 consecutive groups, with 0 to 6 other characters
between each two, ending where a group ends, are replaced. Its groups written together count as an
ordinary piece when they make 12 characters or more.

Only on the process's two original screen streams (sys.__stdout__ on file descriptor 1 and
sys.__stderr__ on 2) is text ever held back: there a secret split across two writes, and a phrase
or spaced groups across line breaks and separate writes, are caught, because the end of a line
that could start such a run waits for the next write that settles it (see `_GuardedStream`). Every
other target the guard wraps (a file, a redirect target, an `io.StringIO`, a capture object, a
tee) gets each write redacted on its own and passed on at once. What this gives up: on such a
target a secret, a phrase or spaced groups split across two writes pass.

The guard re-checks standard output and standard error at every call to `secret`, `load_env`,
`remember`, `redact`, `redact_json` (so every journal write) and `child_env`, and wraps a stream
that was replaced since, unless the stream's chain of wrapped streams already reaches a guard;
`ensure_output_guard` does the same on demand. A stream replaced between two such calls is
unguarded in between. `remove_output_guard` inside a redirect leaves the guard the redirect puts
back switched off (it passes text through untouched) until the guard is installed again.

Still not covered: writes made straight to a file descriptor (os.write, sys.stdout.buffer,
sys.__stdout__, sys.__stderr__); files other than the journal (paper_state.json, the desk's files,
ledgers, backtest reports); a logging handler that builds its text without `Handler.format`; child
processes' own output; log handlers that send the raw record rather than formatted text (socket,
datagram and HTTP handlers send `extra=` fields as they are); a secret split across two writes
inside one line longer than 64 KB; and the forms that still pass: JSON `\\/` escaping of a slash,
text in which JSON has escaped the secret's own characters (`\\uXXXX`, `\\"`, `\\\\`), account
numbers written grouped or with dashes, a second layer of encoding (base64 of hex, and so on).

Also still passing, on the screen: an explicit flush between the pieces of a phrase; a phrase
spread over separate log records (each record is flushed); an `input()` prompt between pieces; a
phrase split between standard output and standard error; colour codes, markup, zero-width
characters, accents or look-alike letters inside or around the words; spaced groups that are
numbered (`1. g 2. g`); a held line when the process is killed or ends through `os._exit`; a held
line printed twice after `fork`; and a TOTP secret or an IBAN written in spaced groups under a name
with no credential or phrase word (`load_env` does not remember it)."""
from __future__ import annotations

import atexit
import base64
import bisect
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
_CREDENTIAL_WORDS = ("KEY", "SECRET", "TOKEN", "PASSWORD", "PASS", "ACCOUNT")
_PHRASE_NAME_WORDS = ("MNEMONIC", "SEED", "PHRASE", "RECOVERY", "WORDS")
_SETTING_ENDINGS = ("_DIR", "_PATH", "_FILE", "_URL", "_HOST", "_PORT", "_TYPE", "_ENV", "_MODE", "_NAME", "_REGION")
_QUOTES = "\"'`"
_TOKEN = re.compile(r"[^\s\"'`]+")
_PHRASE_WORDS = 4                          # a secret of this many words or more is matched as words
_PHRASE_LENGTHS = (12, 15, 18, 21, 24)     # word counts of a recovery phrase, remembered under any name
_SHORT_LETTERS = 3                         # a phrase's runs of letters must have at least this many
_GAP = 6                                   # at most this many non-letters between two words of a phrase
_LETTER_RUN = re.compile(r"[^\W\d_]+")
_NON_LETTERS = re.compile(r"[\W\d_]+")
_ALNUM_RUN = re.compile(r"[^\W_]+")
_NON_ALNUM = re.compile(r"[\W_]+")
_KINDS = {                                 # kind of sequence -> (its words, what lies between, must end on a word end)
    "letters": (_LETTER_RUN, _NON_LETTERS, False),   # a phrase: runs of letters
    "groups": (_ALNUM_RUN, _NON_ALNUM, True),         # spaced groups of digits, or of letters and digits
}

_lock = threading.RLock()                  # never held while a stream is written or flushed
_count_lock = threading.Lock()             # guards `_count` only; nothing is called while it is held
_seen: set[str] = set()                    # a digest of each remembered secret, so each is processed once
_hidden_names: set[str] = set()            # names from the trading file or served by `secret`: kept from children
_NOTHING: tuple = (frozenset(), frozenset(), frozenset(), None, ())
_pieces = _NOTHING                         # (8-character runs holding a non-hex character, 12-character
                                           #  hex-only runs, short hex-only values, the phrase index (see
                                           #  `_phrase_index`) or None, the phrases' words), all folded
_count = 0                                 # replacements made in this process


def _looks_like_credential(name: str) -> bool:
    """A name whose value `load_env` may remember: it contains KEY, SECRET, TOKEN, PASSWORD or PASS,
    or ACCOUNT (an account number is not a credential, but it is the owner's and stays private), or
    one of the phrase words MNEMONIC, SEED, PHRASE, RECOVERY or WORDS (a wallet recovery phrase).
    Which values under such a name are remembered is `_worth_remembering`'s rule."""
    upper = name.upper()
    return any(word in upper for word in _CREDENTIAL_WORDS + _PHRASE_NAME_WORDS)


def _looks_like_a_place(value: str) -> bool:
    return value.startswith(("/", "~", "./", "../")) or "://" in value


def _worth_remembering(name: str, value: str) -> bool:
    """What `load_env` remembers of a value it takes from the file. Under any name at all: a value of
    exactly 12, 15, 18, 21 or 24 words, each of 3 to 8 lower-case letters (a recovery phrase). Then
    only under a credential name or a phrase name (see `_looks_like_credential`), and never a path,
    a URL or a setting (a name ending in a setting ending): a value with whitespace of 4 words or
    groups or more, under a credential name only when every group holds a digit (a spaced number, or
    spaced letters and digits: plain words are not remembered there); a value without whitespace
    only under a credential name, and under an account-only name only when it has a digit. A phrase name alone never makes a value without
    whitespace remembered (so MNEMONIC_LANGUAGE=japanese, SEED_DATE=2024-01-01 and RANDOM_SEED are
    left alone)."""
    words = value.split()
    spaced = any(c.isspace() for c in value)
    if spaced and len(words) in _PHRASE_LENGTHS and all(3 <= len(w) <= 8 and w.isalpha() and w.islower() for w in words):
        return True
    upper = name.upper()
    credential = any(word in upper for word in _CREDENTIAL_WORDS)
    phrase_name = any(word in upper for word in _PHRASE_NAME_WORDS)
    if not (credential or phrase_name) or upper.endswith(_SETTING_ENDINGS) or _looks_like_a_place(value):
        return False
    if spaced:
        spaced_number = all(w.isalnum() and any(c.isdigit() for c in w) for w in words)
        return len(words) >= _PHRASE_WORDS and (spaced_number or not credential)
    if not credential:
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


def _pieces_of(value: str) -> tuple[set, set, set, list]:
    """The 8-character runs that hold a non-hex character, the 12-character hex-only runs, the value
    itself when it is a short (8 to 11) hex-only value, and the sequences of a value of 4
    whitespace-separated words or more: its runs of letters when it has 4 or more (a phrase), and,
    when it holds a digit, its runs of letters and digits when it has 4 or more (spaced groups). Then
    only its words of 8 characters or more that are not purely letters give ordinary pieces, and,
    for spaced groups, their form written together when it has 12 characters or more (its runs of
    letters only left out)."""
    folded = _fold(value)
    words, groups = _LETTER_RUN.findall(folded), _ALNUM_RUN.findall(folded)
    if len(value.split()) >= _PHRASE_WORDS and max(len(words), len(groups)) >= _PHRASE_WORDS:
        as_letters = len(words) >= _PHRASE_WORDS and all(len(w) >= _SHORT_LETTERS for w in words)
        sequences = [("letters", tuple(words))] if as_letters else []
        if len(groups) >= _PHRASE_WORDS and (not as_letters or any(c.isdigit() for c in folded)):
            sequences.append(("groups", tuple(groups)))
        parts: tuple[set, set, set, list] = (set(), set(), set(), sequences)
        for word in value.split():
            word = word.strip(",.;:_-")
            if len(word) >= _RUN and not word.isalpha():
                for whole, piece in zip(parts[:3], _pieces_of(word)):
                    whole.update(piece)
        joined = "".join(_ALNUM_RUN.findall(value))
        if sequences and sequences[-1][0] == "groups" and len(joined) >= _HEX_RUN:
            mixed, hexed, short, _ = _pieces_of(joined)
            parts[0].update(run for run in mixed if not run.isalpha())
            parts[1].update(hexed)
            parts[2].update(short)
        return parts
    mixed: set[str] = set()
    hexed: set[str] = set()
    for form in _forms(value):
        folded = _fold(form)
        mixed.update(run for run in _windows(folded, _RUN) if not _is_hex(run) and not any(c.isspace() for c in run))
        hexed.update(run for run in _windows(folded, _HEX_RUN) if _is_hex(run))
    folded = _fold(value)
    short = {folded} if len(folded) < _HEX_RUN and _is_hex(folded) else set()
    return mixed, hexed, short, []


def _phrase_index(sequences):
    """For the remembered sequences (phrases and spaced groups), one entry per kind: the kind; every
    run of 4 consecutive words written together, mapped to where its inner word boundaries fall;
    every run of 1 to 3 consecutive words that a further word of the same sequence follows, written
    together (an end of text worth holding back); and the longest such. None when there are none."""
    entries = []
    for kind in _KINDS:
        grams: dict[str, frozenset] = {}
        tails: set[str] = set()
        for words in (w for k, w in sequences if k == kind):
            for i in range(len(words) - _PHRASE_WORDS + 1):
                gram = words[i:i + _PHRASE_WORDS]
                bounds = frozenset(sum(map(len, gram[:k])) for k in range(1, _PHRASE_WORDS))
                joined = "".join(gram)
                grams[joined] = grams.get(joined, frozenset()) | bounds
            for i in range(len(words) - 1):
                for j in range(i + 1, min(i + _PHRASE_WORDS - 1, len(words) - 1) + 1):
                    tails.add("".join(words[i:j]))
        if grams:
            entries.append((kind, grams, frozenset(tails), max(map(len, tails), default=0)))
    return tuple(entries) or None


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
            mixed, hexed, short, phrases = _pieces_of(value)
            old = _pieces
            words = old[4] + tuple(p for p in phrases if p not in old[4])
            _pieces = (old[0] | mixed, old[1] | hexed, old[2] | short, _phrase_index(words), words)
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


def _phrase_hits(folded: str, entry) -> list[tuple[int, int]]:
    """Runs of 4 consecutive words of a remembered sequence. For a phrase: words are runs of
    letters, with 0 to 6 characters that are not letters between each two (whitespace, punctuation,
    digits: a numbered list, words written together). For spaced groups: words are runs of letters
    and digits, with 0 to 6 characters that are neither between each two, and the run must end where
    a run of letters and digits ends. A run starts where such a run starts; inside it, the runs may
    break only where one of its words ends."""
    kind, grams = entry[0], entry[1]
    run_of, between, whole_end = _KINDS[kind]
    letters = between.sub("", folded)
    present = [g for g in grams if g in letters]
    if not present:
        return []
    runs = [m.span() for m in run_of.finditer(folded)]
    offsets, total = [], 0
    for a, b in runs:
        offsets.append(total)
        total += b - a
    found = []
    for gram in present:
        bounds = grams[gram]
        i = letters.find(gram)
        while i >= 0:
            k = bisect.bisect_right(offsets, i) - 1
            if offsets[k] == i:
                end, r = i + len(gram), k + 1
                while r < len(runs) and offsets[r] < end:
                    if runs[r][0] - runs[r - 1][1] > _GAP or offsets[r] - i not in bounds:
                        break
                    r += 1
                else:
                    stop = runs[r - 1][0] + end - offsets[r - 1]
                    if not whole_end or stop == runs[r - 1][1]:
                        found.append((runs[k][0], stop))
            i = letters.find(gram, i + 1)
    return found


def _hits(folded: str, pieces) -> list[tuple[int, int]]:
    mixed, hexed, short, index, _ = pieces
    found = [(i, i + _RUN) for i in range(len(folded) - _RUN + 1) if folded[i:i + _RUN] in mixed] if mixed else []
    if hexed:
        found += [(i, i + _HEX_RUN) for i in range(len(folded) - _HEX_RUN + 1) if folded[i:i + _HEX_RUN] in hexed]
    if short:
        found += [m.span() for m in _SHORT_HEX.finditer(folded) if m.group() in short]
    for entry in index or ():
        found += _phrase_hits(folded, entry)
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
    ensure_output_guard()
    return _redact_text(text, _pieces)


def _redact_text(text, pieces) -> tuple[str, int]:
    """`redact` without the stream check (what the guarded streams use while they write)."""
    if pieces is _NOTHING or not isinstance(text, str) or len(text) < _RUN:
        return text, 0
    spans = _spans(text, pieces)
    if not spans:
        return text, 0
    return _masked(text, spans), len(spans)


def _masked(text: str, spans) -> str:
    global _count
    parts, last = [], 0
    for a, b in spans:
        parts += [text[last:a], _MASK]
        last = b
    parts.append(text[last:])
    with _count_lock:
        _count += len(spans)
    return "".join(parts)


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


def _is_screen(stream) -> bool:
    """Whether the stream is one of the process's two original screen streams: sys.__stdout__ on file
    descriptor 1 or sys.__stderr__ on file descriptor 2."""
    for original, fd in ((sys.__stdout__, 1), (sys.__stderr__, 2)):
        if stream is not None and stream is original:
            try:
                return stream.fileno() == fd
            except Exception:
                return False
    return False


def _tail_start(folded: str, start: int, index) -> int:
    """Where the end of `folded` (from `start` on) begins to be 1 to 3 consecutive words of a
    remembered sequence that a further word of it could continue (followed by at most 6 characters
    between words), or len(folded) when it is not."""
    return min(_tail_start_of(folded, start, entry) for entry in index)


def _tail_start_of(folded: str, start: int, entry) -> int:
    kind, _, tails, longest = entry
    run_of = _KINDS[kind][0]
    lo = max(start, len(folded) - (longest + 1) * (_GAP + 1) - _GAP)
    runs = [m.span() for m in run_of.finditer(folded, lo)]
    best = len(folded)
    if not runs or len(folded) - runs[-1][1] > _GAP:
        return best
    suffix = ""
    for k in range(len(runs) - 1, -1, -1):
        a, b = runs[k]
        if k < len(runs) - 1 and runs[k + 1][0] - b > _GAP:
            break
        if a == lo and lo > start and run_of.match(folded, lo - 1):
            break                                  # a word cut off by the window
        suffix = folded[a:b] + suffix
        if len(suffix) > longest:
            break
        if suffix in tails:
            best = a
    return best


class _GuardedStream:
    """A thin writer in front of a text stream: every write goes through `redact`.

    Only in front of the process's two original screen streams (see `_is_screen`) is any text held
    back: an unfinished line, so a secret split across two writes is still caught, and, while a
    phrase or spaced groups are remembered, the end of a line that is 1 to 3 consecutive words of one
    that another word of it could continue (one word per line, three words and a break, a word per
    write), so the run is replaced whole once it is complete. Held text goes out with the next write
    that settles it, on `flush()`, on `close()`, when the guard is removed and at exit. A line that
    does not end in such words goes out before `write` returns, and so does a line ending in a
    carriage return.

    In front of anything else (a file, a redirect target, an `io.StringIO`, a capture object, a
    tee) nothing is ever held: each write is redacted on its own and passed on in the same call. What
    this gives up: there a secret, a phrase or spaced groups split across two writes pass.

    While the guard is off (after `remove_output_guard`) every guard passes text through untouched;
    installing the guard again switches them all back on. Everything else (attributes, `with`,
    iteration) behaves as the wrapped stream does."""

    _HOLD_LIMIT = 1 << 16                     # a line longer than this goes out at its last space

    def __init__(self, stream, holds: bool = False) -> None:
        self._stream = stream
        self._holds = holds
        self._pending = ""
        self._write_lock = threading.RLock()

    def write(self, text):
        if not isinstance(text, str):
            return self._stream.write(text)
        if not self._holds:
            self._stream.write(_redact_text(text, _pieces)[0] if _guard_wanted else text)
            return len(text)
        with self._write_lock:
            if _guard_wanted:
                self._write_held(self._pending + text, _pieces)
            else:
                self._send_pending()
                self._stream.write(text)
        return len(text)

    def _write_held(self, data: str, pieces) -> None:
        cut = max(data.rfind("\n"), data.rfind("\r")) + 1
        if not cut and len(data) > self._HOLD_LIMIT:
            cut = max(data.rfind(" "), data.rfind("\t")) + 1 or len(data)
        if not cut:
            self._pending = data
            return
        head = data[:cut]
        if pieces[3] is None:
            self._pending = data[cut:]
            self._stream.write(_redact_text(head, pieces)[0])
            return
        spans = _spans(head, pieces) if len(head) >= _RUN else []
        keep = _tail_start(_fold(head), spans[-1][1] if spans else 0, pieces[3])
        self._pending = head[keep:] + data[cut:]
        if keep:
            self._stream.write(_masked(head[:keep], spans) if spans else head[:keep])

    def writelines(self, lines) -> None:
        for line in lines:
            self.write(line)

    def _send_pending(self) -> None:
        """With the write lock held: send on what is held, redacted."""
        if self._pending and not getattr(self._stream, "closed", False):
            pending, self._pending = self._pending, ""
            self._stream.write(_redact_text(pending, _pieces)[0])

    def _emit_pending(self, timeout: float = -1) -> None:
        if not self._holds or not self._write_lock.acquire(timeout=timeout):
            return
        try:
            self._send_pending()
        finally:
            self._write_lock.release()

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


_CHAIN = ("_stream", "stream", "wrapped", "_wrapped")   # where a wrapper keeps the stream it writes to
_CHAIN_DEPTH = 8
_screens: list = []                        # the guards of the two screen streams, made once and kept


def _chain(stream):
    """The stream and the streams it wraps, following the guard's own attribute and the common
    names (`stream`, `wrapped`, `_wrapped`) to a depth of 8."""
    for _ in range(_CHAIN_DEPTH + 1):
        if stream is None:
            return
        yield stream
        held = getattr(stream, "__dict__", None)
        if not isinstance(held, dict):
            return
        stream = next((held[n] for n in _CHAIN if held.get(n) is not None), None)


def _guard_in(stream):
    return next((s for s in _chain(stream) if isinstance(s, _GuardedStream)), None)


def _wrap_streams() -> bool:
    """With `_lock` held: wrap sys.stdout and sys.stderr unless their chain already reaches a
    guard. A screen stream gets its one guard back (so what it holds is never dropped); anything
    else gets a guard that holds nothing. Returns whether a stream was wrapped."""
    wrapped = False
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None or _guard_in(stream) is not None:
            continue
        guard = None
        if _is_screen(stream):
            guard = next((g for g in _screens if g._stream is stream), None)
            if guard is None:
                guard = _GuardedStream(stream, holds=True)
                _screens.append(guard)
        setattr(sys, name, guard or _GuardedStream(stream))
        wrapped = True
    return wrapped


def ensure_output_guard() -> bool:
    """While any secret is remembered and the guard is on, wrap sys.stdout or sys.stderr again if
    either was replaced since (and put the logging hooks back if they were lost). A stream whose
    chain of wrapped streams already reaches a guard (see `_chain`) is not wrapped again. Returns
    whether a stream had to be wrapped. A stream replaced between two calls is unguarded in between."""
    if not _guard_wanted or _pieces is _NOTHING:
        return False
    out, err = getattr(sys, "stdout", None), getattr(sys, "stderr", None)
    if _logging_on and (out is None or _guard_in(out)) and (err is None or _guard_in(err)):
        return False
    with _lock:
        if not _guard_wanted:
            return False
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
    wrapped twice). Text holding no secret passes unchanged; only on the two screen streams does an
    unfinished line wait for its newline, carriage return or a flush.

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
    """Undo `install_output_guard`: put the original streams back where sys.stdout or sys.stderr is
    a guard, switch every guard off, send on what the screen guards hold and flush them, and
    restore logging exactly as it was. Harmless when the guard is not installed.

    Called inside a redirect (`contextlib.redirect_stdout` and the like), it cannot reach the stream
    the redirect will put back when it ends: if that stream is a guard, the guard stays in place but
    off, passing everything through untouched, until the guard is installed again (which switches it
    back on rather than wrapping it a second time)."""
    global _guard_wanted
    with _lock:
        _guard_wanted = False
        for name in ("stdout", "stderr"):
            stream = getattr(sys, name, None)
            if isinstance(stream, _GuardedStream):
                setattr(sys, name, stream._stream)
        screens = list(_screens)
        _screens[:] = [g for g in screens if g._stream is sys.__stdout__ or g._stream is sys.__stderr__]
        _remove_logging()
    for guard in screens:                          # outside `_lock`: a writer may hold a guard's lock
        try:
            guard.flush()
        except Exception:
            pass


def _flush_guarded() -> None:
    """At exit: send on what the screen guards still hold (waiting at most a second for a busy writer)."""
    for guard in list(_screens):
        try:
            guard._emit_pending(timeout=1.0)
            flush = getattr(guard._stream, "flush", None)
            if flush is not None:
                flush()
        except Exception:
            pass
