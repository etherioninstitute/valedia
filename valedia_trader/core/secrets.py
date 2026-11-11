"""Credential loading. Secrets live in one owner-only file outside the repository and outside the
general Hermes environment, so only the trading code that asks for them can read them.

Default file: ~/.hermes/secrets/trading.env (override with VALEDIA_ENV_FILE). Values are never
logged, printed or included in exceptions."""
from __future__ import annotations

import os
import stat
from pathlib import Path

__all__ = ["env_file_path", "load_env", "secret", "SecretError", "DEFAULT_ENV_FILE", "SECRETS_DIR"]

SECRETS_DIR = Path("~/.hermes/secrets").expanduser()
DEFAULT_ENV_FILE = SECRETS_DIR / "trading.env"


class SecretError(RuntimeError):
    """A credential is missing or its file is unsafe. The message names the variable, never the value."""


def env_file_path(environ=None) -> Path:
    env = os.environ if environ is None else environ
    return Path(env.get("VALEDIA_ENV_FILE") or DEFAULT_ENV_FILE).expanduser()


def parse_env(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.replace("export ", "", 1).strip()
        if key.replace("_", "").isalnum() and not key[0].isdigit():
            out[key] = value.strip().strip('"').strip("'")
    return out


def load_env(path: str | Path | None = None, environ=None, override: bool = False) -> list[str]:
    """Load KEY=VALUE pairs into the environment and return the names loaded. A file readable by
    group or others is refused. A missing file loads nothing (and returns an empty list)."""
    env = os.environ if environ is None else environ
    p = Path(path).expanduser() if path else env_file_path(env)
    if not p.is_file():
        return []
    if stat.S_IMODE(p.stat().st_mode) & 0o077:
        raise SecretError(f"{p} is readable by group or others; run chmod 600 on it")
    loaded = []
    for key, value in parse_env(p.read_text()).items():
        if override or key not in env:
            env[key] = value
            loaded.append(key)
    return loaded


def secret(name: str, environ=None) -> str:
    """Strict lookup of one credential from the environment."""
    env = os.environ if environ is None else environ
    value = env.get(name, "")
    if not value.strip():
        raise SecretError(f"credential {name} is not set (expected in {env_file_path(env)})")
    return value
