# Runbook: knowledge navigation over network logs

How to tell the navigator is unhealthy, how to confirm it, and what to do. Each entry is one failure mode: the signal, how to confirm it, the action, the owner and the fallback.

**The rule behind every entry:** the navigator is never in an incident's critical path. When it is wrong, stale or down, engineers work from Splunk, ServiceNow and the runbooks directly, as they did before it existed. It reads and explains; it changes nothing.

```bash
cd 14_knowledge_graph
python run_health_check.py     # every check, on the data as it is and once per planted fault
python -m pytest -q            # the same, plus the release gate and the permission tests
```

## The signals

| Check | What it measures | Alarm |
|---|---|---|
| `parse:<source>` | Share of each log's lines that parse | Below 99% |
| `fresh:<source>` | Hours since each feed's newest line | Over 6 hours for a stream; over 31 days for the monthly asset inventory |
| `graph store` | Nodes and edges read back from the store, against those written | Any difference |
| `permissions` | Lines or pages returned that the asker may not read, across every question that names some, for both retrievers | Above 0 |
| `regression` | Held-out 3 multi-hop evidence found by the graph walk | Below the 16 of 26 recorded when the set was first scored |

## Every alarm has been made to fire

A check that never fires proves nothing. [`run_health_check.py`](run_health_check.py) runs every check on the fixtures as they are, then plants each fault below on a copy and records what fired ([`results/health.json`](results/health.json)):

| Planted fault | Alarms | What it showed |
|---|---|---|
| None (healthy) | **none** | 13 checks, all within limits. VPN's newest line is 2.8 hours old |
| A firmware upgrade at noon changes the VPN log format | `parse:vpn` 82.7% (61 of 352 lines) | The rebuild stops at the first bad line (`vpn:292`), so half a feed is never indexed. The other checks cannot run |
| The firewall's log forwarder stops at 11:00 | `fresh:firewall` 12.0 hours; `regression` 14 of 26 | Every line that did arrive still parses. Only freshness sees it. Missing data also cost recall |
| An ingestion change tags the auth log readable by network operations | `permissions` 6 items; `regression` 14 of 26 | Six auth lines reached a network analyst. Recall fell too, because the extra lines crowd the top 10 |
| A graph save stops partway (5% of nodes lost) | `graph store` 1,531 of 1,612 nodes; `permissions` and `regression` cannot run | The walk fails on the first missing node (`KeyError`) rather than returning thinner answers |
| The walk is retuned from 4 hops to 1 without re-measuring | `regression` 12 of 26 | Nothing else notices: data and permissions are fine. Only the release gate catches a change to the retriever |

`tests/test_health.py` checks that healthy data raises no alarm, that each fault trips the check meant for it, and that the table above is what the code produces now. Five weakened checks were tried (a lower parse threshold, a longer freshness limit, a permissions check that always passes, a store check that always passes, a gate at zero). The tests caught all five.

## Failure modes

### 1. A feed changes format

- **Signal:** `parse:<source>` below 99%. The rebuild stops with the source, line number and the first 80 characters of the line.
- **Confirm:** Look at the named line. A changed timestamp style, renamed fields or a new prefix after a firmware or vendor upgrade is the usual cause. Check the change calendar for that device class.
- **Action:**
  1. Do not loosen the pattern to make the alarm stop.
  2. Update the parser in `corpus.py` for the new format and add the new line shape to the tests.
  3. Rebuild, then rerun the health check.
- **Owner:** the network team that owns the device, with the AI team for the parser.
- **Fallback:** the last good index keeps serving. Answers miss lines after the change, so tell the on-call channel which feed and from when.

This is the failure that ran silently for three days in an earlier production system I supported: every check was green and nothing new was coming in. Here it fails on the first line.

### 2. A feed stops

- **Signal:** `fresh:<source>` over its limit. Usually no other check fires: every line that arrived still parses.
- **Confirm:** Compare the newest line in the source system (Splunk) with the newest line indexed. If Splunk is also stale, the forwarder or the device is the problem, not the navigator.
- **Action:**
  1. Restart or repair the forwarder.
  2. Backfill the gap.
  3. Rebuild, then rerun the health check.
- **Owner:** the logging platform team.
- **Fallback:** answers about the gap are incomplete. Say so in the channel, with the source and the time the gap began.

### 3. Someone can see what they should not

- **Signal:** `permissions` above 0. Any value above zero is a security incident, not a quality issue.
- **Confirm:** The alarm names the items. Check their `readers` against the source's readers in `corpus.py`, and check the vector store's metadata for the same keys.
- **Action:**
  1. Stop serving the navigator that day.
  2. Tell Security what was exposed, to whom, and since when.
  3. Fix the tagging, rebuild, and rerun the health check before serving again.
- **Owner:** Security leads. The AI team fixes it.
- **Fallback:** the manual process.

The permission filter is applied before ranking, in the vector query and on the graph. `test_no_retriever_ever_returns_what_the_asker_may_not_read` and its widened-filter control show that the zero is the filter working.

### 4. The graph store is incomplete

- **Signal:** `graph store` node or edge counts differ from what was written. Queries may fail with `KeyError` on a missing node.
- **Confirm:** Count nodes in Upstash Redis (`SCARD ng:nodes`) against the graph built from the same data.
- **Action:** Rebuild the store from the source data: `Navigator.live(reindex=True)` rewrites the graph and the vectors.
- **Owner:** the AI team.
- **Fallback:** the manual process until the rebuild completes.

### 5. The retriever changed and got worse

- **Signal:** `regression` below the recorded score. In CI, `test_held_out_3_was_scored_by_the_retriever_as_it_is_now` fails, which blocks the merge.
- **Confirm:** Run `python run_evaluation.py` and compare it with `results/held_out_3.json` question by question. The `missed` lists show what was lost.
- **Action:** Revert the change. A retriever change is a release: it ships only when it scores at least as well on the held-out set, and its new results are committed with it. Never re-score on the held-out questions while tuning; write a new set.
- **Owner:** the AI team.
- **Fallback:** the previous release.

### 6. The answer cites what it was not shown

- **Signal:** the answer comes back with reason `unknown_citation` or `no_citation`, and the asker sees the evidence instead of an answer. This only happens live, because offline there is no model.
- **Confirm:** The trace records the reason, not the content.
- **Action:** One answer is noise. A rising share after a model or prompt change is a regression, so roll the change back.
- **Owner:** the AI team.
- **Fallback:** the asker still gets the evidence, so this degrades the answer, not the search.

Covered by `test_a_grounded_answer_cites_only_what_it_was_shown`. The share of answers withheld is not yet measured over time.

### 7. The embedding model changed

- **Signal:** startup refuses with "the Upstash index has N dimensions; the model makes M".
- **Action:** Create an index for the new model's dimensions, reindex, and rerun `run_evaluation.py --live` before switching. A new model is a release, like any retriever change.
- **Owner:** the AI team, with the platform team for the index.

Covered by `test_an_index_made_for_another_model_is_refused_before_indexing`.

## Not built here

These are needed in production and named so nobody assumes they exist:

- **A pause switch.** Stopping the navigator means not serving the page. In production, this belongs behind a feature flag owned by Production Services.
- **Scheduled health runs and paging.** The checks run on demand and in CI, not on a timer, and nothing pages anyone.
- **Engineer corrections.** There is no "this is wrong" button, so the correction rate, the best live signal of trust, is not measured.
- **Withheld-answer trend.** Reason codes are traced per answer but not aggregated.
- **Freshness against the wall clock.** Offline, freshness is measured at the time the questions are asked (`NOW` in `corpus.py`). Live, it would use the current time.
