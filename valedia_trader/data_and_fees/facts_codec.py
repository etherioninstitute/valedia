"""The stored forms of a company-facts answer, and the patch of the fields that changed. Pure: no database, no file, no
clock, no text format of its own (the store module writes and parses the canonical text and imports this one).

Three forms of a stored body (the store module tells plain text from bytes; this module reads only the bytes):
  full:            b"\\x01F" + one zlib stream (level 6) of the canonical text's UTF-8 bytes
  changed-fields:  b"\\x01D" + one zlib stream (level 6) of the patch's canonical text (UTF-8); it names no base: its base is
                   the row before it, and the store proves the result by the answer's hash
A stream is read with a cap on the text it may inflate to (MAX_TEXT_BYTES, eight times the store's 5 MB answer cap): a
stream that is incomplete, has bytes after its end, inflates past the cap or is not UTF-8 is Damaged, and so is any body
that does not begin with one of the two marks.

A PATCH maps a key to an EDIT, exactly one of:
  ["-"]           remove the key (it must be there)
  ["=", VALUE]    set the key, there or not, to VALUE
  ["~", PATCH]    the key is there and holds an object: apply PATCH inside it
Anything else is Damaged. Arrays are never edited inside: a changed array is set whole. Two values are THE SAME when their
canonical texts are equal: types identical, floats by repr, objects by keys and values, arrays by length and elements. So
1, 1.0 and true differ, as do 0, 0.0, -0.0 and false, and null is a value like any other.
"""
from __future__ import annotations

import zlib

__all__ = ["FULL", "CHANGED", "LEVEL", "MAX_TEXT_BYTES", "Damaged", "encode_full", "encode_changed", "decode", "same", "diff",
           "apply"]

FULL = b"\x01F"
CHANGED = b"\x01D"
LEVEL = 6
MAX_TEXT_BYTES = 8 * 5 * 1024 * 1024             # eight times the store's answer cap (the store passes its own, the same)


class Damaged(ValueError):
    """A stored body or patch that cannot be read whole."""


def encode_full(text: str) -> bytes:
    return FULL + zlib.compress(text.encode("utf-8"), LEVEL)


def encode_changed(patch_text: str) -> bytes:
    return CHANGED + zlib.compress(patch_text.encode("utf-8"), LEVEL)


def decode(body: bytes, cap: int = MAX_TEXT_BYTES) -> tuple[str, str]:
    """("F", text) for a full body, ("D", patch text) for a changed-fields body; Damaged for anything else. The stream
    inflates into at most `cap` + 1 bytes: one more than the cap is enough to refuse it."""
    if type(body) is not bytes or len(body) < 2 or body[:2] not in (FULL, CHANGED):
        raise Damaged("no known form")
    inflate = zlib.decompressobj()
    try:
        data = inflate.decompress(memoryview(body)[2:], cap + 1)
    except zlib.error:
        raise Damaged("not one zlib stream") from None
    if len(data) > cap or not inflate.eof or inflate.unused_data or inflate.unconsumed_tail:
        raise Damaged("not one whole zlib stream within the cap")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise Damaged("not UTF-8") from None
    return ("F" if body[:2] == FULL else "D"), text


def same(a, b) -> bool:
    """True when `a` and `b` have equal canonical texts (decided without writing either)."""
    t = type(a)
    if t is not type(b):
        return False
    if t is float:
        return float.__repr__(a) == float.__repr__(b)
    if t is dict:
        return len(a) == len(b) and all(k in b and same(v, b[k]) for k, v in a.items())
    if t is list:
        return len(a) == len(b) and all(same(x, y) for x, y in zip(a, b))
    return a == b


def diff(a: dict, b: dict) -> dict:
    """The patch that turns object `a` into object `b`."""
    out: dict = {}
    for k in a:
        if k not in b:
            out[k] = ["-"]
    for k, v in b.items():
        if k not in a:
            out[k] = ["=", v]
        elif type(a[k]) is dict and type(v) is dict:
            inner = diff(a[k], v)
            if inner:
                out[k] = ["~", inner]
        elif not same(a[k], v):
            out[k] = ["=", v]
    return out


def apply(a: dict, patch: dict) -> dict:
    """Object `a` with `patch` applied; `a` and `patch` are not changed. Damaged for any shape the patch rules do not name.
    A key added is put in sorted order (the order a canonical text's keys come back in when parsed), so an object read
    through patches has its keys in the very order its canonical text would give."""
    if type(a) is not dict or type(patch) is not dict:
        raise Damaged("a patch applies to an object only")
    out = dict(a)
    added = False
    for k, edit in patch.items():
        if type(edit) is not list or not edit:
            raise Damaged("not an edit")
        op = edit[0]
        if op == "-" and len(edit) == 1:
            if k not in out:
                raise Damaged("removes a key that is not there")
            del out[k]
        elif op == "=" and len(edit) == 2:
            added = added or k not in out
            out[k] = edit[1]
        elif op == "~" and len(edit) == 2 and type(edit[1]) is dict:
            if k not in out or type(out[k]) is not dict:
                raise Damaged("edits inside a key that holds no object")
            out[k] = apply(out[k], edit[1])
        else:
            raise Damaged("not an edit")
    return dict(sorted(out.items())) if added else out
