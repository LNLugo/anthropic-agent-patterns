# ADR 0019: A deterministic diff finds changes; a model only classifies them

**Status:** Proposed, 2026-11-03. Live runs recorded 2026-11-03 and neither decision rule fired; stays Proposed until revisions of realistic length are measured · **Standards:** AI-01, AI-07, AI-04

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

From `15_document_control/results/comparison.json`, on 16 seeded revision pairs with 51 planted, labelled changes (24 procedural, 14 specification or limit, 8 editorial, 5 safety-relevant), with formatting noise in every newer revision. The model arms ran three times on gpt-6-luna, reasoning effort none, on 2026-11-03; every reply is in `results/comparison_live.json` and the offline tests replay it.

| Arm | Changes found | Type correct | Severity correct | False findings |
|---|---|---|---|---|
| Diff + rules (baseline) | 51 of 51 | 51 | 51 | 0 |
| Diff + model, runs 1 to 3 | 51, 51, 51 | 49, 49, 49 | 49, 49, 49 | 0, 0, 0 |
| Model directly, runs 1 to 3 | 51, 51, 51 | 46, 44, 45 | 36, 39, 40 | 0, 0, 1 |

The baseline's perfect score is an upper bound: the same hand wrote the planted changes and the rules. Building it found two faults in the diff that the noise was planted to expose:
- **A re-wrapped half sentence read as a heading.** Fixed by choosing a document's heading style by majority.
- **A removed step paired with its reworded neighbour.** Fixed by pairing changes by similarity.

### What the live runs showed

- **Coverage: the premise was not supported on this set.** The model comparing directly found all 51 planted changes in every run. Its one false finding (run 3) was a bare "Step 2:" left by a re-wrapped line, reported as an added step: the noise the set plants.
- **Neither rule fired.** It is not Accepted, because the direct model did not find fewer changes. It is not Rejected, because it had one more false finding than the diff (1 against 0 over 153 attempts).
- **The arms separated on grading, which was not the test.** Given the diff, the model made the same two errors in every run: two specification changes graded low instead of medium. Comparing directly, its grading moved from run to run (36 to 40 of 51), it graded high-severity specification changes low in every run, and in run 1 it graded a safety-relevant change medium. Most of its other errors graded procedural changes high, which costs a reviewer time, not a missed change.

### Why it stays Proposed

The revisions are 355 to 789 characters: a paragraph or two each. That cannot test whether a model reading both revisions misses a change, which is where attention would slip on a procedure many pages long. Accepting now on the grading figures would change the test after seeing the result, so the decision rule stands as written and is run again on revision pairs at realistic SOP length, added to the held-out set in PR 4a:
- **Accepted** if the direct model finds fewer planted changes than the diff.
- **Rejected** in favour of the direct model if it matches the diff's recall with no more false findings.

## The strongest objection

*"The model found every change on its own. Isn't the diff just extra machinery?"*

On documents a paragraph long, yes: these runs show nothing the diff adds to coverage. They do not show the same on a forty-page procedure, which is the case the diff exists for, and that is the next measurement. What they do show is that the model given the diff grades the same way every run, and the model given the documents does not.
