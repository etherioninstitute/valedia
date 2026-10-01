"""Offline fake JSON-RPC transport and real-shaped fixtures. No network is ever used."""
from __future__ import annotations

from rh_meme_bot import abi

FACTORY = "0x1f7d7550b1b028f7571e69a784071f0205fd2efa"
MEME = "0x" + "aa" * 20
QUOTE = "0x" + "bb" * 20
POOL = "0x" + "cc" * 20
RUG = "0x" + "dd" * 20  # token with no bytecode
RUG_POOL = "0x" + "ee" * 20
OWNER = "0x" + "12" * 20


def uint_word(v: int) -> str:
    return format(v & ((1 << 256) - 1), "064x")


def addr_word(a: str) -> str:
    return a[2:].lower().rjust(64, "0")


def abi_string(s: str) -> str:
    b = s.encode().hex()
    return "0x" + uint_word(32) + uint_word(len(s)) + b.ljust(((len(b) + 63) // 64) * 64, "0")


def bytes32_string(s: str) -> str:
    return "0x" + s.encode().hex().ljust(64, "0")


def pool_created_log(token0, token1, fee, tick_spacing, pool, block, idx=0) -> dict:
    return {
        "address": FACTORY,
        "topics": [abi.POOL_CREATED_TOPIC, "0x" + addr_word(token0), "0x" + addr_word(token1),
                   "0x" + uint_word(fee)],
        "data": "0x" + uint_word(tick_spacing) + addr_word(pool),
        "blockNumber": hex(block), "transactionHash": "0x" + "ab" * 32,
        "logIndex": hex(idx),
    }


class FakeRpc:
    """Callable transport: ``rpc(payload) -> response``. Records every request."""

    def __init__(self, chain_id=4663, head=1000):
        self.chain_id, self.head = chain_id, head
        self.logs: list = []
        self.code: dict = {}
        self.calls: dict = {}  # (to, selector) -> hex string | "revert"
        self.requests: list = []
        self.log_ranges: list = []
        self.fail_next: list = []  # exceptions or error dicts consumed one per request

    def set_call(self, to, selector, value):
        self.calls[(to.lower(), selector)] = value

    def __call__(self, payload: dict) -> dict:
        self.requests.append(payload)
        if self.fail_next:
            f = self.fail_next.pop(0)
            if isinstance(f, Exception):
                raise f
            return {"jsonrpc": "2.0", "id": payload["id"], "error": f}
        m, p = payload["method"], payload.get("params", [])
        if m == "eth_chainId":
            res = hex(self.chain_id)
        elif m == "eth_blockNumber":
            res = hex(self.head)
        elif m == "eth_getCode":
            res = self.code.get(p[0].lower(), "0x")
        elif m == "eth_getLogs":
            lo, hi = int(p[0]["fromBlock"], 16), int(p[0]["toBlock"], 16)
            self.log_ranges.append((lo, hi))
            res = [lg for lg in self.logs if lo <= int(lg["blockNumber"], 16) <= hi]
        elif m == "eth_call":
            v = self.calls.get((p[0]["to"].lower(), p[0]["data"]), "revert")
            if v == "revert":
                return {"jsonrpc": "2.0", "id": payload["id"],
                        "error": {"code": 3, "message": "execution reverted"}}
            res = v
        else:
            return {"jsonrpc": "2.0", "id": payload["id"],
                    "error": {"code": -32601, "message": "method not found"}}
        return {"jsonrpc": "2.0", "id": payload["id"], "result": res}


def add_token(rpc: FakeRpc, addr, *, decimals=18, supply=10 ** 27, symbol="MEME",
              owner=None, bytes32_symbol=False, owner_fn=True):
    rpc.code[addr] = "0x6080604052"
    rpc.set_call(addr, abi.SEL_DECIMALS, "0x" + uint_word(decimals))
    rpc.set_call(addr, abi.SEL_TOTAL_SUPPLY, "0x" + uint_word(supply))
    rpc.set_call(addr, abi.SEL_SYMBOL,
                 bytes32_string(symbol) if bytes32_symbol else abi_string(symbol))
    if owner_fn:
        rpc.set_call(addr, abi.SEL_OWNER, "0x" + addr_word(owner or abi.ZERO_ADDRESS))


def add_pool(rpc: FakeRpc, pool, token0, token1, *, sqrt_price, tick=0, liquidity=5_000_000):
    rpc.set_call(pool, abi.SEL_TOKEN0, "0x" + addr_word(token0))
    rpc.set_call(pool, abi.SEL_TOKEN1, "0x" + addr_word(token1))
    slot0 = [sqrt_price, tick, 0, 1, 1, 0, 1]
    rpc.set_call(pool, abi.SEL_SLOT0, "0x" + "".join(uint_word(x) for x in slot0))
    rpc.set_call(pool, abi.SEL_LIQUIDITY, "0x" + uint_word(liquidity))


def standard_scenario(head=1000) -> FakeRpc:
    """MEME(token0, renounced) / QUOTE(token1) pool at price 4, plus a no-bytecode token pool."""
    rpc = FakeRpc(head=head)
    add_token(rpc, MEME, symbol="MEME", owner=None, bytes32_symbol=True)
    add_token(rpc, QUOTE, symbol="WETH")
    add_pool(rpc, POOL, MEME, QUOTE, sqrt_price=2 * 2 ** 96, tick=13863)
    add_pool(rpc, RUG_POOL, RUG, QUOTE, sqrt_price=2 ** 96, liquidity=10)
    rpc.logs = [pool_created_log(MEME, QUOTE, 3000, 60, POOL, head - 5, 0),
                {"topics": ["0x1"], "data": "0x", "blockNumber": hex(head - 4)},  # malformed
                pool_created_log(RUG, QUOTE, 10000, 200, RUG_POOL, head - 3, 1)]
    return rpc
