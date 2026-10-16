# 11 AI FinOps

What each answer costs, priced from the trace of the run that produced it;
what a *correct* answer costs, which is the number to choose between options
on; and a budget per identity, checked before every model call so spend is
held to a limit rather than reported after it.

## The rule: no price, no number

Every price comes from [`prices.yaml`](prices.yaml), with the page it was read
from and the date. A model missing from the file is **unpriced**. Any cost that
includes an unpriced call is reported as unpriced, along with the models to add,
never as $0 and never as a partial sum. A partial sum would understate the spend
and still look like a real number.

| Model | Input | Cached input | Output | Per 1M tokens, Standard tier | Checked |
|---|---|---|---|---|---|
| `gpt-6-luna` | $0.10 | $0.01 | $0.50 | [OpenAI model page](https://developers.openai.com/api/docs/models/gpt-6-luna) | 2026-10-16 |
| `gpt-4o-mini` (deprecated) | $0.15 | $0.075 | $0.60 | [OpenAI model page](https://developers.openai.com/api/docs/models/gpt-4o-mini) | 2026-10-16 |

Billing rules applied, from the same sources:

* **Reasoning tokens** are billed as output, and OpenAI counts them inside
  `output_tokens` ([reasoning guide](https://developers.openai.com/api/docs/guides/reasoning)),
  so they are recorded but not added again.
* **Cached input** bills at the cached rate. Spans now record
  `cached_input_tokens` from the provider's usage report.
* **Long context:** a gpt-6-luna request with more than 272K input tokens
  bills the *whole request* at 2x input and 1.5x output.

`claude-sonnet-5-5`, which the repo can also call, is deliberately not in the
file yet. Its runs report as unpriced until someone adds it with a source.

## Results

### Cost per correct answer, from runs already recorded

[`run_cost_per_correct.py`](run_cost_per_correct.py) prices runs already
recorded against real models: 08's router on three question sets (mean tokens
per question and correct routes, from `08_tools/results_published.json`), 07's
300-query CUAD answer evaluation, and the rules router, scored offline. No
model is called.

| Held-out set (20 questions nobody tuned for) | Correct | Per question | Per correct route |
|---|---|---|---|
| Rules router | 9/20 | $0 | $0, but 11 wrong |
| gpt-4o-mini | 18/20 | $0.000122 | $0.000135 |
| gpt-6-luna | 18 to 19/20 (3 runs) | $0.000092 | $0.000099 |
| gpt-6-luna with the coverage check | 19/20 | $0.000096 | $0.000101 |

**Cost per correct answer flatters a free option.** The rules router costs
nothing per call and gets 11 of 20 held-out questions wrong. The comparison
that matters is what each *extra* correct answer costs over the free option:

| Against the rules router | Accuracy | Per extra correct route | Per million questions |
|---|---|---|---|
| gpt-6-luna with the coverage check, held-out | +50 points | $0.00019 | $96 |
| gpt-4o-mini, held-out | +45 points | $0.00027 | $122 |
| gpt-6-luna without the check, coverage set | −5 points | no gain, no price claimed | $95 |
| gpt-6-luna with the check, coverage set | +5 points | $0.0020 | $101 |

What it shows:

* **On questions nobody tuned for, the model earns its cost.** It adds 50
  points of routing accuracy for about $96 per million questions.
* **gpt-4o-mini is beaten on both axes.** It costs more per question than
  gpt-6-luna and routes no better, so moving off the deprecated model is a
  saving, not a cost.
* **On the coverage set the model barely beats free rules.** That set was
  written to stress project scope. Rules route 14 of 20, the model 13 to 15.
  Where the questions are about scope, the model's value is in the coverage
  check, not in routing.

### What a control costs

The coverage check makes the router state which words name the project, which
adds input tokens to every call:

| gpt-6-luna | Per question | Per correct route |
|---|---|---|
| Held-out set, check on vs off | +4.0% | +2.1% |
| Coverage set, check on vs off | +6.4% | **−7.8%** |

On the coverage set the check costs more per call and less per correct answer,
because it turns wrong runs into right ones (13 correct without it, 15 in each
of 3 runs with it). A control that prevents wrong answers can lower the cost
of a correct one.

### 07 answers

300 CUAD queries on gpt-4o-mini: **$0.000238 per query** and **$0.000358 per
correct outcome** (200 correct: 75 answers cited on the lawyer-annotated span
and 125 correct abstentions), or $0.000317 counting citations outside the
annotated span (226). A third of the spend went to queries that ended wrong.
07 has not been re-run on gpt-6-luna, so its cost there is not reported.

### The file reproduces what was recorded

08's runs were costed at the time from prices typed on the command line. The
file reproduces all 9 recorded costs within 0.8%. The small differences come
from applying the file to rounded mean token counts. 08's gpt-4o-mini runs were
recorded with no cost at all; the file now prices them.

### Budgets: held to the limit, not reported after it

[`finops/budget.py`](finops/budget.py) holds a budget per identity per day.
Spend is **measured, not estimated**. A `SpendMeter` is a trace exporter: it
prices each model call from its generation span as the span ends, the same
record module 10 writes and the cost report reads. Before every model call, the
policy adds a reserve (what one more call is expected to cost) to what the
identity has spent today:

| Before the call | Behaviour | Effect |
|---|---|---|
| spent + reserve within the limit | `allow` | the call is made |
| … and at or above 80% of it | `allow`, with `warning` in the trace | the call is made; the trace shows the identity is close |
| spent + reserve over the limit | `reject_content` | **no model call**; the user is told the budget is used and when it resets |
| the model to be called has no price, or earlier calls today had none | `reject_content` | no model call: spend that can't be measured can't be kept within a limit |

Running over budget is not a security event, so nothing halts. The behaviours
are 08's guardrail contract, and each verdict is a `model_budget` guardrail span
with the spend, reserve and limit. 08 takes the budget as a parameter
(`build_assistant(..., budget=)`, `ToolLoop(..., budget=)`) through a
one-method protocol, so 08 does not depend on this module. The rules router
calls no model, so it is never checked.

[`run_budget_check.py`](run_budget_check.py) measures it through 08's assistant
and tool loop, offline. The scripted model is priced as gpt-6-luna and reports
the held-out mean of 728 input and 39 output tokens, so each call costs what an
average real router call does: $0.0000923. Each scenario has a control run with
no budget.

| Scenario | With the budget | Without (control) |
|---|---|---|
| billing_oncall, $0.001 a day, asks 12 counts | 10 answered, refused from question 11 with **no model call**, warned on runs 9 and 10; $0.000923 spent | 12 answered |
| new_hire, separate budget, asks 2 | 2 answered: one identity's spend never blocks another | 2 answered |
| the next day | billing_oncall answered again | n/a |
| A model looping on an out-of-scope project in the tool loop, budget of 2.5 calls | **stopped mid-run** at turn 3, after 2 model calls | ran to the 4-turn limit, 4 model calls |
| The router switched to a model with no price | refused, 0 model calls | n/a |

Model calls in the daily scenario: 12 with the budget against 14 without.
Spend never exceeds the limit because the reserve is checked before the call,
not after. Checking only what was already spent would let the 11th call
through and end the day at $0.001015.

## Costing a trace

Every generation span records its model and token counts
([10_observability](../10_observability/)), so a run's cost comes from its
trace alone. [`run_cost_report.py`](run_cost_report.py) prices a folder of trace
files by identity, by workflow and by how each run ended. It also reports the
spend on runs that ended without an answer: halted, clarified, rejected, or at
the turn limit.

```bash
cd 11_finops
pip install -r requirements-test.txt
python run_cost_per_correct.py
python run_budget_check.py
python run_cost_report.py ../08_tools/traces
```

Run on traces from the offline demos, the report prices nothing. The scripted
model isn't in the price file, and 07's test model names no model, so every
total is reported as unpriced, naming what to add. That is the rule working.

The 08 and 07 evaluation scripts now take their prices from this file when
none are given on the command line. A model the file doesn't price is
reported as unpriced, not costed at a typed guess.

## Tests

From `11_finops`, `pytest` (28 tests): prices and the refusal, cached and
long-context billing, costing trace records and a real trace file, rollups
that refuse partial sums, the cost per correct answer figures (including the
check that the file reproduces 08's recorded costs), and budgets, from the
meter and policy up to the scenarios above run through 08.

Each of these deliberate breakages fails a test: counting an unpriced call as
$0, keeping a partial sum in a rollup, pricing a missing model at zero, applying
the long-context rate at exactly 272K instead of above it, billing cached input
at the full rate, and claiming a price per extra correct answer where the model
did no better than rules. For budgets: the assistant ignoring a refusal,
checking spend without the reserve, failing open on an unpriced model, the
tool loop checking only its first turn, an MCP call not charged to its token's
subject, and spend that never resets by day.

## Limits

* **Standard tier only.** Batch and Flex (50% of Standard) and Fast (2x) aren't
  modelled, because the repo calls Standard.
* **Cache writes are not priced.** gpt-6-luna bills them at 1.25x input, but the
  usage report the spans read does not separate them yet.
* **Recorded runs are means.** 08's published results keep mean tokens per
  question, so per-question costs are means too, with 1 to 3 runs per option.
* **The development set favours the model.** The router's prompt was written
  after seeing those questions; the held-out set is the fair comparison.
* **The meter is in memory.** Spend is rebuilt from the trace, not persisted, so
  a restarted process starts the day at zero. Replaying today's trace file into
  a fresh meter rebuilds it; a shared store would be needed across processes.
* **The reserve is a setting.** A call that costs more than the reserve can take
  spend past the limit by the difference. Set it from the p95 call cost, or from
  the model's maximum output.
* **06 and 07 are not budgeted.** Their runs carry no identity, so their spend
  is reported by workflow, not held to a per-user limit.
