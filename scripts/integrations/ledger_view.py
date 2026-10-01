"""Shared fee-true ledger selection for every Signal sender (settle_run Profit ping,
pnl_run 11pm report, claude_bridge facts block) — one definition, so the senders
can never disagree about which settlements count.

Selection (`select(recs, now)`):
  * live + reconciled only: the settlement AND its entry are not paper / sandbox /
    backfill / shadow / UNRECONCILED (those rows are ignored, never flagged);
  * canonical entry id = str(entry_id) (int 5 and "5" are the same entry); the first
    entry row for an id defines it;
  * the FIRST valid live settlement per entry counts; an identical later duplicate is
    ignored; a later one with a different P&L / win flag is a conflict (the entry is
    then excluded and flagged);
  * flagged (excluded, and every sender omits the all-time total with the same
    `omit_reason` text): unparseable line / non-object row, live orphan, missing /
    NaN / garbage P&L or win flag, missing / non-string / garbage booking time
    (logged_at, else ts), and FUTURE rows — any PRESENT time field (logged_at / ts /
    settled_at / settlement_ts) more than TOL beyond `now`, or present but not a parseable
    timestamp. EVERY present time field is validated, so the two derived times below can
    never disagree about a row: a row is either flagged (omitted from the all-time AND the
    24h figure) or counted in both.
  * ONE documented precedence for the derived times (first PRESENT field wins; "present" =
    not missing / None / ""):
        booked        = logged_at, else ts                      (since-last-report, freshness)
        settled_time  = settled_at, settlement_ts, ts, logged_at (the 24h window)
  * bad rows are counted as distinct ledger LINES: two identical garbage lines are 2, a
    conflicting pair is 2 (every settlement line of that entry), never collapsed by content.
`now` should be taken AFTER the ledger was read (callers pass now=None for that).
"""
import hashlib
import json
import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import signal_verify as sv

TOL = timedelta(minutes=5)  # clock-skew tolerance for "future" rows
KINDS = ("unparseable", "future-dated", "conflicting duplicate settlement")


def _h(x):
    return hashlib.sha1(x.encode("utf-8", "replace")).hexdigest()[:12]


def read(path=None):
    """(rows, err). err None when readable; "missing" / "unreadable (<Error>)" otherwise.
    Invalid JSON / non-object lines become {"type": "_bad_line", "key": "line:<hash>"}."""
    import forward_test_ledger as ftl
    path = path or ftl.LEDGER
    if not os.path.lexists(path):
        return [], "missing"
    try:
        with open(path, errors="replace") as f:
            lines = f.read().splitlines()
    except (OSError, ValueError) as e:
        return [], f"unreadable ({type(e).__name__})"
    return [bad_line_row(l) for l in lines if l.strip()], None


def bad_line_row(line):
    try:
        r = json.loads(line)
    except ValueError:
        r = None
    return r if isinstance(r, dict) else {"type": "_bad_line", "key": "line:" + _h(line)}


TIME_KEYS = ("logged_at", "ts", "settled_at", "settlement_ts")


def _ts(v):
    return sv.parse_ts(v) if isinstance(v, str) else None


def _first(s, keys):
    """Parsed first PRESENT field of `keys` (missing / None / "" are absent); a present
    value that is not a parseable timestamp -> None."""
    for k in keys:
        if s.get(k) not in (None, ""):
            return _ts(s.get(k))
    return None


def time_fields(s):
    """{key: datetime | None} for every PRESENT time field (None = present but garbage)."""
    return {k: _ts(s.get(k)) for k in TIME_KEYS if s.get(k) not in (None, "")}


def booked(s):
    """When the settlement was booked: logged_at, else ts; None if missing / garbage."""
    return _first(s, ("logged_at", "ts"))


def settled_time(s):
    """Settlement time for the 24h window: settled_at, settlement_ts, ts, logged_at (first
    present). None if missing / garbage (select() flags such rows, so for selected rows
    this is always a real time)."""
    return _first(s, ("settled_at", "settlement_ts", "ts", "logged_at"))


def _live(r):
    return not sv._non_real(r.get("source")) and not sv._unrec(r)


def canon(eid):
    if eid is None or isinstance(eid, (bool, dict, list, float)) or str(eid).strip() == "":
        return None
    return str(eid)


def _row_hash(r):
    return _h(json.dumps(r, sort_keys=True, default=str))


def select(recs, now):
    """{"valid": {eid: settlement}, "bad": {line_key: kind}} — see module docstring.
    line_key = "<content hash>#<n>": the n-th identical line, so every ledger line counts."""
    entries = {}
    for r in recs:
        if r.get("type") == "entry" and canon(r.get("id")) is not None:
            entries.setdefault(canon(r.get("id")), r)
    valid, bad, dropped, lines, seen = {}, {}, set(), {}, {}

    def line_key(h):  # the n-th identical line; hashed only for rows that end up flagged
        seen[h] = seen.get(h, -1) + 1
        return f"{h}#{seen[h]}"

    limit = now + TOL
    for r in recs:
        if r.get("type") == "_bad_line":
            bad[line_key(r.get("key") or "line:?")] = "unparseable"
            continue
        if r.get("type") != "settlement" or not _live(r):
            continue
        eid = canon(r.get("entry_id"))
        e = entries.get(eid) if eid is not None else None
        if e is None:
            bad[line_key("row:" + _row_hash(r))] = "unparseable"  # live orphan
            continue
        if not _live(e):
            continue
        tf = time_fields(r)
        if any(t is not None and t > limit for t in tf.values()):
            bad[line_key("row:" + _row_hash(r))] = "future-dated"
            continue
        p, w = sv.num(r.get("realized_pnl_net")), sv.won_flag(r.get("won"))
        if p is None or w is None or not tf or any(t is None for t in tf.values()):
            bad[line_key("row:" + _row_hash(r))] = "unparseable"
            continue
        if eid in dropped:
            bad[line_key("row:" + _row_hash(r))] = KINDS[2]
            continue
        lines.setdefault(eid, []).append(r)
        if eid in valid:
            f = valid[eid]
            if (sv.num(f.get("realized_pnl_net")), sv.won_flag(f.get("won"))) != (p, w):
                for x in lines[eid]:  # every settlement line of the entry is affected
                    bad[line_key("row:" + _row_hash(x))] = KINDS[2]
                del valid[eid]
                dropped.add(eid)
            continue
        valid[eid] = r
    return {"valid": valid, "bad": bad}


def omit_reason(v):
    """The one notice text every sender uses when the all-time total is omitted."""
    bad = v["bad"]
    if not bad:
        return None
    kinds = [k for k in KINDS if k in set(bad.values())]
    return f"{len(bad)} ledger row(s) " + " / ".join(kinds) + " (ledger scan)"


def pnl(rows):
    return sv.dsum(sv.num(s.get("realized_pnl_net")) for s in rows)


def wins(rows):
    return sum(1 for s in rows if sv.won_flag(s.get("won")))


def realized(recs, now):
    """ledger_realized-compatible summary of `select`."""
    v = select(recs, now)
    rows = [v["valid"][k] for k in sorted(v["valid"])]
    w = wins(rows)
    return {"rows": len(rows), "settlements": rows,
            "parts": [sv.num(s.get("realized_pnl_net")) for s in rows], "total": pnl(rows),
            "wins": w, "losses": len(rows) - w, "bad": len(v["bad"]),
            "conflict": sum(1 for k in v["bad"].values() if k == KINDS[2]),
            "future": sum(1 for k in v["bad"].values() if k == KINDS[1]),
            "reason": omit_reason(v), "valid": v["valid"], "bad_keys": v["bad"]}


def last_24h(rows, now):
    """(sum, untimed): settlements with cut < settled_time <= now + TOL."""
    cut, top = now - timedelta(hours=24), now + TOL
    untimed = sum(1 for s in rows if settled_time(s) is None)
    return pnl(s for s in rows if settled_time(s) and cut < settled_time(s) <= top), untimed
