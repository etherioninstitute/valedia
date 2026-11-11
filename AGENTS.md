# Working in Valedia

Before architecture analysis or code changes, run from this repository:

```sh
python3 tools/repo_map.py ensure
python3 tools/repo_map.py check
```

Read `.repo-map/current.json` once, then read compact `navigation.md` inside its named `.repo-map/generations/<generation>/` directory. Keep that generation path for the session. Query only the relevant modules, symbols and dependents from `modules.json` using context-mode or the README query example; do not dump the whole index into context. `repomix.xml` is an optional compact file tree. The map prints relative paths. After changes, run ensure and check again. Never trust cached context solely because a watcher is running. For release review, `check` must exit zero after `ensure`; there is no active CI/precommit integration in this recovered tree (no `.git`).

See `docs/repository-map/README.md` for installation, optional foreground watch and query examples, and `docs/repository-map/ARCHITECTURE.md` for curated subsystem roles, including existing MiroFish. Module/symbol/import facts are static evidence. Curated roles and prior scoped validation are separately labeled. This map is not a behavioral, dynamic-call-graph, security or profitability audit. Do not infer that an import executes, that an external dependency is installed, or that a trading path is operational.

Repository-map work must not import or execute trading modules or read state/ledger/credential data. The map excludes state, models, caches, secret-looking files, special files and symlinks. Default Repomix output contains file navigation only; explicitly request `pack` for a local source pack when needed. Keep secret scanning enabled and do not upload packs.
