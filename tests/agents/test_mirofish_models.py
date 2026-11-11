import json
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from valedia_trader.agents import mirofish_models as mm

SRC = mm.REPO / "src"
DUMP = ("import json, mirofish_native_model as m; print(json.dumps({'profile': m.ACTIVE_MODEL_PROFILE, "
        "'weight_file': str(m.WEIGHT_FILE), 'weight_bytes': m.WEIGHT_BYTES, 'weight_sha256': m.WEIGHT_SHA256, "
        "'tokenizer_root': str(m.TOKENIZER_ROOT), 'key': m.GGUF_SELECTION['modelKey'], "
        "'sel_bytes': m.GGUF_SELECTION['sizeBytes'], 'sel_sha': m.GGUF_SELECTION['sourceSha256'], "
        "'script_has_pin': str(m.WEIGHT_BYTES) in m.SDK_SCRIPT and m.WEIGHT_SHA256 in m.SDK_SCRIPT, "
        "'placeholders_left': '__WEIGHT' in m.SDK_SCRIPT}))")


def native_view(tmp_path, profile_name=None):
    """Import the native model module from a private copy, with an optional profile file beside it."""
    for name in ("mirofish_native_model.py", "mirofish_native_gateway.py"):
        shutil.copy2(SRC / name, tmp_path / name)                # a registry placed in tmp_path by the test is read too
    if profile_name is not None:
        (tmp_path / "mirofish_model_profile.json").write_text(json.dumps({"profile": profile_name}))
    return subprocess.run([sys.executable, "-B", "-c", DUMP], cwd=tmp_path, capture_output=True, text=True)


def test_aliases_and_resolution():
    assert mm.resolve("Qwen3-9b").name == "qwen3-8b" and mm.resolve("small").name == "qwen3-8b"
    assert mm.resolve("27B").name == "qwen3.8-27b" and mm.resolve(" qwen3.8-27b ").name == "qwen3.8-27b"
    assert set(mm.ALIASES.values()) <= set(mm.PROFILES)
    with pytest.raises(KeyError):
        mm.resolve("gpt-9")


def test_use_writes_the_switch_and_active_reads_it(tmp_path):
    f = tmp_path / "profile.json"
    assert mm.active(f, environ={}).name == mm.DEFAULT_PROFILE == "qwen3.8-27b"
    assert mm.use("8b", f).name == "qwen3-8b"
    assert json.loads(f.read_text())["profile"] == "qwen3-8b" and mm.active(f, environ={}).name == "qwen3-8b"
    assert mm.active(f, environ={mm.ENV_VAR: "27b"}).name == "qwen3.8-27b"     # per-process override
    mm.use("large", f)
    assert mm.active(f, environ={}).name == "qwen3.8-27b"
    f.write_text(json.dumps({"profile": "nonsense"}))
    with pytest.raises(KeyError):
        mm.active(f, environ={})


@pytest.mark.parametrize("name", ["qwen3.8-27b", "qwen3-8b"])
def test_native_pipeline_follows_the_switch_and_pins_match(tmp_path, name):
    out = native_view(tmp_path, name)
    assert out.returncode == 0, out.stderr[-400:]
    seen, p = json.loads(out.stdout), mm.PROFILES[name]
    assert seen["profile"] == name and seen["key"] == p.lmstudio_key
    assert seen["weight_file"] == str(p.weight_file) and seen["tokenizer_root"] == str(p.tokenizer_root)
    assert seen["weight_bytes"] == seen["sel_bytes"] == p.weight_bytes
    assert seen["weight_sha256"] == seen["sel_sha"] == p.weight_sha256
    assert seen["script_has_pin"] and not seen["placeholders_left"]


def test_native_default_is_the_reference_checkpoint_and_bad_profiles_are_refused(tmp_path):
    out = native_view(tmp_path)
    assert json.loads(out.stdout)["profile"] == "qwen3.8-27b"
    bad = native_view(tmp_path, "qwen9-900b")
    assert bad.returncode != 0 and "unknown MiroFish model profile" in bad.stderr


def test_backend_env_per_backend():
    small, large = mm.PROFILES["qwen3-8b"], mm.PROFILES["qwen3.8-27b"]
    assert mm.backend_env(small, "ollama") == {
        "LLM_BASE_URL": "http://127.0.0.1:11434/v1", "LLM_MODEL_NAME": "qwen3:8b",
        "LLM_REPORT_BASE_URL": "http://127.0.0.1:11434/v1", "MIROFISH_REPORT_MODEL": "qwen3:8b"}
    assert mm.backend_env(large)["LLM_MODEL_NAME"] == "qwen3.8-27b"
    assert mm.backend_env(large, base_url="http://127.0.0.1:1235/v1")["LLM_BASE_URL"].endswith(":1235/v1")
    with pytest.raises(ValueError):
        mm.backend_env(large, "ollama")                       # no Ollama build of the 27B model
    with pytest.raises(ValueError):
        mm.backend_env(small, "vllm")


def test_apply_env_file_changes_only_model_keys(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# MiroFish\nLLM_API_KEY=keep-me\nLLM_BASE_URL=https://old.example/v1\nLLM_MODEL_NAME=old-model\n"
                   "NEO4J_PASSWORD=also-keep\n")
    keys = mm.apply_env_file(env, mm.PROFILES["qwen3-8b"], "ollama")
    text = env.read_text()
    assert "LLM_API_KEY=keep-me" in text and "NEO4J_PASSWORD=also-keep" in text and "# MiroFish" in text
    assert "LLM_MODEL_NAME=qwen3:8b" in text and "LLM_BASE_URL=http://127.0.0.1:11434/v1" in text
    assert "old-model" not in text and "MIROFISH_REPORT_MODEL=qwen3:8b" in text and len(keys) == 4
    assert stat.S_IMODE(env.stat().st_mode) == 0o600
    backups = list(tmp_path.glob(".env.bak-*"))
    assert len(backups) == 1 and "old-model" in backups[0].read_text()
    with pytest.raises(FileNotFoundError):
        mm.apply_env_file(tmp_path / "missing.env", mm.PROFILES["qwen3-8b"])


@pytest.mark.parametrize("name", ["qwen3.8-27b", "qwen3-8b"])
def test_profiles_are_ready_on_this_machine(name):
    p = mm.PROFILES[name]
    if not p.weight_file.exists():
        pytest.skip(f"{p.weight_file} is not on this machine")
    report = mm.verify(p, probe_servers=False)
    assert report["weights_size_ok"] and report["tokenizer_files_missing"] == [] and report["ready"]


def test_verify_reports_missing_assets(tmp_path):
    ghost = mm.ModelProfile("ghost", "", tmp_path / "none.gguf", 1, "0" * 64, tmp_path, "ghost", None, 1, False)
    report = mm.verify(ghost, probe_servers=False)
    assert not report["ready"] and not report["weights_present"] and len(report["tokenizer_files_missing"]) == 5
    assert mm.main(["use", "not-a-model"]) == 2
    assert Path(mm.PROFILE_FILE).name == "mirofish_model_profile.json"


# ---- registered models

LISTING = {"my-gguf": {"modelKey": "my-gguf", "format": "gguf", "path": "me/My-GGUF/model.gguf",
                       "indexedModelIdentifier": "me/My-GGUF/model.gguf", "publisher": "me", "architecture": "qwen3",
                       "quantization": {"name": "Q4_K_M", "bits": 4}, "sizeBytes": 2048, "vision": False,
                       "trainedForToolUse": True, "maxContextLength": 40960, "deviceIdentifier": "somewhere"},
           "org/mlx-model": {"modelKey": "org/mlx-model", "format": "safetensors", "sizeBytes": 10, "maxContextLength": 262144}}


def assets(tmp_path, size=2048):
    weights = tmp_path / "model.gguf"
    weights.write_bytes(b"g" * size)
    tok = tmp_path / "tokenizer"
    tok.mkdir(exist_ok=True)
    for f in mm.TOKENIZER_FILES:
        (tok / f).write_text(json.dumps({"model_type": "qwen3"}) if f == "config.json" else "{}")
    return weights, tok


def test_add_registers_a_native_model_with_pins_and_use_selects_it(tmp_path):
    reg, switch = tmp_path / "registry.json", tmp_path / "profile.json"
    weights, tok = assets(tmp_path)
    p = mm.add("My-Model", "my-gguf", weights, tok, description="a test checkpoint", listing=LISTING, registry_file=reg)
    assert p.name == "my-model" and p.native and not p.builtin and p.weight_bytes == 2048 and len(p.weight_sha256) == 64
    assert p.extra["gguf_selection"]["modelKey"] == "my-gguf" and p.extra["gguf_selection"]["deviceIdentifier"] is None
    assert p.extra["tokenizer_model_type"] == "qwen3" and p.max_context == 40960
    assert set(mm.profiles(reg)) == {"qwen3-8b", "qwen3.8-27b", "my-model"}
    assert mm.use("my-model", switch, reg).name == "my-model" and mm.active(switch, {}, reg).name == "my-model"
    assert mm.active(switch, {mm.ENV_VAR: "8b"}, reg).name == "qwen3-8b"            # per-process override still wins
    assert mm.verify(p, check_hash=True, probe_servers=False)["ready"]
    weights.write_bytes(b"x" * 2048)                                               # same size, different bytes
    assert mm.verify(mm.resolve("my-model", reg), check_hash=True, probe_servers=False)["weights_sha256_ok"] is False
    with pytest.raises(mm.ProfileError, match="in use"):
        mm.remove("my-model", switch, reg)
    mm.use("27b", switch, reg)
    mm.remove("my-model", switch, reg)
    assert "my-model" not in mm.profiles(reg)
    with pytest.raises(KeyError):
        mm.remove("my-model", switch, reg)
    with pytest.raises(KeyError, match="register it with"):
        mm.resolve("my-model", reg)


def test_http_only_models_can_be_selected_but_are_not_native(tmp_path):
    reg = tmp_path / "registry.json"
    p = mm.add("mlx-27b", "org/mlx-model", listing=LISTING, registry_file=reg, description="MLX build")
    q = mm.add("llama-tag", ollama_tag="llama9:70b", max_context=8192, listing={}, registry_file=reg)
    assert not p.native and p.weight_file is None and p.max_context == 262144 and q.ollama_tag == "llama9:70b"
    assert mm.backend_env(p)["LLM_MODEL_NAME"] == "org/mlx-model" and mm.backend_env(q, "ollama")["LLM_MODEL_NAME"] == "llama9:70b"
    report = mm.verify(p, probe_servers=False)
    assert report == {"profile": "mlx-27b", "native": False, "ready": False}        # no server asked: not known to be ready
    assert mm.resolve("mlx-27b", reg).name == "mlx-27b"


@pytest.mark.parametrize("kwargs, message", [
    ({"name": "X"}, "profile name"), ({"name": "qwen3-8b", "lmstudio_key": "k"}, "built-in"),
    ({"name": "small", "lmstudio_key": "k"}, "built-in"), ({"name": "nokey"}, "lmstudio-key or --ollama-tag"),
    ({"name": "half", "lmstudio_key": "my-gguf", "weights": "W"}, "both --weights and --tokenizer"),
    ({"name": "notgguf", "lmstudio_key": "my-gguf", "weights": "TOK", "tokenizer": "TOK"}, "existing .gguf"),
    ({"name": "wrongkey", "lmstudio_key": "org/mlx-model", "weights": "W", "tokenizer": "TOK"}, "lists as a GGUF"),
    ({"name": "notok", "lmstudio_key": "my-gguf", "weights": "W", "tokenizer": "EMPTY"}, "tokenizer directory lacks"),
])
def test_add_refuses_incomplete_or_mismatched_models(tmp_path, kwargs, message):
    weights, tok = assets(tmp_path)
    (tmp_path / "empty").mkdir()
    swap = {"W": weights, "TOK": tok, "EMPTY": tmp_path / "empty"}
    kwargs = {k: swap.get(v, v) for k, v in kwargs.items()}
    with pytest.raises(mm.ProfileError, match=message):
        mm.add(listing=LISTING, registry_file=tmp_path / "registry.json", **kwargs)
    assert not (tmp_path / "registry.json").exists()


def test_add_checks_the_file_is_the_one_the_server_lists_and_fits_the_native_context(tmp_path):
    weights, tok = assets(tmp_path, size=999)
    with pytest.raises(mm.ProfileError, match="sizes differ"):
        mm.add("other-file", "my-gguf", weights, tok, listing=LISTING, registry_file=tmp_path / "r.json")
    weights, tok = assets(tmp_path)
    small = {"my-gguf": {**LISTING["my-gguf"], "maxContextLength": 8192}}
    with pytest.raises(mm.ProfileError, match="context 32768"):
        mm.add("short-context", "my-gguf", weights, tok, listing=small, registry_file=tmp_path / "r.json")
    (tmp_path / "bad.json").write_text("[]")
    with pytest.raises(mm.ProfileError, match="not a model registry"):
        mm.profiles(tmp_path / "bad.json")


def test_native_pipeline_runs_a_registered_checkpoint_and_refuses_http_only_ones(tmp_path):
    reg = tmp_path / "mirofish_model_registry.json"                                # the name the native module reads
    weights, tok = assets(tmp_path)
    p = mm.add("my-model", "my-gguf", weights, tok, listing=LISTING, registry_file=reg)
    mm.add("mlx-27b", "org/mlx-model", listing=LISTING, registry_file=reg)
    mm.add("qwen3-8b-copy", "my-gguf", weights, tok, listing=LISTING, registry_file=reg)
    out = native_view(tmp_path, "my-model")
    assert out.returncode == 0, out.stderr[-400:]
    seen = json.loads(out.stdout)
    assert seen["profile"] == "my-model" and seen["key"] == "my-gguf" and seen["weight_file"] == str(p.weight_file)
    assert seen["weight_bytes"] == seen["sel_bytes"] == 2048 and seen["weight_sha256"] == seen["sel_sha"] == p.weight_sha256
    assert seen["script_has_pin"] and not seen["placeholders_left"]
    refused = native_view(tmp_path, "mlx-27b")
    assert refused.returncode != 0 and "HTTP backends only" in refused.stderr
    builtin = native_view(tmp_path, "qwen3-8b")                                    # a registry cannot shadow a built-in
    assert json.loads(builtin.stdout)["weight_sha256"] == mm.PROFILES["qwen3-8b"].weight_sha256


def test_cli_add_use_status_remove(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(mm, "REGISTRY_FILE", tmp_path / "registry.json")
    monkeypatch.setattr(mm, "PROFILE_FILE", tmp_path / "profile.json")
    monkeypatch.setattr(mm, "lmstudio_listing", lambda lms=None: LISTING)
    monkeypatch.setattr(mm, "_served_ids", lambda url, timeout: ["org/mlx-model"] if "1235" in url else [])
    assert mm.main(["add", "mlx-27b", "--lmstudio-key", "org/mlx-model", "--description", "MLX build"]) == 0
    assert "registered mlx-27b (HTTP only)" in capsys.readouterr().out
    assert mm.main(["use", "mlx-27b"]) == 0 and "HTTP only; the native pipeline will refuse it" in capsys.readouterr().out
    assert mm.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "active: mlx-27b" in out and "http-only" in out and "qwen3.8-27b" in out
    assert mm.main(["remove", "mlx-27b"]) == 2                                      # in use
    assert mm.main(["use", "27b"]) in (0, 1) and mm.main(["remove", "mlx-27b"]) == 0
    assert mm.main(["add", "bad name!", "--lmstudio-key", "k"]) == 2
