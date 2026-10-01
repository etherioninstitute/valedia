#!/bin/bash
# Paper jsonl counts. Does not read or print DAVIDDTECH_WEBHOOK_PASSPHRASE.
set -euo pipefail
MOD="${DAVIDDTECH_PAPER_PY:-$HOME/.openclaw/workspace-v2/scripts/integrations/daviddtech_paper.py}"
if [[ ! -f "$MOD" ]]; then
  MOD="$HOME/.openclaw/workspace/scripts/integrations/daviddtech_paper.py"
fi
if [[ ! -f "$MOD" ]]; then
  echo "missing daviddtech_paper.py" >&2
  exit 1
fi
exec python3 "$MOD" --status "$@"
