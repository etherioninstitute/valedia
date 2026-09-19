# Ponytail audit — H8 fortress intel fallback (0.33.0)

**Mode:** `/ponytail` full on the H8 diff only.
**Tree:** `~/.openclaw/workspace-v2`
**Scope:** `intel_fetch_fallback.py`, seed `_get` observe hook, `FORTRESS_INTEL_FALLBACK`, `test_h8.py`.
**Fence:** no `--live`, no venue CDP, no captcha/proxy, no Colima/Docker, no second Kelly, flag default off.

## Applied this session

`yagni:` tilion-fortress SDK, Playwright, CDP `:9222`, Docker `tilion/fortress`. Replacement: classify + fail-closed stub. **Not shipped.**

`yagni:` `docker info` / Colima probe (M1 qemu already crashed). `engine_available()` is False unless tests inject. **Not shipped.**

`delete:` 5xx as a fallback trigger. Charter is 403/429/empty/WAF only.

## Findings (biggest cut first)

`yagni:` HTML last-resort crawler, SERP scrape, `solve_captcha`, residential proxy. Charter LEAVE. **Not shipped.**

`yagni:` wrapping `intel_refresh.py` / conviction math. Refresh already skips a fresh swarm. H8 writes `intel_fetch_fallback.json` only. **Not shipped.**

`yagni:` token-capped markdown injected into MiroFish seed body. Observe + JSON file is the hook; seed still API/RSS first. Add seed-append when a reader wants fallback headlines in the briefing.

`shrink:` persist copies last-good headlines aside (`last_good_headlines`), does not flip `ok`. Poisoned HTML cannot look like a live fetch.

## Not cut (spec)

- Structured `{ok, empty, blocked, waf}`.
- Allowlist public intel hosts; venue hosts refused.
- Flag default 0/off (`FORTRESS_INTEL_FALLBACK`).
- Injected `engine_fn` only after failure-gate + flag + allowlist.
- Tests mock engine; no HTTP.

**net:** ~220-line adapter, 1 seed hook, 0 deps, 0 cron, 0 Kelly. Lean enough. Ship.
