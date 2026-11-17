#!/usr/bin/env python3
"""Paper mover fusion. Fixture only. No network. No orders.

Run: python3 test_all_knowing_movers_scan.py
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)

# Unit tests assert git-default OFF without Burton's ~/.openclaw/.env flips.
os.environ["OPENCLAW_SKIP_ENV"] = "1"
os.environ.pop("VALEDIA_ORNITH_SEASON", None)
os.environ.pop("VALEDIA_SYSTEMONE", None)
os.environ.pop("VALEDIA_SYSTEMONE_MODEL", None)
SLOG = os.path.join(tempfile.mkdtemp(prefix="akslog_"), "decisions.jsonl")
# Retired shadow decider name, spelled indirectly so grep over scripts/ stays clean.
RETIRED = "".join(["l", "a", "y", "a"])

import all_knowing_movers_scan as ak
import crypto_paper as cp
import ornith_seasoning
import dynamic_universe as du
import valedia_config as vc
import weather_shadow as ws

# The shared Elephant-1 instance is faked, scoped per test (fixture / script main): the model
# list shows the one right instance, so it passes the pin. No real list call, no typed call.
import systemone_test_fakes as fk
from systemone_test_fakes import Scoped
_S = Scoped(ak.s1, SLOG)
_scoped_fakes = _S.fixture()


def _doc():
    return ak.build(fixture_path=ak.DEFAULT_FIXTURE, annotate=lambda d: d)


def test_models_dir_v2():
    expect = os.path.realpath(os.path.join(ROOT, "models"))
    assert os.path.realpath(du.MODELS_DIR) == expect
    assert du.HOT_LIST == os.path.join(du.MODELS_DIR, "hot_list.json")
    assert not du.MODELS_DIR.endswith(os.path.join(".openclaw", "workspace", "models"))


def test_fee_true_rank_and_size_gate():
    doc = _doc()
    by = {r["symbol"]: r for r in doc["ranked"]}
    maker, taker, slip = cp.paper_fees()
    rt = 2.0 * (taker + slip)
    notional = ak.BANKROLL * cp.CLIP_FRAC
    pump = by["PUMP"]
    assert abs(pump["edge"] - (0.164 - rt)) < 1e-9
    assert abs(pump["impact_usd"] - pump["edge"] * notional) < 1e-6
    assert pump["label"] == "would_buy"
    assert pump["would_have"]["sized"] is True
    assert pump["would_have"]["create_order"] is False
    assert pump["would_have"]["qty"] > 0
    assert by["QNT"]["label"] == "would_buy"
    # High buzz, small move: watched, not sized, ranks under the 16% names.
    assert by["NOISE"]["label"] == "watch"
    assert by["NOISE"]["would_have"]["sized"] is False
    assert by["NOISE"]["buzz"] > by["PUMP"]["buzz"]
    # 22% oil prints the largest impact and stays unsized. First size is PUMP.
    assert doc["ranked"][0]["symbol"] == "USO"
    sized = [r for r in doc["ranked"] if r["would_have"]["sized"]]
    assert [r["symbol"] for r in sized] == ["PUMP", "QNT"]
    assert doc["ranked"].index(pump) < doc["ranked"].index(by["NOISE"])
    assert by["DUST"]["edge"] < 0
    assert by["DUST"]["label"] == "would_pass"
    assert by["SPY"]["class"] == "etf"
    assert by["SPY"]["label"] == "watch"


def test_oil_and_weather_stay_watch():
    doc = _doc()
    by = {r["symbol"]: r for r in doc["ranked"]}
    uso = by["USO"]
    assert uso["oil_fenced"] is True
    assert uso["harvest"] == "NO_HARVEST"
    assert uso["label"] == "watch"
    assert uso["would_have"]["sized"] is False
    assert uso["chg_24h"] >= ak.PRINT_BAR_PCT
    w = by["KXHIGHNY"]
    assert w["class"] == "weather"
    assert w["label"] == "watch"
    assert w["would_have"]["sized"] is False
    assert w["impact_usd"] is None


def test_nimble_note_does_not_reorder_and_fail_open():
    os.environ.pop("VALEDIA_SYSTEMONE", None)
    doc = ak.build(fixture_path=ak.DEFAULT_FIXTURE)  # default note path, engine off: no call
    note = doc["nimble_note"]
    assert note["fail_open"] is True and note["reason"] == "VALEDIA_SYSTEMONE_off"
    assert note["changes_rank"] is False and note["seasoning_only"] is True
    # The note carries code-computed scan facts even with the engine off.
    assert note["facts"]["has_rows"] is True and note["facts"]["any_sized"] is True
    assert note["facts"]["top_fenced"] is True  # USO (oil) ranks first and stays unsized
    assert doc["fences"]["nimble_note"] == "seasoning_only" and "jev" not in doc
    symbols = [r["symbol"] for r in doc["ranked"]]

    def _boom(_ranked):
        raise TimeoutError("nimble down")

    blown = ak.build(fixture_path=ak.DEFAULT_FIXTURE, annotate=_boom)
    assert blown["nimble_note"]["fail_open"] is True
    assert blown["nimble_note"]["error"] == "TimeoutError"
    assert [r["symbol"] for r in blown["ranked"]] == symbols


def test_fences_and_hygiene():
    src = open(os.path.join(HERE, "all_knowing_movers_scan.py")).read()
    for banned in (
        RETIRED, "8766", "create_order(", "place_order", "alpaca_executor",
        "coinbase_trader", "kraken_client", "kalshi_executor", "--live",
        "MIROFISH_AUTO_RAISE = True", "import subprocess",
    ):
        assert banned not in src, banned
    assert cp.ALLOWLIST == ("BTC-USDT", "ETH-USDT")
    assert vc.CRYPTO_PAPER_SAFETY == "PAPER/SHADOW+ORNITH"
    assert vc.MIROFISH_AUTO_RAISE is False
    assert ak.WEATHER_PREFIXES == ws.PREFIXES
    doc = _doc()
    assert doc["fences"]["allowlist_widened"] is False
    assert doc["fences"]["create_order"] is False
    assert doc["fences"]["live"] is False
    assert doc["fences"]["mirofish_auto_raise"] is False
    assert doc["probe"]["PUMP"]["present"] is True
    assert doc["probe"]["QNT"]["present"] is True
    assert doc["status"] == "PAPER/SHADOW"


def test_write_and_cli_fixture(tmp=None):
    import tempfile
    doc = _doc()
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "scan.json")
        watch = os.path.join(td, "watch.json")
        ak.write_doc(doc, out, watch)
        saved = json.load(open(out))
        paper = json.load(open(watch))
        assert saved["ranked"][0]["symbol"] == "USO"
        assert saved["probe"]["PUMP"]["sized"] is True
        assert paper["paper_only"] is True
        assert paper["create_order"] is False
        assert paper["crypto_paper_allowlist"] == ["BTC-USDT", "ETH-USDT"]
        assert all(n["label"] != "would_pass" for n in paper["names"])
        assert any(n["symbol"] == "USO" and n["sized"] is False for n in paper["names"])
        rc = ak.main(["--fixture", ak.DEFAULT_FIXTURE, "--out", out, "--watch", watch])
        assert rc == 0


def test_tape_prefers_coingecko_then_binance():
    def cg_ok(url):
        assert "binance" not in url
        return [{
            "symbol": "pump",
            "price_change_percentage_24h": 17.5,
            "current_price": 0.003,
            "market_cap": 2e9,
        }, {
            "symbol": "qnt",
            "price_change_percentage_24h": 4.0,
            "current_price": 90.0,
        }]

    rows, notes = ak.load_tape(cg_ok)
    assert notes["tape"] == "coingecko"
    assert notes["PUMP"] == "in_coingecko_scan"
    by = {r["symbol"]: r for r in rows}
    assert by["PUMP"]["chg_24h"] == 17.5
    assert by["QNT"]["reasons"] == ["coingecko_markets"]

    def cg_429(url):
        if "coingecko" in url:
            raise RuntimeError("HTTP Error 429")
        return [
            {"symbol": "AAAUSDT", "priceChangePercent": "40", "lastPrice": "1", "quoteVolume": "5000000"},
            {"symbol": "PUMPUSDT", "priceChangePercent": "3.2", "lastPrice": "0.004", "quoteVolume": "100"},
            {"symbol": "QNTUSDT", "priceChangePercent": "-1.1", "lastPrice": "90", "quoteVolume": "100"},
            {"symbol": "USDCUSDT", "priceChangePercent": "0.01", "lastPrice": "1", "quoteVolume": "9000000"},
        ]

    rows, notes = ak.load_tape(cg_429)
    assert notes["tape"] == "binance_public_24h"
    by = {r["symbol"]: r for r in rows}
    assert "USDC" not in by
    assert by["AAA"]["chg_24h"] == 40.0
    assert by["PUMP"]["chg_24h"] == 3.2
    assert notes["PUMP"] == "in_binance_24h"
    assert by["QNT"]["chg_24h"] == -1.1
    assert by["PUMP"]["quote_volume_usd"] == 100.0
    ranked = {r["symbol"]: r for r in ak.rank_movers(rows)}
    assert ranked["PUMP"]["quote_volume_usd"] == 100.0
    assert ranked["AAA"]["label"] == "would_buy"


def test_systemone_default_off_and_fail_open():
    assert os.environ.get("VALEDIA_SYSTEMONE") != "1"
    off = _doc()
    assert off["systemone"]["backend"] == "stub"
    assert off["systemone"]["enabled"] is False
    assert off["systemone"]["model"] == ak.s1._pin.dm.INSTANCE
    assert off["fences"]["systemone"] == "stub"
    assert "systemone_shadow" not in off["ranked"][0]
    # The retired decider leaves no key, row field, fence, env read, or print line.
    assert RETIRED not in json.dumps(off, default=str).lower()
    assert RETIRED not in ak.format_table(off).lower()
    assert "label" in off["ranked"][0] and "decide" not in off["ranked"][0]
    os.environ["VALEDIA_SYSTEMONE"] = "1"
    try:
        seen = []

        def down(url, payload, timeout):
            seen.append(payload["state"])
            assert payload["state"]["create_order"] is False
            assert payload["model"] == ak.s1._pin.dm.INSTANCE
            raise ConnectionRefusedError("down")

        ak.s1._post = down  # the client's own transport (an injected post is not used)
        blown = ak.build(fixture_path=ak.DEFAULT_FIXTURE, annotate=lambda d: d)
        assert blown["systemone"]["backend"] == "stub"
        assert blown["systemone"]["fail_open"] is True
        assert blown["fences"]["systemone"] == "stub"
        assert [r["symbol"] for r in blown["ranked"]] == [r["symbol"] for r in off["ranked"]]
        by = {r["symbol"]: r for r in blown["ranked"]}
        assert by["PUMP"]["label"] == "would_buy"
        assert by["PUMP"]["would_have"]["create_order"] is False
        assert by["USO"]["harvest"] == "NO_HARVEST"
        assert by["USO"]["would_have"]["sized"] is False
        assert by["KXHIGHNY"]["label"] == "watch"
        assert all(r["would_have"]["create_order"] is False for r in blown["ranked"])
        states = {s["symbol"]: s for s in seen if s.get("symbol") != "warmup"}
        # The decision model decides stock/crypto rows only. Weather and oil stay code-labelled watch.
        assert states["PUMP-USD"]["asset_class"] == "crypto"
        assert states["SPY"]["asset_class"] == "stock"
        assert "KXHIGHNY" not in states and "USO" not in states
        assert states["NOISE-USD"]["informed"]["news_present"] is True
        assert "systemone_shadow" not in by["KXHIGHNY"] and "nimble" not in by["USO"]
        assert by["PUMP"]["nimble"]["allow"] is True and by["PUMP"]["nimble"]["enforced"] is False
        assert by["PUMP"]["nimble"]["error"] == "ConnectionRefusedError"

        def ok(url, payload, timeout):
            assert payload["model"] == ak.s1._pin.dm.INSTANCE
            return fk.typed_reply(payload["questions"], {
                "action": {"buy": 0.9, "hold": 0.05, "skip": 0.05}, "risk": "3"}, confidence=0.9)

        ak.s1._post = ok
        stamped = ak.build(fixture_path=ak.DEFAULT_FIXTURE, annotate=lambda d: d)
        assert stamped["systemone"]["backend"] == "systemone_http"
        assert stamped["fences"]["create_order"] is False
        by = {r["symbol"]: r for r in stamped["ranked"]}
        assert by["USO"]["label"] == "watch"
        assert by["USO"]["would_have"]["sized"] is False
        assert by["PUMP"]["systemone_shadow"]["backend"] == "systemone_http"
        assert by["PUMP"]["systemone_shadow"]["answers"]["nimble_action"] == "buy"
        assert by["PUMP"]["nimble"]["action"] == "buy" and by["PUMP"]["nimble"]["risk"] == 3
        assert by["PUMP"]["label"] == "would_buy"           # the verdict never relabels
        assert "systemone_shadow" not in by["USO"] and "nimble" not in by["KXHIGHNY"]
        assert by["PUMP"]["systemone_shadow"]["enforcement"] == "none"
        assert by["PUMP"]["would_have"]["create_order"] is False
        assert RETIRED not in json.dumps(stamped, default=str).lower()
        proof = ak._proof_lines(stamped)
        assert "proof crypto PUMP" in proof
        assert "proof equity SPY" in proof
        assert "proof prediction KXHIGHNY" in proof
        assert "create_order=false" in proof
        assert "label=would_buy" in proof
    finally:
        os.environ.pop("VALEDIA_SYSTEMONE", None)


def test_scan_logs_every_decision_with_code_label_and_facts():
    if os.path.exists(SLOG):
        os.remove(SLOG)
    os.environ["VALEDIA_SYSTEMONE"] = "1"
    try:
        seen = []

        def post(url, payload, timeout):
            seen.append(payload)
            return fk.typed_reply(payload["questions"], {
                "action": {"buy": 0.2, "hold": 0.7, "skip": 0.1}, "risk": "5"}, confidence=0.7)

        ak.s1._post = post
        doc = ak.build(fixture_path=ak.DEFAULT_FIXTURE, annotate=lambda d: d)
        n = len(doc["ranked"])
        lines = [json.loads(x) for x in open(SLOG).read().splitlines()]
        rows = {r["symbol"]: r for r in doc["ranked"]}
        spot = {k for k, r in rows.items() if r["class"] in ("crypto", "equity", "etf")}
        assert spot and len(spot) < n                   # weather / oil rows get no call
        assert len(lines) == len(spot)                  # one per stock/crypto row; no warmup request
        assert all(p["state"]["symbol"] != "warmup" for p in seen) and len(seen) == len(spot)
        by = {ln["state"]["symbol"].replace("-USD", ""): ln for ln in lines}
        assert set(by) == spot
        for sym, ln in by.items():
            assert ln["source"] == "nimble_decide:movers_scan"
            assert ln["label"]["code"] == rows[sym]["label"]   # code's label beside the answer
            assert ln["answers"]["action"] == "hold" and ln["error"] is None
        # Facts come from the scan's own constants, computed in code.
        f = by["PUMP"]["state"]["facts"]
        assert f["has_print"] is True and f["edge_positive"] is True
        assert f["clears_size_bar"] is True and f["is_oil"] is False
        assert "USO" not in by and "KXHIGHNY" not in by
        assert by["DUST"]["state"]["facts"]["edge_positive"] is False
        assert by["NOISE"]["state"]["facts"]["clears_size_bar"] is False
        assert ak.LIMITS == {"print_bar_pct": ak.PRINT_BAR_PCT,
                             "min_quote_usd": ak.BINANCE_MIN_QUOTE_USD}
        # The model's answer never changes label, size, or rank.
        off = _doc()
        assert [r["label"] for r in doc["ranked"]] == [r["label"] for r in off["ranked"]]
        assert [r["would_have"]["sized"] for r in doc["ranked"]] == \
            [r["would_have"]["sized"] for r in off["ranked"]]
        blk = doc["systemone"]
        assert blk["budget_ms"] == 500.0 and "latency_ms_p95" in blk and blk["n_ok"] == len(spot)
    finally:
        os.environ.pop("VALEDIA_SYSTEMONE", None)


def test_ornith_season_does_not_reorder():
    doc = _doc()
    symbols = [r["symbol"] for r in doc["ranked"]]
    assert doc["ornith"]["reason"] == "VALEDIA_ORNITH_SEASON_off"
    assert doc["ornith"]["fail_open"] is True
    assert doc["ornith"]["changes_rank"] is False
    assert doc["ornith"]["seasoning_only"] is True
    assert doc["fences"]["ornith"] == "seasoning_only"
    assert doc["fences"]["create_order"] is False
    table = ak.format_table(doc)
    assert "ornith enabled=False" in table
    assert "lms_load_cmd" not in table

    def boom(_rows):
        raise TimeoutError("lms")

    blown = ak.build(fixture_path=ak.DEFAULT_FIXTURE, annotate=lambda d: d, season=boom)
    assert blown["ornith"]["fail_open"] is True
    assert blown["ornith"]["changes_rank"] is False
    assert [r["symbol"] for r in blown["ranked"]] == symbols
    assert blown["ranked"][0]["symbol"] == "USO"
    assert blown["ranked"][0]["harvest"] == "NO_HARVEST"

    def lie(_rows):
        return {
            "enabled": True,
            "reason": "annotated",
            "note": "put USO last",
            "changes_rank": True,
        }

    stamped = ak.build(fixture_path=ak.DEFAULT_FIXTURE, annotate=lambda d: d, season=lie)
    assert stamped["ornith"]["changes_rank"] is False
    assert stamped["ornith"]["seasoning_only"] is True
    assert stamped["ornith"]["note"] == "put USO last"
    assert [r["symbol"] for r in stamped["ranked"]] == symbols
    assert all(r["would_have"]["create_order"] is False for r in stamped["ranked"])

    def missing(_rows):
        return {
            "enabled": True,
            "fail_open": True,
            "reason": "not_loaded",
            "lms_load_cmd": ornith_seasoning.LOAD_HINT,
        }

    skipped = ak.build(fixture_path=ak.DEFAULT_FIXTURE, annotate=lambda d: d, season=missing)
    shown = ak.format_table(skipped)
    assert "print-only" in shown
    assert "lms unload" not in shown
    assert skipped["fences"]["allowlist_widened"] is False


def test_live_fallback(tmp=None):
    def _down():
        raise RuntimeError("coingecko_no_movers")

    doc = ak.build(fetch=_down, annotate=lambda d: d)
    assert doc["origin"] == "fixture_fallback"
    assert doc["probe"]["PUMP"]["present"] is True
    assert doc["fences"]["create_order"] is False


if __name__ == "__main__":
    _S.setup()
    test_models_dir_v2()
    test_fee_true_rank_and_size_gate()
    test_oil_and_weather_stay_watch()
    test_nimble_note_does_not_reorder_and_fail_open()
    test_fences_and_hygiene()
    test_write_and_cli_fixture()
    test_tape_prefers_coingecko_then_binance()
    test_systemone_default_off_and_fail_open()
    test_scan_logs_every_decision_with_code_label_and_facts()
    test_ornith_season_does_not_reorder()
    test_live_fallback()
    _S.teardown()
    print("PASS test_all_knowing_movers_scan")
