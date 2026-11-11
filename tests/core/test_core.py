from collections import Counter
from datetime import datetime, timedelta, timezone

import pytest

from valedia_trader.core import legacy
from valedia_trader.core.events import EventBus, SimulatedClock


def test_every_legacy_module_has_exactly_one_domain():
    mapping = legacy.domain_map()
    assert len(mapping) >= 200 and set(mapping.values()) <= set(legacy.DOMAINS)
    counts = Counter(mapping.values())
    assert all(counts[d] > 0 for d in legacy.DOMAINS)
    assert mapping["kalshi_executor"] == "venues" and mapping["kelly_sizing"] == "execution_and_risk"
    assert mapping["mirofish_native_run"] == "agents" and mapping["fee_engine"] == "data_and_fees"
    assert mapping["forward_test_ledger"] == "backtest" and mapping["valedia_config"] == "core"
    assert mapping["sports_engine"] == "quant_engine"
    with pytest.raises(KeyError):
        legacy.domain_of("zz_not_a_module")


def test_event_bus_isolates_failures_and_counts():
    t0 = datetime(2026, 1, 2, tzinfo=timezone.utc)
    clock = SimulatedClock(t0)
    bus, seen = EventBus(clock), []
    bus.subscribe("fill", lambda e: seen.append(("fill", e.payload, e.ts)))
    bus.subscribe("fill", lambda e: 1 / 0)
    bus.subscribe("*", lambda e: seen.append(("any", e.topic)))
    clock.advance_to(t0 + timedelta(seconds=5))
    bus.publish("fill", {"qty": 1})
    bus.publish("quote")
    assert seen == [("fill", {"qty": 1}, t0 + timedelta(seconds=5)), ("any", "fill"), ("any", "quote")]
    assert bus.telemetry() == {"published": {"fill": 1, "quote": 1}, "failures": 1}
    assert "ZeroDivisionError" in bus.failures[0][2]
    with pytest.raises(ValueError):
        clock.advance_to(t0)
    with pytest.raises(ValueError):
        SimulatedClock(datetime(2026, 1, 2))
