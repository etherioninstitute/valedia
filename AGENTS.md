# Working in Valedia

Before architecture analysis or code changes, run from this repository:

```sh
python3 tools/repo_map.py ensure
python3 tools/repo_map.py check
```

Read `.repo-map/current.json` once, then read compact `navigation.md` inside its named `.repo-map/generations/<generation>/` directory. Keep that generation path for the session. Query only the relevant modules, symbols and dependents from `modules.json` using context-mode or the README query example; do not dump the whole index into context. `repomix.xml` is an optional compact file tree. The map prints relative paths. After changes, run ensure and check again. Never trust cached context solely because a watcher is running. For release review, `check` must exit zero after `ensure`; the tree is under git since 2026-11-10 (baseline tag `v0.74.1-recovered`, branch `backup/pre-fable-5.2-overhaul`); there is no CI yet.

See `docs/repository-map/README.md` for installation, optional foreground watch and query examples, and `docs/repository-map/ARCHITECTURE.md` for curated subsystem roles, including existing MiroFish. Module/symbol/import facts are static evidence. Curated roles and prior scoped validation are separately labeled. This map is not a behavioral, dynamic-call-graph, security or profitability audit. Do not infer that an import executes, that an external dependency is installed, or that a trading path is operational.

Repository-map work must not import or execute trading modules or read state/ledger/credential data. The map excludes state, models, caches, secret-looking files, special files and symlinks. Default Repomix output contains file navigation only; explicitly request `pack` for a local source pack when needed. Keep secret scanning enabled and do not upload packs.

## Layout since 0.75.0

- `valedia_trader/` is the package for all new code: `core`, `quant_engine` (50 formulas, `formula_registry()`), `agents`, `venues`, `data_and_fees`, `execution_and_risk`, `backtest`. Tests live in `tests/`.
- `valedia_trader/research_loop/` is the research process every new idea goes through (ledger, strategy shape, gauntlet, kill switch, paper trader, critic, capped revisions). Read `docs/RESEARCH_LOOP.md` before adding a strategy. Do not test an idea outside it: a backtest that is not in `memory/ledger.db` is not counted, and an uncounted backtest makes every Deflated Sharpe wrong. Never read prices past `Gauntlet.dev_prices` when developing an idea.
- `src/` is the legacy flat layer. Keep it byte-stable unless you are fixing a defect: several modules are pinned by content hash, the trading halt file is located relative to them, and many tests read their source by path. `valedia_trader/core/legacy.py` records the target domain of each module for when it is moved, one family at a time, with its tests.
- Environment: `uv sync` (Python 3.12, `uv.lock`). New-code tests: `.venv/bin/python -m pytest tests`. Legacy tests: `tools/run_legacy_tests.sh` (sandboxed; restores `state/halt*.json`).
- Credentials are read from `~/.hermes/.env` (override with `VALEDIA_ENV_FILE`). Never commit secrets; `state/` is ignored.
- MiroFish model: `python -m valedia_trader.agents.mirofish_models use <profile>` switches it; `add <name> --lmstudio-key KEY [--weights F.gguf --tokenizer DIR]` registers another model (pinned GGUF = native; otherwise HTTP backends only), `status` lists them. A native candidate must be prepared again after switching.
- Backtests: `python -m valedia_trader.backtest.walkforward`. Every configuration evaluated is recorded in `artifacts/trials.sqlite`; do not delete it, the Deflated Sharpe gate depends on the true trial count.
- No live orders without the owner's explicit confirmation.
