# Ponytail audit — MiroFish Bionic E2E

**Mode:** `/ponytail` full on the ontology path only.
**Tree:** `~/mirofish-offline/backend`
**Scope:** `llm_client.py`, `ontology_generator.py`, `ner_extractor.py`.
**Fence:** `MIROFISH_INFERENCE=bionic` held. No Ollama flip. No venue writers.

## Applied this session

`yagni:` mapping `json_object` → `json_schema`. Drop the field; parse JSON from content. Parent already shipped `normalize_response_format`. **Kept.**

`yagni:` 4096-token ontology on 27B (20min hang). Cap Bionic at 1500, timeout 900, `max_retries=0`. **Shipped.** Same cap on NER.

`yagni:` replacing Neo4j 5.18.1 (auth-matched, bolt up) with a fresh 5.15 volume. **Not shipped.**

`yagni:` intel_refresh overwrite of `mirofish_signal.json`. Empty-graph prepare is not a real swarm. **Not shipped.**

## Findings

`shrink:` first smoke seed was 245 chars → NER 0 nodes. Richer seed is the next swarm attempt, not this slice.

# ponytail: ontology system prompt is huge; shortening it is the real 27B speedup if timeouts return.

**net:** 3 files in mirofish-offline, 0 new services, Colima+existing neo4j container. Lean enough.
