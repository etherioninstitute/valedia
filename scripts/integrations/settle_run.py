"""Settlement Checker — Valedia ASI
Checks pending bets for settlement. Sends Signal ONLY when profit/loss realized.
(c) GOATWORTHY Post LLC
"""
import sys, os, json
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
MODELS = os.path.expanduser("~/.openclaw/workspace/models")

def settle_sports():
    os.environ.setdefault('KALSHI_API_KEY_ID', '86a77487-629d-43d7-a5c0-0058bd8f899a')
    os.environ.setdefault('KALSHI_PRIVATE_KEY_PATH', '/Users/burtonchatelain/.openclaw/kalshi_private_key.pem')
    from kalshi_auth import get

    log_path = os.path.join(MODELS, "sports_engine_log.json")
    with open(log_path) as f:
        log = json.load(f)

    new_settlements = []
    for bet in log['bets']:
        if bet.get('settled') or bet.get('status') not in ('EXECUTED',):
            continue
        ticker = bet.get('fav_ticker', '')
        if not ticker:
            continue
        try:
            r = get(f'/markets/{ticker}')
            m = r.get('market', r)
            result = m.get('result', '')
            if result in ('yes', 'no'):
                won = (result == 'yes')
                pnl = bet['profit_if_win'] if won else -(bet['cost'] + bet.get('fee', 0.0))
                bet['settled'] = True
                bet['result'] = result
                bet['won'] = won
                bet['pnl'] = round(pnl, 2)
                new_settlements.append(bet)
        except:
            pass

    if new_settlements:
        settled = [b for b in log['bets'] if b.get('settled') and b.get('status') == 'EXECUTED']
        wins = sum(1 for b in settled if b.get('won'))
        losses = len(settled) - wins
        net = sum(b.get('pnl', 0) for b in settled)
        log['stats'] = {
            'total_bets': len(settled), 'wins': wins, 'losses': losses,
            'win_rate': round(wins / len(settled) * 100, 1) if settled else 0,
            'net_pnl': round(net, 2),
            'updated': datetime.now(timezone.utc).isoformat(),
        }
        with open(log_path, 'w') as f:
            json.dump(log, f, indent=2)

    return new_settlements, log.get('stats', {})

def _stamp():
    try:
        from valedia_config import version_stamp
        return version_stamp()
    except Exception:
        return ""


def fee_true_settlements(settlements, entries):
    """FTL settlements that may appear as Profit:. UNRECONCILED/paper/backfill/shadow excluded."""
    import forward_test_ledger as ftl
    out, seen = [], set()
    for s in settlements:
        if s.get("type") != "settlement" or s.get("entry_id") in seen:
            continue  # one settlement per entry (first wins), like ftl._settled_ids
        seen.add(s.get("entry_id"))
        e = entries.get(s.get("entry_id"))
        if not e:
            continue
        if ftl._is_non_real_source(s.get("source")) or ftl._is_non_real_source(e.get("source")):
            continue
        if str(e.get("fill_status") or "").upper() == "UNRECONCILED":
            continue
        if str(s.get("fill_status") or "").upper() == "UNRECONCILED":
            continue
        out.append(s)
    return out


def _pl(v):
    """signal_verify.money (half away, no -$0.00, real minus) + "(net loss)" on a loss."""
    import signal_verify as sv
    m = sv.money(v, signed=True)
    return m + (" (net loss)" if m.startswith("−") else "")


def format_settled_signal(new_pnl, w, l, total_net, total_record):
    """Keep Profit: prefix (signal-protocol.md). Body is fee-true FTL only."""
    st = _stamp()
    tail = f"\n{st}" if st else ""
    return (f"Profit: {_pl(new_pnl)} ({w}W/{l}L settled) Realized (ledger, fee-incl.)\n"
            f"Total: {_pl(total_net)} ({total_record}) Realized (ledger, fee-incl.){tail}")


def format_arb_signal(arb):
    """LOG ONLY. latency_arb's arb-log is not the fee-true ledger (no fee on
    losses, potential_profit on wins) and its 'settled' count is cumulative, so
    it is never sent to Signal as Profit."""
    return (f"arb-log (not FTL, not sent): net {arb['net_pnl']:+.2f} "
            f"({arb['wins']}W/{arb['losses']}L cumulative)")


def ftl_profit_signal(recs=None, since=None, minutes=50):
    """Profit: body from fee-true FTL rows. None if nothing admissible (incl. UNRECONCILED)."""
    p = ftl_profit_parts(recs, since, minutes)
    if not p:
        return None
    return format_settled_signal(p["new_pnl"], p["w"], p["l"], p["total_net"],
                                 f"{p['tw']}W/{p['tl']}L")


def ftl_profit_parts(recs=None, since=None, minutes=50):
    """Figures behind the Profit: ping (fee-true FTL rows). None if nothing new."""
    import forward_test_ledger as ftl
    import signal_verify as sv
    recs = recs if recs is not None else ftl._read()
    entries = {r["id"]: r for r in recs if r.get("type") == "entry" and r.get("id")}
    setts = [r for r in recs if r.get("type") == "settlement"]
    all_true = fee_true_settlements(setts, entries)
    # bad: missing/None/NaN/inf/garbage P&L, non-boolean win flag, or a live orphan
    ok = lambda s: sv.num(s.get("realized_pnl_net")) is not None and sv.won_flag(s.get("won")) is not None  # noqa: E731
    orphans = {s.get("entry_id") for s in setts if s.get("entry_id") not in entries
               and not ftl._is_non_real_source(s.get("source"))}
    bad = sum(1 for s in all_true if not ok(s)) + len(orphans)
    all_true = [s for s in all_true if ok(s)]
    new = all_true
    if since:
        new = [s for s in new if (s.get("logged_at") or s.get("ts") or "") > since]
    elif minutes:
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
        kept = []
        for s in new:
            raw = s.get("logged_at") or s.get("ts") or ""
            try:
                ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            except Exception:
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts >= cutoff:
                kept.append(s)
        new = kept
    if not new and not bad:
        return None
    w = sum(1 for s in new if sv.won_flag(s.get("won")))
    tw = sum(1 for s in all_true if sv.won_flag(s.get("won")))
    return {"new": new, "new_pnl": sum(sv.num(s.get("realized_pnl_net")) for s in new),
            "w": w, "l": len(new) - w, "bad": bad,
            "total_net": sum(sv.num(s.get("realized_pnl_net")) for s in all_true),
            "tw": tw, "tl": len(all_true) - tw,
            "cursor": max([(s.get("logged_at") or s.get("ts") or "") for s in new] or [since or ""])}


def profit_ping(parts, body, ledger=None, send_fn=None, now=None):
    """Verified 'Profit:' ping: every figure recomputed from an independent ledger
    re-read (signal_verify.ledger_realized). Any unparseable / orphan row -> withheld."""
    import signal_verify as sv
    kind, now = "Profit", now or sv.utcnow()
    try:
        real = sv.ledger_realized((ledger or sv.read_ledger)())
    except Exception as e:
        return sv.withhold(kind, "ledger read failed", e, send_fn, now)
    new_ids = {s.get("entry_id") for s in parts["new"]}
    key = "profit:" + ",".join(sorted(str(i) for i in new_ids))
    if parts.get("bad") or real["bad"]:
        return sv.deliver(kind, "", problems=[{"code": "ledger settlement rows unparseable",
                                               "detail": [parts.get("bad"), real["bad"]]}],
                          send_fn=send_fn, now=now, dedupe_key=key, dedupe_window_s=7 * 86400)
    new2 = [s for s in real["settlements"] if s.get("entry_id") in new_ids]
    claim = sv.claim
    claims = [
        claim("Profit", parts["new_pnl"], sum(sv.num(s.get("realized_pnl_net")) for s in new2),
              "ledger re-read"),
        claim("settled count", len(parts["new"]), len(new2), "ledger re-read", tol=0),
        claim("wins", parts["w"], sum(1 for s in new2 if sv.won_flag(s.get("won"))),
              "ledger re-read", tol=0),
        claim("Total", parts["total_net"], real["total"], "ledger re-read"),
        sv.sum_claim("Total", parts["total_net"], real["parts"]),
        claim("total wins", parts["tw"], real["wins"], "ledger re-read", tol=0),
    ]
    stamps = [s.get("logged_at") or s.get("ts") for s in parts["new"]]
    oldest = min((t for t in stamps if sv.parse_ts(t)), key=sv.parse_ts, default=None)
    return sv.deliver(kind, body, claims, [sv.fresh("settlement", oldest, 6 * 3600, policy="label")],
                      footer=[f"ledger {real['rows']} settled rows", sv.hhmm(now)],
                      send_fn=send_fn, now=now, dedupe_window_s=7 * 86400, dedupe_key=key)


def run(send_fn=None):
    try:
        new_sports, stats = settle_sports()
        if new_sports:
            print(f"sports-log view: {len(new_sports)} settled (not FTL, not Signal)")
    except Exception as e:
        print(f"[settle_run] sports-log settle failed (Signal uses FTL only): {e}")

    cursor_path = os.path.join(MODELS, "settle_ftl_signal_cursor.txt")
    since = None
    try:
        since = open(cursor_path).read().strip() or None
    except Exception:
        since = None
    try:
        parts = ftl_profit_parts(since=since, minutes=None if since else 50)
        if parts:
            body = format_settled_signal(parts["new_pnl"], parts["w"], parts["l"],
                                         parts["total_net"], f"{parts['tw']}W/{parts['tl']}L")
            res = profit_ping(parts, body, send_fn=send_fn)
            print(f"[settle_run] Signal {res['status']} sent={res['sent']}")
            # Advance only past rows Burton actually got (or already got) — never skip one.
            if res["status"] == "dedupe" or (res["status"] == "verified" and res["sent"]):
                try:
                    with open(cursor_path, "w") as f:
                        f.write(parts["cursor"])
                except Exception:
                    pass
    except Exception as e:
        print(f"[settle_run] FTL Profit ping failed: {e}")

    # Arb settlements: arb-log is not FTL -> local log only, never a Profit ping.
    try:
        from latency_arb import check_arb_settlements
        arb = check_arb_settlements()
        if arb and arb.get('settled', 0) > 0:
            print(format_arb_signal(arb))
    except Exception:
        pass

if __name__ == "__main__":
    run()
