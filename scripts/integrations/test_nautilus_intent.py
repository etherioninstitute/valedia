#!/usr/bin/env python3
"""Nautilus Intent seam — Phase 0 breakage guards.

No network, no keys, no nautilus_trader, no writes to the live ledger.
Run: python3 test_nautilus_intent.py
"""
import os
import tempfile

import forward_test_ledger as ftl
import nautilus_fill_sink as sink
import nautilus_intent as ni
import valedia_config as vc


def _gates(**over):
    g = {
        "ere_level": "NORMAL",
        "fee_ok": True,
        "category_ok": True,
        "blacklist_ok": True,
        "exec_dry_run": False,
        "live_flag": True,
    }
    g.update(over)
    return g


def _intent(**over):
    kw = dict(
        strategy="UNIFIED_BRAIN",
        venue="coinbase",
        instrument="BTC-USD",
        side="buy",
        order_type="market",
        tif="IOC",
        qty=0.001,
        max_notional=50.0,
        max_qty=0.01,
        gates=_gates(),
        ledger_source="live",
    )
    kw.update(over)
    return ni.make_intent(**kw)


def _restore_flags():
    vc.NAUTILUS_ENABLED = False
    vc.NAUTILUS_LIVE = False
    vc.VENUE_WRITER["coinbase"] = "valedia"


def run():
    _restore_flags()

    # 0. Default lock: NT off; Coinbase writer is Valedia, not Nautilus.
    assert vc.NAUTILUS_ENABLED is False
    assert vc.NAUTILUS_LIVE is False
    assert vc.venue_writer("coinbase") == "valedia"
    assert vc.nautilus_may_dispatch("coinbase") is False
    assert vc.nautilus_live_fills_allowed("coinbase") is False
    try:
        vc.assert_exclusive_writer("coinbase", "nautilus")
        raise AssertionError("nautilus must not own coinbase")
    except vc.ExclusiveWriterError:
        pass
    vc.assert_exclusive_writer("coinbase", "valedia")

    # 1. Ungated Intent cannot dispatch (missing gates fail-closed).
    ungated = _intent(gates={})
    d = ni.validate_intent(ungated, dispatch=True)
    assert d.dispatchable is False and d.status == "refused", d
    assert "missing" in d.reason

    # 2. False fee gate cannot dispatch.
    d = ni.validate_intent(_intent(gates=_gates(fee_ok=False)), dispatch=True)
    assert d.dispatchable is False and d.status == "refused", d

    # 3. ERE HALT / DANGER cannot dispatch.
    d = ni.validate_intent(_intent(gates=_gates(ere_level="HALT")), dispatch=True)
    assert d.dispatchable is False and d.status == "refused", d
    d = ni.validate_intent(_intent(gates=_gates(ere_level="DANGER")), dispatch=True)
    assert d.dispatchable is False and d.status == "refused", d

    # 4. Fully gated live Intent still cannot dispatch while NAUTILUS_ENABLED=False.
    gated = _intent()
    d = ni.validate_intent(gated, dispatch=True)
    assert d.dispatchable is False, d
    assert d.status == "held", d
    assert "NAUTILUS_ENABLED" in d.reason or "source=live" in d.reason

    fd, qpath = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    try:
        rec = ni.enqueue_intent(gated, dispatch=True, queue_path=qpath)
        assert rec["dispatchable"] is False, rec
        assert rec["status"] in ("held", "refused", "dry_run")
        with open(qpath) as f:
            line = f.read().strip()
        assert line and '"dispatchable": false' in line.lower().replace("False", "false")
    finally:
        os.remove(qpath)

    # 5. exec_dry_run → dry_run, never dispatchable.
    d = ni.validate_intent(_intent(gates=_gates(exec_dry_run=True)), dispatch=True)
    assert d.dispatchable is False and d.status == "dry_run", d

    # 6. qty > max_qty refused (no silent clip-up).
    d = ni.validate_intent(_intent(qty=1.0, max_qty=0.01), dispatch=True)
    assert d.dispatchable is False and d.status == "refused"
    assert "max_qty" in d.reason

    # 7. both qty and notional → refused; neither → refused.
    d = ni.validate_intent(_intent(qty=0.001, notional=10.0), dispatch=True)
    assert d.status == "refused" and "exactly one" in d.reason
    d = ni.validate_intent(_intent(qty=None, notional=None), dispatch=True)
    assert d.status == "refused" and "exactly one" in d.reason

    # 8. Dual-write guard: even if NT is enabled, Valedia-owned venue cannot dispatch.
    vc.NAUTILUS_ENABLED = True
    vc.NAUTILUS_LIVE = True
    vc.VENUE_WRITER["coinbase"] = "valedia"
    d = ni.validate_intent(_intent(), dispatch=True)
    assert d.dispatchable is False, d
    assert "writer" in d.reason
    _restore_flags()

    # 9. Dispatch becomes legal only with ENABLED + writer=nautilus + gates.
    vc.NAUTILUS_ENABLED = True
    vc.NAUTILUS_LIVE = True
    vc.VENUE_WRITER["coinbase"] = "nautilus"
    d = ni.validate_intent(_intent(), dispatch=True)
    assert d.dispatchable is True and d.status == "dispatchable", d
    _restore_flags()

    # 10. Size rounding never goes up.
    assert ni.round_size_down(1.09, 0.1) == 1.0
    assert ni.round_size_down(1.00, 0.1) == 1.0
    assert ni.round_size_down(0.0015, 0.001) <= 0.0015

    # 11. Fill sink: live refused by default (ENABLED=False, LIVE=False).
    booked = []

    def _rec(*a, **k):
        booked.append((a, k))
        return "ft_test"

    try:
        sink.apply_fill(_intent(), {"price": 100.0, "qty": 0.001, "source": "live"},
                        recorder=_rec)
        raise AssertionError("ungated-live fill must be refused")
    except sink.FillRefused as e:
        assert e.code == "live_denied", e
    assert booked == []

    # 12. Fill sink: ENABLED without LIVE still refuses live.
    vc.NAUTILUS_ENABLED = True
    vc.VENUE_WRITER["coinbase"] = "nautilus"
    try:
        sink.apply_fill(_intent(), {"price": 100.0, "qty": 0.001, "source": "live"},
                        recorder=_rec)
        raise AssertionError("live fill must be refused without NAUTILUS_LIVE")
    except sink.FillRefused as e:
        assert e.code == "live_denied", e
    assert booked == []
    _restore_flags()

    # 13. Fill sink: live_flag false refuses live even with all NT flags on.
    vc.NAUTILUS_ENABLED = True
    vc.NAUTILUS_LIVE = True
    vc.VENUE_WRITER["coinbase"] = "nautilus"
    try:
        sink.apply_fill(
            _intent(gates=_gates(live_flag=False), ledger_source="paper"),
            {"price": 100.0, "qty": 0.001, "source": "live"},
            recorder=_rec,
        )
        raise AssertionError("live_flag false must refuse live")
    except sink.FillRefused as e:
        assert e.code == "live_denied", e
    _restore_flags()

    # 14. Ungated intent fills refused (not booked as live, not booked at all).
    try:
        sink.apply_fill(_intent(gates={}), {"price": 100.0, "qty": 0.001, "source": "sandbox"},
                        recorder=_rec)
        raise AssertionError("ungated fill must be refused")
    except sink.FillRefused as e:
        assert e.code == "ungated", e

    # 15. Sandbox fill records as nautilus_sandbox (never live) without NT live flags.
    booked.clear()
    out = sink.apply_fill(
        _intent(ledger_source="sandbox", gates=_gates(live_flag=False)),
        {"price": 100.0, "qty": 0.001},
        recorder=_rec,
    )
    assert out["recorded"] is True and out["source"] == "nautilus_sandbox"
    assert booked and booked[0][1]["source"] == "nautilus_sandbox"
    assert booked[0][1]["source"] != "live"

    # 16. Overfill vs max_qty refused (no round-up).
    try:
        sink.apply_fill(_intent(), {"price": 100.0, "qty": 0.5, "source": "sandbox"},
                        recorder=_rec)
        raise AssertionError("overfill must be refused")
    except sink.FillRefused as e:
        assert e.code == "overfill", e

    # 17. real_track_record / open_position_count exclude sandbox.
    recs = [
        {"type": "entry", "id": "e1", "source": "live", "ts": "2026-09-01T00:00:00+00:00"},
        {"type": "settlement", "entry_id": "e1", "won": True, "realized_pnl_net": 1.0,
         "source": "live", "strategy": "X"},
        {"type": "entry", "id": "e2", "source": "nautilus_sandbox",
         "ts": "2026-09-01T00:00:00+00:00"},
        {"type": "settlement", "entry_id": "e2", "won": True, "realized_pnl_net": 99.0,
         "source": "nautilus_sandbox", "strategy": "X"},
        {"type": "entry", "id": "e3", "source": "paper", "ts": "2026-09-01T00:00:00+00:00"},
        {"type": "settlement", "entry_id": "e3", "won": True, "realized_pnl_net": 5.0,
         "source": "paper", "strategy": "X"},
    ]
    orig_read = ftl._read
    ftl._read = lambda: recs
    try:
        tr = ftl.real_track_record()
        assert tr["n"] == 1, tr
        assert tr["net"] == 1.0, tr
        # open: e1 live still open? no, settled. e2/e3 non-real. → 0
        assert ftl.open_position_count() == 0
        recs.append({"type": "entry", "id": "e4", "source": "live",
                     "ts": "2026-09-14T00:00:00+00:00"})
        recs.append({"type": "entry", "id": "e5", "source": "sandbox",
                     "ts": "2026-09-14T00:00:00+00:00"})
        assert ftl.open_position_count() == 1
    finally:
        ftl._read = orig_read

    _restore_flags()
    print("nautilus-intent seam checks: PASS")


if __name__ == "__main__":
    run()
