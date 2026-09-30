#!/usr/bin/env python3
"""TC auto-trading paper path. No network except 127.0.0.1 in the HTTP smoke. No orders."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import tc_auto_paper as desk
import tc_auto_webhook_server as srv


class TcAutoPaperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="tc-auto-test-")
        self.book = os.path.join(self.tmp, "book.json")
        self.env = {
            "VALEDIA_TV_WEBHOOK_PASSPHRASE": "test-pass-not-real",
            "VALEDIA_TV_LIVE": "0",
            "TC_AUTO_LIVE": "0",
            "VALEDIA_TRADING_HALT": "0",
        }

    def _body(self, **over):
        b = {
            "passphrase": self.env["VALEDIA_TV_WEBHOOK_PASSPHRASE"],
            "action": "buy",
            "ticker": "COINBASE:BTCUSD",
            "price": "83360.03",
            "strategy": "supertrend_paper",
        }
        b.update(over)
        return b

    def _files_text(self):
        blob = []
        for name in ("book.json", "decisions.jsonl", "fills.jsonl", "intents.jsonl"):
            path = os.path.join(self.tmp, name)
            if os.path.exists(path):
                blob.append(open(path).read())
        return "\n".join(blob)

    def test_flat_round_trip_fee_drag(self):
        code, buy = srv.handle_order(self._body(), env=self.env, book_path=self.book)
        self.assertEqual(code, 200)
        self.assertIs(buy["create_order"], False)
        self.assertIs(buy["live"], False)
        self.assertTrue(buy["filled"])
        self.assertEqual(buy["fill"]["fee_book"], "CE-3")
        mid = 83360.03
        slip = desk.fee_book("crypto")["slip_pct"]
        fee_pct = desk.fee_book("crypto")["fee_pct"]
        self.assertAlmostEqual(slip, 0.0005)
        self.assertAlmostEqual(fee_pct, 0.001)
        qty = buy["fill"]["qty"]
        self.assertAlmostEqual(buy["fill"]["px"], mid * (1.0 + slip), places=6)
        self.assertAlmostEqual(buy["fill"]["fee"], buy["fill"]["px"] * qty * fee_pct, places=6)
        self.assertLess(qty, 0.01)
        self.assertAlmostEqual(qty * mid, 504.0 * 0.10, places=2)
        code, sell = srv.handle_order(
            self._body(action="sell"), env=self.env, book_path=self.book,
        )
        self.assertEqual(code, 200)
        self.assertTrue(sell["filled"])
        self.assertAlmostEqual(sell["fill"]["px"], mid * (1.0 - slip), places=6)
        book = sell["book"]
        self.assertEqual(book["positions"], {})
        self.assertLess(book["realized_pnl"], 0)
        self.assertAlmostEqual(book["pnl_vs_bankroll"], book["realized_pnl"], places=6)
        self.assertAlmostEqual(book["cash"], 504.0 + book["realized_pnl"], places=6)
        self.assertGreater(book["fees_paid"], 0)
        self.assertIs(book["create_order"], False)
        self.assertIs(book["counts_toward_kelly"], False)
        text = self._files_text()
        self.assertNotIn(self.env["VALEDIA_TV_WEBHOOK_PASSPHRASE"], text)
        self.assertNotIn('"passphrase":', text.lower())
        self.assertIn('"create_order": false', text)

    def test_equity_alpaca_zero_commission(self):
        code, buy = srv.handle_order(
            self._body(ticker="AAPL", price="329.4"), env=self.env, book_path=self.book,
        )
        self.assertEqual(code, 200, buy)
        self.assertEqual(buy["fill"]["fee_book"], "alpaca_static")
        self.assertEqual(buy["fill"]["fee"], 0.0)
        self.assertAlmostEqual(buy["fill"]["px"], 329.4 * 1.0001, places=6)
        srv.handle_order(
            self._body(action="sell", ticker="AAPL", price="329.4"),
            env=self.env, book_path=self.book,
        )
        book = desk.load_book(self.book)
        self.assertLess(book["realized_pnl"], 0)
        self.assertEqual(book["positions"], {})

    def test_contracts_cannot_exceed_clip(self):
        code, buy = srv.handle_order(
            self._body(contracts="100"), env=self.env, book_path=self.book,
        )
        self.assertEqual(code, 200)
        self.assertLess(buy["fill"]["qty"], 1.0)
        self.assertAlmostEqual(buy["fill"]["qty"] * 83360.03, 50.4, places=2)

    def test_secrets_stripped_and_auth(self):
        body = self._body(api_token="super-secret-token")
        code, resp = srv.handle_order(body, env=self.env, book_path=self.book)
        self.assertEqual(code, 200)
        self.assertTrue(resp["decision"]["token_stripped"])
        text = self._files_text()
        self.assertNotIn("super-secret-token", text)
        self.assertNotIn("api_token", text.lower())
        code, resp = srv.handle_order(self._body(passphrase="nope"), env=self.env, book_path=self.book)
        self.assertEqual(code, 401)
        code, resp = srv.handle_order(self._body(), env={"VALEDIA_TRADING_HALT": "0"}, book_path=self.book)
        self.assertEqual(code, 401)

    def test_refuses(self):
        code, resp = srv.handle_order(self._body(ticker="USO", price="70"), env=self.env, book_path=self.book)
        self.assertEqual(code, 422)
        self.assertEqual(resp["error"], "oil_no_harvest")
        self.assertEqual(desk.load_book(self.book)["cash"], 504.0)
        code, resp = srv.handle_order(self._body(action="short"), env=self.env, book_path=self.book)
        self.assertEqual(resp["error"], "no_short")
        code, resp = srv.handle_order(
            self._body(strategy="random_community_script"), env=self.env, book_path=self.book,
        )
        self.assertEqual(resp["error"], "unknown_strategy")
        curly = self._body(action="buy\u201d")
        code, resp = srv.handle_order(curly, env=self.env, book_path=self.book)
        self.assertEqual(resp["error"], "curly_quotes")
        code, resp = srv.handle_order(self._body(action="sell"), env=self.env, book_path=self.book)
        self.assertEqual(code, 200)
        self.assertFalse(resp["filled"])
        self.assertEqual(resp["decision"]["reason"], "no_position_no_short")
        self.assertEqual(desk.load_book(self.book)["cash"], 504.0)

    def test_live_flag_and_halt_cannot_order(self):
        env = dict(self.env)
        env["VALEDIA_TV_LIVE"] = "1"
        env["TC_AUTO_LIVE"] = "1"
        code, resp = srv.handle_order(self._body(create_order=True, live=True), env=env, book_path=self.book)
        self.assertEqual(code, 200)
        self.assertTrue(resp["filled"])
        self.assertIs(resp["create_order"], False)
        self.assertIs(resp["live"], False)
        self.assertIs(resp["intent"]["dispatchable"], False)
        self.assertIn("live_flag_ignored", resp["decision"]["reason"])
        text = self._files_text()
        self.assertNotIn('"create_order": true', text)
        self.assertNotIn('"dispatchable": true', text)
        env["VALEDIA_TRADING_HALT"] = "1"
        code, resp = srv.handle_order(self._body(ticker="AAPL", price="329.4"), env=env, book_path=self.book)
        self.assertEqual(code, 200)
        self.assertFalse(resp["filled"])
        self.assertEqual(resp["decision"]["reason"], "trading_halt_shadow")

    def test_health_and_bind(self):
        h = srv.health_payload()
        self.assertIs(h["create_order"], False)
        self.assertEqual(h["bankroll"], 504.0)
        self.assertFalse(h["ngrok"])
        self.assertEqual(srv.bind_host(), "127.0.0.1")
        with self.assertRaises(ValueError):
            srv.bind_host("0.0.0.0")
        src = open(os.path.join(HERE, "tc_auto_paper.py")).read()
        srv_src = open(os.path.join(HERE, "tc_auto_webhook_server.py")).read()
        for blob in (src, srv_src):
            self.assertNotIn("ib_insync", blob)
            self.assertNotIn("place_order", blob)
            self.assertNotIn("import coinbase_trader", blob)
            self.assertNotIn("import alpaca_executor", blob)
            self.assertNotIn("ngrok http", blob)
        self.assertNotIn("ngrok", src.lower())
        self.assertIn("Leave it off", srv_src)

    def test_http_smoke_localhost(self):
        out_json = os.path.join(self.tmp, "smoke.json")
        out_md = os.path.join(self.tmp, "smoke.md")
        doc = srv.run_http_smoke(out_json, out_md)
        self.assertEqual(doc["book"]["fills"], 4)
        self.assertEqual(doc["book"]["positions"], {})
        self.assertLess(doc["book"]["realized_pnl"], 0)
        self.assertIs(doc["create_order"], False)
        self.assertFalse(doc["ngrok"])
        self.assertEqual(doc["health"]["route"], "/valedia-tv-alert")
        text = open(out_json).read() + open(out_md).read()
        self.assertNotIn("smoke-not-a-secret", text)
        books = {f["fee_book"] for f in doc["fills"]}
        self.assertEqual(books, {"CE-3", "alpaca_static"})


if __name__ == "__main__":
    unittest.main()
