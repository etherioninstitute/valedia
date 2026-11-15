"""Tests for the pure codec of the company-facts store (ticket T36a, D2, D4 and D10).

The codec holds the two stored encodings (a compressed full copy, a compressed patch of the changed fields) and the patch
itself: making it, applying it, and the rule of what counts as the same value. It has no SQLite, no file, no clock and no
JSON of its own; these tests use the store's own canonical JSON writer as the reference the codec must agree with.
"""
import ast
import random
import sys
import tracemalloc
import zlib
from pathlib import Path

import pytest

from valedia_trader.data_and_fees import company_facts as cf
from valedia_trader.data_and_fees import facts_codec as fc


# ---- generated JSON values, with the corners the ticket names

SCALARS = [None, True, False, 0, 0.0, -0.0, 1, 1.0, -1, 2, 2.0, 2 ** 64, 2 ** 70, -(2 ** 65), 10 ** 40, 1e-320, 5e-324, 1e308,
           -1e308, 0.1, 1e16, 1e15, 123.456, "", "0", "1", "true", "null", "x", "é", "ключ", "😀", "\u0000", "a\"b\\c", "z" * 300]
KEYS = ["", "-", "=", "~", "/", "a", "b", "c", "ключ", "😀", "k" * 300, " ", "0", "null"]


def gen(rnd, depth=0):
    r = rnd.random()
    if depth > 4 or r < 0.45:
        return rnd.choice(SCALARS)
    if r < 0.75:
        return {rnd.choice(KEYS): gen(rnd, depth + 1) for _ in range(rnd.randint(0, 5))}
    return [gen(rnd, depth + 1) for _ in range(rnd.randint(0, 4))]


def mutate(rnd, v, depth=0):
    """A value near `v`: some keys changed, added or removed, types swapped; sometimes `v` itself unchanged."""
    if rnd.random() < 0.15:
        return v
    if type(v) is dict and depth < 6:
        out = dict(v)
        for k in list(out):
            r = rnd.random()
            if r < 0.15:
                del out[k]
            elif r < 0.45:
                out[k] = mutate(rnd, out[k], depth + 1)
        for _ in range(rnd.randint(0, 2)):
            out[rnd.choice(KEYS)] = gen(rnd, depth + 1)
        return out
    if type(v) is list and rnd.random() < 0.7:
        out = [mutate(rnd, x, depth + 1) for x in v]
        if rnd.random() < 0.3:
            out.append(gen(rnd, depth + 1))
        return out
    return gen(rnd, depth)


def deep(n, leaf):
    v = leaf
    for i in range(n):
        v = {"d": v, "i": i}
    return v


def text_equal(a, b):
    return cf.canonical_json(a) == cf.canonical_json(b)


# ---- D2: the two encodings

def test_full_and_changed_fields_bodies_are_the_named_bytes_and_one_zlib_stream_of_level_6():
    text = cf.canonical_json({"a": [1, 2.5, None], "é": "😀"})
    full = fc.encode_full(text)
    assert full[:2] == b"\x01F" and full[2:] == zlib.compress(text.encode("utf-8"), 6)
    changed = fc.encode_changed('{"a":["-"]}')
    assert changed[:2] == b"\x01D" and changed[2:] == zlib.compress(b'{"a":["-"]}', 6)
    assert fc.decode(full) == ("F", text) and fc.decode(changed) == ("D", '{"a":["-"]}')
    assert fc.decode(fc.encode_full("")) == ("F", "")


def test_the_cap_on_inflated_text_is_eight_times_the_body_cap():
    assert fc.MAX_TEXT_BYTES == 8 * cf.MAX_BODY_BYTES == cf.MAX_TEXT_BYTES


GOOD = fc.encode_full('{"a":1}')


@pytest.mark.parametrize("body", [b"", b"\x01", b"\x01F", b"\x01D", b"\x00F" + GOOD[2:], b"\x02F" + GOOD[2:], b"\x01f" + GOOD[2:],
                                  b"\x01X" + GOOD[2:], GOOD[:-1], GOOD[:5], GOOD + b"\x00", GOOD + GOOD[2:], b"\x01F" + b"\x00" * 20,
                                  b"\x01F" + zlib.compress(b"\xff\xfe{}"), b"\x01D" + zlib.compress(b"\xc3"),
                                  b"\x01F" + zlib.compress(b"x", 6)[:-1], b"\x01F" + zlib.compress(b"ab")[:2] + b"\x00" * 8])
def test_a_body_of_no_known_form_or_a_damaged_stream_is_damaged(body):
    with pytest.raises(fc.Damaged):
        fc.decode(body)


@pytest.mark.parametrize("body", ['{"a":1}', bytearray(GOOD), memoryview(GOOD), None, 7])
def test_only_bytes_are_decoded(body):
    with pytest.raises(fc.Damaged):
        fc.decode(body)


def test_damaged_is_a_value_error():
    assert issubclass(fc.Damaged, ValueError)


@pytest.mark.parametrize("prefix", [b"\x01F", b"\x01D"])
def test_one_byte_flipped_anywhere_in_a_small_stream_is_damaged_or_gives_the_very_same_text(prefix):
    """zlib's own checks refuse nearly every flip. The exception: the unused padding bits after the last deflate code, which
    no inflater reads, so the stream gives the very same text (the store's hash then proves it; tested there). Never
    other text from a flip that zlib let through, never an exception other than Damaged."""
    for text in ('{"k":["=",1],"x":["-"]}', '{"v":1}', '{"a":[1,2,3],"b":{"c":null}}'):
        body = prefix + zlib.compress(text.encode(), 6)
        refused = same = 0
        for i in range(len(body)):
            for bit in (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0xFF):
                b = bytearray(body)
                b[i] ^= bit
                try:
                    got = fc.decode(bytes(b))
                except fc.Damaged:
                    refused += 1
                    continue
                if (i, bit) == (1, 0x02):                     # "F" and "D" differ in one bit: the other form, the same bytes
                    assert got == ("D" if prefix == fc.FULL else "F", text)
                    continue
                assert got == (chr(prefix[1]), text), (text, i, bit)
                assert i == len(body) - 5 and bit != 0xFF, (text, i, bit)          # the last deflate byte, padding bits only
                same += 1
        assert refused + same == 9 * len(body) - 1 and same <= 7


def test_a_stream_that_inflates_past_the_cap_is_refused_without_holding_more_than_the_cap():
    bomb = b"\x01F" + zlib.compress(b" " * (fc.MAX_TEXT_BYTES * 4), 9)          # 160 MB of text in about 160 KB
    assert len(bomb) < 400_000
    tracemalloc.start()
    try:
        with pytest.raises(fc.Damaged):
            fc.decode(bomb)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    # inflated in steps of at most 1 MB with a running total (addendum 3, H4): refusing holds about the cap plus one step,
    # never twice the cap and never the 160 MB the stream would inflate to
    assert peak < fc.MAX_TEXT_BYTES + 8 * 1024 * 1024, peak
    exact = b"\x01F" + zlib.compress(b" " * fc.MAX_TEXT_BYTES, 9)                 # exactly the cap: read
    assert fc.decode(exact) == ("F", " " * fc.MAX_TEXT_BYTES)
    over = b"\x01D" + zlib.compress(b" " * (fc.MAX_TEXT_BYTES + 1), 9)            # one byte past it: damaged
    with pytest.raises(fc.Damaged):
        fc.decode(over)


def test_decode_accepts_a_smaller_cap():
    body = fc.encode_full("x" * 100)
    assert fc.decode(body, 100) == ("F", "x" * 100)
    with pytest.raises(fc.Damaged):
        fc.decode(body, 99)


# ---- D4 and D10: the same value, the patch

def test_same_is_canonical_text_equality_not_python_equality():
    distinct = [1, 1.0, True, 0, 0.0, -0.0, False, None, "", [], {}, "1", [1], [1.0], {"a": 1}, {"a": 1.0}, {"a": True}, 2 ** 70,
                float(2 ** 70), 1e-320, 0.0]
    for a in distinct:
        for b in distinct:
            assert fc.same(a, b) == text_equal(a, b), (a, b)
    assert fc.same({"a": [1, {"b": None}]}, {"a": [1, {"b": None}]})
    assert not fc.same({"a": None}, {})
    assert not fc.same([1, 2], [2, 1]) and not fc.same({"a": 1}, {"b": 1})


def test_same_agrees_with_canonical_text_equality_on_20000_generated_pairs():
    rnd = random.Random(36)
    agreed = equal = 0
    for _ in range(20000):
        a = gen(rnd)
        b = mutate(rnd, a) if rnd.random() < 0.7 else gen(rnd)
        assert fc.same(a, b) == text_equal(a, b), (a, b)
        assert fc.same(a, a) and fc.same(b, b)
        agreed += 1
        equal += text_equal(a, b)
    assert agreed == 20000 and 1000 < equal < 19000


def test_same_calls_no_json_writer(monkeypatch):
    import json
    monkeypatch.setattr(json, "dumps", lambda *a, **k: (_ for _ in ()).throw(AssertionError("a JSON writer was called")))
    assert fc.same({"a": [1, 2.0]}, {"a": [1, 2.0]}) and not fc.same(1, 1.0)


def test_the_patch_follows_the_three_edits_of_the_ticket():
    a = {"gone": 1, "kept": [1, 2], "num": 1, "obj": {"x": 1, "y": {"z": 0}}, "arr": [1, {"q": 1}], "to_null": 5, "swap": {"o": 1}}
    b = {"kept": [1, 2], "num": 1.0, "obj": {"x": 1, "y": {"z": -0.0}}, "arr": [1, {"q": 2}], "to_null": None, "swap": [1],
         "new": {"n": []}}
    p = fc.diff(a, b)
    assert p == {"gone": ["-"], "num": ["=", 1.0], "obj": ["~", {"y": ["~", {"z": ["=", -0.0]}]}], "arr": ["=", [1, {"q": 2}]],
                 "to_null": ["=", None], "swap": ["=", [1]], "new": ["=", {"n": []}]}
    assert text_equal(fc.apply(a, p), b)
    assert fc.diff(a, a) == {} and fc.diff({}, {}) == {}
    assert fc.diff({"a": None}, {}) == {"a": ["-"]} and fc.diff({}, {"a": None}) == {"a": ["=", None]}
    assert fc.diff({"a": True}, {"a": 1}) == {"a": ["=", 1]} and fc.diff({"a": 0}, {"a": False}) == {"a": ["=", False]}
    assert fc.diff({"a": 0.0}, {"a": -0.0}) == {"a": ["=", -0.0]}


def test_an_object_read_through_patches_has_its_keys_in_the_order_its_canonical_text_gives():
    rnd = random.Random(99)
    done = 0
    while done < 5000:
        a, b = gen(rnd), None
        if type(a) is not dict:
            continue
        b = mutate(rnd, a)
        if type(b) is not dict:
            continue
        base = cf._loads_strict(cf.canonical_json(a))                    # keys in sorted order, as a reader parses them
        got = fc.apply(base, cf._loads_strict(cf.canonical_json(fc.diff(a, b))))
        import json
        assert json.dumps(got) == json.dumps(cf._loads_strict(cf.canonical_json(b))), (a, b)   # order kept, at every level
        done += 1


def test_apply_does_not_change_the_base_or_the_patch():
    a = {"o": {"x": 1}, "k": 2}
    p = {"o": ["~", {"x": ["=", 3]}], "k": ["-"]}
    a_text, p_text = cf.canonical_json(a), cf.canonical_json(p)
    assert fc.apply(a, p) == {"o": {"x": 3}}
    assert cf.canonical_json(a) == a_text and cf.canonical_json(p) == p_text


def test_diff_then_apply_gives_the_new_value_on_20000_generated_pairs_of_objects():
    rnd = random.Random(3636)
    done = 0
    while done < 20000:
        a = gen(rnd)
        if type(a) is not dict:
            continue
        b = mutate(rnd, a)
        if type(b) is not dict:
            continue
        p = fc.diff(a, b)
        assert text_equal(fc.apply(a, p), b), (a, b, p)
        assert (p == {}) == text_equal(a, b)
        assert text_equal(fc.apply(a, cf._loads_strict(cf.canonical_json(p))), b)          # through its own text too
        done += 1


def test_a_patch_200_levels_deep_is_made_and_applied():
    a, b = deep(200, 1), deep(200, 1.0)
    p = fc.diff(a, b)
    assert text_equal(fc.apply(a, p), b) and cf.canonical_json(p).count('"~"') == 199      # 199 objects above the change


@pytest.mark.parametrize("base, patch", [
    ({"a": 1}, {"b": ["-"]}),                                   # removing a key that is not there
    ({"a": 1}, {"a": ["~", {"x": ["-"]}]}),                       # a "~" on a key that holds no object
    ({"a": [1]}, {"a": ["~", {}]}),
    ({}, {"a": ["~", {}]}),                                      # a "~" on a missing key
    ({"a": {}}, {"a": ["~", [1]]}),                               # "~" with a patch that is not an object
    ({"a": 1}, {"a": ["+", 1]}),                                  # an unknown edit
    ({"a": 1}, {"a": ["-", 1]}), ({"a": 1}, {"a": ["="]}), ({"a": 1}, {"a": ["=", 1, 2]}), ({"a": 1}, {"a": []}),
    ({"a": 1}, {"a": "="}), ({"a": 1}, {"a": None}), ({"a": 1}, {"a": {"=": 1}}), ({"a": 1}, {"a": [1, 2]}),
    ({"a": {}}, {"a": ["~", {}, 1]}), ({"a": {"b": 1}}, {"a": ["~", {"c": ["-"]}]}),
    ({"a": 1}, ["a", ["-"]]), ({"a": 1}, None), ({"a": 1}, "x"),       # a patch that is not an object
    ([1], {}), (None, {}), ("x", {}),                             # a base that is not an object
])
def test_any_other_patch_shape_is_damaged(base, patch):
    with pytest.raises(fc.Damaged):
        fc.apply(base, patch)


# ---- D10: a pure module

def test_the_codec_is_pure_no_sqlite_no_file_no_clock_no_json_and_no_import_of_the_store():
    src = Path(fc.__file__).read_text()
    tree = ast.parse(src)
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            names.add("%s%s" % ("." * node.level, node.module or ""))
    assert names <= {"zlib", "__future__"}, names
    called = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert not called & {"open", "__import__", "exec", "eval", "compile"}, called
    assert "company_facts" not in src                       # (the package rule: nothing else in it names the store module)
    assert {"encode_full", "encode_changed", "decode", "same", "diff", "apply", "Damaged", "MAX_TEXT_BYTES"} <= set(dir(fc))


# ---- addendum 3, H1: no recursion at any depth or caller stack; H4: inflate in steps of at most 1 MB

def nest(n, leaf, kind="o"):
    """`leaf` under `n` levels: of objects ("o"), arrays ("a") or both in turn ("m")."""
    v = leaf
    for i in range(n):
        k = "oa"[i % 2] if kind == "m" else kind
        v = {"k": v} if k == "o" else [v]
    return v


def at_depth(n, fn):
    return fn() if n == 0 else at_depth(n - 1, fn)


def stack_depth():
    f, n = sys._getframe(), 0
    while f is not None:
        f, n = f.f_back, n + 1
    return n


def caller_depths():
    room = sys.getrecursionlimit() - stack_depth() - 40
    return [0, 100, 300, room]


@pytest.mark.parametrize("kind", ["o", "a", "m"])
@pytest.mark.parametrize("n", [1, 64, 65, 500, 990, 1500, 5000])
def test_same_diff_and_apply_give_the_same_result_at_any_nesting_and_any_caller_stack(n, kind):
    a, b = {"x": nest(n, 1, kind), "y": 0}, {"x": nest(n, 1.0, kind), "y": 0}
    for frames in caller_depths():
        assert at_depth(frames, lambda: fc.same(a, dict(a))) is True
        assert at_depth(frames, lambda: fc.same(a, b)) is False
        p = at_depth(frames, lambda: fc.diff(a, b))
        if kind == "o":                                           # objects all the way: a "~" per level above the change
            assert list(p) == ["x"] and p["x"][0] == "~"
        else:                                                     # an array is set whole where it starts
            assert p == {"x": ["=", b["x"]]} or p["x"][0] == "~"
        got = at_depth(frames, lambda: fc.apply(a, p))
        assert at_depth(frames, lambda: fc.same(got, b)) is True
        assert at_depth(frames, lambda: fc.apply(a, {})) == a


def test_the_codec_calls_none_of_its_own_functions_from_inside_themselves():
    tree = ast.parse(Path(fc.__file__).read_text())
    for fn in [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)]:
        inner = {c.func.id for c in ast.walk(fn) if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)}
        assert fn.name not in inner, fn.name                     # an explicit stack, never Python recursion


def test_deeper_than_counts_objects_and_arrays_as_levels_without_recursion():
    assert [fc.deeper_than(v, 0) for v in (None, 1, "x", {}, [], {"a": 1}, [1])] == [False] * 3 + [True] * 4
    assert fc.deeper_than({"a": [{}]}, 2) is True and fc.deeper_than({"a": [{}]}, 3) is False
    assert fc.deeper_than({"a": [1, {"b": []}]}, 3) is True and fc.deeper_than({"a": [1, {"b": []}]}, 4) is False
    for kind in ("o", "a", "m"):
        assert fc.deeper_than(nest(64, 1, kind), 64) is False and fc.deeper_than(nest(65, 1, kind), 64) is True
        deep = nest(100_000, 1, kind)
        assert at_depth(caller_depths()[-1], lambda: fc.deeper_than(deep, 64)) is True
        assert fc.deeper_than(deep, 100_000) is False and fc.deeper_than(deep, 99_999) is True


def test_a_stream_is_inflated_in_steps_of_at_most_one_mb(monkeypatch):
    seen = []
    real = zlib.decompressobj

    class Spy:
        def __init__(self):
            self.d = real()

        def decompress(self, data, max_length=0):
            seen.append(max_length)
            return self.d.decompress(data, max_length)

        def __getattr__(self, name):
            return getattr(self.d, name)
    monkeypatch.setattr(fc.zlib, "decompressobj", Spy)
    for size in (0, 1, 1024 * 1024 - 1, 1024 * 1024, 1024 * 1024 + 1, 2 * 1024 * 1024, 3 * 1024 * 1024 + 17):
        text = "".join(chr(97 + i % 26) for i in range(min(size, 4096))) * (size // 4096) + "q" * (size % 4096)
        assert len(text) == size
        del seen[:]
        assert fc.decode(fc.encode_full(text)) == ("F", text)
        assert seen and all(0 < m <= 1024 * 1024 for m in seen), seen[:5]
        assert fc.decode(fc.encode_full(text), size) == ("F", text)
        if size:
            with pytest.raises(fc.Damaged):
                fc.decode(fc.encode_full(text), size - 1)
    del seen[:]
    with pytest.raises(fc.Damaged):
        fc.decode(b"\x01F" + zlib.compress(b" " * (fc.MAX_TEXT_BYTES * 2), 9))
    assert 40 <= len(seen) <= 42 and all(0 < m <= 1024 * 1024 for m in seen)     # it stops at the cap
