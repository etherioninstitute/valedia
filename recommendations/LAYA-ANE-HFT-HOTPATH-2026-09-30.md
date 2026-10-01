# LAYA-ANE-HFT-HOTPATH — 2026-09-30

**When:** 2026-09-30 ~01:05 PT  
**Mode:** PAPER/SHADOW. HFT-shaped ANE hot path. Measured decide time is seconds.  
**Service:** `127.0.0.1:8766` pid 25133 after the fail-open restart. `8765` had no listener.  
**Pack:** `~/MemPalace/sources/tools/laya-decide-service/packs/valedia-paper-v1.json`  
**Client:** `scripts/integrations/laya_hotpath_client.py`  
**Scan:** `scripts/integrations/all_knowing_movers_scan.py`

## Architecture

Tape (CoinGecko, else Binance public 24h) → Yahoo/Finnhub ears → fee-true edge on the $504 book → code label (`would_buy` / `watch` / `would_pass`) → optional Laya shadow decide → paper would-have row.

Code owns the label and the size. Laya stamps `admit_paper`, `edge_quality`, and `venue_class`. A Laya answer does not size, veto, or send. Oil stays `NO_HARVEST`. Weather stays watch. `create_order` stays false.

MiroFish is a local file read before the decide loop. This tape's artifact `models/mirofish_signal.json` (1443 bytes, 3 names) overlapped **0** of the 37 scanned symbols, so those rows carried tape-only flags. No LLM call sits inside the decide loop.

## Enable

Default **off**. Unit tests and a bare scan stay on the fee-true stub.

```bash
# one command — paper sniper
VALEDIA_LAYA=1 /usr/local/bin/python3.13 scripts/integrations/all_knowing_movers_scan.py
```

Service, if it is down:

```bash
~/MemPalace/sources/tools/laya-decide-service/scripts/start.sh
```

`LAYA_DECIDE_MODE=shadow`. `LAYA_DECIDE_ENFORCEMENT=none`. Bind `127.0.0.1:8766`.

## Health

`GET http://127.0.0.1:8766/health` after restart:

| field | value |
|---|---|
| ok | true |
| mode | shadow |
| enforcement | none |
| model_present | true |
| agent_loaded | true |
| model_id | `aac6fef/laya-multilingual-coreml` |
| cache path | `/Volumes/NIMROD_SSD/models/laya-coreml/cache` (directory exists, 0 files; the loaded agent is what served the decides) |

Clean scan health line: `laya.health ok=True mode=shadow enforcement=none model_present=True agent_loaded=True`.

## Live proof — 2026-09-30T08:02:41Z

Book **$504**. Clip **$50.40**. Roundtrip **30 bps**. Size bar **16%**. Origin **coingecko**. `laya.backend=coreml_http`. `fail_open=false`. **37/37** HTTP. Decide latency **1881–7320 ms**. `create_order` count **0**.

| symbol | class | chg% | edge | impact$ | code label | sized | admit_paper | edge_quality | venue_class | latency ms |
|---|---|---:|---:|---:|---|---|---:|---:|---|---:|
| NEAR | crypto | +6.461 | 0.06161 | 3.1051 | watch | n | 0.3750 | 1.5134 | crypto | 2412.57 |
| GLD | etf | +1.320 | 0.01020 | 0.5141 | watch | n | 0.9484 | 1.3306 | equity | 2227.97 |
| KXHIGHNY | weather | — | — | — | watch | n | 0.9879 | 1.5792 | crypto | 2210.14 |

No live name cleared 16%, so the live tape sized nobody. PUMP absent. QNT absent. No oil row on this tape. ALLOWLIST still `BTC-USDT`, `ETH-USDT`. `CRYPTO_PAPER_SAFETY=PAPER/SHADOW+ORNITH`. `MIROFISH_AUTO_RAISE` false.

KXHIGHNY's model `venue_class` came back `crypto`. The code class stayed `weather`, label `watch`, unsized.

## Fixture proof — same service, real HTTP

`data/all-knowing/fixture_movers.json`. `n_ok=7`. `laya.backend=coreml_http`.

| symbol | class | code label | sized | create_order | admit_paper | venue_class | latency ms | note |
|---|---|---|---|---|---:|---|---:|---|
| USO | oil | watch | n | false | 0.8706 | crypto | 2036.23 | `NO_HARVEST`. Model admit ignored. |
| PUMP | crypto | would_buy | Y | false | 0.2743 | crypto | 1998.58 | qty 12600. Code sized. Model admit 0.27 did not veto. |
| QNT | crypto | would_buy | Y | false | 0.9909 | crypto | 2051.77 | qty 0.51167513 |
| SPY | etf | watch | n | false | 0.7840 | equity | 2020.23 | under the 16% bar |
| KXHIGHNY | weather | watch | n | false | 0.9879 | crypto | 1956.57 | watch |

## Fail-open

`stop.sh` stopped pid 14223 (laya_decide http only). `VALEDIA_LAYA=1` fixture scan then printed `laya.backend=stub fail_open=True`. PUMP stayed `would_buy` and sized, `create_order=false`. USO stayed watch. Health on the dead port was `ok=False`.

`start.sh` brought pid **25133** back on `127.0.0.1:8766`. Health ok, mode shadow, enforcement none, agent loaded. `8765` still empty.

## Ornith-1.5-35b-a3b — GO / NO-GO

Read-only `lms ps` and `GET http://127.0.0.1:1234/api/v0/models`. No load. No unload.

| model | state | size | ctx | parallel | device |
|---|---|---:|---:|---:|---|
| ornith-1.5-35b-a3b | loaded, GENERATING | 22.62 GB | 131072 | 4 | Local |
| qwen3.8-27b | not-loaded | — | — | — | — |

**NO-GO** for the money path, for replacing the qwen3.8-27b desk context, and for any LMS swap.

**GO** for seasoning only: paper ticks and a light Hermes helper, on the model that is already resident. 22.62 GB at context 131072 does not shorten this hot path. Laya decides on this tape were **1.9–7.3 s**. That is the ANE shadow stamp, measured, and it is the wrong place to advertise exchange latency.

## Fences held

`create_order=false`. `live=false`. `enforcement=none`. No `--live`. No venue order. ALLOWLIST unchanged. Oil `NO_HARVEST`. Weather watch. No glance bind. Screens `ot-valedia-desk` and `valedia-glance` were not in this screen socket and were not signaled. `valedia-grok` (98502) left detached. LMS Ornith left loaded.

## Tests

`/usr/local/bin/python3.13 scripts/integrations/test_laya_hotpath_client.py` PASS.  
`/usr/local/bin/python3.13 scripts/integrations/test_all_knowing_movers_scan.py` PASS.

## Tools

RuFlo `:8810` health `status=ok`. `hooks_route` grok-build confidence 0.5. `hooks_pre-task` grok-build 0.8, single agent. `memory_search` patterns empty, then stored `patterns/laya-ane-hft-paper`. CBM `search_graph` sees `rank_movers`, `fee_true_edge`, `laya_shadow_decide` (index `2026-09-30T07:49:15Z`). Coverage: movers scan `metadata_changed`, `laya_hotpath_client.py` `not_tracked`. Source read. No reindex.

**GROK_EXIT:0**
