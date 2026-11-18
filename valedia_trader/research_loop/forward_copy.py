"""The forward copier, first slice (T54a): the matcher and the paper book. It computes no result.

    python -m valedia_trader.research_loop.forward_copy step   --record PATH --book PATH
                                                                [--kalshi-pause-seconds 10.0] [--kalshi-cap 60]
    python -m valedia_trader.research_loop.forward_copy counts --book PATH

THE RULE is docs/research/2026-11-18-forward-copy-on-webull-prediction.md ("The copy"), with its rule map and listing
table, kept here as data in forward_copy_map.json. `step` is one run after a pass of the leaders' record (the record
is opened read only and never written): it considers each holding of a wallet's first good read and each vouched NEW
of a later good read ONCE, applies the document's conditions in the order of SKIP_REASONS (each skip written with
exactly one reason), and for a holding that passes them all reads Kalshi's public book once (the MATCH READ, stamped
with this program's own clock) and writes ONE paper contract of that side at the ask. It then closes open copies:
(a) Kalshi's market is finalized: the side's settlement value and time; (b) the opening wallet's EXIT in the record
after the copy's read: one book read, the side's bid; (c) the venues part, as far as the two services show it:
flagged, held to Kalshi's settlement. `counts` prints counts only.

No command, function or test here computes, stores or prints a result, a profit, a mean or a sum of prices of copies
(the document: no result before the count is reached; the one read is a later slice). Nothing here sends an order,
previews one, holds a key, or runs itself: whether and when `step` runs is the owner's decision.

Requests. EVERY request to Kalshi goes through ONE pacer (StepPacer, installed as the KalshiData client's own pacer,
so every try of every call waits on it): a pause between requests (--kalshi-pause-seconds, default 10.0, never under
5.0: a collector on this machine reads the same host) and a cap a step (--kalshi-cap, default 60). At the cap the step
stops cleanly between holdings (the holding being worked on is written not at all) and says so; the next step carries
on. KalshiData is built with tries=1, so a 429 or a server error is a failed read, never a retry. The calls used:
`pages("/events", ...)` (one list of a series' open events with their markets, per series reached in a step),
`orderbook` (the match read; an exit's bid read) and `market` (a settlement check; on a 404 from the live tier it asks
the historical tier, so a check costs one request, two on that 404). Polymarket's market-description service is read
by GammaReader here: GET only, one host, one path, the parameters condition_ids and closed, the generic client name,
no key, its own pause; a market's text is asked once and kept by condition id.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from valedia_trader.research_loop import leaders_monitor as lm
from valedia_trader.venues import kalshi_data as kd

MAP_PATH = Path(__file__).with_name("forward_copy_map.json")
DEFAULT_PAUSE = 10.0
MIN_PAUSE = 5.0
DEFAULT_CAP = 60
MIN_CAP = 2                                  # one page of a series' list and one book: what one holding needs
GAMMA_PAUSE = 0.34                          # the other Polymarket readers' manners: about three a second
MIN_VALUE = 1000.0
PRICE_LO, PRICE_HI = 0.10, 0.90
ASK_LO, ASK_HI = 0.05, 0.95
MATCH_WINDOW = 6 * 3600.0                    # the match read falls within 6 hours of the pass's read of that wallet
DAY = 86_400
USER_VERSION = 1
WAIT_SECONDS = 5.0
OK, REFUSED, CAPPED, OTHER_SHAPE, UNREADABLE, BUSY = 0, 3, 5, 7, 8, 9

# The skip reasons, in the order they are applied (see docs/forward-copier.md for why the order cannot change which
# copies are made). `late` and `read_failed` are tested more than once along the way; the first failing test decides.
SKIP_REASONS = (
    "holding_not_in_record",     # the holding's row at that read is no longer in the record (value unknown)
    "value_under_1000",          # the holding's value at that read is under 1,000 dollars
    "price_outside",             # Polymarket's price for it is outside 0.10 to 0.90 (or unknown)
    "no_same",                   # no counterpart of class SAME in the rule map (a NEAR one goes to the NEAR book)
    "not_listed",                # its series is not in the listing table
    "name_not_in_table",         # a team, club or person not in the map's tables (never guessed)
    "leaders_disagree",          # another wallet of the set holds the opposite outcome at that pass
    "late",                      # the match read would fall (or fell) later than 6 hours after the pass's read
    "read_failed",               # a read that failed (Kalshi's list or book, or Polymarket's description)
    "no_unique_market",          # not exactly one Kalshi market for it
    "market_not_open",           # Kalshi's market not open at the match read
    "text_departs",              # a venue's text departs from the map's template, names and dates aside
    "already_open",              # a copy of that Kalshi market and side is already open (the wallet is noted on it)
    "ask_outside",               # the ask of the copy's side is outside 0.05 to 0.95, or there is none
)
FLAG_KINDS = ("exit_no_bid", "parted_pm_closed_first", "parted_pm_trading_at_settlement",
              "parted_settled_differently", "pm_unresolved_at_settlement", "part_check_failed",
              "kalshi_fair_price")
HOLD_FLAGS = ("exit_no_bid", "parted_pm_closed_first")          # a copy so flagged is held to Kalshi's settlement
TICKER_RE = re.compile(r"[A-Z][A-Z0-9.\-]{0,79}")
CONDITION_RE = re.compile(r"0x[0-9a-f]{64}")
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")
WDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONS = tuple(m[:3] for m in MONTHS)


class CapReached(Exception):
    """The step's cap of Kalshi requests is reached: raised before the request, so it is never sent."""


class GammaError(RuntimeError):
    """Polymarket's description service could not be read, or answered in another shape."""


class Skip(Exception):
    def __init__(self, reason: str, detail: str = ""):
        assert reason in SKIP_REASONS, reason
        super().__init__(reason)
        self.reason, self.detail = reason, detail[:300]


# ---------------------------------------------------------------- the map
def load_map(path: Path = MAP_PATH) -> dict:
    m = json.loads(Path(path).read_text())
    m["_listed"] = {row["series"] for row in m["listing_table"]}
    m["_compiled"] = {s: {k: compile_template(t) for k, t in e["templates"].items()} for s, e in m["series"].items()}
    m["_tables"] = {k: {r["polymarket"]: r for r in rows} for k, rows in m["names"].items()}
    m["_slugs"] = {r["slug"]: r for r in m["names"]["nba"]}
    return m


SLOT = re.compile(r"\{(\w+)(?::(\w+))?\}")
SLOT_KINDS = {
    "name": r"[A-Z0-9][A-Za-z0-9.'&\- ]*?",
    "words": r"[A-Z][A-Za-z.'\- ]*?",
    "wday": "(?:%s)" % "|".join(WDAYS),
    "month": "(?:%s)" % "|".join(MONTHS),
    "mon": "(?:%s)" % "|".join(MONS),
    "day": r"\d{1,2}",
    "year": r"\d{4}",
    "time": r"\d{1,2}:\d{2} (?:AM|PM)",
    "line": r"\d+\.5",
    "int": r"\d+",
}


def normal(text: str) -> str:
    """A text with every run of white space made one space, and trimmed: a line break is not a departure."""
    return re.sub(r"\s+", " ", text).strip()


def compile_template(template: str):
    out, pos, seen = [], 0, set()
    t = normal(template)
    for m in SLOT.finditer(t):
        out.append(re.escape(t[pos:m.start()]))
        name, kind = m.group(1), m.group(2)
        if kind:
            if name in seen:
                raise ValueError("slot %s named twice with a kind" % name)
            seen.add(name)
            out.append("(?P<%s>%s)" % (name, SLOT_KINDS[kind]))
        else:
            if name not in seen:
                raise ValueError("slot %s repeated before it is defined" % name)
            out.append("(?P=%s)" % name)
        pos = m.end()
    out.append(re.escape(t[pos:]))
    return re.compile("".join(out))


def fill(template: str, **slots) -> str:
    """A template with its slots filled (for stand-in texts in tests and for reading the docs)."""
    return SLOT.sub(lambda m: str(slots[m.group(1)]), normal(template))


# ---------------------------------------------------------------- recognising a holding
def recognise(h: dict, fmap: dict) -> tuple:
    """("same", series, parts) | ("near", family) | ("none", why): what the holding is, from its title, outcome and
    event slug alone. A spread on a whole-number line is "none" (the map's entry is for half-point lines only)."""
    title, slug, outcome = h.get("title") or "", h.get("event_slug") or "", h.get("outcome") or ""
    for s, e in fmap["series"].items():
        rs = e["recognise"]
        mt, ms = re.fullmatch(rs["title"], title), re.fullmatch(rs["event_slug"], slug)
        if not (mt and ms):
            continue
        parts = {"slug": ms.groupdict(), "title": mt.groupdict(), "outcome": outcome}
        if s == "KXNBASPREAD":
            line = float(parts["title"]["line"])
            if not (line * 2) % 2 == 1:
                return ("none", "a spread on a whole-number line")
        if s in ("KXPREMIERLEAGUE", "KXPRESNOMD") and outcome not in ("Yes", "No"):
            return ("none", "an outcome other than Yes or No")
        return ("same", s, parts)
    for fam in fmap["near_families"]:
        if re.fullmatch(fam["title"], title):
            return ("near", fam["family"])
    return ("none", "no entry of the rule map")


def resolve_names(series: str, parts: dict, fmap: dict) -> dict:
    """The holding's names through the map's tables, or Skip(name_not_in_table). Never a guess."""
    slug, title = parts["slug"], parts["title"]
    nba = fmap["_tables"]["nba"]
    if series in ("KXNBAGAME", "KXNBASPREAD"):
        ra, rb = fmap["_slugs"].get(slug["a"]), fmap["_slugs"].get(slug["b"])
        if ra is None or rb is None:
            raise Skip("name_not_in_table", "a team code of the slug is not in the table")
        try:
            day = _date(slug["date"])
        except ValueError:
            raise Skip("name_not_in_table", "the slug's date is not a date") from None
        if series == "KXNBAGAME":
            ta, tb = nba.get(title["a"]), nba.get(title["b"])
            if ta is None or tb is None:
                raise Skip("name_not_in_table", "a team of the title is not in the table")
            if ta is not ra or tb is not rb:
                raise Skip("name_not_in_table", "the title's teams are not the slug's")
            held = {ta["polymarket"]: ta, tb["polymarket"]: tb}.get(parts["outcome"])
            if held is None:
                raise Skip("name_not_in_table", "the outcome is neither team of the title")
            return {"a": ra, "b": rb, "held": held, "date": day, "side": "yes"}
        fav = nba.get(title["fav"])
        if fav is None:
            raise Skip("name_not_in_table", "the spread's favourite is not in the table")
        if fav not in (ra, rb):
            raise Skip("name_not_in_table", "the spread's favourite is neither team of the slug")
        dog = rb if fav is ra else ra
        if parts["outcome"] == fav["polymarket"]:
            side = "yes"
        elif parts["outcome"] == dog["polymarket"]:
            side = "no"
        else:
            raise Skip("name_not_in_table", "the outcome is neither team of the game")
        return {"a": ra, "b": rb, "fav": fav, "dog": dog, "held": fav, "line": title["line"], "date": day,
                "side": side}
    table, key = {"KXPREMIERLEAGUE": ("epl", "club"), "KXPRESNOMD": ("dem_nominee_2028", "person")}[series]
    row = fmap["_tables"][table].get(title[key])
    if row is None:
        raise Skip("name_not_in_table", "%s not in the table" % key)
    return {"held": row, "side": "yes" if parts["outcome"] == "Yes" else "no"}


def _date(text: str) -> date:
    return date.fromisoformat(text)


def kalshi_day(d: date) -> str:
    return "%02d%s%02d" % (d.year % 100, MONS[d.month - 1].upper(), d.day)


# ---------------------------------------------------------------- finding the Kalshi market
def find_market(series: str, names: dict, events: list) -> tuple:
    """(event, market) for the holding: exactly one market of the series' open events, or Skip(no_unique_market)."""
    found = []
    if series in ("KXNBAGAME", "KXNBASPREAD"):
        day = kalshi_day(names["date"])
        ca, cb = names["a"]["kalshi_code"], names["b"]["kalshi_code"]
        evs = {"%s-%s%s%s" % (series, day, ca, cb), "%s-%s%s%s" % (series, day, cb, ca)}
        held = names["held"]
        if series == "KXNBAGAME":
            want = lambda ev, m: (m.get("ticker") == ev["event_ticker"] + "-" + held["kalshi_code"]
                                  and m.get("yes_sub_title") == held["kalshi_words"])
        else:
            line = names["line"]
            up = int(float(line) + 0.5)
            want = lambda ev, m: (m.get("ticker") == "%s-%s%d" % (ev["event_ticker"], held["kalshi_code"], up)
                                  and _num(m.get("floor_strike")) == float(line)
                                  and m.get("yes_sub_title") == "%s wins by over %s points" % (held["kalshi_words"],
                                                                                              line))
    else:
        evs = {{"KXPREMIERLEAGUE": "KXPREMIERLEAGUE-27", "KXPRESNOMD": "KXPRESNOMD-28"}[series]}
        held = names["held"]
        want = lambda ev, m: (m.get("ticker") == ev["event_ticker"] + "-" + held["kalshi_code"]
                              and m.get("yes_sub_title") == held["kalshi_words"])
    for ev in events:
        if ev.get("event_ticker") in evs:
            found += [(ev, m) for m in ev.get("markets") or [] if isinstance(m, dict) and want(ev, m)]
    if len(found) != 1:
        raise Skip("no_unique_market", "%d markets found" % len(found))
    ev, m = found[0]
    if not (isinstance(m.get("ticker"), str) and TICKER_RE.fullmatch(m["ticker"])):
        raise Skip("no_unique_market", "a ticker not in Kalshi's shape")
    return ev, m


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def iso_ts(text) -> float | None:
    """A UTC ISO time in epoch seconds, or None when it is not one."""
    if not isinstance(text, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z", text):
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def market_open(m: dict, at: float) -> bool:
    """Kalshi's market is open at `at`: status active and its close time later than `at`."""
    close = iso_ts(m.get("close_time"))
    return m.get("status") == "active" and close is not None and close > at


# ---------------------------------------------------------------- the texts
def check_texts(series: str, names: dict, ev: dict, m: dict, pm_text: str, fmap: dict) -> None:
    """Both venues' texts against the map's templates, names and dates aside; Skip(text_departs) otherwise."""
    tpl = fmap["_compiled"][series]
    gp = tpl["polymarket"].fullmatch(normal(pm_text or ""))
    gk = tpl["kalshi_primary"].fullmatch(normal(m.get("rules_primary") or ""))
    gs = tpl["kalshi_secondary"].fullmatch(normal(m.get("rules_secondary") or ""))
    if not gp:
        raise Skip("text_departs", "Polymarket's text")
    if not gk or not gs:
        raise Skip("text_departs", "Kalshi's text")
    p, k = gp.groupdict(), gk.groupdict()
    held = names["held"]
    if k["team"] != held["kalshi_words"]:
        raise Skip("text_departs", "Kalshi's text names another")
    if series in ("KXNBAGAME", "KXNBASPREAD"):
        a, b, d = names["a"], names["b"], names["date"]
        both = {a["polymarket"], b["polymarket"]}
        if {p["t1"], p["t2"]} != both:
            raise Skip("text_departs", "Polymarket's text names other teams")
        if not _same_day(p, "", d) or not _same_day(p, "2", d + timedelta(days=2)):
            raise Skip("text_departs", "Polymarket's dates")
        if "%s vs %s" % (k["k1"], k["k2"]) != (ev.get("title") or "").split(":")[0] or \
                {k["k1"], k["k2"]} != {a["kalshi_words"], b["kalshi_words"]}:
            raise Skip("text_departs", "Kalshi's text names other teams")
        if (k["kmon"], int(k["kday"]), int(k["kyear"])) != (MONS[d.month - 1], d.day, d.year):
            raise Skip("text_departs", "Kalshi's date")
        if series == "KXNBAGAME":
            if {p["w1"], p["w2"]} != both:
                raise Skip("text_departs", "Polymarket's text names other winners")
        else:
            line = names["line"]
            if (p["fav"], p["dog"]) != (names["fav"]["polymarket"], names["dog"]["polymarket"]):
                raise Skip("text_departs", "Polymarket's favourite")
            if int(p["points"]) != int(float(line) + 0.5) or k["kline"] != line:
                raise Skip("text_departs", "the line")


def _same_day(g: dict, n: str, d: date) -> bool:
    try:
        return date(int(g["year" + n]), MONTHS.index(g["month" + n]) + 1, int(g["day" + n])) == d and \
            WDAYS[d.weekday()] == g["wday" + n]
    except (ValueError, KeyError):
        return False


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def kalshi_text(m: dict) -> str:
    return (m.get("rules_primary") or "") + "\n\n" + (m.get("rules_secondary") or "")


# ---------------------------------------------------------------- the readers
class StepPacer:
    """The ONE pacer of a step's Kalshi requests: at least `pause` seconds between the starts of two requests, and at
    most `cap` requests; the request past the cap raises CapReached before it is sent."""

    def __init__(self, pause: float, cap: int, sleep: Callable[[float], Any] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        if not pause >= MIN_PAUSE:
            raise ValueError("the pause between Kalshi requests is never under %.1f s" % MIN_PAUSE)
        if not (isinstance(cap, int) and cap >= 1):
            raise ValueError("the cap is a whole number of at least 1")
        self.pause, self.cap, self.sleep, self.clock = float(pause), cap, sleep, clock
        self.last: float | None = None
        self.count = 0

    def wait(self) -> None:
        if self.count >= self.cap:
            raise CapReached("the cap of %d Kalshi requests a step" % self.cap)
        if self.last is not None:
            gap = self.clock() - self.last
            if gap < self.pause:
                self.sleep(self.pause - gap)
        self.last = self.clock()
        self.count += 1


def kalshi_client(fetch: Callable[[str], Any], pacer: StepPacer) -> kd.KalshiData:
    """KalshiData with one try a call and its own pacer replaced by the step's pacer."""
    client = kd.KalshiData(fetch, tries=1, sleep=pacer.sleep, clock=pacer.clock)
    client.pacer = pacer
    return client


GAMMA_BASE = "https://gamma-api.polymarket.com"
GAMMA_URL = re.compile(re.escape(GAMMA_BASE) + r"/markets\?condition_ids=0x[0-9a-f]{64}&closed=(?:true|false)")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def gamma_urllib_get(url: str, timeout: float = 60.0) -> bytes:
    """GET one address of Polymarket's description service (the allow-list: one host, one path, two parameters);
    any other address is refused before anything is sent; a redirect is never followed; not a 200 is an error."""
    if not (isinstance(url, str) and GAMMA_URL.fullmatch(url)):
        raise GammaError("refused: %s is not an address this reader asks" % url)
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": kd.USER_AGENT},
                                 method="GET")
    try:
        with urllib.request.build_opener(_NoRedirect()).open(req, timeout=timeout) as resp:
            if (getattr(resp, "status", None) or resp.getcode()) != 200:
                raise GammaError("HTTP %s" % resp.getcode())
            return resp.read()
    except urllib.error.HTTPError as e:
        raise GammaError("HTTP %s" % e.code) from None
    except (urllib.error.URLError, OSError) as e:
        raise GammaError(type(e).__name__) from None


class GammaReader:
    """Polymarket's public market-description service, read only: GET /markets?condition_ids=<id>&closed=<bool>."""

    def __init__(self, fetch: Callable[[str], Any] = gamma_urllib_get, pause: float = GAMMA_PAUSE,
                 sleep: Callable[[float], Any] = time.sleep, clock: Callable[[], float] = time.monotonic):
        self.fetch, self.pacer, self.requests = fetch, kd.Pacer(pause, sleep, clock), 0

    def ask(self, condition_id: str, closed: bool) -> list:
        if not (isinstance(condition_id, str) and CONDITION_RE.fullmatch(condition_id)):
            raise GammaError("a condition id not in Polymarket's shape")
        url = "%s/markets?condition_ids=%s&closed=%s" % (GAMMA_BASE, condition_id, "true" if closed else "false")
        if not GAMMA_URL.fullmatch(url):
            raise GammaError("refused")
        self.pacer.wait()
        self.requests += 1
        try:
            body = self.fetch(url)
        except GammaError:
            raise
        except Exception as e:                  # a stand-in's or the network's error is this reader's failure
            raise GammaError(type(e).__name__) from None
        if isinstance(body, tuple):
            body = body[0]
        try:
            rows = json.loads(body)
        except (ValueError, TypeError):
            raise GammaError("not JSON") from None
        if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
            raise GammaError("not a list of markets")
        return [r for r in rows if r.get("conditionId") == condition_id]

    def market(self, condition_id: str, closed_first: bool) -> dict | None:
        """The one market of that id, asked first with closed=`closed_first` and, only when that answers none, with
        the other; None when neither has it. A second answer of more than one market is a GammaError."""
        for closed in (closed_first, not closed_first):
            rows = self.ask(condition_id, closed)
            if len(rows) > 1:
                raise GammaError("more than one market for one id")
            if rows:
                return rows[0]
        return None


# ---------------------------------------------------------------- the book
BOOK_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS steps (step_id INTEGER PRIMARY KEY AUTOINCREMENT, started_ts REAL NOT NULL, finished_ts REAL,
    status TEXT NOT NULL, kalshi_requests INTEGER, gamma_requests INTEGER, considered INTEGER, opened INTEGER,
    closed INTEGER);
CREATE TABLE IF NOT EXISTS considered (wallet_key TEXT NOT NULL, read_id INTEGER NOT NULL, token_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('baseline', 'new')), change_id INTEGER, pass_id INTEGER,
    pass_read_ts REAL NOT NULL, step_id INTEGER NOT NULL, considered_ts REAL NOT NULL,
    outcome TEXT NOT NULL CHECK (outcome IN ('copy', 'skip')), PRIMARY KEY (wallet_key, read_id, token_id));
CREATE TABLE IF NOT EXISTS skips (wallet_key TEXT NOT NULL, read_id INTEGER NOT NULL, token_id TEXT NOT NULL,
    reason TEXT NOT NULL CHECK (reason IN (%(reasons)s)), detail TEXT, ts REAL NOT NULL, step_id INTEGER NOT NULL,
    PRIMARY KEY (wallet_key, read_id, token_id));
CREATE TABLE IF NOT EXISTS texts (text_hash TEXT PRIMARY KEY, venue TEXT NOT NULL CHECK (venue IN ('polymarket',
    'kalshi')), body TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pm_descriptions (condition_id TEXT PRIMARY KEY, read_ts REAL NOT NULL,
    text_hash TEXT NOT NULL, question TEXT, outcomes TEXT, closed INTEGER);
CREATE TABLE IF NOT EXISTS copies (copy_id INTEGER PRIMARY KEY AUTOINCREMENT, wallet_key TEXT NOT NULL,
    read_id INTEGER NOT NULL, token_id TEXT NOT NULL, condition_id TEXT NOT NULL, pm_outcome TEXT,
    series TEXT NOT NULL, kalshi_event TEXT NOT NULL, kalshi_market TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('yes', 'no')), pass_read_ts REAL NOT NULL, match_ts REAL NOT NULL,
    ask REAL NOT NULL, ask_size REAL, pm_text_hash TEXT NOT NULL, kalshi_text_hash TEXT NOT NULL, book_raw TEXT,
    step_id INTEGER NOT NULL, status TEXT NOT NULL CHECK (status IN ('open', 'closed')),
    close_kind TEXT CHECK (close_kind IN ('settled', 'exit')), close_ts REAL, close_price REAL, close_detail TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS copies_one_open ON copies (kalshi_market, side) WHERE status = 'open';
CREATE TABLE IF NOT EXISTS copy_wallets (copy_id INTEGER NOT NULL, wallet_key TEXT NOT NULL, read_id INTEGER NOT NULL,
    token_id TEXT NOT NULL, role TEXT NOT NULL CHECK (role IN ('opened', 'noted')), ts REAL NOT NULL,
    PRIMARY KEY (copy_id, wallet_key, read_id, token_id));
CREATE TABLE IF NOT EXISTS copy_flags (copy_id INTEGER NOT NULL, kind TEXT NOT NULL CHECK (kind IN (%(flags)s)),
    ts REAL NOT NULL, detail TEXT);
CREATE TABLE IF NOT EXISTS near_book (wallet_key TEXT NOT NULL, read_id INTEGER NOT NULL, token_id TEXT NOT NULL,
    condition_id TEXT, family TEXT NOT NULL, title TEXT, outcome TEXT, pass_read_ts REAL NOT NULL, ts REAL NOT NULL,
    step_id INTEGER NOT NULL, PRIMARY KEY (wallet_key, read_id, token_id));
CREATE TRIGGER IF NOT EXISTS copies_closed_never_change BEFORE UPDATE ON copies
    WHEN OLD.status != 'open' OR NEW.status != 'closed' OR NEW.close_kind IS NULL OR NEW.close_ts IS NULL
      OR NEW.close_price IS NULL
      OR NEW.copy_id IS NOT OLD.copy_id OR NEW.wallet_key IS NOT OLD.wallet_key OR NEW.read_id IS NOT OLD.read_id
      OR NEW.token_id IS NOT OLD.token_id OR NEW.condition_id IS NOT OLD.condition_id
      OR NEW.pm_outcome IS NOT OLD.pm_outcome OR NEW.series IS NOT OLD.series
      OR NEW.kalshi_event IS NOT OLD.kalshi_event OR NEW.kalshi_market IS NOT OLD.kalshi_market
      OR NEW.side IS NOT OLD.side OR NEW.pass_read_ts IS NOT OLD.pass_read_ts OR NEW.match_ts IS NOT OLD.match_ts
      OR NEW.ask IS NOT OLD.ask OR NEW.ask_size IS NOT OLD.ask_size OR NEW.pm_text_hash IS NOT OLD.pm_text_hash
      OR NEW.kalshi_text_hash IS NOT OLD.kalshi_text_hash OR NEW.book_raw IS NOT OLD.book_raw
      OR NEW.step_id IS NOT OLD.step_id
    BEGIN SELECT RAISE(ABORT, 'a copy is updated only to close it, and a closed copy never changes'); END;
CREATE TRIGGER IF NOT EXISTS copies_never_deleted BEFORE DELETE ON copies
    BEGIN SELECT RAISE(ABORT, 'a copy is never deleted'); END;
CREATE TRIGGER IF NOT EXISTS flags_only_while_open BEFORE INSERT ON copy_flags
    WHEN (SELECT status FROM copies WHERE copy_id = NEW.copy_id) IS NOT 'open'
    BEGIN SELECT RAISE(ABORT, 'a closed copy never changes'); END;
CREATE TRIGGER IF NOT EXISTS wallets_only_while_open BEFORE INSERT ON copy_wallets
    WHEN (SELECT status FROM copies WHERE copy_id = NEW.copy_id) IS NOT 'open'
    BEGIN SELECT RAISE(ABORT, 'a closed copy never changes'); END;
CREATE TRIGGER IF NOT EXISTS considered_fixed BEFORE UPDATE ON considered
    BEGIN SELECT RAISE(ABORT, 'what was considered never changes'); END;
CREATE TRIGGER IF NOT EXISTS considered_kept BEFORE DELETE ON considered
    BEGIN SELECT RAISE(ABORT, 'what was considered never changes'); END;
CREATE TRIGGER IF NOT EXISTS skips_fixed BEFORE UPDATE ON skips
    BEGIN SELECT RAISE(ABORT, 'a skip never changes'); END;
CREATE TRIGGER IF NOT EXISTS skips_kept BEFORE DELETE ON skips
    BEGIN SELECT RAISE(ABORT, 'a skip never changes'); END;
""" % {"reasons": ", ".join("'%s'" % r for r in SKIP_REASONS), "flags": ", ".join("'%s'" % f for f in FLAG_KINDS)}


def _master(conn) -> list:
    return sorted(tuple(r) for r in conn.execute("SELECT type, name, tbl_name, sql FROM sqlite_master"))


def book_shape_problem(path: Path):
    """None when `path` holds exactly this version's book (schema and user_version); else what differs. Read only."""
    mem = sqlite3.connect(":memory:")
    try:
        mem.executescript(BOOK_SCHEMA)
        want = _master(mem)
    finally:
        mem.close()
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=WAIT_SECONDS)
    try:
        have = _master(conn)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()
    if version != USER_VERSION:
        return "user_version %d, this version's is %d" % (version, USER_VERSION)
    if have != want:
        extra = sorted(set(have) - set(want))
        missing = sorted(set(want) - set(have))
        return "%s %s" % ("an extra" if extra else "no", lm._safe((extra or missing)[0][1]))
    return None


class Book:
    """The paper book. Every write goes through `w`, which calls `hook(sql)` first (a test's stand-in raises there)."""

    def __init__(self, conn: sqlite3.Connection, hook: Callable[[str], None] | None = None):
        self.conn, self.hook = conn, hook or (lambda sql: None)

    def w(self, sql: str, params=()):
        self.hook(sql)
        return self.conn.execute(sql, params)

    def tx(self, fn: Callable[[], Any]):
        """`fn`'s writes together or not at all."""
        self.w("BEGIN IMMEDIATE")
        try:
            out = fn()
            self.w("COMMIT")
            return out
        except BaseException:
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
            raise

    def q(self, sql: str, params=()):
        return self.conn.execute(sql, params).fetchall()


def open_book(path: Path, create: bool):
    """(Book connection, None) or (None, (code, words)). Only `step` creates a book, at its first run."""
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        if not create:
            return None, (REFUSED, "refused: no book at %s (only step makes one); nothing done" % path)
        try:
            conn = sqlite3.connect(str(path), timeout=WAIT_SECONDS, isolation_level=None)
            conn.executescript(BOOK_SCHEMA)
            conn.execute("PRAGMA user_version = %d" % USER_VERSION)
        except sqlite3.Error as e:
            return None, (REFUSED, "refused: the book %s could not be made (%s); nothing done" % (path, type(e).__name__))
        return conn, None
    try:
        why = book_shape_problem(path)
    except sqlite3.Error as e:
        if lm._busy(e):
            return None, (BUSY, "refused: the book %s is busy; nothing done" % path)
        return None, (UNREADABLE, "refused: %s is not readable as a book (%s); nothing done" % (path, type(e).__name__))
    if why:
        return None, (OTHER_SHAPE, "refused: %s was made by another version of the copier, or is not its book (%s); "
                                   "nothing done" % (path, why))
    return sqlite3.connect(str(path), timeout=WAIT_SECONDS, isolation_level=None), None


# ---------------------------------------------------------------- the record (read only)
def open_record(path: Path):
    """The leaders' record opened READ ONLY after its own shape check; (conn, None) or (None, (code, words))."""
    path = Path(path)
    try:
        why, none = lm._shape_problem(path)
    except sqlite3.Error as e:
        return None, (UNREADABLE, "refused: the record %s is not readable (%s); nothing done" % (path, type(e).__name__))
    if why:
        return None, (OTHER_SHAPE, "refused: the record %s is not this version's record (%s); nothing done"
                      % (path, why))
    if none:
        return None, (REFUSED, "refused: no record at %s; nothing done" % path)
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=lm.WAIT_SECONDS), None


HCOLS = ("condition_id", "opposite_token_id", "outcome_index", "size", "avg_price", "current_value", "title",
         "outcome", "end_date", "current_price", "event_slug")


def candidates(rec, considered: set) -> list:
    """Every holding not yet considered: each holding of a wallet's first good read (its baseline), and each vouched
    NEW of a later good read; in the order of the pass's read time, then wallet and token (a fixed order, so which
    wallet opens a copy and which is noted is fixed too)."""
    cols = ", ".join(HCOLS)
    out = []
    for (key,) in rec.execute("SELECT wallet_key FROM leaders ORDER BY wallet_key"):
        base = rec.execute("SELECT read_id, read_ts, pass_id, baseline FROM wallet_reads WHERE wallet_key=? AND good=1 "
                           "ORDER BY read_id LIMIT 1", (key,)).fetchone()
        if base is not None and base[3] == 1:
            for r in rec.execute("SELECT token_id, %s FROM snapshots WHERE read_id=? AND wallet_key=? AND "
                                 "kind='baseline' ORDER BY token_id" % cols, (base[0], key)):
                if (key, base[0], r[0]) not in considered:
                    out.append({"wallet_key": key, "read_id": base[0], "read_ts": base[1], "pass_id": base[2],
                                "token_id": r[0], "kind": "baseline", "change_id": None, **dict(zip(HCOLS, r[1:]))})
        for change_id, read_id, tok, read_ts, pass_id in rec.execute(
                "SELECT c.change_id, c.read_id, c.token_id, c.read_ts, r.pass_id FROM changes c JOIN wallet_reads r "
                "ON r.read_id = c.read_id WHERE c.wallet_key=? AND c.kind='NEW' AND c.vouched=1 ORDER BY c.read_id, "
                "c.token_id", (key,)).fetchall():
            if (key, read_id, tok) in considered:
                continue
            row = rec.execute("SELECT %s FROM snapshots WHERE read_id=? AND wallet_key=? AND token_id=?" % cols,
                              (read_id, key, tok)).fetchone() or \
                rec.execute("SELECT %s FROM holdings_current WHERE wallet_key=? AND token_id=? AND read_id=? AND "
                            "carried=0" % cols, (key, tok, read_id)).fetchone()
            h = {"wallet_key": key, "read_id": read_id, "read_ts": read_ts, "pass_id": pass_id, "token_id": tok,
                 "kind": "new", "change_id": change_id}
            h.update(dict(zip(HCOLS, row)) if row else {"missing": True})
            out.append(h)
    return sorted(out, key=lambda h: (h["read_ts"], h["read_id"], h["wallet_key"], h["token_id"]))


def leaders_disagree(rec, h: dict) -> bool:
    """Another wallet of the set holds another outcome of the same market at that pass: in its holdings now (carried
    ones too) or in a snapshot taken in that pass. Either is taken as holding it (the reading that makes fewer
    copies)."""
    cid, key, tok = h.get("condition_id"), h["wallet_key"], h["token_id"]
    if cid is None:
        return False
    if rec.execute("SELECT 1 FROM holdings_current WHERE condition_id=? AND wallet_key!=? AND token_id!=? LIMIT 1",
                   (cid, key, tok)).fetchone():
        return True
    return rec.execute("SELECT 1 FROM snapshots s JOIN wallet_reads r ON r.read_id = s.read_id WHERE r.pass_id=? AND "
                       "s.condition_id=? AND s.wallet_key!=? AND s.token_id!=? LIMIT 1",
                       (h["pass_id"], cid, key, tok)).fetchone() is not None


def first_exit(rec, wallet_key: str, token_id: str, after_read_id: int):
    """The opening wallet's first EXIT of that token in a read after the copy's read: (change_id, read_id, read_ts)."""
    return rec.execute("SELECT change_id, read_id, read_ts FROM changes WHERE wallet_key=? AND token_id=? AND "
                       "kind='EXIT' AND read_id>? ORDER BY read_id LIMIT 1",
                       (wallet_key, token_id, after_read_id)).fetchone()


# ---------------------------------------------------------------- the quotes
def side_quote(book: dict, side: str) -> dict:
    """The copy's side in Kalshi's book (bids only): its ask is one minus the best bid of the other side, the
    contracts on offer at it that bid's size; its bid is its own best bid."""
    own, other = (book.get("yes") or [], book.get("no") or []) if side == "yes" else \
        (book.get("no") or [], book.get("yes") or [])
    return {"ask": round(1.0 - other[0][0], 6) if other else None, "ask_size": other[0][1] if other else None,
            "bid": own[0][0] if own else None}


def settlement(record: dict, ticker: str, side: str, now: float):
    """(what one contract of `side` is settled at, settlement time, fair_price) when Kalshi's record shows a final
    settlement of that market, else None. Final: status exactly "finalized", a binary market, a settlement value
    from 0 to 1 and a UTC settlement time in the past; a yes or no result must agree with the value. A value
    strictly between 0 and 1 is Kalshi's "fair price" (a game not played): kept, and flagged."""
    if not isinstance(record, dict) or record.get("ticker") != ticker or record.get("market_type") != "binary" \
            or record.get("status") != "finalized":
        return None
    v = record.get("settlement_value_dollars")
    v = _num(v) if isinstance(v, str) else None
    ts = iso_ts(record.get("settlement_ts"))
    if v is None or not 0.0 <= v <= 1.0 or ts is None or not ts < now:
        return None
    result = record.get("result")
    if result in ("yes", "no") and v != (1.0 if result == "yes" else 0.0):
        return None
    fair = 0.0 < v < 1.0
    return (round(v if side == "yes" else 1.0 - v, 6), ts, fair)


# ---------------------------------------------------------------- one step
class Step:
    def __init__(self, rec, book: Book, kalshi: kd.KalshiData, pacer: StepPacer, gamma: GammaReader, fmap: dict,
                 wall: Callable[[], float], say):
        self.rec, self.book, self.kalshi, self.pacer, self.gamma, self.fmap = rec, book, kalshi, pacer, gamma, fmap
        self.wall, self.say = wall, say
        self.lists: dict = {}            # series -> events, or the failure of this step's one list read
        self.n = Counter()

    # -- considering one holding: every read first, then its rows in one transaction
    def consider(self, h: dict) -> dict:
        """What to write for one holding: {"skip": (reason, detail)} or {"copy": {...}} (or a NEAR row with its
        skip). May raise CapReached, before any write."""
        try:
            return self._consider(h)
        except Skip as s:
            out = {"skip": (s.reason, s.detail)}
            if s.reason == "no_same" and h.get("_near"):
                out["near"] = h["_near"]
            return out

    def _consider(self, h: dict) -> dict:
        if h.get("missing"):
            raise Skip("holding_not_in_record")
        value, price = h.get("current_value"), h.get("current_price")
        if not (isinstance(value, (int, float)) and value >= MIN_VALUE):
            raise Skip("value_under_1000")
        if not (isinstance(price, (int, float)) and PRICE_LO <= price <= PRICE_HI):
            raise Skip("price_outside")
        what = recognise(h, self.fmap)
        if what[0] == "near":
            h["_near"] = what[1]
            raise Skip("no_same", "NEAR: %s" % what[1])
        if what[0] != "same":
            raise Skip("no_same", what[1])
        series, parts = what[1], what[2]
        if series not in self.fmap["_listed"]:
            raise Skip("not_listed", series)
        names = resolve_names(series, parts, self.fmap)
        if leaders_disagree(self.rec, h):
            raise Skip("leaders_disagree")
        self._late(h)
        events = self._list(series)
        ev, m = find_market(series, names, events)
        if not market_open(m, self.wall()):
            raise Skip("market_not_open", "status %s" % m.get("status"))
        pm = self._description(h)
        check_texts(series, names, ev, m, pm["description"], self.fmap)
        side = names["side"]
        opened = self.book.q("SELECT copy_id FROM copies WHERE kalshi_market=? AND side=? AND status='open'",
                             (m["ticker"], side))
        if opened:
            return {"skip": ("already_open", "copy %d" % opened[0][0]), "note": opened[0][0]}
        self._late(h)
        try:
            book = self.kalshi.orderbook_raw(m["ticker"])
            stamp = self.wall()
            parsed = {"yes": kd.normalize_levels(book.get("yes_dollars")),
                      "no": kd.normalize_levels(book.get("no_dollars"))}
        except (kd.KalshiDataError, kd.HttpError, ValueError, TypeError, AttributeError) as e:
            raise Skip("read_failed", "Kalshi's book: %s" % type(e).__name__)
        if stamp - h["read_ts"] > MATCH_WINDOW:
            raise Skip("late", "the match read")
        if not market_open(m, stamp):
            raise Skip("market_not_open", "closed by the match read")
        q = side_quote(parsed, side)
        if q["ask"] is None or not ASK_LO <= q["ask"] <= ASK_HI:
            raise Skip("ask_outside", "no ask" if q["ask"] is None else "")
        return {"copy": {"series": series, "event": ev["event_ticker"], "market": m["ticker"], "side": side,
                         "match_ts": stamp, "ask": q["ask"], "ask_size": q["ask_size"],
                         "book_raw": json.dumps(book, sort_keys=True)[:4000],
                         "pm_text": pm["description"], "kalshi_text": kalshi_text(m)}}

    def _late(self, h: dict) -> None:
        if self.wall() - h["read_ts"] > MATCH_WINDOW:
            raise Skip("late", "before any request")

    def _list(self, series: str) -> list:
        if series not in self.lists:
            try:
                events = self.kalshi.pages("/events", {"series_ticker": series, "status": "open",
                                                       "with_nested_markets": "true", "limit": 200}, "events")
                if not all(isinstance(e, dict) and isinstance(e.get("event_ticker"), str) for e in events):
                    raise kd.KalshiDataError("an event not in Kalshi's shape")
                self.lists[series] = events
            except (kd.KalshiDataError, kd.HttpError, ValueError, TypeError, AttributeError) as e:
                self.lists[series] = Skip("read_failed", "Kalshi's list of %s: %s" % (series, type(e).__name__))
        got = self.lists[series]
        if isinstance(got, Skip):
            raise Skip(got.reason, got.detail)
        return got

    def _description(self, h: dict) -> dict:
        """Polymarket's text of the holding's market: from the book when it was asked before, else ONE GET (two only
        when the first, closed=false, answers none) whose answer is kept with this holding's rows, whatever its
        outcome, so that a market is asked once."""
        cid = h["condition_id"]
        row = self.book.q("SELECT p.text_hash, t.body, p.question, p.outcomes FROM pm_descriptions p JOIN texts t ON "
                          "t.text_hash = p.text_hash WHERE p.condition_id=?", (cid,))
        if row:
            return {"cached": True, "description": row[0][1]}
        try:
            got = self.gamma.market(cid, closed_first=False)
        except GammaError as e:
            raise Skip("read_failed", "Polymarket's description: %s" % e)
        if got is None or not isinstance(got.get("description"), str):
            raise Skip("read_failed", "Polymarket's description: no such market")
        h["_pm"] = {"cached": False, "description": got["description"], "read_ts": self.wall(),
                    "question": got.get("question") if isinstance(got.get("question"), str) else None,
                    "outcomes": got.get("outcomes") if isinstance(got.get("outcomes"), str) else None,
                    "closed": 1 if got.get("closed") is True else 0}
        return h["_pm"]

    def write(self, h: dict, out: dict, step_id: int) -> str:
        now = self.wall()
        key = (h["wallet_key"], h["read_id"], h["token_id"])

        def rows():
            b = self.book
            pm = h.get("_pm")
            if pm is not None:
                self._text("polymarket", pm["description"])
                b.w("INSERT OR IGNORE INTO pm_descriptions (condition_id, read_ts, text_hash, question, outcomes, "
                    "closed) VALUES (?, ?, ?, ?, ?, ?)", (h["condition_id"], pm["read_ts"],
                                                          text_hash(pm["description"]), pm["question"],
                                                          pm["outcomes"], pm["closed"]))
            outcome = "copy" if "copy" in out else "skip"
            b.w("INSERT INTO considered (wallet_key, read_id, token_id, kind, change_id, pass_id, pass_read_ts, "
                "step_id, considered_ts, outcome) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (*key, h["kind"], h["change_id"], h["pass_id"], h["read_ts"], step_id, now, outcome))
            if "copy" in out:
                c = out["copy"]
                self._text("kalshi", c["kalshi_text"])
                cur = b.w("INSERT INTO copies (wallet_key, read_id, token_id, condition_id, pm_outcome, series, "
                          "kalshi_event, kalshi_market, side, pass_read_ts, match_ts, ask, ask_size, pm_text_hash, "
                          "kalshi_text_hash, book_raw, step_id, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, "
                          "?, ?, ?, ?, ?, 'open')",
                          (*key, h["condition_id"], h.get("outcome"), c["series"], c["event"], c["market"],
                           c["side"], h["read_ts"], c["match_ts"], c["ask"], c["ask_size"], text_hash(c["pm_text"]),
                           text_hash(c["kalshi_text"]), c["book_raw"], step_id))
                b.w("INSERT INTO copy_wallets (copy_id, wallet_key, read_id, token_id, role, ts) VALUES "
                    "(?, ?, ?, ?, 'opened', ?)", (cur.lastrowid, *key, now))
                return "copy"
            reason, detail = out["skip"]
            b.w("INSERT INTO skips (wallet_key, read_id, token_id, reason, detail, ts, step_id) VALUES "
                "(?, ?, ?, ?, ?, ?, ?)", (*key, reason, detail, now, step_id))
            if "note" in out:
                b.w("INSERT INTO copy_wallets (copy_id, wallet_key, read_id, token_id, role, ts) VALUES "
                    "(?, ?, ?, ?, 'noted', ?)", (out["note"], *key, now))
            if "near" in out:
                b.w("INSERT INTO near_book (wallet_key, read_id, token_id, condition_id, family, title, outcome, "
                    "pass_read_ts, ts, step_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (*key, h.get("condition_id"), out["near"], h.get("title"), h.get("outcome"), h["read_ts"], now,
                     step_id))
            return reason
        return self.book.tx(rows)

    def _text(self, venue: str, body: str) -> None:
        self.book.w("INSERT OR IGNORE INTO texts (text_hash, venue, body) VALUES (?, ?, ?)",
                    (text_hash(body), venue, body))

    # -- closes
    def settle_open(self) -> None:
        """(a) and (c): every open copy's market read once a step; a finalized one is closed at its settlement. The
        checks start after the copy checked last (kept in meta), so a step stopped at the cap is carried on by the
        next one instead of asking the same markets again."""
        after = self.book.q("SELECT value FROM meta WHERE key='settle_after'")
        after = int(after[0][0]) if after else 0
        rows = self.book.q("SELECT copy_id, kalshi_market, side, condition_id, token_id FROM copies WHERE "
                           "status='open' ORDER BY copy_id <= ?, copy_id", (after,))
        for i, (copy_id, ticker, side, cid, tok) in enumerate(rows):
            try:
                rec = self.kalshi.market(ticker)
            except CapReached:
                # every new holding and every exit of this step is done: the checks left wait for the next step,
                # which starts with them, so the step is not stopped by them
                self.n["settle_left"] = len(rows) - i
                return
            except (kd.KalshiDataError, kd.HttpError, ValueError):
                self.n["settle_read_failed"] += 1
                rec = None
            got = None if rec is None else settlement(rec, ticker, side, self.wall())
            if got is None:
                self.book.tx(lambda: self.book.w("INSERT OR REPLACE INTO meta (key, value) VALUES ('settle_after', ?)",
                                                 (str(copy_id),)))
                continue
            value, ts, fair = got
            flags = [("kalshi_fair_price", "")] if fair else []
            flags += self._part_at_settlement(cid, tok, value)
            now = self.wall()

            def rows():
                self.book.w("INSERT OR REPLACE INTO meta (key, value) VALUES ('settle_after', ?)", (str(copy_id),))
                for kind, detail in flags:
                    self.book.w("INSERT INTO copy_flags (copy_id, kind, ts, detail) VALUES (?, ?, ?, ?)",
                                (copy_id, kind, now, detail))
                self.book.w("UPDATE copies SET status='closed', close_kind='settled', close_ts=?, close_price=?, "
                            "close_detail=? WHERE copy_id=? AND status='open'",
                            (ts, value, "Kalshi finalized; read at %.0f" % now, copy_id))
            self.book.tx(rows)
            self.n["closed_settled"] += 1

    def _part_at_settlement(self, cid: str, tok: str, value: float) -> list:
        """(c) at Kalshi's settlement: Polymarket still trading, settled otherwise, or not yet resolved."""
        try:
            pm = self.gamma.market(cid, closed_first=True)
        except GammaError as e:
            return [("part_check_failed", str(e)[:200])]
        if pm is None:
            return [("part_check_failed", "no such market")]
        if pm.get("closed") is not True:
            return [("parted_pm_trading_at_settlement", "")]
        if pm.get("umaResolutionStatus") != "resolved":
            return [("pm_unresolved_at_settlement", str(pm.get("umaResolutionStatus"))[:40])]
        try:
            tokens, prices = json.loads(pm["clobTokenIds"]), json.loads(pm["outcomePrices"])
            paid = float(prices[tokens.index(tok)])
        except (KeyError, ValueError, TypeError, IndexError):
            return [("part_check_failed", "Polymarket's outcome for the token")]
        if not math.isclose(paid, value, abs_tol=1e-9):
            return [("parted_settled_differently", "")]
        return []

    def exits(self) -> None:
        """(b): an open copy whose opening wallet's EXIT of that token is in the record after the copy's read, and
        that is not held to settlement: Polymarket's market is read first (closed there while Kalshi trades: the
        venues part, flagged and held), then ONE read of Kalshi's book, the side's bid (none: flagged and held)."""
        held = ", ".join("'%s'" % k for k in HOLD_FLAGS)
        for copy_id, key, read_id, tok, cid, ticker, side in self.book.q(
                "SELECT copy_id, wallet_key, read_id, token_id, condition_id, kalshi_market, side FROM copies c "
                "WHERE status='open' AND NOT EXISTS (SELECT 1 FROM copy_flags f WHERE f.copy_id = c.copy_id AND "
                "f.kind IN (%s)) ORDER BY copy_id" % held):
            ex = first_exit(self.rec, key, tok, read_id)
            if ex is None:
                continue
            try:
                pm = self.gamma.market(cid, closed_first=False)
            except GammaError:
                self.n["exit_pm_read_failed"] += 1
                continue
            if pm is not None and pm.get("closed") is True:
                self._flag(copy_id, "parted_pm_closed_first", "exit change %d" % ex[0])
                continue
            try:
                book = self.kalshi.orderbook(ticker)
                stamp = self.wall()
            except (kd.KalshiDataError, kd.HttpError, ValueError, TypeError, AttributeError):
                self.n["exit_read_failed"] += 1
                continue
            bid = side_quote(book, side)["bid"]
            if bid is None:
                self._flag(copy_id, "exit_no_bid", "exit change %d" % ex[0])
                continue

            def rows():
                self.book.w("UPDATE copies SET status='closed', close_kind='exit', close_ts=?, close_price=?, "
                            "close_detail=? WHERE copy_id=? AND status='open'",
                            (stamp, bid, "exit change %d read at %.0f" % (ex[0], ex[2]), copy_id))
            self.book.tx(rows)
            self.n["closed_exit"] += 1

    def _flag(self, copy_id: int, kind: str, detail: str) -> None:
        now = self.wall()
        self.book.tx(lambda: self.book.w("INSERT INTO copy_flags (copy_id, kind, ts, detail) VALUES (?, ?, ?, ?)",
                                         (copy_id, kind, now, detail)))
        self.n["flag_" + kind] += 1


def run_step(rec, book: Book, kalshi, pacer: StepPacer, gamma: GammaReader, fmap: dict, wall, say) -> int:
    started = wall()
    book.tx(lambda: book.w("UPDATE steps SET status='died' WHERE status='running'"))
    step_id = book.tx(lambda: book.w("INSERT INTO steps (started_ts, status) VALUES (?, 'running')",
                                     (started,)).lastrowid)
    considered = {tuple(r) for r in book.q("SELECT wallet_key, read_id, token_id FROM considered")}
    todo = candidates(rec, considered)
    st = Step(rec, book, kalshi, pacer, gamma, fmap, wall, say)
    reasons: Counter = Counter()
    done = 0
    capped = False
    try:
        for h in todo:
            out = st.consider(h)
            reasons[st.write(h, out, step_id)] += 1
            done += 1
        st.exits()
        st.settle_open()
    except CapReached:
        capped = True
    opened = reasons.pop("copy", 0)
    status = "capped" if capped else "done"
    book.tx(lambda: book.w("UPDATE steps SET finished_ts=?, status=?, kalshi_requests=?, gamma_requests=?, "
                           "considered=?, opened=?, closed=? WHERE step_id=?",
                           (wall(), status, pacer.count, gamma.requests, done, opened,
                            st.n["closed_settled"] + st.n["closed_exit"], step_id)))
    say("step %d: considered %d of %d (copies opened %d; skips %s); closed settled=%d exit=%d; settlement checks "
        "left for the next step %d; flags %s; Kalshi requests %d of the cap %d; Polymarket requests %d"
        % (step_id, done, len(todo), opened, " ".join("%s=%d" % (r, reasons[r]) for r in SKIP_REASONS if reasons[r])
           or "none", st.n["closed_settled"], st.n["closed_exit"], st.n["settle_left"],
           " ".join("%s=%d" % (k[5:], v) for k, v in sorted(st.n.items()) if k.startswith("flag_")) or "none",
           pacer.count, pacer.cap, gamma.requests))
    if capped:
        say("STOPPED step %d at the cap of %d Kalshi requests: %d holdings and the closes not reached wait for the "
            "next step (nothing considered is lost or made twice)" % (step_id, pacer.cap, len(todo) - done))
        return CAPPED
    say("DONE step %d" % step_id)
    return OK


# ---------------------------------------------------------------- counts
def counts(conn, wall, say) -> int:
    """COUNTS ONLY: copies opened and closed by kind, events, skips by reason, the NEAR book, flags, the days since
    the first copy. No price of a copy is added, averaged or set against another here."""
    n_open = conn.execute("SELECT count(*) FROM copies WHERE status='open'").fetchone()[0]
    by_kind = dict(conn.execute("SELECT close_kind, count(*) FROM copies WHERE status='closed' GROUP BY close_kind"))
    total = conn.execute("SELECT count(*) FROM copies").fetchone()[0]
    ev_all = conn.execute("SELECT count(DISTINCT kalshi_event) FROM copies").fetchone()[0]
    ev_closed = conn.execute("SELECT count(DISTINCT kalshi_event) FROM copies WHERE status='closed'").fetchone()[0]
    first = conn.execute("SELECT min(match_ts) FROM copies").fetchone()[0]
    considered = conn.execute("SELECT count(*) FROM considered").fetchone()[0]
    n_closed = conn.execute("SELECT count(*) FROM copies WHERE status='closed'").fetchone()[0]
    say("copies opened %d: open %d, closed %d (settled %d, exit %d); events %d, of them with a closed copy %d"
        % (total, n_open, n_closed, by_kind.get("settled", 0), by_kind.get("exit", 0), ev_all, ev_closed))
    kinds = dict(conn.execute("SELECT c.kind, count(*) FROM copies p JOIN considered c USING (wallet_key, read_id, "
                              "token_id) GROUP BY c.kind"))
    say("copies by holding: baseline %d, new %d" % (kinds.get("baseline", 0), kinds.get("new", 0)))
    noted = conn.execute("SELECT count(*) FROM copy_wallets WHERE role='noted'").fetchone()[0]
    say("wallets noted on open copies %d" % noted)
    skips = dict(conn.execute("SELECT reason, count(*) FROM skips GROUP BY reason"))
    n_skips = conn.execute("SELECT count(*) FROM skips").fetchone()[0]
    say("considered %d; skips %d: %s" % (considered, n_skips,
                                         " ".join("%s=%d" % (r, skips.get(r, 0)) for r in SKIP_REASONS)))
    near = conn.execute("SELECT family, count(*) FROM near_book GROUP BY family ORDER BY family").fetchall()
    n_near = conn.execute("SELECT count(*) FROM near_book").fetchone()[0]
    say("NEAR book %d: %s" % (n_near, " ".join("%s=%d" % (f.replace(" ", "_"), c) for f, c in near) or "none"))
    flags = dict(conn.execute("SELECT kind, count(*) FROM copy_flags GROUP BY kind"))
    say("flags: %s" % (" ".join("%s=%d" % (k, flags.get(k, 0)) for k in FLAG_KINDS)))
    say("days since the first copy: %s" % ("none yet" if first is None else "%.1f" % ((wall() - first) / DAY)))
    return OK


# ---------------------------------------------------------------- command line
def _pause(text: str) -> float:
    v = float(text)
    if not v >= MIN_PAUSE:
        raise argparse.ArgumentTypeError("the pause is never under %.1f seconds" % MIN_PAUSE)
    return v


def _cap(text: str) -> int:
    v = int(text)
    if v < MIN_CAP:
        raise argparse.ArgumentTypeError("the cap is at least %d (one list and one book: what one holding needs)"
                                         % MIN_CAP)
    return v


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="forward_copy", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("step")
    s.add_argument("--record", required=True)
    s.add_argument("--book", required=True)
    s.add_argument("--kalshi-pause-seconds", type=_pause, default=DEFAULT_PAUSE)
    s.add_argument("--kalshi-cap", type=_cap, default=DEFAULT_CAP)
    c = sub.add_parser("counts")
    c.add_argument("--book", required=True)
    return p


def main(argv=None, *, kalshi_fetch=None, gamma_fetch=None, wall: Callable[[], float] = time.time,
         sleep: Callable[[float], Any] = time.sleep, clock: Callable[[], float] = time.monotonic, out=None,
         write_hook: Callable[[str], None] | None = None, fmap: dict | None = None) -> int:
    args = _parser().parse_args(argv)
    out = out or sys.stdout

    def say(line: str) -> None:
        out.write(line + "\n")
        out.flush()
    if args.cmd == "counts":
        conn, bad = open_book(Path(args.book), create=False)
        if bad:
            say(bad[1])
            return bad[0]
        try:
            return counts(conn, wall, say)
        finally:
            conn.close()
    rec, bad = open_record(Path(args.record))
    if bad:
        say("REFUSED " + bad[1] if bad[0] == REFUSED else bad[1])
        return bad[0]
    try:
        if Path(args.book).resolve() == Path(args.record).resolve():
            say("REFUSED refused: the book cannot be the record; nothing done")
            return REFUSED
        conn, bad = open_book(Path(args.book), create=True)
        if bad:
            say(bad[1])
            return bad[0]
        try:
            pacer = StepPacer(args.kalshi_pause_seconds, args.kalshi_cap, sleep, clock)
            kalshi = kalshi_client(kalshi_fetch or kd.urllib_get, pacer)
            gamma = GammaReader(gamma_fetch or gamma_urllib_get, sleep=sleep, clock=clock)
            return run_step(rec, Book(conn, write_hook), kalshi, pacer, gamma, fmap or load_map(), wall, say)
        finally:
            conn.close()
    finally:
        rec.close()


if __name__ == "__main__":
    sys.exit(main())
