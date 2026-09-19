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
| Whoami (institutional) | `GET /v1/whoami` on `api.prod.polymarketexchange.com` | bearer JWT | no (not this lane) | **out of scope** | Institutional FIX/gRPC host. **404 on `api.polymarket.us`.** Do not call from retail client. |
| Whoami (retail) | identity = `GET /v1/account/balances` (`whoami()` / `whoami_retail()`) | Ed25519 | yes (auth check) | **tested-mock** | Returns `{ok, source:"balances", balances:[redacted], positions_n?}`. Not a fill. |
| Account balances | `GET /v1/account/balances` | yes | yes (buying power / fee gate) | **tested-mock** | Read-only. Optional live smoke if keys present. |
| Markets list | `GET /v1/markets` | public | yes (universe) | **tested-mock** | Gateway only. Not CLOB Gamma. |
| Market by slug/id | `GET /v1/market/slug/{slug}`, `GET /v1/market/id/{id}` | public | yes (universe → one market) | **tested-mock** | POLY-LEARN sizing. Gateway only. |
| BBO | `GET /v1/markets/{slug}/bbo` | public | yes (price/size) | **tested-mock** | `sizing_from_bbo`. Not a fill. |
| Book | `GET /v1/markets/{slug}/book` | public | yes (depth) | **tested-mock** | Prefer BBO for paper size. |
| Settlement, price history | `GET /v1/markets/{slug}/settlement`, `GET /v1/price-history` | public | later | **missing** | YAGNI until live writer. |
| Events list | `GET /v1/events` | public | yes | **tested-mock** | `tagSlug` supported. |
| Event by slug/id | `GET /v1/events/...` | public | later | **missing** | |
| Search | `GET /v1/search` | public | discovery | **stub** (method present) | |
| Sports (leagues/events) | `GET /v1/sports` + sports-by-slug | public | sports max-profit | **stub** (`list_sports`) | Nav uses tag `sports`. |
| Tags + events-for-tag | `GET /v1/tags`, `GET /v2/tags/{slug}/events` | public | **yes** (category nav) | **tested-mock** | Sports/Politics/Crypto/Weather/Tech/Culture/Econ. |
| Series | `GET /v1/series` | public | later | **missing** | |
| Order preview | `POST /v1/order/preview` body `{request: order}` | yes | **yes** (dry-run before live) | **tested-mock** | **Not a create. Not a fill.** Unwrapped body 400s (`Request is required`). |
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

**Retail vs institutional:** `api.polymarket.us` + Ed25519 Key ID/Secret. `GET /v1/whoami` and `POST /v1/trading/orders/preview` belong to `api.prod.polymarketexchange.com` (bearer JWT). Retail preview is `POST /v1/order/preview`. Retail identity is balances.

**Max-profit path (US, still fenced):** public tag-nav + markets → preview → (Burton live later) create → GET order/trade id → fee-true FTL. Writer flip is **not** this slice. Preview dry-run: `scripts/integrations/poly_us_smoke.py` (`POLYMARKET_US_HTTP=1`, `POLYMARKET_US_LIVE=0`).

**Do not confuse with Int'l:** CLOB `POST /order` + Gamma `/markets` in `polymarket_client.py` / `polymarket_order_body.py`. US uses `marketSlug` + `ORDER_INTENT_*`, not CLOB `tokenID`.
