#!/bin/zsh
# Hourly (owner's go 2026-11-13): collect the public pick lists through Hermes' own Reddit skill, parse the new
# comments with the local model, refresh the wiki, then store the schedule and match picks to games.
# Reads no result of any game. Prints counts only, never comment text.
#
# Collection runs from a pinned copy of accepted code ($D). The grading stages run from the main tree, and only
# when the tree's grading code is byte-for-byte the accepted revision named in accepted-grading-commit; otherwise
# they are skipped for that hour (a builder's candidate may be sitting in the tree).
set -u
R=/Volumes/NIMROD_SSD/Qwen-Weight-Space/valedia-trader
DD=/Volumes/NIMROD_SSD/Qwen-Weight-Space/valedia-deploy
D=$DD/picks
PY=$R/.venv/bin/python
ST=$R/memory/pick_lists.sqlite
WIKI=/Volumes/NIMROD_SSD/Hermes/home/wiki/sbpotd
LOCK=$DD/picks-hourly.lock
stamp() { date -u "+%Y-%m-%dT%H:%M:%SZ"; }

if [ ! -x "$PY" ] || [ ! -f "$ST" ] || [ ! -d "$D/valedia_trader" ]; then
  echo "$(stamp) not ready (the SSD, the pick store or the pinned copy is missing): nothing was run"; exit 0
fi
if ! mkdir "$LOCK" 2>/dev/null; then
  if [ -n "$(find "$LOCK" -maxdepth 0 -mmin +120 2>/dev/null)" ]; then
    rmdir "$LOCK" 2>/dev/null; mkdir "$LOCK" 2>/dev/null || { echo "$(stamp) another run is going: skipped"; exit 0; }
  else
    echo "$(stamp) another run is going: skipped"; exit 0
  fi
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT
export PYTHONDONTWRITEBYTECODE=1
echo "$(stamp) start"

cd "$D" || exit 0
P() { PYTHONPATH="$D" "$PY" -m valedia_trader.data_and_fees.pick_lists "$@"; }
P ingest --store "$ST" 2>&1 | grep -E "^(requests|threads followed|new threads|new comment versions|error)" | cut -c1-200

if "$PY" -c 'import urllib.request,sys
try:
    urllib.request.urlopen("http://127.0.0.1:1235/v1/models", timeout=5).read()
except Exception:
    sys.exit(1)'
then
  P parse --store "$ST" 2>&1 | grep -E "^(parsed|unparsed|empty|failed|set aside)" | cut -c1-200
  P export --store "$ST" --wiki "$WIKI" 2>&1 | tail -1 | cut -c1-200
else
  echo "the local model server does not answer: parse and wiki skipped this hour"
fi
P status --store "$ST" --verify 2>&1 | grep -E "^(comments|parsed|unparsed|set aside|times out of order|bodies whose hash differs)" | cut -c1-200

ACC=$(cat "$DD/accepted-grading-commit" 2>/dev/null)
FILES=(valedia_trader/research_loop/pick_grading.py valedia_trader/research_loop/sports_lines.py
       valedia_trader/venues/odds_api.py valedia_trader/venues/kalshi_history.py valedia_trader/venues/kalshi_data.py
       valedia_trader/data_and_fees/pick_lists.py valedia_trader/core/secrets.py)
if [ -n "$ACC" ] && git -C "$R" diff --quiet "$ACC" -- $FILES 2>/dev/null; then
  cd "$R" || exit 0
  G() { "$PY" -m valedia_trader.research_loop.pick_grading "$@"; }
  if G schedule >/dev/null 2>"$DD/picks-hourly.err"; then echo "schedule stored"; else echo "schedule failed (see picks-hourly.err)"; fi
  G match 2>>"$DD/picks-hourly.err" | "$PY" -c 'import sys,json
try:
    d=json.load(sys.stdin); print("match:", {k: d.get(k) for k in ("picks", "by_outcome", "rows_written")})
except Exception:
    print("match failed (see picks-hourly.err)")'
else
  echo "grading skipped this hour: the tree's grading code is not the accepted revision (${ACC:-none named})"
fi
echo "$(stamp) end"
