# NIMBLE-SYSTEMONE-REPLACE-LAYA

**When:** 2026-09-30 ~17:58 PT  
**Charter:** `recommendations/GROK-CHARTER-OLLAMA-SYSTEMONE-VS-LAYA-2026-09-30.md`  
**Addendum:** Burton YES execute-replace ~17:05 PT  
**Verdict:** **REPLACE** executed. Nimble 9B on Ollama `POST /v1/systemone` is the local paper shadow decide. Laya HTTP `:8766` is stopped. `VALEDIA_LAYA` does not enable the movers scan.  
**GROK_EXIT:0**

## What shipped

- Desktop Ollama on `127.0.0.1:11434` is **0.35.0**. Mirofish `com.burton.ollama-mirofish` pid **944** stayed on `:11435` at API **0.33.3**. It was not restarted.
- `nimble:latest` id `24e550a16a70` is pulled. At teardown `ollama ps` showed **9.0 GB, 100% GPU, context 8194**, keep-alive refreshed. Runner: `llama-server -c 8194`. This is the live context, not the model-card 256K figure and not the blog's 91 ms Pac-Man number (M5 Max).
- Client `scripts/integrations/systemone_hotpath_client.py`. Opt-in `VALEDIA_SYSTEMONE=1`. Pin `{nimble, nimble:9b, nimble:latest}`. A pin containing `tev` or `0.8` is refused and is not posted. Loopback allowlist is `/v1/systemone`, `/api/version`, `/api/tags` on `:11434` only.
- Movers scan attaches `systemone_shadow` only when that env is set. Fee-true label field is still named `laya` (`watch` / `would_buy` / `would_pass`). The model does not size and does not veto. `create_order` stays false. Default off, so a bare scan does not load 9 GB next to resident Ornith.
- Jev stays optional cloud seasoning. It was not turned on for these proofs (`OPENCLAW_SKIP_ENV=1`). Tev1-0.8B and Tev1-4B were not pulled.

## Sources (cited, not re-scored on this tape)

- Ollama blog, 2026-09-29: https://ollama.com/blog/ollama-now-supports-jev-style-decision-models — `/v1/systemone` from 0.35; Nimble 9B **91 ms/decision** on an M5 Max Pac-Man example. Not this Mac.
- Ollama library card: https://ollama.com/library/nimble — Ollama mean on Bespoke's 13 public sets, 3,880 decisions: Nimble 9B **75.7%**, Tev1 4B **73.3%**, Tev1 0.8B **63.5%**, Jev 1.13 **76.0%**. Held-out 324: Nimble **292/324 (90.12%)**, Jev **302/324 (93.21%)**.
- Bespoke `docs/PUBLIC_BENCHMARKS.md`: macro **74.8%** / micro **75.9%** for Nimble vs Jev macro **76.0%** / micro **77.3%**. Nimble leads the score subsets; Jev leads noul. https://github.com/bespokelabsai/nimble/blob/main/docs/PUBLIC_BENCHMARKS.md
- API shape: https://docs.ollama.com/capabilities/decision — state + named questions; choice / noul / score; strict JSON (no `NaN`).

No accuracy claim on the Valedia tape. Those public numbers are other people's labels.

## Comparison

| | Laya CoreML | Nimble System One | Jev | Tev1 |
|---|---|---|---|---|
| This run | HTTP stopped | local shadow, opt-in | left optional, not called | not pulled; client refuses `tev` and `0.8` |
| Bind | was `127.0.0.1:8766` ANE | `127.0.0.1:11434` Metal | cloud | — |
| Footprint | prior ANE service | 9.0 GB Q8_0, 100% GPU, ctx 8194 | API | — |
| Latency here | prior stamp **1881–7320 ms** (`LAYA-ANE-HFT-HOTPATH-2026-09-30.md`) | warm fixture **3132–3832 ms**; live **3741–18533 ms**; null-print ETF retry **10407–15739 ms**; cold load **75091 ms** | not measured | — |
| Authority | none | none. Code owns label and size | seasoning only | not on this path |

Warm three-question decides overlap Laya's band and beat Laya's 7.3 s worst. They do not beat Laya's 1.9 s best, and they are not sub-second on this M1 Ultra. Qwen3.5 hybrid reprocesses the prompt. Replace stands because Burton approved the local typed path and the proofs below held, not because this box got faster.

## Proof

Health on the ETF retry: `ok=true version=0.35.0 model_present=true model=nimble mode=shadow enforcement=none`. Same health line on the earlier fixture and live scans.

| Run | File | Result |
|---|---|---|
| Cold fixture | `/tmp/nimble-fixture-scan.json` | 7/7 `systemone_http`. Latency **14082.35–75090.66 ms** (PUMP first, model load). `create_order` 0. No `laya_shadow`. |
| Warm fixture | `/tmp/nimble-fixture-warm.json` ts `2026-10-01T00:43:26Z` | 7/7. **3132.3–3832.35 ms**. USO watch `NO_HARVEST` venue `oil_blocked` (admit 0.422). PUMP/QNT `would_buy` sized, `create_order` false, venue `crypto`. KXHIGHNY watch venue `weather_watch` admit 0.0063. SPY +0.4 watch venue `equity`. |
| Live tape | `/tmp/nimble-live-scan.json` ts `2026-10-01T00:51:38Z` | origin live, n 38. CoinGecko 429, tape `binance_public_24h`. **32/38** `systemone_http`, **3741.32–18532.71 ms**. `create_order` 0. Sized paper only: ANT +125.534, CUDOS +19.947, POLY +16.622. PUMP +0.461 and QNT +10.199 watch, unsized. KXHIGHNY watch. `fences.live` false, oil `NO_HARVEST`, weather `WATCH`, allowlist `BTC-USDT` `ETH-USDT`, `allowlist_widened` false. Canonical `data/all-knowing/movers_scan.json` not rewritten. |
| NaN retry | `/tmp/nimble-etf-nan-retry.json` | First live pass fail-opened SPY QQQ DIA IWM GLD TLT: Yahoo left `NaN`, `json.dumps` emitted `NaN`, Ollama **400** `invalid character 'N'`. Code label stayed watch. Fix: non-finite chg/edge/impact become null before the fee label and before `json.dumps(..., allow_nan=False)`. Retry **6/6** `systemone_http`, `chg_24h` null, label watch, sized false, `create_order` false, venue `equity`. Latency **10406.67–15739.46 ms**. High admit (~0.96–0.99) did not size. |
| Laya ignored | `/tmp/laya-retired-scan.json` | After `:8766` was down, `VALEDIA_LAYA=1` and System One off. `laya.backend=retired`. `systemone.backend=stub`. Port stayed empty. USO `NO_HARVEST`. PUMP/QNT `would_buy` `create_order=false`. |

Tests after the NaN coercion, Python 3.13: `test_systemone_hotpath_client.py`, `test_laya_hotpath_client.py`, `test_all_knowing_movers_scan.py` PASS. `py_compile` on the three modules.

## Teardown

`~/MemPalace/sources/tools/laya-decide-service/scripts/stop.sh` stopped **pid 25133** (`laya_decide http`) at 17:58:28 PT. `:8766` empty. Glance `:8765` empty.

Left running on purpose:

- `com.burton.laya-decide` stays loaded. `KeepAlive` false, `RunAtLoad` false. Not booted out. It does not respawn HTTP.
- `laya_decide mcp` processes stay. They are Claude MCP sessions, not the paper listener. `stop.sh` refuses them.
- Signal **908**, life gateway **30269** / python **34663**, mirofish ollama **944**, CTO **76349**, auditor **76348**, scout **76364**, decider **76365**.
- Desktop `ollama serve` **62765**. LMS `ornith-1.5-35b-a3b-mlx` still **loaded**. No `ollama stop`. No trading LaunchAgent bounce. No `--live`.

## Fence

Paper/shadow only. `enforcement=none`. `judge=dont_trade`. `create_order=false`. Oil `NO_HARVEST`. Weather watch. ALLOWLIST unchanged. Ollama is not the desk chat model. Do not point this client at `:11435`, `:1234`, `:8765`, or `:8766`. Standing note: `recommendations/VALEDIA-SYSTEMONE-FENCED.md`. Laya record: `recommendations/VALEDIA-LAYA-FENCED.md` (retired).

## Proof commands

```
OLLAMA_HOST=127.0.0.1:11434 /usr/local/bin/ollama --version
curl -sS http://127.0.0.1:11434/api/version
curl -sS http://127.0.0.1:11435/api/version
OLLAMA_HOST=127.0.0.1:11434 /usr/local/bin/ollama ps
cd /Users/burtonchatelain/.openclaw/workspace-v2
/usr/local/bin/python3.13 scripts/integrations/test_systemone_hotpath_client.py
/usr/local/bin/python3.13 scripts/integrations/test_laya_hotpath_client.py
/usr/local/bin/python3.13 scripts/integrations/test_all_knowing_movers_scan.py
PYTHONUNBUFFERED=1 VALEDIA_SYSTEMONE=1 OPENCLAW_SKIP_ENV=1 \
  /usr/local/bin/python3.13 scripts/integrations/all_knowing_movers_scan.py \
  --fixture data/all-knowing/fixture_movers.json \
  --out /tmp/nimble-fixture-warm.json --watch /tmp/nimble-fixture-warm-watch.json
PYTHONUNBUFFERED=1 /usr/local/bin/python3.13 /tmp/nimble-etf-nan-retry.py
bash ~/MemPalace/sources/tools/laya-decide-service/scripts/stop.sh
PYTHONUNBUFFERED=1 VALEDIA_LAYA=1 OPENCLAW_SKIP_ENV=1 \
  /usr/local/bin/python3.13 scripts/integrations/all_knowing_movers_scan.py \
  --fixture data/all-knowing/fixture_movers.json \
  --out /tmp/laya-retired-scan.json --watch /tmp/laya-retired-watch.json
```

**GROK_EXIT:0**
