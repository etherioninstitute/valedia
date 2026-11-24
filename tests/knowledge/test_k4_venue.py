"""Tests for the K-4 venue rules, fees and change-log feed. No network: every fetch goes through a fake `http_get`, and a
socket guard makes any real connection fail the test. Every guard is tested both ways (it holds, and it lets the good
case through)."""
import hashlib
import inspect
import json
import socket
from datetime import datetime, timedelta, timezone

import pytest

from valedia_trader.knowledge import k4_venue as k4
from valedia_trader.knowledge.store import StoreError, add_fact, connect, facts_as_of

T0 = "2026-01-01T00:00:00Z"
NOW = datetime(2026, 11, 23, 18, 0, 0, tzinfo=timezone.utc)       # 13:00 in New York, Monday 23 November
NOW_S = "2026-11-23T18:00:00.000000Z"
FAR = "2099-01-01T00:00:00Z"
KR = "kalshi_changelog_rss"
WB = "webull_openapi_changelog"
FEE = "kalshi_fee_schedule"
RULE = "kalshi_rulebook"
PRICE = "webull_pricing"
DOCS = (FEE, RULE, PRICE)
KR_URL = "https://docs.kalshi.com/changelog/rss.xml"
WB_URL = "https://developer.webull.com/apis/docs/changelog"
DOC_URL = {FEE: "https://example.invalid/fees", RULE: "https://example.invalid/rulebook",
           PRICE: "https://example.invalid/pricing"}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("a test tried to open a network connection")
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "k.db")
    yield c
    c.close()


@pytest.fixture
def ready(monkeypatch):
    """Fill in what the owner fills in after reading each source's terms (terms address, licence tag, page address)."""
    for sid, entry in k4.SOURCES.items():
        filled = dict(entry, terms_url=f"https://example.invalid/terms/{sid}", licence_tag="TEST-LICENCE")
        if sid in DOC_URL:
            filled["endpoint"] = DOC_URL[sid]
        monkeypatch.setitem(k4.SOURCES, sid, filled)


@pytest.fixture
def reg(conn, ready):
    k4.register_all(conn, T0, only=list(k4.SOURCES))
    return conn


def at(day, hh=18, mm=0):
    return datetime(2026, 11, day, hh, mm, tzinfo=timezone.utc)


def stamp(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000000Z")


class Fake:
    """An injected http_get that records every request and answers from a map of url to bytes (or an exception)."""

    def __init__(self, answers):
        self.answers, self.calls = dict(answers), []

    def __call__(self, url, headers):
        self.calls.append((url, dict(headers)))
        body = self.answers[url]
        if isinstance(body, Exception):
            raise body
        return body


def n_rows(conn):
    return conn.execute("SELECT count(*) FROM knowledge_facts").fetchone()[0]


def current(conn, as_of=FAR):
    return {r["entity_key"]: r for r in facts_as_of(conn, as_of, domain="K4_VENUE")}


def payload(row):
    return json.loads(row["payload_json"])


def h16(title):
    return hashlib.sha256(title.casefold().encode()).hexdigest()[:16]


# ---- fixtures: hand-written, in the shapes the ticket describes

def rss_item(title, pub="Mon, 09 Nov 2026 15:00:00 GMT", desc="Plain description.", link="https://docs.kalshi.com/c/x"):
    parts = [f"<title>{title}</title>"]
    if link is not None:
        parts.append(f"<link>{link}</link>")
    if pub is not None:
        parts.append(f"<pubDate>{pub}</pubDate>")
    if desc is not None:
        parts.append(f"<description>{desc}</description>")
    return "<item>" + "".join(parts) + "</item>"


def rss(*items, version="2.0", extra=""):
    return (f'<?xml version="1.0" encoding="UTF-8"?><rss version="{version}"><channel><title>Kalshi API changelog</title>'
            f"<link>https://docs.kalshi.com/changelog</link>{extra}" + "".join(items) + "</channel></rss>").encode()


A_TITLE, B_TITLE, C_TITLE, D_TITLE, E_TITLE, F_TITLE = (
    "New order group endpoints", "Fee change for index markets", "Settlement API v3 planned", "Maintenance window TBD",
    "Same day note", "Undated note")
FEED = rss(
    rss_item(A_TITLE, "Mon, 09 Nov 2026 15:00:00 GMT", "&lt;p&gt;Adds &lt;b&gt;order groups&lt;/b&gt; &amp;amp; limits.&lt;/p&gt;"),
    rss_item(B_TITLE, "Fri, 20 Nov 2026 22:30:00 GMT", "Index fees change."),
    rss_item(C_TITLE, "Wed, 02 Dec 2026 15:00:00 GMT", "A plan for next month."),
    rss_item(D_TITLE, "TBD", "No date yet."),
    rss_item(E_TITLE, "Mon, 23 Nov 2026 14:00:00 GMT", "Published today."),
    rss_item(F_TITLE, None, "No date at all."))


def key(source, day, title, n=1):
    return f"{source}:{day}:{h16(title)}:{n}"


WEBULL = """<!doctype html><html><head><title>Changelog</title><script>var build="b-1";</script></head><body>
<nav><h2>2026-01-01 Menu</h2><a href="/">Home</a></nav>
<main><h1>Webull OpenAPI changelog</h1>
<h2>2026-11-18 Order status stream</h2><p>A new stream of order status events.</p><ul><li>Added field <code>status</code>.</li></ul>
<h2>Nov 10, 2026</h2><p>Quote limit raised</p><p>From 10 to 20 requests a second.</p>
<h3>Notes</h3><p>See the guide.</p>
<h2>3 November 2026 - Token expiry</h2><p>Tokens now expire after 24 hours.</p>
<h2>TBD - Options chain endpoint</h2><p>Planned for a later release.</p>
<h2>2026-12-15 Futures quotes</h2><p>Scheduled.</p>
</main><footer><h2>2027-01-01 Footer</h2><p>(c) Example</p></footer></body></html>"""


def page(body_html, nonce="abc123"):
    return (f'<!doctype html><html><head><meta charset="utf-8"><meta name="build-id" content="b-{nonce}">'
            f'<meta name="generated" content="2026-11-23T17:59:59Z"><title>Fee schedule</title>'
            f".x{{color:red}}<style>.y{{color:blue}}</style><script nonce=\"{nonce}\">window.__BUILD__=\"{nonce}\";</script></head>"
            f"<body><!-- built {nonce} --><h1>Fee schedule</h1>{body_html}"
            f'<script nonce="{nonce}">track("{nonce}")</script></body></html>').encode()


CLAUSE = "<p>The taker fee is 7% of expected earnings per contract.</p><p>Makers pay no fee.</p>"
CLAUSE_CHANGED = "<p>The taker fee is 7% of expected earnings per contract.</p><p>Makers pay a fee.</p>"


def refresh_doc(conn, source, body, now=NOW):
    return k4.refresh(conn, source, http_get=Fake({DOC_URL[source]: body}), now=now)


# ---- SOURCES and registration

def test_sources_have_the_listed_fields_and_roles():
    assert set(k4.SOURCES) == {KR, WB, FEE, RULE, PRICE}
    for sid, e in k4.SOURCES.items():
        assert e["source_id"] == sid
        assert e["ua_policy"] == "GENERIC_CLIENT" and e["cost_usd_monthly"] == 0 and e["time_zone"] == "America/New_York"
        assert e["allow_external_llm"] == 0 and e["notes"]
        assert e["role"] == ("CHANGELOG" if sid in (KR, WB) else "DOCUMENT")
    assert k4.SOURCES[KR]["endpoint"] == KR_URL and k4.SOURCES[WB]["endpoint"] == WB_URL


def test_the_terms_and_the_document_addresses_are_left_for_the_owner():
    for sid, e in k4.SOURCES.items():
        assert e["terms_url"] is None and e["licence_tag"] is None, sid
    for sid in DOCS:
        assert k4.SOURCES[sid]["endpoint"] is None, sid


def test_register_all_refuses_a_source_whose_terms_are_not_filled_in_and_registers_nothing(conn, ready, monkeypatch):
    monkeypatch.setitem(k4.SOURCES, WB, dict(k4.SOURCES[WB], terms_url=None))
    with pytest.raises(k4.K4Error, match="terms address"):
        k4.register_all(conn, T0, only=[KR, WB])
    assert n_rows(conn) == 0 and conn.execute("SELECT count(*) FROM source_registry").fetchone()[0] == 0
    monkeypatch.setitem(k4.SOURCES, WB, dict(k4.SOURCES[WB], terms_url="https://example.invalid/t", licence_tag=None))
    with pytest.raises(k4.K4Error, match="licence tag"):
        k4.register_all(conn, T0, only=[WB])
    monkeypatch.setitem(k4.SOURCES, WB, dict(k4.SOURCES[WB], terms_url="http://example.invalid/t", licence_tag="X"))
    with pytest.raises(k4.K4Error, match="terms address"):
        k4.register_all(conn, T0, only=[WB])


def test_register_all_with_the_shipped_sources_refuses_every_one(conn):
    for sid in k4.SOURCES:
        with pytest.raises(k4.K4Error):
            k4.register_all(conn, T0, only=[sid])


def test_register_all_registers_only_the_named_sources(conn, ready):
    k4.register_all(conn, T0, only=[KR, FEE])
    rows = conn.execute("SELECT source_id, ua_policy, allow_external_llm, notes FROM source_registry ORDER BY 1").fetchall()
    assert [r[0] for r in rows] == [KR, FEE]
    assert all(r[1] == "GENERIC_CLIENT" and r[2] == 0 for r in rows)
    assert "role=CHANGELOG" in dict((r[0], r[3]) for r in rows)[KR]
    k4.register_all(conn, T0, only=[KR])                     # same values again: nothing happens
    assert conn.execute("SELECT count(*) FROM source_registry").fetchone()[0] == 2


@pytest.mark.parametrize("only", [[], None, "kalshi_rulebook", ["nope"], [KR, "nope"], [5], {}])
def test_register_all_refuses_an_unknown_empty_or_malformed_list(conn, ready, only):
    with pytest.raises(k4.K4Error):
        k4.register_all(conn, T0, only=only)
    assert conn.execute("SELECT count(*) FROM source_registry").fetchone()[0] == 0


# ---- normalization

def test_normalization_drops_tags_scripts_styles_head_and_comments_and_decodes_entities():
    html = ("<html><head><title>T</title><meta name=x content='n1'><script>var a='SCRIPT-TEXT'</script></head>"
            "<body><!-- COMMENT --><style>.a{}</style><h1>Fees &amp; limits</h1><p>Pay&nbsp;5&#37;\n\n  now</p>"
            "<script>more('SCRIPT-TEXT')</script><noscript>NOSCRIPT-TEXT</noscript></body></html>")
    assert k4.normalize_text(html) == "Fees & limits Pay 5% now"


def test_normalization_keeps_words_of_two_blocks_apart():
    assert k4.normalize_text("<p>one</p><p>two</p>") == "one two"
    assert k4.normalize_text("<li>a</li><li>b</li>") == "a b"


UNCLOSED = {
    "script": "<p>before</p><script>var a = 1;<p>after the script</p>",
    "style": "<p>before</p><style>.a{}<p>after the style</p>",
    "head": "<html><head><title>x</title><body><p>after the head</p></body></html>",
    "noscript": "<p>before</p><noscript><p>after</p>",
    "template": "<template><p>after</p>",
}
CLOSED = {
    "script": "<p>before</p><script>var a = 1;</script><p>after the script</p>",
    "style": "<p>before</p><style>.a{}</style><p>after the style</p>",
    "head": "<html><head><title>x</title></head><body><p>after the head</p></body></html>",
    "noscript": "<p>before</p><noscript>x</noscript><p>after</p>",
    "template": "<template><p>x</p></template><p>after</p>",
}


@pytest.mark.parametrize("name", sorted(UNCLOSED))
def test_an_unclosed_script_style_or_head_refuses_the_body_whole(name):
    with pytest.raises(k4.K4Error, match="never closes"):
        k4.normalize_text(UNCLOSED[name])
    with pytest.raises(k4.K4Error, match="never closes"):
        k4.parse_document(UNCLOSED[name].encode(), source_id=FEE, fetched_at_utc=NOW_S)
    with pytest.raises(k4.K4Error, match="never closes"):
        k4.parse_webull_changelog(("<h2>2026-11-18 Title</h2>" + UNCLOSED[name]).encode(), fetched_at_utc=NOW_S)


@pytest.mark.parametrize("name", sorted(CLOSED))
def test_a_closed_script_style_or_head_keeps_the_text_after_it(name):
    text = k4.normalize_text(CLOSED[name])
    assert text.endswith("after") or text.endswith(f"after the {name}") or "after" in text
    assert "var a" not in text and ".a{}" not in text


def test_an_unclosed_element_in_a_stored_refresh_stores_nothing(reg):
    got = refresh_doc(reg, FEE, UNCLOSED["script"].encode())
    assert got["error"] == 1 and got["stored"] == 0 and n_rows(reg) == 0


def test_two_pages_that_differ_only_in_a_script_nonce_normalize_and_hash_the_same():
    a, b = page(CLAUSE, "nonce-AAAA"), page(CLAUSE, "nonce-ZZZZ")
    assert a != b
    assert k4.normalize_text(a.decode()) == k4.normalize_text(b.decode())
    fa = k4.parse_document(a, source_id=FEE, fetched_at_utc=NOW_S)
    fb = k4.parse_document(b, source_id=FEE, fetched_at_utc=NOW_S)
    assert fa[0]["raw_sha256"] == fb[0]["raw_sha256"]
    assert json.loads(fa[0]["payload_json"])["text_sha256"] == json.loads(fb[0]["payload_json"])["text_sha256"]


def test_a_one_word_clause_change_hashes_differently():
    a = k4.parse_document(page(CLAUSE), source_id=FEE, fetched_at_utc=NOW_S)[0]
    b = k4.parse_document(page(CLAUSE_CHANGED), source_id=FEE, fetched_at_utc=NOW_S)[0]
    assert a["raw_sha256"] != b["raw_sha256"]
    assert json.loads(a["payload_json"])["text_sha256"] != json.loads(b["payload_json"])["text_sha256"]


def test_visible_text_is_kept_and_only_the_volatile_parts_go():
    assert k4.normalize_text("<p>Build id 7</p>") == "Build id 7"
    assert k4.normalize_text("<div>visible</div><script>hidden</script>") == "visible"


@pytest.mark.parametrize("bad", [None, b"<p>x</p>", 5, ["<p>x</p>"], "lone \ud800 surrogate"])
def test_normalization_refuses_input_that_is_not_text(bad):
    with pytest.raises(k4.K4Error):
        k4.normalize_text(bad)


def test_normalization_removes_a_nul_byte():
    assert "\x00" not in k4.normalize_text("<p>a\x00b</p>")


# ---- the document parser

def test_a_document_is_one_fact_with_a_hash_a_length_and_a_short_excerpt():
    long_text = "Clause. " * 400
    facts = k4.parse_document(page(f"<p>{long_text}</p>"), source_id=RULE, fetched_at_utc=NOW_S)
    assert len(facts) == 1
    f = facts[0]
    p = json.loads(f["payload_json"])
    text = k4.normalize_text(page(f"<p>{long_text}</p>").decode())
    assert f["entity_key"] == f"{RULE}:DOC" and f["domain"] == "K4_VENUE" and f["source_id"] == RULE
    assert set(p) == {"text_sha256", "length", "excerpt"}
    assert p["text_sha256"] == hashlib.sha256(text.encode()).hexdigest() and p["length"] == len(text) > 500
    assert p["excerpt"] == text[:500] and len(p["excerpt"]) == 500
    assert f["published_proven"] is False and f["published_at_utc"] is None and f["fetched_at_utc"] == NOW_S


def test_a_short_page_is_its_whole_text_in_the_excerpt():
    p = json.loads(k4.parse_document(b"<p>Short rule.</p>", source_id=PRICE, fetched_at_utc=NOW_S)[0]["payload_json"])
    assert p["excerpt"] == "Short rule." and p["length"] == 11


@pytest.mark.parametrize("raw", [b"", b"   \n ", b"<html><head><title>x</title></head><body></body></html>",
                                 b"<html><body><script>all the words are here</script></body></html>",
                                 b"<script>x", "text, not bytes", None, b"\xff\xfe\x00bad", b"<p>" + b"x" * 5_000_001])
def test_a_document_with_no_usable_text_is_refused_whole(raw):
    with pytest.raises(k4.K4Error):
        k4.parse_document(raw, source_id=FEE, fetched_at_utc=NOW_S)


@pytest.mark.parametrize("sid", [KR, WB, "nope", None, 5])
def test_the_document_parser_only_serves_document_sources(sid):
    with pytest.raises(k4.K4Error):
        k4.parse_document(b"<p>x</p>", source_id=sid, fetched_at_utc=NOW_S)


@pytest.mark.parametrize("bad", ["2026-11-23", "2026-11-23T18:00:00", None, "2026-13-23T18:00:00Z", 5])
def test_every_parser_refuses_a_bad_fetch_time(bad):
    with pytest.raises(k4.K4Error):
        k4.parse_document(b"<p>x</p>", source_id=FEE, fetched_at_utc=bad)
    with pytest.raises(k4.K4Error):
        k4.parse_kalshi_rss(FEED, fetched_at_utc=bad)
    with pytest.raises(k4.K4Error):
        k4.parse_webull_changelog(WEBULL.encode(), fetched_at_utc=bad)


# ---- the Kalshi RSS parser

def parsed(raw=FEED, fetched=NOW_S):
    return {f["entity_key"]: f for f in k4.parse_kalshi_rss(raw, fetched_at_utc=fetched)}


def test_the_rss_parser_makes_one_fact_per_item_with_the_listed_payload():
    facts = parsed()
    assert len(facts) == 6
    f = facts[key(KR, "2026-11-09", A_TITLE)]
    assert set(json.loads(f["payload_json"])) == {"title", "date", "summary", "link", "scheduled"}
    p = json.loads(f["payload_json"])
    assert p == {"title": A_TITLE, "date": "2026-11-09", "summary": "Adds order groups & limits.",
                 "link": "https://docs.kalshi.com/c/x", "scheduled": False}
    assert f["domain"] == "K4_VENUE" and f["source_id"] == KR


def test_the_summary_is_plain_text_and_capped_at_2000_characters():
    long_desc = "&lt;p&gt;" + "word " * 1000 + "&lt;/p&gt;"
    f = list(parsed(rss(rss_item("Long one", desc=long_desc))).values())[0]
    p = json.loads(f["payload_json"])
    assert len(p["summary"]) == 2000 and "<" not in p["summary"] and p["summary"].startswith("word word")
    short = list(parsed(rss(rss_item("Short one", desc="a &lt;i&gt;b&lt;/i&gt;"))).values())[0]
    assert json.loads(short["payload_json"])["summary"] == "a b"
    cdata = rss(rss_item("Cdata", desc="<![CDATA[<p>Hello <b>there</b></p>]]>"))
    assert json.loads(list(parsed(cdata).values())[0]["payload_json"])["summary"] == "Hello there"


def test_an_entry_dated_before_the_fetch_day_is_published_at_the_end_of_its_new_york_day(conn, reg):
    for f in parsed().values():
        add_fact(conn, **f)
    rows = current(conn)
    a = rows[key(KR, "2026-11-09", A_TITLE)]
    assert a["known_at_basis"] == "PUBLISHED" and a["published_at_utc"] == "2026-11-10T04:59:59.999999Z"
    assert a["known_at_utc"] == "2026-11-10T04:59:59.999999Z"        # 23:59:59.999999 EST, not 23:59:59 UTC
    assert payload(a)["scheduled"] is False
    b = rows[key(KR, "2026-11-20", B_TITLE)]
    assert b["known_at_basis"] == "PUBLISHED" and b["known_at_utc"] == "2026-11-21T04:59:59.999999Z"
    # two-way: one microsecond before the end of its day the fact is not known, at the end it is
    key_a = key(KR, "2026-11-09", A_TITLE)
    assert key_a not in current(conn, "2026-11-10T04:59:59.999998Z")
    assert key_a in current(conn, "2026-11-10T04:59:59.999999Z")


def test_the_new_york_date_of_the_instant_is_the_entry_date():
    late = parsed(rss(rss_item("Night item", "Tue, 10 Nov 2026 03:00:00 GMT")))        # 22:00 on 9 Nov in New York
    assert list(late)[0].startswith(f"{KR}:2026-11-09:")
    west = parsed(rss(rss_item("West item", "Mon, 09 Nov 2026 23:00:00 -0800")))        # 02:00 on 10 Nov in New York
    assert list(west)[0].startswith(f"{KR}:2026-11-10:")
    iso_day = parsed(rss(rss_item("Iso day", "2026-11-05")))
    assert list(iso_day)[0].startswith(f"{KR}:2026-11-05:")
    iso_time = parsed(rss(rss_item("Iso time", "2026-11-05T15:00:00Z")))
    assert list(iso_time)[0].startswith(f"{KR}:2026-11-05:")


def test_a_future_tbd_or_undated_entry_is_scheduled_and_known_when_fetched(conn, reg):
    for f in parsed().values():
        add_fact(conn, **f)
    rows = current(conn)
    for day, title in (("2026-12-02", C_TITLE), ("TBD", D_TITLE), ("TBD", F_TITLE)):
        r = rows[key(KR, day, title)]
        assert r["known_at_basis"] == "FETCHED" and r["known_at_utc"] == NOW_S and r["published_at_utc"] is None, title
        assert payload(r)["scheduled"] is True
    assert payload(rows[key(KR, "2026-12-02", C_TITLE)])["date"] == "2026-12-02"
    assert payload(rows[key(KR, "TBD", D_TITLE)])["date"] is None
    assert key(KR, "2026-12-02", C_TITLE) not in current(conn, "2026-11-23T17:59:59Z")        # not before it was fetched


def test_an_entry_dated_on_the_fetch_day_is_known_when_fetched_and_not_scheduled(conn, reg):
    for f in parsed().values():
        add_fact(conn, **f)
    r = current(conn)[key(KR, "2026-11-23", E_TITLE)]
    assert r["known_at_basis"] == "FETCHED" and r["known_at_utc"] == NOW_S and payload(r)["scheduled"] is False


def test_the_fetch_day_is_the_new_york_day_not_the_utc_day():
    one = rss(rss_item("Edge item", "Mon, 23 Nov 2026 15:00:00 GMT"))
    # 02:00 UTC on the 24th is 21:00 on the 23rd in New York: the entry is still "today" (not yet published)
    f = list(parsed(one, "2026-11-24T02:00:00.000000Z").values())[0]
    assert f["published_proven"] is False and json.loads(f["payload_json"])["scheduled"] is False
    # an entry dated the 24th, read at 21:00 on the 23rd in New York (02:00 UTC on the 24th), is still ahead: a plan
    ahead = rss(rss_item("Next day item", "Tue, 24 Nov 2026 15:00:00 GMT"))
    f = list(parsed(ahead, "2026-11-24T02:00:00.000000Z").values())[0]
    assert json.loads(f["payload_json"])["scheduled"] is True and f["published_proven"] is False
    f = list(parsed(ahead, "2026-11-24T06:00:00.000000Z").values())[0]          # 01:00 on the 24th: now it is today
    assert json.loads(f["payload_json"])["scheduled"] is False
    # 06:00 UTC on the 24th is 01:00 on the 24th in New York: the 23rd is over, the entry is published
    f = list(parsed(one, "2026-11-24T06:00:00.000000Z").values())[0]
    assert f["published_proven"] is True and f["published_at_utc"] == "2026-11-24T04:59:59.999999Z"


def test_the_entity_key_is_source_date_and_16_hex_of_the_normalized_title():
    k = list(parsed(rss(rss_item("  Mixed   CASE  <b>title</b> ", "Mon, 09 Nov 2026 15:00:00 GMT"))))[0]
    assert k == f"{KR}:2026-11-09:" + hashlib.sha256(b"mixed case title").hexdigest()[:16] + ":1"
    other = list(parsed(rss(rss_item("mixed case title", "Mon, 09 Nov 2026 15:00:00 GMT"))))[0]
    assert other == k
    different = list(parsed(rss(rss_item("Another title", "Mon, 09 Nov 2026 15:00:00 GMT"))))[0]
    assert different != k


def test_an_item_with_no_description_or_link_is_fine():
    f = list(parsed(rss(rss_item("Bare", desc=None, link=None))).values())[0]
    p = json.loads(f["payload_json"])
    assert p["summary"] == "" and p["link"] is None


def test_repeats_of_one_date_and_title_are_numbered_in_feed_order_and_all_kept():
    feed = rss(rss_item("Updates", desc="first"), rss_item("Other"), rss_item("Updates", desc="second"),
               rss_item("Updates", desc="third"), rss_item("Updates", "Tue, 10 Nov 2026 15:00:00 GMT", desc="next day"))
    facts = parsed(feed)
    assert len(facts) == 5
    assert [json.loads(facts[key(KR, "2026-11-09", "Updates", n)]["payload_json"])["summary"] for n in (1, 2, 3)] == \
        ["first", "second", "third"]
    assert key(KR, "2026-11-09", "Other") in facts and key(KR, "2026-11-10", "Updates", 1) in facts
    assert key(KR, "2026-11-10", "Updates", 2) not in facts
    assert [k.rsplit(":", 1)[1] for k in parsed(rss(rss_item("Single")))] == ["1"]            # one item: :1
    twice = parsed(rss(rss_item("Same"), rss_item("Same")))                                     # even identical repeats
    assert sorted(twice) == [key(KR, "2026-11-09", "Same", 1), key(KR, "2026-11-09", "Same", 2)]


def test_two_updates_items_on_one_day_are_both_stored_and_a_re_read_stores_nothing(reg):
    feed = rss(rss_item("Updates", desc="morning"), rss_item("Updates", desc="evening"))
    got = k4.refresh(reg, KR, http_get=Fake({KR_URL: feed}), now=NOW)
    assert got == {"stored": 2, "unchanged": 0, "error": 0, "diffs_opened": 1}
    rows = current(reg)
    assert payload(rows[key(KR, "2026-11-09", "Updates", 1)])["summary"] == "morning"
    assert payload(rows[key(KR, "2026-11-09", "Updates", 2)])["summary"] == "evening"
    before = n_rows(reg)
    got = k4.refresh(reg, KR, http_get=Fake({KR_URL: feed}), now=at(24))
    assert got == {"stored": 0, "unchanged": 2, "error": 0, "diffs_opened": 0} and n_rows(reg) == before


def test_webull_repeats_are_numbered_too():
    page_html = "<h2>2026-11-18 Updates</h2><p>one</p><h2>2026-11-18 Updates</h2><p>two</p>"
    facts = wb(page_html)
    assert sorted(facts) == [key(WB, "2026-11-18", "Updates", 1), key(WB, "2026-11-18", "Updates", 2)]


GOOD = rss_item("Good one")
REFUSED_FEEDS = {
    "empty": b"",
    "not bytes": "<rss version='2.0'><channel/></rss>",
    "not xml": b"<html><body>gateway error</body></html>",
    "truncated": FEED[:200],
    "wrong root": b"<feed><entry><title>x</title></entry></feed>",
    "wrong version": rss(GOOD, version="0.91"),
    "no items": rss(),
    "two channels": FEED.replace(b"</channel></rss>", b"</channel><channel><item><title>x</title></item></channel></rss>"),
    "no channel": b'<rss version="2.0"></rss>',
    "doctype": b'<?xml version="1.0"?><!DOCTYPE rss [<!ENTITY a "aaaa">]><rss version="2.0"><channel><item><title>&a;</title></item></channel></rss>',
    "entity decl": b'<!ENTITY a "b"><rss version="2.0"><channel><item><title>x</title></item></channel></rss>',
    "item with no title": rss(GOOD, "<item><pubDate>Mon, 09 Nov 2026 15:00:00 GMT</pubDate></item>"),
    "item with a blank title": rss(GOOD, rss_item("   <b></b> ")),
    "bad pubDate": rss(GOOD, rss_item("Bad date", "next Tuesday")),
    "impossible date": rss(GOOD, rss_item("Bad day", "2026-02-30")),
    "year far away": rss(GOOD, rss_item("Far", "Mon, 09 Nov 3026 15:00:00 GMT")),
    "link not http": rss(GOOD, rss_item("Bad link", link="javascript:alert(1)")),
    "not utf-8": b'<rss version="2.0"><channel><item><title>\xff</title></item></channel></rss>',
}


@pytest.mark.parametrize("name", sorted(REFUSED_FEEDS))
def test_the_rss_parser_refuses_a_shape_broken_body_whole(name):
    with pytest.raises(k4.K4Error):
        k4.parse_kalshi_rss(REFUSED_FEEDS[name], fetched_at_utc=NOW_S)


def test_the_rss_parser_accepts_the_extra_things_a_real_feed_has():
    extra = '<language>en</language><atom:link xmlns:atom="http://www.w3.org/2005/Atom" rel="self" href="x"/>'
    assert len(parsed(rss(GOOD, extra=extra))) == 1
    assert len(parsed(rss(rss_item("Item", "Mon, 09 Nov 2026 15:00:00 +0000"))) ) == 1
    assert len(parsed(rss(rss_item("Item", "Mon, 09 Nov 2026 15:00:00 GMT", desc=""))) ) == 1


# ---- the Webull change-log parser

def wb(raw=WEBULL, fetched=NOW_S):
    return {f["entity_key"]: f for f in k4.parse_webull_changelog(raw.encode() if isinstance(raw, str) else raw,
                                                                  fetched_at_utc=fetched)}


def test_the_webull_parser_reads_dated_headings_and_ignores_the_page_furniture():
    facts = wb()
    titles = {json.loads(f["payload_json"])["title"] for f in facts.values()}
    assert titles == {"Order status stream", "Quote limit raised", "Token expiry", "Options chain endpoint",
                      "Futures quotes"}                                                  # nav and footer headings are not entries
    p = json.loads(facts[key(WB, "2026-11-18", "Order status stream")]["payload_json"])
    assert p == {"title": "Order status stream", "date": "2026-11-18", "scheduled": False, "link": WB_URL,
                 "summary": "A new stream of order status events. Added field status ."}


def test_the_webull_parser_reads_the_three_date_forms_and_tbd():
    facts = wb()
    assert key(WB, "2026-11-10", "Quote limit raised") in facts            # "Nov 10, 2026", title from the first text under it
    assert key(WB, "2026-11-03", "Token expiry") in facts                  # "3 November 2026 - Token expiry"
    tbd = facts[key(WB, "TBD", "Options chain endpoint")]
    p = json.loads(tbd["payload_json"])
    assert p["scheduled"] is True and p["date"] is None
    soon = json.loads(facts[key(WB, "2026-12-15", "Futures quotes")]["payload_json"])
    assert soon["scheduled"] is True and soon["date"] == "2026-12-15"
    assert "Notes See the guide." in json.loads(facts[key(WB, "2026-11-10", "Quote limit raised")]["payload_json"])["summary"]


def test_the_webull_entry_dates_get_the_same_known_at_rules(conn, reg):
    for f in wb().values():
        add_fact(conn, **f)
    rows = current(conn)
    r = rows[key(WB, "2026-11-18", "Order status stream")]
    assert r["known_at_basis"] == "PUBLISHED" and r["known_at_utc"] == "2026-11-19T04:59:59.999999Z"
    for k in (key(WB, "2026-12-15", "Futures quotes"), key(WB, "TBD", "Options chain endpoint")):
        assert rows[k]["known_at_basis"] == "FETCHED" and rows[k]["known_at_utc"] == NOW_S


REFUSED_PAGES = {
    "empty": b"",
    "not bytes": "<h2>2026-11-18 x</h2>",
    "no entries": b"<html><body><h1>Changelog</h1><p>Nothing dated here.</p></body></html>",
    "only furniture": b"<nav><h2>2026-11-18 menu</h2></nav><footer><h2>2026-11-19 foot</h2></footer>",
    "impossible date": b"<h2>2026-02-30 Broken</h2><p>x</p>",
    "impossible month day": b"<h2>Feb 30, 2026 Broken</h2><p>x</p>",
    "year out of range": b"<h2>2026-11-18 ok</h2><h2>1999-01-01 old</h2>",
    "heading never closes": b"<h2>2026-11-18 Order status",
    "entry with no title": b"<h2>2026-11-18</h2>",
    "not utf-8": b"<h2>2026-11-18 \xff</h2><p>x</p>",
}


@pytest.mark.parametrize("name", sorted(REFUSED_PAGES))
def test_the_webull_parser_refuses_a_shape_broken_page_whole(name):
    with pytest.raises(k4.K4Error):
        k4.parse_webull_changelog(REFUSED_PAGES[name], fetched_at_utc=NOW_S)


def test_the_webull_parser_accepts_the_minimal_good_pages():
    assert len(wb("<h2>2026-11-18 Title only</h2>")) == 1
    assert len(wb("<h2>2026-11-18</h2><p>Title from the text</p>")) == 1
    assert len(wb("<h3><a href='#x'>2026-11-18</a> Linked</h3>")) == 1


# ---- fetch

def test_fetch_requests_the_page_with_a_generic_agent_and_no_credentials():
    fake = Fake({KR_URL: FEED})
    facts = k4.fetch(KR, http_get=fake, now=NOW)
    assert len(facts) == 6 and [c[0] for c in fake.calls] == [KR_URL]
    headers = fake.calls[0][1]
    assert headers["User-Agent"] == "calendar-and-notice-reader/1.0" and set(headers) == {"User-Agent", "Accept"}
    fake = Fake({WB_URL: WEBULL.encode()})
    assert len(k4.fetch(WB, http_get=fake, now=NOW)) == 5 and fake.calls[0][0] == WB_URL


def test_fetch_a_document_uses_the_owners_address(ready):
    fake = Fake({DOC_URL[RULE]: page(CLAUSE)})
    facts = k4.fetch(RULE, http_get=fake, now=NOW)
    assert [f["entity_key"] for f in facts] == [f"{RULE}:DOC"] and fake.calls[0][0] == DOC_URL[RULE]


def test_fetch_refuses_a_document_source_with_no_address_even_with_a_client():
    fake = Fake({})
    for sid in DOCS:
        with pytest.raises(k4.K4Error, match="address is not set"):
            k4.fetch(sid, http_get=fake, now=NOW)
    assert fake.calls == []


def test_fetch_refuses_unknown_source_no_client_naive_time_and_non_bytes():
    with pytest.raises(k4.K4Error):
        k4.fetch("nope", http_get=Fake({}), now=NOW)
    with pytest.raises(k4.K4Error, match="no default client"):
        k4.fetch(KR, now=NOW)
    with pytest.raises(k4.K4Error):
        k4.fetch(KR, http_get=Fake({KR_URL: FEED}), now=datetime(2026, 11, 23, 18, 0))
    with pytest.raises(k4.K4Error, match="bytes"):
        k4.fetch(KR, http_get=lambda url, headers: FEED.decode(), now=NOW)


def test_the_only_network_client_is_built_by_the_cli():
    src = inspect.getsource(k4)
    assert "urllib.request" in src and "def _urllib_get" in src
    for name in ("fetch", "refresh", "pending_rule_diffs", "close_rule_diff", "parse_document", "parse_kalshi_rss",
                 "parse_webull_changelog", "normalize_text"):
        assert "urllib" not in inspect.getsource(getattr(k4, name)), name


def test_the_module_never_names_the_facts_table_itself():
    text = inspect.getsource(k4)
    assert "knowledge_facts" not in text.lower() and "facts_as_of" in text


# ---- refresh: entries

def settle(conn, source, when=NOW + timedelta(minutes=1)):
    """Close the baseline flag of a change-log source, so the per-entry flags that follow can be counted alone."""
    return k4.close_rule_diff(conn, f"{source}:BASELINE", "rules.baseline", when)


def items(n, start=1):
    return [rss_item(f"Entry number {i}", f"Mon, {i:02d} Nov 2026 15:00:00 GMT") for i in range(start, start + n)]


def test_the_first_read_of_a_change_log_stores_every_entry_and_opens_one_baseline_flag(reg):
    got = k4.refresh(reg, KR, http_get=Fake({KR_URL: FEED}), now=NOW)
    assert got == {"stored": 6, "unchanged": 0, "error": 0, "diffs_opened": 1}
    rows = current(reg)
    assert len(rows) == 7 and f"PENDING_RULE_DIFF:{KR}:BASELINE" in rows
    entities = {k: r for k, r in rows.items() if not k.startswith("PENDING")}
    assert len(entities) == 6
    (d,) = k4.pending_rule_diffs(reg, FAR)
    assert d == {"entity_key": f"{KR}:BASELINE", "source_id": KR, "old_fact_version_id": None,
                 "new_fact_version_id": max(r["fact_version_id"] for r in entities.values()), "opened_at_utc": NOW_S}
    flag = rows[f"PENDING_RULE_DIFF:{KR}:BASELINE"]
    assert payload(flag) == {"entity_key": f"{KR}:BASELINE", "old_fact_version_id": None,
                             "new_fact_version_id": d["new_fact_version_id"], "status": "OPEN", "entries": 6}
    assert flag["known_at_basis"] == "FETCHED" and flag["domain"] == "K4_VENUE" and flag["source_id"] == KR


def test_a_first_read_of_twelve_items_gives_one_open_flag_and_a_second_read_with_one_more_gives_one_more(reg):
    got = k4.refresh(reg, KR, http_get=Fake({KR_URL: rss(*items(12))}), now=NOW)
    assert got["stored"] == 12 and got["diffs_opened"] == 1
    assert len(k4.pending_rule_diffs(reg, FAR)) == 1
    got = k4.refresh(reg, KR, http_get=Fake({KR_URL: rss(*items(13))}), now=at(24))
    assert got == {"stored": 1, "unchanged": 12, "error": 0, "diffs_opened": 1}
    diffs = k4.pending_rule_diffs(reg, FAR)
    assert sorted(d["entity_key"] for d in diffs) == sorted([f"{KR}:BASELINE", key(KR, "2026-11-13", "Entry number 13")])
    assert k4.refresh(reg, KR, http_get=Fake({KR_URL: rss(*items(13))}), now=at(25))["diffs_opened"] == 0


def test_a_source_with_earlier_facts_never_gets_a_baseline_flag(reg):
    k4.refresh(reg, KR, http_get=Fake({KR_URL: rss(*items(3))}), now=NOW)
    settle(reg, KR)
    for n, day in ((4, 24), (5, 25)):
        got = k4.refresh(reg, KR, http_get=Fake({KR_URL: rss(*items(n))}), now=at(day))
        assert got["stored"] == 1 and got["diffs_opened"] == 1
    assert sorted(d["entity_key"] for d in k4.pending_rule_diffs(reg, FAR)) == sorted(
        [key(KR, "2026-11-04", "Entry number 4"), key(KR, "2026-11-05", "Entry number 5")])
    n_baseline = reg.execute("SELECT count(*) FROM knowledge_facts WHERE entity_key LIKE '%:BASELINE'").fetchone()[0]
    assert n_baseline == 2                                      # the open one and its close: no second baseline


def test_a_closed_baseline_is_not_opened_again_by_an_unchanged_read(reg):
    k4.refresh(reg, KR, http_get=Fake({KR_URL: FEED}), now=NOW)
    settle(reg, KR)
    assert k4.pending_rule_diffs(reg, FAR) == []
    assert k4.refresh(reg, KR, http_get=Fake({KR_URL: FEED}), now=at(24))["diffs_opened"] == 0
    assert k4.pending_rule_diffs(reg, FAR) == []
    closed = current(reg)[f"PENDING_RULE_DIFF:{KR}:BASELINE"]
    assert payload(closed)["status"] == "CLOSED" and payload(closed)["rule_module"] == "rules.baseline"
    assert payload(closed)["entries"] == 6


def test_an_interrupted_first_read_is_repaired_with_one_baseline_flag_and_no_per_entry_flags(reg):
    facts = k4.fetch(KR, http_get=Fake({KR_URL: FEED}), now=NOW)
    for f in facts[:4]:
        add_fact(reg, **f)                                       # the run stopped here: 4 entries stored, no flag
    assert k4.pending_rule_diffs(reg, FAR) == []
    got = k4.refresh(reg, KR, http_get=Fake({KR_URL: FEED}), now=at(24))
    assert got == {"stored": 2, "unchanged": 4, "error": 0, "diffs_opened": 1}
    (d,) = k4.pending_rule_diffs(reg, FAR)
    assert d["entity_key"] == f"{KR}:BASELINE" and d["old_fact_version_id"] is None
    assert payload(current(reg)[f"PENDING_RULE_DIFF:{KR}:BASELINE"])["entries"] == 6
    keys = [k for k in current(reg) if k.startswith("PENDING")]
    assert keys == [f"PENDING_RULE_DIFF:{KR}:BASELINE"]
    assert k4.refresh(reg, KR, http_get=Fake({KR_URL: FEED}), now=at(25))["diffs_opened"] == 0      # and only once


def test_an_interrupted_first_read_with_every_entry_stored_is_repaired_too(reg):
    for f in k4.fetch(KR, http_get=Fake({KR_URL: FEED}), now=NOW):
        add_fact(reg, **f)
    got = k4.refresh(reg, KR, http_get=Fake({KR_URL: FEED}), now=at(24))
    assert got == {"stored": 0, "unchanged": 6, "error": 0, "diffs_opened": 1}
    assert [d["entity_key"] for d in k4.pending_rule_diffs(reg, FAR)] == [f"{KR}:BASELINE"]


def test_no_baseline_flag_is_opened_when_a_first_read_had_an_error_and_the_next_read_opens_it(reg, monkeypatch):
    real = k4.store.add_fact
    calls = {"n": 0}

    def flaky(conn, **f):
        calls["n"] += 1
        if calls["n"] == 3:
            raise StoreError("simulated refusal")
        return real(conn, **f)
    monkeypatch.setattr(k4.store, "add_fact", flaky)
    got = k4.refresh(reg, KR, http_get=Fake({KR_URL: FEED}), now=NOW)
    assert got["error"] == 1 and got["stored"] == 5 and got["diffs_opened"] == 0
    assert k4.pending_rule_diffs(reg, FAR) == []
    monkeypatch.setattr(k4.store, "add_fact", real)
    got = k4.refresh(reg, KR, http_get=Fake({KR_URL: FEED}), now=at(24))
    assert got["error"] == 0 and got["diffs_opened"] == 1 and got["stored"] == 1
    assert [d["entity_key"] for d in k4.pending_rule_diffs(reg, FAR)] == [f"{KR}:BASELINE"]


def test_a_document_source_never_gets_a_baseline_flag(reg):
    refresh_doc(reg, FEE, page(CLAUSE))
    assert [d["entity_key"] for d in k4.pending_rule_diffs(reg, FAR)] == [f"{FEE}:DOC"]


def test_the_baseline_flag_is_closed_like_any_other(reg):
    k4.refresh(reg, WB, http_get=Fake({WB_URL: WEBULL.encode()}), now=NOW)
    new_id = k4.close_rule_diff(reg, f"{WB}:BASELINE", "valedia_trader.rules.webull", at(24))
    assert isinstance(new_id, int) and k4.pending_rule_diffs(reg, FAR) == []
    assert [d["entity_key"] for d in k4.pending_rule_diffs(reg, NOW_S)] == [f"{WB}:BASELINE"]
    with pytest.raises(k4.K4Error, match="already closed"):
        k4.close_rule_diff(reg, f"{WB}:BASELINE", "rules.x", at(25))


def test_an_unchanged_re_read_stores_nothing_and_opens_no_diff(reg):
    k4.refresh(reg, KR, http_get=Fake({KR_URL: FEED}), now=NOW)
    before = n_rows(reg)
    got = k4.refresh(reg, KR, http_get=Fake({KR_URL: FEED}), now=at(24))
    assert got == {"stored": 0, "unchanged": 6, "error": 0, "diffs_opened": 0}
    assert n_rows(reg) == before
    assert len(k4.pending_rule_diffs(reg, FAR)) == 1


def test_a_changed_summary_is_a_new_version_with_a_second_flag_that_points_back(reg):
    k4.refresh(reg, KR, http_get=Fake({KR_URL: rss(rss_item("Edited", desc="first"))}), now=NOW)
    settle(reg, KR)
    first = current(reg)[key(KR, "2026-11-09", "Edited")]["fact_version_id"]
    got = k4.refresh(reg, KR, http_get=Fake({KR_URL: rss(rss_item("Edited", desc="second"))}), now=at(24))
    assert got["stored"] == 1 and got["diffs_opened"] == 1
    second = current(reg)[key(KR, "2026-11-09", "Edited")]["fact_version_id"]
    assert second != first
    (d,) = k4.pending_rule_diffs(reg, FAR)
    assert d["old_fact_version_id"] == first and d["new_fact_version_id"] == second     # one flag per entity, newest wins
    assert payload(current(reg)[key(KR, "2026-11-09", "Edited")])["summary"] == "second"


def test_a_planned_entry_that_comes_due_is_a_new_version_known_when_fetched_with_no_new_diff(reg):
    plan = rss(rss_item("Plan", "Wed, 25 Nov 2026 15:00:00 GMT", "soon"))
    k4.refresh(reg, KR, http_get=Fake({KR_URL: plan}), now=NOW)
    settle(reg, KR)
    k = key(KR, "2026-11-25", "Plan")
    assert payload(current(reg)[k])["scheduled"] is True
    got = k4.refresh(reg, KR, http_get=Fake({KR_URL: plan}), now=at(27))
    assert got == {"stored": 1, "unchanged": 0, "error": 0, "diffs_opened": 0}          # V3: the flip alone opens nothing
    assert k4.pending_rule_diffs(reg, FAR) == []
    row = current(reg)[k]
    assert payload(row)["scheduled"] is False
    assert row["known_at_basis"] == "FETCHED" and row["known_at_utc"] == stamp(at(27)) and row["published_at_utc"] is None
    # V1: as of the 26th (after its day) it is still the plan; only the fetch of the 27th shows it as due
    assert payload(current(reg, "2026-11-26T05:00:00Z")[k])["scheduled"] is True
    assert payload(current(reg, stamp(at(27)))[k])["scheduled"] is False
    # and the unchanged read afterwards opens nothing either (the flip is not mistaken for an interrupted run)
    assert k4.refresh(reg, KR, http_get=Fake({KR_URL: plan}), now=at(28))["diffs_opened"] == 0
    assert k4.pending_rule_diffs(reg, FAR) == []


def test_a_text_change_after_a_flip_still_opens_a_diff_and_a_flip_with_a_text_change_too(reg):
    plan = rss(rss_item("Plan", "Wed, 25 Nov 2026 15:00:00 GMT", "soon"))
    k4.refresh(reg, KR, http_get=Fake({KR_URL: plan}), now=NOW)
    settle(reg, KR)
    k4.refresh(reg, KR, http_get=Fake({KR_URL: plan}), now=at(27))                       # the flip: no diff
    edited = rss(rss_item("Plan", "Wed, 25 Nov 2026 15:00:00 GMT", "soon, with a change"))
    got = k4.refresh(reg, KR, http_get=Fake({KR_URL: edited}), now=at(28))
    assert got["stored"] == 1 and got["diffs_opened"] == 1
    (d,) = k4.pending_rule_diffs(reg, FAR)
    assert d["entity_key"] == key(KR, "2026-11-25", "Plan") and d["opened_at_utc"] == stamp(at(28))
    # a plan whose text changes in the same read that it comes due also opens one
    other = rss(rss_item("Other", "Wed, 25 Nov 2026 15:00:00 GMT", "a"))
    k4.refresh(reg, KR, http_get=Fake({KR_URL: other}), now=at(23, 19))
    got = k4.refresh(reg, KR, http_get=Fake({KR_URL: rss(rss_item("Other", "Wed, 25 Nov 2026 15:00:00 GMT", "b"))}), now=at(27, 19))
    assert got["stored"] == 1 and got["diffs_opened"] == 1


def test_an_interrupted_text_change_of_a_change_log_entry_is_still_repaired(reg):
    k4.refresh(reg, KR, http_get=Fake({KR_URL: rss(rss_item("Edited", desc="first"))}), now=NOW)
    settle(reg, KR)
    (fact,) = k4.fetch(KR, http_get=Fake({KR_URL: rss(rss_item("Edited", desc="second"))}), now=at(24))
    add_fact(reg, **dict(fact, published_at_utc=None, published_proven=False))          # stored, its flag never written
    assert k4.pending_rule_diffs(reg, FAR) == []
    got = k4.refresh(reg, KR, http_get=Fake({KR_URL: rss(rss_item("Edited", desc="second"))}), now=at(25))
    assert got == {"stored": 0, "unchanged": 1, "error": 0, "diffs_opened": 1}


def test_an_edited_entry_is_known_when_fetched_not_at_the_end_of_its_old_day(reg):
    def feed(summary):
        return rss(rss_item("Entry A", "Sun, 01 Nov 2026 15:00:00 GMT", "a"),
                   rss_item("Entry B", "Mon, 02 Nov 2026 15:00:00 GMT", summary))
    k = key(KR, "2026-11-02", "Entry B")
    k4.refresh(reg, KR, http_get=Fake({KR_URL: feed("two")}), now=datetime(2026, 11, 10, 18, 0, tzinfo=timezone.utc))
    first = current(reg)[k]
    assert first["known_at_basis"] == "PUBLISHED" and first["known_at_utc"] == "2026-11-03T04:59:59.999999Z"
    got = k4.refresh(reg, KR, http_get=Fake({KR_URL: feed("two CHANGED")}),
                     now=datetime(2026, 11, 12, 18, 0, tzinfo=timezone.utc))
    assert got["stored"] == 1 and got["diffs_opened"] == 1
    edited = current(reg)[k]
    assert edited["known_at_basis"] == "FETCHED" and edited["published_at_utc"] is None
    assert edited["known_at_utc"] == "2026-11-12T18:00:00.000000Z"
    for as_of in ("2026-11-03T05:00:00Z", "2026-11-11T12:00:00Z", "2026-11-12T17:59:59Z"):
        assert payload(current(reg, as_of)[k])["summary"] == "two", as_of            # the old text, not the edit
    assert payload(current(reg, "2026-11-12T18:00:00Z")[k])["summary"] == "two CHANGED"
    assert payload(current(reg, "2026-11-12T19:00:00Z")[k])["summary"] == "two CHANGED"


def test_only_the_first_version_of_an_entry_may_be_published_at_its_date(reg):
    # a re-read of the same text keeps the first version as it was; a first sight of a new entry is still PUBLISHED
    feed = rss(rss_item("Entry A", "Sun, 01 Nov 2026 15:00:00 GMT", "a"))
    k4.refresh(reg, KR, http_get=Fake({KR_URL: feed}), now=NOW)
    k4.refresh(reg, KR, http_get=Fake({KR_URL: feed}), now=at(24))
    row = current(reg)[key(KR, "2026-11-01", "Entry A")]
    assert row["known_at_basis"] == "PUBLISHED" and row["known_at_utc"] == "2026-11-02T04:59:59.999999Z"
    k4.refresh(reg, KR, http_get=Fake({KR_URL: rss(rss_item("Entry A", "Sun, 01 Nov 2026 15:00:00 GMT", "a"),
                                                    rss_item("Entry N", "Tue, 24 Nov 2026 15:00:00 GMT", "n"))}), now=at(25))
    assert current(reg)[key(KR, "2026-11-24", "Entry N")]["known_at_basis"] == "PUBLISHED"


def test_a_refused_body_stores_nothing_even_when_the_first_items_were_good(reg):
    bad = rss(rss_item("Good one"), rss_item("Bad date", "next Tuesday"))
    got = k4.refresh(reg, KR, http_get=Fake({KR_URL: bad}), now=NOW)
    assert got["error"] == 1 and got["stored"] == 0 and got["diffs_opened"] == 0 and "pubDate" in got["detail"]
    assert n_rows(reg) == 0
    got = k4.refresh(reg, KR, http_get=Fake({KR_URL: rss(rss_item("Good one"))}), now=NOW)
    assert got["error"] == 0 and got["stored"] == 1                              # and the good body still goes in


def test_a_refused_body_leaves_the_earlier_facts_and_flags_alone(reg):
    k4.refresh(reg, KR, http_get=Fake({KR_URL: FEED}), now=NOW)
    before = n_rows(reg)
    got = k4.refresh(reg, KR, http_get=Fake({KR_URL: b"<html>gateway error</html>"}), now=at(24))
    assert got["error"] == 1 and n_rows(reg) == before and len(k4.pending_rule_diffs(reg, FAR)) == 1


def test_a_failed_request_is_an_error_not_a_crash_and_says_nothing_about_the_request(reg):
    got = k4.refresh(reg, KR, http_get=Fake({KR_URL: OSError("connection to 10.0.0.1 refused")}), now=NOW)
    assert got["error"] == 1 and "OSError" in got["detail"] and "10.0.0.1" not in got["detail"]
    assert n_rows(reg) == 0


def test_a_document_source_with_no_address_is_an_error_not_a_crash(reg, monkeypatch):
    monkeypatch.setitem(k4.SOURCES, FEE, dict(k4.SOURCES[FEE], endpoint=None))
    fake = Fake({})
    got = k4.refresh(reg, FEE, http_get=fake, now=NOW)
    assert got["error"] == 1 and "address" in got["detail"] and fake.calls == [] and n_rows(reg) == 0


def test_refresh_with_an_unregistered_source_stores_nothing_and_reports_errors(conn, ready):
    got = k4.refresh(conn, KR, http_get=Fake({KR_URL: FEED}), now=NOW)
    assert got["error"] >= 1 and got["stored"] == 0 and got["diffs_opened"] == 0
    assert n_rows(conn) == 0


def test_refresh_arguments_are_checked(reg):
    with pytest.raises(k4.K4Error):
        k4.refresh(reg, "nope", http_get=Fake({}), now=NOW)
    with pytest.raises(k4.K4Error):
        k4.refresh(reg, KR, http_get=Fake({KR_URL: FEED}), now=datetime(2026, 11, 23, 18, 0))
    assert k4.refresh(reg, KR, now=NOW)["error"] == 1            # no client given: counted, nothing stored
    assert n_rows(reg) == 0


def test_refresh_webull_stores_and_opens_one_baseline_flag(reg):
    got = k4.refresh(reg, WB, http_get=Fake({WB_URL: WEBULL.encode()}), now=NOW)
    assert got == {"stored": 5, "unchanged": 0, "error": 0, "diffs_opened": 1}
    assert k4.refresh(reg, WB, http_get=Fake({WB_URL: WEBULL.encode()}), now=at(24))["unchanged"] == 5


# ---- refresh: documents and the rule diff

def test_a_nonce_only_change_stores_nothing_and_a_clause_change_stores_a_version_and_one_open_diff(reg):
    first = refresh_doc(reg, FEE, page(CLAUSE, "nonce-AAAA"))
    assert first == {"stored": 1, "unchanged": 0, "error": 0, "diffs_opened": 1}
    v1 = current(reg)[f"{FEE}:DOC"]["fact_version_id"]
    rows_after_first = n_rows(reg)

    same = refresh_doc(reg, FEE, page(CLAUSE, "nonce-ZZZZ"), now=at(24))
    assert same == {"stored": 0, "unchanged": 1, "error": 0, "diffs_opened": 0}
    assert n_rows(reg) == rows_after_first and current(reg)[f"{FEE}:DOC"]["fact_version_id"] == v1

    changed = refresh_doc(reg, FEE, page(CLAUSE_CHANGED, "nonce-ZZZZ"), now=at(25))
    assert changed == {"stored": 1, "unchanged": 0, "error": 0, "diffs_opened": 1}
    v2 = current(reg)[f"{FEE}:DOC"]["fact_version_id"]
    assert v2 != v1 and n_rows(reg) == rows_after_first + 2
    (d,) = k4.pending_rule_diffs(reg, FAR)
    assert d == {"entity_key": f"{FEE}:DOC", "source_id": FEE, "old_fact_version_id": v1, "new_fact_version_id": v2,
                 "opened_at_utc": stamp(at(25))}
    flag = current(reg)[f"PENDING_RULE_DIFF:{FEE}:DOC"]
    assert payload(flag) == {"entity_key": f"{FEE}:DOC", "old_fact_version_id": v1, "new_fact_version_id": v2,
                             "status": "OPEN"}
    assert flag["known_at_basis"] == "FETCHED" and flag["source_id"] == FEE and flag["domain"] == "K4_VENUE"


def test_a_document_is_always_known_when_fetched_and_the_old_version_stays_visible_before_it(reg):
    refresh_doc(reg, FEE, page(CLAUSE), now=at(23))
    refresh_doc(reg, FEE, page(CLAUSE_CHANGED), now=at(25))
    row = current(reg)[f"{FEE}:DOC"]
    assert row["known_at_basis"] == "FETCHED" and row["known_at_utc"] == stamp(at(25)) and row["published_at_utc"] is None
    old = current(reg, stamp(at(24)))[f"{FEE}:DOC"]
    assert old["known_at_utc"] == stamp(at(23))


def test_no_full_page_text_is_stored_only_the_excerpt_and_the_hash(reg):
    body = "".join(f"<p>Clause {i}: unique-marker-{i} applies to all members.</p>" for i in range(300))
    refresh_doc(reg, RULE, page(body))
    p = payload(current(reg)[f"{RULE}:DOC"])
    assert set(p) == {"text_sha256", "length", "excerpt"} and len(p["excerpt"]) == 500 and p["length"] > 10_000
    stored = " ".join(r[0] for r in reg.execute("SELECT payload_json FROM knowledge_facts"))
    assert "unique-marker-0 " in stored and "unique-marker-299" not in stored and "unique-marker-100" not in stored
    assert len(stored) < 2000 and "<script" not in stored and "<p>" not in stored


def test_documents_of_different_sources_do_not_touch_each_other(reg):
    refresh_doc(reg, FEE, page(CLAUSE))
    refresh_doc(reg, RULE, page(CLAUSE))
    assert {d["source_id"] for d in k4.pending_rule_diffs(reg, FAR)} == {FEE, RULE}
    refresh_doc(reg, FEE, page(CLAUSE_CHANGED), now=at(24))
    by = {d["source_id"]: d for d in k4.pending_rule_diffs(reg, FAR)}
    assert by[FEE]["old_fact_version_id"] is not None and by[RULE]["old_fact_version_id"] is None
    assert refresh_doc(reg, RULE, page(CLAUSE), now=at(25))["unchanged"] == 1


def test_a_run_that_stopped_before_its_flag_is_repaired_by_the_next_read(reg):
    (fact,) = k4.fetch(FEE, http_get=Fake({DOC_URL[FEE]: page(CLAUSE)}), now=NOW)
    add_fact(reg, **fact)                                        # the entity version is stored, its flag is not
    assert k4.pending_rule_diffs(reg, FAR) == []
    got = refresh_doc(reg, FEE, page(CLAUSE), now=at(24))
    assert got == {"stored": 0, "unchanged": 1, "error": 0, "diffs_opened": 1}
    (d,) = k4.pending_rule_diffs(reg, FAR)
    assert d["new_fact_version_id"] == current(reg)[f"{FEE}:DOC"]["fact_version_id"] and d["old_fact_version_id"] is None
    assert refresh_doc(reg, FEE, page(CLAUSE), now=at(25))["diffs_opened"] == 0          # and only once


def test_a_second_change_while_one_is_open_replaces_the_flag_with_a_newer_open_one(reg):
    refresh_doc(reg, FEE, page(CLAUSE))
    v1 = current(reg)[f"{FEE}:DOC"]["fact_version_id"]
    refresh_doc(reg, FEE, page(CLAUSE_CHANGED), now=at(24))
    refresh_doc(reg, FEE, page(CLAUSE), now=at(25))               # changed back: still a new version, still reviewed
    (d,) = k4.pending_rule_diffs(reg, FAR)
    assert d["new_fact_version_id"] == current(reg)[f"{FEE}:DOC"]["fact_version_id"] != v1
    assert d["opened_at_utc"] == stamp(at(25))


# ---- close_rule_diff and pending_rule_diffs

def test_close_rule_diff_closes_it_and_a_read_before_the_close_still_shows_it_open(reg):
    refresh_doc(reg, FEE, page(CLAUSE))
    v1 = current(reg)[f"{FEE}:DOC"]["fact_version_id"]
    refresh_doc(reg, FEE, page(CLAUSE_CHANGED), now=at(24))
    close_at = at(26)
    new_id = k4.close_rule_diff(reg, f"{FEE}:DOC", "valedia_trader.rules.kalshi_fees", close_at)
    assert isinstance(new_id, int)
    assert k4.pending_rule_diffs(reg, FAR) == []
    assert k4.pending_rule_diffs(reg, stamp(close_at)) == []                              # at the close time: closed
    (still,) = k4.pending_rule_diffs(reg, stamp(close_at - timedelta(seconds=1)))           # a second before: open
    assert still["entity_key"] == f"{FEE}:DOC" and still["old_fact_version_id"] == v1
    closed = current(reg)[f"PENDING_RULE_DIFF:{FEE}:DOC"]
    assert payload(closed) == {"entity_key": f"{FEE}:DOC", "old_fact_version_id": v1,
                               "new_fact_version_id": current(reg)[f"{FEE}:DOC"]["fact_version_id"],
                               "status": "CLOSED", "rule_module": "valedia_trader.rules.kalshi_fees"}
    assert closed["fact_version_id"] == new_id and closed["known_at_utc"] == stamp(close_at)
    assert closed["known_at_basis"] == "FETCHED"


def test_closing_leaves_the_entity_fact_alone_and_a_later_change_opens_a_new_diff(reg):
    refresh_doc(reg, FEE, page(CLAUSE))
    k4.close_rule_diff(reg, f"{FEE}:DOC", "rules.first", at(24))
    assert refresh_doc(reg, FEE, page(CLAUSE), now=at(25)) == {"stored": 0, "unchanged": 1, "error": 0, "diffs_opened": 0}
    assert k4.pending_rule_diffs(reg, FAR) == []                    # a closed diff is not reopened by an unchanged read
    refresh_doc(reg, FEE, page(CLAUSE_CHANGED), now=at(26))
    (d,) = k4.pending_rule_diffs(reg, FAR)
    assert d["opened_at_utc"] == stamp(at(26))
    k4.close_rule_diff(reg, f"{FEE}:DOC", "rules.second", at(27))
    assert k4.pending_rule_diffs(reg, FAR) == []


@pytest.mark.parametrize("entity,module,now,why", [
    (f"{FEE}:DOC", "rules.x", at(25), "closed already"),
    ("nope:DOC", "rules.x", at(25), "no such diff"),
    (f"PENDING_RULE_DIFF:{FEE}:DOC", "rules.x", at(25), "the prefix is not part of the key"),
    ("", "rules.x", at(25), "empty key"),
    (None, "rules.x", at(25), "not text"),
    (f"{FEE}:DOC", "", at(25), "empty module"),
    (f"{FEE}:DOC", "has space", at(25), "module with a space"),
    (f"{FEE}:DOC", "x" * 201, at(25), "module too long"),
    (f"{FEE}:DOC", None, at(25), "module not text"),
    (f"{FEE}:DOC", "rules.x", datetime(2026, 11, 25, 18, 0), "naive time"),
    (f"{FEE}:DOC", "rules.x", at(22), "closing before the diff was opened"),
])
def test_close_rule_diff_refuses_what_it_should(reg, entity, module, now, why):
    refresh_doc(reg, FEE, page(CLAUSE), now=at(23))
    if why == "closed already":
        k4.close_rule_diff(reg, entity, "rules.first", at(24))
    before = n_rows(reg)
    with pytest.raises(k4.K4Error):
        k4.close_rule_diff(reg, entity, module, now)
    assert n_rows(reg) == before, why


def test_the_close_goes_through_for_the_good_case_the_refusals_above_protect(reg):
    refresh_doc(reg, FEE, page(CLAUSE), now=at(23))
    assert isinstance(k4.close_rule_diff(reg, f"{FEE}:DOC", "valedia_trader.rules.fees-v2:main", at(25)), int)


def test_a_fact_known_after_as_of_never_shows(reg):
    k4.refresh(reg, KR, http_get=Fake({KR_URL: FEED}), now=NOW)
    refresh_doc(reg, FEE, page(CLAUSE), now=at(24))
    just_before = "2026-11-23T17:59:59Z"
    assert set(current(reg, just_before)) == {key(KR, "2026-11-09", A_TITLE), key(KR, "2026-11-20", B_TITLE)}
    assert k4.pending_rule_diffs(reg, just_before) == []
    assert len(k4.pending_rule_diffs(reg, NOW_S)) == 1                 # the feed's baseline flag, known at the fetch time
    assert len(k4.pending_rule_diffs(reg, "2026-11-24T17:59:59Z")) == 1
    assert len(k4.pending_rule_diffs(reg, "2026-11-24T18:00:00Z")) == 2
    assert f"{FEE}:DOC" not in current(reg, "2026-11-24T17:59:59Z") and f"{FEE}:DOC" in current(reg, "2026-11-24T18:00:00Z")


def test_pending_rule_diffs_is_a_pure_read_and_checks_its_input(reg):
    refresh_doc(reg, FEE, page(CLAUSE))
    before = n_rows(reg)
    assert len(k4.pending_rule_diffs(reg, FAR)) == 1 and n_rows(reg) == before
    for bad in ("2026-11-23", None, 5, "yesterday"):
        with pytest.raises(StoreError):
            k4.pending_rule_diffs(reg, bad)


BROKEN_FLAGS = {
    "no keys": '{"status":"OPEN"}',
    "not an object": "[]",
    "entity key differs": '{"entity_key":"x","old_fact_version_id":null,"new_fact_version_id":1,"status":"OPEN"}',
    "unknown status": '{"entity_key":"%s:DOC","old_fact_version_id":null,"new_fact_version_id":1,"status":"MAYBE"}' % RULE,
    "closed with no module": '{"entity_key":"%s:DOC","old_fact_version_id":null,"new_fact_version_id":1,"status":"CLOSED"}' % RULE,
    "entries is text": '{"entity_key":"%s:DOC","old_fact_version_id":null,"new_fact_version_id":1,"status":"OPEN","entries":"6"}' % RULE,
    "id is text": '{"entity_key":"%s:DOC","old_fact_version_id":null,"new_fact_version_id":"1","status":"OPEN"}' % RULE,
    "old id is a bool": '{"entity_key":"%s:DOC","old_fact_version_id":true,"new_fact_version_id":1,"status":"OPEN"}' % RULE,
}


def put_flag(conn, body, when):
    add_fact(conn, domain="K4_VENUE", entity_key=f"PENDING_RULE_DIFF:{RULE}:DOC", source_id=RULE,
             raw_sha256=hashlib.sha256(body.encode()).hexdigest(), payload_json=body,
             effective_from_utc=stamp(when), fetched_at_utc=stamp(when))


@pytest.mark.parametrize("name", sorted(BROKEN_FLAGS))
def test_pending_rule_diffs_refuses_a_flag_with_a_broken_payload(reg, name):
    put_flag(reg, BROKEN_FLAGS[name], at(24))
    with pytest.raises(k4.K4Error):
        k4.pending_rule_diffs(reg, FAR)
    assert k4.pending_rule_diffs(reg, stamp(at(24) - timedelta(seconds=1))) == []          # before it, nothing to refuse
    good = json.dumps({"entity_key": f"{RULE}:DOC", "old_fact_version_id": None, "new_fact_version_id": 1,
                       "status": "OPEN"}, sort_keys=True)
    put_flag(reg, good, at(26))                                                              # a later good flag puts it right
    assert [d["entity_key"] for d in k4.pending_rule_diffs(reg, FAR)] == [f"{RULE}:DOC"]


# ---- the command line

def run(args, capsys):
    code = k4._main(list(args))
    out, err = capsys.readouterr()
    return code, out, err


@pytest.fixture
def cli(tmp_path, ready, monkeypatch):
    clock = {"now": at(24)}
    monkeypatch.setattr(k4, "_clock", lambda: clock["now"])
    return str(tmp_path / "cli.db"), clock


def test_cli_register_refresh_pending_close_end_to_end(cli, capsys, monkeypatch):
    db, clock = cli
    code, out, _ = run(["register", "--store", db, "--only", f"{FEE},{KR}", "--terms-read-at", T0], capsys)
    assert code == 0 and json.loads(out) == {"registered": [FEE, KR]}
    monkeypatch.setattr(k4, "_urllib_get", Fake({DOC_URL[FEE]: page(CLAUSE), KR_URL: FEED}))
    code, out, _ = run(["refresh", "--store", db, "--source", FEE], capsys)
    assert code == 1 and json.loads(out) == {"stored": 1, "unchanged": 0, "error": 0, "diffs_opened": 1, "open_diffs": 1}
    code, out, _ = run(["pending", "--store", db], capsys)
    got = json.loads(out)
    assert code == 1 and [d["entity_key"] for d in got["open"]] == [f"{FEE}:DOC"]
    code, out, _ = run(["pending", "--store", db, "--as-of", "2026-11-23T00:00:00Z"], capsys)
    assert code == 0 and json.loads(out)["open"] == []                    # before the flag existed
    clock["now"] = at(25)
    code, out, _ = run(["close", "--store", db, "--entity", f"{FEE}:DOC", "--rule-module", "rules.fees"], capsys)
    assert code == 0 and json.loads(out)["open_diffs"] == 0
    code, out, _ = run(["pending", "--store", db], capsys)
    assert code == 0 and json.loads(out)["open"] == []
    code, out, _ = run(["refresh", "--store", db, "--source", FEE], capsys)
    assert code == 0 and json.loads(out)["unchanged"] == 1               # nothing open, no errors: exit 0


def test_cli_close_exits_one_while_other_diffs_remain(cli, capsys, monkeypatch):
    db, clock = cli
    run(["register", "--store", db, "--only", f"{FEE},{RULE}", "--terms-read-at", T0], capsys)
    monkeypatch.setattr(k4, "_urllib_get", Fake({DOC_URL[FEE]: page(CLAUSE), DOC_URL[RULE]: page(CLAUSE)}))
    run(["refresh", "--store", db, "--source", FEE], capsys)
    run(["refresh", "--store", db, "--source", RULE], capsys)
    clock["now"] = at(25)
    code, out, _ = run(["close", "--store", db, "--entity", f"{FEE}:DOC", "--rule-module", "rules.fees"], capsys)
    assert code == 1 and json.loads(out)["open_diffs"] == 1


def test_cli_refresh_exits_one_when_a_body_is_refused(cli, capsys, monkeypatch):
    db, _ = cli
    run(["register", "--store", db, "--only", KR, "--terms-read-at", T0], capsys)
    monkeypatch.setattr(k4, "_urllib_get", Fake({KR_URL: b"<html>gateway error</html>"}))
    code, out, _ = run(["refresh", "--store", db, "--source", KR], capsys)
    assert code == 1 and json.loads(out)["error"] == 1 and json.loads(out)["open_diffs"] == 0


def test_cli_exit_two_when_the_command_failed(cli, tmp_path, capsys):
    db, _ = cli
    assert run(["register", "--store", db, "--only", "no_such", "--terms-read-at", T0], capsys)[0] == 2
    assert run(["register", "--store", db, "--only", FEE, "--terms-read-at", "yesterday"], capsys)[0] == 2
    assert run(["refresh", "--store", db, "--source", "no_such"], capsys)[0] == 2
    assert run(["pending", "--store", db, "--as-of", "yesterday"], capsys)[0] == 2
    assert run(["close", "--store", db, "--entity", "nope:DOC", "--rule-module", "rules.x"], capsys)[0] == 2
    bad = tmp_path / "notastore.db"
    bad.write_bytes(b"this is not a database at all" * 100)
    assert run(["pending", "--store", str(bad)], capsys)[0] == 2
    _, _, err = run(["close", "--store", db, "--entity", "nope:DOC", "--rule-module", "rules.x"], capsys)
    assert err.startswith("error:")


def test_cli_register_with_the_shipped_unfilled_sources_exits_two(tmp_path, capsys):
    assert run(["register", "--store", str(tmp_path / "x.db"), "--only", KR, "--terms-read-at", T0], capsys)[0] == 2


def test_the_cli_exits_zero_with_nothing_open_and_nothing_wrong(cli, capsys):
    db, _ = cli
    code, out, _ = run(["pending", "--store", db], capsys)
    assert code == 0 and json.loads(out)["open"] == []


# ---- round 4: the lost diff, mentioned tags in a feed, entries that appear late

def test_an_edit_whose_flag_write_failed_is_still_flagged_when_the_flip_comes_later(reg, monkeypatch):
    plan = rss(rss_item("x", "Wed, 25 Nov 2026 15:00:00 GMT", "x"))
    edited = rss(rss_item("x", "Wed, 25 Nov 2026 15:00:00 GMT", "EDITED"))
    k4.refresh(reg, KR, http_get=Fake({KR_URL: plan}), now=NOW)
    settle(reg, KR)                                                  # the baseline is closed
    real = k4.store.add_fact

    def no_flags(conn, **f):
        if f["entity_key"].startswith("PENDING_RULE_DIFF:"):
            raise StoreError("simulated refusal")
        return real(conn, **f)
    monkeypatch.setattr(k4.store, "add_fact", no_flags)
    got = k4.refresh(reg, KR, http_get=Fake({KR_URL: edited}), now=at(24))
    assert got["stored"] == 1 and got["error"] == 1 and got["diffs_opened"] == 0      # edit stored, its flag was lost
    monkeypatch.setattr(k4.store, "add_fact", real)
    assert k4.pending_rule_diffs(reg, FAR) == []
    got = k4.refresh(reg, KR, http_get=Fake({KR_URL: edited}), now=at(27))              # it comes due: only the flip
    assert got["stored"] == 1 and got["diffs_opened"] == 1
    (d,) = k4.pending_rule_diffs(reg, FAR)
    assert d["entity_key"] == key(KR, "2026-11-25", "x")
    assert d["new_fact_version_id"] == current(reg)[key(KR, "2026-11-25", "x")]["fact_version_id"]
    assert k4.refresh(reg, KR, http_get=Fake({KR_URL: edited}), now=at(28))["diffs_opened"] == 0      # exactly one


def test_a_flip_after_a_flagged_edit_opens_nothing_more(reg):
    plan = rss(rss_item("x", "Wed, 25 Nov 2026 15:00:00 GMT", "x"))
    edited = rss(rss_item("x", "Wed, 25 Nov 2026 15:00:00 GMT", "EDITED"))
    k4.refresh(reg, KR, http_get=Fake({KR_URL: plan}), now=NOW)
    settle(reg, KR)
    assert k4.refresh(reg, KR, http_get=Fake({KR_URL: edited}), now=at(24))["diffs_opened"] == 1
    got = k4.refresh(reg, KR, http_get=Fake({KR_URL: edited}), now=at(27))
    assert got == {"stored": 1, "unchanged": 0, "error": 0, "diffs_opened": 0}
    assert len(k4.pending_rule_diffs(reg, FAR)) == 1
    assert k4.refresh(reg, KR, http_get=Fake({KR_URL: edited}), now=at(28))["diffs_opened"] == 0
    assert len(k4.pending_rule_diffs(reg, FAR)) == 1


MENTIONS = {
    "escaped script": "&lt;script&gt;x",
    "cdata head": "<![CDATA[use <head> here]]>",
    "escaped template": "&lt;template&gt; tag",
    "escaped style": "&lt;style&gt;.a{}",
}


@pytest.mark.parametrize("name", sorted(MENTIONS))
def test_a_tag_mentioned_in_a_feed_is_plain_text_and_the_feed_is_stored(reg, name):
    feed = rss(rss_item("Item " + name, desc=MENTIONS[name]), rss_item("Other item"))
    got = k4.refresh(reg, KR, http_get=Fake({KR_URL: feed}), now=NOW)
    assert got["error"] == 0 and got["stored"] == 2, name
    summary = payload(current(reg)[key(KR, "2026-11-09", "Item " + name)])["summary"]
    assert "<" not in summary and ">" not in summary
    assert summary == {"escaped script": "x", "cdata head": "use here", "escaped template": "tag",
                       "escaped style": ".a{}"}[name]


def test_a_tag_mentioned_in_a_feed_title_is_plain_text_too():
    facts = parsed(rss(rss_item("Using &lt;script&gt; tags", desc="d")))
    assert json.loads(list(facts.values())[0]["payload_json"])["title"] == "Using tags"


def test_a_closed_script_in_a_feed_description_is_still_dropped():
    f = list(parsed(rss(rss_item("Item", desc="a &lt;script&gt;secret()&lt;/script&gt; b"))).values())[0]
    assert json.loads(f["payload_json"])["summary"] == "a b"


def test_an_unclosed_script_on_a_page_is_still_refused_where_a_feed_would_keep_it(reg):
    bad = b"<p>rule</p><script>x"
    assert k4.refresh(reg, FEE, http_get=Fake({DOC_URL[FEE]: bad}), now=NOW)["error"] == 1
    assert k4.refresh(reg, WB, http_get=Fake({WB_URL: b"<h2>2026-11-18 T</h2><script>x"}), now=NOW)["error"] == 1
    assert n_rows(reg) == 0


def test_an_entry_that_appears_late_with_an_old_date_is_known_when_fetched(reg):
    def feed(*pubs):
        return rss(*[rss_item("Updates", p, desc=f"item {i}") for i, p in enumerate(pubs)])
    nov2, nov14 = "Mon, 02 Nov 2026 15:00:00 GMT", "Sat, 14 Nov 2026 15:00:00 GMT"
    day10, day15 = datetime(2026, 11, 10, 18, 0, tzinfo=timezone.utc), datetime(2026, 11, 15, 18, 0, tzinfo=timezone.utc)
    k4.refresh(reg, KR, http_get=Fake({KR_URL: feed(nov2, nov2)}), now=day10)               # the baseline
    first = current(reg)[key(KR, "2026-11-02", "Updates", 1)]
    assert first["known_at_basis"] == "PUBLISHED" and first["known_at_utc"] == "2026-11-03T04:59:59.999999Z"
    late = key(KR, "2026-11-02", "Updates", 3)
    got = k4.refresh(reg, KR, http_get=Fake({KR_URL: feed(nov2, nov2, nov2)}), now=day15)    # a third item, dated 11-02
    assert got["stored"] == 1
    row = current(reg)[late]
    assert row["known_at_basis"] == "FETCHED" and row["published_at_utc"] is None and row["known_at_utc"] == stamp(day15)
    assert late not in current(reg, "2026-11-14T23:59:59Z")                                   # invisible at 11-14
    assert late in current(reg, stamp(day15))
    assert key(KR, "2026-11-02", "Updates", 2) in current(reg, "2026-11-14T23:59:59Z")        # the baseline ones stay


def test_an_entry_dated_the_day_before_the_fetch_is_published_at_the_end_of_that_day(reg):
    feed = lambda *pubs: rss(*[rss_item(f"N{i}", p) for i, p in enumerate(pubs)])
    nov2, nov13, nov14 = "Mon, 02 Nov 2026 15:00:00 GMT", "Fri, 13 Nov 2026 15:00:00 GMT", "Sat, 14 Nov 2026 15:00:00 GMT"
    k4.refresh(reg, KR, http_get=Fake({KR_URL: feed(nov2)}), now=datetime(2026, 11, 10, 18, 0, tzinfo=timezone.utc))
    day15 = datetime(2026, 11, 15, 18, 0, tzinfo=timezone.utc)
    k4.refresh(reg, KR, http_get=Fake({KR_URL: rss(rss_item("N0", nov2), rss_item("Two days", nov13),
                                                    rss_item("One day", nov14))}), now=day15)
    one = current(reg)[key(KR, "2026-11-14", "One day")]
    assert one["known_at_basis"] == "PUBLISHED" and one["known_at_utc"] == "2026-11-15T04:59:59.999999Z"
    assert key(KR, "2026-11-14", "One day") not in current(reg, "2026-11-15T04:59:59.999998Z")
    assert key(KR, "2026-11-14", "One day") in current(reg, "2026-11-15T05:00:00Z")
    two = current(reg)[key(KR, "2026-11-13", "Two days")]                                      # two days back: too late to prove
    assert two["known_at_basis"] == "FETCHED" and two["known_at_utc"] == stamp(day15)


def test_the_baseline_read_keeps_the_published_rule_for_old_entries_even_when_it_is_repaired(reg):
    facts = k4.fetch(KR, http_get=Fake({KR_URL: FEED}), now=NOW)
    for f in facts[:2]:
        add_fact(reg, **f)                                       # an interrupted first read: two of the six stored
    k4.refresh(reg, KR, http_get=Fake({KR_URL: FEED}), now=at(30))
    rows = current(reg)
    assert rows[key(KR, "2026-11-09", A_TITLE)]["known_at_basis"] == "PUBLISHED"
    assert rows[key(KR, "2026-11-20", B_TITLE)]["known_at_basis"] == "PUBLISHED"
    assert [d["entity_key"] for d in k4.pending_rule_diffs(reg, FAR)] == [f"{KR}:BASELINE"]
