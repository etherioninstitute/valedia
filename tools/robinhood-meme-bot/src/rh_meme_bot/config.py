"""Configuration loaded from environment / .env, validated at load time."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Mapping, Optional

from dotenv import dotenv_values

DEFAULT_RPC_URL = "https://rpc.mainnet.chain.robinhood.com"
DEFAULT_FACTORY = "0x1f7d7550B1b028f7571E69A784071F0205FD2EfA"
_ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


class ConfigError(ValueError):
    """Raised with every invalid setting listed, not just the first."""


@dataclass(frozen=True)
class Config:
    rpc_url: str = DEFAULT_RPC_URL
    chain_id: int = 4663
    factory_address: str = DEFAULT_FACTORY.lower()
    quote_tokens: tuple = ()
    poll_interval_seconds: float = 5.0
    lookback_blocks: int = 200
    max_block_range: int = 2000
    min_liquidity: int = 1_000_000
    min_confidence: float = 0.7
    max_risk_score: float = 0.5
    paper_size: float = 100.0
    positions_file: str = "positions.jsonl"
    decision_engine: str = ""


def _parse(env: Mapping[str, str], key: str, cast, default, errors: list,
           lo=None, hi=None, lo_excl=False):
    raw = env.get(key)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        val = cast(str(raw).strip())
    except (ValueError, ArithmeticError):
        errors.append(f"{key}={raw!r}: expected {cast.__name__}")
        return default
    if val != val:  # NaN
        errors.append(f"{key}={raw!r}: NaN not allowed")
        return default
    if lo is not None and (val <= lo if lo_excl else val < lo):
        errors.append(f"{key}={raw!r}: must be {'>' if lo_excl else '>='} {lo}")
    if hi is not None and val > hi:
        errors.append(f"{key}={raw!r}: must be <= {hi}")
    return val


def _addr(env, key, default, errors) -> str:
    raw = (env.get(key) or "").strip() or default
    if not _ADDR_RE.match(raw):
        errors.append(f"{key}={raw!r}: expected 0x + 40 hex chars")
    return raw.lower()


def parse_config(env: Mapping[str, str]) -> Config:
    """Validate a mapping of settings into a Config (raises ConfigError)."""
    errors: list = []
    rpc = (env.get("RPC_URL") or "").strip() or DEFAULT_RPC_URL
    if not rpc.startswith(("http://", "https://")):
        errors.append(f"RPC_URL={rpc!r}: must start with http:// or https://")
    quotes = []
    for item in (env.get("QUOTE_TOKENS") or "").split(","):
        item = item.strip()
        if not item:
            continue
        if _ADDR_RE.match(item):
            quotes.append(item.lower())
        else:
            errors.append(f"QUOTE_TOKENS entry {item!r}: expected 0x + 40 hex chars")
    cfg = Config(
        rpc_url=rpc,
        chain_id=_parse(env, "CHAIN_ID", int, 4663, errors, lo=1),
        factory_address=_addr(env, "FACTORY_ADDRESS", DEFAULT_FACTORY, errors),
        quote_tokens=tuple(quotes),
        poll_interval_seconds=_parse(env, "POLL_INTERVAL_SECONDS", float, 5.0, errors,
                                     lo=0, lo_excl=True),
        lookback_blocks=_parse(env, "LOOKBACK_BLOCKS", int, 200, errors, lo=0),
        max_block_range=_parse(env, "MAX_BLOCK_RANGE", int, 2000, errors, lo=1),
        min_liquidity=_parse(env, "MIN_LIQUIDITY", int, 1_000_000, errors, lo=0),
        min_confidence=_parse(env, "MIN_CONFIDENCE", float, 0.7, errors, lo=0, hi=1),
        max_risk_score=_parse(env, "MAX_RISK_SCORE", float, 0.5, errors, lo=0, hi=1),
        paper_size=_parse(env, "PAPER_SIZE", float, 100.0, errors, lo=0, lo_excl=True),
        positions_file=(env.get("POSITIONS_FILE") or "").strip() or "positions.jsonl",
        decision_engine=(env.get("DECISION_ENGINE") or "").strip(),
    )
    if cfg.decision_engine and not re.match(r"^[\w.]+:\w+$", cfg.decision_engine):
        errors.append(f"DECISION_ENGINE={cfg.decision_engine!r}: expected 'module:function'")
    if errors:
        raise ConfigError("Invalid configuration:\n  - " + "\n  - ".join(errors))
    return cfg


def load_config(env: Optional[Mapping[str, str]] = None,
                env_file: Optional[str] = ".env") -> Config:
    """Load .env (if present) overlaid by real environment variables.

    Pass ``env`` to bypass the process environment (used in tests).
    """
    if env is not None:
        return parse_config(env)
    merged: dict = {}
    if env_file and os.path.exists(env_file):
        merged.update({k: v for k, v in dotenv_values(env_file).items() if v is not None})
    merged.update(os.environ)
    return parse_config(merged)
