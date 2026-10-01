#!/usr/bin/env python3
"""DAVIDDTECH-SUBSUME-1 — paper desk + localhost webhook. No network. No orders.

Run: python3 test_daviddtech_paper.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import daviddtech_paper as desk
import daviddtech_webhook_server as srv


class DaviddPaperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False)
        self.tmp.close()
        self.path = self.tmp.name
        self.env = {
            "DAVIDDTECH_WEBHOOK_PASSPHRASE": "test-pass-not-real",
            "DAVIDD_LIVE": "0",
            "VALEDIA_TRADING_HALT": "0",
        }

    def tearDown(self):
        for p in (self.path, self.path + ".write.lock"):
            try:
                os.remove(p)
            except OSError:
                pass

    def _body(self, **over):
        b = desk.fixture_payload()
        b["passphrase"] = self.env["DAVIDDTECH_WEBHOOK_PASSPHRASE"]
        b.update(over)
        return b

    def test_valid_payload_create_order_false(self):
        body = self._body()
        ok, reason = desk.validate_payload(body)
        self.assertTrue(ok, reason)
        row = desk.to_decision(body, env=self.env)
        self.assertIs(row["create_order"], False)
        self.assertIs(row["live"], False)
        self.assertIs(row["filled"], False)
        self.assertIs(row["paperTrading"], True)
        self.assertIs(row["counts_toward_kelly"], False)
        self.assertEqual(row["mode"], "PAPER/SHADOW")
        self.assertEqual(row["source"], "daviddtech_paper")
        self.assertEqual(row["venue"], "daviddtech-paper")
        self.assertEqual(row["strategy"], "WADDAH_1H_ANCHOR")
        self.assertEqual(row["side"], "buy")
        self.assertEqual(row["band"], "allowlist")
        self.assertEqual(row["volume_tv"], 0.01)
        self.assertIsNone(row["size_usd"])
        desk.append_decision(row, path=self.path)
        lines = open(self.path).read()
        self.assertIn('"create_order": false', lines)
        self.assertNotIn(self.env["DAVIDDTECH_WEBHOOK_PASSPHRASE"], lines)
        self.assertNotIn("fixture-not-a-real-secret", lines)
        parsed = json.loads(lines.strip().splitlines()[-1])
        self.assertIs(parsed["create_order"], False)
        self.assertNotIn("passphrase", parsed)
        self.assertNotIn("passphrase", parsed.get("raw_payload") or {})
        self.assertNotIn('"passphrase":', lines.lower())

    def test_missing_required_fields_refused(self):
        body = self._body()
        del body["ticker"]
        ok, reason = desk.validate_payload(body)
        self.assertFalse(ok)
        self.assertEqual(reason, "missing_ticker")
        bad_action = self._body(action="market")
        ok2, reason2 = desk.validate_payload(bad_action)
        self.assertFalse(ok2)
        self.assertEqual(reason2, "bad_action")

    def test_passphrase_never_in_jsonl(self):
        secret = "super-secret-passphrase-xyz"
        body = self._body(passphrase=secret)
        row = desk.to_decision(body, env={**self.env, "DAVIDDTECH_WEBHOOK_PASSPHRASE": secret})
        row["passphrase"] = secret
        row["raw_payload"] = {**(row.get("raw_payload") or {}), "passphrase": secret}
        desk.append_decision(row, path=self.path)
        blob = open(self.path).read()
        self.assertNotIn(secret, blob)
        self.assertNotIn("[redacted]", blob)
        self.assertNotIn('"passphrase":', blob.lower())
        parsed = json.loads(blob.strip().splitlines()[-1])
        self.assertNotIn("passphrase", parsed)
        self.assertNotIn("passphrase", parsed.get("raw_payload") or {})

    def test_davidd_live_cannot_force_create_order(self):
        body = self._body()
        # even with live flag and stuffed fields
        body["create_order"] = True
        body["live"] = True
        env = {**self.env, "DAVIDD_LIVE": "1", "DAVIDDTECH_LIVE": "1"}
        row = desk.to_decision(body, env=env)
        self.assertIs(row["create_order"], False)
        self.assertIs(row["live"], False)
        desk.append_decision({**row, "create_order": True, "live": True}, path=self.path)
        parsed = json.loads(open(self.path).read().strip().splitlines()[-1])
        self.assertIs(parsed["create_order"], False)
        self.assertIs(parsed["live"], False)

    def test_halt_still_logs_shadow(self):
        body = self._body()
        env = {**self.env, "VALEDIA_TRADING_HALT": "1"}
        row = desk.to_decision(body, halt=True, env=env)
        self.assertEqual(row["band"], "review_only")
        self.assertIn("halt", row["reason"])
        self.assertIs(row["create_order"], False)
        code, resp = srv.handle_order(body, env=env, path=self.path)
        self.assertEqual(code, 200)
        self.assertIs(resp["create_order"], False)
        rows = desk.read_decisions(self.path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["band"], "review_only")
        self.assertIn("halt", rows[0]["reason"])
        self.assertIs(rows[0]["create_order"], False)

    def test_map_strategy(self):
        self.assertEqual(desk.map_strategy("Trendhoo_45M"), "TRENDHOO_45M")
        self.assertEqual(desk.map_strategy("TopG_5M_Scalp"), "TOPG_5M_SCALP")
        self.assertTrue(desk.map_strategy("OtherThing").startswith("DAV_UNKNOWN_"))

    def test_side_from_action(self):
        self.assertEqual(desk.side_from_action("exit"), "sell")
        self.assertEqual(desk.side_from_action("breakeven"), "flat")
        self.assertEqual(desk.side_from_action("entry", {"side": "short"}), "sell")

    def test_server_bad_passphrase_401(self):
        body = self._body(passphrase="wrong")
        code, resp = srv.handle_order(body, env=self.env, path=self.path)
        self.assertEqual(code, 401)
        self.assertIs(resp["create_order"], False)
        self.assertFalse(os.path.exists(self.path) and open(self.path).read().strip())

    def test_server_missing_passphrase_env_401(self):
        body = self._body()
        env = {"DAVIDD_LIVE": "0"}  # no passphrase configured
        code, resp = srv.handle_order(body, env=env, path=self.path)
        self.assertEqual(code, 401)

    def test_server_schema_422(self):
        body = self._body()
        del body["entry_price"]
        code, resp = srv.handle_order(body, env=self.env, path=self.path)
        self.assertEqual(code, 422)
        self.assertEqual(resp["error"], "missing_entry_price")

    def test_server_success_appends(self):
        body = self._body(strategy_id="TopG_5M_Scalp", action="tp1")
        code, resp = srv.handle_order(body, env=self.env, path=self.path)
        self.assertEqual(code, 200)
        self.assertIs(resp["create_order"], False)
        self.assertEqual(resp["decision"]["strategy"], "TOPG_5M_SCALP")
        self.assertEqual(resp["decision"]["side"], "sell")
        rows = desk.read_decisions(self.path)
        self.assertEqual(len(rows), 1)
        self.assertIs(rows[0]["create_order"], False)

    def test_health_payload(self):
        h = srv.health_payload()
        self.assertTrue(h["ok"])
        self.assertEqual(h["mode"], "PAPER/SHADOW")
        self.assertIs(h["create_order"], False)

    def test_fastapi_testclient(self):
        try:
            from fastapi.testclient import TestClient
        except ImportError:
            self.skipTest("fastapi TestClient unavailable")
        app = srv.build_fastapi_app(env=self.env, decision_path=self.path)
        client = TestClient(app)
        r = client.get("/health")
        self.assertEqual(r.status_code, 200)
        self.assertIs(r.json()["create_order"], False)
        bad = client.post("/tradingview-to-webhook-order", json=self._body(passphrase="nope"))
        self.assertEqual(bad.status_code, 401)
        good = client.post("/tradingview-to-webhook-order", json=self._body())
        self.assertEqual(good.status_code, 200)
        self.assertIs(good.json()["create_order"], False)
        blob = open(self.path).read()
        self.assertNotIn(self.env["DAVIDDTECH_WEBHOOK_PASSPHRASE"], blob)

    def test_alias_passphrase_env(self):
        body = self._body(passphrase="alias-secret")
        env = {"TRADINGVIEW_WEBHOOK_PASSPHRASE": "alias-secret", "DAVIDD_LIVE": "0"}
        code, resp = srv.handle_order(body, env=env, path=self.path)
        self.assertEqual(code, 200)
        self.assertIs(resp["create_order"], False)

    def test_bind_localhost_only(self):
        self.assertEqual(srv.HOST, "127.0.0.1")
        self.assertEqual(srv.bind_host(), "127.0.0.1")
        with self.assertRaises(ValueError):
            srv.bind_host("0.0.0.0")
        with self.assertRaises(ValueError):
            srv.bind_host("::")

    def test_no_ccxt_or_live_order_path(self):
        src = open(desk.__file__).read() + open(srv.__file__).read()
        self.assertNotIn("import ccxt", src)
        self.assertNotIn("from ccxt", src)
        self.assertNotIn("--live", src)

    def test_pine_stub_alert_json(self):
        root = os.path.abspath(os.path.join(HERE, "..", ".."))
        text = open(os.path.join(root, "recommendations", "DAVIDDTECH-PINE-PAPER-STUB.pine")).read()
        self.assertNotIn("http://", text)
        self.assertNotIn("https://", text)
        self.assertNotIn("cloudflare", text.lower())
        start = text.index('{"passphrase":"PASTE_PASSPHRASE_IN_TV_ALERT"')
        end = text.index("}'", start)
        raw = text[start:end + 1]
        raw = (raw.replace("{{timenow}}", "2026-09-28T03:20:00Z")
                  .replace("{{ticker}}", "BTCUSDT")
                  .replace("{{close}}", "65000.0")
                  .replace("{{strategy.order.contracts}}", "1"))
        body = json.loads(raw)
        self.assertEqual(body["strategy_id"], "Waddah_1H_Anchor")
        self.assertEqual(body["subaccount"], "paper")
        self.assertEqual(body["action"], "entry")
        self.assertNotIn("PASTE_PASSPHRASE_IN_TV_ALERT", json.dumps({k: v for k, v in body.items() if k != "passphrase"}))
        body["passphrase"] = self.env["DAVIDDTECH_WEBHOOK_PASSPHRASE"]
        ok, reason = desk.validate_payload(body)
        self.assertTrue(ok, reason)
        row = desk.to_decision(body, env=self.env)
        self.assertIs(row["create_order"], False)
        self.assertEqual(row["strategy"], "WADDAH_1H_ANCHOR")
        self.assertEqual(row["subaccount"], "paper")

    def _pine_entry(self, name, strategy_id, mapped, risk):
        root = os.path.abspath(os.path.join(HERE, "..", ".."))
        text = open(os.path.join(root, "recommendations", name)).read()
        self.assertNotIn("http://", text)
        self.assertNotIn("https://", text)
        self.assertNotIn("cloudflare", text.lower())
        self.assertNotIn("alert(", text.replace("f_alert(", "").replace("alert_message", ""))
        start = text.index('{"passphrase":"PASTE_PASSPHRASE_IN_TV_ALERT"')
        end = text.index("}'", start)
        raw = text[start:end + 1]
        raw = (raw.replace("{{timenow}}", "2026-09-28T03:20:00Z")
                  .replace("{{ticker}}", "BTCUSDT")
                  .replace("{{close}}", "65000.0")
                  .replace("{{strategy.order.contracts}}", "1"))
        body = json.loads(raw)
        self.assertEqual(body["strategy_id"], strategy_id)
        self.assertEqual(body["subaccount"], "paper")
        self.assertEqual(body["action"], "entry")
        self.assertAlmostEqual(body["portfolio_risk"], risk)
        body["passphrase"] = self.env["DAVIDDTECH_WEBHOOK_PASSPHRASE"]
        ok, reason = desk.validate_payload(body)
        self.assertTrue(ok, reason)
        row = desk.to_decision(body, env=self.env)
        self.assertIs(row["create_order"], False)
        self.assertEqual(row["strategy"], mapped)
        self.assertEqual(row["subaccount"], "paper")
        self.assertIsNone(row.get("passphrase"))

    def test_sister_pine_alert_json(self):
        self._pine_entry("DAVIDDTECH-PINE-TRENDHOO-45M-PAPER.pine", "Trendhoo_45M", "TRENDHOO_45M", 0.3)
        self._pine_entry("DAVIDDTECH-PINE-TOPG-5M-PAPER.pine", "TopG_5M_Scalp", "TOPG_5M_SCALP", 0.2)


def main():
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(DaviddPaperTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
