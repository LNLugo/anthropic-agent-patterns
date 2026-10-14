# Anthropic Workflow Patterns

Implementations of the workflow and agent patterns described in Anthropic's
**Building effective agents**, followed by the capability layer they run on:
retrieval, tools, memory, guardrails, observability and cost.

This project uses LangGraph to implement the patterns incrementally, starting
with predictable workflows and progressing toward dynamic agent behavior.

> **Effective agents are not about adding more autonomy.**
>
> **They're about choosing the simplest architecture that reliably solves the problem.**

---

## Where to Start

| If you want to see | Go to |
|---|---|
| Contract review RAG measured against lawyer annotations | [07_rag](07_rag/) |
| Proof that retrieval never returns text a user cannot open | [Permission-aware retrieval](07_rag/README.md#permission-aware-retrieval) |
| How the modules compose into a law firm system with ethical walls | [Design: Matter-Aware Legal AI](docs/designs/legal-matter-rag.md) |
| How the modules compose into an enterprise knowledge assistant | [Design: Permission-Aware RAG over Atlassian](docs/designs/atlassian-rag.md) |
| The orchestration patterns themselves | [Architecture Progression](#architecture-progression) |

---

## Architecture Progression

| # | Anthropic Architecture | Status | Core Idea |
|---|---|---|---|
| Foundation | Augmented LLM | 🟡 Supporting capability | LLM + retrieval + tools + memory |
| 01 | Prompt Chaining | ✅ Complete | Fixed sequence of LLM calls |
| 02 | Routing | ✅ Complete | Classify → select specialized path |
| 03 | Parallelization | ✅ Complete | Independent work in parallel; sectioning / voting |
| 04 | Orchestrator-Workers | ✅ Complete | LLM dynamically decomposes → workers → synthesis |
| 05 | Evaluator-Optimizer | ✅ Complete | Generate → evaluate → feedback → refine |
| 06 | Agents | ✅ Complete | LLM dynamically directs its own process and tool use |
| **Phase 2** | **Capability layer** | | |
| 07 | Retrieval (RAG) | ✅ Complete | Retrieval ablation, cited answers, abstention and permission-aware retrieval, measured on CUAD |
| 08 | Tools and MCP | 🟡 In progress | Router plus a bounded, read only query tool, built against a mock Jira |
| 09 | Memory | ⬜ Planned | Retrieve relevant history instead of appending it |
| 10 | Guardrails and observability | ⬜ Planned | Policy checks and traces built on the context ledger |
| 11 | AI FinOps | ⬜ Planned | Cost per answer and cost per correct answer |

---

## Control Questions

Each pattern answers a different architectural control question.

| Pattern | Control Question |
|---|---|
| Prompt Chaining | What step comes next? |
| Routing | Which path should I take? |
| Parallelization | What can I do independently? |
| Orchestrator-Workers | What work needs to be decomposed? |
| Evaluator-Optimizer | Is the result good enough? |
| Agents | What should I do next? |
| Retrieval | What evidence did the model see, and can every claim be traced back to it? |
| Permission-aware retrieval | Can a user ever be shown text they are not allowed to open? |

---

## Conceptual Architecture

```text
                         AUGMENTED LLM
                  Retrieval + Tools + Memory
                              │
                              ▼
                     ┌─────────────────┐
                     │ 01 Prompt       │
                     │    Chaining     │
                     └────────┬────────┘
                              ▼
                     ┌─────────────────┐
                     │ 02 Routing      │
                     └────────┬────────┘
                              ▼
                     ┌─────────────────┐
                     │ 03 Parallel-    │
                     │    ization      │
                     └────────┬────────┘
                              ▼
                     ┌─────────────────┐
                     │ 04 Orchestrator │
                     │    -Workers     │
                     └────────┬────────┘
                              ▼
                     ┌─────────────────┐
                     │ 05 Evaluator-   │
                     │    Optimizer    │
                     └────────┬────────┘
                              ▼
                     ┌─────────────────┐
                     │ 06 Agents       │
                     │                 │
                     │ Dynamic         │
                     │ decision-making │
                     │ + tool use      │
                     └─────────────────┘
```

---

## Model Provider

The architecture follows Anthropic's paper; the model does not have to. The
patterns are model agnostic, and the provider is a run setting, not a code
change: every Phase 1 module gets its model from [`llm_provider.py`](llm_provider.py),
and 07_rag makes the same choice through its `--provider` flag (default `openai`).
The published results in this repo were produced with OpenAI's `gpt-4o-mini`.
OpenAI retires it on January 14, 2027 and names `gpt-6-luna` as the
replacement, so that is now the default; results will be rerun on it.

| Setting | Default | Notes |
|---|---|---|
| `LLM_PROVIDER` | `openai` | `openai` or `anthropic` |
| `OPENAI_MODEL` | `gpt-6-luna` | needs `OPENAI_API_KEY`; any `gpt-4*` model still runs at temperature 0 |
| `OPENAI_REASONING_EFFORT` | `none` | for `gpt-6-luna`; tool calling on Chat Completions needs `none` |
| `CLAUDE_MODEL` | `claude-sonnet-5-5` | needs `ANTHROPIC_API_KEY` |

Install with `pip install -r requirements.txt`, then copy
[`.env.example`](.env.example) to `.env` at the repo root. Modules that ask
the model for JSON parse the reply with `parse_json`, which tolerates markdown
fences and surrounding prose, so switching providers does not break them.

---

## Phase 2: The Capability Layer

Phase 1 covered how control flows. Phase 2 covers what each pattern runs on:
retrieval, tools, memory, guardrails, observability and cost. The rule from
Phase 1 still holds. Each capability is added only where a measured gap
justifies it, and every claim in this repo comes with a number the repo
itself produces.

The first capability is retrieval, built on **CUAD**, a public set of
commercial contracts where lawyers annotated the exact text for 41 clause
types. Those annotations make retrieval quality and citation accuracy
measurable instead of anecdotal.

### [07_rag](07_rag/): contract review RAG

* every retrieval strategy defined as a YAML config (BM25, dense, hybrid with
  Reciprocal Rank Fusion, cross encoder reranking, parent retrieval)
* an ablation runner that scores each strategy against the lawyer annotations
* a LangGraph answer flow that ships an answer only when a citation resolves
  to text the model was shown, and abstains otherwise
* a context ledger recording what entered the model's context, the tokens it
  cost and how long it took
* permission-aware retrieval that filters by access rules before ranking, with
  a leakage test that proves restricted text never comes back

**Retrieval.** All 102 CUAD test contracts, 1,244 answerable clause queries:

| Strategy | k | Hit@k | MRR |
|---|---|---|---|
| BM25 | 5 | 0.667 | 0.503 |
| Dense embeddings | 5 | 0.612 | 0.438 |
| Hybrid (BM25 + dense, RRF) | 5 | 0.652 | 0.511 |
| BM25 + cross encoder rerank | 5 | 0.721 | 0.567 |
| Hybrid + rerank | 5 | 0.719 | 0.564 |
| Hybrid + rerank + parent windows | 3 | 0.774 | 0.638 |

The measured answer is not the fashionable one. On contract language, BM25
beat a small dense embedder, hybrid fusion added nothing measurable, and the
cross encoder reranker did the real work. Once reranking was in place, the
dense model could be removed with no loss. That is the Phase 1 thesis applied
to retrieval: the simplest pipeline that holds up under measurement wins.

**Answers.** On 300 queries, half with the clause present and half without,
the system abstained correctly on 83% of contracts that lacked the clause.
When the clause reached the model, the model found it almost every time.
Nearly every failure traced to evidence handling: retrieval that missed the
clause, or quotes that paraphrased the source. The citation rule caught every
one of those quotes before it shipped as a supported answer. A larger model
would fix little of this; better retrieval and stricter citation would fix
most of it.

**Permissions.** Against a synthetic wiki with inherited page restrictions,
8 personas each ran 8 probe queries aimed at restricted pages:

| Filter | Chunks returned | Restricted chunks returned |
|---|---|---|
| Off (positive control) | 320 | 224 |
| On | 144 | **0** |

The positive control is what makes the zero meaningful: it shows the test can
see a leak when one exists.

Details, costs and limits are in [07_rag/README.md](07_rag/README.md).

### [08_tools](08_tools/): tools and MCP

Not every question is a retrieval question. "How many P1 incidents did we have
last quarter?" needs a query, not a search. This module adds a router that
sends aggregation questions to a bounded, read only query tool, built against
a mock Jira. The tool has a fixed surface: allowed projects, read only, a
result limit. The router is measured twice: as rules, and as a model choosing
tools. On 20 held-out questions the rules routed 9 correctly and gpt-6-luna 18
to 19 over three runs, at $0.09 per thousand questions. The model's one
repeated miss, answering half of a two project question, is inside policy, so
the bound cannot stop it; that is the next control to build.

---

## Designs

Design documents show how the modules compose into a real system before every
piece is built. Each one marks which parts are built and which are planned,
and names the number that will prove each part.

| Design | What it covers | Builds on |
|---|---|---|
| [Matter-Aware Legal AI](docs/designs/legal-matter-rag.md) | Research, drafting and playbook review for a law firm: matter scoping, ethical walls as deny rules, firm know-how kept apart from client files, fail-closed wall enforcement | 07, 08, Phase 1 routing, parallelization and evaluator-optimizer |
| [Permission-Aware RAG over Atlassian](docs/designs/atlassian-rag.md) | Knowledge and aggregation questions over Confluence and Jira Data Center, with replicated ACLs for retrieval and delegated permissions for queries | 07, 08, 10, 11 |

---

## Roadmap

* **Exclusion gates for ethical walls** in 07_rag, with a wall leakage test and
  positive control, so deny rules can be proven the same way allow rules are
* **Cross-matter contamination test**: a user on one matter must never retrieve
  another matter's documents
* **09 Memory**: retrieve relevant history instead of appending it to the prompt
* **10 Guardrails and observability** on top of the context ledger
* **11 AI FinOps**: cost per answer and cost per correct answer
