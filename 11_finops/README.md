# 11 AI FinOps

What each answer costs, priced from the trace of the run that produced it,
and what a *correct* answer costs, which is the number to choose between
options on.

> **Status:** prices and costing are built. Per-identity budgets enforced
> before each model call follow in the next change.

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
python run_cost_report.py ../08_tools/traces
```

Run on traces from the offline demos, the report prices nothing. The scripted
model isn't in the price file, and 07's test model names no model, so every
total is reported as unpriced, naming what to add. That is the rule working.

The 08 and 07 evaluation scripts now take their prices from this file when
none are given on the command line. A model the file doesn't price is
reported as unpriced, not costed at a typed guess.

## Tests

From `11_finops`, `pytest` (18 tests): prices and the refusal, cached and
long-context billing, costing trace records and a real trace file, rollups
that refuse partial sums, and the cost per correct answer figures, including
the check that the file reproduces 08's recorded costs.

Each of these deliberate breakages fails a test: counting an unpriced call as
$0, keeping a partial sum in a rollup, pricing a missing model at zero, applying
the long-context rate at exactly 272K instead of above it, billing cached input
at the full rate, and claiming a price per extra correct answer where the model
did no better than rules.

## Limits

* **Standard tier only.** Batch and Flex (50% of Standard) and Fast (2x) aren't
  modelled, because the repo calls Standard.
* **Cache writes are not priced.** gpt-6-luna bills them at 1.25x input, but the
  usage report the spans read does not separate them yet.
* **Recorded runs are means.** 08's published results keep mean tokens per
  question, so per-question costs are means too, with 1 to 3 runs per option.
* **The development set favours the model.** The router's prompt was written
  after seeing those questions; the held-out set is the fair comparison.
