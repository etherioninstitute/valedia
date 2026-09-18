#!/bin/bash
# Print Valedia version+sha+branch+dirty for cron/Signal headers.
# Usage: scripts/valedia_version.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CFG="$ROOT/scripts/integrations/valedia_config.py"
VER="$(grep -E '^VALEDIA_VERSION[[:space:]]*=' "$CFG" | head -1 | sed -E 's/.*"([^"]+)".*/\1/')"
SHA="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
BRANCH="$(git -C "$ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
if git -C "$ROOT" diff --quiet 2>/dev/null && git -C "$ROOT" diff --cached --quiet 2>/dev/null; then
  DIRTY=clean
else
  DIRTY=dirty
fi
echo "v${VER} ${SHA} ${BRANCH} ${DIRTY}"
