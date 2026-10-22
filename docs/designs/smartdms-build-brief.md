Build module 15_document_control by following docs/designs/smartdms.md (attached; commit it first). The design is the specification. Work through it section by section and keep a status table in the module README, one row per design section: Built, Built as in-process stand-in (per ADR 0015), or Documented only, with the file or test that proves it. Follow this repo's conventions from 07 to 14: offline pytest with no API key, seeded synthetic fixtures reproduced by a test, every claim backed by a number the repo produces, results/ committed, ADRs, root README row, CI suite, app tab. The design's volumes and prices are planning assumptions; keep them labeled as assumptions, never as results.

SECTION BY SECTION
§1 Planning assumptions: assumptions.yaml plus sizing.py that derives every sizing and cost figure in the design; a test asserts the design's numbers reproduce (about 41 GB raw vectors, about 10 GB int8, about $90K vs about $36K OCR backfill, about $300/week run rate).
§2 Requirements and SLOs: slo.yaml; tests for what can be proven offline (zero cross-group leakage, revocation applied before answer, latency budget accounted per stage from traces).
§3 Architecture: package layout mirrors the layers. Stand-ins: Document Intelligence -> local parsers; Azure AI Search -> 07's retrieval in process; Azure OpenAI -> llm_provider; Event Grid/Event Hubs -> 12's in-process broker. Map each stand-in to its Azure service in the README.
§4 Lifecycle and system of record: lifecycle state machine (draft, in review, approved, effective, superseded, obsolete); effective->superseded flip in one update; business key = doc_number + revision; content hash finds the same document in two sources; decisions written back to a mock DMS API.
§5 Ingestion and chunking: change events and data contracts through 12; parsers for PDF text, HTML, email, spreadsheet fixtures; structure-aware chunking on headings with tables kept whole; content-hash dedup so unchanged sections are not re-embedded; PII redaction at intake.
§6 Classification, extraction, validation: taxonomy YAML owned by "Quality"; constrained-schema classification with confidence; per-type field extraction with page references; validation rules against synthetic SAP master data; nothing silently corrected.
§7 Embeddings: embedding_model_id on every vector; shadow-index migration with gold-set comparison and pointer flip, tested.
§8 Indexing and sizing: pointer-flip zero-downtime reindex tested; HNSW/int8 parameters documented; sizing from §1.
§9 Retrieval and reranking: reuse 07; add lifecycle filter (effective by default), doc-number exact-match short circuit, RRF, answerability threshold. Stale-version leakage test in the style of 07's permission leakage test.
§10 Comparison engine: all three modes (version vs version; CoA vs specification by rules; document vs standard). Deterministic diff finds changes; model only classifies and explains, citing both sides.
§11 Prompt orchestration: context budget and packing, citation contract, allowlisted user-scoped tools, routing via 08's router.
§12 Hallucination and safety: attribution check (07's citation rule), abstention policy, a planted prompt injection inside a supplier document fixture that must never trigger a tool, content-safety stand-in.
§13 Human in the loop and audit: approval as LangGraph interrupt (08, ADR 0005); routing by confidence and risk class; blind-review sample; audit record per AI output; reviewer edits feed the gold set.
§14 Caching and freshness: cache keys include ACL fingerprint and document versions; test that a status change purges cached answers citing the old revision.
§15 Multilingual: language detection and analyzer choice on a small non-English fixture set; rest documented.
§16 Scale and resilience: latency budget from traces; rebuild the index by replaying the event log (tested); DR tiers documented.
§17 Observability and evaluation: traces through 10 (content off); metrics from §17; gold sets and merge-blocking gate through 13.
§18 Cost: cost per document and per 1,000 queries through 11 with sourced prices; backfill calculator.
§19 Fallback: tests for clarify, scope expansion within permissions, abstain and route, and degraded mode (model down, search still works).
§20 Interfaces: the design's endpoints behind 12's gateway, pydantic schemas from the design's data schema, contract tests in CI.
§21 Rollout: shadow-mode runner comparing AI output to reviewer decisions; feature flags per site and type.
§22 Partner governance: add the design's gates to docs/architecture standards, enforced by conformance.py where checkable.
§23 Open questions: documented only.

HEADLINE RESULT
On seeded SOP revision pairs with planted, labeled changes: change recall, severity accuracy and false findings for deterministic diff + model classification versus asking the model to compare directly. Offline tests replay recorded live runs (ADR 0013). Report what the runs show. ADR 0019 records the decision the result supports.

ORDER (one PR each, all tests green before the next)
1. Design doc, corpus and fixtures, lifecycle, intake (§1 inputs, §4, §5, §6)
2. Retrieval, safety, caching (§7, §8, §9, §11, §12, §14)
3. Comparison, CoA check, review and audit, headline result (§10, §13)
4. API, cost, observability, gate, sizing, fallback, rollout, governance, multilingual, app tab, README status table (§1 to §3, §15 to §23)