# Grok Build status

**NORTH STAR:** world's smartest, highest-profiting DYNAMIC auto-trader.

**Updated:** 2026-09-18 PT

**Mode:** RuFlo + Ponytail. Hard fence held.

**Current:** TREASURY-1 SHIPPED. Report-only multi-venue funding. No money moved.

**Version:** `0.36.0` tag `valedia-v0.36.0` (Build). Prior: POLY-US-2 `0.35.0`.

**Tests:** `test_treasury.py` PASS (mocked balances; auto path refused). `test_slice23.py` / `test_rebalancer_shadow.py` / `test_poly_us.py` / `test_h2.py`–`test_h8.py` / `test_poly_e.py` / `test_nt1.py` / `test_nt2.py` / `test_adversarial_paper_fight.py` PASS. No network.

**Ponytail:** `recommendations/PONYTAIL-AUDIT-TREASURY-1.md`

**TREASURY-1:** Burton guide `VALEDIA-TREASURY-FUNDING.md`. `treasury_status.py` reads cash where helpers exist (Kalshi cents, Poly US balances, Coinbase USD, Alpaca cash/buying_power); HL/Kraken DOCUMENT. Floors: Poly 25, Kalshi stub 25, Coinbase 20, Alpaca 10. Emits `models/treasury_status.json`. Below floor → WARN strings only. `VALEDIA_TREASURY_AUTO=False`; `attempt_auto_topup` raises even if env=1. `TREASURY_ALERT_WEBHOOK` unused. Rebalancer stays shadow (`REBALANCER_AUTOEXEC=False`). First human fund still required.

**Fence:** No `--live`, no ACH/wire/card, no wallet/venue transfer, no API-key edits, no `VENUE_WRITER` flips, no `SPOT_RECONCILE_HTTP`, no `NAUTILUS_LIVE`, no LN, no crontab. `MIROFISH_AUTO_RAISE` False. `VALEDIA_SELF_ADJUST` False. `FORTRESS_INTEL_FALLBACK` False. `LIVE_CLIENT_HOOK` None. `POLYMARKET_US_LIVE` False. `REBALANCER_AUTOEXEC` False. `VALEDIA_TREASURY_AUTO` False.

**Next:** STOP.
