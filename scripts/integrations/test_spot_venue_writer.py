#!/usr/bin/env python3
"""Spot BUY paths honor valedia_config.venue_writer; exits do not; DCA retries when nothing ACKed.

Fakes only: no network, no keys, no orders. Run: python3 test_spot_venue_writer.py
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import test_nimble_wiring_spot as tw  # noqa: E402  (reuses its fake run_brain / run_dca harness)
import nimble_wiring_spot as nws  # noqa: E402
import strategy_modules as sm  # noqa: E402
import valedia_config as vc  # noqa: E402

NONE = {"cb_buy": [], "alp_buy": []}


class Writers:
    """Temporarily set VENUE_WRITER entries; restores the real values on exit."""

    def __init__(self, **w):
        self.w, self.saved = w, dict(vc.VENUE_WRITER)

    def __enter__(self):
        vc.VENUE_WRITER.update(self.w)

    def __exit__(self, *a):
        vc.VENUE_WRITER.clear()
        vc.VENUE_WRITER.update(self.saved)


def test_current_writers_are_valedia():
    assert vc.venue_writer("coinbase") == "valedia" and vc.venue_writer("alpaca") == "valedia"
    assert nws.venue_buy_allowed("coinbase") and nws.venue_buy_allowed("alpaca")
    assert not nws.venue_buy_allowed("kraken") and not nws.venue_buy_allowed("nope")


def test_unified_brain_buy_blocked_by_writer():
    tw.setup(on=False)
    base_rpt, base = tw.run_brain(tw.BOTH)
    assert base["cb_buy"] and base["alp_buy"] == ["SPY"] and "SKIP_VENUE_WRITER" not in base_rpt  # valedia: unchanged
    for w, blocked in ((dict(coinbase="none"), ("cb_buy",)), (dict(alpaca="none"), ("alp_buy",)),
                       (dict(coinbase="none", alpaca="none"), ("cb_buy", "alp_buy")),
                       (dict(coinbase="nautilus"), ("cb_buy",))):
        with Writers(**w):
            rpt, got = tw.run_brain(tw.BOTH)
        assert rpt.count("SKIP_VENUE_WRITER") == len(blocked), rpt
        for k in ("cb_buy", "alp_buy"):
            assert (got[k] == []) is (k in blocked), (w, k, got)


def test_unified_brain_exits_unaffected():
    tw.setup(on=False)
    with Writers(coinbase="none", alpaca="none"):
        rpt, got = tw.run_brain(tw.BOTH, fused=30.0, has_pos=True)
    assert got["cb_sell"] == ["BTC-USD"] and got["alp_sell"] == ["SPY"], got
    assert got["cb_buy"] == [] and got["alp_buy"] == []


def test_unified_brain_dca_blocked_by_writer():
    tw.setup(on=False)
    base = tw.run_brain({}, dca_due=True)[1]
    assert len(base["cb_buy"]) == 4
    with Writers(coinbase="none"):
        rpt, got = tw.run_brain({}, dca_due=True)
    assert got["cb_buy"] == [] and "SKIP_VENUE_WRITER" in rpt, rpt
    with Writers(alpaca="none"):  # alpaca writer is irrelevant to coinbase DCA
        assert tw.run_brain({}, dca_due=True)[1] == base


def test_dca_module_blocked_by_writer():
    tw.setup(on=False)
    out, base = tw.run_dca()
    assert base == {"cb_buy": ["BTC-USD"], "alp_buy": ["SPY"]} and "SKIP_VENUE_WRITER" not in out
    for w, want in ((dict(coinbase="none"), {"cb_buy": [], "alp_buy": ["SPY"]}),
                    (dict(alpaca="none"), {"cb_buy": ["BTC-USD"], "alp_buy": []}),
                    (dict(coinbase="none", alpaca="none"), NONE)):
        with Writers(**w):
            out, got = tw.run_dca()
        assert got == want and out.count("SKIP_VENUE_WRITER") == len(w), (w, got, out)


def test_wrap_buy_blocks_before_nimble_and_buy():
    placed = []
    buy = nws.wrap_buy(lambda p, a: placed.append(p) or {"success": True}, source="t")
    with Writers(coinbase="none"):
        try:
            buy("BTC-USD", 5)
            raise AssertionError("expected VenueWriterSkip")
        except nws.VenueWriterSkip as e:
            assert "SKIP_VENUE_WRITER" in str(e)
    assert placed == []
    tw.setup(on=False)
    assert buy("BTC-USD", 5) == {"success": True} and placed == ["BTC-USD"]


def _engine(path):
    sm.DCAEngine.STATE_FILE = path
    return sm.DCAEngine(weekly_budget=10, assets={"BTC-USD": 0.5, "ETH-USD": 0.5})


def test_dca_engine_retries_when_nothing_acked():
    saved = sm.DCAEngine.STATE_FILE
    path = os.path.join(tempfile.mkdtemp(prefix="dcaeng_"), "state.json")
    json.dump({"last_buy": None, "total_invested": 0, "buys": []}, open(path, "w"))
    try:
        # all vetoed (SKIP_NIMBLE): last_buy stays None, still due
        def vetoed(p, a):
            raise nws.NimbleSkip("SKIP_NIMBLE veto_model_skip")
        acts = _engine(path).execute(vetoed, lambda: 100.0)
        assert _engine(path).state["last_buy"] is None and _engine(path).should_buy()
        assert any("SKIP_NIMBLE" in a for a in acts) and not any(a.startswith("DCA: bought") for a in acts)
        # venue not acking (rejected): also retries
        _engine(path).execute(lambda p, a: {"success": False, "error": "rejected"}, lambda: 100.0)
        assert _engine(path).state["last_buy"] is None and _engine(path).state["buys"] == []
        # writer none: no buy call at all, state untouched
        placed = []
        with Writers(coinbase="none"):
            acts = _engine(path).execute(lambda p, a: placed.append(p) or {"success": True}, lambda: 100.0)
        assert placed == [] and acts == ["DCA: SKIP_VENUE_WRITER coinbase writer!=valedia"]
        assert _engine(path).should_buy() and _engine(path).state["last_buy"] is None
        # one of two acked: advances, only the acked buy is recorded
        acts = _engine(path).execute(
            lambda p, a: {"success": True} if p == "ETH-USD" else {"success": False}, lambda: 100.0)
        st = _engine(path).state
        assert st["last_buy"] is not None and [b["pair"] for b in st["buys"]] == ["ETH-USD"]
        assert not _engine(path).should_buy() and st["total_invested"] == 5.0
        assert sum(a.startswith("DCA: bought") for a in acts) == 1
        # the weekly gate still holds after an ack
        assert _engine(path).execute(lambda p, a: 1 / 0, lambda: 100.0)[0].startswith("DCA: next buy in")
    finally:
        sm.DCAEngine.STATE_FILE = saved


def test_unified_brain_dca_vetoed_week_is_a_non_action():
    tw.setup("veto", post=tw.Nim("skip"))
    rpt, got = tw.run_brain({}, dca_due=True)
    assert got["cb_buy"] == [] and rpt.count("SKIP_NIMBLE") == 4 and "no buy acked" in rpt
    assert "NO NEW TRADES" in rpt  # the retry note is not counted as an action
    tw.setup(on=False)


def main():
    tw._S.setup()
    try:
        for name, fn in sorted(globals().items()):
            if name.startswith("test_") and callable(fn) and fn.__module__ == __name__:
                fn()
                print("ok", name)
    finally:
        tw.setup(on=False)
        tw._S.teardown()
    print("PASS test_spot_venue_writer")


if __name__ == "__main__":
    main()
