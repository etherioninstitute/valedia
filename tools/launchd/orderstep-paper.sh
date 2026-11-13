#!/bin/zsh
# Weekdays, twice after the open (owner's standing go for paper-trading validation; installed 2026-11-13):
# run the order step against Webull's PAPER account. The command itself refuses anything but the test
# environment, anything outside 09:35 to 15:45 New York, and holidays. The second run of a day journals the
# first run's fills and sends only what is still missing.
#
# It runs from the main tree, and only when every existing file of the package is byte-for-byte the accepted
# revision named in accepted-orderpath-commit (the pick-grading module aside, which is not on the order path);
# otherwise the day's run is skipped and says so. Prints a short summary, never an id or a key.
set -u
R=/Volumes/NIMROD_SSD/Qwen-Weight-Space/valedia-trader
DD=/Volumes/NIMROD_SSD/Qwen-Weight-Space/valedia-deploy
PY=$R/.venv/bin/python
stamp() { date -u "+%Y-%m-%dT%H:%M:%SZ"; }

if [ ! -x "$PY" ]; then echo "$(stamp) not ready (the SSD is not mounted): nothing was run"; exit 0; fi
ACC=$(cat "$DD/accepted-orderpath-commit" 2>/dev/null)
if [ -z "$ACC" ] || ! git -C "$R" diff --quiet --diff-filter=MDRT "$ACC" -- valedia_trader ':(exclude)valedia_trader/research_loop/pick_grading.py' 2>/dev/null; then
  echo "$(stamp) skipped: the tree's order path is not the accepted revision (${ACC:-none named})"; exit 0
fi
cd "$R" || exit 0
export PYTHONDONTWRITEBYTECODE=1
OUT=$("$PY" -m valedia_trader.research_loop order-step 2>&1); CODE=$?
print -r -- "$OUT" | "$PY" -c 'import sys, json
text = sys.stdin.read()
try:
    d = json.loads(text)
    keep = {k: d.get(k) for k in ("status", "stage", "reason", "orders_sent", "fills_journaled", "broker_equity")}
    keep["sent"] = [(o.get("symbol"), o.get("side"), o.get("units")) for o in d.get("sent") or []]
    keep["refused"] = len(d.get("refused") or []); keep["left_over"] = len(d.get("left_over") or [])
    print(keep)
except Exception:
    print(text.strip().splitlines()[-1][:300] if text.strip() else "(no output)")'
echo "$(stamp) order step (paper) ended with exit $CODE"
