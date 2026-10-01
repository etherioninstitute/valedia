# Ponytail + RuFlo rollup — 2026-09-23

**Scope:** Vaughan arb paper, eToro paper, CE ladder, OT INTEL (0.68.0). Report only. No drive-by edits.
**Screens left up:** `ot-valedia-desk` 783, `valedia-glance` 755, `valedia-grok` 98551.

## RuFlo health

Filtered MCP `:8810` answered. Catalog was not dumped (`search_tool` hid 109 tools; the ~357 stdio catalog stays disabled).

| Call | Result |
|---|---|
| `hooks_route` | keyword fallback, primary `grok-build`, no swarm |
| `memory_search` | HNSW + sql.js, patterns hit (CE-1, CE-3, Vaughan) |
| `memory_stats` | 189 entries, 144 embedded (76.2%). Stats string says sqlite brute-force cosine |
| `hooks_post-task` | `vaughan-subsume-2` stored, success |
| `swarm_status` | `swarm-1789281243984` terminated, 0 agents |

Server is up. No live swarm is running. That is not a fence failure.

## Fence proof

| Check | Result | Evidence |
|---|---|---|
| eToro write socket | PASS | `start_copy` / `create_order` / non-GET call `_refuse` and raise. No sender after `write_endpoint` |
| `ETORO_LIVE` | PASS | unset in this shell. Code stays off unless `ETORO_LIVE=1` and `ETORO_UNLOCK` |
| Vaughan `create_order` | PASS | `_row` sets false. Live GET of the cache: both rows false |
| OT INTEL writes | PASS | GET 200, POST 405. View `create_order`/`start_copy`/`live`/`wallet` false |
| Kelly ignores paper | PASS | sources contain `paper`. `forward_test_ledger._is_non_real_source` drops those |
| Oil / weather | PASS | `LIVE_EXEC_FENCED` = `OIL_BRACKET`,`WEATHER_ARB`. Series `KXWTI`,`KXOIL`. `WEATHER_LIVE` const False, env unset |
| Poly writer constant | PASS | `POLYMARKET_US_LIVE = False` in `valedia_config.py`. This Build did not assign it |
| Parent shell `POLYMARKET_US_LIVE` | NOTE | this shell inherited `1`. Not flipped here. Constant stays False |
| Parent shell `VALEDIA_JEV` / `KALSHI_FEE_MAP_GATE` | NOTE | inherited `1`. Constants stay False / gate-as-coded. Not flipped here |
| Screens | PASS | 783, 755, 98551 still detached. `:3001` still listening |
| Commit / tag | PASS | not done |

## Applied

`reuse:` one paper decision shape across crypto, Vaughan, and eToro. Kelly stays off because the source string contains `paper`.

`reuse:` BB-Terminal tally plus FOMO tag names on the desk. No OpenBB client.

`reuse:` existing JSONL files. INTEL only reads the tail.

## Skipped

`yagni:` eToro POST body, Ornith on the copy desk, OpenTerminal copy buttons, cron, a second fee table.

`yagni:` GoldQuant, HedgeFund, and vibe-trader as live brains.

`yagni:` restarting glance or killing the desk to force 0.68.0 onto `:8765`.

`delete:` nothing in this rollup. No fence hole required a patch.

## Findings

`shrink:` FOMO numbers live in `fomo_radar_enrich.py` and `web/lib/intelSignals.ts`. Change both, or leave them, until a third caller shows up.

`yagni:` `gate()` can return `write_endpoint` when live, unlock, and a non-paper mode are all set. `_refuse` still raises. Do not add the sender.

`shrink:` `test_openterminal_valedia.py` looks for `badge {source}` inside 500 characters. `INFORMED_BEFORE_CREATE` notes are longer, so that assert fails in a clean env. Pre-existing. Not a writer.

`native:` RuFlo swarm is already stopped. Do not boot one for a report.

## Punch list

1. Reload the OpenTerminal tab once so desk storage version 2 shows INTEL. Leave screen 783 running.
2. Leave glance 755 alone. Working tree is 0.68.0. The process on `:8765` was not reloaded.
3. Do not clear or set `POLYMARKET_US_LIVE`, `VALEDIA_JEV`, or `ETORO_LIVE` from this shell.
4. Keep oil and weather fenced. Do not unfence `KXWTI` / `KXOIL` / `WEATHER_ARB` / `OIL_BRACKET`.
5. If FOMO thresholds change, edit the Python module and `intelSignals.ts` in the same change.
6. When `test_openterminal_valedia.py` is next touched, widen the badge window or shorten the informed-before-create note.
7. No commit and no tag until Burton asks.

**net:** report only. Version `0.68.0` working tree, not tagged. Next = Burton. Stop.
