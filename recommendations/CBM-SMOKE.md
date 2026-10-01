# CBM-SMOKE — 2026-09-18

**Verdict: PASS**

Grok Build MCP `codebase-memory` is connected (stdio). Config: `.grok/config.toml` → `[mcp_servers.codebase-memory]` command `/Users/burtonchatelain/.openclaw/tools/cbm/codebase-memory-mcp`. `index_repository` not run (projects already present).

## Tools seen (this session)

`list_projects`, `index_repository`, `search_graph`, `get_architecture`, `get_code_snippet`, `trace_path`, `query_graph`, `check_index_coverage`, `index_status`, `search_code`, `detect_changes`, `get_graph_schema`, `manage_adr`, `ingest_traces`, `delete_project` (15).

## Project

`Users-burtonchatelain-.openclaw-workspace-v2`  
root `/Users/burtonchatelain/.openclaw/workspace-v2`  
branch `valedia-v2-grok-build`  
~41385 nodes / ~65974 edges (list_projects `include_details=true`).

Also indexed (not used as H3 cwd): `valedia-scripts`, `valedia-workspace`, `…-kalshi-agent`, `…-OpenTerminal`, nautilus, hypergrok, ai-fund-inspect.

## Real query snippet

`search_graph` query=`kalshi_executor sports_engine master_allocator` on that project (BM25, format=json):

```
kalshi_executor.execute_trade     Function  scripts/integrations/kalshi_executor.py     343-392
kalshi_executor.run               Function  scripts/integrations/kalshi_executor.py     508-643
master_allocator.execute_allocations Function scripts/integrations/master_allocator.py 359-555
master_allocator.allocate         Function  scripts/integrations/master_allocator.py     277-330
sports_engine.execute_bets        Function  scripts/integrations/sports_engine.py        257-300
sports_engine.run                 Function  scripts/integrations/sports_engine.py        374-489
```

Follow-up `search_graph` also hit `forward_test_ledger.extract_kalshi_fill` (253-296) and `book_kalshi_fill` (299-324). `get_architecture` returned 41392 nodes, Python 373 files, hotspot `kalshi_auth.get`. Coverage on the six H3 paths: `no_recorded_issue`.

## FAIL would have been

MCP missing / list_projects empty with index failure / graph miss of the three Kalshi-path symbols. None of those happened.
