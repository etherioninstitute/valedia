"""Offline US-public bridge tests: synthetic fixtures are NOT market evidence."""
import copy
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from poly_guide_paper import PaperDesk
from poly_guide_runner import PaperRunner
from poly_guide_us_public import PublicUSAdapter, parse_book, parse_market

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def market():
    return {"slug": "fixture-us", "active": True, "closed": False, "archived": False,
            "orderPriceMinTickSize": .001, "minimumTradeQty": "1",
            "marketSides": [{"long": True, "tradable": True},
                            {"long": False, "tradable": True}],
            "description": "Unverified market description"}


def verified():
    return {"market_slug": "fixture-us", "rules_verified": True,
            "rules_text": "Independently verified fixture rules",
            "resolves_at": (NOW + timedelta(days=7)).isoformat(), "volume_usd": 50001}


def book():
    return {"marketData": {"marketSlug": "fixture-us", "state": "MARKET_STATE_OPEN",
            "transactTime": (NOW - timedelta(seconds=1)).isoformat(),
            "bids": [{"px": {"value": .39, "currency": "USD"}, "qty": "100"}],
            "offers": [{"px": {"value": .40, "currency": "USD"}, "qty": "50"}]}}


class USPublicTests(unittest.TestCase):
    def test_market_rejects_unverified_rules_closed_or_nonbinary(self):
        self.assertEqual(parse_market(market(), verified())["venue"], "polymarket_us")
        for mutation in (lambda m: m.update(closed=True),
                         lambda m: m.update(marketSides=[m["marketSides"][0]]),
                         lambda m: m.update(minimumTradeQty=None),
                         lambda m: m.update(orderPriceMinTickSize=0),
                         lambda m: m.update(marketSides=[{"long": True, "tradable": True}] * 2)):
            row = market(); mutation(row)
            with self.assertRaises(ValueError): parse_market(row, verified())
        with self.assertRaises(ValueError): parse_market(market(), {})
        bad = verified(); bad["rules_verified"] = False
        with self.assertRaises(ValueError): parse_market(market(), bad)
        bad = verified(); bad["resolves_at"] = "2020-01-01T00:00:00Z"
        with self.assertRaises(ValueError): parse_market(market(), bad)

    def test_book_strict_depth_currency_state_and_timestamp(self):
        result = parse_book(book(), "fixture-us", received_at=NOW.isoformat())
        self.assertEqual(result["book"]["marketData"]["bestAsk"]["quantity"], 50)
        self.assertEqual(result["as_of"], (NOW - timedelta(seconds=1)).isoformat())
        for change in (lambda b: b["marketData"].update(transactTime="2020-01-01T00:00:00Z"),
                       lambda b: b["marketData"].update(transactTime=(NOW + timedelta(seconds=1)).isoformat()),
                       lambda b: b["marketData"].update(state="MARKET_STATE_CLOSED"),
                       lambda b: b["marketData"]["offers"][0]["px"].update(currency="EUR"),
                       lambda b: b["marketData"]["offers"][0].update(qty="NaN"),
                       lambda b: b["marketData"]["offers"][0]["px"].update(value=.38),
                       lambda b: b["marketData"].update(transactTime=None)):
            bad = copy.deepcopy(book()); change(bad)
            with self.assertRaises(ValueError): parse_book(bad, "fixture-us", received_at=NOW.isoformat())

    def test_adapter_no_metadata_no_network_and_get_only(self):
        adapter = PublicUSAdapter(fetch=lambda url: self.fail("no network without rules"), clock=lambda: NOW)
        self.assertEqual(adapter.markets(5), [])
        with self.assertRaises(ValueError): adapter._get("/v1/orders")
        urls = []
        def fetch(url):
            urls.append(url)
            if url.endswith("/book"): return book()
            return {"markets": [market()]}
        adapter = PublicUSAdapter(fetch=fetch, verified_metadata=lambda m: verified(), clock=lambda: NOW)
        candidates = adapter.markets(5)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(adapter.bbo(candidates[0])["slug"], "fixture-us")
        self.assertEqual(len(urls), 2)
        self.assertTrue(all(u.startswith("https://gateway.polymarket.us/v1/markets") for u in urls))
        with self.assertRaises(ValueError): adapter.markets(6)
        with self.assertRaises(ValueError): adapter.bbo({"venue": "international", "slug": "fixture-us"})
    def test_runner_adapter_blocks_before_unmeasured_inference(self):
        with tempfile.TemporaryDirectory() as directory:
            desk = PaperDesk(Path(directory) / "paper.sqlite", 1000)
            adapter = PublicUSAdapter(fetch=lambda url: {"markets": [market()]},
                                      verified_metadata=lambda m: verified(), clock=lambda: NOW)
            runner = PaperRunner(desk, us_transport=adapter,
                                 inference_transport=lambda m: self.fail("fee missing: do not infer"),
                                 output_dir=str(Path(directory) / "reports"), clock=lambda: NOW)
            try:
                result = runner.run_once(enabled=True)
                self.assertEqual(result["processed"], 1)
                self.assertEqual(desk.db.execute("SELECT COUNT(*) FROM inference_attempts").fetchone()[0], 0)
                self.assertEqual(desk.db.execute("SELECT COUNT(*) FROM fills").fetchone()[0], 0)
            finally:
                runner.close(); desk.close()


if __name__ == "__main__": unittest.main()
