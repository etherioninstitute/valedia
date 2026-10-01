# H1 dossier — fortress

**Path:** `/Users/burtonchatelain/MemPalace/sources/repos/github/fortress`  
**Upstream:** tiliondev/fortress (Tilion / arham766)  
**License:** **BSD-3-Clause** on patches/tooling/SDKs. Chromium + third-party in `NOTICE`. Published `patches/` are a **demo subset**; shipped stealth lives in the **binary** and is not fully open.  
**Slice:** H8 — optional scrape sidecar **if intel fetch blocked**. ToS-respecting. Not for abuse.  
**Parking:** `VALEDIA-SUBSUME-NAUTILUS.md` notes fortress **PARKED** (M1 Colima qemu crash). macOS has **no native engine** (Docker amd64 / Rosetta).

---

## Identity

Stealth Chromium: fingerprint corrected in **C++** (not puppeteer-stealth). CDP on `:9222`. Playwright/Puppeteer drop-in. MCP ~29 tools (`tilion-mcp`). Gauntlet: CreepJS 0% headless, Cloudflare Turnstile, Sannysoft. **Not a finance product.** Demos: retailers, Reddit, Wikipedia, detector sites.

---

## Architecture

```
Playwright / Puppeteer / MCP
        → Fortress Chromium :9222
        → target site (WAF / SPA)
```

SDK: `tilion-fortress` (PyPI/npm). Docker `tilion/fortress:149` (channel `latest` 404s). MCP: SSRF deny of private/metadata unless `TILION_ALLOW_PRIVATE_EGRESS=1`, path sandbox, timeouts.

Hermes may list `tilion-mcp`; it is **not** on Valedia cron (`AGENT-INVENTORY.md`).

---

## STEAL (hygiene patterns only)

1. **Failure-gated fallback** — use a harder fetch **only** after 403/429/empty/WAF, never as default. Matches `intel_refresh.py` “don’t clobber a fresh swarm.”  
2. **Structured `{ok, empty, blocked, waf}`** — retry by status, not vibes.  
3. **API/RSS first, HTML last** — seed already does CoinGecko/Yahoo chart/F&G/Polymarket/Kalshi/RSS.  
4. **Token-capped extract** — headlines, not 353k-token dumps into MiroFish.  
5. **Politeness** — delay, page caps, allowlist.  
6. **SSRF + write sandbox + timeouts** if a browser is ever shelled.  
7. **SHA-256 verify** if a binary is ever fetched.

Do **not** steal fingerprint patches, unpublished persona mint, captcha farms, residential proxies.

---

## LEAVE

| Leave | Why |
|-------|-----|
| WAF/CAPTCHA circumvention as a product | CFAA/ToS-adjacent |
| `crawl_site` / `recon_site_apis` | Mass harvest / private XHR recon |
| Login, cookies, `save_profile`, checkout | Credential-stuffing *shape* |
| `solve_captcha` + 2captcha | Paid farm |
| Residential `TILION_PROXY` | IP laundering |
| Browser SERP scrape | Google/Bing ToS |
| Pointing CDP at Kalshi/Coinbase/Alpaca UIs | Venue session theft risk |
| Vendoring Chromium | Unpublished core; fonts renamed to Windows families |
| Hermes auto-approve MCP | Open-world browser on 9222 |
| Colima-on-M1 as cron | Already crashed; PARKED |

---

## Valedia touchpoints

| Module | Role |
|--------|------|
| `mirofish_seed_accurate.py` | **Only** H8 insertion: if an allowlisted RSS/API section fails, capped headlines |
| `intel_refresh.py` | Existing fallback; H8 must not overwrite fresh swarm |
| `intel_layer.py` / `intel_sentiment.py` / `hedge_fund_intel.py` | Prefer their APIs; fortress never in conviction math |
| `intel_opportunity_gatherer.py` | **Stays local JSON.** Sidecar writes a file; gatherer never CDP |
| `injury_checker.py` | ESPN **JSON** already; do not scrape |
| Kelly / FTL / allocator | **No hook** |

---

## Risks

- ToS / CFAA for beating bot walls.  
- IP: binary ≠ this repo’s 34 patches.  
- macOS reliability (PARKED).  
- CDP `:9222` is remote-control of a browser — localhost only.  
- Poisoned HTML → bad seed → bad MiroFish. Fail closed to last good seed.  
- Supply chain: GitHub release binaries; SUMS sometimes skipped.

---

## Seamless hook

```
API/RSS (primary)
  └─ fail 403/empty/WAF ─[FORTRESS_INTEL_FALLBACK=0 default]─► sidecar
        → capped markdown → seed / models/intel_fetch_fallback.json
        → MiroFish → gatherer (still files only)
        → brain unchanged
```

Never a second Kelly. Never OMS.

---

## H8-ready

Flag `FORTRESS_INTEL_FALLBACK` default False (same family as `SPOT_RECONCILE_HTTP`). Allowlist public intel hosts only. Skip if Docker/Colima unhealthy. Tests mock CDP. **Do not enable this slice.** Honest H8 on this Mac is adapter+flag+tests; engine optional until Linux sidecar or native mac.
