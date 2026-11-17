"""Offline typed-contract and caller-mode regressions on the shared Elephant-1 instance.

Named for the Nimble v3 typed contract it first covered. The typed call goes through the client's own
transport (replaced here); an injected post is never used. Run: python3 test_nimble_v3_modes.py.
"""
import copy
import os
import tempfile
import unittest
from unittest.mock import patch

os.environ["OPENCLAW_SKIP_ENV"] = "1"
import calibration_ledger as cl  # noqa: E402
import nimble_decide as nd  # noqa: E402
import systemone_hotpath_client as client  # noqa: E402
import systemone_test_fakes as fk  # noqa: E402

DM = client._pin.dm


class TypedModes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, {
            "VALEDIA_SYSTEMONE": "1", "VALEDIA_SYSTEMONE_MODEL": DM.INSTANCE,
            "VALEDIA_SYSTEMONE_LOG": os.path.join(self.tmp.name, "log.jsonl"),
        })
        env.start()
        self.addCleanup(env.stop)
        client._reset_check()
        self.addCleanup(client._reset_check)
        cl._reset_cache(items=[])
        self.addCleanup(cl._reset_cache)
        phi = patch.object(nd, "phi_multiplier", return_value=(1.0, "mock"))
        phi.start()
        self.addCleanup(phi.stop)
        self.posts = []
        transport = patch.object(client, "_post", self.post)
        transport.start()
        self.addCleanup(transport.stop)
        self.get = fk.list_get()

    def response(self, payload, *, confidence=.2):
        return fk.typed_reply(payload["questions"], {"action": "buy"}, confidence=confidence, p=.8)

    def post(self, url, payload, timeout):
        self.posts.append((url, copy.deepcopy(payload), timeout))
        return self.response(payload)

    def injected(self, url, payload, timeout):
        raise AssertionError("an injected post was used")

    def decide(self, mode, side="buy"):
        os.environ["VALEDIA_NIMBLE_MODE"] = mode
        return nd.decide_candidate("stock", {"symbol": "SYNTHETIC", "venue": "paper"}, side=side,
                                   code_facts={"filter_passed": True}, informed={},
                                   source="typed-mock", post=self.injected, get=self.get)

    def test_client_typed_answer_usage_and_separate_confidence(self):
        result = client.ask({"symbol": "SYNTHETIC"}, nd.QUESTIONS["stock"],
                            source="typed-mock", post=self.injected, get=self.get, parse=nd._parse)
        self.assertIsNone(result["error"], result)
        self.assertEqual(result["usage"], {"input_tokens": 10, "output_tokens": 2})
        self.assertEqual(result["typed_answers"]["action"]["confidence"], .2)
        self.assertEqual(result["answers"]["p_buy"], .8)
        self.assertEqual(result["answers"]["confidence"], .2)
        self.assertEqual(self.posts[0][0], client.DECIDE_URL)
        self.assertEqual(self.posts[0][1]["model"], DM.INSTANCE)
        self.assertNotIn("keep_alive", self.posts[0][1])

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
        with patch.object(client, "_post", malformed):
            shadow = self.decide("shadow")
            self.assertTrue(shadow["allow"])
            self.assertEqual(shadow["reason"], "shadow_error")
            self.assertEqual(shadow["error"], "ContractError")
            veto = self.decide("veto")
            self.assertFalse(veto["allow"])
            self.assertEqual(veto["reason"], "veto_engine_error")
            self.assertEqual(veto["error"], "ContractError")

    def test_veto_does_not_replace_server_sharpness_with_probability(self):
        v = self.decide("veto")
        self.assertEqual(v["confidence"], .2)
        self.assertEqual(v["p_buy"], .8)
        self.assertNotEqual(v["confidence"], v["p_buy"])
        self.assertEqual(v["reason"], "veto_no_p_buy")          # held: no map under its own key
        self.assertEqual(v["calibration"], "untrained_hold")
        self.assertIsNone(v["p_cal"])
        self.assertEqual(len(self.posts), 1)

    def test_calibration_error_path_is_unavailable(self):
        with patch.object(cl, "calibrate_ex", side_effect=RuntimeError("x")):
            v = self.decide("veto")
        self.assertEqual(v["calibration"], "unavailable")
        self.assertIsNone(v["p_cal"])
        self.assertFalse(v["allow"])
        self.assertEqual(v["reason"], "veto_no_p_buy")

    def test_no_warmup_request(self):
        nd._WARMED = False
        client._WARM = False
        self.assertIsNone(nd.warm(post=self.injected, get=self.get))
        self.decide("shadow")
        self.decide("shadow")
        self.assertEqual(len(self.posts), 2)
        self.assertTrue(all(p[1]["state"].get("symbol") == "SYNTHETIC" for p in self.posts))


if __name__ == "__main__":
    unittest.main()
