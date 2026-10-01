# rh-meme-bot

A **monitoring-only** bot for new memecoin pools on Robinhood Chain (chain ID 4663).
It watches the Uniswap V3 factory for `PoolCreated` events, reads on-chain facts about the new
pool and token, asks a pluggable decision engine what to do, applies safety gates, and records
**simulated** positions. It is self-contained and independent of any other code in this repo.

## Safety: paper trading only

- No transactions are ever signed or sent. There is no wallet, signer, or private-key handling
  anywhere in the code, and no key variables in the configuration.
- The RPC methods used are read-only: `eth_chainId`, `eth_blockNumber`, `eth_getLogs`,
  `eth_getCode`, `eth_call`.
- `LiveSwapExecutor.swap()` raises `NotImplementedError` by design.
- The risk score is a crude red-flag heuristic. **Not financial advice.** New memecoin pools are
  frequently scams; a low score does not mean a token is safe.

## Install

```bash
cd tools/robinhood-meme-bot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && pip install -e .
# create .env from the template in the Configure section below
```

## Configure

All settings come from `.env` (or real environment variables, which take precedence).
Invalid values stop startup with a message listing every problem.
`.env` is gitignored. Every variable has a default, so an empty or missing `.env` works.
A starter `.env` (also usable as `.env.example`):

```bash
RPC_URL=https://rpc.mainnet.chain.robinhood.com
CHAIN_ID=4663
FACTORY_ADDRESS=0x1f7d7550B1b028f7571E69A784071F0205FD2EfA
QUOTE_TOKENS=
POLL_INTERVAL_SECONDS=5
LOOKBACK_BLOCKS=200
MAX_BLOCK_RANGE=2000
MIN_LIQUIDITY=1000000
MIN_CONFIDENCE=0.7
MAX_RISK_SCORE=0.5
PAPER_SIZE=100
POSITIONS_FILE=positions.jsonl
DECISION_ENGINE=
```

| Variable | Default | Meaning |
|---|---|---|
| `RPC_URL` | `https://rpc.mainnet.chain.robinhood.com` | JSON-RPC endpoint |
| `CHAIN_ID` | `4663` | Verified against `eth_chainId` on startup; mismatch aborts |
| `FACTORY_ADDRESS` | `0x1f7d7550B1b028f7571E69A784071F0205FD2EfA` | Uniswap V3 factory |
| `QUOTE_TOKENS` | empty | Comma list of quote-token addresses (WETH, stables). The other token in a pool is the memecoin; if empty, token0 is used |
| `POLL_INTERVAL_SECONDS` | `5` | Delay between polls |
| `LOOKBACK_BLOCKS` | `200` | On start, scan from `head - N` |
| `MAX_BLOCK_RANGE` | `2000` | Max blocks per `eth_getLogs` call |
| `MIN_LIQUIDITY` | `1000000` | Gate: minimum raw `pool.liquidity()` units (not USD) |
| `MIN_CONFIDENCE` | `0.7` | Gate: minimum decision confidence |
| `MAX_RISK_SCORE` | `0.5` | Gate: maximum heuristic risk score |
| `PAPER_SIZE` | `100` | Simulated position size (quote-token units) |
| `POSITIONS_FILE` | `positions.jsonl` | Where paper positions are appended |
| `DECISION_ENGINE` | empty | `package.module:function`; empty = inert stub |

## Run

```bash
python -m rh_meme_bot                 # live rich dashboard (Ctrl+C to quit)
python -m rh_meme_bot --headless --once   # one poll, plain lines, exit (CI-friendly)
rh-meme-bot --headless                # plain lines, continuous
```

The dashboard has three panels: new pool launches (time, pool, tokens, fee, price, liquidity,
risk), decisions (engine action and confidence, final action after gates, reason), and an action
log. Paper positions append to `POSITIONS_FILE` as JSON lines:
`{"id", "timestamp" (UTC ISO), "pool", "token", "size", "entry_price", "simulated": true}`.

## Plug in a decision model

Set `DECISION_ENGINE=mypkg.model:decide`. The callable must have this contract:

```python
def decide(state: dict) -> dict:
    return {"action": "buy" | "hold" | "skip", "confidence": 0.0 - 1.0}
```

`state` contains: `pool, token0, token1, fee, tick_spacing, block_number, tx_hash, detected_at,
target_token, quote_token, target_is_token0, symbol, decimals, total_supply, has_bytecode,
owner_status ("renounced" | "owned:<addr>" | "no_owner_fn"), liquidity, tick,
price_token1_per_token0, price_token0_per_token1, target_price_in_quote, risk_score,
risk_reasons`. Facts that could not be read are `None`.

Output is validated: an unknown action, a non-numeric or out-of-range confidence, a wrong type,
or an exception becomes `{"action": "skip", "confidence": 0.0}` and is logged. After the engine
returns, the app gates a `buy`: it is downgraded to `skip` (with the reason shown) unless
`confidence >= MIN_CONFIDENCE`, `risk_score <= MAX_RISK_SCORE`, and `liquidity >= MIN_LIQUIDITY`.
The built-in stub (`rh_meme_bot.decision.decide`) always returns skip.

## Risk heuristic

`risk_score` is the sum of fixed penalties, capped at 1.0 (see `src/rh_meme_bot/risk.py`):
no bytecode at the token address = 1.0 immediately; owner not renounced +0.25; liquidity
unreadable +0.30; liquidity below `MIN_LIQUIDITY` +0.30 (zero: +0.40); total supply
unreadable/zero +0.20; price unreadable +0.15; symbol unreadable +0.10; fee tier >= 1% +0.05.
It checks only a handful of on-chain facts; it cannot detect honeypots, transfer taxes, hidden
mint functions, or proxy upgrades.

## Tests

```bash
pip install pytest
python3 -m pytest -q        # fully offline: uses a fake RPC transport, no network
```

Tests verify the `PoolCreated` topic and the function selectors against a pure-Python keccak-256
(`tests/keccak.py`), so no crypto dependency is needed.

## Limitations

- `MIN_LIQUIDITY` is raw in-range V3 liquidity, not a dollar value; tune it per pool/token pair.
- Pools whose tokens are not a standard quote/meme pair may be mislabeled; set `QUOTE_TOKENS`.
- Positions are only appended, never marked to market or closed.
- Reorgs are not handled; polling starts at the chain head minus `LOOKBACK_BLOCKS` and does not
  persist the last block across restarts.
- Not yet exercised against the live Robinhood RPC in this repo's CI (the sandbox that built
  this had no outbound access to it). First live check:
  `python -m rh_meme_bot --headless --once`.
