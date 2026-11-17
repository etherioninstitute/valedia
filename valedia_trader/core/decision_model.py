"""The decision model: Elephant-1 (GGUF Q8_0) on the local LM Studio, one instance of it, and the pure functions
that read LM Studio's model list (`GET /api/v1/models`) to tell whether that instance is loaded as it must be.

One copy of these values for every user (the order-book collector; later the panel and the doctor). Standard
library only; nothing from the project.

Measured on 2026-11-16 (the lead's probe): the model's entry in the list has key `elephant-1`, type `decision`,
decision_type `openjev`, publisher `debackerl`, quantisation Q8_0 and size_bytes 10151733984 (the model file plus
a vision file LM Studio pairs with it; 9527504224 without it). An instance loaded through the REST call
`POST /api/v1/models/load` takes the model's key as its id: there is no private identifier.
"""
from __future__ import annotations

URL = "http://127.0.0.1:1235"                   # LM Studio, loopback
KEY = "elephant-1"
PUBLISHER = "debackerl"
DECISION_TYPE = "openjev"
QUANT = "Q8_0"
FILE = "~/.lmstudio/models/debackerl/elephant-1-Q8_0-GGUF/elephant-1.Q8_0.gguf"   # home expanded at use
FILE_SIZE = 9527504224
FILE_SHA256 = "652c102967efa9a1adf9c63ebaada547fba189888ca0680944aae595398fdf9d"
LISTED_SIZES = (10151733984, 9527504224)        # the entry's size_bytes, with and without the vision file
INSTANCE = "elephant-1"                         # the instance's id: the model's key
SLOTS = 1
CONTEXT = 8192
PHYSICAL_BATCH = 2048                           # at the default 512 every typed call fails
EVAL_BATCH = 2048
TAG = "lmstudio:elephant-1@q8_0"                # what a record stores as the model's tag


def _int_is(x, v: int) -> bool:
    return type(x) is int and x == v


def _instances(listing) -> tuple[list[dict], list[tuple[dict, dict]]]:
    """The entries of `GET /api/v1/models`, and (entry, instance) for every loaded instance named INSTANCE.
    ValueError for a list that is not of the expected shape."""
    models = listing.get("models") if isinstance(listing, dict) else None
    if not isinstance(models, list) or not all(isinstance(e, dict) for e in models):
        raise ValueError("no list of model entries")
    found = []
    for e in models:
        inst = e.get("loaded_instances", [])
        if not isinstance(inst, list) or not all(isinstance(i, dict) for i in inst):
            raise ValueError("loaded_instances of %r is not a list of objects" % e.get("key"))
        found += [(e, i) for i in inst if i.get("id") == INSTANCE]
    return models, found


def _the_file(e: dict) -> str | None:
    """Why this entry is not the model's file (None when it is): its key is KEY exactly (the instance is named
    after it), it is a decision model of DECISION_TYPE from PUBLISHER, in QUANT, of one of LISTED_SIZES."""
    q = e.get("quantization")
    size = e.get("size_bytes")
    if e.get("key") != KEY:
        return "key %r, not %s" % (e.get("key"), KEY)
    if e.get("type") != "decision":
        return "type %r, not 'decision'" % e.get("type")
    if e.get("decision_type") != DECISION_TYPE:
        return "decision_type %r, not %s" % (e.get("decision_type"), DECISION_TYPE)
    if e.get("publisher") != PUBLISHER:
        return "publisher %r, not %s" % (e.get("publisher"), PUBLISHER)
    if not isinstance(q, dict) or q.get("name") != QUANT:
        return "quantisation %r, not %s" % (q.get("name") if isinstance(q, dict) else q, QUANT)
    if type(size) is not int or size not in LISTED_SIZES:
        return "size_bytes %r, not one of %s" % (size, LISTED_SIZES)
    return None


def _wrong(e: dict, i: dict) -> str | None:
    """Why this loaded instance is not the one to call (None when it is): its entry is the file and its settings
    are CONTEXT, SLOTS, PHYSICAL_BATCH and EVAL_BATCH."""
    why = _the_file(e)
    if why:
        return why
    cfg = i.get("config") if isinstance(i.get("config"), dict) else {}
    for name, want in (("context_length", CONTEXT), ("parallel", SLOTS), ("physical_batch_size", PHYSICAL_BATCH),
                       ("eval_batch_size", EVAL_BATCH)):
        if not _int_is(cfg.get(name), want):
            return "%s %r, not %d" % (name, cfg.get(name), want)
    return None


def _wrong_found(found: list[tuple[dict, dict]]) -> str | None:
    """Why the instances named INSTANCE (from `_instances`, at least one) are not exactly one right instance."""
    if len(found) > 1:
        return "%d instances named %s" % (len(found), INSTANCE)
    return _wrong(*found[0])
