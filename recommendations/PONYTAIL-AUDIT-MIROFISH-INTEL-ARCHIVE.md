# Ponytail audit — MiroFish intel archive

**Mode:** `/ponytail` full. Swarm publish path only.
**Fence:** oil/weather stay `LIVE_EXEC_FENCED`. Screens `ot-valedia-desk` (783) and `valedia-glance` (755) not killed. Glance was not reloaded.

## Applied

`reuse:` `process_mirofish_report` is still the only PASS writer. It now calls `publish_pass` instead of `open(dest,"w")`. **Shipped.**

`reuse:` `intel_refresh` already skips a fresh `mirofish_swarm` file. **Not wrapped.**

`stdlib:` `tempfile.mkstemp` + `os.replace` for the archive and the live file. No new dependency.

## Skipped

`yagni:` a second intel store, a database, or a retention daemon. One dated JSON plus a run-id sibling, pruned by filename on the next PASS. **Not built.**

`yagni:` rewriting the current live file (bias 51.5) just to stamp fields. Readers derive `as_of` from `ts`. Near-neutral already fails `usable_for_fv`. **Not rewritten.**

`yagni:` a partial git commit. The branch still carries earlier uncommitted slices. **Not committed.**

## Findings

`shrink:` one module (`mirofish_intel_archive.py`) and `load_for_fv` at the five readers (gatherer, quant scan, brain fusion, intel layer, sports bias). A per-venue freshness class would repeat the same three booleans.

`shrink:` neutral band is `|bias-50| <= 5` and the age window is 24h (`ponytail:` on both). Widen the band or the window if a scored edge needs a 51.5 print to size.

**net:** archive then atomic replace on PASS; sidecar stale marker on FAIL; near-neutral and archives are not create fuel. Version `0.65.0` working tree, not tagged.
