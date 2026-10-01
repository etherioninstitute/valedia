"""Build the per-pool state dict from on-chain facts. All numbers are computed here in code.

liquidity_usd = 2 x quote_reserve_usd, where quote_reserve_usd is the pool's REAL holding of
the quote token valued in USD: the ERC-20 balanceOf(pool) for v3, and for v4 (no per-pool
contract) the amounts rebuilt from ModifyLiquidity events at the current price (v4.py). Only
the quote side is real exit liquidity: the meme side is valued at a price the deployer
controls, and launches are often single-sided. For a balanced pool, TVL equals 2x the quote
side. tvl_usd (both sides at pool price) is informational.
Unknown quote USD value -> liquidity_usd None -> the liquidity gate fails (fail-closed).
v4 currency 0x0 is native ETH: it is priced like WETH, and no ERC-20 call is ever made on it.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Dict, Optional, Tuple

from .chain import ChainReader, PoolCreatedEvent, TokenInfo, price_from_sqrt
from .config import Config
from .profiles import NATIVE
from .quotes import QuoteUsd  # noqa: F401 - re-exported (moved to quotes.py)
from .risk import risk_flags


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


def split_roles(ev: PoolCreatedEvent, quotes: Dict[str, dict]) -> Tuple[str, str]:
    """(meme, quote). The meme is the non-quote side; with no configured quote, token0."""
    if ev.token0 in quotes and ev.token1 not in quotes:
        return ev.token1, ev.token0
    return ev.token0, ev.token1


def _decimals(reader: ChainReader, token: str) -> Optional[int]:
    return 18 if token == NATIVE else reader.decimals(token)


def _v3_reserves(reader, ev, info, meme) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    """(sqrt_price, raw token0, raw token1) for a v3 pool; balanceOf(pool) per side."""
    bal = {t: (reader.balance_of(t, ev.pool) if (t != meme or info.has_bytecode) else None)
           for t in (ev.token0, ev.token1)}
    s0 = reader.pool_slot0(ev.pool)
    return (s0.sqrt_price_x96 if s0 else None), bal[ev.token0], bal[ev.token1]


def _v4_reserves(v4, ev, extra: dict) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    """(sqrt_price, raw token0, raw token1) for a v4 pool from StateView + ModifyLiquidity."""
    if v4 is None:
        return None, None, None
    s0 = v4.slot0(ev.pool)
    if s0 is None or not s0.sqrt_price_x96:
        return None, None, None
    res = v4.reserves(ev, s0)
    extra["v4_liquidity_consistent"] = res.consistent if res else None
    if res is None or not res.consistent:  # logs disagree with StateView: do not trust them
        return s0.sqrt_price_x96, None, None
    return s0.sqrt_price_x96, res.amount0, res.amount1


def build_state(reader: ChainReader, cfg: Config, ev: PoolCreatedEvent, quote_usd: QuoteUsd,
                v4=None) -> Tuple[dict, Optional[Decimal]]:
    """Return (state for the model, entry price Decimal quote-per-meme or None)."""
    meme, quote = split_roles(ev, cfg.quote_tokens)
    qcfg = cfg.quote_tokens.get(quote)
    if meme == NATIVE:  # both sides are quotes (e.g. ETH/USDG): not a meme launch
        info = TokenInfo(meme, False, None, None, None, "no_owner_fn")
    else:
        info = reader.token_info(meme)
    m_dec, q_dec = info.decimals, _decimals(reader, quote)
    dec = {meme: m_dec, quote: q_dec}
    extra: dict = {}
    if ev.version == "v4":
        sqrt, raw0, raw1 = _v4_reserves(v4, ev, extra)
    else:
        sqrt, raw0, raw1 = _v3_reserves(reader, ev, info, meme)
    bal_m, bal_q = (raw0, raw1) if meme == ev.token0 else (raw1, raw0)
    price = None
    if sqrt and m_dec is not None and q_dec is not None:
        t1_per_t0, t0_per_t1 = price_from_sqrt(sqrt, dec[ev.token0], dec[ev.token1])
        price = t1_per_t0 if meme == ev.token0 else t0_per_t1  # quote per 1 meme
    usd = quote_usd.usd(quote) if qcfg else None
    q_res, m_res = human(bal_q, q_dec), human(bal_m, m_dec)
    q_res_usd = q_res * usd if q_res is not None and usd is not None else None
    m_res_usd = (m_res * price * usd
                 if None not in (m_res, price, usd) else None)
    state = {
        "version": ev.version, "token_address": meme, "symbol": info.symbol,
        "decimals": info.decimals, "total_supply": to_float(human(info.total_supply, m_dec)),
        "owner_renounced": info.owner_status == "renounced",  # no owner() is NOT renounced
        "owner_status": info.owner_status, "contract_has_code": info.has_bytecode,
        "pool_address": ev.pool_address, "pool_id": ev.pool_id,
        "fee_tier": None if ev.dynamic_fee else ev.fee,
        # booleans only: the model never compares hook addresses or fee numbers
        "has_hooks": ev.has_hooks, "dynamic_fee": ev.dynamic_fee,
        "quote_symbol": qcfg["symbol"] if qcfg else None,
        "price_in_quote": to_float(price),
        "price_usd": to_float(price * usd) if price is not None and usd is not None else None,
        "liquidity_usd": to_float(2 * q_res_usd) if q_res_usd is not None else None,
        "quote_reserve_usd": to_float(q_res_usd), "meme_reserve_usd": to_float(m_res_usd),
        "tvl_usd": to_float(q_res_usd + m_res_usd)
        if q_res_usd is not None and m_res_usd is not None else None,
        **extra,
    }
    state.update(risk_flags(state))
    return state, price


def fallback_state(ev: PoolCreatedEvent, meme: str) -> dict:
    """Minimal state when facts could not be read at all (logged as a skip)."""
    return {"version": ev.version, "token_address": meme, "pool_address": ev.pool_address,
            "pool_id": ev.pool_id, "symbol": None, "fee_tier": ev.fee,
            "has_hooks": ev.has_hooks, "dynamic_fee": ev.dynamic_fee, "liquidity_usd": None}

