"""Offline synthetic contract tests; imports only the shared stdlib intake module.

Run with TMPDIR pointing to an owned fixture directory. MIROFISH_TEST_SOURCE can
select the frozen before-source for red/green evidence without editing source.
"""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

SOURCE = Path(os.environ.get("MIROFISH_TEST_SOURCE", str(Path(__file__).with_name("mirofish_intel_archive.py"))))
spec = importlib.util.spec_from_file_location("mirofish_intake_under_test", SOURCE)
mia = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mia)
NOW = datetime(2026, 11, 6, 21, 45, tzinfo=timezone.utc)


def strong(**updates):
    doc = {"source": "mirofish_swarm", "status": "active", "as_of": NOW.isoformat(),
           "overall_bias": 75, "signals": {"BTC-USD": {"score": 80, "confidence": 0.7}}}
    doc.update(updates)
    return doc


class IntakeContract(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="mf-admission-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.live = self.root / "mirofish_signal.json"
        self.archive = self.root / "archive"

    def usable(self, doc, marker=None, now=NOW):
        return mia.evaluate(doc, marker=marker, now=now)["usable_for_fv"]

    def put(self, doc):
        self.live.write_text(json.dumps(doc))

    def snapshot(self):
        return {str(p.relative_to(self.root)): p.read_bytes()
                for p in self.root.rglob("*") if p.is_file()}

    def test_valid_numeric_bounds_and_optional_fields(self):
        for bias in (0, 100, 75.0):
            for score in (0, 100, 50.0):
                for confidence in (0, 1, 0.7):
                    with self.subTest(bias=bias, score=score, confidence=confidence):
                        self.assertTrue(self.usable(strong(overall_bias=bias, signals={"BTC": {"score": score, "confidence": confidence}})))
        self.assertTrue(self.usable(strong(signals={"BTC": {"score": 80}})))
        doc = strong(); doc.pop("status"); doc.pop("signals")
        self.assertTrue(self.usable(doc))
        self.assertTrue(self.usable(strong(signals={})))
        self.assertTrue(self.usable(strong(as_of=NOW.replace(tzinfo=None).isoformat())))

    def test_invalid_bias_is_not_evidence(self):
        for bias in (None, True, False, "75", "NaN", "Infinity", float("nan"), float("inf"), -float("inf"), -1, 101, 10**1000):
            with self.subTest(bias=repr(bias)):
                self.assertFalse(self.usable(strong(overall_bias=bias)))

    def test_invalid_signal_shape_and_score_are_not_evidence(self):
        for signals in (None, [], "bad", {"BTC": []}, {"BTC": {}}, {"BTC": {"score": True}}, {"BTC": {"score": "80"}}, {"BTC": {"score": float("nan")}}, {"BTC": {"score": float("inf")}}, {"BTC": {"score": -1}}, {"BTC": {"score": 101}}, {"BTC": {"score": 10**1000}}):
            with self.subTest(signals=repr(signals)):
                self.assertFalse(self.usable(strong(signals=signals)))

    def test_invalid_optional_confidence_is_not_evidence(self):
        for conf in (None, True, "0.7", float("nan"), float("inf"), -0.1, 1.1, 10**1000):
            with self.subTest(conf=repr(conf)):
                self.assertFalse(self.usable(strong(signals={"BTC": {"score": 80, "confidence": conf}})))

    def test_future_time_before_integer_truncation(self):
        for delta in (timedelta(microseconds=1), timedelta(seconds=1), timedelta(days=365)):
            for key in ("as_of", "ts"):
                with self.subTest(delta=delta, key=key):
                    doc = strong(**{key: (NOW + delta).isoformat()})
                    self.assertFalse(self.usable(doc))

    def test_both_supplied_timestamps_are_validated(self):
        for key, other in (("as_of", "ts"), ("ts", "as_of")):
            for bad in ("bad", None, "0001-01-01T00:00:00+14:00", (NOW + timedelta(microseconds=1)).isoformat()):
                with self.subTest(key=key, bad=bad):
                    doc = strong(**{key: bad, other: NOW.isoformat()})
                    self.assertFalse(self.usable(doc))
        self.assertFalse(self.usable(strong(), now=datetime.min.replace(tzinfo=timezone(timedelta(hours=14)))))

    def test_source_status_archive_and_stale_gates(self):
        for updates in ({"source": "lightweight"}, {"status": "failed"}, {"status": "inactive"}, {"archive": True}, {"role": "archive"}, {"stale": True}, {"stale": "false"}, {"stale": 0}, {"archive": "true"}):
            with self.subTest(updates=updates):
                self.assertFalse(self.usable(strong(**updates)))

    def test_neutral_and_age_boundaries(self):
        for bias in (45, 50, 55):
            self.assertFalse(self.usable(strong(overall_bias=bias)))
        self.assertTrue(self.usable(strong(overall_bias=55.0001)))
        self.assertTrue(self.usable(strong(as_of=(NOW-timedelta(seconds=mia.MAX_AGE_S-1)).isoformat())))
        self.assertFalse(self.usable(strong(as_of=(NOW-timedelta(seconds=mia.MAX_AGE_S)).isoformat())))
        self.assertFalse(self.usable({"source": "mirofish_swarm", "overall_bias": 75}))

    def test_marker_absent_and_empty_are_compatible(self):
        self.put(strong())
        self.assertIsNotNone(mia.load_for_fv(str(self.live), now=NOW))
        self.assertTrue(self.usable(strong(), {}))
        self.assertTrue(self.usable(strong(), {"stale": False}))

    def test_marker_malformed_or_stale_blocks_evidence(self):
        self.put(strong())
        marker = Path(mia.marker_path(str(self.live)))
        for raw in ("{bad", "[]", "null", "true", '{"stale":"false"}', '{"stale":0}', '{"stale":true}', '{"stale":NaN}'):
            with self.subTest(raw=raw):
                marker.write_text(raw)
                self.assertIsNone(mia.load_for_fv(str(self.live), now=NOW))
                self.assertFalse(mia.freshness_of(str(self.live), now=NOW)["usable_for_fv"])
        marker.unlink()
        marker.symlink_to(self.root / "missing-sidecar-target")
        self.assertIsNone(mia.load_for_fv(str(self.live), now=NOW))
        for marker_doc in (False, [], "bad", {"stale": "false"}):
            self.assertFalse(self.usable(strong(), marker_doc))

    def test_unreadable_marker_is_not_absent(self):
        self.put(strong())
        real_open = open
        marker = mia.marker_path(str(self.live))
        def guarded_open(path, *args, **kwargs):
            if os.fspath(path) == marker:
                raise PermissionError("synthetic marker unreadable")
            return real_open(path, *args, **kwargs)
        with patch("builtins.open", guarded_open):
            self.assertIsNone(mia.load_for_fv(str(self.live), now=NOW))

    def test_strict_live_json_and_missing_live(self):
        self.assertIsNone(mia.load_for_fv(str(self.live), now=NOW))
        for raw in ("{bad", "[]", json.dumps(strong(extra=float("nan")))):
            self.live.write_text(raw)
            self.assertIsNone(mia.load_for_fv(str(self.live), now=NOW))

    def test_publish_invalid_has_no_partial_writes(self):
        mia.publish_pass(strong(), live_path=str(self.live), archive_dir=str(self.archive), now=NOW)
        before = self.snapshot()
        cases = [strong(overall_bias=float("nan")), strong(overall_bias=True), strong(overall_bias=10**1000), strong(signals=[]), strong(signals={"BTC": {"score": 101}}), strong(status="failed"), strong(stale=True), strong(role="archive"), strong(source="other"), strong(extra={"nested": float("nan")}), strong(extra={"nested": float("inf")}), strong(extra={"not_json": object()})]
        for key, other in (("as_of", "ts"), ("ts", "as_of")):
            for bad in ("bad", None, (NOW + timedelta(microseconds=1)).isoformat()):
                cases.append(strong(**{key: bad, other: NOW.isoformat()}))
        for doc in cases:
            with self.subTest(doc=repr(doc)):
                with self.assertRaises((ValueError, TypeError, OverflowError)):
                    mia.publish_pass(doc, live_path=str(self.live), archive_dir=str(self.archive), run_id="bad", now=NOW)
                self.assertEqual(before, self.snapshot())
        fresh = self.root / "not_created" / "mirofish_signal.json"
        with self.assertRaises((ValueError, TypeError)):
            mia.publish_pass(strong(extra=float("nan")), live_path=str(fresh), now=NOW)
        self.assertFalse(fresh.parent.exists())

    def test_publish_neutral_macro_and_publication_time(self):
        for bias in (50, 75):
            doc = {"source": "mirofish_swarm", "overall_bias": bias, "signals": {}, "ts": (NOW-timedelta(days=100)).isoformat()}
            body = mia.publish_pass(doc, live_path=str(self.live), archive_dir=str(self.archive), run_id="valid", now=NOW)
            self.assertEqual(body["as_of"], NOW.isoformat())
            self.assertEqual(body["ts"], doc["ts"])
            self.assertEqual(body["usable_for_fv"], bias == 75)
            self.assertEqual(mia.load_for_fv(str(self.live), now=NOW) is not None, bias == 75)
        doc.pop("signals"); doc.pop("ts")
        body = mia.publish_pass(doc, live_path=str(self.live), archive_dir=str(self.archive), now=NOW)
        self.assertTrue(body["usable_for_fv"])

    def test_failure_preserves_live_bytes_and_archive_is_never_fuel(self):
        mia.publish_pass(strong(), live_path=str(self.live), archive_dir=str(self.archive), now=NOW)
        before = self.live.read_bytes()
        mia.publish_fail(str(self.live), "synthetic failure", now=NOW)
        self.assertEqual(self.live.read_bytes(), before)
        self.assertIsNone(mia.load_for_fv(str(self.live), now=NOW))
        for path in self.archive.iterdir():
            self.assertIsNone(mia.load_for_fv(str(path), now=NOW))
        archive_path = self.root / "mirofish-archive" / "copy.json"
        archive_path.parent.mkdir(); archive_path.write_text(json.dumps(strong()))
        self.assertIsNone(mia.load_for_fv(str(archive_path), now=NOW))


if __name__ == "__main__":
    unittest.main(verbosity=2)
