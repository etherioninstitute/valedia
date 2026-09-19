# Ponytail audit — H3 Kalshi fill-true GET poll (0.28.0)

**Mode:** `/ponytail-audit` on the H3 diff, not the whole trading surface.
**Tree:** `~/.openclaw/workspace-v2`
**Scope:** `resolve_kalshi_fill` + sports/allocator/executor wiring, `test_h3.py`, version 0.28.0.
**Fence:** no `--live`, no venue orders, no API-key edits, no `MIROFISH_AUTO_RAISE`, no crontab.

## Applied this session

Nothing extra to delete. Helper is ~25 lines in existing FTL; three callers already booked via `book_kalshi_fill` — they now pass GET `fills_payload` when POST-ack is incomplete.

## Findings (biggest cut first)

`yagni:` Brier calibration file, Decimal money types, `dynamic_min_edge`, stale-book `no_trade` port, ESPN scores. Dossier listed them second. Fill leak was POST-ack without GET poll. **Not shipped.** Add calibration when FTL settlements exist to score.

`yagni:` New module / `KalshiFillResolver` class / retry loop / websocket. Replacement: `resolve_kalshi_fill(raw, get_fills_fn)` + existing `extract_kalshi_fill`. **Not shipped.**

`yagni:` Vendoring kalshi-agent `apis.py` / `get_order`. Their API has no `get_order`; Valedia already has `kalshi_auth.get_fills`. **Not shipped.**

`yagni:` New cron to poll UNRECONCILED later. Taker path polls once on the same cycle as POST. Maker already uses `check_and_manage` → `get_fills`. **Not shipped.**

`shrink:` Maker `_record_fill` still calls `record_entry` not `book_kalshi_fill`. Already fill-true (GET count > 0). Aligning it is a second diff. **Not this slice.**

`yagni:` Electron OMS / debate / second Kelly / weather `create_order`. Charter LEAVE. **Not shipped.**

## Not cut (correctness)

- No poll without `order_id` (would steal other fills — `extract_kalshi_fill` matches `if not order_id`).
- Empty/raising `get_fills` stays UNRECONCILED (fail-closed on fill, fail-open on gate elsewhere).
- Tests mock `get_fills`; no HTTP.

**net:** +~25 lines helper, +~15 across three callers, +one test file. 0 deps. Lean enough. Ship.
