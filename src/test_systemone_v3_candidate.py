"""Offline synthetic contract checks only; never touch Ollama or finance verifier."""
import unittest

import systemone_v3_candidate as v3


class V3CandidateTests(unittest.TestCase):
    def setUp(self):
        self.questions = {
            "admit": {"type": "noul", "instructions": "Is this admissible?", "criteria": {}},
            "venue": {"type": "choice", "instructions": "Pick a label", "criteria": {"crypto": "spot", "abstain": "unknown"}},
            "quality": {"type": "score", "instructions": "Grade quality", "criteria": ["none", "thin", "strong"]},
        }

    def test_rejects_key_order_and_missing_server_fields(self):
        import copy
        q = self.questions
        body = {"model": v3.TAG, "usage": {"input_tokens": 1, "output_tokens": 1}, "answers": {
            "admit": {"type": "noul", "noul": .5},
            "venue": {"type": "choice", "choice": "crypto", "confidence": .2,
                      "probabilities": {"crypto": .7, "abstain": .3}},
            "quality": {"type": "score", "score": 1.4, "confidence": .2,
                        "probabilities": {"0": .1, "1": .4, "2": .5},
                        "legend": {"0": "none", "1": "thin", "2": "strong"}},
        }}
        for edit in (lambda x: x["answers"]["venue"]["probabilities"].update({"crypto": float("nan")}),
                     lambda x: x["answers"]["venue"].pop("confidence"),
                     lambda x: x["answers"]["quality"].update({"probabilities": [0.1, .4, .5]}),
                     lambda x: x["answers"]["quality"].update({"legend": {"0": "none", "1": "wrong", "2": "strong"}}),
                     lambda x: x.pop("usage"),
                     lambda x: x["answers"]["venue"].update({"choice": "abstain"})):
            wrong = copy.deepcopy(body)
            edit(wrong)
            with self.assertRaises(v3.ContractError):
                v3.parse_response(wrong, q)

    def test_pin_requires_full_digest_and_min_version(self):
        row = {"models": [{"name": v3.TAG, "digest": v3.MANIFEST_SHA256}]}
        self.assertEqual(v3.pinned_tag(row, "0.40.4"), v3.TAG)
        self.assertEqual(v3.pinned_tag(row, "0.35.0"), "")
        self.assertEqual(v3.pinned_tag({"models": [{"name": v3.TAG, "digest": "24e550a16a70"}]}, "0.40.5"), "")
        self.assertEqual(v3.pinned_tag({"models": [{"name": "nimble:latest", "digest": v3.MANIFEST_SHA256}]}, "0.40.5"), "")
        self.assertFalse(v3.version_ok("0.40.4oops"))

    def test_request_is_explicit_and_no_order_fields(self):
        request = v3.make_request({"facts": {"edge_positive": True}}, self.questions, tag=v3.TAG)
        self.assertEqual(set(request), {"model", "state", "questions"})
        with self.assertRaises(v3.ContractError):
            v3.make_request("state", self.questions, tag="nimble:latest")

    def test_typed_response_without_fake_confidence(self):
        body = {"model": v3.TAG, "usage": {"input_tokens": 12, "output_tokens": 4}, "answers": {
            "admit": {"type": "noul", "noul": .8},
            "venue": {"type": "choice", "choice": "crypto", "confidence": .12, "probabilities": {"crypto": .7, "abstain": .3}},
            "quality": {"type": "score", "score": 1.4, "confidence": .2, "probabilities": {"0": .1, "1": .4, "2": .5}, "legend": {"0": "none", "1": "thin", "2": "strong"}},
        }}
        answers = v3.parse_answers(body, self.questions)
        self.assertEqual(answers["admit"]["noul"], .8)
        self.assertEqual(answers["quality"]["score"], 1.4)
        self.assertEqual(answers["venue"]["confidence"], .12)
        self.assertEqual(v3.parse_response(body, self.questions)["usage"]["input_tokens"], 12)
        for bad in (None, float("nan"), 1.2, True):
            body["answers"]["admit"]["noul"] = bad
            with self.assertRaises(v3.ContractError):
                v3.parse_answers(body, self.questions)

    def test_refuse_wrong_model_and_mismatched_options(self):
        body = {"model": "nimble:latest", "answers": {}}
        with self.assertRaises(v3.ContractError):
            v3.parse_answers(body, self.questions)
        body = {"model": v3.TAG, "answers": {"venue": {"choice": "crypto", "probabilities": {"crypto": 1}}}}
        with self.assertRaises(v3.ContractError):
            v3.parse_answers(body, {"venue": self.questions["venue"]})


if __name__ == "__main__":
    unittest.main()
