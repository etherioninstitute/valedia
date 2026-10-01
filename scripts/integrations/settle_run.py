"""Settlement Checker — Valedia ASI
Checks pending bets for settlement. Sends Signal ONLY when profit/loss realized.
(c) GOATWORTHY Post LLC
"""
import sys, os, json
from datetime import datetime, timedelta, timezone

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
    """Profit ping body (signal-protocol.md). Fee-true FTL figures only."""
    st = _stamp()
    tail = f"\n{st}" if st else ""
    return (f"Profit (settled since last report): {_pl(new_pnl)} ({w}W/{l}L) "
            f"Realized (ledger, fee-incl.)\n"
            f"Total (all-time realized, ledger): {_pl(total_net)} ({total_record}) fee-incl.{tail}")


def format_arb_signal(arb):
    """LOG ONLY. latency_arb's arb-log is not the fee-true ledger (no fee on
    losses, potential_profit on wins) and its 'settled' count is cumulative, so
    it is never sent to Signal as Profit."""
    return (f"arb-log (not FTL, not sent): net {arb['net_pnl']:+.2f} "
            f"({arb['wins']}W/{arb['losses']}L cumulative)")


def read_ledger_safe(path=None):
    """(rows, problems): parse the ledger line by line, never raising. Truncated JSON
    or a non-object row is a PROBLEM (the caller withholds and says so), never skipped
    silently."""
    import forward_test_ledger as ftl
    path = path or ftl.LEDGER
    if not os.path.exists(path):
        return [], []
    try:
        lines = open(path).read().splitlines()
    except Exception as e:
        return [], [f"ledger unreadable ({type(e).__name__})"]
    rows, problems = [], set()
    for line in lines:
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except ValueError:
            problems.add("ledger has a truncated / invalid JSON line")
            continue
        if not isinstance(r, dict):
            problems.add("ledger has a non-object row")
            continue
        rows.append(r)
    return rows, sorted(problems)


def ftl_profit_parts(recs=None, since=None, minutes=50, now=None, exclude=()):
    """Figures behind the Profit ping (fee-true FTL rows newer than `since`, else the
    last `minutes`; neither -> all), minus settlements already reported (`exclude`:
    entry ids from the sent-set). None if nothing new and nothing wrong.
    bad: missing/None/NaN/inf/garbage P&L, non-boolean win flag, unparseable row time,
    or a live orphan settlement. future: any live row dated > 5 min ahead."""
    import forward_test_ledger as ftl
    import signal_verify as sv
    now = now or sv.utcnow()
    recs = recs if recs is not None else read_ledger_safe()[0]
    entries = {r["id"]: r for r in recs if r.get("type") == "entry" and r.get("id")}
    setts = [r for r in recs if r.get("type") == "settlement"]
    all_true = fee_true_settlements(setts, entries)
    when = lambda s: sv.parse_ts(s.get("logged_at") or s.get("ts")) if isinstance(  # noqa: E731
        s.get("logged_at") or s.get("ts"), str) else None
    ok = lambda s: (sv.num(s.get("realized_pnl_net")) is not None  # noqa: E731
                    and sv.won_flag(s.get("won")) is not None and when(s) is not None)
    orphans = {s.get("entry_id") for s in setts if s.get("entry_id") not in entries
               and not ftl._is_non_real_source(s.get("source"))}
    bad = sum(1 for s in all_true if not ok(s)) + len(orphans)
    future = sv.ledger_latest(recs, now)[1]
    all_true = [s for s in all_true if ok(s)]
    since = sv.parse_ts(since) if isinstance(since, str) else since
    if since is None and minutes:
        since = now - timedelta(minutes=minutes)
    exclude = set(exclude or ())
    new = [s for s in all_true if (since is None or when(s) > since)
           and str(s.get("entry_id")) not in exclude]
    new = [s for s in new if when(s) <= now + timedelta(minutes=5)]
    if not new and not bad and not future:
        return None
    w = sum(1 for s in new if sv.won_flag(s.get("won")))
    tw = sum(1 for s in all_true if sv.won_flag(s.get("won")))
    newest = max([when(s) for s in new], default=since or now)
    return {"new": new, "new_pnl": sv.dsum(sv.num(s.get("realized_pnl_net")) for s in new),
            "w": w, "l": len(new) - w, "bad": bad, "future": future,
            "total_net": sv.dsum(sv.num(s.get("realized_pnl_net")) for s in all_true),
            "tw": tw, "tl": len(all_true) - tw,
            "cursor": min(newest, now).isoformat()}  # never past now


def ftl_profit_signal(recs=None, since=None, minutes=50):
    """Profit body from fee-true FTL rows. None if nothing admissible (incl. UNRECONCILED)."""
    p = ftl_profit_parts(recs, since, minutes)
    if not p or not p["new"]:
        return None
    return format_settled_signal(p["new_pnl"], p["w"], p["l"], p["total_net"],
                                 f"{p['tw']}W/{p['tl']}L")


WITHHELD_KEY = "profit-withheld"  # one notice per cause per 7 days, whatever rows are pending


def profit_ping(parts, body, ledger=None, send_fn=None, now=None):
    """Verified Profit ping: every figure recomputed from an independent ledger re-read
    (signal_verify.ledger_realized). Bad / orphan / future-dated rows -> withheld."""
    import signal_verify as sv
    kind, now = "Profit", now or sv.utcnow()
    try:
        real = sv.ledger_realized((ledger or sv.read_ledger)())
    except Exception as e:
        return sv.withhold(kind, "ledger read failed", e, send_fn, now, dedupe_window_s=7 * 86400)
    probs = ([{"code": "ledger settlement rows unparseable", "detail": [parts.get("bad"), real["bad"]]}]
             if parts.get("bad") or real["bad"] else [])
    if parts.get("future"):
        probs.append({"code": "ledger has future-dated rows", "detail": "future"})
    if probs:
        return sv.deliver(kind, "", problems=probs, send_fn=send_fn, now=now,
                          dedupe_key=WITHHELD_KEY, dedupe_window_s=7 * 86400)
    new_ids = {s.get("entry_id") for s in parts["new"]}
    new2 = [s for s in real["settlements"] if s.get("entry_id") in new_ids]
    claim = sv.claim
    claims = [
        claim("Profit", parts["new_pnl"], sv.dsum(sv.num(s.get("realized_pnl_net")) for s in new2),
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
                      send_fn=send_fn, now=now, dedupe_window_s=7 * 86400,
                      dedupe_key="profit:" + ",".join(sorted(str(i) for i in new_ids)))


SENT_IDS_MAX = 5000  # bounded: the newest reported settlement ids are kept


def _sent_ids_path():
    import signal_verify as sv
    return os.path.join(sv.signal_dir(), "profit_sent_ids.jsonl")  # gitignored (*.jsonl)


def load_sent_ids():
    """Entry ids of settlements already reported in a Profit ping (the source of truth;
    the cursor is only an optimization). Unreadable lines are ignored."""
    try:
        with open(_sent_ids_path()) as f:
            return {json.loads(l) for l in f if l.strip()}
    except Exception:
        return set()


def add_sent_ids(ids):
    try:
        path = _sent_ids_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        old = open(path).read().splitlines() if os.path.exists(path) else []
        new = [json.dumps(str(i)) for i in sorted(ids, key=str)]
        keep = [l for l in old if l.strip() and l not in set(new)] + new
        with open(path, "w") as f:
            f.write("".join(l + "\n" for l in keep[-SENT_IDS_MAX:]))
    except Exception as e:
        print(f"[settle_run] sent-set write failed: {e}")


def _write_cursor(path, iso):
    try:
        with open(path, "w") as f:
            f.write(iso)
    except Exception as e:
        print(f"[settle_run] cursor write failed: {e}")


def run(send_fn=None, now=None):
    import signal_verify as sv
    now = now or sv.utcnow()
    try:
        new_sports, stats = settle_sports()
        if new_sports:
            print(f"sports-log view: {len(new_sports)} settled (not FTL, not Signal)")
    except Exception as e:
        print(f"[settle_run] sports-log settle failed (Signal uses FTL only): {e}")

    # Cursor: an ISO time, never in the future; first run starts 50 min back and is
    # persisted, so rows held back by a withheld report are still sent once valid.
    # It is only an optimization: the persisted sent-set (profit_sent_ids.jsonl) is
    # what guarantees no settlement is reported twice, even after a window reset.
    cursor_path = os.path.join(MODELS, "settle_ftl_signal_cursor.txt")
    try:
        since = sv.parse_ts(open(cursor_path).read().strip())
    except Exception:
        since = None
    if since is None or since > now:  # missing, garbage or impossible (future) cursor:
        since = now - timedelta(minutes=50)  # restart the window rather than skip rows
        _write_cursor(cursor_path, since.isoformat())
    try:
        recs, problems = read_ledger_safe()
        if problems:  # never silent: one withheld notice per cause per 7 days
            res = sv.deliver("Profit", "", problems=[{"code": p, "detail": p} for p in problems],
                             send_fn=send_fn, now=now, dedupe_key=WITHHELD_KEY,
                             dedupe_window_s=7 * 86400)
            print(f"[settle_run] ledger file problems {problems}: Signal {res['status']}")
        else:
            parts = ftl_profit_parts(recs, since=since, now=now, exclude=load_sent_ids())
            if parts:
                body = format_settled_signal(parts["new_pnl"], parts["w"], parts["l"],
                                             parts["total_net"], f"{parts['tw']}W/{parts['tl']}L")
                res = profit_ping(parts, body, ledger=lambda: read_ledger_safe()[0],
                                  send_fn=send_fn, now=now)
                print(f"[settle_run] Signal {res['status']} sent={res['sent']}")
                # Advance only over rows Burton actually got (or already got, verified).
                delivered = (res["status"] == "verified" and res["sent"]) or (
                    res["status"] == "dedupe" and not res.get("problems"))
                if delivered and parts["new"]:  # sent-set first: never report a row twice
                    add_sent_ids(str(s.get("entry_id")) for s in parts["new"])
                    _write_cursor(cursor_path, parts["cursor"])
    except Exception as e:
        res = sv.withhold("Profit", "settlement check failed", f"{type(e).__name__}: {e}",
                          send_fn, now, dedupe_window_s=7 * 86400)
        print(f"[settle_run] FTL Profit ping failed: {e} (Signal {res['status']})")

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
