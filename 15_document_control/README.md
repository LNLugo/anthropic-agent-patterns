# 15 Document control (SmartDMS)

AI document management for quality and regulatory documents, built from the specification in [docs/designs/smartdms.md](../docs/designs/smartdms.md), section by section. The build plan is [docs/designs/smartdms-build-brief.md](../docs/designs/smartdms-build-brief.md).

The principles it is built on:
- **Deterministic first, model second.** Rules, parsers and code make every decision in this step. A model is added only where a measurement shows rules fall short.
- **The AI recommends, a person approves.** Anything uncertain or invalid goes to review. Nothing is silently corrected.
- **Effective versions only, by default.** At most one revision of a document is effective at any moment.
- **Every claim cites document, revision and section, or the system abstains.** (From PR 2, when answers exist.)
- **Everything synthetic and unbranded.** Sites, people, suppliers, materials and documents are invented.
- **Every number here is produced by the repo**, in [results/intake.json](results/intake.json).

```bash
cd 15_document_control
python -m docctl.corpus       # rewrite the fixtures (a test checks they reproduce)
python run_intake.py          # intake over every fixture; writes results/intake.json
python -m pytest -q           # offline, no key
streamlit run ../app/Agent_Patterns.py   # then "Document Control" in the sidebar
```

## Build status

One row per section of the specification. Rendered from [status.yaml](status.yaml); a test checks the table matches it and that every file and test it names exists.

<!-- status:begin (rendered from status.yaml) -->
| § | Section | Status | PR | Proof | Note |
|---|---|---|---|---|---|
| 1 | Planning assumptions | Built | 1 | `assumptions.yaml`, `test_the_assumptions_are_the_designs` | The inputs only. sizing.py, which derives every sizing and cost figure from them, is PR 4. |
| 2 | Requirements and service levels | Planned | 4 |  |  |
| 3 | Architecture overview | Planned | 4 |  | The stand-in map below covers the parts built so far. |
| 4 | Document lifecycle and system of record | Built | 1 | `docctl/lifecycle.py`, `docctl/identity.py`, `test_never_two_effective_revisions_at_any_step_in_any_order`, `test_illegal_transitions_are_quarantined_not_applied`, `test_the_system_of_records_copy_is_canonical_whatever_arrives_first` | Write-back of review decisions to the DMS moves to PR 3, where the decisions exist. |
| 5 | Ingestion, parsing and chunking | Built as in-process stand-in | 1 | `docctl/contracts.py`, `docctl/eventlog.py`, `docctl/parse.py`, `docctl/chunking.py`, `docctl/pii.py`, `test_a_record_that_breaks_its_contract_is_quarantined_and_the_rest_flow`, `test_shuffled_duplicated_and_crashed_delivery_reaches_the_same_outcome`, `test_an_unchanged_section_is_not_embedded_again`, `test_personal_data_is_redacted_and_approvers_are_kept` | OCR is a layout-result stand-in; PII detection is rules; Event Hubs is an append-only log. |
| 6 | Classification, extraction and validation | Built | 1 | `taxonomy.yaml`, `docctl/classify.py`, `docctl/extract.py`, `docctl/validate.py`, `test_no_document_is_filed_as_the_wrong_type`, `test_every_planted_validation_failure_goes_to_review`, `test_nothing_is_silently_corrected` | Rules make every decision. The model path is built and tested as plumbing; its quality is not claimed. |
| 7 | Embeddings | Planned | 2 |  |  |
| 8 | Indexing, ANN and sizing | Planned | 2 |  |  |
| 9 | Retrieval and reranking | Planned | 2 |  |  |
| 10 | Comparison engine | Planned | 3 |  |  |
| 11 | Prompt orchestration | Planned | 2 |  |  |
| 12 | Hallucination mitigation and safety | Planned | 2 |  | The prompt injection fixture is already in the corpus (COA-2004-L26-0689). |
| 13 | Human in the loop and audit trail | Planned | 3 |  |  |
| 14 | Caching and freshness | Planned | 2 |  |  |
| 15 | Multilingual handling | Planned | 4 |  |  |
| 16 | Scalability, capacity and resilience | Planned | 4 |  | The event log that the rebuild-by-replay test will use is built (s5). |
| 17 | Observability and evaluation | Planned | 4 |  |  |
| 18 | Cost model and trade offs | Planned | 4 |  |  |
| 19 | Fallback strategies | Planned | 4 |  |  |
| 20 | Interfaces | Planned | 4 |  |  |
| 21 | Deployment and rollout | Planned | 4 |  |  |
| 22 | Partner delivery governance | Planned | 4 |  |  |
| 23 | Open questions for discovery | Documented only | 1 | `../docs/designs/smartdms.md` |  |

**Stand-ins (ADR 0015).**

| Managed service | Stand-in here |
|---|---|
| Azure AI Document Intelligence (read and layout) | pypdf for native PDFs; a layout-result JSON (lines with confidence, tables as cells) for scans |
| Event Grid and Event Hubs | 12's envelope and in-process broker, plus an append-only event log for replay |
| Azure Blob Storage or ADLS Gen2 (landing) | fixtures/sources/*/files and an in-memory blob store for attachments |
| Azure AI Language PII detection | rules for the forms the sources use (docctl/pii.py) |
| Databricks Delta tables over SAP master data | fixtures/master/*.csv |
| Azure OpenAI (classification | llm_provider; scripted in tests, plumbing only |
| Azure OpenAI embeddings | llm_provider's HashingEmbeddings, used only to count what needs embedding |
<!-- status:end -->

## The data

A seeded (15), synthetic document estate held in three invented source systems, each with its own metadata shape:

| Source | Holds | Metadata shape |
|---|---|---|
| Quality DMS (`qdms`) | SOPs, specifications, change controls, deviations; the status change feed. The system of record for lifecycle | Flat records: `DocumentNumber`, `Revision`, `Readers` |
| Team sites (`sharepoint`) | Complaint summaries; copies of some SOPs | Graph-style: `listItem.fields.DocNumber` |
| ERP-linked documents (`sap`) | Certificates of analysis as PDFs, scans and spreadsheets; supplier emails carrying them | `DOKNR`, `DOKVR`, `MATNR`, `LIFNR` |

There are 166 source records (121, 23 and 22) and 524 status events, covering 165 document revisions in seven types and five formats: native PDF, OCR layout results standing in for scans, HTML, email and spreadsheets.

Cases are planted and recorded in [fixtures/labels.yaml](fixtures/labels.yaml), so every count below is checked against ground truth:
- contract failures;
- copies held in two systems (identical, edited, and filed under the wrong number);
- illegal status changes;
- personal data (some in forms the redactor was not written for);
- validation failures;
- misleading source metadata;
- OCR noise.

Two cases are planted now and used in later PRs: out-of-specification certificate results (PR 3) and a prompt injection inside a supplier certificate (PR 2).

## What intake measured

All figures are from `results/intake.json`. No model is called.

**Contracts and delivery.** 6 of 6 planted contract failures were quarantined with their reason, through 12's dead-letter queue; every other record arrived. Each source's contract has a named owner. A failure is per record: one bad record never holds up the rest. This differs from 14, where one bad log line stops the rebuild.

Delivery was tested three times, each with a shuffled arrival order, a quarter of events sent twice, and a consumer crash mid-run. Every run reached the same outcome as in-order delivery. The control, without the processed store, ran the handler 213 times instead of 170 and still reached the same outcome. As in 12, the event store saves work; keying the work on the document is what keeps the outcome right.

**Lifecycle.** The mirror never showed more than 1 effective revision of a document, across 655 steps of a shuffled, duplicated replay. The replay's final state is the same as in-order delivery. Both planted illegal transitions (an obsolete SOP reactivated, a draft pushed straight to effective) were quarantined, and nothing else was. Legality is decided on the ordered history, so an event that only looks illegal because its predecessor is late is applied when the predecessor arrives.

**Identity.** All 8 SOPs copied unchanged to a team site are one record with two locations. Both edited copies are kept apart as conflicts, with the quality DMS's copy canonical whichever arrives first. The copy filed under the wrong number is flagged as having the same content as the original.

**Parsing and chunking.** 160 documents produced 871 chunks, 114 of them tables kept whole. All 10 email attachments re-entered intake as documents of their own. 41% of chunks were unchanged from an earlier revision and were not embedded again.

**Personal data.** 55 of 65 planted items were redacted, with no redactions of anything else, and 105 of 105 approver names were kept, as the controlled record requires. The 10 misses are all names written in free text ("her neighbour ..."), outside the forms the rules know. That is the limit of a rules stand-in, and a test pins it.

**Classification.** 156 of 159 documents were classified correctly and 3 went to intake review. 0 were filed as the wrong type. The three are scans where OCR misread the document number and the line naming the type. Two complaints that their team site had filed as SOPs were classified by their content and flagged.

**Extraction and validation.** 991 of 996 values were extracted correctly, every one with the page it came from. All 875 on born-digital documents are correct, and 116 of 121 on scans. Of 16 planted validation failures, 15 of 16 were caught by the rule written for them. The other one (an unapproved supplier) was caught as a missing field, because OCR misread the label. None were missed. The only other failures are 3 on damaged scans, and none on born-digital documents. No value is ever changed: validation records the failure and sends the document to review.

**Controls checked by breaking them.** Six controls were broken one at a time:
- the same-update supersede;
- the state machine's legality check;
- the approver-name guard;
- the approved-supplier rule;
- system-of-record priority;
- the one-document-once key.

The suite caught five. The sixth, the approver-name guard, changed nothing, because no rule ever targeted an approver line. It was dead code and was removed. The test that every approver name survives still stands.

**Read with care.**
- **One author.** The same hand wrote the documents and the rules, so the born-digital figures are an upper bound, not an estimate of production accuracy.
- **What is real here** is the handling of what goes wrong: OCR damage, contract breaks, late and duplicated events, conflicting copies.
- **A held-out document set** written after these rules are frozen is what will measure them, as in 14.

## Not built yet

These are named so nobody assumes they exist:
- **The model path for classification and extraction.** It is tested for schema, unknown types and confidence. Its quality is not measured.
- **Write-back of review decisions** to the source DMS (PR 3).
- **Real OCR.** Scans are layout results with planted noise.
- **Free-text name detection.**
