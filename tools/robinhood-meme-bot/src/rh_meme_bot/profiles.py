"""Built-in per-chain facts (pure data, no I/O) so QUOTE_TOKENS works out of the box.

Sources (Burton; corroborated by docs.robinhood.com/chain/protocol-contracts and sqd.dev):
  4663 mainnet: WETH 0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73 (18 dec),
                USDG (Paxos Global Dollar, the official dollar token; there is NO canonical
                USDC) 0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168 (6 dec).
  46630 testnet: WETH 0x7943e237c7F95DA44E0301572D358911207852Fa (no USDG).
Uniswap v4 on 4663: PoolManager 0x8366a39c... (two independent sources: the Uniswap
deployments page and github.com/saturn012/v4-pool-index), StateView 0xf3334192... (ONE
source, so it is verified on-chain at startup; see v4.V4Reader.verify).
No pool address is ever hardcoded: the WETH/USDG pool is discovered at runtime (quotes.py).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional

NATIVE = "0x" + "00" * 20  # Uniswap v4 currency 0x0 = native ETH (priced like WETH)


@dataclass(frozen=True)
class ChainProfile:
    chain_id: int
    weth: Optional[str] = None
    usdg: Optional[str] = None  # the reference USD stable for WETH discovery
    v4_pool_manager: Optional[str] = None
    v4_state_view: Optional[str] = None

    def quote_tokens(self) -> Dict[str, dict]:
        """Default QUOTE_TOKENS entries. 'weth' = priced from the deepest WETH/USDG pool."""
        out: Dict[str, dict] = {}
        if self.usdg:
            out[self.usdg] = {"symbol": "USDG", "usd": "stable"}
        if self.weth:
            out[self.weth] = {"symbol": "WETH", "usd": "weth"}
            out[NATIVE] = {"symbol": "ETH", "usd": "weth"}
        return out


PROFILES: Dict[int, ChainProfile] = {
    4663: ChainProfile(
        4663,
        weth="0x0bd7d308f8e1639fab988df18a8011f41eacad73",
        usdg="0x5fc5360d0400a0fd4f2af552add042d716f1d168",
        v4_pool_manager="0x8366a39cc670b4001a1121b8f6a443a643e40951",
        v4_state_view="0xf3334192d15450cdd385c8b70e03f9a6bd9e673b"),
    46630: ChainProfile(46630, weth="0x7943e237c7f95da44e0301572d358911207852fa"),
}


def profile_for(chain_id: int) -> ChainProfile:
    """Built-in profile, or an empty one (no defaults) for an unknown chain."""
    return PROFILES.get(chain_id, ChainProfile(chain_id))
