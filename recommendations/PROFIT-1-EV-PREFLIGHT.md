# PROFIT-1 — fee-true EV preflight + maker-first + fill ledger

Scout-ranked 1→2→3 on the Kalshi fee-true profit path. Research helpers. **No live writers. No `KALSHI_FEE_MAP_GATE` flip.** Version `0.48.0`.

Module: `scripts/integrations/kalshi_profit.py`

## A) Fee-true EV preflight (Scout #1)

```
net = edge − fee(P,C,M)/C − half_spread − slip
```

`fee(P,C,M)` is `kalshi_fee_map.fee` (PDF Jul 7 2026: taker `0.07`, maker `0.0175`, ceil-cent). `P` is executable (`p_exec`), never last trade. Half-spread = `(ask−bid)/2`.

`annotate(opp)` always writes `ev_preflight` on the opp.

**Flag:** `KALSHI_EV_PREFLIGHT_GATE` default **False**. Env `=1` skips suggest/rest/size when `net <= 0`. This lane never sets True.

Call sites (fail-open):

- `kalshi_executor._score_market` (suggest)
- `kalshi_executor._size_contracts` + `run` (size / rest)
- `master_allocator.allocate` + `execute_allocations` (size / rest)

## B) Maker-first router (Scout #2)

Consult fee map (`maker_viable` = maker multiplier 0). Prefer rest on those series. Taker only if taker net ≥ maker net + hurdle (`KALSHI_TAKER_HURDLE`, default `0.02` $/contract).

Fee-map gate may still be False — still annotate. Soft when `KALSHI_MAKER_FIRST` is False (today’s taker fallthrough). Env `=1`: skip taker unless hurdle clears; rest if a live maker manager is already in the path.

**Flag:** `KALSHI_MAKER_FIRST` default **False**. This lane never sets True.

## C) Fill-quality / adverse-selection ledger (Scout #3)

Read-only. `record_fill` / `ingest_fills` take existing fill price + a later mid. Adverse drift: buy/yes → mid down; sell/no → mid up. EMA score per series and strategy. Persist `data/kalshi-fill-quality.json`.

**Flag:** `KALSHI_FILL_LEDGER_DEMOTE` default **False**. Persist always. Hard-skip only if env `=1` and `n≥5` and score `≥0.03`. This lane never sets True.

## Fence

- `KALSHI_FEE_MAP_GATE` stays False in committed config
- No `POLYMARKET_US_LIVE`, no CB/Alpaca writers, no weather/oil unfence
- No wallet/crypto live MM, no FinRL/hummingbot live
- No `VALEDIA_TRADING_HALT` bypass
- No crontab for this module

## Tests

```bash
python3 scripts/integrations/test_kalshi_profit.py
python3 scripts/integrations/test_kalshi_fee_map.py
python3 scripts/integrations/test_kalshi_orders_v2.py
python3 scripts/integrations/test_kill_switch.py
```

## Next

Burton: CONTROLS-READOUT / AUTONOMY-2 Jev optional / GATE flip still his. Do not auto-flip any of the new flags or `KALSHI_FEE_MAP_GATE`.
