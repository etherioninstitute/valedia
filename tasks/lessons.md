# lessons.md — Valedia Self-Learning Log
# Format: [YYYY-MM-DD] | what went wrong | rule to follow next time
# Read this file at the start of every session. Apply every rule before doing anything.

[2026-03-13] | Model specified without provider prefix | Always use "ollama/qwen3:32b" not "qwen3:32b" in openclaw.json
[2026-03-13] | Stuck delivery queue retrying indefinitely | Check ~/.openclaw/delivery-queue/ on startup and clear stale entries older than 1 hour
[2026-03-14] | Heartbeat used Tailscale IP without SSH key | Always use direct VPS IP 85.239.234.32 with -i ~/.ssh/openclaw_vps for all SSH to VPS
[2026-03-14] | Hyperliquid removed but still referenced in signal_fusion.py | When removing a platform, grep all integration files for references before marking complete
[2026-03-15] | Added "extensions" key to openclaw.json — invalid schema | Never add keys to openclaw.json without verifying via "openclaw config validate" first
[2026-03-15] | openclaw-engram cloned but never built — caused "escapes package directory" crash | Do not add a plugin to extensions/ unless it is fully built and has a valid openclaw.plugin.json
[2026-03-15] | Used ./dist/index.js (with ./) in package.json openclaw.extensions — triggered security check | Use "dist/index.js" (no leading ./) in openclaw.extensions array
[2026-03-15] | yfinance 404 errors spamming Signal reports for new coins | Always use CoinGecko for crypto price history; suppress yfinance stderr with contextlib.redirect_stderr
[2026-03-15] | unified_brain.py had "if True:" indentation artifact after patch | After any programmatic patch to a Python file, always run py_compile.compile() to verify
[2026-03-16] | launchctl bootstrap failed because plist was already loaded | Always run bootout before bootstrap when restarting gateway; check launchctl list first
2026-03-17 | Cron runner was dumping raw errors+404s to Signal | Built self-healing cron runner v2: auto-diagnoses, auto-fixes (pip, tunnel, retry), filters noise, logs lessons
2026-03-17 | Self-audit: Hit rate 100.0% is strong — maintain current approach | Continue current approach
2026-03-17 | Self-audit: Thursday win rate 100.0% — increase position sizes | Continue current approach
2026-03-17 | Self-audit: Friday win rate 100.0% — increase position sizes | Continue current approach
2026-03-17 | Self-audit: Sunday win rate 100.0% — increase position sizes | Continue current approach
2026-03-18 | Self-audit: Hit rate 100.0% is strong — maintain current approach | Continue current approach
2026-03-18 | Self-audit: Thursday win rate 100.0% — increase position sizes | Continue current approach
2026-03-18 | Self-audit: Friday win rate 100.0% — increase position sizes | Continue current approach
2026-03-18 | Self-audit: Sunday win rate 100.0% — increase position sizes | Continue current approach
2026-03-19 | Self-audit: Hit rate 100.0% is strong — maintain current approach | Continue current approach
2026-03-19 | Self-audit: Thursday win rate 100.0% — increase position sizes | Continue current approach
2026-03-19 | Self-audit: Friday win rate 100.0% — increase position sizes | Continue current approach
2026-03-19 | Self-audit: Sunday win rate 100.0% — increase position sizes | Continue current approach
2026-03-20 | Self-audit: Hit rate 100.0% is strong — maintain current approach | Continue current approach
2026-03-20 | Self-audit: Friday win rate 100.0% — increase position sizes | Continue current approach
2026-03-20 | Self-audit: Sunday win rate 100.0% — increase position sizes | Continue current approach
2026-03-21 | Self-audit: Hit rate 100.0% is strong — maintain current approach | Continue current approach
2026-03-21 | Self-audit: Thursday win rate 100.0% — increase position sizes | Continue current approach
2026-03-21 | Self-audit: Friday win rate 100.0% — increase position sizes | Continue current approach
2026-03-21 | Self-audit: Sunday win rate 100.0% — increase position sizes | Continue current approach
2026-03-22 | Self-audit: Hit rate 100.0% is strong — maintain current approach | Continue current approach
2026-03-22 | Self-audit: Thursday win rate 100.0% — increase position sizes | Continue current approach
2026-03-22 | Self-audit: Friday win rate 100.0% — increase position sizes | Continue current approach
2026-03-22 | Self-audit: Sunday win rate 100.0% — increase position sizes | Continue current approach
2026-03-23 | Self-audit: Hit rate 100.0% is strong — maintain current approach | Continue current approach
2026-03-23 | Self-audit: Thursday win rate 100.0% — increase position sizes | Continue current approach
2026-03-23 | Self-audit: Friday win rate 100.0% — increase position sizes | Continue current approach
2026-03-23 | Self-audit: Sunday win rate 100.0% — increase position sizes | Continue current approach
2026-03-24 | Self-audit: Hit rate 100.0% is strong — maintain current approach | Continue current approach
2026-03-24 | Self-audit: Thursday win rate 100.0% — increase position sizes | Continue current approach
2026-03-24 | Self-audit: Friday win rate 100.0% — increase position sizes | Continue current approach
2026-03-24 | Self-audit: Sunday win rate 100.0% — increase position sizes | Continue current approach
2026-03-25 | Self-audit: Hit rate 100.0% is strong — maintain current approach | Continue current approach
2026-03-25 | Self-audit: Thursday win rate 100.0% — increase position sizes | Continue current approach
2026-03-25 | Self-audit: Friday win rate 100.0% — increase position sizes | Continue current approach
2026-03-25 | Self-audit: Sunday win rate 100.0% — increase position sizes | Continue current approach
2026-03-26 | Self-audit: Hit rate 100.0% is strong — maintain current approach | Continue current approach
2026-03-26 | Self-audit: Thursday win rate 100.0% — increase position sizes | Continue current approach
2026-03-26 | Self-audit: Friday win rate 100.0% — increase position sizes | Continue current approach
2026-03-26 | Self-audit: Sunday win rate 100.0% — increase position sizes | Continue current approach
2026-03-27 | Self-audit: Hit rate 100.0% is strong — maintain current approach | Continue current approach
2026-03-27 | Self-audit: Thursday win rate 100.0% — increase position sizes | Continue current approach
2026-03-27 | Self-audit: Friday win rate 100.0% — increase position sizes | Continue current approach
2026-03-27 | Self-audit: Sunday win rate 100.0% — increase position sizes | Continue current approach
2026-03-28 | Self-audit: Hit rate 100.0% is strong — maintain current approach | Continue current approach
2026-03-28 | Self-audit: Thursday win rate 100.0% — increase position sizes | Continue current approach
2026-03-28 | Self-audit: Friday win rate 100.0% — increase position sizes | Continue current approach
2026-03-28 | Self-audit: Sunday win rate 100.0% — increase position sizes | Continue current approach
2026-03-29 | Self-audit: Hit rate 100.0% is strong — maintain current approach | Continue current approach
2026-03-29 | Self-audit: Thursday win rate 100.0% — increase position sizes | Continue current approach
2026-03-29 | Self-audit: Friday win rate 100.0% — increase position sizes | Continue current approach
2026-03-29 | Self-audit: Sunday win rate 100.0% — increase position sizes | Continue current approach
2026-06-09 | SPORTS ENGINE: SOCKS tunnel died | Auto-restarted SSH tunnel
2026-06-09 | SPORTS ENGINE: SOCKS tunnel died | Auto-restarted SSH tunnel
2026-08-11 | MORNING BRIEF: SOCKS tunnel died | Auto-restarted SSH tunnel
2026-08-26 | SPORTS ENGINE: SOCKS tunnel died | Auto-restarted SSH tunnel
2026-08-26 | SPORTS ENGINE: SOCKS tunnel died | Auto-restarted SSH tunnel

## ⚠️ RETRACTION (2026-09-09, Claude Code + SDA HOLDS)
ALL prior "Hit rate 100.0% — increase position sizes / maintain approach" lessons above
(2026-03-17 onward) are RETRACTED as INVALID. Root cause: (1) a W/WIN counting bug —
self_audit wrote result="WIN" while unified_brain counted result=="W", so one reader saw
0% and the other 100%; (2) the resolver scored WIN/LOSS by comparing entry to a price
fetched at audit time (no fixed horizon, no fee), not a real settled exit. The 100% was an
artifact, never real. Do NOT increase position sizes on the basis of those entries.
Real realized record is NEGATIVE (pnl_tracker net -$12.62; sports executed-only -$62.66).
Standing rule going forward: no accuracy claim or sizing change is admissible until the
fee-inclusive, fill-reconciled forward-test ledger (forward_test_ledger.py) shows it.

[2026-09-17] | Plan said do not auto-unblacklist weather/oil | Burton override: a blacklist is a pause, not a grave. Re-enable evaluation/sizing when TTL expires or fee-true net recovers. Do not re-arm Slice 8 live writers (weather create_order / scalper alp_buy). Pause on net, never WR after 3 trades.
