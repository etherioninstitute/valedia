# Bionic Brave Search MCP (M1)

**Key file (normalized, mode 600):** `~/.config/brave-search/api_key`  
(source: ProtonDrive `VPS/API Keys/Brave Search API.txt`)

**App file written:** `~/.lmstudio/mcp.json` — `brave-search` via `BRAVE_API_KEY_FILE`

## Custom MCP dialog (if you prefer UI over mcp.json)

| Field | Value |
|-------|--------|
| Name | `Brave Search` |
| Connection | **On this computer** |
| Command | `npx` |
| Args (advanced, separate) | `-y` · `@brave/brave-search-mcp-server` · `--transport` · `stdio` |
| Env | `BRAVE_API_KEY_FILE` = `/Users/burtonchatelain/.config/brave-search/api_key` |
| Web URL | leave empty |

Or Program → Install → Edit mcp.json — should already show the brave-search block.

## M2

Copy the same `mcp.json` block and either sync the key file to `~/.config/brave-search/api_key` on M2 or point `BRAVE_API_KEY_FILE` at M2’s ProtonDrive copy of the same file.

## Hermes note

Hermes uses `BRAVE_SEARCH_API_KEY` in `~/.hermes/.env`. Same secret; MCP uses `BRAVE_API_KEY` / `BRAVE_API_KEY_FILE`.
