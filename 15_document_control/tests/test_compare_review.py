"""Module 15, PR 3: version comparison, certificate and standard checks, review and audit,
write-back, and the headline runner's recording and replay. Offline: rules make every
comparison decision; the model arms are tested on scripted and replayed replies only."""

import json
import sqlite3
from pathlib import Path

import pytest
import yaml
from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite import SqliteSaver

from docctl import headline
from docctl.checks_summary import summary
from docctl.compare import Change, classify_model, classify_rules, compare_direct, diff, score
from docctl.conformance_checks import accept_model_verdict, check_coa, check_standard
from docctl.corpus import FIXTURES
from docctl.intake import run_intake
from docctl.parse import parse_pdf
from docctl.review import (SERVICE, AuditLog, MockDMS, Reviews, build_review, classification_output, pending,
                           resume, route, submit, write_back)
from llm_provider import ScriptedChatModel, Step

HERE = Path(__file__).resolve().parents[1]
PAIRS = yaml.safe_load((FIXTURES / "comparison" / "pairs.yaml").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def records():
    return run_intake().records()


@pytest.fixture(scope="module")
def recorded():
    return json.loads((HERE / "results" / "comparison.json").read_text(encoding="utf-8"))


def _pair(pid):
    p = next(x for x in PAIRS["pairs"] if x["id"] == pid)
    return p, *headline.load(p)


def _model(*replies):
    m = ScriptedChatModel()
    m.load([Step(AIMessage(content=r)) for r in replies])
    return m


# ---------------------------------------------------------------- s10: version against version

def test_the_diff_finds_every_planted_change_and_nothing_else(recorded):
    r = recorded["version_vs_version"]["rules"]
    assert r["found"] == r["planted"] == 51 and r["false_findings"] == 0


def test_rewrapped_text_extra_spaces_and_a_moved_page_break_are_not_changes():
    p, a, b = _pair("P05")
    assert len(b.sections) == len(a.sections)                 # the re-wrapped first half is not a heading
    changes = diff(a, b)
    assert all(c.section != "Purpose" for c in changes)       # Purpose was only re-wrapped
    assert len(changes) == len(p["changes"])


def test_a_removed_step_next_to_a_reworded_one_is_not_paired_with_it():
    p, a, b = _pair("P02")
    removed = [c for c in diff(a, b) if c.section == "Procedure" and c.kind == "removed"]
    assert [c.before for c in removed] == [x["before"] for x in p["changes"]
                                           if x["section"] == "Procedure" and not x["after"]]


def test_every_change_cites_both_revisions_where_it_exists():
    p, a, b = _pair("P03")
    for c in diff(a, b, "P03_a.pdf", "P03_b.pdf"):
        assert (c.cite_a.startswith("P03_a.pdf#") or not c.before) and (c.cite_b.startswith("P03_b.pdf#") or not c.after)


@pytest.mark.parametrize("before, after, row, want", [
    ("pH | 5.0 - 7.5 | - | Potentiometric", "pH | 5.0 - 7.0 | - | Potentiometric", "pH", ("specification_or_limit", "medium")),
    ("pH | 5.0 - 7.5 | - | Potentiometric", "pH | 5.0 - 8.2 | - | Potentiometric", "pH", ("specification_or_limit", "high")),
    ("Water | 0.00 - 0.50 | % | KF", "Water | 0.00 - 0.50 | % w/w | KF", "Water", ("specification_or_limit", "high")),
    ("Wear nitrile gloves and safety glasses.", "Wear gloves.", None, ("safety_relevant", "high")),
    ("Step 4: The operator must be trained.", "Step 4: The operator shall be trained.", None, ("editorial", "low")),
    ("Quality approves it.", "The area supervisor approves it.", None, ("procedural", "medium")),
])
def test_the_rules_baseline_types_and_grades_each_kind_of_change(before, after, row, want):
    f = classify_rules(Change("S", before, after, "a", "b", row))
    assert (f.type, f.severity) == want


def test_the_model_classifier_accepts_only_allowed_types_and_severities():
    c = Change("Procedure", "Step 2: Record the batch.", "", "a#5 Procedure", "")
    ok = classify_model(c, _model('{"type": "procedural", "severity": "medium", "explanation": "A step was '
                                  'removed.", "confidence": 0.9}'))
    assert (ok.type, ok.severity, ok.by) == ("procedural", "medium", "model") and ok.citations == ["a#5 Procedure"]
    bad = classify_model(c, _model('{"type": "major", "severity": "critical"}'))
    assert bad.type is None and bad.severity is None
    prose = classify_model(c, _model("This looks procedural to me."))
    assert prose.type is None


def test_a_change_the_diff_found_is_reported_even_when_the_model_cannot_classify_it():
    """The diff guarantees coverage: an unclassified change is still a finding, so it is not missed."""
    p, a, b = _pair("P01")
    changes = diff(a, b)
    fs = [classify_model(c, _model("not json")) for c in changes]
    s = score(fs, p["changes"])
    assert s["found"] == s["planted"] and s["type_correct"] == 0


def test_a_model_comparing_directly_is_scored_the_same_way():
    p, a, b = _pair("P01")
    first = p["changes"][0]
    reply = json.dumps({"findings": [{"section": first["section"], "before": first["before"], "after": first["after"],
                                      "type": first["type"], "severity": first["severity"], "explanation": "x"},
                                     {"section": "Scope", "before": "Applies to production.", "after": "Applies.",
                                      "type": "editorial", "severity": "low", "explanation": "invented"}]})
    s = score(compare_direct(a, b, _model(reply)), p["changes"])
    assert s["found"] == 1 and s["false_findings"] == 1 and s["type_correct"] == 1


# ---------------------------------------------------------------- the headline runner

def test_without_a_recording_the_model_arms_are_reported_as_not_run(recorded):
    if headline.RECORDED.exists():
        pytest.skip("a live recording exists")
    assert str(recorded["version_vs_version"]["model_arms"]).startswith("not run")


def test_a_recording_replays_to_the_same_scores(tmp_path, monkeypatch):
    """Record against a scripted model standing in for the live one, then replay the file."""
    class Echo:                                  # classifies like the rules, and compares by listing nothing
        def invoke(self, prompt):
            if "REVISION A" in prompt:
                return AIMessage(content='{"findings": []}')
            return AIMessage(content='{"type": "procedural", "severity": "medium", "explanation": "x", '
                                     '"confidence": 0.5}')

    monkeypatch.setattr(headline, "RECORDED", tmp_path / "live.json")
    rec = headline.record_live(Echo(), runs=2, model="echo", measured="2026-11-03")
    assert len(rec["runs"]) == 2 and (tmp_path / "live.json").exists()
    again = headline.replay_recorded(tmp_path / "live.json")
    for run in again["runs"]:
        assert run["direct"]["found"] == 0 and run["diff_model"]["found"] == 51
        assert run["diff_model"]["type_correct"] == sum(1 for p in PAIRS["pairs"] for c in p["changes"]
                                                        if c["type"] == "procedural")


def test_a_replay_that_runs_out_of_replies_fails_loudly():
    r = headline.Replay(["one"])
    r.invoke("a")
    with pytest.raises(RuntimeError):
        r.invoke("b")


# ---------------------------------------------------------------- s10: certificate against specification

def test_out_of_specification_results_are_caught_and_nothing_else_is(recorded):
    c = recorded["certificate_vs_specification"]
    assert c["planted_caught"] == c["planted_arrived"] == 2 and c["other_findings"] == 0
    assert c["not_arrived"] == ["COA-2001-L26-0265/01"]       # quarantined at its contract in intake


def test_a_certificate_is_checked_against_the_specification_in_force_when_the_lot_was_made(records):
    f = [x for x in check_coa("COA-2005-L26-0742/01", records["COA-2005-L26-0742/01"], records)
         if x.kind == "out_of_specification"]
    assert [(x.parameter, x.spec) for x in f] == [("Microbial count", "SPEC-RM-0003/02")]
    assert any("Results" in c for c in f[0].citations) and any("Limits" in c for c in f[0].citations)


# ---------------------------------------------------------------- s10: document against standard

def test_each_planted_break_of_the_standard_is_found_and_none_is_passed(recorded):
    d = recorded["document_vs_standard"]
    assert d["planted_caught"] == d["planted"] == 3 and d["planted_passed_wrongly"] == 0


def test_on_a_shaky_scan_an_absent_heading_is_uncertain_not_missing(records):
    r = records["SOP-QA-0006/01"]
    v = {x.clause: x.verdict for x in check_standard(r.parsed, PAIRS["standard"]["clauses"])}
    assert v["STD-2"] == "uncertain"


def test_a_model_saying_covered_without_a_real_quote_is_downgraded(records):
    doc = records["SOP-QA-0001/02"].parsed
    assert accept_model_verdict("covered", "Records of line clearance are kept for", doc) == "covered"
    assert accept_model_verdict("covered", "a sentence that is not in the document at all", doc) == "uncertain"
    assert accept_model_verdict("passed", "", doc) == "uncertain"


# ---------------------------------------------------------------- s13: review and audit

def test_high_risk_documents_always_go_to_a_person_and_some_automatic_approvals_are_sampled(recorded):
    r = recorded["review"]
    assert r["high_risk_to_review"] and r["blind_sampled"] > 0


def _app(reviews, db):
    return build_review(reviews, SqliteSaver(sqlite3.connect(str(db), check_same_thread=False)))


def test_a_review_waits_for_a_person_across_a_restart(records, tmp_path):
    reviews = Reviews(AuditLog(), MockDMS())
    out = classification_output(records["SPEC-RM-0004/01"])
    submit(_app(reviews, tmp_path / "cp.db"), "t", out, SERVICE)
    restarted = _app(reviews, tmp_path / "cp.db")
    assert pending(restarted, "t")["doc_ids"] == ["SPEC-RM-0004/01"]
    assert resume(restarted, "t", "Grace Okafor", "accept")["outcome"] == "accept"
    assert len(reviews.audit.records) == 1 and len(reviews.dms.written) == 1


def test_the_service_and_the_requester_cannot_approve_their_own_output(records, tmp_path):
    reviews = Reviews(AuditLog(), MockDMS())
    app = _app(reviews, tmp_path / "cp.db")
    out = classification_output(records["SOP-QA-0001/02"])
    submit(app, "a", out, SERVICE)
    assert resume(app, "a", SERVICE, "accept")["outcome"].startswith("declined")
    submit(app, "b", out, "Dev Abara")
    assert resume(app, "b", "Dev Abara", "accept")["outcome"].startswith("declined")
    assert reviews.audit.records == [] and reviews.dms.written == []


def test_an_edit_becomes_a_gold_example_with_the_reviewers_label(records, tmp_path):
    reviews = Reviews(AuditLog(), MockDMS())
    app = _app(reviews, tmp_path / "cp.db")
    submit(app, "e", classification_output(records["COA-2005-L26-0742/01"]), SERVICE)
    resume(app, "e", "Hugo Lindqvist", "edit", {"supplier_id": "SUP-2005"}, "wrong_value")
    assert reviews.gold == [{"output_id": reviews.audit.records[0].output_id, "kind": "classification",
                             "doc_ids": ["COA-2005-L26-0742/01"], "ai": reviews.audit.records[0].output,
                             "label": {"supplier_id": "SUP-2005"}, "reviewer": "Hugo Lindqvist",
                             "reason_code": "wrong_value"}]


def test_the_audit_chain_shows_where_a_record_was_changed(recorded):
    r = recorded["review"]
    assert r["audit_chain_verifies"] and r["tampered_record_found_at"] == 3


def test_a_lost_response_writes_the_decision_once_and_the_control_writes_it_twice(recorded):
    r = recorded["review"]
    assert r["written_back"] == r["audit_records"]
    assert r["written_back_without_request_id_after_a_lost_response"] == 2


def test_write_back_gives_up_rather_than_loop_forever():
    class AlwaysLost(MockDMS):
        def post_decision(self, *a, **k):
            from docctl.review import LostResponse
            raise LostResponse("x")

    with pytest.raises(RuntimeError):
        write_back(AlwaysLost(), "K", {"decision": "accept"}, "rid", attempts=3)


def test_an_unclassified_document_is_treated_as_high_risk(records):
    unclassified = next(r for r in records.values() if r.doc_type is None)
    assert route(unclassified).to == "review" and route(unclassified).risk == "high"


# ---------------------------------------------------------------- recorded results

def test_the_recorded_comparison_results_are_what_the_code_produces_now(recorded):
    assert json.loads(json.dumps(summary(), sort_keys=True)) == recorded


def test_the_readme_quotes_the_recorded_comparison_results(recorded):
    readme = (HERE / "README.md").read_text(encoding="utf-8")
    r = recorded["version_vs_version"]["rules"]
    for phrase in (f"{r['found']} of {r['planted']} planted changes",
                   f"{recorded['review']['audit_records']} decisions"):
        assert phrase in readme, phrase


def test_the_compare_and_review_tabs_open_offline():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(HERE.parent / "app" / "pages" / "4_Document_Control.py"), default_timeout=120).run()
    assert not at.exception
    shown = {m.label: m.value for m in at.metric}
    assert shown["Planted changes found (diff + rules)"] == "51 of 51" and shown["False findings"] == "0"
    assert shown["Audit chain"] == "verifies"
    at.selectbox(key="pair").set_value("P02").run()
    assert not at.exception
