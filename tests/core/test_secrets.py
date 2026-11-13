import os

import pytest

from valedia_trader.core import secrets as s


def test_parse_and_load_respects_existing_values(tmp_path):
    f = tmp_path / "trading.env"
    f.write_text("# comment\nexport A_KEY='abc123'\nB_SECRET=\"xyz\"\nbad line\n9BAD=1\nC=\n")
    os.chmod(f, 0o600)
    env = {"B_SECRET": "already-set"}
    assert s.load_env(f, environ=env) == ["A_KEY", "C"]
    assert env == {"B_SECRET": "already-set", "A_KEY": "abc123", "C": ""}
    assert s.load_env(f, environ=env, override=True) == ["A_KEY", "B_SECRET", "C"] and env["B_SECRET"] == "xyz"
    assert s.load_env(tmp_path / "missing.env", environ={}) == []


def test_world_readable_file_is_refused(tmp_path):
    f = tmp_path / "trading.env"
    f.write_text("A_KEY=topsecretvalue\n")
    os.chmod(f, 0o644)
    with pytest.raises(s.SecretError) as err:
        s.load_env(f, environ={})
    assert "chmod 600" in str(err.value) and "topsecretvalue" not in str(err.value)


def test_strict_lookup_names_the_variable_not_the_value(tmp_path):
    env = {"VALEDIA_ENV_FILE": str(tmp_path / "x.env"), "PRESENT": "v"}
    assert s.secret("PRESENT", env) == "v" and s.env_file_path(env) == tmp_path / "x.env"
    with pytest.raises(s.SecretError, match="MISSING_KEY"):
        s.secret("MISSING_KEY", env)
    assert s.env_file_path({}) == s.DEFAULT_ENV_FILE and s.DEFAULT_ENV_FILE.name == "trading.env"


# ---- the tripwire: redaction and the output guard -------------------------------------------
# Every secret below is made up inside the test. Assertions compare booleans and counts only, so a
# failure never prints a value (pytest shows the arguments of a call inside an assert, so the
# checks are computed on a line of their own first).

import base64  # noqa: E402
import io  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import random  # noqa: E402
import string  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
import traceback  # noqa: E402
from urllib.parse import quote, quote_plus  # noqa: E402


def _made_up(rng, n=32, alphabet=string.ascii_letters + string.digits):
    return "".join(rng.choice(alphabet) for _ in range(n))


def _pieces(value, n=8):
    low = value.lower()
    return {low[i:i + n] for i in range(len(low) - n + 1)}


def _leaks(text, *values):
    """True when any 8-character run of any value appears in the text, ignoring case."""
    low = text.lower()
    return any(p in low for v in values for p in _pieces(v))


def _remember(value):
    s.secret("MADE_UP_KEY", {"MADE_UP_KEY": value})


def _forms(value):
    raw = value.encode()
    return {"whole": value, "cut12": value[7:19], "upper": value.upper(), "lower": value.lower(),
            "swapped": value.swapcase(), "url": quote(value, safe=""), "url_plus": quote_plus(value),
            "b64": base64.b64encode(raw).decode(), "b64url": base64.urlsafe_b64encode(raw).decode(),
            "hex": raw.hex(), "HEX": raw.hex().upper()}


def test_short_values_are_not_remembered_and_install_nothing(tmp_path):
    f = tmp_path / "trading.env"
    f.write_text("SHORT_KEY=Ab3dE6g\n")
    os.chmod(f, 0o600)
    before_out, before_err = sys.stdout, sys.stderr
    s.load_env(f, environ={})
    s.secret("OTHER_TOKEN", {"OTHER_TOKEN": "Zy9xW8v"})
    out, n = s.redact("value Ab3dE6g and Zy9xW8v here")
    unchanged = out == "value Ab3dE6g and Zy9xW8v here"
    assert unchanged and n == 0
    assert sys.stdout is before_out and sys.stderr is before_err


def test_load_env_remembers_credential_names_only(tmp_path):
    rng = random.Random(1)
    key, plain = _made_up(rng), _made_up(rng)
    f = tmp_path / "trading.env"
    f.write_text(f"BROKER_API_KEY={key}\nREPORT_TITLE={plain}\n")
    os.chmod(f, 0o600)
    s.load_env(f, environ={})
    _, n_key = s.redact(f"a {key} b")
    out, n_plain = s.redact(f"a {plain} b")
    plain_kept = out == f"a {plain} b"
    assert n_key == 1 and n_plain == 0 and plain_kept


@pytest.mark.parametrize("form", sorted(_forms("x" * 40)))
def test_every_planted_form_is_replaced_with_its_whole_token(form):
    rng = random.Random(form)
    value = _made_up(rng, 36, string.ascii_letters + string.digits + "/+=-_.~")
    _remember(value)
    planted = _forms(value)[form]
    out, n = s.redact(f"before key={planted}, after\nnext line")
    leaked = _leaks(out, value, planted)
    kept = out == "before <secret> after\nnext line"
    assert not leaked and n == 1 and kept


def test_a_bearer_header_and_a_query_string_lose_only_the_secret_token():
    value = _made_up(random.Random(2), 40)
    _remember(value)
    out, n = s.redact(f'Authorization: Bearer {value}\nGET /v1/x?key={quote(value)}&page=2 HTTP/1.1\n"id": "{value}"')
    kept = out == 'Authorization: Bearer <secret>\nGET <secret> HTTP/1.1\n"id": "<secret>"'
    assert kept and n == 3


def test_secret_inside_basic_auth_base64_is_caught_at_any_alignment():
    value = _made_up(random.Random(3), 30)
    _remember(value)
    for user in ("u", "us", "usr", "user"):
        token = base64.b64encode(f"{user}:{value}".encode()).decode()
        out, n = s.redact(f"Authorization: Basic {token}")
        leaked = _leaks(out, value, token)
        assert not leaked and n == 1


def test_redact_json_covers_nested_values_and_keys():
    value = _made_up(random.Random(4), 32)
    _remember(value)
    record = {"type": "decision", "venue": {"name": "x", "error": f"HTTP 401 for key {value}",
                                             "headers": [{"k": value.upper()}, ("ok", base64.b64encode(value.encode()).decode())]},
              f"key-{value}": 1, "price": 1.5, "units": -3, "flag": None, "nested": [[{"deep": f"x{value[3:15]}y"}]]}
    out, n = s.redact_json(record)
    text = json.dumps(out)
    leaked = _leaks(text, value)
    assert not leaked and n == 5
    kept = (out["type"], out["price"], out["units"], out["flag"], out["venue"]["name"]) == ("decision", 1.5, -3, None, "x")
    assert kept and "<secret>" in out and isinstance(out["venue"]["headers"][1], tuple)


def test_redact_json_returns_the_same_object_when_nothing_matches():
    _remember(_made_up(random.Random(5), 32))
    record = {"a": [1, 2.5, {"b": "plain words of more than eight letters"}], "c": None}
    out, n = s.redact_json(record)
    assert out is record and n == 0


def test_tripped_counts_every_replacement_and_nothing_else():
    value = _made_up(random.Random(6), 24)
    _remember(value)
    start = s.tripped()
    s.redact("nothing here at all")
    s.redact(f"one {value} two {value.lower()}")
    s.redact_json({"k": value, value: "v"})
    assert s.tripped() - start == 4 and isinstance(s.tripped(), int)


def test_random_secrets_in_several_formats_for_200_rounds():
    rng = random.Random(20261112)
    alphabets = [string.ascii_letters + string.digits, string.hexdigits.lower(), string.ascii_uppercase + string.digits,
                 string.ascii_letters + string.digits + "-_", string.printable[:94]]
    misses = 0
    for _ in range(200):
        s._forget_all()
        value = _made_up(rng, rng.randint(8, 64), rng.choice(alphabets))
        if rng.random() < 0.3:
            value = "PK" + value.upper()
        _remember(value)
        forms = _forms(value)
        if len(value) < 19:
            forms.pop("cut12")
        name = rng.choice(sorted(forms))
        planted = forms[name]
        filler = " ".join(_made_up(rng, rng.randint(1, 12), string.ascii_lowercase) for _ in range(rng.randint(0, 6)))
        text = f"{filler} {rng.choice(['', 'k=', 'Bearer ', '?key='])}{planted} {filler}"
        out, n = s.redact(text)
        misses += _leaks(out, value, planted) or n < 1
    assert misses == 0


def test_guard_redacts_print_stderr_and_a_traceback(capsys):
    value = _made_up(random.Random(7), 32)
    _remember(value)                                         # installs the guard by itself
    print(f"order rejected for key {value}")
    print(f"warning: {quote(value, safe='')}", file=sys.stderr)
    try:
        raise RuntimeError(f"broker said no to {value}")
    except RuntimeError:
        sys.__excepthook__(*sys.exc_info())
        traceback.print_exc(file=sys.stderr)
    out, err = capsys.readouterr()
    leaked = _leaks(out + err, value)
    assert not leaked and "<secret>" in out and err.count("<secret>") == 3 and "RuntimeError" in err


def test_guard_catches_a_secret_split_across_two_writes(capsys):
    value = _made_up(random.Random(8), 32)
    _remember(value)
    sys.stdout.write("key " + value[:13])
    sys.stdout.write(value[13:] + " end\n")
    sys.stdout.write("tail without newline")
    sys.stdout.flush()
    out, _ = capsys.readouterr()
    leaked = _leaks(out, value)
    kept = out == "key <secret> end\ntail without newline"
    assert not leaked and kept


def test_guard_redacts_logging_through_the_root_logger_and_children():
    value = _made_up(random.Random(9), 32)
    sink = logging.StreamHandler(io.StringIO())
    root = logging.getLogger()
    root.addHandler(sink)
    try:
        _remember(value)
        logging.getLogger("valedia_trader.tripwire_test").warning("login with %s failed", value)
        root.warning("raw %s", value.upper())
        try:
            raise ValueError(f"token {value}")
        except ValueError:
            logging.getLogger("valedia_trader.tripwire_test").exception("call failed")
        text = sink.stream.getvalue()
        leaked = _leaks(text, value)
        assert not leaked and text.count("<secret>") == 3 and "call failed" in text
        s.remove_output_guard()
        assert not sink.filters and not root.filters
    finally:
        root.removeHandler(sink)


class _Stream:
    encoding = "latin-1"
    errors = "strict"
    mode = "w"

    def __init__(self):
        self.parts = []
        self.flushes = 0

    def write(self, text):
        self.parts.append(text)
        return len(text)

    def flush(self):
        self.flushes += 1

    def isatty(self):
        return True

    def fileno(self):
        return 97


def test_guard_keeps_the_stream_attributes_installs_once_and_removes_cleanly(monkeypatch):
    out, err = _Stream(), _Stream()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    s.install_output_guard()
    first = sys.stdout
    s.install_output_guard()
    assert sys.stdout is first and first is not out
    assert sys.stdout.isatty() and sys.stdout.fileno() == 97 and sys.stdout.encoding == "latin-1" and sys.stdout.mode == "w"
    sys.stdout.write("plain text, no secret here\n")
    sys.stdout.writelines(["a", "b\n"])
    sys.stdout.write("held")
    assert out.parts == ["plain text, no secret here\n", "ab\n"]
    sys.stdout.flush()
    assert out.parts[-1] == "held" and out.flushes == 1
    s.remove_output_guard()
    assert sys.stdout is out and sys.stderr is err


def test_redacting_a_2kb_record_with_10_secrets_is_fast():
    rng = random.Random(10)
    for _ in range(10):
        _remember(_made_up(rng, 40))
    record = {"type": "decision", "reasoning": " ".join(_made_up(rng, rng.randint(2, 12), string.ascii_lowercase) for _ in range(200)),
              "market_conditions": {"regime": "calm", "volatility_20d": 0.18, "return_20d": 0.01},
              "order": {"symbol": "SPY", "units": 12.5, "notes": [_made_up(rng, 30) for _ in range(20)]}}
    assert 1800 <= len(json.dumps(record)) <= 2600
    timings = []
    for _ in range(50):
        t0 = time.perf_counter()
        s.redact_json(record)
        timings.append(time.perf_counter() - t0)
    timings.sort()
    assert timings[len(timings) // 2] < 0.002


def test_account_identifiers_are_remembered_with_the_same_minimum(tmp_path):
    rng = random.Random(11)
    account, short = _made_up(rng, 12, string.digits), _made_up(rng, 7, string.digits)
    f = tmp_path / "trading.env"
    f.write_text(f"WEBULL_ACCOUNT_ID={account}\nOTHER_ACCOUNT={short}\n")
    os.chmod(f, 0o600)
    s.load_env(f, environ={})
    out, n = s.redact(f"account {account} and {short} here")
    kept = out == f"account <secret> and {short} here"
    assert n == 1 and kept


def test_two_redacted_keys_of_one_record_stay_distinct_and_keep_their_values():
    rng = random.Random(12)
    one, two = _made_up(rng, 30), _made_up(rng, 30)
    _remember(one)
    _remember(two)
    record = {"plain": 0, one: "first", f"x {two}": "skip", two.upper(): "second", "<secret-2>": "literal",
              "inner": {one: 1, two: 2}}
    out, n = s.redact_json(record)
    text = json.dumps(out)
    leaked = _leaks(text, one) or _leaks(text, two)
    assert not leaked and n == 5
    assert list(out) == ["plain", "<secret>", "x <secret>", "<secret-3>", "<secret-2>", "inner"]
    assert [out[k] for k in list(out)[:5]] == [0, "first", "skip", "second", "literal"]
    assert out["inner"] == {"<secret>": 1, "<secret-2>": 2}


_LEFT_ON = logging.NullHandler()
_PLAIN_FORMAT = logging.Handler.format                     # taken at import, before any test turns the guard on
_PLAIN_FACTORY = logging.getLogRecordFactory()


def test_a_test_may_leave_the_guard_on():
    logging.getLogger().addHandler(_LEFT_ON)                 # taken off again by the next test
    _remember(_made_up(random.Random(13), 32))               # the suite-wide fixture must clean this up
    assert isinstance(sys.stdout, s._GuardedStream) and logging.Handler.format is not _PLAIN_FORMAT


def test_the_next_test_starts_with_no_guard_and_no_remembered_secret():
    root = logging.getLogger()
    try:
        clean_streams = not isinstance(sys.stdout, s._GuardedStream) and not isinstance(sys.stderr, s._GuardedStream)
        clean_logging = (not _LEFT_ON.filters and logging.Handler.format is _PLAIN_FORMAT
                         and logging.getLogRecordFactory() is _PLAIN_FACTORY)
        _, n = s.redact(_made_up(random.Random(13), 32))
    finally:
        root.removeHandler(_LEFT_ON)
    assert clean_streams and clean_logging and n == 0


def test_a_hex_only_piece_counts_from_12_characters_and_any_other_piece_from_8():
    rng = random.Random(14)
    hex_value = _made_up(rng, 40, "0123456789abcdef")
    word = _made_up(rng, 40, "ghijklmnopqrstuvwxyz")
    _remember(hex_value)
    _remember(word)
    _, n11 = s.redact(f"id {hex_value[5:16]} end")
    _, n12 = s.redact(f"id {hex_value[5:17].upper()} end")
    hex_form = word.encode().hex()
    _, h11 = s.redact(f"fp {hex_form[3:14]} end")
    _, h12 = s.redact(f"fp {hex_form[3:15]} end")
    _, w8 = s.redact(f"w {word[9:17]} end")
    assert (n11, n12, h11, h12, w8) == (0, 1, 0, 1, 1)


def test_a_short_digit_or_hex_value_is_caught_only_standing_alone(tmp_path):
    rng = random.Random(15)
    account = _made_up(rng, 8, string.digits)
    f = tmp_path / "trading.env"
    f.write_text(f"WEBULL_ACCOUNT_ID={account}\n")
    os.chmod(f, 0o600)
    s.load_env(f, environ={})
    _, alone = s.redact(f"account {account} here")
    _, punctuated = s.redact(f"acct={account}. and x{account}")
    out, inside = s.redact(f"order 9{account}1 and {account}5 and a{account}")
    unchanged = out == f"order 9{account}1 and {account}5 and a{account}"
    assert (alone, punctuated, inside) == (1, 2, 0) and unchanged


def test_no_ordinary_number_or_hash_is_ever_replaced():
    rng = random.Random(16)

    def make(alphabet, n):
        return _made_up(rng, n, alphabet)

    kinds = [string.ascii_letters + string.digits] * 5 + ["0123456789abcdef"] * 2 + [string.ascii_lowercase] * 2
    env = {f"MADE_UP_{i}_KEY": make(a, rng.randint(24, 48)) for i, a in enumerate(kinds)}
    env["MADE_UP_ACCOUNT_ID"] = make(string.digits, 12)
    for name in env:
        s.secret(name, env)
    floats = [rng.uniform(-1.0, 1.0) * 10.0 ** rng.randint(-12, 15) for _ in range(800_000)]
    ints = [rng.randrange(10 ** rng.randint(8, 19)) for _ in range(200_000)]
    hashes = [f"{rng.getrandbits(256):064x}" for _ in range(20_000)]
    text = json.dumps({"values": floats, "ints": ints, "hashes": hashes})
    out, n = s.redact(text)
    same = out == text
    assert n == 0 and same
    record = {"values": floats, "ints": ints, "hashes": hashes}
    back, m = s.redact_json(record)                           # numbers held as numbers: none changes
    assert m == 0 and back is record and back["values"] is floats and back["ints"] is ints


# ---- rev 4 -------------------------------------------------------------------------------------

import subprocess  # noqa: E402
from pathlib import Path  # noqa: E402

REPO = Path(__file__).resolve().parents[2]

FAKE_SDK = '''
import logging
import sys

log = logging.getLogger("webull.fake_sdk")
_handler = logging.StreamHandler()                 # created at import, before any guard
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
log.addHandler(_handler)
log.setLevel(logging.DEBUG)
log.propagate = False


class SdkHandler(logging.StreamHandler):
    """A third-party handler class."""
'''

LOGGING_CASES = {
    "own handler, propagate off, before": (
        "lg = logging.getLogger('sdk'); lg.propagate = False; lg.addHandler(logging.StreamHandler(sys.stderr))",
        "lg.warning('MARK key %s', V)"),
    "handler before, attached to root after, child logger": (
        "h = logging.StreamHandler(sys.stdout)",
        "logging.getLogger().addHandler(h); logging.getLogger('a.b').warning('MARK %s', V)"),
    "logger and handler after": (
        "",
        "lg = logging.getLogger('late'); lg.propagate = False; lg.addHandler(logging.StreamHandler(sys.stdout)); lg.error('MARK ' + V)"),
    "file handler before": (
        "lg = logging.getLogger('f'); lg.propagate = False; lg.addHandler(logging.FileHandler('out.log'))",
        "lg.warning('MARK %s', V)"),
    "basicConfig before": (
        "logging.basicConfig(level=logging.INFO)",
        "logging.getLogger('x.y').info('MARK %s', V)"),
    "basicConfig after, to a file": (
        "",
        "logging.basicConfig(filename='out.log', format='%(name)s %(message)s'); logging.getLogger('z').warning('MARK %s', V)"),
    "logging.exception with the secret in the exception": (
        "logging.basicConfig()",
        "try:\n    raise RuntimeError('bad ' + V)\nexcept RuntimeError:\n    logging.exception('MARK failed')"),
    "third-party handler made at import": (
        "import fake_sdk",
        "fake_sdk.log.debug('MARK %s', V); fake_sdk.log.info('MARK ' + V.upper())"),
    "third-party handler class, to a file, with a traceback": (
        "import fake_sdk; lg = logging.getLogger('q'); lg.propagate = False; lg.addHandler(fake_sdk.SdkHandler(open('out.log', 'w')))",
        "try:\n    raise ValueError(V)\nexcept ValueError:\n    lg.exception('MARK')"),
    "socket handler's pickled record": (
        "import logging.handlers, pickle\n"
        "class Cap(logging.handlers.SocketHandler):\n"
        "    def send(self, data):\n"
        "        open('out.log', 'a').write(repr(pickle.loads(data[4:])))\n"
        "lg = logging.getLogger('sock'); lg.propagate = False; lg.addHandler(Cap('localhost', 9))",
        "try:\n    raise KeyError(V)\nexcept KeyError:\n    lg.exception('MARK %s', V)"),
    "stack info and args through a child propagating to root": (
        "logging.basicConfig(stream=sys.stdout)",
        "logging.getLogger('p.q.r').warning('MARK %s %r', V, {'k': V}, stack_info=True)"),
}


def _child_log_case(tmp_path, before, after, value):
    (tmp_path / "fake_sdk.py").write_text(FAKE_SDK)
    script = "import logging, os, sys\n" + before + "\nfrom valedia_trader.core import secrets\n" \
             "secrets.secret('MADE_UP_CHILD_TOKEN')\nV = os.environ['MADE_UP_CHILD_TOKEN']\n" + after + "\n"
    (tmp_path / "child.py").write_text(script)
    env = {**os.environ, "MADE_UP_CHILD_TOKEN": value, "PYTHONPATH": str(REPO)}
    done = subprocess.run([sys.executable, "child.py"], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    log = tmp_path / "out.log"
    return done.returncode, done.stdout + done.stderr + (log.read_text() if log.exists() else "")


@pytest.mark.parametrize("case", sorted(LOGGING_CASES))
def test_every_log_record_through_every_handler_is_redacted(tmp_path, case):
    value = _made_up(random.Random(case), 32)
    before, after = LOGGING_CASES[case]
    code, text = _child_log_case(tmp_path, before, after, value)
    leaked = _leaks(text, value)
    assert code == 0 and "MARK" in text and "<secret>" in text and not leaked


def test_removing_the_guard_restores_logging_exactly():
    fmt, factory = logging.Handler.format, logging.getLogRecordFactory()
    _remember(_made_up(random.Random(17), 32))
    changed = logging.Handler.format is not fmt and logging.getLogRecordFactory() is not factory
    s.remove_output_guard()
    assert changed and logging.Handler.format is fmt and logging.getLogRecordFactory() is factory


def test_a_remembered_number_is_replaced_and_no_other_number_changes():
    rng = random.Random(18)
    account = int("7" + _made_up(rng, 8, string.digits))          # nine digits
    s.remember(str(account))
    record = {"account": account, account: "by key", "as_float": float(account), "negative": -account,
              "longer": account * 10 + 3, "price": 101.25, "flag": True, "count": 12, "big": 10 ** 15 + 1}
    out, n = s.redact_json(record)
    text = json.dumps({k: v for k, v in out.items() if k != "longer"})   # "longer" holds the digits inside a longer number
    leaked = _leaks(text, str(account))
    assert not leaked and n == 4
    assert out["account"] == "<secret>" and "<secret>" in out and out["as_float"] == "<secret>" and out["negative"] == "<secret>"
    kept = [type(out[k]) for k in ("longer", "price", "flag", "count", "big")] == [int, float, bool, int, int]
    assert kept and (out["longer"], out["price"], out["flag"], out["count"], out["big"]) == (account * 10 + 3, 101.25, True, 12, 10 ** 15 + 1)


class _Recorder(io.StringIO):
    def close(self):
        self.final = self.getvalue()
        super().close()


def test_the_wrapper_flushes_on_close_works_in_with_iterates_and_lets_carriage_returns_through(monkeypatch):
    out = _Recorder()
    monkeypatch.setattr(sys, "stdout", out)
    s.install_output_guard()
    sys.stdout.write("progress 10%\r")
    shown_at_once = out.getvalue() == "progress 10%\r"
    sys.stdout.write("held back")
    with sys.stdout as stream:
        same = stream is sys.stdout
        stream.write(" and more")
    assert shown_at_once and same and out.closed and out.final == "progress 10%\rheld back and more"
    reader = s._GuardedStream(io.StringIO("a\nb\n"))
    assert list(reader) == ["a\n", "b\n"] and reader.readable() and not reader.closed
    s.remove_output_guard()


def test_no_test_can_reach_the_real_secrets_file(real_secret_file_touches):
    p = s.env_file_path()
    assert p != s.DEFAULT_ENV_FILE                                # checked first: nothing below may touch the real path
    assert not p.exists() and p.parent.name.startswith("valedia-no-secrets-") and s.load_env() == []
    assert real_secret_file_touches == []


def test_paths_urls_plain_words_and_settings_are_never_remembered(tmp_path):
    rng = random.Random(19)
    good, digits = _made_up(rng, 30), "AC" + _made_up(rng, 10, string.digits)
    lines = {"WEBULL_OPENAPI_TOKEN_DIR": "/tmp/made-up/token-folder", "BROKER_TOKEN": "https://example.invalid/path/abcdefgh",
             "WEBULL_ACCOUNT_TYPE": "individual", "MAIN_ACCOUNT": "brokeragemain", "HOME_KEY": "~/made-up/keyfolder",
             "REL_KEY": "./made-up/relative", "UP_KEY": "../made-up/upwards", "SPACE_KEY": "made up words here",
             "KEY_PATH": "relative-made-up-path", "API_KEY_FILE": "keyfile-made-up", "TOKEN_URL": "tokens.example.invalid",
             "SECRET_HOST": "secrets-host-name", "SECRET_PORT": "123456789", "PASS_MODE": "interactive",
             "KEY_NAME": "my-key-name-here", "KEY_REGION": "us-east-region", "WEBULL_ENV": "production-mode",
             "GOOD_KEY": good, "WEBULL_ACCOUNT_ID": digits}
    f = tmp_path / "trading.env"
    f.write_text("".join(f"{k}={v}\n" for k, v in lines.items()))
    os.chmod(f, 0o600)
    s.load_env(f, environ={})
    caught = {k for k, v in lines.items() if s.redact(f"x {v} y")[1]}
    assert caught == {"GOOD_KEY", "WEBULL_ACCOUNT_ID"}
    s.secret("SOME_DIR", {"SOME_DIR": "/made-up/served/path"})     # the caller says it is a secret
    assert s.redact("at /made-up/served/path now")[1] == 1


def test_a_value_already_in_the_environment_is_remembered_and_left_alone(tmp_path):
    rng = random.Random(20)
    present, in_file = _made_up(rng, 30), _made_up(rng, 30)
    f = tmp_path / "trading.env"
    f.write_text(f"BROKER_API_KEY={in_file}\nOTHER_DIR=/made-up/dir\n")
    os.chmod(f, 0o600)
    env = {"BROKER_API_KEY": present, "OTHER_DIR": "/made-up/other"}
    loaded = s.load_env(f, environ=env)
    kept = env["BROKER_API_KEY"] == present
    assert loaded == [] and kept and s.redact(f"a {present} b")[1] == 1 and s.redact("at /made-up/other")[1] == 0


def test_remember_hands_the_tripwire_a_value_learned_elsewhere():
    rng = random.Random(21)
    value, short = _made_up(rng, 16), _made_up(rng, 7)
    assert s.remember(value) is None and s.remember(short) is None
    assert s.redact(f"id {value}")[1] == 1 and s.redact(f"id {short}")[1] == 0
