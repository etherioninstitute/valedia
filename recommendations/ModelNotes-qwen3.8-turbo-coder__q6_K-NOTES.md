# qwen3.8-turbo-coder:q6_K

DavidAU **TURBO** Fable + Cold-Fusion heretic/uncensored NEO-CODER MAX — Q6_K GGUF (regular, not MTP).

## What this lane is for

- RuFlo / coding agent with **fewer thinking tokens** than stock Qwen3.8-27B (author claim: ~½ to ~¹⁄₁₀).
- Uncensored / heretic. Not a faithful “Team Qwen” base.
- **Not** Valedia live risk brain. Keep the coder hard fence in RuVocal system prompt.

## Reasoning / thinking directions (from HF card)

Qwen3.8 supports three reasoning efforts (jinja / `reasoning_effort`):

| Level | Meaning |
|---|---|
| `xhigh` | Default. Careful, validate assumptions, alternatives. |
| `medium` | Balance; card notes this can turn off injected reasoning instructions so you can set your own. |
| `low` | Brief thinking, go to conclusion faster. |

TURBO tuning already shrinks think blocks across all three. To shrink further: make prompts more specific (less for the model to guess).

If the app has no Thinking dropdown wiring into jinja, set at top of chat template:

```
{%- set reasoning_effort = 'medium' %}
```

or `'low'` / `'xhigh'`.

RuVocal: prefer **Low** or **Medium** over `None` for agent work unless you want maximum speed/shallow replies.

`preserve_thinking` (stock Qwen3.8): keeps prior turn think blocks; good for agents. Disable only if context bloats.

## Suggested samplers (HF)

**Thinking / precise coding (WebDev etc.):**

- temperature `0.6`
- top_p `0.95`
- top_k `20`
- min_p `0`
- presence_penalty `0`
- repetition_penalty `1.0` (off)

**General thinking:**

- temperature `1.0`, top_p `0.95`, top_k `20`, others as above

**Instruct / non-thinking:**

- temperature `0.7`, top_p `0.80`, top_k `20`, presence_penalty `1.5`, repetition_penalty `1.0`

Context: native 256K; our Modelfile sets `num_ctx 65536` for Mac headroom (raise later if needed).

## Tool calling

- Min quant **Q4_K_M**; **Q5/Q6 preferred** (this tag is Q6_K — good).
- Below Q4_K_M, tools may break (general Qwen note).
- Aggressive KV/cache compression can hurt tools — avoid for agent runs.

## MTP vs regular

- This tag is **regular** Q6_K (no MTP suffix).
- MTP quants: keep temp ≤1, rep pen 1; if token acceptance &lt; ~50%, use regular (faster).
- Author speed note (5090): Q4_K_S regular ~75 t/s, MTP can exceed ~90 t/s when acceptance is good — Mac numbers will differ.

## Vision

- Vision supported with a separate **mmproj** in the same folder as the GGUF.
- Files on HF: `mmproj-F16.gguf` / `mmproj-BF16.gguf` / `mmproj-F32.gguf` (not pulled unless needed).

## Fences (Burton / RuFlo)

```
HARD FENCE: Coder / workflow only. No --live, no venue orders, no trading, no kalshi-agent copy, no wallet or spend. Valedia stays sole risk. If asked to trade or go live, refuse and stop.
```

Put that at the **top** of RuVocal System Prompt, before “You are RuFlo…”.

## Related local lanes

| Tag / path | Role |
|---|---|
| `orcarouter/Qwen3.8-27B-Uncensored:q6_K` (= `qwen3.8-27b-obliterated:q6_K`) | Current uncensored twin |
| Unsloth clean BF16 only on SSD | Faithful base — quants not pulled yet |
| Flash-Next UD-Q3_K_XL on SSD | Scout gatherer GGUF, not this tag |
| Official `qwen3.8:27b` | Library Q4 — optional alternate clean |

## Author bench snapshot (treat as marketing until side-by-side)

Nightmedia instruct-mode (mxfp8): TURBO ARC-C 0.735 / ARC-E 0.882 vs stock Qwen3.8-27B 0.591 / 0.782.

## License

Apache-2.0 (per HF card).

## Quant choice (Burton) — 2026-09-13

**Verdict: we are on the correct file for RuFlo Local.**

| What we run | Detail |
|---|---|
| Tag | `qwen3.8-turbo-coder:q6_K` |
| GGUF | `…NEO-CODER-MAX-Q6_K.gguf` |
| Family | **Regular** (not MTP) |
| Quant | **Q6_K** (~6-bit K-quant), ~22GB |

### What “4-bit” / “8-bit” mean on the HF card

Not separate mystery downloads. DavidAU’s marketing/benches for the *same* model line at different bit-widths:

- **4-bit** ≈ Q4-class GGUFs (`Q4_K_S`, `Q4_K_M`, IQ4_*) — card claims ~719 ARC-C even here
- **8-bit** ≈ `Q8_0` (and the high-bit benches that hit ~735 ARC-C)

Our **Q6_K** sits between those: better tool fidelity than Q4, lighter than Q8.

### HF tool-calling advice (same card)

> Min quant **q4km**; q5ks/q5km better; **recommend Q6** (MAX or “low”). Below q4km, tool calling may break.

→ Regular **Q6_K** matches the recommended tier for RuFlo MCP / agent loops.

### Repo file families (same HF repo; title says MTP but ships both)

| Kind | Name cue | When to use |
|---|---|---|
| **Regular** (ours) | `…-MAX-Q6_K.gguf` | Default for tools / coding |
| **MTP** | `…-MTP-Q6_K.gguf` | Speed when token acceptance stays high; else regular is faster |
| **Q8_0** | `…-Q8_0.gguf` | Closest to “8-bit” quality claim; more RAM |
| **Q4_*** | `…-Q4_K_*` | “4-bit” bench story; weaker for tools |

Optional upgrades later: side-by-side **MTP-Q6_K** for speed, or **Q8_0** for max fidelity — not because current is wrong.

## Context for RuFlo Local (2026-09-13)

RuVocal attaches **all** MCP tool schemas each tools-enabled turn (~357 tools ≈ **71k tokens**). With `num_ctx 65536` Ollama returned `exceed_context_size_error` and RuVocal fell back to no-tools.

**Fix:** `num_ctx 131072` on this tag (weights support up to 262144). Do **not** disable RuFlo tools to “fit” — raise context instead.

If long agent threads still OOM or thrash: try `num_ctx 180000` or `262144`, or unload other models first (GPU steward).

## Reasoning / thinking (HF card)

Qwen3.8 / this tune: `reasoning_effort` = `xhigh` (default), `medium`, `low`. RuVocal: prefer Low/Medium over None for agent work. TURBO already shrinks think blocks.

## Samplers (HF + our Modelfile)

- temperature `0.6`, top_p `0.95`, top_k `20`, repeat_penalty `1.0`
- Tools: avoid aggressive KV/cache compression
