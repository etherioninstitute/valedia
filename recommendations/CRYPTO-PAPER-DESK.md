# CRYPTO-PAPER-1 — paper/shadow crypto simulation ledger

**Version:** ledger shipped `0.47.0`. CE-1 gate `0.62.0`. CE-2 tick `0.63.0`. CE-3 decision log `0.64.0` (working tree, not tagged).  
**Mode:** `PAPER/SHADOW`. Entry gate `PAPER/SHADOW+ORNITH` (`CRYPTO_PAPER_SAFETY`). Not a live trading bot.  
**Fence:** held. Next = MIROFISH-INTEL-ARCHIVE, which the parent launches on exit. This desk does not start it.

Would-have P&L as if a CEX account existed. Simulated fills only. No wallets, no signing, no exchange order APIs.

## What it is

| Piece | Where | Behavior |
|-------|--------|----------|
| Paper book | `scripts/integrations/crypto_paper.py` | cash + positions, mark-to-market |
| Fee stub | same (`--maker` / `--taker`) | default `CRYPTO_FEE_PCT`; fill at mid±`CRYPTO_SLIPPAGE_PCT` |
| Tape | `data/crypto-paper/bars_fixture.json` or public klines | allowlist `BTC-USDT`, `ETH-USDT` |
| Strategy hook | `sma_intents` | long-only SMA(5/20) cross → paper intents, not orders |
| Artifact | `data/crypto-paper/equity.json` | equity curve + daily would-have PnL |
| CLI | `scripts/integrations/crypto_paper_run.py` | dry/fixture default; `--live-data` = public REST reads only |

```bash
python3 scripts/integrations/crypto_paper_run.py
python3 scripts/integrations/crypto_paper_run.py --live-data --pair BTC-USDT
```

`--live-data` GETs Binance public `/api/v3/klines`. No API key. No `/order`. No CCXT `create_order`.

## Mapping to future real wallets (Burton go later)

Paper fields are a rehearsal, not a bridge that can spend:

| Paper now | Later live (Burton only) |
|-----------|--------------------------|
| `book.cash` | venue free cash (GET balances) |
| `positions` | venue lots after fill-true GET |
| `fills[].px` mid±slip | exchange last fill + fee from GET |
| maker/taker stub | venue fee schedule (not this stub) |
| SMA reference hook | Valedia strategy + Kelly + ERE + fee gate |
| `equity.json` would-have | `forward_test_ledger` fee-true rows |

Do not copy paper fills into FTL as live. Do not restore Coinbase/Alpaca writers from this slice. Do not import CCXT writers. Eliza wallet plugins stay out of scope.

## Ideas used (licenses)

- **jesse** (MIT): report shape (start/end equity, n fills, fees, daily PnL). Not vendored.
- **hummingbot** (Apache-2.0): paper tape vs public data, no keystore.
- **ccxt** (MIT): unified pair/ticker thinking. Not imported.
- **freqtrade** (GPL-3.0): pair allowlist *idea only* — reimplemented, not copied.

## Hard fence (do not)

- Wallet private keys, signing, onchain broadcast
- Exchange API keys for trading; CCXT `create_order` / `withdraw`
- Kalshi/Poly writer changes; `KALSHI_FEE_MAP_GATE` flip; `POLYMARKET_US_LIVE`
- `VALEDIA_TRADING_HALT` bypass; ACH/wire/card/treasury auto
- Weather/oil unfence; CB/Alpaca writer restore; `NAUTILUS_LIVE`; `SELF_ADJUST`; `MIROFISH_AUTO_RAISE`
- Crontab install; allocator execute of paper intents
- Calling this a live crypto bot in Signal or status lines — it is a **would-have** ledger

## CE-1 safety gate

`PAPER/SHADOW` + Ornith. `--safety` loads `data/crypto-paper/rules.json`, computes EMA/RSI/SMA/VWAP in `crypto_paper_indicators.py`, and asks LiteLLM alias `ornith-1.5-k-m` (`:4000`, backend Q4). A fill is one `crypto_paper.apply_fill` and only when Ornith returns a complete PASS that matches those numbers. Model down, bad JSON, a missing condition, a Qwen model name, or a local disagreement refuses. No Qwen retry. No venue order.

```bash
python3 scripts/integrations/crypto_paper_run.py --safety
```

Log: `data/crypto-paper/safety-check-log.json`. Paper book on PASS: `data/crypto-paper/safety-equity.json`. Tests: `python3 scripts/integrations/test_crypto_paper_safety.py`.

## CE-2 tick

`--tick` adds MACD(12, 26, 9) and may ask LiteLLM alias `qwen3.8-27b` to rank the watchlist. Ornith `ornith-1.5-k-m` is still required before `apply_fill`. Qwen down, bad JSON, or a pair with no bars does not skip Ornith and cannot fill. Cron is not installed. A later launchd or cron line is the command below.

```bash
python3 scripts/integrations/crypto_paper_run.py --tick
python3 scripts/integrations/crypto_paper_run.py --tick --no-qwen
```

Dead-port proof: `data/crypto-paper/tick-check-log.json` (`qwen_down`, then `ornith_down`, no fill).

## CE-3 decision log

Every `--tick` result appends one row to `data/crypto-paper/decisions.jsonl`. Pass, refuse, `ornith_down`, `duplicate_tick`, and `tick_busy` all write. The row carries side, qty, `entry_fee`, `fee_pct`, `slip_pct`, and `fee_table=CRYPTO_FEE_PCT` (paper table, 0.001 taker, slippage 0.0005). Source is `crypto_paper`. A fill sets `would_have_pnl`. A refuse keeps the fee quote and leaves `would_have_pnl` empty.

The file is not `models/forward_test_ledger.jsonl`. Rows are `type=decision`, not settlements. `real_track_record` and Kelly ignore `source` values that contain `paper`.

Single-flight lock: `decisions.jsonl.lock`. A second tick that finds the lock held writes `tick_busy` and does not fill. The same pair and bar that already filled writes `duplicate_tick` and does not call Ornith again. The log keeps the last 400 rows.

Controls, source `file`:

| Name | Meaning |
|------|---------|
| `CRYPTO_PAPER_TICK_AGE_S` | seconds since the last tick, or `none` |
| `CRYPTO_PAPER_LAST_ORNITH` | last reason (`pass`, `ornith_refuse`, `ornith_down`, `duplicate_tick`, `tick_busy`) |
| `CRYPTO_PAPER_TICK_STALE` | true when the last tick is missing or older than 2 hours |

Cron is still not installed. The command is the same `--tick` line. `ot-valedia-desk` and `valedia-glance` stay up; a glance process started before this slice keeps serving its old snapshot until the next start.

```bash
python3 scripts/integrations/crypto_paper_run.py --tick
python3 scripts/integrations/crypto_paper_run.py --tick --no-qwen
```

Dead-port `--tick --no-qwen` (2026-09-22): `ornith_down`, side buy, qty 9.25388969, would-have fee 1.0005, no fill, `create_order` false. Log: `data/crypto-paper/decisions.jsonl`.

## Status

Fixture run writes `data/crypto-paper/equity.json`. Tests: `python3 scripts/integrations/test_crypto_paper.py` and `test_crypto_paper_safety.py`. No cron installed. No live flags flipped.

The shipped fixture is a synthetic SMA-cross tape so the ledger has fills. Its would-have PnL is **not** a live track record and must not be reported as realized profit. The CE-1 safety fill is the same kind of would-have.

## FEED freshness (2026-09-29 PT) — Binance.US dual-home

- **Root cause:** `KLINES_URL` pointed at `api.binance.com` (HTTP 451 from Studio). `--tick` always loaded stale `bars_safety_pass.json` (BTC bar_ts 2026-01-02; ETH 0 bars) and ignored `--live-data`.
- **Fix (workspace-v2 + accel-ornith twin):** `KLINES_URL` → `https://api.binance.us/api/v3/klines?...`. `--tick` defaults to public Binance.US bars for watchlist BTC+ETH unless `--fixture` pins a file. `create_order=false` `live=false` unchanged.
- **Prove:** one `--tick --no-qwen` → `CRYPTO_PAPER_TICK_AGE_S=0`, `stale=false`, `bar_ts` current hour UTC, no orders.
- **Still open:** `:8788` daviddtech webhook not listening (secondary; do not blind-restart). Ornith `:4000` auth **fixed** (see section below). Cron for `--tick` still not installed.

## Ornith :4000 auth (2026-09-29 PT) — LiteLLM master key

- **Symptom (FEED):** `POST /v1/chat/completions` model `ornith-1.5-k-m` → **HTTP 401** while `GET /health` → **200**. LiteLLM log: `No api key passed in.`
- **Root cause:** LiteLLM `general_settings.master_key` gates chat. `/health` (and list `/v1/models`) stay public. Clients that omit `Authorization: Bearer $LITELLM_MASTER_KEY` get 401. Key lives in `~/.litellm/env` (and `master.key`); launchd `com.burton.litellm-gateway` sources that env.
- **Fix (workspace-v2 only):** `crypto_paper_safety._litellm_key()` reads env → `~/.litellm/env` → `~/.litellm/master.key`. `_http_post` always sends Bearer; missing key raises `ornith_auth` before the wire. HTTP 401 surfaces as reason `ornith_auth` (not opaque `ornith_down`). `_content` falls back to `reasoning_content` when visible content is empty (Ornith reasoning budget under `drop_params`).
- **Dual-home:** Auth client fix is **workspace-v2** `scripts/integrations/crypto_paper_safety.py` only. `workspace-accel-ornith` has the Binance.US `KLINES_URL` twin for bars but **no** CE-1 `--safety`/`--tick` CLI (older stub `crypto_paper_run.py`). No LiteLLM config.yaml edit; no M2 mirror required for :4000 (M1-only gateway).
- **Prove (no --live / no orders):**
  - `GET :4000/health` → **200** (Ornith Q4 endpoints healthy).
  - Unauth chat → **401** (`No api key passed in`).
  - Auth chat via `_http_post` (key from file, env cleared) → **200**, content `PONG`, model `ornith-1.5-k-m`.
  - `python3 crypto_paper_run.py --safety` (fixture) → **pass=true**, model `ornith-1.5-k-m`, `create_order=false`, `live=false`, 1 would-have fill.

