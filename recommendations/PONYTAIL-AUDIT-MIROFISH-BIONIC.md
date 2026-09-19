# Ponytail audit — MIROFISH-BIONIC (0.44.0)

**Mode:** `/ponytail` full on the MIROFISH-BIONIC diff only.
**Fence:** no POLYMARKET_US_LIVE, no VENUE_WRITER restore, no ollama-mirofish stop, no model deletes, no live orders.

## Applied

`yagni:` new HTTP client / SDK. `embedding_service` still `requests.post`; OpenAI vs Ollama is URL + JSON parse.

`yagni:` auto-swap LMS models or a small/fast GGUF. Sync stays spec id `qwen3.8-27b-obliterated-k`. Flip back is env/`mirofish-inference ollama`.

`yagni:` wholesale live `valedia_config.py`. Helpers rsync only; live writers untouched.

## Findings

`shrink:` LMS did not keep nomic-embed listed `loaded` next to 27B (embed after chat **46.7s**). Add a pinned dual-load / GPU steward note if overnight swarm interleaves embed+chat and that 46s repeats.

`yagni:` `llm_client._is_ollama` still matches `127.0.0.1` so extra_body may hit Bionic. Smoke `reasoning_effort=none` returned `ok`. Tighten to `:11434`/`:11435` if LMS ever 400s.

**net:** one embed path + healthcheck dual probe + env flip. 0 deps. 0 live flag flips. Lean enough. Ship.
