"""The decision model's one shared module: Elephant-1 on LM Studio, its constants and the pure list readers."""
import ast
from pathlib import Path

import pytest

from valedia_trader.core import decision_model as dm


def entry(instances=None, **kw):
    e = {"type": "decision", "decision_type": "openjev", "key": "elephant-1", "publisher": "debackerl",
         "format": "gguf", "size_bytes": 10151733984, "quantization": {"name": "Q8_0", "bits_per_weight": 8},
         "loaded_instances": [instance()] if instances is None else instances}
    e.update(kw)
    return e


def instance(name="elephant-1", **config):
    return {"id": name, "config": {"context_length": 8192, "eval_batch_size": 2048, "physical_batch_size": 2048,
                                   "parallel": 1, "flash_attention": True, **config}}


def test_the_constants():
    assert dm.URL == "http://127.0.0.1:1235"
    assert (dm.KEY, dm.PUBLISHER, dm.DECISION_TYPE, dm.QUANT) == ("elephant-1", "debackerl", "openjev", "Q8_0")
    assert dm.FILE == "~/.lmstudio/models/debackerl/elephant-1-Q8_0-GGUF/elephant-1.Q8_0.gguf"
    assert dm.FILE_SIZE == 9527504224
    assert dm.FILE_SHA256 == "652c102967efa9a1adf9c63ebaada547fba189888ca0680944aae595398fdf9d"
    assert dm.LISTED_SIZES == (10151733984, 9527504224)
    assert dm.INSTANCE == "elephant-1"
    assert (dm.SLOTS, dm.CONTEXT, dm.PHYSICAL_BATCH, dm.EVAL_BATCH) == (1, 8192, 2048, 2048)
    assert dm.TAG == "lmstudio:elephant-1@q8_0"


def test_it_imports_only_the_standard_library():
    tree = ast.parse(Path(dm.__file__).read_text())
    names = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    names |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert names <= {"__future__"}


def test_instances_reads_a_good_list_and_refuses_a_bad_one():
    other = {"key": "qwen/qwen3.8-27b", "type": "llm", "loaded_instances": [{"id": "qwen/qwen3.8-27b"}]}
    models, found = dm._instances({"models": [other, entry()]})
    assert len(models) == 2 and found == [(entry(), instance())]
    assert dm._instances({"models": [other, entry(instances=[])]})[1] == []
    assert dm._instances({"models": [{"key": "x"}]})[1] == []             # no loaded_instances: none loaded
    for bad in (None, [], {"data": []}, {"models": "x"}, {"models": ["x"]}, {"models": [entry(instances="x")]},
                {"models": [entry(instances=["x"])]}):
        with pytest.raises(ValueError):
            dm._instances(bad)


def test_the_file_and_the_instance_good_case():
    assert dm._the_file(entry()) is None
    assert dm._the_file(entry(size_bytes=9527504224)) is None                  # without the vision file
    assert dm._wrong(entry(), instance()) is None
    assert dm._wrong_found([(entry(), instance())]) is None


ENTRY_FAULTS = [("key", "elephant-1@q8_0"), ("key", "Elephant-1"), ("key", "elephant-1-2"), ("type", "llm"),
                ("decision_type", "nimble"), ("publisher", "someone"), ("quantization", {"name": "Q4_K_M"}),
                ("quantization", "Q8_0"), ("size_bytes", 9527503392), ("size_bytes", 10151733985),
                ("size_bytes", "10151733984"), ("size_bytes", 10151733984.0), ("size_bytes", True),
                ("size_bytes", None)]
ENTRY_FAULTS += [(k, "MISSING") for k in ("key", "type", "decision_type", "publisher", "quantization", "size_bytes")]


@pytest.mark.parametrize("field,value", ENTRY_FAULTS)
def test_each_single_fault_of_the_entry_is_refused(field, value):
    e = entry()
    if value == "MISSING":
        del e[field]
    else:
        e[field] = value
    assert dm._the_file(e) is not None
    assert dm._wrong(e, instance()) is not None


CONFIG_FAULTS = [(k, v) for k, good in (("context_length", 8192), ("parallel", 1), ("physical_batch_size", 2048),
                                         ("eval_batch_size", 2048))
                 for v in (good * 2, 512 if good != 512 else 1, str(good), float(good), True, None, "MISSING")]


@pytest.mark.parametrize("field,value", CONFIG_FAULTS)
def test_each_single_fault_of_the_instance_settings_is_refused(field, value):
    i = instance()
    if value == "MISSING":
        del i["config"][field]
    else:
        i["config"][field] = value
    assert dm._wrong(entry(), i) is not None and field in dm._wrong(entry(), i)
    assert dm._wrong(entry(), {"id": "elephant-1"}) is not None                 # no config at all


def test_more_than_one_instance_with_the_name_is_wrong():
    assert dm._wrong_found([(entry(), instance()), (entry(), instance())]) == "2 instances named elephant-1"
