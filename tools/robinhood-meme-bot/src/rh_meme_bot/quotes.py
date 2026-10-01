"""USD value of quote tokens. Reusable: ``quote_usd(token, chain_profile, reader, ...)``.

Kinds (QUOTE_TOKENS / chain profile ``usd`` field):
  stable       -> exactly 1.00 USD (only for the configured address; a token merely NAMED
                  "USDC" elsewhere is not a stable)
  pool:0xPOOL  -> from that v3 pool's slot0 (the other side is assumed to be a USD stable)
  weth         -> WETH (and native ETH) priced from the DEEPEST WETH/USDG pool, discovered
                  at runtime: v3 factory getPool over fee tiers, largest USDG balanceOf wins.
                  If no v3 pool exists, a hookless v4 WETH/USDG or ETH/USDG pool is used.
                  No pool -> None (unknown) -> the liquidity gate skips. Nothing hardcoded.
"""
from __future__ import annotations

import time
from decimal import Decimal
from typing import Callable, Dict, Optional, Tuple

from .chain import ChainReader, price_from_sqrt
from .profiles import NATIVE, ChainProfile
from .rpc import RpcError

V3_FEE_TIERS = (100, 500, 3000, 10000)


def discover_weth_pool(reader: ChainReader, weth: str, usdg: str) -> Optional[Tuple[str, int]]:
    """(pool, usdg_balance) of the v3 WETH/USDG pool holding the most USDG, or None."""
    t0, t1 = sorted((weth.lower(), usdg.lower()), key=lambda a: int(a, 16))
    best: Optional[Tuple[str, int]] = None
    for fee in V3_FEE_TIERS:
        pool = reader.get_pool(t0, t1, fee)
        bal = reader.balance_of(usdg, pool) if pool else None
        if bal and (best is None or bal > best[1]):
            best = (pool, bal)
    return best


def _price_vs_stable(reader: ChainReader, quote: str, pool: str) -> Optional[Decimal]:
    """USD price of ``quote`` from a v3 pool whose other side is a USD stable."""
    t0, t1 = reader.pool_tokens(pool)
    if quote not in (t0, t1) or t0 is None or t1 is None:
        return None
    d0, d1, s0 = reader.decimals(t0), reader.decimals(t1), reader.pool_slot0(pool)
    if d0 is None or d1 is None or s0 is None or not s0.sqrt_price_x96:
        return None
    t1_per_t0, t0_per_t1 = price_from_sqrt(s0.sqrt_price_x96, d0, d1)
    return t1_per_t0 if quote == t0 else t0_per_t1


def _weth_usd(reader: ChainReader, profile: ChainProfile, v4=None) -> Optional[Decimal]:
    if not profile.weth or not profile.usdg:
        return None  # e.g. testnet 46630: no USDG -> WETH USD unknown
    found = discover_weth_pool(reader, profile.weth, profile.usdg)
    if found:
        return _price_vs_stable(reader, profile.weth, found[0])
    if v4 is None:
        return None
    usdg_dec = reader.decimals(profile.usdg)
    for eth_side in (profile.weth, NATIVE):
        hit = v4.find_standard_pool(eth_side, profile.usdg)
        if hit and usdg_dec is not None:
            c0 = min((eth_side, profile.usdg), key=lambda a: int(a, 16))
            d0, d1 = (18, usdg_dec) if c0 == eth_side else (usdg_dec, 18)
            t1_per_t0, t0_per_t1 = price_from_sqrt(hit[1].sqrt_price_x96, d0, d1)
            return t1_per_t0 if c0 == eth_side else t0_per_t1
    return None


def quote_usd(token: str, chain_profile: ChainProfile, reader: ChainReader,
              quotes: Optional[Dict[str, dict]] = None, v4=None) -> Optional[Decimal]:
    """USD per 1 ``token`` (human units), or None when unknown. ``quotes`` defaults to the
    profile's built-in entries (QUOTE_TOKENS overrides are merged in config)."""
    token = token.lower()
    entry = (quotes if quotes is not None else chain_profile.quote_tokens()).get(token)
    if entry is None:
        return None
    kind = entry["usd"]
    if kind == "stable":
        return Decimal(1)
    if kind == "weth":
        return _weth_usd(reader, chain_profile, v4)
    if kind.startswith("pool:"):
        return _price_vs_stable(reader, token, kind.split(":", 1)[1])
    return None


class QuoteUsd:
    """Cached ``quote_usd`` for the configured quotes (TTL per token)."""

    def __init__(self, reader: ChainReader, quotes: Dict[str, dict], ttl: float = 30.0,
                 clock: Callable[[], float] = time.monotonic,
                 profile: Optional[ChainProfile] = None, v4=None):
        self.reader, self.quotes, self.ttl, self._clock = reader, quotes, ttl, clock
        self.profile = profile or ChainProfile(getattr(reader, "chain_id", 0))
        self.v4 = v4
        self._cache: Dict[str, Tuple[float, Optional[Decimal]]] = {}

    def usd(self, quote: str) -> Optional[Decimal]:
        entry = self.quotes.get(quote)
        if entry is None:
            return None
        if entry["usd"] == "stable":
            return Decimal(1)
        key = "weth" if entry["usd"] == "weth" else quote  # WETH and native ETH share a price
        hit = self._cache.get(key)
        if hit and self._clock() - hit[0] < self.ttl:
            return hit[1]
        try:
            val = quote_usd(quote, self.profile, self.reader, self.quotes, self.v4)
        except RpcError:
            val = None
        self._cache[key] = (self._clock(), val)
        return val
