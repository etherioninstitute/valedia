# Ponytail audit — CRYPTO-PAPER-1 (0.47.0)

**Mode:** `/ponytail` full on this slice only.
**Fence:** no wallets, no keys, no `create_order`, no CCXT writers, no `POLYMARKET_US_LIVE`, no CB/Alpaca restore, no weather/oil unfence, no halt bypass, `KALSHI_FEE_MAP_GATE` stays False.

## Applied

`yagni:` CCXT / jesse / hummingbot / freqtrade imports. Stdlib + `CRYPTO_FEE_PCT` / `CRYPTO_SLIPPAGE_PCT`. GPL freqtrade = ideas only.

`yagni:` class/factory/OMS. Dict book + functions. One SMA hook.

`yagni:` allocator / crontab / FTL live rows. Artifact JSON is the proof.

`yagni:` new config flags. CLI `--live-data` is the public-read opt-in.

`yagni:` Sharpe/drawdown/order-book/multi-pair. Two fills on a synthetic cross is enough to prove the ledger.

## Findings

`shrink:` `crypto_paper_run.py` is argparse over `run()`. Keep it — slice asked for that CLI path.

Fixture would-have PnL is a constructed tape, not a track record.

**net:** one module + thin CLI + fixture/equity JSON + tests + desk doc. 0 deps. 0 live flag flips. Lean enough. Ship.
