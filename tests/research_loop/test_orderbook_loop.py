"""The order-book loop's collector: selection, snapshots, the model's state, the battery, decisions, health and
the record. Everything runs on a stand-in Kalshi, a stand-in model and a clock that moves only when told to."""
import http.client
import json
import sqlite3
import urllib.error
from collections import Counter
import urllib.parse
from datetime import datetime, timezone

import pytest

from valedia_trader.core import legacy
from valedia_trader.research_loop import orderbook_loop as ol
from valedia_trader.venues import kalshi_data as kd

v3 = legacy.load("systemone_v3_candidate")
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

    def __call__(self, url):
        self.urls.append(url)
        parts = urllib.parse.urlsplit(url)
        assert url.startswith(kd.BASE_URL + "/") and parts.scheme == "https"
        path, q = parts.path.split("/trade-api/v2", 1)[1], dict(urllib.parse.parse_qsl(parts.query))
        if self.fail or path in self.fail_paths:
            raise kd.HttpError(404, url)
        if path == "/markets":
            size = self.page_size or max(len(self.markets), 1)
            pages = max(1, -(-len(self.markets) // size))
            self.clock.t += self.markets_delay / pages
            at = int(q.get("cursor") or 0)
            nxt = str(at + size) if at + size < len(self.markets) else ""
            return json.dumps({"markets": self.markets[at:at + size], "cursor": nxt}).encode()
        if path == "/markets/trades":
            rows = [r for r in self.tape.get(q["ticker"], [])
                    if int(q["min_ts"]) <= kd.iso_to_ts(r["created_time"]) <= self.clock.t]
            rows.sort(key=lambda r: r["created_time"], reverse=True)
            return json.dumps({"trades": rows, "cursor": ""}).encode()
        if path.endswith("/orderbook"):
            self.clock.t += self.book_delay.get(path.split("/")[2], 0.0)
            if path.split("/")[2] in self.raw_books:
                return json.dumps({"orderbook_fp": self.raw_books[path.split("/")[2]]}).encode()
            b = self.books.get(path.split("/")[2], {"yes": [], "no": []})
            return json.dumps({"orderbook_fp": {"yes_dollars": levels_api(b["yes"]),
                                                "no_dollars": levels_api(b["no"])}}).encode()
        if path.startswith("/series/"):
            s = path.split("/")[2]
            return json.dumps({"series": {"ticker": s, **self.fees.get(s, {"fee_type": "quadratic",
                                                                          "fee_multiplier": 1})}}).encode()
        raise kd.HttpError(404, url)

    def trade(self, ticker, n, ts, side="yes", count=1.0):
        self.tape.setdefault(ticker, []).append(
            {"trade_id": "%s-%d" % (ticker, n), "ticker": ticker, "created_time": iso(ts), "yes_price_dollars": "0.4800",
             "no_price_dollars": "0.5200", "count_fp": "%.2f" % count, "taker_side": side, "is_block_trade": False})


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
    col = ol.Collector(store, kalshi, ol.ModelClient(transport=model, clock=clock), clock=clock)
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
    assert snaps[1][0] == int(snaps[0][1] - 5) and [s[2] for s in snaps] == [2, 1]
    tr = [u for u in world.urls if "/markets/trades" in u]
    assert "min_ts=%d" % int(snaps[0][1] - 5) in tr[1]


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
    assert ol.BATTERY_ID == "6e7beea638da51f1be273cc2815391d2300fe8b75da6e3e7bafd80405f2f21f2"


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
    run_minutes(col, clock, 3, 7)                         # paused: no request at all, a heartbeat each minute
    assert len(world.urls) == before
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
    run_minutes(col, clock, 211, 211)                     # 09:31, within 30 minutes of B's close at 10:00
    run_minutes(col, clock, 720, 721)
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
    col2 = ol.Collector(store2, kd.KalshiData(fetch=world, sleep=clock.sleep, clock=clock, tries=1),
                        ol.ModelClient(transport=model, clock=clock), clock=clock)
    col2.start()
    gap = store2.conn.execute("SELECT detail_json FROM events WHERE kind = 'gap'").fetchone()
    gap = json.loads(gap[0])
    assert gap["from_ts"] == last_beat and gap["to_ts"] == pytest.approx(T0 + 1800.5)
    assert sorted(w["ticker"] for w in col2.watched()) == ["A-E-M", "B-E-M"]
    col2.cycle()
    mins = store2.conn.execute("SELECT ticker, trades_min_ts FROM snapshots ORDER BY id DESC LIMIT 2").fetchall()
    assert all(m == int(last_read[t] - 5) for t, m in mins)       # the tape is read again from where it stopped
    assert store2.conn.execute("SELECT COUNT(*) FROM selections").fetchone()[0] == 1   # no new selection


def test_resuming_after_the_watch_ran_out_drops_it_and_selects_at_once(tmp_path):
    col, world, model, store, clock = started(tmp_path, n=1)
    run_minutes(col, clock, 1, 2)
    store.close()
    clock.t = T0 + 13 * 3600 + 60.5
    store2 = ol.Store(tmp_path / "loop.sqlite")
    col2 = ol.Collector(store2, kd.KalshiData(fetch=world, sleep=clock.sleep, clock=clock, tries=1),
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
    ins("snapshots", {"watch_id": w, "ticker": "A-E-M", **ol.stamp("book_start", D0 + 29.5),
                      **ol.stamp("book_end", D0 + 30), **ol.stamp("end", D0 + 30.5), "book_state": "two_sided",
                      "trades_ok": 1, "new_trades": 0, "yes_json": "[]", "no_json": "[]"})
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
    assert ol.main(["select", "--db", str(db)], kalshi=kalshi, model=None, clock=clock) == 0
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
                   clock=clock) == 0
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
    col2 = ol.Collector(store2, kd.KalshiData(fetch=world, sleep=clock.sleep, clock=clock, tries=1),
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
    for w, t in ((wa, D0 + 30), (wb, D0 + 600)):
        ins("snapshots", {"watch_id": w, "ticker": "X", **ol.stamp("book_start", t - 0.5), **ol.stamp("book_end", t),
                          **ol.stamp("end", t + 0.5), "book_state": "two_sided", "trades_ok": 1, "new_trades": 0,
                          "yes_json": "[]", "no_json": "[]"})
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
    assert rows[0]["watched_minutes"] == pytest.approx(2 * (T0 + 600.5 - fin) / 60)
    assert rows[0]["watched_share"] == pytest.approx(rows[0]["watched_minutes"] / (20 * 1440))
    assert "watched" in ol.format_status(rows)
    s2 = ol.Store(tmp_path / "s.sqlite")                        # a watch across midnight is split between the days
    w = s2.insert("markets", {"selection_id": 1, "ticker": "A-E-M", "series": "A", **ol.stamp("selected", D0 + 85800),
                              **ol.stamp("close", D0 + 9 * 86400), "market_json": "{}", "book_json": "{}", "why": "x"})
    s2.insert("market_drops", {"watch_id": w, "ticker": "A-E-M", **ol.stamp("dropped", D0 + 87600), "reason": "x"})
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


def test_on_a_simulated_day_every_one_minute_reference_is_30_to_90_seconds_old(tmp_path):
    import random
    rnd = random.Random(11)
    col, world, model, store, clock = started(tmp_path, n=4)
    for i in range(1, 24 * 60 + 1):
        for j, m in enumerate(world.markets):
            bid = round(0.30 + 0.01 * ((i + j) % 10), 2)
            world.books[m["ticker"]] = book(bid, round(bid + 0.05, 2))
        clock.t = T0 + 60 * i + rnd.uniform(0.2, 15.0)          # each minute's cycle starts a little earlier or later
        col.cycle()
    snaps = {}
    for tk, end, bend, mid in store.conn.execute("SELECT ticker, end_ts, book_end_ts, mid FROM snapshots "
                                                 "ORDER BY book_end_ts"):
        snaps.setdefault(tk, []).append((end, bend, round(mid * 100, 4)))
    ds = decisions(store)
    assert len(ds) > 4 * 250
    ages = []
    for d in ds:
        t = d["decision_ts"]
        before = [x for x in snaps[d["ticker"]] if x[0] < t]
        near = [x for x in before if abs(x[1] - (t - 60)) <= 30]
        ref = min(near, key=lambda x: abs(x[1] - (t - 60)))
        ages.append(t - ref[1])
        assert json.loads(d["state_json"])["mid_change_1m_cents"] == pytest.approx(before[-1][2] - ref[2])
    assert 30 <= min(ages) and max(ages) <= 90


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
    col2 = ol.Collector(store2, kd.KalshiData(fetch=world, sleep=clock.sleep, clock=clock, tries=1),
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


# ---------------------------------------------------------------- item 11: the clock goes back

def test_a_clock_that_goes_back_is_recorded_and_the_loop_carries_on(tmp_path):
    col, world, model, store, clock = started(tmp_path)
    run_minutes(col, clock, 1, 40)
    n = len(decisions(store))
    clock.t = T0 + 36 * 60 + 0.5                                # four minutes back
    col.cycle()
    ev = store.conn.execute("SELECT detail_json FROM events WHERE kind = 'clock_back'").fetchall()
    assert len(ev) == 1 and json.loads(ev[0][0])["seconds"] == pytest.approx(-240, abs=2)
    run_minutes(col, clock, 41, 46)
    assert len(decisions(store)) == n + 1                       # slot 06:45 only; none decided twice
    waits = []

    def wait(s):
        waits.append(s)
        clock.t += s
        if len(waits) >= 2:
            col.stop.set()
    clock.t = T0 + 10 * 60 + 5.0                                # an hour back, inside a run
    col.run(wait=wait)
    assert len(waits) == 2 and all(0 <= w <= 60.2 for w in waits)
    assert store.conn.execute("SELECT COUNT(*) FROM events WHERE kind = 'clock_back'").fetchone()[0] == 2
