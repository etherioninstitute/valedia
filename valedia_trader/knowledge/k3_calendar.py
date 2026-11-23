"""K-3: market calendars and closures, the first feed into the knowledge store.

What it does, in plain words:
- Turns the XNYS calendar (the `exchange_calendars` library) and three closure sources into versioned facts in the
  knowledge store: Finnhub's holiday list, Finnhub's market status, and the Federal Register's presidential documents.
- Answers one question, `closure_votes(conn, trade_date, as_of_utc)`: what did she know, at that time, about whether
  the exchange was open on that date, and how many independent authorities said it was closed?
- Fetches nothing by itself. Every fetch goes through an injected `http_get(url, headers) -> bytes`. Only the command
  line builds a real client, and the owner runs it after reading each source's terms.

Sources considered and left out:
- The Nasdaq Trader trade-halts feed was considered and excluded on its terms: its copyright notice bars anything
  "stored for subsequent use" without written consent, apart from personal viewing, and a store is exactly that.
  There is no entry, parser or entity key for it here.
- The exchange's own closure notice is a third authority that is NOT built yet. It is a slot: add a source with the
  role AUTHORITY and its own `authority_group` to SOURCES and one rule to _CLOSURE_RULES, and the quorum counts it
  with no other change.

Removed holidays: when a Finnhub holiday read no longer lists a date that an earlier read listed, and the date is inside
the range the new list covers (its earliest to its latest date), `refresh` stores a "removed" version for it (closed
false), so the old closure stops counting. A date outside that range only rolled off the list's edge: it is left alone.

Roles: SCHEDULE (the library, derived), AUTHORITY (a source that can say "closed" and votes, through its group),
SIGNAL (logged only, never votes, has no group; no source has this role today).

Times and dates:
- A source that gives only a date ("2026-01-15") is known at the END of that date in the source's own zone, in UTC
  (`date_only_known_at`). For New York in winter that is 04:59:59.999999 UTC on the next day. 23:59:59 UTC would
  claim the fact an hour or so too early, so it is never used.
- Federal Register `publication_date` is the known time. `signing_date` is never used for it: documents usually
  appear days after they are signed.

The Federal Register lag: a document is published days after signing, and its known time is the end of its
publication date. For a sudden closure the Federal Register therefore usually votes too late to help on the day
itself. It confirms a closure afterwards (and warns of planned ones, such as a day of mourning announced ahead).

The Federal Register closure-date rule (`fr_closure_dates`), kept small on purpose. It names a date only when ALL of
these hold, and returns an empty list otherwise (it never guesses):
1. the document's subtype is "Executive Order" or "Proclamation";
2. the title or abstract has no word of change or withdrawal (amend, revoke, rescind, cancel, postpone, ...);
3. a sentence of the title or abstract contains a trigger: "National Day of Mourning", or an order closing executive
   departments and agencies;
4. that same sentence states each date in full ("December 24, 2025": month name, day, year);
5. the sentence has no half-stated date (a month and day with no year) and, when it has several dates, they are a
   plain list joined by commas or "and". Any other mix (for example a death date and a mourning date) is unsure;
6. the dates are grammatically the closure day, in one of the forms: "<date>, as a National Day of Mourning";
   "National Day of Mourning on <date>"; "declare/designate <date> ... National Day of Mourning"; or an order closing
   executive departments and agencies "... on <date>" (letters and spaces only between the order and "on");
7. the sentence has none of the words through, beginning, until, died, death, passed, half-staff, flag, following,
   after, since, from, born, anniversary (a span, a start or a death is not the closure day).
One unsure sentence voids the whole document: the result is then an empty list, whatever other sentences say.

Only the National Day of Mourning forms VOTE. `fr_closure_dates` returns the dates of those forms and nothing else. An
order "closing executive departments and agencies ... on <date>" casts no vote: the Federal Register lists 17 such
orders since 1998, and 15 of them were Christmas-week leave on days the exchange was open (only the two mourning days
were real closures, and a separate proclamation declared each National Day of Mourning). The dates of a closing order
are kept in the payload field `closing_order_dates`, a signal for the log and never a vote. A document with an unsure
sentence has neither kind of date.

Death proclamations (T63). A Proclamation titled "Announcing the Death of ..." or naming a National Day of Mourning often
has an empty abstract and no date in the title: the date is in the body ("I do further appoint <date>, as a National Day
of Mourning"). For such a document only, `refresh` makes one extra request for its `raw_text_url` (https on
www.federalregister.gov only; the same client, user agent and size cap). `fr_body_closure_dates(body_text)` strips tags,
collapses whitespace and reads only the sentences that contain "National Day of Mourning", with the same forms and void
words as above, so the death and half-staff sentences never vote or void. One unsure sentence across title, abstract and
body voids the document. The payload keeps the dates, `body_read` and the body's sha256 (`body_sha256`), never the text.
A body that cannot be fetched or read (another host, a failed request, an empty or oversize answer) stores the document
with `closure_dates: []`, `body_read: false` and counts one error: it never guesses.

Quorum (`closure_votes`): `ordinary_stop` is "the calendar says open and at least two authority GROUPS say closed";
`suspected` is "open and exactly one group says closed". Every source has an `authority_group` (the sources of one
vendor or one government share a group: both Finnhub endpoints are FINNHUB, the Federal Register and the White House
are US_PRESIDENT, the XNYS library is XNYS_CALENDAR), and a group counts once however many of its sources, or facts,
say closed, so one vendor's data error cannot reach the quorum alone. `authorities_closed` is the sorted groups that say
closed; `sources_closed` is the sorted source ids behind them, for the log. A Finnhub status counts only with a
non-null `holiday`: before the open `isOpen` is false every day, so it is never a vote. Facts known after `as_of_utc`
never count. Everything is read through the store's `facts_as_of`. The group is kept in code only: the source registry
has no column for it, and registration is unchanged.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo

from valedia_trader.knowledge import store

_UA = "valedia-client/1.0"          # generic: no owner, project or email
_NY = "America/New_York"
_DOMAIN = "K3_CAL"
_FINNHUB_KEY_NAME = "FINNHUB_API_KEY"
_FR_URL = "https://www.federalregister.gov/api/v1/documents.json"
_FR_FIELDS = ("document_number", "title", "type", "subtype", "publication_date", "signing_date", "html_url", "abstract",
               "raw_text_url")
_FR_PER_PAGE = 100                  # the Federal Register's largest page; a bigger window is refused, never cut
_FR_DEFAULT_DAYS = 30
_FR_MAX_DAYS = 92
_SCHEDULE_MAX_DAYS = 3660
_FINNHUB_TERMS = ("terms: personal use only; no sharing with third parties; all data must be deleted if the "
                  "subscription ends; 30 calls/s cap")
_MAX_BODY = 5_000_000
_HTTP_TIMEOUT_S = 30                # the command line's client only; one named knob


class K3Error(ValueError):
    """The input, the setup or a source's answer is not what this module accepts."""


SOURCES = {
    "xnys_exchange_calendars": {
        "source_id": "xnys_exchange_calendars", "role": "SCHEDULE", "endpoint": None,
        "terms_url": "https://github.com/gerrymanoim/exchange_calendars/blob/master/LICENSE",
        "licence_tag": "Apache-2.0", "allow_external_llm": 1, "ua_policy": "GENERIC_CLIENT", "cost_usd_monthly": 0,
        "time_zone": _NY, "authority_group": "XNYS_CALENDAR",
    },
    "finnhub_market_holiday": {
        "source_id": "finnhub_market_holiday", "role": "AUTHORITY",
        "endpoint": "https://finnhub.io/api/v1/stock/market-holiday?exchange=US",
        "terms_url": "https://finnhub.io/terms-of-service",
        "licence_tag": "FINNHUB-PERSONAL-TOS", "notes": _FINNHUB_TERMS, "allow_external_llm": 0, "ua_policy": "GENERIC_CLIENT", "cost_usd_monthly": 0,
        "time_zone": _NY, "authority_group": "FINNHUB",
    },
    "finnhub_market_status": {
        "source_id": "finnhub_market_status", "role": "AUTHORITY",
        "endpoint": "https://finnhub.io/api/v1/stock/market-status?exchange=US",
        "terms_url": "https://finnhub.io/terms-of-service",
        "licence_tag": "FINNHUB-PERSONAL-TOS", "notes": _FINNHUB_TERMS, "allow_external_llm": 0, "ua_policy": "GENERIC_CLIENT", "cost_usd_monthly": 0,
        "time_zone": _NY, "authority_group": "FINNHUB",
    },
    "federal_register_presdocs": {
        "source_id": "federal_register_presdocs", "role": "AUTHORITY", "endpoint": _FR_URL,
        "terms_url": "https://www.federalregister.gov/reader-aids/using-federalregister-gov/developer-resources",
        "licence_tag": "US-PUBLIC-DOMAIN", "allow_external_llm": 1, "ua_policy": "GENERIC_CLIENT",
        "cost_usd_monthly": 0, "time_zone": _NY, "authority_group": "US_PRESIDENT",   # a White House source joins it
    },
}
_X, _H, _S, _FR = ("xnys_exchange_calendars", "finnhub_market_holiday", "finnhub_market_status",
                   "federal_register_presdocs")
_ROLES = ("SCHEDULE", "AUTHORITY", "SIGNAL")


# ---- small helpers

_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_UTC_RE = re.compile(r"([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})(\.[0-9]{1,6})?Z")
_DOC_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")


def _fmt(moment: datetime) -> str:
    """A UTC time in the store's fixed form."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _zone(name) -> ZoneInfo:
    if not isinstance(name, str) or not name.strip():
        raise K3Error("a date-only stamp needs the source's time zone name")
    try:
        return ZoneInfo(name)
    except Exception:
        raise K3Error(f"unknown time zone {name[:40]!r}") from None


def _parse_date(value, what: str) -> date:
    """A date written exactly YYYY-MM-DD (or a plain date object)."""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        raise K3Error(f"{what}: must be a date written YYYY-MM-DD, got {str(value)[:30]!r}")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise K3Error(f"{what}: not a real date: {value!r}") from None


def _utc_text(value, what: str) -> str:
    """A UTC time with a Z suffix, returned in the store's fixed form."""
    m = _UTC_RE.fullmatch(value) if isinstance(value, str) else None
    if not m:
        raise K3Error(f"{what}: must be UTC ISO-8601 ending in Z, got {str(value)[:40]!r}")
    try:
        datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        raise K3Error(f"{what}: not a real time: {value!r}") from None
    return m.group(1) + "." + (m.group(2) or ".")[1:].ljust(6, "0") + "Z"


def _now_text(now) -> str:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise K3Error("now: must be a timezone-aware datetime")
    return _fmt(now)


def _day_start_utc(day: date, zone_name: str) -> str:
    return _fmt(datetime(day.year, day.month, day.day, tzinfo=_zone(zone_name)))


def date_only_known_at(date_str, tz_name) -> str:
    """The end of `date_str` (23:59:59.999999 local) in the source's own zone, as a UTC time in the store's form.
    A source with no zone cannot use it."""
    zone = _zone(tz_name)
    day = _parse_date(date_str, "date")
    return _fmt(datetime(day.year, day.month, day.day, 23, 59, 59, 999999, tzinfo=zone))


def _payload(obj: dict) -> tuple[str, str]:
    """The normalized payload text (sorted keys, no spaces) and its sha256."""
    text = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()


def _fact(source_id, entity_key, payload, *, effective_from, effective_to, fetched, published=None, proven=False) -> dict:
    text, digest = _payload(payload)
    return {"domain": _DOMAIN, "entity_key": entity_key, "source_id": source_id, "raw_sha256": digest,
            "payload_json": text, "effective_from_utc": effective_from, "effective_to_utc": effective_to,
            "fetched_at_utc": fetched, "published_at_utc": published, "published_proven": proven}


def _text(value, what: str, *, allow_none=False, non_empty=True):
    if value is None and allow_none:
        return None
    if (not isinstance(value, str) or (non_empty and not value) or "\x00" in value
            or not _encodable(value)):
        raise K3Error(f"{what}: must be text, got {str(value)[:40]!r}")
    return value


def _encodable(text: str) -> bool:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _load(raw, what: str) -> dict:
    """The body as a JSON object, or a refusal."""
    if not isinstance(raw, bytes) or not raw:
        raise K3Error(f"{what}: the body must be non-empty bytes")

    def no_constant(token):
        raise ValueError(token)

    try:
        body = json.loads(raw.decode("utf-8"), parse_constant=no_constant)
    except (ValueError, RecursionError):
        raise K3Error(f"{what}: the body is not valid JSON") from None
    if not isinstance(body, dict):
        raise K3Error(f"{what}: the body must be a JSON object")
    return body


# ---- source registration

def register_all(conn, terms_read_at_utc, *, only) -> None:
    """Register the named sources. The caller decides which: the owner reads the terms first. Same values again:
    nothing happens. An unknown, empty or malformed list registers nothing."""
    if not isinstance(only, (list, tuple, set, frozenset)) or not only:
        raise K3Error("only: name the sources to register, as a non-empty list")
    names = list(only)
    for name in names:
        if not isinstance(name, str) or name not in SOURCES:
            raise K3Error(f"only: unknown source {str(name)[:60]!r}")
    for name in names:
        e = SOURCES[name]
        store.register_source(
            conn, source_id=e["source_id"], terms_url=e["terms_url"], licence_tag=e["licence_tag"],
            personal_use_ok=True, allow_external_llm=bool(e["allow_external_llm"]), ua_policy=e["ua_policy"],
            terms_read_at_utc=terms_read_at_utc, cost_usd_monthly=e["cost_usd_monthly"],
            notes=f"role={e['role']}; time_zone={e['time_zone']}" + (f"; {e['notes']}" if e.get("notes") else ""))


# ---- the XNYS schedule

def _check_range(start, end) -> tuple[date, date]:
    s, e = _parse_date(start, "start"), _parse_date(end, "end")
    if e < s:
        raise K3Error("end is before start")
    if (e - s).days > _SCHEDULE_MAX_DAYS:
        raise K3Error(f"the range is longer than {_SCHEDULE_MAX_DAYS} days")
    return s, e


def schedule_facts(start, end, *, now=None) -> list[dict]:
    """One fact per calendar date from `start` to `end` (both included): open or not, and the close in New York time
    (16:00, or 13:00 on an early close). Taken from the installed `exchange_calendars`, known when it was read."""
    s, e = _check_range(start, end)
    fetched = _now_text(now if now is not None else datetime.now(timezone.utc))
    try:
        import exchange_calendars
        # the library's bounds are sessions, so pad the range: a holiday at either end is then still inside
        cal = exchange_calendars.get_calendar("XNYS", start=(s - timedelta(days=10)).isoformat(),
                                              end=(e + timedelta(days=10)).isoformat())
    except Exception as exc:
        raise K3Error(f"the XNYS calendar could not be built for {s} to {e}: {type(exc).__name__}") from None
    facts = []
    day = s
    while day <= e:
        iso = day.isoformat()
        try:
            is_open = bool(cal.is_session(iso))
            close = cal.session_close(iso).tz_convert(_NY).strftime("%H:%M") if is_open else None
        except Exception as exc:
            raise K3Error(f"the XNYS calendar failed on {iso}: {type(exc).__name__}") from None
        facts.append(_fact(_X, f"XNYS:SESSION:{iso}", {"open": is_open, "close_et": close},
                           effective_from=_day_start_utc(day, _NY), effective_to=date_only_known_at(iso, _NY),
                           fetched=fetched))
        day += timedelta(days=1)
    return facts


# ---- parsers (pure: bytes in, fact dicts out, or a refusal of the whole body)

def parse_finnhub_holiday(raw, *, fetched_at_utc) -> list[dict]:
    """Finnhub `/stock/market-holiday`: `{"exchange":"US","timezone":"America/New_York","data":[{eventName, atDate,
    tradingHour}]}`. One fact per `atDate`; `closed` is `tradingHour == ""`. No publication time: known when fetched."""
    fetched = _utc_text(fetched_at_utc, "fetched_at_utc")
    body = _load(raw, "finnhub holiday")
    if body.get("exchange") != "US" or body.get("timezone") != _NY:
        raise K3Error("finnhub holiday: expected exchange US and timezone America/New_York")
    data = body.get("data")
    if not isinstance(data, list) or not data:
        raise K3Error("finnhub holiday: `data` must be a non-empty list (Finnhub always lists years of holidays, so "
                      "an empty list is a broken feed, not 'no holidays')")
    facts, seen = [], set()
    for item in data:
        if not isinstance(item, dict) or not {"eventName", "atDate", "tradingHour"} <= set(item):
            raise K3Error("finnhub holiday: each item needs eventName, atDate and tradingHour")
        day = _parse_date(item["atDate"], "finnhub holiday atDate")
        if not isinstance(item["atDate"], str) or item["atDate"] in seen:
            raise K3Error(f"finnhub holiday: repeated or malformed date {str(item['atDate'])[:20]!r}")
        seen.add(item["atDate"])
        hours = _text(item["tradingHour"], "tradingHour", non_empty=False)
        name = _text(item["eventName"], "eventName", non_empty=False)
        facts.append(_fact(_H, f"XNYS:CLOSURE:{item['atDate']}", {"closed": hours == "", "trading_hours": hours,
                                                                   "name": name},
                           effective_from=_day_start_utc(day, _NY), effective_to=date_only_known_at(item["atDate"], _NY),
                           fetched=fetched))
    return facts


def parse_finnhub_status(raw, *, fetched_at_utc) -> list[dict]:
    """Finnhub `/stock/market-status`: `{"exchange":"US","holiday":null|str,"isOpen":bool,"session":str,
    "timezone":...,"t":unix}`. One fact, keyed by the New York date of `t`. Only a non-null `holiday` is a closure
    vote: before the open `isOpen` is false on every day."""
    fetched = _utc_text(fetched_at_utc, "fetched_at_utc")
    body = _load(raw, "finnhub status")
    if body.get("exchange") != "US" or body.get("timezone") != _NY:
        raise K3Error("finnhub status: expected exchange US and timezone America/New_York")
    if not {"holiday", "isOpen", "session", "t"} <= set(body):
        raise K3Error("finnhub status: needs holiday, isOpen, session and t")
    t, is_open = body["t"], body["isOpen"]
    if type(is_open) is not bool:
        raise K3Error("finnhub status: isOpen must be true or false")
    if type(t) is not int or not 0 <= t <= 4102444800:
        raise K3Error("finnhub status: t must be a whole number of seconds since 1970")
    holiday = _text(body["holiday"], "holiday", allow_none=True, non_empty=False)
    session = _text(body["session"], "session", non_empty=False)
    moment = datetime.fromtimestamp(t, timezone.utc)
    ny_day = moment.astimezone(ZoneInfo(_NY)).date().isoformat()
    return [_fact(_S, f"XNYS:STATUS:{ny_day}", {"holiday": holiday, "session": session, "is_open": is_open},
                  effective_from=_fmt(moment), effective_to=None, fetched=fetched)]


_MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
           "November", "December")
_MONTH = "(?:" + "|".join(_MONTHS) + ")"
_FULL_DATE = re.compile(rf"\b({_MONTH})\s+([0-9]{{1,2}}),\s+([0-9]{{4}})\b")
_MONTH_DAY = re.compile(rf"\b{_MONTH}\s+[0-9]{{1,2}}\b")
_LIST_GAP = re.compile(r"\s*,\s*|\s+and\s+|\s*,\s+and\s+")
_SENTENCE = re.compile(r"(?<=\.)\s+(?=[A-Z])")
_MOURNING = re.compile(r"\bnational\s+day\s+of\s+mourning\b", re.I)
_CLOSING = re.compile(r"\bclos(?:ing|ure|es|e)\s+(?:of\s+)?(?:the\s+)?executive\s+departments?\s+and\s+agenc(?:y|ies)\b",
                      re.I)
_CHANGE = re.compile(r"\b(?:amend\w*|revok\w*|revocation|rescind\w*|rescission|cancel\w*|postpon\w*|supersed\w*|"
                     r"repeal\w*|vacat\w*)\b", re.I)
_FR_KINDS = ("executive order", "proclamation")


_WEEKDAY = r"(?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+)?"
_ONE_DATE = rf"{_MONTH}\s+[0-9]{{1,2}},\s+[0-9]{{4}}"
_DATE_LIST = rf"(?P<dl>{_ONE_DATE}(?:(?:\s*,\s+and\s+|\s*,\s*|\s+and\s+){_ONE_DATE})*)"
_MOURN = r"(?i:national\s+day\s+of\s+mourning)"
_CLOSE = r"(?i:clos(?:ing|ure|es|e)\s+(?:of\s+)?(?:the\s+)?executive\s+departments?\s+and\s+agenc(?:y|ies))"
# The only grammatical forms that make a date the closure day. The date list (`dl`) must be all the dates of the sentence.
# The mourning forms vote; the closing-order form is only a signal.
_MOURNING_ANCHORS = (
    re.compile(rf"{_DATE_LIST},?\s+as\s+(?:(?i:a|the)\s+)?{_MOURN}"),                       # <date>, as a National Day of Mourning
    re.compile(rf"{_MOURN}\s+(?i:on)\s+{_WEEKDAY}{_DATE_LIST}"),                             # National Day of Mourning on <date>
    re.compile(rf"(?i:\b(?:declar|designat)\w*)\s+{_WEEKDAY}{_DATE_LIST}[^.;]{{0,60}}?{_MOURN}"),   # designate <date> ... Mourning
)
_CLOSING_ANCHORS = (
    re.compile(rf"{_CLOSE}[A-Za-z ]{{0,100}}?\b(?i:on)\s+{_WEEKDAY}{_DATE_LIST}"),         # closing ... on <date>
)
# Words that make a date in a trigger sentence something else: a span, a start, a death, a flag order.
_NOT_THE_DAY = re.compile(r"\b(?:through|beginning|begins?|until|died|dies|death|deaths|passed|passing|half-staff|"
                          r"flags?|following|after|since|from|born|anniversary|"
                          r"not|proposed|notice|ending|ends?|only|early|noon|region|area|portion|remembering|"
                          r"commemorat\w*|"
                          r"no|never|none|neither|nor|won't|cannot|decline\w*|withdraw\w*|rescind\w*|revok\w*|cancel\w*|"
                          r"postpon\w*|in\s+the\s+state\s+of|county|city|in\s+\d{4})\b", re.I)
# lead: negation, partial scope and past dates void the sentence too (T62 blind check: no/never/decline/withdraw/...,
# a single state, and "in <year>" history sentences also voided). A voided sentence voids the document: miss, never wrong.


def _sentence_dates(sentence: str):
    """The dates a trigger sentence states, as (mourning dates, closing-order dates), each a list of ISO text (both empty
    when the sentence states no date); None when the sentence is unsure. A date counts only in one of the forms of
    `_MOURNING_ANCHORS` or `_CLOSING_ANCHORS`, as the whole of the sentence's dates, with no word of `_NOT_THE_DAY` in
    the sentence. A sentence that fits a mourning form is a mourning sentence, even if it also fits the closing form."""
    full = list(_FULL_DATE.finditer(sentence))
    if len(_MONTH_DAY.findall(sentence)) != len(full):
        return None                                        # a month and day with no year: not a stated date
    if not full:
        return [], []
    for a, b in zip(full, full[1:]):
        if not _LIST_GAP.fullmatch(sentence[a.end():b.start()]):
            return None                                    # the dates are not a plain list
    if _NOT_THE_DAY.search(sentence):
        return None
    first, last = full[0].start(), full[-1].end()

    def anchored(anchors) -> bool:
        return any(m.start("dl") == first and m.end("dl") == last for rx in anchors for m in rx.finditer(sentence))

    is_mourning = anchored(_MOURNING_ANCHORS)
    if not is_mourning and not anchored(_CLOSING_ANCHORS):
        return None                                        # a date, but not grammatically the closure day
    out = []
    for m in full:
        try:
            out.append(date(int(m.group(3)), _MONTHS.index(m.group(1)) + 1, int(m.group(2))).isoformat())
        except ValueError:
            return None                                    # not a real date
    return (out, []) if is_mourning else ([], out)


def _scan_text(parts, *, mourning_only=False):
    """(mourning dates, closing-order dates) of the trigger sentences of the given texts, each a sorted unique list; None
    when one trigger sentence is unsure. With `mourning_only`, only a sentence that names the National Day of Mourning is
    a trigger sentence (a document body: its other sentences never are)."""
    mourning, closing = set(), set()
    for part in parts:
        for sentence in _SENTENCE.split(part):
            if not (_MOURNING.search(sentence) or (not mourning_only and _CLOSING.search(sentence))):
                continue
            found = _sentence_dates(sentence)
            if found is None:
                return None
            mourning.update(found[0])
            closing.update(found[1])
    return sorted(mourning), sorted(closing)


def _fr_scan(title, abstract, subtype) -> tuple[list[str], list[str]]:
    """(mourning dates, closing-order dates) a presidential document names, each sorted and unique. Both empty when the
    document is the wrong kind, changes or withdraws something, or has one unsure sentence."""
    _text(title, "title", non_empty=False)
    _text(abstract, "abstract", allow_none=True, non_empty=False)
    _text(subtype, "subtype", allow_none=True, non_empty=False)
    if subtype is None or subtype.strip().casefold() not in _FR_KINDS:
        return [], []
    if _CHANGE.search(title + " " + (abstract or "")):
        return [], []
    return _scan_text((title, abstract or "")) or ([], [])


_TAG = re.compile(r"<[^>]*>")
_DEATH_TITLE = re.compile(r"^\s*Announcing the Death of\b", re.I)
_FR_BODY_HOST = "www.federalregister.gov"


def _is_death_proclamation(title, subtype) -> bool:
    """A Proclamation announcing a death or naming a National Day of Mourning in its title: the kind whose abstract is
    often empty and whose date sits in the body, so `refresh` reads the body."""
    return (isinstance(subtype, str) and subtype.strip().casefold() == "proclamation" and isinstance(title, str)
            and bool(_DEATH_TITLE.search(title) or _MOURNING.search(title)))


def _plain_text(body_text: str) -> str:
    # the real raw text carries stray control characters (NUL among them): they are whitespace here
    return re.sub(r"[\s\x00-\x1f]+", " ", html.unescape(_TAG.sub(" ", body_text))).strip()


def fr_body_closure_dates(body_text) -> list[str]:
    """The National Day of Mourning dates a document body names, as sorted ISO dates; an empty list when unsure. Tags
    are stripped and whitespace collapsed, then only the sentences that contain "National Day of Mourning" are read,
    with the same forms and void words as `fr_closure_dates`. The other sentences (the death date, the half-staff
    order) are never trigger sentences. One unsure trigger sentence gives an empty list."""
    if not isinstance(body_text, str) or not _encodable(body_text):
        raise K3Error(f"body_text: must be text, got {str(body_text)[:40]!r}")
    found = _scan_text((_plain_text(body_text),), mourning_only=True)
    return found[0] if found else []


def fr_closure_dates(title, abstract, subtype) -> list[str]:
    """The closure dates a presidential document names, as sorted ISO dates; an empty list when unsure (the rule is in
    the module docstring). Only an Executive Order or Proclamation counts, and only the National Day of Mourning forms
    vote: an order closing executive departments and agencies gives no date here (see `_fr_closing_order_dates`)."""
    return _fr_scan(title, abstract, subtype)[0]


def _fr_closing_order_dates(title, abstract, subtype) -> list[str]:
    """The dates of an order closing executive departments and agencies, as sorted ISO dates: a signal for the log,
    never a vote. The same document rules as `fr_closure_dates` (kind, change words, one unsure sentence voids all)."""
    return _fr_scan(title, abstract, subtype)[1]


def parse_federal_register(raw, *, fetched_at_utc) -> list[dict]:
    """Federal Register `documents.json`: `{"count":n,"results":[{document_number, title, type, subtype,
    publication_date, signing_date, html_url, abstract}]}`, extra top-level keys allowed. The page must be the whole
    answer (`count` equals the number of results, no next page). One fact per document, known at the end of its
    publication date (never the signing date); if that is still ahead of the fetch, it is known when fetched."""
    fetched = _utc_text(fetched_at_utc, "fetched_at_utc")
    body = _load(raw, "federal register")
    results, count = body.get("results"), body.get("count")
    if not isinstance(results, list) or type(count) is not int:
        raise K3Error("federal register: needs a results list and a whole-number count")
    if count != len(results) or body.get("next_page_url") is not None or body.get("total_pages") not in (None, 0, 1):
        raise K3Error("federal register: the page is not the whole answer; narrow the date window")
    facts, seen = [], set()
    for doc in results:
        if not isinstance(doc, dict):
            raise K3Error("federal register: each result must be an object")
        for key in ("document_number", "title", "type", "publication_date", "html_url"):
            if key not in doc:
                raise K3Error(f"federal register: a result lacks {key}")
        number = _text(doc["document_number"], "document_number")
        if not _DOC_RE.fullmatch(number) or number in seen:
            raise K3Error(f"federal register: bad or repeated document_number {number[:30]!r}")
        seen.add(number)
        title = _text(doc["title"], "title")
        dtype = _text(doc["type"], "type")
        subtype = _text(doc.get("subtype"), "subtype", allow_none=True)
        pub = doc["publication_date"]
        pub_day = _parse_date(pub, "publication_date")
        if not isinstance(pub, str):
            raise K3Error("federal register: publication_date must be text")
        signing = doc.get("signing_date")
        if signing is not None:
            _parse_date(signing, "signing_date")
            if not isinstance(signing, str):
                raise K3Error("federal register: signing_date must be text")
        url = _text(doc["html_url"], "html_url")
        abstract = _text(doc.get("abstract"), "abstract", allow_none=True, non_empty=False)
        stamp = date_only_known_at(pub, _NY)
        proven = stamp <= fetched
        mourning, closing = _fr_scan(title, abstract, subtype)
        payload = {"document_number": number, "title": title, "type": dtype, "subtype": subtype,
                   "publication_date": pub, "signing_date": signing, "html_url": url, "abstract": abstract,
                   "closure_dates": mourning, "closing_order_dates": closing}
        if _is_death_proclamation(title, subtype):
            # the date is usually in the body, which `refresh` reads: until it has, the body is unread
            raw_url = doc.get("raw_text_url")
            payload.update({"raw_text_url": raw_url if isinstance(raw_url, str) else None, "body_read": False,
                            "body_sha256": None})
        facts.append(_fact(_FR, f"FR:{number}", payload, effective_from=_day_start_utc(pub_day, _NY),
                           effective_to=None, fetched=fetched, published=stamp if proven else None, proven=proven))
    return facts


_PARSERS = {_H: parse_finnhub_holiday, _S: parse_finnhub_status, _FR: parse_federal_register}


# ---- fetch and refresh

def _default_credentials(name: str) -> str:
    from valedia_trader.core import secrets
    return secrets.secret(name)


def _valid_key(key) -> bool:
    """1 to 256 printable ASCII characters, no space: fit for a header value."""
    return type(key) is str and 1 <= len(key) <= 256 and all(0x21 <= ord(c) <= 0x7E for c in key)


def _check_params(source_id: str, params, now_text: str):
    """The validated request window: (start, end) dates for the XNYS schedule and the Federal Register, None for
    Finnhub (which takes none)."""
    if source_id in (_H, _S):
        if params:
            raise K3Error(f"{source_id} takes no params")
        return None
    if params is None:
        params = {}
    if not isinstance(params, dict) or not set(params) <= {"start", "end"}:
        raise K3Error("params: only start and end are known")
    if source_id == _X:
        if "start" not in params or "end" not in params:
            raise K3Error("the XNYS schedule needs params start and end")
        return _check_range(params["start"], params["end"])
    end = _parse_date(params["end"], "end") if "end" in params else \
        datetime.fromisoformat(now_text.replace("Z", "+00:00")).astimezone(ZoneInfo(_NY)).date()
    start = _parse_date(params["start"], "start") if "start" in params else end - timedelta(days=_FR_DEFAULT_DAYS)
    if end < start:
        raise K3Error("end is before start")
    if (end - start).days > _FR_MAX_DAYS:
        raise K3Error(f"the window is longer than {_FR_MAX_DAYS} days")
    return start, end


def _fr_request_url(start: date, end: date) -> str:
    query = [("fields[]", f) for f in _FR_FIELDS]
    query += [("per_page", str(_FR_PER_PAGE)), ("order", "newest"), ("conditions[type][]", "PRESDOCU"),
              ("conditions[presidential_document_type][]", "proclamation"),
              ("conditions[presidential_document_type][]", "executive_order"),
              ("conditions[publication_date][gte]", start.isoformat()),
              ("conditions[publication_date][lte]", end.isoformat())]
    return _FR_URL + "?" + urlencode(query, safe="[]")


def fetch(source_id, *, http_get=None, now, credentials=None, params=None) -> list[dict]:
    """The facts of one source, fetched through the injected `http_get(url, headers) -> bytes`. The schedule needs no
    client. A Finnhub key comes from `credentials(name)` (default: `core.secrets`) and goes only in a header."""
    if not isinstance(source_id, str) or source_id not in SOURCES:
        raise K3Error(f"unknown source {str(source_id)[:60]!r}")
    fetched = _now_text(now)
    window = _check_params(source_id, params, fetched)
    if source_id == _X:
        return schedule_facts(window[0], window[1], now=now)
    if not callable(http_get):
        raise K3Error("http_get: a fetching function is required (there is no default client)")
    headers = {"User-Agent": _UA, "Accept": "application/json"}
    if source_id == _FR:
        url = _fr_request_url(*window)
    else:
        url = SOURCES[source_id]["endpoint"]
        try:
            key = (credentials or _default_credentials)(_FINNHUB_KEY_NAME)
        except Exception:
            raise K3Error(f"the {_FINNHUB_KEY_NAME} credential is not available") from None
        if not _valid_key(key):
            raise K3Error(f"the {_FINNHUB_KEY_NAME} credential cannot be used as a header value")
        headers["X-Finnhub-Token"] = key
    try:
        body = http_get(url, headers)
    except Exception as exc:
        raise K3Error(f"{source_id}: the request failed ({type(exc).__name__})") from None
    if not isinstance(body, bytes):
        raise K3Error(f"{source_id}: http_get must return bytes")
    return _PARSERS[source_id](body, fetched_at_utc=fetched)


def _holiday_tombstones(conn, facts: list[dict]) -> list[dict]:
    """New versions for holiday dates that an earlier read listed and this read no longer lists, when the date is
    inside the range this list covers (its earliest to its latest atDate). A date outside that range only rolled off
    the list's edge: it is not a removal. The new version is `{"closed": false, "removed": true, "trading_hours":
    null, "name": <old name>}`, so the quorum stops counting it. An already-removed date is left alone."""
    if not facts:
        return []
    fetched = facts[0]["fetched_at_utc"]
    listed = {f["entity_key"] for f in facts}
    days = sorted(k.rsplit(":", 1)[1] for k in listed)
    first, last = days[0], days[-1]
    out = []
    for row in store.facts_as_of(conn, fetched, domain=_DOMAIN):
        key = row["entity_key"]
        if row["source_id"] != _H or not key.startswith("XNYS:CLOSURE:") or key in listed:
            continue
        if not first <= key.rsplit(":", 1)[1] <= last:
            continue
        old = json.loads(row["payload_json"])
        if not isinstance(old, dict) or old.get("removed") is True:
            continue
        out.append(_fact(_H, key, {"closed": False, "removed": True, "trading_hours": None, "name": old.get("name")},
                         effective_from=row["effective_from_utc"], effective_to=row["effective_to_utc"],
                         fetched=fetched))
    return out


def _store_facts(conn, facts: list[dict]) -> dict:
    """Store parsed facts. Returns counts of stored (a new version), unchanged (the same read again) and error."""
    counts = {"stored": 0, "unchanged": 0, "error": 0}
    if not facts:
        return counts
    prior = {(r["entity_key"], r["source_id"]): r
             for r in store.facts_as_of(conn, facts[0]["fetched_at_utc"], domain=_DOMAIN)}
    before = {k: r["fact_version_id"] for k, r in prior.items()}
    detail = None
    for f in facts:
        old = prior.get((f["entity_key"], f["source_id"]))
        if old is not None and old["raw_sha256"] != f["raw_sha256"] and f["published_proven"]:
            # a revision of a stored entity is known when we fetched it, never back at the first publication
            f = dict(f, published_at_utc=None, published_proven=False)
        try:
            version = store.add_fact(conn, **f)
        except store.StoreError as exc:
            counts["error"] += 1
            detail = detail or str(exc)[:200]
            continue
        counts["unchanged" if before.get((f["entity_key"], f["source_id"])) == version else "stored"] += 1
    if detail:
        counts["detail"] = detail
    return counts


def _body_url_ok(url) -> bool:
    if not isinstance(url, str):
        return False
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme == "https" and parts.netloc == _FR_BODY_HOST


def _kept_good_read(conn, fact: dict, payload: dict) -> bool:
    """True when the version of this Federal Register document current at this fetch was a good body read and its title
    and abstract are the ones just fetched: a failed re-read then stores nothing, so that version (and its vote) stays."""
    rows = [r for r in store.facts_as_of(conn, fact["fetched_at_utc"], domain=_DOMAIN, entity_key=fact["entity_key"])
            if r["source_id"] == _FR]
    if len(rows) != 1:
        return False
    try:
        prior = json.loads(rows[0]["payload_json"])
    except (TypeError, ValueError):
        return False
    return (isinstance(prior, dict) and prior.get("body_read") is True and prior.get("title") == payload["title"]
            and prior.get("abstract") == payload["abstract"])


def _read_death_bodies(conn, facts: list[dict], http_get) -> tuple[list[dict], int]:
    """For each death proclamation among the Federal Register facts, one request for its `raw_text_url` (https on
    www.federalregister.gov only; the same client, user agent and size cap), then the body's mourning sentences are read
    together with the title and abstract. Returns the facts and the number of bodies that could not be read: such a
    document keeps only the dates its title and abstract give (`body_read: false`), never a guess. Only the body's sha256 is
    kept, never its text. Any other document costs no request."""
    out, errors = [], 0
    headers = {"User-Agent": _UA, "Accept": "text/html, text/plain"}
    for fact in facts:
        payload = json.loads(fact["payload_json"])
        if fact["source_id"] != _FR or "body_read" not in payload:
            out.append(fact)
            continue
        text = None
        digest = None
        if _body_url_ok(payload.get("raw_text_url")):
            try:
                body = http_get(payload["raw_text_url"], headers)
                if isinstance(body, bytes) and 0 < len(body) <= _MAX_BODY:
                    text, digest = body.decode("utf-8"), hashlib.sha256(body).hexdigest()
            except Exception:
                text = None
        head = _scan_text((payload["title"], payload["abstract"] or ""))
        tail = None if text is None else _scan_text((_plain_text(text),), mourning_only=True)
        if text is None:
            errors += 1
            if _kept_good_read(conn, fact, payload):
                continue  # lead: a failed re-read never replaces an earlier good read of the same document
            keep = [] if head is None or _CHANGE.search(payload["title"] + " " + (payload["abstract"] or "")) else head[0]
            payload.update({"closure_dates": sorted(set(keep)), "body_read": False, "body_sha256": None})  # lead: an
            # unreadable body costs only the body's vote; a vote the title or abstract casts by itself is kept
        else:
            unsure = head is None or tail is None or _CHANGE.search(payload["title"] + " " + (payload["abstract"] or ""))
            payload.update({"closure_dates": [] if unsure else sorted(set(head[0]) | set(tail[0])),
                            "body_read": True, "body_sha256": digest})
        out.append(_fact(_FR, fact["entity_key"], payload, effective_from=fact["effective_from_utc"],
                         effective_to=fact["effective_to_utc"], fetched=fact["fetched_at_utc"],
                         published=fact["published_at_utc"], proven=fact["published_proven"]))
    return out, errors


def refresh(conn, source_id, *, http_get=None, now, credentials=None, params=None) -> dict:
    """Fetch, parse and store every fact of one source. A bad argument raises. A failed request, a refused body or a
    fact the store refuses is counted as an error (a refused body stores nothing at all). A Federal Register death
    proclamation also costs one request for its body text; if that fails the failure is counted as an error, an earlier
    good read of the same title and abstract stays current, and otherwise only the title's and abstract's own dates are stored."""
    if not isinstance(source_id, str) or source_id not in SOURCES:
        raise K3Error(f"unknown source {str(source_id)[:60]!r}")
    _check_params(source_id, params, _now_text(now))
    body_errors = 0
    if source_id == _X:
        facts = fetch(source_id, http_get=http_get, now=now, params=params)
    else:
        try:
            facts = fetch(source_id, http_get=http_get, now=now, credentials=credentials, params=params)
        except K3Error as exc:
            return {"stored": 0, "unchanged": 0, "error": 1, "detail": str(exc)[:200]}
    if source_id == _FR:
        facts, body_errors = _read_death_bodies(conn, facts, http_get)
    if source_id == _H:
        facts = facts + _holiday_tombstones(conn, facts)
    counts = _store_facts(conn, facts)
    if body_errors:
        counts["error"] += body_errors
        counts.setdefault("detail", f"{body_errors} death proclamation body(ies) could not be read")
    return counts


# ---- the quorum

def _rule_finnhub_holiday(entity_key, payload, day) -> bool:
    return entity_key == f"XNYS:CLOSURE:{day}" and payload.get("closed") is True


def _rule_finnhub_status(entity_key, payload, day) -> bool:
    holiday = payload.get("holiday")
    return entity_key == f"XNYS:STATUS:{day}" and isinstance(holiday, str) and holiday != ""


def _rule_federal_register(entity_key, payload, day) -> bool:
    dates = payload.get("closure_dates")
    return entity_key.startswith("FR:") and isinstance(dates, list) and day in dates


# One rule per AUTHORITY source: does this source's fact say the date is closed? The exchange's own closure notice
# (a third authority) goes here when it is built: add its source to SOURCES with role AUTHORITY and one rule.
_CLOSURE_RULES = {_H: _rule_finnhub_holiday, _S: _rule_finnhub_status, _FR: _rule_federal_register}


def closure_votes(conn, trade_date, as_of_utc) -> dict:
    """What was known at `as_of_utc` about `trade_date`: the calendar's answer and the authority GROUPS that say closed
    (`authorities_closed`), with the source ids behind them (`sources_closed`). A group counts once however many of its
    sources say closed. A pure read: no network, and facts known after `as_of_utc` are not seen."""
    day = _parse_date(trade_date, "trade_date").isoformat()
    if not isinstance(trade_date, str):
        raise K3Error("trade_date: must be text written YYYY-MM-DD")
    rows = store.facts_as_of(conn, as_of_utc, domain=_DOMAIN)
    open_now = None
    closed_by = set()                                  # source ids
    groups = set()                                     # their authority groups
    for row in rows:
        source_id, key = row["source_id"], row["entity_key"]
        rule = _CLOSURE_RULES.get(source_id)
        is_schedule = source_id == _X and key == f"XNYS:SESSION:{day}"
        if rule is None and not is_schedule:
            continue
        if source_id in SOURCES and SOURCES[source_id]["role"] not in ("AUTHORITY", "SCHEDULE"):
            continue
        try:
            payload = json.loads(row["payload_json"])
        except ValueError:
            payload = None
        if not isinstance(payload, dict):
            raise K3Error(f"a stored fact has a payload that is not an object: {key[:60]}")
        if is_schedule:
            if type(payload.get("open")) is not bool:
                raise K3Error(f"a schedule fact has no open flag: {key}")
            open_now = payload["open"]
        elif SOURCES.get(source_id, {}).get("role") == "AUTHORITY" and rule(key, payload, day):
            group = SOURCES[source_id].get("authority_group")
            if not isinstance(group, str) or not group:
                raise K3Error(f"the authority source {source_id[:60]} has no authority_group")
            closed_by.add(source_id)
            groups.add(group)
    authorities = sorted(groups)
    return {
        "trade_date": day, "as_of_utc": as_of_utc, "schedule_known": open_now is not None,
        "calendar_open": open_now, "calendar_closed": None if open_now is None else not open_now,
        "authorities_closed": authorities, "sources_closed": sorted(closed_by),
        "ordinary_stop": open_now is True and len(authorities) >= 2,
        "suspected": open_now is True and len(authorities) == 1,
    }


# ---- the command line (the only place a real network client is built)

def _urllib_get(url: str, headers: dict) -> bytes:
    """A plain GET with the standard library. Used only by the command line's `refresh`."""
    import urllib.request
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT_S) as response:
        body = response.read(_MAX_BODY + 1)
    if len(body) > _MAX_BODY:
        raise K3Error("the answer is larger than the limit")
    return body


def _print(obj) -> None:
    print(json.dumps(obj, sort_keys=True))


def _main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="k3_calendar", description="K-3 calendars and closures")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def command(name):
        p = sub.add_parser(name)
        p.add_argument("--store", required=True)
        return p

    p = command("register")
    p.add_argument("--only", required=True)
    p.add_argument("--terms-read-at", required=True)
    p = command("schedule")
    p.add_argument("--from", dest="start", required=True)
    p.add_argument("--to", dest="end", required=True)
    p = command("refresh")
    p.add_argument("--source", required=True)
    p.add_argument("--from", dest="start")
    p.add_argument("--to", dest="end")
    p = command("votes")
    p.add_argument("--date", required=True)
    p.add_argument("--as-of")
    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc)
    conn = None
    try:
        conn = store.connect(args.store)
        if args.cmd == "register":
            register_all(conn, args.terms_read_at, only=[s.strip() for s in args.only.split(",") if s.strip()])
            _print({"registered": [s.strip() for s in args.only.split(",") if s.strip()]})
            return 0
        if args.cmd in ("schedule", "refresh"):
            source = _X if args.cmd == "schedule" else args.source
            params = {k: v for k, v in (("start", args.start), ("end", args.end)) if v is not None}
            result = refresh(conn, source, http_get=_urllib_get, now=now, params=params or None)
            _print(result)
            return 1 if result["error"] else 0
        result = closure_votes(conn, args.date, args.as_of or _fmt(now))
        _print(result)
        return 1 if (result["suspected"] or not result["schedule_known"]) else 0
    except (K3Error, store.StoreError, OSError) as exc:
        print(f"error: {exc}"[:300], file=sys.stderr)
        return 2
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    sys.exit(_main())
