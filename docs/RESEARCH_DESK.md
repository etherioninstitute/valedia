# The research desk

Four seats in one capped room, filled by the trader's existing bots, and you decide what is kept.

| Seat | Bot (Hermes profile) | Model | Tools | Job |
|---|---|---|---|---|
| Orchestrator | Proposer (`valedia-trading-decider`) | Claude Sonnet (your subscription) | none | Turns your question into a brief |
| Researcher | Scout (`valedia-trading-scout`) | local 27B | web only | Reads sources, reports findings with links |
| Reviewer | Auditor (`valedia-trading-auditor`) | Claude Sonnet | none | Attacks the report against a rubric |
| Librarian | Proposer (`valedia-trading-decider`) | Claude Sonnet | none | Files a report you approved as a short library note |

The seats are assigned in `valedia_trader/agents/team.json` (see `docs/TEAM.md`); there are no bots that exist only for
the desk. The bot that writes the brief is not the one that reviews, and the Scout can do neither. The room shares the
conversation and nothing else.

## Use

```bash
python -m valedia_trader.agents.desk ask "What does published evidence say about X?"
python -m valedia_trader.agents.desk list
python -m valedia_trader.agents.desk show ID
python -m valedia_trader.agents.desk salvage ID --who NAME     # a cut-off room: drop unsourced findings, ask the reviewer once
python -m valedia_trader.agents.desk approve ID --who Burton
python -m valedia_trader.agents.desk reject ID --who Burton --why "sources too thin"
```

`ask` runs the room and stops at `awaiting owner` (the reviewer accepted the report) or `stopped at the
cap` (it did not, and the bots have used their turns). Either way the next move is yours. `approve` gives the
librarian one turn to file the note in `research/library/`; from then on the research agents see its title and
one-sentence takeaway in their prompts as outside reading.

## What the code holds, whatever the bots write

- **The room is capped**: 7 bot turns and 2 returned reports, then it waits for you. Two bots cannot talk all night.
- **A finding needs a link.** A report with an unsourced finding goes back without a review being spent on it.
- **Nothing reaches the library before you approve it by name.** Reports come from web pages nobody here
  controls; your read is the boundary between that text and the agents that propose strategies.
- **The librarian cannot add a source**: a link that is not in the approved report is refused.
- **The desk tests nothing and orders nothing.** An idea in a note is reading. The only way an idea is tested
  is the research loop's gate (locked prediction, sealed period, counted variations).

## Limits

- The reviewer judges whether a claim is supported by the evidence quoted beside it. It does not open
  the links. Open the ones a decision would rest on before you approve.
- Published results cover periods the research agents are not shown. A note is marked as background, and an
  idea drawn from it still has to pass on data it has not seen; the ledger counts every variation as before.
- Working rooms (`research/desk/`) are runtime state and are not committed. Approved notes
  (`research/library/`) are.
- If a run is stopped or a turn hits its hard time limit, the Scout's turn keeps running inside the Hermes gateway and
  keeps the local model busy until it ends (see Known limits in `docs/TEAM.md`).
- One research pass by the Scout on the local model took 39 minutes in the first real run (about four minutes a step).
  A returned report costs another pass. Seating a cloud model as researcher in `team.json` would be much faster; it is
  on the local model so the bulk reading stays free and private.
