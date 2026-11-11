"""Offline, fixture-only tests; no accounts, network, or live writes."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from poly_guide_paper import PaperDesk, VENUE

NOW = "2026-10-05T17:00:00+00:00"


def fixture(slug="fixture-a", p=.85, bid=.39, ask=.40):
    market = dict(venue=VENUE, schema="us_binary_v1", slug=slug,
                  tradeable=True, rules_text="YES if the published count exceeds threshold",
                  rules_digest="sha256:fixture-only", resolves_at="2026-10-10T00:00:00Z",
                  volume_usd=50001, tick=.01, min_shares=1)
    bbo = dict(venue=VENUE, slug=slug, as_of=NOW,
               book=dict(marketData=dict(bestBid={"value": bid}, bestAsk={"value": ask})))
    forecast = dict(venue=VENUE, market_slug=slug, p_yes=p, confidence="high",
                    model_id="fixture-local-model", as_of=NOW,
                    rules_digest=market["rules_digest"], could_be_wrong="Evidence may lag",
                    evidence=[{"claim": f"source {i}", "url": f"https://example.org/{i}"}
                              for i in range(3)])
    return market, bbo, forecast


class PaperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.desk = PaperDesk(self.root / "paper.sqlite", 1000)

    def tearDown(self):
        self.desk.close()
        self.tmp.cleanup()

    def run_case(self, slug="fixture-a", fee=.01, **changes):
        market, bbo, forecast = fixture(slug)
        for key, value in changes.items():
            if key in market:
                market[key] = value
            elif key in bbo:
                bbo[key] = value
            else:
                forecast[key] = value
        return self.desk.evaluate(market, bbo, forecast, fee_per_share=fee,
                                  now=NOW, stop_dir=self.root)

    def test_paper_yes_fee_aware_quarter_kelly_and_cap(self):
        result = self.run_case()
        # Daily $20 risk budget binds before the $25 per-position cap.
        self.assertEqual(result["status"], "PAPER_FILL")
        self.assertEqual(result["shares"], 48)
        self.assertEqual(result["limit_price"], .4)
        rows = self.desk.db.execute("SELECT kind,fee FROM fills").fetchall()
        self.assertEqual(rows, [("paper_entry", .48)])
        self.assertAlmostEqual(self.desk.settle("fixture-a", NOW, 1), 28.32)
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM pnl").fetchone()[0], 1)
        with self.assertRaises(ValueError):
            self.desk.settle("fixture-a", NOW, 1)

    def test_no_side_uses_implied_executable_ask(self):
        m, b, f = fixture(p=.1)
        r = self.desk.evaluate(m, b, f, fee_per_share=.01, now=NOW, stop_dir=self.root)
        self.assertEqual(r["side"], "NO")
        self.assertAlmostEqual(r["limit_price"], .61)
        self.assertEqual(r["status"], "PAPER_FILL")

    def test_blocks_forecast_venue_evidence_fees_and_stale_quote(self):
        self.assertEqual(self.run_case(fee=0)["status"], "BLOCKED")
        self.assertEqual(self.run_case(venue="international")["status"], "BLOCKED")
        self.assertEqual(self.run_case(evidence=[])["status"], "BLOCKED")
        self.assertEqual(self.run_case(as_of="2026-10-05T15:00:00Z")["status"], "BLOCKED")
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM fills").fetchone()[0], 0)

    def test_stop_only_this_desk_and_5_positions(self):
        (self.root / "STOP").touch()
        self.assertIn("STOP", self.run_case()["reason"])
        (self.root / "STOP").unlink()
        for i in range(5):
            self.desk.db.execute("""INSERT INTO decisions(at,strategy,venue,slug,status,reason)
                VALUES(?,?,?,?,?,?)""", (NOW, "poly_guide_paper", VENUE, f"m{i}", "PAPER_FILL", "fixture"))
            did = self.desk.db.execute("SELECT last_insert_rowid()").fetchone()[0]
            self.desk.db.execute("""INSERT INTO fills
                (decision_id,at,venue,slug,side,shares,price,fee,kind)
                VALUES(?,?,?,?,?,?,?,?,?)""", (did, "2026-10-04T17:00:00Z", VENUE,
                                            f"m{i}", "YES", 1, .40, .01, "paper_entry"))
        self.assertIn("position limit", self.run_case(slug="sixth")["reason"])

    def test_daily_loss_stop_and_weekly_latch(self):
        self.desk.db.execute("INSERT INTO pnl(at,strategy,venue,slug,net_usd) VALUES(?,?,?,?,?)",
                             (NOW, "poly_guide_paper", VENUE, "prior", -20))
        self.assertIn("loss stop", self.run_case()["reason"])
        self.desk.db.execute("DELETE FROM pnl")
        self.desk.record_cost(NOW, 2, "local inference estimated")
        monday = "2026-10-12T17:00:00Z"
        m, b, f = fixture()
        m["resolves_at"] = "2026-10-20T00:00:00Z"
        b["as_of"] = monday
        f["as_of"] = monday
        halt = self.desk.evaluate(m, b, f, fee_per_share=.01, now=monday, stop_dir=self.root)
        self.assertIn("weekly net-profit halt", halt["reason"])
        self.assertIn("latched", self.run_case()["reason"])

    def test_total_drawdown_and_contract_minimum(self):
        self.desk.db.execute("INSERT INTO pnl(at,strategy,venue,slug,net_usd) VALUES(?,?,?,?,?)",
                             ("2026-10-05T12:00:00Z", "poly_guide_paper", VENUE, "old", -400))
        self.assertIn("loss stop", self.run_case()["reason"])
        self.desk.db.execute("DELETE FROM pnl")
        self.assertIn("minimum", self.run_case(min_shares=100)["reason"])
    def test_future_pnl_ignored_and_foreign_rows_isolated(self):
        self.desk.db.execute("INSERT INTO pnl(at,strategy,venue,slug,net_usd) VALUES(?,?,?,?,?)",
                             ("2026-10-06T00:00:00Z", "poly_guide_paper", VENUE, "future", -100))
        self.assertEqual(self.run_case()["status"], "PAPER_FILL")
        self.desk.db.execute("""INSERT INTO decisions(at,strategy,venue,slug,status,reason)
            VALUES(?,?,?,?,?,?)""", (NOW, "foreign", VENUE, "foreign-slug", "PAPER_FILL", "fixture"))
        did = self.desk.db.execute("SELECT last_insert_rowid()").fetchone()[0]
        self.desk.db.execute("""INSERT INTO fills(decision_id,at,venue,slug,side,shares,price,fee,kind)
            VALUES(?,?,?,?,?,?,?,?,?)""", (did, NOW, VENUE, "foreign-slug", "YES", 10, .4, .1, "paper_entry"))
        with self.assertRaisesRegex(ValueError, "no unsettled"):
            self.desk.settle("foreign-slug", NOW, 1)
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM fills WHERE kind='paper_settlement'").fetchone()[0], 0)

    def test_nonfinite_quote_and_minimum_fail_closed(self):
        for value in (float("nan"), float("inf")):
            market, bbo, forecast = fixture()
            bbo["book"] = {"marketData": {"bestBid": {"value": .39},
                                          "bestAsk": {"value": value}}}
            r = self.desk.evaluate(market, bbo, forecast, fee_per_share=.01,
                                   now=NOW, stop_dir=self.root)
            self.assertEqual(r["status"], "BLOCKED")
        self.assertEqual(self.run_case(min_shares=1.5)["status"], "BLOCKED")

    def test_total_loss_includes_open_maximum_risk(self):
        self.desk.db.execute("INSERT INTO pnl(at,strategy,venue,slug,net_usd) VALUES(?,?,?,?,?)",
                             ("2026-10-05T12:00:00Z", "poly_guide_paper", VENUE, "old", -390))
        self.desk.db.execute("""INSERT INTO decisions(at,strategy,venue,slug,status,reason)
            VALUES(?,?,?,?,?,?)""", (NOW, "poly_guide_paper", VENUE, "open", "PAPER_FILL", "fixture"))
        did = self.desk.db.execute("SELECT last_insert_rowid()").fetchone()[0]
        self.desk.db.execute("""INSERT INTO fills(decision_id,at,venue,slug,side,shares,price,fee,kind)
            VALUES(?,?,?,?,?,?,?,?,?)""", (did, NOW, VENUE, "open", "YES", 30, .4, .3, "paper_entry"))
        self.assertIn("total loss stop", self.run_case()["reason"])

    def test_weekly_boundary_not_current_week_cost(self):
        self.desk.record_cost(NOW, 2, "model")
        self.assertEqual(self.run_case()["status"], "PAPER_FILL")
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM halts").fetchone()[0], 0)
    def test_trailing_14_days_earlier_week_changes_verdict(self):
        # The most recent seven days lose $2, but the earlier seven earn $3.
        # A seven-day calendar check would incorrectly halt; the guide's 14-day net wins.
        self.desk.db.execute("INSERT INTO pnl(at,strategy,venue,slug,net_usd) VALUES(?,?,?,?,?)",
                             ("2026-10-01T12:00:00Z", "poly_guide_paper", VENUE, "first-week", 3))
        self.desk.record_cost("2026-10-07T12:00:00Z", 2, "second-week")
        boundary_now = "2026-10-12T17:00:00Z"
        m, b, f = fixture()
        m["resolves_at"] = "2026-10-20T00:00:00Z"
        b["as_of"] = f["as_of"] = boundary_now
        r = self.desk.evaluate(m, b, f, fee_per_share=.01, now=boundary_now,
                               stop_dir=self.root)
        self.assertEqual(r["status"], "PAPER_FILL")
        self.assertEqual(self.desk.db.execute("SELECT net_usd FROM weekly_checks WHERE boundary=?",
                                               ("2026-10-12T00:00:00+00:00",)).fetchone()[0], 1)
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM halts").fetchone()[0], 0)

    def test_missed_sunday_caught_up_and_latched_after_restart(self):
        # First boundary is persisted; no calls at all during the next Sunday.
        self.assertEqual(self.run_case(fee=1)["status"], "BLOCKED")
        self.desk.record_cost("2026-10-07T12:00:00Z", 3, "older-week")
        self.desk.record_cost("2026-10-14T12:00:00Z", 1, "newer-week")
        self.desk.db.execute("INSERT INTO pnl(at,strategy,venue,slug,net_usd) VALUES(?,?,?,?,?)",
                             ("2026-10-14T13:00:00Z", "poly_guide_paper", VENUE, "later", 5))
        self.desk.close()
        self.desk = PaperDesk(self.root / "paper.sqlite", 1000)
        now = "2026-10-19T17:00:00Z"
        m, b, f = fixture()
        m["resolves_at"] = "2026-10-25T00:00:00Z"
        b["as_of"] = f["as_of"] = now
        r = self.desk.evaluate(m, b, f, fee_per_share=.01, now=now, stop_dir=self.root)
        self.assertIn("weekly net-profit halt", r["reason"])
        self.assertEqual(self.desk.db.execute("SELECT COUNT(*) FROM weekly_checks").fetchone()[0], 2)
        self.assertEqual(self.desk.db.execute("SELECT at FROM halts").fetchone()[0],
                         "2026-10-12T00:00:00+00:00")
        self.assertIn("latched", self.desk.evaluate(m, b, f, fee_per_share=.01,
                           now=now, stop_dir=self.root)["reason"])


if __name__ == "__main__":
    unittest.main()
