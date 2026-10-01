# OKX-PHASE1-STATUS

**Updated:** 2026-09-27
**Slice:** OKX-SUBSUME-1 Phase 1
**GROK_EXIT:** 0

## Fence
`OKX_LIVE` unset. `VALEDIA_TRADING_HALT` unset. Halt file absent (`trading_halted` 0). No create, cancel, transfer, or withdraw. No wallet. Credentials were not printed.

## Exit IP
SOCKS `socks5h://127.0.0.1:1080` (tunnel already listening) exits **85.239.234.32** on IPv4 (`api4.ipify.org`). The same tunnel's path to ifconfig.me returns the VPS IPv6, which is not the OKX allowlist. OKX also has AAAA, so the client connects to the A record through the tunnel. A hostname-only SOCKS call would leave via v6 and miss the whitelist.

## Read smoke
`python3 scripts/integrations/okx_client.py summary` exit 0. Host `us.okx.com`. `www.okx.com` returned `50119` (US account domain).

```
OKX portfolio exit_ip=85.239.234.32
  trading USD eq=60 avail=60 nonzero
  funding: empty
  open_orders=0
```

Public ticker on the same path: BTC-USDT last 83514.6 (read).

## Keys
`~/.openclaw/okx_keys.json` mode `600`. Materializer stdout was `ok=1 path=/Users/burtonchatelain/.openclaw/okx_keys.json mode=600`. The json is outside the git repo.

## Tests
`python3 scripts/integrations/test_okx_client.py`: 7 passed, no network. `OKX_LIVE=0` and unset refuse `create_order` / `cancel_order` before HTTP. `VALEDIA_TRADING_HALT=1` blocks even when `OKX_LIVE=1` (env restored after the test; halt file not written). Fake secrets are absent from `repr`. CLI `--live` exits 2.

## Stack
Native REST v5. CBM found no Valedia `import ccxt` (the package is not installed). PySocks 1.7.1 installed with `python3 -m pip install --user PySocks` so `socks5h` works on system Python 3.9.6.

## Skipped
`portfolio_monitor.check_kraken` was the injection point. Wired 2026-09-27 as `check_okx` (see OKX-MONITOR-STATUS.md). No Cloudflare. No Davidd bridge. No live order.
