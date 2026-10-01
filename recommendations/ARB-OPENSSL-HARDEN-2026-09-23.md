# ARB-OPENSSL-HARDEN — 2026-09-23

## Goal
Stop LATENCY ARB (`arb_run.py`) from SIGSEGV (exit 139) after ~15min dry-run, which surfaces as macOS “Python quit unexpectedly.”

## Confirmed cause
- Cron: `*/30 * * * * .../cron_runner_mac.sh arb_run.py "LATENCY ARB"`
- Path cron runs: `~/.openclaw/workspace/scripts/integrations/arb_run.py` via cron_runner
- Crash: EXC_BAD_ACCESS at 0x0 in OpenSSL `SSL_CTX_load_verify_locations` / `pthread_rwlock_wrlock`
- Report: `~/Library/Logs/DiagnosticReports/Python-2026-09-23-131516.ips` (twin 2026-09-18)
- Log: `~/.openclaw/logs/valedia-cron.log` → `[LATENCY ARB] exit=139`

## Fix
1. Load verify locations / build SSLContext **once** on main thread; reuse Session across polls.
2. No concurrent cert-store mutation from worker threads.
3. cron_runner: treat exit 139/134 for LATENCY ARB as known TLS crash — clear log, no blind multi-retry storm.
4. Keep DRY-RUN; no --live / orders / wallet.

## Fence
Code/audit only. Do not flip trading env. Do not touch oil/weather/treasury. Preserve screens ot-valedia-desk / valedia-glance.

## Done when
- Patch shipped + short note in recommendations/
- Smoke: parallel HTTPS without reloading verify locations
- GROK_EXIT:0

## Shipped 2026-09-23 (Grok Build)

Build tree `workspace-v2` **0.69.0** (working tree, not committed, not tagged). Live cron tree received the three files only (`latency_arb.py`, `arb_run.py`, `cron_runner_mac.sh`). Live `VALEDIA_VERSION` stays **0.44.0**.

- `https_session()` builds `SSLContext` + `requests.Session` once on the main thread (`arb_run.py` and `run()` before the poller).
- `_PinnedTLSAdapter.cert_verify` clears `ca_certs` so urllib3 does not `SSL_CTX_load_verify_locations` on every GET.
- Poller uses `sess.get`; `arb_run.py` stays `mode='dry-run'`.
- `cron_runner_mac.sh`: LATENCY ARB exit 139/134 logs `known OpenSSL/TLS crash — no retry`, replaces output with `[openssl-tls-crash]`, does not diagnose/retry.
- Smoke: `test_arb_openssl_harden.py` PASS (8 parallel HTTPS, `load_verify_locations` count unchanged). `test_slice22.py` PASS.

No `--live`. No orders. No wallet. Oil/weather untouched. Screens `ot-valedia-desk` / `valedia-glance` / `valedia-grok` left running.
