# Ponytail — MiroFish report model

**Mode:** full. Diagnose only. No MiroFish diff.

The split already routes reports through `MIROFISH_REPORT_MODEL` and `LLM_REPORT_BASE_URL`. No new client.

Skipped the flip. `qwen3.8-27b` is loaded and still failed a 16-token pong in 90s while Hermes was generating on both LMS models. Pid 91859 parents four sleeping sims. `valedia_models.sh mirofish-inference ollama` would load Ornith again on `:11435`, not replace it.

Add the one-line Qwen report id only after a solo pong and after those sims can exit. Leave Ornith loaded for Hermes.

**net:** 0 env writes, 0 restarts, 0 unloads. Keep (C).
