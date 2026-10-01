#!/bin/bash
# VAL-11 silent-failure fail-closed wrapper (paper/ops). Empty artifact ≠ PASS.
set -euo pipefail
exec /usr/bin/env python3 "$(dirname "$0")/valedia_silent_failure_probes.py" "$@"
