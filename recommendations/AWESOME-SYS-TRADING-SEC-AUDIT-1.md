# AWESOME-SYS-TRADING-SEC-AUDIT-1

**Date:** 2026-09-25 PT  
**Status:** charter only — do **not** launch Grok Build yet; parent/Burton decides  
**Target tree:** `~/MemPalace/sources/repos/github/awesome-systematic-trading`  
**Upstream:** https://github.com/paperswithbacktest/awesome-systematic-trading  
**Clone HEAD (this machine):** `4e23dd84c9ff746ddfcbc856316bcbcef0855b81` (`4e23dd8` Merge PR #105 upstream-picks)  
**Fence:** read-only security review. No strategy live-run. No `--live`. No wallet/venue keys. Oil stays fenced. Never decompile NT DLLs.

**Related:** `PAPERCLIP-INFORMED-RISK-LOOP-1.md`, `PAPERCLIP-VALEDIA-ORG-2026-09-24.md`, `RUFLO-PONYTAIL-AUDIT.md`, `VIBE-TRADING-SUBSUME-1.md` / `VIBE-TRADING-SECURITY-2026-09-23.md`, RuFlo plugin `plugins/ruflo-security-audit/`

---

## Scope (do)

Read-only security review of the **cloned curated list** and any **referenced code patterns** Valedia might later pull:

1. **Supply-chain** — remote URLs in README; CI workflow `.github/workflows/link-check.yml`; `scripts/*.py` network behavior; pins vs floating deps if any code is executed.
2. **License** — repo license clarity; third-party paper/strategy snippets under `static/strategies/` (~61 sample strategy files); GPL/unclear reuse for Valedia absorb (prefer inventory + reimplement).
3. **Secrets** — `.env`, keys, tokens, hardcoded credentials in scripts/static/strategy samples.
4. **Install / remote code** — any `curl | sh`, `npm install` of untrusted specs, `pip install` from raw URLs, `eval`/`exec`/`subprocess(shell=True)` in repo scripts.
5. **Malware / abuse patterns** — unexpected exfil, wallet prompts, broker write templates that could be mistaken for Valedia live path.
6. **Overlap hygiene** — map themes Valedia cares about (risk gates, momentum/confirmation, session filters, mean-reversion vs trend, news blackout, position sizing) vs equities backtest papers that **do not** map to Kalshi/Poly weather — do not recommend pulling those live.

**Explicit non-goals**

- Running awesome strategies live or in Valedia OMS.
- Installing the full paperswithbacktest stack into Valedia.
- Flipping `WEATHER_LIVE` / oil unfence / Poly writers.
- Launching Grok Build in this charter (recommend only).

---

## Decision recommendation (ownership)

### Prefer: Paperclip owns goal + issues; RuFlo/Ornith executes the audit

| Plane | Owner | Why |
|-------|--------|-----|
| **Company goal + issue tracking** | **Paperclip** (existing INFORMED goal / child tickets; Scout→Decider→Auditor→CTO) | Matches `PAPERCLIP-VALEDIA-ORG-2026-09-24.md`; no venue keys; no `--live` in adapters |
| **Security audit execution** | **RuFlo `security` + plugin `ruflo-security-audit`** (CTO default builder Ornith+RuFlo) | Real audit path exists on this machine; matches prior ponytail/security scrub pattern (Vibe subsume) |
| **Coding fixes after findings** | **CTO tickets** → Ornith+RuFlo default; **Grok Build only if Burton opts in** (`~/bin/valedia-cto-builder grok-build`) | Fixes must resolve to Valedia hard fences / INFORMED-only; never oil unfence |

**RuFlo security-audit: YES** — invoke paths:

```bash
# CLI (installed ruflo v3.41.2)
ruflo security scan --depth deep --target ~/MemPalace/sources/repos/github/awesome-systematic-trading
ruflo security secrets   # secrets detection
ruflo security cve --list
ruflo security threats --model stride

# Plugin contract / Claude command surface
# /audit [--depth quick|standard|deep] [--target <dir>]
# → npx @claude-flow/cli@latest security scan|cve|threats
# Plugin: ~/MemPalace/sources/repos/github/ruflo/plugins/ruflo-security-audit/
# ADR: .../docs/adrs/0001-security-audit-contract.md
```

Complement (not substitute): Valedia **ponytail** audits for fence integrity after any absorb (`RUFLO-PONYTAIL-AUDIT.md` pattern) — separate from CVE/supply-chain scan.

**Grok Build:** do **not** launch now. Use only after audit findings need Valedia code remediation **and** Burton opts in.

---

## Audit checklist (executor)

1. Inventory tree: README categories (136 libraries, Strategies by asset class, 55 books, 22 videos); `static/strategies/` sample count; `scripts/` (`check_links.py`, `build_strategies_table.py`, `update_counts.py`, `paper_meta.json`).
2. Run RuFlo `security scan` + `secrets` on clone root; store summary under Paperclip issue comment + optional `recommendations/AWESOME-SYS-TRADING-SEC-FINDINGS-YYYY-MM-DD.md`.
3. Manual skim: workflow YAML, scripts for network/shell, LICENSE, any binary blobs.
4. Produce findings table: severity · path · pattern · Valedia action (`ignore` / `inventory-only` / `fence` / `fix-ticket`).
5. If absorb candidates exist: only themes that strengthen INFORMED weather / fee-true / risk gates — never equities factor zoo as live Kalshi strategy.
6. Ponytail-style fence proof if any Valedia file changes: no `--live`, oil fenced, keys untouched.

---

## Fix-ticket policy (if remediation needed)

Charter fix tickets that resolve **per Valedia goals**:

- Hard fences preserved (no oil unfence, no venue keys in Paperclip, no `--live` from adapters).
- INFORMED-only create path; lottery/late floors unchanged unless Board says otherwise.
- Prefer clean-room reimplement over vendoring GPL / unclear snippets.
- CTO assignee; Auditor HOLD/FAIL on PR; Board before any runtime flag flip.

---

## Recommended launch command / path (Burton / parent decides)

**Do not run yet from this charter.** When approved:

1. Ensure Paperclip up (`~/.paperclip-valedia`, `:3100`) — was **down** at charter time.
2. Create child issue under INFORMED goal, e.g.  
   `SEC-AUDIT awesome-systematic-trading — RuFlo read-only supply-chain`  
   Assignee: **CTO** (coding-only); Decider owns decompose; Auditor reviews findings.
3. Execute audit via CTO/Ornith+RuFlo:

```bash
cd ~/MemPalace/sources/repos/github/awesome-systematic-trading
ruflo security scan --depth standard --target .
ruflo security secrets
# optional deep:
# ruflo security scan --depth deep --target .
# Write findings MD under ~/.openclaw/workspace/recommendations/
```

4. Only if findings need Valedia code changes **and** Burton opts in:

```bash
~/bin/valedia-cto-builder grok-build "Remediate AWESOME-SYS-TRADING-SEC findings per AWESOME-SYS-TRADING-SEC-AUDIT-1 — fences held, no --live"
# or persist: ~/bin/valedia-cto-builder grok-build  + PATCH Paperclip builder
```

Default remains: `~/bin/valedia-cto-builder ornith`.

---

## Success criteria

- Findings doc written (or clean scan attested) with evidence paths.
- Paperclip issue tracks GOAL; RuFlo performed scan; Grok Build not auto-launched.
- No live strategy runs; oil/WEATHER fences untouched; no keys in Paperclip.
