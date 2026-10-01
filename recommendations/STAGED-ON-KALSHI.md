# Staged ON — Kalshi-only (2026-09-19 PT)

**Burton go:** Kalshi-only ON while OpenTerminal-Valedia UI builds.

## Live now
- Kalshi writer: `valedia` (allocator + sports already `[LIVE]`)
- Kill-switch: `VALEDIA_TRADING_HALT` wired on live; **halt OFF**
- Coinbase / Alpaca writers: `none` + allocator `SKIP_WRITER` (Kalshi-only)
- Polymarket / HL / Kraken: still `none`
- Weather / oil: still `LIVE_EXEC_FENCED` (no live orders)
- MiroFish / intel: keep gathering (ears always on; halt does not stop data)

## Capital reality (blocker)
Last allocator (2026-09-19 07:20 UTC): Kalshi cash **$5**, deployable **$0** (cash tied in open positions, mostly CPI Nos). New trades need more Kalshi cash or positions to resolve/cash out.

## Brake
```bash
python3 ~/.openclaw/workspace/scripts/integrations/valedia_trading_halt.py on "reason"
python3 ~/.openclaw/workspace/scripts/integrations/valedia_trading_halt.py off
```

## Still fenced
No POLYMARKET_US_LIVE, no treasury auto, no REBALANCER_AUTOEXEC, no SELF_ADJUST auto-raise, no ACH/wire from this lane.
