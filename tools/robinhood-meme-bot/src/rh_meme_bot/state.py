"""Build the per-pool state dict from on-chain facts. All numbers are computed here in code.

liquidity_usd = 2 x quote_reserve_usd, where quote_reserve_usd is the pool's ERC-20 balance of
the quote token valued in USD. Only the quote side is real exit liquidity: the meme side is
valued at a slot0 price the deployer controls, and V3 launches are often single-sided. For a
balanced pool, TVL equals 2x the quote side. tvl_usd (both sides at pool price) is informational.
Unknown quote USD value -> liquidity_usd None -> the liquidity gate fails (fail-closed).
"""
from __future__ import annotations

import time
from decimal import Decimal
from typing import Callable, Dict, Optional, Tuple

from .chain import ChainReader, PoolCreatedEvent, price_from_sqrt
from .config import Config
from .risk import risk_flags
from .rpc import RpcError


def to_float(d: Optional[Decimal]) -> Optional[float]:
    if d is None:
        return None
    try:
        f = float(d)
    except (OverflowError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def human(raw: Optional[int], decimals: Optional[int]) -> Optional[Decimal]:
    if raw is None or decimals is None:
        return None
    return Decimal(raw) / (Decimal(10) ** decimals)


class QuoteUsd:
    """USD value of one quote token: 'stable' = 1.0, or derived from a V3 pool's slot0."""

    def __init__(self, reader: ChainReader, quotes: Dict[str, dict], ttl: float = 30.0,
                 clock: Callable[[], float] = time.monotonic):
        self.reader, self.quotes, self.ttl, self._clock = reader, quotes, ttl, clock
        self._cache: Dict[str, Tuple[float, Optional[Decimal]]] = {}

    def usd(self, quote: str) -> Optional[Decimal]:
        cfg = self.quotes.get(quote)
        if cfg is None:
            return None
        if cfg["usd"] == "stable":
            return Decimal(1)
        hit = self._cache.get(quote)
        if hit and self._clock() - hit[0] < self.ttl:
            return hit[1]
        try:
            val = self._from_pool(quote, cfg["usd"].split(":", 1)[1])
        except RpcError:
            val = None
        self._cache[quote] = (self._clock(), val)
        return val

    def _from_pool(self, quote: str, pool: str) -> Optional[Decimal]:
        t0, t1 = self.reader.pool_tokens(pool)
        if quote not in (t0, t1) or t0 is None or t1 is None:
            return None
        d0, d1, s0 = self.reader.decimals(t0), self.reader.decimals(t1), self.reader.pool_slot0(pool)
        if d0 is None or d1 is None or s0 is None:
            return None
        t1_per_t0, t0_per_t1 = price_from_sqrt(s0.sqrt_price_x96, d0, d1)
        # the other side of that pool is assumed to be a USD stablecoin worth 1.0
        return t1_per_t0 if quote == t0 else t0_per_t1


def split_roles(ev: PoolCreatedEvent, quotes: Dict[str, dict]) -> Tuple[str, str]:
    """(meme, quote). The meme is the non-quote side; with no configured quote, token0."""
    if ev.token0 in quotes and ev.token1 not in quotes:
        return ev.token1, ev.token0
    return ev.token0, ev.token1


def build_state(reader: ChainReader, cfg: Config, ev: PoolCreatedEvent,
                quote_usd: QuoteUsd) -> Tuple[dict, Optional[Decimal]]:
    """Return (state for the model, entry price Decimal quote-per-meme or None)."""
    meme, quote = split_roles(ev, cfg.quote_tokens)
    qcfg = cfg.quote_tokens.get(quote)
    info = reader.token_info(meme)
    m_dec, q_dec = info.decimals, reader.decimals(quote)
    dec = {meme: m_dec, quote: q_dec}
    bal_m = reader.balance_of(meme, ev.pool) if info.has_bytecode else None
    bal_q = reader.balance_of(quote, ev.pool)
    slot0 = reader.pool_slot0(ev.pool)
    price = None
    if slot0 and m_dec is not None and q_dec is not None:
        t1_per_t0, t0_per_t1 = price_from_sqrt(slot0.sqrt_price_x96, dec[ev.token0],
                                               dec[ev.token1])
        price = t1_per_t0 if meme == ev.token0 else t0_per_t1  # quote per 1 meme
    usd = quote_usd.usd(quote) if qcfg else None
    q_res, m_res = human(bal_q, q_dec), human(bal_m, m_dec)
    q_res_usd = q_res * usd if q_res is not None and usd is not None else None
    m_res_usd = (m_res * price * usd
                 if None not in (m_res, price, usd) else None)
    state = {
        "token_address": meme, "symbol": info.symbol, "decimals": info.decimals,
        "total_supply": to_float(human(info.total_supply, m_dec)),
        "owner_renounced": info.owner_status == "renounced",  # no owner() is NOT renounced
        "owner_status": info.owner_status, "contract_has_code": info.has_bytecode,
        "pool_address": ev.pool, "fee_tier": ev.fee,
        "quote_symbol": qcfg["symbol"] if qcfg else None,
        "price_in_quote": to_float(price),
        "price_usd": to_float(price * usd) if price is not None and usd is not None else None,
        "liquidity_usd": to_float(2 * q_res_usd) if q_res_usd is not None else None,
        "quote_reserve_usd": to_float(q_res_usd), "meme_reserve_usd": to_float(m_res_usd),
        "tvl_usd": to_float(q_res_usd + m_res_usd)
        if q_res_usd is not None and m_res_usd is not None else None,
    }
    state.update(risk_flags(state))
    return state, price
