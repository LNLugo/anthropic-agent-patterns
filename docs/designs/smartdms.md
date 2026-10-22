# SmartDMS: AI Document Management for Quality

Solution architecture and design. Draft for discussion. Luis N. Lugo, October 2026.

> **Status:** design. This document is the specification for module `15_document_control`. Each section is either built in the module, built as an in-process stand-in, or documented only; the module README carries that status section by section.

**Purpose.** End to end design for an AI document management capability that aggregates quality and regulatory documents, automates classification, extraction, validation, comparison and summarization, keeps qualified people in the loop, and runs on an Azure and Databricks platform. Volumes and service levels below are planning assumptions to confirm in discovery; every sizing figure is derived from them so it can be recomputed.

## 1. Planning assumptions

| **Assumption**                                                  | **Value**                                  | **Used for**                     |
|-----------------------------------------------------------------|--------------------------------------------|----------------------------------|
| Documents indexed (effective plus retained superseded versions) | 600,000                                    | Index sizing, backfill cost      |
| Average pages per document                                      | 15                                         | OCR cost, chunk count            |
| Pages in backfill                                               | 9,000,000                                  | 600,000 x 15                     |
| Chunks (about one 500 to 800 token chunk per page)              | about 10,000,000                           | Vector index sizing              |
| New or revised documents per week                               | 5,000 (75,000 pages)                       | Run rate processing cost         |
| Users                                                           | 3,000 named, 300 concurrent at peak        | Query capacity                   |
| Queries                                                         | 20,000 a day, peak about 5 per second      | Query capacity and cost          |
| Share of documents that are scans                               | 30%                                        | OCR lane sizing                  |
| Languages                                                       | English first, plus major market languages | Analyzers, embeddings, gold sets |

## 2. Requirements and service levels

### Functional

- **Aggregate:** one search, Q&A and review experience over existing document systems (quality DMS, SharePoint, SAP linked documents). Source systems remain the system of record.

- **Find the right version:** effective version by default; superseded versions only on explicit request and clearly labeled.

- **Automate review:** classify documents into Quality's taxonomy, extract and validate metadata and key values, compare documents, summarize, and route anything uncertain to a person.

- **Human control:** AI recommends and drafts; a qualified person approves. Approval, electronic signature and status change stay in the system of record.

- **Audit:** every AI output traceable to source document and version, model and prompt version, and the reviewer's decision.

### Service levels (proposed)

| **Measure**                                            | **Target**                                                             |
|--------------------------------------------------------|------------------------------------------------------------------------|
| Search results (no generation)                         | P50 400 ms, P95 1 s                                                    |
| Q&A answer                                             | P50 1 s to first token, P95 3 s to first token, answer streamed        |
| Text document, arrival to searchable                   | P95 15 minutes                                                         |
| Scanned document over 100 pages, arrival to searchable | P95 60 minutes                                                         |
| Version comparison, 50 page document                   | P95 5 minutes, asynchronous                                            |
| Permission revocation effective                        | Under 5 minutes, plus live recheck of cited documents at answer time   |
| Availability, query path                               | 99.9% during business hours across regions served                      |
| Cross security domain leakage                          | Zero, enforced by query filters and proven by an automated canary test |

## 3. Architecture overview

Four paths share one platform: a processing path (documents in), a query path (questions in), a review path (people deciding) and a control plane (identity, policy, versions, evaluation, cost).

| **Layer**                                 | **Azure and Databricks components**                                                                        | **Responsibility**                                |
|-------------------------------------------|------------------------------------------------------------------------------------------------------------|---------------------------------------------------|
| Connectors and landing                    | Source APIs and change feeds, Azure Blob Storage or ADLS Gen2, Event Grid, Event Hubs                      | Change capture, durable landing, replay           |
| Processing orchestration                  | Azure Durable Functions or Container Apps jobs; Databricks Workflows for backfill                          | Per document pipeline, retries, lanes             |
| OCR and layout                            | Azure AI Document Intelligence (read and layout models)                                                    | Text, tables, checkboxes, signatures              |
| Classification, extraction, summarization | Azure OpenAI (small and large tiers); Databricks AI Functions for batch backfill                           | Type, fields, summaries, change explanation       |
| Structured reference data                 | Databricks Delta tables over SAP material, supplier and specification data; Unity Catalog                  | Validation lookups, lineage                       |
| Search                                    | Azure AI Search: BM25 plus HNSW vectors, semantic ranker or hosted cross encoder                           | Hybrid retrieval with security and status filters |
| Q&A and comparison services               | Containerized APIs on AKS or Container Apps, API Management in front                                       | Orchestration, citations, verification            |
| Review workbench                          | Web app; writes decisions back to the DMS API                                                              | Accept, edit, reject; audit capture               |
| Identity and security                     | Entra ID, managed identities, private endpoints, Key Vault with customer managed keys                      | AuthN, AuthZ, encryption                          |
| Safety                                    | Azure AI Content Safety, Prompt Shields, PII detection                                                     | Input, retrieved content and output checks        |
| Observability and evaluation              | Azure Monitor and Application Insights via OpenTelemetry; MLflow for prompt, model and evaluation tracking | Traces, SLOs, quality gates, cost                 |

## 4. Document lifecycle and system of record

- **Decision: intelligence layer, not a migration.** SmartDMS indexes and enriches documents held elsewhere. Replacing the validated DMS would turn an AI program into a revalidation program; a layer ships in months and keeps validation scope contained.

- **Lifecycle states mirrored from source:** draft, in review, approved, effective, superseded, obsolete. Every chunk carries status, revision, effective date and superseded_by.

- **Status is a filter, not a ranking signal.** Default queries return effective content only. Retrieving a superseded SOP is treated as a defect, not a relevance miss.

- **Write back:** review decisions, approvals and signatures go to the system of record through its API; SmartDMS stores records of assistance (what the AI produced, who accepted it), versioned and auditable.

- **Canonical identity:** document number plus revision is the business key; content hash detects the same file held in two systems; one canonical record lists all locations.

## 5. Ingestion, parsing and chunking

### Connectors and change capture

- Event driven where the source supports it (webhooks, change feeds, Microsoft Graph delta for SharePoint); scheduled delta polling otherwise; full reconciliation weekly to catch missed events.

- Every source has a named owner and a data contract: expected schema and metadata fields, checked on arrival. A contract failure alerts and quarantines rather than failing silently.

- Events land in Event Hubs partitioned by source; retention of 7 days allows replay after an outage or a parser fix.

### Parsing and normalization

| **Format**                      | **Handling**                                                                                                        |
|---------------------------------|---------------------------------------------------------------------------------------------------------------------|
| Native PDF                      | Document Intelligence read model for text heavy types; layout model only for types with tables, forms or checkboxes |
| Scanned PDF and images          | Layout model with OCR in a separate heavy lane; OCR confidence stored per line and propagated to extracted values   |
| Word, HTML                      | Converted to structured text keeping headings, numbering and tables; boilerplate removed                            |
| Email (supplier correspondence) | Split per message, quoted history removed, attachments routed back through intake as their own documents            |
| Spreadsheets                    | Per sheet and per table, header row kept, rendered to text plus a structured copy for exact lookups                 |

### Chunking

- **Structure first:** split on headings and section numbers, since SOPs and specifications are written by section. Target 500 to 800 tokens, 15% overlap only when a section must be split. Tune on the gold set.

- **Tables:** kept whole; if too large, split by rows with the header repeated; a generated one paragraph description is indexed alongside for retrieval.

- **Parent and child:** retrieve the section chunk, give the generator the parent section when the answer needs surrounding context.

- **Metadata on every chunk:** doc_id, doc_number, revision, status, effective_date, doc_type, site, owner, language, acl_groups, section_path, page_range, content_hash, ocr_confidence, embedding_model_id.

### Dedup and PII

- SHA 256 of normalized text at document and chunk level. Unchanged chunks in a new revision keep their embeddings; only changed sections are re embedded.

- Quality documents carry limited PII (names, signatures, occasionally employee or consumer data in complaints). Detected at intake with Azure AI Language PII detection; consumer and employee personal data redacted in indexed text; names of document approvers kept as they are part of the record. Originals stay in the source system under its controls.

## 6. Classification, extraction and validation

- **Taxonomy:** owned by Quality and versioned like code: document types, required fields per type, allowed values.

- **Classification:** first pages, headers and layout signals into a small model with a constrained output schema (type, confidence, rationale). Threshold per type; below threshold goes to intake review.

- **Extraction:** Document Intelligence fields and tables, then an LLM with a JSON schema per type. Every value stores the page and bounding region it came from so the reviewer sees it in place.

- **Validation rules:** required fields present, date and revision formats, document number uniqueness, material and supplier IDs reconciled against SAP master data in Delta tables, specification values within allowed ranges, OCR confidence above floor. Any failure routes to review; nothing is silently corrected.

- **Summaries:** section cited; generated per document type template (purpose, scope, key requirements, changes since last revision).

## 7. Embeddings

- **Model:** Azure OpenAI text-embedding-3-large reduced to 1,024 dimensions (the model supports shortened outputs), cosine similarity on normalized vectors. Compared against a multilingual open model on the gold set before committing.

- **Multilingual versus specialized:** one multilingual model covers market languages; no code corpus, so no code model. If formulation or chemistry text underperforms, test a domain tuned model on that document class only.

- **Multi vector:** one vector per chunk plus a title and summary vector per document for document level search; added only where the gold set shows a gain.

- **Versioning and migration:** embedding_model_id on every vector. A model change builds a shadow index, backfills offline, compares recall at 10 on the gold set, then switches the application's index pointer; the old index is kept for a two week rollback window. At about 6 billion tokens for the full corpus, re embedding costs on the order of a thousand dollars at current list prices; the time to backfill, not the money, is the planning constraint.

## 8. Indexing, ANN and sizing

- **Index type:** HNSW in Azure AI Search with int8 scalar quantization and rescoring on full precision originals. At 10 million vectors HNSW gives the best recall and latency; IVF with product quantization only becomes attractive at hundreds of millions of vectors, which this corpus will not reach.

- **Parameters:** start m 4 to 8, efConstruction 400, efSearch 100 to 500; tune efSearch until recall at 10 versus exhaustive search on a sample reaches 0.95 within the latency budget. Oversampling of 4 for rescoring.

- **Sparse index:** BM25 on the same index with language specific analyzers; document numbers, material codes and part numbers indexed as keyword fields for exact match.

### Sizing math

| **Calculation**                                           | **Result**                                      |
|-----------------------------------------------------------|-------------------------------------------------|
| 10M vectors x 1,024 dimensions x 4 bytes (float32)        | about 41 GB raw vectors                         |
| int8 scalar quantization (4x smaller)                     | about 10 GB in the vector index                 |
| HNSW graph overhead (roughly m x 2 links x 4 bytes, m 8)  | about 0.6 GB                                    |
| Full precision originals kept for rescoring               | about 41 GB on storage, not in the vector quota |
| Text, metadata and BM25 structures (about 4 KB per chunk) | about 40 GB storage                             |

- **Partitions and replicas:** choose the Azure AI Search tier and partition count from the vector quota per partition of that tier against about 10 GB of vectors plus headroom for growth; 3 replicas for the read and write availability SLA and query throughput, spread across availability zones.

- **Tiering:** effective and recently superseded versions in the primary index; obsolete versions older than the retention window in a cold index searched only on explicit request.

- **Zero downtime reindex:** build the new index alongside, dual write during the switch, validate on the gold set, flip the pointer, retire the old index after the rollback window.

## 9. Retrieval and reranking

- **Query understanding:** language detection; multi turn conversation condensed to a standalone query by a small model; intent: find a document, ask a question, compare, or out of scope; extraction of document numbers, material codes and sites into filters.

- **Mandatory filters:** acl_groups intersect the user's Entra ID groups; status eq effective unless the user asks for history; site and type filters from the query.

- **Hybrid candidates:** BM25 top 50 and vector top 50 in parallel; exact keyword hit on a document number short circuits to that document.

- **Fusion and dedup:** reciprocal rank fusion (k 60), then collapse near duplicate chunks and multiple hits from one section.

- **Rerank:** semantic ranker or a cross encoder on the top 50, keep the top 5 to 8.

- **Freshness weighting:** no generic time decay; recency is expressed through lifecycle status and effective date, which is what Quality means by current.

- **Answerability threshold:** if the top rerank score is below a calibrated threshold, or fewer than two supporting chunks agree, do not generate; clarify or abstain.

## 10. Comparison engine

| **Mode**                                | **How it works**                                                                                                                                                                                                                        | **Main risk and control**                                                  |
|-----------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------|
| Version against version                 | Align sections by number and heading; deterministic text and table diff finds every change; LLM classifies each change (editorial, procedural, specification or limit, safety relevant) and explains it with citations to both versions | Missed change: deterministic diff guarantees coverage, LLM only classifies |
| Supplier document against specification | Extract values to a schema; compare against specification limits by rules; LLM explains mismatches and missing items                                                                                                                    | Wrong extraction: page and region shown, OCR confidence floor              |
| Document against standard               | Retrieve relevant requirement clauses; LLM checks coverage clause by clause with citations to both sides                                                                                                                                | False assurance: uncertain clauses flagged, never passed                   |

Findings carry type, severity, confidence and citations; the reviewer accepts, edits or rejects each one. Recall on findings is the primary metric: a false alarm costs a minute, a missed change is a quality event.

## 11. Prompt orchestration

- **Context budget:** instructions and schema about 600 tokens, retrieved context up to 4,000 tokens (5 to 8 chunks), conversation summary up to 500, answer up to 600.

- **Packing:** order chunks by rerank score, merge adjacent chunks from one section, drop near duplicates, label each with chunk ID, document number, revision and section.

- **Grounding contract:** answer only from supplied context; every sentence ends with chunk citations; structured output (answer, citations, abstained, reason).

- **Tools:** allowlisted and user scoped: document lookup by number, SAP material and supplier lookup through Databricks, open comparison job. Retrieved text can never trigger a tool.

- **Model routing:** small model for query rewrite, classification and short lookups; larger model for comparison explanations and multi document synthesis; routing by intent and context length, measured on the gold set.

- **Versioning:** prompts and schemas versioned in source control and registered in MLflow with their evaluation results.

## 12. Hallucination mitigation and safety

- **Attribution check:** after generation, a verifier checks each sentence is supported by its cited chunk; unsupported sentences are removed; if coverage of the question drops below threshold the response becomes an abstention.

- **Abstention and refusal policy:** abstain when evidence is weak or sources conflict, with the conflicting documents shown and a route to the document owner. Refuse requests outside scope (for example regulatory advice on a product decision).

- **Guardrails:** Azure AI Content Safety on inputs and outputs; Prompt Shields on user input and on retrieved document text.

- **Prompt injection in documents:** retrieved text delimited and treated as data; instruction like text flagged at ingest; supplier documents scanned before indexing; no tool execution from retrieved content.

- **Runtime PII and compliance:** output scanned for personal data; post retrieval recheck that the user may open every cited document; responses logged with redaction.

## 13. Human in the loop and audit trail

- **Routing:** by confidence and document risk class (for example specifications and batch records always reviewed; meeting minutes auto approved above threshold).

- **Workbench:** side by side source and AI output, values highlighted in place; accept, edit or reject with reason codes.

- **Keeping reviewers independent:** a sample of high confidence items goes to blind review; override rates tracked per type and per reviewer to spot rubber stamping.

- **Audit record per AI output:** output ID, document and revision, model and prompt version, inputs (chunk IDs), output, confidence, reviewer, decision, edits, timestamps. Immutable storage; lineage in Unity Catalog.

- **Learning loop:** reviewer edits become labeled examples; confirmed cases join the gold set; thresholds revisited monthly with Quality.

## 14. Caching and freshness

| **Cache**           | **Key**                                                 | **Invalidation**                                         |
|---------------------|---------------------------------------------------------|----------------------------------------------------------|
| Answer              | normalized query + user ACL fingerprint + index version | Purged when any cited document changes version or status |
| Retrieval results   | query embedding hash + filters + ACL fingerprint        | Short time to live (15 minutes) and on index update      |
| Query embeddings    | text hash + embedding model ID                          | On embedding model change                                |
| Rerank scores       | query hash + chunk ID + chunk hash                      | Chunk hash changes when content changes                  |
| Document processing | content hash + pipeline version                         | Pipeline version change                                  |

- Freshness is driven by change events: a new revision is processed and upserted; its status change to effective flips the previous revision to superseded in the same transaction; caches referencing either are purged. Freshness is measured per source as arrival to searchable at P95.

## 15. Multilingual handling

- Language detected per document and per section at intake; language specific analyzers for BM25 (stemming, stop words).

- Multilingual embeddings allow an English question to find a French or Japanese document; the answer is generated in the user's language with citations to the original language text.

- Machine translation is shown as an aid, never as the controlled text; the original language document remains the record.

- A gold set per major language; release gates apply per language.

## 16. Scalability, capacity and resilience

### Latency budget (Q&A, P95)

| **Stage**                                     | **Budget** | **Note**                        |
|-----------------------------------------------|------------|---------------------------------|
| Gateway, authentication, ACL group resolution | 50 ms      | In parallel with rewrite        |
| Query rewrite and intent (small model)        | 300 ms     | Skipped for single turn lookups |
| Query embedding                               | 60 ms      | Cached                          |
| Hybrid retrieval with filters                 | 150 ms     | BM25 and vector in parallel     |
| Rerank top 50                                 | 250 ms     |                                 |
| Generation to first token                     | 1,000 ms   | Then streamed                   |
| Live ACL recheck of cited documents           | 100 ms     | Parallel with streaming         |

- **Autoscaling:** processing workers on queue depth, separate pools for OCR heavy and text lanes; query APIs on requests per second and P95; LLM capacity as provisioned throughput for the baseline with pay as you go overflow; per user and per app rate limits.

- **Capacity:** run rate is about 75,000 pages a week, so processing is small; the backfill of 9 million pages dominates. Backfill runs as Databricks Workflows in priority order (effective documents of high value types first) within Document Intelligence and Azure OpenAI quotas.

- **Disaster recovery:** search replicas across availability zones; a secondary region with a standby index rebuilt from landing storage and the event log. The source DMS is the system of record, so the index is rebuildable.

| **Component**            | **RPO**    | **RTO** | **Method**                             |
|--------------------------|------------|---------|----------------------------------------|
| Search index             | 15 minutes | 4 hours | Replay events into standby index       |
| Audit and review records | near zero  | 1 hour  | Geo redundant storage, write ahead     |
| Processing pipeline      | replayable | 4 hours | Event Hubs retention, idempotent steps |

## 17. Observability and evaluation

- **Tracing:** OpenTelemetry spans per document (each pipeline step, model, tokens, cost) and per query (retrieval, rerank, generation, verification).

- **Retrieval metrics:** recall at 10, MRR, nDCG at 5, effective version precision (share of results that are effective).

- **Answer and automation metrics:** faithfulness, citation precision, abstention precision, classification accuracy per type, extraction field precision and recall, comparison finding recall, reviewer override rate.

- **Gold sets:** a few hundred real documents and questions per type and language, labeled from Quality's past decisions; synthetic questions generated from sections and spot checked by people.

- **Release gates:** any change to taxonomy, prompts, models, chunking or index reruns the gold set; regression beyond agreed tolerance blocks release.

- **Online:** A/B by site or user group; thumbs and reason codes; citation click through; query reformulation; drift alarms on query mix, rerank score distribution, abstention rate and OCR confidence.

## 18. Cost model and trade offs

| **Item**                                                                                      | **Estimate at list prices (confirm current pricing)**                |
|-----------------------------------------------------------------------------------------------|----------------------------------------------------------------------|
| Backfill OCR, layout model for all 9M pages at \$10 per 1,000                                 | about \$90,000 one time                                              |
| Backfill OCR, read model (\$1.50 per 1,000) for the 70% text only pages, layout for 30%       | about \$36,000 one time                                              |
| Run rate OCR, 75,000 pages a week, same split                                                 | about \$300 a week                                                   |
| Embeddings, full corpus, about 6B tokens                                                      | on the order of \$1,000 one time                                     |
| Q&A per 1,000 queries: 1,000 x (5,100 input tokens x input price + 400 output x output price) | about \$1 on a small model, roughly \$10 to \$20 on a frontier model |

- **Biggest lever:** routing pages to the cheaper OCR model by document type cuts the backfill cost by more than half. Embeddings are not the cost problem.

- **Other levers:** small model for classification and lookups; rerank harder to send fewer chunks; answer cache keyed by ACL; skip reprocessing unchanged sections; provisioned throughput only for the baseline.

- **Trade off stance:** accuracy first on comparison and extraction (quality risk), latency first on search (adoption), cost first on backfill (one time, schedulable).

## 19. Fallback strategies

- **Weak retrieval:** ask one clarifying question (site, document type, product) before searching wider.

- **Scope expansion:** relax site and type filters within the user's permissions; offer superseded versions only with a clear label.

- **Abstain:** show the closest documents with links and route to the document owner; never answer from model memory.

- **Degraded mode:** if the LLM endpoint fails, search still works and returns documents; processing queues and resumes.

## 20. Interfaces

### API

- **POST /v1/search** {query, filters{type, site, status}, top} returns ranked documents with highlights.

- **POST /v1/ask** {question, conversation_id, filters, stream} returns {answer, citations\[{doc_number, revision, section, page, uri}\], abstained, reason, trace_id}.

- **POST /v1/compare** {doc_a{doc_id, revision}, doc_b or spec_id, mode} returns job_id; GET /v1/compare/{job_id} returns findings\[{type, severity, section_a, section_b, explanation, confidence, citations}\].

- **POST /v1/reviews/{output_id}** {decision: accept, edit or reject, edits, reason_code} writes the decision and pushes to the system of record.

- **GET /v1/documents/{doc_id}/status** processing state and arrival to searchable time.

### Data schema

- **document:** doc_id, source_system, source_uri, doc_number, revision, status, effective_date, superseded_by, doc_type, type_confidence, site, owner, language, acl_groups, content_hash, retention_class, pipeline_version, processed_at.

- **chunk:** chunk_id, doc_id, revision, section_path, page_range, text_redacted, token_count, chunk_hash, vector (1,024, int8 with originals), embedding_model_id, status, effective_date, acl_groups, language, ocr_confidence.

- **extracted_field:** doc_id, revision, field_name, value, page, bounding_region, confidence, validation_result.

- **ai_output:** output_id, kind (classification, extraction, summary, comparison, answer), doc_ids, model_version, prompt_version, input_chunk_ids, output, confidence, created_at.

- **review:** review_id, output_id, reviewer, decision, edits, reason_code, decided_at, written_back_at.

## 21. Deployment and rollout

- **Cloud versus on premises:** cloud on the existing Azure and Databricks platform, with private endpoints and customer managed keys; on premises adds GPU capacity and operations burden with no benefit unless a market requires data residency that the region set cannot meet.

1.  **Phase 0, 4 weeks:** discovery, taxonomy, gold sets for two document types, acceptance criteria signed by Quality.

2.  **Phase 1, 8 weeks:** intake for two types at one site in shadow mode; AI output compared to reviewer decisions; search over effective documents.

3.  **Phase 2, 8 weeks:** Q&A and version comparison for the pilot site; A/B against the current process; go live per type when thresholds hold.

4.  **Phase 3:** more types and sites, backfill in priority order, supplier document comparison feeding Swift Release.

- **Rollback:** every model, prompt, taxonomy and index versioned; rollback is a pointer change; feature flags per site and per type.

## 22. Partner delivery governance

| **Gate**       | **What the partner must show**                                                                                                        |
|----------------|---------------------------------------------------------------------------------------------------------------------------------------|
| Before build   | Acceptance criteria and gold sets signed by Quality; architecture decision records for index location, models and comparison approach |
| Design review  | Permission enforcement at query time; effective version filter; data contract per source; audit record design                         |
| Each release   | Gold set results versus thresholds; a trace of one document and one query; cost per document and per 1,000 queries                    |
| Before go live | Shadow mode results by type; leakage canary test passing; runbooks, alerts and on call                                                |

## 23. Open questions for discovery

1.  Which systems are in scope, and which one is the system of record for the effective version?

2.  Compare against what: prior version, specification, or standard? Which first?

3.  Which document types and which review step cost Quality the most time today?

4.  What is today's baseline accuracy and turnaround for manual review?

5.  What validation approach does Quality expect for AI assisted records?

6.  Which markets and languages are in scope for phase one?

7.  Who owns the taxonomy, and who signs off acceptance criteria?

## Appendix: controls, evidence and decisions

The repository's [design template](../templates/design-doc.md) asks for three things the sections above do not state directly. They are given here; build status is in the [module README](../../15_document_control/README.md#build-status).

### Controls

| Standard | How SmartDMS meets it | Proof |
|---|---|---|
| AI-01 · The simplest pattern that reliably works | Rules classify, extract and validate; a model is added only where a held-out measurement shows rules fall short | `15_document_control/results/intake.json`; the model path is plumbing only until then |
| AI-04 · Answers are grounded or withheld | Every sentence cites a chunk it was shown with an exact quote (07's validator), or it is removed; below half supported, or below the answerability threshold, the system abstains | `test_a_sentence_citing_something_it_was_not_shown_is_removed`, `test_without_evidence_the_assistant_abstains_without_calling_the_model` |
| AI-06 · A write waits for a person | Review decisions are approved by a qualified person through 08's approval; write-back to the DMS only after | PR 3 |
| APP-01 · Every call is made as a named identity | Retrieval is filtered by the asker's groups before ranking, and access is rechecked at answer time. Reader groups are replicated into the index, so this meets 07's filter-before-rank rule but not APP-01's delegated permissions; not claimed | `test_no_one_is_shown_a_document_they_may_not_open_and_the_control_sees_them`, `test_access_revoked_after_retrieval_is_applied_before_the_answer` |
| APP-03 · Fail closed | A record outside its contract, a reply outside the schema or a score below threshold is quarantined or sent to review, never defaulted | `test_below_the_threshold_a_document_goes_to_intake_review`, `test_the_model_path_accepts_only_a_reply_that_fits_the_schema` |
| APP-04 · Offline tests, with positive controls | 52 tests offline, no key; the redaction and contract checks each have a control | `test_personal_data_is_redacted_and_approvers_are_kept`, `test_a_record_that_breaks_its_contract_is_quarantined_and_the_rest_flow` |
| INT-01, INT-02, INT-04 · One envelope, exactly once, dead-letter what cannot recover | 12's envelope, processed store and dead-letter queue; work keyed on document number, revision and content | `test_shuffled_duplicated_and_crashed_delivery_reaches_the_same_outcome` and the INT tests named in [standards](../architecture/standards.md) |
| OPS-01, OPS-02, OPS-03 · Traced, priced, budgeted | Through 10 and 11, with OCR and embedding prices added to the sourced price file | PR 4 |

### Evidence that will show it works

| Claim | Measured as | Control | Would prove the design wrong |
|---|---|---|---|
| Effective versions only | Superseded or obsolete content returned to a default query: 0 on development questions | The status filter switched off: 123 returned | Any superseded chunk in a default result |
| At most one effective revision | Effective revisions per document number at every step of a shuffled, duplicated status replay | In-order replay reaches the same final state | A count of 2 at any step |
| Nothing silently corrected | Every extracted value against its source line | A planted bad value stays bad and goes to review | A value that differs from its line |
| Deterministic diff then model classification beats asking the model to compare | Change recall, severity accuracy and false findings on seeded revision pairs with planted, labelled changes | The model comparing directly | Lower recall than the model alone (PR 3, ADR 0019) |
| Rules are good enough for intake | Classification and extraction accuracy on a held-out document set written after the rules are frozen | The development set | Rules below the model challenger on held-out documents |

### Decisions needed

The open questions in section 23 stand. Two more came out of building the first part:
1. **Revision for uncontrolled records.** Certificates, complaints and emails state no revision; the key defaults to `/01`. Recommendation: confirm with Quality whether a reissued certificate is a new revision or a new document.
2. **Free-text personal data.** The rules stand-in misses names written in prose (10 of 10 planted). Recommendation: a PII service is needed before complaints are indexed, and its recall measured on the same planted set.
