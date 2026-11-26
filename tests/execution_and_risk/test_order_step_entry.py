"""The order step's own entry point (execution_and_risk/order_step_cli.py): the same argv gives the same result through
it and through `research_loop order-step` (refusals included), the old command hands its arguments to the one code
path, and the new entry imports nothing of the research desk, the decision panel or the research round."""
import ast
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest

from valedia_trader.execution_and_risk import order_step
from valedia_trader.execution_and_risk import order_step_cli as osc
from valedia_trader.research_loop import cli, risk_gate

REPO = Path(__file__).resolve().parents[2]
ENTRY = "valedia_trader.execution_and_risk.order_step_cli"
OLD_ENTRY = "valedia_trader.research_loop.__main__"
# what the order step never needs: the research desk, the decision panel, the research round and what only they import
NOT_ON_THE_ORDER_PATH = ("valedia_trader.agents", "valedia_trader.research_loop.cli", "valedia_trader.research_loop.__main__",
                         "valedia_trader.research_loop.research", "valedia_trader.research_loop.gauntlet",
                         "valedia_trader.research_loop.critic", "valedia_trader.research_loop.revision",
                         "valedia_trader.data_and_fees.prices", "valedia_trader.data_and_fees.timesfm_forecaster",
                         "valedia_trader.data_and_fees.venue_registry", "valedia_trader.backtest.walkforward",
                         "valedia_trader.core.decision_model", "valedia_trader.core.legacy")


def _off_path(names):
    return sorted(n for n in names if any(n == p or n.startswith(p + ".") for p in NOT_ON_THE_ORDER_PATH))


# ---- the import closure: at import time (sys.modules of a fresh process) and inside functions (every import statement)

def _imported_by(module: str, run: str = "") -> list[str]:
    code = f"import sys, {module}\n{run}\nprint('\\n'.join(sorted(sys.modules)))"
    done = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]
    return done.stdout.split()


def test_the_new_entry_imports_no_desk_panel_or_research_module(tmp_path):
    seen = _imported_by(ENTRY)                                     # at import time
    assert ENTRY in seen and _off_path(seen) == []
    # and in a run: `--check` without `--live` is refused inside live mode's command, after its imports
    seen = _imported_by(ENTRY, f"assert {ENTRY}.main(['--root', {str(tmp_path)!r}, '--check']) == 2")
    assert {"valedia_trader.execution_and_risk.order_step", "valedia_trader.execution_and_risk.go_live",
            "valedia_trader.venues.webull"} <= set(seen)
    assert _off_path(seen) == []


def test_the_import_check_sees_those_modules_when_they_are_imported():
    seen = _imported_by("valedia_trader.research_loop.cli")       # the research loop's own command line imports them
    assert {"valedia_trader.research_loop.research", "valedia_trader.research_loop.gauntlet"} <= set(_off_path(seen))


def _module_file(mod: str) -> Path | None:
    p = REPO / Path(*mod.split("."))
    if (p / "__init__.py").is_file():
        return p / "__init__.py"
    return p.with_suffix(".py") if p.with_suffix(".py").is_file() else None


def _static_closure(entry: str) -> set[str]:
    """Every valedia_trader module reachable through any import statement (module level or inside a function)."""
    seen, todo = set(), [entry]
    while todo:
        mod = todo.pop()
        parts = mod.split(".")
        for i in range(1, len(parts) + 1):
            sub = ".".join(parts[:i])
            f = _module_file(sub)
            if f is None or sub in seen:
                continue
            seen.add(sub)
            base = sub if f.name == "__init__.py" else sub.rpartition(".")[0]
            for n in ast.walk(ast.parse(f.read_text())):
                if isinstance(n, ast.Import):
                    todo += [a.name for a in n.names]
                elif isinstance(n, ast.ImportFrom):
                    src = n.module
                    if n.level:
                        up = base.split(".")[:len(base.split(".")) - (n.level - 1)]
                        src = ".".join(up + ([n.module] if n.module else []))
                    todo += [src] + [f"{src}.{a.name}" for a in n.names]
    return {m for m in seen if m.startswith("valedia_trader")}


def test_no_function_of_the_new_entry_reaches_a_desk_panel_or_research_module():
    closure = _static_closure(ENTRY)
    assert {ENTRY, "valedia_trader.execution_and_risk.order_step", "valedia_trader.venues.webull"} <= closure
    assert _off_path(closure) == []


def test_the_static_closure_sees_the_old_entrys_desk_and_panel():
    off = _off_path(_static_closure(OLD_ENTRY))
    assert {"valedia_trader.agents.desk", "valedia_trader.agents.panel", "valedia_trader.research_loop.research"} <= set(off)


# ---- the old command hands its arguments to the one code path

def test_research_loop_order_step_runs_the_new_entrys_code(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(osc, "run", lambda args: seen.append(vars(args)) or 7)
    assert cli.main(["--root", str(tmp_path), "order-step", "--shared", "--fractional"]) == 7
    assert osc.main(["--root", str(tmp_path), "--shared", "--fractional"]) == 7
    old, new = seen
    assert old.pop("cmd") == "order-step" and old == new          # the same arguments, every flag, the same values


EVERY_FLAG = [["--capital", "5"], ["--fractional"], ["--shared"], ["--propose"], ["--approve", "123456"], ["--check"],
              ["--live"], ["--owner-entry", "split"], ["--venue", "v"], ["--amount", "1.00"], ["--symbol", "AAA"],
              ["--units", "2"], ["--ratio", "3"], ["--mark", "AAA=1"], ["--who", "o"], ["--note", "n"], ["--confirm", "C"],
              ["--owner-entry-rejournal", "4"], ["--hash", "h"], ["--accept-history", "2026-11-12"], ["--data", "d"]]


@pytest.mark.parametrize("flag", EVERY_FLAG, ids=[f[0] for f in EVERY_FLAG])
def test_every_flag_reads_the_same_through_both_entries(monkeypatch, tmp_path, flag):
    seen = []
    monkeypatch.setattr(osc, "run", lambda args: seen.append(vars(args)) or 0)
    if flag[0] == "--data":                                    # the research loop's own option, before the command
        cli.main(["--root", str(tmp_path), *flag, "order-step"])
    else:
        cli.main(["--root", str(tmp_path), "order-step", *flag])
    osc.main(["--root", str(tmp_path), *flag])
    old, new = seen
    assert old.pop("cmd") == "order-step" and old == new


@pytest.mark.parametrize("argv", [["--refresh"], ["--alpaca"], ["--liv"], ["--sha"], ["--propose", "--check"],
                                  ["--owner-entry", "gift"]])
def test_what_the_old_command_refuses_to_read_the_new_entry_refuses_too(monkeypatch, tmp_path, argv):
    monkeypatch.setattr(osc, "run", lambda args: pytest.fail("ran"))
    for call in (lambda: cli.main(["--root", str(tmp_path), "order-step", *argv]),
                 lambda: osc.main(["--root", str(tmp_path), *argv])):
        with pytest.raises(SystemExit) as exc:
            call()
        assert exc.value.code == 2


# ---- the same argv, the same result, through both entries

class _Venue:
    """A stand-in for Webull's test environment: no network."""
    environment = "sandbox"
    name = "standin-sandbox"

    def __init__(self):
        self.calls, self.book = [], {}

    def positions(self):
        self.calls.append("positions")
        return {}

    def cash(self):
        self.calls.append("cash")
        return {"cash": 1_000_000.0}

    def submit(self, symbol, units, tag, price=None, limit_price=None, session="CORE"):
        self.calls.append("submit")
        self.book[tag] = {"symbol": symbol, "units": units, "limit": limit_price, "state": "open"}
        return {"status": "accepted"}

    def order_status(self, tag):
        self.calls.append("order_status")
        if tag not in self.book:
            return None
        return {"state": self.book[tag]["state"], "filled_units": 0.0, "avg_price": None, "fee": None,
                "venue_order_id": None, "raw_status": None}

    def cancel(self, tag):
        self.calls.append("cancel")
        self.book[tag]["state"] = "cancelled"
        return {"status": "cancel requested"}


NOW = pd.Timestamp("2026-11-13 10:00", tz="America/New_York").timestamp()


@pytest.fixture
def template(tmp_path):
    t = tmp_path / "template"
    (t / "config").mkdir(parents=True)
    (t / "config" / "risk.json").write_text(json.dumps({"max_daily_loss_pct": 50.0, "max_drawdown_pct": 50.0,
                                                        "max_position_pct": 0.6, "max_open_positions": 5}))
    (t / "journal").mkdir()
    (t / "journal" / "paper_state.json").write_text(json.dumps({
        "starting_cash": 100_000.0, "cash": 98_000.0, "positions": {"a": {"AAA": {"units": 4.0, "cost": 500.0}}},
        "pending": [{"strategy": "a", "version": "1", "symbol": "AAA", "units": 1.5, "decided_on": "2026-11-12",
                     "reduces_risk": False, "reasoning": "r", "conditions": None}],
        "history": [{"day": "2026-11-12", "equity": 100_000.0, "capital_per_strategy": 100_000.0}],
        "peak_equity": 100_000.0, "last_day": "2026-11-12", "realized": {}, "panel_cases": []}))
    return t


_RANDOM = [(re.compile(r"os-\d{8}-\d{6}-[0-9a-f]{4,16}"), "os-BATCH"),          # the batch id's random tail
           (re.compile(r"ticket number: \d{6}"), "ticket number: NNNNNN"),
           (re.compile(r"(--confirm |code: )\d{6}"), r"\1NNNNNN"),            # an owner's entry's confirm code
           (re.compile(r'"(ticket|code|ticket_ends)": "\d+"'), r'"\1": "N"')]


def _plain(text: str) -> str:
    for pattern, said in _RANDOM:
        text = pattern.sub(said, text)
    return text


def _snapshot(root: Path) -> dict:
    files = {}
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        files[rel] = _plain(p.read_text()) if p.is_file() and p.suffix in (".jsonl", ".json") else p.is_dir()
    return files


def _through(entry, root, template, argv, capsys):
    if root.exists():
        shutil.rmtree(root)
    shutil.copytree(template, root)
    capsys.readouterr()
    try:
        code = (cli.main(["--root", str(root), "order-step", *argv]) if entry == "old"
                else osc.main(["--root", str(root), *argv]))
    except SystemExit as exc:                                          # the parser's own refusals
        code = ("exit", exc.code)
    out = capsys.readouterr()
    return code, _plain(out.out), _plain(out.err), _snapshot(root)


SCENARIOS = {                                       # argv: the exit code both entries give
    "a run that sends": ([], 0),
    "the job's run (--shared, no opening entry)": (["--shared"], 2),
    "fractional with a capital": (["--fractional", "--capital", "200000"], 0),
    "propose": (["--propose"], 2),
    "approve a number never issued": (["--approve", "123456"], 2),
    "--check without --live": (["--check"], 2),
    "live without --shared": (["--live", "--propose"], 2),
    "live, shared, with a capital": (["--live", "--shared", "--propose", "--capital", "1000"], 2),
    "live check from another root": (["--live", "--check"], 2),
    "an owner's entry with no venue": (["--owner-entry", "cash_in", "--amount", "5", "--who", "o", "--note", "n"], 2),
    "an owner's entry preview": (["--owner-entry", "cash_in", "--amount", "5.00", "--venue", "standin-sandbox", "--who", "o",
                                  "--note", "n"], 0),
    "the live repair outside live mode": (["--accept-history", "2026-11-12", "--who", "o", "--note", "n"], 2),
    "a prefix of --live": (["--liv"], ("exit", 2)),
    "propose and approve together": (["--propose", "--approve", "123456"], ("exit", 2)),
}


@pytest.mark.parametrize("argv,code", list(SCENARIOS.values()), ids=list(SCENARIOS))
def test_the_same_argv_gives_the_same_result_through_both_entries(template, tmp_path, monkeypatch, capsys, argv, code):
    monkeypatch.setattr(osc, "_order_step_venue", _Venue)                    # a fresh stand-in each run
    monkeypatch.setattr(osc, "_order_step_environment", lambda environ=None: "sandbox")   # never the secrets file
    monkeypatch.setattr(osc, "_attended", lambda: False)
    monkeypatch.setattr(order_step, "wall_clock", lambda: NOW)
    monkeypatch.setattr(order_step, "minute_quotes", lambda symbols: {s: (500.0, NOW - 60) for s in symbols})
    monkeypatch.setattr(risk_gate, "HALT_FILE", tmp_path / "no-halt.json")
    root = tmp_path / "root"
    old = _through("old", root, template, argv, capsys)
    new = _through("new", root, template, argv, capsys)
    assert old[0] == new[0], (old[0], new[0], old[2], new[2])
    if code is not None:
        assert old[0] == code, old
    assert old[1] == new[1]
    if not (isinstance(old[0], tuple) and old[0][0] == "exit"):            # argparse's usage line names its own program
        assert old[2] == new[2]
    assert old[3] == new[3]


@pytest.mark.parametrize("how", ["environment", "live name", "account class", "events"])
def test_the_refusals_before_any_venue_is_built_are_the_same_through_both_entries(template, tmp_path, monkeypatch, capsys,
                                                                                    how):
    from valedia_trader.core import secrets
    if how == "environment":
        monkeypatch.setattr(osc, "_order_step_environment", lambda environ=None: "production")
    else:
        monkeypatch.setattr(osc, "_order_step_environment", lambda environ=None: "sandbox")
    if how == "live name":
        monkeypatch.setattr(osc, "_order_step_live_names", lambda environ=None: ["WEBULL_LIVE_APP_KEY"])
    if how == "account class":
        monkeypatch.delenv("WEBULL_ACCOUNT_CLASS", raising=False)
        monkeypatch.setattr(secrets, "load_env", lambda *a, **k: None)          # never a file
        monkeypatch.setattr("valedia_trader.venues.webull.WebullVenue.from_secrets",
                            classmethod(lambda cls: pytest.fail("a venue was built")))
    elif how == "events":
        class Events(_Venue):
            name = "standin-events"
        monkeypatch.setattr(osc, "_order_step_venue", Events)
    else:
        monkeypatch.setattr(osc, "_order_step_venue", lambda: pytest.fail("a venue was built"))
    root = tmp_path / "root"
    old = _through("old", root, template, [], capsys)
    new = _through("new", root, template, [], capsys)
    assert old == new
    code, out, err, files = new
    assert code == 2 and out == "" and err.startswith("refused:") and len(err.strip().splitlines()) == 1, new
    assert set(files) == set(_snapshot(template))                             # nothing made


def test_a_run_reaches_the_venue_through_the_new_entry(template, tmp_path, monkeypatch, capsys):
    """The comparison above is not of two runs that both did nothing: the new entry's plain run sends the order."""
    built = []
    monkeypatch.setattr(osc, "_order_step_venue", lambda: built.append(_Venue()) or built[-1])
    monkeypatch.setattr(osc, "_order_step_environment", lambda environ=None: "sandbox")
    monkeypatch.setattr(order_step, "wall_clock", lambda: NOW)
    monkeypatch.setattr(order_step, "minute_quotes", lambda symbols: {s: (500.0, NOW - 60) for s in symbols})
    monkeypatch.setattr(risk_gate, "HALT_FILE", tmp_path / "no-halt.json")
    code, out, err, files = _through("new", tmp_path / "root", template, [], capsys)
    summary = json.loads(out)
    assert code == 0 and summary["status"] == "done" and summary["orders_sent"] == 1, (out, err)
    assert built[0].calls.count("submit") == 1


def test_the_job_starts_the_new_entry_as_a_module(tmp_path):
    """`python -m valedia_trader.execution_and_risk.order_step_cli` is a command: --help prints the order step's flags."""
    done = subprocess.run([sys.executable, "-m", ENTRY, "--help"], cwd=REPO, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0 and "--shared" in done.stdout and "--accept-history" in done.stdout, done.stderr
