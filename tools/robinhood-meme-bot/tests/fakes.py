"""Offline fake JSON-RPC transport and real-shaped fixtures. No network is ever used."""
from __future__ import annotations

from decimal import Decimal, getcontext

from rh_meme_bot import abi

getcontext().prec = 60

FACTORY = "0x1f7d7550b1b028f7571e69a784071f0205fd2efa"
MEME = "0x" + "aa" * 20
QUOTE = "0x" + "bb" * 20  # USDC-like stable, 6 decimals
WETH = "0x" + "f1" * 20  # pool-priced quote, 18 decimals
WETH_POOL = "0x" + "f2" * 20  # USDC/WETH pool: 1 WETH = 2000 USDC
MEME2 = "0x" + "a1" * 20
POOL2 = "0x" + "c2" * 20  # MEME2/WETH pool
QUOTE_ENV = f"{QUOTE}:USDC:stable"
WETH_ENV = f"{WETH}:WETH:pool:{WETH_POOL}"
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


def _log_matches(lg: dict, flt: dict) -> bool:
    """Address/topic filter like a node. Logs without an address (deliberately malformed
    fixtures) match any filter so decoders still see them."""
    if "address" not in lg:
        return True
    if flt.get("address") and lg["address"].lower() != flt["address"].lower():
        return False
    topics = lg.get("topics") or []
    for i, t in enumerate(flt.get("topics") or []):
        if t is not None and (i >= len(topics) or topics[i].lower() != t.lower()):
            return False
    return True


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
            res = [lg for lg in self.logs if lo <= int(lg["blockNumber"], 16) <= hi
                   and _log_matches(lg, p[0])]
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


def sqrt_for(price_t1_per_t0: float, dec0: int, dec1: int) -> int:
    """sqrtPriceX96 giving a human-unit token1-per-token0 price (matches price_from_sqrt)."""
    raw = Decimal(str(price_t1_per_t0)) * Decimal(10) ** (dec1 - dec0)
    return int(raw.sqrt() * Decimal(2 ** 96))


def set_balance(rpc: "FakeRpc", token: str, holder: str, amount: int) -> None:
    rpc.set_call(token, abi.encode_balance_of(holder), "0x" + uint_word(amount))


class FakeClock:
    """Deterministic perf_counter replacement; the fake Nimble advances it."""

    def __init__(self):
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def advance_ms(self, ms: float) -> None:
        self.t += ms / 1000.0


NIMBLE_DIGEST_FULL = "24e550a16a70" + "ab" * 26  # what `ollama list` shows as 24e550a16a70


def nimble_tags(name="nimble:latest", digest=NIMBLE_DIGEST_FULL) -> dict:
    """Shape of Ollama GET /api/tags."""
    return {"models": [{"name": name, "model": name, "digest": digest, "size": 1,
                        "details": {"family": "qwen"}},
                       {"name": "jev:latest", "model": "jev:latest", "digest": "ff" * 32}]}


def nimble_body(action="buy", conf=0.9, risk="2", model="nimble2") -> dict:
    rest = {"buy": 0.05, "hold": 0.05, "skip": 0.05}
    rest[action] = conf
    return {"model": model, "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            "answers": {
                "action": {"type": "choice", "choice": action, "probabilities": rest,
                           "confidence": conf},
                "risk_score": {"type": "choice", "choice": risk,
                               "probabilities": {risk: 0.6, str(min(10, int(risk) + 1)): 0.4},
                               "confidence": 0.6}}}


def default_nimble_response(payload: dict) -> dict:
    st = payload["state"]
    good = st.get("owner_renounced") is True and st.get("contract_has_code") is True
    model = payload.get("model", "nimble2")
    return nimble_body("buy", 0.91, "2", model) if good else nimble_body("skip", 0.8, "9", model)


class FakeNimble:
    """Callable Nimble transport ``(url, payload, timeout) -> body``. Records requests."""

    def __init__(self, clock: "FakeClock" = None, step_ms: float = 100.0, respond=None):
        self.requests: list = []
        self.clock, self.step_ms = clock, step_ms
        self.respond = respond or default_nimble_response
        self.fail: Exception = None
        self.tags = nimble_tags()  # GET /api/tags body (dict) or an Exception to raise
        self.tag_requests: list = []

    def get(self, url, timeout):
        """Fake GET {nimble base}/api/tags (digest pin check)."""
        self.tag_requests.append((url, timeout))
        if isinstance(self.tags, Exception):
            raise self.tags
        return self.tags

    def __call__(self, url, payload, timeout):
        self.requests.append((url, payload, timeout))
        if self.clock:
            self.clock.advance_ms(self.step_ms)
        if self.fail:
            raise self.fail
        return self.respond(payload)


def standard_scenario(head=1000) -> FakeRpc:
    """MEME(18d, renounced, token0)/USDC(6d, token1) pool at 0.0005 USDC per MEME with
    30,000 USDC + 40M MEME in the pool, plus a no-bytecode token pool and a malformed log."""
    rpc = FakeRpc(head=head)
    add_token(rpc, MEME, symbol="MEME", owner=None, bytes32_symbol=True)
    add_token(rpc, QUOTE, decimals=6, symbol="USDC", owner_fn=False)
    add_pool(rpc, POOL, MEME, QUOTE, sqrt_price=sqrt_for(0.0005, 18, 6), tick=-76000)
    set_balance(rpc, QUOTE, POOL, 30_000 * 10 ** 6)
    set_balance(rpc, MEME, POOL, 40_000_000 * 10 ** 18)
    add_pool(rpc, RUG_POOL, RUG, QUOTE, sqrt_price=2 ** 96, liquidity=10)
    set_balance(rpc, QUOTE, RUG_POOL, 100 * 10 ** 6)
    rpc.logs = [pool_created_log(MEME, QUOTE, 3000, 60, POOL, head - 5, 0),
                {"topics": ["0x1"], "data": "0x", "blockNumber": hex(head - 4)},  # malformed
                pool_created_log(RUG, QUOTE, 10000, 200, RUG_POOL, head - 3, 1)]
    return rpc


def weth_scenario(head=1000) -> FakeRpc:
    """MEME2/WETH pool where WETH is USD-priced via the USDC/WETH pool (2000 USDC per WETH)."""
    rpc = standard_scenario(head)
    add_token(rpc, WETH, decimals=18, symbol="WETH", owner_fn=False)
    add_token(rpc, MEME2, symbol="MEME2", owner=None)
    add_pool(rpc, WETH_POOL, QUOTE, WETH, sqrt_price=sqrt_for(0.0005, 6, 18))
    add_pool(rpc, POOL2, MEME2, WETH, sqrt_price=sqrt_for(0.0001, 18, 18))
    set_balance(rpc, WETH, POOL2, 10 * 10 ** 18)  # 10 WETH = 20,000 USD -> liquidity 40,000
    set_balance(rpc, MEME2, POOL2, 100_000_000 * 10 ** 18)
    rpc.logs = [pool_created_log(MEME2, WETH, 3000, 60, POOL2, head - 2, 0)]
    return rpc
