"""Run intake over every fixture and record what it found against the ground truth.

    python run_intake.py          # prints the headline numbers, writes results/intake.json

Offline: no key, no network. The embedding model is 07's hashing stand-in, used here only
to count which chunks needed embedding; no retrieval is measured in this step.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
for p in (HERE, HERE.parent, HERE.parent / "12_integration", HERE.parent / "10_observability"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from docctl.measure import summary  # noqa: E402

OUT = HERE / "results" / "intake.json"


def main() -> int:
    s = summary()
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(s, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    c, life, ids, v, pii, cls, ext = (s["contracts"], s["lifecycle"], s["identity"], s["validation"],
                                     s["personal_data"], s["classification"], s["extraction"])
    print(f"contracts: {c['quarantined']} records quarantined ({c['planted_caught']} of {c['planted']} planted)")
    print(f"lifecycle: at most {life['most_effective_revisions_of_one_document_at_any_step']} effective revision per "
          f"document over {life['steps_checked']} shuffled steps; {life['illegal_transitions']['planted_caught']} of "
          f"{life['illegal_transitions']['planted']} illegal transitions quarantined")
    print(f"identity: {ids['held_in_two_systems']['found']} held in two systems, {ids['conflicting_copies']['found']} "
          f"conflicts, {ids['same_content_under_another_number']['records_flagged']} flagged as misfiled")
    print(f"personal data: {pii['redacted']} of {pii['planted']} redacted; {pii['approver_names_kept']} of "
          f"{pii['approver_names']} approver names kept")
    print(f"classification: {cls['correct']} of {cls['documents']} correct, {cls['to_intake_review']} to review, "
          f"{cls['wrong_type_filed']} filed as the wrong type")
    print(f"extraction: {ext['correct']} of {ext['of']} values; validation: {v['caught_by_its_rule']} of "
          f"{v['planted']} planted failures caught by their rule, {len(v['caught_by_another_rule'])} by another, "
          f"{v['missed']} missed")
    print(f"wrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
