# Grok Build next queue (post Slice 21)

## Standing (Burton 2026-09-18)
Use **git + Valedia versions** on every ship. See `VALEDIA-VERSIONING.md`.
Status messages must be truthful and version-stamped.

## After Slice 21 lands
### Slice 22 — Trade-status accuracy fixes (from Slice 21 report)
Messaging/label only + tests. No fence lifts. Stamp messages with `VALEDIA_VERSION` + sha.

### Slice 23 — Versioning bootstrap
- Add `VALEDIA_VERSION` to `valedia_config.py` (align to current tip / Slice 21+)
- Annotated tags `valedia-v*` on Build; mirror policy on Live after fenced deploy
- Helper: `scripts/valedia_version.sh` prints version+sha+branch+dirty for cron/Signal headers
- STATUS always shows version+tag+tip

### Then (from Ponytail Top 10, after Grok agree/disagree)
Cleanup slices only where Grok AGREEd — ERE bind, cron 404s, one Kelly/fee/ledger — still fenced.
