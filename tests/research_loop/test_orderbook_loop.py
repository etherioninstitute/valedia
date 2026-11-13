"""The order-book loop's collector: selection, snapshots, the model's state, the battery, decisions, health and
the record. Everything runs on a stand-in Kalshi, a stand-in model and a clock that moves only when told to."""
import http.client
import json
import sqlite3
import urllib.error
from collections import Counter
import urllib.parse
from datetime import datetime, timezone
from email.utils import formatdate
import inspect
import math

import pytest

from valedia_trader.core import legacy
from valedia_trader.research_loop import orderbook_loop as ol
from valedia_trader.venues import kalshi_data as kd

v3 = legacy.load("systemone_v3_candidate")
# rev 6: answers carry their Date header and the collector takes a monotonic clock. Detected rather than assumed so
# that the same tests can be run against a collector without the rule, to see them fail.
DATED = hasattr(kd, "urllib_get_dated")
MONO = "monotonic" in inspect.signature(ol.Collector).parameters


def make_collector(store, kalshi, model, *, clock, monotonic=None, **kw):
    if MONO:
        kw["monotonic"] = monotonic or clock.mono
    return ol.Collector(store, kalshi, model, clock=clock, **kw)


def main_kw(clock):
    return {"clock": clock, **({"monotonic": clock.mono} if MONO else {})}
UTC = timezone.utc
T0 = 1794549600            # 2026-11-13 06:00:00 UTC, on the hour
D0 = 1794528000            # 2026-11-13 00:00:00 UTC


def iso(ts):
    return datetime.fromtimestamp(ts, UTC).isoformat().replace("+00:00", "Z")


class Clock:
    """Epoch seconds that move when the code sleeps or a stand-in says time passed, and by a microsecond at each
    reading, as a real clock does between two readings with work in between (rev 3: the state uses only what
    finished strictly before the decision time)."""
    def __init__(self, t=T0):
        self.t = float(t)

    def __call__(self):
        self.t += 1e-6
        return self.t

    def sleep(self, s):
        self.t += s

    def mono(self):
        """The monotonic clock beside it: it moves with this clock, never stepping (rev 6)."""
        return self.t


# ---------------------------------------------------------------- stand-in Kalshi

def levels_api(levels):
    """Best-first (price, size) levels as Kalshi sends them: ascending dollar strings."""
    return [["%.4f" % p, "%.2f" % s] for p, s in sorted(levels)]


def book(bid, ask, bid_size=10.0, ask_size=10.0):
    """A one-level-a-side book (best first) with a yes bid and a yes ask in dollars."""
    yes = [(bid, bid_size)] if bid is not None else []
    no = [(round(1 - ask, 4), ask_size)] if ask is not None else []
    return {"yes": yes, "no": no}


class World:
    """Kalshi's public API as far as the collector uses it. Records every URL; `fail` makes every call a 404."""
    def __init__(self, clock):
        self.clock, self.markets, self.books, self.tape, self.fees = clock, [], {}, {}, {}
        self.urls, self.fail, self.fail_paths = [], False, set()
        self.raw_books, self.markets_delay = {}, 0.0        # a book sent as given; seconds a selection's read takes
        self.page_size, self.book_delay = None, {}          # markets a page (None: one page); seconds a book read takes
        self.request_delay = 0.0                            # seconds every request takes (rev 4)
        self.trade_page = None                              # trades a page (None: one page)
        self.on_request, self.date_offset = None, 0.0       # rev 6: a hook inside each request; a Date that is off
        self.fail_status = 404                              # rev 7: the status of a failed answer
        self.date_bad, self.date_bad_left = None, None      # rev 7: a Date "missing", "malformed" or "zoneless",
                                                            # on the next `date_bad_left` answers (None: on all)
        self.fail_trades = set()                            # rev 10: tickers whose trade reads fail (books answer)
        self._sorted = {}                                   # ticker: (tape length, created times, rows by created)

    def __call__(self, url):
        """The answer and, as every Kalshi answer carries, its HTTP Date header (the true time, to the second)."""
        body = self._answer(url)
        date = formatdate(int(self.clock.t + self.date_offset), usegmt=True)
        if self.date_bad and self.date_bad_left != 0:
            if self.date_bad_left is not None:
                self.date_bad_left -= 1
            date = {"missing": None, "malformed": "yesterday, around noon",
                    "zoneless": date.rsplit(" ", 1)[0]}[self.date_bad]
        return (body, date) if DATED else body

    def _answer(self, url):
        self.urls.append(url)
        self.clock.t += self.request_delay
        parts = urllib.parse.urlsplit(url)
        assert url.startswith(kd.BASE_URL + "/") and parts.scheme == "https"
        path, q = parts.path.split("/trade-api/v2", 1)[1], dict(urllib.parse.parse_qsl(parts.query))
        if self.on_request is not None:
            self.on_request(path)
        if self.fail or path in self.fail_paths:
            raise kd.HttpError(self.fail_status, url)
        if path == "/markets":
            size = self.page_size or max(len(self.markets), 1)
            pages = max(1, -(-len(self.markets) // size))
            self.clock.t += self.markets_delay / pages
            at = int(q.get("cursor") or 0)
            nxt = str(at + size) if at + size < len(self.markets) else ""
            return json.dumps({"markets": self.markets[at:at + size], "cursor": nxt}).encode()
        if path == "/markets/trades" and q.get("ticker") in self.fail_trades:
            raise kd.HttpError(self.fail_status, url)
        if path == "/markets/trades":
            if "ticker" in q:                               # rev 10: only the rows from min_ts on (the same answer)
                import bisect
                raw = self.tape.get(q["ticker"], [])
                key = self._sorted.get(q["ticker"])
                if key is None or key[0] != len(raw):
                    rows_by = sorted(raw, key=lambda r: kd.iso_to_ts(r["created_time"]))
                    key = (len(raw), [kd.iso_to_ts(r["created_time"]) for r in rows_by], rows_by)
                    self._sorted[q["ticker"]] = key
                tape = key[2][bisect.bisect_left(key[1], int(q.get("min_ts", 0))):]
            else:
                tape = [r for v in self.tape.values() for r in v]
            rows = [{k: v for k, v in r.items() if k != "_on_tape"} for r in tape
                    if int(q.get("min_ts", 0)) <= kd.iso_to_ts(r["created_time"]) <= self.clock.t
                    and r.get("_on_tape", 0) <= self.clock.t]
            rows.sort(key=lambda r: r["created_time"], reverse=True)
            if "ticker" not in q:                           # the exchange's newest trades (rev 5)
                return json.dumps({"trades": rows[:int(q.get("limit", 100))], "cursor": ""}).encode()
            if self.trade_page:                             # rev 5: pages of `trade_page` trades
                at = int(q.get("cursor") or 0)
                nxt = str(at + self.trade_page) if at + self.trade_page < len(rows) else ""
                return json.dumps({"trades": rows[at:at + self.trade_page], "cursor": nxt}).encode()
            return json.dumps({"trades": rows, "cursor": ""}).encode()
        if path.endswith("/orderbook"):
            self.clock.t += self.book_delay.get(path.split("/")[2], 0.0)
            if path.split("/")[2] in self.raw_books:
                return json.dumps({"orderbook_fp": self.raw_books[path.split("/")[2]]}).encode()
            b = self.books.get(path.split("/")[2], {"yes": [], "no": []})
            return json.dumps({"orderbook_fp": {"yes_dollars": levels_api(b["yes"]),
                                                "no_dollars": levels_api(b["no"])}}).encode()
        if path == "/exchange/status":
            return b'{"exchange_active": true, "trading_active": true}'
        if path.startswith("/series/"):
            s = path.split("/")[2]
            return json.dumps({"series": {"ticker": s, **self.fees.get(s, {"fee_type": "quadratic",
                                                                          "fee_multiplier": 1})}}).encode()
        raise kd.HttpError(404, url)

    def trade(self, ticker, n, ts, side="yes", count=1.0, lag=0.0):
        """A trade made at ts that reaches the tape `lag` seconds later."""
        self.tape.setdefault(ticker, []).append(
            {"trade_id": "%s-%d" % (ticker, n), "ticker": ticker, "created_time": iso(ts), "yes_price_dollars": "0.4800",
             "no_price_dollars": "0.5200", "count_fp": "%.2f" % count, "taker_side": side, "is_block_trade": False,
             "_on_tape": ts + lag})


def mkt(ticker, vol=1000.0, close=T0 + 2 * 86400, bid=0.40, ask=0.45, structure="linear_cent", **extra):
    row = {"ticker": ticker, "event_ticker": ticker.rsplit("-", 1)[0], "title": "title of " + ticker, "status": "active",
           "price_level_structure": structure, "volume_24h_fp": "%.2f" % vol, "close_time": iso(close),
           "yes_bid_dollars": "%.4f" % bid, "yes_ask_dollars": "%.4f" % ask}
    row.update(extra)
    return row


# ---------------------------------------------------------------- stand-in model

def valid_answer(questions=None):
    questions = questions or ol.BATTERY
    out = {}
    for name, q in questions.items():
        if q["type"] == "noul":
            out[name] = {"type": "noul", "noul": 0.4}
        elif q["type"] == "choice":
            keys = list(q["criteria"])
            probs = dict(zip(keys, [0.2, 0.3, 0.5]))
            out[name] = {"type": "choice", "choice": keys[2], "probabilities": probs, "confidence": 0.4}
        else:
            probs = {str(i): p for i, p in enumerate([0.1, 0.2, 0.3, 0.4])}
            out[name] = {"type": "score", "score": sum(i * p for i, p in enumerate(probs.values())),
                         "legend": {str(i): t for i, t in enumerate(q["criteria"])}, "probabilities": probs,
                         "confidence": 0.3}
    return {"model": v3.TAG, "answers": out, "usage": {"input_tokens": 900, "output_tokens": 0},
            "prompt_eval_cached_count": 0}


class Model:
    """Ollama at 127.0.0.1:11434 as the collector uses it: tags, version, and the typed endpoint."""
    def __init__(self, clock, latency=0.8, digest=v3.MANIFEST_SHA256, body=None, down=False, post_down=False,
                 exc=None):
        self.clock, self.latency, self.digest, self.body, self.down, self.post_down = clock, latency, digest, body, down, post_down
        self.exc, self.posts, self.urls = exc, [], []
        self.on_post = None                                 # rev 6: a hook inside each call

    def __call__(self, url, data, timeout):
        self.urls.append(url)
        assert url.startswith("http://127.0.0.1:11434/")
        if self.down:
            raise ol.ModelUnavailable("connection refused")
        if url.endswith("/api/tags"):
            return json.dumps({"models": [{"name": v3.TAG, "digest": "sha256:" + self.digest}]}).encode()
        if url.endswith("/api/version"):
            return b'{"version": "0.40.16"}'
        assert url.endswith("/v1/systemone") and data is not None and timeout == ol.MODEL_LIMIT_S
        self.posts.append(data)
        if self.on_post is not None:
            self.on_post()
        if self.post_down:
            raise ol.ModelUnavailable("connection refused")
        if self.exc is not None:
            raise self.exc
        if self.latency > timeout:                        # the socket gives up at the limit, as a real one does
            self.clock.t += timeout
            raise TimeoutError("timed out")
        self.clock.t += self.latency
        body = self.body if self.body is not None else valid_answer()
        return body if isinstance(body, bytes) else json.dumps(body).encode()


def rig(tmp_path, clock=None, **model_kw):
    clock = clock or Clock()
    world = World(clock)
    model = Model(clock, **model_kw)
    store = ol.Store(tmp_path / "loop.sqlite")
    kalshi = kd.KalshiData(fetch=world, sleep=clock.sleep, clock=clock, tries=1)
    col = make_collector(store, kalshi, ol.ModelClient(transport=model, clock=clock), clock=clock)
    return col, world, model, store, clock


# ================================================================= selection

def test_every_clause_of_the_selection_rule_is_applied_and_its_reason_reported():
    now = T0
    markets = [mkt("A-E-OK", vol=5000), mkt("B-E-SUB", vol=4900, structure="tapered_deci_cent"),
               mkt("C-E-LOWBID", vol=4800, bid=0.08, ask=0.12), mkt("D-E-HIGHASK", vol=4700, bid=0.86, ask=0.92),
               mkt("E-E-TIGHT", vol=4600, bid=0.40, ask=0.42), mkt("F-E-WIDE", vol=4500, bid=0.40, ask=0.56),
               mkt("G-E-THIN", vol=499), mkt("H-E-SOON", vol=4400, close=now + 3 * 3600 - 1),
               mkt("I-E-FAR", vol=4300, close=now + 30 * 86400 + 1), mkt("J-E-ONESIDE", vol=4200),
               mkt("K-E-CACHED", vol=4100, bid=0.02, ask=0.03), mkt("L-E-SHUT", vol=4000, status="closed"),
               mkt("M-E-AGAIN", vol=3900), mkt("N-E-NOBOOK", vol=3800),
               # the edges are inside: bid 10, ask 90, spreads 3 and 15, volume 500, 3 hours and 30 days
               mkt("P-E-EDGE1", vol=500, bid=0.10, ask=0.13), mkt("Q-E-EDGE2", vol=600, bid=0.75, ask=0.90),
               mkt("R-E-EDGE3", vol=700, close=now + 3 * 3600), mkt("S-E-EDGE4", vol=800, close=now + 30 * 86400)]
    books = {"A-E-OK": book(0.40, 0.45), "C-E-LOWBID": book(0.09, 0.12), "D-E-HIGHASK": book(0.86, 0.91),
             "E-E-TIGHT": book(0.40, 0.42), "F-E-WIDE": book(0.40, 0.56), "J-E-ONESIDE": book(0.40, None),
             "M-E-AGAIN": book(0.40, 0.45), "N-E-NOBOOK": None, "P-E-EDGE1": book(0.10, 0.13), "Q-E-EDGE2": book(0.75, 0.90),
             "R-E-EDGE3": book(0.40, 0.55), "S-E-EDGE4": book(0.40, 0.43)}
    sel = ol.select_markets(markets, books, now)
    assert sel.need_book is None
    # most traded first; a market watched before may be chosen again (rev 3: that clause is not in the prediction)
    assert sel.chosen == ["A-E-OK", "M-E-AGAIN", "S-E-EDGE4", "R-E-EDGE3", "Q-E-EDGE2", "P-E-EDGE1"]
    r = sel.reasons
    assert "whole-cent" in r["B-E-SUB"]
    assert "best bid 9" in r["C-E-LOWBID"] and "under 10" in r["C-E-LOWBID"]
    assert "best ask 91" in r["D-E-HIGHASK"] and "over 90" in r["D-E-HIGHASK"]
    assert "spread 2" in r["E-E-TIGHT"] and "spread 16" in r["F-E-WIDE"]
    assert "499" in r["G-E-THIN"] and "500" in r["G-E-THIN"]
    assert "under 3 hours" in r["H-E-SOON"] and "more than 30 days" in r["I-E-FAR"]
    assert "one-sided" in r["J-E-ONESIDE"]
    assert "cached quote" in r["K-E-CACHED"]
    assert "not open" in r["L-E-SHUT"]
    assert "could not be read" in r["N-E-NOBOOK"]
    assert set(r) | set(sel.chosen) == {m["ticker"] for m in markets}                # every market has an answer
    assert "spread 5" in sel.why["A-E-OK"] and "5000" in sel.why["A-E-OK"]


def test_at_most_four_from_one_series_and_at_most_twenty_in_all():
    markets = [mkt("KXSER-E%d-M" % i, vol=9000 - i) for i in range(6)]
    markets += [mkt("Z%02d-E-M" % i, vol=5000 - i) for i in range(30)]
    books = {m["ticker"]: book(0.40, 0.45) for m in markets}
    sel = ol.select_markets(markets, books, T0)
    assert sel.chosen[:4] == ["KXSER-E%d-M" % i for i in range(4)]
    assert "four already chosen from series KXSER" in sel.reasons["KXSER-E4-M"]
    assert len(sel.chosen) == 20 and sel.chosen[4:] == ["Z%02d-E-M" % i for i in range(16)]
    assert "twenty already chosen" in sel.reasons["Z16-E-M"]
    # the markets being watched take their places and count toward their series
    watched = [mkt("KXSER-W1-M"), mkt("KXSER-W2-M"), mkt("KXSER-W3-M")]
    sel = ol.select_markets(markets, books, T0, watched=watched)
    assert sel.chosen[:1] == ["KXSER-E0-M"] and "four already" in sel.reasons["KXSER-E1-M"]
    assert len(sel.chosen) == 17


def test_books_are_asked_for_one_at_a_time_and_only_while_places_remain():
    markets = [mkt("KXSER-E%d-M" % i, vol=9000 - i) for i in range(6)] + [mkt("Y-E-M", vol=10)]
    asked = []

    def fetch(t):
        asked.append(t)
        return book(0.40, 0.45)
    sel = ol.resolve_selection(markets, fetch, lambda: T0, slots=3)
    assert sel.chosen == ["KXSER-E0-M", "KXSER-E1-M", "KXSER-E2-M"]
    assert asked == ["KXSER-E0-M", "KXSER-E1-M", "KXSER-E2-M"]       # nothing read once three were chosen
    first = ol.select_markets(markets, {}, T0, slots=3)
    assert first.need_book == "KXSER-E0-M" and first.chosen == []


def test_series_comes_from_the_event_ticker():
    assert ol.series_of(mkt("KXODIMATCH-26NOV122100INDNZ-IND")) == "KXODIMATCH"
    assert ol.series_of({"ticker": "A-B", "series_ticker": "SER"}) == "SER"


# ================================================================= snapshots and the book

def test_best_bid_and_ask_and_the_yes_ask_from_the_no_side(tmp_path):
    col, world, _, store, clock = rig(tmp_path)
    world.markets = [mkt("A-E-M")]
    world.books["A-E-M"] = book(0.40, 0.45)
    col.start()
    col.select()
    world.books["A-E-M"] = {"yes": [(0.47, 1126.49), (0.46, 31298.15)], "no": [(0.52, 113806.8), (0.51, 36618.31)]}
    clock.t = T0 + 60.5
    col.cycle()
    row = store.conn.execute("SELECT best_bid, best_bid_size, best_ask, best_ask_size, mid, spread, book_state, "
                             "yes_json, no_json, book_start_iso, book_start_ts, book_end_ts FROM snapshots").fetchone()
    assert row[:7] == (0.47, 1126.49, 0.48, 113806.8, 0.475, 0.01, "two_sided")
    assert json.loads(row[7]) == [[0.47, 1126.49], [0.46, 31298.15]] and json.loads(row[8])[0] == [0.52, 113806.8]
    assert row[9] == iso(row[10]) and row[10] >= T0 + 60.5 and row[11] >= row[10]
    assert "depth=10" in [u for u in world.urls if "/orderbook" in u][-1]


def test_an_empty_or_one_sided_book_is_stored_as_such():
    s = ol.book_summary({"yes": [], "no": []})
    assert s["book_state"] == "empty" and s["mid"] is None and s["spread"] is None
    s = ol.book_summary({"yes": [(0.30, 4.0)], "no": []})
    assert s["book_state"] == "one_sided_yes" and s["best_bid"] == 0.30 and s["best_ask"] is None and s["mid"] is None
    s = ol.book_summary({"yes": [], "no": [(0.30, 4.0)]})
    assert s["book_state"] == "one_sided_no" and s["best_ask"] == 0.70


def test_trades_are_kept_once_across_overlapping_reads(tmp_path):
    col, world, _, store, clock = rig(tmp_path)
    world.markets = [mkt("A-E-M")]
    world.books["A-E-M"] = book(0.40, 0.45)
    col.start()
    col.select()
    world.trade("A-E-M", 1, T0 + 10)
    world.trade("A-E-M", 2, T0 + 58)
    clock.t = T0 + 60.5
    col.cycle()
    world.trade("A-E-M", 3, T0 + 100)
    clock.t = T0 + 120.5
    col.cycle()                                    # reads again from 5 s before the last read: trade 2 comes back
    ids = [r[0] for r in store.conn.execute("SELECT trade_id FROM trades ORDER BY created_ts")]
    assert ids == ["A-E-M-1", "A-E-M-2", "A-E-M-3"]
    snaps = store.conn.execute("SELECT trades_min_ts, trades_start_ts, new_trades FROM snapshots ORDER BY id").fetchall()
    assert snaps[1][0] == int(snaps[0][1] - ol.TRADE_READBACK_S) and [s[2] for s in snaps] == [2, 1]
    tr = [u for u in world.urls if "/markets/trades" in u]
    assert "min_ts=%d" % int(snaps[0][1] - ol.TRADE_READBACK_S) in tr[1]


# ================================================================= the state

def hist_snap(ts, bid, ask, yes=None, no=None, end=None):
    """A stored snapshot as `state_at` reads it; `ts` is when its book read ended."""
    yes = yes if yes is not None else ([(bid, 10.0)] if bid is not None else [])
    no = no if no is not None else ([(round(1 - ask, 4), 10.0)] if ask is not None else [])
    return {"book_end_ts": ts, "end_ts": end if end is not None else ts + 0.5, "trades_ok": 1,
            "trades_min_ts": None, "trades_start_ts": ts + 0.1, "yes": yes, "no": no}


def hand_worked():
    """Sixty-one minutes of one market: snapshot k ends its book read at T0 + 60k + 2."""
    mids = {k: 40 for k in range(30)} | {k: 50 for k in range(30, 45)} | {k: 45 for k in range(45, 55)}
    mids |= {55: 46, 56: 46, 57: 46, 58: 46, 59: 47}
    snaps = []
    for k in range(60):
        m = mids[k] / 100
        snaps.append(hist_snap(T0 + 60 * k + 2, round(m - 0.01, 2), round(m + 0.01, 2)))
    snaps.append(hist_snap(T0 + 3602, 0.45, 0.50,
                           yes=[(0.45, 100.0), (0.44, 50.0), (0.43, 30.0), (0.41, 20.0), (0.38, 10.0)],
                           no=[(0.50, 80.0), (0.49, 40.0), (0.47, 25.0), (0.44, 5.0)]))
    for i, s in enumerate(snaps):                      # each read starts 5 s before the previous one began
        s["trades_min_ts"] = int(snaps[i - 1]["trades_start_ts"] - 5) if i else int(s["trades_start_ts"] - 60)
    t = T0 + 3610
    trades = [{"created_ts": T0 + 3400, "seen_ts": T0 + 3422.6, "count": 10.0, "taker_side": "yes"},
              {"created_ts": T0 + 3000, "seen_ts": T0 + 3002.6, "count": 4.0, "taker_side": "no"},
              {"created_ts": T0 + 2800, "seen_ts": T0 + 2822.6, "count": 6.0, "taker_side": "yes"},
              {"created_ts": T0 + 2600, "seen_ts": T0 + 2642.6, "count": 100.0, "taker_side": "no"},
              {"created_ts": T0 + 3605, "seen_ts": t + 40, "count": 50.0, "taker_side": "no"},     # seen after t
              {"created_ts": t + 5, "seen_ts": t + 40, "count": 70.0, "taker_side": "no"}]          # after t
    return snaps, trades, t, t + 7220


def test_the_state_against_a_hand_worked_book_and_tape():
    snaps, trades, t, close = hand_worked()
    s = ol.state_at(snaps, trades, t, close)
    assert list(s) == list(ol.STATE_KEYS)
    assert s == {"mid_cents": 47.5, "spread_cents": 5.0, "bid_size_top": 100, "ask_size_top": 80,
                 "bid_depth_5c": 200, "ask_depth_5c": 145,        # 0.38 and the no bid at 0.44 lie beyond 5 cents
                 "imbalance_top": 0.56, "imbalance_10_levels": 0.58,             # 100/180 and 210/360
                 "microprice_minus_mid_cents": 0.28,                             # (0.45*80 + 0.50*100)/180 - 0.475
                 "mid_change_1m_cents": 0.5, "mid_change_5m_cents": 1.5, "mid_change_15m_cents": 2.5,
                 "mid_change_30m_cents": -2.5, "mid_change_60m_cents": 7.5,
                 "trades_5m": 1, "trades_15m": 3, "volume_5m": 10, "volume_15m": 20,
                 "yes_taker_share_5m": 1.0, "yes_taker_share_15m": 0.8,          # 16 of 20 contracts
                 "max_1m_mid_move_30m_cents": 5.0,                                # 50 -> 45; the 40 -> 50 is older
                 "spread_vs_60m_median": 2.5, "minutes_to_close": 120}
    text = json.dumps(s)
    assert len(text) < 1000 and "T0" not in text                                   # well under 400 tokens


def test_a_number_that_cannot_be_computed_yet_is_null():
    snaps, trades, t, close = hand_worked()
    recent = [dict(x) for x in snaps[-11:]]                    # ten minutes of history only
    recent[0]["trades_min_ts"] = int(recent[0]["trades_start_ts"] - 60)
    s = ol.state_at(recent, trades, t, close)
    assert s["mid_change_1m_cents"] == 0.5 and s["mid_change_5m_cents"] == 1.5 and s["trades_5m"] == 1
    for k in ("mid_change_15m_cents", "mid_change_30m_cents", "mid_change_60m_cents", "max_1m_mid_move_30m_cents",
              "spread_vs_60m_median", "trades_15m", "volume_15m", "yes_taker_share_15m"):
        assert s[k] is None, k
    # a gap in the trade reads makes the windows it touches unknown
    broken = [dict(x) for x in snaps]
    broken[-2]["trades_ok"] = 0
    s = ol.state_at(broken, trades, t, close)
    assert s["trades_5m"] is None and s["trades_15m"] is None
    # a one-sided book now: nothing that needs a mid
    onesided = snaps[:-1] + [hist_snap(T0 + 3602, 0.45, None)]
    onesided[-1]["trades_min_ts"] = snaps[-1]["trades_min_ts"]
    s = ol.state_at(onesided, trades, t, close)
    assert s["mid_cents"] is None and s["spread_cents"] is None and s["mid_change_1m_cents"] is None
    assert s["bid_size_top"] == 10 and s["ask_size_top"] is None and s["trades_5m"] == 1
    # no volume: no share
    s = ol.state_at(snaps, [], t, close)
    assert s["trades_5m"] == 0 and s["volume_5m"] == 0 and s["yes_taker_share_5m"] is None


def test_the_state_ignores_everything_after_the_decision_time():
    snaps, trades, t, close = hand_worked()
    later = [hist_snap(t + 30, 0.10, 0.90), hist_snap(t - 20, 0.20, 0.80, end=t + 3)]   # the last ends after t
    assert ol.state_at(snaps + later, trades, t, close) == ol.state_at(snaps, trades, t, close)
    assert ol.state_at(snaps, trades[:4], t, close) == ol.state_at(snaps, trades, t, close)


# ================================================================= the battery

def test_the_battery_is_the_six_questions_of_the_prediction_in_order_and_frozen():
    assert list(ol.BATTERY) == ["up_15m", "down_15m", "move_15m", "toxic_15m", "regime", "quote_env"]
    assert [q["type"] for q in ol.BATTERY.values()] == ["noul", "noul", "noul", "noul", "choice", "score"]
    assert list(ol.BATTERY["regime"]["criteria"]) == ["trending", "mean_reverting", "quiet"]
    assert len(ol.BATTERY["quote_env"]["criteria"]) == 4                          # a rating 0 to 3
    for q in ol.BATTERY.values():
        assert "cents" in q["instructions"] and "contracts" in q["instructions"] and "minutes" in q["instructions"]
    v3.make_request({"x": 1}, ol.BATTERY, tag=v3.TAG)                             # a valid typed request
    assert ol.BATTERY_ID == ol.battery_id(ol.BATTERY)
    assert ol.BATTERY_ID == "3e07adb11ae25c23b334bb9f9576208854686cdb1177d0728026d2d9bd927730"


def test_the_model_request_is_the_typed_request_of_the_state_and_the_battery():
    snaps, trades, t, close = hand_worked()
    state = ol.state_at(snaps, trades, t, close)
    body = ol.request_bytes(state)
    assert json.loads(body) == v3.make_request(state, ol.BATTERY, tag=v3.TAG)
    assert list(json.loads(body)["questions"]) == list(ol.BATTERY)


# ================================================================= decisions

def run_minutes(col, clock, first, last, base=T0):
    for i in range(first, last + 1):
        clock.t = base + 60 * i + 0.5
        col.cycle()


def started(tmp_path, n=1, **model_kw):
    col, world, model, store, clock = rig(tmp_path, **model_kw)
    world.markets = [mkt("%s-E-M" % chr(65 + i), vol=1000 + i) for i in range(n)]
    for m in world.markets:
        world.books[m["ticker"]] = book(0.40, 0.45)
    col.start()
    col.select()
    return col, world, model, store, clock


def decisions(store):
    cur = store.conn.execute("SELECT * FROM decisions ORDER BY id")
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def test_decisions_fall_on_the_five_minute_grid_after_thirty_minutes_of_snapshots(tmp_path):
    col, world, model, store, clock = started(tmp_path)
    run_minutes(col, clock, 1, 46)
    first_snap = store.conn.execute("SELECT MIN(book_end_ts) FROM snapshots").fetchone()[0]
    got = decisions(store)
    assert [d["slot_ts"] for d in got] == [T0 + 2100, T0 + 2400, T0 + 2700]     # 06:35, 06:40, 06:45
    assert T0 + 2100 - 1800 >= first_snap > T0 + 2100 - 1800 - 300            # 06:30 would have been too early
    assert all(d["slot_ts"] % 300 == 0 and 0 <= d["decision_ts"] - d["slot_ts"] < 60 for d in got)
    assert all(d["slot_iso"] == iso(d["slot_ts"]) and d["decision_iso"] == iso(d["decision_ts"]) for d in got)
    assert len(model.posts) == 3


def test_an_ok_decision_stores_everything_the_evaluation_needs(tmp_path):
    col, world, model, store, clock = started(tmp_path)
    run_minutes(col, clock, 1, 35)
    d = decisions(store)[0]
    assert d["status"] == "ok" and d["forced"] == 0 and d["battery_id"] == ol.BATTERY_ID
    assert d["model_tag"] == v3.TAG and d["model_digest"] == "sha256:" + v3.MANIFEST_SHA256
    assert d["latency_s"] == pytest.approx(0.8, abs=1e-3) and d["input_tokens"] == 900 and d["error"] is None
    state = json.loads(d["state_json"])
    assert model.posts[0] == ol.request_bytes(state)                          # the bytes sent are the state stored
    assert d["request_sha256"] == ol.sha256(model.posts[0])
    assert json.loads(d["raw_response"]) == valid_answer()
    assert json.loads(d["answers_json"]) == v3.parse_answers(valid_answer(), ol.BATTERY)
    assert state["mid_cents"] == 42.5 and state["spread_cents"] == 5.0 and state["mid_change_30m_cents"] == 0.0
    assert state["mid_change_60m_cents"] is None and state["spread_vs_60m_median"] is None   # only 34 minutes so far


@pytest.mark.parametrize("kw, status, posts, answered", [
    ({"latency": 12.0}, "late", 1, False),
    ({"down": True}, "model_unavailable", 0, False),
    ({"post_down": True}, "model_unavailable", 1, False),
    ({"digest": "0" * 64}, "tag_mismatch", 0, False),
    ({"body": b"not json"}, "error", 1, False),
    ({"body": {"model": "nimble-v3:9b-mxfp8", "answers": {}, "usage": {"input_tokens": 1, "output_tokens": 0}}},
     "error", 1, False),
])
def test_each_decision_status(tmp_path, kw, status, posts, answered):
    col, world, model, store, clock = started(tmp_path, **kw)
    run_minutes(col, clock, 1, 35)
    d = decisions(store)[0]
    assert d["status"] == status and len(model.posts) == posts
    assert (d["answers_json"] is not None) is answered
    assert d["state_json"] is not None and d["battery_id"] == ol.BATTERY_ID
    if status == "late":                                  # abandoned at the limit (rev 3), nothing stored
        assert d["latency_s"] == pytest.approx(10.0, abs=1e-3) and d["raw_response"] is None
    if status in ("model_unavailable", "error", "tag_mismatch"):
        assert d["error"]


def test_stale_data_makes_no_call(tmp_path):
    col, world, model, store, clock = started(tmp_path)
    run_minutes(col, clock, 1, 33)
    world.fail_paths.add("/markets/A-E-M/orderbook")
    run_minutes(col, clock, 34, 35)                       # the newest snapshot is now about 120 s old
    d = decisions(store)[0]
    assert d["status"] == "stale_data" and d["state_json"] is None and model.posts == []


def test_once_the_model_is_unreachable_the_rest_of_the_slot_is_marked_without_calls(tmp_path):
    col, world, model, store, clock = started(tmp_path, n=6, post_down=True)   # two markets share phase 0
    run_minutes(col, clock, 1, 35)
    at = [d for d in decisions(store) if d["slot_ts"] == T0 + 2100]
    before = len([d for d in decisions(store) if d["slot_ts"] < T0 + 2100])
    assert [d["status"] for d in at] == ["model_unavailable"] * 2
    assert len(model.posts) == before + 1                  # the second market of that minute was not called


def test_twenty_markets_never_make_more_than_five_model_calls_or_forty_kalshi_requests_in_a_minute(tmp_path):
    col, world, model, store, clock = started(tmp_path, n=20)
    run_minutes(col, clock, 1, 40)                        # every market is past its warm-up by minute 36
    per_minute = []
    for i in range(41, 101):                              # a simulated hour, minute by minute
        urls, posts = len(world.urls), len(model.posts)
        run_minutes(col, clock, i, i)
        per_minute.append((len(world.urls) - urls, len(model.posts) - posts))
    assert all(u == 40 for u, _ in per_minute)
    assert all(p == 4 for _, p in per_minute)             # 20 markets over 5 phases: 4 calls every minute
    assert sum(p for _, p in per_minute) == 20 * 60 // 5
    assert all("/orderbook" in u or "/markets/trades" in u for u in world.urls[-40:])


# ================================================================= health, limits and the record

def test_one_bad_market_does_not_stop_the_others(tmp_path):
    col, world, model, store, clock = started(tmp_path, n=3)
    world.fail_paths.add("/markets/B-E-M/orderbook")
    run_minutes(col, clock, 1, 2)
    got = store.conn.execute("SELECT ticker, COUNT(*) FROM snapshots GROUP BY ticker").fetchall()
    assert dict(got) == {"A-E-M": 2, "C-E-M": 2}
    errs = store.conn.execute("SELECT ticker FROM events WHERE kind = 'error'").fetchall()
    assert errs == [("B-E-M",), ("B-E-M",)]


def test_five_failed_requests_in_a_row_pause_the_loop_for_five_minutes(tmp_path):
    col, world, model, store, clock = started(tmp_path, n=6)
    run_minutes(col, clock, 1, 1)
    world.fail = True
    before = len(world.urls)
    run_minutes(col, clock, 2, 2)
    assert len(world.urls) - before == 5                  # the sixth market is not even tried
    pause = store.conn.execute("SELECT ts, detail_json FROM events WHERE kind = 'pause'").fetchall()
    assert len(pause) == 1 and json.loads(pause[0][1])["until_ts"] == pytest.approx(pause[0][0] + 300)
    world.fail = False
    before = len(world.urls)
    run_minutes(col, clock, 3, 7)                         # paused: no request but one clock check, a heartbeat
    assert [u.split("/v2")[1] for u in world.urls[before:]] == ["/exchange/status"]    # each minute (rev 7)
    before = len(world.urls)
    beats = store.conn.execute("SELECT paused FROM heartbeats ORDER BY id").fetchall()
    assert [b[0] for b in beats][-5:] == [1, 1, 1, 1, 1]
    run_minutes(col, clock, 8, 8)                         # five minutes after the fifth failure
    assert len(world.urls) - before == 12
    assert store.conn.execute("SELECT COUNT(*) FROM events WHERE kind = 'pause_end'").fetchone()[0] == 1


def test_a_heartbeat_every_minute_counts_requests_and_failures(tmp_path):
    col, world, model, store, clock = started(tmp_path, n=2)
    world.fail_paths.add("/markets/B-E-M/orderbook")
    run_minutes(col, clock, 1, 4)                         # the first minute also carries the selection's requests
    beats = store.conn.execute("SELECT watched, kalshi_requests, kalshi_failures, paused FROM heartbeats "
                               "ORDER BY id").fetchall()
    assert beats[-3:] == [(2, 3, 1, 0)] * 3


def test_a_market_is_dropped_after_twelve_hours_near_its_close_or_one_sided_for_thirty_minutes(tmp_path):
    col, world, model, store, clock = rig(tmp_path)
    world.markets = [mkt("A-E-M", vol=3000), mkt("B-E-M", vol=2000, close=T0 + 4 * 3600), mkt("C-E-M", vol=1000)]
    for m in world.markets:
        world.books[m["ticker"]] = book(0.40, 0.45)
    col.start()
    col.select()
    world.books["C-E-M"] = book(0.40, None)
    run_minutes(col, clock, 1, 31)
    run_minutes(col, clock, 211, 212)                     # 09:31, within 30 minutes of B's close at 10:00 (rev 7:
    run_minutes(col, clock, 720, 722)                     # after hours with no answer, a minute out of sync first)
    drops = store.conn.execute("SELECT ticker, reason FROM market_drops ORDER BY id").fetchall()
    assert [d[0] for d in drops] == ["C-E-M", "B-E-M", "A-E-M"]
    assert "one-sided" in drops[0][1] and "before it closes" in drops[1][1] and "12 hours" in drops[2][1]
    view = store.conn.execute("SELECT ticker, drop_reason FROM market_watch WHERE ticker = 'A-E-M' "
                              "ORDER BY watch_id").fetchall()
    assert view[0][1] == drops[2][1]


def test_the_selection_is_recorded_with_its_series_fees_and_reasons(tmp_path):
    col, world, model, store, clock = rig(tmp_path)
    world.markets = [mkt("KXNFLGAME-E-M", vol=3000), mkt("B-E-M", vol=2000, bid=0.02, ask=0.03)]
    world.books["KXNFLGAME-E-M"] = book(0.40, 0.45)
    world.fees["KXNFLGAME"] = {"fee_type": "quadratic_with_maker_fees", "fee_multiplier": 1}
    col.start()
    col.select()
    row = store.conn.execute("SELECT ticker, series, fee_type, fee_multiplier, why, market_json, book_json, "
                             "selected_iso, selected_ts, close_ts FROM markets").fetchone()
    assert row[:4] == ("KXNFLGAME-E-M", "KXNFLGAME", "quadratic_with_maker_fees", 1.0)
    assert "spread 5" in row[4] and json.loads(row[5])["ticker"] == "KXNFLGAME-E-M"
    assert json.loads(row[6])["yes"] == [[0.4, 10.0]] and row[7] == iso(row[8])
    assert row[9] == T0 + 2 * 86400
    sel = store.conn.execute("SELECT markets_seen, chosen, reasons_json FROM selections").fetchone()
    assert sel[:2] == (2, 1) and "B-E-M" in json.loads(sel[2])


def test_resuming_after_a_stop_keeps_watching_and_records_the_gap(tmp_path):
    col, world, model, store, clock = started(tmp_path, n=2)
    run_minutes(col, clock, 1, 10)
    last_beat = store.conn.execute("SELECT MAX(ts) FROM heartbeats").fetchone()[0]
    last_read = dict(store.conn.execute("SELECT ticker, MAX(trades_start_ts) FROM snapshots GROUP BY ticker"))
    store.close()
    clock.t = T0 + 30 * 60 + 0.5
    store2 = ol.Store(tmp_path / "loop.sqlite")
    col2 = make_collector(store2, kd.KalshiData(fetch=world, sleep=clock.sleep, clock=clock, tries=1),
                        ol.ModelClient(transport=model, clock=clock), clock=clock)
    col2.start()
    gap = store2.conn.execute("SELECT detail_json FROM events WHERE kind = 'gap'").fetchone()
    gap = json.loads(gap[0])
    assert gap["from_ts"] == last_beat and gap["to_ts"] == pytest.approx(T0 + 1800.5)
    assert sorted(w["ticker"] for w in col2.watched()) == ["A-E-M", "B-E-M"]
    col2.cycle()
    mins = store2.conn.execute("SELECT ticker, trades_min_ts FROM snapshots ORDER BY id DESC LIMIT 2").fetchall()
    assert all(m == int(last_read[t] - ol.TRADE_READBACK_S) for t, m in mins)   # read again from where it stopped
    assert store2.conn.execute("SELECT COUNT(*) FROM selections").fetchone()[0] == 1   # no new selection


def test_resuming_after_the_watch_ran_out_drops_it_and_selects_at_once(tmp_path):
    col, world, model, store, clock = started(tmp_path, n=1)
    run_minutes(col, clock, 1, 2)
    store.close()
    clock.t = T0 + 13 * 3600 + 60.5
    store2 = ol.Store(tmp_path / "loop.sqlite")
    col2 = make_collector(store2, kd.KalshiData(fetch=world, sleep=clock.sleep, clock=clock, tries=1),
                        ol.ModelClient(transport=model, clock=clock), clock=clock)
    col2.start()
    assert col2.watched() == []
    assert "12 hours" in store2.conn.execute("SELECT reason FROM market_drops").fetchone()[0]
    col2.cycle()                                                  # nothing watched: a selection at once
    assert [w["ticker"] for w in col2.watched()] == ["A-E-M"]     # the rule may choose it again (rev 3)


def test_nothing_in_the_record_can_be_updated_or_deleted(tmp_path):
    col, world, model, store, clock = started(tmp_path, n=1)
    world.trade("A-E-M", 1, T0 + 30)
    run_minutes(col, clock, 1, 35)
    col.drop(col.watched()[0], "dropped by the test")
    for table in ol.TABLES:
        assert store.conn.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0] > 0, table
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            store.conn.execute("UPDATE %s SET rowid = rowid" % table)
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            store.conn.execute("DELETE FROM %s" % table)


def test_a_clean_stop_finishes_the_current_write_and_records_itself(tmp_path):
    col, world, model, store, clock = started(tmp_path, n=3)
    real = world.__call__

    def stopping(url):
        if "/markets/B-E-M/orderbook" in url:
            col.stop.set()
        return real(url)
    col.kalshi.fetch = stopping
    clock.t = T0 + 60.5
    col.cycle()
    got = [r[0] for r in store.conn.execute("SELECT ticker FROM snapshots ORDER BY id")]
    assert got == ["C-E-M", "B-E-M"]          # watched most traded first; B's read was under way: finished, kept
    beats = []
    col.run(wait=lambda s: beats.append(s))
    kinds = [r[0] for r in store.conn.execute("SELECT kind FROM events ORDER BY id")]
    assert kinds[-1] == "stop" and beats == []


def test_sigterm_and_sigint_ask_for_a_clean_stop(tmp_path):
    import signal
    col, *_ = started(tmp_path)
    old = ol.stop_on_signals(col.stop)
    try:
        signal.raise_signal(signal.SIGTERM)
        assert col.stop.is_set()
        col.stop.clear()
        signal.raise_signal(signal.SIGINT)
        assert col.stop.is_set()
    finally:
        for sig, handler in old.items():
            signal.signal(sig, handler)


def test_run_cycles_once_a_minute_on_the_minute(tmp_path):
    col, world, model, store, clock = started(tmp_path)
    clock.t = T0 + 59.0
    waits = []

    def wait(s):
        waits.append(s)
        clock.t += s
        if len(waits) == 3:
            col.stop.set()                                # asked to stop while waiting: no further cycle
    col.run(wait=wait)
    beats = [r[0] for r in store.conn.execute("SELECT ts FROM heartbeats ORDER BY id")]
    assert [int(b // 60) for b in beats] == [int(T0 // 60) + 1, int(T0 // 60) + 2]
    assert waits[0] == pytest.approx(1.2)
    assert store.conn.execute("SELECT kind FROM events ORDER BY id DESC").fetchone()[0] == "stop"


# ================================================================= no look-ahead

def test_the_request_is_byte_identical_when_everything_after_the_decision_time_changes(tmp_path):
    t = T0 + 36 * 60 + 30

    def collect(sub, later_book, extra_trades):
        col, world, model, store, clock = started(tmp_path / sub, n=1)
        for i in range(1, 37):
            world.books["A-E-M"] = book(round(0.30 + 0.01 * (i % 7), 2), round(0.40 + 0.01 * (i % 5), 2), i, 2 * i)
            world.trade("A-E-M", i, T0 + 60 * i - 7, "yes" if i % 3 else "no", float(i))
            run_minutes(col, clock, i, i)
        world.books["A-E-M"] = later_book
        for n, ts, cnt in extra_trades:
            world.trade("A-E-M", n, ts, "no", cnt)
        run_minutes(col, clock, 37, 50)
        return col, store

    a, sa = collect("a", book(0.40, 0.45), [])
    b, sb = collect("b", book(0.12, 0.88, 999, 1), [(900, t - 10, 77.0), (901, t + 40, 55.0), (902, t + 400, 9.0)])
    w = sa.conn.execute("SELECT watch_id FROM markets").fetchone()[0]
    assert a.request_for(w, t) == b.request_for(w, t)
    assert json.loads(a.request_for(w, t))["state"]["mid_cents"] is not None
    late = T0 + 45 * 60 + 30
    assert a.request_for(w, late) != b.request_for(w, late)     # after the change the data differ, as they should


# ================================================================= status

def test_status_on_a_synthetic_three_day_record(tmp_path):
    store = ol.Store(tmp_path / "s.sqlite")
    ins = store.insert
    w = ins("markets", {"selection_id": 1, "ticker": "A-E-M", "series": "A", **ol.stamp("selected", D0),
                        **ol.stamp("close", D0 + 9 * 86400), "market_json": "{}", "book_json": "{}", "why": "x"})
    for k in range(31):                                         # rev 9: the unbroken 30 minutes a warm-up needs
        ins("snapshots", {"watch_id": w, "ticker": "A-E-M", **ol.stamp("book_start", D0 + 29.5 + 60 * k),
                          **ol.stamp("book_end", D0 + 30 + 60 * k), **ol.stamp("end", D0 + 30.5 + 60 * k),
                          "book_state": "two_sided", "trades_ok": 1, "new_trades": 0, "yes_json": "[]", "no_json": "[]"})
    ins("market_drops", {"watch_id": w, "ticker": "A-E-M", **ol.stamp("dropped", D0 + 2 * 86400 + 43200),
                         "reason": "watched 12 hours"})

    def decide(slot, status, latency=None, forced=0):
        ins("decisions", {"watch_id": w, "ticker": "A-E-M", **ol.stamp("slot", slot), **ol.stamp("decision", slot + 20),
                          "status": status, "forced": forced, "battery_id": ol.BATTERY_ID, "latency_s": latency})
    day1 = [D0 + 2100 + 300 * i for i in range(281)]
    for s in day1[20:]:
        decide(s, "ok", 1.0)
    day2 = [D0 + 86400 + 300 * i for i in range(288)]
    statuses = ["ok"] * 270 + ["late"] * 10 + ["stale_data"] * 5 + ["model_unavailable"] * 3
    for i, (s, st) in enumerate(zip(day2, statuses)):
        decide(s, st, 0.5 + (i % 10) / 10 if st == "ok" else (12.0 if st == "late" else None))
    for s in [D0 + 2 * 86400 + 300 * i for i in range(100)]:
        decide(s, "ok", 2.0)
    decide(D0 + 2 * 86400 + 30030, "ok", 0.1, forced=1)               # a smoke-test decision does not count
    for day, req, fail in ((0, 100, 1), (0, 100, 1), (1, 300, 0), (2, 50, 5)):
        ins("heartbeats", {**ol.stamp("", D0 + day * 86400 + 600 + req), "watched": 1, "kalshi_requests": req,
                           "kalshi_failures": fail, "model_calls": 0, "paused": 0})
    for k in (1, 2):
        ins("events", {**ol.stamp("", D0 + 86400 + k), "kind": "error", "ticker": "A-E-M", "detail_json": "{}"})
    rows = ol.day_status(store, now=D0 + 3 * 86400)
    assert [r["day"] for r in rows] == ["2026-11-13", "2026-11-14", "2026-11-15"]
    assert [r["expected"] for r in rows] == [281, 288, 144]
    assert [r["collected"] for r in rows] == [261, 270, 100]          # a late slot has no answers (rev 3)
    assert rows[0]["share"] == pytest.approx(261 / 281) and rows[1]["share"] == pytest.approx(270 / 288)
    assert rows[1]["statuses"] == {"ok": 270, "late": 10, "model_unavailable": 0 + 3, "tag_mismatch": 0,
                                   "stale_data": 5, "error": 0}
    # over answered calls only: a late slot (rev 3) has none, so the median of day 2 is that of its 270 ok calls
    assert rows[1]["latency_median"] == pytest.approx(0.95) and rows[1]["latency_p95"] == pytest.approx(1.4)
    assert rows[0]["latency_median"] == 1.0 and rows[2]["latency_p95"] == 2.0
    assert [(r["requests"], r["failures"], r["errors"]) for r in rows] == [(200, 2, 0), (300, 0, 2), (50, 5, 0)]
    text = ol.format_status(rows)
    assert "2026-11-14" in text and "93.8%" in text


# ================================================================= commands and hosts

def test_select_prints_what_it_would_watch_and_writes_nothing(tmp_path, capsys):
    clock = Clock()
    world = World(clock)
    world.markets = [mkt("A-E-M", vol=3000), mkt("B-E-M", vol=2000, bid=0.02, ask=0.03)]
    world.books["A-E-M"] = book(0.40, 0.45)
    kalshi = kd.KalshiData(fetch=world, sleep=clock.sleep, clock=clock, tries=1)
    db = tmp_path / "x.sqlite"
    assert ol.main(["select", "--db", str(db)], kalshi=kalshi, model=None, **main_kw(clock)) == 0
    out = capsys.readouterr().out
    assert "A-E-M" in out and "spread 5" in out and "cached quote" in out
    store = ol.Store(db)
    assert store.conn.execute("SELECT COUNT(*) FROM markets").fetchone()[0] == 0


def test_once_runs_one_full_cycle_and_marks_its_decisions_as_forced(tmp_path, capsys):
    clock = Clock()
    world = World(clock)
    world.markets = [mkt("A-E-M", vol=3000)]
    world.books["A-E-M"] = book(0.40, 0.45)
    model = Model(clock)
    kalshi = kd.KalshiData(fetch=world, sleep=clock.sleep, clock=clock, tries=1)
    db = tmp_path / "x.sqlite"
    assert ol.main(["once", "--db", str(db)], kalshi=kalshi, model=ol.ModelClient(transport=model, clock=clock),
                   **main_kw(clock)) == 0
    store = ol.Store(db)
    d = decisions(store)
    assert len(d) == 1 and d[0]["forced"] == 1 and d[0]["status"] == "ok"
    assert store.conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1
    assert "ok" in capsys.readouterr().out


def test_the_model_is_reached_only_on_the_loopback_address():
    with pytest.raises(ValueError):
        ol.ModelClient(base_url="http://example.com:11434")
    with pytest.raises(ValueError):
        ol.ModelClient(base_url="https://127.0.0.1.example.com")
    assert ol.ModelClient().base == "http://127.0.0.1:11434"
    assert ol.KALSHI_BASE == kd.BASE_URL == "https://api.elections.kalshi.com/trade-api/v2"


def test_a_late_minute_inside_a_run_is_recorded_as_a_gap(tmp_path):
    col, world, model, store, clock = started(tmp_path)
    run_minutes(col, clock, 1, 1)
    run_minutes(col, clock, 5, 5)                          # the machine slept through minutes 2 to 4
    gap = json.loads(store.conn.execute("SELECT detail_json FROM events WHERE kind = 'gap'").fetchone()[0])
    assert gap["to_ts"] == pytest.approx(T0 + 300.5) and 239 < gap["seconds"] < 240   # from the last heartbeat


# ================================================================= phases (rev 2)

def test_phases_go_to_the_least_used_lowest_first():
    assert ol.assign_phases([], 7) == [0, 1, 2, 3, 4, 0, 1]
    assert ol.assign_phases([0, 1, 1, 4], 3) == [2, 3, 0]
    assert ol.assign_phases([0, 1, 2, 3, 4] * 4, 1) == [0]


def test_phases_are_spread_evenly_when_selected_and_kept_across_a_restart(tmp_path):
    col, world, model, store, clock = started(tmp_path, n=7)
    got = {w["ticker"]: w["phase"] for w in col.watched()}
    by_volume = sorted(got, key=lambda t: -next(float(m["volume_24h_fp"]) for m in world.markets if m["ticker"] == t))
    assert [got[t] for t in by_volume] == [0, 1, 2, 3, 4, 0, 1]
    stored = dict(store.conn.execute("SELECT ticker, phase FROM markets"))
    assert stored == got
    for w in col.watched():                                # free phases 2 and 3 and one of the two 0s
        if w["phase"] in (2, 3) or w["ticker"] == by_volume[5]:
            col.drop(w, "dropped by the test")
            world.books[w["ticker"]] = book(0.40, None)    # rev 3 may choose a dropped market again: not these
    world.markets += [mkt("NEW%d-E-M" % i, vol=900 - i) for i in range(3)]
    for m in world.markets[-3:]:
        world.books[m["ticker"]] = book(0.40, 0.45)
    col.select()
    now = {w["ticker"]: w["phase"] for w in col.watched()}
    assert [now["NEW%d-E-M" % i] for i in range(3)] == [2, 3, 0]
    assert sorted(Counter(now.values()).values()) == [1, 1, 1, 2, 2]     # seven markets, as even as five phases allow
    store.close()
    clock.t += 3600
    store2 = ol.Store(tmp_path / "loop.sqlite")
    col2 = make_collector(store2, kd.KalshiData(fetch=world, sleep=clock.sleep, clock=clock, tries=1),
                        ol.ModelClient(transport=model, clock=clock), clock=clock)
    col2.start()
    assert {w["ticker"]: w["phase"] for w in col2.watched()} == now


def test_each_market_decides_exactly_every_five_minutes_on_its_own_phase(tmp_path):
    col, world, model, store, clock = started(tmp_path, n=5)
    run_minutes(col, clock, 1, 70)
    phase = {w["watch_id"]: w["phase"] for w in col.watched()}
    assert sorted(phase.values()) == [0, 1, 2, 3, 4]
    for wid, p in phase.items():
        slots = [d["slot_ts"] for d in decisions(store) if d["watch_id"] == wid]
        first_snap = store.conn.execute("SELECT MIN(book_end_ts) FROM snapshots WHERE watch_id = ?", (wid,)).fetchone()[0]
        assert all((s // 60) % 5 == p for s in slots)
        assert all(b - a == 300 for a, b in zip(slots, slots[1:]))
        assert slots[0] >= first_snap + 1800 > slots[0] - 300          # the first slot after the warm-up
        assert slots[-1] > T0 + 70 * 60 - 300
    minutes = Counter(d["slot_ts"] for d in decisions(store))
    assert max(minutes.values()) == 1                                    # five phases: one call a minute


def test_status_counts_each_markets_own_grid(tmp_path):
    store = ol.Store(tmp_path / "s.sqlite")
    ins = store.insert
    wa = ins("markets", {"selection_id": 1, "ticker": "A-E-M", "series": "A", **ol.stamp("selected", D0),
                         **ol.stamp("close", D0 + 9 * 86400), "market_json": "{}", "book_json": "{}", "why": "x",
                         "phase": 1})
    wb = ins("markets", {"selection_id": 1, "ticker": "B-E-M", "series": "B", **ol.stamp("selected", D0 + 500),
                         **ol.stamp("close", D0 + 9 * 86400), "market_json": "{}", "book_json": "{}", "why": "x",
                         "phase": 3})
    for w, tk, t0 in ((wa, "A-E-M", D0 + 30), (wb, "B-E-M", D0 + 600)):
        for t in (t0 + 60 * k for k in range(31)):              # rev 9: the unbroken 30 minutes a warm-up needs
            ins("snapshots", {"watch_id": w, "ticker": tk, **ol.stamp("book_start", t - 0.5),
                              **ol.stamp("book_end", t), **ol.stamp("end", t + 0.5), "book_state": "two_sided",
                              "trades_ok": 1, "new_trades": 0, "yes_json": "[]", "no_json": "[]"})
    ins("market_drops", {"watch_id": wb, "ticker": "B-E-M", **ol.stamp("dropped", D0 + 43200), "reason": "x"})
    # A: minutes 1, 6, 11, ... from 00:31 (the first after 00:30:30) to 23:56: 282 slots
    # B: minutes 3, 8, 13, ... from 00:43 (the first after 00:40:00) to 11:58: 136 slots
    for k in range(6, 288):
        ins("decisions", {"watch_id": wa, "ticker": "A-E-M", **ol.stamp("slot", D0 + 60 + 300 * k),
                          **ol.stamp("decision", D0 + 61 + 300 * k), "status": "ok", "forced": 0,
                          "battery_id": ol.BATTERY_ID, "latency_s": 1.0})
    rows = ol.day_status(store, now=D0 + 86400)
    assert [(r["day"], r["expected"], r["collected"]) for r in rows] == [("2026-11-13", 282 + 136, 282)]


# ================================================================= rev 3

def run_until(col, clock, end):
    """`Collector.run` with a wait that moves the clock; it stops once the clock has reached `end`."""
    def wait(s):
        clock.t += max(s, 0.0)
        if clock.t >= end:
            col.stop.set()
    col.run(wait=wait)


def watch_intervals(store, now):
    """(start, end) of every watch: from its market row (when its reads begin) to its drop, or now."""
    return store.conn.execute("SELECT m.selected_ts, COALESCE(d.dropped_ts, ?) FROM markets m LEFT JOIN market_drops d "
                              "ON d.watch_id = m.watch_id", (now,)).fetchall()


def watched_at(intervals, t):
    return sum(1 for a, b in intervals if a <= t < b)


# ---------------------------------------------------------------- item 1: reselection

@pytest.mark.parametrize("took", [5, 40, 120])
def test_every_reselection_leaves_twenty_watched_and_no_minute_goes_unwatched_over_72_hours(tmp_path, took):
    col, world, model, store, clock = rig(tmp_path)
    world.markets = [mkt("S%02d-E-M" % i, vol=5000 - i, close=T0 + 20 * 86400) for i in range(40)]
    for m in world.markets:
        world.books[m["ticker"]] = book(0.40, 0.45)
    world.page_size, world.markets_delay = 4, float(took)      # ten pages; reading them takes `took` seconds
    col.start()
    end = T0 + 72 * 3600
    run_until(col, clock, end)
    sels = store.conn.execute("SELECT started_ts, finished_ts FROM selections ORDER BY id").fetchall()
    assert [int(a // 60) - int(T0 // 60) for a, _ in sels] == [360 * k for k in range(12)]   # on the 6-hour grid
    assert all(b - a >= took for a, b in sels)
    iv = watch_intervals(store, end)
    assert all(watched_at(iv, fin) == 20 for _, fin in sels)   # twenty right after every reselection
    minutes = range(int(sels[0][1] // 60) + 1, int(end // 60))
    assert [m for m in minutes if watched_at(iv, 60 * m) == 0 or watched_at(iv, 60 * m + 59.9) == 0] == []
    assert min(watched_at(iv, 60 * m + 30) for m in minutes) == 20
    per_minute = Counter(int(b // 60) for (b,) in store.conn.execute("SELECT book_start_ts FROM snapshots "
                                                                    "WHERE tail = 0"))
    assert [m for m in minutes if per_minute[m] != 20] == []    # twenty reads every minute, selections included


def test_with_nothing_watched_a_selection_runs_at_once_off_the_grid(tmp_path):
    col, world, model, store, clock = rig(tmp_path)
    world.markets = [mkt("A-E-M", vol=3000, close=T0 + 5 * 3600)]
    world.books["A-E-M"] = book(0.40, 0.45)
    col.start()
    clock.t = T0 + 0.5
    col.cycle()                                                 # a new record: the first selection
    world.markets.append(mkt("B-E-M", vol=2000))
    world.books["B-E-M"] = book(0.40, None)                     # not choosable yet
    run_minutes(col, clock, 1, 270)                             # 10:30: A is dropped, 30 minutes before its close
    off = "SELECT COUNT(*) FROM events WHERE kind = 'selection_off_grid'"
    assert col.watched() == [] and store.conn.execute(off).fetchone()[0] == 1
    world.books["B-E-M"] = book(0.40, 0.45)
    run_minutes(col, clock, 271, 271)                           # still nothing watched: the next minute tries again
    assert [w["ticker"] for w in col.watched()] == ["B-E-M"] and store.conn.execute(off).fetchone()[0] == 2
    run_minutes(col, clock, 272, 361)
    rows = store.conn.execute("SELECT kind, started_ts FROM selections ORDER BY id").fetchall()
    assert [k for k, _ in rows] == ["grid", "off_grid", "off_grid", "grid"]
    assert [int(s // 60) - int(T0 // 60) for _, s in rows] == [0, 270, 271, 360]     # the grid did not move


def test_status_reports_market_minutes_watched_a_day_and_their_share(tmp_path):
    col, world, model, store, clock = started(tmp_path, n=2)
    run_minutes(col, clock, 1, 10)
    fin = store.conn.execute("SELECT finished_ts FROM selections").fetchone()[0]
    rows = ol.day_status(store, now=T0 + 600.5)
    # rev 4: only minutes with a heartbeat count; the selection here ran outside a cycle, so minute 0 has none
    assert fin < T0 + 60 and rows[0]["watched_minutes"] == pytest.approx(2 * (T0 + 600.5 - (T0 + 60)) / 60)
    assert rows[0]["watched_share"] == pytest.approx(rows[0]["watched_minutes"] / (20 * 1440))
    assert "watched" in ol.format_status(rows)
    s2 = ol.Store(tmp_path / "s.sqlite")                        # a watch across midnight is split between the days
    w = s2.insert("markets", {"selection_id": 1, "ticker": "A-E-M", "series": "A", **ol.stamp("selected", D0 + 85800),
                              **ol.stamp("close", D0 + 9 * 86400), "market_json": "{}", "book_json": "{}", "why": "x"})
    s2.insert("market_drops", {"watch_id": w, "ticker": "A-E-M", **ol.stamp("dropped", D0 + 87600), "reason": "x"})
    for m in range(int((D0 + 85800) // 60), int((D0 + 87600) // 60)):     # the loop ran all that time
        s2.insert("heartbeats", {**ol.stamp("", 60 * m + 1), "watched": 1, "kalshi_requests": 2,
                                 "kalshi_failures": 0, "model_calls": 0, "paused": 0})
    rows = ol.day_status(s2, now=D0 + 2 * 86400)
    assert [(r["day"], r["watched_minutes"]) for r in rows] == [("2026-11-13", 10.0), ("2026-11-14", 20.0)]


# ---------------------------------------------------------------- item 2: references

def test_each_history_reference_is_the_snapshot_nearest_its_target_within_thirty_seconds():
    t = T0 + 3610
    snaps = []
    for k in range(60):                     # the mid is 40 + k % 10 cents; minute 59's read ends 15 s in, not 2 s
        m = (40 + k % 10) / 100
        snaps.append(hist_snap(T0 + 60 * k + (15 if k == 59 else 2), round(m - 0.01, 2), round(m + 0.01, 2)))
    snaps.append(hist_snap(t - 8, 0.49, 0.51))                  # now: mid 50
    for i, s in enumerate(snaps):
        s["trades_min_ts"] = int(snaps[i - 1]["trades_start_ts"] - 5) if i else int(s["trades_start_ts"] - 60)
    s = ol.state_at(snaps, [], t, t + 7200)
    # one minute back is T0 + 3550: minute 59's read (T0 + 3555) is 5 s from it, minute 58's (T0 + 3482) 68 s
    assert s["mid_change_1m_cents"] == 1.0                      # 50 - 49, not the two-minute 50 - 48
    assert s["mid_change_5m_cents"] == 5.0                      # T0 + 3310: minute 55 (mid 45) at T0 + 3302
    holed = [x for x in snaps if x["book_end_ts"] != T0 + 3302]
    assert ol.state_at(holed, [], t, t + 7200)["mid_change_5m_cents"] is None   # nothing within 30 s: unknown
    late = snaps + [hist_snap(t - 0.4, 0.20, 0.90, end=t)]      # a read that ends at the decision time is not used
    assert ol.state_at(late, [], t, t + 7200) == s


# ---------------------------------------------------------------- item 3: a slow model

@pytest.mark.parametrize("latency", [15.0, 60.0])
def test_a_slow_model_never_costs_a_cycle_a_snapshot_or_a_slot_row(tmp_path, latency):
    col, world, model, store, clock = started(tmp_path, n=20, latency=latency)
    end = T0 + 3 * 3600
    run_until(col, clock, end)
    c = store.conn
    minutes = range(int(T0 // 60) + 1, int(end // 60))
    beats = {int(ts // 60) for (ts,) in c.execute("SELECT ts FROM heartbeats")}
    assert set(minutes) <= beats                                # a cycle every minute
    per: dict = {}
    for tk, b in c.execute("SELECT ticker, book_end_ts FROM snapshots"):
        per.setdefault(tk, set()).add(int(b // 60))
    assert len(per) == 20 and all(set(minutes) <= got for got in per.values())    # no snapshot missing
    rows = set(c.execute("SELECT watch_id, slot_ts FROM decisions WHERE forced = 0").fetchall())
    expected = set()
    for wid, phase in c.execute("SELECT watch_id, phase FROM markets").fetchall():
        first = c.execute("SELECT MIN(book_end_ts) FROM snapshots WHERE watch_id = ?", (wid,)).fetchone()[0]
        expected |= {(wid, 60.0 * m) for m in minutes if m % 5 == phase and 60 * m >= first + 1800}
    assert len(expected) > 500 and expected <= rows             # every slot has its row
    assert {r[0] for r in c.execute("SELECT status FROM decisions")} == {"late"}
    assert c.execute("SELECT COUNT(*) FROM decisions WHERE answers_json IS NOT NULL").fetchone()[0] == 0
    assert c.execute("SELECT MAX(latency_s) FROM decisions").fetchone()[0] == pytest.approx(10.0, abs=1e-3)
    assert c.execute("SELECT COUNT(*) FROM events WHERE kind = 'overrun'").fetchone()[0] == 0


def test_each_minute_takes_every_snapshot_first_in_the_same_order_then_decides(tmp_path):
    col, world, model, store, clock = started(tmp_path, n=6)
    run_minutes(col, clock, 1, 40)
    c = store.conn
    order = None
    for m in range(int(T0 // 60) + 1, int(T0 // 60) + 41):
        snaps = c.execute("SELECT watch_id, end_ts FROM snapshots WHERE book_start_ts >= ? AND book_start_ts < ? "
                          "ORDER BY book_start_ts", (60 * m, 60 * m + 60)).fetchall()
        order = order or [w for w, _ in snaps]
        assert [w for w, _ in snaps] == order
        for (d,) in c.execute("SELECT decision_ts FROM decisions WHERE slot_ts = ?", (60 * m,)).fetchall():
            assert d > max(e for _, e in snaps)
    assert c.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] > 0


def test_a_cycle_that_runs_into_the_next_minute_is_followed_at_once_and_recorded(tmp_path):
    col, world, model, store, clock = started(tmp_path, n=2)
    clock.t = T0 + 59.0
    world.book_delay["A-E-M"] = 70.0                            # one book read takes 70 s
    starts, waits = [], []
    real = col.cycle

    def cycle(*a, **k):
        starts.append(clock.t)
        if len(starts) > 1:
            world.book_delay.clear()
        return real(*a, **k)
    col.cycle = cycle

    def wait(s):
        waits.append(len(starts))
        clock.t += s
        if len(starts) >= 3:
            col.stop.set()
    col.run(wait=wait)
    ends = [r[0] for r in store.conn.execute("SELECT ts FROM heartbeats ORDER BY id")]
    assert waits == [0, 2, 3]                                   # no wait between the first and second cycles
    assert starts[1] == pytest.approx(ends[0]) and starts[1] > T0 + 120
    ev = store.conn.execute("SELECT detail_json FROM events WHERE kind = 'overrun'").fetchall()
    assert len(ev) == 1 and json.loads(ev[0][0])["minute_ts"] == T0 + 60


# ---------------------------------------------------------------- item 4: one transaction

def test_a_selection_is_one_transaction_and_a_restart_with_nothing_watched_selects_at_once(tmp_path):
    col, world, model, store, clock = rig(tmp_path)
    world.markets = [mkt("A-E-M", vol=3000), mkt("B-E-M", vol=2000)]
    for m in world.markets:
        world.books[m["ticker"]] = book(0.40, 0.45)
    col.start()
    store.conn.execute("CREATE TEMP TRIGGER power_cut BEFORE INSERT ON markets WHEN NEW.ticker = 'B-E-M' "
                       "BEGIN SELECT RAISE(ABORT, 'power cut'); END")
    with pytest.raises(sqlite3.DatabaseError, match="power cut"):
        col.select()                                            # killed between the market rows
    c = store.conn
    assert c.execute("SELECT COUNT(*) FROM selections").fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM markets").fetchone()[0] == 0
    store.close()
    clock.t += 30
    store2 = ol.Store(tmp_path / "loop.sqlite")
    col2 = make_collector(store2, kd.KalshiData(fetch=world, sleep=clock.sleep, clock=clock, tries=1),
                        ol.ModelClient(transport=model, clock=clock), clock=clock)
    col2.start()
    col2.cycle()
    assert sorted(w["ticker"] for w in col2.watched()) == ["A-E-M", "B-E-M"]


# ---------------------------------------------------------------- item 5: holes in a window

def test_window_numbers_need_ninety_percent_of_their_minutes():
    snaps, trades, t, close = hand_worked()

    def without(ks):
        return [dict(x) for i, x in enumerate(snaps) if i not in ks]
    assert ol.state_at(without({40, 41, 42}), trades, t, close)["max_1m_mid_move_30m_cents"] == 5.0   # 27 of 30
    assert ol.state_at(without({40, 41, 42, 43}), trades, t, close)["max_1m_mid_move_30m_cents"] is None
    assert ol.state_at(without(set(range(10, 16))), trades, t, close)["spread_vs_60m_median"] == 2.5   # 54 of 60
    assert ol.state_at(without(set(range(10, 17))), trades, t, close)["spread_vs_60m_median"] is None
    assert "90%" in ol.state_at.__doc__


# ---------------------------------------------------------------- item 6: the tail after a drop

def test_a_dropped_market_is_read_on_for_35_minutes_or_until_it_closes_with_no_decisions(tmp_path):
    col, world, model, store, clock = rig(tmp_path)
    world.markets = [mkt("A-E-M", vol=3000), mkt("B-E-M", vol=2000, close=T0 + 4 * 3600), mkt("C-E-M", vol=1000)]
    for m in world.markets:
        world.books[m["ticker"]] = book(0.40, 0.45)
    col.start()
    col.select()
    world.books["C-E-M"] = book(0.40, None)                     # C: one-sided, dropped after 30 minutes
    run_minutes(col, clock, 1, 245)                             # B: dropped 30 minutes before its close at 10:00
    c = store.conn
    drops = dict(c.execute("SELECT ticker, dropped_ts FROM market_drops").fetchall())
    assert set(drops) == {"B-E-M", "C-E-M"}
    for tk, n, until in (("C-E-M", 35, drops["C-E-M"] + 2100), ("B-E-M", 29, T0 + 4 * 3600)):
        tail = [r[0] for r in c.execute("SELECT book_start_ts FROM snapshots WHERE ticker = ? AND tail = 1 "
                                        "ORDER BY id", (tk,))]
        after = c.execute("SELECT COUNT(*) FROM snapshots WHERE ticker = ? AND book_start_ts > ?",
                          (tk, drops[tk])).fetchone()[0]
        assert len(tail) == n == after                          # every read after the drop is marked as the tail
        assert all(drops[tk] < b < until for b in tail)
        assert [int(b // 60) for b in tail] == list(range(int(drops[tk] // 60) + 1, int(drops[tk] // 60) + 1 + n))
        assert c.execute("SELECT COUNT(*) FROM decisions WHERE ticker = ? AND decision_ts > ?",
                         (tk, drops[tk])).fetchone()[0] == 0
    assert c.execute("SELECT COUNT(*) FROM snapshots WHERE ticker = 'A-E-M' AND tail = 0").fetchone()[0] == 245


# ---------------------------------------------------------------- item 7: chosen again; the cached screen

def test_a_market_chosen_again_goes_on_deciding_without_a_new_warm_up(tmp_path):
    col, world, model, store, clock = started(tmp_path, n=1)
    run_minutes(col, clock, 1, 760)                             # its 12 hours end at the 18:00 reselection
    watches = [r[0] for r in store.conn.execute("SELECT watch_id FROM markets ORDER BY watch_id")]
    assert len(watches) == 2
    got = decisions(store)
    assert {d["watch_id"] for d in got} == set(watches)
    slots = [d["slot_ts"] for d in got]
    assert all(b - a == 300 for a, b in zip(slots, slots[1:]))  # no new 30-minute warm-up
    assert slots[-1] > T0 + 760 * 60 - 300


def test_the_cached_quote_screen_is_five_cents_wide_and_counted(tmp_path):
    col, world, model, store, clock = rig(tmp_path)
    world.markets = [mkt("A-E-M", vol=5000, bid=0.06, ask=0.10), mkt("B-E-M", vol=4000, bid=0.04, ask=0.10),
                     mkt("C-E-M", vol=3000, bid=0.88, ask=0.95), mkt("D-E-M", vol=2000, bid=0.88, ask=0.96),
                     mkt("E-E-M", vol=1000, bid=0.40, ask=0.41), mkt("F-E-M", vol=900, bid=0.30, ask=0.51)]
    for m in world.markets:
        world.books[m["ticker"]] = book(0.40, 0.45)             # the live books are all inside the band
    col.start()
    col.select()
    assert sorted(w["ticker"] for w in col.watched()) == ["A-E-M", "C-E-M", "E-E-M"]
    sel = store.conn.execute("SELECT screened_out, reason_counts_json FROM selections").fetchone()
    assert sel[0] == 3 and json.loads(sel[1])["cached_quote"] == 3
    assert "practical addition" in ol.__doc__


# ---------------------------------------------------------------- item 8: any model failure

@pytest.mark.parametrize("exc, status", [(http.client.IncompleteRead(b"{"), "model_unavailable"),
                                         (http.client.RemoteDisconnected("closed"), "model_unavailable"),
                                         (RuntimeError("something else"), "error")])
def test_any_failure_of_the_model_call_still_gives_the_slot_its_row(tmp_path, exc, status):
    col, world, model, store, clock = started(tmp_path, exc=exc)
    run_minutes(col, clock, 1, 35)
    d = decisions(store)[0]
    assert d["status"] == status and d["error"] and d["answers_json"] is None and d["state_json"] is not None


def test_loopback_http_turns_protocol_failures_and_timeouts_into_the_loops_own(monkeypatch):
    class Opener:
        def __init__(self, exc):
            self.exc = exc

        def open(self, req, timeout):
            raise self.exc
    url = "http://127.0.0.1:11434/v1/systemone"
    monkeypatch.setattr(ol, "_OPENER", Opener(http.client.IncompleteRead(b"")))
    with pytest.raises(ol.ModelUnavailable):
        ol.loopback_http(url, b"{}", 10.0)
    for exc in (TimeoutError("timed out"), urllib.error.URLError(TimeoutError("timed out"))):
        monkeypatch.setattr(ol, "_OPENER", Opener(exc))
        with pytest.raises(TimeoutError):
            ol.loopback_http(url, b"{}", 10.0)


# ---------------------------------------------------------------- item 9: the battery's id

def test_the_battery_id_is_the_hash_of_the_question_bytes_sent_in_order():
    body = ol.request_bytes({"mid_cents": 50.0})
    q = json.dumps(json.loads(body)["questions"], separators=(",", ":"), ensure_ascii=False).encode()
    assert q in body and ol.BATTERY_ID == ol.sha256(q)
    swapped = dict(ol.BATTERY)
    swapped["regime"] = dict(swapped["regime"], criteria=dict(reversed(list(swapped["regime"]["criteria"].items()))))
    assert ol.battery_id(swapped) != ol.BATTERY_ID              # the order of the options counts
    assert ol.battery_id(dict(reversed(list(ol.BATTERY.items())))) != ol.BATTERY_ID


# ---------------------------------------------------------------- item 10: invalid books

@pytest.mark.parametrize("raw, why", [
    ({"yes_dollars": [["0.5000", "10.00"]], "no_dollars": [["0.5500", "10.00"]]}, "crossed"),
    ({"yes_dollars": [["0.4000", "-1.00"]], "no_dollars": [["0.5500", "10.00"]]}, "negative size"),
    ({"yes_dollars": [["abc", "1.00"]], "no_dollars": [["0.5500", "10.00"]]}, "malformed"),
])
def test_an_invalid_book_is_stored_as_such_never_decided_on_and_dropped_after_thirty_minutes(tmp_path, raw, why):
    col, world, model, store, clock = started(tmp_path)
    run_minutes(col, clock, 1, 30)
    world.raw_books["A-E-M"] = raw
    run_minutes(col, clock, 31, 62)
    c = store.conn
    bad = c.execute("SELECT book_state, invalid_reason, raw_json, best_bid, mid FROM snapshots WHERE book_start_ts > ?",
                    (T0 + 1860,)).fetchall()
    assert len(bad) == 32
    assert all(b[0] == "invalid" and why in b[1] and json.loads(b[2]) == raw and b[3] is None and b[4] is None
               for b in bad)
    ds = decisions(store)
    assert len(ds) == 6 and all(d["status"] == "stale_data" and d["state_json"] is None for d in ds)
    assert model.posts == []
    when, reason = c.execute("SELECT dropped_ts, reason FROM market_drops").fetchone()
    assert "invalid" in reason and int(when // 60) - int(T0 // 60) == 61


# ================================================================= rev 4

# ---------------------------------------------------------------- item 1: references aimed at the newest snapshot

@pytest.mark.parametrize("model_s, kalshi_s", [(0.8, 0.0), (9.5, 0.0), (0.8, 0.9)],
                         ids=["fast", "model_9.5s", "kalshi_0.9s"])
def test_on_simulated_days_every_reference_is_aimed_at_the_newest_snapshot(tmp_path, model_s, kalshi_s):
    import bisect
    col, world, model, store, clock = started(tmp_path, n=20, latency=model_s)
    world.request_delay = kalshi_s
    for i in range(1, 24 * 60 + 1):
        for j, m in enumerate(world.markets):                  # every market's mid moves every minute
            bid = round(0.30 + 0.01 * ((i + j) % 10), 2)
            world.books[m["ticker"]] = book(bid, round(bid + 0.05, 2))
        clock.t = max(clock.t, T0 + 60 * i + 0.2)
        col.cycle()
    snaps: dict = {}
    for tk, end, bend, mid in store.conn.execute("SELECT ticker, end_ts, book_end_ts, mid FROM snapshots "
                                                 "ORDER BY book_end_ts"):
        snaps.setdefault(tk, []).append((end, bend, round(mid * 100, 4)))
    ends = {tk: [x[0] for x in v] for tk, v in snaps.items()}
    first = min(x[1] for v in snaps.values() for x in v)
    checked = holes = 0
    ds = [d for d in decisions(store) if d["status"] == "ok"]
    assert len(ds) > 4 * 1300
    for d in ds:
        t, st = d["decision_ts"], json.loads(d["state_json"])
        before = snaps[d["ticker"]][:bisect.bisect_left(ends[d["ticker"]], t)]
        newest = before[-1]
        assert st["mid_cents"] == pytest.approx(newest[2])
        times = [x[1] for x in before[:-1]]
        for k in (1, 5, 15, 30, 60):
            target = newest[1] - 60 * k
            lo, hi = bisect.bisect_left(times, target - 30), bisect.bisect_right(times, target + 30)
            band = before[lo:hi]
            got = st["mid_change_%dm_cents" % k]
            if not band:                                        # null only where the history has a hole
                assert got is None and target < first + 30
                holes += 1
                continue
            ref = min(band, key=lambda x: abs(x[1] - target))
            assert ref is not newest and abs(newest[1] - ref[1] - 60 * k) <= 30
            assert got == pytest.approx(round(newest[2] - ref[2], 1)), (k, d["decision_ts"])
            checked += 1
    assert checked > 5 * 4 * 1300 * 0.9 and holes < 0.05 * checked


# ---------------------------------------------------------------- item 2: status counts each slot once

def warm_times_from_rows(store):
    """For each watch, from the stored rows alone: the first moment u, from its choice on, at which its market's
    snapshots (of any watch of it, tails included) hold an unbroken run reaching back 30 minutes before u: the run
    starts at or before u - 1800, no two consecutive snapshots of it are more than 90 s apart, and one of them lies
    in [u - 90, u]. None when that never happens. (Written apart from the module's code.)"""
    c = store.conn
    by: dict = {}
    for tk, b in c.execute("SELECT ticker, book_end_ts FROM snapshots ORDER BY book_end_ts"):
        by.setdefault(tk, []).append(b)
    out = {}
    for wid, tk, sel in c.execute("SELECT watch_id, ticker, selected_ts FROM markets"):
        times, runs = by.get(tk, []), []
        for b in times:
            if runs and b - runs[-1][-1] <= 90:
                runs[-1].append(b)
            else:
                runs.append([b])
        best = None
        for run in runs:
            lo = max(run[0] + 1800, sel)
            for b in run:                                       # the first u >= lo with a snapshot in [u - 90, u]
                if b >= lo - 90:
                    best = max(lo, b)
                    break
            if best is not None:
                break
        out[wid] = best
    return out


def independent_status(store, now):
    """Expected slots, collected slots and watched minutes per UTC day, counted minute by minute from the raw
    record, not with the module's code."""
    c = store.conn

    def day(ts):
        return datetime.fromtimestamp(ts, UTC).date().isoformat()
    alive = {int(ts // 60) for (ts,) in c.execute("SELECT ts FROM heartbeats WHERE paused = 0")}   # rev 5
    watches = c.execute("SELECT m.watch_id, m.ticker, m.selected_ts, COALESCE(d.dropped_ts, ?), m.phase FROM markets m "
                        "LEFT JOIN market_drops d ON d.watch_id = m.watch_id ORDER BY m.watch_id", (now,)).fetchall()
    by_ticker: dict = {}
    first: dict = {}
    for wid, tk, b in c.execute("SELECT watch_id, ticker, book_end_ts FROM snapshots ORDER BY book_end_ts"):
        by_ticker.setdefault(tk, []).append(b)
        first.setdefault(wid, b)
    warm = warm_times_from_rows(store)                          # rev 9: an unbroken run of 30 minutes
    slots, watched = set(), Counter()
    for wid, tk, sel, end, ph in watches:
        end = min(end, now)
        for m in range(int(sel // 60), int(end // 60) + 1):
            lo, hi = max(sel, 60.0 * m), min(end, 60.0 * m + 60)
            if hi > lo and m in alive:
                watched[day(60 * m)] += (hi - lo) / 60
            if m % 5 == ph and sel <= 60 * m < end and warm[wid] is not None and 60 * m >= warm[wid]:
                slots.add((tk, 60.0 * m))
    expected = Counter(day(s) for _, s in slots)
    ok = Counter(day(s) for tk, s, st in c.execute("SELECT ticker, slot_ts, status FROM decisions WHERE forced = 0")
                 if st == "ok" and (tk, s) in slots)
    return expected, ok, watched


def test_status_counts_each_slot_once_and_no_stopped_minute_as_watched(tmp_path):
    col, world, model, store, clock = rig(tmp_path)
    col.slots = 6
    world.markets = [mkt("S%d-E-M" % i, vol=5000 - i, close=T0 + 20 * 86400) for i in range(8)]
    for m in world.markets:
        world.books[m["ticker"]] = book(0.40, 0.45)
    col.start()

    def at(ts):
        return int((ts - T0) // 60)
    d2 = D0 + 86400
    stop_from, stop_to = 11 * 60, 13 * 60 + 13                 # 17:00 to 19:13 on day 1: 2 h 13 min stopped
    pause_at = at(d2 + 3 * 3600)
    late, down = range(at(d2 + 8 * 3600), at(d2 + 9 * 3600)), range(at(d2 + 10 * 3600), at(d2 + 11 * 3600))
    onesided = range(at(d2 + 14 * 3600), at(d2 + 15 * 3600))
    i, end = 0, at(D0 + 3 * 86400)
    while i < end:
        if i == stop_from:
            i = stop_to
            clock.t = T0 + 60 * i + 0.3
            col = make_collector(store, kd.KalshiData(fetch=world, sleep=clock.sleep, clock=clock, tries=1),
                               ol.ModelClient(transport=model, clock=clock), clock=clock, slots=6)
            col.start()
        model.latency = 12.0 if i in late else 0.8
        model.post_down = i in down
        world.fail = pause_at <= i < pause_at + 2
        world.books["S0-E-M"] = book(0.40, None) if i in onesided else book(0.40, 0.45)
        clock.t = T0 + 60 * i + 0.5
        col.cycle()
        i += 1
    c = store.conn
    reasons = " ".join(r[0] for r in c.execute("SELECT reason FROM market_drops"))
    assert "while stopped" in reasons and "one-sided" in reasons
    assert c.execute("SELECT COUNT(*) FROM markets WHERE warm_from_ts IS NOT NULL").fetchone()[0] > 0   # no gap
    statuses = {r[0] for r in c.execute("SELECT status FROM decisions")}
    assert {"ok", "late", "model_unavailable", "stale_data"} <= statuses
    now = clock.t
    rows = {r["day"]: r for r in ol.day_status(store, now)}
    expected, ok, watched = independent_status(store, now)
    assert sorted(rows) == sorted(expected) == ["2026-11-13", "2026-11-14", "2026-11-15"]
    for d, r in rows.items():
        assert (r["expected"], r["collected"]) == (expected[d], ok[d]), d
        assert r["watched_minutes"] == pytest.approx(watched[d]), d
        assert r["watched_share"] == pytest.approx(watched[d] / (20 * 1440))
    assert rows["2026-11-14"]["expected"] <= 6 * 288 and rows["2026-11-15"]["expected"] <= 6 * 288
    assert rows["2026-11-13"]["watched_minutes"] <= 6 * (18 * 60 - 133) + 6     # the stop shows


# ---------------------------------------------------------------- item 3: trades that reach the tape late

def test_trades_reaching_the_tape_up_to_a_minute_late_are_all_kept(tmp_path):
    import random
    rnd = random.Random(5)
    col, world, model, store, clock = started(tmp_path, n=2)
    n, per_minute = 0, []
    for i in range(1, 181):
        for m in world.markets:
            for _ in range(3):
                n += 1
                world.trade(m["ticker"], n, T0 + 60 * (i - 1) + rnd.uniform(0, 60), lag=rnd.uniform(0, 60))
        before = len(world.urls)
        run_minutes(col, clock, i, i)
        per_minute.append(len(world.urls) - before)
    last = clock.t - 1
    on_tape = {r["trade_id"] for v in world.tape.values() for r in v if r["_on_tape"] <= last - 5}
    stored = {r[0] for r in store.conn.execute("SELECT trade_id FROM trades")}
    assert len(on_tape) > 900 and on_tape - stored == set()     # none lost
    assert store.conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == len(stored)   # each once
    assert set(per_minute[1:]) == {4}                           # still one book and one trades page a market


# ---------------------------------------------------------------- item 4: a store that refuses writes

def test_a_store_that_refuses_writes_for_a_while_does_not_end_the_loop(tmp_path, capsys):
    col, world, model, store, clock = started(tmp_path, n=2)

    def wait(s):
        clock.t += s
        minute = int((clock.t - T0) // 60)
        store.conn.execute("PRAGMA query_only = %d" % (1 if 5 <= minute < 9 else 0))   # the disk is full
        if minute >= 15:
            col.stop.set()
    col.run(wait=wait)
    err = capsys.readouterr().err.strip().splitlines()
    assert len(err) == 4 and all("cannot write the record" in e for e in err)       # one line a minute
    c = store.conn
    beats = sorted(int((ts - T0) // 60) for (ts,) in c.execute("SELECT ts FROM heartbeats"))
    assert [m for m in range(1, 15) if m not in beats] == [5, 6, 7, 8]
    gap = json.loads(c.execute("SELECT detail_json FROM events WHERE kind = 'gap'").fetchone()[0])
    assert int((gap["from_ts"] - T0) // 60) == 4 and int((gap["to_ts"] - T0) // 60) == 9
    assert c.execute("SELECT kind FROM events ORDER BY id DESC").fetchone()[0] == "stop"


# ---------------------------------------------------------------- item 5: the selection checks books

def test_the_selection_turns_down_an_invalid_book_and_does_not_count_it_as_a_failure(tmp_path):
    col, world, model, store, clock = rig(tmp_path)
    world.markets = [mkt("A%d-E-M" % i, vol=5000 - i) for i in range(6)] + [mkt("OK-E-M", vol=900)]
    for i in range(6):
        world.raw_books["A%d-E-M" % i] = ({"yes_dollars": [["0.4000", "-1.00"]], "no_dollars": [["0.5500", "10.00"]]}
                                          if i % 2 else {"yes_dollars": [["abc", "1.00"]], "no_dollars": []})
    world.books["OK-E-M"] = book(0.40, 0.45)
    col.start()
    col.select()
    assert [w["ticker"] for w in col.watched()] == ["OK-E-M"]
    sel = store.conn.execute("SELECT reasons_json, reason_counts_json FROM selections").fetchone()
    reasons = json.loads(sel[0])
    assert all(("negative size" if i % 2 else "malformed") in reasons["A%d-E-M" % i] for i in range(6))
    assert json.loads(sel[1])["invalid_book"] == 6
    kinds = [r[0] for r in store.conn.execute("SELECT kind FROM events")]
    assert "error" not in kinds and "pause" not in kinds and not col.paused()


# ---------------------------------------------------------------- item 6: four small things

def test_a_reselection_that_crosses_minutes_leaves_a_heartbeat_in_each_and_no_gap(tmp_path):
    col, world, model, store, clock = rig(tmp_path)
    world.markets = [mkt("S%02d-E-M" % i, vol=5000 - i, close=T0 + 20 * 86400) for i in range(40)]
    for m in world.markets:
        world.books[m["ticker"]] = book(0.40, 0.45)
    world.page_size, world.markets_delay = 4, 150.0
    col.start()
    end = T0 + 13 * 3600
    run_until(col, clock, end)
    beats = {int(ts // 60) for (ts,) in store.conn.execute("SELECT ts FROM heartbeats")}
    assert [m for m in range(int(T0 // 60), int(end // 60)) if m not in beats] == []
    assert store.conn.execute("SELECT COUNT(*) FROM events WHERE kind = 'gap'").fetchone()[0] == 0


def test_a_restart_after_an_overdue_grid_point_selects_off_grid_and_keeps_the_grid(tmp_path):
    col, world, model, store, clock = started(tmp_path, n=2)       # the grid: 06:00, 12:00, 18:00, ...
    run_minutes(col, clock, 1, 10)
    store.close()
    clock.t = T0 + 7 * 3600 + 0.5                                  # 12:00 passed while stopped
    store2 = ol.Store(tmp_path / "loop.sqlite")
    col2 = make_collector(store2, kd.KalshiData(fetch=world, sleep=clock.sleep, clock=clock, tries=1),
                        ol.ModelClient(transport=model, clock=clock), clock=clock)
    col2.start()
    run_minutes(col2, clock, 420, 721)
    rows = store2.conn.execute("SELECT kind, started_ts FROM selections ORDER BY id").fetchall()
    assert [(k, int((s - T0) // 60)) for k, s in rows] == [("grid", 0), ("off_grid", 420), ("grid", 720)]


def test_with_nothing_qualifying_selections_back_off_to_every_five_minutes_after_three_empty(tmp_path):
    col, world, model, store, clock = rig(tmp_path)
    world.markets = [mkt("A-E-M", vol=100)]                        # nothing qualifies
    col.start()
    run_minutes(col, clock, 0, 20)
    got = [int((s - T0) // 60) for (s,) in store.conn.execute("SELECT started_ts FROM selections ORDER BY id")]
    assert got == [0, 1, 2, 7, 12, 17]


# ---------------------------------------------------------------- item 7: the preamble

def test_the_preamble_says_changes_can_be_negative_and_what_the_taker_share_is():
    text = ol.BATTERY["up_15m"]["instructions"]
    assert "changes are in cents and can be negative" in text and "yes_taker_share" in text
    assert "(0 to 100); sizes" not in text


# ================================================================= rev 5

# ---------------------------------------------------------------- outcome 1: window minutes by the reference rule

def my_window_numbers(rows, t):
    """`max_1m_mid_move_30m_cents` and `spread_vs_60m_median` worked out here on their own, not with the module's
    code. `rows`: (book_end_ts, end_ts, yes, no) of one market's valid snapshots, yes and no best first in dollars.
    "Now" is the newest snapshot that ended before t. Minute k of an N-minute window (k = 1 to N) is covered by the
    snapshot nearest now's book time less 60k seconds, within 30 s either side, the earlier one on a tie; a snapshot
    covers one minute at most (minute 1 is taken first). Fewer than 27 of 30 or 54 of 60 covered: null. The move is
    the largest change of the mid from one covered minute to the next (now is minute 0); the median is that of the
    spreads of now and the covered minutes."""
    import statistics
    before = sorted((r for r in rows if r[1] < t), key=lambda r: r[0])
    if not before:
        return None, None

    def cents(r):
        yes, no = r[2], r[3]
        if not yes or not no:
            return None, None
        bid, ask = round(yes[0][0] * 100, 6), round(100 - no[0][0] * 100, 6)
        return (bid + ask) / 2, ask - bid
    now = before[-1]
    windows = []
    for n, need in ((30, 27), (60, 54)):
        free, got = list(before[:-1]), {0: now}
        for k in range(1, n + 1):
            aim = now[0] - 60 * k
            near = [r for r in free if abs(r[0] - aim) <= 30]
            if near:
                r = min(near, key=lambda r: (abs(r[0] - aim), r[0]))
                free.remove(r)
                got[k] = r
        windows.append(got if len(got) - 1 >= need else None)
    w30, w60 = windows
    move = ratio = None
    if w30 is not None and cents(now)[0] is not None:
        steps = [abs(cents(w30[k - 1])[0] - cents(w30[k])[0]) for k in range(1, 31)
                 if k in w30 and k - 1 in w30 and cents(w30[k])[0] is not None and cents(w30[k - 1])[0] is not None]
        move = round(max(steps), 1) if steps else None
    if w60 is not None and cents(now)[1] is not None:
        spreads = [cents(r)[1] for r in w60.values() if cents(r)[1] is not None]
        med = statistics.median(spreads)
        ratio = round(cents(now)[1] / med, 2) if med > 0 else None
    return move, ratio


def test_the_verifiers_smallest_case_has_both_window_numbers():
    snaps = []
    for k in range(71):                     # one a minute, alternately 0.4 s early and 0.4 s late
        mid, spread = 40 + k % 7, 1 + k % 4
        snaps.append(hist_snap(T0 + 60 * k + (0.4 if k % 2 else -0.4), (mid - spread / 2) / 100,
                               (mid + spread / 2) / 100))
    for i, s in enumerate(snaps):
        s["trades_min_ts"] = int(snaps[i - 1]["trades_start_ts"] - 5) if i else int(s["trades_start_ts"] - 60)
    t = snaps[-1]["end_ts"] + 1
    s = ol.state_at(snaps, [], t, t + 7200)
    mine = my_window_numbers([(x["book_end_ts"], x["end_ts"], x["yes"], x["no"]) for x in snaps], t)
    assert None not in mine
    assert (s["max_1m_mid_move_30m_cents"], s["spread_vs_60m_median"]) == pytest.approx(mine)


@pytest.mark.parametrize("holes", [0.0, 0.08], ids=["every_minute", "with_holes"])
def test_on_simulated_days_with_wandering_times_the_window_numbers_follow_the_minute_rule(tmp_path, holes):
    import bisect
    import random
    rnd = random.Random(11)
    col, world, model, store, clock = started(tmp_path, n=20)
    for i in range(1, 10 * 60 + 1):
        world.fail_paths, world.raw_books = set(), {}
        for j, m in enumerate(world.markets):
            tk = m["ticker"]
            bid, spread = 30 + (7 * i + 3 * j) % 11, 1 + (i + 2 * j) % 6
            world.books[tk] = book(bid / 100, (bid + spread) / 100)
            if holes and rnd.random() < holes:                   # a hole: a failed read or an invalid book
                if rnd.random() < 0.5:
                    world.fail_paths.add("/markets/%s/orderbook" % tk)
                else:
                    world.raw_books[tk] = {"yes_dollars": [["0.5000", "10.00"]], "no_dollars": [["0.6000", "10.00"]]}
        clock.t = max(clock.t, T0 + 60 * i + rnd.uniform(0.2, 4.2))   # the minute's reads start up to 4 s late
        col.cycle()
    c = store.conn
    assert c.execute("SELECT COUNT(*) FROM events WHERE kind = 'pause'").fetchone()[0] == 0
    rows: dict = {}
    for tk, b, e, y, n, bad in c.execute("SELECT ticker, book_end_ts, end_ts, yes_json, no_json, invalid_reason "
                                         "FROM snapshots"):
        if bad is None:
            rows.setdefault(tk, []).append((b, e, json.loads(y), json.loads(n)))
    for v in rows.values():
        v.sort()
    first = {tk: v[0][0] for tk, v in rows.items()}
    ds = [d for d in decisions(store) if d["status"] == "ok"]
    assert len(ds) >= 2000
    nulls = full = values = 0
    for d in ds:
        st = json.loads(d["state_json"])
        mine = my_window_numbers(rows[d["ticker"]][:bisect.bisect_left([r[0] for r in rows[d["ticker"]]],
                                                                         d["decision_ts"])], d["decision_ts"])
        got = (st["max_1m_mid_move_30m_cents"], st["spread_vs_60m_median"])
        assert got == pytest.approx(mine), (d["ticker"], d["decision_ts"])
        if d["decision_ts"] - first[d["ticker"]] > 62 * 60:     # a full hour of history behind it
            full += 1
            nulls += got.count(None)
            values += 2 - got.count(None)
    assert full >= 1500
    if holes:
        assert nulls > 50 and values > 1000                     # both kinds occur, each exactly where it should
    else:
        assert nulls == 0


# ---------------------------------------------------------------- outcome 3: three small things

def test_a_slot_skipped_because_the_cycle_before_overran_gets_a_late_row(tmp_path):
    col, world, model, store, clock = started(tmp_path, n=5)    # one market on each phase
    run_minutes(col, clock, 1, 40)
    for m in world.markets:                                     # minute 41: every book read takes 25 s
        world.book_delay[m["ticker"]] = 25.0
    clock.t = T0 + 41 * 60 + 0.5
    col.cycle()
    for m in world.markets:
        world.book_delay[m["ticker"]] = 0.0
    assert clock.t > T0 + 43 * 60                               # the cycle ran through 06:42 into 06:43
    col.cycle()                                                 # the next one at once: minute 06:43
    phase2 = [w for w in col.watched() if w["phase"] == 2]
    assert len(phase2) == 1
    rows = store.conn.execute("SELECT status, error FROM decisions WHERE watch_id = ? AND slot_ts = ?",
                              (phase2[0]["watch_id"], T0 + 42 * 60)).fetchall()
    assert len(rows) == 1 and rows[0][0] == "late" and "overran" in rows[0][1]
    for w in col.watched():                                     # every slot of every market has its row
        slots = {s for (s,) in store.conn.execute("SELECT slot_ts FROM decisions WHERE watch_id = ? AND forced = 0",
                                                  (w["watch_id"],))}
        want = {T0 + 60 * m for m in range(32, 44) if m % 5 == w["phase"]}   # all warm by 06:32
        assert want <= slots, w["ticker"]


def test_a_paused_minute_is_not_counted_as_watched(tmp_path):
    col, world, model, store, clock = started(tmp_path, n=6)
    run_minutes(col, clock, 1, 1)
    world.fail = True
    run_minutes(col, clock, 2, 2)                               # five failures: paused for five minutes
    world.fail = False
    run_minutes(col, clock, 3, 20)
    paused = {int(ts // 60) for ts, p in store.conn.execute("SELECT ts, paused FROM heartbeats") if p}
    assert len(paused) >= 5
    now = T0 + 20 * 60 + 30
    row = ol.day_status(store, now)[0]
    _, _, watched = independent_status(store, now)
    assert row["watched_minutes"] == pytest.approx(watched["2026-11-13"])
    assert row["watched_minutes"] <= 6 * (21 - len(paused))


def test_a_kalshi_request_over_thirty_seconds_is_a_failed_request(tmp_path):
    assert ol.KALSHI_REQUEST_LIMIT_S == 30.0
    col, world, model, store, clock = started(tmp_path, n=2)
    run_minutes(col, clock, 1, 1)
    world.book_delay["A-E-M"] = 31.0
    run_minutes(col, clock, 2, 2)
    c = store.conn
    err = [json.loads(d)["error"] for (d,) in c.execute("SELECT detail_json FROM events WHERE kind = 'error' AND "
                                                         "ticker = 'A-E-M'")]
    assert len(err) == 1 and "30" in err[0]
    got = {t for (t,) in c.execute("SELECT ticker FROM snapshots WHERE book_start_ts >= ?", (T0 + 120,))}
    assert got == {"B-E-M"}


def test_the_kalshi_transport_gives_up_after_its_limit():
    import threading
    import time
    gate = threading.Event()

    def slow(url):
        gate.wait(5)
        return b"{}"
    get = ol.bounded_fetch(slow, limit=0.2)
    t0 = time.monotonic()
    with pytest.raises(TimeoutError):
        get(kd.BASE_URL + "/markets")
    assert time.monotonic() - t0 < 1.5
    gate.set()
    assert ol.bounded_fetch(lambda url: b"ok", limit=0.2)("x") == b"ok"

    def refused(url):
        raise kd.HttpError(404, url)
    with pytest.raises(kd.HttpError):
        ol.bounded_fetch(refused, limit=0.2)("x")
    assert ol.kalshi_client().fetch.limit == 30.0               # what `collect` and `once` use


# ================================================================= rev 6: nothing stored unless the clock is right

class Skewed:
    """The collector's wall clock: the test's own true clock plus an offset the test steps. Every reading is
    logged (value -> the true time it was read at), so each stored time can be traced back."""
    def __init__(self, true):
        self.true, self.offset, self.log = true, 0.0, {}

    def __call__(self):
        t = self.true()
        v = t + self.offset
        self.log[v] = t
        return v


class Recorder:
    """The record's connection, passed through; it notes the true time and the offset at every INSERT, and once
    a write is committed calls `after(tables)` with the tables it wrote."""
    def __init__(self, conn, true, skew):
        self.conn, self.true, self.skew = conn, true, skew
        self.writes, self.after, self.pending = [], None, []

    def execute(self, sql, *a):
        import re
        cur = self.conn.execute(sql, *a)
        m = re.match(r"\s*INSERT (?:OR \w+ )?INTO (\w+)", sql)
        if m:
            self.writes.append((m.group(1), self.true.t, self.skew.offset))
            self.pending.append(m.group(1))
            if not self.conn.in_transaction:
                self._done()
        return cur

    def _done(self):
        tables, self.pending = self.pending, []
        if tables and self.after is not None:
            self.after(tables)

    def __enter__(self):
        self.conn.__enter__()
        return self

    def __exit__(self, *a):
        out = self.conn.__exit__(*a)
        self._done()
        return out

    def __getattr__(self, k):
        return getattr(self.conn, k)


def sync_rig(tmp_path, n=4, mono=None, tries=1):
    true = Clock()
    mono = mono(true) if mono else true.mono
    skew = Skewed(true)
    world, model = World(true), Model(true)
    store = ol.Store(tmp_path / "loop.sqlite")
    rec = Recorder(store.conn, true, skew)
    store.conn = rec
    kalshi = kd.KalshiData(fetch=world, sleep=true.sleep, clock=mono, tries=tries)
    col = make_collector(store, kalshi, ol.ModelClient(transport=model, clock=skew), clock=skew, monotonic=mono)
    world.markets = [mkt("%s-E-M" % chr(65 + i), vol=1000 + i, close=T0 + 5 * 86400) for i in range(n)]
    for m in world.markets:
        world.books[m["ticker"]] = book(0.40, 0.45)
    return col, world, model, store, rec, true, skew


POINTS = ["before_snapshots", "between_snapshot_reads", "inside_a_request", "between_snapshots_and_decisions",
          "during_a_model_call", "during_a_selection", "wrong_from_the_start"]
SIZES = [3.0, 150.0, 3600.0, 86400.0]


@pytest.mark.parametrize("sign", [-1, 1], ids=["back", "ahead"])
@pytest.mark.parametrize("size", SIZES, ids=["3s", "150s", "1h", "1d"])
@pytest.mark.parametrize("point", POINTS)
def test_nothing_is_stored_unless_the_clock_is_right(tmp_path, point, size, sign):
    import random
    rnd = random.Random(7)
    col, world, model, store, rec, true, skew = sync_rig(tmp_path)
    step_min = 0 if point in ("during_a_selection", "wrong_from_the_start") else 40
    right_min = step_min + 8                                    # the clock is set right again
    armed = {"on": False, "n": 0}

    def fire():
        armed["on"] = False
        skew.offset = sign * size

    def counter(k):
        def hook(*_):
            if armed["on"]:
                armed["n"] += 1
                if armed["n"] == k:
                    fire()
        return hook
    if point == "between_snapshot_reads":
        rec.after = lambda tables: counter(2)() if "snapshots" in tables else None
    elif point == "between_snapshots_and_decisions":
        rec.after = lambda tables: counter(4)() if "snapshots" in tables else None
    elif point == "inside_a_request":
        world.on_request = counter(3)
    elif point == "during_a_model_call":
        model.on_post = counter(1)
    elif point == "during_a_selection":
        world.on_request = lambda path: counter(1)() if path == "/markets" else None
    if point == "wrong_from_the_start":
        skew.offset = sign * size
    elif point == "during_a_selection":
        armed["on"] = True
    col.start()
    calls = []
    history = store.history

    def spy(watch_id, t):
        out = history(watch_id, t)
        calls.append(out)
        return out
    store.history = spy
    n, timeline, per_cycle = 0, [], []
    for i in range(0, 91):
        for m in world.markets:
            for _ in range(2):
                n += 1
                world.trade(m["ticker"], n, T0 + 60 * (i - 1) + rnd.uniform(0, 60), lag=rnd.uniform(0, 30))
        if i == step_min and point == "before_snapshots":
            skew.offset = sign * size
        elif i == step_min and point not in ("during_a_selection", "wrong_from_the_start"):
            armed.update(on=True, n=0)
        if i == right_min:
            skew.offset = 0.0
        began = getattr(col, "synced", True)
        before = len(world.urls)
        true.t = max(true.t, T0 + 60 * i + 0.5)
        col.cycle()
        per_cycle.append((i, began, getattr(col, "synced", True), len(world.urls) - before))
        timeline.append((true.t, getattr(col, "synced", True)))
    c = store.conn
    # every snapshot, trade, decision and heartbeat was written, and its times read, while the two clocks agreed
    kept = ("snapshots", "trades", "decisions", "heartbeats")
    lim = ol.CLOCK_AGREE_S + 2                                  # the threshold, and the Date's one-second steps
    assert all(abs(off) <= lim for table, _, off in rec.writes if table in kept)
    stamps = []
    for row in c.execute("SELECT book_start_ts, book_end_ts, trades_start_ts, trades_end_ts, end_ts FROM snapshots"):
        stamps += [x for x in row if x is not None]
    stamps += [x for (x,) in c.execute("SELECT seen_ts FROM trades")]
    stamps += [x for (x,) in c.execute("SELECT decision_ts FROM decisions")]
    stamps += [x for (x,) in c.execute("SELECT ts FROM heartbeats")]
    assert stamps and all(abs(v - skew.log[v]) <= lim for v in stamps)
    # no decision's state uses a row read while they did not
    for snaps, trades, _close in calls:
        assert all(abs(s["book_end_ts"] - skew.log[s["book_end_ts"]]) <= lim for s in snaps)
        assert all(abs(x["seen_ts"] - skew.log[x["seen_ts"]]) <= lim for x in trades)
    # no trade made while the collector was in sync is lost
    stored = {r[0] for r in c.execute("SELECT trade_id FROM trades")}
    end = timeline[-1][0]
    lost = []
    for v in world.tape.values():
        for r in v:
            ts = kd.iso_to_ts(r["created_time"])
            if ts < T0 or r["_on_tape"] > end - 61:
                continue
            before = [s for t, s in timeline if t <= ts]
            after = [s for t, s in timeline if t > ts]
            if before and after and before[-1] and after[0] and r["trade_id"] not in stored:
                lost.append(r["trade_id"])
    assert lost == []
    # once the clock is right again, the collector is back within two minutes
    right = T0 + 60 * right_min
    assert any(table == "snapshots" and right < t <= right + 120 for table, t, _ in rec.writes)
    assert getattr(col, "synced", True) and [w for w in col.watched()]
    # while out of sync, at most one Kalshi request a minute
    assert all(k <= 1 for i, began, ended, k in per_cycle if not began and not ended)
    if size > lim:
        assert any(not began for _, began, _, _ in per_cycle)   # it did go out of sync
    rows = ol.day_status(store, skew())
    assert all("era" not in r and (r["share"] is None or r["share"] <= 1) for r in rows)


def test_the_two_thresholds_are_named_and_both_clocks_are_injected():
    assert ol.STEP_TOLERANCE_S == 2.0 and ol.CLOCK_AGREE_S == 30.0                 # rev 7: 30 s
    assert ol.TRADE_READBACK_S == ol.TAPE_LAG_S + 2 * ol.CLOCK_AGREE_S + ol.READBACK_MARGIN_S == 150.0
    params = inspect.signature(ol.Collector).parameters
    assert "clock" in params and "monotonic" in params
    assert "monotonic" in inspect.signature(ol.main).parameters


def test_a_step_of_more_than_two_seconds_is_detected_and_one_of_less_is_not(tmp_path):
    col, world, model, store, rec, true, skew = sync_rig(tmp_path, n=2)
    col.start()
    for i in range(0, 5):
        true.t = T0 + 60 * i + 0.5
        col.cycle()
    skew.offset = 1.9                                           # within the tolerance: nothing happens
    true.t = T0 + 5 * 60 + 0.5
    col.cycle()
    kinds = [k for (k,) in store.conn.execute("SELECT kind FROM events")]
    assert col.synced and "clock_off" not in kinds
    beats = store.conn.execute("SELECT COUNT(*) FROM heartbeats").fetchone()[0]
    skew.offset = 1.9 + 2.1                                     # a step of 2.1 s
    true.t = T0 + 6 * 60 + 0.5
    before = len(world.urls)
    col.cycle()
    assert not col.synced and len(world.urls) == before         # stopped at its first clock reading
    assert store.conn.execute("SELECT COUNT(*) FROM heartbeats").fetchone()[0] == beats
    true.t = T0 + 7 * 60 + 0.5                                  # 4 s off agrees with the Date header: back
    col.cycle()
    kinds = [k for (k,) in store.conn.execute("SELECT kind FROM events ORDER BY id")]
    assert col.synced and kinds.count("clock_off") == 1 and kinds.count("clock_on") == 1
    assert store.conn.execute("SELECT COUNT(*) FROM heartbeats").fetchone()[0] == beats + 1


def test_a_date_header_more_than_30_seconds_off_stops_the_storing_until_it_agrees(tmp_path):
    col, world, model, store, rec, true, skew = sync_rig(tmp_path, n=2)
    col.start()
    for i in range(0, 5):
        true.t = T0 + 60 * i + 0.5
        col.cycle()
    snaps = store.conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
    world.date_offset = -32.0                                   # Kalshi's Date disagrees; no step on our side
    per = []
    for i in range(5, 12):
        before = len(world.urls)
        true.t = T0 + 60 * i + 0.5
        col.cycle()
        per.append(len(world.urls) - before)
    assert not col.synced and per == [1] * 7     # the read that disagreed, then one cheap request a minute
    assert store.conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == snaps   # that read thrown away
    world.date_offset = -19.0                                   # rev 8: back in sync only within 20 s
    true.t = T0 + 12 * 60 + 0.5
    col.cycle()
    kinds = [k for (k,) in store.conn.execute("SELECT kind FROM events ORDER BY id")]
    assert col.synced and kinds.count("clock_off") == 1 and kinds.count("clock_on") == 1
    assert kinds.index("clock_off") < kinds.index("clock_on") < len(kinds) - kinds[::-1].index("gap")
    assert store.conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == snaps + 2


def test_at_start_nothing_but_one_event_is_stored_until_the_clock_agrees(tmp_path):
    col, world, model, store, rec, true, skew = sync_rig(tmp_path, n=2)
    skew.offset = -150.0
    col.start()
    for i in range(0, 4):
        true.t = T0 + 60 * i + 0.5
        col.cycle()
    c = store.conn
    assert [k for (k,) in c.execute("SELECT kind FROM events")] == ["clock_off"]
    assert sum(c.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0] for t in ol.TABLES if t != "events") == 0
    assert len(world.urls) == 4                                 # one at the start, then one a minute (rev 7)
    skew.offset = 0.0
    true.t = T0 + 4 * 60 + 0.5
    col.cycle()
    assert col.synced and [w["ticker"] for w in col.watched()] and \
        c.execute("SELECT kind FROM selections").fetchone()[0] == "grid"


# ---------------------------------------------------------------- outcome 4: an overrun that also drops

def test_a_slot_skipped_by_an_overrun_gets_its_row_when_that_cycle_also_drops_the_watch(tmp_path):
    col, world, model, store, clock = started(tmp_path, n=5)    # one market on each phase
    run_minutes(col, clock, 1, 10)
    two = [w for w in col.watched() if w["phase"] == 2][0]
    world.books[two["ticker"]] = book(0.40, None)               # one-sided from 06:11: dropped at the 30th minute
    run_minutes(col, clock, 11, 40)
    assert two["watch_id"] in [w["watch_id"] for w in col.watched()]
    for m in world.markets:                                     # minute 41: every book read takes 25 s
        world.book_delay[m["ticker"]] = 25.0
    clock.t = T0 + 41 * 60 + 0.5
    col.cycle()
    for m in world.markets:
        world.book_delay[m["ticker"]] = 0.0
    assert clock.t > T0 + 43 * 60 and two["watch_id"] not in [w["watch_id"] for w in col.watched()]
    col.cycle()                                                 # the next one at once: minute 06:43
    rows = store.conn.execute("SELECT status, error FROM decisions WHERE watch_id = ? AND slot_ts = ?",
                              (two["watch_id"], T0 + 42 * 60)).fetchall()
    assert len(rows) == 1 and rows[0][0] == "late" and "overran" in rows[0][1]


# ---------------------------------------------------------------- outcome 5: abandoned request threads

def test_with_four_abandoned_requests_still_running_no_new_one_is_started():
    import threading
    gate = threading.Event()
    calls = []

    def slow(url):
        calls.append(url)
        gate.wait(10)
        return b"{}"
    get = ol.bounded_fetch(slow, limit=0.05)
    for _ in range(4):
        with pytest.raises(TimeoutError):
            get("u")
    assert len(calls) == 4
    with pytest.raises(TimeoutError, match="still running"):
        get("u")                                                # a failed request, and not started
    assert len(calls) == 4 and ol.MAX_ABANDONED == 4
    gate.set()
    for _ in range(100):
        if get.alive() == 0:
            break
        threading.Event().wait(0.01)
    gate.clear()
    assert ol.bounded_fetch(lambda url: b"ok", limit=0.05)("u") == b"ok"
    gate.set()
    assert get("u") == b"{}" and len(calls) == 5


# ================================================================= rev 7: the edges of the clock rule

BAD_DATES = ["missing", "malformed", "zoneless"]


def counts(store):
    return {t: store.conn.execute("SELECT COUNT(*) FROM %s" % t).fetchone()[0] for t in ol.TABLES}


def event_kinds(store):
    return [k for (k,) in store.conn.execute("SELECT kind FROM events ORDER BY id")]


# ---------------------------------------------------------------- outcome 1: only a Date with a zone confirms

@pytest.mark.parametrize("bad", BAD_DATES)
def test_a_date_that_cannot_be_read_never_confirms_the_clock(tmp_path, bad):
    """The verifier's case: the clock an hour behind from the start and no usable Date on any answer."""
    col, world, model, store, rec, true, skew = sync_rig(tmp_path, n=2)
    skew.offset = -3600.0
    world.date_bad = bad
    col.start()
    per = []
    for i in range(0, 30):
        before = len(world.urls)
        true.t = T0 + 60 * i + 0.5
        col.cycle()
        per.append(len(world.urls) - before)
    assert not col.synced and event_kinds(store) == ["clock_off"]
    assert sum(n for t, n in counts(store).items() if t != "events") == 0
    assert set(per[1:]) == {1}


@pytest.mark.parametrize("bad", BAD_DATES)
def test_one_answer_without_a_usable_date_changes_nothing(tmp_path, bad):
    runs = []
    for k, one_bad in enumerate((False, True)):
        col, world, model, store, rec, true, skew = sync_rig(tmp_path / str(k), n=2)
        col.start()
        for i in range(0, 25):
            if i == 10 and one_bad:
                world.date_bad, world.date_bad_left = bad, 1
            true.t = T0 + 60 * i + 0.5
            col.cycle()
            assert col.synced
        runs.append((counts(store), event_kinds(store), len(world.urls)))
    assert runs[0] == runs[1] and "clock_off" not in runs[1][1]


@pytest.mark.parametrize("bad", BAD_DATES)
def test_no_usable_date_on_every_answer_takes_it_out_of_sync_and_nothing_more_is_stored(tmp_path, bad):
    col, world, model, store, rec, true, skew = sync_rig(tmp_path, n=2)
    col.start()
    for i in range(0, 10):
        true.t = T0 + 60 * i + 0.5
        col.cycle()
    before = counts(store)
    world.date_bad = bad
    true.t = T0 + 10 * 60 + 0.5
    col.cycle()
    assert not col.synced                                       # after the third answer in a row
    after = counts(store)
    assert after["snapshots"] - before["snapshots"] <= 1        # A's book and trades (two answers); B's is dropped
    assert after["decisions"] == before["decisions"] and after["heartbeats"] == before["heartbeats"]
    for i in range(11, 20):
        true.t = T0 + 60 * i + 0.5
        col.cycle()
    assert counts(store) == {**after, "events": after["events"]}
    assert event_kinds(store).count("clock_off") == 1
    world.date_bad = None
    true.t = T0 + 20 * 60 + 0.5
    col.cycle()
    assert col.synced and counts(store)["snapshots"] == after["snapshots"] + 2


def test_five_minutes_without_a_usable_date_take_it_out_of_sync(tmp_path):
    col, world, model, store, rec, true, skew = sync_rig(tmp_path, n=2)
    col.start()
    for i in range(0, 5):
        true.t = T0 + 60 * i + 0.5
        col.cycle()
    before = counts(store)
    true.t = T0 + 4 * 60 + 0.5 + ol.DATELESS_MAX_S + 5           # no answer at all for over five minutes
    col.cycle()
    assert not col.synced and counts(store) == {**before, "events": before["events"] + 1}
    assert event_kinds(store)[-1] == "clock_off"
    true.t += 60
    col.cycle()
    assert col.synced and counts(store)["snapshots"] == before["snapshots"] + 2


# ---------------------------------------------------------------- outcome 2: a clock inside the threshold

@pytest.mark.parametrize("off", [-1.0, -0.5, 0.0, 0.5, 1.0, "drift"])
def test_with_the_clock_within_the_threshold_no_trade_is_lost_over_a_day(tmp_path, off):
    import random
    rnd = random.Random(11)
    a = ol.CLOCK_AGREE_S
    col, world, model, store, rec, true, skew = sync_rig(tmp_path, n=2)
    minutes = 1440

    def offset(i):                                              # rev 8: it starts right (entry needs 20 s) and
        b = a - 1                                               # moves 1 s a minute to its place; 29 s, as the
        if off == "drift":                                      # middle of a Date's second is what is judged
            return -min(i, b) if i <= b else -b + 2 * b * (i - b) / (minutes - b)
        return math.copysign(min(abs(off * b), i), off)
    n = 0
    for i in range(0, minutes + 1):
        for m in world.markets:
            for _ in range(2):
                n += 1
                world.trade(m["ticker"], n, T0 + 60 * (i - 1) + rnd.uniform(0, 60), lag=rnd.uniform(0, ol.TAPE_LAG_S))
    skew.offset = offset(0)
    col.start()
    for i in range(0, minutes + 1):
        skew.offset = offset(i)
        true.t = max(true.t, T0 + 60 * i + 0.5)
        col.cycle()
    c = store.conn
    assert "clock_off" not in event_kinds(store)
    first = {}                                                  # the true time each market's reads began
    for t, ts in c.execute("SELECT ticker, MIN(trades_start_ts) FROM snapshots GROUP BY ticker"):
        first[t] = skew.log[ts]
    assert set(first) == {m["ticker"] for m in world.markets}
    stored = {r[0] for r in c.execute("SELECT trade_id FROM trades")}
    end = true.t
    want = {r["trade_id"] for t, v in world.tape.items() for r in v
            if kd.iso_to_ts(r["created_time"]) >= first[t] and r["_on_tape"] <= end - 61}
    assert len(want) > 5000 and want - stored == set()


# ---------------------------------------------------------------- outcome 3: a long sleep

class Mono:
    """A monotonic clock that stops while the machine sleeps."""
    def __init__(self, true):
        self.true, self.slept = true, 0.0

    def __call__(self):
        return self.true.t - self.slept


def sleep_run(tmp_path, hours):
    import random
    rnd = random.Random(3)
    mono = {}

    def make(true):
        mono["m"] = Mono(true)
        return mono["m"]
    col, world, model, store, rec, true, skew = sync_rig(tmp_path, mono=make)
    sleep_at, wake = 40, 40 + int(hours * 60) + 1
    last = wake + 20
    n = 0
    for i in range(0, last + 1):
        for m in world.markets:
            for _ in range(2):
                n += 1
                world.trade(m["ticker"], n, T0 + 60 * (i - 1) + rnd.uniform(0, 60), lag=rnd.uniform(0, ol.TAPE_LAG_S))
    col.start()
    for i in list(range(0, sleep_at + 1)) + list(range(wake, last + 1)):
        true.t = max(true.t, T0 + 60 * i + 0.5)
        col.cycle()
        if i == sleep_at:
            slept_from = true.t                                 # the machine sleeps here
            true.t += hours * 3600
            mono["m"].slept += hours * 3600
    on = [skew.log[ts] for (ts,) in store.conn.execute("SELECT ts FROM events WHERE kind = 'clock_on'")]
    return col, world, store, true, slept_from, on


@pytest.mark.parametrize("hours", [1, 12])
def test_no_in_sync_trade_is_lost_across_a_sleep(tmp_path, hours):
    col, world, store, true, slept_from, on = sleep_run(tmp_path, hours)
    assert len(on) == 1 and col.synced
    stored = {r[0] for r in store.conn.execute("SELECT trade_id FROM trades")}
    end = true.t
    want = {r["trade_id"] for v in world.tape.values() for r in v
            if (T0 + 1 <= kd.iso_to_ts(r["created_time"]) < slept_from or kd.iso_to_ts(r["created_time"]) >= on[0])
            and r["_on_tape"] <= end - 61}
    last_minute = {r["trade_id"] for v in world.tape.values() for r in v
                   if slept_from - 60 <= kd.iso_to_ts(r["created_time"]) < slept_from}
    assert len(want) > 300 and last_minute and want - stored == set()
    assert "trades_lost" not in event_kinds(store)


def test_after_a_sleep_of_more_than_a_day_the_trades_it_lost_are_recorded(tmp_path):
    col, world, store, true, slept_from, on = sleep_run(tmp_path, 30)
    lost = [json.loads(d) for (d,) in store.conn.execute("SELECT detail_json FROM events WHERE kind = 'trades_lost'")]
    assert len(lost) == len(world.markets)
    for x in lost:
        assert x["from_ts"] < slept_from and x["to_ts"] - x["from_ts"] > 5 * 3600


# ---------------------------------------------------------------- outcome 4: one request a minute, no retry

def test_out_of_sync_exactly_one_request_a_minute_with_no_retry_from_the_first_minute(tmp_path):
    col, world, model, store, rec, true, skew = sync_rig(tmp_path, n=2, tries=3)   # retries, as `collect` has
    world.fail, world.fail_status = True, 503
    times = []
    world.on_request = lambda path: times.append(true.t)

    def wait(s):
        true.t += s
        if true.t > T0 + 10 * 60:
            col.stop.set()
    col.start()
    col.run(wait=wait)
    assert len(times) >= 10 and not col.synced
    assert times[0] < T0 + 1 and [round((t - times[0]) / 60) for t in times] == list(range(len(times)))
    assert all(b - a >= 59 for a, b in zip(times, times[1:]))


# ---------------------------------------------------------------- outcome 5: the dropping cycle ran into the slot

def test_a_slot_whose_minute_the_dropping_cycle_ran_into_gets_its_row(tmp_path):
    col, world, model, store, clock = started(tmp_path, n=5)    # one market on each phase
    run_minutes(col, clock, 1, 10)
    two = [w for w in col.watched() if w["phase"] == 2][0]
    world.books[two["ticker"]] = book(0.40, None)               # one-sided from 06:11: dropped at 06:41's cycle
    run_minutes(col, clock, 11, 40)
    for m in world.markets:                                     # 06:41's cycle runs into 06:42, not through it
        world.book_delay[m["ticker"]] = 12.5
    clock.t = T0 + 41 * 60 + 0.5
    col.cycle()
    for m in world.markets:
        world.book_delay[m["ticker"]] = 0.0
    assert T0 + 42 * 60 < clock.t < T0 + 43 * 60
    assert two["watch_id"] not in [w["watch_id"] for w in col.watched()]
    col.cycle()                                                 # the next one at once: minute 06:42
    rows = store.conn.execute("SELECT status, error FROM decisions WHERE watch_id = ? AND slot_ts = ? AND forced = 0",
                              (two["watch_id"], T0 + 42 * 60)).fetchall()
    assert len(rows) == 1 and rows[0][0] == "late" and "dropped" in rows[0][1]


# ---------------------------------------------------------------- outcome 6: what clock_off and clock_on carry

def test_clock_off_and_on_carry_both_times_and_clock_on_waits_for_a_writable_store(tmp_path):
    col, world, model, store, rec, true, skew = sync_rig(tmp_path, n=2)
    col.start()
    for i in range(0, 5):
        true.t = T0 + 60 * i + 0.5
        col.cycle()
    world.date_offset = -100.0
    true.t = T0 + 5 * 60 + 0.5
    col.cycle()
    off = json.loads(store.conn.execute("SELECT detail_json FROM events WHERE kind = 'clock_off'").fetchone()[0])
    assert off["clock_ts"] - off["date_ts"] == pytest.approx(100, abs=1.5)
    world.date_offset = 0.0
    store.conn.execute("PRAGMA query_only = 1")                 # the disk is full when it agrees again
    true.t = T0 + 6 * 60 + 0.5
    with pytest.raises(sqlite3.OperationalError):
        col.cycle()
    assert col.synced and "clock_on" not in event_kinds(store)
    store.conn.execute("PRAGMA query_only = 0")
    true.t = T0 + 7 * 60 + 0.5
    col.cycle()
    kinds = event_kinds(store)
    assert kinds.count("clock_on") == 1 and kinds.index("clock_on") < kinds.index("gap", kinds.index("clock_off"))
    on = json.loads(store.conn.execute("SELECT detail_json FROM events WHERE kind = 'clock_on'").fetchone()[0])
    assert abs(on["clock_ts"] - on["date_ts"]) <= 1.5 and on["clock_ts"] < T0 + 7 * 60



# ================================================================= rev 8: trades after any outage; agreement edges

def outage_run(tmp_path, kind, minutes, in_pause):
    """Two watched markets trading all along (tape lag up to a minute). At 06:40 either an outage begins, or
    failed book reads make the five-failure pause and the outage begins in it at 06:43. `kind`: "network" (every
    request fails), "sleep" (the machine sleeps: both clocks stop), "step" (the clock steps an hour back until it
    is set right) or "restart" (the process stops and a new one starts on the same record)."""
    import random
    rnd = random.Random(19)
    mono = {}

    def make(true):
        mono["m"] = Mono(true)
        return mono["m"]
    col, world, model, store, rec, true, skew = sync_rig(tmp_path, n=2, mono=make)
    begin = 43 if in_pause else 40
    stop = begin + minutes
    last = stop + 40
    n = 0
    for i in range(0, last + 1):
        for m in world.markets:
            for _ in range(2):
                n += 1
                world.trade(m["ticker"], n, T0 + 60 * (i - 1) + rnd.uniform(0, 60), lag=rnd.uniform(0, ol.TAPE_LAG_S))
    col.start()
    i = 0
    while i <= last:
        if in_pause and i == 40:
            world.fail_paths = {"/markets/%s/orderbook" % m["ticker"] for m in world.markets}
        if i == begin:
            assert col.paused() or not in_pause                 # the outage begins in the pause
            world.fail_paths = set()
            if kind == "network":
                world.fail = True
            elif kind == "step":
                skew.offset = -3600.0
        if i == stop:
            world.fail, skew.offset = False, 0.0
        true.t = max(true.t, T0 + 60 * i + 0.5)
        col.cycle()
        if i == begin and kind == "sleep":
            true.t += minutes * 60
            mono["m"].slept += minutes * 60
            i = stop
        elif i == begin and kind == "restart":
            true.t += minutes * 60
            kalshi = kd.KalshiData(fetch=world, sleep=true.sleep, clock=mono["m"], tries=1)
            col = make_collector(store, kalshi, ol.ModelClient(transport=model, clock=skew), clock=skew,
                                 monotonic=mono["m"])
            i = stop
            true.t = max(true.t, T0 + 60 * i + 0.5)
            col.start()
        i += 1
    return col, world, store, true


def trades_missing(world, store, true):
    stored = {r[0] for r in store.conn.execute("SELECT trade_id FROM trades")}
    return {r["trade_id"]: kd.iso_to_ts(r["created_time"]) for v in world.tape.values() for r in v
            if kd.iso_to_ts(r["created_time"]) >= T0 + 2 and r["_on_tape"] <= true.t - 61 and r["trade_id"] not in stored}


OUTAGES = [("network", 65, True), ("network", 120, True), ("network", 780, True),
           ("network", 65, False), ("network", 120, False), ("network", 780, False),
           ("sleep", 120, True), ("step", 120, True), ("restart", 180, False)]


@pytest.mark.parametrize("kind,minutes,in_pause", OUTAGES,
                         ids=["%s-%dm-%s" % (k, m, "in_pause" if p else "outside") for k, m, p in OUTAGES])
def test_after_any_outage_the_trades_are_caught_up(tmp_path, kind, minutes, in_pause):
    col, world, store, true = outage_run(tmp_path, kind, minutes, in_pause)
    assert col.synced and len(col.watched()) == 2
    assert trades_missing(world, store, true) == {}
    assert "trades_lost" not in event_kinds(store)


@pytest.mark.parametrize("minutes,lost", [(23 * 60 + 30, False), (24 * 60 + 30, True)])
def test_a_trades_lost_event_exactly_when_the_hole_is_over_24_hours(tmp_path, minutes, lost):
    col, world, store, true = outage_run(tmp_path, "network", minutes, True)
    events = [json.loads(d) for (d,) in store.conn.execute("SELECT detail_json FROM events WHERE kind = 'trades_lost'")]
    missing = trades_missing(world, store, true)
    if not lost:
        assert events == [] and missing == {}
    else:
        assert len(events) == 2 and missing
        to = min(e["to_ts"] for e in events)
        assert all(ts < to for ts in missing.values())          # only what lies beyond the 24 hours


# ---------------------------------------------------------------- outcome 2: agreement does not grow with the request

@pytest.mark.parametrize("took", [0.0, 1.0, 2.5, 4.9, 5.5, 7.5, 10.0])
def test_no_row_is_stored_with_the_clock_more_than_33_seconds_off_whatever_the_request_time(tmp_path, took):
    col, world, model, store, rec, true, skew = sync_rig(tmp_path, n=1)
    world.request_delay = took
    path = list(range(0, 46)) + list(range(44, -46, -1)) + list(range(-44, 1))   # 1 s a minute, no step
    col.start()
    for i, off in enumerate(path):
        skew.offset = float(off)
        true.t = max(true.t, T0 + 60 * i + 0.5)
        col.cycle()
    kept = ("snapshots", "trades", "decisions", "heartbeats")
    offs = [abs(off) for table, _, off in rec.writes if table in kept]
    assert all(o <= 33 for o in offs)
    if took <= ol.DATE_MAX_TOOK_S:
        assert max(offs) >= 28 and col.synced                   # it does work up to near the edge, and returns
    else:
        assert offs == []                                       # no answer that slow confirms the clock


def test_a_clock_31_seconds_off_leaves_once_and_returns_only_within_20_seconds(tmp_path):
    col, world, model, store, rec, true, skew = sync_rig(tmp_path, n=1)
    path = list(range(0, 32)) + [31] * 10 + list(range(30, 20, -1)) + [21] * 10 + [19] * 3
    col.start()
    for i, off in enumerate(path):
        skew.offset = float(off)
        true.t = max(true.t, T0 + 60 * i + 0.5)
        col.cycle()
        if off > 20 and i > 31:
            assert "clock_on" not in event_kinds(store)
    kinds = event_kinds(store)
    assert kinds.count("clock_off") == 1 and kinds.count("clock_on") == 1 and col.synced
    on = store.conn.execute("SELECT ts FROM events WHERE kind = 'clock_on'").fetchone()[0]
    assert on - skew.log[on] == pytest.approx(19.0)


# ---------------------------------------------------------------- outcome 3: three small things

def test_a_minute_a_cycle_runs_right_through_still_gets_its_heartbeat(tmp_path):
    col, world, model, store, clock = started(tmp_path, n=5)
    run_minutes(col, clock, 1, 40)
    for m in world.markets:                                     # Kalshi slow: the cycle takes over a minute
        world.book_delay[m["ticker"]] = 14.0
    clock.t = T0 + 41 * 60 + 50
    col.cycle()
    assert clock.t > T0 + 43 * 60
    beats = {int((ts - T0) // 60) for (ts,) in store.conn.execute("SELECT ts FROM heartbeats")}
    assert 42 in beats and 43 in beats
    row = ol.day_status(store, clock.t)[0]
    _, _, watched = independent_status(store, clock.t)
    assert row["watched_minutes"] == pytest.approx(watched["2026-11-13"])


def test_a_clock_off_the_store_could_not_take_is_written_when_it_can_and_before_clock_on(tmp_path):
    for later in (True, False):                                 # writable while still off, or only once it agrees
        col, world, model, store, rec, true, skew = sync_rig(tmp_path / str(later), n=2)
        col.start()
        for i in range(0, 5):
            true.t = T0 + 60 * i + 0.5
            col.cycle()
        store.conn.execute("PRAGMA query_only = 1")
        world.date_offset = -100.0
        true.t = T0 + 5 * 60 + 0.5
        col.cycle()
        assert not col.synced and "clock_off" not in event_kinds(store)
        true.t = T0 + 6 * 60 + 0.5
        col.cycle()
        if later:
            store.conn.execute("PRAGMA query_only = 0")
            true.t = T0 + 7 * 60 + 0.5
            col.cycle()
            assert event_kinds(store)[-1] == "clock_off" and not col.synced
        store.conn.execute("PRAGMA query_only = 0")
        world.date_offset = 0.0
        true.t = T0 + 8 * 60 + 0.5
        col.cycle()
        kinds = event_kinds(store)
        assert col.synced and kinds.count("clock_off") == 1 and kinds.index("clock_off") < kinds.index("clock_on")
        off = json.loads(store.conn.execute("SELECT detail_json FROM events WHERE kind = 'clock_off'").fetchone()[0])
        assert int((off["clock_ts"] - T0) // 60) == 5           # placed where it happened


def test_the_docstring_no_longer_says_a_pause_makes_no_request_or_most_slots_are_late_at_0_9_s():
    doc = " ".join(ol.__doc__.split())
    assert "makes no request for five minutes" not in doc and "0.9 s a request and more" not in doc



# ================================================================= rev 9: invariants

def assert_warm_rule(store, now):
    """No decision, and no slot counted as due, before the watch's market has an unbroken 30-minute run."""
    warm = warm_times_from_rows(store)
    bad = [(wid, slot) for wid, slot in store.conn.execute("SELECT watch_id, slot_ts FROM decisions WHERE forced = 0")
           if warm[wid] is None or slot < warm[wid]]
    assert bad == []
    expected, _, _ = independent_status(store, now)
    assert {r["day"]: r["expected"] for r in ol.day_status(store, now) if r["expected"]} == \
        {d: n for d, n in expected.items() if n}


def run_cycle(col):
    """One cycle as `run` makes it: a failure is recorded (or, with the store refusing writes, reported)."""
    try:
        col.cycle()
    except Exception as e:
        col._record_failure(e)
    except ol._OutOfSync as e:
        col._go_off(str(e))


def scenario(tmp_path, events, last, n=2, slots=None, trades=True, seed=0, per_minute=2):
    """A run on the test's own true clock with `events`, each (minute, kind, length[, market]): "network" (every
    request fails), "pause" (book reads fail for 3 minutes: the five-failure pause), "step" (the clock an hour
    back), "unwritable" (the store refuses writes), "one_sided" (that market's book one-sided), "sleep" (both
    clocks stop: the machine sleeps) and "restart" (a new process on the same record), each for `length` minutes;
    rev 10: "trades_fail" (that market's trade reads fail, its book answers)."""
    import random
    rnd = random.Random(seed)
    mono = {}

    def make(true):
        mono["m"] = Mono(true)
        return mono["m"]
    col, world, model, store, rec, true, skew = sync_rig(tmp_path, n=n, mono=make)
    if slots:
        col.slots = slots
    if trades:
        k = 0
        for i in range(0, last + 1):
            for m in world.markets:
                for _ in range(per_minute):
                    k += 1
                    world.trade(m["ticker"], k, T0 + 60 * (i - 1) + rnd.uniform(0, 60), lag=rnd.uniform(0, ol.TAPE_LAG_S))
    books = {m["ticker"]: book(0.40, 0.45) for m in world.markets}
    col.start()
    ends: dict = {}
    i, seen = 0, -1                                             # rev 10: what began or ended inside a jump, at once
    while i <= last:
        for e in events:
            start, kind, length = e[:3]
            if seen < start <= i:
                if kind == "network":
                    world.fail = True
                elif kind == "pause":
                    world.fail_paths = {"/markets/%s/orderbook" % m["ticker"] for m in world.markets}
                    length = 3
                elif kind == "step":
                    skew.offset = -3600.0
                elif kind == "unwritable":
                    store.conn.execute("PRAGMA query_only = 1")
                elif kind == "one_sided":
                    world.books[world.markets[e[3]]["ticker"]] = book(0.40, None)
                elif kind == "trades_fail":
                    world.fail_trades.add(world.markets[e[3]]["ticker"])
                ends.setdefault(start + length, []).append(e)
        for e in [x for k in sorted(k for k in ends if k <= i) for x in ends.pop(k)]:
            kind = e[1]
            if kind == "network":
                world.fail = False
            elif kind == "pause":
                world.fail_paths = set()
            elif kind == "step":
                skew.offset = 0.0
            elif kind == "unwritable":
                store.conn.execute("PRAGMA query_only = 0")
            elif kind == "one_sided":
                t = world.markets[e[3]]["ticker"]
                world.books[t] = books[t]
            elif kind == "trades_fail":
                world.fail_trades.discard(world.markets[e[3]]["ticker"])
        seen = i
        true.t = max(true.t, T0 + 60 * i + 0.5)
        run_cycle(col)
        jump = [e for e in events if e[0] == i and e[1] in ("sleep", "restart")]
        if jump:
            kind, length = jump[0][1], jump[0][2]
            true.t += length * 60
            if kind == "sleep":
                mono["m"].slept += length * 60
            i += length
            if kind == "restart":
                kalshi = kd.KalshiData(fetch=world, sleep=true.sleep, clock=mono["m"], tries=1)
                col = make_collector(store, kalshi, ol.ModelClient(transport=model, clock=skew), clock=skew,
                                     monotonic=mono["m"], slots=slots or ol.WATCH_COUNT)
                true.t = max(true.t, T0 + 60 * i + 0.5)
                col.start()
        i += 1
    return col, world, store, true, skew


# ---------------------------------------------------------------- invariant 1: warm-up needs an unbroken run

GAPS = ([("network", L, b) for b in ("warm_up", "outside", "in_pause") for L in (6, 30, 65, 120, 780, 1500)]
        + [("sleep", 120, "in_pause"), ("sleep", 780, "outside"), ("restart", 180, "outside"),
           ("restart", 780, "outside"), ("restart", 20, "warm_up"), ("unwritable", 20, "warm_up"),
           ("unwritable", 65, "outside"), ("unwritable", 65, "in_pause")])


@pytest.mark.parametrize("kind,minutes,begin", GAPS, ids=["%s-%dm-%s" % g for g in GAPS])
def test_no_decision_before_an_unbroken_30_minute_run_whatever_the_gap(tmp_path, kind, minutes, begin):
    """The verifier's case is network-780m-in_pause: a 13-hour outage that begins in a pause."""
    at = {"warm_up": 10, "outside": 40, "in_pause": 43}[begin]
    events = [(40, "pause", 3)] if begin == "in_pause" else []
    events.append((at, kind, minutes))
    last = at + minutes + 75
    col, world, store, true, skew = scenario(tmp_path, events, last, trades=False)
    assert col.synced
    assert store.conn.execute("SELECT COUNT(*) FROM decisions WHERE forced = 0").fetchone()[0] > 0
    assert_warm_rule(store, skew())


def random_events(rnd, n):
    """A seeded sequence of gaps and re-choices that do not overlap."""
    events, t = [], rnd.randint(5, 45)
    for _ in range(rnd.randint(2, 6)):
        kind = rnd.choice(["network", "network", "pause", "step", "unwritable", "one_sided", "one_sided", "sleep",
                           "restart"])
        length = rnd.choice([2, 5, 20, 40, 70, 200, 760, 1500]) if kind not in ("pause", "one_sided") else \
            (3 if kind == "pause" else rnd.choice([31, 40, 60]))
        events.append((t, kind, length) + ((rnd.randrange(n),) if kind == "one_sided" else ()))
        t += (length if kind != "pause" else 3) + rnd.randint(3, 50)
    return events, t + rnd.randint(35, 70)


@pytest.mark.parametrize("chunk", range(8))
def test_the_warm_up_rule_holds_over_generated_sequences_of_gaps_and_re_choices(tmp_path, chunk):
    import random
    for seed in range(25 * chunk, 25 * chunk + 25):              # 200 sequences in all
        rnd = random.Random(1000 + seed)
        n = rnd.choice([2, 3])
        events, last = random_events(rnd, n)
        col, world, store, true, skew = scenario(tmp_path / str(seed), events, last, n=n,
                                                 slots=rnd.choice([1, n]), trades=False, seed=seed)
        try:
            assert_warm_rule(store, skew())
        except AssertionError as e:
            raise AssertionError("seed %d, events %s: %s" % (seed, events, e)) from None


# ---------------------------------------------------------------- invariant 2: a watch never read

@pytest.mark.parametrize("minutes", [65, 120])
def test_an_outage_between_a_selections_book_reads_and_its_commit_loses_no_trade(tmp_path, minutes):
    import random
    rnd = random.Random(23)
    col, world, model, store, rec, true, skew = sync_rig(tmp_path, n=2)
    last = minutes + 60
    k = 0
    for i in range(0, last + 1):
        for m in world.markets:
            for _ in range(2):
                k += 1
                world.trade(m["ticker"], k, T0 + 60 * (i - 1) + rnd.uniform(0, 60), lag=rnd.uniform(0, ol.TAPE_LAG_S))
    began = {}

    def hook(path):
        if path.startswith("/series/") and not began:            # after the books, before the commit
            began["t"] = true.t
            world.fail = True
    world.on_request = hook
    col.start()
    for i in range(0, last + 1):
        if began and i == int((began["t"] - T0) // 60) + minutes:
            world.fail = False
        true.t = max(true.t, T0 + 60 * i + 0.5)
        col.cycle()
    c = store.conn
    start = c.execute("SELECT MIN(watch_start_ts) FROM markets").fetchone()[0]
    assert began and start < began["t"] and len(col.watched()) == 2
    stored = {r[0] for r in c.execute("SELECT trade_id FROM trades")}
    want = {r["trade_id"] for v in world.tape.values() for r in v
            if kd.iso_to_ts(r["created_time"]) >= start and r["_on_tape"] <= true.t - 61}
    assert len(want) > 200 and want - stored == set()
    assert "trades_lost" not in event_kinds(store)


# ---------------------------------------------------------------- invariant 3: trade windows need every read

def window_read(snaps, a, minutes):
    """From stored snapshot rows: whether the good trade reads cover (a - 60 * minutes, a] without a hole."""
    spans = sorted((s["trades_min_ts"], s["trades_start_ts"]) for s in snaps if s["trades_ok"])
    lo, hi = a - 60 * minutes, a
    at = lo
    for x, y in spans:
        if x <= at < y or x <= at <= y:
            at = max(at, y)
    return at >= hi


@pytest.mark.parametrize("failing", [1, 5, 20])
def test_a_trade_window_is_null_unless_every_read_covering_it_succeeded(tmp_path, failing):
    import random
    runs = []
    for k, fail in enumerate((False, True)):
        rnd = random.Random(29)
        col, world, model, store, rec, true, skew = sync_rig(tmp_path / str(k), n=5)
        n = 0
        for i in range(0, 101):
            for m in world.markets:
                for _ in range(3):
                    n += 1
                    world.trade(m["ticker"], n, T0 + 60 * (i - 1) + rnd.uniform(0, 60), lag=rnd.uniform(0, 20))
        col.start()
        for i in range(0, 101):
            if fail and i == 60:
                world.fail_paths = {"/markets/trades"}          # books still answer, trade reads fail
            if fail and i == 60 + failing:
                world.fail_paths = set()
            true.t = max(true.t, T0 + 60 * i + 0.5)
            col.cycle()
        rows = store.conn.execute("SELECT watch_id, ticker, slot_ts, decision_ts, state_json FROM decisions "
                                  "WHERE forced = 0 AND state_json IS NOT NULL ORDER BY id").fetchall()
        runs.append((store, {(tk, slot): (wid, t, json.loads(st)) for wid, tk, slot, t, st in rows}))
    (_, control), (store, failed) = runs
    unread = 0
    for key, (wid, t, state) in failed.items():
        snaps, _, _ = store.history(wid, t)
        valid = [s for s in snaps if not s["invalid"]]
        a = valid[-1]["book_end_ts"]
        for w in (5, 15):
            names = ("trades_%dm" % w, "volume_%dm" % w, "yes_taker_share_%dm" % w)
            if not window_read(snaps, a, w):
                unread += 1
                assert all(state[x] is None for x in names), (key, w, state)
            elif key in control:
                assert [state[x] for x in names] == [control[key][2][x] for x in names], (key, w)
    assert unread > 0


# ---------------------------------------------------------------- low 4 to 8

def test_two_clock_offs_waiting_for_the_store_are_both_written_in_order(tmp_path):
    col, world, model, store, rec, true, skew = sync_rig(tmp_path, n=2)
    col.start()
    for i in range(0, 5):
        true.t = T0 + 60 * i + 0.5
        col.cycle()
    store.conn.execute("PRAGMA query_only = 1")                 # the disk is full from here
    world.date_offset = -100.0
    true.t = T0 + 5 * 60 + 0.5
    run_cycle(col)                                              # off (a Date 100 s away): not written
    world.date_offset = 0.0
    true.t = T0 + 6 * 60 + 0.5
    run_cycle(col)                                              # right again, but clock_on cannot be written
    assert col.synced
    skew.offset = -3600.0
    true.t = T0 + 7 * 60 + 0.5
    run_cycle(col)                                              # the clock steps: off again, not written either
    assert not col.synced
    skew.offset = 0.0
    store.conn.execute("PRAGMA query_only = 0")
    for i in (8, 9):
        true.t = T0 + 60 * i + 0.5
        run_cycle(col)
    rows = [(k, json.loads(d)) for k, d in store.conn.execute("SELECT kind, detail_json FROM events ORDER BY id")]
    offs = [d for k, d in rows if k == "clock_off"]
    assert len(offs) == 2 and "Date" in offs[0]["why"] and "stepped" in offs[1]["why"]
    # rev 10: the return to sync in between gets its own clock_on, after its clock_off
    assert col.synced and [k for k, _ in rows if k in ("clock_off", "clock_on")] == ["clock_off", "clock_on"] * 2


def test_an_unreadable_cut_short_tail_gives_one_trades_lost_across_restarts(tmp_path):
    col, world, model, store, rec, true, skew = sync_rig(tmp_path, n=3)
    col.start()
    for i in range(0, 41):
        true.t = T0 + 60 * i + 0.5
        col.cycle()
    gone = world.markets[0]["ticker"]
    world.fail = True                                           # 13 hours without Kalshi: every watch runs out
    for i in range(41, 821):
        true.t = T0 + 60 * i + 0.5
        run_cycle(col)
    world.fail = False
    world.markets = world.markets[1:]                           # not chosen again,
    world.fail_paths = {"/markets/%s/orderbook" % gone}         # and its book can no longer be read
    lost = []
    for k in range(4):                                          # four processes on the same record
        for i in range(6):
            true.t += 60
            run_cycle(col)
        lost.append([t for (t,) in store.conn.execute("SELECT ticker FROM events WHERE kind = 'trades_lost'")])
        kalshi = kd.KalshiData(fetch=world, sleep=true.sleep, clock=true.mono, tries=1)
        col = make_collector(store, kalshi, ol.ModelClient(transport=model, clock=skew), clock=skew,
                             monotonic=true.mono)
        col.start()
    tries = store.conn.execute("SELECT COUNT(*) FROM events WHERE kind = 'catch_up_failed' AND ticker = ?",
                               (gone,)).fetchone()[0]
    assert lost[0].count(gone) == 1 and lost[-1].count(gone) == 1 and tries == ol.CATCH_UP_TRIES


def test_the_minute_in_which_the_first_overrunning_cycle_starts_gets_its_heartbeat(tmp_path):
    col, world, model, store, clock = started(tmp_path, n=5)
    run_minutes(col, clock, 1, 40)
    for m in world.markets:
        world.book_delay[m["ticker"]] = 14.0                    # 70 s of books: into 06:42
    clock.t = T0 + 41 * 60 + 0.5
    col.cycle()
    assert clock.t > T0 + 42 * 60
    beats = {int((ts - T0) // 60) for (ts,) in store.conn.execute("SELECT ts FROM heartbeats")}
    assert {40, 41, 42} <= beats


def test_a_pause_that_begins_inside_a_selection_stops_its_market_pages(tmp_path):
    col, world, model, store, clock = rig(tmp_path)
    world.markets = [mkt("M%03d-E-M" % i, vol=1000 + i) for i in range(40)]
    for m in world.markets:
        world.books[m["ticker"]] = book(0.40, 0.45)
    col.start()
    col.select()
    run_minutes(col, clock, 1, 3)
    world.page_size, world.markets_delay = 5, 8 * 25.0           # eight pages of 25 s each: into the next minutes
    world.fail_paths = {"/markets/%s/orderbook" % m["ticker"] for m in world.markets}
    seen = []
    world.on_request = lambda path: seen.append((path, col.paused()))
    clock.t = T0 + 4 * 60 + 0.5
    assert col.select(kind="grid") is None                      # a cycle inside it fails five times: a pause
    kinds = event_kinds(store)
    assert "pause" in kinds and kinds[-1] == "selection_aborted"
    assert [p for p, paused in seen if paused and p != ol.SYNC_PATH] == []
    assert 0 < len([p for p, _ in seen if p == "/markets"]) < 8


def test_the_docstring_says_catch_up_tries_are_deliberate_and_what_a_pause_still_sends():
    doc = " ".join(ol.__doc__.split())
    assert "deliberate" in doc and ol.CATCH_UP_TRIES == 3
    assert "a pause that begins inside a selection" in doc and "selection_aborted" in doc


# ================================================================= rev 10: the trade catch-up, compound sequences

def trades_uncovered(world, store, now, margin=200.0):
    """From the stored rows and events alone, against the test's own tape: the trades made while their market was
    watched (from its watch's start to its drop, or now) that the tape offered, neither stored nor inside a
    `trades_lost` interval of that market. Trades of the last `margin` seconds are left out (not read yet)."""
    c = store.conn
    stored = {r[0] for r in c.execute("SELECT trade_id FROM trades")}
    lost: dict = {}
    for tk, d in c.execute("SELECT ticker, detail_json FROM events WHERE kind = 'trades_lost'"):
        d = json.loads(d)
        lost.setdefault(tk, []).append((d["from_ts"], d["to_ts"]))
    watched: dict = {}
    for tk, a, b in c.execute("SELECT m.ticker, COALESCE(m.watch_start_ts, m.selected_ts), d.dropped_ts FROM markets m "
                              "LEFT JOIN market_drops d ON d.watch_id = m.watch_id"):
        watched.setdefault(tk, []).append((a, b if b is not None else now))
    out = []
    for tk, rows in world.tape.items():
        for r in rows:
            ts = kd.iso_to_ts(r["created_time"])
            if ts > now - margin or r["_on_tape"] > now - 61 or r["trade_id"] in stored:
                continue
            if any(a <= ts <= b for a, b in watched.get(tk, [])) and not any(a <= ts <= b for a, b in lost.get(tk, [])):
                out.append((r["trade_id"], ts))
    return out


@pytest.mark.parametrize("hole", ["network", "restart", "sleep"])
def test_trade_reads_failing_on_after_a_hole_over_24_hours_extend_the_lost_interval(tmp_path, hole):
    """The verifier's first sequence: a hole of 25 hours, then market A's trade reads go on failing for 4 hours
    while its books answer, then they succeed."""
    events = [(39, "trades_fail", 1500 + 240 + 2, 0), (40, hole, 1500)]
    col, world, store, true, skew = scenario(tmp_path, events, 39 + 1742 + 60, n=2, per_minute=1)
    assert col.synced and not world.fail_trades
    a = world.markets[0]["ticker"]
    lost = [json.loads(d) for (d,) in store.conn.execute("SELECT detail_json FROM events WHERE kind = 'trades_lost' "
                                                         "AND ticker = ?", (a,))]
    assert lost and max(x["to_ts"] for x in lost) > T0 + 60 * (39 + 1742) - ol.CATCH_UP_S - 300
    assert trades_uncovered(world, store, true.t) == []


@pytest.mark.parametrize("restarts", [False, True])
def test_each_watch_of_a_market_whose_trades_cannot_be_read_gets_its_own_catch_up_and_trades_lost(tmp_path, restarts):
    """The verifier's second sequence: a market whose trade reads never succeed is dropped, its tail fails its
    three tries and gets `trades_lost`, then it is chosen again and dropped again."""
    events = [(0, "trades_fail", 260, 0), (5, "one_sided", 70, 0), (100, "one_sided", 75, 0)]
    if restarts:                                                # one inside each set of tries, one later
        events += [(71, "restart", 1), (166, "restart", 1), (205, "restart", 1)]
    col, world, store, true, skew = scenario(tmp_path, events, 280, n=1, slots=1, per_minute=1)
    c = store.conn
    a = world.markets[0]["ticker"]
    assert c.execute("SELECT COUNT(*) FROM markets").fetchone()[0] >= 2          # chosen again
    assert c.execute("SELECT COUNT(*) FROM market_drops").fetchone()[0] >= 2     # and dropped again
    lost = [json.loads(d) for (d,) in c.execute("SELECT detail_json FROM events WHERE kind = 'trades_lost'")]
    assert len([x for x in lost if x.get("cause") == "tail"]) == 2                 # one a watch, across restarts
    tries = c.execute("SELECT COUNT(*) FROM events WHERE kind = 'catch_up_failed' AND ticker = ?", (a,)).fetchone()[0]
    assert tries == 2 * ol.CATCH_UP_TRIES
    assert trades_uncovered(world, store, true.t) == []


def random_trade_events(rnd, n):
    """A seeded sequence: trade-read failures of single markets (some lasting through a hole of over 24 hours),
    long holes, drops (one-sided books) inside a failure and the re-choices after them, restarts, sleeps and
    network outages; then everything answers again, with time to catch up."""
    events, t = [], rnd.randint(3, 40)
    for _ in range(rnd.randint(2, 5)):
        kind = rnd.choice(["trades_fail", "trades_fail", "trades_fail", "one_sided", "one_sided", "restart",
                           "sleep", "network"])
        k = rnd.randrange(n)
        if kind == "trades_fail":
            length = rnd.choice([2, 10, 45, 120, 400, 1700, 1900])
            events.append((t, kind, length, k))
            if length >= 1700:                                  # it lasts through a hole of over 24 hours
                hole = rnd.choice([1450, 1600])
                events.append((t + rnd.randint(1, 30), rnd.choice(["restart", "sleep"]), hole))
                t = events[-1][0] + hole + rnd.randint(2, 60)
            elif length >= 120 and rnd.random() < 0.7:           # drops (and re-choices) inside it
                for _ in range(rnd.randint(1, 2)):
                    events.append((t + rnd.randint(1, 10), "one_sided", rnd.choice([31, 40]), k))
                    t = events[-1][0] + events[-1][2] + rnd.randint(5, 50)
            else:
                t += rnd.randint(1, length + 30)
            continue
        if kind == "one_sided":
            length = rnd.choice([31, 40, 80])
            events.append((t, kind, length, k))
        elif kind == "network":
            length = rnd.choice([5, 30, 90])
            events.append((t, kind, length))
        else:
            length = rnd.choice([3, 60, 300, 1450, 1600])
            events.append((t, kind, length))
        t += length + rnd.randint(2, 30)
    end = max([e[0] + e[2] for e in events] + [t])
    return events, end + 80                                     # time to catch up afterwards


@pytest.mark.parametrize("chunk", range(8))
def test_every_watched_trade_is_stored_or_covered_over_generated_sequences(tmp_path, chunk):
    import random
    for seed in range(25 * chunk, 25 * chunk + 25):              # 200 sequences in all
        rnd = random.Random(5000 + seed)
        n = rnd.choice([1, 2])
        events, last = random_trade_events(rnd, n)
        col, world, store, true, skew = scenario(tmp_path / str(seed), events, last, n=n, slots=rnd.choice([1, n]),
                                                 seed=seed, per_minute=1)
        missing = trades_uncovered(world, store, true.t)
        assert missing == [], "seed %d, events %s: %d uncovered, first %s" % (seed, events, len(missing), missing[:3])


def test_every_clock_off_written_is_followed_by_its_clock_on_in_order(tmp_path):
    col, world, model, store, rec, true, skew = sync_rig(tmp_path, n=2)
    col.start()
    for i in range(0, 5):
        true.t = T0 + 60 * i + 0.5
        col.cycle()
    store.conn.execute("PRAGMA query_only = 1")                 # the disk is full while the clock flaps
    for i in range(5, 14):
        skew.offset = -3600.0 if i % 2 else 0.0                 # off (a step), then right again, five times
        true.t = T0 + 60 * i + 0.5
        run_cycle(col)
    skew.offset = 0.0
    store.conn.execute("PRAGMA query_only = 0")
    for i in (14, 15):
        true.t = T0 + 60 * i + 0.5
        run_cycle(col)
    kinds = [k for k in event_kinds(store) if k in ("clock_off", "clock_on")]
    assert col.synced and kinds == ["clock_off", "clock_on"] * 5


# ---------------------------------------------------------------- a store written by 09ecec7 (rev 9, collecting)

OLD_PATH = ol.Path(__file__).resolve().parents[2] / ".foreman" / "scratch" / "T12r10_orderbook_loop_09ecec7.py"
OLD_SHA = "63c7ef66e5edec60343dd0400e83aaed02b2f775fe9002eff2d8e006b00465e3"


def rev9_module():
    """09ecec7's orderbook_loop.py, exported to the scratch folder (`git show 09ecec7:...`), checked by its hash."""
    import hashlib
    import importlib.util
    if not OLD_PATH.exists():
        pytest.skip("09ecec7's module is not exported to %s" % OLD_PATH)
    assert hashlib.sha256(OLD_PATH.read_bytes()).hexdigest() == OLD_SHA
    import sys
    spec = importlib.util.spec_from_file_location("orderbook_loop_09ecec7", OLD_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod                                # its dataclasses look their module up
    spec.loader.exec_module(mod)
    return mod


def test_a_store_written_by_09ecec7_is_continued_and_stays_readable_by_it(tmp_path):
    import random
    old = rev9_module()
    rnd = random.Random(31)
    true = Clock()
    mono, skew = true.mono, Skewed(true)
    world, model = World(true), Model(true)
    world.markets = [mkt("%s-E-M" % chr(65 + i), vol=1000 + i, close=T0 + 5 * 86400) for i in range(3)]
    for m in world.markets:
        world.books[m["ticker"]] = book(0.40, 0.45)
    for i in range(0, 200):
        for m in world.markets:
            world.trade(m["ticker"], i * 10 + ord(m["ticker"][0]), T0 + 60 * (i - 1) + rnd.uniform(0, 60),
                        lag=rnd.uniform(0, ol.TAPE_LAG_S))
    path = tmp_path / "loop.sqlite"

    def make(mod):
        kalshi = kd.KalshiData(fetch=world, sleep=true.sleep, clock=mono, tries=1)
        return mod.Collector(mod.Store(path), kalshi, mod.ModelClient(transport=model, clock=skew), clock=skew,
                             monotonic=mono)

    def minutes(col, a, b):
        for i in range(a, b):
            true.t = max(true.t, T0 + 60 * i + 0.5)
            try:
                col.cycle()
            except Exception as e:
                raise AssertionError("minute %d: %r" % (i, e)) from None

    col = make(old)                                             # 09ecec7 writes the first 70 minutes
    col.start()
    world.fail_trades = {"A-E-M"}
    minutes(col, 0, 20)
    world.fail_trades = set()
    minutes(col, 20, 70)
    col.finish("stop")
    col = make(ol)                                              # this version continues the same store
    col.start()
    world.fail_trades = {"B-E-M"}
    minutes(col, 70, 90)
    world.fail_trades = set()
    minutes(col, 90, 150)
    col.finish("stop")
    store = ol.Store(path)
    c = store.conn
    assert {r[0] for r in c.execute("SELECT DISTINCT watch_id FROM decisions WHERE slot_ts > ?", (T0 + 4200,))} == \
        {r[0] for r in c.execute("SELECT watch_id FROM markets")}                # deciding on, no new warm-up
    assert trades_uncovered(world, store, true.t) == []
    rows = old.day_status(old.Store(path), true.t)              # and 09ecec7 still reads it,
    assert old.format_status(rows) and rows[0]["expected"] == ol.day_status(store, true.t)[0]["expected"]
    col = make(old)                                             # and goes on writing it
    col.start()
    minutes(col, 150, 160)
    n = c.execute("SELECT COUNT(*) FROM snapshots WHERE book_end_ts > ?", (T0 + 150 * 60,)).fetchone()[0]
    assert n == 3 * 10
