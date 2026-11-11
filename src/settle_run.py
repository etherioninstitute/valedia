"""Settlement Checker — Valedia ASI
Checks pending bets for settlement. Sends Signal ONLY when profit/loss realized.
(c) GOATWORTHY Post LLC
"""
import sys, os, json
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
MODELS = os.path.expanduser("~/.openclaw/workspace/models")

def settle_sports():
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


ALL_TIME = "Realized P&L (all-time, ledger, all venues, fee-incl.)"
REINIT = "Profit reporting (re)initialized — earlier settlements are included in the 11pm all-time total"
TWICE = "Profit report delivery unconfirmed twice — see the 11pm report"
SCAN_LINE = "ledger scan count — no money figure to verify"  # instead of "✓ verified"


def format_settled_signal(new_pnl, w, l, total_net=None, total_record=None, bad=0, resent=0,
                          reason=None):
    """Profit ping body. All-time line only when no ledger row is flagged (ledger_view)."""
    st = _stamp()
    lines = [f"Profit (settled since last report): {_pl(new_pnl)} ({w}W/{l}L)"]
    if resent:
        lines.append(f"(re-sent: previous delivery unconfirmed) {resent} of these settlement(s)")
    if not bad and not reason and total_net is not None:
        lines.append(f"{ALL_TIME}: {_pl(total_net)} ({total_record})")
    else:
        lines.append(f"⚠ {reason or f'{bad} ledger row(s) unparseable'} — all-time total omitted")
    return "\n".join(lines + ([st] if st else []))


def format_arb_signal(arb):
    """LOG ONLY. latency_arb's arb-log is not the fee-true ledger (no fee on
    losses, potential_profit on wins) and its 'settled' count is cumulative, so
    it is never sent to Signal as Profit."""
    return (f"arb-log (not FTL, not sent): net {arb['net_pnl']:+.2f} "
            f"({arb['wins']}W/{arb['losses']}L cumulative)")


def ftl_profit_parts(recs=None, since=None, minutes=None, now=None, exclude=()):
    """Figures for valid settlements (ledger_view.select) not in `exclude`, optionally
    newer than `since` / the last `minutes`. None if nothing to report."""
    import signal_verify as sv
    import ledger_view as lv
    recs = recs if recs is not None else lv.read()[0]
    now = now or sv.utcnow()
    v = lv.select(recs, now)
    valid = v["valid"]
    since = sv.parse_ts(since) if isinstance(since, str) else since
    if since is None and minutes:
        since = now - timedelta(minutes=minutes)
    new = [s for k, s in sorted(valid.items()) if k not in set(exclude or ())
           and (since is None or lv.booked(s) > since)]
    if not new and not v["bad"]:
        return None
    w, tw = lv.wins(new), lv.wins(valid.values())
    return {"new": new, "new_ids": [lv.canon(s.get("entry_id")) for s in new], "new_pnl": lv.pnl(new),
            "w": w, "l": len(new) - w, "bad": len(v["bad"]), "bad_keys": sorted(v["bad"]),
            "reason": lv.omit_reason(v), "total_net": lv.pnl(valid.values()), "tw": tw,
            "tl": len(valid) - tw}


def ftl_profit_signal(recs=None, since=None, minutes=50):
    """Profit body from fee-true FTL rows. None if nothing admissible (incl. UNRECONCILED)."""
    p = ftl_profit_parts(recs, since, minutes)
    if not p or not p["new"]:
        return None
    return format_settled_signal(p["new_pnl"], p["w"], p["l"], p["total_net"],
                                 f"{p['tw']}W/{p['tl']}L", bad=p["bad"], reason=p["reason"])


# Persistent records + lock live in settle_record.py (kept under 500 lines each).
from settle_record import (SENT_FILE, UNCONF_FILE, STATE_FILE, NOTICE_FILE, SENT_IDS_MAX,  # noqa: E402,F401
                           LOCK_STALE_S, SentRecordError, _paths, _load_pair, _write_pair,
                           record_sent, record_seed, load_sent, load_sent_full, load_sent_ids, set_unconfirmed, _json_pair,
                           _save_json_pair, _cursor, _write_cursor, _lock_info, _acquire)


def _paused(send_fn, now, what, waiting=None, why=""):
    """Figure-free paused notice, at most once per UTC day per reason."""
    import signal_verify as sv
    tag = f"{what}|{now.date().isoformat()}"
    days = _json_pair(NOTICE_FILE)
    if tag in days:
        print(f"[settle_run] paused ({what}); notice already sent today")
        return {"status": "dedupe", "sent": False, "text": ""}
    text = f"Profit report paused — {what}" + (
        f" · {waiting} settlement(s) waiting (ledger scan)" if waiting is not None else "")
    # the count is a ledger scan, not an independently re-read figure: labelled, never "verified"
    res = sv.deliver("Profit", text, (), send_fn=send_fn, now=now, dedupe_window_s=0,
                     allowed=[waiting] if waiting is not None else (),
                     verified_line=SCAN_LINE if waiting is not None else None,
                     info={"why": str(why)[:300]})
    if res.get("delivery") in ("ok", "unknown"):
        days = {k: v for k, v in days.items() if k.split("|")[-1] >= (now - timedelta(days=7)).date().isoformat()}
        _save_json_pair(NOTICE_FILE, dict(days, **{tag: now.isoformat()}))
    return res


def _bootstrap(valid, send_fn, now):
    """First run, NO legacy cursor: record every existing id as baseline ONLY after one
    figure-free notice went out (returns res; nothing recorded otherwise -> retried).
    No settlements at all -> record the empty baseline (+ marker), returns None."""
    import signal_verify as sv
    import ledger_view as lv
    if not valid:
        if not record_seed([], None):
            raise SentRecordError("baseline not recorded")
        return None
    res = sv.deliver("Profit", REINIT, send_fn=send_fn, now=now, dedupe_window_s=0,
                     info={"baseline_ids": len(valid)})
    if res.get("delivery") in ("ok", "unknown") and record_seed(valid, None):
        _write_cursor(max([now] + [lv.booked(s) for s in valid.values()]))
    return res


def _seed_from_cursor(valid, sent, now):
    """Legacy cursor file present and the sent-record lacks its seeded_from_cursor marker:
    UNION-seed ids booked at or before the cursor (the old code already reported them),
    once, then write the marker. No cursor -> the marker alone (nothing to reconcile).
    Returns the ids now known sent."""
    import ledger_view as lv
    cur = _cursor(now)
    base = {k for k, s in valid.items() if lv.booked(s) <= cur} if cur else set()
    if not record_seed(base - set(sent), cur):
        raise SentRecordError("baseline not recorded")
    if base - set(sent):
        print(f"[settle_run] seeded from old cursor {cur.isoformat()}: "
              f"{len(base - set(sent))} already reported")
    return set(sent) | base


def _twice_notice(send_fn, now, twice, unc):
    """The "unconfirmed twice" notice. The ids are marked sent ONLY after it is confirmed
    delivered. A confirmed failure leaves them unrecorded (figures stay withheld) and the
    notice is retried on the next run; an unknown outcome (maybe delivered) is retried at
    most once per UTC day, so a duplicate is bounded. Never silent: every attempt is in
    verify_log + stderr and a skipped retry is logged."""
    import signal_verify as sv
    tag = f"twice|{now.date().isoformat()}"
    days = _json_pair(NOTICE_FILE)
    if tag in days:
        print(f"[settle_run] {len(twice)} settlement(s) still unconfirmed-twice: notice outcome "
              "unknown today; retry tomorrow")
        return None
    res = sv.deliver("Profit", TWICE, send_fn=send_fn, now=now, dedupe_window_s=0,
                     info={"ids": twice})
    if res.get("delivery") == "ok" and res["status"] == "verified":
        return res
    if res.get("delivery") == "unknown" or res.get("sent"):  # may have reached Burton
        days = {k: v for k, v in days.items() if k.split("|")[-1] >= (now - timedelta(days=7)).date().isoformat()}
        _save_json_pair(NOTICE_FILE, dict(days, **{tag: now.isoformat()}))
    print(f"[settle_run] 'unconfirmed twice' notice not confirmed ({res.get('delivery')}); "
          f"{len(twice)} id(s) stay unrecorded and are retried")
    return res


def _cycle(send_fn, now_in):
    import signal_verify as sv
    import ledger_view as lv
    recs, err = lv.read()
    now = now_in or sv.utcnow()  # taken after the ledger read
    if err:  # missing / directory / I/O error: nothing recorded, bootstrap deferred
        return _paused(send_fn, now, "ledger unreadable", why=err)
    v = lv.select(recs, now)
    valid, bad, reason = v["valid"], v["bad"], lv.omit_reason(v)
    try:
        d = load_sent_full(keep=valid)
        sent, first = d["ids"], d["first"]
        if first and _cursor(now) is None:  # never recorded, no old cursor: one re-init notice
            res = _bootstrap(valid, send_fn, now)
            if res is not None:
                return res
            sent = load_sent(keep=valid)[0]
        elif d["marker"] is None:  # record never reconciled with the legacy cursor (once)
            sent = _seed_from_cursor(valid, sent, now)
        unc_d = _load_pair(UNCONF_FILE)
        st = _json_pair(STATE_FILE)
    except SentRecordError as e:
        cur = _cursor(now)
        waiting = sum(1 for s in valid.values() if lv.booked(s) > cur) if cur else None
        return _paused(send_fn, now, "sent-record unreadable", waiting, e)
    pend = {str(k) for k in st.get("pending_sent") or []}
    if pend:  # delivered (confirmed) but the record write failed last time: retry recording
        sent |= pend
        if record_sent(pend):
            st = {k: v for k, v in st.items() if k != "pending_sent"}
            _save_json_pair(STATE_FILE, st)
    unc = unc_d["ids"]
    if any(k in sent for k in unc) or unc_d["bad"]:  # delivered + recorded: drop write-ahead
        unc = type(unc)({k: n for k, n in unc.items() if k not in sent})
        set_unconfirmed(unc)
    todo = sorted(k for k in valid if k not in sent)
    res = None
    twice = [k for k in todo if unc[k] >= 2]
    if twice:  # two unknown deliveries: stop re-sending figures, point to the 11pm report
        res = _twice_notice(send_fn, now, twice, unc)
        if res and res.get("delivery") == "ok" and res["status"] == "verified" and record_sent(twice):
            unc = type(unc)({k: n for k, n in unc.items() if k not in twice})
            set_unconfirmed(unc)
        todo = [k for k in todo if k not in twice]
    bad_key = lv._h(",".join(sorted(bad))) if bad else ""
    if not todo:
        if bad and st.get("bad_notified") != bad_key:
            r = sv.deliver("Profit", f"⚠ {reason} — their figures withheld until fixed", (),
                           allowed=[len(bad)], verified_line=SCAN_LINE, send_fn=send_fn, now=now, dedupe_key="bad:" + bad_key,
                           dedupe_window_s=30 * 86400, info={"bad": sorted(bad)})
            if r.get("delivery") in ("ok", "unknown") or r["status"] == "dedupe":
                _save_json_pair(STATE_FILE, dict(st, bad_notified=bad_key))
            return r
        return res
    return _report(send_fn, now, valid, bad, reason, todo, unc, st, bad_key)


def _report(send_fn, now, valid, bad, reason, todo, unc, st, bad_key):
    import signal_verify as sv
    import ledger_view as lv
    rows = [valid[k] for k in todo]
    w, tw = lv.wins(rows), lv.wins(valid.values())
    resent = [k for k in todo if unc[k] >= 1]
    body = format_settled_signal(lv.pnl(rows), w, len(rows) - w, lv.pnl(valid.values()),
                                 f"{tw}W/{len(valid) - tw}L", bad=len(bad), resent=len(resent),
                                 reason=reason)
    v2 = lv.select(lv.read()[0], now)["valid"]  # independent re-read
    rows2 = [v2[k] for k in todo if k in v2]
    claims = [sv.claim("settled count", len(rows), len(rows2), "ledger re-read", tol=0),
              sv.claim("Profit", lv.pnl(rows), lv.pnl(rows2), "ledger re-read"),
              sv.claim("wins", w, lv.wins(rows2), "ledger re-read", tol=0)]
    if not bad:
        claims += [sv.claim("All-time", lv.pnl(valid.values()), lv.pnl(v2.values()), "ledger re-read"),
                   sv.claim("all-time wins", tw, lv.wins(v2.values()), "ledger re-read", tol=0)]
    # WRITE-AHEAD: these ids are "possibly delivered" before the transport is touched
    if not _write_pair(UNCONF_FILE, [json.dumps(k) for k in todo]):
        return _paused(send_fn, now, "sent-record unwritable", len(todo))
    oldest = min(lv.booked(s) for s in rows)
    res = sv.deliver("Profit", body, claims,
                     [sv.fresh("settlement", oldest.isoformat(), 6 * 3600, policy="label")],
                     footer=[f"{len(valid)} settled rows (ledger scan)", sv.hhmm(now)], send_fn=send_fn,
                     now=now, dedupe_window_s=0, info={"ids": todo, "bad": sorted(bad)})
    d = res.get("delivery") if res["status"] == "verified" else None
    if d == "ok":
        if record_sent(todo):
            set_unconfirmed(type(unc)({k: n for k, n in unc.items() if k not in todo}))
            _write_cursor(max([now] + [lv.booked(s) for s in valid.values()]))
        else:  # delivered, not recordable: no "withheld" notice; log + retry recording
            st = dict(st, pending_sent=sorted(set(st.get("pending_sent") or []) | set(todo)))
            _save_json_pair(STATE_FILE, st)
            set_unconfirmed(type(unc)({k: n for k, n in unc.items() if k not in todo}))
            print(f"[settle_run] delivered {todo} but sent-record write failed; recording "
                  "retried next run (pending_sent; else one labelled re-send)")
    elif d != "unknown":  # withheld / failed: nothing left this host with these figures
        set_unconfirmed(unc)
    if d in ("ok", "unknown") and bad:
        _save_json_pair(STATE_FILE, dict(st, bad_notified=bad_key))
    return res


def run(send_fn=None, now=None):
    """Profit ping, id-based: report exactly the valid settlements Burton has not been
    told about (persisted sent record + write-ahead), under an exclusive lock."""
    import signal_verify as sv
    try:
        new_sports, stats = settle_sports()
        if new_sports:
            print(f"sports-log view: {len(new_sports)} settled (not FTL, not Signal)")
    except Exception as e:
        print(f"[settle_run] sports-log settle failed (Signal uses FTL only): {e}")
    kind, lk = _acquire()
    if kind == "held":
        pid, age = _lock_info(lk)
        print(f"[settle_run] another settle_run holds the lock (pid {pid}); it will report")
        if age > LOCK_STALE_S:  # a stuck holder never silences reporting for good
            _paused(send_fn, now or sv.utcnow(), "settle lock held too long", why=f"pid {pid}")
        return
    if kind is None:
        res = _paused(send_fn, now or sv.utcnow(), "no lock available", why=lk)
        print(f"[settle_run] no lock possible ({lk}); Signal {res['status']}")
        return
    try:
        res = _cycle(send_fn, now)
        print(f"[settle_run] Signal {res['status'] if res else 'nothing new'}")
    except Exception as e:
        res = sv.withhold("Profit", "settlement check failed", f"{type(e).__name__}: {e}",
                          send_fn, now or sv.utcnow(), dedupe_window_s=86400)
        print(f"[settle_run] FTL Profit ping failed: {e} (Signal {res['status']})")
    finally:
        if kind == "flock":
            lk.close()  # releases the flock
        else:
            try:
                os.remove(lk)
            except OSError:
                pass

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
