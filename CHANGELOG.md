# Valedia CHANGELOG

Self-modification / maintenance log. Newest first.

## 2026-09-13 — MiroFish → brain signal pipeline (RC1/RC2/RC3)

Context: audit found MiroFish's broad market intelligence (Fed, IPO, FDA, M&A,
regulation, geopolitics, equities) was generated but not reliably reaching the
decision brain — the live `mirofish_signal.json` was frequently a shallow
`intel_refresh (lightweight)` crypto-sentiment fallback, and even real-swarm
signals were crypto-only. Three root causes (RC1/RC2/RC3).

### RC3 — DONE: event/catalyst layer added to the signal extractor
- File: `scripts/integrations/mirofish_signal_extractor.py`
  (backup: `mirofish_signal_extractor.py.bak.pre_rc3_20260912_203537`).
- Added `extract_events()` + `EVENT_MAP`: detects monetary_policy (FOMC/Fed/rate/
  Powell/Warsh), ipo, fda, earnings, m_and_a, regulation, geopolitics, scores each
  by surrounding bull/bear context, emits `events[]` + a 0-100 `macro_bias`.
- `process_mirofish_report()` now blends macro_bias into `overall_bias`
  (0.65*asset + 0.35*macro). KEY FIX: a macro-heavy report that names no tickers
  no longer collapses to neutral 50 — it carries the event read to the brain
  (which already consumes `overall_bias`). Output schema gained `events`,
  `macro_bias`.
- Tested on the real report: extracted regulation + geopolitics events,
  macro_bias 68.8, blended overall 60.5 (was asset-only 56.0). Takes effect on the
  next completed real swarm run (live signal not clobbered from a stale report).
- KNOWN CAVEAT: event sentiment SIGN is context-driven (e.g. "war" scored RISK_ON
  from surrounding bullish framing). Detection solid; sign may need tuning. Only
  35% weight, so bounded. Follow-up candidate: risk-off priors for war/sanctions;
  and source an economic/FOMC calendar (see repos/OpenTerminal eval).

### RC2 — VERIFIED ALREADY FIXED (no action)
- The historical `Signal extraction error: .../mirofish_seeds/reports` was fixed by
  a runner patch at 2026-09-12 19:49 (`mirofish_runner.py` now reads
  `~/mirofish-offline/backend/uploads/reports`, isdir-guarded). Confirmed by the
  02:46 ctrl-run producing a proper `source:"mirofish_swarm"` signal, no error.

### RC1 — DIAGNOSED (fix pending owner decision on levers)
- Symptom: most scheduled runs fall back to lightweight; the real blocker is
  Step 4 "Preparing agents" → "agents not ready after 90min (prepare timeout)".
- RED HERRING ruled out: the 20s `batch_interview` IPC timeout
  (`graph_tools.py` MIROFISH_INTERVIEW_TIMEOUT, default 20) is INTENTIONAL — the
  OASIS env never services that IPC, so interviews are deliberately failed fast so
  the report completes on graph evidence. Raising it would make runs slower, not
  fix them. DO NOT raise.
- Real cause: persona + sim-config generation for the agent set is throughput-bound
  on the local model. Contributors: `OLLAMA_KEEP_ALIVE` unset (default 5min → model
  unloads between phases → ~6.4s cold-reloads each, measured); TWO `ollama serve`
  processes running (possible contention); GPU-steward juggling.
- Proposed levers (pending owner decision — memory/fidelity tradeoffs):
  (1) scoped OLLAMA_KEEP_ALIVE (~30m) for the persona model to kill cold-loads;
  (2) cap agent count (faster prepare, mild fidelity tradeoff);
  (3) resolve the two-ollama-server question. Not yet applied.
