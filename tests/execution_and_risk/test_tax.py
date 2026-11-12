import json

import pytest

from valedia_trader.execution_and_risk import tax
from valedia_trader.research_loop import LoopPaths
from valedia_trader.research_loop.paper import Journal

NOTE = "Computed from the rules file; not tax advice."
UNVERIFIED = ["long_term", "wash_sale", "wash_sale_same_purchase", "lot_method"]


def rules():
    return tax.TaxRules.load()


def ledger(*fills, rules_=None):
    """A ledger fed (day, symbol, units, price[, fee]) tuples in the order given."""
    led = tax.TaxLedger(rules_ or rules())
    for f in fills:
        led.add_fill(*f)
    return led


def only(records, **match):
    got = [r for r in records if all(r[k] == v for k, v in match.items())]
    assert len(got) == 1, got
    return got[0]


def edited_rules(tmp_path, edit):
    """The repository's rules file with one change, loaded from a temporary copy."""
    raw = json.loads((LoopPaths().config / "tax_rules.json").read_text())
    edit(raw)
    path = tmp_path / "tax_rules.json"
    path.write_text(json.dumps(raw))
    return tax.TaxRules.load(path)


def crypto_rules(tmp_path):
    return edited_rules(tmp_path, lambda raw: raw["wash_sale"]["applies_to"].__setitem__("crypto", True))


# ---- the rules file


def test_the_rules_file_holds_the_specified_values_and_none_is_verified_yet():
    r = rules()
    assert r.jurisdiction == "US federal" and r.long_term_years == 1 and r.window_days == 30 and r.lot_method == "FIFO"
    assert r.applies_to == {"security": True, "crypto": False, "event_contract": False}
    assert r.remainder_is_replacement is False
    assert r.unverified() == UNVERIFIED
    assert r.rules_as_of() is None                                    # no rule verified, so no date the set is good as of
    raw = json.loads((LoopPaths().config / "tax_rules.json").read_text())
    assert "not tax advice" in raw["note"].lower() or "nothing here is tax advice" in raw["note"].lower()
    assert all(raw[k]["source"] and raw[k]["verified_on"] is None for k in UNVERIFIED)


def test_a_rules_file_without_the_same_purchase_rule_is_refused(tmp_path):
    with pytest.raises(ValueError, match="wash_sale_same_purchase"):
        edited_rules(tmp_path, lambda raw: raw.pop("wash_sale_same_purchase"))


def test_a_lot_method_other_than_fifo_is_refused(tmp_path):
    raw = json.loads((LoopPaths().config / "tax_rules.json").read_text())
    raw["lot_method"]["method"] = "LIFO"
    (tmp_path / "r.json").write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="FIFO"):
        tax.TaxRules.load(tmp_path / "r.json")


# ---- lots, fees, FIFO, term


def test_gain_and_loss_carry_the_fees_on_both_sides():
    led = ledger(("2025-01-02", "AAA", 10, 100.0, 1.0), ("2025-01-02", "BBB", 10, 50.0, 2.0),
                 ("2025-02-03", "AAA", -10, 110.0, 1.0), ("2025-03-03", "BBB", -10, 45.0, 2.0))
    recs = led.realized()
    a = only(recs, symbol="AAA")
    # basis 10 x 100 + 1 = 1001; proceeds 10 x 110 - 1 = 1099; gain 98
    assert a["cost_basis"] == pytest.approx(1001) and a["proceeds"] == pytest.approx(1099)
    assert a["gain_loss"] == pytest.approx(98) and a["term"] == "short" and a["disallowed_loss"] == 0
    b = only(recs, symbol="BBB")
    # basis 10 x 50 + 2 = 502; proceeds 10 x 45 - 2 = 448; loss -54, nothing bought back so all of it allowed
    assert b["gain_loss"] == pytest.approx(-54) and b["allowed_gain_loss"] == pytest.approx(-54)
    assert b["disallowed_loss"] == pytest.approx(0)


def test_a_sale_closes_the_oldest_units_first_across_partial_lots():
    led = ledger(("2025-01-02", "AAA", 10, 10.0), ("2025-01-03", "AAA", 10, 20.0), ("2025-02-03", "AAA", -15, 30.0, 3.0))
    recs = led.realized()
    # proceeds 15 x 30 - 3 = 447, 29.8 a unit
    first = only(recs, purchase_date="2025-01-02")
    assert first["units"] == pytest.approx(10) and first["proceeds"] == pytest.approx(298)
    assert first["cost_basis"] == pytest.approx(100) and first["gain_loss"] == pytest.approx(198)
    second = only(recs, purchase_date="2025-01-03")
    # 5 of the 10 units bought at 20: basis 100, proceeds 5 x 29.8 = 149, gain 49
    assert second["units"] == pytest.approx(5) and second["proceeds"] == pytest.approx(149)
    assert second["gain_loss"] == pytest.approx(49)
    lots = led.open_lots("AAA")
    assert len(lots) == 1 and lots[0]["units"] == pytest.approx(5) and lots[0]["cost_basis"] == pytest.approx(100)
    assert lots[0]["purchase_date"] == "2025-01-03"


def test_the_one_year_boundary_is_short_on_the_anniversary_and_long_the_day_after():
    led = ledger(("2025-03-10", "AAA", 2, 100.0), ("2026-03-10", "AAA", -1, 120.0), ("2026-03-11", "AAA", -1, 120.0))
    assert only(led.realized(), sale_date="2026-03-10")["term"] == "short"
    assert only(led.realized(), sale_date="2026-03-11")["term"] == "long"
    assert led.summary("2026", as_of="2026-12-31")["long_term"]["gain_loss"] == pytest.approx(20)


def test_selling_more_than_is_held_raises():
    led = ledger(("2025-01-02", "AAA", 5, 10.0))
    with pytest.raises(ValueError, match="short"):
        led.add_fill("2025-01-03", "AAA", -6, 10.0)
    with pytest.raises(ValueError, match="short"):
        led.add_fill("2025-01-03", "ZZZ", -1, 10.0)
    assert led.open_lots("AAA")[0]["units"] == pytest.approx(5)          # the refused fill changed nothing


def test_fills_must_arrive_in_date_order():
    led = ledger(("2025-02-01", "AAA", 5, 10.0))
    with pytest.raises(ValueError, match="date order"):
        led.add_fill("2025-01-31", "AAA", 1, 10.0)


# ---- wash sales


def test_a_loss_with_a_full_repurchase_ten_days_later_is_disallowed_into_the_new_lot():
    led = ledger(("2025-01-02", "XLE", 10, 100.0), ("2025-06-02", "XLE", -10, 90.0), ("2025-06-12", "XLE", 10, 92.0))
    rec = only(led.realized())
    # loss 1000 - 900 = 100, all 10 units bought back: disallowed 100, allowed 0
    assert rec["gain_loss"] == pytest.approx(-100) and rec["disallowed_loss"] == pytest.approx(100)
    assert rec["allowed_gain_loss"] == pytest.approx(0)
    assert rec["wash_sales"][0]["purchase_date"] == "2025-06-12" and rec["wash_sales"][0]["units"] == pytest.approx(10)
    lot = only(led.open_lots("XLE"))
    # basis 10 x 92 + 100 = 1020; held 2025-01-02 -> 2025-06-02 = 151 days, so holding starts 2025-06-12 - 151 = 2025-01-12
    assert lot["cost_basis"] == pytest.approx(1020) and lot["holding_start"] == "2025-01-12"
    assert lot["purchase_date"] == "2025-06-12" and lot["wash_adjustment"] == pytest.approx(100)


def test_the_replacement_lots_higher_basis_and_tacked_holding_period_show_in_a_later_sale():
    led = ledger(("2025-01-02", "XLE", 10, 100.0), ("2025-06-02", "XLE", -10, 90.0), ("2025-06-12", "XLE", 10, 92.0),
                 ("2026-01-12", "XLE", -5, 95.0), ("2026-01-13", "XLE", -5, 95.0))
    # replacement lot: basis 1020 for 10 units, holding start 2025-01-12 (see the test above)
    on_anniversary = only(led.realized(), sale_date="2026-01-12")
    # 5 units: proceeds 475, basis 510, loss -35; 2026-01-12 is not later than 2025-01-12 + 1 year: short
    assert on_anniversary["cost_basis"] == pytest.approx(510) and on_anniversary["gain_loss"] == pytest.approx(-35)
    assert on_anniversary["term"] == "short" and on_anniversary["holding_start"] == "2025-01-12"
    day_after = only(led.realized(), sale_date="2026-01-13")
    # long-term only because of tacking: bought 2025-06-12, which alone would make it short-term
    assert day_after["term"] == "long" and day_after["purchase_date"] == "2025-06-12"
    assert day_after["gain_loss"] == pytest.approx(-35)


def test_a_partial_repurchase_disallows_only_the_matched_units():
    led = ledger(("2025-01-02", "XLE", 10, 100.0), ("2025-06-02", "XLE", -10, 90.0), ("2025-06-12", "XLE", 4, 91.0))
    rec = only(led.realized())
    # loss 10 a unit; 4 units bought back: disallowed 40, allowed -60
    assert rec["disallowed_loss"] == pytest.approx(40) and rec["allowed_gain_loss"] == pytest.approx(-60)
    lot = only(led.open_lots("XLE"))
    assert lot["cost_basis"] == pytest.approx(4 * 91 + 40)               # 364 + 40 = 404


def test_a_purchase_ten_days_before_the_loss_sale_still_held_is_a_replacement():
    led = ledger(("2025-01-02", "XLE", 10, 100.0), ("2025-05-23", "XLE", 5, 80.0), ("2025-06-02", "XLE", -10, 90.0))
    rec = only(led.realized())
    # FIFO closes the January lot: loss 1000 - 900 = 100, 10 a unit; the 5 May units replace: disallowed 50, allowed -50
    assert rec["purchase_date"] == "2025-01-02" and rec["disallowed_loss"] == pytest.approx(50)
    assert rec["allowed_gain_loss"] == pytest.approx(-50) and rec["wash_sales"][0]["purchase_date"] == "2025-05-23"
    lot = only(led.open_lots("XLE"))
    # basis 400 + 50 = 450; holding start 2025-05-23 - 151 days = 2024-12-23
    assert lot["cost_basis"] == pytest.approx(450) and lot["holding_start"] == "2024-12-23"


@pytest.mark.parametrize("day, washed", [("2025-07-02", True), ("2025-07-03", False)])
def test_a_repurchase_on_day_30_after_is_a_wash_sale_and_day_31_is_not(day, washed):
    led = ledger(("2025-01-02", "XLE", 10, 100.0), ("2025-06-02", "XLE", -10, 90.0), (day, "XLE", 10, 90.0))
    # 2025-06-02 + 30 days = 2025-07-02
    assert only(led.realized())["disallowed_loss"] == pytest.approx(100 if washed else 0)


@pytest.mark.parametrize("day, washed", [("2025-05-03", True), ("2025-05-02", False)])
def test_a_purchase_30_days_before_is_a_replacement_and_31_days_before_is_not(day, washed):
    led = ledger(("2025-01-02", "XLE", 10, 100.0), (day, "XLE", 1, 95.0), ("2025-06-02", "XLE", -10, 90.0))
    # 2025-06-02 - 30 days = 2025-05-03; one unit replaces: disallowed 10
    assert only(led.realized())["disallowed_loss"] == pytest.approx(10 if washed else 0)


def test_the_same_replacement_units_are_never_used_twice():
    led = ledger(("2025-01-02", "AAA", 20, 100.0), ("2025-03-03", "AAA", -10, 90.0), ("2025-03-10", "AAA", 10, 90.0),
                 ("2025-03-17", "AAA", -10, 80.0))
    first = only(led.realized(), sale_date="2025-03-03")
    second = only(led.realized(), sale_date="2025-03-17")
    # sale 1: loss 100, replaced by the 2025-03-10 units. Sale 2 closes the last 10 January units: loss 1000 - 800 = 200;
    # the 2025-03-10 units are inside its window but already used, so nothing is disallowed
    assert first["disallowed_loss"] == pytest.approx(100) and second["disallowed_loss"] == pytest.approx(0)
    repl = only(led.open_lots("AAA"))
    # basis 900 + 100 = 1000; held 2025-01-02 -> 2025-03-03 = 60 days: holding start 2025-03-10 - 60 = 2025-01-09
    assert repl["cost_basis"] == pytest.approx(1000) and repl["holding_start"] == "2025-01-09"
    assert repl["used_as_replacement"] is True
    led.add_fill("2025-03-20", "AAA", 10, 85.0)
    first = only(led.realized(), sale_date="2025-03-03")
    second = only(led.realized(), sale_date="2025-03-17")
    # the new buy replaces only sale 2 (sale 1 is fully matched): disallowed 200
    assert first["disallowed_loss"] == pytest.approx(100) and second["disallowed_loss"] == pytest.approx(200)
    new = only(led.open_lots("AAA"), purchase_date="2025-03-20")
    # basis 850 + 200 = 1050; held 2025-01-02 -> 2025-03-17 = 74 days: holding start 2025-03-20 - 74 = 2025-01-05
    assert new["cost_basis"] == pytest.approx(1050) and new["holding_start"] == "2025-01-05"


def test_crypto_is_not_washed_by_default_and_is_when_the_rules_file_switches_it_on(tmp_path):
    fills = (("2025-01-02", "BTC-USD", 1, 100.0), ("2025-03-03", "BTC-USD", -1, 90.0), ("2025-03-10", "BTC-USD", 1, 90.0))
    assert only(ledger(*fills).realized())["disallowed_loss"] == pytest.approx(0)
    assert only(ledger(*fills).realized())["asset_class"] == "crypto"
    # loss 10 on one unit, bought back 7 days later
    assert only(ledger(*fills, rules_=crypto_rules(tmp_path)).realized())["disallowed_loss"] == pytest.approx(10)
    led = tax.TaxLedger(rules())                                         # a fill's asset_class overrides the symbol
    led.add_fill("2025-01-02", "GBTC", 1, 100.0, asset_class="crypto")
    led.add_fill("2025-03-03", "GBTC", -1, 90.0, asset_class="crypto")
    led.add_fill("2025-03-10", "GBTC", 1, 90.0, asset_class="crypto")
    assert only(led.realized())["disallowed_loss"] == pytest.approx(0)


def test_an_asset_class_the_rules_file_does_not_know_is_refused():
    with pytest.raises(ValueError, match="option"):
        tax.TaxLedger(rules()).add_fill("2025-01-02", "SPY 500C", 1, 3.0, asset_class="option")


def test_a_provisional_loss_becomes_disallowed_when_a_later_buy_arrives():
    led = ledger(("2025-01-02", "XLE", 10, 100.0), ("2025-06-02", "XLE", -10, 90.0))
    s = led.summary("2025")
    # as of the last fill (2025-06-02) the -100 is allowed but provisional until 30 days after the sale
    assert s["as_of"] == "2025-06-02" and s["provisional_records"] == 1
    assert s["total"]["net_gain_loss"] == pytest.approx(-100) and s["total"]["provisional_loss"] == pytest.approx(100)
    assert only(led.realized())["provisional"] is True
    assert led.wash_sale_windows("2025-06-02") == {"XLE": {"loss_at_risk": pytest.approx(100), "clean_from": "2025-07-03",
                                                           "sales": [{"day": "2025-06-02", "units": pytest.approx(10),
                                                                      "loss": pytest.approx(100)}]}}
    led.add_fill("2025-06-20", "XLE", 10, 95.0)
    s = led.summary("2025")
    assert s["as_of"] == "2025-06-20" and s["provisional_records"] == 0
    assert s["total"]["disallowed_loss"] == pytest.approx(100) and s["total"]["net_gain_loss"] == pytest.approx(0)
    assert led.wash_sale_windows("2025-06-20") == {}
    earlier = led.summary("2025", as_of="2025-06-10")                    # as of a date before the buy, the buy is unseen
    assert earlier["total"]["disallowed_loss"] == pytest.approx(0) and earlier["provisional_records"] == 1


def test_a_loss_is_final_once_31_days_have_passed_without_a_repurchase():
    led = ledger(("2025-01-02", "XLE", 10, 100.0), ("2025-06-02", "XLE", -10, 90.0))
    assert led.summary("2025", as_of="2025-07-02")["provisional_records"] == 1
    assert led.summary("2025", as_of="2025-07-03")["provisional_records"] == 0
    assert led.wash_sale_windows("2025-07-03") == {}


# ---- the rest of the same purchase is not a replacement


def trim():
    """One buy of 100 at 50 on day 1 (2025-03-01); 40 of them sold at 45 on day 10 (2025-03-10)."""
    return (("2025-03-01", "XLK", 100, 50.0), ("2025-03-10", "XLK", -40, 45.0))


def test_selling_part_of_one_purchase_at_a_loss_is_not_washed_by_the_rest_of_that_purchase():
    led = ledger(*trim())
    rec = only(led.realized())
    # basis 40 x 50 = 2000, proceeds 40 x 45 = 1800: loss 200. The other 60 units are the same purchase: no replacement
    assert rec["gain_loss"] == pytest.approx(-200) and rec["disallowed_loss"] == pytest.approx(0)
    assert rec["allowed_gain_loss"] == pytest.approx(-200) and rec["wash_sales"] == []
    # provisional through day 40: 2025-03-10 + 30 days = 2025-04-09
    assert rec["provisional"] is True and rec["provisional_until"] == "2025-04-09"
    assert led.summary("2025", as_of="2025-04-09")["provisional_records"] == 1
    assert led.summary("2025", as_of="2025-04-10")["provisional_records"] == 0
    lot = only(led.open_lots("XLK"))
    assert lot["units"] == pytest.approx(60) and lot["cost_basis"] == pytest.approx(3000) and lot["wash_adjustment"] == 0


def test_a_second_purchase_inside_the_window_washes_only_its_own_units_of_the_trim():
    led = ledger(*trim(), ("2025-03-20", "XLK", 25, 46.0))
    rec = only(led.realized())
    # 25 of the 40 loss units, 5 a unit: disallowed 125; the other 15 x 5 = 75 stay allowed
    assert rec["disallowed_loss"] == pytest.approx(125) and rec["allowed_gain_loss"] == pytest.approx(-75)
    new = only(led.open_lots("XLK"), purchase_date="2025-03-20")
    # basis 25 x 46 + 125 = 1275; held 2025-03-01 -> 2025-03-10 = 9 days: holding start 2025-03-20 - 9 = 2025-03-11
    assert new["cost_basis"] == pytest.approx(1275) and new["holding_start"] == "2025-03-11"
    old = only(led.open_lots("XLK"), purchase_date="2025-03-01")
    assert old["units"] == pytest.approx(60) and old["used_as_replacement"] is False


@pytest.mark.parametrize("second_day", ["2025-03-05", "2025-03-01"])
def test_two_buy_fills_are_two_purchases_even_on_the_same_day(second_day):
    led = ledger(("2025-03-01", "XLK", 10, 50.0), (second_day, "XLK", 10, 48.0), ("2025-03-10", "XLK", -10, 45.0))
    rec = only(led.realized())
    # FIFO closes the first fill: basis 500, proceeds 450, loss 50; the second fill is a different purchase: disallowed 50
    assert rec["disallowed_loss"] == pytest.approx(50) and rec["wash_sales"][0]["purchase_date"] == second_day
    lot = only(led.open_lots("XLK"))
    assert lot["cost_basis"] == pytest.approx(480 + 50) and lot["purchase_id"] != rec["purchase_id"]


def test_a_lot_split_by_a_wash_sale_is_not_washed_by_the_other_part_of_the_same_purchase():
    led = ledger(("2025-01-02", "XLE", 10, 100.0), ("2025-06-02", "XLE", -10, 90.0), ("2025-06-12", "XLE", 20, 92.0))
    # loss 100 on the January lot; 10 of the 20 June units replace it: that part's basis 920 + 100 = 1020, the rest 920
    parts = led.open_lots("XLE")
    assert [p["cost_basis"] for p in parts] == [pytest.approx(1020), pytest.approx(920)]
    assert parts[0]["purchase_id"] == parts[1]["purchase_id"] and parts[0]["id"] != parts[1]["id"]
    led.add_fill("2025-06-20", "XLE", -10, 80.0)
    rec = only(led.realized(), sale_date="2025-06-20")
    # FIFO sells the replacement part first: proceeds 800, basis 1020, loss 220. The other 10 June units are inside the
    # window and unused, but they are the same purchase: nothing disallowed
    assert rec["lot_id"] == parts[0]["id"] and rec["gain_loss"] == pytest.approx(-220)
    assert rec["disallowed_loss"] == pytest.approx(0) and rec["purchase_id"] == parts[0]["purchase_id"]
    assert only(led.open_lots("XLE"))["used_as_replacement"] is False


def test_with_remainder_is_replacement_switched_on_the_trim_is_a_wash_sale_again(tmp_path):
    on = edited_rules(tmp_path, lambda raw: raw["wash_sale_same_purchase"].__setitem__("remainder_is_replacement", True))
    led = ledger(*trim(), rules_=on)
    # the 60 held units of the same purchase are inside the window: all 40 loss units matched, 5 a unit: disallowed 200
    assert only(led.realized())["disallowed_loss"] == pytest.approx(200)


def test_open_lots_and_realised_records_show_the_purchase_they_came_from():
    led = ledger(("2025-03-01", "XLK", 100, 50.0), ("2025-03-02", "XLK", 5, 50.0), ("2025-03-10", "XLK", -40, 55.0))
    rec = only(led.realized())
    lots = led.open_lots("XLK")
    assert rec["purchase_id"] and all(lot["purchase_id"] for lot in lots)
    assert lots[0]["purchase_id"] == rec["purchase_id"] and lots[1]["purchase_id"] != rec["purchase_id"]
    assert lots[0]["purchase_date"] == "2025-03-01" and lots[1]["purchase_date"] == "2025-03-02"


# ---- summaries


def mixed():
    return ledger(("2024-01-02", "BBB", 10, 50.0),
                  ("2025-01-02", "AAA", 10, 100.0),
                  ("2025-02-03", "AAA", -4, 110.0),       # gain 40, short
                  ("2025-02-10", "AAA", -3, 90.0),        # loss 30, short; the last January units are 39 days old: no replacement
                  ("2025-02-20", "AAA", 3, 95.0),         # washes the 2025-02-10 loss: disallowed 30
                  ("2025-03-05", "AAA", -3, 120.0),       # FIFO: the last 3 January units, basis 300, proceeds 360: gain 60
                  ("2025-03-20", "BBB", -10, 60.0))       # gain 100, long


def test_summaries_for_a_day_a_month_and_a_year_add_up():
    led = mixed()
    as_of = "2025-12-31"
    day = lambda d: led.summary(d, as_of=as_of)["total"]
    feb, mar, year = (led.summary(p, as_of=as_of) for p in ("2025-02", "2025-03", "2025"))
    assert day("2025-02-03")["net_gain_loss"] == pytest.approx(40)
    assert day("2025-02-10")["gain_loss"] == pytest.approx(-30) and day("2025-02-10")["disallowed_loss"] == pytest.approx(30)
    assert day("2025-02-10")["net_gain_loss"] == pytest.approx(0)
    for key in ("proceeds", "cost_basis", "gain_loss", "disallowed_loss", "net_gain_loss"):
        assert day("2025-02-03")[key] + day("2025-02-10")[key] == pytest.approx(feb["total"][key])
        assert day("2025-03-05")[key] + day("2025-03-20")[key] == pytest.approx(mar["total"][key])
        assert feb["total"][key] + mar["total"][key] == pytest.approx(year["total"][key])
        assert year["short_term"][key] + year["long_term"][key] == pytest.approx(year["total"][key])
    # short: 40 - 30 + 60 = 70 raw, 30 disallowed, 100 net; long: 100
    assert year["short_term"]["gain_loss"] == pytest.approx(70) and year["short_term"]["net_gain_loss"] == pytest.approx(100)
    assert year["long_term"]["net_gain_loss"] == pytest.approx(100) and year["total"]["count"] == 4
    assert led.summary("2025-04", as_of=as_of)["total"]["count"] == 0


def test_reports_name_their_as_of_date_the_unverified_rules_and_that_they_are_not_advice():
    s = mixed().summary("2025", as_of="2025-12-31")
    assert s["as_of"] == "2025-12-31" and s["period"] == "2025" and s["note"] == NOTE
    assert s["unverified_rules"] == UNVERIFIED and "rules_as_of" in s
    json.dumps(s)                                                         # plain JSON
    with pytest.raises(ValueError):
        mixed().summary("2025/02")


# ---- pre-trade checks


def test_check_sell_reports_the_gain_by_term_the_wash_sale_and_the_first_clean_repurchase_date():
    led = ledger(("2025-01-02", "XLE", 10, 100.0), ("2025-05-23", "XLE", 5, 80.0))
    c = led.check_sell("XLE", 10, 90.0, "2025-06-02")
    # same as the purchase-before test: loss 100 short-term, the 5 May units disallow 50, the other 50 is at risk
    assert c["gain_loss"]["short_term"] == pytest.approx(-100) and c["gain_loss"]["long_term"] == pytest.approx(0)
    assert c["disallowed_loss"] == pytest.approx(50) and c["allowed_gain_loss"]["short_term"] == pytest.approx(-50)
    assert c["wash_sale"]["matches"][0]["purchase_date"] == "2025-05-23"
    assert c["wash_sale"]["matches"][0]["units"] == pytest.approx(5)
    assert c["wash_sale"]["loss_at_risk"] == pytest.approx(50) and c["wash_sale"]["clean_from"] == "2025-07-03"
    assert c["note"] == NOTE and c["unverified_rules"] == UNVERIFIED and "rules_as_of" in c
    json.dumps(c)
    assert sum(lot["units"] for lot in led.open_lots("XLE")) == pytest.approx(15) and led.realized() == []   # unchanged


def test_check_sell_refuses_more_than_is_held():
    with pytest.raises(ValueError, match="short"):
        ledger(("2025-01-02", "XLE", 10, 100.0)).check_sell("XLE", 11, 90.0, "2025-06-02")


def test_check_buy_says_which_loss_sale_it_would_wash_and_when_buying_is_clean():
    led = ledger(("2025-01-02", "XLE", 10, 100.0), ("2025-06-02", "XLE", -10, 90.0))
    c = led.check_buy("XLE", 4, "2025-06-12")
    # 4 of the 10 loss units, 10 a unit: disallowed 40; the sale held 151 days
    assert c["disallowed_loss"] == pytest.approx(40) and c["matches"][0]["sale_date"] == "2025-06-02"
    assert c["matches"][0]["units"] == pytest.approx(4) and c["matches"][0]["holding_days_added"] == 151
    assert c["clean_from"] == "2025-07-03" and c["note"] == NOTE
    json.dumps(c)
    assert led.open_lots("XLE") == []                                    # unchanged
    assert led.check_buy("XLE", 4, "2025-07-03")["disallowed_loss"] == pytest.approx(0)


# ---- replay from the journal


def test_from_fills_reads_journal_entries_and_ignores_everything_but_fills():
    entries = [{"type": "fill", "day": "2025-06-12", "symbol": "XLE", "units": 10, "price": 92.0, "fee": 0.0, "strategy": "b"},
               {"type": "decision", "day": "2025-06-01", "symbol": "XLE"},
               {"type": "fill", "day": "2025-01-02", "symbol": "XLE", "units": 10, "price": 100.0, "fee": 0.0, "strategy": "a"},
               {"type": "fill", "day": "2025-06-02", "symbol": "XLE", "units": -10, "price": 90.0, "fee": 0.0, "strategy": "b"}]
    led = tax.TaxLedger.from_fills(entries, rules())
    # lots are the account's across strategies: a's lot is sold by b and replaced by b
    assert only(led.realized())["disallowed_loss"] == pytest.approx(100)


def test_the_command_line_reports_and_checks_from_a_journal(tmp_path, capsys):
    j = Journal(tmp_path)
    j.write("2025-01-02", {"type": "fill", "symbol": "XLE", "units": 10, "price": 100.0, "fee": 0.0, "strategy": "s"})
    j.write("2025-06-02", {"type": "fill", "symbol": "XLE", "units": -10, "price": 90.0, "fee": 0.0, "strategy": "s"})
    assert tax.main(["report", "--journal", str(tmp_path), "--period", "2025", "--as-of", "2025-06-05"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["as_of"] == "2025-06-05" and out["total"]["net_gain_loss"] == pytest.approx(-100)
    assert out["wash_sale_windows"]["XLE"]["clean_from"] == "2025-07-03"
    assert tax.main(["check", "--journal", str(tmp_path), "--symbol", "XLE", "--side", "buy", "--units", "4",
                     "--day", "2025-06-12"]) == 0
    assert json.loads(capsys.readouterr().out)["disallowed_loss"] == pytest.approx(40)
    assert tax.main(["check", "--journal", str(tmp_path), "--symbol", "XLE", "--side", "sell", "--units", "1",
                     "--day", "2025-06-12"]) == 2                          # a sale check needs a price
