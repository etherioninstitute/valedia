"""signal_verify.py — nothing reaches Burton on Signal unless every figure checks out.

Every money figure in a Signal message must name its source and be recomputed
independently from that source before send:
  * broker  — Kalshi /portfolio/positions (Invested = open-position cost), re-read
  * ledger  — forward_test_ledger.jsonl (Realized P&L, fee-incl.), re-read
  * model   — labelled "Modeled EV — not profit", recomputed from its parts
Checks: figure == independent recompute (half-cent), totals == sum of parts,
counts match, sources fresh, and no 2-decimal figure in the text that no claim
backs (catches hardcoded / guessed numbers). Any failure -> the figures are NOT
sent; Burton gets one short "Report withheld (<kind>): <reason>" instead and the
full discrepancy goes to data/signal/verify_log.jsonl (gitignored). Same message
within its window is not re-sent (data/signal/sent_log.jsonl). Never raises.
No orders, no keys, no gates touched — reporting only.
(c) GOATWORTHY Post LLC
"""
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
CENT = 0.005 + 1e-9
MAX_LEN = 1400
# 2-decimal money-like figures; skips version stamps (v0.74.1) and dotted ids.
FIG_RE = re.compile(r"(?<![\w.])[+-]?\d[\d,]*\.\d{2}(?![.\d])")
LOG_HINT = "see data/signal/verify_log.jsonl"


# ── plumbing ────────────────────────────────────────────────────────────────
def signal_dir():
    return os.environ.get("VALEDIA_SIGNAL_DIR") or os.path.join(ROOT, "data", "signal")


def utcnow():
    return datetime.now(timezone.utc)


def parse_ts(raw):
    if isinstance(raw, datetime):
        dt = raw
    else:
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except Exception:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def fmt_age(sec):
    sec = int(abs(sec))
    if sec < 60:
        return f"{sec}s"
    if sec < 3600:
        return f"{sec // 60}m"
    if sec < 86400:
        return f"{sec // 3600}h{(sec % 3600) // 60:02d}m"
    return f"{sec // 86400}d{(sec % 86400) // 3600}h"


def hhmm(ts):
    dt = parse_ts(ts)
    return dt.astimezone(timezone.utc).strftime("%H:%M UTC") if dt else "?"


def _append(name, rec):
    try:
        d = signal_dir()
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, name), "a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
    except Exception as e:
        print(f"[signal_verify] could not write {name}: {e}", file=sys.stderr)


def _read(name):
    try:
        with open(os.path.join(signal_dir(), name)) as f:
            return [json.loads(l) for l in f if l.strip()]
    except Exception:
        return []


def _f(x):
    try:
        return float(x)
    except Exception:
        return 0.0


# ── independent source readers ──────────────────────────────────────────────
def _exposure(p):
    """Open-position cost in dollars (new *_dollars field, legacy cents fallback)."""
    if p.get("market_exposure_dollars") is not None:
        return abs(_f(p.get("market_exposure_dollars")))
    return abs(_f(p.get("market_exposure"))) / 100.0


def _qty(p):
    for k in ("position_fp", "position"):
        if p.get(k) is not None:
            return _f(p.get(k))
    return 0.0


def broker_snapshot(get_positions=None, now=None):
    """One fresh broker read. Invested = sum of open-position cost (not market value)."""
    if get_positions is None:
        from kalshi_auth import get_positions  # network; never used in tests
    raw = get_positions() or {}
    rows = [p for p in (raw.get("market_positions") or []) if abs(_qty(p)) > 0]
    return {"invested": sum(_exposure(p) for p in rows), "n": len(rows),
            "tickers": sorted(p.get("ticker", "") for p in rows),
            "ts": (now or utcnow()).isoformat()}


def read_ledger():
    import forward_test_ledger as ftl
    return ftl._read()


def _non_real(source):
    s = str(source or "").lower()
    return any(t in s for t in ("paper", "sandbox", "unreconciled", "backfill", "shadow"))


def _unrec(rec):
    return str(rec.get("fill_status") or "").upper() == "UNRECONCILED"


def ledger_realized(recs):
    """Admissible (real, reconciled) settlements — independent of settle_run's filter."""
    entries = {r.get("id"): r for r in recs if r.get("type") == "entry" and r.get("id")}
    rows = []
    for s in recs:
        if s.get("type") != "settlement":
            continue
        e = entries.get(s.get("entry_id"))
        if e is None or _non_real(s.get("source")) or _non_real(e.get("source")):
            continue
        if _unrec(e) or _unrec(s):
            continue
        rows.append(s)
    parts = [_f(s.get("realized_pnl_net")) for s in rows]
    wins = sum(1 for s in rows if s.get("won"))
    return {"rows": len(rows), "settlements": rows, "parts": parts, "total": sum(parts),
            "wins": wins, "losses": len(rows) - wins}


def ledger_open_kalshi(recs):
    done = {r.get("entry_id") for r in recs if r.get("type") == "settlement"}
    return sum(1 for e in recs if e.get("type") == "entry" and e.get("id") not in done
               and str(e.get("venue") or "").startswith("kalshi")
               and not _non_real(e.get("source")) and not _unrec(e))


def ledger_order_cost(recs, order_ids):
    """Fee-incl. cost of real FILLED ledger entries for these Kalshi order ids."""
    want = {o for o in order_ids if o}
    rows = [e for e in recs if e.get("type") == "entry" and e.get("order_id") in want
            and not _non_real(e.get("source")) and not _unrec(e)]
    cost = sum(_f(e.get("entry_price")) * _f(e.get("contracts")) + _f(e.get("entry_fee"))
               for e in rows)
    stamps = [parse_ts(e.get("logged_at") or e.get("ts")) for e in rows]
    stamps = [t for t in stamps if t]
    return {"n": len(rows), "cost": cost, "order_ids": sorted({e.get("order_id") for e in rows}),
            "oldest_ts": min(stamps).isoformat() if stamps else None}


# ── checks ──────────────────────────────────────────────────────────────────
def claim(name, reported, recomputed, source, tol=CENT):
    return {"name": name, "reported": reported, "recomputed": recomputed,
            "source": source, "tol": tol}


def sum_claim(name, total, parts, source="sum of parts"):
    return claim(name, total, sum(parts), source)


def fresh(name, ts, max_age_s, policy="withhold"):
    return {"name": name, "ts": ts, "max_age_s": max_age_s, "policy": policy}


def unsourced_figures(text, claims, allowed=()):
    known = [abs(_f(c["reported"])) for c in claims if c.get("reported") is not None]
    known += [abs(_f(a)) for a in allowed]
    out = []
    for m in FIG_RE.findall(text or ""):
        v = abs(_f(m.replace(",", "")))
        if not any(abs(v - k) <= CENT for k in known):
            out.append(m)
    return out


def evaluate(body, claims=(), freshness=(), now=None, allowed=()):
    now = now or utcnow()
    problems, stale = [], []
    for c in claims:
        rep, rec = c.get("reported"), c.get("recomputed")
        if rep is None or rec is None:
            problems.append({"code": f"{c['name']}: no source value",
                             "detail": f"{c['name']} reported={rep} {c['source']}={rec}"})
        elif abs(_f(rep) - _f(rec)) > c.get("tol", CENT):
            problems.append({"code": f"{c['name']} does not match {c['source']}",
                             "detail": f"{c['name']} reported={rep} {c['source']}={rec}"})
    for fr in freshness:
        ts = parse_ts(fr.get("ts")) if fr.get("ts") else None
        if ts is None:
            problems.append({"code": f"{fr['name']}: no timestamp", "detail": str(fr)})
            continue
        age = (now - ts).total_seconds()
        if age < -300:
            problems.append({"code": f"{fr['name']}: timestamp in the future",
                             "detail": f"{fr['name']} ts={fr['ts']} age={age:.0f}s"})
        elif age > fr["max_age_s"]:
            stale.append({"name": fr["name"], "age": fmt_age(age), "age_s": age,
                          "policy": fr.get("policy", "withhold")})
    extra = unsourced_figures(body, claims, allowed)
    if extra:
        problems.append({"code": "figure(s) with no named source",
                         "detail": f"unsourced: {extra}"})
    return {"problems": problems, "stale": stale}


def withheld_text(kind, reason):
    return f"Report withheld ({kind}): {reason}. Unverified figures not sent; {LOG_HINT}."


def _default_send(msg):
    import signal_send  # resolved at call time so tests can install a fake module
    return signal_send.send(msg)


def _dedupe_hit(key, window_s, now):
    cutoff = now - timedelta(seconds=window_s)
    for r in reversed(_read("sent_log.jsonl")):
        if r.get("key") == key:
            ts = parse_ts(r.get("ts"))
            if ts and ts >= cutoff:
                return True
    return False


def deliver(kind, body, claims=(), freshness=(), footer=(), notes=(), send_fn=None,
            dedupe_key=None, dedupe_window_s=3600, now=None, allowed=(), info=None,
            problems=(), transport=True):
    """Verify, then send the verified body OR a single withheld notice. Never raises.
    transport=False: verify + log only (caller prints/relays res["text"])."""
    now = now or utcnow()
    res = {"kind": kind, "status": "error", "text": "", "sent": False, "problems": []}
    try:
        ev = evaluate(body, claims, freshness, now, allowed)
        problems = list(problems) + list(ev["problems"])
        stale_wh = [s for s in ev["stale"] if s["policy"] == "withhold"]
        for s in stale_wh:
            problems.append({"code": f"{s['name']} data STALE ({s['age']} old)",
                             "detail": str(s)})
        if problems:
            reason = problems[0]["code"] + (f" (+{len(problems) - 1} more)" if len(problems) > 1 else "")
            status, text = "withheld", withheld_text(kind, reason)
            key_src = f"{kind}|withheld|{dedupe_key or ''}|" + "|".join(p["code"] for p in problems)
        else:
            head = [f"⚠ STALE: {s['name']} data is {s['age']} old" for s in ev["stale"]]
            tail = [f"⚠ {n}" for n in notes]
            ver = "✓ verified: " + ", ".join(footer) if footer else "✓ verified"
            text = "\n".join(head + [body.strip()[:MAX_LEN]] + tail + [ver])
            status = "verified"
            key_src = f"{kind}|verified|{dedupe_key or body}"
        key = hashlib.sha1(key_src.encode()).hexdigest()[:16]
        res.update(status=status, text=text, problems=problems, stale=ev["stale"], key=key)
        if not transport:
            pass
        elif _dedupe_hit(key, dedupe_window_s, now):
            res["status"] = "dedupe"
        else:
            try:
                ok = (send_fn or _default_send)(text)
                res["sent"] = ok is not False
            except Exception as e:
                res["sent"] = False
                res["transport_error"] = f"{type(e).__name__}: {e}"[:200]
            if res["sent"]:
                _append("sent_log.jsonl", {"ts": now.isoformat(), "kind": kind,
                                           "key": key, "status": status})
            else:
                print(f"[signal_verify] {kind}: Signal transport failed; see verify_log",
                      file=sys.stderr)
    except Exception as e:  # verification itself broke: never crash the cron
        res.update(status="error", error=f"{type(e).__name__}: {e}"[:200],
                   text=withheld_text(kind, "verification error"))
        print(f"[signal_verify] {kind}: verifier error {res['error']}", file=sys.stderr)
        if transport:  # never silently drop: the withheld notice still goes out
            try:
                res["sent"] = (send_fn or _default_send)(res["text"]) is not False
            except Exception:
                res["sent"] = False
    _append("verify_log.jsonl", {
        "ts": now.isoformat(), "kind": kind, "status": res["status"], "sent": res["sent"],
        "problems": res.get("problems"), "stale": res.get("stale"),
        "claims": list(claims), "freshness": list(freshness), "notes": list(notes),
        "info": info, "body": body, "text": res.get("text"),
        "transport_error": res.get("transport_error"), "error": res.get("error")})
    return res


def withhold(kind, code, detail="", send_fn=None, now=None, dedupe_window_s=3600):
    """Source could not be read at all: send the withheld notice (deduped), log detail."""
    return deliver(kind, "", problems=[{"code": code, "detail": str(detail)[:500]}],
                   send_fn=send_fn, now=now, dedupe_window_s=dedupe_window_s)


# ── senders' verified builders ──────────────────────────────────────────────
def pnl_report(get_positions=None, ledger=None, send_fn=None, now=None):
    """11pm P&L: Invested (broker) + Profit (ledger realized, fee-incl.)."""
    kind, now = "11pm P&L", now or utcnow()
    ledger = ledger or read_ledger
    try:
        recs, snap = ledger(), broker_snapshot(get_positions, now)
        recs2, snap2 = ledger(), broker_snapshot(get_positions, now)  # independent re-reads
    except Exception as e:
        return withhold(kind, "source read failed", e, send_fn, now)
    real, real2 = ledger_realized(recs), ledger_realized(recs2)
    day, cut = now.astimezone().date(), now - timedelta(hours=24)  # cron is 11pm local
    last = lambda rows: [s for s in rows  # noqa: E731 — settled in the last 24h
                         if (parse_ts(s.get("logged_at") or s.get("ts")) or cut) > cut]
    today, today2 = last(real["settlements"]), last(real2["settlements"])
    t_pnl = sum(_f(s.get("realized_pnl_net")) for s in today)
    body = (f"💰 Valedia P&L — {day:%b} {day.day}\n"
            f"Invested: {snap['invested']:,.2f} (Kalshi open-position cost, broker)\n"
            f"Profit: {real['total']:+,.2f} Realized (ledger, fee-incl.) "
            f"{real['wins']}W/{real['losses']}L · last 24h {t_pnl:+,.2f}")
    claims = [
        claim("Invested", snap["invested"], snap2["invested"], "broker re-read"),
        claim("open positions", snap["n"], snap2["n"], "broker re-read", tol=0),
        claim("Profit", real["total"], real2["total"], "ledger re-read"),
        sum_claim("Profit total", real["total"], real2["parts"]),
        claim("Profit last 24h", t_pnl, sum(_f(s.get("realized_pnl_net")) for s in today2),
              "ledger re-read"),
        claim("wins", real["wins"], real2["wins"], "ledger re-read", tol=0),
        claim("losses", real["losses"], real2["losses"], "ledger re-read", tol=0),
    ]
    notes, open_n = [], ledger_open_kalshi(recs2)
    if open_n != snap2["n"]:
        notes.append(f"ledger has {open_n} open Kalshi entries vs broker {snap2['n']} positions"
                     " (not reconciled)")
    return deliver(kind, body, claims, [fresh("broker", snap["ts"], 900)],
                   footer=[f"ledger {real['rows']} settled rows", f"broker {hhmm(snap['ts'])}"],
                   notes=notes, send_fn=send_fn, dedupe_key=f"pnl:{day}",
                   dedupe_window_s=20 * 3600, now=now)


def invested_ping(executed, ledger=None, send_fn=None, now=None):
    """Sports 'Invested' ping from ledger fills of the orders just executed."""
    kind, now = "Invested", now or utcnow()
    ledger = ledger or read_ledger
    ids = [r.get("order_id") for r in executed]
    try:
        got, got2 = ledger_order_cost(ledger(), ids), ledger_order_cost(ledger(), ids)
    except Exception as e:
        return withhold(kind, "ledger read failed", e, send_fn, now)
    intent = sum(_f(r.get("cost")) + _f(r.get("fee")) for r in executed)
    body = f"Invested: {got['cost']:,.2f} on {got['n']} games (Kalshi fills + fees, ledger)"
    claims = [claim("games", len(executed), got2["n"], "ledger fills", tol=0),
              claim("Invested", got["cost"], got2["cost"], "ledger re-read")]
    return deliver(kind, body, claims,
                   [fresh("ledger fill", got["oldest_ts"], 1800)],
                   footer=[f"ledger {got['n']} fills", f"{hhmm(now)}"], send_fn=send_fn,
                   dedupe_key="invested:" + ",".join(sorted(str(i) for i in ids)),
                   dedupe_window_s=7 * 86400, now=now,
                   info={"order_intent_cost_incl_fee": round(intent, 4),
                         "ledger_cost": round(got["cost"], 4)})


def profit_ping(parts, body, ledger=None, send_fn=None, now=None):
    """settle_run 'Profit:' ping. parts from settle_run.ftl_profit_parts()."""
    kind, now = "Profit", now or utcnow()
    try:
        real = ledger_realized((ledger or read_ledger)())
    except Exception as e:
        return withhold(kind, "ledger read failed", e, send_fn, now)
    new_ids = {s.get("entry_id") for s in parts["new"]}
    new2 = [s for s in real["settlements"] if s.get("entry_id") in new_ids]
    w2 = sum(1 for s in new2 if s.get("won"))
    claims = [
        claim("Profit", parts["new_pnl"], sum(_f(s.get("realized_pnl_net")) for s in new2),
              "ledger re-read"),
        claim("settled count", len(parts["new"]), len(new2), "ledger re-read", tol=0),
        claim("wins", parts["w"], w2, "ledger re-read", tol=0),
        claim("Total", parts["total_net"], real["total"], "ledger re-read"),
        sum_claim("Total", parts["total_net"], real["parts"]),
        claim("total wins", parts["tw"], real["wins"], "ledger re-read", tol=0),
    ]
    stamps = [s.get("logged_at") or s.get("ts") for s in parts["new"]]
    oldest = min((t for t in stamps if parse_ts(t)), key=lambda t: parse_ts(t), default=None)
    return deliver(kind, body, claims, [fresh("settlement", oldest, 6 * 3600, policy="label")],
                   footer=[f"ledger {real['rows']} settled rows", hhmm(now)],
                   send_fn=send_fn, now=now, dedupe_window_s=7 * 86400,
                   dedupe_key="profit:" + ",".join(sorted(str(i) for i in new_ids)))


# ── ops alerts (no figures): text-only, money numbers stripped ──────────────
MONEY_RE = re.compile(r"[+-]?\$\s*\d[\d,]*(?:\.\d+)?|(?<![\w.])[+-]?\d[\d,]*\.\d{2}(?![.\d])")


def strip_money(text):
    """Replace anything that looks like a money figure with '[figure withheld]'.
    Returns (clean_text, n_stripped). Ops alerts carry no verified money numbers."""
    clean, n = MONEY_RE.subn("[figure withheld]", str(text or ""))
    return clean, n


def ops_alert(kind, body, send_fn=None, now=None, dedupe_key=None, dedupe_window_s=0):
    """Ops/health alert (watchdog etc.): text only, money figures stripped + labelled,
    logged to verify_log. Never raises. dedupe_window_s=0 -> every alert is sent."""
    try:
        clean, n = strip_money(body)
        notes = [f"{n} money figure(s) removed; ops alerts carry no verified money numbers"] if n else []
        return deliver(kind, clean, notes=notes, footer=["ops alert, no money figures"],
                       send_fn=send_fn, now=now, dedupe_key=dedupe_key or clean,
                       dedupe_window_s=dedupe_window_s, info={"stripped": n})
    except Exception as e:
        print(f"[signal_verify] {kind}: ops_alert error {type(e).__name__}: {e}", file=sys.stderr)
        return {"kind": kind, "status": "error", "sent": False, "text": ""}
