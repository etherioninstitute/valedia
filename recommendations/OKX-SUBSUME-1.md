# OKX-SUBSUME-1 — Valedia OKX venue (Contabo IP lock)

**Burton:** 2026-09-27 ~21:46 PT — wire OKX into Valedia with ~$60 account risk.  
**Executor:** Grok Build (`workspace-v2`, RuFlo `:8810`, CBM, Ponytail). Valedia Trader = architect / audit / relay.  
**Fence for this Build slice:** Implement + **read-only smoke** only. **No** `create_order` / transfer / withdraw in this slice. Default `OKX_LIVE=0`. Live orders are a later Valedia runtime flip after keys + SOCKS prove green. Grok Build / Grok Bot never place live venue orders from this lane.

## Secret materialization (never print / commit / log values)
Source files (ProtonDrive; already present):
- API Key: `/Users/burtonchatelain/Library/CloudStorage/ProtonDrive-burtworthy@protonmail.com-folder/VPS/API Keys/OKX API Key.txt`
- API Sec Key: `/Users/burtonchatelain/Library/CloudStorage/ProtonDrive-burtworthy@protonmail.com-folder/VPS/API Keys/OKX API Sec Key.txt`
- API Passphrase: `/Users/burtonchatelain/Library/CloudStorage/ProtonDrive-burtworthy@protonmail.com-folder/VPS/API Keys/OKX Passphrase.txt`

Canonical runtime file (mirror Kraken): `~/.openclaw/okx_keys.json`  
Shape: `{"api_key":"...","secret":"...","passphrase":"..."}` chmod `600`. Strip whitespace/newlines when reading the `.txt` files. Fall back env: `OKX_API_KEY` / `OKX_SECRET` / `OKX_PASSPHRASE` (or `OKX_API_SECRET`). Never write secrets into workspace git, STATUS, jsonl, or stdout.

## IP allowlist truth
OKX whitelist = public egress only. Contabo: **`85.239.234.32`**. Do **not** use Tailscale `100.x`. Mac calls must go through SOCKS `socks5h://127.0.0.1:1080` (`vps_proxy` / LaunchAgent `com.goatworthy.valedia.vpstunnel`). Fail closed if tunnel exit IP ≠ `85.239.234.32`.

## Code shape (cleanest = Kraken + Coinbase patterns)
1. `scripts/integrations/okx_client.py` (dual-home workspace + workspace-v2)
   - Load keys from `~/.openclaw/okx_keys.json` (fallback env).
   - Prefer **CCXT** `okx` if importable; else native OKX REST v5. Prove choice with CBM `search_graph` / existing deps first — do not invent a third stack.
   - Always set proxies via `vps_proxy.get_proxies()` on Darwin (same as `coinbase_trader` / Kraken).
   - Reads: `get_balance` / `portfolio_summary`, `get_ticker`, `get_open_orders` (read).
   - Writes (implemented but gated): `create_order` / `cancel_order` require `OKX_LIVE=1` **and** `valedia_config.require_not_halted(...)`. Default refuse. Never withdraw.
   - CLI: `python3 scripts/integrations/okx_client.py summary` (read). `--live` must no-op / exit non-zero in this slice.
2. Materialize keys once: small helper or one-shot in STATUS docs — `scripts/integrations/okx_materialize_keys.py` that reads the three Proton paths → `okx_keys.json`, prints only `ok=1 path=... mode=600` (no secret bytes).
3. Smoke (read-only): ensure tunnel → exit IP check → balance summary with **masked** asset lines (amounts OK; never keys). Record in `recommendations/OKX-PHASE1-STATUS.md`.
4. Update `recommendations/ADAPT-DOSSIERS/ccxt.md` §5: OKX is the **exception** Burton unlocked for Valedia live risk later; research-only still default for other CCXT venues.
5. Optional thin hook: portfolio_monitor / cockpit can call `okx_client.portfolio_summary` later — only if CBM shows an existing clean injection point; YAGNI otherwise this slice.
6. Tests: offline unit tests with mocked transport for gate (`OKX_LIVE=0` cannot create_order; halt blocks; secrets absent from repr/str). No network in unit tests.
7. Ponytail after code. `GROK-STATUS.md` end-to-end. Dual-home charter + client + STATUS.

## Explicit non-goals this slice
- Live market buy/sell / any create_order against the $60
- Withdraw / deposit / sub-account transfer
- Cloudflare / public bind
- DaviddTech → OKX bridge
- Changing Kalshi/Poly live fences
- Printing passphrase or keys

## Done when
- `okx_keys.json` exists mode 600; materializer does not echo secrets
- `okx_client.py` dual-homed; SOCKS forced; read smoke green via Contabo IP
- create_order path exists but refuses unless `OKX_LIVE=1` + not halted; Build run leaves `OKX_LIVE` unset/0
- Tests green offline; Ponytail filed; GROK_EXIT:0


## Deps note (Mac Studio check 2026-09-27)
- System `python3` 3.9.6: `requests` present; **PySocks missing** (SOCKS via requests fails until installed). Homebrew python 3.14 also lacks PySocks/ccxt.
- Prefer **native OKX REST v5** (no new mega-dep) unless CBM shows an existing Valedia ccxt install path. If CCXT is chosen, pin a user install and document the interpreter.
- Build may `python3 -m pip install --user PySocks` (and `ccxt` only if chosen). Prove SOCKS exit IP before private calls.

## Phase 1 result (2026-09-27)
Read smoke green. See `recommendations/OKX-PHASE1-STATUS.md`. Native REST v5 on `us.okx.com` (`www.okx.com` returns 50119 for this US key). `OKX_LIVE` left unset. Trading balance USD 60, funding empty, open orders 0. No order sent.

