# AWESOME-SYS-TRADING-SEC-AUDIT-FINDINGS-2026-09-25

**Date:** 2026-09-25 PT  
**Charter:** `~/.openclaw/workspace/recommendations/AWESOME-SYS-TRADING-SEC-AUDIT-1.md`  
**Target:** `~/MemPalace/sources/repos/github/awesome-systematic-trading`  
**Clone HEAD:** `4e23dd84c9ff746ddfcbc856316bcbcef0855b81` (`4e23dd8` Merge PR #105 upstream-picks)  
**Upstream:** https://github.com/paperswithbacktest/awesome-systematic-trading  
**Fence held:** read-only review. No strategy live-run. No `--live`. No wallet/venue keys. Oil stayed fenced. No NT DLL decompile. No Grok Build launch. No Paperclip mutation.

---

## Overall verdict: **WARN** (absorb-safe as inventory only; do not vendor or live-run)

| Gate | Result |
|------|--------|
| RuFlo `security scan --depth standard` | **PASS** — 0 Critical / 0 High / 0 Medium / 0 Low |
| RuFlo `security secrets` | **PASS*** — no secrets reported (*caveat: CLI reported “Files scanned: 1”; manual pass covers the gap) |
| Manual supply-chain / install abuse | **PASS** — no `curl\|sh`, no `eval`/`exec`/`shell=True`, no wallet keys |
| License clarity for Valedia absorb | **WARN** — **no LICENSE file** in clone |
| Strategy samples as live path risk | **WARN** — 61 QuantConnect samples with `SetHoldings` / `MarketOrder` / remote Quantpedia CSV fetches; equities/crypto/oil themes ≠ Kalshi/Poly INFORMED weather |
| Valedia north-star fit | **PASS if inventory-only** — reimplement useful themes clean-room; never live-run awesome strategies |

---

## Severity summary

| Severity | Count | Notes |
|----------|------:|-------|
| Critical | 0 | — |
| High | 0 | — |
| Medium | 3 | Missing LICENSE; QC broker-write samples; remote Quantpedia data fetch in samples |
| Low | 4 | Secrets CLI shallow scan; RuFlo left `.claude*` artifacts; HF/GitHub token env docs; oil-named sample theme |
| Info | 3 | Workflow hygiene good; scripts network is intentional; theme overlap for INFORMED inventory |

---

## RuFlo CLI evidence

**CLI worked:** YES — `/Users/burtonchatelain/.npm-global/bin/ruflo` **v3.41.2**

Commands run (discovered via `ruflo security --help` / `scan --help` / `secrets --help`; flags not invented):

```bash
ruflo security scan --depth standard --target ~/MemPalace/sources/repos/github/awesome-systematic-trading
# → No security issues found! Critical:0 High:0 Medium:0 Low:0 Total:0

ruflo security secrets --action scan --path ~/MemPalace/sources/repos/github/awesome-systematic-trading
# → No secrets detected. (Files scanned: 1 — treat as incomplete coverage)

ruflo security scan -t <path> -d standard --type code -o json
# → same clean result
```

Raw tees (same machine):

- `~/.openclaw/workspace/recommendations/awesome-sys-trading-ruflo-scan-2026-09-25.txt`
- `~/.openclaw/workspace/recommendations/awesome-sys-trading-ruflo-secrets-2026-09-25.txt`

Plugin contract consulted: `~/MemPalace/sources/repos/github/ruflo/plugins/ruflo-security-audit/README.md` + `commands/audit` (`/audit` → scan + cve + threats). This run stayed at **standard scan + secrets** per parent preference; CVE/threats STRIDE not required for inventory WARN.

**Side effect (Low):** RuFlo wrote local scan artifacts under the clone:

- `awesome-systematic-trading/.claude/` (`proven-config.json`, `security-scans/`)
- `awesome-systematic-trading/.claude-flow/` (`harness-active-policy.json`, `policy/`)

These are scanner state, not upstream content. Do not commit; optional cleanup later (out of this read-only charter).

---

## Findings list

| ID | Sev | Path / evidence | Pattern | Valedia action |
|----|-----|-----------------|---------|----------------|
| F1 | Medium | *(repo root — no `LICENSE*`)* | No license file; README has no SPDX/license grant. Awesome-list reuse of third-party QC strategy snippets under `static/strategies/` is legally unclear for absorb. | **inventory-only**; prefer clean-room reimplement; do not vendor GPL/unclear snippets into Valedia |
| F2 | Medium | `static/strategies/*` (61 entries; 60 `*.py` + extensionless `pairs-trading-with-stocks`) | QuantConnect `QCAlgorithm` samples: **94** `SetHoldings`, **26** `MarketOrder`, **5** `self.Buy`/`self.Sell`. Looks like “runnable strategies,” easy to mistake for Valedia live path. | **fence** — never live-run; never wire to OMS / `--live` / Poly writers; themes only |
| F3 | Medium | `static/strategies/*.py` (~30 `RemoteFile` / ~35 `data.quantpedia.com/...`) | Strategies pull remote CSV/JSON from `data.quantpedia.com/backtesting_data/...` at QC data-subscribe time. Supply-chain / availability / ToS risk if executed. | **ignore for Valedia runtime**; do not execute samples; no network allowlist for Quantpedia in Valedia |
| F4 | Low | RuFlo secrets output | `Files scanned: 1` despite ~77 non-git files / ~63 `.py`. Manual grep found **no** hardcoded API keys/tokens/wallets (`wallet`/`mnemonic`/`PRIVATE_KEY` = 0; `paper_meta.json` `sk-` hits are “Risk-…” paper titles). | **document**; trust manual pass; optional deeper secrets tool later |
| F5 | Low | `.claude/`, `.claude-flow/` (created 2026-09-25 PT by this scan) | Scanner left local config/scan state inside clone working tree. | **ignore** / don’t commit; optional delete outside this charter |
| F6 | Low | `scripts/check_links.py`, `scripts/build_strategies_table.py` | Intentional network: GitHub API via `urllib` + optional `GITHUB_TOKEN`; HF hub download needs `HF_ACCESS_TOKEN` from env (not hardcoded). CI uses `secrets.GITHUB_TOKEN` with `permissions: contents: read`. | **ignore** if scripts not run in Valedia; if run, env-only tokens, no store in Paperclip |
| F7 | Low | `static/strategies/crude-oil-predicts-equity-returns.py` | Oil-named equities factor sample (SP100 + oil predictor theme). | **fence** — oil stays fenced; do not map to WEATHER_LIVE / oil unfence |
| F8 | Info | `.github/workflows/link-check.yml` | Monthly link/count check; `actions/checkout@v4`, `setup-python@v5`, `contents: read`, report to step summary. No write tokens, no deploy keys. | **ignore** (good hygiene) |
| F9 | Info | `scripts/update_counts.py`, `scripts/paper_meta.json` (~3806 meta entries) | Local counter rewrite / catalogue metadata; no secrets patterns of concern. | **ignore** |
| F10 | Info | README library/strategy catalogue vs Valedia INFORMED | Equities factor zoo, crypto rebalance, FX carry, commodities momentum dominate. Weak direct map to Kalshi/Poly **weather** prediction markets. Useful *themes*: momentum/confirmation, session/seasonality filters, mean-reversion vs trend, position sizing / fee awareness (QC `GetOrderFee` stubs). | **inventory-only** — reimplement INFORMED-relevant themes; do not pull equities papers live |

---

## Manual checklist (charter)

| Check | Result |
|-------|--------|
| Tree inventory | README (+ ja/zh), `scripts/` (4 files), `static/strategies/` (61), `static/images/`, one workflow |
| LICENSE | **Missing** |
| `.github/workflows` | `link-check.yml` — read-only, OK |
| `scripts/*.py` for `curl\|sh` / `eval` / network / secrets | Network yes (GitHub + HF); no shell injection / no hardcoded secrets |
| Sample strategies for broker-write / wallet | Broker-write **yes** (QC APIs); wallet **no** |
| Binaries / NT DLLs | None found (no decompile needed) |
| `.env` / credential files | None |

---

## Recommended remediations (Valedia north star)

1. **Treat clone as curated bibliography only** — inventory themes; do **not** install paperswithbacktest stack into Valedia; do **not** `pip`/`npm` from this tree into production.
2. **Never live-run** `static/strategies/*` — no QuantConnect live, no Valedia OMS bridge, no `--live`, no Poly/Kalshi writers from these samples.
3. **Oil fence** — ignore/quarantine oil-commodity predictor themes relative to WEATHER / oil unfence; Board before any related flag.
4. **Clean-room only** if a theme is useful (risk gates, momentum confirmation, session filters, fee-true sizing). Prefer Valedia-owned code under INFORMED; Auditor HOLD/FAIL on any PR that vendors these files.
5. **License** — do not redistribute strategy bodies without upstream clarity; citation + reimplement is the absorb path.
6. **Secrets / tokens** — if maintainers run `check_links.py` / `build_strategies_table.py`, keep `GITHUB_TOKEN` / `HF_ACCESS_TOKEN` in local env only; never Paperclip / never commit.
7. **Ponytail follow-up** (separate) only if any Valedia file later changes because of this work — prove no `--live`, oil fenced, keys untouched (`RUFLO-PONYTAIL-AUDIT.md` pattern).
8. **Do not launch Grok Build** from this audit. Default builder remains Ornith+RuFlo if CTO tickets appear.

---

## Paperclip issue text (paste when Paperclip is up — not created this run)

> **Title:** `SEC-AUDIT awesome-systematic-trading — RuFlo read-only supply-chain (2026-09-25)`  
> **Parent goal:** INFORMED  
> **Assignee:** CTO (coding-only if remediations); Decider decompose; Auditor review findings  
> **Body:** RuFlo v3.41.2 standard scan = 0 issues; secrets CLI shallow (1 file) but manual pass clean. Findings MD: `~/.openclaw/workspace/recommendations/AWESOME-SYS-TRADING-SEC-AUDIT-FINDINGS-2026-09-25.md`. Overall **WARN**: missing LICENSE; 61 QC samples with broker-write + Quantpedia remote data — inventory themes only; never live-run; oil fenced; no Grok Build. Remediations = policy/fence tickets, not stack install.

---

## Success criteria (charter)

- [x] Findings doc written with evidence paths  
- [x] RuFlo performed scan (+ secrets)  
- [x] Grok Build not launched  
- [x] No live strategy runs; oil/WEATHER fences untouched; no keys in Paperclip  
- [ ] Paperclip child issue — **deferred** (document paste text above; Paperclip not mutated)

---

## Raw command log (abbrev)

```
Fri Sep 25 23:51:12 PDT 2026  ruflo security scan --depth standard → 0 issues
Fri Sep 25 23:51:13 PDT 2026  ruflo security secrets -a scan → no secrets (1 file scanned)
Manual grep: no curl|sh / eval / wallet; QC SetHoldings/MarketOrder present; no LICENSE
```
