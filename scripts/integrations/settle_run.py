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


ALL_TIME = "Realized P&L (all-time, ledger, all venues, fee-incl.)"


def format_settled_signal(new_pnl, w, l, total_net=None, total_record=None, bad=0, resent=0):
    """Profit ping body. All-time line only when every ledger row parsed."""
    st = _stamp()
    lines = [f"Profit (settled since last report): {_pl(new_pnl)} ({w}W/{l}L)"]
    if resent:
        lines.append(f"(re-sent: previous delivery unconfirmed) {resent} of these settlement(s)")
    lines.append(f"{ALL_TIME}: {_pl(total_net)} ({total_record})" if not bad and total_net is not None
                 else f"⚠ {bad} ledger row(s) unparseable — all-time total omitted")
    return "\n".join(lines + ([st] if st else []))


def format_arb_signal(arb):
    """LOG ONLY. latency_arb's arb-log is not the fee-true ledger (no fee on
    losses, potential_profit on wins) and its 'settled' count is cumulative, so
    it is never sent to Signal as Profit."""
    return (f"arb-log (not FTL, not sent): net {arb['net_pnl']:+.2f} "
            f"({arb['wins']}W/{arb['losses']}L cumulative)")


def _h(x):
    import hashlib
    return hashlib.sha1(x.encode("utf-8", "replace")).hexdigest()[:12]


def read_ledger_safe(path=None):
    """(rows, problems, bad_keys): parse line by line, never raising. A truncated /
    invalid JSON line or non-object row is a problem with a stable key (hash of the
    line) so it is reported, never skipped silently, and never blocks valid rows."""
    import forward_test_ledger as ftl
    path = path or ftl.LEDGER
    if not os.path.exists(path):
        return [], [], set()
    try:
        lines = open(path, errors="replace").read().splitlines()
    except Exception as e:
        return [], [f"ledger unreadable ({type(e).__name__})"], {"file:unreadable"}
    rows, problems, bad = [], set(), set()
    for line in lines:
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except ValueError:
            problems.add("ledger has a truncated / invalid JSON line")
            bad.add("line:" + _h(line))
            continue
        if not isinstance(r, dict):
            problems.add("ledger has a non-object row")
            bad.add("line:" + _h(line))
            continue
        rows.append(r)
    return rows, sorted(problems), bad


def _when(s):
    import signal_verify as sv
    raw = s.get("logged_at") or s.get("ts")
    return sv.parse_ts(raw) if isinstance(raw, str) else None


def classify(recs, now):
    """{entry_id: settlement} of VALID live settlements, and the set of bad keys:
    missing/None/NaN/inf/garbage P&L, non-boolean win flag, missing/garbage/future time,
    live orphan settlement, or conflicting duplicate settlements for one entry."""
    import forward_test_ledger as ftl
    import signal_verify as sv
    entries = {r["id"]: r for r in recs if r.get("type") == "entry" and r.get("id")}
    groups, bad = {}, set()
    for s in recs:
        if s.get("type") != "settlement":
            continue
        e, key = entries.get(s.get("entry_id")), _h(json.dumps(s, sort_keys=True, default=str))
        if e is None:
            if not ftl._is_non_real_source(s.get("source")):
                bad.add("orphan:" + key)
            continue
        if fee_true_settlements([s], {s.get("entry_id"): e}) == []:
            continue  # paper / sandbox / backfill / shadow / UNRECONCILED: not live
        t = _when(s)
        if (sv.num(s.get("realized_pnl_net")) is None or sv.won_flag(s.get("won")) is None
                or t is None or t > now + timedelta(minutes=5)):
            bad.add("row:" + key)
            groups.setdefault(s.get("entry_id"), []).append(None)
            continue
        groups.setdefault(s.get("entry_id"), []).append(s)
    valid = {}
    for eid, rows in groups.items():
        if None in rows:
            continue  # its bad row is already counted
        if len({(sv.num(r.get("realized_pnl_net")), sv.won_flag(r.get("won"))) for r in rows}) > 1:
            bad.add(f"conflict:{eid}")
        else:
            valid[str(eid)] = rows[0]
    return valid, bad


def ftl_profit_parts(recs=None, since=None, minutes=None, now=None, exclude=()):
    """Figures for valid settlements not in `exclude` (optionally newer than `since` /
    the last `minutes`, for callers that want a window). None if nothing to report."""
    import signal_verify as sv
    now = now or sv.utcnow()
    recs = recs if recs is not None else read_ledger_safe()[0]
    valid, bad = classify(recs, now)
    since = sv.parse_ts(since) if isinstance(since, str) else since
    if since is None and minutes:
        since = now - timedelta(minutes=minutes)
    new = [s for k, s in sorted(valid.items()) if k not in set(exclude or ())
           and (since is None or _when(s) > since)]
    if not new and not bad:
        return None
    pnl = lambda rows: sv.dsum(sv.num(s.get("realized_pnl_net")) for s in rows)  # noqa: E731
    w = sum(1 for s in new if sv.won_flag(s.get("won")))
    tw = sum(1 for s in valid.values() if sv.won_flag(s.get("won")))
    return {"new": new, "new_ids": [str(s.get("entry_id")) for s in new], "new_pnl": pnl(new),
            "w": w, "l": len(new) - w, "bad": len(bad), "bad_keys": sorted(bad),
            "total_net": pnl(valid.values()), "tw": tw, "tl": len(valid) - tw}


def ftl_profit_signal(recs=None, since=None, minutes=50):
    """Profit body from fee-true FTL rows. None if nothing admissible (incl. UNRECONCILED)."""
    p = ftl_profit_parts(recs, since, minutes)
    if not p or not p["new"]:
        return None
    return format_settled_signal(p["new_pnl"], p["w"], p["l"], p["total_net"],
                                 f"{p['tw']}W/{p['tl']}L", bad=p["bad"])


# ── sent-record: which settlement ids Burton has already been told about ──────
SENT_FILE = "profit_sent_ids.jsonl"            # data/signal/ (gitignored *.jsonl)
UNCONF_FILE = "profit_unconfirmed_ids.jsonl"   # delivery state unknown -> one re-send
STATE_FILE = "settle_state.json"               # last bad-row set already notified
SENT_IDS_MAX = 5000


class SentRecordError(Exception):
    pass


def _paths():
    import signal_verify as sv
    d = sv.signal_dir()
    return (os.path.join(d, SENT_FILE), os.path.join(MODELS, "settle_" + SENT_FILE),
            os.path.join(d, UNCONF_FILE), os.path.join(d, STATE_FILE))


def _read_ids(path):
    """(ids, bad_lines). Missing -> empty. Directory / unreadable -> SentRecordError."""
    if not os.path.lexists(path):
        return set(), 0
    if not os.path.isfile(path):
        raise SentRecordError(f"{path} is not a file")
    try:
        raw = open(path, "rb").read()
    except OSError as e:
        raise SentRecordError(str(e))
    ids, bad = set(), 0
    for line in raw.split(b"\n"):
        if not line.strip():
            continue
        try:
            v = json.loads(line.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            v = None
        if isinstance(v, str) and v:
            ids.add(v)
        else:
            bad += 1
    return ids, bad


def _fsync_write(path, lines, mode):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp" if mode == "w" else path
    with open(tmp, mode) as f:
        f.write("".join(l + "\n" for l in lines))
        f.flush()
        os.fsync(f.fileno())
    if mode == "w":
        os.replace(tmp, path)


def load_sent(keep=()):
    """(ids, first_run). Union of the primary record and its backup copy; bad lines are
    counted and rewritten away atomically. Unreadable -> SentRecordError (fail closed)."""
    primary, backup = _paths()[:2]
    (p_ids, p_bad), (b_ids, b_bad) = _read_ids(primary), _read_ids(backup)
    ids, first = p_ids | b_ids, not os.path.lexists(primary) and not os.path.lexists(backup)
    if not first and (p_bad or b_bad or p_ids != b_ids or len(ids) > SENT_IDS_MAX):
        keep, others = set(keep), sorted(ids - set(keep))
        room = max(0, SENT_IDS_MAX - len(ids & keep))  # ids still in the ledger are never dropped
        trimmed = sorted(ids & keep) + (others[len(others) - room:] if room else [])
        for path in (primary, backup):
            _fsync_write(path, [json.dumps(i) for i in trimmed], "w")
        ids = set(trimmed)
    return ids, first


def record_sent(ids):
    """Append (write + fsync) to the record and its backup — before the lock is released."""
    lines = [json.dumps(str(i)) for i in sorted(ids)]
    for path in _paths()[:2]:
        _fsync_write(path, lines, "a")


def load_sent_ids():
    return load_sent()[0]


def _state():
    try:
        return json.load(open(_paths()[3]))
    except Exception:
        return {}


def _save_state(st):
    _fsync_write(_paths()[3], [json.dumps(st)], "w")


def _paused(send_fn, now, why):
    import signal_verify as sv
    return sv.deliver("Profit", "Profit report paused — sent-record unreadable",
                      send_fn=send_fn, now=now, dedupe_key="profit-paused",
                      dedupe_window_s=7 * 86400, info={"why": str(why)[:300]})


def _profit_cycle(send_fn, now):
    import signal_verify as sv
    recs, problems, line_bad = read_ledger_safe()
    valid, bad = classify(recs, now)
    bad |= line_bad
    try:
        sent_ids, first = load_sent(keep=valid)
        unconf = _read_ids(_paths()[2])[0]
    except (SentRecordError, OSError) as e:
        return _paused(send_fn, now, e)
    if first:  # bootstrap: settlements older than 50 min count as already reported
        old = {k for k, s in valid.items() if _when(s) <= now - timedelta(minutes=50)}
        record_sent(old)
        sent_ids |= old
    todo = sorted(k for k in valid if k not in sent_ids)
    st = _state()
    bad_key = _h(",".join(sorted(bad))) if bad else ""
    if not todo:
        if bad and st.get("bad_notified") != bad_key:
            res = sv.deliver("Profit", f"⚠ {len(bad)} ledger row(s) unparseable — their figures "
                             "withheld until fixed", [sv.claim("rows", len(bad), len(bad), "ledger", tol=0)],
                             send_fn=send_fn, now=now, dedupe_window_s=0, info={"bad": sorted(bad)})
            if res.get("delivery") in ("ok", "unknown"):
                _save_state(dict(st, bad_notified=bad_key))
            return res
        return None
    rows = [valid[k] for k in todo]
    pnl = lambda rs: sv.dsum(sv.num(s.get("realized_pnl_net")) for s in rs)  # noqa: E731
    w, tw = sum(1 for s in rows if sv.won_flag(s.get("won"))), sum(1 for s in valid.values() if sv.won_flag(s.get("won")))
    resent = [k for k in todo if k in unconf]
    body = format_settled_signal(pnl(rows), w, len(rows) - w, pnl(valid.values()),
                                 f"{tw}W/{len(valid) - tw}L", bad=len(bad), resent=len(resent))
    valid2, bad2 = classify(read_ledger_safe()[0], now)  # independent re-read
    rows2 = [valid2.get(k) for k in todo]
    claims = [sv.claim("settled count", len(rows), sum(1 for r in rows2 if r), "ledger re-read", tol=0),
              sv.claim("Profit", pnl(rows), pnl([r for r in rows2 if r]), "ledger re-read"),
              sv.claim("wins", w, sum(1 for r in rows2 if r and sv.won_flag(r.get("won"))),
                       "ledger re-read", tol=0)]
    if not bad:
        claims += [sv.claim("All-time", pnl(valid.values()), pnl(valid2.values()), "ledger re-read"),
                   sv.claim("all-time wins", tw, sum(1 for s in valid2.values() if sv.won_flag(s.get("won"))),
                            "ledger re-read", tol=0)]
    oldest = min((_when(s) for s in rows), default=None)
    res = sv.deliver("Profit", body, claims,
                     [sv.fresh("settlement", oldest.isoformat() if oldest else None, 6 * 3600, policy="label")],
                     footer=[f"ledger {len(valid)} settled rows", sv.hhmm(now)], send_fn=send_fn,
                     now=now, dedupe_window_s=0, info={"ids": todo, "bad": sorted(bad)})
    if res["status"] != "verified":
        return res  # mismatch -> nothing recorded, retried next run
    d = res.get("delivery")
    done = todo if d == "ok" else resent if d == "unknown" else []
    if done:
        record_sent(done)
        try:  # informational only (last report time); never used to select settlements
            _fsync_write(os.path.join(MODELS, "settle_ftl_signal_cursor.txt"), [now.isoformat()], "w")
        except OSError:
            pass
    if d == "unknown":  # first ambiguous delivery -> one labelled re-send next run
        _fsync_write(_paths()[2], [json.dumps(k) for k in todo if k not in unconf], "a")
    if d in ("ok", "unknown") and bad:
        _save_state(dict(st, bad_notified=bad_key))
    return res


def run(send_fn=None, now=None):
    """Profit ping, id-based: report exactly the valid settlements Burton has not been
    told about (persisted sent-record), under an exclusive lock. No cursor."""
    import fcntl
    import signal_verify as sv
    now = now or sv.utcnow()
    try:
        new_sports, stats = settle_sports()
        if new_sports:
            print(f"sports-log view: {len(new_sports)} settled (not FTL, not Signal)")
    except Exception as e:
        print(f"[settle_run] sports-log settle failed (Signal uses FTL only): {e}")
    try:
        os.makedirs(sv.signal_dir(), exist_ok=True)
        lock = open(os.path.join(sv.signal_dir(), "settle.lock"), "a+")
    except OSError as e:
        res = _paused(send_fn, now, e)
        print(f"[settle_run] cannot open lock ({e}); Signal {res['status']}")
        return
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print("[settle_run] another settle_run holds the lock; it will report")
            return
        try:
            res = _profit_cycle(send_fn, now)
            print(f"[settle_run] Signal {res['status'] if res else 'nothing new'}")
        except Exception as e:
            res = sv.withhold("Profit", "settlement check failed", f"{type(e).__name__}: {e}",
                              send_fn, now, dedupe_window_s=7 * 86400)
            print(f"[settle_run] FTL Profit ping failed: {e} (Signal {res['status']})")
    finally:
        lock.close()  # releases the flock

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
