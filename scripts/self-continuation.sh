#!/bin/bash
# Valedia self-continuation CLI (Flux P0-3). Paper/ops only — no live/orders.
set -euo pipefail
exec /usr/bin/env node "$(dirname "$0")/self-continuation.js" "$@"
