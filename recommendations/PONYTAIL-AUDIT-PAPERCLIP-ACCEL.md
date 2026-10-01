# Ponytail audit — PAPERCLIP ACCEL machine

## Reconcile pass 3 — 2026-09-28 ~02:42 PT

`reuse:` POST `/recovery-actions/resolve` on the settled `replay=blocked` rows. Inner `actionOutcome=not_performed`. No new route, no new agent.

`shipped:` VAL-1 `bbab904b` and VAL-3 `d728ab74` handed back. One sweeper wake each (`d801ca4c`, `69f53e43`). Review stages and the VAL-1 watchdog were already set.

`shipped:` VAL-13 review=Decider after `b4a3cefa` timed out and dropped its checkout. Continuation `ec20ac29`.

`yagni:` a second Scout or Decider invoke. The 15s sweeper already woke the idle assignee.

`yagni:` another VAL-11/VAL-12 resolve. `dbc1f464` and `0a7036a0` timed out again at 1800s with no tool calls. Waking CTO would replay that timeout, and VAL-12 is the money four-pack.

`yagni:` CTO heartbeat, Hermes `--replace`, life-gateway bounce, trading-runtime edits.

**Mode:** `/ponytail` full. Control-plane only.
**Fence:** no money four-pack, no `OKX_LIVE`, no grok-run VAL-12, no `--live`.

## Follow-up 2026-09-28 ~02:12 PT

`reuse:` the same Paperclip recovery resolve, executionPolicy PATCH, and watchdog PUT. No new agents.

`shipped:` VAL-10 (`c6778eec`, the UUID in the follow-up) restored off `recovery_needed`, Scout assignee, Auditor review, Decider approve. VAL-12 Auditor review, `user:local-board` approve, Auditor watchdog, CTO continuation `0a7036a0`. VAL-11 and VAL-4 stages filled.

`yagni:` Scout heartbeat. VAL-3 run `e06d4773` still holds Scout. Assignee patch used `deferWakeForGoal`.

`yagni:` a second CTO invoke. The 15s sweeper already started VAL-12 `0a7036a0` and VAL-11 `dbc1f464`.

`yagni:` editing VAL-13. Auditor run `b4a3cefa` is live. Policy stays empty until that checkout ends.

`yagni:` a new DB backup. Newest dump was 12 minutes old.

`shrink:` zero trading-runtime edits. Grok did not run the money four-pack.

**Mode:** `/ponytail` full. Control-plane only.
**Fence:** no money four-pack, no `OKX_LIVE`, no grok-run VAL-12, no `--live`.

## Applied

`reuse:` existing Paperclip employees Scout/Decider/Auditor/CTO and Hermes profiles `valedia-trading-*` / `valedia-cto`. No new agents, no new profiles, no `gateway run --replace`.

`reuse:` native `executionPolicy.stages`, `executionPolicy.monitor`, `PUT /watchdog`, `POST .../recovery-actions/resolve`. No custom workflow engine.

`shipped:` rebound `adapterConfig.apiBaseUrl` to `:8642-8645` and rotated apiKey secret-refs from the live profile `.env`. Auth proven with `GET /v1/models` 200.

`shipped:` VAL-5 Reviewer=Auditor, Monitor=ACCEL-CIO, Watchdog=Auditor. Children got charter stages. VAL-1/3 restored off stale `blocked`.

## Skipped

`yagni:` Approver on VAL-5. Decider is the parent executor; Paperclip excludes self-approve. Approver=Decider lives on VAL-3/6/7/9.

`yagni:` watchdog on every child. One Auditor watchdog on VAL-5 covers the ACCEL subtree; VAL-1 has its own for the weather pilot.

`yagni:` invoking CTO heartbeat. VAL-12 already holds the checkout. Grok Build did not run the money four-pack.

`yagni:` new Hermes profiles, LiteLLM flip, Ollama `:11434` for these four. They already use LMS `:1234`.

`yagni:` looping restore on VAL-5/6/7/8/9 while Decider/Scout/CTO are claimed. Each restore re-queues a successor; a skip recreates the no-replay notice. Dashboard `blocked=0`. Add a fourth restore after VAL-1/3 finish if the notice still defers the monitor.

`yagni:` git commit. **Not committed.**

## Findings

`shrink:` four curl/PATCH classes (agent bind, executionPolicy, watchdog, recovery resolve). No code in the trading runtime.

**net:** EXECUTION panel on VAL-5 is filled. VAL-1/3 are live Hermes runs. Local Hermes+LMS only. VAL-12 untouched.
