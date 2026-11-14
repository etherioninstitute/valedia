#!/bin/zsh
# Nightly backup of the trader's stores to a third disk (owner's yes, 2026-11-13: "yes to the nightly backup").
# Runs the backup tool from a pinned copy at the revision accepted for use; reads the main tree, writes only
# under the destination. Says so and does nothing when either disk is not mounted.
set -u
R=/Volumes/NIMROD_SSD/Qwen-Weight-Space/valedia-trader
TOOL=/Volumes/NIMROD_SSD/Qwen-Weight-Space/valedia-deploy/backup/tools/backup.py
DEST=/Volumes/18TB/Backups/valedia-trader
PY=$R/.venv/bin/python
stamp() { date -u "+%Y-%m-%dT%H:%M:%SZ"; }
if [ ! -x "$PY" ] || [ ! -f "$TOOL" ]; then echo "$(stamp) not ready (the SSD is not mounted): no backup made"; exit 0; fi
if [ ! -d /Volumes/18TB ] || [ ! -d "$DEST" ]; then echo "$(stamp) not ready (the backup disk is not mounted): no backup made"; exit 0; fi
export PYTHONDONTWRITEBYTECODE=1
OUT=$(nice -n 15 "$PY" "$TOOL" run --root "$R" --dest "$DEST" 2>&1); CODE=$?
print -r -- "$OUT" | grep -E "^backup " | tail -n 1
print -r -- "$OUT" | grep -c -E "^FAILED " | sed 's/^/files named as failed: /'
NEW=$(ls -1 "$DEST" 2>/dev/null | grep -E '^[0-9]{8}T[0-9]{6}Z$' | tail -n 1)
if [ -n "$NEW" ]; then nice -n 15 "$PY" "$TOOL" verify "$DEST/$NEW" >/dev/null 2>&1; echo "verify of $NEW exit $?"; fi
echo "$(stamp) backup run ended with exit $CODE"
