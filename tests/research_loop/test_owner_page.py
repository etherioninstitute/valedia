"""The owner's page: one static file generated from the trader's own stores, never changing any of them.

Every store here is a small one built in a temporary tree through the store's own writing code; `launchctl` is never
started (the program launcher is replaced for the whole module), the clock, the folders and the environment are given
to the page. Nothing real is read, nothing is created in the repository. Every text that a file can supply holds
hostile markup, a made-up secret and a made-up account number, and the page is checked by parsing it."""
import hashlib
import json
import os
import random
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

import pandas as pd
import pytest

from valedia_trader.core import alerts as al
from valedia_trader.core import secrets as s
from valedia_trader.data_and_fees import pick_lists as pl
from valedia_trader.execution_and_risk.orders import OrderLedger
from valedia_trader.research_loop import orderbook_loop as ol
from valedia_trader.research_loop import owner_page as op
from valedia_trader.research_loop import pick_grading as pg
from valedia_trader.research_loop import tournament as tm
from valedia_trader.research_loop.ledger import HypothesisLedger
from valedia_trader.research_loop.paper import Journal
from valedia_trader.research_loop.risk_gate import DEFAULT_LIMITS

NOW = datetime(2026, 11, 13, 20, 0, tzinfo=timezone.utc).timestamp()
HOSTILE = '<script>alert("x")</script><img src=x onerror=alert(1)>'
SECRET = "MadeUpSecretValueQ7x9Zk2LmN4"
KEY_VALUE = "MadeUpBrokerKeyValueW3r8Tq5Yp1"
ACCOUNT = "123456789012345"
VENUE_ORDER_ID = "V-1234567890"
PICK_TEXT = "UNIQUE-PICK-COMMENT-TEXT-lakers-minus-three"
ANSWER_TEXT = "UNIQUE-MODEL-ANSWER-TEXT-yes-it-moves"
TITLES = ["Strategies", "Paper account", "Broker (paper)", "Forecasters", "Tax so far", "Limits and switches",
          "Research tests in progress", "Health"]
LABELS = ("com.valedia.paper.daily", "com.valedia.orderbook-collector", "com.valedia.picks.hourly",
          "com.valedia.research-loop.weekly")


@pytest.fixture(autouse=True)
def no_real_launch(monkeypatch):
    def refuse(argv, **kw):
        raise AssertionError(f"a test tried to start {argv[:1]}")
    monkeypatch.setattr(al.subprocess, "run", refuse)


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ----------------------------------------------------------------------------------------------- the page, parsed

class Page(HTMLParser):
    """The page as a browser would see it: the tags and attributes it holds, and the text of each section by heading."""

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.tags, self.attrs, self.sections, self.points = Counter(), [], {}, []
        self._cur = self._in_h2 = None
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        self.tags[tag] += 1
        self.attrs += [(tag, k, v) for k, v in attrs]
        if tag == "section":
            self._cur = {"title": "", "text": []}
        if tag == "h2":
            self._in_h2 = True
        if tag == "polyline" and self._cur is not None:
            self.points.append(len(dict(attrs).get("points", "").split()))

    def handle_endtag(self, tag):
        if tag == "h2":
            self._in_h2 = False
        if tag == "section" and self._cur is not None:
            self.sections[self._cur["title"]] = " ".join(" ".join(self._cur["text"]).split())
            self._cur = None

    def handle_data(self, data):
        if self._cur is not None:
            if self._in_h2:
                self._cur["title"] += data
            else:
                self._cur["text"].append(data)


# ----------------------------------------------------------------------------------------------- a small trader

class FakePicks:
    """The skill script's answers: a listing with one thread and a thread with one comment."""
    URL = "https://www.reddit.com/r/sportsbook/comments/abc123/pick_of_the_day_nov_13/"

    def user(self, name):
        return json.dumps([{"title": "Pick of the Day - Nov 13", "author": "sbpotdbot", "created": "2026-11-13T09:00:00+00:00",
                            "url": self.URL, "body": ""}])

    def thread(self, url):
        return json.dumps({"title": "Pick of the Day - Nov 13", "author": "sbpotdbot", "created": "2026-11-13T09:00:00+00:00",
                           "url": url, "body": "", "note": "x",
                           "comments": [{"author": "someone", "created": "2026-11-13T15:00:00+00:00", "body": PICK_TEXT,
                                         "url": url + "c1abc/"}]})


class World:
    def __init__(self, tmp_path):
        self.tmp, self.root = tmp_path, tmp_path / "repo"
        self.logs, self.agents, self.halt = tmp_path / "logs", tmp_path / "agents", tmp_path / "state" / "halt.json"
        for d in ("journal", "memory", "config", "artifacts/pick_grading"):
            (self.root / d).mkdir(parents=True, exist_ok=True)
        for d in (self.logs, self.agents, self.halt.parent):
            d.mkdir(parents=True, exist_ok=True)
        self.jobs = [("-", "0", LABELS[0]), ("512", "0", LABELS[1]), ("-", "9", LABELS[2]), ("-", "0", f"com.valedia.{HOSTILE} {SECRET}")]
        self.launchctl_error = None
        self.environ = {"WEBULL_ENV": "sandbox", "WEBULL_APP_KEY": KEY_VALUE}
        for label in LABELS:
            (self.agents / f"{label}.plist").write_text("<plist/>")
        (self.agents / "com.valedia.not-loaded.plist").write_text("<plist/>")
        (self.root / "config" / "risk.json").write_text(json.dumps(DEFAULT_LIMITS))
        self.make_ledger()
        self.make_journal()
        self.make_paper_state()
        self.make_orders()
        self.make_tournament()
        self.make_orderbook()
        self.make_picks()
        self.make_logs()
        self.make_alerts()

    # ---- what the page is given
    def env(self):
        def launchctl():
            if self.launchctl_error:
                raise OSError(self.launchctl_error)
            return "PID\tStatus\tLabel\n" + "\n".join(f"{p}\t{st}\t{lb}" for p, st, lb in self.jobs) + "\n"
        return al.Env(root=self.root, now=NOW, halt_file=self.halt, logs_dir=self.logs, launch_agents_dir=self.agents,
                      launchctl=launchctl, free_bytes=lambda p: 50 * 10 ** 9,
                      sessions=lambda: pd.bdate_range("2026-01-01", "2027-12-31"), environ=self.environ)

    def page(self):
        return Page(self.html())

    def html(self):
        out = self.tmp / "out" / "index.html"
        op.build(self.env(), out=out)
        return out.read_text()

    # ---- the ledger of ideas: 5 ideas, 4 tested, 2 failed, 2 passed (and one untested)
    def make_ledger(self):
        led = HypothesisLedger(self.root / "memory" / "ledger.db")
        ids = {}
        for name, idea in (("mom", f"Sector funds that rose last month keep rising {HOSTILE}"),
                           ("rev", f"Buy a fund after a sharp one day drop {SECRET} {ACCOUNT}"),
                           ("bad1", "Gold leads the dollar by a week"),
                           ("bad2", "Rainy Mondays predict weak small caps"),
                           ("new", "Treasury auctions move bank stocks")):
            ids[name] = led.propose(idea, "tester", "funds")
        for name, status in (("mom", "passed paper"), ("rev", "passed backtest only"), ("bad1", "failed"), ("bad2", "failed")):
            led.record_backtest(ids[name], {"result": {}}, "choppy", f"strategies/{name}_h{ids[name]}.json", "1.0.0", status)
        led.close()
        self.ids = ids

    # ---- the journal: day summaries (strategy mom_a), decisions that name the idea, order-step summaries
    def make_journal(self):
        j = self.journal = Journal(self.root / "journal")
        values = [(0.0, 0.0), (500.0, 250.0), (1000.0, 400.0), (750.0, 600.0)]
        for i, (mine, held) in enumerate(values):
            day = f"2026-11-{9 + i}"
            j.write(day, {"type": "decision", "hypothesis_id": self.ids["mom"], "strategy": "mom_a", "decision": "no trade"})
            j.write(day, {"type": "summary", "equity": 100000 + mine, "strategy_values": {"mom_a": mine},
                          "benchmark_values": {"mom_a": held}, "capital_per_strategy": 50000.0, "orders_placed": 0,
                          "tax": {"unavailable": f"the tax ledger could not take a fill: {HOSTILE} {SECRET} {ACCOUNT}"}})
        j.write("2026-11-12", {"type": "order_step", "venue": "webull-sandbox", "event": "summary", "status": "ok", "stage": None,
                               "reason": None, "orders_sent": 2, "sent": [{"symbol": "XLE", "tag": "t1"}, {"symbol": "SPY", "tag": "t2"}],
                               "refused": [{"symbol": "QQQ"}], "left_over": [{"symbol": "IWM"}, {"symbol": "DIA"}]})
        j.write("2026-11-13", {"type": "order_step", "venue": "webull-sandbox", "event": "summary", "status": "stopped",
                               "stage": "send",
                               "reason": f"the market is closed {HOSTILE} {SECRET} account {ACCOUNT}", "orders_sent": 0, "sent": [],
                               "refused": [], "left_over": []})

    def make_paper_state(self):
        history = [{"day": f"2026-11-{9 + i:02d}", "equity": e} for i, e in enumerate((100000.0, 100500.0, 101000.0, 100750.0))]
        state = {"starting_cash": 100000.0, "cash": 62000.5, "last_day": "2026-11-12", "peak_equity": 101000.0,
                 "positions": {"mom_a": {"SPY": {"units": 10.0, "cost": 450.0}, f"X{HOSTILE}": {"units": 2.0, "cost": 10.0},
                                         "QQQ": {"units": 0.0, "cost": 0.0}}},
                 "pending": [{"strategy": "mom_a", "symbol": "XLE", "units": 5.0, "decided_on": "2026-11-12", "reduces_risk": False}],
                 "history": history, "realized": {"mom_a": 320.25, "mom_b": -20.0}}
        (self.root / "journal" / "paper_state.json").write_text(json.dumps(state))

    # ---- one order ledger: an open order, a fill of yesterday, a fill ten days ago
    def make_orders(self):
        path = self.ledger_path = self.root / "memory" / "orders-webull-sandbox.sqlite"
        OrderLedger(path, clock=lambda: NOW).close()
        db = sqlite3.connect(path)
        db.execute("INSERT INTO reconciles (time, clean, summary) VALUES (?, 1, '{}')", (NOW - 3600,))
        db.execute("INSERT INTO orders (tag, symbol, units, reference_price, state, created, updated, venue_order_id) "
                   "VALUES (?, ?, 5, 60.0, 'accepted', ?, ?, ?)", (f"paper-{SECRET}-0007", "<b>XLE</b>", NOW - 7200, NOW - 7000, VENUE_ORDER_ID))
        db.execute("INSERT INTO orders (tag, symbol, units, reference_price, state, created, updated) VALUES ('paper-done-9001', 'SPY', 3, 450, 'filled', ?, ?)",
                   (NOW - 90000, NOW - 86000))
        db.execute("INSERT INTO fills (tag, symbol, units, price, fee, time, applied) VALUES ('paper-done-9001', 'SPY', 3, 450.0, 0.05, ?, 1)", (NOW - 86400,))
        db.execute("INSERT INTO fills (tag, symbol, units, price, fee, time, applied) VALUES ('paper-old-8000', 'QQQ', 2, 400.0, 9.99, ?, 1)", (NOW - 10 * 86400,))
        db.commit()
        db.close()

    def make_tournament(self):
        led = tm.TournamentLedger(self.root / "memory" / "tournament.sqlite")
        qs = [tm.Question(f"funds:SPY:2026-10-{i + 1:02d}:5d", "funds", "forward", f"2026-10-{i + 1:02d}", 5, "SPY", "SPY closes higher", {}, 0.6)
              for i in range(12)]
        led.add(qs)
        for i, q in enumerate(qs):
            for who, p in (("nimble-v3", 0.7), (f"model {HOSTILE}", 0.5), ("base-rule", 0.6)):
                led.record(q.id, who, p, None, 10.0)
            led.settle(q.id, i % 2)
        led.close()

    def make_orderbook(self):
        store = ol.Store(self.root / "memory" / "orderbook_loop.sqlite")
        t0 = NOW - 3 * 3600
        store.insert("markets", {"watch_id": 1, "selection_id": 1, "ticker": "KX-T1", "series": "KX", **ol.stamp("selected", t0),
                                 "phase": 0}, commit=False)
        for i in range(180):
            store.insert("snapshots", {"watch_id": 1, "ticker": "KX-T1", "book_end_ts": t0 + 60 * i, "end_ts": t0 + 60 * i + 1,
                                       "trades_ok": 1, "tail": 0}, commit=False)
            store.insert("heartbeats", {**ol.stamp("", t0 + 60 * i), "watched": 1, "kalshi_requests": 3, "kalshi_failures": 0,
                                        "model_calls": 1, "paused": 0}, commit=False)
        store.conn.commit()
        slot = (int(store.warm_time("KX-T1", t0) // 300) + 1) * 300
        for k in range(3):
            store.insert("decisions", {"watch_id": 1, "ticker": "KX-T1", "slot_ts": slot + 300 * k, **ol.stamp("slot", slot + 300 * k),
                                       "status": "ok", "forced": 0, "answers_json": json.dumps({"q1": ANSWER_TEXT})})
        store.close()
        self.heartbeat_newest = t0 + 60 * 179

    def make_picks(self):
        store = pl.Store(self.root / "memory" / "pick_lists.sqlite")
        pl.ingest(store, FakePicks(), clock=lambda: datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc), sleep=lambda x: None)
        store.close()
        pg.GradingStore(self.root / "artifacts" / "pick_grading" / pg.STORE_FILE).close()

    def make_logs(self):
        for name, last in (("valedia-paper-daily", f"done {HOSTILE} {SECRET} {ACCOUNT}"), ("valedia-orderbook-collector", "collector alive"),
                           ("valedia-picks-hourly", f"{iso(NOW - 600)} end")):
            (self.logs / f"{name}.log").write_text(f"first line\n{last}\n\n")
        (self.logs / "other.log").write_text("not a valedia log\n")

    def make_alerts(self):
        d = self.root / "artifacts" / "owner"
        d.mkdir(parents=True, exist_ok=True)
        rows = [{"time": iso(NOW - 900), "level": "critical", "key": "switch:halt", "title": f"Halt {HOSTILE} {SECRET}", "detail": "d"},
                {"time": iso(NOW - 800), "level": "warning", "key": "disk:low", "title": "Disk is low", "detail": "d"},
                {"time": iso(NOW - 700), "level": "cleared", "key": "disk:low", "title": "Cleared: Disk is low", "detail": "d"}]
        (d / "alerts.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))


@pytest.fixture
def world(tmp_path):
    s.remember(SECRET)
    s.remember(KEY_VALUE)
    return World(tmp_path)


def hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and not p.name.endswith(("-wal", "-shm"))}


# ----------------------------------------------------------------------------------------------- the whole page

def test_the_page_has_every_heading_and_each_states_the_time_of_its_newest_data(world):
    page = world.page()
    assert list(page.sections) == TITLES
    for title in TITLES:
        assert "Newest data:" in page.sections[title], title


def test_the_page_is_one_self_contained_file(world):
    html = world.html()
    page = Page(html)
    assert html.lstrip().lower().startswith("<!doctype html>")
    assert page.tags["style"] == 1 and page.tags["svg"] >= 1
    assert not set(page.tags) & {"script", "link", "img", "iframe", "object", "embed", "a", "form", "input", "video", "audio", "source"}
    assert not [a for a in page.attrs if a[1] in ("src", "href", "srcset", "action", "style", "data") or a[1].startswith("on")]
    assert "http" not in html.lower() and "@import" not in html and "url(" not in html and "<link" not in html
    assert '<meta charset="utf-8">' in html


def test_the_page_stores_are_not_changed_and_nothing_else_is_created(world):
    before = hashes(world.tmp)
    html_path = world.tmp / "out" / "index.html"
    op.build(world.env(), out=html_path)
    after = hashes(world.tmp)
    assert {k: v for k, v in after.items() if k != "out/index.html"} == before
    assert sorted(set(after) - set(before)) == ["out/index.html"]


def test_default_output_file_is_under_artifacts_owner(world):
    path = op.build(world.env())
    assert path == world.root / "artifacts" / "owner" / "index.html" and path.read_text().lstrip().startswith("<!doctype")


def test_the_command_line_builds_the_page(world, capsys):
    out = world.tmp / "elsewhere" / "owner.html"
    assert op.main(["build", "--out", str(out)], env=world.env()) == 0
    assert out.is_file() and str(out) in capsys.readouterr().out
    assert op.main(["build"], env=world.env()) == 0
    assert (world.root / "artifacts" / "owner" / "index.html").is_file()
    assert not list((world.root / "artifacts" / "owner").glob("*.tmp"))


def test_a_page_that_cannot_be_written_is_exit_two(world, capsys):
    blocker = world.tmp / "afile"
    blocker.write_text("x")
    assert op.main(["build", "--out", str(blocker / "index.html")], env=world.env()) == 2
    assert "could not" in capsys.readouterr().err.lower()


# ----------------------------------------------------------------------------------------------- each section

def test_strategies_lists_each_idea_that_trades_or_passed_with_paper_numbers(world):
    text = world.page().sections["Strategies"]
    assert "5 ideas in the ledger; 4 tested, 2 failed" in text
    assert "Sector funds that rose last month keep rising" in text and "Buy a fund after a sharp one day drop" in text
    assert "Gold leads the dollar" not in text and "Treasury auctions" not in text        # failed or untested: counted, not listed
    assert "passed paper" in text and "passed backtest only" in text
    # mom_a: 4 paper days; 750 of 50 000 = +1.50% against +1.20% for holding its funds; worst fall from 51 000 to 50 750
    assert "4" in text.split("mom_a")[1][:40] and "+1.50%" in text and "+1.20%" in text and "0.49%" in text
    assert "Newest data: 2026-11-12" in text


def test_an_idea_with_no_paper_days_says_so(world):
    text = world.page().sections["Strategies"]
    row = text.split("Buy a fund after a sharp one day drop")[1]
    assert "no paper days" in row.lower() or "none yet" in row.lower()


def test_paper_account_has_a_chart_cash_positions_pending_and_realised(world):
    page = world.page()
    text = page.sections["Paper account"]
    assert page.points == [4] and "2026-11-09" in text and "2026-11-12" in text            # one point per recorded day
    assert "62,000.50" in text and "SPY" in text and "450.00" in text
    assert "QQQ" not in text                                                                # a closed lot is not a position
    assert "XLE" in text and "2026-11-12" in text                                           # the pending order
    assert "300.25" in text                                                                 # 320.25 - 20.00 realised
    assert "100,750.00" in text
    assert "Newest data: 2026-11-12" in text


def test_broker_section_has_open_orders_recent_fills_with_fees_and_the_step_summaries(world):
    text = world.page().sections["Broker (paper)"]
    assert "webull-sandbox" in text
    assert "accepted" in text and "XLE" in text and "<secret>" in text and "0007" not in text   # a tag that holds a value: its mask
    assert "SPY" in text and "0.05" in text and "450.00" in text                            # the fill of the last 7 days, with its fee
    assert "QQQ" not in text and "9.99" not in text                                         # ten days ago: not shown
    assert "sent 2" in text and "refused 1" in text and "left over 2" in text               # an ok step
    assert "stopped at stage send" in text and "the market is closed" in text               # a stopped step: stage and reason
    assert "Newest data:" in text and "2026-11-13" in text


def test_a_run_outside_market_hours_is_shown_as_that_not_as_a_failure(world):
    world.journal.write("2026-11-13", {"type": "order_step", "venue": "webull-sandbox", "event": "summary", "status": "stopped",
                                       "stage": "session", "reason": "outside the regular session window 09:35 to 15:45 New York time (it is 06:10)",
                                       "orders_sent": 0, "sent": [], "refused": [], "left_over": []})
    world.journal.write("2026-11-14", {"type": "order_step", "venue": "webull-sandbox", "event": "summary", "status": "stopped",
                                       "stage": "session", "reason": "another order step is running on this venue's state file",
                                       "orders_sent": 0, "sent": [], "refused": [], "left_over": []})
    text = world.page().sections["Broker (paper)"]
    assert "2026-11-13 outside market hours" in text
    assert "stopped at stage session: another order step is running" in text
    assert "stopped at stage session: outside" not in text


def test_every_order_ledger_found_has_its_own_part(world):
    second = world.root / "memory" / "orders-webull-sandbox-events.sqlite"
    OrderLedger(second, clock=lambda: NOW).close()
    text = world.page().sections["Broker (paper)"]
    assert "webull-sandbox-events" in text and "no open orders" in text.lower()


def test_forecasters_is_the_tournaments_own_leaderboard(world):
    text = world.page().sections["Forecasters"]
    led = tm.TournamentLedger(world.root / "memory" / "tournament.sqlite")
    rows = tm.scores(led)
    led.close()
    assert len(rows) == 3
    for r in rows:
        assert r["forecaster"].replace("<script>", "") and f"{r['brier']:.4f}" in text and f"{r['baseline_brier']:.4f}" in text
        assert r["verdict"] in text
    assert "nimble-v3" in text and "base-rule" in text and "12" in text


def forecasters_html(world):
    page = world.html()
    start = page.index(">Forecasters</h2>")
    return page[start:page.index("</section>", start)]


def test_the_forecasters_table_shows_which_model_answered_and_what_it_may_remember(world):
    part = forecasters_html(world)
    assert re.findall(r"<th>(.*?)</th>", part) == ["Forecaster", "Model", "Market", "Kind", "Memory", "Settled", "Brier",
                                                   "Baseline Brier", "Skill", "Verdict"]
    text = world.page().sections["Forecasters"]
    assert text.count("not recorded") == 3 and text.count("asked live") == 3      # recorded without a model, forward questions


OLD_TOURNAMENT_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS questions (id TEXT PRIMARY KEY, market TEXT, kind TEXT, asked_on TEXT, horizon INTEGER, "
    "symbol TEXT, thesis TEXT, facts TEXT, baseline REAL);"
    "CREATE TABLE IF NOT EXISTS forecasts (question_id TEXT, forecaster TEXT, probability REAL, error TEXT, latency_ms REAL, "
    "ts REAL, PRIMARY KEY (question_id, forecaster));"
    "CREATE TABLE IF NOT EXISTS outcomes (question_id TEXT PRIMARY KEY, outcome INTEGER, settled_ts REAL);")


def test_the_forecasters_table_renders_an_old_tournament_file_and_leaves_it_alone(world):
    path = world.root / "memory" / "tournament.sqlite"
    path.unlink()
    db = sqlite3.connect(path)
    db.executescript(OLD_TOURNAMENT_SCHEMA)
    for i in range(12):
        kind = "forward" if i < 4 else "blind history"
        db.execute("INSERT INTO questions VALUES (?,?,?,?,?,?,?,?,?)", (f"q{i}", "funds", kind, f"2026-10-{i + 1:02d}", 5, "SPY", "t", "{}", 0.6))
        db.execute("INSERT INTO forecasts VALUES (?,?,?,?,?,?)", (f"q{i}", "nimble-v3", 0.7, None, 10.0, NOW - 3600))
        db.execute("INSERT INTO outcomes VALUES (?,?,?)", (f"q{i}", i % 2, NOW - 3600))
    db.commit()
    db.close()
    digest, files = hashlib.sha256(path.read_bytes()).hexdigest(), sorted(p.name for p in path.parent.iterdir() if p.name.startswith("tournament"))
    text = world.page().sections["Forecasters"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == digest and sorted(p.name for p in path.parent.iterdir() if p.name.startswith("tournament")) == files
    assert "12 questions asked, 12 settled" in text and "asked live" in text and "may be remembered" in text
    assert "not evidence: the model may remember these dates" in text and "unavailable" not in text.lower()


def test_tax_section_says_unavailable_and_why(world):
    text = world.page().sections["Tax so far"]
    assert "unavailable" in text.lower() and "the tax ledger could not take a fill" in text


def test_tax_section_gives_the_figures_the_notes_give(world):
    world.journal.write("2026-11-12", {"type": "summary", "equity": 1.0, "strategy_values": {}, "benchmark_values": {},
                                       "tax": {"year": "2026", "short_term_net": 12.5, "long_term_net": -3.0, "disallowed_loss": 1.25,
                                               "provisional_loss": 7.0, "as_of": "2026-11-12", "unverified_rules": ["lot method"],
                                               "note": "computed from the rules file, not tax advice"}})
    text = world.page().sections["Tax so far"]
    assert "12.50" in text and "-3.00" in text and "1.25" in text and "7.00" in text and "not tax advice" in text
    assert "unavailable" not in text.lower() and "Newest data: 2026-11-12" in text


def test_limits_and_switches(world):
    text = world.page().sections["Limits and switches"]
    for key, value in DEFAULT_LIMITS.items():
        assert key in text and str(value) in text
    assert text.count("not set") == 2 and "sandbox" in text and "not sandbox" not in text
    world.halt.write_text("{}")
    (world.root / "journal" / "kill_switch.json").write_text("{}")
    text = world.page().sections["Limits and switches"]
    assert "not set" not in text and text.count("set") >= 2
    world.environ["WEBULL_ENV"] = "production"
    text = world.page().sections["Limits and switches"]
    assert "not sandbox" in text and "production" not in text and KEY_VALUE not in text


def test_a_link_as_the_latch_or_the_halt_file_counts_as_set_by_the_risk_gates_rule(world):
    (world.root / "journal" / "kill_switch.json").symlink_to(world.tmp / "nowhere")
    text = world.page().sections["Limits and switches"]
    assert text.count("not set") == 1


def test_research_tests_show_coverage_and_counts_but_no_result(world):
    text = world.page().sections["Research tests in progress"]
    store = ol.Store(world.root / "memory" / "orderbook_loop.sqlite")
    (row,) = ol.day_status(store, NOW)
    store.close()
    assert row["day"] == "2026-11-13" and row["expected"] > 0 and row["collected"] == 3
    assert "2026-11-13" in text and str(row["expected"]) in text and f"{row['share']:.1%}" in text
    low = text.lower()
    assert "threads" in low and "comments" in low and "gate" in low and "closed" in low
    assert ANSWER_TEXT not in text and PICK_TEXT not in text
    assert "Newest data:" in text and "2026-11-13" in text


def test_health_has_jobs_logs_damage_and_the_open_alerts(world):
    with (world.root / "journal" / "2026-11-10.jsonl").open("a") as fh:
        fh.write('{"type": "decision", "br\n')
    text = world.page().sections["Health"]
    assert LABELS[0] in text and "loaded" in text and "not loaded" in text and "com.valedia.not-loaded" in text
    assert "last exit code 9" in text or "exit code 9" in text
    assert "valedia-paper-daily.log" in text and "collector alive" in text and "other.log" not in text
    assert "2026-11-10.jsonl line" in text
    assert "Halt" in text and "Disk is low" not in text                                      # the cleared one is not open
    assert "Newest data:" in text


def test_health_with_nothing_wrong_says_no_damage_and_no_open_alerts(world):
    (world.root / "artifacts" / "owner" / "alerts.jsonl").unlink()
    text = world.page().sections["Health"]
    assert "no damaged" in text.lower() and "no open alerts" in text.lower()


# ----------------------------------------------------------------------------------------------- escaping and secrets

def test_hostile_text_in_every_field_is_text_never_markup(world):
    html = world.html()
    page = Page(html)
    assert not set(page.tags) & {"script", "img", "b"}, page.tags
    assert not [a for a in page.attrs if a[1].startswith("on") or a[1] == "src"]
    shown = " ".join(page.sections.values())
    assert HOSTILE in shown
    for title in ("Strategies", "Paper account", "Broker (paper)", "Forecasters", "Tax so far", "Health"):
        assert "<script>" in page.sections[title], title                                      # as text, in each section that shows it
    assert "<b>XLE</b>" in page.sections["Broker (paper)"]
    assert "<script>" not in html and "<img" not in html and "<b>XLE" not in html           # nothing unescaped in the file itself


def test_nothing_secret_no_account_number_no_order_id_no_pick_text_reaches_the_page(world):
    html = world.html()
    low = html.lower()
    assert SECRET.lower() not in low and KEY_VALUE.lower() not in low
    assert ACCOUNT not in html and ACCOUNT[:10] not in html
    assert VENUE_ORDER_ID not in html and "1234567890" not in html and "paper-done-9001" not in html
    assert PICK_TEXT not in html and ANSWER_TEXT not in html and "sbpotdbot" not in html
    assert "<secret>" in html and "<number>" in html or "&lt;secret&gt;" in html


# ----------------------------------------------------------------------------------------------- a source that is missing, unreadable or busy

def garbage(path):
    path.write_bytes(b"this is not a database at all, just text" * 20)


def remove(*paths):
    def go(w):
        for p in paths:
            (w.root / p).unlink()
    return go


def missing_orders(w):
    for p in (w.root / "memory").glob("orders-*.sqlite"):
        p.unlink()


def broken_ledger(w): garbage(w.root / "memory" / "ledger.db")
def broken_tournament(w): garbage(w.root / "memory" / "tournament.sqlite")
def broken_orders(w): garbage(w.ledger_path)
def broken_orderbook(w): garbage(w.root / "memory" / "orderbook_loop.sqlite")
def broken_state(w): (w.root / "journal" / "paper_state.json").write_text("{not json")
def broken_risk(w): (w.root / "config" / "risk.json").write_text("[1, 2")
def no_launchctl(w): setattr(w, "launchctl_error", "launchctl is not here")
def no_journal(w):
    for p in (w.root / "journal").glob("*.jsonl"):
        p.unlink()
    (w.root / "journal" / "paper_state.json").unlink()
    (w.root / "journal").rmdir()


BREAKS = {
    "ledger missing": (remove("memory/ledger.db"), ["Strategies"], "not there"),
    "ledger unreadable": (broken_ledger, ["Strategies"], "unavailable"),
    "paper state missing": (remove("journal/paper_state.json"), ["Paper account"], "not there"),
    "paper state unreadable": (broken_state, ["Paper account"], "unavailable"),
    "order ledgers missing": (missing_orders, ["Broker (paper)"], "no order ledger"),
    "order ledger unreadable": (broken_orders, ["Broker (paper)"], "unavailable"),
    "tournament missing": (remove("memory/tournament.sqlite"), ["Forecasters"], "not there"),
    "tournament unreadable": (broken_tournament, ["Forecasters"], "unavailable"),
    "risk limits missing": (remove("config/risk.json"), ["Limits and switches"], "cannot read"),
    "risk limits unreadable": (broken_risk, ["Limits and switches"], "cannot read"),
    "order book missing": (remove("memory/orderbook_loop.sqlite"), ["Research tests in progress"], "not there"),
    "order book unreadable": (broken_orderbook, ["Research tests in progress"], "unavailable"),
    "pick store missing": (remove("memory/pick_lists.sqlite"), ["Research tests in progress"], "not there"),
    "launchctl fails": (no_launchctl, ["Health"], "could not ask launchd"),
}


@pytest.mark.parametrize("name", sorted(BREAKS))
def test_a_source_that_cannot_be_read_is_said_in_its_section_and_the_page_is_still_written(world, name):
    baseline = world.page().sections
    mutate, affected, words = BREAKS[name]
    mutate(world)
    page = world.page()
    assert list(page.sections) == TITLES
    for title in affected:
        assert words in page.sections[title].lower(), (title, page.sections[title][:300])
    for title in TITLES:
        if title not in affected:
            assert page.sections[title] == baseline[title], title


def test_without_a_journal_the_sections_that_need_it_say_so_and_the_rest_stand(world):
    baseline = world.page().sections
    no_journal(world)
    page = world.page()
    assert list(page.sections) == TITLES
    for title in ("Tax so far", "Strategies"):
        assert "journal" in page.sections[title].lower() and "not there" in page.sections[title].lower() \
            or "unavailable" in page.sections[title].lower()
    for title in ("Forecasters", "Limits and switches", "Research tests in progress"):
        assert page.sections[title] == baseline[title]


def test_a_busy_store_makes_that_one_section_say_busy(world, monkeypatch):
    baseline = world.page().sections
    real = al.open_readonly

    def busy_for(*names):
        def fake(path, timeout=5.0, row_factory=True):
            if Path(path).name in names:
                raise sqlite3.OperationalError("database is locked")
            return real(path, timeout, row_factory)
        return fake

    for names, title in ((("ledger.db",), "Strategies"), ((world.ledger_path.name,), "Broker (paper)"),
                         (("tournament.sqlite",), "Forecasters"), (("orderbook_loop.sqlite",), "Research tests in progress")):
        monkeypatch.setattr(al, "open_readonly", busy_for(*names))
        page = world.page()
        assert "busy" in page.sections[title].lower(), title
        assert all(page.sections[t] == baseline[t] for t in TITLES if t != title), title


@pytest.mark.parametrize("name", ["pick_lists.sqlite", "grading.sqlite"])
def test_busy_pick_stores_say_busy_for_the_research_section_only(world, monkeypatch, name):
    baseline = world.page().sections
    real = al.open_readonly

    def fake(path, timeout=5.0, row_factory=True):
        if Path(path).name == name:
            raise sqlite3.OperationalError("database is locked")
        return real(path, timeout, row_factory)

    monkeypatch.setattr(al, "open_readonly", fake)
    page = world.page()
    assert "unavailable" in page.sections["Research tests in progress"].lower() and "busy" in page.sections["Research tests in progress"].lower()
    assert "2026-11-13" in page.sections["Research tests in progress"]                       # the order-book part is still there
    assert all(page.sections[t] == baseline[t] for t in TITLES if t != "Research tests in progress")


def test_the_page_still_builds_when_every_source_is_gone(world):
    for p in list((world.root / "memory").iterdir()) + list((world.root / "journal").iterdir()) + [world.root / "config" / "risk.json"]:
        p.unlink()
    shutil.rmtree(world.root / "artifacts" / "pick_grading")
    world.launchctl_error = "no launchd"
    for f in world.logs.iterdir():
        f.unlink()
    page = world.page()
    assert list(page.sections) == TITLES
    for title in TITLES:
        assert "Newest data: none" in page.sections[title] or "unavailable" in page.sections[title].lower() \
            or "not there" in page.sections[title].lower() or "cannot read" in page.sections[title].lower(), title



# =====================================================================================================================
# rev 2: what the blind check found
# =====================================================================================================================

PHRASE = "alpha bravo candle delta eagle frost gamma hotel india juliet kilo lemon"
TRADING = {"BROKER_API_KEY": "Zq8Lm2Xv9Rt4Wc7Yb1Nh5Jd3Kf6Gs0Pa", "BROKER_API_SECRET": "Hn4Tb7Qw2Ze9Ux5Cv8Ml3Kj6Ar1Sd0Fg",
           "WALLET_MNEMONIC": PHRASE, "WEBULL_ACCOUNT_ID": "987654321098", "WEBULL_ENV": "production"}


@pytest.fixture
def trading_file(tmp_path, monkeypatch):
    f = tmp_path / "trading.env"
    f.write_text("".join(f"{k}={v}\n" for k, v in TRADING.items()))
    os.chmod(f, 0o600)
    monkeypatch.setenv("VALEDIA_ENV_FILE", str(f))
    for k in TRADING:
        monkeypatch.delenv(k, raising=False)
    return f


@pytest.fixture
def bare_world(tmp_path):
    """A world in which the test itself never calls remember(): only the command's own registration can hide a value."""
    return World(tmp_path)


def leaked(text):
    low = text.lower()
    words = PHRASE.split()
    grams = [" ".join(words[i:i + 4]) for i in range(len(words) - 3)]
    return [n for n in ("BROKER_API_KEY", "BROKER_API_SECRET", "WEBULL_ACCOUNT_ID")
            if TRADING[n].lower() in low or TRADING[n][:12].lower() in low] + [g for g in grams if g in low]


def plant_everywhere(w):
    values = f"{TRADING['BROKER_API_KEY']} {TRADING['BROKER_API_SECRET']} account {TRADING['WEBULL_ACCOUNT_ID']} words {PHRASE}"
    w.journal.write("2026-11-13", {"type": "order_step", "venue": "webull-sandbox", "event": "summary", "status": "stopped",
                                   "stage": "send", "reason": f"the venue said {values}", "orders_sent": 0, "sent": [], "refused": [],
                                   "left_over": []})
    w.journal.write("2026-11-12", {"type": "summary", "equity": 1.0, "strategy_values": {}, "benchmark_values": {},
                                   "tax": {"unavailable": f"the ledger could not take a fill: {values}"}})
    (w.logs / "valedia-paper-daily.log").write_text(f"done {values}\n")
    w.jobs = w.jobs + [("-", "9", f"com.valedia.{TRADING['BROKER_API_KEY']}")]
    state = json.loads((w.root / "journal" / "paper_state.json").read_text())
    state["positions"]["mom_a"][f"S{TRADING['BROKER_API_KEY']}"] = {"units": 1.0, "cost": 1.0}
    (w.root / "journal" / "paper_state.json").write_text(json.dumps(state))
    (w.root / "artifacts" / "owner" / "alerts.jsonl").write_text(json.dumps(
        {"time": iso(NOW), "level": "critical", "key": "switch:halt", "title": f"Halt {values}", "detail": "d"}) + "\n")


def test_the_real_build_registers_the_trading_file_and_no_value_reaches_any_section(bare_world, trading_file, capsys):
    w = bare_world
    plant_everywhere(w)
    env = w.env()
    env.environ = None                                                                     # as the real command: read the file itself
    before = dict(os.environ)
    out = w.tmp / "out" / "index.html"
    assert op.main(["build", "--out", str(out)], env=env) == 0
    assert os.environ == before and not set(TRADING) & set(os.environ)                      # nothing was exported
    html = out.read_text()
    seen = capsys.readouterr()
    assert leaked(html) == [] and leaked(seen.out + seen.err) == []
    page = Page(html)
    for title in ("Strategies", "Paper account", "Broker (paper)", "Tax so far", "Health"):
        assert leaked(page.sections[title]) == [], title
    assert "&lt;secret&gt;" in html


def test_the_webull_word_is_read_from_the_trading_file_and_nothing_else_of_it_is_kept(bare_world, trading_file):
    env = bare_world.env()
    env.environ = None
    op.build(env, out=bare_world.tmp / "o.html")
    text = Page((bare_world.tmp / "o.html").read_text()).sections["Limits and switches"]
    assert "not sandbox" in text and "production" not in text and not set(TRADING) & set(os.environ)
    os.environ["WEBULL_ENV"] = "sandbox"                                                    # the environment wins, as for the trader
    try:
        env2 = bare_world.env()
        env2.environ = None
        op.build(env2, out=bare_world.tmp / "o2.html")
        assert "not sandbox" not in Page((bare_world.tmp / "o2.html").read_text()).sections["Limits and switches"]
    finally:
        del os.environ["WEBULL_ENV"]


def test_a_trading_file_that_cannot_be_read_stops_the_build_with_nothing_written(bare_world, trading_file, capsys):
    out = bare_world.tmp / "out" / "index.html"
    for mode in (0o644, 0o000):
        os.chmod(trading_file, mode)
        assert op.main(["build", "--out", str(out)], env=bare_world.env()) == 2
        err = capsys.readouterr().err
        assert "trading file" in err and leaked(err) == [] and not out.exists()
    os.chmod(trading_file, 0o600)


def test_a_missing_trading_file_builds_the_page(bare_world, tmp_path, monkeypatch):
    monkeypatch.setenv("VALEDIA_ENV_FILE", str(tmp_path / "no-trading-file.env"))
    out = bare_world.tmp / "out" / "index.html"
    assert op.main(["build", "--out", str(out)], env=bare_world.env()) == 0 and out.is_file()


def test_a_whole_order_tag_or_id_is_shown_by_its_last_characters_only(world):
    tag = "os-20261113-143501-a1b2c3-XLE"
    world.journal.write("2026-11-13", {"type": "order_step", "venue": "webull-sandbox", "event": "summary", "status": "stopped",
                                       "stage": "send", "reason": f"order {tag} could not be recorded or sent; venue_order_id=V-1234567890",
                                       "orders_sent": 0, "sent": [], "refused": [], "left_over": []})
    world.journal.write("2026-11-12", {"type": "summary", "equity": 1.0, "strategy_values": {}, "benchmark_values": {},
                                       "tax": {"unavailable": f"order {tag} is unknown, client order id 7f3a9c2e51b84d60"}})
    (world.logs / "valedia-paper-daily.log").write_text(f"order {tag} id 9988776655aabbcc\n")
    html = world.html()
    for whole in (tag, "a1b2c3", "1234567890", "7f3a9c2e51b84d60", "9988776655aabbcc"):
        assert whole not in html, whole
    assert "…b2c3-XLE" in html and "…7890" in html and "…bbcc" in html


# ----------------------------------------------------------------------------------------------- stores are only read

def store_files(world):
    return [world.root / "memory" / "pick_lists.sqlite", world.root / "artifacts" / "pick_grading" / "grading.sqlite"]


def state_of(world):
    return {str(p.relative_to(world.tmp)): (p.read_bytes(), p.stat().st_mtime_ns) for p in sorted(world.tmp.rglob("*"))
            if p.is_file() and not p.name.endswith(("-wal", "-shm")) and "out" not in p.relative_to(world.tmp).parts[:1]}


def make_hot_journal(db):
    code = (f"import sqlite3, os\nc = sqlite3.connect({str(db)!r}, isolation_level=None)\nc.execute('PRAGMA cache_size = 10')\n"
            "c.execute('BEGIN')\nc.execute('CREATE TABLE IF NOT EXISTS junk (a)')\n"
            "c.executemany('INSERT INTO junk VALUES (?)', [('x' * 200,)] * 3000)\nos._exit(0)\n")
    p = subprocess.Popen([sys.executable, "-I", "-c", code])
    assert p.wait() == 0
    assert (db.parent / (db.name + "-journal")).stat().st_size > 0


@pytest.mark.parametrize("which", [0, 1], ids=["pick store", "grading store"])
def test_a_store_that_needs_recovery_is_said_so_and_is_not_touched(world, which):
    baseline = world.page().sections
    db = store_files(world)[which]
    make_hot_journal(db)
    before = state_of(world)
    names_before = sorted(p.name for p in db.parent.iterdir())
    page = world.page()
    text = page.sections["Research tests in progress"].lower()
    assert "unavailable" in text and "needs its own program to recover it" in text
    assert state_of(world) == before and sorted(p.name for p in db.parent.iterdir()) == names_before
    assert all(page.sections[t] == baseline[t] for t in TITLES if t != "Research tests in progress")


@pytest.mark.parametrize("which", [0, 1], ids=["pick store", "grading store"])
def test_a_store_held_by_a_writer_is_said_busy_within_five_seconds_and_is_not_touched(world, which):
    db = store_files(world)[which]
    holder = sqlite3.connect(db, isolation_level=None)
    holder.execute("BEGIN EXCLUSIVE")
    try:
        before = state_of(world)
        t0 = time.monotonic()
        page = world.page()
        took = time.monotonic() - t0
        assert took < 5.0 + 3.0                                                              # 5 s of waiting for the store, and no more
        text = page.sections["Research tests in progress"].lower()
        assert "unavailable" in text and "busy" in text
        assert state_of(world) == before
    finally:
        holder.execute("ROLLBACK")
        holder.close()


def test_the_page_never_opens_a_store_through_a_class_that_can_write(world, monkeypatch):
    from valedia_trader.data_and_fees import pick_lists

    def refuse(*a, **k):
        raise AssertionError("a store class that can recover, write or migrate was used")
    monkeypatch.setattr(pick_lists.Store, "__init__", refuse)
    monkeypatch.setattr(pg.GradingStore, "__init__", refuse)
    text = world.page().sections["Research tests in progress"]
    assert "threads" in text.lower() and "gate" in text.lower() and "unavailable" not in text.lower()


# ----------------------------------------------------------------------------------------------- small ones

def test_a_non_finite_equity_is_left_out_of_the_chart(world):
    state = json.loads((world.root / "journal" / "paper_state.json").read_text())
    state["history"][1]["equity"] = float("nan")
    state["history"][2]["equity"] = float("inf")
    (world.root / "journal" / "paper_state.json").write_text(json.dumps(state))            # json writes NaN and Infinity
    html = world.html()
    page = Page(html)
    assert page.points == [2] and "nan" not in html.lower() and "inf" not in html.lower().replace("information", "")


def test_the_open_alerts_say_whether_each_was_shown(world):
    from valedia_trader.core import alerts as a
    rows = a.open_alerts(a.alerts_file(world.root))
    (world.root / "artifacts" / "owner" / "notifications.jsonl").write_text(
        "".join(json.dumps({"time": iso(NOW), "sender": "mac", "key": rows[0]["key"], "since": rows[0]["time"], "result": "failed"}) + "\n"
                for _ in range(5)))
    text = world.page().sections["Health"]
    assert "not shown" in text and "5 times" in text
    (world.root / "artifacts" / "owner" / "notifications.jsonl").write_text(
        json.dumps({"time": iso(NOW), "sender": "mac", "key": rows[0]["key"], "since": rows[0]["time"], "result": "shown"}) + "\n")
    assert "Notification" in world.page().sections["Health"] and "shown" in world.page().sections["Health"]


def test_a_ledger_tag_in_the_order_tables_is_shown_by_its_last_four_characters_only(world):
    tag = "os-20261113-143501-a1b2c3-XLE"
    db = sqlite3.connect(world.ledger_path)
    db.execute("INSERT INTO orders (tag, symbol, units, reference_price, state, created, updated) VALUES (?, 'XLE', 5, 60.0, 'accepted', ?, ?)",
               (tag, NOW - 100, NOW - 90))
    db.execute("INSERT INTO fills (tag, symbol, units, price, fee, time, applied) VALUES (?, 'XLE', 5, 60.0, 0.01, ?, 1)", (tag, NOW - 80))
    db.commit()
    db.close()
    html = world.html()
    assert tag not in html and "a1b2c3" not in html and "b2c3" not in html and html.count("…-XLE") >= 2   # fund: own column


# =====================================================================================================================
# rev 3
# =====================================================================================================================

def test_values_already_in_the_alert_and_notification_files_do_not_reach_the_page_through_the_real_command(bare_world, trading_file, capsys):
    w = bare_world
    key, sec, acct = TRADING["BROKER_API_KEY"], TRADING["BROKER_API_SECRET"], TRADING["WEBULL_ACCOUNT_ID"]
    folder = w.root / "artifacts" / "owner"
    rows = [{"time": f"2026-11-13T{sec}", "level": f"warning{key}", "key": f"job:com.valedia.{key}", "title": f"Job {sec}", "detail": f"words {PHRASE}"},
            {"time": "2026-11-13T19:01:00Z", "level": "critical", "key": "job:alpha_bravo_candle_delta_eagle_frost", "title": "t", "detail": f"acct {acct}"}]
    (folder / "alerts.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (folder / "notifications.jsonl").write_text("".join(json.dumps(
        {"time": "x", "sender": f"phone-{key}", "key": r["key"], "since": r["time"], "result": "failed"}) + "\n" for r in rows))
    env = w.env()
    env.environ = None
    out = w.tmp / "out" / "index.html"
    assert op.main(["build", "--out", str(out)], env=env) == 0
    seen = capsys.readouterr()
    assert leaked(out.read_text()) == [] and leaked(seen.out + seen.err) == []
    assert "withheld" in out.read_text() and "job:com.valedia._number_" not in out.read_text()          # both old lines held a secret in the key


@pytest.mark.parametrize("body, words", [("# " + "x" * 70000 + "\n", "64 KB"), ("".join(f"S_{i}=v{i}\n" for i in range(201)), "200"),
                                         ("K=\xff\n".encode("latin-1"), "UTF-8")])
def test_a_file_that_is_not_a_trading_file_stops_the_build_at_once_with_nothing_written(bare_world, trading_file, capsys, body, words):
    if isinstance(body, bytes):
        trading_file.write_bytes(body)
    else:
        trading_file.write_text(body)
    os.chmod(trading_file, 0o600)
    out = bare_world.tmp / "out" / "index.html"
    t0 = time.monotonic()
    assert op.main(["build", "--out", str(out)], env=bare_world.env()) == 2
    err = capsys.readouterr().err
    assert time.monotonic() - t0 < 1.0 and words in err and "chmod" not in err and "nothing was done" in err and not out.exists()


def write_step_entry(w, mode, status, stage=None, reason=None, day="2026-11-13", sent=0):
    entry = {"type": "order_step", "venue": "webull-sandbox", "event": "summary", "status": status, "stage": stage, "reason": reason,
             "orders_sent": sent, "sent": [{"symbol": "XLE"}] * sent, "refused": [], "left_over": []}
    if mode:
        entry["mode"] = mode
    w.journal.write(day, entry)


def test_the_order_step_section_keeps_the_unattended_run_apart_from_the_practice_runs(world):
    write_step_entry(world, "propose", "done", day="2026-11-14", sent=4)
    write_step_entry(world, "approve", "stopped", "ticket", "the ticket is not the latest one", day="2026-11-15")
    text = world.page().sections["Broker (paper)"]
    unattended, practice = text.split("Practice runs of the approval flow")
    assert "Last order steps of the unattended run" in unattended
    assert "2026-11-14" not in unattended and "2026-11-15" not in unattended and "the ticket is not the latest" not in unattended
    assert "stopped at stage send" in unattended                                      # the unattended run's own last result
    assert "Last propose 2026-11-14 done sent 4" in practice
    assert "Last approve 2026-11-15 stopped at stage ticket: the ticket is not the latest one" in practice


def test_the_practice_rows_say_none_yet_when_there_is_no_practice_run(world):
    practice = world.page().sections["Broker (paper)"].split("Practice runs of the approval flow")[1]
    assert "Last propose none yet" in practice and "Last approve none yet" in practice


# =====================================================================================================================
# rev 4
# =====================================================================================================================

def test_the_page_treats_only_the_two_exact_modes_as_practice_and_no_key_as_the_unattended_run(world):
    for odd in ("", None, "Propose", " approve", 3):
        write_step_entry(world, None, "done", day="2026-11-16", sent=1)
        entry = {"type": "order_step", "venue": "webull-sandbox", "event": "summary", "status": "stopped", "stage": "send",
                 "reason": "unknown-mode-reason", "orders_sent": 0, "sent": [], "refused": [], "left_over": [], "mode": odd}
        world.journal.write("2026-11-17", entry)
    text = world.page().sections["Broker (paper)"]
    unattended, practice = text.split("Practice runs of the approval flow")
    assert "unknown-mode-reason" not in unattended and "unknown-mode-reason" not in practice       # neither run: not shown as either
    assert "2026-11-16" in unattended                                                              # the real unattended run is


def test_the_alert_lines_withheld_are_counted_on_the_page_not_listed_as_open(bare_world, trading_file):
    key = TRADING["BROKER_API_KEY"]
    folder = bare_world.root / "artifacts" / "owner"
    (folder / "alerts.jsonl").write_text("".join(json.dumps(
        {"time": "2026-11-13T19:00:00Z", "level": "warning", "key": f"job:com.valedia.{key}{i}", "title": "t", "detail": "d"}) + "\n" for i in range(4)))
    env = bare_world.env()
    env.environ = None
    out = bare_world.tmp / "o.html"
    op.build(env, out=out)
    health = Page(out.read_text()).sections["Health"]
    assert "No open alerts" in health and health.count("withheld") == 1 and "4 old lines" in health and leaked(health) == []


# =====================================================================================================================
# rev 5: a value across either cut of a long text, and an order tag that is a value
# =====================================================================================================================

FORMS = [TRADING["BROKER_API_KEY"], TRADING["BROKER_API_KEY"].lower(), TRADING["BROKER_API_SECRET"], TRADING["BROKER_API_SECRET"].upper(),
         "987654321098", "9876 5432 1098", "987-654-321-098", "987 654 321 098",
         PHRASE, "  ".join(PHRASE.split()), "\n".join(PHRASE.split()), "_".join(PHRASE.split())]
FILLER = "ee eee e eeee ee e eee eeeee "                       # only the letter e
PHRASE_WORD = re.compile(r"(?i)(?<![a-z])(?:" + "|".join(PHRASE.split()) + r")(?![a-z])")


def any_part(text):
    """Any 4 characters in a row (in any letter case) of the key, the secret or the account number, or any word of the phrase."""
    low = text.lower()
    found = [v[i:i + 4] for n in ("BROKER_API_KEY", "BROKER_API_SECRET", "WEBULL_ACCOUNT_ID") for v in [TRADING[n].lower()]
             for i in range(len(v) - 3) if v[i:i + 4] in low]
    return found + PHRASE_WORD.findall(text)


def across(value, at, size=None):
    """The value starting at `at` of a long text; with `size`, behind padding that cleaning removes, so that it stands across
    the raw bound of a text shown cut to a limit whose bound is `size`."""
    if size is not None:
        return " " * at + value + " " + FILLER * ((size - at) // len(FILLER) + 30)
    return (FILLER * (at // len(FILLER) + 1))[:max(0, at - 1)] + " " + value + " " + FILLER * 20


def placements(limit, prefix_len=0):
    """At the head, in the middle, just across the length cut and just across the raw bound."""
    raw = max(20 * limit, 4096)
    return ([(0, None), (limit // 2, None)] + [(max(0, limit - 1 - prefix_len - j), None) for j in (1, 3, 6, 10)]
            + [(raw - prefix_len - j, raw) for j in (1, 4, 8, 12)])


def build_real(w, capsys):
    env = w.env()
    env.environ = None                                                                     # the real command reads the file itself
    out = w.tmp / "out" / "index.html"
    assert op.main(["build", "--out", str(out)], env=env) == 0
    seen = capsys.readouterr()
    return out.read_text(), (seen.out + seen.err).replace(str(out), " ")                   # the path it prints is the test's own folder


def test_the_leak_check_finds_nothing_on_a_page_without_planted_values(bare_world, trading_file, capsys):
    html, said = build_real(bare_world, capsys)
    assert any_part(html) == [] and any_part(said) == []


def test_no_part_of_a_value_reaches_the_page_at_the_head_middle_or_across_either_cut_of_any_long_text(bare_world, trading_file, capsys):
    w = bare_world
    for n, form in enumerate(FORMS):
        flat = " ".join(form.split("\n"))
        logs = sorted(w.logs.glob("valedia-*.log"))
        for p in logs:
            p.unlink()
        for k, (at, size) in enumerate(placements(200)):                                   # the last line of a log: cut to 200
            (w.logs / f"valedia-x{k}.log").write_text("first line\n" + across(flat, at, size) + "\n")   # (one line holds no break)
        w.jobs = [("-", "9", "com.valedia." + across(flat, at, size)) for at, size in placements(120, len("com.valedia."))]
        rows = []
        for field, limit in (("title", 200), ("time", 40), ("level", 20), ("key", 80)):
            for k, (at, size) in enumerate(placements(limit, len("job:x-0 ") if field == "key" else 0)):
                row = {"time": "2026-11-13T19:00:00Z", "level": "warning", "key": f"job:x{n}-{field}-{k}", "title": "t", "detail": "d"}
                row[field] = (row["key"] + " " if field == "key" else "") + across(form, at, size)
                rows.append(row)
        (w.root / "artifacts" / "owner" / "alerts.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
        unverified = [across(form, at, size) for at, size in placements(80)]
        for k, ((at, size), (at4, size4)) in enumerate(zip(placements(240), placements(400))):
            for d in range(5):                                                             # the step reason: cut to 240
                write_step_entry(w, None, "stopped", "send", across(form, *placements(240)[(k + d) % 10]), day=f"2026-11-{9 + d}")
            tax = ({"unavailable": across(form, at4, size4)} if k % 2 else
                   {"year": 2026, "note": across(form, at4, size4), "unverified": unverified})
            w.journal.write("2026-11-13", {"type": "summary", "equity": 1.0, "strategy_values": {}, "benchmark_values": {}, "tax": tax})
            html, said = build_real(w, capsys)
            assert any_part(html) == [] and any_part(said) == [], (n, k, any_part(html)[:6], any_part(said)[:6])
    assert "&lt;secret&gt;" in html and "&lt;number&gt;" in html


def test_an_order_tag_that_is_or_holds_a_value_shows_no_part_of_it(bare_world, trading_file, capsys):
    w = bare_world
    db = sqlite3.connect(w.ledger_path)
    tags = [TRADING["WEBULL_ACCOUNT_ID"], "9876-5432-1098", TRADING["BROKER_API_KEY"], "paper-" + TRADING["BROKER_API_KEY"],
            TRADING["BROKER_API_SECRET"].lower(), "_".join(PHRASE.split())]
    for i, tag in enumerate(tags):
        db.execute("INSERT INTO orders (tag, symbol, units, reference_price, state, created, updated) VALUES (?, 'XLE', 5, 60.0, 'accepted', ?, ?)",
                   (tag, NOW - 100 - i, NOW - 90 - i))
        db.execute("INSERT INTO fills (tag, symbol, units, price, fee, time, applied) VALUES (?, 'XLE', 5, 60.0, 0.01, ?, 1)", (tag, NOW - 80 - i))
    db.commit()
    db.close()
    html, said = build_real(w, capsys)
    broker = Page(html).sections["Broker (paper)"]
    assert any_part(html) == [] and any_part(said) == [], any_part(html)
    assert broker.count("<number>") + broker.count("<secret>") >= 2 * len(tags)               # each tag, in both tables, by its mask


# =====================================================================================================================
# rev 6: an order is shown by its mask alone or by its last 4 characters, never by anything else of its tag
# =====================================================================================================================

MASKS = ("<secret>", "<number>", "<held>")
HEXD = "0123456789abcdef"
FUNDS = ["XLE", "SPY", "BRK.B", "BTC-USD", "QQQ", "IWM"]
EXAMPLES = ["client-ABCDEF-7f3a9c2e-123456789012", "webull-ord-9f8e7d6c5b4a-555666777888", "valedia-20261113-123456789-XLE"]


def generated_tags(rng, n):
    """Order tags of every shape the trader, its brokers and its clients write, with long digit runs, hex runs, dates, fund
    symbols and the trading file's values, whole and inside."""
    def digits(a, b):
        return "".join(rng.choice("0123456789") for _ in range(rng.randint(a, b)))

    def hexes(a, b):
        return "".join(rng.choice(HEXD) for _ in range(rng.randint(a, b)))

    def alnum(a, b):
        return "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz0123456789") for _ in range(rng.randint(a, b)))

    values = [TRADING["BROKER_API_KEY"], TRADING["BROKER_API_SECRET"], TRADING["WEBULL_ACCOUNT_ID"], "_".join(PHRASE.split()),
              "-".join(PHRASE.split()), TRADING["BROKER_API_KEY"].lower(), TRADING["BROKER_API_SECRET"].upper()]
    day = lambda: f"2026{rng.randint(1, 12):02d}{rng.randint(1, 28):02d}"                 # noqa: E731
    shapes = [
        lambda: f"os-{day()}-{rng.randint(100000, 235959)}-{hexes(6, 6)}" + rng.choice(["", "-" + rng.choice(FUNDS)]),
        lambda: f"os-{day()}-{rng.randint(100000, 235959)}-{alnum(4, 9)}-{rng.choice(FUNDS)}",
        lambda: f"client-{alnum(4, 8)}-{hexes(6, 10)}-{digits(9, 16)}",
        lambda: f"webull-ord-{hexes(8, 16)}-{digits(9, 14)}",
        lambda: f"valedia-{day()}-{digits(6, 12)}-{rng.choice(FUNDS)}",
        lambda: digits(9, 20),
        lambda: "-".join(digits(3, 4) for _ in range(rng.randint(3, 5))),
        lambda: hexes(8, 32),
        lambda: f"{rng.choice(['paper', 'live', 'ord', 'client'])}-{alnum(4, 12)}-{digits(1, 8)}",
        lambda: f"{day()[:4]}-{day()[4:6]}-{day()[6:]}-{rng.choice(FUNDS)}",
        lambda: rng.choice(FUNDS) + "-" + alnum(1, 6),
        lambda: rng.choice(values),
        lambda: rng.choice(["paper-", "ord-", "x", ""]) + rng.choice(values) + rng.choice(["-0007", "-XLE", "", "_9"]),
        lambda: f"{alnum(2, 6)}-{digits(9, 12)}-{alnum(2, 6)}",
        lambda: alnum(1, 4),
    ]
    return [rng.choice(shapes)() for _ in range(n)] + EXAMPLES


def test_short_id_is_the_mask_alone_or_the_ellipsis_and_the_last_four_characters_for_generated_tags(bare_world, trading_file):
    al.register_trading_file(bare_world.env())
    tags = generated_tags(random.Random(66), 10000)
    masked = 0
    for tag in tags:
        out = op.short_id(tag)
        found = re.search("|".join(map(re.escape, MASKS)), al.clean(tag, 400))
        if found:
            masked += 1
            assert out == found.group(), (tag, out)                                         # the mask alone
        else:
            assert out == "…" + tag[-4:], (tag, out)                                        # the last 4 characters only
    assert [op.short_id(t) for t in EXAMPLES] == ["<number>"] * 3
    assert masked > 2000 and len(tags) - masked > 2000                                       # both kinds were met
    assert op.short_id("os-20261113-143501-a1b2c3-XLE") == "…-XLE"
    assert [op.short_id(t) for t in ("abcd", "ab", "", None, 12345)] == ["…abcd", "…ab", "…", "…None", "…2345"]  # as in rev 4


def grams(text, n=5):
    low = text.lower()
    return {low[i:i + n] for i in range(len(low) - n + 1)}


def test_no_five_characters_of_any_order_tag_reach_the_page(bare_world, trading_file, capsys):
    w = bare_world
    base, _ = build_real(w, capsys)
    tags = sorted(set(generated_tags(random.Random(67), 10000)))
    db = sqlite3.connect(w.ledger_path)
    for i, tag in enumerate(tags):
        db.execute("INSERT INTO orders (tag, symbol, units, reference_price, state, created, updated) VALUES (?, 'XLE', 1, 1.0, 'accepted', ?, ?)",
                   (tag, NOW - 100, NOW - 90))
        db.execute("INSERT INTO fills (tag, symbol, units, price, fee, time, applied) VALUES (?, 'XLE', 1, 1.0, 0.0, ?, 1)", (tag, NOW - 80))
    db.commit()
    db.close()
    html, said = build_real(w, capsys)
    assert html.count("<tr>") >= 2 * len(tags)                                               # every planted tag is in both tables
    page = grams(html) | grams(said)
    known = grams(base)                                                                      # the page's own words and numbers
    shown = sorted({(t, t.lower()[i:i + 5]) for t in tags for i in range(len(t) - 4)
                    if t.lower()[i:i + 5] in page and t.lower()[i:i + 5] not in known})
    assert shown == [], shown[:10]
