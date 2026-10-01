# Vaughan arb — Poly↔Kalshi paper/shadow

Paper scanner only. Mode `PAPER/SHADOW`. No writers, no wallets, no `--live`.

```bash
python3 scripts/integrations/vaughan_arb_paper.py
python3 scripts/integrations/vaughan_arb_paper.py --read   # GET books. Still no orders.
```

Default command uses the built-in fixture book (no network). `--read` calls `polymarket_client.get_active_markets` and `kalshi_auth.get_markets`. A quote without both asks is dropped. A Gamma mid is not treated as an ask.

Allowlist pairs (the ArbitrageBot curated slugs) can paper-fill after:

1. Triple gate: `TRADING_MODE` paper/shadow, `LIVE_VENUES` empty, `EXECUTION_DRY_RUN` not false, `POLYMARKET_US_LIVE` off, trading halt off
2. Oil series fence (`KXWTI` / `KXOIL`)
3. Fee-true edge ≥ 1% (Kalshi taker `kalshi_fee_map.fee`, poly rate 0, slip 5 bps), inside $25 / $100 / 5% daily-loss caps
4. Ornith `ornith-1.5-k-m` PASS in the CE-1 JSON shape

Heuristic title matches log `review_only` and never fill. Contradictory names are dropped.

Log: `data/vaughan-arb/decisions.jsonl`. Source `vaughan_arb_paper`. `create_order` is false. The live forward-test ledger is not written. Kelly ignores this source. A would-have pnl is paper.

Controls: `VAUGHAN_ARB_MODE`, `VAUGHAN_ARB_TICK_AGE_S`, `VAUGHAN_ARB_LAST_ORNITH`.

This slice did not install cron and did not start BB-Terminal Slice 2.
