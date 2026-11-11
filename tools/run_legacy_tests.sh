#!/bin/bash
# Run every legacy test file (src/test_*.py, tools/test_*.py) as a script and under pytest,
# inside a sandbox that denies outbound network and writes outside the repo and temp dirs.
# usage: tools/run_legacy_tests.sh [out.tsv]   -> columns: file, script exit code, pytest summary, script last line
set -u
REPO="$(cd "$(dirname "$0")/.." && pwd)"; OUT="${1:-$REPO/artifacts/legacy_tests.tsv}"
mkdir -p "$(dirname "$OUT")"; : > "$OUT"; cd "$REPO" || exit 2
# Some legacy tests drive the real kill-switch CLI, which writes state/halt*.json. Restore what was there.
HALT_BEFORE=0; [ -e state/halt.json ] && HALT_BEFORE=1
one() {
  f="$1"; d=$(dirname "$f"); b=$(basename "$f")
  common=(env -u VALEDIA_LIVE_OPT_IN -u OKX_LIVE -u POLYMARKET_US_LIVE -u WEATHER_LIVE PYTHONDONTWRITEBYTECODE=1 sandbox-exec -f "$REPO/tools/legacy_tests.sb")
  s_out=$( (cd "$d" && perl -e 'alarm 150; exec @ARGV' "${common[@]}" "$REPO/.venv/bin/python" "$b") 2>&1 | tail -1 | tr '\t\n' '  ' | cut -c1-110)
  s_rc=$( (cd "$d" && perl -e 'alarm 150; exec @ARGV' "${common[@]}" "$REPO/.venv/bin/python" "$b") >/dev/null 2>&1; echo $?)
  p_out=$(perl -e 'alarm 200; exec @ARGV' "${common[@]}" "$REPO/.venv/bin/python" -m pytest "$f" -o addopts="" -o testpaths="" -p no:cacheprovider --timeout=120 -q 2>&1 | tail -1 | tr '\t\n' '  ' | cut -c1-90)
  printf '%s\t%s\t%s\t%s\n' "$f" "$s_rc" "$p_out" "$s_out"
}
export -f one; export REPO
ls src/test_*.py tools/test_*.py | xargs -P 1 -I{} bash -c 'one "$@"' _ {} >> "$OUT"
[ "$HALT_BEFORE" = 0 ] && rm -f state/halt.json state/halt-ping.json
awk -F'\t' '{n++; if($2==0)ok++; if(match($3,/[0-9]+ passed/)){split(substr($3,RSTART,RLENGTH),a," "); p+=a[1]}} END{printf "legacy: %d files, %d pass as scripts, %d pytest tests passed\n", n, ok, p}' "$OUT"
