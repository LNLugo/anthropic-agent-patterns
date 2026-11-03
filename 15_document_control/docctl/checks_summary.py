"""Every number PR 3 reports: the headline comparison, the certificate and standard checks,
and a review run over intake's records. run_comparison.py writes them to results/comparison.json."""

from __future__ import annotations

import sqlite3
from collections import Counter
from pathlib import Path

import yaml

from . import headline
from .conformance_checks import accept_model_verdict, check_coa, check_standard
from .corpus import FIXTURES
from .intake import run_intake
from .measure import labels
from .parse import parse_pdf
from .review import (SERVICE, AuditLog, MockDMS, Reviews, build_review, classification_output, override_rates,
                     resume, route, submit, write_back)

REVIEWERS = ("Grace Okafor", "Hugo Lindqvist")


def certificates(records, lab) -> dict:
    planted = {p["key"]: p["parameter"] for p in lab["planted"]["out_of_spec"]}
    found = [f for k, r in records.items() if r.doc_type == "COA" for f in check_coa(k, r, records)]
    oos = {(f.coa, f.parameter) for f in found if f.kind == "out_of_specification"}
    arrived = {k: v for k, v in planted.items() if k in records}
    caught = sum(1 for k, v in arrived.items() if (k, v) in oos)
    return {"certificates": sum(1 for r in records.values() if r.doc_type == "COA"),
            "planted": len(planted), "planted_arrived": len(arrived), "planted_caught": caught,
            "not_arrived": sorted(set(planted) - set(arrived)),
            "other_findings": sum(1 for f in found if f.kind == "out_of_specification"
                                  and (f.coa, f.parameter) not in set(arrived.items())),
            "by_kind": dict(sorted(Counter(f.kind for f in found).items())),
            "no_specification": sorted(f.coa for f in found if f.kind == "no_specification")}


def standards(records) -> dict:
    d = yaml.safe_load((FIXTURES / "comparison" / "pairs.yaml").read_text(encoding="utf-8"))
    clauses = d["standard"]["clauses"]
    planted = []
    for s in d["standard_checks"]:
        res = {r.clause: r.verdict for r in check_standard(parse_pdf((FIXTURES / "comparison" / s["file"])
                                                                     .read_bytes()), clauses)}
        planted.append({"id": s["id"], "breaks": s["breaks"], "expected": s["expected"], "got": res[s["breaks"]],
                        "others_covered": all(v == "covered" for c, v in res.items() if c != s["breaks"])})
    corpus = Counter()
    for r in records.values():
        if r.doc_type == "SOP":
            corpus.update(x.verdict for x in check_standard(r.parsed, clauses))
    return {"clauses": len(clauses), "planted": len(planted),
            "planted_caught": sum(1 for p in planted if p["got"] == p["expected"] and p["others_covered"]),
            "planted_passed_wrongly": sum(1 for p in planted if p["got"] == "covered"),
            "planted_checks": planted,
            "corpus": {"sops": sum(1 for r in records.values() if r.doc_type == "SOP"),
                       "verdicts": dict(sorted(corpus.items())),
                       "note": "every corpus SOP meets every clause by construction; any verdict other than "
                               "covered is a false alarm, never a false pass"}}


def _wrong(r, truth) -> dict:
    """What a reviewer would correct: a wrong type, or a value intake read wrongly. A validation
    failure intake flagged correctly is not an error: the reviewer accepts the flag."""
    from .measure import _same

    if truth is None:
        return {}                                  # a copy filed under another number: nothing to correct
    if r.doc_type != truth["doc_type"]:
        return {"doc_type": truth["doc_type"]}
    return {n: w for n, w in truth["fields"].items() if n != "attachments"
            and not (n in r.fields and _same(n, r.fields[n].value, w))}


def review_run(records, lab) -> dict:
    """Route every record, then have two reviewers decide the ones routed to a person, through
    the review graph: accept when intake got it right, edit to the label when it did not."""
    from langgraph.checkpoint.sqlite import SqliteSaver

    routes = {k: route(r) for k, r in records.items()}
    reviews = Reviews(AuditLog(), MockDMS(lose_first_response=True))
    app = build_review(reviews, SqliteSaver(sqlite3.connect(":memory:", check_same_thread=False)))
    docs = lab["documents"]
    decided = Counter()
    for n, (k, r) in enumerate(sorted(records.items())):
        if routes[k].to != "review" and not routes[k].blind:
            continue
        out = classification_output(r)
        submit(app, k, out, SERVICE)
        truth = docs.get(k)
        wrong = _wrong(r, truth)
        edits = {name: want for name, want in wrong.items()}
        res = resume(app, k, REVIEWERS[n % 2], "edit" if wrong else "accept", edits,
                     ("wrong_type" if "doc_type" in wrong else "wrong_value") if wrong else None)
        decided[res["outcome"]] += 1
    self_approval = Reviews(AuditLog(), MockDMS())
    app2 = build_review(self_approval, SqliteSaver(sqlite3.connect(":memory:", check_same_thread=False)))
    any_key = sorted(records)[0]
    submit(app2, "x", classification_output(records[any_key]), SERVICE)
    refused = resume(app2, "x", SERVICE, "accept")["outcome"].startswith("declined")
    chain_ok = reviews.audit.verify() is None
    tampered = AuditLog()
    tampered.records = [type(r)(**{**r.__dict__}) for r in reviews.audit.records]
    tampered.records[3].decision = "accept" if tampered.records[3].decision != "accept" else "reject"
    no_id = MockDMS(lose_first_response=True)
    write_back(no_id, "SOP-QA-0001/02", {"decision": "accept"}, None)
    return {"routed": dict(sorted(Counter(f"{x.risk}:{x.to}" for x in routes.values()).items())),
            "high_risk_to_review": all(x.to == "review" for x in routes.values() if x.risk == "high"),
            "blind_sampled": sum(x.blind for x in routes.values()),
            "decisions": dict(sorted(decided.items())), "audit_records": len(reviews.audit.records),
            "audit_chain_verifies": chain_ok, "tampered_record_found_at": tampered.verify(),
            "gold_examples_added": len(reviews.gold),
            "written_back": len(reviews.dms.written), "written_back_without_request_id_after_a_lost_response":
                len(no_id.written),
            "service_cannot_approve": refused, "override_rates": override_rates(reviews.audit)}


def summary() -> dict:
    lab = labels()
    records = run_intake().records()
    return {"version_vs_version": headline.summary(), "certificate_vs_specification": certificates(records, lab),
            "document_vs_standard": standards(records), "review": review_run(records, lab)}
