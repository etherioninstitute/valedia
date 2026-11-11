# Valedia architecture guide

These are curated navigation hypotheses based on filenames and the prior scoped static preflight of 2026-11-06. They are not a complete design review. The generated module paths, symbols and import targets are source-derived facts; subsystem roles below need confirmation before changes.

| Existing area | Starting modules or name families | Curated role |
|---|---|---|
| MiroFish | `mirofish_runner`, `mirofish_seed_accurate`, `mirofish_signal_extractor`, `mirofish_accuracy_ledger`, `mirofish_intel_archive`, `mirofish_job_lock`, `mirofish_neural_enrich` | Seed, graph, simulation, report, signal extraction, freshness and ledger pipeline |
| Decision and risk | `unified_brain`, kernel, Etherion, Kelly modules | Fusion, risk boundaries and position sizing |
| Allocation/execution | master allocator, execution-policy/intent and reconciliation modules | Allocation and order-policy boundaries |
| Trading desks | crypto, sports, weather, oil, prediction-market name families | Venue/domain-specific research and decisions |
| Venue clients | venue API, broker/client and order reconciliation modules | External interfaces; imports do not prove working connectivity |
| Evaluation | forward-test, calibration, PnL and ledger modules | Tracking/evaluation source, distinct from excluded ledger data |
| Paper/shadow interfaces | Nautilus and TradingView modules | Paper/shadow adapters and input interfaces |
| Research | ATLAS, local/offline research, `research_desk`, `ta_research`, `weather_sources` | Existing research adapters and nested packages |
| Operations | cockpit, memory, wrappers | Navigation/UI and operational entrypoints |

Use the generated complete module table to locate exact filenames; the table above is intentionally a guide, not an exhaustive membership classification. MiroFish is an existing subsystem and must be considered when changing fusion, freshness, simulation or signal handling.

Prior validation scope: top-level Python AST indexing plus selected MiroFish/config/fusion source inspection. That preflight was explicitly not a full audit. The new map indexes every allowlisted recursive Python module and the whole AST, including imports nested in functions or conditionals. Neither establishes execution order, a complete dynamic call graph, installed dependency availability, model quality, safety, connectivity, current trading state or profitability. Old home paths and backend references remain unresolved runtime dependencies. This tool does not repair or execute them.
