# 08 · Tools and MCP

**Control question:** *What is the model allowed to do, and who decides what it can see?*

Some questions are not retrieval questions. "How many P1 incidents did the
billing platform have last quarter?" has an exact answer, and it lives in a
query, not in a passage. This module adds the second path of the
[Atlassian design](../docs/designs/atlassian-rag.md): a router that separates
the two question shapes, and a bounded, read only Jira tool for the counting
kind. Knowledge questions still go to [07_rag](../07_rag)'s
permission-filtered retrieval.

It is built in layers, each one added because a measurement showed the gap:

| Layer | What it answers | Measured |
|---|---|---|
| [Bounded tool](#design-decisions) | What can a query touch? | Typed arguments, allowlist, delegated permissions; counts differ per user |
| [Model router](#model-router-through-tool-calling) | Count or wiki? | 18 to 19 of 20 held-out questions, against 9 for the rules |
| [Coverage check](#coverage-check) | Does an allowed query answer what was asked? | Nothing that should not run ran, in three runs of 20 new questions |
| [Tool guardrails](#tool-guardrails) | Should this deployment run it? | allow, reject_content, raise_exception; Jira calls counted, with a control |
| [Human approval](#human-approval) | Who decides that a write happens? | Pauses, survives a restart, posts once, no self-approval |
| [Tools over MCP](#tools-over-mcp) | Whose data does any agent see? | Identity from the bearer token only; same call, per-token answers; 401 for a bad token |

## Architecture

```text
 question ──► router ──knowledge──► 07_rag permission-filtered retrieval ──► pages or "not in the wiki"
                │
                ├──clarify───────► ask, run nothing   (no project, or the coverage check: two projects)
                │
                └──aggregation──► input guardrails ──► bound ──► Jira, called AS the user ──► output guardrails
                                        │                │                                       │
                                  reject: say why    outside bound:                       count + keys + JQL
                                  halt: tripwire     refuse, run nothing                  "only issues you can see"
```

The answer flow is a LangGraph `StateGraph` (`agent_tools/assistant.py`),
consistent with patterns 01 to 07. The router is chosen when the graph is
built: `ModelRouter` (a model choosing a tool, with the coverage check) or
`RulesRouter` (the offline baseline). Both answer `decide(question, today)`.
Guardrails see every count the model attempts, including ones the bound will
refuse, so an attempt on a restricted project halts instead of being politely
refused.

## Design decisions

| Decision | Choice | Reason |
|---|---|---|
| What the model produces | A typed `QuerySpec`, never JQL | Every field is an enum or a date, so there is no free text to inject "OR project = X" into. The tool compiles the JQL itself. |
| Scope | Fixed project allowlist, enum values, date range capped at 366 days, at most 25 keys returned | The bound is a policy on the tool, separate from the user's permissions. A security engineer can browse SECOPS in Jira, and the tool still refuses it. |
| Permissions | Delegated to Jira: every call names the user, no service account | Jira applies project permissions and issue security levels itself. Nothing is replicated, so nothing can drift. |
| Counts | "Issues visible to you", stated in every answer | The true total would reveal that restricted issues exist. Jira behaves the same way. |
| Write access | None for counts; one write, a comment, only after a person approves it | The count tool exposes one operation, and a test asserts it has no other public method. The comment tool is separate, and every comment waits for an approver who is not the requester. |
| Missing project | Ask a question, run nothing | Guessing a project is how a wrong number reaches a VP. |
| Missing period | Default to the last 90 days and say so | A bounded default beats an unbounded query, and the stated assumption lets the user correct it. |
| Date bounds | `created < next day`, not `created <= last day` | In JQL, `<=` a bare date means midnight at the start of that day and silently drops it. A test covers the boundary. |
| Router | A model choosing one of three tools, with the coverage check; the rules as an offline baseline | Measured on held-out questions: the model routed 18 to 19 of 20, the rules 9. The rules stay for offline runs and as the baseline, behind the same `decide()` interface. |

## Results

### Counts depend on who asks

Billing P1 incidents created in Q3 2026. The fixture holds four, and one
(BILL-107) carries a security level visible only to the billing on-call group.

| Caller | Groups | Count | Missing |
|---|---|---|---|
| billing_oncall | eng-all, billing-oncall | 4 | none |
| new_hire | eng-all | 3 | BILL-107 |
| sec_engineer | eng-all, security-team | 3 | BILL-107 |
| contractor | none | 0 | all |

### Router and extraction (the rules baseline)

25 labeled questions (`tests/fixtures/router_questions.json`), relative dates
fixed at 2026-10-12. Generated by `run_router_eval.py`. These are the rules
the model router is measured against; on the held-out questions further down
they route 9 of 20, because they were written against this set's vocabulary.

| Measure | Result |
|---|---|
| Routed to the right path | 22 of 25 (88%) |
| Query extracted exactly, on 12 counting questions | 11 of 12 (92%) |

The four misses are the cases labeled hard before the rules were written, and
the rules were not tuned to pass them:

* "Did billing have a lot of outages recently?" has no counting cue, so it goes
  to retrieval.
* "Which page explains how we triage P1 incidents?" and "Show me how to file a
  bug" have both cues but ask for a page or a procedure.
* "How many tickets did the payments team close last month?" gives the status
  as a verb, so the status filter is missed.

These are where a model based router earns its cost. The bound does not
depend on the router: whatever fills the `QuerySpec`, the same validator sits
between it and Jira.

### Model router through tool calling

`agent_tools/model_router.py` makes the same two decisions with a model. The
model is bound to three tools and must call exactly one:

| Tool | Route |
|---|---|
| `count_jira_issues` (the schema above) | aggregation, with the `QuerySpec` as the arguments |
| `search_wiki` | knowledge |
| `ask_clarification` | neither: the project cannot be identified |

The arguments go through `spec_from_arguments` and `validate`, the code the
rules path uses, so an invented project, an out of range value or a raw `jql`
argument is refused before anything runs. The conventions the rules encode
(calendar quarters, exclusive end date, 90 day default, P1 means a P1
incident) are stated in the system prompt as the tool's contract.
`run_model_router_eval.py` scores the model with the same field comparison as
the rules, on the same 25 questions, and adds what the rules do not cost:
tokens, latency and, with prices supplied, cost per question.

Development set results, the same 25 questions, 2026-10-14:

| | Rules | gpt-4o-mini | gpt-6-luna |
|---|---|---|---|
| Routed correctly | 22 of 25 | 25 of 25 | 25 of 25 |
| Query extracted exactly | 11 of 12 | 10 of 12 | 12 of 12 |
| Arguments refused by the bound | n/a | 1 | 0 |
| Tokens per question (in / out) | 0 | 643 / 32 | 727 / 37 |
| Latency p50 | ~0 | 1.02 s | 1.33 s |
| Cost per question | 0 | not measured | $0.000091 |

Both models fix every routing miss the rules make. gpt-4o-mini extracts worse
than the rules, and both of its misses are date arithmetic: "last 60 days"
came out one day long, and "open P2s", which gives no period, came back as a
range over a year. The validator refused that one, so no query ran. That is
the bound doing its job against a real model.

Two caveats keep these from being the headline. The system prompt was written
after the development questions were seen, so 25 of 25 partly measures a
prompt fitted to them. And each model was run once, without a temperature
setting to pin its output. The numbers that count come from the held-out set
below, run three times.

### Held-out set

`tests/fixtures/router_questions_heldout.json` holds 20 questions written
without reading the prompt or the development set: unusual time phrases,
informal names, typos, and questions where the right answer is to run
nothing. Those carry the label `no_query`, scored correct when the system
asks (no project, two projects) or the bound refuses (a project off the
allowlist), and wrong if any query runs. Rules and prompt are frozen while it
is scored.

Held-out results, 2026-10-14. gpt-6-luna was run three times, since it takes
no temperature setting and its output can vary between runs.

| | Rules | gpt-4o-mini, 1 run | gpt-6-luna, 3 runs |
|---|---|---|---|
| Routed correctly | 9 of 20 | 18 of 20 | 19, 19, 18 of 20 |
| Query extracted exactly | 3 of 7 | 5 of 7 | 7, 7, 6 of 7 |
| Ran a query when it should have asked | not measured | 2 | 1 in every run |
| Latency p50 | ~0 | 0.78 s | 1.04 to 1.23 s |
| Cost per 1,000 questions | 0 | not measured | $0.09 |

**The rules were what overfit.** They scored 88% on the development set and
45% here. They were written against the development vocabulary and break on
"fortnight", "tix", "invoice stack" and terse requests with no counting words.
That is the case for a model in one line: rules look fine until people phrase
things their own way.

**gpt-6-luna holds up on questions nobody tuned for.** No date errors in three
runs. Its run to run variation fails safe: in one run it asked which project
"the invoice stack" meant, where the other two runs answered correctly.

**The miss that matters is one the bound cannot catch.** Asked "How many bugs
did BILL and PAY open in September?", gpt-6-luna counted BILL bugs for
September, in every run, and dropped PAY without saying so. The user gets a
correct looking number that answers half the question. gpt-4o-mini did the
same, and also ran a query for "How many incidents were created last week?",
which names no project. The bound stops queries outside policy; these were
inside policy and simply wrong. Catching them takes a different control, one
that checks the query covers what the question asked for. That control is the
[coverage check](#coverage-check) below, and because the failure was found on
this set, it is measured on new questions, not on these.

**gpt-4o-mini still gets dates wrong.** "The past fortnight" came out one day
long, the same off by one as on the development set, and it read "opened" as
the status Open. Moving date arithmetic out of the model and into
`time_window` would remove that class of error for cheaper models.

### Coverage check

**Control question:** *does the query cover what the question asked for?*

The bound answers "is this query allowed". The held-out miss needed a second
question, because "count BILL bugs in September" is allowed; it just is not
what was asked. `agent_tools/coverage.py` asks it in code, after the model has
chosen its arguments and before anything reaches Jira.

The count tool, as offered to the model, gains one required argument,
`project_evidence`: the exact words in the question that name the project.
Nothing else in the prompt or the tool descriptions changes, so a difference
in results can be credited to the check. It is the same move as 07_rag's
citation check, where an answer ships only when its citation resolves to text
the model was shown; here a query runs only when its project resolves to
words the user typed. The check asks instead of running when:

| Condition | Example |
|---|---|
| The question names more than one known project | "BILL and PAY" |
| The quoted evidence is not in the question | the model quotes "payments" for a question that never says it |
| The evidence is only generic words | "incidents", for a question that names no project |
| The evidence names a different project than the one chosen | evidence "payments", project BILL |

It fires on positive evidence only. An informal name the code does not know,
such as "the invoice stack", passes, because it is real text from the
question; whether it really means BILL stays the model's call, with the quoted
words logged for review. For the same reason, an unknown project name ("OPS")
mapped onto an allowed project is not caught. Two known projects in one
question are stopped even when the second is only mentioned in passing, which
may over-ask; the coverage set measures how often.

`--no-coverage` reruns the router exactly as measured before the check: same
schema, no check. The check is measured on
`tests/fixtures/router_questions_coverage.json`, 20 questions written after
it was designed, half that should be stopped and half that must still run.
The development and held-out sets are rerun with it on as a regression check:
it should not make the router ask where it used to answer correctly.

Coverage set results, gpt-6-luna, 2026-10-14:

| | Rules | Model, check off (1 run) | Model, check on (3 runs) |
|---|---|---|---|
| Routed correctly | 14 of 20 | 13 of 20 | 15, 15, 15 of 20 |
| Should-stop questions handled | 5 of 10 | 8 of 10 | 10 of 10 every run |
| Should-run questions answered | 9 of 10 | 5 of 10 | 5 of 10 every run |
| Queries that ran when nothing should have | 1 | 2 | 0 |
| Cost per 1,000 questions | 0 | $0.10 | $0.10 |

**The check does the job it was built for.** With it off, the model ran
queries for "How many bugs did BILL and PAY log?" and for Billing Platform
compared with Infrastructure: two more correct looking, incomplete answers.
With it on, nothing ran that should not have, in any run. Without the check
the model trails the rules on this set; with it, the model leads.

**The same five should-run questions went unanswered in every run, with or
without the check:** the two informal names ("invoice stack",
"money-movement") and the three single project questions that mention a
second project in passing. On the informal names the model itself asked, in
all four runs. "Money-movement" is counted as an ambiguity signal rather than
a plain error, since billing moves money too. Requiring `project_evidence`
did not cause this: without it, the model asked about the same names and
asked about, or sent to the wiki, the same passing-mention questions.

**The check's own cost is real but, so far, redundant.** Its rule "the
question names more than one known project" cannot tell "BILL and PAY" from
"BILL tickets opened after a PAY outage". Across three runs it stopped those
passing-mention questions four times, including "awaiting payment", where
"payment" reads as the project PAY. Each was a question the model also failed
to answer without the check (it asked, or sent it to the wiki), so the check
cost no answers on this set. On a model that answered those questions, it
would.

**Held-out regression check.** With the check on, the held-out set routes 19
of 20, as before, but the miss changed kind: "BILL and PAY in September" is
now stopped by the check instead of answered for BILL alone, and the one miss
is the model asking about "the invoice stack", which it also did once without
the check. The held-out set is not evidence for the fix, since the fix was
built from its miss; the coverage set is.

### Tool guardrails

**Control question:** *when a call is allowed by the tool, should this deployment still run it?*

The bound in `jql_tool.py` is the tool's contract and is the same everywhere.
Guardrails are deployment policy around it. Each returns one of three
behaviours, following the tool guardrail contract in OpenAI's Agents SDK:

| Behaviour | Effect | Demo on the JQL tool (finance assistant, BILL and PAY) |
|---|---|---|
| `allow` | the call runs | BILL: counted, the model answers from the result |
| `reject_content` | blocked; the model is told why, in place of a result, and gets another turn | INFRA: the tool allows it, this deployment does not serve it; the model asks the user |
| `raise_exception` | the run halts with a tripwire exception | SECOPS: an attempt on a restricted project is a security event, not a mistake to coach |

Input guardrails see the model's raw arguments before anything reaches Jira;
output guardrails see the result before the model does. The demo policy
(`finance_assistant_policy`) runs four, in order:

1. `restricted_project` (input): SECOPS halts the run.
2. `deployment_scope` (input): projects outside BILL and PAY are rejected with a message.
3. `question_coverage` (input): the coverage check above, as `reject_content`.
4. `keys_match_project` (output): a result holding keys from another project halts the run before the model sees it.

Order is policy: SECOPS is also outside the deployment's scope, and because the
restricted check runs first, the attempt halts instead of being politely
redirected. A test pins this down.

`reject_content` needs the model to hear the rejection, so guardrails run
inside `agent_tools/tool_loop.py`: a bounded loop (4 turns by default) in
which each tool reply, or each rejection, goes back to the model as a tool
message. The router above stays single shot, since it is what the
evaluations measure. Every verdict, allow included, is kept as a
`GuardrailRecord`, so a run can be audited afterwards. The tool's own bound
still runs on every call the guardrails allow, so a guardrail can narrow
what reaches Jira but never widen it.

What the tests show, with Jira calls counted rather than assumed:

| Case | Model turns | Jira queries |
|---|---|---|
| BILL, allowed | 2 | 1 |
| INFRA, rejected, model asks the user | 2 | 0 |
| INFRA, the same call with no guardrails (control) | 2 | 1 |
| INFRA, model keeps retrying | 4, then `max_turns` | 0 |
| SECOPS, halted | 1 | 0 |
| A stub tool returns a SECOPS key for a BILL query; the output guardrail halts | 1; the result never reaches the model | none (stub) |

### Human approval

**Control question:** *who decides that this write happens, and can the decision wait?*

The module has one write, `comment_on_issue`, and it never runs on the model's
say so. The graph pauses with LangGraph's `interrupt()`, saves the run to a
checkpoint, and waits for a person:

```text
decide --comment--> propose --> approve [interrupt: wait] --approved, by someone else--> post --> respond
                       |                                 --declined or self-approved--> declined --> respond
                       +-- guardrails, arguments, can the requester see the issue? -- no --> respond, nobody asked
```

| Control | How |
|---|---|
| The pause survives a restart | Checkpoints are saved to SQLite. The test stops the service, builds a new graph on a new connection to the same file, finds the pending request there and resumes it. The model is not called again. |
| The write happens once | LangGraph runs an interrupted node again from the top on resume, so the approval node has no side effects; the write is its own node after it. Resuming a finished run does not post twice. |
| No self-approval | The approver must be someone other than the requester. |
| Nobody approves an impossible write | Guardrails, argument checks and the requester's access to the issue run before anyone is asked. An issue the requester cannot see gets the same answer as one that does not exist. |
| Access is checked again at post time | Permissions can change while the approval waits. |
| The comment is attributable | Posted as the requester, with the approver recorded on it. |
| Saved approvals stay loadable | LangGraph is moving to blocking unregistered types when loading a checkpoint. The module's types are registered (`checkpoint_serde()`), and a test runs the full restart with that blocking already on. |

The write path is opt in: `build_assistant(..., comment_tool=..., checkpointer=...)`
refuses a comment tool without a checkpointer, and `ModelRouter` offers the
comment tool only when given one, so the measured evaluations run as they
were measured. `pending()` reads a waiting request from the checkpoint and
`resume()` answers it, which is all an approval screen needs.

### Tools over MCP

**Control question:** *when any agent can call these tools, who decides whose data it sees?*

`agent_tools/mcp_server.py` serves `count_jira_issues`, `search_wiki` and
`comment_on_issue` over MCP (Streamable HTTP, mcp 2.x `MCPServer`). The
caller's identity comes only from the transport:

```text
MCP client --Authorization: Bearer <token>--> token verifier --> subject --> caller in the directory
                                                                               |
                     tool call --> the assistant graph: guardrails --> bound --> Jira, as the caller
```

| Control | How it holds, tested over real HTTP against a local server |
|---|---|
| Identity from the token, never the arguments | No tool takes a user, caller or group argument. The same count returns 4 for billing_oncall's token and 3 for new_hire's. A `user="billing_oncall"` argument sent with new_hire's token is dropped by the SDK, and the answer is still new_hire's 3. |
| No valid token, no session | An unknown or expired token gets HTTP 401 with `WWW-Authenticate: Bearer error="invalid_token"`, before any tool runs. A valid token for someone outside the directory gets nothing. |
| Least privilege per tool | Reads need `jira:read`; commenting also needs `jira:write`. |
| The same controls as the assistant | Each call runs through the assistant graph, with a `PresetRouter` passing in the client's chosen tool: the bound, the guardrails and Jira's permissions apply exactly as they do in the assistant. |
| Writes still need a person | `comment_on_issue` returns `pending_approval` and a request id. There is deliberately no approve tool, so no agent can approve its own write; a person decides through `ApprovalDesk`, outside MCP, on the same checkpoints as above. |
| Every call is audited | Tool, token subject, arguments, outcome and guardrail verdicts. |

Two limits stated plainly. A guardrail's `raise_exception` refuses the call and
is recorded as a halt, but stopping the client's own agent run is the
client's job; the server cannot reach into it. And the token table is a
stand-in: in production the verifier would validate an on-behalf-of token
exchanged from the user's sign-in (OAuth 2.0 token exchange, RFC 8693), and
Jira would be called with it. The question coverage check is not in the MCP
policy, since the server never sees the user's question, only the client's
chosen arguments.

## Run it

```bash
cd 08_tools
pip install -r requirements.txt

pytest -q                       # 134 offline tests
python run_router_eval.py       # router and extraction accuracy

# the same questions, routed by a model through tool calling (needs OPENAI_API_KEY,
# or --provider anthropic with ANTHROPIC_API_KEY); prices are USD per million tokens
python run_model_router_eval.py --input-price <in> --output-price <out>
python run_model_router_eval.py --questions tests/fixtures/router_questions_heldout.json

# the coverage check: on by default; --no-coverage reproduces the router without it
python run_model_router_eval.py --questions tests/fixtures/router_questions_coverage.json
python run_model_router_eval.py --questions tests/fixtures/router_questions_coverage.json --no-coverage

# counting question, as three different users, on the model router (needs the key for LLM_PROVIDER)
python demo_assistant.py
python demo_assistant.py --router rules     # the same, offline, on the rules
python demo_assistant.py "How many INFRA incidents last quarter?" --policy finance   # with guardrails

# tool guardrails: allow, reject_content, raise_exception (offline; --live asks the configured model)
python demo_guardrails.py
python demo_guardrails.py --live "How many INFRA incidents last quarter?" --user new_hire

# human approval: a comment pauses, the service restarts, the approval resumes it (offline; --live for the model)
python demo_approval.py

# the tools over MCP: a local server and client, two tokens, two answers, a 401, a pending comment
python demo_mcp.py
python serve_mcp.py                      # serve on localhost:8765 for an MCP client; prints demo tokens

# knowledge question, routed to 07_rag's permission-filtered retrieval
python demo_assistant.py "How do I rotate the billing API credentials?" --users billing_oncall new_hire

# the bound and the clarifying question
python demo_assistant.py "How many incidents did security operations have last quarter?" --users sec_engineer
python demo_assistant.py "How many P1 incidents last quarter?" --users new_hire
```

`demo_assistant.py` imports `rag.acl` from the sibling `07_rag` folder for the
knowledge path. Its minimum retrieval score of 1.0 stands in for the calibrated
rerank threshold in the design: on this small wiki, real matches score 3 to 8
and pages that share only words like "how" and "the" score about 0.5. It was
set by eye on 8 pages, not calibrated.

## Layout

```text
08_tools/
├── agent_tools/
│   ├── jira_mock.py     Jira stand-in: project permissions, issue security levels, read only
│   ├── jql_tool.py      QuerySpec, validator, JQL compiler, tool schema, count_jira_issues
│   ├── router.py        classify (two cues), extract (project, type, priority, status, period)
│   ├── model_router.py  the same routing and extraction by a model, through tool calling
│   ├── coverage.py      stops valid queries that do not cover the question (two projects, none)
│   ├── guardrails.py    tool guardrails: allow, reject_content, raise_exception; the demo policy
│   ├── tool_loop.py     bounded tool loop that runs guardrails and tells the model when one rejects
│   ├── comments.py      comment_on_issue, the one write; kept apart from the read only count tool
│   ├── mcp_server.py    the tools over MCP; identity from the bearer token; ApprovalDesk for writes
│   ├── evaluate.py      router and extraction accuracy on the labeled set, rules or model
│   └── assistant.py     LangGraph flow: classify, extract, run tool or retrieve, respond
├── run_router_eval.py
├── run_model_router_eval.py
├── demo_assistant.py
├── demo_guardrails.py
├── demo_approval.py
├── demo_mcp.py
├── serve_mcp.py
└── tests/               134 offline tests, synthetic Jira, development, held-out and coverage questions
```

## Limits

* The Jira here is a mock. It reproduces project permissions and issue
  security levels, not the Jira REST API, pagination or rate limits.
* The coverage check cannot tell a second project named as scope ("BILL and
  PAY") from one named in passing ("after a PAY outage"), and reads "payment"
  as the project PAY. It also cannot judge whether an informal name the code
  does not know ("OPS", "the invoice stack") means the project the model chose.
* The tool filters by creation date only. "Resolved this month" cannot be
  expressed: it would need a resolution date filter.
* The MCP server's tokens are a static table standing in for on-behalf-of
  tokens, and the server is meant for localhost.
* Answers are composed by code, not by a model, so every word is testable. In
  production a model would phrase them, with the count, keys and JQL passed
  through unchanged.
