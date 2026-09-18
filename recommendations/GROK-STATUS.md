# Grok Build status

**Updated:** 2026-09-17 19:46 PT

**Mode:** RuFlo MCP required (`:8810` filtered).

**Current:** SHIPPED — Slice 14: Burton-gated spot GET (`SPOT_RECONCILE_HTTP` default False)

**Shipped:** Slices 1–14

**Next:** STOP for Burton. Do not flip `SPOT_RECONCILE_HTTP` / `REBALANCER_AUTOEXEC` / `VENUE_WRITER` / `--live`. No NT.

**Done this slice:**
- Valedia-runtime getter wiring complete: `reconcile_run.get_spot_order_getter()` → `spot_order_get.live_spot_order_getter` iff `spot_reconcile_http_enabled()`. Flag-off cron stays `mock_spot_order_getter` (no HTTP, even if a test transport is installed).
- Single flip (this lane never True): `export SPOT_RECONCILE_HTTP=1` **or** `valedia_config.SPOT_RECONCILE_HTTP = True`. Env wins. No extra `set_spot_order_getter` call.
- Tests inject mocked transport via `set_spot_http_transport` only. Any module inject blocks fall-through to live `get_order`. Default tests never hit network.
- Tests: `test_slice14.py` PASS (+ slices 7–13, `test_reconcile_run.py`). Drift check 10 PASS. No network.

**Fence:** No --live, no VENUE_WRITER flip, no REBALANCER_AUTOEXEC, no NT, no keys. `SPOT_RECONCILE_HTTP` default False.
