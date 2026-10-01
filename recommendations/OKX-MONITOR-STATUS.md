# OKX-MONITOR-STATUS

**Updated:** 2026-09-27
**Slice:** OKX portfolio_monitor hook
**GROK_EXIT:** 0

`check_okx` calls `OKXClient().portfolio_summary()` and `main()` appends it after Kraken. Dual-home `scripts/integrations/portfolio_monitor.py`. Exception returns `OKX: [API ERROR] ...` and does not stop the other lines.

Proof from workspace-v2, exit 0, `2026-09-28 05:29 UTC`. Exit IP `85.239.234.32`. Trading USD eq=60 avail=60 nonzero. Funding empty. Open orders 0. `key_hits` 0. `OKX_LIVE` unset.

No order. No TradingView. No Davidd unlock.
