"""Minimal ABI constants and decoders (no web3 dependency). Addresses are lowercase."""
from __future__ import annotations

from typing import Optional

# keccak256("PoolCreated(address,address,uint24,int24,address)"); verified in tests.
POOL_CREATED_TOPIC = "0x783cca1c0412dd0d695e784568c96da2e9c22ff989357a2e8b1d9b2b4e6b7118"
ZERO_ADDRESS = "0x" + "00" * 20

SEL_TOTAL_SUPPLY = "0x18160ddd"
SEL_DECIMALS = "0x313ce567"
SEL_SYMBOL = "0x95d89b41"
SEL_OWNER = "0x8da5cb5b"
SEL_SLOT0 = "0x3850c7bd"
SEL_LIQUIDITY = "0x1a686502"
SEL_TOKEN0 = "0x0dfe1681"
SEL_TOKEN1 = "0xd21220a7"
SEL_BALANCE_OF = "0x70a08231"
SEL_GET_POOL = "0x1698ee82"  # UniswapV3Factory.getPool(address,address,uint24)

# --- Uniswap v4 (all verified against keccak in tests) ---
# Initialize(bytes32 indexed id, address indexed currency0, address indexed currency1,
#            uint24 fee, int24 tickSpacing, address hooks, uint160 sqrtPriceX96, int24 tick)
V4_INITIALIZE_TOPIC = "0xdd466e674ea557f56295e2d0218a125ea4b4f0f6f3307b95f85e6110838d6438"
# ModifyLiquidity(bytes32 indexed id, address indexed sender, int24 tickLower,
#                 int24 tickUpper, int256 liquidityDelta, bytes32 salt)
V4_MODIFY_LIQUIDITY_TOPIC = "0xf208f4912782fd25c7f114ca3723a2d5dd6f3bcc3ac8db5af63baa85f711d5ec"
SEL_V4_GET_SLOT0 = "0xc815641c"  # StateView.getSlot0(bytes32)
SEL_V4_GET_LIQUIDITY = "0xfa6793d5"  # StateView.getLiquidity(bytes32)
SEL_V4_POOL_MANAGER = "0xdc4c90d3"  # StateView.poolManager() (ImmutableState)
V4_DYNAMIC_FEE = 0x800000  # LPFeeLibrary.DYNAMIC_FEE_FLAG


def strip0x(h: str) -> str:
    return h[2:] if h[:2] in ("0x", "0X") else h


def words(data: str) -> list:
    """Split hex return data into 32-byte word hex strings (no 0x)."""
    raw = strip0x(data)
    return [raw[i:i + 64] for i in range(0, len(raw) - len(raw) % 64, 64)]


def decode_uint(data: str, index: int = 0) -> Optional[int]:
    w = words(data)
    return int(w[index], 16) if len(w) > index else None


def decode_int(data: str, bits: int, index: int = 0) -> Optional[int]:
    """Signed int of ``bits`` width read from the low bits of word ``index``."""
    v = decode_uint(data, index)
    if v is None:
        return None
    v &= (1 << bits) - 1
    return v - (1 << bits) if v >> (bits - 1) else v


def decode_address(data: str, index: int = 0) -> Optional[str]:
    w = words(data)
    return "0x" + w[index][-40:].lower() if len(w) > index else None


def encode_balance_of(holder: str) -> str:
    """Calldata for balanceOf(holder)."""
    return SEL_BALANCE_OF + strip0x(holder).lower().rjust(64, "0")


def encode_get_pool(token_a: str, token_b: str, fee: int) -> str:
    """Calldata for UniswapV3Factory.getPool(tokenA, tokenB, fee)."""
    return (SEL_GET_POOL + strip0x(token_a).lower().rjust(64, "0")
            + strip0x(token_b).lower().rjust(64, "0") + format(fee, "064x"))


def encode_bytes32_call(selector: str, word: str) -> str:
    """Calldata for f(bytes32)."""
    w = strip0x(word).lower()
    if len(w) != 64:
        raise ValueError(f"bad bytes32: {word!r}")
    return selector + w


def topic_to_address(topic: str) -> str:
    t = strip0x(topic)
    if len(t) != 64:
        raise ValueError(f"bad topic length: {topic!r}")
    return "0x" + t[-40:].lower()


def topic_to_uint(topic: str) -> int:
    return int(strip0x(topic), 16)


def _clean(b: bytes) -> str:
    return b.rstrip(b"\x00").decode("utf-8", errors="replace").strip()


def decode_string_or_bytes32(data: str) -> Optional[str]:
    """Decode an ERC-20 name/symbol return: dynamic ABI string or legacy bytes32."""
    try:
        raw = bytes.fromhex(strip0x(data))
    except ValueError:
        return None
    if len(raw) < 32:
        return None
    if len(raw) >= 64:
        offset = int.from_bytes(raw[:32], "big")
        if offset + 32 <= len(raw):
            length = int.from_bytes(raw[offset:offset + 32], "big")
            body = raw[offset + 32:offset + 32 + length]
            if len(body) == length and length > 0:
                return _clean(body)
    if len(raw) == 32:
        return _clean(raw) or None
    return None
