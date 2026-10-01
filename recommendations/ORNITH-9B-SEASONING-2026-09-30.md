# ORNITH-9B-SEASONING — 2026-09-30

**Updated:** 2026-09-30 05:30 PT  
**Charter:** `recommendations/GROK-CHARTER-ORNITH-9B-SEASONING-2026-09-30.md`  
**GROK_EXIT:0**

Optional paper-tick seasoning. Same job as the Jev stamp on the movers scan: a note beside a rank that is already fixed. `changes_rank` is forced false after the helper returns. The note is not an input to `rank_movers`.

## Wire

| Piece | Value |
|---|---|
| Client | `scripts/integrations/ornith_seasoning.py` |
| Hook | `all_knowing_movers_scan.build` → `ornith_seasoning.stamp` |
| Flag | `VALEDIA_ORNITH_SEASON=1` (default off, same shape as `VALEDIA_LAYA`) |
| Endpoint | `http://127.0.0.1:1234` model `ornith-1.5-9b` |
| Probe | `GET /api/v0/models` |
| Chat | `POST /v1/chat/completions` only when that id is already `loaded` / `idle` / `busy` |
| Unloaded | `reason=not_loaded`, `fail_open=true`, print-only load hint |
| Timeout / probe error | `fail_open=true`, rank left as ranked |

Print-only hint:

```
lms load ornith-1.5-9b -c 8192 --parallel 1 --gpu max --yes
```

`ORNITH_LOAD_APPLY` is not read here. A load of 9B while 35B is resident can evict the 35B. The desk load path stays `ornith_load_policy.py`.

## Proof

Python 3.13.5. `test_ornith_seasoning.py` and `test_all_knowing_movers_scan.py` PASS. Mocked LMS covers off, 35B-loaded-but-9B-absent, not-loaded (chat not called), loaded note, fenced JSON, probe timeout, chat timeout, empty content, and a raising season callable. `VALEDIA_ORNITH_SEASON_LIVE=1` re-read LMS before and after `stamp([])`.

LMS, read-only, 05:30 PT, before and after the live probe and the flag-on fixture scan:

| id | state | context |
|---|---|---|
| `ornith-1.5-35b-a3b` | loaded | 131072 |
| `ornith-1.5-9b` | not-loaded | — |
| `qwen3.8-27b` | not-loaded | — |

Flag-on fixture (`data/all-knowing/fixture_movers.json`, written under `/tmp` and removed):

- `status` PAPER/SHADOW, bankroll $504, clip 10% ($50.40), bar 16%
- `ornith.reason=not_loaded`, `seasoning_only=true`, `changes_rank=false`, `note=null`
- USO +22% edge 0.2170 impact $10.9368 `NO_HARVEST` unsized
- PUMP +16.40% and QNT +16.10% `would_buy`, `create_order=false`
- `fences.create_order=false`, `live=false`, `allowlist_widened=false`, `mirofish_auto_raise=false`, `oil=NO_HARVEST`, `weather=WATCH`, `counts_toward_kelly=false`
- ALLOWLIST `BTC-USDT`, `ETH-USDT`

Default (flag unset) table line: `ornith enabled=False fail_open=True reason=VALEDIA_ORNITH_SEASON_off changes_rank=false`. No load hint on that path.

Canonical `data/all-knowing/movers_scan.json` was not rewritten.

## Tools

- RuFlo `hooks_pre-task` grok-build 0.75. `hooks_route` devops-task 0.4. `hooks_pre-edit` on the client and the scan. `memory_store` `patterns/ornith-9b-seasoning-paper`. `hooks_post-task` quality 0.92.
- CBM project `Users-burtonchatelain-.openclaw-workspace-v2`. `search_graph` sees `rank_movers`, `season_jev`, and `ornith_seasoning.stamp` / `annotate` / `model_loaded`. `check_index_coverage` `metadata_match`, `no_recorded_issue`, index `2026-09-30T12:31:38Z`. Best-effort.

## Fence

No `--live`. No venue order. No `create_order`. No LMS unload or reload. Desk Hermes model untouched. Oil `NO_HARVEST`. Weather watch. ALLOWLIST unchanged. `MIROFISH_AUTO_RAISE` false. Screens `ot-valedia-desk` and `valedia-glance` not touched.

## Ponytail

One GET, then one short chat, and only when 9B is already loaded. No `valedia_config` row (version stays `0.74.1`). Add that row when the controls snapshot needs the flag. Execute a load only when 9B is the resident model and the load cannot evict 35B.

**GROK_EXIT:0**
