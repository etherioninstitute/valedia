# Ornith Q5 scrubbed (2026-09-23 PT)

**Machine:** Burtons-Mac-Studio-M1 (`b3f456da-e4fc-42d6-8147-a47ad76a8982`)  
**Goal:** stop `:1234` JIT-loading Q5; keep Valedia/LiteLLM `:4000` alias `ornith-1.5-k-m` on Q4.

## LMS catalog (local M1)

- **No local Q5 in LMS library or `model-index-cache.json`.** Active local Ornith is Q4 only:
  - key: `ornith-1.5-35b-a3b` → `ornith-ai/Ornith-1.5-35B-A3B-GGUF/Ornith-1.5-35B-Q4_K_M.gguf`
- Legacy id `ornith-1.5-k-m` is **not** a local LMS model key (gone from active `~/.lmstudio/models`).
- **`lms` has no remove/unregister CLI.** Local scrub = delete/quarantine weight files + confirm index has no Q5 (done).
- Old bak import dir moved into quarantine: `models.bak.../local/ornith-1.5-q5-k-m`.

## LM Link

- Set preferred device → **M1-Mac-Studio** `04c18a5520ad5e22af9405cd77bb2105`.
- **Leftover:** Mac-Studio-M2 (`b5d4104b…`) still advertises `ornith-1.5-35b-a3b@q5_k_m` over Link (M2 agent/SSH not reachable from this scrub). Explicit `@q5_k_m` requests could still resolve to M2 until M2’s LMS Q5 is deleted or Link is disconnected.

## Paths quarantined (1-day hold on NIMROD)

Dir: `/Volumes/NIMROD_SSD/_quarantine/ornith-q5-2026-09-23/`  
(safe to `rm -rf` after **2026-09-24** if Valedia Q4 path still good)

| Item | Notes |
|------|--------|
| `Ornith-1.5-Q5_K_M.gguf` | was `/Volumes/NIMROD_SSD/models/gguf/ornith-1.5/Ornith-1.5-Q5_K_M.gguf` (24G) |
| `ollama-sha256-08afe748…` | **hardlink of same inode**; Ollama tags that shared it: `ornith-1.5`, `ornith-1.5-mf`, `ornith-1.5-deep`, `ornith-ugft`, `ornith-ugft-deep` |
| `hf-blob-2ac3a459…` | separate 24G HF hub blob |
| removed snapshot symlink | `.../snapshots/.../Ornith-1.5-35B-Q5_K_M.gguf` |
| `bak-lms-local/ornith-1.5-q5-k-m/` | old LMS bak symlink dir |

## NOT touched (as required)

- mmproj (`Ornith-1.5-mmproj.gguf`, `mmproj-Ornith-1.5-35B-BF16.gguf`)
- Q4: `/Volumes/NIMROD_SSD/models/lms-library/ornith-ai/Ornith-1.5-35B-A3B-GGUF/Ornith-1.5-35B-Q4_K_M.gguf`
- LiteLLM aliases (`~/.litellm/config.yaml` already → `openai/ornith-1.5-35b-a3b@q4_k_m`)
- Trading / fences / wallets / Poly / Kalshi
- HF BF16 blob; Ollama manifests left in place (blobs moved → those tags broken until restore)

## Proof (PT 2026-09-23 ~15:10)

### LiteLLM `:4000` → Q4 fingerprint

```
POST /v1/chat/completions model=ornith-1.5-k-m
HTTP 200
model: ornith-1.5-k-m
system_fingerprint: ornith-1.5-35b-a3b@q4_k_m
```

Also `ornith-1.5` → `system_fingerprint: ornith-1.5-35b-a3b@q4_k_m`.

### Direct LMS `:1234`

```
POST model=ornith-1.5-k-m
HTTP 200
model: ornith-1.5-35b-a3b@q4_k_m
system_fingerprint: ornith-1.5-35b-a3b@q4_k_m
```

`lms ps`: only `ornith-1.5-35b-a3b@q4_k_m` loaded (Local, 22.62 GB).  
No live `*Q5*` Ornith files under LMS library / gguf / HF hub / ollama blobs (outside quarantine).

## Leftover references / caveats

1. **M2 Link catalog** still lists `ornith-1.5-35b-a3b@q5_k_m` on `:1234` `/v1/models` — scrub M2 LMS library when that machine is available.
2. **Ollama** ornith-* tags above are broken (shared Q5 blob quarantined). Re-point to Q4 or restore quarantine hardlink if Ollama Ornith is needed.
3. Modelfile only (not weights): `/Volumes/NIMROD_SSD/ugft-agent/Ornith-1.5-Q5-256k.Modelfile`
4. Docs still mention Q5 lanes: `VALEDIA-LOCAL-MODEL-SWITCH.md` (historical); `lms-thin-load.sh` already defaults to Q4 key.
5. Quarantine holds ~48G (hardlinked pair counts once on disk + separate HF 24G ≈ **~48G** reclaimable after permanent delete).
