"""The team file is the one place a bot's jobs are written; everything else is checked against it."""
import copy
import datetime as dt
import json

import pytest
import yaml

from valedia_trader.agents import team as T

NOW = dt.datetime(2026, 11, 11, 10, 0, 0)


def home_for(team, tmp_path, souls=None):
    """A Hermes home holding a configured profile for every bot on the team."""
    for key, bot in team["bots"].items():
        folder = tmp_path / "profiles" / bot["profile"]
        folder.mkdir(parents=True)
        (folder / "config.yaml").write_text(yaml.safe_dump({"model": {"default": bot["model"]}, **bot.get("config", {})}))
        if souls and key in souls:
            (folder / "SOUL.md").write_text(souls[key])
    return tmp_path


def test_the_real_team_file_is_valid_and_fills_every_seat_from_existing_bots():
    team = T.load_team()
    roster = T.desk_roster(team)
    assert {r: roster[r]["bot"] for r in T.DESK_ROLES} == {"orchestrator": "decider", "researcher": "scout", "reviewer": "auditor", "librarian": "decider"}
    assert roster["researcher"]["profile"] == "valedia-trading-scout" and roster["researcher"]["toolsets"] == "web"
    assert T.backend("proposer", team) == "hermes:valedia-trading-decider" and T.backend("skeptic", team) == "hermes:valedia-trading-auditor"
    assert T.backend("narrator", team) == "hermes:valedia-trading-auditor"
    stages = {row[0] for row in T.table(team)}
    assert stages == {"research desk", "research round", "decision panel", "weekly review", "outside the loop"}
    assert not any("desk-orchestrator" in row[2] or "desk-librarian" in row[2] for row in T.table(team))   # no extra bots


def test_every_bot_gets_the_same_card_and_it_names_them_all():
    team = T.load_team()
    card = T.card(team)
    for bot in team["bots"].values():
        assert f"`{bot['profile']}`" in card
    assert card.startswith(T.START) and card.rstrip().endswith(T.END) and len(card) < 2600      # read on every call: keep it short
    for key in ("decider", "scout", "auditor"):
        text = T.soul(team, key)
        assert card.strip() in text and "Never place, change or cancel an order" in text and "## What you are asked to do" in text
    with pytest.raises(T.TeamError):
        T.soul(team, "valedia")                                                                # hers is not generated


def test_sync_rewrites_generated_files_and_only_the_marked_section_of_the_others(tmp_path):
    team = T.load_team()
    mine = "# Valedia\n\nMy own words stay.\n"
    home = home_for(team, tmp_path, {"valedia": mine, "decider": "# old Paperclip role\n"})
    assert all(r["action"] == "would write" for r in T.sync(team, home, write=False, now=NOW))
    assert (home / "profiles" / "valedia" / "SOUL.md").read_text() == mine                   # a dry run writes nothing
    rows = {r["bot"]: r for r in T.sync(team, home, now=NOW)}
    assert all(r["action"] == "written" for r in rows.values())
    valedia = (home / "profiles" / "valedia" / "SOUL.md").read_text()
    assert valedia.startswith(mine) and valedia.endswith(T.card(team))
    decider = (home / "profiles" / "valedia-trading-decider" / "SOUL.md").read_text()
    assert decider == T.soul(team, "decider") and "Paperclip" not in decider
    assert (home / "profiles" / "valedia-trading-decider" / "backups" / "SOUL.md.before-team-sync-20261111-100000").read_text() == "# old Paperclip role\n"
    assert "backup" not in rows["scout"]                                                       # nothing existed to back up
    assert all(r["action"] == "unchanged" for r in T.sync(team, home, now=NOW))              # and it is stable
    changed = copy.deepcopy(team)
    changed["goal"] = "A new goal."
    T.sync(changed, home, now=NOW)
    again = (home / "profiles" / "valedia" / "SOUL.md").read_text()
    assert again.startswith(mine) and again.count(T.END) == 1 and "A new goal." in again     # the section is replaced, not stacked


def test_check_reports_drift_in_models_role_files_panel_members_and_tools(tmp_path):
    team = T.load_team()
    home = home_for(team, tmp_path)
    T.sync(team, home, now=NOW)
    panel = tmp_path / "panel.json"
    panel.write_text(json.dumps({"members": [{"name": "d", "role": "decider", "backend": "hermes", "profile": "valedia-trading-decider"},
                                             {"name": "local", "role": "decider", "backend": "systemone"}]}))
    assert T.check(team, home, panel) == []
    (home / "profiles" / "valedia-trading-scout" / "config.yaml").write_text("model:\n  default: something-else\n")
    (home / "profiles" / "valedia-trading-auditor" / "SOUL.md").write_text("edited by hand")
    (home / "profiles" / "valedia-cto" / "config.yaml").unlink()
    panel.write_text(json.dumps({"members": [{"name": "stranger", "role": "vetter", "backend": "hermes", "profile": "someone-else"},
                                             {"name": "armed", "role": "decider", "backend": "hermes", "profile": "valedia-trading-decider", "toolsets": "web"},
                                             {"name": "off", "role": "scout", "backend": "hermes", "profile": "nobody", "enabled": False}]}))
    loose = copy.deepcopy(team)
    loose["desk"]["librarian"]["toolsets"] = "file"
    found = T.check(loose, home, panel)
    text = " | ".join(p["problem"] for p in found)
    assert "Scout: the profile's model is something-else" in text and "Auditor: its role file does not match" in text
    assert "CTO: Hermes profile valedia-cto is missing" in text and "stranger" in text and "armed is given tools" in text
    assert "librarian is given tools (file)" in text and "nobody" not in text                # a disabled member is not checked
    assert {p["level"] for p in found} == {"drift"}                                           # the CTO fills no seat: not a failure
    (home / "profiles" / "valedia-trading-scout" / "config.yaml").unlink()
    assert any(p["level"] == "fail" and "Scout" in p["problem"] for p in T.check(team, home, panel))


def test_a_team_that_lets_a_bot_judge_its_own_work_is_refused(tmp_path):
    team = T.load_team()
    for change in (lambda t: t["desk"]["reviewer"].update(bot="scout"), lambda t: t["research_round"].update(skeptic="decider"),
                   lambda t: t["desk"]["orchestrator"].update(bot="nobody"), lambda t: t["bots"]["cto"].update(profile="valedia"),
                   lambda t: t["bots"]["scout"].pop("duties")):
        bad = copy.deepcopy(team)
        change(bad)
        f = tmp_path / "team.json"
        f.write_text(json.dumps(bad))
        with pytest.raises(T.TeamError):
            T.load_team(f)


def test_profile_settings_a_seat_depends_on_are_checked_and_applied(tmp_path):
    team = T.load_team()
    assert team["bots"]["scout"]["config"] == {"web": {"extract_char_limit": 6000}, "agent": {"run_budget_seconds": 1800}}
    home = home_for(team, tmp_path)
    T.sync(team, home, now=NOW)
    scout = home / "profiles" / "valedia-trading-scout" / "config.yaml"
    scout.write_text("model:\n  default: qwen/qwen3.8-27b\nweb:\n  backend: ddg\n")
    panel = tmp_path / "panel.json"
    panel.write_text(json.dumps({"members": []}))
    drift = [p["problem"] for p in T.check(team, home, panel)]
    assert len(drift) == 2 and "web.extract_char_limit is None, team.json expects 6000" in drift[0] and "agent.run_budget_seconds" in drift[1]
    assert T.sync_settings(team, home, write=False, now=NOW)[0]["action"] == "would write settings" and "6000" not in scout.read_text()
    row = T.sync_settings(team, home, now=NOW)[0]
    assert row["settings"] == {"web.extract_char_limit": 6000, "agent.run_budget_seconds": 1800}
    after = yaml.safe_load(scout.read_text())
    assert after == {"model": {"default": "qwen/qwen3.8-27b"}, "web": {"backend": "ddg", "extract_char_limit": 6000},
                     "agent": {"run_budget_seconds": 1800}}                                     # merged, nothing lost
    assert (home / "profiles" / "valedia-trading-scout" / "backups" / "config.yaml.before-team-sync-20261111-100000").is_file()
    assert T.check(team, home, panel) == [] and T.sync_settings(team, home, now=NOW) == []


def test_t44_5_the_hermes_decider_bot_proposes_and_writes_but_no_longer_votes_in_the_panel():
    team = T.load_team()
    bot = team["bots"]["decider"]
    assert bot["profile"] == "valedia-trading-decider" and bot["model"] == "sonnet" and bot["title"] == "Proposer"
    assert "panel" not in bot["card"].lower() and "vote" not in bot["card"].lower()
    assert not any("panel" in d.lower() for d in bot["duties"]) and len(bot["duties"]) == 2
    assert "decide" not in bot["motto"].lower() and "propos" in bot["motto"].lower()
    assert "only decider" in team["local_forecasters"] and "risk vetter" in team["local_forecasters"] and "Elephant-1" in team["local_forecasters"]
    assert team["research_round"]["proposer"] == "decider" and team["desk"]["orchestrator"]["bot"] == "decider" and team["desk"]["librarian"]["bot"] == "decider"
    line = [l for l in T.card(team).splitlines() if "`valedia-trading-decider`" in l][0]
    assert line.startswith("- Proposer (`valedia-trading-decider`):") and "panel" not in line and "votes" not in line
    seats = {(r[0], r[1]) for r in T.table(team) if "valedia-trading-decider" in r[2]}
    assert ("decision panel", "decider") not in seats and ("research round", "proposer (price, macro, fundamentals)") in seats


def test_t44_5_the_real_team_and_roster_show_no_drift_from_the_decider_change(tmp_path):
    team = T.load_team()
    home = home_for(team, tmp_path)
    T.sync(team, home, now=NOW)
    assert T.check(team, home) == []        # against the real panel_roster.json: the Hermes decider is disabled there, the bot is still on the team
