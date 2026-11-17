# The team

One list of every bot that works on Valedia Trader: `valedia_trader/agents/team.json`.

```bash
python -m valedia_trader.agents.team            # who sits where, and anything that has drifted
python -m valedia_trader.agents.team sync       # write each bot's role file and profile settings from team.json
python -m valedia_trader.agents.team sync --dry-run
```

| Bot | Hermes profile | Model | What it does |
|---|---|---|---|
| Proposer | `valedia-trading-decider` | Claude Sonnet (your subscription) | Proposes ideas in the research round, writes the desk's brief, files approved notes. No panel vote since 2026-11-16 |
| Scout | `valedia-trading-scout` | local 27B | Reads sources for the desk (web tools only there) |
| Auditor | `valedia-trading-auditor` | Claude Sonnet | Skeptic in the research round, vetter in the panel, reviewer at the desk, narrator of the weekly review |
| CTO | `valedia-cto` | local 27B | Builds and repairs the code when you ask; no seat in any trading decision |
| Valedia | `valedia` | local 27B | Your assistant: reports the trader's state and carries your decisions |
| Elephant-1 | none (a local model in LM Studio) | local | The panel's only decider (owner's instruction, 2026-11-16); also votes as a risk vetter |

## How they are kept in step

- **One file assigns the seats.** The research desk, the research round's defaults and the weekly critic read their bots
  from `team.json`. The panel keeps `panel_roster.json` (it also holds local models) and is checked against the team.
- **Every bot carries the same team card** in its Hermes role file (`SOUL.md`): who the others are, the shared rules
  (the gate, nobody orders, you approve the library) and "if it is another bot's job, say whose". For Proposer, Scout and
  Auditor the whole role file is generated. For Valedia and the CTO only the marked team section is; the rest of their
  files is never touched. Old files are backed up in each profile's `backups/` before any change.
- **Nobody judges their own work.** The file is refused if the researcher would brief or review its own report, or if
  the skeptic is the bot whose ideas it attacks.
- **Drift is reported.** `team` and `python -m valedia_trader.doctor` flag a missing profile, a changed model, a role
  file edited by hand, a panel member that is not on the team, or a bot given tools its seat does not need.
- **Profile settings a seat depends on live here too** (a bot's `config`): the Scout's page size (6,000 characters a
  page, so the local model is not handed 90,000 characters at once) and its soft time budget (30 minutes, below the
  desk's one-hour hard stop).

## Handing work to the team from a Hermes chat

The official `advanced-delegation` plugin is installed in the default profile (Claude). Two modes are set up and both
are off until you switch one on in a chat:

```
/delegation model_map --now        Claude leads, up to 3 subagents run on the local 27B (route local_qwen)
/delegation profile_routes --now   Claude hands a task to Scout, CTO, Auditor or Decider as that bot
/delegation default --now          back to normal: subagents use the main model
/delegation                        show the current mode
```

A subagent that runs as a profile uses that profile's model, tools, role file and terminal policy.

## Known limits

- A one-shot Hermes call keeps running inside the gateway after its caller is stopped or times out. A Scout turn that
  is cut off keeps using the local model until it finishes or fails. The page size and time budget above are what
  keep a turn short; if one is stuck, unloading the local model three times ends it.
- The local model answers one bot at a time at full speed. Three local workers, a Scout turn and a panel call at once
  share it and all slow down. A MiroFish run needs it alone (see `docs/mirofish-swarm.md`).
