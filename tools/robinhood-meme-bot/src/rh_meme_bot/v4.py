"""Uniswap v4 reader: Initialize watcher, StateView price, real reserves from ModifyLiquidity.

v4 has one singleton PoolManager and no per-pool contract, so ``balanceOf(pool)`` is
meaningless. Real reserves are rebuilt from the pool's ModifyLiquidity events: net
liquidityDelta per (tickLower, tickUpper), then LiquidityAmounts at the current sqrtPrice.
Read-only: eth_getCode / eth_getLogs / eth_call only.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from . import abi
from .chain import PoolCreatedEvent
from .keccak import keccak256
from .rpc import RpcClient, RpcError
from .tickmath import MAX_TICK, MIN_TICK, get_amounts_for_liquidity, get_sqrt_ratio_at_tick

log = logging.getLogger(__name__)
# Standard (fee, tickSpacing) pairs used to look up hookless WETH/USDG v4 pools.
STANDARD_FEES = ((100, 1), (500, 10), (3000, 60), (10000, 200))


def _word_addr(a: str) -> str:
    return abi.strip0x(a).lower().rjust(64, "0")


def _word_int(v: int) -> str:
    return format(v & ((1 << 256) - 1), "064x")


def compute_pool_id(currency0: str, currency1: str, fee: int, tick_spacing: int,
                    hooks: str) -> str:
    """keccak256(abi.encode(PoolKey{currency0, currency1, fee, tickSpacing, hooks}))."""
    enc = (_word_addr(currency0) + _word_addr(currency1) + _word_int(fee)
           + _word_int(tick_spacing) + _word_addr(hooks))
    return "0x" + keccak256(bytes.fromhex(enc)).hex()


def decode_initialize(entry: dict) -> PoolCreatedEvent:
    """Decode one Initialize log. Raises ValueError if malformed or the id does not match."""
    topics = entry.get("topics") or []
    if len(topics) != 4 or str(topics[0]).lower() != abi.V4_INITIALIZE_TOPIC:
        raise ValueError("not an Initialize log")
    data = entry.get("data", "0x")
    if len(abi.words(data)) < 5:
        raise ValueError("Initialize data too short")
    pool_id = "0x" + abi.strip0x(topics[1]).lower()
    c0, c1 = abi.topic_to_address(topics[2]), abi.topic_to_address(topics[3])
    fee, spacing = abi.decode_uint(data, 0) & 0xFFFFFF, abi.decode_int(data, 24, 1)
    hooks = abi.decode_address(data, 2)
    if len(pool_id) != 66 or int(c0, 16) >= int(c1, 16):
        raise ValueError("bad pool id or unsorted currencies")
    if compute_pool_id(c0, c1, fee, spacing, hooks) != pool_id:
        raise ValueError("pool id does not match keccak(PoolKey)")
    return PoolCreatedEvent(pool=pool_id, token0=c0, token1=c1, fee=fee, tick_spacing=spacing,
                            block_number=int(entry["blockNumber"], 16),
                            tx_hash=entry.get("transactionHash", ""),
                            log_index=int(entry.get("logIndex", "0x0"), 16),
                            version="v4", hooks=hooks)


def decode_modify_liquidity(entry: dict) -> Tuple[str, int, int, int]:
    """(pool_id, tickLower, tickUpper, liquidityDelta). Raises ValueError if malformed."""
    topics = entry.get("topics") or []
    if len(topics) != 3 or str(topics[0]).lower() != abi.V4_MODIFY_LIQUIDITY_TOPIC:
        raise ValueError("not a ModifyLiquidity log")
    data = entry.get("data", "0x")
    if len(abi.words(data)) < 4:
        raise ValueError("ModifyLiquidity data too short")
    return ("0x" + abi.strip0x(topics[1]).lower(), abi.decode_int(data, 24, 0),
            abi.decode_int(data, 24, 1), abi.decode_int(data, 256, 2))


@dataclass(frozen=True)
class V4Slot0:
    sqrt_price_x96: int
    tick: int
    protocol_fee: int
    lp_fee: int


@dataclass(frozen=True)
class V4Reserves:
    amount0: int  # raw token0 units actually held by the pool's positions
    amount1: int
    in_range_liquidity: int  # sum of net L of positions covering the current tick
    onchain_liquidity: Optional[int]  # StateView.getLiquidity, for the consistency check

    @property
    def consistent(self) -> bool:
        return self.onchain_liquidity is not None \
            and self.onchain_liquidity == self.in_range_liquidity


def reserves_from_positions(positions: Dict[Tuple[int, int], int], sqrt_price: int,
                            tick: int) -> Optional[Tuple[int, int, int]]:
    """Sum LiquidityAmounts over net positions. None if any net position is negative or
    out of tick bounds (logs incomplete: fail-closed)."""
    a0 = a1 = in_range = 0
    for (lo, hi), liq in positions.items():
        if liq < 0 or not MIN_TICK <= lo < hi <= MAX_TICK:
            return None
        if liq == 0:
            continue
        x, y = get_amounts_for_liquidity(sqrt_price, get_sqrt_ratio_at_tick(lo),
                                         get_sqrt_ratio_at_tick(hi), liq)
        a0, a1 = a0 + x, a1 + y
        if lo <= tick < hi:
            in_range += liq
    return a0, a1, in_range


class V4Reader:
    def __init__(self, rpc: RpcClient, pool_manager: str, state_view: str,
                 max_range: int = 2000):
        self.rpc = rpc
        self.pool_manager, self.state_view = pool_manager.lower(), state_view.lower()
        self.max_range = max(1, max_range)

    def _call(self, to: str, data: str) -> Optional[str]:
        try:
            res = self.rpc.call("eth_call", [{"to": to, "data": data}, "latest"])
        except RpcError as e:
            if e.retryable:
                raise
            return None
        return res if isinstance(res, str) and len(res) > 2 else None

    def verify(self) -> Optional[str]:
        """Startup check of the configured addresses. Returns a problem string, or None.

        Both must have bytecode, StateView.poolManager() must equal the PoolManager (the
        StateView address has one source only), and a StateView.getSlot0 call must succeed.
        Raises RpcError on transport trouble (the caller retries later).
        """
        for name, addr in (("V4_POOL_MANAGER", self.pool_manager),
                           ("V4_STATE_VIEW", self.state_view)):
            code = self.rpc.call("eth_getCode", [addr, "latest"])
            if not isinstance(code, str) or code in ("0x", "0x0", ""):
                return f"{name} {addr} has no bytecode"
        res = self._call(self.state_view, abi.SEL_V4_POOL_MANAGER)
        pm = abi.decode_address(res) if res else None
        if pm != self.pool_manager:
            return f"V4_STATE_VIEW.poolManager() = {pm}, expected {self.pool_manager}"
        if self.slot0("0x" + "00" * 32) is None:
            return "V4_STATE_VIEW getSlot0 call failed"
        return None

    def fetch_initialize(self, from_block: int, to_block: int,
                         skipped: Optional[list] = None) -> List[PoolCreatedEvent]:
        params = [{"address": self.pool_manager, "topics": [abi.V4_INITIALIZE_TOPIC],
                   "fromBlock": hex(from_block), "toBlock": hex(to_block)}]
        events = []
        for entry in self.rpc.call("eth_getLogs", params) or []:
            try:
                events.append(decode_initialize(entry))
            except (ValueError, KeyError, TypeError, AttributeError) as e:
                log.warning("skipping bad Initialize log: %s", e)
                if skipped is not None:
                    skipped.append(f"{type(e).__name__}: {e}")
        return events

    def slot0(self, pool_id: str) -> Optional[V4Slot0]:
        res = self._call(self.state_view, abi.encode_bytes32_call(abi.SEL_V4_GET_SLOT0, pool_id))
        if not res or len(abi.words(res)) < 4:
            return None
        return V4Slot0(abi.decode_uint(res, 0), abi.decode_int(res, 24, 1),
                       abi.decode_uint(res, 2), abi.decode_uint(res, 3))

    def liquidity(self, pool_id: str) -> Optional[int]:
        res = self._call(self.state_view,
                         abi.encode_bytes32_call(abi.SEL_V4_GET_LIQUIDITY, pool_id))
        return abi.decode_uint(res) if res else None

    def positions(self, pool_id: str, from_block: int,
                  to_block: int) -> Dict[Tuple[int, int], int]:
        """Net liquidityDelta per (tickLower, tickUpper) for one pool. Raises RpcError /
        ValueError if any chunk or log is unreadable (a partial view would be wrong)."""
        out: Dict[Tuple[int, int], int] = {}
        start = from_block
        while start <= to_block:
            end = min(to_block, start + self.max_range - 1)
            params = [{"address": self.pool_manager,
                       "topics": [abi.V4_MODIFY_LIQUIDITY_TOPIC, pool_id],
                       "fromBlock": hex(start), "toBlock": hex(end)}]
            for entry in self.rpc.call("eth_getLogs", params) or []:
                pid, lo, hi, delta = decode_modify_liquidity(entry)
                if pid != pool_id.lower():
                    raise ValueError("ModifyLiquidity log for another pool")
                out[(lo, hi)] = out.get((lo, hi), 0) + delta
            start = end + 1
        return out

    def reserves(self, ev: PoolCreatedEvent, slot0: V4Slot0,
                 head: Optional[int] = None) -> Optional[V4Reserves]:
        """Real token amounts held by the pool, or None if unknowable (fail-closed)."""
        head = int(self.rpc.call("eth_blockNumber"), 16) if head is None else head
        try:
            pos = self.positions(ev.pool, ev.block_number, head)
        except (ValueError, KeyError, TypeError) as e:
            log.warning("v4 reserves for %s unreadable: %s", ev.pool, e)
            return None
        got = reserves_from_positions(pos, slot0.sqrt_price_x96, slot0.tick)
        if got is None:
            return None
        return V4Reserves(got[0], got[1], got[2], self.liquidity(ev.pool))

    def find_standard_pool(self, currency_a: str, currency_b: str
                           ) -> Optional[Tuple[str, V4Slot0]]:
        """Hookless static-fee pool between two currencies with the most in-range liquidity."""
        c0, c1 = sorted((currency_a.lower(), currency_b.lower()), key=lambda a: int(a, 16))
        best = None
        for fee, spacing in STANDARD_FEES:
            pid = compute_pool_id(c0, c1, fee, spacing, abi.ZERO_ADDRESS)
            liq = self.liquidity(pid)
            if liq and (best is None or liq > best[0]):
                best = (liq, pid)
        if best is None:
            return None
        s0 = self.slot0(best[1])
        return (best[1], s0) if s0 and s0.sqrt_price_x96 else None
