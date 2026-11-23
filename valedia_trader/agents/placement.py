"""Refuse a Hermes call whose LM Studio model is not already loaded.

A request that names an unloaded LM Studio model makes LM Studio load it on this Mac (just-in-time loading), beside
a latency-critical live process. So before the trader code launches a Hermes profile served by LM Studio, the model
must already be loaded, here or on another device. If it is not, or the check itself fails, the call is refused.
There is no fallback to another model or provider: trader prompts may hold data that must not leave the machine."""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Callable

import yaml

from valedia_trader.core.secrets import child_env

_PROFILE = re.compile(r"[A-Za-z0-9_.-]+")
# Hermes flags that take a value; the value is the next element and is never scanned for flags (a prompt may say "-m").
_VALUE_FLAGS = {"-p", "--profile", "-z", "-t", "--toolsets", "--reasoning", "-m", "--model", "--provider"}


class ModelNotLoaded(RuntimeError):
    """The model a Hermes call would use is not loaded in LM Studio (or that could not be checked)."""


def profile_model(profile: str, hermes_home: Path | None = None) -> tuple[str | None, str | None]:
    """`(model.provider, model.default)` from the profile's config.yaml; `(None, None)` if the file or keys are missing."""
    if not _PROFILE.fullmatch(str(profile)):
        raise ValueError("invalid profile name")
    home = Path(hermes_home) if hermes_home is not None else Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")
    path = home / "profiles" / profile / "config.yaml"
    if not path.is_file():
        return None, None
    data = yaml.safe_load(path.read_text())
    model = data.get("model") if isinstance(data, dict) else None
    if not isinstance(model, dict):
        return None, None
    provider, default = model.get("provider"), model.get("default")
    return (str(provider) if provider else None), (str(default) if default else None)


def lmstudio_placement(identifier: str, runner: Callable | None = None, lms: str | None = None) -> str:
    """Where LM Studio has `identifier` loaded: "unloaded", "remote" (every match on another device), "local" (any match
    on this Mac) or "unknown" (the listing failed). `runner` is `subprocess.run` unless a test injects one."""
    exe = lms or os.environ.get("VALEDIA_LMS") or str(Path.home() / ".lmstudio/bin/lms")
    argv = [exe, "ps", "--json"]
    try:
        if runner is None:
            out = subprocess.run(argv, capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL, env=child_env())
        else:
            out = runner(argv, capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL, env=child_env())
        if out.returncode != 0:
            return "unknown"
        rows = json.loads(out.stdout)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return "unknown"
    if not isinstance(rows, list):
        return "unknown"
    matches = [r for r in rows if isinstance(r, dict) and r.get("identifier") == identifier]
    if not matches:
        return "unloaded"
    return "local" if any(not r.get("deviceIdentifier") for r in matches) else "remote"


def _flags(argv: list[str]) -> dict[str, str]:
    """The value of each value-taking flag in a Hermes argv. A flag's value is the next element and is skipped over."""
    seen: dict[str, str] = {}
    i = 1
    while i < len(argv):
        flag, eq, value = str(argv[i]).partition("=")
        if eq and flag in _VALUE_FLAGS:                                   # --model=X
            seen[flag] = value
            i += 1
        elif argv[i] in _VALUE_FLAGS:
            if i + 1 >= len(argv):                                        # a flag with no value: cannot tell what runs
                raise ModelNotLoaded(f"hermes call was refused: {argv[i]} has no value")
            seen[argv[i]] = argv[i + 1]
            i += 2
        else:
            i += 1
    return seen


def check_hermes_argv(argv: list[str], hermes_home: Path | None = None, runner: Callable | None = None,
                      lms: str | None = None) -> str | None:
    """Raise `ModelNotLoaded` unless this Hermes call is safe to run. Returns None when the call does not use LM Studio,
    else where the model is loaded ("remote" or "local"). Call it immediately before the launch."""
    flags = _flags(argv)
    profile = flags.get("-p") or flags.get("--profile")
    try:
        prof_provider, prof_model = profile_model(profile, hermes_home) if profile else (None, None)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise ModelNotLoaded(f"hermes profile {profile!r} was refused: its model could not be read ({type(exc).__name__}), "
                             "so LM Studio is not asked to load anything on this Mac") from None
    provider = flags.get("--provider") or prof_provider
    if str(provider or "").lower().replace("-", "").replace("_", "") != "lmstudio":
        return None
    model = flags.get("-m") or flags.get("--model") or prof_model
    if not model:
        raise ModelNotLoaded(f"hermes profile {profile!r} was refused: it uses LM Studio but names no model, "
                             "so LM Studio might load one on this Mac")
    placement = lmstudio_placement(model, runner, lms)
    if placement in ("remote", "local"):
        return placement
    raise ModelNotLoaded(f"hermes profile {profile!r} was refused: LM Studio model {model!r} is {placement}, "
                         "and the call is not made so that LM Studio does not load it on this Mac")
