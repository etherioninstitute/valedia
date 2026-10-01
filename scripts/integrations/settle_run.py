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
    out = []
    for s in settlements:
        if s.get("type") != "settlement":
            continue
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


def format_settled_signal(new_pnl, w, l, total_net, total_record):
    """Keep Profit: prefix (signal-protocol.md). Body is fee-true FTL only."""
    st = _stamp()
    tail = f"\n{st}" if st else ""
    return (f"Profit: {new_pnl:+.2f} ({w}W/{l}L settled) Realized (ledger, fee-incl.)\n"
            f"Total: {total_net:+.2f} ({total_record}) Realized (ledger, fee-incl.){tail}")


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
    recs = recs if recs is not None else ftl._read()
    entries = {r["id"]: r for r in recs if r.get("type") == "entry" and r.get("id")}
    all_true = fee_true_settlements(
        [r for r in recs if r.get("type") == "settlement"], entries)
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
    if not new:
        return None
    w = sum(1 for s in new if s.get("won"))
    tw = sum(1 for s in all_true if s.get("won"))
    return {"new": new, "new_pnl": sum(float(s.get("realized_pnl_net") or 0) for s in new),
            "w": w, "l": len(new) - w,
            "total_net": sum(float(s.get("realized_pnl_net") or 0) for s in all_true),
            "tw": tw, "tl": len(all_true) - tw,
            "cursor": max((s.get("logged_at") or s.get("ts") or "") for s in new)}


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
            import signal_verify
            body = format_settled_signal(parts["new_pnl"], parts["w"], parts["l"],
                                         parts["total_net"], f"{parts['tw']}W/{parts['tl']}L")
            res = signal_verify.profit_ping(parts, body, send_fn=send_fn)
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
