"""One switch for the model MiroFish runs on.

    python -m valedia_trader.agents.mirofish_models status
    python -m valedia_trader.agents.mirofish_models use qwen3-8b        # small and fast
    python -m valedia_trader.agents.mirofish_models use qwen3.8-27b     # large and deep
    python -m valedia_trader.agents.mirofish_models verify --hash

`use` writes src/mirofish_model_profile.json. The native MiroFish pipeline reads that file when a
candidate is prepared and copies it into the candidate, so a prepared run keeps its checkpoint.
For the HTTP (offline backend) path, `use --env-file PATH` also rewrites LLM_BASE_URL and
LLM_MODEL_NAME in that backend's .env. The checkpoint pins here must match
src/mirofish_native_model.py; a test enforces it."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

__all__ = ["ModelProfile", "PROFILES", "ALIASES", "DEFAULT_PROFILE", "PROFILE_FILE", "ENV_VAR", "resolve",
           "active", "use", "verify", "backend_env", "apply_env_file", "BACKEND_URLS", "TOKENIZER_FILES"]

REPO = Path(__file__).resolve().parents[2]
PROFILE_FILE = REPO / "src" / "mirofish_model_profile.json"
ENV_VAR = "VALEDIA_MIROFISH_MODEL"
MODEL_ROOT = Path("/Volumes/NIMROD_SSD/Qwen-Weight-Space/models")
TOKENIZER_FILES = ("config.json", "tokenizer.json", "tokenizer_config.json", "chat_template.jinja",
                   "model.safetensors.index.json")
BACKEND_URLS = {"lmstudio": "http://127.0.0.1:1234/v1", "ollama": "http://127.0.0.1:11434/v1"}


@dataclass(frozen=True)
class ModelProfile:
    name: str
    description: str
    weight_file: Path
    weight_bytes: int
    weight_sha256: str
    tokenizer_root: Path
    lmstudio_key: str
    ollama_tag: str | None
    max_context: int
    native_reference: bool   # the checkpoint the native pipeline was built and reviewed on


PROFILES: dict[str, ModelProfile] = {
    "qwen3-8b": ModelProfile(
        "qwen3-8b", "Qwen3 8B Q4_K_M: small and fast; MiroFish's original prep/simulation model",
        MODEL_ROOT / "lmstudio-library/Qwen/Qwen3-8B/Qwen3-8B-Q4_K_M.gguf", 5225374496,
        "a3de86cd1c132c822487ededd47a324c50491393e6565cd14bafa40d0b8e686f",
        MODEL_ROOT / "Qwen3-8B-tokenizer", "qwen3-8b", "qwen3:8b", 40960, False),
    "qwen3.8-27b": ModelProfile(
        "qwen3.8-27b", "Qwen3.8 27B Q4_K_M: large and deep; the native pipeline's reference checkpoint",
        MODEL_ROOT / "ggml-org/Qwen3.8-27B-GGUF/Qwen3.8-27B-Q4_K_M.gguf", 18973870528,
        "c600de0300ae8a0eb3a6c0b8b5561b8b96f16bd2c863c2a66c42de29d391a747",
        MODEL_ROOT / "Qwen3.8-27B-MLX-4bit", "qwen3.8-27b", None, 262144, True),
}
DEFAULT_PROFILE = "qwen3.8-27b"
ALIASES = {
    "8b": "qwen3-8b", "qwen3:8b": "qwen3-8b", "qwen3-9b": "qwen3-8b", "qwen3:9b": "qwen3-8b", "small": "qwen3-8b",
    "fast": "qwen3-8b", "27b": "qwen3.8-27b", "qwen3.8:27b": "qwen3.8-27b", "large": "qwen3.8-27b",
    "deep": "qwen3.8-27b",
}


def resolve(name: str) -> ModelProfile:
    """Profile for a name or alias (case-insensitive)."""
    key = str(name).strip().lower()
    key = ALIASES.get(key, key)
    if key not in PROFILES:
        raise KeyError(f"unknown MiroFish model '{name}'; choose one of {sorted(PROFILES)}")
    return PROFILES[key]


def active(profile_file: Path = PROFILE_FILE, environ=None) -> ModelProfile:
    """The selected profile: the environment variable for this process if set, else the profile
    file, else the default. Prepared native candidates only ever see the file."""
    env = os.environ if environ is None else environ
    if env.get(ENV_VAR, "").strip():
        return resolve(env[ENV_VAR])
    if Path(profile_file).exists():
        return resolve(json.loads(Path(profile_file).read_text()).get("profile", ""))
    return PROFILES[DEFAULT_PROFILE]


def use(name: str, profile_file: Path = PROFILE_FILE) -> ModelProfile:
    """Select a profile by writing the profile file atomically."""
    profile = resolve(name)
    path = Path(profile_file)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"profile": profile.name, "set_at": datetime.now(timezone.utc).isoformat(timespec="seconds")},
                              indent=2) + "\n")
    tmp.replace(path)
    return profile


def _served_ids(base_url: str, timeout: float) -> list[str] | None:
    try:
        with urllib.request.urlopen(base_url.rstrip("/") + "/models", timeout=timeout) as resp:
            return [str(m.get("id")) for m in json.load(resp).get("data", [])]
    except (urllib.error.URLError, OSError, ValueError):
        return None


def verify(profile: ModelProfile, check_hash: bool = False, probe_servers: bool = True, timeout: float = 2.0) -> dict:
    """Readiness report for a profile: weights present and the pinned size, optional SHA-256 check,
    tokenizer files present, and which local servers currently list the model."""
    exists = profile.weight_file.is_file()
    report: dict = {
        "profile": profile.name,
        "weights_present": exists,
        "weights_size_ok": bool(exists and profile.weight_file.stat().st_size == profile.weight_bytes),
        "tokenizer_files_missing": [f for f in TOKENIZER_FILES if not (profile.tokenizer_root / f).is_file()],
    }
    if check_hash and exists:
        digest = hashlib.sha256()
        with profile.weight_file.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 22), b""):
                digest.update(chunk)
        report["weights_sha256_ok"] = digest.hexdigest() == profile.weight_sha256
    if probe_servers:
        served = {}
        for backend, url in BACKEND_URLS.items():
            wanted = profile.lmstudio_key if backend == "lmstudio" else profile.ollama_tag
            ids = _served_ids(url, timeout)
            served[backend] = None if ids is None or wanted is None else wanted in ids
        report["served"] = served
    report["ready"] = bool(report["weights_size_ok"] and not report["tokenizer_files_missing"]
                           and report.get("weights_sha256_ok", True))
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
    p_use.add_argument("name", help="qwen3-8b | qwen3.8-27b (aliases: 8b, small, 27b, large)")
    p_use.add_argument("--env-file", help="also update this MiroFish backend .env")
    p_use.add_argument("--backend", default="lmstudio", choices=sorted(BACKEND_URLS))
    p_ver = sub.add_parser("verify", help="check the active profile")
    p_ver.add_argument("--hash", action="store_true", help="also re-hash the weights (slow)")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "use":
            profile = use(args.name)
            print(f"MiroFish model -> {profile.name} ({profile.description})")
            if args.env_file:
                print("updated", ", ".join(apply_env_file(args.env_file, profile, args.backend)), "in", args.env_file)
            if not profile.native_reference:
                print("note: the native pipeline was reviewed on qwen3.8-27b; prepare and qualify a new "
                      "candidate before trusting a native run on this profile")
            report = verify(profile)
        elif args.cmd == "verify":
            report = verify(active(), check_hash=args.hash)
        else:
            current = active()
            source = ENV_VAR if os.environ.get(ENV_VAR, "").strip() else (
                str(PROFILE_FILE) if PROFILE_FILE.exists() else "default")
            print(f"active: {current.name} (from {source})")
            for p in PROFILES.values():
                r = verify(p)
                print(f"  {p.name:12s} ready={r['ready']} weights_ok={r['weights_size_ok']} "
                      f"tokenizer_missing={len(r['tokenizer_files_missing'])} served={r['served']}")
            return 0
    except (KeyError, ValueError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2))
    return 0 if report["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
