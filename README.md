# Anthropic Workflow Patterns

[![tests](https://github.com/LNLugo/anthropic-agent-patterns/actions/workflows/tests.yml/badge.svg)](https://github.com/LNLugo/anthropic-agent-patterns/actions/workflows/tests.yml)

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
| A model router measured against rules on questions nobody tuned for | [08_tools held-out results](08_tools/README.md#held-out-set) |
| Tools any agent can call, answering only as the signed-in user | [08_tools over MCP](08_tools/README.md#tools-over-mcp) |
| Guardrails and human approval around a tool, with every claim counted | [Guardrails](08_tools/README.md#tool-guardrails), [approval](08_tools/README.md#human-approval) |
| Traces an auditor can use that hold no prompt, document or tool result | [10_observability](10_observability/) |
| What a correct answer costs, and what each extra one costs over free rules | [11_finops](11_finops/) |
| The orchestration patterns themselves | [Architecture Progression](#architecture-progression) |
| Everything above, interactively | `streamlit run app/Agent_Patterns.py` (Phase 2 results run offline, no API key) |

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
| 06 | Agents | ✅ Complete | LLM directs its own process and tool use; every turn ends in a typed outcome |
| **Phase 2** | **Capability layer** | | |
| 07 | Retrieval (RAG) | ✅ Complete | Retrieval ablation, cited answers, abstention and permission-aware retrieval, measured on CUAD |
| 08 | Tools and MCP | ✅ Complete | Bounded, read only Jira tool; model router measured against rules on held-out questions; coverage check, tool guardrails and human approval in the main path; the tools over MCP with identity from the token |
| 09 | Memory | ⬜ Planned | Retrieve relevant history instead of appending it |
| 10 | Observability | ✅ Complete | One trace per run across 06, 07 and 08, with agent, generation, tool, guardrail and handoff spans; local by default, sensitive content off; 0 of 16 planted content markers in the trace file, an audit report from the file alone. (Tool guardrails are built in 08) |
| 11 | AI FinOps | ✅ Complete | Prices with sources and unpriced models refused; every run costed from its trace; cost per correct answer from recorded runs ($96 per million questions for +50 points of routing accuracy); per-identity budgets checked before every model call |

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
| `LLM_PROVIDER` | `openai` | `openai`, `anthropic`, or `scripted` for offline tests |
| `OPENAI_MODEL` | `gpt-6-luna` | needs `OPENAI_API_KEY`; any `gpt-4*` model still runs at temperature 0 |
| `OPENAI_REASONING_EFFORT` | `none` | for `gpt-6-luna`; tool calling on Chat Completions needs `none` |
| `CLAUDE_MODEL` | `claude-sonnet-5-5` | needs `ANTHROPIC_API_KEY` |

Install with `pip install -r requirements.txt`, then copy
[`.env.example`](.env.example) to `.env` at the repo root. Modules that ask
the model for JSON parse the reply with `parse_json`, which tolerates markdown
fences and surrounding prose, so switching providers does not break them.

---

## Tests

Every suite runs offline, with no API key, and CI runs all five on Python
3.12 and 3.13 for every push and pull request.

| Suite | Run from | Tests | What stands in for the model |
|---|---|---|---|
| Phase 1 patterns and the explorer app | repo root: `pytest` | 90 | `ScriptedChatModel` in [`llm_provider.py`](llm_provider.py) |
| 07 Retrieval | `07_rag`: `pytest` | 63 | fixtures; no model call in the suite |
| 08 Tools | `08_tools`: `pytest` | 148 | `ScriptedChatModel` and scripted tool calls |
| 10 Observability | `10_observability`: `pytest` | 14 | runs 06, 07 and 08 on the scripted model and fixtures |
| 11 AI FinOps | `11_finops`: `pytest` | 28 | prices recorded runs and trace files; budgets through 08 on `ScriptedChatModel` |

Phase 1 tests assert on the **path the graph took**, not on model prose: which
nodes ran, in what order, and what each node was given. `LLM_PROVIDER=scripted`
replaces the model with a stand-in that replays scripted replies and fails
loudly on drift. A call with no reply left raises `UnexpectedModelCall`; a test
that ends with replies unused raises `UnconsumedModelSteps`. The contract
follows `ScriptedModel` in OpenAI's Agents SDK; `Step(match=...)` is added so
parallel branches can be scripted without depending on which thread calls
first. The suite was checked against deliberate breakage: moving the agent
loop cap, the evaluator's pass mark, a routing branch, or a chain edge each
fails it.

A few tests pin down behaviour worth knowing, not just behaviour that works:
the free text router in 02 sends "not news, it is research" to the news
branch, and the loop cap in 05 ends a run without marking that the cap, not
the evaluator, ended it. The explorer app is tested too: selecting a pattern
must make no model call, and Run must execute it. Its Agents page has one
scenario per way a run ends; the two where the agent abstains on a bad
decision are replays of scripted model replies, so they need no API key.

### Turn outcomes in 06

The current agent ([`agents_06_v3_1.py`](06_agents/agents_06_v3_1.py)) ends
every turn in exactly one typed outcome, and every run in an explicit status:

| Outcome | Next | Run status |
|---|---|---|
| `RunAgain(tool)` | run the tool, give the model another turn | |
| `FinalOutput()` | answer | `completed` |
| `Handoff(to)` | stop, recording the target | `handed_off` |
| `Interruption("max_turns")` | answer from what was gathered, saying what is missing | `max_turns` |
| `Interruption("invalid_decision")` | stop without an answer | `invalid_decision` |

The model's reply is parsed into one outcome; an unknown tool, an unknown
handoff target or unparseable output is an `Interruption`, never a guess. The
turn limit is checked before the model is asked, so no call is spent on a
decision that would be overruled; the earlier version asked a fourth time and
silently ignored the answer. The outcome names follow the turn results in
OpenAI's Agents SDK; only the contract is borrowed.

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
to 19 over three runs, at $0.09 per thousand questions. Its one repeated
miss, answering half of a two project question, was inside policy, so the
bound could not stop it. A coverage check in code now does: on 20 new
questions, no query ran that should not have in any of three runs, against
two such queries without it. Tool guardrails add deployment policy on top:
`allow`, `reject_content` (blocked, and the model is told why) and
`raise_exception` (the run halts), shown on the JQL tool with an
out-of-scope project and a restricted one. The one write, a comment, posts
only after a different person approves it, and the pending approval survives
a restart on a saved checkpoint. Over MCP the caller's identity comes from the
bearer token, never from tool arguments: the same call returns each token
holder's own view, and a bad token gets a 401 before any tool runs.

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
* **Handoff leakage test**: restricted context must not reach a worker that is
  not cleared for it, proven the same way 07 proves retrieval permissions
* **12 Integration**: agents triggered by events, with idempotency, a
  dead-letter queue and replay; an outbox so an approved write posts exactly
  once; a gateway with per-identity quotas fed by 11's budgets; API contracts
  with breaking-change tests; legacy, SaaS and cloud-native adapters behind one
  interface
* **Architecture standards and COE**: one reference architecture tying the
  modules together; application, API, integration and AI standards, each
  enforced by a named test; ADRs; a design review checklist and an exception
  register
* **Conformance check**: standards adoption computed from the repo, not claimed
* **Housekeeping**: one current file per pattern, tagged releases with a
  changelog, a single `pyproject.toml`
