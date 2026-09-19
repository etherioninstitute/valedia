# Polymarket US API matrix (POLY-US)

**Canon:** https://docs.polymarket.us/ (llms.txt + getting-started + api-reference).
**Account label:** `valedia.ai@gmail.com` (label only; no keys in git).
**SDK shape:** `POLYMARKET_KEY_ID` + `POLYMARKET_SECRET_KEY` (`polymarket-us`). **Not** Int'l CLOB wallet/`py-clob-client`.
**Hosts:** auth `https://api.polymarket.us` · public `https://gateway.polymarket.us`.
**Code:** `scripts/integrations/polymarket_us_client.py` (US DCM). Int'l Gamma/CLOB remains `polymarket_client.py`.
**Fence:** `POLYMARKET_US_LIVE` default False. `VENUE_WRITER["polymarket"]` stays `none`. `LIVE_CLIENT_HOOK` stays `None`. No live create/cancel.

Fill-true (H3 / POLY-E habit): **preview ≠ create**; **create ack ≠ fill**. Reconcile with GET by exchange `order id` / `trade id`. Empty/wrong-id GET stays `UNRECONCILED`.

| API | Method / path | Auth | Needed for max-profit | Valedia status | Fill-true notes |
|-----|---------------|------|----------------------|----------------|-----------------|
| Auth (Key ID + Secret, Ed25519 headers) | `X-PM-Access-Key` / `Timestamp` / `Signature` | yes | yes (orders + portfolio) | **tested-mock** | Keys from env only. Missing keys → mock HTTP. |
| Whoami | `GET /v1/whoami` | yes | identity check | **stub** (wired; retail vs institutional host may 404) | Not a fill. |
| Account balances | `GET /v1/account/balances` | yes | yes (buying power / fee gate) | **tested-mock** | Read-only. Optional live smoke if keys present. |
| Markets list | `GET /v1/markets` | public | yes (universe) | **tested-mock** | Gateway only. Not CLOB Gamma. |
| Market by slug/id, BBO, book, settlement, price history | `GET /v1/markets/...` | public | yes (price/size) | **missing** (list only this slice) | Add when sizing US live. |
| Events list | `GET /v1/events` | public | yes | **tested-mock** | `tagSlug` supported. |
| Event by slug/id | `GET /v1/events/...` | public | later | **missing** | |
| Search | `GET /v1/search` | public | discovery | **stub** (method present) | |
| Sports (leagues/events) | `GET /v1/sports` + sports-by-slug | public | sports max-profit | **stub** (`list_sports`) | Nav uses tag `sports`. |
| Tags + events-for-tag | `GET /v1/tags`, `GET /v2/tags/{slug}/events` | public | **yes** (category nav) | **tested-mock** | Sports/Politics/Crypto/Weather/Tech/Culture/Econ. |
| Series | `GET /v1/series` | public | later | **missing** | |
| Order preview | `POST /v1/order/preview` | yes | **yes** (dry-run before live) | **tested-mock** | **Not a create. Not a fill.** |
| Create order | `POST /v1/orders` | yes | live path only | **tested-mock, LIVE-gated** | Ack `{id}` ≠ fill. `POLYMARKET_US_LIVE` default False. |
| Get order | `GET /v1/order/{orderId}` | yes | **yes** (reconcile) | **tested-mock** | FILLED/`cumQuantity`/`tradeId` on GET only. |
| Open orders | `GET /v1/orders/open` | yes | yes (open risk) | **tested-mock** | |
| Cancel order | `POST /v1/order/{orderId}/cancel` | yes | live path only | **tested-mock, LIVE-gated** | Cancel ack ≠ fill/void until GET. |
| Modify / batch create-cancel-modify | `/modify`, `/orders` batch | yes | later | **missing** | YAGNI until live writer. |
| Close position | `POST /v1/order/close-position` | yes | later | **missing** | Write. Do not call. |
| Portfolio positions | `GET /v1/portfolio/positions` | yes | **yes** | **tested-mock** | Positions ≠ fills. |
| Activities | `GET /v1/portfolio/activities` | yes | reconcile / P&amp;L | **stub** (method present) | Match `tradeId`/`orderId`. |
| Private WebSocket | `wss://api.polymarket.us/v1/ws/private` | yes | live fills faster | **missing** | Constants only. Poll GET until Burton. |
| Markets WebSocket | `wss://api.polymarket.us/v1/ws/markets` | public/auth | book | **missing** | |
| Combos / RFQ / incentives / institutional FIX/gRPC | various | mixed | not this lane | **missing** | Out of scope. |

**Max-profit path (US, still fenced):** public tag-nav + markets → preview → (Burton live later) create → GET order/trade id → fee-true FTL. Writer flip is **not** this slice.

**Do not confuse with Int'l:** CLOB `POST /order` + Gamma `/markets` in `polymarket_client.py` / `polymarket_order_body.py`. US uses `marketSlug` + `ORDER_INTENT_*`, not CLOB `tokenID`.
