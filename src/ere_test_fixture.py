"""Shared ERE v3 test fixture: write valid HEALTH_ONLY state into a models dir.

Mirrors test_etherion_risk.RuntimeTests.setUp so consumers' tests exercise the
canonical reader (read_gate) instead of relying on the retired fail-open
latest_etherion_risk.json behavior. Never touches a live models dir unless the
caller explicitly passes one (tests must pass a temp dir).
"""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path


def write_state(models_dir, *, multiplier=1.0, now=None):
    """Write ere_runtime_config/positions/state so read_gate succeeds.

    multiplier=1.0 -> NORMAL (healthy book); multiplier=0.0 -> HALT.
    Returns the state dict written.
    """
    import ere_runtime as rt
    from etherion_risk import EREConfig
    now = now or datetime.now(timezone.utc)
    policy = {"mode": "HEALTH_ONLY", "all_risk_increasing_readers_migrated": True,
              "universe": ["BTC-USD"], "core": {}}
    pos = {"schema": "ere-positions/1", "asof": now.isoformat(), "complete": True,
           "position_version": "test-only-v1", "nav": 500.0, "weights": {},
           "binary_positions": []}
    cfg = EREConfig()
    good = rt.result_dict(multiplier, now.isoformat(), "test fixture", cfg)
    state = {**good, "results": {"BOOK": dict(good), "BTC-USD": dict(good)},
             "generated_at": now.isoformat(),
             "expires_at": (now + timedelta(seconds=cfg.heartbeat_ttl_s)).isoformat(),
             "risk_level": rt.level_for(multiplier, cfg), "heartbeat": {"status": "OK"},
             "config_digest": rt.digest(policy), "holdings_digest": rt.digest(pos),
             "core_sha256": rt.core_digest(), "mode": "HEALTH_ONLY"}
    os.makedirs(models_dir, exist_ok=True)
    rt.atomic_write(Path(models_dir) / "ere_runtime_config.json", policy)
    rt.atomic_write(Path(models_dir) / "ere_positions.json", pos)
    rt.atomic_write(Path(models_dir) / "ere_state.json", state)
    return state