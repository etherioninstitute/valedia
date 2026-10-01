"""Robinhood Chain reader: pool watcher, ERC-20 facts, Uniswap V3 pool price (v4: v4.py).

Read-only. Every method is a JSON-RPC query; nothing here signs or sends transactions.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal, getcontext
from typing import Callable, List, Optional, Tuple

from . import abi
from .rpc import RpcClient, RpcError

getcontext().prec = 60
log = logging.getLogger(__name__)
Q96 = 2 ** 96


class ChainMismatchError(RuntimeError):
    pass


@dataclass(frozen=True)
class PoolCreatedEvent:
    """Unified new-pool record for Uniswap v3 (PoolCreated) and v4 (Initialize).

    ``pool`` is the dedupe key: the pool contract address (v3) or the bytes32 pool id (v4).
    token0/token1 are the sorted currencies (v4 currency 0x0 = native ETH).
    """
    pool: str
    token0: str
    token1: str
    fee: int
    tick_spacing: int
    block_number: int
    tx_hash: str
    log_index: int
    version: str = "v3"  # "v3" | "v4"
    hooks: Optional[str] = None  # v4 hooks contract (0x0 = none); None for v3

    @property
    def pool_address(self) -> Optional[str]:
        return self.pool if self.version == "v3" else None

    @property
    def pool_id(self) -> Optional[str]:
        return self.pool if self.version == "v4" else None

    @property
    def has_hooks(self) -> bool:
        return self.hooks is not None and self.hooks != abi.ZERO_ADDRESS

    @property
    def dynamic_fee(self) -> bool:
        return self.version == "v4" and self.fee == abi.V4_DYNAMIC_FEE


@dataclass(frozen=True)
class TokenInfo:
    address: str
    has_bytecode: bool
    total_supply: Optional[int]
    decimals: Optional[int]
    symbol: Optional[str]
    owner_status: str  # "renounced" | "owned:<addr>" | "no_owner_fn"


@dataclass(frozen=True)
class PoolSlot0:
    sqrt_price_x96: int
    tick: int


def decode_pool_created(entry: dict) -> PoolCreatedEvent:
    """Decode one eth_getLogs entry for PoolCreated. Raises ValueError if malformed."""
    topics = entry.get("topics") or []
    if len(topics) != 4 or topics[0].lower() != abi.POOL_CREATED_TOPIC:
        raise ValueError("not a PoolCreated log")
    data = entry.get("data", "0x")
    if len(abi.words(data)) < 2:
        raise ValueError("PoolCreated data too short")
    return PoolCreatedEvent(
        pool=abi.decode_address(data, 1),
        token0=abi.topic_to_address(topics[1]),
        token1=abi.topic_to_address(topics[2]),
        fee=abi.topic_to_uint(topics[3]),
        tick_spacing=abi.decode_int(data, 24, 0),
        block_number=int(entry["blockNumber"], 16),
        tx_hash=entry.get("transactionHash", ""),
        log_index=int(entry.get("logIndex", "0x0"), 16),
    )


def price_from_sqrt(sqrt_price_x96: int, dec0: int, dec1: int) -> Tuple[Decimal, Decimal]:
    """Return (token1 per token0, token0 per token1), adjusted for token decimals.

    Exact integer ratio first, then one Decimal division: no float overflow.
    """
    num = Decimal(sqrt_price_x96 * sqrt_price_x96)
    raw = num / Decimal(2 ** 192)  # raw token1 units per raw token0 unit
    t1_per_t0 = raw * (Decimal(10) ** (dec0 - dec1))
    t0_per_t1 = (Decimal(1) / t1_per_t0) if t1_per_t0 else Decimal(0)
    return t1_per_t0, t0_per_t1


class ChainReader:
    def __init__(self, rpc: RpcClient, factory: str, chain_id: int = 4663):
        self.rpc = rpc
        self.factory = factory.lower()
        self.chain_id = chain_id

    # --- basics -------------------------------------------------------
    def verify_chain(self) -> int:
        got = int(self.rpc.call("eth_chainId"), 16)
        if got != self.chain_id:
            raise ChainMismatchError(
                f"RPC {self.rpc.url} reports chain id {got}, expected {self.chain_id}. "
                "Check RPC_URL / CHAIN_ID.")
        return got

    def block_number(self) -> int:
        return int(self.rpc.call("eth_blockNumber"), 16)

    def _call(self, to: str, selector: str) -> Optional[str]:
        """eth_call; returns None on revert / empty return, raises on transport trouble."""
        try:
            res = self.rpc.call("eth_call", [{"to": to, "data": selector}, "latest"])
        except RpcError as e:
            if e.retryable:
                raise
            return None
        return res if isinstance(res, str) and len(res) > 2 else None

    # --- PoolCreated --------------------------------------------------
    def fetch_pool_created(self, from_block: int, to_block: int,
                           skipped: Optional[list] = None) -> List[PoolCreatedEvent]:
        params = [{"address": self.factory, "topics": [abi.POOL_CREATED_TOPIC],
                   "fromBlock": hex(from_block), "toBlock": hex(to_block)}]
        entries = self.rpc.call("eth_getLogs", params) or []
        events = []
        for entry in entries:
            try:
                events.append(decode_pool_created(entry))
            except (ValueError, KeyError, TypeError, AttributeError) as e:
                log.warning("skipping bad PoolCreated log: %s", e)
                if skipped is not None:
                    skipped.append(f"{type(e).__name__}: {e}")
        return events

    # --- ERC-20 -------------------------------------------------------
    def token_info(self, address: str) -> TokenInfo:
        address = address.lower()
        code = self.rpc.call("eth_getCode", [address, "latest"])
        if not isinstance(code, str) or code in ("0x", "0x0", ""):
            return TokenInfo(address, False, None, None, None, "no_owner_fn")
        supply = self._call(address, abi.SEL_TOTAL_SUPPLY)
        dec = self._call(address, abi.SEL_DECIMALS)
        sym = self._call(address, abi.SEL_SYMBOL)
        own = self._call(address, abi.SEL_OWNER)
        return TokenInfo(
            address=address, has_bytecode=True,
            total_supply=abi.decode_uint(supply) if supply else None,
            decimals=abi.decode_uint(dec) if dec else None,
            symbol=abi.decode_string_or_bytes32(sym) if sym else None,
            owner_status=self._owner_status(own),
        )

    @staticmethod
    def _owner_status(res: Optional[str]) -> str:
        owner = abi.decode_address(res) if res else None
        if owner is None:
            return "no_owner_fn"
        return "renounced" if owner == abi.ZERO_ADDRESS else f"owned:{owner}"

    def decimals(self, token: str) -> Optional[int]:
        res = self._call(token, abi.SEL_DECIMALS)
        return abi.decode_uint(res) if res else None

    def balance_of(self, token: str, holder: str) -> Optional[int]:
        """Raw ERC-20 balance of ``holder`` (None if the call reverts or is empty)."""
        res = self._call(token, abi.encode_balance_of(holder))
        return abi.decode_uint(res) if res else None

    # --- Uniswap V3 pool ---------------------------------------------
    def get_pool(self, token_a: str, token_b: str, fee: int) -> Optional[str]:
        """Factory getPool; None when no pool exists (zero address) or the call fails."""
        res = self._call(self.factory, abi.encode_get_pool(token_a, token_b, fee))
        addr = abi.decode_address(res) if res else None
        return None if addr in (None, abi.ZERO_ADDRESS) else addr

    def pool_tokens(self, pool: str) -> Tuple[Optional[str], Optional[str]]:
        t0, t1 = self._call(pool, abi.SEL_TOKEN0), self._call(pool, abi.SEL_TOKEN1)
        return (abi.decode_address(t0) if t0 else None, abi.decode_address(t1) if t1 else None)

    def pool_slot0(self, pool: str) -> Optional[PoolSlot0]:
        res = self._call(pool, abi.SEL_SLOT0)
        sqrt, tick = (abi.decode_uint(res, 0), abi.decode_int(res, 24, 1)) if res else (None, None)
        if sqrt is None or tick is None:
            return None
        return PoolSlot0(sqrt, tick)

    def pool_liquidity(self, pool: str) -> Optional[int]:
        res = self._call(pool, abi.SEL_LIQUIDITY)
        return abi.decode_uint(res) if res else None

    def pool_price(self, pool: str, dec0: int, dec1: int) -> Optional[Tuple[Decimal, Decimal]]:
        s0 = self.pool_slot0(pool)
        return price_from_sqrt(s0.sqrt_price_x96, dec0, dec1) if s0 else None


class PoolWatcher:
    """Polls for new pool events in bounded block ranges and tracks progress.

    ``fetch(from_block, to_block, skipped)`` defaults to the v3 PoolCreated reader; the v4
    watcher passes V4Reader.fetch_initialize. Same chunking / lookback / last-block tracking.
    """

    def __init__(self, reader: ChainReader, lookback: int = 200, max_range: int = 2000,
                 on_error: Optional[Callable[[str], None]] = None,
                 fetch: Optional[Callable[..., List[PoolCreatedEvent]]] = None,
                 label: str = "v3"):
        self.reader = reader
        self.fetch = fetch or reader.fetch_pool_created
        self.label = label
        self.lookback = lookback
        self.max_range = max(1, max_range)
        self.next_block: Optional[int] = None  # first block not yet processed
        self.last_head: Optional[int] = None
        self.skipped: list = []
        self._on_error = on_error or (lambda msg: None)

    @property
    def last_block(self) -> Optional[int]:
        """Last block fully processed (None before the first poll)."""
        return None if self.next_block is None else self.next_block - 1

    def poll(self) -> List[PoolCreatedEvent]:
        head = self.reader.block_number()
        self.last_head = head
        if self.next_block is None:
            self.next_block = max(0, head - self.lookback)
        events: List[PoolCreatedEvent] = []
        start = self.next_block
        while start <= head:
            end = min(head, start + self.max_range - 1)
            try:
                events.extend(self.fetch(start, end, self.skipped))
            except RpcError as e:
                # Keep progress made so far; retry this range on the next poll.
                self._on_error(f"{self.label} getLogs {start}-{end} failed: {e}")
                break
            self.next_block = end + 1
            start = end + 1
        return events
