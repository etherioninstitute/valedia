"""Owned relocated-source integration only; no original account state or backend imports.

Set MIROFISH_TEST_WORK to the authorized builder directory containing fixture_app.
Run this copied file from fixture_app/src under the task's denied-network OS policy.
"""
import dataclasses
from contextlib import closing, contextmanager
from datetime import datetime, timezone, timedelta
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import time
import sqlite3
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
import unittest
import uuid

from mirofish_provenance import AuthorityStore, ProducerProfile, PaperBinding, read_file
from mirofish_paper import LocalPaperFlow, PaperReview
from recovery_state import Journal, Refused

WORK = Path(os.environ["MIROFISH_TEST_WORK"])
APP = Path(__file__).resolve().parent.parent
if APP != WORK / "fixture_app":
    raise RuntimeError("tests require exact-byte owned relocated app; original state refused")
PINS = WORK.parent / "provenance-scout/pinned-source"
RELS = ("backend/app/services/report_agent.py", "backend/app/services/simulation_manager.py", "backend/app/services/simulation_runner.py", "backend/app/models/project.py", "backend/app/api/report.py", "backend/app/api/simulation.py")
TEXT = "BUY NVDA BUY NVDA BUY BTC BUY BTC\nNVIDIA and Bitcoin bullish surge rally breakout recovery strong upside."


@contextmanager
def fifo_watchdog():
    """A test deadline, never the product's mechanism for rejecting a FIFO."""
    fired = {"fired": False}
    previous = signal.getsignal(signal.SIGALRM)
    def interrupt(*unused):
        fired["fired"] = True
        raise TimeoutError("test watchdog interrupted blocking FIFO open")
    signal.signal(signal.SIGALRM, interrupt)
    signal.setitimer(signal.ITIMER_REAL, 1.0)
    try:
        yield fired
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def record_fifo_result(result):
    result["reader_sha256"] = hashlib.sha256((APP / "src/mirofish_provenance.py").read_bytes()).hexdigest()
    with (WORK / "test_runs/fifo-results.jsonl").open("a") as output:
        output.write(json.dumps(result, sort_keys=True) + "\n")


class Clock:
    def __init__(self):
        self.value = datetime(2026, 11, 6, 12, tzinfo=timezone.utc)
        self.trigger = None
        self.fired = False
        self.journal = None
        self.authority = None
        self.phase = "SUBMITTING"

    def __call__(self):
        if self.trigger and not self.fired:
            active = False
            target = self.journal if self.phase == "SUBMITTING" else self.authority
            if target and Path(target).exists():
                try:
                    with closing(sqlite3.connect("file:" + str(target) + "?mode=ro", uri=True, timeout=1)) as c, c:
                        query = "SELECT COUNT(*) FROM intents WHERE status='SUBMITTING'" if self.phase == "SUBMITTING" else "SELECT COUNT(*) FROM claims WHERE status='PENDING'"
                        active = c.execute(query).fetchone()[0] > 0
                except sqlite3.Error:
                    pass
            if active:
                self.fired = True
                self.trigger()
        return self.value


class Fixture:
    def __init__(self, base, *, limit="200.00"):
        self.base = Path(base)
        self.root = self.base / "producer"
        (self.root / "backend/uploads/reports").mkdir(parents=True)
        pins = []
        for rel in RELS:
            raw = (PINS / rel).read_bytes()
            p = self.root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(raw)
            pins.append((rel, hashlib.sha256(raw).hexdigest()))
        self.sim, self.proj, self.report = "sim_000000000001", "proj_000000000001", "report_000000000001"
        self.graph = "graph-synthetic"
        self.simrel = "backend/uploads/simulations/" + self.sim
        self.projrel = "backend/uploads/projects/" + self.proj + "/project.json"
        self.runrel = self.simrel + "/run_state.json"
        self.put(self.projrel, {"project_id": self.proj, "graph_id": self.graph, "created_at": "2026-11-06T10:00:00", "updated_at": "2026-11-06T10:30:00", "simulation_requirement": "owned synthetic local paper"})
        self.put(self.simrel + "/state.json", {"simulation_id": self.sim, "project_id": self.proj, "graph_id": self.graph, "created_at": "2026-11-06T11:00:00", "updated_at": "2026-11-06T12:00:00", "status": "running"})
        self.run = {"simulation_id": self.sim, "runner_status": "running", "started_at": "2026-11-06T12:00:00", "updated_at": "2026-11-06T12:00:00", "completed_at": None}
        self.put(self.runrel, self.run)
        self.clock = Clock()
        self.jp = str(APP / "state" / ("mf-test-" + uuid.uuid4().hex + ".sqlite3"))
        self.profile = ProducerProfile("synthetic-reviewed-local", str(self.root), tuple(pins), hashlib.sha256((APP / "src/mirofish_signal_extractor.py").read_bytes()).hexdigest(), research_only=False)
        self.binding = PaperBinding("paper-local", self.jp, (("alpaca", "NVDA"), ("coinbase", "BTC-USD")))
        self.store = AuthorityStore(self.base / "authority", self.profile, self.binding, clock=self.clock)
        self.clock.authority = self.store.path
        self.journal = Journal(self.jp, account="paper-local", equity="1000.00", daily_limit=limit, clock=self.clock)
        self.clock.journal = self.jp
        self.limit = limit
        self.flow = LocalPaperFlow(self.store, self.journal)
        self.rid = None

    def put(self, rel, obj):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(obj))

    def complete(self, *, report=None, text=TEXT, seconds=90):
        self.clock.value += timedelta(seconds=seconds)
        self.run.update(runner_status="completed", updated_at="2026-11-06T12:01:00", completed_at="2026-11-06T12:01:00")
        self.put(self.runrel, self.run)
        rid = report or self.report
        self.meta_rel = "backend/uploads/reports/" + rid + "/meta.json"
        self.body_rel = "backend/uploads/reports/" + rid + "/full_report.md"
        self.meta = {"report_id": rid, "simulation_id": self.sim, "graph_id": self.graph, "status": "completed", "created_at": "2026-11-06T12:01:01", "completed_at": "2026-11-06T12:01:20", "markdown_content": text, "outline": {"summary": ""}}
        self.put(self.meta_rel, self.meta)
        (self.root / self.body_rel).write_text(text)
        return rid

    def capture(self):
        sid = self.store.begin(self.sim)
        self.complete()
        self.rid = self.store.finish(sid, self.report)
        return self.rid

    def review(self, venue="alpaca", instrument="NVDA", **kwargs):
        fields = dict(review_id="fixed-10-v1", account="paper-local", journal_path=self.jp, venue=venue, instrument=instrument, amount="10.00", equity="1000.00", daily_limit=self.limit)
        fields.update(kwargs)
        return PaperReview(**fields)

    def proposal(self, instrument="NVDA", **kwargs):
        return {"receipt_ref": self.rid, "instrument": instrument, "action": "buy", **kwargs}

    def rows(self, table):
        with closing(sqlite3.connect(self.jp)) as c, c:
            return c.execute("SELECT * FROM " + table).fetchall()

    def acks(self):
        with closing(sqlite3.connect(self.store.path)) as c, c:
            return c.execute("SELECT COUNT(*) FROM acknowledgements").fetchone()[0]


def worker(context):
    x = json.loads(Path(context).read_text())
    clock = Clock(); clock.value = datetime.fromisoformat(x["now"])
    profile = ProducerProfile(x["producer_id"], x["root"], tuple(tuple(v) for v in x["pins"]), x["extractor"], research_only=False)
    binding = PaperBinding("paper-local", x["journal"], (("alpaca", "NVDA"), ("coinbase", "BTC-USD")))
    store = AuthorityStore(x["store"], profile, binding, clock=clock)
    journal = Journal(x["journal"], account="paper-local", equity="1000.00", daily_limit="200.00", clock=clock)
    review = PaperReview("fixed-10-v1", "paper-local", x["journal"], "alpaca", "NVDA", "10.00", "1000.00", "200.00")
    try:
        result = LocalPaperFlow(store, journal).submit({"receipt_ref": x["rid"], "instrument": "NVDA", "action": "buy"}, review)
        print(json.dumps({"ok": True, "result": result}))
    except Refused as exc:
        print(json.dumps({"ok": False, "refused": str(exc)}))


class PaperIntegration(unittest.TestCase):
    def setUp(self):
        (WORK / "test_runs").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix="case-", dir=WORK / "test_runs")
        self.addCleanup(self.temp.cleanup)
        self.fixtures = []
        self.f = self.new_fixture()
        self.halt = APP / "state/halt.json"
        self.halt.unlink(missing_ok=True)
        self.addCleanup(lambda: self.halt.unlink(missing_ok=True))
        self.environment = {k: os.environ.get(k) for k in ("VALEDIA_LIVE_OPT_IN", "ALPACA_PAPER", "VALEDIA_TRADING_HALT")}
        for key in self.environment:
            os.environ.pop(key, None)
        self.addCleanup(self.restore_environment)

    def restore_environment(self):
        for key, value in self.environment.items():
            if value is None: os.environ.pop(key, None)
            else: os.environ[key] = value

    def new_fixture(self, **kwargs):
        f = Fixture(Path(self.temp.name) / str(len(self.fixtures)), **kwargs)
        self.fixtures.append(f)
        self.addCleanup(lambda: Path(f.jp).unlink(missing_ok=True))
        return f

    def untouched(self, f=None):
        f = f or self.f
        self.assertEqual(f.rows("decisions"), [])
        self.assertEqual(f.rows("intents"), [])
        self.assertEqual(f.acks(), 0)

    def test_equity_crypto_actual_leaf_and_explicit_replay(self):
        f = self.f; f.capture()
        for venue, instrument in f.binding.registry:
            result = f.flow.submit(f.proposal(instrument), f.review(venue, instrument))
            self.assertEqual(result[0]["status"], "ACKNOWLEDGED")
            replay = f.flow.submit(f.proposal(instrument), f.review(venue, instrument))
            self.assertTrue(replay[0]["replay"])
            self.assertFalse(replay[0]["attempted"])
            self.assertTrue(replay[0]["original_attempted"])
        self.assertEqual(len(f.rows("intents")), 2)
        self.assertEqual(f.acks(), 2)
        with closing(sqlite3.connect(f.jp)) as c, c:
            request = json.loads(c.execute("SELECT request FROM decisions LIMIT 1").fetchone()[0])
        evidence = request["legs"][0]["mirofish_evidence"]
        self.assertEqual(len(evidence["manifest_sha256"]), 64)
        self.assertEqual(evidence["review"]["amount"], "10.00")
        self.assertIn("approved-local-observation", evidence["proof"])

    def test_begin_only_running_and_historical_profile(self):
        f = self.f
        for status in ("idle", "starting", "completed", "stopped", "failed", None):
            with self.subTest(status=status):
                f.put(f.runrel, {**f.run, "runner_status": status})
                with self.assertRaises(Refused): f.store.begin(f.sim)
        f.put(f.runrel, {**f.run, "started_at": None})
        with self.assertRaises(Refused): f.store.begin(f.sim)
        historical = dataclasses.replace(f.profile, root="/Volumes/NIMROD_SSD/backups/2026-10-22/home/mirofish-offline", research_only=True)
        store = AuthorityStore(f.base / "historical-audit-store", historical, f.binding, clock=f.clock)
        with self.assertRaisesRegex(Refused, "BLOCKED_HISTORICAL"): store.begin("sim_44935cf0125b")
        self.untouched()

    def test_all_baseline_ids_including_orphan_and_pending(self):
        for kind in ("orphan", "pending"):
            f = self.new_fixture()
            p = f.root / ("backend/uploads/reports/" + f.report);p.mkdir()
            if kind == "pending": f.put(str(p.relative_to(f.root) / "meta.json"), {"status": "pending"})
            sid = f.store.begin(f.sim);f.complete()
            with self.assertRaisesRegex(Refused, "baseline/orphan"): f.store.finish(sid, f.report)
            self.untouched(f)

    def test_baseline_body_identity_even_new_report_id(self):
        f = self.f
        old = "backend/uploads/reports/report_000000000099"
        f.put(old + "/meta.json", {"markdown_content": TEXT, "status": "failed"})
        sid = f.store.begin(f.sim);f.complete()
        with self.assertRaisesRegex(Refused, "content"): f.store.finish(sid, f.report)
        self.untouched()

    def test_token_graph_identity_chronology_and_split_publication(self):
        for case in ("token", "graph", "project", "report_id", "sim_id", "stopped", "report_failed", "chronology", "split", "summary"):
            f = self.new_fixture();sid = f.store.begin(f.sim);f.complete()
            if case == "token": f.put(f.runrel, {**f.run, "started_at": "2026-11-06T12:00:01"})
            elif case == "graph": f.put(f.meta_rel, {**f.meta, "graph_id": "other"})
            elif case == "project": f.put(f.projrel, {"project_id": f.proj, "graph_id": f.graph, "created_at": "2026-11-06T10:00:00", "simulation_requirement": "changed"})
            elif case == "report_id": f.put(f.meta_rel, {**f.meta, "report_id": "report_000000000002"})
            elif case == "sim_id": f.put(f.meta_rel, {**f.meta, "simulation_id": "sim_000000000002"})
            elif case == "stopped": f.put(f.runrel, {**f.run, "runner_status": "stopped"})
            elif case == "report_failed": f.put(f.meta_rel, {**f.meta, "status": "failed"})
            elif case == "chronology": f.put(f.meta_rel, {**f.meta, "created_at": "2026-11-06T11:59:00"})
            elif case == "split": (f.root / f.body_rel).write_text("different")
            elif case == "summary": f.put(f.meta_rel, {**f.meta, "outline": {"summary": 2}})
            with self.subTest(case=case):
                with self.assertRaises(Refused): f.store.finish(sid, f.report)
                self.untouched(f)

    def test_malformed_symlink_oversize_and_duplicate_metadata(self):
        for case in ("empty_run", "duplicate_key", "symlink", "oversize"):
            f = self.new_fixture();sid = f.store.begin(f.sim);f.complete()
            if case == "empty_run": (f.root / f.runrel).write_text("")
            elif case == "duplicate_key": (f.root / f.meta_rel).write_text('{"report_id":"x","report_id":"y"}')
            elif case == "symlink":
                p = f.root / f.body_rel;p.unlink();p.symlink_to(f.root / f.meta_rel)
            else: (f.root / f.body_rel).write_bytes(b"x" * (1024 * 1024 + 1))
            with self.subTest(case=case):
                with self.assertRaises(Refused): f.store.finish(sid, f.report)
                self.untouched(f)

    def test_forged_receipt_and_proposal_overrides_no_journal_effects(self):
        f = self.f;f.capture()
        for extra in ({"verified": True}, {"score": 99}, {"amount": "100"}, {"account": "other"}, {"policy": "live"}, {"clock": "now"}, {"transport": "broker"}, {"action": "sell"}, {"instrument": "nvda"}, {"receipt_ref": "forged-verified-json"}):
            with self.subTest(extra=extra):
                with self.assertRaises(Refused): f.flow.submit(f.proposal(**extra), f.review())
                self.untouched()

    def test_snapshot_summary_source_receipt_and_profile_tamper(self):
        for case in ("body", "summary", "source", "receipt", "profile"):
            f = self.new_fixture();f.capture()
            if case == "body": (f.root / f.body_rel).write_text(TEXT + " changed")
            elif case == "summary": f.put(f.meta_rel, {**f.meta, "outline": {"summary": "changed"}})
            elif case == "source": (f.root / RELS[0]).write_text("changed pinned code")
            elif case == "receipt":
                with closing(sqlite3.connect(f.store.path)) as c, c: c.execute("UPDATE receipts SET body='{}'")
            else: object.__setattr__(f.profile, "producer_id", "changed")
            with self.subTest(case=case):
                with self.assertRaises(Refused): f.flow.submit(f.proposal(), f.review())
                self.untouched(f)

    def test_clock_expiry_future_naive_and_restart_rollback(self):
        f = self.f;f.capture();f.clock.value += timedelta(seconds=510)
        with self.assertRaises(Refused): f.flow.submit(f.proposal(), f.review())
        self.untouched()
        f.clock.value -= timedelta(seconds=1)
        reopened = AuthorityStore(f.base / "authority", f.profile, f.binding, clock=f.clock)
        with self.assertRaisesRegex(Refused, "regressed"): reopened.validate(f.rid)
        for value in (datetime(2026, 11, 6, 12), datetime(2026, 11, 6, 11, tzinfo=timezone.utc)):
            f.clock.value = value
            with self.assertRaises(Refused): f.store.validate(f.rid)

    def test_finished_restart_replay_and_deleted_recreated_journal(self):
        f = self.f;f.capture();f.flow.submit(f.proposal(), f.review())
        store = AuthorityStore(f.base / "authority", f.profile, f.binding, clock=f.clock)
        j = Journal(f.jp, account="paper-local", equity="1000.00", daily_limit="200.00", clock=f.clock)
        replay = LocalPaperFlow(store, j).submit(f.proposal(), f.review())
        self.assertTrue(replay[0]["replay"]);self.assertFalse(replay[0]["attempted"])
        with closing(sqlite3.connect(f.jp)) as c, c:
            original = c.execute("SELECT request FROM decisions").fetchone()[0]
            drifted = json.loads(original); drifted["legs"][0]["allocation"] = "11.00"
            c.execute("UPDATE decisions SET request=?", (json.dumps(drifted),))
        with self.assertRaises(Refused): f.flow.submit(f.proposal(), f.review())
        with closing(sqlite3.connect(f.jp)) as c, c: c.execute("UPDATE decisions SET request=?", (original,))
        with self.assertRaises(Refused):
            AuthorityStore(f.base / "authority", f.profile, dataclasses.replace(f.binding, journal_path=f.jp + ".other"), clock=f.clock)
        Path(f.jp).unlink()
        with self.assertRaises(Refused): f.flow.submit(f.proposal(), f.review())
        self.assertFalse(Path(f.jp).exists())
        j = Journal(f.jp, account="paper-local", equity="1000.00", daily_limit="200.00", clock=f.clock)
        with self.assertRaises(Refused): LocalPaperFlow(store, j).submit(f.proposal(), f.review())
        self.assertEqual(f.acks(), 1);self.assertEqual(f.rows("decisions"), [])

    def test_changed_review_amount_reason_and_alias_refused(self):
        f = self.f;f.capture();f.flow.submit(f.proposal(), f.review())
        for review, proposal in ((f.review(amount="11.00"), f.proposal()), (f.review(review_id="other"), f.proposal()), (f.review(), f.proposal(reason="changed")), (f.review(instrument="NVDA-USD"), f.proposal("NVDA-USD")), (f.review(account="other"), f.proposal())):
            with self.assertRaises(Refused): f.flow.submit(proposal, review)
        self.assertEqual(f.acks(), 1);self.assertEqual(len(f.rows("intents")), 1)

    def test_recapture_and_concurrent_finish_reuses_first_observation(self):
        f = self.f
        sessions = [f.store.begin(f.sim) for _ in range(4)]
        f.complete()
        with ThreadPoolExecutor(max_workers=4) as pool:
            refs = list(pool.map(lambda sid: f.store.finish(sid, f.report), sessions))
        self.assertEqual(len(set(refs)), 1)
        f.rid = refs[0]
        before = f.store.validate(f.rid)[0]
        f.clock.value += timedelta(seconds=1)
        self.assertEqual(f.store.finish(sessions[0], f.report), f.rid)
        self.assertEqual(f.store.validate(f.rid)[0], before)
        with self.assertRaises(Refused): f.store.begin(f.sim)
        with closing(sqlite3.connect(f.store.path)) as c, c: self.assertEqual(c.execute("SELECT COUNT(*) FROM receipts").fetchone()[0], 1)

    def test_process_concurrency_one_send(self):
        f = self.f;f.capture()
        context = f.base / "trusted-test-control.json"
        context.write_text(json.dumps({"producer_id": f.profile.producer_id, "root": str(f.root), "pins": f.profile.source_pins, "extractor": f.profile.extractor_sha256, "journal": f.jp, "store": str(f.base / "authority"), "now": f.clock.value.isoformat(), "rid": f.rid}))
        jobs = [subprocess.Popen([sys.executable, "-B", __file__, "--worker", str(context)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(3)]
        answers = []
        for job in jobs:
            out, err = job.communicate(timeout=20)
            self.assertEqual(job.returncode, 0, err)
            answers.append(json.loads(out))
        self.assertTrue(any(x["ok"] for x in answers))
        self.assertEqual(f.acks(), 1);self.assertEqual(len(f.rows("intents")), 1)

    def test_pending_claim_crash_is_not_auto_retried(self):
        f = self.f;f.capture()
        f.clock.phase = "PENDING"
        def crash(): raise RuntimeError("synthetic crash after durable claim, before allocator decision")
        f.clock.trigger = crash
        with self.assertRaises(RuntimeError): f.flow.submit(f.proposal(), f.review())
        self.assertTrue(f.clock.fired)
        f.clock.trigger = None
        with self.assertRaisesRegex(Refused, "pending"): f.flow.submit(f.proposal(), f.review())
        self.untouched()
        with closing(sqlite3.connect(f.store.path)) as c, c: self.assertEqual(c.execute("SELECT status FROM claims").fetchone()[0], "PENDING")

    def test_actual_accumulated_cap_no_second_reservation(self):
        f = self.new_fixture(limit="15.00");f.capture()
        f.flow.submit(f.proposal(), f.review())
        with self.assertRaises(Refused): f.flow.submit(f.proposal("BTC-USD"), f.review("coinbase", "BTC-USD"))
        self.assertEqual(f.acks(), 1);self.assertEqual(len(f.rows("intents")), 1)

    def test_pre_reservation_halt_live_and_unsupported_review(self):
        f = self.f;f.capture()
        self.halt.write_text('{"halted":true}')
        with self.assertRaises(Refused): f.flow.submit(f.proposal(), f.review())
        self.halt.unlink();os.environ["ALPACA_PAPER"] = "false"
        with self.assertRaises(Refused): f.flow.submit(f.proposal(), f.review())
        os.environ.pop("ALPACA_PAPER")
        with self.assertRaises(Refused): f.flow.submit(f.proposal(), f.review(venue="unsupported"))
        self.untouched()

    def test_late_faults_retain_unknown_notional_and_clock_floor(self):
        for case in ("expiry", "day", "halt", "live", "source", "profile", "lost_reply"):
            f = self.new_fixture()
            if case == "day": f.clock.value = datetime(2026, 11, 6, 23, 59, 30, tzinfo=timezone.utc)
            sid = f.store.begin(f.sim);f.complete(seconds=5 if case == "day" else 90);f.rid = f.store.finish(sid, f.report)
            def fault():
                if case == "expiry": f.clock.value += timedelta(seconds=510)
                elif case == "day": f.clock.value += timedelta(seconds=30)
                elif case == "halt": self.halt.write_text('{"halted":true}')
                elif case == "live": os.environ["VALEDIA_LIVE_OPT_IN"] = "1"
                elif case == "source": (f.root / RELS[0]).write_text("changed source")
                elif case == "profile": object.__setattr__(f.profile, "producer_id", "changed")
                else: raise OSError("synthetic local transport reply failure")
            f.clock.trigger = fault
            with self.subTest(case=case):
                if case == "profile":
                    with self.assertRaises(Refused): f.flow.submit(f.proposal(), f.review())
                    with closing(sqlite3.connect(f.jp)) as c, c:
                        self.assertEqual(c.execute("SELECT status,amount FROM intents").fetchone(), ("UNKNOWN", 1000))
                    with closing(sqlite3.connect(f.store.path)) as c, c:
                        self.assertEqual(c.execute("SELECT status FROM claims").fetchone()[0], "PENDING")
                    object.__setattr__(f.profile, "producer_id", "synthetic-reviewed-local")
                    reopened = AuthorityStore(f.base / "authority", f.profile, f.binding, clock=f.clock)
                    with self.assertRaisesRegex(Refused, "pending"):
                        LocalPaperFlow(reopened, f.journal).submit(f.proposal(), f.review())
                else:
                    answer = f.flow.submit(f.proposal(), f.review())
                    self.assertEqual(answer[0]["status"], "UNKNOWN")
                    self.assertEqual(answer[0]["amount"], 1000)
                self.assertTrue(f.clock.fired)
                self.assertEqual(f.acks(), 0)
                self.assertEqual(len(f.rows("intents")), 1)
                if case == "expiry":
                    f.clock.trigger = None;f.clock.value -= timedelta(seconds=1)
                    reopened = AuthorityStore(f.base / "authority", f.profile, f.binding, clock=f.clock)
                    with self.assertRaisesRegex(Refused, "regressed"): reopened.validate(f.rid)
            self.halt.unlink(missing_ok=True);os.environ.pop("VALEDIA_LIVE_OPT_IN", None)

    def test_fifo_reader_refuses_without_watchdog(self):
        fifo = self.f.root / "artifact-without-writer.fifo"
        os.mkfifo(fifo)
        start = time.monotonic()
        with fifo_watchdog() as guard:
            with self.assertRaises(Refused):
                read_file(self.f.root, fifo.name)
        elapsed = time.monotonic() - start
        record_fifo_result({"case": "direct_reader", "watchdog_fired": guard["fired"], "elapsed_seconds": elapsed, "acknowledgements": self.f.acks()})
        self.assertFalse(guard["fired"], "product reader blocked until watchdog")
        self.assertLess(elapsed, 0.5)
        self.untouched()

    def test_post_reservation_fifo_retains_unknown_without_watchdog_or_retry(self):
        f = self.f; f.capture()
        body = f.root / f.body_rel
        original = body.read_bytes()
        def replace_after_reservation():
            body.unlink()
            os.mkfifo(body)
        f.clock.trigger = replace_after_reservation
        start = time.monotonic()
        try:
            with fifo_watchdog() as guard:
                answer = f.flow.submit(f.proposal(), f.review())
        finally:
            body.unlink(missing_ok=True)
            body.write_bytes(original)
        elapsed = time.monotonic() - start
        record_fifo_result({"case": "actual_post_reservation", "watchdog_fired": guard["fired"], "elapsed_seconds": elapsed, "fault_after_actual_submitting": f.clock.fired, "status": answer[0]["status"], "reserved_cents": answer[0]["amount"], "acknowledgements": f.acks()})
        self.assertFalse(guard["fired"], "actual final guard blocked until watchdog")
        self.assertLess(elapsed, 0.5)
        self.assertTrue(f.clock.fired)
        self.assertIn(answer[0]["status"], {"UNKNOWN", "SUBMITTING"})
        self.assertEqual(answer[0]["amount"], 1000)
        self.assertEqual(f.acks(), 0)
        replay = f.flow.submit(f.proposal(), f.review())
        self.assertEqual(replay[0]["status"], answer[0]["status"])
        self.assertTrue(replay[0]["replay"])
        self.assertFalse(replay[0]["attempted"])
        self.assertEqual(f.acks(), 0)
        self.assertEqual(len(f.rows("intents")), 1)
        record_fifo_result({"case": "post_reservation_restored_replay", "status": replay[0]["status"], "attempted": replay[0]["attempted"], "acknowledgements": f.acks(), "intents": len(f.rows("intents"))})

    def test_no_signal_macro_neutral_and_bearish_refuse(self):
        for text in ("No named instrument or event.", "The Fed outlook is bullish rally recovery.", "SELL NVDA SELL NVDA NVIDIA bearish crash plunge downside."):
            f = self.new_fixture();sid = f.store.begin(f.sim);f.complete(text=text);f.rid = f.store.finish(sid, f.report)
            with self.assertRaises(Refused): f.flow.submit(f.proposal(), f.review())
            self.untouched(f)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        worker(sys.argv[2])
    else:
        unittest.main(verbosity=2)
