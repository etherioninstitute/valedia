# Ponytail audit — ARB-OPENSSL-HARDEN

**Mode:** `/ponytail` on the OpenSSL SIGSEGV fix.
**Fence:** dry-run only. No `--live`. No venue order. No wallet. Oil/weather untouched. Screens `ot-valedia-desk` (783), `valedia-glance` (17171), `valedia-grok` (76348) not killed.

## Applied

`reuse:` `requests.Session` + `HTTPAdapter` already installed. **Shipped** as one pinned `SSLContext` on the main thread.

`bug:` urllib3 still called `SSL_CTX_load_verify_locations` per GET because `HTTPAdapter.cert_verify` sets `conn.ca_certs=certifi`. **Shipped** `cert_verify` clears `ca_certs` after super.

## Skipped

`yagni:` `kalshi_auth.get` Session. Main-thread `requests.get` still loads CA; the poller no longer does, so the wrlock race is gone. Add when a 139 survives one cron cycle.

`yagni:` a 15-minute soak in this session. Cron `*/30` is the soak.

`yagni:` a git commit. **Not committed. Not tagged.** Live `valedia_config.py` not copied (stays 0.44.0).

## Findings

`shrink:` adapter + singleton Session. That is the load-once path.

**net:** dry-run LATENCY ARB, version `0.69.0` on Build. `create_order` not called from this slice.
