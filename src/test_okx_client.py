#!/usr/bin/env python3
"""Offline OKX fence. No network, no real keys."""
import json
import os
import stat
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Unit tests assert git-default OFF without Burton's ~/.openclaw/.env flips.
os.environ["OPENCLAW_SKIP_ENV"] = "1"

import okx_client as okx
import okx_materialize_keys as mat
import valedia_config as vc

FAKE = {"api_key": "k-test", "secret": "s-test", "passphrase": "p-test"}


def _env(updates):
    old = {k: os.environ.get(k) for k in updates}
    for k, v in updates.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    return old


def _restore(old):
    for k, v in old.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


class OKXFenceTests(unittest.TestCase):
    def test_live_off_does_not_order(self):
        calls = []
        client = okx.OKXClient(keys=FAKE, transport=lambda *a: calls.append(a) or (200, "{}"))
        old = _env({"OKX_LIVE": "0"})
        try:
            with self.assertRaises(okx.OKXLiveRefused):
                client.create_order("BTC-USDT", "buy", "1", px="1")
            with self.assertRaises(okx.OKXLiveRefused):
                client.cancel_order("BTC-USDT", "1")
        finally:
            _restore(old)
        self.assertEqual(calls, [])
        self.assertNotIn("k-test", str(client))
        self.assertNotIn("s-test", repr(client))
        self.assertNotIn("p-test", repr(client))

    def test_unset_live_does_not_order(self):
        calls = []
        client = okx.OKXClient(keys=FAKE, transport=lambda *a: calls.append(a) or (200, "{}"))
        old = _env({"OKX_LIVE": None})
        try:
            with self.assertRaises(okx.OKXLiveRefused):
                client.create_order("BTC-USDT", "buy", "1", px="1")
        finally:
            _restore(old)
        self.assertEqual(calls, [])

    def test_halt_blocks_even_if_live(self):
        calls = []
        client = okx.OKXClient(keys=FAKE, transport=lambda *a: calls.append(a) or (200, "{}"))
        old = _env({"OKX_LIVE": "1", "VALEDIA_TRADING_HALT": "1"})
        try:
            with self.assertRaises(vc.HaltedError):
                client.create_order("BTC-USDT", "buy", "1", px="1")
            with self.assertRaises(vc.HaltedError):
                client.cancel_order("BTC-USDT", "9")
        finally:
            _restore(old)
        self.assertEqual(calls, [])

    def test_live_and_clear_reaches_transport_not_withdraw(self):
        seen = {}

        def transport(method, url, headers, raw):
            seen["method"] = method
            seen["url"] = url
            seen["raw"] = raw
            self.assertNotIn("withdraw", url)
            self.assertNotIn("transfer", url)
            return 200, '{"code":"0","data":[{"ordId":"abc"}]}'

        client = okx.OKXClient(keys=dict(FAKE), transport=transport)
        old = _env({"OKX_LIVE": "1", "VALEDIA_TRADING_HALT": "0"})
        try:
            out = client.create_order("BTC-USDT", "buy", "0.01", px="1")
        finally:
            _restore(old)
        self.assertEqual(seen["method"], "POST")
        self.assertTrue(seen["url"].endswith("/api/v5/trade/order"))
        self.assertNotIn("withdraw", seen["raw"])
        self.assertEqual(out[0]["ordId"], "abc")
        self.assertNotIn("s-test", json.dumps(out))

    def test_sign_matches_independent_hmac(self):
        import base64
        import hashlib
        import hmac
        secret, ts, method, path, body = "s-test", "2020-12-08T09:08:57.715Z", "GET", "/api/v5/account/balance", ""
        msg = (ts + method + path + body).encode()
        expect = base64.b64encode(hmac.new(secret.encode(), msg, hashlib.sha256).digest()).decode()
        self.assertEqual(okx._sign(secret, ts, method, path, body), expect)

    def test_cli_live_flag_exits(self):
        self.assertEqual(okx.main(["--live"]), 2)
        self.assertEqual(okx.main(["summary", "--live"]), 2)
        self.assertEqual(okx.main(["summary", "create_order"]), 2)

    def test_materializer_hides_secrets(self):
        secret = "super-secret-value"
        with tempfile.TemporaryDirectory() as td:
            paths = []
            for name, text in (("k", "key-value-1"), ("s", secret), ("p", "phrase value")):
                path = os.path.join(td, name)
                with open(path, "w") as f:
                    f.write(text + "\n")
                paths.append(path)
            dest = os.path.join(td, "okx_keys.json")
            buf = []
            old = sys.stdout
            class _B:
                def write(self, s):
                    buf.append(s)
                def flush(self):
                    pass
            sys.stdout = _B()
            try:
                rc = mat.main(paths + [dest])
            finally:
                sys.stdout = old
            out = "".join(buf)
            self.assertEqual(rc, 0)
            self.assertIn("ok=1", out)
            self.assertIn("mode=600", out)
            self.assertNotIn(secret, out)
            self.assertNotIn("key-value-1", out)
            mode = stat.S_IMODE(os.stat(dest).st_mode)
            self.assertEqual(mode, 0o600)
            with open(dest) as f:
                doc = json.load(f)
            self.assertEqual(doc["secret"], secret)
            self.assertEqual(doc["passphrase"], "phrase value")


if __name__ == "__main__":
    unittest.main()
