# CRYPTO-PAPER-1 — paper/shadow crypto simulation ledger

**Version:** `0.47.0`  
**Mode:** `PAPER/SHADOW` research ledger. Not a live trading bot.  
**Fence:** held. Next = Burton (AUTONOMY-2 MIROFISH-JEV-SHADOW still available).

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

## Status

Fixture run writes `data/crypto-paper/equity.json`. Tests: `python3 scripts/integrations/test_crypto_paper.py`. No cron. No live flags flipped.

The shipped fixture is a synthetic SMA-cross tape so the ledger has fills. Its would-have PnL is **not** a live track record and must not be reported as realized profit.
