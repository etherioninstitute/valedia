# Valedia versioning (Burton 2026-09-18)

Standing rule: **every Valedia change ships through git with an explicit version.** No “mystery live tree.”

## Trees

| Tree | Path | Branch | Role |
|------|------|--------|------|
| **Build** | `~/.openclaw/workspace-v2` | `valedia-v2-grok-build` | Grok Build codes/tests/commits here |
| **Live runtime** | `~/.openclaw/workspace` | `master` | Sole live risk authority; receives **fenced** deploys only |

Neither has a remote today — local git is still the source of truth. Add a remote later only with Burton yes.

## Version identity

1. **`VALEDIA_VERSION`** in `scripts/integrations/valedia_config.py` — semver-ish string, e.g. `0.20.0` (slice-aligned) or `0.20.0+s20`.
2. **Git tag** on the commit that defines that version: `valedia-v0.20.0` (Build) and, after fenced deploy, same tag on Live tip **or** `valedia-v0.20.0-live`.
3. **Deploy tags** (already started): `pre-*` before risky apply, `fenced-deploy-sNN` after apply.
4. **Restore point**: `pre-ruflo-B1` remains the hard rollback landmark.

`MANUSCRIPT_VERSION` (UGFT-*) is framework manuscript — **not** a substitute for `VALEDIA_VERSION`.

## Every slice / deploy must

1. Commit on Build with message `Slice N: …` (or `Slice N status: record commit …`).
2. Bump `VALEDIA_VERSION` when behavior or messaging contract changes (not for pure docs-only unless Burton wants it).
3. `git tag -a valedia-vX.Y.Z -m "…"`.
4. Update `recommendations/GROK-STATUS.md` with **tip commit + version + tag**.
5. Fenced live apply: tag Live `pre-deploy-sNN` → sync → tag `fenced-deploy-sNN` / `valedia-vX.Y.Z-live`.
6. Trade **status messages** include `VALEDIA_VERSION` + short git sha (or tag) so Signal never reports an unversioned mystery build.

## Accuracy tie-in (Slice 21+)

Status lines must name:
- version + sha
- mode: `LIVE` / `DRY` / `SHADOW` / `FENCED` (exactly one, truthful)
- fill truth: `filled` only with fee-true ledger fields; else `ack` / `UNRECONCILED` / `shadow`

## Do not

- Edit Live without a Build commit + tag first (except emergency Burton hotfix — then back-port to Build same day).
- Force-push either tree.
- Flip trading flags as part of “versioning.”
