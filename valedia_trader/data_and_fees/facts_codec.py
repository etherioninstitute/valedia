"""The stored forms of a company-facts answer, and the patch of the fields that changed. Pure: no database, no file, no
clock, no text format of its own (the store module writes and parses the canonical text and imports this one).

Three forms of a stored body (the store module tells plain text from bytes; this module reads only the bytes):
  full:            b"\\x01F" + one zlib stream (level 6) of the canonical text's UTF-8 bytes
  changed-fields:  b"\\x01D" + one zlib stream (level 6) of the patch's canonical text (UTF-8); it names no base: its base is
                   the row before it, and the store proves the result by the answer's hash
A stream is read with a cap on the text it may inflate to (MAX_TEXT_BYTES, eight times the store's 5 MB answer cap), in
steps of at most INFLATE_STEP (1 MB) with a running total, so refusing one holds about the cap plus one step: a stream that
is incomplete, has bytes after its end, inflates past the cap or is not UTF-8 is Damaged, and so is any body that does not
begin with one of the two marks.

A PATCH maps a key to an EDIT, exactly one of:
  ["-"]           remove the key (it must be there)
  ["=", VALUE]    set the key, there or not, to VALUE
  ["~", PATCH]    the key is there and holds an object: apply PATCH inside it
Anything else is Damaged. Arrays are never edited inside: a changed array is set whole. Two values are THE SAME when their
canonical texts are equal: types identical, floats by repr, objects by keys and values, arrays by length and elements. So
1, 1.0 and true differ, as do 0, 0.0, -0.0 and false, and null is a value like any other.

No function here recurses in Python: `same`, `diff`, `apply` and `deeper_than` walk with an explicit stack, so what they
give does not depend on how deep a value nests or on how deep the caller's stack is. `deeper_than` counts each object and
each array as one level (a scalar is level 0).
"""
from __future__ import annotations

import zlib

__all__ = ["FULL", "CHANGED", "LEVEL", "MAX_TEXT_BYTES", "Damaged", "encode_full", "encode_changed", "decode", "same", "diff",
           "apply", "deeper_than", "INFLATE_STEP"]

FULL = b"\x01F"
CHANGED = b"\x01D"
LEVEL = 6
INFLATE_STEP = 1024 * 1024                       # the most a stream is inflated by in one step
MAX_TEXT_BYTES = 8 * 5 * 1024 * 1024             # eight times the store's answer cap (the store passes its own, the same)


class Damaged(ValueError):
    """A stored body or patch that cannot be read whole."""


def encode_full(text: str) -> bytes:
    return FULL + zlib.compress(text.encode("utf-8"), LEVEL)


def encode_changed(patch_text: str) -> bytes:
    return CHANGED + zlib.compress(patch_text.encode("utf-8"), LEVEL)


def decode(body: bytes, cap: int = MAX_TEXT_BYTES) -> tuple[str, str]:
    """("F", text) for a full body, ("D", patch text) for a changed-fields body; Damaged for anything else. The stream is
    inflated in steps of at most INFLATE_STEP with a running total and refused as soon as the total passes `cap`."""
    if type(body) is not bytes or len(body) < 2 or body[:2] not in (FULL, CHANGED):
        raise Damaged("no known form")
    inflate = zlib.decompressobj()
    parts: list = []
    total = 0
    data = memoryview(body)[2:]
    try:
        while not inflate.eof:
            step = min(INFLATE_STEP, cap + 1 - total)
            out = inflate.decompress(data, step)
            parts.append(out)
            total += len(out)
            if total > cap:
                raise Damaged("inflates past the cap")
            data = inflate.unconsumed_tail
            if not data and len(out) < step:
                break                                       # all input used and no output held back: the stream ends here
    except zlib.error:
        raise Damaged("not one zlib stream") from None
    if not inflate.eof or inflate.unused_data or inflate.unconsumed_tail:
        raise Damaged("not one whole zlib stream within the cap")
    try:
        text = b"".join(parts).decode("utf-8")
    except UnicodeDecodeError:
        raise Damaged("not UTF-8") from None
    return ("F" if body[:2] == FULL else "D"), text


def same(a, b) -> bool:
    """True when `a` and `b` have equal canonical texts (decided without writing either)."""
    todo = [(a, b)]
    while todo:
        x, y = todo.pop()
        t = type(x)
        if t is not type(y):
            return False
        if t is float:
            if float.__repr__(x) != float.__repr__(y):
                return False
        elif t is dict:
            if len(x) != len(y):
                return False
            for k, v in x.items():
                if k not in y:
                    return False
                todo.append((v, y[k]))
        elif t is list:
            if len(x) != len(y):
                return False
            todo.extend(zip(x, y))
        elif x != y:
            return False
    return True


def diff(a: dict, b: dict) -> dict:
    """The patch that turns object `a` into object `b`."""
    root: dict = {}
    todo = [(a, b, root)]                                   # each object pair still to compare, and the patch it fills
    nested = []                                             # (patch of the parent, key, inner patch), parents first
    while todo:
        x, y, out = todo.pop()
        for k in x:
            if k not in y:
                out[k] = ["-"]
        for k, v in y.items():
            if k not in x:
                out[k] = ["=", v]
            elif type(x[k]) is dict and type(v) is dict:
                inner: dict = {}
                out[k] = None                               # its place in the key order, filled or removed below
                nested.append((out, k, inner))
                todo.append((x[k], v, inner))
            elif not same(x[k], v):
                out[k] = ["=", v]
    for out, k, inner in reversed(nested):                  # children before parents: an empty inner patch is no entry
        if inner:
            out[k] = ["~", inner]
        else:
            del out[k]
    return root


def apply(a: dict, patch: dict) -> dict:
    """Object `a` with `patch` applied; `a` and `patch` are not changed. Damaged for any shape the patch rules do not name.
    A key added is put in sorted order (the order a canonical text's keys come back in when parsed), so an object read
    through patches has its keys in the very order its canonical text would give."""
    if type(a) is not dict or type(patch) is not dict:
        raise Damaged("a patch applies to an object only")
    # each frame: [the object being made, the patch's remaining edits, whether a key was added, parent frame, key in it]
    stack = [[dict(a), iter(patch.items()), False, None, None]]
    while True:
        frame = stack[-1]
        out = frame[0]
        for k, edit in frame[1]:
            if type(edit) is not list or not edit:
                raise Damaged("not an edit")
            op = edit[0]
            if op == "-" and len(edit) == 1:
                if k not in out:
                    raise Damaged("removes a key that is not there")
                del out[k]
            elif op == "=" and len(edit) == 2:
                frame[2] = frame[2] or k not in out
                out[k] = edit[1]
            elif op == "~" and len(edit) == 2 and type(edit[1]) is dict:
                if k not in out or type(out[k]) is not dict:
                    raise Damaged("edits inside a key that holds no object")
                stack.append([dict(out[k]), iter(edit[1].items()), False, frame, k])
                break
            else:
                raise Damaged("not an edit")
        else:
            stack.pop()
            done = dict(sorted(out.items())) if frame[2] else out
            if frame[3] is None:
                return done
            frame[3][0][frame[4]] = done


def deeper_than(value, levels: int) -> bool:
    """True when `value` nests more than `levels` levels (each object and each array is one level; a scalar is 0)."""
    todo = [(value, 0)]
    while todo:
        v, d = todo.pop()
        t = type(v)
        if t is dict or t is list:
            d += 1
            if d > levels:
                return True
            todo.extend((x, d) for x in (v.values() if t is dict else v))
    return False
