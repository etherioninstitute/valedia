# Grok Build status

**Updated:** 2026-09-18 PT

**Mode:** RuFlo MCP (`:8810` filtered) + Ponytail. Hard fence held.

**Version:** 0.23.0  
**Tag:** `valedia-v0.23.0` (Build, annotated)  
**Tip:** `0a94a5b` (`valedia-v2-grok-build`; tag `valedia-v0.23.0` on Build tip)

**Current:** Slices 22 + 23 SHIPPED. **Next = STOP for Burton.**

**Slice 23:** `VALEDIA_VERSION` in `valedia_config.py`. `scripts/valedia_version.sh` prints version+sha+branch+dirty. Status/Signal headers stamp `version_stamp()`. Annotated tag on Build tip. Live tag only after fenced deploy (do not apply this slice).

**Slice 22:** FTL `Profit:` from fee-true ledger (sports JSON view-only; UNRECONCILED cannot be realized P&L). Allocator Coinbase/Alpaca `UNRECONCILED` unless `extract_spot_fill.complete`; missing `place_market_order` stays FAILED. Alpaca banner = env `ALPACA_PAPER`. latency_arb POST-ack → UNRECONCILED; `arb_run` dry-run. Crontab stagger **patch only** — not installed. ERE still `FAIL_CLOSED`. Tests: `test_slice22.py` + `test_slice23.py` PASS.

**Propose (do not apply):** fenced rsync of v2 scripts → live `~/.openclaw/workspace` (messaging only). No crontab rewrite unless Burton pastes `CRONTAB-STAGGER-S22.patch`. No `install_cron.sh`. No flag flips. No live tag until that deploy.

**Fence:** No --live, no VENUE_WRITER, no REBALANCER_AUTOEXEC, no NT live, SPOT_RECONCILE_HTTP default False. Do **not** run `install_cron.sh`.
