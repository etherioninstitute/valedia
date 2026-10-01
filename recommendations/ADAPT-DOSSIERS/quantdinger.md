# ADAPT: quantdinger

**Source:** `vendors/inbox/QuantDinger` → `~/MemPalace/sources/repos/github/QuantDinger` @ `9e3095d` (v5.4.1)  
**Upstream:** https://github.com/OpenByteInc/QuantDinger  
**License:** Backend Apache-2.0. Name / logo / trademarks are not granted (`TRADEMARKS.md`). No UI vendored.  
**Stack:** study clone only. Not installed. Not on `PYTHONPATH` for writers.  
**Audited:** 2026-09-30 Phase 1  
**Stamp:** `recommendations/QUANTDINGER-SUBSUME-2026-09-30.md`

## 1) Summary
Signal-only virtual fills: **5 bps** commission + **5 bps** adverse slippage, role **taker**. Commission is `qty * fill_price * rate`. Leverage is recorded and not multiplied again. Valedia's default crypto-paper book stays `CRYPTO_FEE_PCT` **10 bps** + `CRYPTO_SLIPPAGE_PCT` **5 bps**.

## 2) Pipeline fit
`paper compare` next to `crypto_paper`. Not a second OMS. Not a Kalshi/Poly path.

## 3) Overlap
`crypto_paper.fill_px` and `fee_usd` already do adverse slip and a rate on notional. `forward_test_ledger._is_non_real_source` already drops any source containing `paper`. Kelly is not fed.

## 4) Steal (Phase 1)
- `scripts/integrations/quantdinger_virtual_costs.py` — open/add/reduce/close, long and short
- Opt-in label: `paper_decision(opt_in=False)` keeps `fee_table=CRYPTO_FEE_PCT`. `opt_in=True` labels `quantdinger_virtual`
- Fixture: `data/quantdinger-paper/decision.json` (`create_order` false, `counts_toward_kelly` false, oil `NO_HARVEST`)
- $100 notional compare on that row: QD commission **$0.05**, crypto-paper fee **$0.10**

## 5) Hard exclusions
- No docker / compose, no upstream order-adapter import, no billing, no `AGENT_LIVE`
- No `--live`, no RH/CB Instant, no `MIROFISH_AUTO_RAISE`
- Oil stays `NO_HARVEST` / `LIVE_EXEC_FENCED_SERIES`. Weather stays `WEATHER_ARB` fenced
- No allocator execute, no crontab, no trademark rebrand
- Phase 2 evolution stats, Phase 3 Jev question pack, Phase 4 intent keys: not this slice

## 6) Valedia shape
**shadow compare.** The helper does not write `data/crypto-paper/` and does not change `CRYPTO_FEE_PCT`.

## 7) Effort / deps
**S.** Stdlib plus the two `crypto_paper` functions. No new package.
