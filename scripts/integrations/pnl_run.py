"""P&L Tracker — Cron Wrapper (11pm)
Tracks Kalshi settlements, writes learning pauses (TTL/evidence, not graves),
runs local AI audit (log only), then sends Burton the verified money ping:
  Invested = Kalshi open-position cost (broker, re-read to verify)
  Profit   = Realized P&L from forward_test_ledger (fee-incl., re-read to verify)
Any mismatch / stale broker -> "Report withheld" instead (signal_verify).
Broker freshness uses the venue's own timestamp when it sends one (else the footer
says "broker time unknown"). Ledger staleness = newest row of any kind: quiet for
signal_verify.LEDGER_STALE_OPEN_S (36h) with positions open, or LEDGER_STALE_ANY_S
(7d) regardless, or no timestamps at all -> "⚠ STALE" label, never plain verified.
WR flags are report-only and do not feed Kelly.
"""
import sys, os
from datetime import timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import signal_verify as sv


def pnl_report(get_positions=None, ledger=None, send_fn=None, now=None):
    """11pm P&L: Invested (broker) + Profit (ledger realized, fee-incl.)."""
    kind, now = "11pm P&L", now or sv.utcnow()
    ledger = ledger or sv.read_ledger
    try:
        recs, snap = ledger(), sv.broker_snapshot(get_positions, now)
        recs2, snap2 = ledger(), sv.broker_snapshot(get_positions, now)  # independent re-reads
    except Exception as e:
        return sv.withhold(kind, "source read failed", e, send_fn, now)
    real, real2 = sv.ledger_realized(recs), sv.ledger_realized(recs2)
    day, cut = now.astimezone().date(), now - timedelta(hours=24)  # cron is 11pm local
    last = lambda rows: [s for s in rows  # noqa: E731 — settled in the last 24h
                         if (sv.parse_ts(s.get("logged_at") or s.get("ts")) or cut) > cut]
    today, today2 = last(real["settlements"]), last(real2["settlements"])
    t_pnl = sum(sv._f(s.get("realized_pnl_net")) for s in today)
    body = (f"💰 Valedia P&L — {day.isoformat()}\n"
            f"Invested: {snap['invested']:,.2f} (Kalshi open-position cost, broker)\n"
            f"Profit: {real['total']:+,.2f} Realized (ledger, fee-incl.) "
            f"{real['wins']}W/{real['losses']}L · last 24h {t_pnl:+,.2f}")
    claim = sv.claim
    claims = [
        claim("Invested", snap["invested"], snap2["invested"], "broker re-read"),
        claim("open positions", snap["n"], snap2["n"], "broker re-read", tol=0),
        claim("Profit", real["total"], real2["total"], "ledger re-read"),
        sv.sum_claim("Profit total", real["total"], real2["parts"]),
        claim("Profit last 24h", t_pnl, sum(sv._f(s.get("realized_pnl_net")) for s in today2),
              "ledger re-read"),
        claim("wins", real["wins"], real2["wins"], "ledger re-read", tol=0),
        claim("losses", real["losses"], real2["losses"], "ledger re-read", tol=0),
    ]
    notes, open_n = [], sv.ledger_open_kalshi(recs2)
    if open_n != snap2["n"]:
        notes.append(f"ledger has {open_n} open Kalshi entries vs broker {snap2['n']} positions"
                     " (not reconciled)")
    bfresh, bfoot = sv.broker_fresh(snap)
    lts = sv.ledger_latest(recs2)
    lfresh = [sv.fresh("ledger", lts or "1970-01-01T00:00:00+00:00",
                       sv.LEDGER_STALE_OPEN_S if snap2["n"] else sv.LEDGER_STALE_ANY_S,
                       policy="label")]
    return sv.deliver(kind, body, claims, bfresh + lfresh,
                      footer=[f"ledger {real['rows']} settled rows"
                              + (f", last row {sv.parse_ts(lts):%Y-%m-%d %H:%M} UTC" if lts
                                 else ", ledger time unknown"),
                              bfoot],
                      notes=notes, send_fn=send_fn, dedupe_key=f"pnl:{day}",
                      dedupe_window_s=20 * 3600, now=now)


def main():
    try:
        from pnl_tracker import run
        print(run())  # full tracking + local log (verbose, not sent)
    except Exception as e:
        print(f"[pnl_run] tracker failed (report still verified from broker+ledger): {e}")
    try:
        res = pnl_report()
        print(f"[pnl_run] Signal {res['status']} sent={res['sent']}: {res['text']}")
    except Exception as e:
        print(f"[pnl_run] Signal report failed: {e}")


if __name__ == "__main__":
    main()
