# DAVIDDTECH-SUBSUME-1 — TradingView DaviddTech → Valedia paper/shadow desk

**Burton:** 2026-09-27 ~18:55 PT — subsume DaviddTech Algorithmic Trading Architecture into Valedia as **paper/shadow intel only**.  
**Source PDF:** `~/Downloads/DaviddTech Algorithmic Trading Architecture.pdf` (11pp blueprint: three TV engines + FastAPI webhook + CCXT live — Valedia takes the **signal contract**, not the live path).  
**Fence:** `DAVIDD_LIVE=0` (and never set). Mode `PAPER/SHADOW`. **Valedia sole live risk** (Kalshi / Poly / crypto-paper shadow). No `--live`, no CCXT create_order, no Cloudflare tunnel, no Qwen approve gate in this slice.  
**Keys / passphrase:** `DAVIDDTECH_WEBHOOK_PASSPHRASE` (alias `TRADINGVIEW_WEBHOOK_PASSPHRASE`) in `~/.openclaw/.env` only. **Do not** print, log, or commit the value. Never store passphrase in jsonl.

## Goal
Wire DaviddTech / TradingView alert payloads into Valedia as a **first-class paper/shadow venue**:
1. Charter + Phase 0 inventory of the PDF engines and payload contract.
2. Pine/TV paper checklist (Boilerplate v2.9.4, three engines, alert JSON template) — operator work; Valedia does not invent Pine.
3. Localhost FastAPI webhook that **only** verifies passphrase + schema and appends `data/daviddtech-paper/decisions.jsonl` with `create_order=false` always.
4. Hard gate: **no** exchange orders, **no** CCXT, **no** public bind, **no** Cloudflare, **no** Qwen calls — unless a later Burton-documented unlock (this slice never unlocks).


## STEER (2026-09-27 20:09 PT) — Phase 1 to Grok Build
**Executor:** Grok Build (`workspace-v2`). Valedia Trader stopped Bot-side Phase 1 worker to save tokens.
**Jackson MCP:** `~/tradingview-mcp-jackson`, CDP `TV_CDP_PORT=9333`. Chart/Pine only.
**Scope:** webhook smoke + Pine stub + PHASE1-STATUS + Ponytail. No Cloudflare. No live orders.

## STEER (2026-09-27 18:56 PT) — Grok Build owns implementation
**Executor:** Grok Build (`workspace-v2`, RuFlo `:8810`, CBM, Ponytail). Valedia Trader = architect / audit / relay only (see `GROK-BUILD-LANE.md`).
**Do not** treat prior Bot-side file drops as the finished Build slice — Grok Build must audit, Ponytail, fix gaps, update `GROK-STATUS.md`, and own any later networking (Cloudflare/Nginx) when Burton unlocks it.
**Primary job:** DaviddTech is **intel/shadow ears** for Valedia. TradingView alerts hit `127.0.0.1` only; decisions are hypothetical. Volume from TV is **contracts for research**, never live size. Kelly ignores these rows (`counts_toward_kelly=false`).

## Phase 0 — inventory (PDF)
| Engine | strategy_id example | TF | Role |
|---|---|---|---|
| Trend-Momentum Anchor (Waddah / Deadzone / QQE / McGinley) | `Waddah_1H_Anchor` | 1H | Higher-TF trend anchor |
| Trendhoo mid engine | `Trendhoo_45M` | 45M | Mid-TF stabilizer |
| Top-G scalper (G-Channel + HEMA Volume Flow) | `TopG_5M_Scalp` | 5M | HF scalp |

Shared: DaviddTech **Boilerplate v2.9.4** (inverse flip, global MDD, session filter, RiskMTip, kill switch). PDF Phase 2/3 also describe Qwen parse → CCXT → Cloudflare — **explicit non-goals** for Valedia this slice.

**Payload keys (contract):** `passphrase`, `timestamp` (ISO), `strategy_id`, `ticker`, `action` (`entry`\|`exit`\|`tp1`\|`tp2`\|`breakeven`), `subaccount` (optional), `entry_price`, `volume`, `portfolio_risk` (optional).

## Phase 1 — Pine / TradingView paper checklist (operator)

Status 2026-09-27: Phase 1d compiled paper scripts for all three `strategy_id`s. Waddah uses the PDF inputs. Trendhoo and TopG use the PDF gates, allocation, and (for TopG) the written entry rule, with lengths the PDF does not publish marked as public stand-ins. Commercial Boilerplate v2.9.4 source is not in the workspace. No saved TV script. No public webhook.
- [ ] Build/compile Pine on **Boilerplate v2.9.4 by DaviddTech**
- [ ] Wire three engines (or unified master) with distinct `strategy_id`s above
- [ ] `strategy.entry` / `strategy.exit` `alert_message` emits exact JSON template (Phase 0 keys)
- [ ] TV **paper trading** alerts only; webhook URL stays **off public net** until a later unlock (this slice: localhost curl / TV paper pointing nowhere public)
- [ ] Confirm passphrase lives in `.env` only; never in Pine source committed to git

## Phase 2 — localhost webhook (this slice — done when green)
Paths (primary workspace `~/.openclaw/workspace`):
- Desk: `scripts/integrations/daviddtech_paper.py`
- Server: `scripts/integrations/daviddtech_webhook_server.py`
- Tests: `scripts/integrations/test_daviddtech_paper.py`
- Log: `data/daviddtech-paper/decisions.jsonl`
- Desk note: `recommendations/DAVIDDTECH-PAPER.md`

Routes (bind **127.0.0.1** only; port `DAVIDDTECH_WEBHOOK_PORT` default **8787**):
- `GET /health` → `{ok, mode: PAPER/SHADOW, create_order: false}`
- `POST /tradingview-to-webhook-order` → passphrase check → validate → append decision → `{ok, create_order: false, decision}`

If `VALEDIA_TRADING_HALT` is on: **still accept and log** paper decisions (shadow ears on) with band/reason noting halt; never create orders.

## Explicit non-goals
- CCXT live / testnet create_order
- Cloudflare tunnel / public exposure in this paper slice (Grok Build owns networking later when Burton unlocks)
- Qwen / LM Studio approve gate on the webhook path
- Setting `DAVIDD_LIVE=1` or any `--live` flag
- Treating TV `volume` as live USD size
- Replacing Kalshi/Poly as primary live prediction path
- Committing secrets; echoing passphrase in STATUS/log/jsonl

## Hard fence
`DAVIDD_LIVE=0`. Paper/shadow only. Valedia is sole live risk brain. See `recommendations/KILL-SWITCH.md`. CCXT remains **research-only** per `recommendations/ADAPT-DOSSIERS/ccxt.md`.

## Tools
Offline unit tests (no network). FastAPI if importable (already on Mac); else document stdlib fallback. Grok Build executes this slice end-to-end; Valedia relays status.

## Done when
- Charter + optional `DAVIDDTECH-PAPER.md` written
- Module + FastAPI server + tests green offline
- One fixture jsonl line with `create_order=false`, `live=false`, no passphrase stored
- Bad/missing passphrase → 401; schema invalid → 422
- `DAVIDD_LIVE` / live flags cannot force `create_order` true
- **Next = Burton stop** (parent may queue Pine checklist / dual-home sync later)

## Results
- 2026-09-27 — Grok Build executor (`workspace-v2`) audited architect draft and closed charter gaps.
- Passphrase omitted from jsonl (key dropped; stuffed keys stripped; write refuses `"passphrase":`).
- `hmac.compare_digest` on the webhook. `bind_host()` allows `127.0.0.1` only.
- Default `python3` (3.9.6) has no FastAPI; listen path is stdlib `http.server` (`--stdlib` or auto-fallback).
- Offline tests: **15 pass, 1 skip** (FastAPI TestClient missing). `create_order` false; halt still logs; `DAVIDD_LIVE=1` cannot force orders.
- Fixture `data/daviddtech-paper/decisions.jsonl` has `create_order=false`, `live=false`, no passphrase field.
- Ponytail: `PONYTAIL-AUDIT-DAVIDDTECH-SUBSUME-1.md`.
- **GROK_EXIT:0**. Next = Phase 1 Pine checklist (operator). Cloudflare/tunnel OUT until Burton unlocks.
- 2026-09-27 — Phase 1 (Grok Build): `DAVIDDTECH-PINE-PAPER-STUB.pine` dual-homed (`Waddah_1H_Anchor`, subaccount `paper`, passphrase sentinel only). `pine_check` compiled. Editor inject skipped (Monaco did not open); no alert created. `127.0.0.1:8787` is Hermes WebUI, so the desk was smoked on `127.0.0.1:8788`. jsonl `create_order=false`, no passphrase field. Tests 16 pass, 1 skip. See `DAVIDDTECH-PHASE1-STATUS.md`. Tunnel OUT.
- 2026-09-27 — Phase 1c (Grok Build): EMA stand-in replaced with a paper-shaped Boilerplate subset for `Waddah_1H_Anchor`. `pine_set_source` + `pine_check` (0 errors, 0 warnings). Not added to the chart. No alert. No webhook. `DAVIDD_LIVE=0`. Tunnel OUT.
- 2026-09-27 — Phase 1d (Grok Build): `DAVIDDTECH-PINE-TRENDHOO-45M-PAPER.pine` and `DAVIDDTECH-PINE-TOPG-5M-PAPER.pine` dual-homed. `pine_check` 0/0 each. Editor restored to the Waddah stub. Alerts 0. Chart unchanged. No webhook. `DAVIDD_LIVE=0`. Tunnel OUT. Operator port in desk docs is 8788 while Hermes holds 8787. Code constant stays 8787.
