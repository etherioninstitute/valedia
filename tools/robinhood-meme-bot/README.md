# rh-meme-bot

A **monitoring-only** bot for new memecoin pools on Robinhood Chain (chain ID 4663). It watches
**both** Uniswap V3 (factory `PoolCreated`) and Uniswap V4 (PoolManager `Initialize`) for new
pools, reads on-chain facts about each pool and token, asks a local **Nimble 2** model one
narrow question, enforces hard numeric gates in code, and records **simulated** positions.
Every decision (buy, hold, skip, error) is logged. It is self-contained and independent of any
other code in this repo.

## Safety: paper trading only

- No transactions are ever signed or sent. There is no wallet, signer, or private-key handling
  anywhere in the code, and no key variables in the configuration.
- RPC methods used are read-only: `eth_chainId`, `eth_blockNumber`, `eth_getLogs`,
  `eth_getCode`, `eth_call`. The only other request is a loopback `GET /api/tags` (digest pin).
- `LiveSwapExecutor.swap()` raises `NotImplementedError` by design.
- The model is called only on loopback (`127.0.0.1`) unless you set `NIMBLE_ALLOW_REMOTE=1`.
- The risk answer and every score are heuristics. **Not financial advice.** New memecoin pools
  are frequently scams; passing every gate does not make a token safe.

## Install

```bash
cd tools/robinhood-meme-bot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && pip install -e .   # then create .env (see Configure)
```

## How the decision flow works

```
PoolCreated (v3) / Initialize (v4) -> state (code) -> decide -> hard gates (code) -> paper buy -> log
```

**The model answers one narrow question; code enforces every number.** Nimble 2 only sees
precomputed facts (liquidity in USD, owner status, price, supply, flags) and answers two
multiple-choice questions: `action` (buy / hold / skip) and `risk_score` (a choice among
labels "1".."10"). It never computes anything, and no threshold (20000, 0.70, 4) appears in any
question text. `gates.py` then applies the numbers. If the model is down, slow, refused, or
returns anything malformed, the result is `skip` (fail-closed for buying).

At pool creation there is no momentum data, so the questions only concern facts in the state.
Risk is a 10-way `choice`, not Ollama's `score` type (its index base is ambiguous and an
off-by-one would silently move the risk gate). `risk_score = int(choice)`;
`risk_expected = sum(k * p_k)` is recorded for information only.

### Nimble 2 setup and pin

Endpoint: `POST http://127.0.0.1:11434/v1/systemone` (Ollama >= 0.35, System One). No auth.
Default tag `nimble:latest`: Burton's Nimble 2 (Bespoke-Nimble-9B-v2, 8192 ctx), which
`ollama list` shows with ID `24e550a16a70`. Pin rule (`src/rh_meme_bot/nimble_pin.py`):

- Explicit Nimble 2 names pass by name (`nimble2`, `nimble2:<tag>`, `nimble:2|v2|9b-v2`,
  `bespoke-nimble-9b-v2`).
- `nimble:latest` and bare `nimble` pass **only** while `GET {nimble base}/api/tags` lists the
  model with a digest starting with `NIMBLE2_DIGEST` (default `24e550a16a70`; override with
  `NIMBLE_DIGEST`, an optional `sha256:` prefix is accepted). Checked in `warmup()` and cached
  per process: a pass for 300 s (`NIMBLE_DIGEST_TTL_S`; junk, <= 0, inf or nan mean 300), then
  re-verified so a re-pull with another digest is caught; a failure for 30 s, then retried.
  Unverified (Ollama down, model not listed, no digest) or mismatched = refused: every decision
  is a fail-closed skip with error `digest_unverified: ...` / `digest_mismatch: ...`, logged,
  and nothing is POSTed.
- Explicit v1 tags (`nimble:9b`, `nimble:v1`, ...) and Tev tags are always refused.

This copies Valedia's rule (source of truth: `scripts/integrations/systemone_pin.py`); the
project stays standalone, so it is not imported. The model's own temperature is not
overridden. At startup `warmup()` makes one tiny call so the model is resident (`keep_alive`).

### Latency: timeout vs budget vs warmup

| Setting | Default | Role |
|---|---|---|
| `WARMUP_TIMEOUT_S` | 120 | startup warmup call only; the first call cold-loads the 9B model |
| `DECISION_TIMEOUT_S` | 10 | per-decision cap, **checked on return**: an answer that took longer is discarded and the pool is skipped, fail-closed |
| `DECISION_LATENCY_BUDGET_MS` | 500 | **observable target only**; never blocks or skips anything |

The cap is not a preemptive kill: the HTTP timeout bounds each socket read and the engine
compares total elapsed time to the cap when the call returns (plugin engines get the same
check, but a plugin that hangs forever is not interrupted). The under-0.5 s goal depends on
hardware and a warm model and was not measured here (3-4 s warm on a laptop with an earlier
Nimble), so a slower-than-budget decision still trades if it answers within the cap. Each
decision records `latency_ms` and `within_budget`; the status line shows model, warm/cold,
last latency, p50/p95 and the **budget hit-rate**. A low hit-rate with p50 of a few seconds means decisions work but are slow
(smaller quant or more memory are the levers, not a higher timeout); decisions over
`DECISION_TIMEOUT_S` show up as skips with a `timeout` error.

## Configure

Settings come from `.env` (real environment variables take precedence). Invalid values stop
startup with every problem listed. `.env` is gitignored. Every variable has a default;
chain-specific defaults (quote tokens, v4 addresses) come from the built-in profile for
`CHAIN_ID`. Starter `.env`:

```bash
RPC_URL=https://rpc.mainnet.chain.robinhood.com
CHAIN_ID=4663
FACTORY_ADDRESS=0x1f7d7550B1b028f7571E69A784071F0205FD2EfA
QUOTE_TOKENS=
V4_POOL_MANAGER=0x8366a39cc670b4001a1121b8f6a443a643e40951
V4_STATE_VIEW=0xf3334192d15450cdd385c8b70e03f9a6bd9e673b
ALLOWED_V4_HOOKS=
ALLOW_DYNAMIC_FEE=0
RECHECK_BLOCKS=300
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
NIMBLE_MODEL=nimble:latest
NIMBLE_DIGEST=24e550a16a70
NIMBLE_DIGEST_TTL_S=300
NIMBLE_KEEP_ALIVE=30m
NIMBLE_ALLOW_REMOTE=0
DECISION_TIMEOUT_S=10.0
WARMUP_TIMEOUT_S=120
DECISION_LATENCY_BUDGET_MS=500
ENGINE_RETRY_BLOCKS=10
ENGINE_BREAKER_ERRORS=3
ENGINE_COOLDOWN_S=30
```

### Quote tokens (built in per chain; QUOTE_TOKENS overrides/extends)

The meme token is the non-quote side of each pool. Built-in profiles (`profiles.py`; sources:
Burton, corroborated by docs.robinhood.com/chain/protocol-contracts and sqd.dev):

| Chain | Token | Address | Valued as |
|---|---|---|---|
| 4663 | USDG (Paxos Global Dollar, the official dollar token) | `0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168` (6 dec) | stable, 1.00 USD |
| 4663 | WETH | `0x0Bd7D308f8E1639FAb988df18A8011f41EAcAD73` (18 dec) | deepest WETH/USDG pool |
| 4663 / 46630 | native ETH (v4 currency `0x0`) | - | same price as WETH |
| 46630 testnet | WETH | `0x7943e237c7F95DA44E0301572D358911207852Fa` | unknown (no USDG on testnet) |

There is **no canonical USDC** on Robinhood Chain: a token named "USDC" elsewhere is not a
stable. WETH's USD price is discovered **at runtime**, never from a hardcoded pool: V3
factory `getPool(WETH, USDG, fee)` over fee tiers 100/500/3000/10000, using the pool holding the
most USDG (`balanceOf`). With no V3 pool, a hookless V4 WETH/USDG or ETH/USDG pool found via
StateView is the fallback. No pool = WETH USD unknown = `liquidity_usd` empty = skip.
`quote_usd(token, chain_profile, reader)` in `quotes.py` is the reusable function.
`QUOTE_TOKENS` (comma-separated) extends the profile: `0xADDR:SYMBOL:stable` is 1.0 USD;
`0xADDR:SYMBOL:pool:0xPOOL` is valued from that V3 pool's slot0 (other side a stable).

## Uniswap v3 and v4: both are watched

Launches happen on both, so the bot watches both; v4 pools add hooks and dynamic fees, which
get their own hard gates. One unified pool record carries
`version` ("v3"/"v4"), the pool address (v3) or bytes32 pool id (v4), `hooks`, `fee` and
`tick_spacing`. The v4 id is recomputed as `keccak256(abi.encode(PoolKey))` and must equal the
event id, otherwise the log is skipped.

| Contract (chain 4663) | Address | Sources |
|---|---|---|
| V3 factory | `0x1f7d7550B1b028f7571E69A784071F0205FD2EfA` | `FACTORY_ADDRESS` default |
| V4 PoolManager | `0x8366a39cc670b4001a1121b8f6a443a643e40951` | two independent: Uniswap deployments page, github.com/saturn012/v4-pool-index |
| V4 StateView | `0xf3334192d15450cdd385c8b70e03f9a6bd9e673b` | **one source only** |

Because StateView has one source, both v4 addresses are configurable (`V4_POOL_MANAGER`,
`V4_STATE_VIEW`) and **verified at startup**: both must have bytecode, `StateView.poolManager()`
must equal the PoolManager, and `StateView.getSlot0` must succeed. On failure the v4 watcher is
disabled with a warning (`v4 DISABLED: ...`) and v3 keeps running; a transient RPC error leaves
v4 unverified and retried every poll. Chain 46630 has no built-in v4 addresses (v4 off).

v4 specifics: currency `0x0` is native ETH (no ERC-20 call on address 0). Price comes from
`StateView.getSlot0(poolId)` with the same Decimal math as v3. v4 has no per-pool contract, so
real reserves are rebuilt from the pool's `ModifyLiquidity` events (net `liquidityDelta` per
tick range, then `getAmountsForLiquidity` with an exact TickMath port). The in-range sum must
equal `StateView.getLiquidity(poolId)`; otherwise liquidity is unknown and the pool is skipped.

### Re-check window

Liquidity is often added blocks after pool creation. A pool whose only failing **fact** gate is
`liquidity_usd` (owner, code, price, hooks and fee gates pass; the model's answer is not part
of this test because the model is asked again) is re-evaluated every poll until
`creation block + RECHECK_BLOCKS` (default 300; `0` = off). Facts are rebuilt each poll; only
when they change is Nimble called again and a decision-log line written (`"recheck": n`). An
engine error (Nimble down, timeout, digest refusal) is not a verdict: the pool stays watched
and is re-asked (after the backoff below) until the window ends. A buy ends the watch, and the
`duplicate_pool` rule still prevents any re-buy.

### Engine failure backoff

A failing or hanging Nimble would otherwise be re-asked for every watched pool each poll, up to
`DECISION_TIMEOUT_S` per call. Two guards (`recheck.py`, `backoff.py`; `0` turns each off):

- **Per-pool:** after an engine error the pool is not re-asked for `ENGINE_RETRY_BLOCKS`
  chain blocks (default 10). New pools are still asked once at detection.
- **Circuit breaker:** after `ENGINE_BREAKER_ERRORS` consecutive engine errors (default 3) all
  engine calls pause for `ENGINE_COOLDOWN_S` seconds (default 30). Each affected pool gets one
  decision-log line, a skip with error `engine_cooldown`, and no call is made. Afterwards the
  next call is a probe: one more error reopens it, a success resets it. Settings are validated
  at startup; an `engine_cooldown` pool that passed the fact gates stays in the re-check window.

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
| v4_hooks | no hooks contract, or hooks in the allow list (hooks can tax or block sells: honeypot) | `ALLOWED_V4_HOOKS` (default empty) |
| v4_dynamic_fee | static fee, or dynamic fee (`fee == 0x800000`) explicitly allowed | `ALLOW_DYNAMIC_FEE=1` |

A token with no `owner()` function is **not** treated as renounced (`owner_status` is
`no_owner_fn`), so it fails the owner gate. Missing data always fails a gate.

`liquidity_usd = 2 x quote_reserve_usd`: the pool's real holding of the quote token in USD (v3:
ERC-20 `balanceOf(pool)`; v4: amounts rebuilt from ModifyLiquidity). Only the quote side is
real exit liquidity (the meme side is valued at a price the deployer controls, and launches are
often single-sided); a balanced pool's TVL equals 2x its quote side. `tvl_usd` (both sides at
pool price) is informational, as are the boolean `flag_*` facts in the state.

## Run

```bash
python -m rh_meme_bot                     # live rich dashboard (Ctrl+C to quit)
python -m rh_meme_bot --headless --once   # one poll, plain lines, exit (CI-friendly)
python -m rh_meme_bot --once --redecide   # also re-decide pools already in the decision log
rh-meme-bot --headless                    # plain lines, continuous
```

The dashboard has three panels plus an engine/watchers status line (model, latency, and
`watchers: v3 ok | v4 ok` or `v4 DISABLED: <reason>`): new pool launches (time, version v3/v4,
pool, token, fee (`dyn` for dynamic), price, liquidity USD, owner/code flags, v4 `hooks:Y/N`),
decisions (model action, confidence, risk, latency, final action, first failing gate; buys in
bold green `BUY ★`), and an action log. Paper positions append to `POSITIONS_FILE`:
`{"id", "timestamp" (UTC ISO), "pool", "token", "size", "entry_price", "simulated": true}`.

## Decision log and review

`DECISIONS_LOG` (default `decisions.jsonl`, gitignored) gets one JSON line per decision, every
outcome: one at detection, plus one per re-check whose facts changed (or whose earlier answer
was an engine error, or an `engine_cooldown` skip). Each line has timestamp, pool, `version`,
`recheck`, `pool_record`, full state, the model's raw response, parsed decision, latency,
`within_budget`, the gate list (value / threshold / passed), `final_action`, `position_id`.

```bash
python -m rh_meme_bot review --last 20                # most recent 20
python -m rh_meme_bot review --action skip            # what the model said no to
python -m rh_meme_bot review --action buy --json      # full records (--final = post-gate)
```

## Restarts and duplicates

`PaperExecutor` reloads `POSITIONS_FILE` at start, continues position ids from the max, and
refuses a second position for the same pool (logged as a `duplicate_pool` skip). Addresses and
v4 pool ids are compared lowercase. The dedupe fails closed: a corrupt or torn line containing a
pool address or id still counts as a known pool. Corrupt decision-log records are skipped with
a warning, and startup errors exit 2 with a one-line message. Pools already in the decision
log are not decided again after a restart inside `LOOKBACK_BLOCKS` (`--redecide` overrides);
pools whose earlier decision ended in an engine error are retried.

## Plug in a different decision model

`DECISION_ENGINE=` (empty) uses Nimble 2; `stub` (or `rh_meme_bot.decision:decide`) selects the
inert stub that always skips; `mypkg.model:decide` loads your own callable:

```python
def decide(state: dict) -> dict:
    return {"action": "buy" | "hold" | "skip", "confidence": 0.0 - 1.0, "risk_score": 1-10}
```

`risk_score` is optional but required for a buy to pass the risk gate. Output is validated:
unknown action, bad confidence, wrong type, or an exception becomes a logged skip. Extra keys
are kept in the decision log under `raw.extra`; an unparseable Nimble body is logged as `raw`.
`state` keys: `version, token_address, symbol, decimals, total_supply, owner_renounced,
owner_status, contract_has_code, pool_address (v3), pool_id (v4), fee_tier (None for a dynamic
fee), has_hooks, dynamic_fee, quote_symbol, price_in_quote, price_usd, liquidity_usd,
quote_reserve_usd, meme_reserve_usd, tvl_usd, flag_*` (v4 also `v4_liquidity_consistent`).
Hooks and dynamic fee reach the model only as booleans (the hooks address is in the log's
`pool_record`). Unreadable facts are `None`.

## Tests

`python3 -m pytest -q` (needs `pytest`) runs fully offline: fake RPC and fake Nimble
transports, no network. Event topics, all selectors and v4 pool ids are checked against an
independent pure-Python keccak-256 (`tests/keccak.py`); TickMath against known vectors and an
80-digit Decimal reference. Backoff tests use a fake chain head and a fake monotonic clock.

## Limitations

- Not run against the live Robinhood RPC or a real Nimble 2. First live check:
  `python -m rh_meme_bot --headless --once`.
- `liquidity_usd` relies on token balances (v3) or ModifyLiquidity history (v4) and the quote
  mapping; fee-on-transfer or rebasing tokens can make balances misleading.
- Positions are only appended, never marked to market or closed. Uniswap **v2** is not watched.
- The re-check list and the breaker state are in memory only; a restart forgets them.
- The V4 StateView address has one source (verified on-chain at startup). Reorgs are not
  handled; polling starts at head minus `LOOKBACK_BLOCKS` (not persisted).
