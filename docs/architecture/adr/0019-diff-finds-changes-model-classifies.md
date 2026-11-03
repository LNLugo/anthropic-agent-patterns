# ADR 0019: A deterministic diff finds changes; a model only classifies them

**Status:** Proposed, 2026-11-03. Becomes Accepted or Rejected when the recorded live runs are in · **Standards:** AI-01, AI-07, AI-04

## Context

Quality reviewers compare revisions of procedures and specifications to see what changed and how much it matters. Asking a model to read both revisions and list the changes is the obvious build. But a model can miss a change, and in a regulated document a missed change is a quality event, while a false alarm costs a reviewer a minute (SmartDMS design, s10).

## Decision

Proposed: a deterministic diff finds every change, and a model only decides what kind of change it is and how severe. The diff aligns sections by heading. It compares text sentence by sentence after normalising whitespace and line wrapping, and compares tables row by row. A change the diff finds is reported even when the model cannot classify it, so coverage does not depend on the model.

## Alternatives rejected (pending the measurement)

- **The model compares directly.** One call per pair, no diff. Simpler to build; coverage depends on the model's attention.
- **Rules alone.** No model at all. Kept as the non-model baseline every model arm must beat (ADR 0013).

## Consequences

- Coverage is a property of code, testable offline.
- A model is still needed for type and severity wherever the rules fall short; the measurement decides whether it is.
- Comparing revisions needs both documents parsed into sections, which intake already does.

## Evidence

From `15_document_control/results/comparison.json`, on 16 seeded revision pairs with 51 planted, labelled changes (24 procedural, 14 specification or limit, 8 editorial, 5 safety-relevant), with formatting noise in every newer revision:

| Arm | Changes found | Type correct | Severity correct | False findings |
|---|---|---|---|---|
| Diff + rules (baseline) | 51 of 51 | 51 | 51 | 0 |
| Diff + model | not run yet | | | |
| Model directly | not run yet | | | |

The baseline's perfect score is an upper bound: the same hand wrote the planted changes and the rules. Building it found two faults in the diff that the noise was planted to expose:
- **A re-wrapped half sentence read as a heading.** Fixed by choosing a document's heading style by majority.
- **A removed step paired with its reworded neighbour.** Fixed by pairing changes by similarity.

Still to measure: the two model arms, three live runs each, recorded in `results/comparison_live.json` and replayed by the offline tests (`python run_comparison.py --live`). This ADR is decided by those runs:
- **Accepted** if the direct model finds fewer planted changes than the diff.
- **Rejected** in favour of the direct model if it matches the diff's recall with no more false findings.

## The strongest objection

*"Your rules already score 51 of 51. Why would the model arms ever matter?"*

Because the rules were written by the same person who planted the changes, on changes of four known shapes. Real revisions vary more. The question the live runs answer is narrower and still open: when a model is used, should it be given the diff or the documents?
