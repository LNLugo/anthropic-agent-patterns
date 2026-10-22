# 15 Document control (SmartDMS)

AI document management for quality and regulatory documents, built from the specification in [docs/designs/smartdms.md](../docs/designs/smartdms.md), section by section. The build plan is [docs/designs/smartdms-build-brief.md](../docs/designs/smartdms-build-brief.md).

The principles it is built on:
- **Deterministic first, model second.** Rules, parsers and code make every decision in this step. A model is added only where a measurement shows rules fall short.
- **The AI recommends, a person approves.** Anything uncertain or invalid goes to review. Nothing is silently corrected.
- **Effective versions only, by default.** At most one revision of a document is effective at any moment.
- **Every claim cites document, revision and section, or the system abstains.**
- **Everything synthetic and unbranded.** Sites, people, suppliers, materials and documents are invented.
- **Every number here is produced by the repo**, in [results/intake.json](results/intake.json).

```bash
cd 15_document_control
python -m docctl.corpus       # rewrite the fixtures (a test checks they reproduce)
python run_intake.py          # intake over every fixture; writes results/intake.json
python run_retrieval.py       # retrieval on the development questions; writes results/retrieval.json
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
| 7 | Embeddings | Built as in-process stand-in | 2 | `docctl/index.py`, `test_every_vector_names_its_model_and_a_query_from_another_model_is_refused`, `test_a_new_index_replaces_the_old_only_if_it_does_as_well_and_can_be_rolled_back` | HashingEmbeddings stands in for text-embedding-3-large at 1,024 dimensions; the candidate model compared is a second stand-in. Title and summary vectors are not built: no gain has been measured to justify them. |
| 8 | Indexing, ANN and sizing | Built as in-process stand-in | 2 | `docctl/index.py`, `test_queries_keep_using_the_old_index_until_the_flip`, `test_a_new_effective_revision_replaces_the_old_in_one_write` | Search is exact over every row. HNSW, int8 quantization and rescoring are the design's parameters (s8), not exercised here. Sizing is PR 4. |
| 9 | Retrieval and reranking | Built as in-process stand-in | 2 | `docctl/retrieve.py`, `test_no_default_query_returns_a_superseded_revision_and_the_control_does`, `test_no_one_is_shown_a_document_they_may_not_open_and_the_control_sees_them`, `test_a_named_document_short_circuits_to_that_document_only`, `test_the_answerability_threshold_is_the_calibrated_midpoint` | 07's fusion over BM25 and vectors. Not built: a reranker (07's needs a model download), conversation rewriting, language detection. |
| 10 | Comparison engine | Planned | 3 |  |  |
| 11 | Prompt orchestration | Built | 2 | `docctl/assistant.py`, `test_every_development_question_is_routed_by_rules`, `test_the_context_is_packed_labelled_and_within_budget`, `test_a_tool_runs_only_from_the_question_and_only_from_the_allowlist` | Routing is rules: every development question routes correctly, so 08's model router is not needed yet. Prompt registration in MLflow is not built. |
| 12 | Hallucination mitigation and safety | Built as in-process stand-in | 2 | `test_a_sentence_citing_something_it_was_not_shown_is_removed`, `test_an_answer_mostly_unsupported_becomes_an_abstention`, `test_instructions_inside_a_supplier_document_never_trigger_a_tool`, `test_access_revoked_after_retrieval_is_applied_before_the_answer`, `test_personal_data_in_the_output_is_redacted` | Rules stand in for Content Safety and Prompt Shields. Attribution is citation validity (07's validator), not a model judging support. |
| 13 | Human in the loop and audit trail | Planned | 3 |  |  |
| 14 | Caching and freshness | Built | 2 | `docctl/cache.py`, `test_people_with_different_access_never_share_a_cached_answer`, `test_a_status_change_purges_the_answers_that_cite_the_document`, `test_retrieval_results_expire_after_fifteen_minutes` | No rerank cache: there is no reranker. Document processing is keyed on content hash at intake, not yet on pipeline version. |
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
| Azure OpenAI embeddings | llm_provider's HashingEmbeddings: a lexical stand-in, so vector results are a word-matching baseline |
| Azure AI Search (BM25 | docctl/index.py: exact search over every row, BM25 from rank_bm25, the same filters |
| Semantic ranker or hosted cross-encoder | none: section collapse only (07's cross-encoder needs a model download) |
| Azure AI Content Safety, Prompt Shields | rules: instruction-like text flagged at ingest, release and approval decisions refused, output scanned for personal data |
| Azure OpenAI (answers) | llm_provider; ScriptedChatModel in tests, so answer quality is not measured |
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

## What retrieval measured

All figures are from [results/retrieval.json](results/retrieval.json), on 43 development questions in [fixtures/questions.yaml](fixtures/questions.yaml). Each question lists the sections that answer it, as its asker may read them, and the sections it must never return.

The vector half of the search is the hashing stand-in, so these numbers test the filters and the ranking over a word-matching baseline, not a real embedding model. Answers are produced only by the scripted model in tests, which checks the plumbing (citations, abstention, access, tools), not answer quality.

**Effective versions only.** Twelve questions ask about content that changed between revisions. Across all default questions, 0 superseded or obsolete sections were returned. The control, with the status filter switched off, returned 123 superseded or obsolete sections to the same questions. Four questions ask for an older revision by name: they get it, and every such section is labelled as not the current version.

**Access.** 0 sections were returned to someone who may not open them, and a person in no group got nothing. The control, asking the complaint questions with every group, got 16 forbidden sections, so the zero is the filter working. A complaint number that the asker cannot open behaves exactly like a number that does not exist.

**Finding the evidence.** 29 of 29 expected sections were found. 13 questions name a document and go straight to it. The others are found by BM25 and vectors fused with 07's reciprocal rank fusion. Two changes were needed on the way, each found by a question that failed:
- **A document context line.** Every chunk is indexed with its document's number, title, material, supplier and lot, so a results table that never names its lot is still found by the lot.
- **Revision left out of that line.** This keeps an unchanged section's indexed text identical across revisions. 147 of 871 chunks were not embedded again.

**Knowing when not to answer.** 38 of 38 questions were correctly answered or declined. The test is how much of the question's distinctive vocabulary the best sections contain. On development questions the lowest answerable scored 0.681 and the highest unanswerable 0.316. The threshold is the midpoint, 0.5, and a test keeps it equal to the measured midpoint. The raw BM25 score was tried first and failed to separate the two before the context line was added; it separates them now too.

**Routing.** 43 of 43 questions were routed correctly by rules: release and approval decisions refused, allowlisted lookups, finding a named document, and questions. 08's model router is not needed until a question set shows rules falling short.

**A new embedding model.** It builds a shadow index and is compared on vector-only recall. The candidate found 15 of 19 against 15 for the model in use, so the alias flipped. A deliberately meaningless model found 4 and was refused. A query embedded with another model is refused, and rollback is one pointer change.

**Safety.**
- **Planted instructions.** One supplier certificate tells the reader to release the lot and call a release tool. It is flagged at ingest and labelled in the prompt as data. The generation step has no tools, so a model reply asking for one is counted and ignored.
- **Citations.** A sentence citing a chunk it was not shown, or quoting words that are not there, is removed by 07's citation validator. If fewer than half the sentences survive, the answer becomes an abstention.
- **Access revoked mid-answer.** If access is revoked between retrieval and the answer, the sentence citing that document is removed.
- **Personal data in the output** is redacted with intake's rules.

**Caching.** An answer is cached on the question, the asker's access fingerprint and the index in use. It is purged in the same step that changes the status, revision or readers of any document it cites. Two people with different access never share an entry.

**Controls checked by breaking them.** Eight PR 2 controls were broken one at a time:
- the status filter;
- the access filter;
- 07's citation check;
- the answer-time access recheck;
- the injection label;
- the cache purge;
- the section collapse;
- the release refusal.

The suite caught all eight.

**Read with care.**
- **Development questions only.** All 43 questions were written by the same hand that built the retriever, and the threshold was calibrated on them. A held-out set written after this retriever is frozen is what will test it, as in 14.

## Not built yet

These are named so nobody assumes they exist:
- **The model path for classification and extraction.** It is tested for schema, unknown types and confidence. Its quality is not measured.
- **Write-back of review decisions** to the source DMS (PR 3).
- **Real OCR.** Scans are layout results with planted noise.
- **A reranker, conversation rewriting, language detection** (s9), and prompt registration in MLflow (s11).
- **A held-out question set for retrieval**, and any live measurement of answers.
- **Free-text name detection.**
