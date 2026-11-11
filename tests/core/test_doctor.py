import json

import numpy as np
import pandas as pd

from valedia_trader import doctor as D
from valedia_trader.research_loop import LoopPaths, risk_gate


def _paths(tmp_path):
    p = LoopPaths(tmp_path)
    for d in (p.config, p.journal, p.memory):
        d.mkdir(parents=True, exist_ok=True)
    return p


def test_an_empty_folder_fails_loudly_and_nothing_raises(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(D, "_get", lambda *a, **k: (_ for _ in ()).throw(OSError("no server")))
    rows = {r["check"]: r for r in D.run(tmp_path)}
    assert set(rows) == set(D.CHECKS)
    assert rows["price history"]["status"] == D.FAIL and rows["hypothesis ledger"]["status"] == D.FAIL
    assert rows["risk limits"]["status"] == D.FAIL and "every order is blocked" in rows["risk limits"]["detail"]
    assert rows["local models"]["status"] == D.FAIL and "not reachable" in rows["local models"]["detail"]
    assert rows["quant sheet"]["status"] == D.OK                      # the engine does not depend on the folder
    assert D.main(["--root", str(tmp_path)]) == 1                     # a failing part makes the command fail
    assert "failing" in capsys.readouterr().out
    assert D.main(["--root", str(tmp_path), "--json"]) == 1
    assert {r["status"] for r in json.loads(capsys.readouterr().out)} <= {D.OK, D.ATTENTION, D.FAIL}


def test_risk_limits_and_kill_switch_states(tmp_path):
    p = _paths(tmp_path)
    risk_gate.write_default_limits(p.risk_limits)
    status, detail = D.check_risk_limits(p)
    assert status == D.ATTENTION and "example limits" in detail       # the guide's numbers are not the owner's
    p.risk_limits.write_text(json.dumps({**risk_gate.DEFAULT_LIMITS, "max_drawdown_pct": 6.0}))
    assert D.check_risk_limits(p)[0] == D.OK
    assert D.check_kill_switch(p) == (D.OK, "armed; an order with no limits supplied is refused")
    (p.journal / "kill_switch.json").write_text("{}")
    status, detail = D.check_kill_switch(p)
    assert status == D.ATTENTION and "latch is set" in detail


def test_data_age_and_missing_assets(tmp_path):
    from valedia_trader.data_and_fees.prices import UNIVERSE
    p = _paths(tmp_path); d = tmp_path / "artifacts" / "data"; d.mkdir(parents=True)
    idx = pd.bdate_range(end=pd.Timestamp.now().normalize(), periods=30)
    pd.DataFrame(np.ones((30, len(UNIVERSE))), index=idx, columns=list(UNIVERSE)).to_parquet(d / "close.parquet")
    assert D.check_data(p)[0] == D.OK
    old = pd.bdate_range(end=pd.Timestamp.now().normalize() - pd.Timedelta(days=20), periods=30)
    pd.DataFrame(np.ones((30, len(UNIVERSE))), index=old, columns=list(UNIVERSE)).to_parquet(d / "close.parquet")
    status, detail = D.check_data(p)
    assert status == D.ATTENTION and "refresh" in detail
    pd.DataFrame(np.ones((30, 2)), index=idx, columns=list(UNIVERSE[:2])).to_parquet(d / "close.parquet")
    assert D.check_data(p)[0] == D.FAIL


def test_a_broken_check_is_reported_not_raised(tmp_path, monkeypatch):
    monkeypatch.setitem(D.CHECKS, "repository", lambda paths: (_ for _ in ()).throw(RuntimeError("boom")))
    row = D.run(tmp_path, only=("repository",))[0]
    assert row["status"] == D.FAIL and "the check itself failed: RuntimeError: boom" in row["detail"]


def test_schedule_check_names_each_job_and_fails_on_a_bad_exit(monkeypatch, tmp_path):
    import subprocess
    from types import SimpleNamespace
    from valedia_trader import doctor as D
    from valedia_trader.research_loop import LoopPaths

    def launchd(listing):
        monkeypatch.setattr(subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=listing))
        return D.check_schedule(LoopPaths(tmp_path))

    monkeypatch.setattr(D.Path, "home", classmethod(lambda cls: tmp_path))
    status, text = launchd("-\t0\tcom.valedia.research-loop.weekly\n-\t0\tcom.valedia.paper.daily\n")
    assert status == D.OK and "weekly cycle loaded" in text and "daily paper step loaded" in text and "has not run yet" in text
    status, text = launchd("-\t0\tcom.valedia.research-loop.weekly\n")
    assert status == D.ATTENTION and "daily paper step is not loaded" in text
    status, _ = launchd("-\t0\tcom.valedia.research-loop.weekly\n-\t1\tcom.valedia.paper.daily\n")
    assert status == D.FAIL
    status, _ = launchd("-\t0\tcom.example.research-loop.weekly.other\n")
    assert status == D.ATTENTION
