# Ponytail audit — LAYA-ANE-HFT

**Mode:** `/ponytail` full. Paper/shadow only.
**Fence:** `create_order` false. `enforcement=none`. Oil `NO_HARVEST`. Port 8766. Screens `ot-valedia-desk` / `valedia-glance` not touched.

## Applied

`reuse:` `laya_shadow_decide` stays the fee-true label. `would_have` / `create_order=false` unchanged. Jev fail-open shape (stdlib `urllib`, stub on error, bad `enforcement` → stub).

`reuse:` existing movers scan CLI. `VALEDIA_LAYA=1` is the switch. No new daemon.

## Skipped

`yagni:` letting Laya answers size or veto. The model called USO admit 0.87 and PUMP admit 0.27. Code kept oil unsized and PUMP a paper would-buy.

`yagni:` a batch decide API, a second port, and `laya-coreml` inside the Valedia interpreter.

`yagni:` an LMS load or unload. Ornith was already resident. qwen3.8-27b stayed unloaded.

`yagni:` a repo reindex. CBM index `2026-09-30T07:49:15Z` has `rank_movers` at `metadata_changed`. `laya_hotpath_client.py` is `not_tracked`. Source was read and tested.

`yagni:` git commit. **Not committed.**

## Findings

`shrink:` one client, one pack JSON, two test modules. Scan gained a flag, a shadow stamp, and a three-row proof line.

`ponytail:` client timeout is 20s. ANE predict on this tape was 1.9–7.3 s. Raise the cap only if receipts show client timeouts.

**net:** default scan is still the stub. `VALEDIA_LAYA=1` attaches the shadow stamp and keeps the fee-true size gate.
