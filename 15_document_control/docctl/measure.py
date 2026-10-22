"""Every number intake reports, computed from a run against fixtures/labels.yaml.

run_intake.py writes these to results/intake.json; the README and the app read that file;
a test checks it is what the code produces now.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import yaml

from . import contracts
from .corpus import FIXTURES
from .intake import Intake, arrivals, run_intake
from .lifecycle import LifecycleMirror, StatusEvent


def labels(folder: Path = FIXTURES) -> dict:
    return yaml.safe_load((folder / "labels.yaml").read_text(encoding="utf-8"))


def _num(x):
    try:
        return round(float(x), 4)
    except (TypeError, ValueError):
        return str(x)


def _same(name, got, want) -> bool:
    if name == "limits":
        return isinstance(got, dict) and {k: [_num(v) for v in lv] for k, lv in got.items()} == \
            {k: [_num(v) for v in lv] for k, lv in want.items()}
    if name == "results":
        return isinstance(got, dict) and {k: _num(v) for k, v in got.items()} == {k: _num(v) for k, v in want.items()}
    return str(got) == str(want)


def arrivals_summary(folder: Path = FIXTURES) -> dict:
    c = Counter(s for s, _ in arrivals(folder))
    return {"document_records": {s: c[s] for s in ("qdms", "sharepoint", "sap")}, "status_events": c["status"]}


def contract_summary(intake: Intake, lab: dict) -> dict:
    quarantined = sorted({(r.source.split("/")[-1], r.where, r.reason) for r in intake.rejected})
    planted = lab["planted"]["contract_failures"]
    caught = sum(1 for p in planted if any(p["id"].split("/")[0] in w for _, w, _ in quarantined))
    return {"quarantined": len(quarantined), "by_source": dict(Counter(s for s, _, _ in quarantined)),
            "planted": len(planted), "planted_caught": caught,
            "owners": {k: v.owner for k, v in contracts.CONTRACTS.items()}}


def delivery_summary(base: Intake, seeds=(1, 2, 3)) -> dict:
    runs = []
    for seed in seeds:
        r = run_intake(shuffle_seed=seed, redeliver=0.25, crash_after=40)
        runs.append({"seed": seed, "events_handled": r.consumer.stats.handled,
                     "duplicates_skipped": r.consumer.stats.duplicates_skipped,
                     "same_outcome_as_in_order": r.fingerprint() == base.fingerprint()})
    control = run_intake(shuffle_seed=1, redeliver=0.25, crash_after=40, dedup=False)
    return {"in_order_handler_runs": base.handler_runs, "documents": len(base.records()),
            "shuffled_with_duplicates_and_a_crash": runs,
            "control_without_processed_store": {
                "handler_runs": control.handler_runs, "duplicates_skipped": control.consumer.stats.duplicates_skipped,
                "same_outcome_as_in_order": control.fingerprint() == base.fingerprint()}}


def lifecycle_summary(intake: Intake, lab: dict, folder: Path = FIXTURES) -> dict:
    # the invariant at every step, replaying the status feed in a shuffled order with duplicates
    import random

    events = [r for s, r in arrivals(folder) if s == "status"]
    rng = random.Random(15)
    order = events + rng.sample(events, len(events) // 4)
    rng.shuffle(order)
    m, worst, steps = LifecycleMirror(), 0, 0
    for r in order:
        m.apply(StatusEvent(r["id"], r["doc_number"], r["revision"], r["to"], r["at"]))
        steps += 1
        worst = max(worst, max(m.effective_counts().values()))
    final = {(n, rv): s.status for n, revs in m.documents().items() for rv, s in revs.items()}
    in_order = {(n, rv): s.status for n, revs in intake.mirror.documents().items() for rv, s in revs.items()}
    planted = {p["event"] for p in lab["planted"]["illegal_transitions"]}
    found = {e.id for e in intake.mirror.illegal()}
    return {"revisions": len(in_order), "by_status": dict(sorted(Counter(in_order.values()).items())),
            "documents_with_an_effective_revision": sum(1 for v in intake.mirror.effective_counts().values() if v == 1),
            "most_effective_revisions_of_one_document_at_any_step": worst, "steps_checked": steps,
            "shuffled_replay_same_final_state": final == in_order,
            "illegal_transitions": {"planted": len(planted), "quarantined": len(found),
                                    "planted_caught": len(planted & found), "other": len(found - planted)}}


def identity_summary(intake: Intake, lab: dict) -> dict:
    recs = intake.registry.records()
    p = lab["planted"]
    two = sorted(k for k, c in recs.items() if len(c.locations) > 1)
    conflicts = sorted(k for k, c in recs.items() if c.conflicts)
    misfiled = sorted(k for k, c in recs.items() if c.same_content_as)
    return {"records": len(recs),
            "held_in_two_systems": {"found": len(two), "planted": len(p["identical_copies"]),
                                    "planted_found": len({x["key"] for x in p["identical_copies"]} & set(two))},
            "conflicting_copies": {"found": len(conflicts), "planted": len(p["conflicting_copies"]),
                                   "planted_found": len({x["key"] for x in p["conflicting_copies"]} & set(conflicts)),
                                   "canonical_is_system_of_record": all(
                                       recs[k].locations[0].system == "qdms" for k in conflicts)},
            "same_content_under_another_number": {"records_flagged": len(misfiled), "pairs_planted":
                                                  len(p["misfiled_copies"]), "keys": misfiled}}


def parsing_summary(intake: Intake, lab: dict) -> dict:
    recs = intake.records()
    docs = lab["documents"]
    fmt = Counter(docs[k]["format"] for k in recs if k in docs)
    chunks = [c for r in recs.values() for c in r.chunks]
    return {"documents_by_format": dict(sorted(fmt.items())),
            "attachments_reentered": sum(1 for r in recs.values() if r.parent),
            "attachments_planted": sum(1 for d in docs.values() if d["parent"]),
            "chunks": len(chunks), "table_chunks": sum(c.is_table for c in chunks),
            "largest_chunk_tokens": max(c.token_count for c in chunks),
            "embeddings_computed": intake.embeddings.computed, "embeddings_reused": intake.embeddings.reused,
            "share_reused": round(intake.embeddings.reused / (intake.embeddings.computed + intake.embeddings.reused), 3)}


def pii_summary(intake: Intake, lab: dict) -> dict:
    recs = intake.records()
    text = {k: "\n".join(c.text for c in r.chunks) for k, r in recs.items()}
    planted, kept = lab["planted"]["pii"], lab["planted"]["pii_kept"]
    by_kind, caught_kind, missed = Counter(), Counter(), []
    for p in planted:
        if p["key"] not in text:
            continue
        by_kind[p["kind"]] += 1
        if p["text"] in text[p["key"]]:
            missed.append(p["kind"])
        else:
            caught_kind[p["kind"]] += 1
    kept_ok = sum(1 for p in kept if p["key"] in text and p["name"] in text[p["key"]])
    kept_n = sum(1 for p in kept if p["key"] in text)
    planted_texts = {(p["key"], p["text"]) for p in planted}
    over = [(k, f.kind) for k, r in recs.items() for f in r.pii if (k, f.text) not in planted_texts]
    n = sum(by_kind.values())
    return {"planted": n, "redacted": sum(caught_kind.values()), "by_kind": {
        k: {"planted": by_kind[k], "redacted": caught_kind[k]} for k in sorted(by_kind)},
        "missed_by_kind": dict(Counter(missed)), "approver_names_kept": kept_ok, "approver_names": kept_n,
        "redactions_of_unplanted_text": len(over)}


def classification_summary(intake: Intake, lab: dict) -> dict:
    recs = intake.records()
    docs = lab["documents"]
    out, review, wrong = Counter(), Counter(), []
    total = Counter()
    for k, r in recs.items():
        if k not in docs:
            continue
        t = docs[k]["doc_type"]
        total[t] += 1
        if r.classification.review:
            review[t] += 1
        elif r.doc_type == t:
            out[t] += 1
        else:
            wrong.append({"key": k, "said": r.doc_type, "is": t})
    review_formats = Counter(docs[k]["format"] for k, r in recs.items() if k in docs and r.classification.review)
    return {"documents": sum(total.values()), "correct": sum(out.values()), "to_intake_review": sum(review.values()),
            "wrong_type_filed": len(wrong), "wrong": wrong,
            "by_type": {t: {"documents": total[t], "correct": out[t], "to_review": review[t]} for t in sorted(total)},
            "review_by_format": dict(review_formats), "by": "rules"}


def extraction_summary(intake: Intake, lab: dict) -> dict:
    recs = intake.records()
    docs = lab["documents"]
    per, per_fmt = {}, {}
    for k, r in recs.items():
        if k not in docs or r.doc_type != docs[k]["doc_type"]:
            continue
        fmt = docs[k]["format"]
        for name, want in docs[k]["fields"].items():
            if name == "attachments":
                continue
            f = r.fields.get(name)
            ok = f is not None and _same(name, f.value, want)
            a = per.setdefault(name, [0, 0])
            a[0] += ok
            a[1] += 1
            b = per_fmt.setdefault(fmt, [0, 0])
            b[0] += ok
            b[1] += 1
    with_page = sum(1 for r in recs.values() for f in r.fields.values() if f.page)
    all_fields = sum(len(r.fields) for r in recs.values())
    return {"by_field": {k: {"correct": v[0], "of": v[1]} for k, v in sorted(per.items())},
            "by_format": {k: {"correct": v[0], "of": v[1]} for k, v in sorted(per_fmt.items())},
            "correct": sum(v[0] for v in per.values()), "of": sum(v[1] for v in per.values()),
            "values_with_a_page": with_page, "values": all_fields}


def validation_summary(intake: Intake, lab: dict) -> dict:
    recs = intake.records()
    docs = lab["documents"]
    planted = lab["planted"]["validation_failures"]
    own, other, missed = 0, [], []
    for p in planted:
        r = recs.get(p["key"])
        rules = {f.rule for f in r.failures} if r else set()
        if p["rule"] in rules:
            own += 1
        elif r is not None and r.review:
            other.append({"key": p["key"], "planted": p["rule"], "caught_as": sorted(rules)})
        else:
            missed.append(p)
    keys = {(p["key"], p["rule"]) for p in planted}
    unplanted = [(k, f.rule, docs.get(k, {}).get("format", "copy")) for k, r in recs.items() for f in r.failures
                 if (k, f.rule) not in keys]
    born_digital = [x for x in unplanted if x[2] in ("pdf", "html", "eml")]
    return {"planted": len(planted), "caught_by_its_rule": own, "caught_by_another_rule": other,
            "missed": len(missed), "other_failures": len(unplanted),
            "other_by_rule": dict(Counter(r for _, r, _ in unplanted)),
            "other_by_format": dict(Counter(f for _, _, f in unplanted)),
            "other_on_born_digital_documents": sorted({(k, r) for k, r, _ in born_digital}),
            "to_review": sum(r.review for r in recs.values()), "documents": len(recs)}


def summary(folder: Path = FIXTURES) -> dict:
    lab = labels(folder)
    base = run_intake(folder)
    return {
        "arrivals": arrivals_summary(folder),
        "contracts": contract_summary(base, lab),
        "delivery": delivery_summary(base),
        "lifecycle": lifecycle_summary(base, lab, folder),
        "identity": identity_summary(base, lab),
        "parsing_and_chunking": parsing_summary(base, lab),
        "personal_data": pii_summary(base, lab),
        "classification": classification_summary(base, lab),
        "extraction": extraction_summary(base, lab),
        "validation": validation_summary(base, lab),
    }
