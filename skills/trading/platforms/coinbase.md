---
description: >
  Coinbase Advanced Trade — crypto execution. EC key auth, SOCKS proxy required,
  API is NOT IP-locked but was routed via VPS for home IP privacy.
---

# coinbase

Coinbase Advanced Trade API. Executes crypto buys/sells.
Current balance: ~$24.59 USD + BTC, ETH, SOL, XRP positions.

## Authentication

- Key type: EC private key (JWT-based, not HMAC)
- Key file: `/Users/burtonchatelain/.openclaw/coinbase_key.json`
- ProtonDrive backup: `VPS/API Keys/coinbase_api_key.json`
- Auth: generates ES256 JWT per request, 120-second expiry

## SOCKS proxy (why it exists)

Coinbase API is NOT IP-locked — it accepts any IP.
The proxy exists to hide Burton's home IP from Coinbase's logs.
Routes through VPS (85.239.234.32) via SOCKS5 on port 1080.

If SOCKS tunnel is down → Coinbase calls fail with `Connection refused`.
Fix: `launchctl unload/load ~/Library/LaunchAgents/com.goatworthy.valedia.vpstunnel.plist`

## Available crypto pairs (from COINBASE_KNOWN set)

BTC, ETH, SOL, XRP, ADA, DOGE, AVAX, LINK, DOT, UNI, ATOM, LTC, BCH,
MATIC, NEAR, APT, SUI, OP, ARB, PEPE, SHIB, WIF, BONK, TRUMP, HYPE, TAO,
INJ, TIA, RENDER, ONDO, SEI, and 30+ more.

## Key error patterns

| Error | Cause | Fix |
|-------|-------|-----|
| `Connection refused` | SOCKS tunnel down | Restart launchd plist |
| `401 Unauthorized` | Wrong key name in JWT | Use ProtonDrive key |
| `SOCKSHTTPSConnectionPool` | Proxy unreachable | Check VPS SSH |

## Related

- [[platforms/universe]] — which crypto assets Valedia trades
- [[core/fee-gate]] — Coinbase 0.6% taker + 0.5% spread = 1.1% effective
