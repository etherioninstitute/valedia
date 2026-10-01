"""P&L Tracker — Cron Wrapper (11pm)
Tracks Kalshi settlements, writes learning pauses (TTL/evidence, not graves),
runs local AI audit (log only), then sends Burton the verified money ping:
  Open-position cost (Kalshi, broker) — re-read to verify
  Realized P&L (all-time, ledger, all venues, fee-incl.) — re-read to verify; omitted
    with a notice when any ledger row is unparseable / conflicting
  Settled last 24h (ledger) — omitted when any settlement lacks a usable time
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


ALL_TIME = "Realized P&L (all-time, ledger, all venues, fee-incl.)"


def settled_at(s):
    """Settlement time: the row's own settlement field, else when it was booked.
    Missing / non-string / garbage -> None (the 24h figure is then omitted)."""
    raw = s.get("settled_at") or s.get("settlement_ts") or s.get("ts") or s.get("logged_at")
    return sv.parse_ts(raw) if isinstance(raw, str) else None


def pnl_report(get_positions=None, ledger=None, send_fn=None, now=None):
    """11pm P&L: Invested (broker) + Profit (ledger realized, fee-incl.). Never sends
    nothing: any unexpected error -> one withheld notice."""
    now = now or sv.utcnow()
    try:
        return _pnl_report(get_positions, ledger, send_fn, now)
    except Exception as e:
        return sv.withhold("11pm P&L", "report failed", f"{type(e).__name__}: {e}", send_fn, now)


def _pnl_report(get_positions, ledger, send_fn, now):
    kind = "11pm P&L"
    ledger = ledger or sv.read_ledger
    try:
        recs, snap = ledger(), sv.broker_snapshot(get_positions, now)
        recs2, snap2 = ledger(), sv.broker_snapshot(get_positions, now)  # independent re-reads
    except Exception as e:
        return sv.withhold(kind, "source read failed", e, send_fn, now)
    real, real2 = sv.ledger_realized(recs), sv.ledger_realized(recs2)
    day, cut = now.astimezone().date(), now - timedelta(hours=24)  # cron is 11pm local
    claim = sv.claim
    claims = [claim("Open-position cost", snap["invested"], snap2["invested"], "broker re-read"),
              claim("open positions", snap["n"], snap2["n"], "broker re-read", tol=0)]
    lines = [f"💰 Valedia P&L — {day.isoformat()}",
             f"Open-position cost (Kalshi, broker): {sv.money(snap['invested'])}"]
    bad = max(real["bad"], real2["bad"])
    if bad:  # garbage / NaN / orphan / conflicting duplicate rows: never summed, never 0.00
        what = "conflicting duplicate settlement" if real["conflict"] else "unparseable"
        lines += [f"{ALL_TIME}: omitted — {bad} ledger row(s) {what}",
                  "Settled last 24h (ledger): omitted — ledger rows unparseable"]
        claims.append(claim("bad rows", bad, bad, "ledger re-read", tol=0))
    else:
        prof = sv.money(real["total"], signed=True)
        lines.append(f"{ALL_TIME}: {prof}{' (net loss)' if prof.startswith('−') else ''}"
                     f" · {real['wins']}W/{real['losses']}L")
        claims += [claim("Realized", real["total"], real2["total"], "ledger re-read"),
                   sv.sum_claim("Realized total", real["total"], real2["parts"]),
                   claim("wins", real["wins"], real2["wins"], "ledger re-read", tol=0),
                   claim("losses", real["losses"], real2["losses"], "ledger re-read", tol=0)]
        untimed = sum(1 for s in real["settlements"] if settled_at(s) is None)
        if untimed:  # never a verified 0.00 for rows we cannot place in time
            lines.append(f"Settled last 24h (ledger): omitted — {untimed} row(s) lack times")
            claims.append(claim("untimed rows", untimed, untimed, "ledger re-read", tol=0))
        else:
            last = lambda rows: [s for s in rows if cut < settled_at(s) <= now]  # noqa: E731
            t1, t2 = (sv.dsum(sv.num(s.get("realized_pnl_net")) for s in last(r["settlements"]))
                      for r in (real, real2))
            lines.append(f"Settled last 24h (ledger): {sv.money(t1, signed=True)}")
            claims.append(claim("last 24h", t1, t2, "ledger re-read"))
    body = "\n".join(lines)
    notes, open_n = [], sv.ledger_open_kalshi(recs2)
    if open_n != snap2["n"]:
        notes.append(f"ledger has {open_n} open Kalshi entries vs broker {snap2['n']} positions"
                     " (not reconciled)")
    bfresh, bfoot = sv.broker_fresh(snap)
    lts, future = sv.ledger_latest(recs2, now)
    if future or not lts:  # suspect / unknown ledger time -> STALE, never plain verified
        body = "⚠ STALE: ledger " + ("has future-dated rows (suspect)" if future
                                     else "time unknown") + "\n" + body
    lfresh = [] if (future or not lts) else [sv.fresh(
        "ledger", lts, sv.LEDGER_STALE_OPEN_S if snap2["n"] else sv.LEDGER_STALE_ANY_S,
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
