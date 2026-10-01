"""signal_verify.py — nothing reaches Burton on Signal unless every figure checks out.

Every figure names its source (broker re-read, fee-incl. ledger re-read, or a labelled
model) and must equal an independent recompute; totals == parts; fresh; no unbacked
figure. Failure -> one number-free "Report withheld (<kind>): <reason>"; detail ->
data/signal/verify_log.jsonl. Dedupe: sent_log.jsonl. Never raises. Reporting only.
(c) GOATWORTHY Post LLC
"""
import hashlib
import json
import os
import re
import math
import sys
import unicodedata
from decimal import ROUND_HALF_UP, Decimal
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
CENT = 0.005 + 1e-9
MAX_LEN = 1400  # chars of body kept in a verified message
# 2-decimal money-like figures; skips version stamps (v0.74.1) and dotted ids.
FIG_RE = re.compile(r"(?<![\w.])[+-]?\d[\d,]*\.\d{2}(?![.\d])")
LEDGER_STALE_OPEN_S = 36 * 3600   # no new ledger row for 36h while positions are open
LEDGER_STALE_ANY_S = 7 * 86400    # no new ledger row for 7 days, positions or not

# ── money detector for code-built pings: any number on a line with a money word,
# currency/% marker or signed decimal must be backed by a claim. Dates/times/versions/IPs skipped.
MONEY_WORDS = tuple("profit profits p&l p/l pnl gain gains gained loss losses lost net balance balances "
                    "invested investment equity realized unrealized payout payouts return returns cash "
                    "deployed made earned earnings earn value worth usd usdc dollar dollars buck bucks "
                    "cent cents ev".split())
_WORD_RE = re.compile(r"(?<![a-z0-9_])(?:" + "|".join(re.escape(w) for w in MONEY_WORDS)
                      + r")(?![a-z0-9_])", re.I)
_MARK_RE = re.compile(r"[$€£¢%]|(?<![\w.])[+\-−]\s?\d[\d,]*\.\d")
_NOISE_RE = re.compile(r"\d{4}-\d{2}-\d{2}(?:[T ][\d:.]+(?:Z|[+-]\d{2}:?\d{2})?)?"
                       r"|(?<!\d)\d{1,2}:\d{2}(?::\d{2})?(?!\d)|v?\d+(?:\.\d+){2,}")
NUM_RE = re.compile(r"(?<![\w.])[+\-−]?\s?[$€£]?\s?(\d[\d,]*(?:\.\d+)?|\.\d+)"
                    r"(?:\s?(k|mm|m|bn|b)(?![a-z]))?(?![a-z\d])", re.I)
_MULT = {"k": 1e3, "m": 1e6, "mm": 1e6, "b": 1e9, "bn": 1e9}


def _denoise(line):  # length-preserving, so token positions match the original line
    return _NOISE_RE.sub(lambda m: " " * len(m.group(0)), str(line or ""))


def _tokens(line):
    return [(m.group(0).strip(), abs(_f(m.group(1).replace(",", ""))
                                    * _MULT.get((m.group(2) or "").lower(), 1.0)))
            for m in NUM_RE.finditer(_denoise(line))]


def is_money_line(line):
    clean = _denoise(line)
    return bool(_WORD_RE.search(clean) or _MARK_RE.search(clean))


def money_tokens(text):
    """[(raw, abs value)] for every numeric token on a money line."""
    return [t for line in str(text or "").splitlines() if is_money_line(line)
            for t in _tokens(line)]


# ── number scrubber (ops alert free text): after NFKC + dropping format chars, every
# numeric char (any script, superscript, fraction, CJK/roman) and number word -> "[n]".
_NUM_WORDS = ("zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen"
              "|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty"
              "|fourty|fifty|sixty|seventy|eighty|ninety|hundreds?|thousands?|millions?"
              "|billions?|trillions?|grand|half|halves|dozens?|quarter|quarters|k|bn|mm")
_WORDNUM_RE = re.compile(r"(?<![^\W\d_])(?:" + _NUM_WORDS + r")(?![^\W\d_])", re.I)
_ROMAN_RE = re.compile(r"(?<![^\W\d_])(?=[MDCLXVI]{2,}(?![^\W\d_]))M{0,3}(?:CM|CD|D?C{0,3})"
                       r"(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3})(?![^\W\d_])")
_RUN_RE = re.compile(r"0(?:[.,'’_\u00a0\u202f ]?0)*(?:[eE][+-]?0+)?(?:(?:k|mm|m|bn|b)(?![^\W\d_]))?")


def _is_num(c):
    return unicodedata.numeric(c, None) is not None


def scrub_numbers(text):
    """(clean, n): every numeral / number word -> "[n]". clean has no numeric char."""
    t = "".join("0" if _is_num(c) else c for c in str(text or ""))  # Ⅻ, ½, 十 before NFKC
    t = unicodedata.normalize("NFKC", t)
    t = t.replace("\u2028", "\n").replace("\u2029", "\n")
    t = "".join(c for c in t if unicodedata.category(c) != "Cf")
    marked = "".join("0" if _is_num(c) else c for c in t)
    out, n, last = [], 0, 0
    for m in _RUN_RE.finditer(marked):  # numeric runs incl. separators/exponent/k-m-b
        out.append(t[last:m.start()] + "[n]")
        last, n = m.end(), n + 1
    t = "".join(out) + t[last:]
    t, k = _WORDNUM_RE.subn("[n]", t)
    t, r = _ROMAN_RE.subn("[n]", t)
    t = "".join("[n]" if _is_num(c) else c for c in t)  # belt and braces
    return t, n + k + r


# ── plumbing ────────────────────────────────────────────────────────────────
def signal_dir():
    return os.environ.get("VALEDIA_SIGNAL_DIR") or os.path.join(ROOT, "data", "signal")


def utcnow():
    return datetime.now(timezone.utc)


def parse_ts(raw):
    try:
        dt = raw if isinstance(raw, datetime) else datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except Exception:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def fmt_age(sec):
    sec = int(abs(sec))
    return (f"{sec}s" if sec < 60 else f"{sec // 60}m" if sec < 3600
            else f"{sec // 3600}h{(sec % 3600) // 60:02d}m" if sec < 86400
            else f"{sec // 86400}d{(sec % 86400) // 3600}h")


def hhmm(ts):
    dt = parse_ts(ts) if ts else None
    return dt.astimezone(timezone.utc).strftime("%H:%M UTC") if dt else "?"


def _append(name, rec):
    try:
        os.makedirs(signal_dir(), exist_ok=True)
        with open(os.path.join(signal_dir(), name), "a") as f:
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


_NUM_STR = re.compile(r"\s*[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\s*", re.ASCII)
NUM_MAX = 1e15  # anything this large is not a real account figure -> invalid


def num(x):
    """Finite float from int/float/ASCII numeric str; else None (bool, NaN, inf, >=1e15)."""
    if x is None or isinstance(x, bool) or (isinstance(x, str) and not _NUM_STR.fullmatch(x)):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) and abs(v) < NUM_MAX else None


def won_flag(v):
    """Explicit win flag: True/False, or the strings "true"/"false". Else None (bad)."""
    s = {"true": True, "false": False}.get(v.strip().lower()) if isinstance(v, str) else v
    return s if isinstance(s, bool) else None


def money(v, signed=False):
    """Half-away cents, sign by the rounded value: no "-$0.00"; "−$5.00"; "+$5.00"."""
    d = Decimal(str(abs(v))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    body = f"${d:,.2f}"
    if d == 0:
        return body
    return ("−" if v < 0 else ("+" if signed else "")) + body


def dsum(values):
    """Exact Decimal(str(x)) sum -> one float for money(): no binary drift."""
    return float(sum((Decimal(str(v)) for v in values), Decimal("0")))


# ── independent source readers ──────────────────────────────────────────────
def _exposure(p):
    """Open-position cost in dollars (new *_dollars field, legacy cents fallback)."""
    if p.get("market_exposure_dollars") is not None:
        v = num(p.get("market_exposure_dollars"))
    else:
        v = num(p.get("market_exposure"))
        v = None if v is None else v / 100.0
    if v is None:
        raise ValueError("position exposure missing/unparseable")
    return abs(v)


def _qty(p):
    for k in ("position_fp", "position"):
        if p.get(k) is not None:
            v = num(p.get(k))
            if v is None:
                raise ValueError("position size unparseable")
            return v
    raise ValueError("position size missing")


def broker_snapshot(get_positions=None, now=None):
    """One fresh broker read. Invested = sum of open-position cost (not market value)."""
    if get_positions is None:
        from kalshi_auth import get_positions  # network; never used in tests
    raw = get_positions()
    if not isinstance(raw, dict) or not isinstance(raw.get("market_positions"), list):
        raise ValueError("positions payload missing market_positions")
    if raw.get("incomplete") or raw.get("cursor"):  # more pages exist -> counts incomplete
        raise ValueError("positions incomplete (pagination)")
    keyed = {}
    for p in raw["market_positions"]:  # duplicates (paging): identical collapse, conflicting -> invalid
        k = (p.get("ticker"), p.get("side")) if isinstance(p, dict) else None
        if k in keyed and keyed[k] != p:
            raise ValueError("positions incomplete (conflicting duplicate rows)")
        keyed[k] = p
    raw = dict(raw, market_positions=list(keyed.values()))
    rows = [p for p in raw["market_positions"] if abs(_qty(p)) > 0]
    if any(_qty(p) == 0 and (num(p.get("market_exposure_dollars")) or num(p.get("market_exposure")))
           for p in raw["market_positions"]):
        raise ValueError("position size zero with exposure (inconsistent)")
    vts = next((raw.get(k) for k in ("ts", "timestamp", "server_time", "as_of", "updated_time")
                if raw.get(k) not in (None, "")), None)  # the venue's own clock, if sent
    return {"invested": dsum(_exposure(p) for p in rows), "n": len(rows),
            "tickers": sorted(p.get("ticker", "") for p in rows),
            "ts": (now or utcnow()).isoformat(), "venue_ts": vts}


def broker_fresh(snap, max_age_s=900):
    """Venue timestamp: absent -> "time unknown"; garbage / future -> not fresh."""
    v = snap.get("venue_ts")
    if v is None or v == "":
        return [], f"broker time unknown (read {hhmm(snap['ts'])})"
    ok = parse_ts(v) if isinstance(v, str) else None
    return [fresh("broker", ok.isoformat() if ok else None, max_age_s)], f"broker {hhmm(ok)}"


def ledger_latest(recs, now=None):
    """(newest LIVE entry/settlement time, future_flag: any live row > 5 min ahead)."""
    now = now or utcnow()
    ent = {r.get("id"): r for r in recs if r.get("type") == "entry"}
    live = [r for r in recs if r.get("type") in ("entry", "settlement")
            and not _non_real(r.get("source")) and not _unrec(r)
            and (r.get("type") == "entry" or not _non_real(ent.get(r.get("entry_id"), {}).get("source")))]
    stamps = [t for t in (parse_ts(r.get("logged_at") or r.get("ts")) for r in live) if t]
    future = any((t - now).total_seconds() > 300 for t in stamps)
    ok = [t for t in stamps if (t - now).total_seconds() <= 300]
    return (max(ok).astimezone(timezone.utc).isoformat() if ok else None), future


def read_ledger():
    """Ledger rows; an invalid / non-object line -> {"type": "_bad_line"} (counted bad)."""
    import forward_test_ledger as ftl
    import ledger_view as lv
    if not os.path.exists(ftl.LEDGER):
        return []
    return [lv.bad_line_row(l) for l in open(ftl.LEDGER, errors="replace") if l.strip()]


def _row(line):  # one ledger line -> row (bad line -> {"type": "_bad_line", ...})
    import ledger_view as lv
    return lv.bad_line_row(line)


def _non_real(source):
    s = str(source or "").lower()
    return any(t in s for t in ("paper", "sandbox", "unreconciled", "backfill", "shadow"))


def _unrec(rec):
    return str(rec.get("fill_status") or "").upper() == "UNRECONCILED"


def ledger_realized(recs, now=None):
    """Admissible settlements via the ONE shared selection (ledger_view.select): live,
    reconciled, first valid settlement per canonical entry id; bad / future / conflicting
    rows counted in "bad" with the shared "reason" text."""
    import ledger_view as lv
    return lv.realized(recs, now or utcnow())


def ledger_open_kalshi(recs):
    done = {str(r.get("entry_id")) for r in recs if r.get("type") == "settlement"}
    return sum(1 for e in recs if e.get("type") == "entry" and str(e.get("id")) not in done
               and str(e.get("venue") or "").startswith("kalshi")
               and not _non_real(e.get("source")) and not _unrec(e))


def ledger_order_cost(recs, order_ids):
    """Fee-incl. cost per DISTINCT filled order id; bad = price not in (0,1], contracts
    <= 0, fee < 0, or any of them missing / garbage."""
    want, by_id = {o for o in order_ids if o}, {}
    for e in recs:
        if e.get("type") == "entry" and e.get("order_id") in want and not _non_real(
                e.get("source")) and not _unrec(e):
            by_id.setdefault(e.get("order_id"), e)
    rows, cost, bad = list(by_id.values()), Decimal("0"), 0
    for e in rows:
        p, c, f = num(e.get("entry_price")), num(e.get("contracts")), num(e.get("entry_fee"))
        if p is None or c is None or f is None or not (0 < p <= 1) or c <= 0 or f < 0:
            bad += 1
        else:
            cost += Decimal(str(p)) * Decimal(str(c)) + Decimal(str(f))
    stamps = [t for t in (parse_ts(e.get("logged_at") or e.get("ts")) for e in rows) if t]
    return {"n": len(rows), "cost": float(cost), "order_ids": sorted(by_id), "bad": bad,
            "oldest_ts": min(stamps).isoformat() if stamps else None}


# ── checks ──────────────────────────────────────────────────────────────────
def claim(name, reported, recomputed, source, tol=CENT):
    return {"name": name, "reported": reported, "recomputed": recomputed,
            "source": source, "tol": tol}


def sum_claim(name, total, parts, source="sum of parts"):
    return claim(name, total, dsum(parts), source)


def fresh(name, ts, max_age_s, policy="withhold"):
    return {"name": name, "ts": ts, "max_age_s": max_age_s, "policy": policy}


def unsourced_figures(text, claims, allowed=()):
    """Money tokens + 2-decimal figures no claim (half-cent) or allowed figure backs."""
    known = [abs(_f(c["reported"])) for c in claims if c.get("reported") is not None]
    exact = [abs(_f(a)) for a in allowed]
    toks = money_tokens(text) + [(m, abs(_f(m.replace(",", ""))))
                                 for m in FIG_RE.findall(_NOISE_RE.sub(" ", text or ""))]
    return [raw for raw, v in toks if not any(abs(v - k) <= CENT for k in known)
            and not any(abs(v - a) <= 1e-9 for a in exact)]


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
    return f"Report withheld ({kind}): {reason}. Unverified figures not sent; see data/signal/verify_log.jsonl."


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
            problems=(), transport=True, verified_line=None):
    """Verify, then send the body OR one withheld notice. Never raises. transport=False:
    verify + log only. res["delivery"]: ok / failed (confirmed) / unknown."""
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
            ver = verified_line or ("✓ verified: " + ", ".join(footer) if footer else "✓ verified")
            text = "\n".join(head + [body.strip()[:MAX_LEN]] + tail + [ver])
            status = "verified"
            key_src = f"{kind}|verified|{dedupe_key or body}"
        key = hashlib.sha1(key_src.encode()).hexdigest()[:16]
        res.update(status=status, text=text, problems=problems, stale=ev["stale"], key=key)
        if transport and dedupe_window_s > 0 and _dedupe_hit(key, dedupe_window_s, now):
            res["status"] = "dedupe"
        elif transport:
            try:
                ok = (send_fn or _default_send)(text)
                res["sent"], res["delivery"] = ok is not False, {True: "ok", False: "failed"}.get(ok, "unknown")
            except Exception as e:
                res["sent"], res["delivery"] = False, "unknown"
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


# ── senders' verified builders (11pm P&L: pnl_run.py; Profit: settle_run.py) ──
def invested_ping(executed, ledger=None, send_fn=None, now=None):
    """Sports 'Invested' ping from ledger fills of the orders just executed."""
    kind, now = "Invested", now or utcnow()
    ledger = ledger or read_ledger
    ids = [r.get("order_id") for r in executed]
    try:
        got, got2 = ledger_order_cost(ledger(), ids), ledger_order_cost(ledger(), ids)
    except Exception as e:
        return withhold(kind, "ledger read failed", e, send_fn, now)
    if got["bad"] or got2["bad"]:
        return withhold(kind, "ledger fill rows unparseable", got, send_fn, now)
    intent = sum(_f(r.get("cost")) + _f(r.get("fee")) for r in executed)
    body = f"Sports fills cost incl. fees (ledger): {money(got['cost'])} on {got['n']} games"
    claims = [claim("games", len(executed), got2["n"], "ledger fills", tol=0),
              claim("Invested", got["cost"], got2["cost"], "ledger re-read")]
    return deliver(kind, body, claims,
                   [fresh("ledger fill", got["oldest_ts"], 1800)],
                   footer=[f"ledger {got['n']} fills", f"{hhmm(now)}"], send_fn=send_fn,
                   dedupe_key="invested:" + ",".join(sorted(str(i) for i in ids)),
                   dedupe_window_s=7 * 86400, now=now,
                   info={"order_intent_cost_incl_fee": round(intent, 4),
                         "ledger_cost": round(got["cost"], 4)})
