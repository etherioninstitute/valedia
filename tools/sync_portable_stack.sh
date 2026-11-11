#!/bin/bash
# Link this machine to the portable Hermes + Valedia stack that lives on the external SSD.
# The whole Hermes home (config, skills, plugins, MCP definitions, memory) already sits on the SSD,
# so "installing" on another Mac is three links and a check. Dry run by default; pass --apply to act.
# Nothing is ever overwritten: an existing path that points elsewhere is reported and left alone.
set -u
SSD="${VALEDIA_SSD:-/Volumes/NIMROD_SSD}"
APPLY=0; [ "${1:-}" = "--apply" ] && APPLY=1
say() { printf '%s\n' "$*"; }
link() { # link <target> <link path>
  local target="$1" path="$2"
  if [ ! -e "$target" ]; then say "MISSING  $target (cannot link $path)"; return 1; fi
  if [ -L "$path" ] && [ "$(readlink "$path")" = "$target" ]; then say "OK       $path -> $target"; return 0; fi
  if [ -e "$path" ] || [ -L "$path" ]; then say "CONFLICT $path exists and is not a link to $target; left untouched"; return 1; fi
  if [ "$APPLY" = 1 ]; then mkdir -p "$(dirname "$path")" && ln -s "$target" "$path" && say "LINKED   $path -> $target"
  else say "WOULD    link $path -> $target"; fi
}
[ -d "$SSD" ] || { say "SSD not mounted at $SSD (set VALEDIA_SSD to override)"; exit 2; }
status=0
link "$SSD/Hermes/home" "$HOME/.hermes" || status=1
link "$SSD/Hermes/bin/hermes" "$HOME/.local/bin/hermes" || status=1
[ -f "$SSD/Hermes/home/.env" ] && [ "$(stat -f '%Lp' "$SSD/Hermes/home/.env" 2>/dev/null || stat -c '%a' "$SSD/Hermes/home/.env")" != "600" ] && { say "WARN     $SSD/Hermes/home/.env is not mode 600"; status=1; }
for item in "Codex/Workspaces/valedia-skill-system/manifest.json" "Qwen-Weight-Space/valedia-trader/pyproject.toml" "Hermes/home/config.yaml" "Hermes/home/skills"; do
  [ -e "$SSD/$item" ] && say "PRESENT  $SSD/$item" || { say "MISSING  $SSD/$item"; status=1; }
done
if [ "$APPLY" = 1 ] && command -v hermes >/dev/null 2>&1; then hermes doctor 2>&1 | grep -E '✗|⚠' | head -10; fi
[ "$APPLY" = 0 ] && say "(dry run; re-run with --apply to create the links)"
exit $status
