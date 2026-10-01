# Ponytail audit — MIROFISH-100-E2E

**Mode:** `/ponytail` full on the overnight swarm path.
**Trees:** `~/mirofish-offline` (backend + `.env`), live `~/.openclaw/workspace/scripts` (cron), `workspace-v2` copies of the same helpers.
**Fence:** no venue writers, no `POLYMARKET_US_LIVE` / `WEATHER_LIVE` / HALT edits. `com.burton.ollama-mirofish` left running. Screens `ot-valedia-desk` / `valedia-glance` not killed.

## Applied

`reuse:` sync vs report was already two model names on one URL. Added `LLM_REPORT_BASE_URL` and pointed sync at Ollama `qwen3-8b-mf:latest` (`:11435`). Report stays Bionic `ornith-1.5-k-m`. **Shipped.** Full `MIROFISH_INFERENCE=ollama` and all-Bionic 27B remain one `valedia_models.sh` flip.

`yagni:` a json_schema mapper, a new ontology service, or a shorter system prompt. 8B finished a 7141-char seed in 99s. **Not shipped.**

`reuse:` CoinGecko error objects were iterated as strings (`'str' object has no attribute 'get'`), which dropped the crypto section. Two tries, then raise. **Shipped.** No new HTTP client.

`reuse:` graph poll that fell through into prepare. Return when the build never reaches `graph_completed`. **Shipped** on the cron runner.

`reuse:` report client timeout 600s. Ornith section calls died at that ceiling. `MIROFISH_REPORT_LLM_TIMEOUT=1800`. **Shipped.**

`reuse:` Bionic nomic was unloaded when Ornith loaded (`400 Model unloaded`). Embeddings moved to Ollama `nomic-embed-text` on `:11435` (768-d, 1.51s). **Shipped.**

`yagni:` raising persona parallelism or a new persona generator. Parallel 5 on one 8B blew the 45s prep cap and the existing rule-based fallback carried 19 agents. `MIROFISH_PREP_LLM_TIMEOUT=120` for the next backend start. **Timeout only.**

`yagni:` lengthening the 20s interview IPC wait. Report finished on graph tools after those timeouts. **Not shipped.**

## Findings

`shrink:` prep still fans 5 persona calls at one 8B. 120s should absorb the queue. If 03:00 still logs `using rule-based generation` on every row, drop `parallel_count` to 2. [oasis_profile_generator.py]

**net:** env split + one report URL + one timeout + a seed guard. No new service.
