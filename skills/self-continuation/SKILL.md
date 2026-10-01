---
name: self-continuation
description: >-
  Before asking Burton a mid-task question, classify it. Stop only for
  irreversible, outward-facing, preference, or resource-blocked gates;
  else decide + one-line why-safe and continue. Call the CLI helper for
  ambiguous cases.
---

# Self-Continuation (Flux P0-3)

## Rubric
**STOP + ASK** only if the pending decision is one of:
1. **Irreversible** — LMS unload/swap, kill exclusive `flux-grok`/`valedia-grok`, volume wipe, force-push, permanent delete
2. **Outward-facing** — `--live` / venue orders, send/email/post, wallet/spend/transfer, deploy
3. **Preference** — pure taste with no derivable best
4. **Resource-blocked** — missing credential, 2FA, Burton pin/yes the agent lacks

**Everything else → decide, one-line why-safe, continue.**

## When to use
Agent is about to ask Burton "should I…?" mid paper/ops/Build work, or is about to stop while work remains.

## When not to use
Already inside an explicit Burton-gated action (approval-gate already fired). Never use this skill to *bypass* a fence.

## Steps
1. Form the question in one sentence.
2. Run (optional but preferred on ambiguous text):
   `~/.openclaw/workspace-v2/scripts/self-continuation.sh classify "<question>"`
3. If exit 2 / `gate: user` → stop, show category + question, wait same-turn yes.
4. If exit 0 / `gate: self` → pick best option, state one-line why-safe, continue. Do **not** ping.
5. If ending a turn with pending work → `.../self-continuation.sh on-stop true` and follow the directive (do not stop).

## Valedia hard examples (always USER)
- `--live`, `create_order`, WEATHER_LIVE / OKX_LIVE flips
- send / email / Signal beyond allowed Invested/Profit formats
- wallet transfer/deposit/sweep
- LMS unload or kill loaded Ornith/Qwen/Bionic
- Paperclip or automation kill of exclusive Build screens

## Full map
`~/.openclaw/workspace-v2/recommendations/VALEDIA-SELF-CONTINUATION.md`

> **Audience:** Valedia Hermes owns self-continuation. Grok Build must not `onStop`-loop itself; it only respects the four stop gates.
