# The research desk

Three specialist bots in one capped room, and you decide what is kept.

| Bot | Hermes profile | Model | Tools | Job |
|---|---|---|---|---|
| Orchestrator | `valedia-desk-orchestrator` | Claude Sonnet (your subscription) | none | Writes the brief, reviews the report against a rubric |
| Researcher | `valedia-trading-scout` | local 27B | web only | Reads sources, reports findings with links |
| Librarian | `valedia-desk-librarian` | local 27B | none | Files a report you approved as a short library note |

The roster is `valedia_trader/agents/desk_roster.json`. Each bot has its own instructions (`SOUL.md` in its
profile), model and tools. The room shares the conversation and nothing else.

## Use

```bash
python -m valedia_trader.agents.desk ask "What does published evidence say about X?"
python -m valedia_trader.agents.desk list
python -m valedia_trader.agents.desk show ID
python -m valedia_trader.agents.desk approve ID --who Burton
python -m valedia_trader.agents.desk reject ID --who Burton --why "sources too thin"
```

`ask` runs the room and stops at `awaiting owner` (the orchestrator accepted the report) or `stopped at the
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

- The orchestrator judges whether a claim is supported by the evidence quoted beside it. It does not open
  the links. Open the ones a decision would rest on before you approve.
- Published results cover periods the research agents are not shown. A note is marked as background, and an
  idea drawn from it still has to pass on data it has not seen; the ledger counts every variation as before.
- Working rooms (`research/desk/`) are runtime state and are not committed. Approved notes
  (`research/library/`) are.
