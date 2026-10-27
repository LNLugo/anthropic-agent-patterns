"""Module 15, PR 1: the corpus, contracts and delivery, the lifecycle mirror, identity,
parsing, chunking, personal data, classification, extraction, validation, the recorded
results, the build status table and the app tab. Offline, no key."""

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from langchain_core.messages import AIMessage

from docctl import contracts, corpus, status
from docctl.chunking import EmbeddingCache, chunk
from docctl.classify import classify, classify_with_model
from docctl.extract import Field, extract
from docctl.identity import Registry, content_hash
from docctl.intake import arrivals, run_intake
from docctl.lifecycle import LifecycleMirror, StatusEvent
from docctl.measure import labels, summary
from docctl.parse import Line, Parsed, Section, Table, parse
from docctl.pii import redact
from docctl.validate import validate
from llm_provider import HashingEmbeddings, ScriptedChatModel, Step

HERE = Path(__file__).resolve().parents[1]
F = corpus.FIXTURES
LAB = labels()
PDF, SCAN, HTML = "application/pdf", "application/vnd.ocr-layout+json", "text/html"
EML, XLSX = "message/rfc822", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@pytest.fixture(scope="module")
def base():
    return run_intake()


@pytest.fixture(scope="module")
def recorded():
    return json.loads((HERE / "results" / "intake.json").read_text())


def _file(source, name):
    return (F / "sources" / source / "files" / name).read_bytes()


# ---------------------------------------------------------------- the corpus

def test_the_fixtures_are_exactly_what_the_generator_writes(tmp_path):
    corpus.generate(tmp_path)
    ours = sorted(p.relative_to(F) for p in F.rglob("*") if p.is_file())
    theirs = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("*") if p.is_file())
    assert ours == theirs
    for rel in ours:
        assert (F / rel).read_bytes() == (tmp_path / rel).read_bytes(), rel


def test_the_corpus_is_invented():
    """No real company names: every supplier is marked fictional and every domain is example.*."""
    assert all("(fictional)" in name for _, name, _ in corpus.SUPPLIERS)
    domains = set(re.findall(r"@([\w.-]+)", "".join(p.read_text(errors="ignore") for p in F.rglob("*.eml"))))
    assert domains and all(d.endswith(("example.com", "example.org")) for d in domains)


def test_the_assumptions_are_the_designs():
    """assumptions.yaml carries the design's s1 table, labelled as assumptions."""
    a = yaml.safe_load((HERE / "assumptions.yaml").read_text())
    design = (HERE.parent / "docs" / "designs" / "smartdms.md").read_text()
    assert a["status"] == "assumption"
    for value, text in ((a["documents_indexed"], "600,000"), (a["pages_per_document"], "15"),
                        (a["new_or_revised_documents_per_week"], "5,000"), (a["pages_per_week"], "75,000"),
                        (a["queries_per_day"], "20,000"), (a["users_named"], "3,000")):
        assert f"{value:,}" == text and text in design
    assert a["documents_indexed"] * a["pages_per_document"] == 9_000_000 and "9,000,000" in design
    assert a["share_scanned"] == 0.30 and "30%" in design


# ---------------------------------------------------------------- contracts and delivery

def test_every_source_record_becomes_one_envelope_or_a_named_rejection():
    for source, record in arrivals():
        out = contracts.to_event(source, record)
        if hasattr(out, "reason"):
            assert out.reason.startswith("contract: ")
        else:
            assert out.source == contracts.SOURCE[source] and out.id and out.subject


def test_a_record_that_breaks_its_contract_is_quarantined_and_the_rest_flow(base):
    planted = LAB["planted"]["contract_failures"]
    reasons = {x["key"]: x["reason"] for x in base.dead.items()}
    for p in planted:
        assert any(p["id"].split("/")[0] in k and "contract" in r for k, r in reasons.items()), p
    assert len(base.records()) == 160                       # every other document still arrived
    # the control: the same record with the field restored passes its contract
    sp = next(json.loads(x) for x in (F / "sources" / "sharepoint" / "items.jsonl").read_text().splitlines()
              if json.loads(x)["id"] == "sp-0010")
    assert contracts.check("sharepoint", sp) == ["listItem.fields.DocNumber missing"]
    sp["listItem"]["fields"]["DocNumber"] = sp["listItem"]["fields"].pop("DocumentNo")
    assert contracts.check("sharepoint", sp) == []


def test_a_material_number_is_required_on_certificates_only():
    rec = {"DOKNR": "X", "DOKVR": "01", "DOKAR": "QCO", "DKTXT": "t", "ACL": ["a"],
           "ORIGINAL": {"FILE": "f", "MIME": "m"}, "AEDAT": "2026-01-01", "MATNR": ""}
    assert contracts.check("sap", rec) == []
    assert contracts.check("sap", {**rec, "DOKAR": "QCA"}) == ["MATNR missing"]


def test_shuffled_duplicated_and_crashed_delivery_reaches_the_same_outcome(base):
    for seed in (1, 2):
        other = run_intake(shuffle_seed=seed, redeliver=0.25, crash_after=40)
        assert other.consumer.stats.duplicates_skipped > 0
        assert other.fingerprint() == base.fingerprint()


def test_without_the_processed_store_the_work_repeats_but_the_outcome_holds(base):
    """As in 12: the event store saves work; keying on the document is what keeps the outcome right."""
    control = run_intake(shuffle_seed=1, redeliver=0.25, crash_after=40, dedup=False)
    assert control.handler_runs > base.handler_runs
    assert control.fingerprint() == base.fingerprint()


def test_every_accepted_event_is_in_the_log_for_replay(base):
    assert len(base.log) == sum(1 for s, r in arrivals() if not hasattr(contracts.to_event(s, r), "reason")) + 10
    assert {e.type for e in base.log.read()} == {"document.upserted", "document.status_changed"}


# ---------------------------------------------------------------- lifecycle

def _ev(i, rev, to, at, number="SOP-X"):
    return StatusEvent(f"e{i}", number, rev, to, at)


def test_never_two_effective_revisions_at_any_step_in_any_order():
    import random

    feed = [StatusEvent(r["id"], r["doc_number"], r["revision"], r["to"], r["at"])
            for s, r in arrivals() if s == "status"]
    for seed in range(3):
        order = feed + random.Random(seed).sample(feed, 50)
        random.Random(seed + 10).shuffle(order)
        m = LifecycleMirror()
        for e in order:
            m.apply(e)
            assert max(m.effective_counts().values()) <= 1
    ref = LifecycleMirror()
    for e in feed:
        ref.apply(e)
    assert {n: {r: s.status for r, s in v.items()} for n, v in m.documents().items()} == \
        {n: {r: s.status for r, s in v.items()} for n, v in ref.documents().items()}


def test_a_new_effective_revision_supersedes_the_old_one_in_the_same_update():
    m = LifecycleMirror()
    for i, (rev, to, at) in enumerate([("01", "draft", "1"), ("01", "in_review", "2"), ("01", "approved", "3"),
                                       ("01", "effective", "4"), ("02", "draft", "5"), ("02", "in_review", "6"),
                                       ("02", "approved", "7"), ("02", "effective", "8")]):
        m.apply(_ev(i, rev, to, at))
    # the source's own "01 superseded" event has not arrived; 01 already reads superseded
    assert m.effective("SOP-X") == "02"
    assert m.revision("SOP-X", "01").status == "superseded" and m.revision("SOP-X", "01").superseded_by == "02"


def test_illegal_transitions_are_quarantined_not_applied(base):
    planted = {p["event"] for p in LAB["planted"]["illegal_transitions"]}
    assert {e.id for e in base.mirror.illegal()} == planted
    assert base.mirror.effective("SOP-QA-0019") is None            # obsolete stays obsolete
    assert base.mirror.revision("SPEC-RM-0007", "03").status == "draft"   # not pushed straight to effective


def test_an_event_that_only_looks_illegal_is_applied_once_its_predecessor_arrives():
    m = LifecycleMirror()
    m.apply(_ev(1, "01", "draft", "1"))
    m.apply(_ev(3, "01", "approved", "3"))                   # in_review has not arrived yet
    assert m.revision("SOP-X", "01").status == "draft" and len(m.illegal()) == 1
    m.apply(_ev(2, "01", "in_review", "2"))
    assert m.revision("SOP-X", "01").status == "approved" and m.illegal() == []


# ---------------------------------------------------------------- identity

def test_copies_in_a_second_system_are_one_record_conflicts_are_kept_apart(base):
    recs = base.registry.records()
    p = LAB["planted"]
    for c in p["identical_copies"]:
        assert {x.system for x in recs[c["key"]].locations} == {"qdms", "sharepoint"}
    for c in p["conflicting_copies"]:
        assert recs[c["key"]].locations[0].system == "qdms" and recs[c["key"]].conflicts
    m = p["misfiled_copies"][0]
    assert recs[m["filed_as"]].same_content_as == [m["key"]]


def test_the_system_of_records_copy_is_canonical_whatever_arrives_first():
    for order in (["sharepoint", "qdms"], ["qdms", "sharepoint"]):
        reg = Registry()
        for system in order:
            reg.register("SOP-1/01", system, f"{system}.pdf", "hash-" + system)
        rec = reg.records()["SOP-1/01"]
        assert rec.content_hash == "hash-qdms" and [x.system for x in rec.conflicts] == ["sharepoint"]


# ---------------------------------------------------------------- parsing and chunking

@pytest.mark.parametrize("name, style", [("SOP-QA-0003_01.pdf", "numbered"), ("SOP-QA-0002_02.pdf", "decimal"),
                                         ("SOP-QA-0001_02.pdf", "plain")])
def test_headings_are_found_in_all_three_styles(name, style):
    doc = parse(PDF, _file("qdms", name))
    assert [s.heading for s in doc.sections] == ["Purpose", "Scope", "Responsibilities", "Procedure", "Records",
                                                 "Revision history"], style
    assert doc.sections[-1].tables and doc.sections[-1].tables[0].header == ["Revision", "Change"]


def test_a_scan_keeps_each_lines_confidence_and_region():
    doc = parse(SCAN, _file("qdms", "SOP-QA-0002_01.json"))
    assert all(x.confidence is not None and len(x.region) == 8 for x in doc.all_lines())


def test_html_drops_navigation_and_footer():
    doc = parse(HTML, _file("qdms", "CC-2025-001_01.html"))
    assert "Intranet footer" not in doc.text() and "Home | Quality" not in doc.text()
    assert doc.kind_line == "CHANGE CONTROL"


def test_email_drops_quoted_history_and_returns_its_attachments():
    doc = parse(EML, _file("sap", "COR-2026-007_01.eml"))
    assert "purchasing wrote" not in doc.text() and "Please send the CoA" not in doc.text()
    assert len(doc.attachments) == 2 and all(a.name.startswith("COA-") for a in doc.attachments)


def test_a_spreadsheet_keeps_its_header_row():
    name = next(p.name for p in (F / "sources" / "sap" / "files").glob("*.xlsx"))
    doc = parse(XLSX, _file("sap", name))
    t = next(t for s in doc.sections for t in s.tables)
    assert t.header == ["Test", "Result", "Unit"] and t.rows


def test_a_file_that_will_not_parse_is_quarantined_with_its_reason(base, tmp_path):
    import shutil

    shutil.copytree(F, tmp_path / "f")
    (tmp_path / "f" / "sources" / "qdms" / "files" / "SOP-QA-0001_01.pdf").write_bytes(b"%PDF-1.4 broken")
    other = run_intake(tmp_path / "f")
    assert any("parse: pdf" in x["reason"] for x in other.dead.items())
    assert len(other.records()) == len(base.records()) - 1


def test_a_long_section_is_split_with_overlap_and_a_large_table_repeats_its_header():
    lines = [Line(f"Sentence {i} " + "word " * 40) for i in range(30)]
    rows = [[f"P{i}", str(i), "u"] for i in range(400)]
    doc = Parsed("pdf", "", "T", [], [Section(1, "Procedure", lines), Section(2, "Limits", [],
                                                                       [Table("Limits", ["A", "B", "C"], rows)])])
    chunks = chunk("K/01", doc, max_tokens=200)
    proc = [c for c in chunks if c.section_path == "1 Procedure"]
    assert len(proc) > 1 and all(c.token_count <= 200 + 60 for c in proc)
    assert proc[0].text.split("\n")[-1] == proc[1].text.split("\n")[1]       # the overlap
    tables = [c for c in chunks if c.is_table]
    assert len(tables) > 1 and all(c.text.split("\n")[1] == "A | B | C" for c in tables)
    assert sum(len(c.text.split("\n")) - 2 for c in tables) == 400            # every row, once


def test_an_unchanged_section_is_not_embedded_again(base):
    r1 = chunk("SOP-QA-0001/01", parse(PDF, _file("qdms", "SOP-QA-0001_01.pdf")))
    r2 = chunk("SOP-QA-0001/02", parse(PDF, _file("qdms", "SOP-QA-0001_02.pdf")))
    cache = EmbeddingCache(HashingEmbeddings())
    cache.vectors(r1)
    before = cache.computed
    cache.vectors(r2)
    unchanged = len({c.chunk_hash for c in r2} & {c.chunk_hash for c in r1})
    assert unchanged > 0 and cache.computed - before == len({c.chunk_hash for c in r2}) - unchanged
    assert base.embeddings.reused > 0


# ---------------------------------------------------------------- personal data

def test_personal_data_is_redacted_and_approvers_are_kept(base):
    text = {k: "\n".join(c.text for c in r.chunks) for k, r in base.records().items()}
    for p in LAB["planted"]["pii"]:
        if p["key"] in text and p["kind"] != "name_in_text":
            assert p["text"] not in text[p["key"]], p
    kept = [p for p in LAB["planted"]["pii_kept"] if p["key"] in text]
    assert kept and all(p["name"] in text[p["key"]] for p in kept)     # the control: names do survive


def test_a_name_in_free_text_is_not_caught_by_the_rules(base):
    """The limit of the stand-in, pinned so it cannot be forgotten: rules know forms, not prose."""
    text = {k: "\n".join(c.text for c in r.chunks) for k, r in base.records().items()}
    free = [p for p in LAB["planted"]["pii"] if p["kind"] == "name_in_text" and p["key"] in text]
    assert free and all(p["text"] in text[p["key"]] for p in free)


def test_role_mailboxes_are_not_personal_data():
    doc = Parsed("eml", "", "t", [Line("From: qa@sup-2001.example.com")],
                 [Section(1, "Message", [Line("Write to jane.doe@example.org or qa@example.com")])])
    red, found = redact(doc)
    assert red.header[0].text == "From: qa@sup-2001.example.com"
    assert red.sections[0].lines[0].text == "Write to [REDACTED:EMAIL] or qa@example.com"
    assert [f.kind for f in found] == ["email"]


# ---------------------------------------------------------------- classification

def test_no_document_is_filed_as_the_wrong_type(recorded):
    c = recorded["classification"]
    assert c["wrong_type_filed"] == 0
    assert c["correct"] + c["to_intake_review"] == c["documents"]


def test_misleading_source_metadata_does_not_decide_the_type(base):
    for p in LAB["planted"]["misleading_metadata"]:
        r = base.records()[p["key"]]
        assert r.doc_type == "CMP" and any(f.rule == "type_disagrees_with_metadata" for f in r.failures)


def test_below_the_threshold_a_document_goes_to_intake_review():
    doc = Parsed("pdf", "", "Untitled", [Line("Note: nothing to go on")], [])
    c = classify(doc, "SOP")
    assert c.doc_type is None and c.review and "below" in c.reason


def _model(reply):
    m = ScriptedChatModel()
    m.load([Step(AIMessage(content=reply))])
    return m


@pytest.mark.parametrize("reply, accepted", [
    ('{"doc_type": "SOP", "confidence": 0.92, "rationale": "Says so on page 1."}', True),
    ('{"doc_type": "SOP", "confidence": 0.4, "rationale": "Unsure."}', False),
    ('{"doc_type": "MEMO", "confidence": 0.95, "rationale": "Not a type."}', False),
    ('{"doc_type": "SOP", "confidence": 7, "rationale": "Out of range."}', False),
    ("It is probably an SOP.", False),
])
def test_the_model_path_accepts_only_a_reply_that_fits_the_schema(reply, accepted):
    doc = parse(PDF, _file("qdms", "SOP-QA-0001_02.pdf"))
    c = classify_with_model(doc, _model(reply))
    assert c.by == "model" and (not c.review) == accepted
    assert (c.doc_type == "SOP") == accepted


# ---------------------------------------------------------------- extraction and validation

def test_every_value_has_the_page_it_came_from_and_scan_values_their_confidence(base):
    recs = base.records()
    assert all(f.page >= 1 for r in recs.values() for f in r.fields.values())
    scan = recs["SOP-QA-0002/01"]
    assert scan.fields["revision"].confidence is not None and scan.fields["revision"].region


def test_nothing_is_silently_corrected(base):
    """Every header value is exactly what its line says, failed validation or not."""
    for r in base.records().values():
        lines = [x.text for x in r.parsed.header]
        for f in r.fields.values():
            if f.source == "content" and isinstance(f.value, str) and f.name != "title":
                assert any(f.value in x for x in lines), (r.key, f.name, f.value)
    bad = base.records()["DEV-2025-006/01"]
    assert bad.fields["opened"].value == "2025-02-31" and bad.review


def test_every_planted_validation_failure_goes_to_review(recorded, base):
    v = recorded["validation"]
    assert v["missed"] == 0 and v["other_on_born_digital_documents"] == []
    for p in LAB["planted"]["validation_failures"]:
        assert base.records()[p["key"]].review, p


@pytest.mark.parametrize("fields, rule", [
    ({"revision": "A"}, "bad_revision_format"),
    ({"opened": "2025-13-01"}, "bad_date"),
    ({"material_id": "RM-00000"}, "unknown_material"),
    ({"supplier_id": "SUP-2005"}, "supplier_not_approved"),
    ({"supplier_id": "SUP-0000"}, "unknown_supplier"),
    ({"site": "Atlantis"}, "site_not_allowed"),
])
def test_each_rule_fires_on_its_own_and_not_on_a_clean_value(fields, rule):
    clean = {"doc_number": "DEV-2025-001", "revision": "01", "site": "Northfield", "opened": "2025-02-03",
             "material_id": "RM-10001", "lot": "L1", "approved_by": "A B", "supplier_id": "SUP-2001"}
    mk = lambda d: {k: Field(k, v, 1, None, None) for k, v in d.items()}  # noqa: E731
    assert validate("DEV", mk(clean), {}) == []
    assert [f.rule for f in validate("DEV", mk({**clean, **fields}), {})] == [rule]


def test_a_value_read_below_the_ocr_floor_goes_to_review():
    f = {"doc_number": Field("doc_number", "COA-2001-L26-0001", 1, None, 0.95),
         "lot": Field("lot", "L26-0001", 1, None, 0.62)}
    assert "ocr_confidence_below_floor" in [x.rule for x in validate("COA", f, {})]


# ---------------------------------------------------------------- results, status, app

def test_the_recorded_results_are_what_the_code_produces_now(recorded):
    """README and the app quote results/intake.json; this fails until it is regenerated."""
    assert json.loads(json.dumps(summary(), sort_keys=True)) == recorded


def test_the_readme_quotes_the_recorded_results(recorded):
    readme = (HERE / "README.md").read_text()
    p, c, v = recorded["personal_data"], recorded["classification"], recorded["validation"]
    for phrase in (f"{p['redacted']} of {p['planted']}", f"{c['correct']} of {c['documents']}",
                   f"{v['caught_by_its_rule']} of {v['planted']}",
                   f"{recorded['extraction']['correct']} of {recorded['extraction']['of']}"):
        assert phrase in readme, phrase


def test_the_status_table_in_the_readme_is_rendered_from_status_yaml():
    readme = (HERE / "README.md").read_text()
    assert status.block() in readme


def test_every_proof_in_the_status_table_exists():
    data = status.load()
    assert [s["section"] for s in data["sections"]] == list(range(1, 24))
    assert all(s["status"] in data["statuses"] for s in data["sections"])
    tests = "".join(p.read_text() for p in (HERE / "tests").glob("test_*.py"))
    for s in data["sections"]:
        for p in s.get("proof", []):
            path, _, name = p.partition("::")
            assert (HERE / path).exists(), p
            if name:
                assert f"def {name}(" in tests, p


def _page():
    from streamlit.testing.v1 import AppTest

    return AppTest.from_file(str(HERE.parent / "app" / "pages" / "4_Document_Control.py"), default_timeout=120).run()


def test_the_app_tab_opens_offline_on_the_build_status():
    at = _page()
    assert not at.exception
    assert any("Build status" in t.label for t in at.tabs)
    assert any("Planned" in str(df.value.to_dict()) for df in at.dataframe)


def test_the_app_tab_shows_the_recorded_intake_numbers(recorded):
    at = _page()
    shown = {m.label: m.value for m in at.metric}
    c = recorded["classification"]
    assert shown["Classified correctly"] == f"{c['correct']} of {c['documents']}"
    assert shown["Filed as the wrong type"] == "0"
