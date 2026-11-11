"""Offline contract tests for finance_evidence_review; synthetic packets only."""
import copy
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("finance_evidence_review", HERE / "finance_evidence_review.py")
mod = importlib.util.module_from_spec(spec)


def packet():
    return {
        "schema_version": 1, "venue": "polymarket_us", "market_id": "us-market-1",
        "decision_at": "2026-10-01T12:00:00Z", "deadline_at": "2026-10-01T12:00:03Z",
        "sources": [
            {"id": "a", "publisher": "one", "observed_at": "2026-10-01T11:59:00Z", "published_at": "2026-10-01T11:58:00Z", "captured_at": "2026-10-01T11:59:01Z", "sha256": "a"*64},
            {"id": "b", "publisher": "two", "observed_at": "2026-10-01T11:59:30Z", "published_at": "2026-10-01T11:59:00Z", "captured_at": "2026-10-01T11:59:31Z", "sha256": "b"*64},
        ],
        "timing": {"retrieval_started_at": "2026-10-01T11:59:40Z", "retrieval_finished_at": "2026-10-01T11:59:41Z", "model_started_at": "2026-10-01T11:59:42Z", "model_finished_at": "2026-10-01T11:59:43Z", "end_to_end_started_at": "2026-10-01T11:59:40Z", "end_to_end_finished_at": "2026-10-01T11:59:44Z"},
        "rules": {"decision": "abstain", "receipt": "rules-hash"},
        "model_observation": {"identifier": "unselected", "suggestion": "yes"},
        "intents": [{"id": "i1", "side": "yes", "quantity": 2, "price": 0.4, "venue": "polymarket_us", "market_id": "us-market-1", "status": "paper"}],
        "fills": [{"intent_id": "i1", "quantity": 2, "price": 0.4, "filled_at": "2026-10-01T12:00:01Z", "receipt": "fill-1"}],
        "costs": {"fee": 0.01, "slippage": 0.01, "inference": 0.0, "receipt": "cost-1"},
        "settlement": {"outcome": "yes", "settled_at": "2026-10-02T12:00:00Z", "receipt": "settlement-1", "venue": "polymarket_us", "market_id": "us-market-1"},
        "evaluation": {"registered_at": "2026-09-30T12:00:00Z", "holdout_ids": ["us-market-1"], "baseline": {"name": "always-no", "receipt": "baseline-1", "same_holdout": True}, "candidate": {"receipt": "candidate-1"}},
    }


def reasons(p):
    return mod.review(p)["reasons"]


class ReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec.loader.exec_module(mod)

    def test_valid_packet_is_evidence_complete_not_profitable_or_promoted(self):
        r = mod.review(packet())
        self.assertEqual(r["status"], "STRUCTURALLY_COMPLETE_UNVERIFIED")
        self.assertEqual(r["reasons"], [])
        self.assertFalse(r["promotion_authorized"])
        self.assertNotIn("profit", r)

    def test_future_publication_or_capture_refused(self):
        for field in ("published_at", "captured_at", "observed_at"):
            p = packet(); p["sources"][0][field] = "2026-10-01T12:01:00Z"
            self.assertIn("SOURCE_FUTURE", reasons(p))

    def test_stale_and_duplicate_or_single_publisher_refused(self):
        p = packet(); p["sources"][0]["observed_at"] = "2026-09-30T11:00:00Z"
        self.assertIn("SOURCE_STALE", reasons(p))
        p = packet(); p["sources"][1]["sha256"] = p["sources"][0]["sha256"]
        self.assertIn("SOURCE_DUPLICATE", reasons(p))
        p = packet(); p["sources"][1]["publisher"] = "one"
        self.assertIn("SOURCE_DIVERSITY", reasons(p))

    def test_missing_partial_and_duplicate_fills_refused(self):
        for fills in ([], [{**packet()["fills"][0], "quantity": 1}], packet()["fills"]*2):
            p = packet(); p["fills"] = fills
            self.assertIn("FILLS_UNRECONCILED", reasons(p))

    def test_missing_cost_and_settlement_receipts_refused(self):
        for field in ("fee", "slippage", "inference", "receipt"):
            p = packet(); p["costs"].pop(field)
            self.assertIn("COSTS_INSUFFICIENT", reasons(p))
        p = packet(); p["settlement"].pop("receipt")
        self.assertIn("SETTLEMENT_INSUFFICIENT", reasons(p))

    def test_late_model_decision_and_bad_timing_refused(self):
        p = packet(); p["timing"]["end_to_end_finished_at"] = "2026-10-01T12:00:04Z"
        self.assertIn("MISSED_DEADLINE", reasons(p))
        p = packet(); p["timing"]["model_finished_at"] = "2026-10-01T12:00:04Z"
        self.assertIn("MISSED_DEADLINE", reasons(p))
        p = packet(); p["timing"].pop("retrieval_started_at")
        self.assertIn("TIMING_INSUFFICIENT", reasons(p))

    def test_us_venue_mismatch_and_missing_venue_refused(self):
        for field in ("venue", "settlement"):
            p = packet()
            if field == "venue": p["venue"] = "polymarket_global"
            else: p["settlement"]["venue"] = "polymarket_global"
            self.assertIn("VENUE_MISMATCH", reasons(p))
        p = packet(); p["venue"] = ""
        self.assertIn("VENUE_MISMATCH", reasons(p))

    def test_holdout_must_be_registered_before_decision_and_compared(self):
        p = packet(); p["evaluation"]["registered_at"] = "2026-10-01T12:00:01Z"
        self.assertIn("HOLDOUT_INSUFFICIENT", reasons(p))
        p = packet(); p["evaluation"]["baseline"].pop("receipt")
        self.assertIn("BASELINE_INSUFFICIENT", reasons(p))
        p = packet(); p["evaluation"]["holdout_ids"] = ["another-market"]
        self.assertIn("HOLDOUT_INSUFFICIENT", reasons(p))

    def test_model_suggestion_never_overrides_rule_or_supplies_evidence(self):
        p = packet(); p["rules"].pop("receipt")
        self.assertIn("RULES_INSUFFICIENT", reasons(p))
        p = packet(); p["model_observation"] = {"identifier": "arbitrary", "suggestion": "buy", "confidence": 0.99}
        self.assertEqual(mod.review(p)["status"], "STRUCTURALLY_COMPLETE_UNVERIFIED")
        self.assertEqual(mod.review(p)["rules_decision"], "abstain")

    def test_capture_after_retrieval_finished_refused(self):
        p = packet(); p["sources"][1]["captured_at"] = "2026-10-01T11:59:42Z"
        self.assertIn("SOURCE_CHRONOLOGY", reasons(p))

    def test_settlement_before_fill_refused(self):
        p = packet(); p["settlement"]["settled_at"] = "2026-10-01T12:00:00.500000Z"
        self.assertIn("SETTLEMENT_INSUFFICIENT", reasons(p))

    def test_fill_conflicting_or_ambiguous_identity_refused(self):
        for fields in ({"venue": "polymarket_global", "market_id": "other"},
                       {"venue": "polymarket_us"}, {"market_id": "other"}):
            p = packet(); p["fills"][0].update(fields)
            self.assertIn("VENUE_MISMATCH", reasons(p))

    def test_nested_unhashable_fill_intent_id_is_structured_refusal(self):
        p = packet(); p["fills"][0]["intent_id"] = ["i1"]
        result = mod.review(p)
        self.assertEqual(result["status"], "INSUFFICIENT_EVIDENCE")
        self.assertIn("FILLS_UNRECONCILED", result["reasons"])

    def test_rules_deny_is_advisory_not_certified_fill_gate(self):
        p = packet(); p["rules"]["decision"] = "deny"
        result = mod.review(p)
        self.assertEqual(result["status"], "STRUCTURALLY_COMPLETE_UNVERIFIED")
        self.assertIs(result["rules_fill_gate_certified"], False)
        self.assertIs(result["promotion_authorized"], False)

    def test_cli_reads_one_file_emits_json_and_does_not_modify_packet(self):
        with tempfile.TemporaryDirectory() as td:
            source = Path(td)/"packet.json"; source.write_text(json.dumps(packet()))
            original = source.read_bytes()
            run = subprocess.run([sys.executable, str(HERE/"finance_evidence_review.py"), str(source)], capture_output=True, text=True, cwd=td)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(json.loads(run.stdout)["status"], "STRUCTURALLY_COMPLETE_UNVERIFIED")
            self.assertEqual(source.read_bytes(), original)

    def test_invalid_json_or_extra_execution_fields_fail_closed(self):
        p = packet(); p["order"] = {"send": True}
        self.assertEqual(mod.review(p)["status"], "INSUFFICIENT_EVIDENCE")
        with tempfile.TemporaryDirectory() as td:
            f = Path(td)/"bad.json"; f.write_text("{")
            run = subprocess.run([sys.executable, str(HERE/"finance_evidence_review.py"), str(f)], capture_output=True, text=True)
            self.assertNotEqual(run.returncode, 0)
            self.assertEqual(json.loads(run.stdout)["status"], "INSUFFICIENT_EVIDENCE")


if __name__ == "__main__":
    unittest.main()
