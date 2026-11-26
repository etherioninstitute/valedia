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


def test_guard_catches_a_secret_split_across_two_writes(tmp_path, monkeypatch):
    value = _made_up(random.Random(8), 32)
    target = _Screen()                                        # the process's standard output: it holds back
    monkeypatch.setattr(sys, "stdout", target)
    monkeypatch.setattr(sys, "__stdout__", target)
    _remember(value)
    sys.stdout.write("key " + value[:13])
    sys.stdout.write(value[13:] + " end\n")
    sys.stdout.write("tail without newline")
    sys.stdout.flush()
    s.remove_output_guard()
    out = "".join(target.parts)
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


class _Screen(_Stream):
    """A stand-in for the process's own standard output (file number 1) that records every write."""

    def fileno(self):
        return 1


def test_guard_keeps_the_stream_attributes_installs_once_and_removes_cleanly(monkeypatch):
    out, err = _Screen(), _Stream()                          # the screen holds back, another stream never does
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "__stdout__", out)
    monkeypatch.setattr(sys, "stderr", err)
    s.install_output_guard()
    first = sys.stdout
    s.install_output_guard()
    assert sys.stdout is first and first is not out
    assert sys.stdout.isatty() and sys.stdout.fileno() == 1 and sys.stdout.encoding == "latin-1" and sys.stdout.mode == "w"
    sys.stdout.write("plain text, no secret here\n")
    sys.stdout.writelines(["a", "b\n"])
    sys.stdout.write("held")
    sys.stderr.write("not held")
    assert out.parts == ["plain text, no secret here\n", "ab\n"] and err.parts == ["not held"]
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
    words = random.Random(160)
    for n in (12, 24):                                            # two recovery phrases as well
        s.remember(" ".join(words.choice(WORDS) for _ in range(n)))
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
             "REL_KEY": "./made-up/relative", "UP_KEY": "../made-up/upwards", "SPACE_KEY": "three made-up words",
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
    served = s.redact("at /made-up/served/path now")[1]
    assert served == 1


def test_a_value_already_in_the_environment_is_remembered_and_left_alone(tmp_path):
    rng = random.Random(20)
    present, in_file = _made_up(rng, 30), _made_up(rng, 30)
    f = tmp_path / "trading.env"
    f.write_text(f"BROKER_API_KEY={in_file}\nOTHER_DIR=/made-up/dir\n")
    os.chmod(f, 0o600)
    env = {"BROKER_API_KEY": present, "OTHER_DIR": "/made-up/other"}
    loaded = s.load_env(f, environ=env)
    kept = env["BROKER_API_KEY"] == present
    counts = (s.redact(f"a {present} b")[1], s.redact("at /made-up/other")[1])
    assert loaded == [] and kept and counts == (1, 0)


def test_remember_hands_the_tripwire_a_value_learned_elsewhere():
    rng = random.Random(21)
    value, short = _made_up(rng, 16), _made_up(rng, 7)
    assert s.remember(value) is None and s.remember(short) is None
    counts = (s.redact(f"id {value}")[1], s.redact(f"id {short}")[1])
    assert counts == (1, 0)


# ---- rev 5 -------------------------------------------------------------------------------------

import re  # noqa: E402

def _word_list(n=600, seed=587):
    """A list of ordinary words built at run time: from the system dictionary when there is one,
    otherwise made-up pronounceable words. Never typed out by hand."""
    path = Path("/usr/share/dict/words")
    rng = random.Random(seed)
    if path.is_file():
        pool = sorted({w for w in path.read_text().split() if w.isalpha() and w.islower() and 4 <= len(w) <= 8})
        if len(pool) >= n:
            return rng.sample(pool, n)
    made = set()
    while len(made) < n:
        made.add("".join(rng.choice("bcdfghklmnprstvz") + rng.choice("aeiou") for _ in range(rng.randint(2, 4))))
    return sorted(made)


WORDS = _word_list()


def _phrase(rng, n):
    return [rng.choice(WORDS) for _ in range(n)]


def _phrase_leaks(text, words):
    """True when any 4 consecutive words of the phrase appear in the text in order as separate words,
    ignoring case and whatever stands between them."""
    seq = re.findall(r"[^\W\d_]+", text.lower())
    grams = {tuple(words[i:i + 4]) for i in range(len(words) - 3)}
    return any(tuple(seq[i:i + 4]) in grams for i in range(len(seq) - 3))


def test_a_phrase_is_caught_as_any_four_consecutive_words():
    rng = random.Random(30)
    words = _phrase(rng, 12)
    s.secret("WALLET_MNEMONIC", {"WALLET_MNEMONIC": " ".join(words)})
    forms = {"spaces": " ".join(words), "mixed whitespace": " \t".join(words[:6]) + "\n\n  " + "   ".join(words[6:]),
             "commas": ",".join(words), "comma space": ", ".join(words), "hyphens": "-".join(words), "underscores": "_".join(words),
             "upper": " ".join(words).upper(), "title": " ".join(w.title() for w in words), "json": json.dumps({"m": " ".join(words)}),
             "four from the middle": " ".join(words[4:8]), "in a sentence": "my words are " + " ".join(words[2:6]) + " today."}
    results = {}
    for name, planted in forms.items():
        out, n = s.redact(f"before {planted} after")
        results[name] = (n >= 1, not _phrase_leaks(out, words), out.startswith("before ") and out.endswith(" after"))
    assert all(all(r) for r in results.values()), sorted(k for k, r in results.items() if not all(r))
    exact = s.redact("my words are " + " ".join(words[2:6]) + " today.")[0] == "my words are <secret> today."
    three = " ".join(words[4:7])
    out, n3 = s.redact(f"only {three} here")
    assert exact and n3 == 0 and out == f"only {three} here"


def test_a_phrase_word_that_is_not_purely_letters_counts_alone():
    rng = random.Random(31)
    odd = _made_up(rng, 11, string.ascii_lowercase + string.digits) + "7"
    words = _phrase(rng, 6) + [odd] + _phrase(rng, 5)
    s.remember(" ".join(words))
    long_word = max(words[:6], key=len)
    counts = (s.redact(f"token {odd} here")[1], s.redact(f"a {long_word} b")[1])
    assert counts == (1, 0)


def test_load_env_remembers_a_phrase_of_four_words_or_more(tmp_path):
    rng = random.Random(32)
    phrase, two, three = (" ".join(_phrase(rng, n)) for n in (12, 2, 3))
    f = tmp_path / "trading.env"
    f.write_text(f"WALLET_SEED_PHRASE={phrase}\nBROKER_KEY={two}\nNOTE_TOKEN={three}\nKEY_DIR=/made up/four word path\n")
    os.chmod(f, 0o600)
    s.load_env(f, environ={})
    counts = [s.redact(f"x {v} y")[1] for v in (phrase, two, three, "/made up/four word path")]
    assert counts == [1, 0, 0, 0]


def test_ordinary_sentences_are_never_replaced_unless_four_words_match():
    rng = random.Random(33)
    phrases = [_phrase(rng, 12), _phrase(rng, 24)]
    for words in phrases:
        s.secret("MADE_UP_MNEMONIC", {"MADE_UP_MNEMONIC": " ".join(words)})
    grams = {tuple(w[i:i + 4]) for w in phrases for i in range(len(w) - 3)}

    sentences = []
    for _ in range(20_000):
        seq = _phrase(rng, rng.randint(6, 18))
        text = " ".join(w + ("," if rng.random() < 0.1 else "") for w in seq)
        sentences.append(text[0].upper() + text[1:].rstrip(",") + ".")
    body = "\n".join(sentences)
    seq = re.findall(r"[^\W\d_]+", body.lower())           # independent count: every word of the body, in order
    starts = [i for i in range(len(seq) - 3) if tuple(seq[i:i + 4]) in grams]
    regions = sum(1 for k, i in enumerate(starts) if k == 0 or i > starts[k - 1] + 3)
    out, n = s.redact(body)
    leaked = any(_phrase_leaks(out, w) for w in phrases)
    same = out == body if not regions else True
    assert n == regions and not leaked and same
    planted = []
    for _ in range(200):
        words = rng.choice(phrases)
        i = rng.randrange(len(words) - 3)
        planted.append(" ".join(_phrase(rng, 3) + words[i:i + 4] + _phrase(rng, 3)) + ".")
    caught = [s.redact(x) for x in planted]
    assert all(k >= 1 and not any(_phrase_leaks(o, w) for w in phrases) for o, k in caught)


def test_remember_takes_a_number_as_its_plain_decimal_text():
    rng = random.Random(34)
    account = int("9" + _made_up(rng, 11, string.digits))
    s.remember(account)
    s.remember(True)
    s.remember(None)
    s.remember(87654321.0)
    counts = (s.redact(f"acct {account} here")[1], s.redact("value 87654321 here")[1], s.redact("flag True and None here")[1])
    assert counts == (1, 1, 0)


def test_ensure_output_guard_wraps_a_replaced_stream_once(monkeypatch):
    _remember(_made_up(random.Random(35), 32))
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    first, second = s.ensure_output_guard(), s.ensure_output_guard()
    assert first is True and second is False and isinstance(sys.stdout, s._GuardedStream)
    s.remove_output_guard()


REWRAP_CALLS = {
    "secret": "secrets.secret('MADE_UP_CHILD_TOKEN')",
    "load_env": "secrets.load_env('missing.env')",
    "remember": "secrets.remember('x' * 3)",
    "redact": "secrets.redact('nothing here')",
    "redact_json": "secrets.redact_json({'a': 1})",
    "child_env": "secrets.child_env()",
    "ensure_output_guard": "assert secrets.ensure_output_guard() is True",
    "journal write": "from valedia_trader.research_loop.paper import Journal; Journal('journal').write('2026-01-05', {'type': 'note'})",
}


@pytest.mark.parametrize("call", sorted(REWRAP_CALLS))
@pytest.mark.parametrize("how", ["replaced", "redirect closed"])
def test_a_stream_replaced_after_the_guard_is_wrapped_again_at_the_next_call(tmp_path, call, how):
    value = _made_up(random.Random(call + how), 32)
    if how == "replaced":
        setup = ("secrets.secret('MADE_UP_CHILD_TOKEN')\n"
                 "sys.stdout = os.fdopen(os.dup(1), 'w')\nsys.stderr = os.fdopen(os.dup(2), 'w')\n")
    else:
        setup = ("with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):\n"
                 "    secrets.secret('MADE_UP_CHILD_TOKEN')\n")
    script = ("import contextlib, io, os, sys\nfrom valedia_trader.core import secrets\n" + setup + REWRAP_CALLS[call] + "\n"
              "V = os.environ['MADE_UP_CHILD_TOKEN']\nprint('MARK', V)\nprint('MARK', V, file=sys.stderr)\n")
    (tmp_path / "child.py").write_text(script)
    env = {**os.environ, "MADE_UP_CHILD_TOKEN": value, "PYTHONPATH": str(REPO)}
    done = subprocess.run([sys.executable, "child.py"], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
    leaked = _leaks(done.stdout + done.stderr, value)
    assert done.returncode == 0 and not leaked and done.stdout.count("<secret>") == 1 and done.stderr.count("<secret>") == 1


# ---- rev 6 -------------------------------------------------------------------------------------

def _run_child(tmp_path, body, timeout=60, **env_extra):
    """Run `body` in a child Python with the guard's module imported; return code, stdout, stderr."""
    script = "import contextlib, io, os, sys, threading\nfrom valedia_trader.core import secrets\n" + body + "\n"
    (tmp_path / "child.py").write_text(script)
    env = {**os.environ, "PYTHONPATH": str(REPO), **env_extra}
    done = subprocess.run([sys.executable, "child.py"], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=timeout)
    return done.returncode, done.stdout, done.stderr


PHRASE_SCREEN_CASES = {
    "one word per line": "for w in W:\n    print(w)",
    "three words, a break, three words": "print(' '.join(W[:3]) + '\\n' + ' '.join(W[3:6]))",
    "a word per write": "for w in W[:6]:\n    sys.stdout.write(w + ' ')\nsys.stdout.write('\\n')",
    "a numbered list": "for i, w in enumerate(W, 1):\n    print(f'{i}. {w}')",
    "one word per line on stderr": "for w in W[:5]:\n    print(w, file=sys.stderr)",
    "upper case with blank lines": "for w in W[:6]:\n    print(w.upper())\n    print()",
    "a word per write with line breaks": "for w in W[2:9]:\n    sys.stdout.write(w)\n    sys.stdout.write('\\n')",
}


@pytest.mark.parametrize("case", sorted(PHRASE_SCREEN_CASES))
def test_a_phrase_broken_by_lines_or_writes_never_reaches_the_screen(tmp_path, case):
    words = _phrase(random.Random(case), 12)
    body = ("secrets.secret('MADE_UP_CHILD_PHRASE')\nW = os.environ['MADE_UP_CHILD_PHRASE'].split()\nprint('MARK start')\n"
            + PHRASE_SCREEN_CASES[case] + "\nprint('MARK end')")
    code, out, err = _run_child(tmp_path, body, MADE_UP_CHILD_PHRASE=" ".join(words))
    leaked = _phrase_leaks(out, words) or _phrase_leaks(err, words)
    assert code == 0 and not leaked and "MARK start" in out and "MARK end" in out and "<secret>" in out + err




def test_with_a_phrase_remembered_ordinary_lines_go_out_before_the_write_returns(monkeypatch):
    rng = random.Random(40)
    phrase = _phrase(rng, 12)
    others = [w for w in WORDS if w not in phrase]
    target = _Screen()
    monkeypatch.setattr(sys, "stdout", target)
    monkeypatch.setattr(sys, "__stdout__", target)
    s.remember(" ".join(phrase))
    late, written = 0, ""
    for _ in range(10_000):
        line = " ".join(rng.choice(others) for _ in range(rng.randint(3, 12))) + "\n"
        sys.stdout.write(line)
        written += line
        late += "".join(target.parts) != written
    sys.stdout.write("progress 10%\r")
    shown = "".join(target.parts) == written + "progress 10%\r"
    s.remove_output_guard()
    assert late == 0 and shown


def test_with_no_phrase_remembered_the_timing_is_as_before(monkeypatch):
    target = _Screen()
    monkeypatch.setattr(sys, "stdout", target)
    monkeypatch.setattr(sys, "__stdout__", target)
    _remember(_made_up(random.Random(41), 32))
    steps = []
    for text in ("a", "b\n", "c\r", "d"):
        sys.stdout.write(text)
        steps.append("".join(target.parts))
    sys.stdout.flush()
    steps.append("".join(target.parts))
    s.remove_output_guard()
    assert steps == ["", "ab\n", "ab\nc\r", "ab\nc\r", "ab\nc\rd"]


IN_MEMORY_CASES = {
    "a redirect to a buffer": (
        "with contextlib.redirect_stdout(io.StringIO()) as buf:\n"
        "    secrets.secret('MADE_UP_CHILD_TOKEN')\n"
        "    sys.stdout.write('partial-no-newline')\n"
        "    got = buf.getvalue()\n"
        "print('OK' if got == 'partial-no-newline' else 'LOST')"),
    "a string buffer set after the guard": (
        "secrets.secret('MADE_UP_CHILD_TOKEN')\nV = os.environ['MADE_UP_CHILD_TOKEN']\n"
        "sys.stdout = io.StringIO()\nsecrets.redact('x' * 9)\n"
        "print('A', end='')\nprint('B', end='')\nprint(' ' + V, end='')\n"
        "got = sys.stdout.getvalue()\nsys.stdout = sys.__stdout__\n"
        "print('OK' if got == 'AB <secret>' else 'LOST')"),
    "text held for the old stream": (
        "secrets.secret('MADE_UP_CHILD_TOKEN')\nsys.stdout.write('OK-held-partial ')\n"
        "sys.stdout = io.StringIO()\nsecrets.redact('x' * 9)\nsys.stdout = sys.__stdout__\nprint('end')"),
}


@pytest.mark.parametrize("case", sorted(IN_MEMORY_CASES))
def test_nothing_written_to_a_guarded_stream_is_lost(tmp_path, case):
    value = _made_up(random.Random(case), 32)
    code, out, err = _run_child(tmp_path, IN_MEMORY_CASES[case], MADE_UP_CHILD_TOKEN=value)
    leaked = _leaks(out + err, value)
    assert code == 0 and not leaked and "OK" in out and "LOST" not in out


def test_any_short_run_of_non_letters_between_phrase_words_counts_as_a_separator():
    words = _phrase(random.Random(43), 12)
    s.remember(" ".join(words))
    four = words[3:7]
    forms = {"numbered": " ".join(f"{i}. {w}" for i, w in enumerate(four, 4)), "together": "".join(four)}
    for sep in ("+", ".", "/", ";", "|", ":", "--", ",,", "\u2013", " 7 ", "\t\t", "...!!", "(1) "):
        forms[repr(sep)] = sep.join(four)
    results = {}
    for name, planted in forms.items():
        out, n = s.redact(f"start {planted} stop")
        results[name] = n >= 1 and not _phrase_leaks(out, words) and "".join(four) not in out.lower()
    assert all(results.values()), sorted(k for k, ok in results.items() if not ok)
    out, n = s.redact(" ".join(words[3:5]) + " !!!!!!! " + " ".join(words[5:7]))   # a gap of 7 breaks the run
    assert n == 0


class _Tee:
    """A wrapper of the common kind: writes go on to `stream`."""

    def __init__(self, stream):
        self.stream = stream

    def write(self, text):
        return self.stream.write(text)

    def flush(self):
        self.stream.flush()


def _guard_layers(stream):
    layers, seen = 0, 0
    while stream is not None and seen < 200:
        layers += isinstance(stream, s._GuardedStream)
        stream = stream.__dict__.get("_stream") or stream.__dict__.get("stream")
        seen += 1
    return layers


def test_the_guard_never_wraps_a_stream_that_already_reaches_a_guard(monkeypatch):
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    _remember(_made_up(random.Random(44), 32))
    for _ in range(8):
        sys.stdout = _Tee(sys.stdout)
        s.redact("plain text here")
    after_eight = _guard_layers(sys.stdout)
    for _ in range(42):
        sys.stdout = _Tee(sys.stdout)
        s.redact("plain text here")
    after_fifty = _guard_layers(sys.stdout)
    s.remove_output_guard()
    assert after_eight == 1 and after_fifty <= 6


def test_remove_inside_a_redirect_leaves_nothing_redacting(tmp_path):
    body = ("secrets.secret('MADE_UP_CHILD_TOKEN')\n"
            "with contextlib.redirect_stdout(io.StringIO()):\n    secrets.remove_output_guard()\n"
            "g = sys.stdout\nactive = isinstance(g, secrets._GuardedStream) and secrets._guard_wanted\n"
            "secrets.remember('another-made-up-value-1234')\n"
            "h = sys.stdout\nlayers = 0\nwhile h is not None:\n"
            "    layers += isinstance(h, secrets._GuardedStream)\n    h = h.__dict__.get('_stream')\n"
            "print('ACTIVE' if active else 'INERT', layers)")
    code, out, _ = _run_child(tmp_path, body, MADE_UP_CHILD_TOKEN=_made_up(random.Random(45), 32))
    assert code == 0 and out.split() == ["INERT", "1"]


def test_eight_writers_and_a_guard_switched_2000_times_never_deadlock(tmp_path):
    body = ("V = secrets.secret('MADE_UP_CHILD_TOKEN')\n"
            "stop, counts = threading.Event(), [0] * 8\n"
            "def writer(i):\n    n = 0\n    while not stop.is_set() or n < 300:\n"
            "        sys.stdout.write(f't{i} {n} {V}\\n' if n % 10 == 0 else f't{i} {n}\\n')\n"
            "        n += 1\n    counts[i] = n\n"
            "threads = [threading.Thread(target=writer, args=(i,)) for i in range(8)]\n"
            "for t in threads:\n    t.start()\n"
            "for _ in range(2000):\n    secrets.install_output_guard()\n    secrets.ensure_output_guard()\n"
            "    secrets.remove_output_guard()\n"
            "stop.set()\nfor t in threads:\n    t.join()\nsys.stdout.flush()\n"
            "print('COUNTS', *counts)")
    value = _made_up(random.Random(46), 32)
    try:
        code, out, _ = _run_child(tmp_path, body, timeout=60, MADE_UP_CHILD_TOKEN=value)
    except subprocess.TimeoutExpired:
        code, out = None, ""
    lines = out.replace(value, "<secret>").splitlines()
    counts = [int(x) for x in lines[-1].split()[1:]] if lines and lines[-1].startswith("COUNTS") else []
    expected = sorted(f"t{i} {n} <secret>" if n % 10 == 0 else f"t{i} {n}" for i in range(len(counts)) for n in range(counts[i]))
    ok = code == 0 and len(counts) == 8 and sorted(lines[:-1]) == expected
    assert ok


# ---- rev 6 follow-up: values of spaced groups of digits or of letters and digits ---------------

def _groups(rng, n, mixed=False):
    """Made-up groups: four digits each, or two letters and two digits each."""
    if mixed:
        return [_made_up(rng, 2, string.ascii_lowercase) + _made_up(rng, 2, string.digits) for _ in range(n)]
    return [_made_up(rng, 4, string.digits) for _ in range(n)]


def _group_leaks(text, groups):
    """True when any 4 consecutive groups appear in the text in order as separate runs of letters and
    digits, ignoring case and whatever stands between them."""
    seq = re.findall(r"[^\W_]+", text.lower())
    grams = {tuple(groups[i:i + 4]) for i in range(len(groups) - 3)}
    return any(tuple(seq[i:i + 4]) in grams for i in range(len(seq) - 3))


@pytest.mark.parametrize("mixed", [False, True])
def test_a_value_of_spaced_groups_is_caught_as_a_sequence(mixed):
    rng = random.Random(50 + mixed)
    groups = _groups(rng, 6, mixed)
    s.remember(" ".join(groups))
    four = groups[1:5]
    forms = {"spaced": " ".join(four), "dashes": "-".join(four), "lines": "\n".join(four), "together": "".join(four),
             "upper": " ".join(four).upper(), "commas": ", ".join(four), "slashes": " / ".join(four),
             "first four": " ".join(groups[:4]), "last four": "  ".join(groups[2:])}
    results = {}
    for name, planted in forms.items():
        out, n = s.redact(f"card {planted} end")
        results[name] = n >= 1 and not _group_leaks(out, groups) and "".join(four) not in out.lower()
    assert all(results.values()), sorted(k for k, ok in results.items() if not ok)
    out, n = s.redact("x " + " ".join(groups[:3]) + " y")                      # three groups pass
    out2, n2 = s.redact(" ".join(groups[:2]) + " ...!!!! " + " ".join(groups[2:4]))  # a gap of 7 breaks
    assert n == 0 and n2 == 0


GROUP_SCREEN_CASES = {
    "one group per line": "for g in G:\n    print(g)",
    "a group per write": "for g in G[:5]:\n    sys.stdout.write(g)\n    sys.stdout.write(' ')\nprint()",
    "two, a break, two": "print(' '.join(G[:2]) + '\\n' + ' '.join(G[2:4]))",
}


@pytest.mark.parametrize("case", sorted(GROUP_SCREEN_CASES))
def test_spaced_groups_broken_by_lines_or_writes_never_reach_the_screen(tmp_path, case):
    groups = _groups(random.Random(case), 6)
    body = ("secrets.secret('MADE_UP_CHILD_CARD')\nG = os.environ['MADE_UP_CHILD_CARD'].split()\nprint('MARK start')\n"
            + GROUP_SCREEN_CASES[case] + "\nprint('MARK end')")
    code, out, err = _run_child(tmp_path, body, MADE_UP_CHILD_CARD=" ".join(groups))
    leaked = _group_leaks(out, groups) or _group_leaks(err, groups)
    assert code == 0 and not leaked and "MARK end" in out and "<secret>" in out


def _number_lines(rng, groups, n):
    """Ordinary lines of spaced numbers: dates, times, prices, table rows, where the remembered
    groups turn up often, one at a time or a few together."""
    def num():
        r = rng.random()
        if r < 0.35:
            return rng.choice(groups)
        if r < 0.6:
            return _made_up(rng, 4, string.digits)
        if r < 0.75:
            return f"{rng.randint(1, 9999)}.{rng.randint(0, 99):02d}"
        if r < 0.9:
            return f"{rng.randint(2000, 2030)}-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"
        return f"{rng.randint(0, 23):02d}:{rng.randint(0, 59):02d}"
    seps = [" ", "  ", ", ", " | ", "/", "-", "\t", " : "]
    return [rng.choice(seps).join(num() for _ in range(rng.randint(2, 9))) for _ in range(n)]


def test_ordinary_spaced_numbers_are_never_replaced_unless_four_groups_match():
    rng = random.Random(53)
    groups = _groups(rng, 4)
    s.remember(" ".join(groups))
    lines = _number_lines(rng, groups, 200_000)
    gram = tuple(groups)
    expected = replaced = leaked = 0
    for k in range(0, len(lines), 1000):
        chunk = "\n".join(lines[k:k + 1000])
        seq = re.findall(r"[^\W_]+", chunk.lower())
        starts = [i for i in range(len(seq) - 3) if tuple(seq[i:i + 4]) == gram]
        expected += sum(1 for j, i in enumerate(starts) if j == 0 or i > starts[j - 1] + 3)
        out, n = s.redact(chunk)
        replaced += n
        leaked += _group_leaks(out, groups)
    assert replaced == expected and not leaked


# ---- rev 7 -------------------------------------------------------------------------------------

LOST_CASES = {
    "the verifier's case": (
        "with open('out.txt', 'w') as f, contextlib.redirect_stdout(f):\n"
        "    secrets.redact('x')\n    sys.stdout.write('{\"r\":1}')\n"
        "print('OK' if open('out.txt').read() == '{\"r\":1}' else 'LOST')"),
    "a phrase word at the end of a line in a file": (
        "secrets.remember(os.environ['MADE_UP_CHILD_PHRASE'])\nW = os.environ['MADE_UP_CHILD_PHRASE'].split()\n"
        "with open('out.txt', 'w') as f, contextlib.redirect_stdout(f):\n"
        "    secrets.redact('x')\n    print('first', W[0])\n"
        "print('OK' if open('out.txt').read() == 'first ' + W[0] + '\\n' else 'LOST')"),
    "a file set as stdout and closed": (
        "f = open('out.txt', 'w')\nsys.stdout = f\nsecrets.ensure_output_guard()\nprint('partial', end='')\n"
        "f.close()\nsys.stdout = sys.__stdout__\n"
        "print('OK' if open('out.txt').read() == 'partial' else 'LOST')"),
}


@pytest.mark.parametrize("case", sorted(LOST_CASES))
def test_text_written_to_a_file_the_program_opened_is_never_lost(tmp_path, case):
    value = _made_up(random.Random(case), 32)
    body = "secrets.secret('MADE_UP_CHILD_TOKEN')\n" + LOST_CASES[case]
    code, out, err = _run_child(tmp_path, body, MADE_UP_CHILD_TOKEN=value,
                                MADE_UP_CHILD_PHRASE=" ".join(_phrase(random.Random(case), 12)))
    leaked = _leaks(out + err, value)
    assert code == 0 and not leaked and out.split() == ["OK"]


_SEQUENCES = '''
import random
from pathlib import Path
seed = int(os.environ['SEQ_SEED'])
rng = random.Random(seed)
secrets.secret('MADE_UP_CHILD_TOKEN')
secrets.remember(os.environ['MADE_UP_CHILD_PHRASE'])
W = os.environ['MADE_UP_CHILD_PHRASE'].split()
SOME = [W[0], W[5], W[10]]                       # phrase words, never 4 in a row

class Tee:
    def __init__(self, stream):
        self.stream = stream
    def write(self, text):
        return self.stream.write(text)
    def flush(self):
        self.stream.flush()

expected, screen, lost, made = {}, [], 0, 0

def close(entry):
    global lost
    name, stack, kind, obj = entry
    if kind == 'buffer':
        lost += obj.getvalue() != ''.join(expected[name])
    stack.close()

for _ in range(10):
    open_now = []
    for step in range(rng.randint(5, 30)):
        op = rng.random()
        if op < 0.2 and len(open_now) < 4:
            stack, kind = contextlib.ExitStack(), rng.choice(['file', 'file', 'buffer', 'tee'])
            name = f'{seed}-{made}'
            made += 1
            if kind == 'buffer':
                obj = target = io.StringIO()
            else:
                obj = stack.enter_context(open(name + '.txt', 'w'))
                target = Tee(obj) if kind == 'tee' else obj
            stack.enter_context(contextlib.redirect_stdout(target))
            open_now.append((name, stack, kind, obj))
            expected[name] = []
        elif op < 0.35 and open_now:
            close(open_now.pop())
        elif op < 0.5:
            rng.choice([lambda: secrets.redact('x'), secrets.ensure_output_guard, lambda: secrets.redact_json({'a': 1})])()
        else:
            text = rng.choice(['{"r":%d}' % step, 'line %d\\n' % step, 'part%d ' % step, '\\r', 'p%d' % step,
                               rng.choice(SOME) + rng.choice(['\\n', ' ', ''])])
            if rng.random() < 0.6:
                sys.stdout.write(text)
            else:
                print(text, end='')
            (expected[open_now[-1][0]] if open_now else screen).append(text)
    while open_now:
        close(open_now.pop())
for name, parts in expected.items():
    if Path(name + '.txt').exists():
        with open(name + '.txt', newline='') as back:      # read back exactly, carriage returns too
            lost += back.read() != ''.join(parts)
Path(f'screen-{seed}.txt').write_text(''.join(screen))
Path(f'result-{seed}.txt').write_text(f'{lost} {len(expected)}')
'''


def test_300_sequences_of_opens_redirects_writes_and_closes_lose_nothing(tmp_path):
    phrase = " ".join(_phrase(random.Random(60), 12))
    lost = targets = sequences = screens_lost = 0
    for seed in range(30):
        code, out, _ = _run_child(tmp_path, _SEQUENCES, SEQ_SEED=str(seed), MADE_UP_CHILD_PHRASE=phrase,
                                  MADE_UP_CHILD_TOKEN=_made_up(random.Random(seed), 32))
        a, b = (tmp_path / f"result-{seed}.txt").read_text().split() if code == 0 else ("1", "0")
        lost, targets, sequences = lost + int(a), targets + int(b), sequences + 10
        screens_lost += out != (tmp_path / f"screen-{seed}.txt").read_text() if code == 0 else 1
    assert sequences == 300 and targets > 300 and lost == 0 and screens_lost == 0


def test_load_env_leaves_ordinary_settings_alone(tmp_path):
    rng = random.Random(61)
    settings = {"SEED_DATE": "2024-01-01", "MNEMONIC_LANGUAGE": "japanese", "MNEMONIC_WORDLIST": "bip39_english.txt",
                "RANDOM_SEED": "20261112", "APP_SCOPES": " ".join(_phrase(rng, 14)),
                "RELEASE_NOTE": " ".join(w.capitalize() + rng.choice([",", ".", "", ";"]) for w in _phrase(rng, 20))}
    f = tmp_path / "trading.env"
    f.write_text("".join(f"{k}={v}\n" for k, v in settings.items()))
    os.chmod(f, 0o600)
    s.load_env(f, environ={})
    caught = sorted(k for k, v in settings.items() if s.redact(f"x {v} y")[1])
    assert caught == []


def test_load_env_remembers_a_recovery_phrase_under_any_name(tmp_path):
    rng = random.Random(62)
    values = {}
    for name in ("WALLET", "BACKUP", "COLD_STORAGE", "METAMASK", "THING_SEVEN"):
        values[name + "_A"] = " ".join(_phrase(rng, 12))
        values[name + "_B"] = " ".join(_phrase(rng, 24))
    values["THING_EIGHT"] = " ".join(_phrase(rng, 13))                 # 13 words, no hint in the name: left alone
    f = tmp_path / "trading.env"
    f.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
    os.chmod(f, 0o600)
    s.load_env(f, environ={})
    caught = sorted(k for k, v in values.items() if s.redact(f"x {v} y")[1])
    assert caught == sorted(k for k in values if k != "THING_EIGHT")


def test_load_env_name_rules_for_credential_and_phrase_names(tmp_path):
    rng = random.Random(63)
    values = {"WALLET_SEED": " ".join(_phrase(rng, 12)), "BIP39_WORDS": " ".join(_phrase(rng, 4)),
              "RECOVERY_CODE": " ".join(_groups(rng, 4)), "BROKER_KEY": " ".join(_phrase(rng, 5) + ["w0rd"]),
              "CARD_KEY": " ".join(_groups(rng, 4)), "BACKUP_TOKEN": " ".join(_groups(rng, 5, True)),
              "MNEMONIC_SALT": _made_up(rng, 12) + "9", "SEED_HEX": _made_up(rng, 32, "0123456789abcdef"),
              "NOTE_TOKEN": " ".join(_phrase(rng, 3)), "WEBULL_ACCOUNT_ID": "acct" + _made_up(rng, 8, string.digits)}
    f = tmp_path / "trading.env"
    f.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
    os.chmod(f, 0o600)
    s.load_env(f, environ={})
    caught = sorted(k for k, v in values.items() if s.redact(f"x {v} y")[1])
    assert caught == ["BACKUP_TOKEN", "BIP39_WORDS", "CARD_KEY", "RECOVERY_CODE", "WALLET_SEED", "WEBULL_ACCOUNT_ID"]
    doc = s._looks_like_credential.__doc__.upper()
    listed = all(w in doc for w in ("KEY", "SECRET", "TOKEN", "PASSWORD", "PASS", "ACCOUNT", "MNEMONIC", "SEED", "PHRASE",
                                    "RECOVERY", "WORDS"))
    assert listed


def test_short_letters_inside_spaced_groups_are_not_a_phrase():
    rng = random.Random(64)
    groups = [_made_up(rng, 1, string.digits) + c + _made_up(rng, 2, string.digits) for c in "abcde"]
    s.remember(" ".join(groups))
    plain = [s.redact(text)[1] for text in ("list a, b, c, d here", "a b c d e", "(a) (b) (c) (d)")]
    _, whole = s.redact("card " + " ".join(groups[:4]) + " end")
    assert plain == [0, 0, 0] and whole == 1


def test_load_env_leaves_plain_words_under_credential_names_alone(tmp_path):
    settings = {"KEYBOARD_LAYOUT": "us english qwerty standard", "TOKEN_SCOPES": "read write trade admin",
                "API_KEY_NOTE": "rotate this key monthly", "WEBULL_ACCOUNT_LABEL": "my cash account 2"}
    f = tmp_path / "trading.env"
    f.write_text("".join(f"{k}={v}\n" for k, v in settings.items()))
    os.chmod(f, 0o600)
    s.load_env(f, environ={})
    caught = sorted(k for k, v in settings.items() if s.redact(f"x {v} y")[1])
    assert caught == []


# ---- rev 8 -------------------------------------------------------------------------------------

def test_load_env_leaves_spaced_lists_of_short_numbers_alone(tmp_path):
    rng = random.Random(70)
    values = {"API_KEY_BACKOFF": "1 2 4 8 16", "PASS_RETRIES": "3 5 8 13", "KEY_SIZES": "128 192 256 512",
              "CARD_KEY": " ".join(_groups(rng, 4)), "BACKUP_TOKEN": " ".join(_groups(rng, 5, True))}
    f = tmp_path / "trading.env"
    f.write_text("".join(f"{k}={v}\n" for k, v in values.items()))
    os.chmod(f, 0o600)
    s.load_env(f, environ={})
    caught = sorted(k for k, v in values.items() if s.redact(f"retry after {v} seconds")[1])
    plain = s.redact("retry after 1, 2, 4, 8 seconds")[1]
    assert caught == ["BACKUP_TOKEN", "CARD_KEY"] and plain == 0


# ---- T22d-2, B4: one WEBULL_ENV, said once (a repeated key is refused; a file that disagrees with the process is refused)

@pytest.mark.parametrize("text, key, lines", [
    ("WEBULL_ENV=sandbox\nWEBULL_ENV=production\n", "WEBULL_ENV", (1, 2)),                  # the verifier's reproduction
    ("# c\nA_KEY=one-value-here\n\nexport A_KEY=another-value-here\n", "A_KEY", (2, 4)),    # `export` names the same key
    ("X=1\nWEBULL_ENV = sandbox\nY=2\nWEBULL_ENV= production \n", "WEBULL_ENV", (2, 4)),
    ("A_SECRET=s3cr3t-value-0001\nB=2\nA_SECRET=s3cr3t-value-0001\n", "A_SECRET", (1, 3)),  # even with the same value
])
def test_a_key_repeated_in_the_file_is_refused_naming_the_key_and_both_lines_never_a_value(text, key, lines):
    with pytest.raises(s.SecretError) as err:
        s.parse_env(text)
    message = str(err.value)
    assert key in message and f"lines {lines[0]} and {lines[1]}" in message, message
    for value in ("sandbox", "production", "one-value-here", "another-value-here", "s3cr3t-value-0001"):
        assert value not in message


def test_a_key_repeated_in_the_file_is_refused_by_load_env_before_anything_is_loaded(tmp_path):
    f = tmp_path / "trading.env"
    f.write_text("A_KEY=first-value-abcdef\nWEBULL_ENV=sandbox\nWEBULL_ENV=production\n")
    os.chmod(f, 0o600)
    env = {}
    with pytest.raises(s.SecretError, match="WEBULL_ENV") as err:
        s.load_env(f, environ=env)
    assert env == {} and "production" not in str(err.value) and "first-value-abcdef" not in str(err.value)


def test_lines_that_are_not_settings_are_still_skipped_and_a_single_key_is_read_as_before():
    assert s.parse_env("# WEBULL_ENV=x\n# WEBULL_ENV=y\nbad line\n9BAD=1\n9BAD=2\nWEBULL_ENV='sandbox'\n") == {"WEBULL_ENV": "sandbox"}
    assert s.parse_env("") == {} and s.parse_env("A=1\nB=2") == {"A": "1", "B": "2"}


@pytest.mark.parametrize("override", [False, True])
@pytest.mark.parametrize("in_file, in_process", [("production", "sandbox"), ("sandbox", "production"), ("sandbox", " sandbox"),
                                                 ("sandbox", "")])
def test_a_webull_env_in_the_file_that_disagrees_with_the_process_is_refused(tmp_path, override, in_file, in_process):
    f = tmp_path / "trading.env"
    f.write_text(f"A_KEY=key-value-0123456789\nWEBULL_ENV={in_file}\n")
    os.chmod(f, 0o600)
    env = {"WEBULL_ENV": in_process}
    with pytest.raises(s.SecretError) as err:
        s.load_env(f, environ=env, override=override)
    message = str(err.value)
    assert "WEBULL_ENV" in message and "disagree" in message, message
    assert in_file not in message.replace("WEBULL_ENV", "") and "key-value-0123456789" not in message
    assert in_process.strip() == "" or in_process.strip() not in message.replace("WEBULL_ENV", "")
    assert env == {"WEBULL_ENV": in_process}                                     # nothing loaded


def test_a_webull_env_that_agrees_or_is_absent_on_one_side_loads_as_before(tmp_path):
    f = tmp_path / "trading.env"
    f.write_text("A_KEY=key-value-0123456789\nWEBULL_ENV=sandbox\n")
    os.chmod(f, 0o600)
    env = {"WEBULL_ENV": "sandbox"}
    assert s.load_env(f, environ=env) == ["A_KEY"] and env["WEBULL_ENV"] == "sandbox"
    env = {}
    assert s.load_env(f, environ=env) == ["A_KEY", "WEBULL_ENV"] and env["WEBULL_ENV"] == "sandbox"
    g = tmp_path / "other.env"
    g.write_text("A_KEY=key-value-0123456789\n")
    os.chmod(g, 0o600)
    env = {"WEBULL_ENV": "production"}
    assert s.load_env(g, environ=env) == ["A_KEY"] and env["WEBULL_ENV"] == "production"


# ---- T50: the live key pair lives in a file of its own (webull-live.env), read only by the live entry. Every value
# below is made up here; each check is computed on a line of its own so that a failure never prints a value.

LIVE_KEY, LIVE_SECRET = "ZqLiveKey7Rt4Kw9PmXv2Bn", "ZqLiveSecret3LbNHs4GdYp8"
GOOD_LIVE = f"WEBULL_LIVE_APP_KEY={LIVE_KEY}\nWEBULL_LIVE_APP_SECRET={LIVE_SECRET}\n"


def _live_file(folder, text=GOOD_LIVE, mode=0o600, folder_mode=0o700):
    folder.mkdir(parents=True, exist_ok=True)
    folder.chmod(folder_mode)
    p = folder / "webull-live.env"
    p.write_bytes(text if isinstance(text, bytes) else text.encode())
    p.chmod(mode)
    return p


def _refusal(path):
    """The LiveFileError's text, or None when the file was accepted."""
    try:
        s.read_live_pair(path)
    except s.LiveFileError as exc:
        return str(exc)
    return None


def _no_value(text):
    return LIVE_KEY not in text and LIVE_SECRET not in text


def _refused_with(why, words):
    """A refusal that holds neither value (checked first, so a failure never shows one) and names `words`."""
    clean = why is not None and _no_value(why) and why.startswith("webull-live.env ")
    assert clean, "accepted" if why is None else "the refusal holds a value or does not name the file"
    assert words in why, why


def test_t50_the_live_names_and_the_files_place_are_fixed_in_code():
    import inspect
    import pwd
    from pathlib import Path
    assert s.LIVE_NAMES == ("WEBULL_LIVE_APP_KEY", "WEBULL_LIVE_APP_SECRET")
    assert s.LIVE_ENV_FILE == Path(pwd.getpwuid(os.geteuid()).pw_dir) / ".hermes" / "secrets" / "webull-live.env"
    assert s.LIVE_ENV_FILE.parent == Path(pwd.getpwuid(os.geteuid()).pw_dir) / ".hermes" / "secrets"
    assert issubclass(s.LiveFileError, s.SecretError)
    assert inspect.signature(s.read_live_pair).parameters["path"].default is None
    for fn in (s.read_live_pair, s._live_fault, s._live_pairs):
        source = inspect.getsource(fn)
        assert "os.environ" not in source and "getenv" not in source and "VALEDIA_ENV_FILE" not in source, fn


def test_t50_a_good_live_file_gives_the_pair_and_nothing_enters_the_process_environment(tmp_path):
    before = dict(os.environ)
    p = _live_file(tmp_path / "secrets")
    pair = s.read_live_pair(p)
    right = pair == (LIVE_KEY, LIVE_SECRET)
    assert right
    unchanged = dict(os.environ) == before
    assert unchanged
    for i, text in enumerate((f"WEBULL_LIVE_APP_SECRET={LIVE_SECRET}\nWEBULL_LIVE_APP_KEY={LIVE_KEY}\n",   # either order
                              f"WEBULL_LIVE_APP_KEY={LIVE_KEY}\nWEBULL_LIVE_APP_SECRET={LIVE_SECRET}",     # no final LF
                              )):
        right = s.read_live_pair(_live_file(tmp_path / f"other{i}", text)) == (LIVE_KEY, LIVE_SECRET)
        assert right
    edge = ("a" * 8, "~" * 512)                                          # 8 and 512 printable ASCII characters
    right = s.read_live_pair(_live_file(tmp_path / "edge", f"WEBULL_LIVE_APP_KEY={edge[0]}\nWEBULL_LIVE_APP_SECRET={edge[1]}\n")) == edge
    assert right
    right = s.read_live_pair(_live_file(tmp_path / "ro", mode=0o400)) == (LIVE_KEY, LIVE_SECRET)
    assert right


def test_t50_the_live_values_are_registered_with_the_output_guard(tmp_path):
    s.read_live_pair(_live_file(tmp_path / "secrets"))
    masked, hits = s.redact(f"x-app-key={LIVE_KEY} and {LIVE_SECRET}.")
    hidden = _no_value(masked) and hits >= 2
    assert hidden
    child = s.child_env({"CARRIES_KEY": LIVE_KEY, "CARRIES_SECRET": f"prefix {LIVE_SECRET}", "PLAIN": "ok"})
    dropped = "CARRIES_KEY" not in child and "CARRIES_SECRET" not in child and child.get("PLAIN") == "ok"
    assert dropped


KEY_LINE, SECRET_LINE = f"WEBULL_LIVE_APP_KEY={LIVE_KEY}", f"WEBULL_LIVE_APP_SECRET={LIVE_SECRET}"


@pytest.mark.parametrize("text, words", [
    (GOOD_LIVE + "WEBULL_APP_KEY=ZqPracticeKey1234567\n", "line 3 names a setting other than"),
    (GOOD_LIVE + f"OTHER_NAME={LIVE_SECRET}\n", "line 3 names a setting other than"),
    (GOOD_LIVE + f"{KEY_LINE}x\n", "sets WEBULL_LIVE_APP_KEY more than once (lines 1 and 3)"),
    (f"WEBULL_LIVE_APP_KEY=\n{SECRET_LINE}\n", "WEBULL_LIVE_APP_KEY is empty"),
    (f"{KEY_LINE}\n", "WEBULL_LIVE_APP_SECRET is missing"),
    (GOOD_LIVE + f"{LIVE_SECRET}\n", "line 3 is not NAME=VALUE"),
    (f"{LIVE_KEY}={LIVE_SECRET}\n" + GOOD_LIVE, "line 1 names a setting other than"),
    ("", "is empty"),
    ("WEBULL_LIVE_APP_KEY=" + "a" * 5000 + "\n", "is larger than"),
    # T50 addendum 3 (B): the format is exact
    (f"# the owner's live pair\n{GOOD_LIVE}", "line 1 is a comment"),
    (f"{KEY_LINE}\n\n{SECRET_LINE}\n", "line 2 is blank"),
    (GOOD_LIVE + "\n", "line 3 is blank"),
    (f"export {KEY_LINE}\n{SECRET_LINE}\n", "line 1 starts with export"),
    (f'WEBULL_LIVE_APP_KEY="{LIVE_KEY}"\n{SECRET_LINE}\n', "line 1 holds a quote"),
    (f"{KEY_LINE}\nWEBULL_LIVE_APP_SECRET='{LIVE_SECRET}'\n", "line 2 holds a quote"),
    (f"WEBULL_LIVE_APP_KEY=Zq7Rt\"4Kw9PmXv\n{SECRET_LINE}\n", "line 1 holds a quote"),
    (f"WEBULL_LIVE_APP_KEY=Zq7Rt`4Kw9PmXv\n{SECRET_LINE}\n", "line 1 holds a quote"),
    (f"WEBULL_LIVE_APP_KEY=Zq7Rt 4Kw9PmXv\n{SECRET_LINE}\n", "line 1 holds a space or a tab"),
    (f"WEBULL_LIVE_APP_KEY= {LIVE_KEY}\n{SECRET_LINE}\n", "line 1 holds a space or a tab"),
    (f"{KEY_LINE} \n{SECRET_LINE}\n", "line 1 holds a space or a tab"),
    (f"{KEY_LINE}\n {SECRET_LINE}\n", "line 2 holds a space or a tab"),
    (f"WEBULL_LIVE_APP_KEY\t={LIVE_KEY}\n{SECRET_LINE}\n", "line 1 holds a space or a tab"),
    (f"{KEY_LINE}\r\n{SECRET_LINE}\r\n", "carriage return"),
    (f"{KEY_LINE}\n{SECRET_LINE}\r", "carriage return"),
    (b"\xef\xbb\xbf" + GOOD_LIVE.encode(), "begins with a byte-order mark"),
    (f"WEBULL_LIVE_APP_KEY=Zq7Rt\x014Kw9PmXv\n{SECRET_LINE}\n", "line 1 holds a byte that is not printable ASCII"),
    (f"WEBULL_LIVE_APP_KEY=Zq7Rt\x7f4Kw9PmXv\n{SECRET_LINE}\n", "line 1 holds a byte that is not printable ASCII"),
    (f"WEBULL_LIVE_APP_KEY={'é' * 10}\n{SECRET_LINE}\n", "line 1 holds a byte that is not printable ASCII"),
    (b"WEBULL_LIVE_APP_KEY=\xff\xfeZq7Rt4Kw9\n" + SECRET_LINE.encode() + b"\n", "line 1 holds a byte that is not printable ASCII"),
    (f"WEBULL_LIVE_APP_KEY=Zq7Rt4K\n{SECRET_LINE}\n", "WEBULL_LIVE_APP_KEY is shorter than 8 characters"),
    (f"{KEY_LINE}\nWEBULL_LIVE_APP_SECRET={'a' * 513}\n", "WEBULL_LIVE_APP_SECRET is longer than 512 characters"),
])
def test_t50_a_live_file_with_anything_but_the_two_names_once_each_is_refused_naming_the_fault(tmp_path, text, words):
    _refused_with(_refusal(_live_file(tmp_path / "secrets", text)), words)


@pytest.mark.parametrize("mode, words", [(0o644, "readable by its group or by others"), (0o640, "readable by its group"),
                                         (0o604, "readable by its group or by others"), (0o620, "writable by its group"),
                                         (0o602, "writable by its group or by others"), (0o666, "by its group or by others"),
                                         (0o000, "cannot be opened")])
def test_t50_a_live_file_that_is_not_the_owners_alone_is_refused(tmp_path, mode, words):
    _refused_with(_refusal(_live_file(tmp_path / "secrets", mode=mode)), words)


def test_t50_a_live_file_owned_by_another_user_is_refused(tmp_path, monkeypatch):
    p = _live_file(tmp_path / "secrets")
    real = os.geteuid()
    monkeypatch.setattr(os, "geteuid", lambda: real + 1)
    _refused_with(_refusal(p), "belongs to another user")


@pytest.mark.parametrize("folder_mode", [0o777, 0o775, 0o757])
def test_t50_a_live_file_in_a_folder_others_can_write_is_refused(tmp_path, folder_mode):
    _refused_with(_refusal(_live_file(tmp_path / "secrets", folder_mode=folder_mode)), "a folder that is not the owner's alone")


def test_t50_a_link_a_second_name_or_a_folder_is_refused_and_a_missing_file_is_named(tmp_path):
    real = _live_file(tmp_path / "real")
    links = tmp_path / "links"
    links.mkdir(mode=0o700)
    (links / "webull-live.env").symlink_to(real)
    _refused_with(_refusal(links / "webull-live.env"), "symbolic link")
    (tmp_path / "linked-folder").symlink_to(tmp_path / "real", target_is_directory=True)
    _refused_with(_refusal(tmp_path / "linked-folder" / "webull-live.env"), "symbolic link")
    second = tmp_path / "second"
    second.mkdir(mode=0o700)
    os.link(real, second / "webull-live.env")
    _refused_with(_refusal(second / "webull-live.env"), "more than one name")
    os.unlink(second / "webull-live.env")
    folder = tmp_path / "dir" / "webull-live.env"
    folder.mkdir(parents=True)
    _refused_with(_refusal(folder), "not a regular file")
    _refused_with(_refusal(tmp_path / "nowhere" / "webull-live.env"), "is missing")


def test_t50_a_path_whose_real_path_differs_is_refused(tmp_path, monkeypatch):
    _live_file(tmp_path / "secrets")
    why = _refusal(f"{tmp_path}/secrets/../secrets/webull-live.env")     # abspath folds it: the same file
    assert why is None
    real = os.path.realpath
    monkeypatch.setattr(os.path, "realpath", lambda p, *a, **k: real(p, *a, **k) + "-elsewhere")
    _refused_with(_refusal(tmp_path / "secrets" / "webull-live.env"), "real path")


def test_t50_the_live_names_in_reach_are_found_in_any_mapping_and_through_the_ordinary_file(tmp_path):
    assert s.live_names_in({"WEBULL_APP_KEY": "x", "WEBULL_ENV": "sandbox"}) == []
    assert s.live_names_in({"WEBULL_LIVE_APP_SECRET": ""}) == ["WEBULL_LIVE_APP_SECRET"]          # present, even empty
    assert s.live_names_in({"WEBULL_LIVE_APP_SECRET": "a", "WEBULL_LIVE_APP_KEY": "b"}) == list(s.LIVE_NAMES)
    f = tmp_path / "trading.env"
    f.write_text(f"WEBULL_APP_KEY=ZqPracticeKey1234567\nWEBULL_LIVE_APP_KEY={LIVE_KEY}\n")
    os.chmod(f, 0o600)
    env = {}
    with pytest.raises(s.LiveNameInReach):                       # addendum 3 (A): the loader refuses it, sets nothing
        s.load_env(f, environ=env)
    assert env == {}
    assert s.is_practice_environment({}) and s.is_practice_environment({"WEBULL_ENV": " Sandbox "})
    assert s.is_practice_environment({"WEBULL_ENV": ""})
    for word in ("production", "PRODUCTION", "live", "sandbox2", "paper"):
        assert not s.is_practice_environment({"WEBULL_ENV": word}), word


# ---- T50 addendum 3 (A): the ordinary loader never loads the live file and never places a live name in the environment

import sys  # noqa: E402

ORDINARY = "WEBULL_APP_KEY=ZqPracticeKey1234567\nWEBULL_APP_SECRET=ZqPracticeSecret98765\nWEBULL_ENV=sandbox\n"
_WATCHED: dict = {}                     # the live file's (device, inode) while a test watches it; empty otherwise
_OPENS: list = []


def _audit_live_opens(event, args):
    if event == "open" and args and _WATCHED.get("inode"):
        try:
            st = os.stat(args[0]) if isinstance(args[0], (str, bytes, os.PathLike)) else None
        except (OSError, ValueError, TypeError):
            st = None
        if st is not None and (st.st_dev, st.st_ino) == _WATCHED["inode"]:
            _OPENS.append("opened")


sys.addaudithook(_audit_live_opens)


@pytest.fixture
def live_place(tmp_path, monkeypatch):
    """The live file at its place (secrets.LIVE_ENV_FILE moved into tmp), its reader failing the test if called, and
    every open of it recorded (by device and inode, so a link or another spelling counts)."""
    live = _live_file(tmp_path / "home" / ".hermes" / "secrets")
    monkeypatch.setattr(s, "LIVE_ENV_FILE", live)
    monkeypatch.setattr(s, "read_live_pair", lambda *a, **k: pytest.fail("the live file was read by its reader"))
    st = os.stat(live)
    _OPENS.clear()
    _WATCHED["inode"] = (st.st_dev, st.st_ino)
    yield live
    _WATCHED.clear()
    _OPENS.clear()


def _ways(tmp_path, live):
    """The live file itself, a link to it, another spelling of it, a hard link to it, and a copy under another name."""
    links = tmp_path / "links"
    links.mkdir(mode=0o700, exist_ok=True)
    link = links / "trading.env"
    if not link.exists():
        link.symlink_to(live)
    copy = tmp_path / "copies" / "trading.env"
    if not copy.exists():
        copy.parent.mkdir(mode=0o700)
        copy.write_bytes(live.read_bytes())
        copy.chmod(0o600)
    return {"live": live, "link": link, "spelling": live.parent / ".." / "secrets" / ".." / "secrets" / live.name,
            "copy": copy}


WAYS = ("live", "link", "spelling", "copy")


@pytest.mark.parametrize("way", WAYS)
@pytest.mark.parametrize("override", [False, True])
def test_t50_a3_the_ordinary_loader_refuses_the_live_file_however_it_is_named_and_sets_nothing(tmp_path, live_place, way,
                                                                                              override):
    path = _ways(tmp_path, live_place)[way]
    _OPENS.clear()
    for env in ({"VALEDIA_ENV_FILE": str(path)}, {}):
        given = dict(env)
        with pytest.raises(s.LiveNameInReach) as exc:
            s.load_env(None if env else path, environ=env, override=override)
        assert env == given                                           # nothing set from that file
        why = str(exc.value)
        clean = _no_value(why)
        assert clean and ("webull-live.env" in why or "WEBULL_LIVE_APP_KEY" in why), why
    assert _OPENS == [] or way == "copy"                              # the live file itself is refused before reading


def test_t50_a3_a_hard_link_to_the_live_file_is_refused_before_it_is_read(tmp_path, live_place):
    other = tmp_path / "other"
    other.mkdir(mode=0o700)
    os.link(live_place, other / "trading.env")
    _OPENS.clear()
    env = {}
    with pytest.raises(s.LiveNameInReach) as exc:
        s.load_env(other / "trading.env", environ=env)
    assert env == {} and _OPENS == [] and "webull-live.env" in str(exc.value)


@pytest.mark.parametrize("extra", [f"WEBULL_LIVE_APP_KEY={LIVE_KEY}\n", f"export WEBULL_LIVE_APP_SECRET='{LIVE_SECRET}'\n",
                                   f"  WEBULL_LIVE_APP_KEY = {LIVE_KEY}\n", "# WEBULL_LIVE_APP_SECRET belongs elsewhere\n",
                                   f"WEBULL_LIVE_APP_SECRET={LIVE_SECRET}\nWEBULL_LIVE_APP_KEY={LIVE_KEY}\n"])
def test_t50_a3_an_ordinary_file_naming_a_live_name_anywhere_sets_nothing_at_all(tmp_path, extra):
    f = tmp_path / "trading.env"
    f.write_text(ORDINARY + extra)
    f.chmod(0o600)
    for override in (False, True):
        env = {"KEEP": "1"}
        with pytest.raises(s.LiveNameInReach) as exc:
            s.load_env(f, environ=env, override=override)
        assert env == {"KEEP": "1"}                                   # not even the ordinary names
        why = str(exc.value)
        clean = _no_value(why) and "ZqPractice" not in why
        assert clean and any(n in why for n in s.LIVE_NAMES), why
    if LIVE_KEY in extra:                                             # what was refused is still hidden from output
        masked, _ = s.redact(f"value {LIVE_KEY} here")
        assert LIVE_KEY not in masked


class _EnvWatch:
    """Each moment a live name or a live value is in the process environment: looked at after every write to it
    (os.environ's __setitem__ and os.putenv) and at every call of a stand-in (`look`)."""

    def __init__(self, monkeypatch):
        self.looks, self.seen = 0, []
        real_set, real_putenv = os._Environ.__setitem__, os.putenv
        watch = self

        def setitem(env, key, value):
            real_set(env, key, value)
            if env is os.environ:
                watch.look(f"set {key}")

        def putenv(key, value):
            real_putenv(key, value)
            k = key.decode() if isinstance(key, bytes) else str(key)
            v = value.decode(errors="replace") if isinstance(value, bytes) else str(value)
            if k in s.LIVE_NAMES or LIVE_KEY in v or LIVE_SECRET in v:
                watch.seen.append(("putenv", [k]))

        monkeypatch.setattr(os._Environ, "__setitem__", setitem)
        monkeypatch.setattr(os, "putenv", putenv)

    def look(self, where):
        self.looks += 1
        bad = sorted(k for k, v in os.environ.items() if k in s.LIVE_NAMES or LIVE_KEY in v or LIVE_SECRET in v)
        if bad:
            self.seen.append((where, bad))

    def stand_in(self, where, result=None):
        def call(*a, **k):
            self.look(where)
            return result() if callable(result) else result
        return call


def _entry_order_step(root, watch, monkeypatch):
    from valedia_trader.research_loop import cli
    monkeypatch.setattr(cli.order_step_cli, "_order_step_venue", watch.stand_in("the order step's venue builder"))
    return cli.main(["--root", str(root), "order-step"]) == 2


def _entry_event_practice(root, watch, monkeypatch):
    from valedia_trader.execution_and_risk import event_practice
    return event_practice.main(["--reconcile-only"], build_venue=watch.stand_in("the events venue builder"),
                               root=root) == 2


def _entry_events_probe(root, watch, monkeypatch):
    from valedia_trader.venues import webull_events
    return webull_events.main(["probe"], client=watch.stand_in("the events client"),
                              kalshi_fetch=watch.stand_in("kalshi", b"{}"), sleep=lambda s: None) == 2


def _entry_webull_check(root, watch, monkeypatch):
    from valedia_trader.venues import webull
    monkeypatch.setattr(webull.WebullVenue, "__init__", watch.stand_in("the venue's constructor"))
    monkeypatch.setattr(webull.WebullVenue, "check", watch.stand_in("the venue's check", {}))
    return webull.main(["check"]) == 2


def _entry_doctor_venues(root, watch, monkeypatch):
    from valedia_trader import doctor
    from valedia_trader.research_loop import LoopPaths
    monkeypatch.setattr(doctor, "live_mode_note", watch.stand_in("the live-mode note", "live mode: not judged here"))
    status, detail = doctor.check_venues(LoopPaths(root))
    watch.look("after the venues check")
    return status == doctor.FAIL and _no_value(detail)


def _entry_owner_page(root, watch, monkeypatch):
    from valedia_trader.research_loop import owner_page as op
    monkeypatch.setattr(op, "SECTIONS", [(n, i, watch.stand_in(f"section {n}", ([], ""))) for n, i, _ in op.SECTIONS])
    return op.main(["--root", str(root), "build", "--out", str(root / "page.html")]) == 2 and not (root / "page.html").exists()


ENTRIES = {"order step": _entry_order_step, "event practice": _entry_event_practice, "events probe": _entry_events_probe,
           "webull check": _entry_webull_check, "doctor venues": _entry_doctor_venues, "owner page": _entry_owner_page}


@pytest.mark.parametrize("way", WAYS)
@pytest.mark.parametrize("entry", list(ENTRIES))
def test_t50_a3_every_practice_entry_pointed_at_the_live_file_refuses_and_no_live_value_is_ever_in_the_environment(
        tmp_path, live_place, monkeypatch, capsys, entry, way):
    path = _ways(tmp_path, live_place)[way]
    root = tmp_path / "repo"
    root.mkdir()
    for name in (*s.LIVE_NAMES, "WEBULL_APP_KEY", "WEBULL_APP_SECRET", "WEBULL_ENV", "WEBULL_ACCOUNT_CLASS"):
        monkeypatch.setenv(name, "x")
        monkeypatch.delenv(name)                                      # the undo removes whatever a load puts there
    monkeypatch.setenv("VALEDIA_ENV_FILE", str(path))
    watch = _EnvWatch(monkeypatch)
    real_load = s.load_env

    def load_env(*a, **k):
        try:
            return real_load(*a, **k)
        finally:
            watch.look("after load_env")
    monkeypatch.setattr(s, "load_env", load_env)
    from valedia_trader.core import alerts
    monkeypatch.setattr(alerts, "load_env", load_env)                 # the owner page's loader, imported by name
    _OPENS.clear()
    refused = ENTRIES[entry](root, watch, monkeypatch)
    watch.look("after the entry")
    printed = capsys.readouterr()
    assert watch.seen == [], watch.seen                               # names only: never a value
    assert not set(s.LIVE_NAMES) & set(os.environ)
    clean = _no_value(printed.out + printed.err)
    assert clean and refused, (entry, way)
    assert watch.looks >= 1                                           # the last look; a refusal before any call is the aim
    assert _OPENS == [] or way == "copy", (entry, way, len(_OPENS))  # the live file itself is never opened


def test_t50_a3_a_file_named_like_the_live_file_is_refused_by_its_name_alone(tmp_path, live_place):
    other = tmp_path / "elsewhere"
    other.mkdir(mode=0o700)
    f = other / "webull-live.env"                                    # not the live file, and naming no live name
    f.write_text(ORDINARY)
    f.chmod(0o600)
    env = {}
    with pytest.raises(s.LiveNameInReach) as exc:
        s.load_env(f, environ=env)
    assert env == {} and "webull-live.env" in str(exc.value)
