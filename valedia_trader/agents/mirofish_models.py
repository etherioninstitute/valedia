"""One switch for the model MiroFish runs on, for any registered model.

    python -m valedia_trader.agents.mirofish_models status
    python -m valedia_trader.agents.mirofish_models use qwen3-8b        # small and fast
    python -m valedia_trader.agents.mirofish_models use qwen3.8-27b     # large and deep
    python -m valedia_trader.agents.mirofish_models add my-model --lmstudio-key KEY [--weights F.gguf --tokenizer DIR]
    python -m valedia_trader.agents.mirofish_models remove my-model
    python -m valedia_trader.agents.mirofish_models verify --hash

`use` writes src/mirofish_model_profile.json in one atomic step; the next MiroFish run or candidate
picks it up, and VALEDIA_MIROFISH_MODEL overrides it for a single process. A run that has already
been prepared keeps the model it was prepared with.

Two kinds of profile:

* native: a GGUF checkpoint pinned by size and SHA-256 with its tokenizer origin. The native
  pipeline can run it. `add` computes the pins when given --weights and --tokenizer.
* HTTP only: any model a local OpenAI-compatible server offers (an MLX build in LM Studio, an Ollama
  tag). The HTTP backend path can use it; the native pipeline refuses it by name.

The built-in profiles are fixed here and in src/mirofish_native_model.py (a test keeps them in
step). Registered profiles live in src/mirofish_model_registry.json, which that module also reads.
A registered native profile's files lie under MODEL_ROOT after resolving links, and a registry that
names a built-in profile or alias, or has a row of the wrong shape, is refused as a whole.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from valedia_trader.core.secrets import child_env

__all__ = ["ModelProfile", "PROFILES", "ALIASES", "DEFAULT_PROFILE", "PROFILE_FILE", "REGISTRY_FILE", "ENV_VAR", "resolve",
           "profiles", "active", "use", "add", "remove", "verify", "backend_env", "apply_env_file", "BACKEND_URLS",
           "TOKENIZER_FILES", "lmstudio_listing", "ProfileError"]

REPO = Path(__file__).resolve().parents[2]
PROFILE_FILE = REPO / "src" / "mirofish_model_profile.json"
REGISTRY_FILE = REPO / "src" / "mirofish_model_registry.json"
ENV_VAR = "VALEDIA_MIROFISH_MODEL"
MODEL_ROOT = Path("/Volumes/NIMROD_SSD/Qwen-Weight-Space/models")
TOKENIZER_FILES = ("config.json", "tokenizer.json", "tokenizer_config.json", "chat_template.jinja",
                   "model.safetensors.index.json")
# The local LM Studio-compatible server on this machine listens on 1235.
BACKEND_URLS = {"lmstudio": "http://127.0.0.1:1235/v1", "ollama": "http://127.0.0.1:11434/v1"}
NATIVE_CONTEXT = 32768          # the native pipeline loads every checkpoint at this context length
_NAME = re.compile(r"[a-z0-9][a-z0-9._-]{1,40}")
_SELECTION_KEYS = ("modelKey", "path", "indexedModelIdentifier", "format", "publisher", "architecture", "quantization",
                   "deviceIdentifier", "vision", "trainedForToolUse", "maxContextLength")
_SHA256 = re.compile(r"[0-9a-f]{64}")


class ProfileError(ValueError):
    pass


@dataclass(frozen=True)
class ModelProfile:
    name: str
    description: str
    weight_file: Path | None
    weight_bytes: int | None
    weight_sha256: str | None
    tokenizer_root: Path | None
    lmstudio_key: str | None
    ollama_tag: str | None
    max_context: int
    native_reference: bool = False   # the checkpoint the native pipeline was built and reviewed on
    native: bool = True              # pinned GGUF the native pipeline can run
    builtin: bool = True
    extra: dict = field(default_factory=dict, compare=False)


PROFILES: dict[str, ModelProfile] = {
    "qwen3-8b": ModelProfile(
        "qwen3-8b", "Qwen3 8B Q4_K_M: small and fast; MiroFish's original prep/simulation model",
        MODEL_ROOT / "lmstudio-library/Qwen/Qwen3-8B/Qwen3-8B-Q4_K_M.gguf", 5225374496,
        "a3de86cd1c132c822487ededd47a324c50491393e6565cd14bafa40d0b8e686f",
        MODEL_ROOT / "Qwen3-8B-tokenizer", "qwen3-8b", "qwen3:8b", 40960),
    "qwen3.8-27b": ModelProfile(
        "qwen3.8-27b", "Qwen3.8 27B Q4_K_M: large and deep; the native pipeline's reference checkpoint",
        MODEL_ROOT / "ggml-org/Qwen3.8-27B-GGUF/Qwen3.8-27B-Q4_K_M.gguf", 18973870528,
        "c600de0300ae8a0eb3a6c0b8b5561b8b96f16bd2c863c2a66c42de29d391a747",
        MODEL_ROOT / "Qwen3.8-27B-MLX-4bit", "qwen3.8-27b", None, 262144, native_reference=True),
    "ternary-bonsai-2-27b": ModelProfile(
        "ternary-bonsai-2-27b", "Ternary Bonsai 2 27B Q2_0: the 27B's structure at half its memory; LM Studio also loads its "
        "vision projector (loaded and unused); the native pipeline's setup and report model in a two-model plan",
        MODEL_ROOT / "lmstudio-library/prism-ml/Ternary-Bonsai-2-27B-gguf/Ternary-Bonsai-2-27B-Q2_0.gguf", 7626008928,
        "4f99aed01b8a877e153f9aa6569a4440fe17c59701cc0953e36d7f460549e70e",
        MODEL_ROOT / "Qwen3.8-27B-MLX-4bit", "ternary-bonsai-2-27b", None, 262144),
}
DEFAULT_PROFILE = "qwen3.8-27b"
ALIASES = {
    "8b": "qwen3-8b", "qwen3:8b": "qwen3-8b", "qwen3-9b": "qwen3-8b", "qwen3:9b": "qwen3-8b", "small": "qwen3-8b",
    "fast": "qwen3-8b", "27b": "qwen3.8-27b", "qwen3.8:27b": "qwen3.8-27b", "large": "qwen3.8-27b",
    "deep": "qwen3.8-27b",
}


# ---- the registry of further models

def _under_root(value, what: str) -> Path:
    """A registered native file or folder: an absolute path that lies under MODEL_ROOT after resolving links."""
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise ProfileError(f"{what} must be an absolute path")
    resolved, root = Path(value).resolve(), MODEL_ROOT.resolve()
    if resolved == root or not resolved.is_relative_to(root):
        raise ProfileError(f"{what} lies outside the models root {root} after resolving links")
    return resolved


def _check_row(file: str, name, row) -> None:
    if not isinstance(name, str) or not _NAME.fullmatch(name):
        raise ProfileError(f"{file}: profile name {str(name)[:64]!r} has the wrong shape")
    if name in PROFILES or name in ALIASES:
        raise ProfileError(f"{file}: '{name}' is a built-in profile or alias, which a registry cannot override")
    if not isinstance(row, dict) or type(row.get("native")) is not bool:
        raise ProfileError(f"{file}: profile '{name}' needs native true or false")
    if not row["native"]:
        return
    _under_root(row.get("weight_file"), f"{file}: profile '{name}' weight_file")
    _under_root(row.get("tokenizer_root"), f"{file}: profile '{name}' tokenizer_root")
    if type(row.get("weight_bytes")) is not int or row["weight_bytes"] <= 0:
        raise ProfileError(f"{file}: profile '{name}' weight_bytes must be a positive integer")
    if not isinstance(row.get("weight_sha256"), str) or not _SHA256.fullmatch(row["weight_sha256"]):
        raise ProfileError(f"{file}: profile '{name}' weight_sha256 must be 64 lowercase hexadecimal characters")
    if not isinstance(row.get("gguf_selection"), dict) or set(row["gguf_selection"]) != set(_SELECTION_KEYS):
        raise ProfileError(f"{file}: profile '{name}' gguf_selection must have exactly the fields {list(_SELECTION_KEYS)}")


def _read_registry(registry_file: Path | None) -> dict:
    path = Path(registry_file or REGISTRY_FILE)
    if not path.exists():
        return {"profiles": {}}
    try:
        data = json.loads(path.read_text())
    except (ValueError, UnicodeError):
        raise ProfileError(f"{path.name} is not a model registry (invalid JSON)") from None
    if not isinstance(data, dict) or not isinstance(data.get("profiles"), dict):
        raise ProfileError(f"{path.name} is not a model registry")
    for name, row in data["profiles"].items():
        _check_row(path.name, name, row)
    return data


def _write_registry(registry_file: Path | None, data: dict) -> None:
    path = Path(registry_file or REGISTRY_FILE)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def _from_row(name: str, row: dict) -> ModelProfile:
    return ModelProfile(
        name, row.get("description", ""), Path(row["weight_file"]) if row.get("weight_file") else None,
        row.get("weight_bytes"), row.get("weight_sha256"), Path(row["tokenizer_root"]) if row.get("tokenizer_root") else None,
        row.get("lmstudio_key"), row.get("ollama_tag"), int(row.get("max_context") or 0), False, bool(row.get("native")),
        False, {k: row[k] for k in ("gguf_selection", "tokenizer_model_type", "tokenizer_quant_bits", "added_at") if k in row})


def profiles(registry_file: Path | None = None) -> dict[str, ModelProfile]:
    """Every selectable profile: the built-in ones, then the registered ones (a registry that names a
    built-in is refused)."""
    out = dict(PROFILES)
    for name, row in _read_registry(registry_file)["profiles"].items():
        out[name] = _from_row(name, row)
    return out


def resolve(name: str, registry_file: Path | None = None) -> ModelProfile:
    """Profile for a name or alias (case-insensitive)."""
    key = str(name).strip().lower()
    key = ALIASES.get(key, key)
    known = profiles(registry_file)
    if key not in known:
        raise KeyError(f"unknown MiroFish model '{name}'; choose one of {sorted(known)} or register it with `add`")
    return known[key]


def active(profile_file: Path | None = None, environ=None, registry_file: Path | None = None) -> ModelProfile:
    """The selected profile: the environment variable for this process if set, else the profile
    file, else the default. Prepared native candidates only ever see the file."""
    env = os.environ if environ is None else environ
    profile_file = profile_file or PROFILE_FILE
    if env.get(ENV_VAR, "").strip():
        return resolve(env[ENV_VAR], registry_file)
    if Path(profile_file).exists():
        return resolve(json.loads(Path(profile_file).read_text()).get("profile", ""), registry_file)
    return PROFILES[DEFAULT_PROFILE]


def use(name: str, profile_file: Path | None = None, registry_file: Path | None = None) -> ModelProfile:
    """Select a profile by writing the profile file atomically."""
    profile = resolve(name, registry_file)
    path = Path(profile_file or PROFILE_FILE)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"profile": profile.name, "set_at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
                              indent=2) + "\n")
    tmp.replace(path)
    return profile


def lmstudio_listing(lms: str | None = None) -> dict[str, dict]:
    """LM Studio's own record of the models on disk, by model key (empty if the CLI is missing)."""
    exe = lms or str(Path.home() / ".lmstudio/bin/lms")
    try:
        out = subprocess.run([exe, "ls", "--json"], capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL,
                             env=child_env())
        return {m["modelKey"]: m for m in json.loads(out.stdout) if isinstance(m, dict) and m.get("modelKey")}
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return {}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 22), b""):
            digest.update(chunk)
    return digest.hexdigest()


def add(name: str, lmstudio_key: str | None = None, weights: str | Path | None = None, tokenizer: str | Path | None = None,
        ollama_tag: str | None = None, description: str = "", max_context: int | None = None,
        listing: dict[str, dict] | None = None, registry_file: Path | None = None) -> ModelProfile:
    """Register a model. With `weights` (a GGUF) and `tokenizer` (a directory holding TOKENIZER_FILES)
    the profile is native: the weights are hashed now and pinned. Without them it is HTTP only and
    needs a key or tag a local server offers."""
    key = str(name).strip().lower()
    if not _NAME.fullmatch(key):
        raise ProfileError("a profile name is 2 to 41 characters of a-z, 0-9, dot, dash or underscore")
    if key in PROFILES or key in ALIASES:
        raise ProfileError(f"'{key}' is a built-in profile or alias")
    if not lmstudio_key and not ollama_tag:
        raise ProfileError("give --lmstudio-key or --ollama-tag: a profile must name something a server can load")
    if bool(weights) != bool(tokenizer):
        raise ProfileError("a native profile needs both --weights and --tokenizer; leave both out for an HTTP-only profile")
    registry = _read_registry(registry_file)
    entry = (lmstudio_listing() if listing is None else listing).get(lmstudio_key or "", {})
    row: dict = {"description": description or f"registered {datetime.now(timezone.utc).date()}", "lmstudio_key": lmstudio_key,
                 "ollama_tag": ollama_tag, "max_context": int(max_context or entry.get("maxContextLength") or 0), "native": False,
                 "added_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    if weights:
        weight_file, tokenizer_root = Path(weights).expanduser().resolve(), Path(tokenizer).expanduser().resolve()
        for path in (weight_file, tokenizer_root):
            if path == MODEL_ROOT.resolve() or not path.is_relative_to(MODEL_ROOT.resolve()):
                raise ProfileError(f"native files must lie under the models root {MODEL_ROOT.resolve()}: {path}")
        if weight_file.suffix != ".gguf" or not weight_file.is_file():
            raise ProfileError("the native pipeline runs a single GGUF file; --weights must be an existing .gguf")
        missing = [f for f in TOKENIZER_FILES if not (tokenizer_root / f).is_file()]
        if missing:
            raise ProfileError(f"tokenizer directory lacks {missing}")
        if not lmstudio_key or entry.get("format") != "gguf":
            raise ProfileError("a native profile needs an --lmstudio-key that LM Studio lists as a GGUF model")
        if entry.get("sizeBytes") != weight_file.stat().st_size:
            raise ProfileError("--weights is not the file LM Studio lists under that key (sizes differ)")
        if row["max_context"] < NATIVE_CONTEXT:
            raise ProfileError(f"the native pipeline loads at context {NATIVE_CONTEXT}; this model allows {row['max_context']}")
        config = json.loads((tokenizer_root / "config.json").read_text())
        selection = {k: entry.get(k) for k in _SELECTION_KEYS}
        selection["deviceIdentifier"] = None
        row.update(native=True, weight_file=str(weight_file), weight_bytes=weight_file.stat().st_size,
                   weight_sha256=_sha256(weight_file), tokenizer_root=str(tokenizer_root),
                   tokenizer_model_type=config.get("model_type"),
                   tokenizer_quant_bits=(config.get("quantization") or {}).get("bits"), gguf_selection=selection)
    registry["profiles"][key] = row
    _write_registry(registry_file, registry)
    return _from_row(key, row)


def remove(name: str, profile_file: Path | None = None, registry_file: Path | None = None) -> None:
    """Forget a registered profile. Built-in profiles and the profile in use cannot be removed."""
    key = str(name).strip().lower()
    profile_file = profile_file or PROFILE_FILE
    registry = _read_registry(registry_file)
    if key in PROFILES:
        raise ProfileError("built-in profiles cannot be removed")
    if key not in registry["profiles"]:
        raise KeyError(f"'{name}' is not a registered profile")
    if Path(profile_file).exists() and json.loads(Path(profile_file).read_text()).get("profile") == key:
        raise ProfileError(f"'{key}' is the profile in use; switch to another one first")
    del registry["profiles"][key]
    _write_registry(registry_file, registry)


def _served_ids(base_url: str, timeout: float) -> list[str] | None:
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/models", timeout=timeout) as resp:
            return [str(m.get("id")) for m in json.load(resp).get("data", [])]
    except (urllib.error.URLError, OSError, ValueError):
        return None


def verify(profile: ModelProfile, check_hash: bool = False, probe_servers: bool = True, timeout: float = 2.0) -> dict:
    """Readiness report. Native profile: weights present at the pinned size, optional SHA-256 check,
    tokenizer files present. HTTP-only profile: some local server lists the model. Either way the
    report says which local servers currently list it."""
    report: dict = {"profile": profile.name, "native": profile.native}
    if profile.native:
        exists = profile.weight_file.is_file()
        report.update(weights_present=exists,
                      weights_size_ok=bool(exists and profile.weight_file.stat().st_size == profile.weight_bytes),
                      tokenizer_files_missing=[f for f in TOKENIZER_FILES if not (profile.tokenizer_root / f).is_file()])
        if check_hash and exists:
            report["weights_sha256_ok"] = _sha256(profile.weight_file) == profile.weight_sha256
    served = {}
    if probe_servers:
        for backend, url in BACKEND_URLS.items():
            wanted = profile.lmstudio_key if backend == "lmstudio" else profile.ollama_tag
            ids = _served_ids(url, timeout)
            served[backend] = None if ids is None or wanted is None else wanted in ids
        report["served"] = served
    if profile.native:
        report["ready"] = bool(report["weights_size_ok"] and not report["tokenizer_files_missing"]
                               and report.get("weights_sha256_ok", True))
    else:
        report["ready"] = any(v is True for v in served.values()) if probe_servers else False
    return report


def backend_env(profile: ModelProfile, backend: str = "lmstudio", base_url: str | None = None) -> dict[str, str]:
    """Environment for a MiroFish backend that talks to an OpenAI-compatible local server."""
    if backend not in BACKEND_URLS:
        raise ValueError(f"backend must be one of {sorted(BACKEND_URLS)}")
    model = profile.lmstudio_key if backend == "lmstudio" else profile.ollama_tag
    if model is None:
        raise ValueError(f"profile '{profile.name}' is not available on {backend}")
    url = base_url or BACKEND_URLS[backend]
    return {"LLM_BASE_URL": url, "LLM_MODEL_NAME": model, "LLM_REPORT_BASE_URL": url, "MIROFISH_REPORT_MODEL": model}


def apply_env_file(env_path: str | Path, profile: ModelProfile, backend: str = "lmstudio",
                   base_url: str | None = None) -> list[str]:
    """Point a MiroFish backend .env at `profile`. Only the model/base-URL keys change; a timestamped
    backup is kept and the file ends up mode 600. Returns the keys written."""
    path = Path(env_path)
    if not path.is_file():
        raise FileNotFoundError(path)
    wanted = backend_env(profile, backend, base_url)
    lines = path.read_text().splitlines()
    seen: set[str] = set()
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].replace("export ", "", 1).strip() if "=" in line and not line.lstrip().startswith("#") else None
        if key in wanted:
            lines[i] = f"{key}={wanted[key]}"
            seen.add(key)
    lines += [f"{k}={v}" for k, v in wanted.items() if k not in seen]
    backup = path.with_name(path.name + ".bak-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(path, backup)
    os.chmod(backup, 0o600)
    path.write_text("\n".join(lines) + "\n")
    os.chmod(path, 0o600)
    return sorted(wanted)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="mirofish_models", description="Select the model MiroFish runs on.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="show the active profile and readiness of every profile")
    p_use = sub.add_parser("use", help="switch profile")
    p_use.add_argument("name", help="a built-in or registered profile (aliases: 8b, small, 27b, large)")
    p_use.add_argument("--env-file", help="also update this MiroFish backend .env")
    p_use.add_argument("--backend", default="lmstudio", choices=sorted(BACKEND_URLS))
    p_add = sub.add_parser("add", help="register another model")
    p_add.add_argument("name")
    p_add.add_argument("--lmstudio-key", help="model key as `lms ls` shows it")
    p_add.add_argument("--ollama-tag")
    p_add.add_argument("--weights", help="GGUF file (makes the profile native; hashed now)")
    p_add.add_argument("--tokenizer", help="directory with the tokenizer and chat template files")
    p_add.add_argument("--description", default="")
    p_add.add_argument("--context", type=int, help="maximum context length if the server listing lacks it")
    p_rm = sub.add_parser("remove", help="forget a registered model")
    p_rm.add_argument("name")
    p_ver = sub.add_parser("verify", help="check the active profile")
    p_ver.add_argument("--hash", action="store_true", help="also re-hash the weights (slow)")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "use":
            profile = use(args.name)
            print(f"MiroFish model -> {profile.name} ({profile.description})")
            if args.env_file:
                print("updated", ", ".join(apply_env_file(args.env_file, profile, args.backend)), "in", args.env_file)
            if not profile.native:
                print("note: this profile is HTTP only; the native pipeline will refuse it")
            elif not profile.native_reference:
                print("note: the native pipeline was reviewed on qwen3.8-27b; prepare and qualify a new "
                      "candidate before trusting a native run on this profile")
            report = verify(profile)
        elif args.cmd == "add":
            profile = add(args.name, args.lmstudio_key, args.weights, args.tokenizer, args.ollama_tag, args.description, args.context)
            print(f"registered {profile.name} ({'native, pinned ' + profile.weight_sha256[:12] if profile.native else 'HTTP only'})")
            report = verify(profile)
        elif args.cmd == "remove":
            remove(args.name)
            print(f"removed {args.name}")
            return 0
        elif args.cmd == "verify":
            report = verify(active(), check_hash=args.hash)
        else:
            current = active()
            source = ENV_VAR if os.environ.get(ENV_VAR, "").strip() else (
                str(PROFILE_FILE) if PROFILE_FILE.exists() else "default")
            print(f"active: {current.name} (from {source})")
            for p in profiles().values():
                r = verify(p)
                kind = "native" if p.native else "http-only"
                print(f"  {p.name:18s} {kind:9s} ready={r['ready']} served={r['served']}"
                      + (f" weights_ok={r['weights_size_ok']} tokenizer_missing={len(r['tokenizer_files_missing'])}" if p.native else ""))
            return 0
    except (KeyError, ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2))
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
