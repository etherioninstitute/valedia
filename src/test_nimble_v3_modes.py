"""Offline v3 HTTP-contract and caller-mode regressions. Run: python3 test_nimble_v3_modes.py."""
import copy
import os
import tempfile
import unittest
from unittest.mock import patch

os.environ["OPENCLAW_SKIP_ENV"] = "1"
import nimble_decide as nd
import systemone_hotpath_client as client
import systemone_v3_candidate as v3


class V3Modes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {
            "VALEDIA_SYSTEMONE": "1", "VALEDIA_SYSTEMONE_MODEL": v3.TAG,
            "VALEDIA_SYSTEMONE_LOG": os.path.join(self.tmp.name, "log.jsonl"),
        })
        env.start()
        self.addCleanup(env.stop)
        client._reset_digest()
        nd._WARMED = True
        phi = patch.object(nd, "phi_multiplier", return_value=(1.0, "mock"))
        phi.start()
        self.addCleanup(phi.stop)
        self.posts = []

    def get(self, url, timeout):
        if url.endswith("/api/version"):
            return {"version": "0.40.5"}
        if url.endswith("/api/tags"):
            return {"models": [{"name": v3.TAG, "digest": v3.MANIFEST_SHA256}]}
        raise AssertionError(url)

    def response(self, payload, *, confidence=.2):
        qs = payload["questions"]
        answers = {}
        for key, q in qs.items():
            if q["type"] == "noul":
                answers[key] = {"type": "noul", "noul": .8}
            elif q["type"] == "choice":
                keys = list(q["criteria"])
                chosen = "buy" if "buy" in keys else keys[0]
                rest = (1 - .8) / (len(keys) - 1)
                probs = {k: (.8 if k == chosen else rest) for k in keys}
                answers[key] = {"type": "choice", "choice": chosen,
                                "probabilities": probs, "confidence": confidence}
            else:
                raise AssertionError("unsupported fixture question")
        return {"model": v3.TAG, "answers": answers,
                "usage": {"input_tokens": 10, "output_tokens": 2}}

    def post(self, url, payload, timeout):
        self.posts.append((url, copy.deepcopy(payload), timeout))
        return self.response(payload)

    def decide(self, mode, side="buy", post=None):
        os.environ["VALEDIA_NIMBLE_MODE"] = mode
        return nd.decide_candidate("stock", {"symbol": "SYNTHETIC", "venue": "paper"}, side=side,
                                   code_facts={"filter_passed": True}, informed={},
                                   source="v3-mock", post=post or self.post, get=self.get)

    def test_client_typed_answer_usage_and_separate_confidence(self):
        result = client.ask({"symbol": "SYNTHETIC"}, nd.QUESTIONS["stock"],
                            source="v3-mock", post=self.post, get=self.get, parse=nd._parse)
        self.assertIsNone(result["error"], result)
        self.assertEqual(result["usage"], {"input_tokens": 10, "output_tokens": 2})
        self.assertEqual(result["typed_answers"]["action"]["confidence"], .2)
        self.assertEqual(result["answers"]["p_buy"], .8)
        self.assertEqual(result["answers"]["confidence"], .2)
        self.assertEqual(self.posts[0][0], client.DECIDE_URL)
        self.assertEqual(self.posts[0][1]["model"], v3.TAG)

    def test_off_exit_shadow_and_veto_malformed(self):
        self.assertEqual(self.decide("off")["reason"], "mode_off")
        self.assertEqual(self.posts, [])
        for mode in ("off", "shadow", "veto"):
            result = self.decide(mode, side="sell")
            self.assertTrue(result["allow"])
            self.assertEqual(result["reason"], "exit_never_blocked")
        self.assertEqual(self.posts, [])
        def malformed(url, payload, timeout):
            self.posts.append((url, payload, timeout))
            body = self.response(payload)
            body["answers"]["action"].pop("confidence")
            return body
        shadow = self.decide("shadow", post=malformed)
        self.assertTrue(shadow["allow"])
        self.assertEqual(shadow["reason"], "shadow_error")
        self.assertEqual(shadow["error"], "ContractError")
        veto = self.decide("veto", post=malformed)
        self.assertFalse(veto["allow"])
        self.assertEqual(veto["reason"], "veto_engine_error")
        self.assertEqual(veto["error"], "ContractError")

    def test_veto_does_not_replace_server_sharpness_with_probability(self):
        v = self.decide("veto")
        self.assertEqual(v["confidence"], .2)
        self.assertEqual(v["p_buy"], .8)
        self.assertNotEqual(v["confidence"], v["p_buy"])
        self.assertIn(v["reason"], {"veto_model_buy", "veto_p_below_required", "veto_no_p_buy"})
        self.assertEqual(len(self.posts), 1)


if __name__ == "__main__":
    unittest.main()
