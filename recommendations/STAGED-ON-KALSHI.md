# Staged ON — Kalshi-only (2026-09-19 PT)

**Burton go:** Kalshi-only ON while OpenTerminal-Valedia UI builds.

## Live now
- Kalshi writer: `valedia` (allocator + sports already `[LIVE]`). Create/sell/cancel = Order V2 (`0.43.0`; no V1 POST `/portfolio/orders`).
- Kill-switch: `VALEDIA_TRADING_HALT` wired on live; **halt OFF**
- Coinbase / Alpaca writers: `none` + allocator `SKIP_WRITER` (Kalshi-only)
- Polymarket / HL / Kraken: still `none`
- Weather / oil: still `LIVE_EXEC_FENCED` (no live orders)
- MiroFish / intel: keep gathering (ears always on; halt does not stop data)

## Capital reality (blocker)
SCAN-ACCURACY 0.42.0: deployable is **per-venue**. Kalshi cash+positions fuse; CB/Alpaca writer `none` must not zero Kalshi deployable. Older mixed-book formula (Alpaca equity in `deployed`, not in `total`) could print **$0** with Kalshi cash on hand. New trades still need Kalshi cash above the fuse after open positions.

## Brake
```bash
python3 ~/.openclaw/workspace/scripts/integrations/valedia_trading_halt.py on "reason"
python3 ~/.openclaw/workspace/scripts/integrations/valedia_trading_halt.py off
```

## Still fenced
No POLYMARKET_US_LIVE, no treasury auto, no REBALANCER_AUTOEXEC, no SELF_ADJUST auto-raise, no ACH/wire from this lane.
