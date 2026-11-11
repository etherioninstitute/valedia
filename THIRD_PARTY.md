# Third-party provenance

License texts are not kept in this private, personal-use repository. This file records where
incorporated ideas and code came from so the origin is never lost. If this code is ever shared
outside personal use, restore each upstream license text first.

| Component | Upstream | Pinned at | License (SPDX) | What is used here |
|---|---|---|---|---|
| ATLAS research cohorts | https://github.com/chrisworsey55/atlas-gic | commit cf4349f15c9d68a67042f792973372f2e0231088 | MIT, (c) 2024-2026 General Intelligence Capital | The idea of weighting research cohorts by measured outcomes (`src/atlas_research_adapter.py`, `valedia_trader/agents/`). No upstream code is imported. |
| Position buffering | https://github.com/pst-group/pysystemtrade | idea only (no code copied) | GPL-3.0 | The no-trade band around target positions (`valedia_trader/execution_and_risk/buffering.py`), written independently. |
| Upstream checkouts | `/Volumes/NIMROD_SSD/Qwen-Weight-Space/sources/upstream/PROVENANCE.tsv` | per row | per row | Agent-G2, Harness2, TimesFM, MiroFish (AGPL-3.0), agent-office, Hermes3D staged as source; commit, date and license recorded there. |
