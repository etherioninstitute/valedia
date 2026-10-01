# rh-meme-bot

A **monitoring-only** bot for new memecoin pools on Robinhood Chain (chain ID 4663).
It watches the Uniswap V3 factory for `PoolCreated` events, reads on-chain facts about each new
pool and token, asks a local **Nimble 2** model one narrow question, enforces hard numeric gates
in code, and records **simulated** positions. Every decision (buy, hold, skip, error) is logged.
It is self-contained and independent of any other code in this repo.

## Safety: paper trading only

- No transactions are ever signed or sent. There is no wallet, signer, or private-key handling
  anywhere in the code, and no key variables in the configuration.
- RPC methods used are read-only: `eth_chainId`, `eth_blockNumber`, `eth_getLogs`,
  `eth_getCode`, `eth_call`.
- `LiveSwapExecutor.swap()` raises `NotImplementedError` by design.
- The model is called only on loopback (`127.0.0.1`) unless you set `NIMBLE_ALLOW_REMOTE=1`.
- The risk answer and every score are heuristics. **Not financial advice.** New memecoin pools
  are frequently scams; passing every gate does not make a token safe.

## Install

```bash
cd tools/robinhood-meme-bot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && pip install -e .
# create .env from the template in the Configure section below
```

## How the decision flow works

```
PoolCreated -> build state (code) -> decide(state) -> hard gates (code) -> paper buy -> log
```

**The model answers one narrow question; code enforces every number.** Nimble 2 only sees
precomputed facts (liquidity in USD, owner status, price, supply, flags) and answers two
multiple-choice questions: `action` (buy / hold / skip) and `risk_score` (a choice among
labels "1".."10"). It never computes anything, and no threshold (20000, 0.70, 4) appears in any
question text. `gates.py` then applies the numbers. If the model is down, slow, refused, or
returns anything malformed, the result is `skip` (fail-closed for buying).

At pool creation there is no momentum or buy-pressure data, so the questions only concern facts
in the state. Risk uses a 10-way `choice` rather than Ollama's `score` type because the score
index scale is ambiguous (0- vs 1-based) and an off-by-one would silently move the risk gate.
`risk_score = int(choice)`; `risk_expected = sum(k * p_k)` from the returned probabilities is
recorded for information only.

### Nimble 2 setup

Endpoint: `POST http://127.0.0.1:11434/v1/systemone` (Ollama >= 0.35, System One). No auth.
Default model tag is `nimble2`; **the exact tag on your machine is unverified**, so confirm it:

```bash
ollama pull <your Nimble 2 source>   # Bespoke-Nimble-9B-v2 (8192 ctx)
ollama list                          # confirm the tag, then set NIMBLE_MODEL if it differs
```

The tag must pass the Nimble 2 pin rule (`nimble2`, `nimble2:<tag>`, `nimble:2|v2|9b-v2`,
`bespoke-nimble-9b-v2`); v1 and Tev tags are refused and never POSTed. The pin function is a
copy of Valedia's `is_nimble2` (source of truth: `scripts/integrations/systemone_hotpath_client`).
The model's own calibrated temperature is not overridden.

At startup `warmup()` makes one tiny call so the model is resident (`keep_alive`, default 30m).

### Latency observability

The goal is a decision in under half a second; this cannot be measured from the build sandbox,
so it is made observable instead. Each decision records `latency_ms` and `within_budget`
(`DECISION_LATENCY_BUDGET_MS`, default 500). The dashboard status line shows model, warm/cold,
last latency, p50/p95 over the session, and the budget hit-rate. The first call after a cold
load is slow; check the warmup line and subsequent numbers on your hardware.

## Configure

All settings come from `.env` (or real environment variables, which take precedence). Invalid
values stop startup with a message listing every problem. `.env` is gitignored. Every variable
has a default except the quote-token addresses. Starter `.env`:

```bash
RPC_URL=https://rpc.mainnet.chain.robinhood.com
CHAIN_ID=4663
FACTORY_ADDRESS=0x1f7d7550B1b028f7571E69A784071F0205FD2EfA
QUOTE_TOKENS=
POLL_INTERVAL_SECONDS=5
LOOKBACK_BLOCKS=200
MAX_BLOCK_RANGE=2000
MIN_LIQUIDITY_USD=20000
MIN_CONFIDENCE=0.70
MAX_RISK_SCORE=4
PAPER_SIZE=100
POSITIONS_FILE=positions.jsonl
DECISIONS_LOG=decisions.jsonl
DECISION_ENGINE=
NIMBLE_URL=http://127.0.0.1:11434/v1/systemone
NIMBLE_MODEL=nimble2
NIMBLE_KEEP_ALIVE=30m
NIMBLE_ALLOW_REMOTE=0
DECISION_TIMEOUT_S=2.0
DECISION_LATENCY_BUDGET_MS=500
```

### QUOTE_TOKENS (required for USD liquidity)

The meme token is the non-quote side of each pool. List the quote tokens, comma-separated:

- `0xADDR:SYMBOL:stable` is valued at 1.0 USD (e.g. USDC).
- `0xADDR:SYMBOL:pool:0xPOOL` is valued from that Uniswap V3 pool's slot0 (the pool's other
  token is assumed to be a USD stablecoin), e.g. WETH priced by a WETH/USDC pool.

```
QUOTE_TOKENS=0x<USDC>:USDC:stable,0x<WETH>:WETH:pool:0x<WETH/USDC pool>
```

**The WETH/USDC addresses on Robinhood Chain are not included; you must fill them in** (no
addresses are invented here). An unknown quote leaves `liquidity_usd` empty, and the liquidity
gate then skips every such pool.

## Hard gates

A paper buy requires **all** to pass (`src/rh_meme_bot/gates.py`, thresholds from `.env`):

| Gate | Passes when | Config |
|---|---|---|
| model_action | model said `buy` | |
| confidence | `confidence >= MIN_CONFIDENCE` | default 0.70 |
| risk_score | `risk_score` present and `<= MAX_RISK_SCORE` | default 4 (of 10) |
| liquidity_usd | known and `>= MIN_LIQUIDITY_USD` | default 20000 |
| owner_renounced | `owner()` returned the zero address | |
| contract_has_code | token address has bytecode | |
| entry_price | pool price readable and > 0 (needed for the paper entry) | |

A token with no `owner()` function is **not** treated as renounced (`owner_status` is recorded
separately as `no_owner_fn`), so it fails the owner gate. Missing data always fails a gate.

`liquidity_usd = 2 x quote_reserve_usd`, where the quote reserve is the pool's ERC-20
`balanceOf` for the quote token valued in USD. Only the quote side is real exit liquidity (the
meme side is valued at a slot0 price the deployer controls, and V3 launches are often
single-sided); a balanced pool's TVL equals 2x its quote side. `tvl_usd` (both sides at pool
price) is recorded for information only. The old 0-1 risk heuristic survives only as
informational boolean flags (`flag_*`) in the state.

## Run

```bash
python -m rh_meme_bot                     # live rich dashboard (Ctrl+C to quit)
python -m rh_meme_bot --headless --once   # one poll, plain lines, exit (CI-friendly)
rh-meme-bot --headless                    # plain lines, continuous
```

The dashboard has three panels plus an engine status line: new pool launches (time, pool,
token, fee, price, liquidity USD, owner/code flags), decisions (model action, confidence, risk,
latency, final action, first failing gate; buys show in bold green `BUY ★`), and an action log.
Paper positions append to `POSITIONS_FILE`:
`{"id", "timestamp" (UTC ISO), "pool", "token", "size", "entry_price", "simulated": true}`.

## Decision log and review

`DECISIONS_LOG` (default `decisions.jsonl`, gitignored) gets one JSON line per pool, every
outcome: timestamp, pool, full state, the model's full raw response, parsed decision, latency,
`within_budget`, the gate list (value / threshold / passed), `final_action`, and `position_id`.

```bash
python -m rh_meme_bot review --last 20                # most recent 20
python -m rh_meme_bot review --action skip            # what the model said no to
python -m rh_meme_bot review --action buy --json      # full records
python -m rh_meme_bot review --final skip             # filter by the post-gate outcome
```

## Plug in a different decision model

`DECISION_ENGINE=` (empty) uses Nimble 2. `DECISION_ENGINE=stub` (or
`rh_meme_bot.decision:decide`) selects the inert stub that always skips.
`DECISION_ENGINE=mypkg.model:decide` loads your own callable:

```python
def decide(state: dict) -> dict:
    return {"action": "buy" | "hold" | "skip", "confidence": 0.0 - 1.0, "risk_score": 1-10}
```

`risk_score` is optional but required for a buy to pass the risk gate. Output is validated:
unknown action, bad confidence, wrong type, or an exception becomes a skip and is logged.
`state` keys: `token_address, symbol, decimals, total_supply, owner_renounced, owner_status,
contract_has_code, pool_address, fee_tier, quote_symbol, price_in_quote, price_usd,
liquidity_usd, quote_reserve_usd, meme_reserve_usd, tvl_usd, flag_*`. Unreadable facts are `None`.

## Tests

```bash
pip install pytest
python3 -m pytest -q        # fully offline: fake RPC and fake Nimble transports, no network
```

The `PoolCreated` topic and all selectors are checked against a pure-Python keccak-256
(`tests/keccak.py`).

## Limitations

- Not exercised against the live Robinhood RPC or a real Nimble 2 here (the build sandbox had no
  access). First live check: `python -m rh_meme_bot --headless --once`. The Nimble tag and
  actual latency are unverified.
- `liquidity_usd` relies on the pool's token balances and your `QUOTE_TOKENS` mapping; tokens
  with fee-on-transfer or rebasing behaviour can make balances misleading.
- Positions are only appended, never marked to market or closed.
- Reorgs are not handled; polling starts at head minus `LOOKBACK_BLOCKS` and the last block is
  not persisted across restarts.
