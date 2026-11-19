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

One step at a time on a book: `step` takes an exclusive, non-blocking flock on `<book>.step.lock` (a file beside the
book, not part of its bytes) before it reads or sends anything and holds it to the end; a second step exits with
code 75, "another step is running on this book", with no request, no write and no `died` marking.

No command, function or test here computes, stores or prints a result, a profit, a mean or a sum of prices of copies
(the document: no result before the count is reached; the one read is a later slice). Nothing here sends an order,
previews one, holds a key, or runs itself: whether and when `step` runs is the owner's decision.

Requests. EVERY request to Kalshi goes through ONE pacer (StepPacer, installed as the KalshiData client's own pacer,
so every try of every call waits on it): a pause between requests (--kalshi-pause-seconds, default 10.0, never under
5.0: a collector on this machine reads the same host) and a cap a step (--kalshi-cap, default 60). At the cap the step
stops cleanly between holdings (the holding being worked on is written not at all) and says so; the next step carries
on. KalshiData is built with tries=1, so a 429 or a server error is a failed read, never a retry, and with this
module's OWN fetch (kalshi_urllib_get: GET only, https, Kalshi's one host and its /trade-api/v2/ paths the copier
uses, no redirect followed, no proxy, the generic client name, a size limit and a timeout). The pause holds across
steps: the time of the last Kalshi request is kept in the book. The calls used: `pages("/events", ...)` (one list of a
series' open events with their markets, per series reached in a step by a new holding or by an open copy),
`orderbook` (the match read: ONE for a Kalshi market and a pass, kept in the book; an exit's bid read) and `market`
(a settlement check, only for an open copy whose market is not open in its series' list; on a 404 from the live tier
it asks the historical tier). Polymarket's market-description service is read by GammaReader here: GET only, one
host, one path, the parameters condition_ids and closed, the generic client name, no key, its own pause; a market's
text is asked once and kept by condition id.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
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
MAX_UNIT = 3       # the most Kalshi requests of one unit of work: a market read (two on a live 404) and an exit's book
GAMMA_PAUSE = 0.34                          # the other Polymarket readers' manners: about three a second
MIN_VALUE = 1000.0
PRICE_LO, PRICE_HI = 0.10, 0.90
ASK_LO, ASK_HI = 0.05, 0.95
MATCH_WINDOW = 6 * 3600.0                    # the match read falls within 6 hours of the pass's read of that wallet
DAY = 86_400
USER_VERSION = 6                             # 6: token_not_in_market (5: settlement_unreadable, clock_before_read,
                                             # other_wallet_read_unclear)
WAIT_SECONDS = 5.0
MAX_BODY = 8_000_000                         # an answer larger than this is a failed read (a list of 200 events)
FETCH_TIMEOUT = 60.0
OK, REFUSED, CAPPED, OTHER_SHAPE, UNREADABLE, BUSY = 0, 3, 5, 7, 8, 9
STEP_RUNNING = 75        # another step holds the book's lock

# The skip reasons, in the order they are applied (see docs/forward-copier.md for why the order cannot change which
# copies are made). `late` and `read_failed` are tested more than once along the way; the first failing test decides.
SKIP_REASONS = (
    "holding_not_in_record",     # the holding's row at that read is no longer in the record (value unknown)
    "value_under_1000",          # the holding's value at that read is under 1,000 dollars
    "price_outside",             # Polymarket's price for it is outside 0.10 to 0.90 (or unknown)
    "no_same",                   # no counterpart of class SAME in the rule map (a NEAR one goes to the NEAR book)
    "not_listed",                # its series is not in the listing table
    "name_not_in_table",         # a team, club or person not in the map's tables (never guessed)
    "opposite_unknown",          # the other outcome's token is not known: whether the leaders disagree cannot be told
    "other_wallet_read_unclear", # another wallet of the set has no clean read in that pass: nobody can say it does not
                                 # hold the opposite outcome (fail closed)
    "leaders_disagree",          # another wallet of the set holds the opposite outcome at that pass
    "clock_before_read",         # this host's clock stands before the wallet's read (before the request, or at the stamp)
    "late",                      # the match read would fall (or fell) later than 6 hours after the pass's read
    "read_failed",               # a read that failed (Kalshi's list or book, or Polymarket's description)
    "no_unique_market",          # not exactly one Kalshi market for it
    "market_not_open",           # Kalshi's market not open at the match read
    "token_not_in_market",       # Polymarket's description does not list the holding's token among its market's tokens
    "text_departs",              # a venue's text departs from the map's template, names and dates aside
    "already_open",              # a copy of that Kalshi market and side is already open (the wallet is noted on it)
    "ask_outside",               # the ask of the copy's side is outside 0.05 to 0.95, or there is none
)
FLAG_KINDS = ("exit_no_bid", "exit_read_failed", "parted_pm_closed_first", "parted_pm_trading_at_settlement",
              "parted_settled_differently", "pm_unresolved_at_settlement", "part_check_failed",
              "kalshi_fair_price", "settlement_unreadable")
HOLD_FLAGS = ("exit_no_bid", "exit_read_failed", "parted_pm_closed_first", "settlement_unreadable")          # a copy so flagged is held to Kalshi's settlement
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


class Defer(Exception):
    """A series list or a description could not be read: no book read was made, so the holding is neither considered
    nor skipped; a later step asks again, until the six hours end it (`late`)."""
    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


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
    m["_lists"] = len({s for s, e in m["series"].items() if e.get("class") == "SAME"} & m["_listed"])
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


# The smallest cap a step accepts: the lists a step can need (one a listed SAME series, one page each), then one unit
# of work. A list of more pages can still spend a step's cap: the step then says it made no progress.
MIN_CAP = load_map()["_lists"] + MAX_UNIT


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
    found = _found(evs, want, events)
    if len(found) != 1:
        raise Skip("no_unique_market", "%d markets found" % len(found))
    ev, m = found[0]
    if not (isinstance(m.get("ticker"), str) and TICKER_RE.fullmatch(m["ticker"])):
        raise Skip("no_unique_market", "a ticker not in Kalshi's shape")
    return ev, m


def candidate_tickers(series: str, names: dict) -> set:
    """The tickers find_market can answer for the holding, from its names alone (no request): to look up a kept match
    read before anything is judged against the clock."""
    if series in ("KXNBAGAME", "KXNBASPREAD"):
        day = kalshi_day(names["date"])
        ca, cb = names["a"]["kalshi_code"], names["b"]["kalshi_code"]
        evs = ("%s-%s%s%s" % (series, day, ca, cb), "%s-%s%s%s" % (series, day, cb, ca))
        if series == "KXNBAGAME":
            return {e + "-" + names["held"]["kalshi_code"] for e in evs}
        return {"%s-%s%d" % (e, names["held"]["kalshi_code"], int(float(names["line"]) + 0.5)) for e in evs}
    ev = {"KXPREMIERLEAGUE": "KXPREMIERLEAGUE-27", "KXPRESNOMD": "KXPRESNOMD-28"}[series]
    return {ev + "-" + names["held"]["kalshi_code"]}


def _found(evs, want, events) -> list:
    found = []
    for ev in events:
        if ev.get("event_ticker") in evs:
            found += [(ev, m) for m in ev.get("markets") or [] if isinstance(m, dict) and want(ev, m)]
    return found


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
    if any(t is not None and not isinstance(t, str) for t in (pm_text, m.get("rules_primary"), m.get("rules_secondary"))):
        raise Skip("text_departs", "a text that is not a string")
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
    most `cap` requests; the request past the cap raises CapReached before it is sent. The pause holds across steps:
    the first request of a step waits until `pause` has passed since `last_wall` (the wall time of the step before's
    last Kalshi request, kept in the book; a clock set back waits the full pause), and `keep(wall time)` is called
    before every request is sent, so the book always holds the time of the last one."""

    def __init__(self, pause: float, cap: int, sleep: Callable[[float], Any] = time.sleep,
                 clock: Callable[[], float] = time.monotonic, wall: Callable[[], float] | None = None,
                 last_wall: float | None = None, keep: Callable[[float], Any] | None = None):
        if not pause >= MIN_PAUSE:
            raise ValueError("the pause between Kalshi requests is never under %.1f s" % MIN_PAUSE)
        if not (isinstance(cap, int) and cap >= 1):
            raise ValueError("the cap is a whole number of at least 1")
        self.pause, self.cap, self.sleep, self.clock = float(pause), cap, sleep, clock
        self.wall, self.last_wall, self.keep = wall, last_wall, keep
        self.last: float | None = None
        self.count = 0

    def _sleep_for(self, seconds: float) -> None:
        """Wait `seconds` by the pacer's clock: a sleep that returns early is asked again for the rest. A clock that
        does not move at all across a sleep (a stand-in) cannot be waited on, so the wait ends there."""
        end = self.clock() + seconds
        while seconds > 1e-9:
            before = self.clock()
            self.sleep(seconds)
            after = self.clock()
            if after <= before:
                return
            seconds = end - after

    def wait(self) -> None:
        if self.count >= self.cap:
            raise CapReached("the cap of %d Kalshi requests a step" % self.cap)
        if self.last is not None:
            self._sleep_for(self.pause - (self.clock() - self.last))
        elif self.last_wall is not None and self.wall is not None:
            gap = self.wall() - self.last_wall
            if gap < 0.0:                                 # the clock was set back: the full pause, never none
                self._sleep_for(self.pause)
            else:
                self._sleep_for(self.pause - gap)
        if self.keep is not None and self.wall is not None:
            self.keep(self.wall())
        self.last = self.clock()
        self.count += 1


def kalshi_client(fetch: Callable[[str], Any], pacer: StepPacer) -> kd.KalshiData:
    """KalshiData with one try a call and its own pacer replaced by the step's pacer."""
    client = kd.KalshiData(fetch, tries=1, sleep=pacer.sleep, clock=pacer.clock)
    client.pacer = pacer
    return client


GAMMA_BASE = "https://gamma-api.polymarket.com"
GAMMA_URL = re.compile(re.escape(GAMMA_BASE) + r"/markets\?condition_ids=0x[0-9a-f]{64}&closed=(?:true|false)")
_T = r"[A-Z][A-Z0-9.\-]{0,79}"                       # a Kalshi ticker (TICKER_RE)
KALSHI_URL = re.compile(re.escape(kd.BASE_URL) + "(?:" + "|".join((
    r"/events\?series_ticker=[A-Z][A-Z0-9]{0,39}&status=open&with_nested_markets=true&limit=200"
    r"(?:&cursor=[A-Za-z0-9%_.\-]{1,500})?",
    r"/markets/" + _T + r"/orderbook\?depth=\d{1,3}",
    r"/markets/" + _T,
    r"/historical/markets/" + _T)) + ")")
_HANDLERS: tuple = ()          # extra urllib handlers; empty here (a test puts a stand-in for the network in it)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None                                    # a redirect is never followed: the answer is an HTTP error


def _opener():
    """urllib with no proxy (the request goes to the host named, nowhere else) and no redirect followed."""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), *_HANDLERS, _NoRedirect())


def _get(url: str, timeout: float) -> bytes:
    """GET `url` (already allowed); its body, at most MAX_BODY bytes. Raises urllib's errors, or ValueError (size)."""
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": kd.USER_AGENT},
                                 method="GET")
    with _opener().open(req, timeout=timeout) as resp:
        code = getattr(resp, "status", None) or resp.getcode()
        if code != 200:
            raise urllib.error.HTTPError(url, code, "not 200", resp.headers, None)
        body = resp.read(MAX_BODY + 1)
    if len(body) > MAX_BODY:
        raise ValueError("an answer over the size limit of %d bytes" % MAX_BODY)
    return body


def kalshi_urllib_get(url: str, timeout: float = FETCH_TIMEOUT) -> bytes:
    """The copier's OWN fetch for Kalshi's public API (the allow-list KALSHI_URL: https, the one host, the paths the
    copier asks); any other address is refused before anything is sent; a redirect is a failed read (HttpError with
    its status); not a 200 is HttpError; no network or too large an answer is KalshiDataError."""
    if not (isinstance(url, str) and KALSHI_URL.fullmatch(url)):
        raise kd.KalshiDataError("refused: %s is not an address this copier asks" % lm._safe(str(url))[:200])
    try:
        return _get(url, timeout)
    except urllib.error.HTTPError as e:
        raise kd.HttpError(e.code, url) from None
    except ValueError as e:
        raise kd.KalshiDataError(str(e)) from None
    except (urllib.error.URLError, OSError) as e:
        raise kd.KalshiDataError(type(e).__name__) from None


def gamma_urllib_get(url: str, timeout: float = FETCH_TIMEOUT) -> bytes:
    """GET one address of Polymarket's description service (the allow-list: one host, one path, two parameters);
    any other address is refused before anything is sent; a redirect is never followed; not a 200 is an error."""
    if not (isinstance(url, str) and GAMMA_URL.fullmatch(url)):
        raise GammaError("refused: %s is not an address this reader asks" % url)
    try:
        return _get(url, timeout)
    except urllib.error.HTTPError as e:
        raise GammaError("HTTP %s" % e.code) from None
    except ValueError as e:
        raise GammaError(str(e)) from None
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
CREATE TABLE IF NOT EXISTS match_reads (kalshi_market TEXT NOT NULL, pass_key INTEGER NOT NULL, stamp REAL NOT NULL,
    ok INTEGER NOT NULL CHECK (ok IN (0, 1)), book_raw TEXT, detail TEXT, event_json TEXT, market_json TEXT,
    step_id INTEGER NOT NULL, PRIMARY KEY (kalshi_market, pass_key));
CREATE TABLE IF NOT EXISTS match_begun (kalshi_market TEXT NOT NULL, pass_key INTEGER NOT NULL, ts REAL NOT NULL,
    step_id INTEGER NOT NULL, PRIMARY KEY (kalshi_market, pass_key));
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
CREATE TRIGGER IF NOT EXISTS flags_fixed BEFORE UPDATE ON copy_flags
    BEGIN SELECT RAISE(ABORT, 'a flag never changes'); END;
CREATE TRIGGER IF NOT EXISTS flags_kept BEFORE DELETE ON copy_flags
    BEGIN SELECT RAISE(ABORT, 'a flag never changes'); END;
CREATE TRIGGER IF NOT EXISTS wallets_fixed BEFORE UPDATE ON copy_wallets
    BEGIN SELECT RAISE(ABORT, 'a wallet on a copy never changes'); END;
CREATE TRIGGER IF NOT EXISTS wallets_kept BEFORE DELETE ON copy_wallets
    BEGIN SELECT RAISE(ABORT, 'a wallet on a copy never changes'); END;
CREATE TRIGGER IF NOT EXISTS match_reads_fixed BEFORE UPDATE ON match_reads
    BEGIN SELECT RAISE(ABORT, 'a match read never changes'); END;
CREATE TRIGGER IF NOT EXISTS match_reads_kept BEFORE DELETE ON match_reads
    BEGIN SELECT RAISE(ABORT, 'a match read never changes'); END;
CREATE TRIGGER IF NOT EXISTS match_begun_fixed BEFORE UPDATE ON match_begun
    BEGIN SELECT RAISE(ABORT, 'a begun match read never changes'); END;
CREATE TRIGGER IF NOT EXISTS match_begun_kept BEFORE DELETE ON match_begun
    BEGIN SELECT RAISE(ABORT, 'a begun match read never changes'); END;
""" % {"reasons": ", ".join("'%s'" % r for r in SKIP_REASONS), "flags": ", ".join("'%s'" % f for f in FLAG_KINDS)}

# INSERT OR REPLACE over an existing row deletes it without firing a delete trigger: every append-only table refuses
# an insert that meets an existing row (by its key or its rowid). The texts are kept by their hash, so a second insert
# of one is quietly ignored there instead of refused (the program asks INSERT OR IGNORE).
_KEPT = (("considered", ("wallet_key", "read_id", "token_id"), "ABORT"),
         ("skips", ("wallet_key", "read_id", "token_id"), "ABORT"),
         ("copies", ("copy_id",), "ABORT"),
         ("copy_wallets", ("copy_id", "wallet_key", "read_id", "token_id"), "ABORT"),
         ("copy_flags", (), "ABORT"),
         ("match_reads", ("kalshi_market", "pass_key"), "ABORT"),
         ("match_begun", ("kalshi_market", "pass_key"), "ABORT"),
         ("near_book", ("wallet_key", "read_id", "token_id"), "ABORT"),
         ("texts", ("text_hash",), "IGNORE"),
         ("pm_descriptions", ("condition_id",), "IGNORE"))


def _no_overwrite(table, key, how):
    when = "NEW.rowid IN (SELECT rowid FROM %s)" % table
    if key:
        when += " OR EXISTS (SELECT 1 FROM %s WHERE %s)" % (table, " AND ".join("%s = NEW.%s" % (c, c) for c in key))
    raise_ = "IGNORE" if how == "IGNORE" else "ABORT, 'an append-only row is never written over'"
    return ("CREATE TRIGGER IF NOT EXISTS %s_no_overwrite BEFORE INSERT ON %s\n    WHEN %s\n"
            "    BEGIN SELECT RAISE(%s); END;\n" % (table, table, when, raise_))


BOOK_SCHEMA += "".join(_no_overwrite(*k) for k in _KEPT)


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


def _needs_recovery(e: sqlite3.Error) -> bool:
    """SQLITE_READONLY_ROLLBACK (776): a read-only open met a hot journal."""
    return getattr(e, "sqlite_errorcode", None) == 776 or "attempt to write a readonly database" in str(e)


def _recover(path: Path) -> None:
    conn = sqlite3.connect(str(path), timeout=WAIT_SECONDS)
    try:
        conn.execute("SELECT count(*) FROM sqlite_master").fetchall()
    finally:
        conn.close()


def open_book(path: Path, create: bool):
    """(Book connection, None) or (None, (code, words)). Only `step` creates a book, at its first run."""
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        if not create:
            return None, (REFUSED, "refused: no book at %s (only step makes one); nothing done" % path)
        conn = None
        try:
            conn = sqlite3.connect(str(path), timeout=WAIT_SECONDS, isolation_level=None)
            # ONE transaction, user_version with it: a creation that stops part way (the connection closed with the
            # transaction open) leaves no file content to refuse
            conn.executescript("BEGIN IMMEDIATE;\n" + BOOK_SCHEMA + "\nPRAGMA user_version = %d;\nCOMMIT;" % USER_VERSION)
        except sqlite3.Error as e:
            if conn is not None:
                conn.close()
            return None, (REFUSED, "refused: the book %s could not be made (%s); nothing done" % (path, type(e).__name__))
        return conn, None
    why = None
    for attempt in (1, 2):
        try:
            why = book_shape_problem(path)
            break
        except sqlite3.Error as e:
            if attempt == 1 and _needs_recovery(e):          # a write was cut short: a hot journal, which a read-only
                if not create:                               # open cannot roll back
                    return None, (UNREADABLE, "refused: the book %s needs a step to recover (a write was cut short); "
                                              "nothing done" % path)
                try:                                         # step opens it read-write once; SQLite recovers it
                    _recover(path)
                except sqlite3.Error:
                    pass
                continue
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
    wallet opens a copy and which is noted is fixed too). A holding of a pass the record still shows running is left
    for a later step: the match read is the first read AFTER the pass."""
    cols = ", ".join(HCOLS)
    running = {p for (p,) in rec.execute("SELECT pass_id FROM passes WHERE status='running'")}
    out = []
    for (key,) in rec.execute("SELECT wallet_key FROM leaders ORDER BY wallet_key"):
        base = rec.execute("SELECT read_id, read_ts, pass_id, baseline FROM wallet_reads WHERE wallet_key=? AND good=1 "
                           "ORDER BY read_id LIMIT 1", (key,)).fetchone()
        if base is not None and base[3] == 1 and base[2] not in running:
            for r in rec.execute("SELECT token_id, %s FROM snapshots WHERE read_id=? AND wallet_key=? AND "
                                 "kind='baseline' ORDER BY token_id" % cols, (base[0], key)):
                if (key, base[0], r[0]) not in considered:
                    out.append({"wallet_key": key, "read_id": base[0], "read_ts": base[1], "pass_id": base[2],
                                "token_id": r[0], "kind": "baseline", "change_id": None, **dict(zip(HCOLS, r[1:]))})
        for change_id, read_id, tok, read_ts, pass_id in rec.execute(
                "SELECT c.change_id, c.read_id, c.token_id, c.read_ts, r.pass_id FROM changes c JOIN wallet_reads r "
                "ON r.read_id = c.read_id WHERE c.wallet_key=? AND c.kind='NEW' AND c.vouched=1 AND r.good=1 ORDER BY c.read_id, "
                "c.token_id", (key,)).fetchall():
            if (key, read_id, tok) in considered or pass_id in running:
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


def _rejects_clean(text) -> bool:
    """The record's rejects column is a JSON object of counts; clean when it holds none but "repeated_row". Anything
    else (NULL, a list, null, not JSON) cannot be read as clean."""
    try:
        d = json.loads(text)
    except (TypeError, ValueError):
        return False
    return isinstance(d, dict) and not any(n for why, n in d.items() if why != "repeated_row")


def unclear_wallets(rec, h: dict) -> list:
    """The other wallets of the fixed set with no CLEAN read in the holding's pass, in key order. A clean read is the
    record's own (the one that lets an EXIT be recorded, leaders_monitor.write_wallet): in that pass, wallet_reads
    good = 1, carried = 0 and no rejected rows (the column rejects holds none but "repeated_row"). A failed read, a
    carried or rejecting one, and a wallet with no read in the pass are all unclear. A holding with no pass has no
    pass to read the others in: every other wallet is unclear."""
    out = []
    for (v,) in rec.execute("SELECT wallet_key FROM leaders WHERE wallet_key != ? ORDER BY wallet_key",
                            (h["wallet_key"],)).fetchall():
        got = [] if h.get("pass_id") is None else rec.execute(
            "SELECT carried, rejects FROM wallet_reads WHERE wallet_key=? AND pass_id=? AND good=1",
            (v, h["pass_id"])).fetchall()
        clean = len(got) >= 1 and all(not c and _rejects_clean(r) for c, r in got)
        if not clean:
            out.append(v)
    return out


def leaders_disagree(rec, h: dict) -> bool:
    """Another wallet of the set held another outcome of the same market AT THAT PASS, rebuilt from the record's
    history and never from its holdings now: that wallet's last snapshot (its baseline or a daily one, carried rows
    too; the read that took one is marked, also when the wallet held nothing) taken in a read of that pass or before, then its NEW (opened) and EXIT (left) changes after that snapshot up
    to and including that pass. Where the record cannot say (the wallet's read in that pass failed or was carried),
    no change is recorded; the step never asks here: a wallet with no clean read in the pass has already made the
    holding a skip (unclear_wallets, other_wallet_read_unclear). A wallet with no good read up to that pass is not
    known to hold anything."""
    cid, key, tok = h.get("condition_id"), h["wallet_key"], h["token_id"]
    if cid is None:
        return False
    others = {t for (t,) in rec.execute("SELECT token_id FROM snapshots WHERE condition_id=? UNION "
                                        "SELECT token_id FROM holdings_current WHERE condition_id=?", (cid, cid))}
    if h.get("opposite_token_id"):
        others.add(h["opposite_token_id"])
    others.discard(tok)
    if not others:
        return False
    if h.get("pass_id") is not None:
        bound, arg = "((r.pass_id IS NOT NULL AND r.pass_id <= ?) OR (r.pass_id IS NULL AND r.read_id <= ?))", \
            (h["pass_id"], h["read_id"])
    else:
        bound, arg = "r.read_id <= ?", (h["read_id"],)
    for (v,) in rec.execute("SELECT wallet_key FROM leaders WHERE wallet_key != ? ORDER BY wallet_key",
                            (key,)).fetchall():
        snap = rec.execute("SELECT MAX(r.read_id) FROM wallet_reads r WHERE r.wallet_key=? AND r.good=1 AND "
                           "(r.snapshot_day IS NOT NULL OR r.baseline = 1) AND " + bound, (v,) + arg).fetchone()[0]
        if snap is None:
            continue
        held = {t for (t,) in rec.execute("SELECT token_id FROM snapshots WHERE read_id=? AND wallet_key=?",
                                          (snap, v)) if t in others}
        for kind, t in rec.execute("SELECT c.kind, c.token_id FROM changes c JOIN wallet_reads r ON r.read_id = "
                                   "c.read_id WHERE c.wallet_key=? AND c.read_id > ? AND c.kind IN ('NEW', 'EXIT') "
                                   "AND " + bound + " ORDER BY c.read_id, c.change_id", (v, snap) + arg):
            if t in others:
                (held.add if kind == "NEW" else held.discard)(t)
        if held:
            return True
    return False


def first_exit(rec, wallet_key: str, token_id: str, after_read_id: int):
    """The opening wallet's first EXIT of that token in a read after the copy's read, of a pass no longer running:
    (change_id, read_id, read_ts, pass_id of that read)."""
    return rec.execute("SELECT c.change_id, c.read_id, c.read_ts, r.pass_id FROM changes c JOIN wallet_reads r ON r.read_id = "
                       "c.read_id LEFT JOIN passes p ON p.pass_id = r.pass_id WHERE c.wallet_key=? AND c.token_id=? "
                       "AND c.kind='EXIT' AND c.read_id>? AND (p.status IS NULL OR p.status != 'running') "
                       "ORDER BY c.read_id LIMIT 1", (wallet_key, token_id, after_read_id)).fetchone()


def pass_key(h: dict) -> int:
    """THAT PASS of a holding: the record's pass of the read that showed it (wallet_reads.pass_id); a read with no
    pass is its own (minus its read id)."""
    return h["pass_id"] if h.get("pass_id") is not None else -int(h["read_id"])


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
        self.pm_failed: dict = {}        # condition id -> the failure of this step's one description read
        self.step_id = 0
        self.n = Counter()

    # -- considering one holding: every read first, then its rows in one transaction
    def consider(self, h: dict) -> dict:
        """What to write for one holding: {"skip": (reason, detail)} or {"copy": {...}} (or a NEAR row with its
        skip). May raise CapReached, before any write."""
        try:
            return self._consider(h)
        except Defer as d:
            return {"defer": d.kind}
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
        if self.fmap["series"][series].get("class") != "SAME":          # the map's class, read by the program
            raise Skip("no_same", "%s is of class %s" % (series, self.fmap["series"][series].get("class")))
        if series not in self.fmap["_listed"]:
            raise Skip("not_listed", series)
        names = resolve_names(series, parts, self.fmap)
        opp = h.get("opposite_token_id")
        if not (isinstance(opp, str) and opp.strip()):
            raise Skip("opposite_unknown", "the other outcome's token is not known: whether the leaders disagree "
                                           "cannot be told")
        unclear = unclear_wallets(self.rec, h)
        if unclear:                                  # nobody can say those wallets do not hold the opposite outcome
            raise Skip("other_wallet_read_unclear", "%d wallets, first %s" % (len(unclear), unclear[0]))
        if leaders_disagree(self.rec, h):
            raise Skip("leaders_disagree")
        kept = self._kept(h, series, names)          # looked up BEFORE anything is judged against the clock
        if kept is not None:
            ticker, stamp, raw, ev, m = kept
            if ev is None or m is None:              # kept by an exit check: the market as this step's list shows it
                ev, m = find_market(series, names, self._list(series))
                if m["ticker"] != ticker:
                    raise Skip("no_unique_market", "the list's market is not the one read for the pass")
        else:
            self._late(h)                            # the clock decides only whether a read may still be made
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
        if kept is None:
            stamp, raw = self._match_read(h, ev, m)
        book = json.loads(raw)
        parsed = {"yes": kd.normalize_levels(book.get("yes_dollars")), "no": kd.normalize_levels(book.get("no_dollars"))}
        if stamp < h["read_ts"]:                     # a read cannot come before the read it follows (own stamp)
            raise Skip("clock_before_read", "the match read")
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
        now = self.wall()
        if now < h["read_ts"]:                       # this host's clock stands before the wallet's read: no request
            raise Skip("clock_before_read", "before any request")
        if now - h["read_ts"] > MATCH_WINDOW:
            raise Skip("late", "before any request")

    def _kept(self, h: dict, series: str, names: dict):
        """The match read already kept for the holding's market and pass: (stamp, raw book, event, market), the event
        and market as they were found for it; None when no read of that market and pass was begun. A read BEGUN and
        never kept (the step stopped between its marker and its result) is the pass's first read having failed."""
        pk, tickers = pass_key(h), sorted(candidate_tickers(series, names))
        marks = ", ".join("?" * len(tickers))
        got = self.book.q("SELECT kalshi_market, stamp, ok, book_raw, detail, event_json, market_json FROM "
                          "match_reads WHERE pass_key=? AND kalshi_market IN (%s) ORDER BY kalshi_market" % marks, (pk, *tickers))
        if len(got) > 1:
            raise Skip("no_unique_market", "%d markets read for the pass" % len(got))
        if got:
            ticker, stamp, ok, raw, detail, ev, m = got[0]
            if not ok:
                raise Skip("read_failed", "%s (the pass's first read)" % detail)
            return ticker, stamp, raw, json.loads(ev) if ev else None, json.loads(m) if m else None
        if self.book.q("SELECT 1 FROM match_begun WHERE pass_key=? AND kalshi_market IN (%s)" % marks, (pk, *tickers)):
            raise Skip("read_failed", "Kalshi's book: begun and never kept (the pass's first read)")
        return None

    def _match_read(self, h: dict, ev: dict, m: dict) -> tuple:
        """THE match read of the market for the holding's pass: (stamp, raw book), through `book_read`. A first read
        that failed, or begun and never kept, is the answer too: read_failed."""
        ok, stamp, raw, detail = self.book_read(m["ticker"], pass_key(h), ev, m)
        if not ok:
            raise Skip("read_failed", "%s (the pass's first read)" % detail)
        return stamp, raw

    def book_read(self, ticker: str, pk: int, ev: dict | None = None, m: dict | None = None) -> tuple:
        """THE ONE way the module reads a Kalshi market's book for a pass, for a match read and for an exit alike:
        (ok, stamp, raw book, detail). For one market and one pass the book is read ONCE, ever: a read already kept is
        returned with no request; a read BEGUN and never kept (a step stopped between the two writes) is a failed read,
        with no request. Otherwise, BEFORE the request is sent the book holds that the read is begun (its own committed
        write; with no room left under the cap, nothing is marked and CapReached is raised); after it, the read (with
        the event and market it was found by, when known) in another."""
        got = self.book.q("SELECT ok, stamp, book_raw, detail FROM match_reads WHERE kalshi_market=? AND pass_key=?",
                          (ticker, pk))
        if got:
            ok, stamp, raw, detail = got[0]
            return bool(ok), stamp, raw, detail
        if self.book.q("SELECT 1 FROM match_begun WHERE kalshi_market=? AND pass_key=?", (ticker, pk)):
            return False, None, None, "Kalshi's book: begun and never kept"
        if self.pacer.count >= self.pacer.cap:       # no room for the request: stop before the marker, not after it
            raise CapReached("the cap of %d Kalshi requests a step" % self.pacer.cap)
        begun = self.wall()
        self.book.tx(lambda: self.book.w("INSERT INTO match_begun (kalshi_market, pass_key, ts, step_id) VALUES "
                                         "(?, ?, ?, ?)", (ticker, pk, begun, self.step_id)))
        ok, raw, detail = 1, None, ""
        try:
            book = self.kalshi.orderbook_raw(ticker)
            stamp = self.wall()
            kd.normalize_levels(book.get("yes_dollars"))
            kd.normalize_levels(book.get("no_dollars"))
            raw = json.dumps(book, sort_keys=True)
        except (kd.KalshiDataError, kd.HttpError, ValueError, TypeError, AttributeError) as e:
            stamp, ok, detail = self.wall(), 0, "Kalshi's book: %s" % type(e).__name__
        ev_json = None if ev is None else json.dumps({k: v for k, v in ev.items() if k != "markets"}, sort_keys=True)
        m_json = None if m is None else json.dumps(m, sort_keys=True)
        self.book.tx(lambda: self.book.w(
            "INSERT INTO match_reads (kalshi_market, pass_key, stamp, ok, book_raw, detail, event_json, market_json, "
            "step_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (ticker, pk, stamp, ok, raw, detail, ev_json, m_json,
                                                           self.step_id)))
        return bool(ok), stamp, raw, detail

    def _list(self, series: str) -> list:
        if series not in self.lists:
            try:
                events = self.kalshi.pages("/events", {"series_ticker": series, "status": "open",
                                                       "with_nested_markets": "true", "limit": 200}, "events")
                if not all(isinstance(e, dict) and isinstance(e.get("event_ticker"), str) and
                           ("markets" not in e or (isinstance(e["markets"], list) and
                                                   all(isinstance(m, dict) for m in e["markets"]))) for e in events):
                    raise kd.KalshiDataError("an event not in Kalshi's shape")
                self.lists[series] = events
            except (kd.KalshiDataError, kd.HttpError, ValueError, TypeError, AttributeError) as e:
                self.lists[series] = Defer("deferred_list")
        got = self.lists[series]
        if isinstance(got, Defer):
            raise Defer(got.kind)
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
        if cid in self.pm_failed:                        # a failed read is not asked again in the same step
            raise Defer("deferred_description")
        try:
            got = self.gamma.market(cid, closed_first=False)
        except GammaError as e:
            self.pm_failed[cid] = "Polymarket's description: %s" % e
            raise Defer("deferred_description")
        if got is None or not isinstance(got.get("description"), str):
            self.pm_failed[cid] = "Polymarket's description: no such market"
            raise Defer("deferred_description")
        try:                                             # the answer must be for this market and list this token
            listed = json.loads(got.get("clobTokenIds")) if isinstance(got.get("clobTokenIds"), str) else None
        except ValueError:
            listed = None
        if not isinstance(listed, list) or h["token_id"] not in listed:
            raise Skip("token_not_in_market", "the description does not list the holding's token")
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
    def closes(self) -> None:
        """Each open copy in one unit of work: its settlement check, then, when it is not final, its exit check.
        (a) and (c): the copy's series list of open events is read once a step (the same list the new holdings use);
        a copy whose market is in it and open needs no market read and is not final; any other gets ONE market read
        (two on a live 404), and a finalized one is closed at its settlement. (b), "before that" (a market already
        final is closed at its settlement): a copy shown NOT final this step, whose opening wallet's EXIT of that
        token is in the record after the copy's read, and that is not held to settlement: Polymarket's market is read
        first (closed there while Kalshi trades: the venues part, flagged and held), then ONE read of Kalshi's book,
        the side's bid (none: flagged and held). A series whose list failed is not checked this step (nor exited).
        The units start after the copy whose paid unit was done last (kept in meta), so a step stopped at the cap is
        carried on by the next one; a cap reached inside a unit stops the step (CapReached) and the unit is done
        again, whole, by the next step. A unit costs at most MAX_UNIT requests after the lists."""
        after = self.book.q("SELECT value FROM meta WHERE key='settle_after'")
        after = int(after[0][0]) if after else 0
        open_copies = self.book.q("SELECT copy_id FROM copies WHERE status='open' ORDER BY copy_id <= ?, copy_id",
                                  (after,))
        for i, (copy_id,) in enumerate(open_copies):
            before = self.pacer.count
            try:
                self._close_unit(copy_id)
            except CapReached:
                self.n["settle_left"] = len(open_copies) - i
                raise
            self.n["units"] += 1
            if self.pacer.count > before:
                self.book.tx(lambda: self.book.w("INSERT OR REPLACE INTO meta (key, value) VALUES ('settle_after', ?)",
                                                 (str(copy_id),)))

    def _close_unit(self, copy_id: int) -> None:
        series, ticker, side, cid, tok = self.book.q("SELECT series, kalshi_market, side, condition_id, token_id FROM "
                                                     "copies WHERE copy_id=?", (copy_id,))[0]
        try:
            events = self._list(series)
        except Defer:
            self.n["settle_list_failed"] += 1
            return
        listed = [(e, m) for e in events for m in (e.get("markets") or [])
                  if isinstance(m, dict) and m.get("ticker") == ticker]
        shown = len(listed) == 1 and market_open(listed[0][1], self.wall())
        if not shown:
            try:
                rec = self.kalshi.market(ticker)
            except (kd.KalshiDataError, kd.HttpError, ValueError, TypeError, AttributeError):
                self.n["settle_read_failed"] += 1
                return
            got = settlement(rec, ticker, side, self.wall())
            if got is not None:
                self._settle(copy_id, cid, tok, got)
                return
            if isinstance(rec, dict) and rec.get("status") == "finalized":
                # Kalshi has settled it and its payout cannot be read here: no book read, no exit; the copy stays open
                # and is flagged once, and every later step reads the market again
                self.n["settle_unreadable"] += 1
                if not self.book.q("SELECT 1 FROM copy_flags WHERE copy_id=? AND kind='settlement_unreadable'",
                                   (copy_id,)):
                    self._flag(copy_id, "settlement_unreadable", "")
                return
        self._exit(copy_id, *(listed[0] if shown else (None, None)))   # shown not final this step

    def _settle(self, copy_id: int, cid: str, tok: str, got: tuple) -> None:
        value, ts, fair = got
        flags = [("kalshi_fair_price", "")] if fair else []
        flags += self._part_at_settlement(cid, tok, value)
        now = self.wall()

        def close_rows():
            for kind, detail in flags:
                self.book.w("INSERT INTO copy_flags (copy_id, kind, ts, detail) VALUES (?, ?, ?, ?)",
                            (copy_id, kind, now, detail))
            self.book.w("UPDATE copies SET status='closed', close_kind='settled', close_ts=?, close_price=?, "
                        "close_detail=? WHERE copy_id=? AND status='open'",
                        (ts, value, "Kalshi finalized; read at %.0f" % now, copy_id))
        self.book.tx(close_rows)
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
            if not isinstance(tokens, list) or not isinstance(prices, list):
                return [("part_check_failed", "Polymarket's token or price list is not a list")]
            paid = float(prices[tokens.index(tok)])
        except (KeyError, ValueError, TypeError, IndexError):
            return [("part_check_failed", "Polymarket's outcome for the token")]
        if not math.isclose(paid, value, abs_tol=1e-9):
            return [("parted_settled_differently", "")]
        return []

    def _exit(self, copy_id: int, ev: dict | None = None, m: dict | None = None) -> None:
        """(b): priced at the bid of the copy's side in THE book read of the market for the pass of the read that
        showed the EXIT (`book_read`: a read already kept for that market and pass, a match read too, is used with no
        request). A read of that pass that failed, or was begun and never kept, is no bid for that pass: flagged
        `exit_read_failed` and held to settlement (the exit was that pass's; a later pass asks nothing more)."""
        held = ", ".join("'%s'" % k for k in HOLD_FLAGS)
        got = self.book.q("SELECT wallet_key, read_id, token_id, condition_id, kalshi_market, side FROM copies c "
                          "WHERE copy_id=? AND status='open' AND NOT EXISTS (SELECT 1 FROM copy_flags f WHERE "
                          "f.copy_id = c.copy_id AND f.kind IN (%s))" % held, (copy_id,))
        if not got:
            return
        key, read_id, tok, cid, ticker, side = got[0]
        ex = first_exit(self.rec, key, tok, read_id)
        if ex is None:
            return
        try:
            pm = self.gamma.market(cid, closed_first=False)
        except GammaError:
            self.n["exit_pm_read_failed"] += 1
            return
        if pm is not None and pm.get("closed") is True:
            self._flag(copy_id, "parted_pm_closed_first", "exit change %d" % ex[0])
            return
        ok, stamp, raw, detail = self.book_read(ticker, pass_key({"pass_id": ex[3], "read_id": ex[1]}), ev, m)
        if not ok:
            self._flag(copy_id, "exit_read_failed", "exit change %d: %s" % (ex[0], detail))
            return
        book = json.loads(raw)
        bid = side_quote({"yes": kd.normalize_levels(book.get("yes_dollars")),
                          "no": kd.normalize_levels(book.get("no_dollars"))}, side)["bid"]
        if bid is None:
            self._flag(copy_id, "exit_no_bid", "exit change %d" % ex[0])
            return

        def exit_rows():
            self.book.w("UPDATE copies SET status='closed', close_kind='exit', close_ts=?, close_price=?, "
                        "close_detail=? WHERE copy_id=? AND status='open'",
                        (stamp, bid, "exit change %d read at %.0f" % (ex[0], ex[2]), copy_id))
        self.book.tx(exit_rows)
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
    st.step_id = step_id
    reasons: Counter = Counter()
    done = 0
    capped = False
    stage = "the new holdings"
    try:
        for h in todo:
            if h["pass_id"] is not None and rec.execute("SELECT status FROM passes WHERE pass_id=?",
                                                        (h["pass_id"],)).fetchone() == ("running",):
                st.n["pass_still_running"] += 1       # a pass that started after the running ones were collected
                continue
            out = st.consider(h)
            if "defer" in out:                        # no book read was made: not considered, not skipped
                st.n[out["defer"]] += 1
                continue
            reasons[st.write(h, out, step_id)] += 1
            done += 1
        stage = "the settlement checks and their exits"
        st.closes()
    except CapReached:
        capped = True
    opened = reasons.pop("copy", 0)
    status = "capped" if capped else "done"
    book.tx(lambda: book.w("UPDATE steps SET finished_ts=?, status=?, kalshi_requests=?, gamma_requests=?, "
                           "considered=?, opened=?, closed=? WHERE step_id=?",
                           (wall(), status, pacer.count, gamma.requests, done, opened,
                            st.n["closed_settled"] + st.n["closed_exit"], step_id)))
    deferred = " ".join("%s=%d" % (k, st.n[k]) for k in ("deferred_list", "deferred_description", "pass_still_running")
                        if st.n[k])
    say("step %d: considered %d of %d (copies opened %d; skips %s); closed settled=%d exit=%d; settlement checks "
        "left for the next step %d; flags %s; Kalshi requests %d of the cap %d; Polymarket requests %d"
        % (step_id, done, len(todo), opened, " ".join("%s=%d" % (r, reasons[r]) for r in SKIP_REASONS if reasons[r])
           or "none", st.n["closed_settled"], st.n["closed_exit"], st.n["settle_left"],
           " ".join("%s=%d" % (k[5:], v) for k, v in sorted(st.n.items()) if k.startswith("flag_")) or "none",
           pacer.count, pacer.cap, gamma.requests) + ("; left for a later step: " + deferred if deferred else ""))
    if capped:
        say("STOPPED step %d at the cap of %d Kalshi requests, in %s: left for the next step %d holdings and %s "
            "(nothing considered is lost or made twice)"
            % (step_id, pacer.cap, stage, len(todo) - done,
               "%d settlement checks, each with its exit check" % st.n["settle_left"] if st.n["settle_left"] else
               "every settlement check and exit check"))
        if done == 0 and st.n["units"] == 0:
            say("NO PROGRESS in step %d: the cap of %d Kalshi requests was spent before one holding or one copy's "
                "checks were done (Kalshi's lists ran to more pages than the cap allows); a larger --kalshi-cap is "
                "needed" % (step_id, pacer.cap))
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
    if not math.isfinite(v):
        raise argparse.ArgumentTypeError("the pause is a finite number of seconds")
    if not v >= MIN_PAUSE:
        raise argparse.ArgumentTypeError("the pause is never under %.1f seconds" % MIN_PAUSE)
    return v


def _cap(text: str) -> int:
    if not math.isfinite(float(text)):
        raise argparse.ArgumentTypeError("the cap is a finite whole number of requests")
    v = int(text)
    if v < MIN_CAP:
        raise argparse.ArgumentTypeError("the cap is at least %d: the %d series lists a step can need, then one unit "
                                         "of work (a market read, two on a live 404, and an exit's book read); a "
                                         "smaller cap could spend every step on the lists" % (MIN_CAP, MIN_CAP - MAX_UNIT))
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
    if Path(args.book).resolve() == Path(args.record).resolve():
        say("REFUSED refused: the book cannot be the record; nothing done")
        return REFUSED
    lock, bad = _lock_book(Path(args.book))                  # before anything is read or sent
    if bad:
        say(bad[1])
        return bad[0]
    try:
        return _step(args, say, kalshi_fetch, gamma_fetch, wall, sleep, clock, write_hook, fmap)
    finally:
        os.close(lock)                                       # (the operating system drops it too if the process dies)


def _lock_book(book: Path):
    """An exclusive, non-blocking flock on `<book>.step.lock` (made if missing; not part of the book's bytes): the fd,
    or (None, (code, words)). One step at a time on a book: two would read the same kalshi_last and send requests
    with no pause between them, and the second would mark the first `died` while it lives."""
    path = Path(os.path.realpath(book) + ".step.lock")       # the real path: every spelling of a book is one lock
    try:
        fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as e:
        return None, (REFUSED, "refused: the lock %s could not be made (%s); nothing done" % (path, type(e).__name__))
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as e:
        os.close(fd)
        if isinstance(e, BlockingIOError):
            return None, (STEP_RUNNING, "refused: another step is running on this book; nothing done")
        return None, (REFUSED, "refused: the lock %s could not be taken (%s); nothing done" % (path, type(e).__name__))
    return fd, None


def _step(args, say, kalshi_fetch, gamma_fetch, wall, sleep, clock, write_hook, fmap) -> int:
    rec, bad = open_record(Path(args.record))
    if bad:
        say("REFUSED " + bad[1] if bad[0] == REFUSED else bad[1])
        return bad[0]
    try:
        conn, bad = open_book(Path(args.book), create=True)
        if bad:
            say(bad[1])
            return bad[0]
        try:
            book = Book(conn, write_hook)
            last = book.q("SELECT value FROM meta WHERE key='kalshi_last'")

            def keep(t: float) -> None:                  # the time of the last Kalshi request, before it is sent
                book.tx(lambda: book.w("INSERT OR REPLACE INTO meta (key, value) VALUES ('kalshi_last', ?)",
                                       (repr(float(t)),)))
            pacer = StepPacer(args.kalshi_pause_seconds, args.kalshi_cap, sleep, clock, wall=wall,
                              last_wall=float(last[0][0]) if last else None, keep=keep)
            kalshi = kalshi_client(kalshi_fetch or kalshi_urllib_get, pacer)
            gamma = GammaReader(gamma_fetch or gamma_urllib_get, sleep=sleep, clock=clock)
            return run_step(rec, book, kalshi, pacer, gamma, fmap or load_map(), wall, say)
        finally:
            conn.close()
    finally:
        rec.close()


if __name__ == "__main__":
    sys.exit(main())
