"""K-4: venue rules, fees and API change logs, the second feed into the knowledge store.

What it does, in plain words:
- Reads each venue's own published change logs (Kalshi's RSS feed, Webull's change-log page) and rule pages (fee
  schedule, rulebook, pricing) and stores them as versioned facts in the knowledge store.
- Reads no legal prose at run time. A changed page or a new change-log entry opens a `PENDING_RULE_DIFF` item. A person
  then reads the change and turns the clause into a tested rule module, and closes the item with `close_rule_diff`.
- Fetches nothing by itself. Every fetch goes through an injected `http_get(url, headers) -> bytes`. Only the command
  line builds a real client, and the owner runs it after reading each source's terms.

Sources: two CHANGELOG sources (dated entries) and three DOCUMENT sources (one undated page each). Every source ships
with its terms address and licence tag UNSET (`None`): the owner fills them in after reading the terms, and
`register_all` refuses a source until they are filled. The three document pages also have no address yet.

Facts (all in the domain K4_VENUE):
- A change-log entry: key `<source_id>:<YYYY-MM-DD or TBD>:<16 hex of the sha256 of the lower-cased, normalized
  title>`; payload `title, date, summary (plain text, at most 2,000 characters), link, scheduled`.
  An entry dated before the fetch's New York date is PUBLISHED: known at the end of its New York day
  (`k3_calendar.date_only_known_at`). An entry dated on the fetch's own New York day cannot be proven yet (that day has
  not ended), so it is known when fetched, with `scheduled` false. An entry dated later, marked TBD, or with no date is
  `scheduled` true and known when fetched: a plan is not a publication.
- A document: key `<source_id>:DOC`; payload `text_sha256, length, excerpt` (the first 500 characters of the normalized
  text, never the page). Always known when fetched. A new `text_sha256` is a new version.
- A rule-diff flag: key `PENDING_RULE_DIFF:<entity key>`; payload `entity_key, old_fact_version_id (or null),
  new_fact_version_id, status`. `refresh` opens one (status OPEN) whenever it stores a new version of an entity, and also
  when the current version of an entity has no flag that points at it (a run that stopped between the two writes).
- The baseline read: the first read of a CHANGELOG source (one with no flag of its own yet) stores every entry but opens
  ONE flag only, `PENDING_RULE_DIFF:<source_id>:BASELINE`, with `entity_key` `<source_id>:BASELINE`, `old_fact_version_id`
  null, `new_fact_version_id` the highest version stored in that read, and `entries` the number of entries. The
  history of a change log is one thing to read, not one flag per old entry. After it, each new or changed entry opens its
  own flag. A read that stopped after storing entries but before the baseline flag is repaired by the next read: one
  baseline flag and no per-entry flags. A DOCUMENT source needs no baseline: its first version opens its own flag.
  `close_rule_diff` writes a later version with status CLOSED and the `rule_module`. `pending_rule_diffs` lists the open
  ones as of a time.

Normalization (`normalize_text`): tags, scripts, styles, `<head>` (meta tags, build ids, nonces) and comments are
dropped, entities are decoded and white space is collapsed. So a page that changes only in a script nonce hashes the
same, and a one-word change of a clause does not.

A body that fails its shape check is refused whole: nothing from it is stored, and the refusal counts as an error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from xml.etree import ElementTree
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

from valedia_trader.knowledge import store
from valedia_trader.knowledge.k3_calendar import date_only_known_at

_UA = "valedia-client/1.0"          # generic: no owner, project or email
_NY = "America/New_York"
_DOMAIN = "K4_VENUE"
_DIFF_PREFIX = "PENDING_RULE_DIFF:"
_SUMMARY_MAX = 2000
_EXCERPT_MAX = 500
_FALLBACK_TITLE_MAX = 200
_MAX_BODY = 5_000_000
_HTTP_TIMEOUT_S = 30                # the command line's client only; one named knob
_YEARS = (2000, 2100)               # an entry date outside this is a typo, so the body is refused
_KALSHI_RSS = "kalshi_changelog_rss"
_WEBULL_LOG = "webull_openapi_changelog"


class K4Error(ValueError):
    """The input, the setup or a source's answer is not what this module accepts."""


def _source(source_id, role, endpoint, notes):
    # terms_url and licence_tag stay None until the owner has read the source's terms
    return {"source_id": source_id, "role": role, "endpoint": endpoint, "terms_url": None, "licence_tag": None,
            "allow_external_llm": 0, "ua_policy": "GENERIC_CLIENT", "cost_usd_monthly": 0, "time_zone": _NY,
            "notes": notes}


SOURCES = {
    _KALSHI_RSS: _source(_KALSHI_RSS, "CHANGELOG", "https://docs.kalshi.com/changelog/rss.xml",
                         "RSS 2.0; each item is dated"),
    _WEBULL_LOG: _source(_WEBULL_LOG, "CHANGELOG", "https://developer.webull.com/apis/docs/changelog",
                         "HTML; dated entries"),
    "kalshi_fee_schedule": _source("kalshi_fee_schedule", "DOCUMENT", None, "page address set by the owner"),
    "kalshi_rulebook": _source("kalshi_rulebook", "DOCUMENT", None, "page address set by the owner"),
    "webull_pricing": _source("webull_pricing", "DOCUMENT", None, "page address set by the owner; the page is undated"),
}


# ---- small helpers

_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_UTC_RE = re.compile(r"([0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2})(\.[0-9]{1,6})?Z")
_MODULE_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.:/-]{0,199}")


def _fmt(moment: datetime) -> str:
    """A UTC time in the store's fixed form."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _utc_text(value, what: str) -> str:
    m = _UTC_RE.fullmatch(value) if isinstance(value, str) else None
    if not m:
        raise K4Error(f"{what}: must be UTC ISO-8601 ending in Z, got {str(value)[:40]!r}")
    try:
        datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        raise K4Error(f"{what}: not a real time: {value!r}") from None
    return m.group(1) + "." + (m.group(2) or ".")[1:].ljust(6, "0") + "Z"


def _now_text(now) -> str:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise K4Error("now: must be a timezone-aware datetime")
    return _fmt(now)


def _clock() -> datetime:
    return datetime.now(timezone.utc)


def _ny_date_of(fetched: str) -> date:
    moment = datetime.strptime(fetched, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    return moment.astimezone(ZoneInfo(_NY)).date()


def _day_start(day: date) -> str:
    return _fmt(datetime(day.year, day.month, day.day, tzinfo=ZoneInfo(_NY)))


def _payload(obj: dict) -> tuple[str, str]:
    """The normalized payload text (sorted keys, no spaces) and its sha256."""
    text = json.dumps(obj, sort_keys=True, separators=(",", ":"))
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()


def _fact(source_id, entity_key, payload, *, effective_from, fetched, published=None, proven=False) -> dict:
    text, digest = _payload(payload)
    return {"domain": _DOMAIN, "entity_key": entity_key, "source_id": source_id, "raw_sha256": digest,
            "payload_json": text, "effective_from_utc": effective_from, "effective_to_utc": None,
            "fetched_at_utc": fetched, "published_at_utc": published, "published_proven": proven}


def _decode(raw, what: str) -> str:
    """The body as text: non-empty bytes, not over the size limit, valid UTF-8."""
    if not isinstance(raw, bytes) or not raw:
        raise K4Error(f"{what}: the body must be non-empty bytes")
    if len(raw) > _MAX_BODY:
        raise K4Error(f"{what}: the body is larger than the limit")
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise K4Error(f"{what}: the body is not valid UTF-8") from None


def _encodable(text: str) -> bool:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


# ---- text normalization (pure)

_SKIP_BASE = frozenset({"script", "style", "noscript", "template", "head"})
_SKIP_PAGE = _SKIP_BASE | {"nav", "footer", "aside"}       # a change-log page's furniture


class _Collector(HTMLParser):
    """Walks markup and keeps a list of events: ("text", s), ("start", tag), ("end", tag). Everything inside a skipped
    element is dropped; so are comments, declarations and every attribute."""

    def __init__(self, skip):
        super().__init__(convert_charrefs=True)
        self._skip = skip
        self._depth = 0
        self.events: list[tuple[str, str]] = []

    def handle_starttag(self, tag, attrs):
        if tag == "body":
            self._depth = 0                  # an unclosed <head> must not swallow the page
        if tag in self._skip:
            self._depth += 1
        elif not self._depth:
            self.events.append(("start", tag))

    def handle_endtag(self, tag):
        if tag in self._skip:
            self._depth = max(0, self._depth - 1)
        elif not self._depth:
            self.events.append(("end", tag))

    def handle_data(self, data):
        if self._depth:
            return
        if self.events and self.events[-1][0] == "text":
            self.events[-1] = ("text", self.events[-1][1] + data)
        else:
            self.events.append(("text", data))


def _events(markup: str, skip) -> list[tuple[str, str]]:
    if not isinstance(markup, str) or not _encodable(markup):
        raise K4Error("markup: must be text")
    parser = _Collector(skip)
    try:
        parser.feed(markup)
        parser.close()
    except Exception:
        raise K4Error("markup: could not be read") from None
    return parser.events


def _collapse(text: str) -> str:
    return " ".join(text.replace("\x00", " ").split())


def normalize_text(markup) -> str:
    """The plain text of a page: tags, scripts, styles, `<head>` and comments dropped, entities decoded, white space
    collapsed. A block boundary is a space, so words of two paragraphs never join."""
    pieces = []
    for kind, value in _events(markup, _SKIP_BASE):
        pieces.append(value if kind == "text" else " ")
    return _collapse("".join(pieces))


# ---- source registration

def _unready(entry: dict):
    """Why a source cannot be registered yet (None when it can)."""
    if not isinstance(entry.get("terms_url"), str) or not entry["terms_url"].startswith("https://"):
        return "its terms address is not set (read the terms, then fill terms_url)"
    if not isinstance(entry.get("licence_tag"), str) or not entry["licence_tag"]:
        return "its licence tag is not set (read the terms, then fill licence_tag)"
    return None


def register_all(conn, terms_read_at_utc, *, only) -> None:
    """Register the named sources. The caller decides which: the owner reads the terms first. A source whose terms
    address or licence tag is still unset is refused, and then nothing at all is registered. Same values again: nothing
    happens. An unknown, empty or malformed list registers nothing."""
    if not isinstance(only, (list, tuple, set, frozenset)) or not only:
        raise K4Error("only: name the sources to register, as a non-empty list")
    names = list(only)
    for name in names:
        if not isinstance(name, str) or name not in SOURCES:
            raise K4Error(f"only: unknown source {str(name)[:60]!r}")
    for name in names:
        reason = _unready(SOURCES[name])
        if reason:
            raise K4Error(f"{name}: not registered, {reason}")
    for name in names:
        e = SOURCES[name]
        store.register_source(
            conn, source_id=e["source_id"], terms_url=e["terms_url"], licence_tag=e["licence_tag"],
            personal_use_ok=True, allow_external_llm=bool(e["allow_external_llm"]), ua_policy=e["ua_policy"],
            terms_read_at_utc=terms_read_at_utc, cost_usd_monthly=e["cost_usd_monthly"],
            notes=f"role={e['role']}; time_zone={e['time_zone']}" + (f"; {e['notes']}" if e.get("notes") else ""))


# ---- change-log entries (shared by both change-log parsers)

def _title_hash(title: str) -> str:
    return hashlib.sha256(_collapse(title).casefold().encode("utf-8")).hexdigest()[:16]


def _entry_facts(source_id: str, entries: list[dict], fetched: str) -> list[dict]:
    """Facts for parsed entries (`title`, `day` (a date or None), `summary`, `link`). A repeat of the same entry is one
    fact; two different entries with one key refuse the whole body."""
    if not entries:
        raise K4Error(f"{source_id}: the body holds no entries (a change log is never empty, so this is a broken page)")
    today = _ny_date_of(fetched)
    facts: dict[str, dict] = {}
    for e in entries:
        day = e["day"]
        iso = day.isoformat() if day is not None else None
        scheduled = day is None or day > today
        payload = {"title": e["title"], "date": iso, "summary": e["summary"][:_SUMMARY_MAX], "link": e["link"],
                   "scheduled": scheduled}
        key = f"{source_id}:{iso or 'TBD'}:{_title_hash(e['title'])}"
        if scheduled:
            fact = _fact(source_id, key, payload, effective_from=_day_start(day) if day else fetched, fetched=fetched)
        else:
            stamp = date_only_known_at(iso, _NY)
            proven = stamp <= fetched           # today's entry: the day has not ended, so it is known when fetched
            fact = _fact(source_id, key, payload, effective_from=_day_start(day), fetched=fetched,
                         published=stamp if proven else None, proven=proven)
        if key in facts and facts[key]["raw_sha256"] != fact["raw_sha256"]:
            raise K4Error(f"{source_id}: two different entries share one key {key[:80]!r}")
        facts[key] = fact
    return list(facts.values())


def _check_year(day: date, what: str) -> date:
    if not _YEARS[0] <= day.year <= _YEARS[1]:
        raise K4Error(f"{what}: the year {day.year} is outside {_YEARS[0]} to {_YEARS[1]}")
    return day


# ---- Kalshi RSS

def _rss_day(text):
    """The New York date of an item's `pubDate`, or None for a missing date or `TBD`. An RFC 822 time is read as the
    instant it is, then its New York date is taken (so the known time is never before the real one); a plain
    YYYY-MM-DD is that date. Anything else refuses the body."""
    s = (text or "").strip()
    if not s or s.upper() == "TBD":
        return None
    if _DATE_RE.fullmatch(s):
        try:
            day = date.fromisoformat(s)
        except ValueError:
            raise K4Error(f"pubDate: not a real date: {s[:40]!r}") from None
        return _check_year(day, "pubDate")
    moment = None
    try:
        moment = parsedate_to_datetime(s)
    except (TypeError, ValueError, IndexError):
        try:
            moment = datetime.fromisoformat(s)
        except ValueError:
            moment = None
    if moment is None:
        raise K4Error(f"pubDate: not a date this module reads: {s[:40]!r}")
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return _check_year(moment.astimezone(ZoneInfo(_NY)).date(), "pubDate")


def _child_text(item, name: str):
    node = item.find(name)
    return None if node is None else "".join(node.itertext())


def parse_kalshi_rss(raw, *, fetched_at_utc) -> list[dict]:
    """Kalshi's change-log RSS 2.0 feed: `<rss version="2.0"><channel><item>` with `title`, `pubDate`, `description`,
    `link`. One fact per item. A missing or TBD `pubDate` makes a scheduled entry. A feed with a DOCTYPE or entity
    declaration, a wrong root, no items, an item with no title, a bad `pubDate` or a link that is not http(s) is
    refused whole."""
    fetched = _utc_text(fetched_at_utc, "fetched_at_utc")
    text = _decode(raw, "kalshi rss")
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)", text, re.I):
        raise K4Error("kalshi rss: a DOCTYPE or entity declaration is not accepted")
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError:
        raise K4Error("kalshi rss: the body is not valid XML") from None
    if root.tag != "rss" or root.get("version") != "2.0":
        raise K4Error("kalshi rss: expected an RSS 2.0 document")
    channels = root.findall("channel")
    if len(channels) != 1:
        raise K4Error("kalshi rss: expected exactly one channel")
    entries = []
    for item in channels[0].findall("item"):
        title = normalize_text(_child_text(item, "title") or "")
        if not title:
            raise K4Error("kalshi rss: an item has no title")
        link = _collapse(_child_text(item, "link") or "") or None
        if link is not None and not re.fullmatch(r"https?://\S+", link):
            raise K4Error("kalshi rss: an item link is not an http(s) address")
        entries.append({"title": title, "day": _rss_day(_child_text(item, "pubDate")),
                        "summary": normalize_text(_child_text(item, "description") or ""), "link": link})
    return _entry_facts(_KALSHI_RSS, entries, fetched)


# ---- Webull change-log page

_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10,
           "nov": 11, "dec": 12}
_MONTH_NAMES = ("January|February|March|April|May|June|July|August|September|October|November|December|"
                "Sept|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec")
_ISO_IN_TEXT = re.compile(r"\b(?P<y>[0-9]{4})-(?P<m>[0-9]{2})-(?P<d>[0-9]{2})\b")
_MONTH_FIRST = re.compile(rf"\b(?P<mon>{_MONTH_NAMES})\.?\s+(?P<d>[0-9]{{1,2}})(?:st|nd|rd|th)?,?\s+(?P<y>[0-9]{{4}})\b")
_DAY_FIRST = re.compile(rf"\b(?P<d>[0-9]{{1,2}})(?:st|nd|rd|th)?\s+(?P<mon>{_MONTH_NAMES})\.?,?\s+(?P<y>[0-9]{{4}})\b")
_TBD = re.compile(r"\bTBD\b", re.I)
_HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_TITLE_EDGE = " \t-–—:|()[]·•,."


def _heading_entry(text: str):
    """(day or None, title rest) when a heading opens a change-log entry (it holds a date, or the word TBD); None for any
    other heading. A date-shaped heading with an impossible date refuses the body."""
    best = None
    for rx in (_ISO_IN_TEXT, _MONTH_FIRST, _DAY_FIRST):
        m = rx.search(text)
        if m and (best is None or m.start() < best[0].start()):
            best = (m, rx)
    if best is not None:
        m, rx = best
        month = int(m.group("m")) if rx is _ISO_IN_TEXT else _MONTHS[m.group("mon")[:3].lower()]
        try:
            day = date(int(m.group("y")), month, int(m.group("d")))
        except ValueError:
            raise K4Error(f"a heading holds a date that does not exist: {m.group(0)[:30]!r}") from None
        _check_year(day, "heading date")
        rest = text[:m.start()] + " " + text[m.end():]
        return day, _collapse(rest).strip(_TITLE_EDGE).strip()
    if _TBD.search(text):
        return None, _collapse(_TBD.sub(" ", text)).strip(_TITLE_EDGE).strip()
    return None


def parse_webull_changelog(raw, *, fetched_at_utc) -> list[dict]:
    """Webull's change-log page (HTML). An entry starts at a heading (h1 to h6) that holds a date (YYYY-MM-DD,
    "Nov 10, 2026" or "10 November 2026") or the word TBD, and runs to the next such heading. Its title is the heading
    without the date, or, if nothing is left, the first piece of text under it. `nav`, `footer` and `aside` are ignored.
    A page with no entry, an entry with no title, an impossible date or a heading that never closes is refused whole."""
    fetched = _utc_text(fetched_at_utc, "fetched_at_utc")
    text = _decode(raw, "webull changelog")
    entries, cur, heading = [], None, None
    for kind, value in _events(text, _SKIP_PAGE):
        if heading is not None:
            if kind == "text":
                heading[1].append(value)
            elif kind == "end" and value == heading[0]:
                words = _collapse(" ".join(heading[1]))
                heading = None
                found = _heading_entry(words)
                if found is not None:
                    cur = {"day": found[0], "title": found[1], "nodes": []}
                    entries.append(cur)
                elif cur is not None and words:
                    cur["nodes"].append(words)
            continue
        if kind == "start" and value in _HEADINGS:
            heading = (value, [])
        elif kind == "text" and cur is not None and _collapse(value):
            cur["nodes"].append(_collapse(value))
    if heading is not None:
        raise K4Error("webull changelog: a heading never closes")
    out = []
    for e in entries:
        title = e["title"] or (e["nodes"][0][:_FALLBACK_TITLE_MAX] if e["nodes"] else "")
        if not title:
            raise K4Error("webull changelog: an entry has no title")
        out.append({"title": title, "day": e["day"], "summary": " ".join(e["nodes"]),
                    "link": SOURCES[_WEBULL_LOG]["endpoint"]})
    return _entry_facts(_WEBULL_LOG, out, fetched)


# ---- documents

def parse_document(raw, *, source_id, fetched_at_utc) -> list[dict]:
    """A rule or fee page (HTML) of a DOCUMENT source: one fact holding the hash and length of its normalized text and
    the first 500 characters of it, never the page. A body with no text left after normalization (an empty page, or one
    whose words are all in scripts) is refused."""
    fetched = _utc_text(fetched_at_utc, "fetched_at_utc")
    if not isinstance(source_id, str) or SOURCES.get(source_id, {}).get("role") != "DOCUMENT":
        raise K4Error(f"{str(source_id)[:60]!r} is not a document source")
    text = normalize_text(_decode(raw, source_id))
    if not text:
        raise K4Error(f"{source_id}: the page has no text after normalization")
    payload = {"text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "length": len(text),
               "excerpt": text[:_EXCERPT_MAX]}
    return [_fact(source_id, f"{source_id}:DOC", payload, effective_from=fetched, fetched=fetched)]


# ---- fetch and refresh

def fetch(source_id, *, http_get=None, now) -> list[dict]:
    """The entity facts of one source (never the rule-diff flags), fetched through the injected
    `http_get(url, headers) -> bytes`. A source with no page address yet is refused."""
    if not isinstance(source_id, str) or source_id not in SOURCES:
        raise K4Error(f"unknown source {str(source_id)[:60]!r}")
    fetched = _now_text(now)
    url = SOURCES[source_id]["endpoint"]
    if not isinstance(url, str) or not url.startswith("https://"):
        raise K4Error(f"{source_id}: its page address is not set")
    if not callable(http_get):
        raise K4Error("http_get: a fetching function is required (there is no default client)")
    headers = {"User-Agent": _UA, "Accept": "application/rss+xml, text/html, */*" if source_id == _KALSHI_RSS
               else "text/html, */*"}
    try:
        body = http_get(url, headers)
    except Exception as exc:
        raise K4Error(f"{source_id}: the request failed ({type(exc).__name__})") from None
    if not isinstance(body, bytes):
        raise K4Error(f"{source_id}: http_get must return bytes")
    if source_id == _KALSHI_RSS:
        return parse_kalshi_rss(body, fetched_at_utc=fetched)
    if source_id == _WEBULL_LOG:
        return parse_webull_changelog(body, fetched_at_utc=fetched)
    return parse_document(body, source_id=source_id, fetched_at_utc=fetched)


def _diff_payload(row: dict) -> dict:
    """A stored flag's payload, checked."""
    key = row["entity_key"]
    try:
        p = json.loads(row["payload_json"])
    except ValueError:
        p = None
    ok = (isinstance(p, dict) and set(p) >= {"entity_key", "old_fact_version_id", "new_fact_version_id", "status"}
          and (p.get("entries") is None or (type(p["entries"]) is int and p["entries"] > 0))
          and p["entity_key"] == key[len(_DIFF_PREFIX):] and p["status"] in ("OPEN", "CLOSED")
          and type(p["new_fact_version_id"]) is int
          and (p["old_fact_version_id"] is None or type(p["old_fact_version_id"]) is int)
          and (p["status"] == "OPEN" or isinstance(p.get("rule_module"), str)))
    if not ok:
        raise K4Error(f"a stored rule-diff flag is not in the expected shape: {key[:80]}")
    return p


def _store_facts(conn, source_id: str, facts: list[dict]) -> dict:
    """Store parsed facts, and open a rule diff for each new version. Counts: stored (a new version), unchanged (the
    same read again), error, diffs_opened."""
    counts = {"stored": 0, "unchanged": 0, "error": 0, "diffs_opened": 0}
    if not facts:
        return counts
    fetched = facts[0]["fetched_at_utc"]
    before, flags = {}, {}
    for row in store.facts_as_of(conn, fetched, domain=_DOMAIN):
        if row["source_id"] != source_id:
            continue
        if row["entity_key"].startswith(_DIFF_PREFIX):
            flags[row["entity_key"][len(_DIFF_PREFIX):]] = _diff_payload(row)
        else:
            before[row["entity_key"]] = row["fact_version_id"]
    detail = None
    baseline = SOURCES[source_id]["role"] == "CHANGELOG" and not flags     # a change log with no flag of its own yet
    versions = []
    covered = (flags.get(f"{source_id}:BASELINE") or {}).get("new_fact_version_id", 0)

    def fail(exc):
        nonlocal detail
        counts["error"] += 1
        detail = detail or str(exc)[:200]

    for f in facts:
        key = f["entity_key"]
        try:
            version = store.add_fact(conn, **f)
        except store.StoreError as exc:
            fail(exc)
            continue
        changed = before.get(key) != version
        counts["stored" if changed else "unchanged"] += 1
        versions.append(version)
        if baseline:
            continue                                  # one flag for the whole first read, below
        flag = flags.get(key)
        if flag is not None and flag["new_fact_version_id"] == version:
            continue                                  # a flag already points at this version
        if flag is None and version <= covered:
            continue                                  # the baseline flag covers every entry of the first read
        old = flag["new_fact_version_id"] if flag is not None else (before.get(key) if changed else None)
        body = {"entity_key": key, "old_fact_version_id": old, "new_fact_version_id": version, "status": "OPEN"}
        try:
            store.add_fact(conn, **_fact(source_id, _DIFF_PREFIX + key, body, effective_from=fetched, fetched=fetched))
        except store.StoreError as exc:
            fail(exc)
            continue
        counts["diffs_opened"] += 1
    if baseline and versions and not counts["error"]:     # after a failed fact, the next read repairs it
        body = {"entity_key": f"{source_id}:BASELINE", "old_fact_version_id": None,
                "new_fact_version_id": max(versions), "status": "OPEN", "entries": len(facts)}
        try:
            store.add_fact(conn, **_fact(source_id, f"{_DIFF_PREFIX}{source_id}:BASELINE", body,
                                         effective_from=fetched, fetched=fetched))
            counts["diffs_opened"] += 1
        except store.StoreError as exc:
            fail(exc)
    if detail:
        counts["detail"] = detail
    return counts


def refresh(conn, source_id, *, http_get=None, now) -> dict:
    """Fetch, parse and store every fact of one source, and open a rule diff for each new version. A bad argument
    raises. A failed request, a refused body or a fact the store refuses is counted as an error (a refused body stores
    nothing at all)."""
    if not isinstance(source_id, str) or source_id not in SOURCES:
        raise K4Error(f"unknown source {str(source_id)[:60]!r}")
    _now_text(now)
    try:
        facts = fetch(source_id, http_get=http_get, now=now)
        return _store_facts(conn, source_id, facts)
    except K4Error as exc:
        return {"stored": 0, "unchanged": 0, "error": 1, "diffs_opened": 0, "detail": str(exc)[:200]}


# ---- the rule-diff list

def pending_rule_diffs(conn, as_of_utc) -> list[dict]:
    """The rule diffs that were open at `as_of_utc`, each as `entity_key, source_id, old_fact_version_id,
    new_fact_version_id, opened_at_utc`. A pure read through the store's one reader: a flag known after `as_of_utc` is
    not seen, and a close made after it is not seen either."""
    out = []
    for row in store.facts_as_of(conn, as_of_utc, domain=_DOMAIN):
        if not row["entity_key"].startswith(_DIFF_PREFIX):
            continue
        p = _diff_payload(row)
        if p["status"] == "OPEN":
            out.append({"entity_key": p["entity_key"], "source_id": row["source_id"],
                        "old_fact_version_id": p["old_fact_version_id"],
                        "new_fact_version_id": p["new_fact_version_id"], "opened_at_utc": row["known_at_utc"]})
    return out


def close_rule_diff(conn, entity_key, rule_module, now) -> int:
    """Close the open rule diff of `entity_key`: a later version with status CLOSED and the rule module that now holds
    the clause. Returns the new version's id. Refused when no diff is open for the key at `now`."""
    if not isinstance(entity_key, str) or not entity_key or entity_key.startswith(_DIFF_PREFIX):
        raise K4Error("entity_key: give the entity's own key, without the PENDING_RULE_DIFF: prefix")
    if not isinstance(rule_module, str) or not _MODULE_RE.fullmatch(rule_module):
        raise K4Error("rule_module: must be 1 to 200 characters of letters, digits and _ . : / -")
    stamp = _now_text(now)
    rows = store.facts_as_of(conn, stamp, domain=_DOMAIN, entity_key=_DIFF_PREFIX + entity_key)
    if len(rows) != 1:
        raise K4Error(f"no rule diff for {entity_key[:80]!r}")
    row = rows[0]
    p = _diff_payload(row)
    if p["status"] != "OPEN":
        raise K4Error(f"the rule diff for {entity_key[:80]!r} is already closed")
    body = dict(p, status="CLOSED", rule_module=rule_module)
    return store.add_fact(conn, **_fact(row["source_id"], row["entity_key"], body, effective_from=stamp, fetched=stamp))


# ---- the command line (the only place a real network client is built)

def _urllib_get(url: str, headers: dict) -> bytes:
    """A plain GET with the standard library. Used only by the command line's `refresh`."""
    import urllib.request
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT_S) as response:
        body = response.read(_MAX_BODY + 1)
    if len(body) > _MAX_BODY:
        raise K4Error("the answer is larger than the limit")
    return body


def _print(obj) -> None:
    print(json.dumps(obj, sort_keys=True))


def _main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="k4_venue", description="K-4 venue rules, fees and API change logs")
    sub = parser.add_subparsers(dest="cmd", required=True)

    def command(name):
        p = sub.add_parser(name)
        p.add_argument("--store", required=True)
        return p

    p = command("register")
    p.add_argument("--only", required=True)
    p.add_argument("--terms-read-at", required=True)
    p = command("refresh")
    p.add_argument("--source", required=True)
    p = command("pending")
    p.add_argument("--as-of")
    p = command("close")
    p.add_argument("--entity", required=True)
    p.add_argument("--rule-module", required=True)
    args = parser.parse_args(argv)
    now = _clock()
    conn = None
    try:
        conn = store.connect(args.store)
        if args.cmd == "register":
            names = [s.strip() for s in args.only.split(",") if s.strip()]
            register_all(conn, args.terms_read_at, only=names)
            _print({"registered": names})
            return 0
        if args.cmd == "refresh":
            result = refresh(conn, args.source, http_get=_urllib_get, now=now)
            result["open_diffs"] = len(pending_rule_diffs(conn, _fmt(now)))
            _print(result)
            return 1 if result["error"] or result["open_diffs"] else 0
        if args.cmd == "close":
            version = close_rule_diff(conn, args.entity, args.rule_module, now)
            still = pending_rule_diffs(conn, _fmt(now))
            _print({"closed": args.entity, "fact_version_id": version, "open_diffs": len(still)})
            return 1 if still else 0
        as_of = args.as_of or _fmt(now)
        open_now = pending_rule_diffs(conn, _utc_text(as_of, "--as-of"))
        _print({"as_of_utc": as_of, "open": open_now})
        return 1 if open_now else 0
    except (K4Error, store.StoreError, OSError) as exc:
        print(f"error: {exc}"[:300], file=sys.stderr)
        return 2
    finally:
        if conn is not None:
            conn.close()


if __name__ == "__main__":
    sys.exit(_main())
