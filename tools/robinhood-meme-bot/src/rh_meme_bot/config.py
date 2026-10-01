"""Configuration loaded from environment / .env, validated at load time."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Mapping, Optional

from dotenv import dotenv_values

DEFAULT_RPC_URL = "https://rpc.mainnet.chain.robinhood.com"
DEFAULT_FACTORY = "0x1f7d7550B1b028f7571E69A784071F0205FD2EfA"
DEFAULT_NIMBLE_URL = "http://127.0.0.1:11434/v1/systemone"
_ADDR_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
_SYM_RE = re.compile(r"^[A-Za-z0-9_.-]{1,16}$")


class ConfigError(ValueError):
    """Raised with every invalid setting listed, not just the first."""


@dataclass(frozen=True)
class Config:
    rpc_url: str = DEFAULT_RPC_URL
    chain_id: int = 4663
    factory_address: str = DEFAULT_FACTORY.lower()
    # address -> {"symbol": str, "usd": "stable" | "pool:<v3 pool address>"}
    quote_tokens: dict = field(default_factory=dict)
    poll_interval_seconds: float = 5.0
    lookback_blocks: int = 200
    max_block_range: int = 2000
    # Hard gates (applied in code after the model answers; see gates.py)
    min_liquidity_usd: float = 20000.0
    min_confidence: float = 0.70
    max_risk_score: int = 4
    paper_size: float = 100.0
    positions_file: str = "positions.jsonl"
    decisions_log: str = "decisions.jsonl"
    # "" = Nimble 2 (default); "stub" or "module:function" = other engine
    decision_engine: str = ""
    nimble_url: str = DEFAULT_NIMBLE_URL
    nimble_model: str = "nimble2"
    nimble_keep_alive: str = "30m"
    nimble_allow_remote: bool = False
    decision_timeout_s: float = 2.0
    decision_latency_budget_ms: float = 500.0


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
    if val != val or val in (float("inf"), float("-inf")):
        errors.append(f"{key}={raw!r}: must be finite")
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


def _bool(env, key, errors) -> bool:
    raw = (env.get(key) or "").strip().lower()
    if raw in ("", "0", "false", "no"):
        return False
    if raw in ("1", "true", "yes"):
        return True
    errors.append(f"{key}={raw!r}: expected 0 or 1")
    return False


def parse_quote_tokens(raw: str, errors: list) -> dict:
    """Parse ``addr:SYMBOL:stable`` or ``addr:SYMBOL:pool:0xPOOL`` entries, comma-separated."""
    out: dict = {}
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        parts = item.split(":")
        ok = (len(parts) in (3, 4) and _ADDR_RE.match(parts[0]) and _SYM_RE.match(parts[1])
              and ((len(parts) == 3 and parts[2] == "stable")
                   or (len(parts) == 4 and parts[2] == "pool" and _ADDR_RE.match(parts[3]))))
        if not ok:
            errors.append(f"QUOTE_TOKENS entry {item!r}: expected 0xADDR:SYMBOL:stable "
                          "or 0xADDR:SYMBOL:pool:0xPOOL")
            continue
        usd = "stable" if len(parts) == 3 else f"pool:{parts[3].lower()}"
        out[parts[0].lower()] = {"symbol": parts[1], "usd": usd}
    return out


def parse_config(env: Mapping[str, str]) -> Config:
    """Validate a mapping of settings into a Config (raises ConfigError)."""
    errors: list = []
    rpc = (env.get("RPC_URL") or "").strip() or DEFAULT_RPC_URL
    if not rpc.startswith(("http://", "https://")):
        errors.append(f"RPC_URL={rpc!r}: must start with http:// or https://")
    nurl = (env.get("NIMBLE_URL") or "").strip() or DEFAULT_NIMBLE_URL
    if not nurl.startswith(("http://", "https://")):
        errors.append(f"NIMBLE_URL={nurl!r}: must start with http:// or https://")
    cfg = Config(
        rpc_url=rpc,
        chain_id=_parse(env, "CHAIN_ID", int, 4663, errors, lo=1),
        factory_address=_addr(env, "FACTORY_ADDRESS", DEFAULT_FACTORY, errors),
        quote_tokens=parse_quote_tokens(env.get("QUOTE_TOKENS", ""), errors),
        poll_interval_seconds=_parse(env, "POLL_INTERVAL_SECONDS", float, 5.0, errors,
                                     lo=0, lo_excl=True),
        lookback_blocks=_parse(env, "LOOKBACK_BLOCKS", int, 200, errors, lo=0),
        max_block_range=_parse(env, "MAX_BLOCK_RANGE", int, 2000, errors, lo=1),
        min_liquidity_usd=_parse(env, "MIN_LIQUIDITY_USD", float, 20000.0, errors, lo=0),
        min_confidence=_parse(env, "MIN_CONFIDENCE", float, 0.70, errors, lo=0, hi=1),
        max_risk_score=_parse(env, "MAX_RISK_SCORE", int, 4, errors, lo=1, hi=10),
        paper_size=_parse(env, "PAPER_SIZE", float, 100.0, errors, lo=0, lo_excl=True),
        positions_file=(env.get("POSITIONS_FILE") or "").strip() or "positions.jsonl",
        decisions_log=(env.get("DECISIONS_LOG") or "").strip() or "decisions.jsonl",
        decision_engine=(env.get("DECISION_ENGINE") or "").strip(),
        nimble_url=nurl,
        nimble_model=(env.get("NIMBLE_MODEL") or "").strip() or "nimble2",
        nimble_keep_alive=(env.get("NIMBLE_KEEP_ALIVE") or "").strip() or "30m",
        nimble_allow_remote=_bool(env, "NIMBLE_ALLOW_REMOTE", errors),
        decision_timeout_s=_parse(env, "DECISION_TIMEOUT_S", float, 2.0, errors,
                                  lo=0, lo_excl=True),
        decision_latency_budget_ms=_parse(env, "DECISION_LATENCY_BUDGET_MS", float, 500.0,
                                          errors, lo=0, lo_excl=True),
    )
    eng = cfg.decision_engine
    if eng and eng != "stub" and not re.match(r"^[\w.]+:\w+$", eng):
        errors.append(f"DECISION_ENGINE={eng!r}: expected empty, 'stub', or 'module:function'")
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
