# MiroFish native runs on two models by phase

Date: 2026-11-15. Status: built on `cand/tmf4` (TMF4a, the addendum-3 repairs, TMF4b). The owner approved the build ("Yes,
build it"). Nothing here has been launched. The `src/` revisions were blind-checked on 2026-11-15 (V136, pass with notes).
The four source repairs of addendum 3 were applied afterwards and await their own check.

## Why

Qwen3 8B made valid tool calls on 44 real agent turns at about 5.5 times the 27B's speed, but its first run failed closed on
a long structured ontology reply. Ternary Bonsai 2 (GGUF Q2_0) matched the 27B on recorded personas, config and extraction
requests at the 27B's speed and half its memory. So Bonsai 2 serves setup and the report, and the 8B serves the agent turns.

## The twelve rulings as built

- **R1. Where.** The revisions are in `src/mirofish_native_model.py` and `src/mirofish_native_run.py`. The gateway file is
  unchanged. Model staging pins both revisions by SHA-256 and reads them from `src/` beside the tool. The pins are
  `e1cd97e15a990a8418fca22e044520eb19b5bf76edb7b227a4e471e431792115` and
  `0426dd5a52ad363f92d02149258507f80bf5f9d8fe51f4a1fa066733f0816b50`. Default staging (no model named) stays byte-identical
  W16 for `report64_coverage` and `swarm8`.
- **R2. Plan and selector.** The selector is `{"profile": "<name>"}\n` or `{"plan": {"setup": "<name>", "turns": "<name>"}}\n`.
  Both names must be built-in and different. The options are `--model <name>`, or `--setup-model X --turns-model Y`.
- **R3. One identifier.** Every load is made under the run's one `mf-native-<hex>` identifier. The gateway, the worker and
  the producer are unchanged.
- **R4. The swap.** It is the observer's act, at B1 (after `native_prepared`, before `worker.request('start')`) and at B2
  (after `native_terminal`, before `worker.request('report')`). The steps:
  1. Hold `gateway.serial` and require nothing reserved or in flight.
  2. Unload, and confirm that no language model is listed.
  3. Load the next checkpoint with the same settings.
  4. Read it back (selection, size, instance reference) as the first load is read back.
  5. Rebuild the admission, and require that the call list has not grown.
  6. Save `model_swap` with both readbacks.

  Any failure refuses the run.
- **R5. Ledger.** Each call's context carries the profile name beside the identifier and the instance reference.
- **R6. Helper constants.** These are a per-profile JSON table, sort-keyed and written with `json.dumps`: selection,
  `weightSize` equal to the selection's size, and `weightSha`. Each value is validated first (sizes are integers, hashes are
  64 hex characters). The single-profile path uses the same table.
- **R7. Sandbox.** The observer policy's one 27B tokenizer rule and one GGUF rule are each replaced in place by rules for
  exactly the plan's tokenizer roots and serving files, in load order.
- **R8. Memory.** See ruling (c). The `src/` line that holds 65 is untouched, and the threshold is a staged closed integer.
  The 30% floor and the 40 GiB bound are unchanged.
- **R9. Registry.** A registered profile's checks:
  - its paths lie under the pinned models root after links are resolved;
  - its name, hashes and sizes have the right shape;
  - it cannot override a built-in;
  - a malformed registry refuses with the pipeline's own refusal.

  Staged folders carry no registry.
- **R10. Provenance.** `model-source-manifest.json` names each plan member with its own files and hashes. The 27B's revision
  identifiers are written only into the 27B's record.
- **R11. Limits.** Limits stay the 27B's conservative ones. Expected durations use each phase's model.
- **R12. Manifest.** The manifest carries:
  - the plan, and each member's pins and serving bytes;
  - `model_plan` and `model_tokenizer_by_profile`;
  - "format conformance NOT shown" for each non-default checkpoint, with the 27B-only evidence blocks labelled as the 27B's;
  - `runtime_predicates` with the staged threshold (57 for this plan, not 65);
  - the review status above.

## Addendum 1's four rulings

- **(a) Projector.** A profile may pin a second file. Bonsai 2's `mmproj-Q8_0.gguf` is 629,246,976 bytes. The selection's
  `sizeBytes` (8,255,255,904) is the sum of the two files. The projector is counted in R6, R7 and R8. It is loaded and
  unused (every native message is text), and the manifest says so.
- **(b) Identifier.** Reusing a freed identifier is shown for the same model key. Reuse for a different key is settled by
  the first swap, which fails closed if the server refuses.
- **(c) Start threshold.** 30 + ceil(100 × (serving bytes + KV at 32,768 in fp16 + 4 GiB) / 68,719,476,736) + 5.

  | Checkpoint | KV at 32,768 | Threshold |
  |---|---|---|
  | 8B | 4,831,838,208 B | 56 |
  | Bonsai 2 | 2,147,483,648 B | 57 |
  | 27B, through the model path | — | 72 |
  | Plan | — | the larger member's, 57 |
  | Default path | — | the reviewed 65 |
- **(d) Probes after a swap.** After each swap and before the worker continues, the observer runs the two start probes on
  the new model and refuses the run if either fails. The budgets add 4 chats at 512 output tokens each.

## Addendum 3's repairs

- **S1.** A swap target outside the run's plan is refused before any unload.
- **S2.** A registered tokenizer folder must be narrow: it directly holds the five files, it is not a parent of a pinned
  file, and it has no `.gguf` beneath it.
- **S3.** `mirofish_models.py add` checks the row with the reader's own checks before writing.
- **S4.** The boundary helpers `model_boundary` and `unload_owned` are driven by tests. A failed post-swap probe cancels
  the gateway, and cleanup unloads the new profile.

**Known behaviour.** If a load puts the wrong size or the wrong model key behind the run's identifier, the helper refuses to
unload what it cannot prove is its own. The model stays loaded, and the receipt shows cleanup as not proven. The first load
of a run behaves the same way.

## Numbers for the plan (Bonsai 2 setup and report, 8B turns)

| | `swarm8` | `swarm48` |
|---|---|---|
| Model calls: expected / bound / allowed | 121 / 143 / 192 | 436 / 525 / 672 |
| Output reserve | (2+4)×512 + 186×4096 = 764,928 | (2+4)×512 + 666×4096 = 2,731,008 |
| Output budget | 800,000 | 2,800,000 |
| Setup / simulation / swaps / report | 1,085 / 616 / 30 / 1,179 s | 3,536 / 3,907 / 30 / 1,179 s |
| Expected total / ceiling / wall | 3,044 / 4,182 / 5,400 s | 8,787 / 16,279 / 19,800 s |

## What a first run settles

- Whether LM Studio accepts a different model key under the freed identifier (B1 is the first such load).
- What LM Studio reports as the loaded size for Bonsai 2: the helper requires 8,255,255,904, the sum of both files.
- Each checkpoint's rendering against the admission:
  - Bonsai 2's at the first setup call;
  - the 8B's at the first agent turn whose history carries a tool call, and at any turn with null assistant content.

  `tools/mirofish_format_qualify.py` can show this before a run, with the owner's yes, because it loads the model.

## Still unproven

- Bonsai 2's speeds inside the pipeline: assumed equal to the 27B's.
- The 8B's speeds: an estimate.
- The swap's expected 15 s each: assumed.
- The report phase: assumed at three times the one measured value.
- The quality of the 8B's turns and of Bonsai 2's report: unjudged.
- The format-qualification tool: tested against a fake helper only, never against LM Studio.
