"""Offline tests for the disabled paper runner. Network and broker fail closed."""
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from poly_guide_paper import PaperDesk, VENUE
from poly_guide_runner import PaperRunner, RunnerConfig, validate_forecast_json

NOW = "2026-10-05T17:00:00+00:00"


def market(slug):
    return dict(venue=VENUE, schema="us_binary_v1", slug=slug, tradeable=True,
                rules_text="Fixture resolution rules", rules_digest="sha256:fixture",
                resolves_at="2026-10-10T00:00:00Z", volume_usd=50001,
                tick=.01, min_shares=1)


def forecast(slug):
    return dict(venue=VENUE, market_slug=slug, p_yes=.85, confidence="high",
                model_id="fixture-model", as_of=NOW, rules_digest="sha256:fixture",
                could_be_wrong="Fixture uncertainty", evidence=[
                    {"claim": f"claim {i}", "url": f"https://example.org/{i}"}
                    for i in range(3)])


class DenyTransport:
    def __call__(self, *args, **kwargs):
        raise AssertionError("network must not be called")


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.desk = PaperDesk(self.root / "paper.sqlite", 1000)
        self.addCleanup(self.desk.close)
        self.calls = []
        def markets(limit):
            self.calls.append(("markets", limit))
            return [market(f"m{i}") for i in range(7)]
        def bbo(m):
            return dict(venue=VENUE, slug=m["slug"], as_of=NOW, book={
                "marketData": {"bestBid": {"value": .39}, "bestAsk": {"value": .40, "quantity": 100}}})
        def infer(m):
            return {"forecast": forecast(m["slug"]), "cost_usd": 0.0}
        self.runner = PaperRunner(self.desk, market_transport=markets,
                                  bbo_transport=bbo, inference_transport=infer,
                                  fee_per_share=.01, output_dir=self.root / "daily",
                                  clock=lambda: datetime.fromisoformat(NOW))
        self.addCleanup(self.runner.close)


    def test_disabled_default_makes_no_transport_calls(self):
        runner = PaperRunner(self.desk, market_transport=lambda n: self.fail("network"),
            bbo_transport=lambda m: self.fail("network"),
            inference_transport=lambda m: self.fail("inference"),
            output_dir=self.root, clock=lambda: datetime.fromisoformat(NOW))
        self.addCleanup(runner.close)
        self.assertEqual(runner.run_once(), {"enabled": False, "processed": 0})

    def test_enabled_cycle_limits_top_five_and_writes_daily_report(self):
        self.assertEqual(self.runner.run_once(enabled=True)["processed"], 5)
        self.assertEqual(self.calls, [("markets", 5)])
        path = self.root / "daily" / "2026-10-05.json"
        report = json.loads(path.read_text())
        self.assertEqual(len(report["decisions"]), 5)
        self.assertEqual(report["decisions"][0]["status"], "PAPER_FILL")
        self.assertTrue(all(d["status"] in ("PAPER_FILL", "BLOCKED") for d in report["decisions"]))
        self.assertIn("provenance", report["decisions"][0])

    def test_forecast_json_schema_rejects_invalid_and_preserves_provenance(self):
        obj = forecast("m0")
        parsed = validate_forecast_json(json.dumps(obj), market("m0"), NOW)
        self.assertEqual(parsed["model_id"], "fixture-model")
        with self.assertRaises(ValueError):
            validate_forecast_json('{"p_yes": 9}', market("m0"), NOW)

    def test_cost_requires_measured_nonnegative_value(self):
        with self.assertRaises(ValueError):
            self.runner.record_inference_cost(NOW, None, "local model")
        self.runner.record_inference_cost(NOW, .12, "local inference measured")
        self.assertEqual(self.desk.db.execute("SELECT usd FROM costs").fetchone()[0], .12)

    def test_cadence_blocks_early_repeat(self):
        self.runner.run_once(enabled=True)
        self.assertEqual(self.runner.run_once(enabled=True)["reason"], "cadence")

    def test_depth_is_rejected_conservatively_and_transport_injected(self):
        self.assertIs(self.runner.us_transport, None)
        self.assertEqual(self.runner.config.max_depth_levels, 1)

    def test_insufficient_top_ask_depth_blocks_fill_but_deeper_book_does_not_help(self):
        # PaperDesk's proposal is large relative to a deliberately tiny best-ask depth.
        bbo = lambda m: dict(venue=VENUE, slug=m["slug"], as_of=NOW, book={
            "marketData": {"bestBid": {"value": .39, "quantity": 1000},
                           "bestAsk": {"value": .40, "quantity": .25},
                           "offers": [{"px": {"value": .40}, "quantity": 10000}]}})
        self.runner.bbo_transport = bbo
        self.runner.run_once(enabled=True)
        rows = self.desk.db.execute("SELECT status,reason,shares FROM decisions ORDER BY id").fetchall()
        self.assertTrue(all(status == "BLOCKED" and shares == 0 and "depth" in reason.lower()
                            for status, reason, shares in rows))
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM fills").fetchone()[0], 0)

    def test_old_evidence_blocked_at_48_hours(self):
        old = forecast("m0")
        old["as_of"] = "2026-10-03T17:00:00+00:00"
        self.assertNotEqual(self.desk.evaluate(market("m0"), {
            "venue": VENUE, "slug": "m0", "as_of": NOW, "book": {
                "marketData": {"bestBid": {"value": .39}, "bestAsk": {"value": .40, "quantity": 100}}}},
            old, fee_per_share=.01, now=NOW, stop_dir=str(self.root))["status"], "PAPER_FILL")

    def test_cadence_persists_across_runner_instances(self):
        self.runner.run_once(enabled=True)
        another = PaperRunner(self.desk, market_transport=lambda n: self.fail("cadence ignored"),
            bbo_transport=self.runner.bbo_transport, inference_transport=self.runner.inference_transport,
            fee_per_share=.01, output_dir=self.root / "daily", clock=lambda: datetime.fromisoformat(NOW))
        self.addCleanup(another.close)
        self.assertEqual(another.run_once(enabled=True)["reason"], "cadence")

    def test_failed_unmeasured_attempt_blocks_remaining_candidates_and_is_durable(self):
        self.runner.inference_transport = lambda m: (_ for _ in ()).throw(RuntimeError("model down"))
        self.runner.run_once(enabled=True)
        rows = self.desk.db.execute("SELECT status,cost_usd,error FROM inference_attempts").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "FAILED")
        self.assertIsNone(rows[0][1])
        self.assertIn("model down", rows[0][2])
        report = json.loads((self.root / "daily" / "2026-10-05.json").read_text())
        self.assertIn("measurement", report["cycles"][0]["decisions"][0]["reason"])
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM fills").fetchone()[0], 0)
        self.runner.clock = lambda: datetime.fromisoformat("2026-10-05T17:16:00+00:00")
        self.runner.inference_transport = lambda m: self.fail("unknown cost must halt proposals")
        self.runner.run_once(enabled=True)
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM inference_attempts").fetchone()[0], 1)

    def test_failed_measured_attempt_retains_cost(self):
        self.runner.inference_transport = lambda m: {"cost_usd": .12, "error": "model rejected"}
        self.runner.run_once(enabled=True)
        row = self.desk.db.execute("SELECT status,cost_usd,error FROM inference_attempts LIMIT 1").fetchone()
        self.assertEqual(row, ("FAILED", .12, "model rejected"))
        self.assertEqual(self.desk.db.execute("SELECT usd FROM costs LIMIT 1").fetchone()[0], .12)

    def test_exception_with_known_estimated_cost_persists_in_runner_ledger(self):
        def infer(_market):
            error = ValueError("forecast validation rejected")
            setattr(error, "cost_usd", .12)
            setattr(error, "usage", {"prompt_tokens": 30, "completion_tokens": 20})
            raise error
        self.runner.inference_transport = infer
        self.runner.run_once(enabled=True)
        row = self.desk.db.execute("SELECT status,cost_usd,error FROM inference_attempts").fetchone()
        self.assertEqual(row, ("FAILED", .12, "forecast validation rejected"))
        self.assertEqual(self.desk.db.execute("SELECT usd FROM costs").fetchall(), [(.12,)] * 5)

    def test_transport_failure_is_persisted_and_retryable(self):
        self.runner.market_transport = lambda n: (_ for _ in ()).throw(RuntimeError("offline"))
        self.runner.run_once(enabled=True)
        self.assertIsNone(self.desk.db.execute("SELECT value FROM poly_guide_runner_state WHERE key='last_cycle'").fetchone())
        self.assertIn("offline", json.loads((self.root / "daily" / "2026-10-05.json").read_text())["cycles"][0]["decisions"][0]["reason"])
        self.runner.market_transport = lambda n: [market("retry")]
        self.assertEqual(self.runner.run_once(enabled=True)["processed"], 1)

    def test_old_evidence_direct_runner_regression(self):
        self.runner.market_transport = lambda n: [market("m0")]
        def infer(m):
            f = forecast("m0")
            f["as_of"] = "2026-10-03T16:59:59+00:00"
            return {"forecast": f, "cost_usd": .02}
        self.runner.inference_transport = infer
        self.runner.run_once(enabled=True)
        self.assertIn("stale", self.desk.db.execute("SELECT reason FROM decisions").fetchone()[0])

    def test_no_depth_cap_is_checked_before_fill(self):
        self.runner.market_transport = lambda n: [market("m0")]
        self.runner.inference_transport = lambda m: {"forecast": dict(forecast("m0"), p_yes=.1), "cost_usd": .01}
        self.runner.bbo_transport = lambda m: dict(venue=VENUE, slug="m0", as_of=NOW, book={"marketData": {
            "bestBid": {"value": .39, "quantity": .25}, "bestAsk": {"value": .4, "quantity": 100}}})
        self.runner.run_once(enabled=True)
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM fills").fetchone()[0], 0)
        self.assertIn("depth", self.desk.db.execute("SELECT reason FROM decisions").fetchone()[0])
        report = json.loads((self.root / "daily" / "2026-10-05.json").read_text())
        self.assertIn("depth", report["cycles"][0]["decisions"][0]["reason"])

    def test_multiple_daily_cycles_are_preserved(self):
        self.runner.market_transport = lambda n: [market("m0")]
        self.runner.run_once(enabled=True)
        self.runner.clock = lambda: datetime.fromisoformat("2026-10-05T17:16:00+00:00")
        self.runner.run_once(enabled=True)
        report = json.loads((self.root / "daily" / "2026-10-05.json").read_text())
        self.assertEqual(len(report["cycles"]), 2)


if __name__ == "__main__":
    unittest.main()
