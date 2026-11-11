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
        shutil.copy2(SRC / name, tmp_path / name)
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
