# Architecture standards

<!-- Rendered from standards.yaml and exceptions.yaml by render_standards.py. Edit those, not this. -->

28 standards. 27 are enforced by named tests or static checks that run in CI; 1 is enforced by design review only, and says so. A test checks that every test cited here exists. Conformance per module is in the [scorecard](scorecard.md).

Each standard has an ID used by the [design review checklist](design-review-checklist.md), the ADRs that explain it, and the modules it applies to. Where a module doesn't meet a standard yet, the gap is in the [exception register](#exception-register) with an owner and an expiry date.

## AI

### AI-01 · The simplest pattern that reliably works

Start with the least autonomous pattern that solves the problem, and add a model or a loop only when a measurement shows the simpler one falls short.

*Why:* Every added step is cost and a way to be wrong. The repo's routing, triage and evaluations are each justified by a number.

*Applies to:* 06, 08, 12 · *ADRs:* [0001](adr/0001-model-router-over-rules.md), [0011](adr/0011-no-model-in-event-triage.md)

*Enforced by:* design review only. No test checks this yet.

### AI-02 · A model never writes queries

A model produces typed, validated arguments; the tool builds the query. No free text from a model reaches a query language.

*Why:* Typed arguments leave nothing to inject into.

*Applies to:* 08 · *ADRs:* [0002](adr/0002-typed-arguments-not-jql.md)

*Enforced by:*

- 08: [`test_injection_through_a_field_is_rejected`](../../08_tools/tests/test_jql_tool.py), [`test_raw_jql_argument_cannot_pass_through`](../../08_tools/tests/test_jql_tool.py), [`test_jql_is_built_by_the_tool`](../../08_tools/tests/test_jql_tool.py)

### AI-03 · Every run ends in a typed outcome

An agent turn ends in exactly one typed outcome, and a run always states why it ended. A reply outside the allowed set stops the run; it is never guessed at.

*Why:* An agent that cannot say why it stopped cannot be audited or debugged.

*Applies to:* 06, 08 · *ADRs:* none

*Enforced by:*

- 06: [`test_anything_else_is_an_interruption_not_a_guess`](../../06_agents/test_agents_06_v3_1.py), [`test_every_run_ends_with_a_status`](../../06_agents/test_agents_06_v3_1.py)
- 08: [`test_a_reply_with_no_tool_call_is_not_guessed_at`](../../08_tools/tests/test_assistant_model_router.py)

### AI-04 · Answers are grounded or withheld

An answer ships only with evidence that resolves to what the model was shown (a citation, or the words that named the query's scope); otherwise the system abstains or asks.

*Why:* A confident wrong answer is the expensive failure.

*Applies to:* 07, 08 · *ADRs:* none

*Enforced by:*

- 07: [`test_graph_abstains_when_citation_is_fabricated`](../../07_rag/tests/test_citations_and_graph.py)
- 08: [`test_the_coverage_check_stops_a_half_answer_in_the_main_path`](../../08_tools/tests/test_assistant_model_router.py), [`test_missing_project_asks_a_question_and_runs_nothing`](../../08_tools/tests/test_assistant.py)

### AI-05 · Guardrails return one of three behaviours, in a defined order

A tool guardrail allows, rejects with a message the model can act on, or halts the run. Security checks run first and halt; deployment policy coaches.

*Why:* A security event must not be politely redirected into a retry.

*Applies to:* 08, 12 · *ADRs:* [0004](adr/0004-guardrail-behaviours-and-order.md)

*Enforced by:*

- 08: [`test_order_matters_restricted_halts_before_scope_could_redirect`](../../08_tools/tests/test_guardrails.py), [`test_raise_exception_halts_the_graph_even_for_a_call_the_bound_refuses`](../../08_tools/tests/test_assistant_model_router.py)
- 12: [`test_an_event_cannot_point_the_service_at_a_restricted_project`](../../12_integration/tests/test_triage.py)

### AI-06 · A write waits for a person who is not the requester

Any action that changes data pauses for approval, survives a restart while waiting, and cannot be approved by the identity that asked for it. No agent-facing interface can approve.

*Why:* Accountability for a change belongs to a person.

*Applies to:* 08, 12 · *ADRs:* [0005](adr/0005-approval-as-interrupt.md)

*Enforced by:*

- 08: [`test_the_requester_cannot_approve_their_own_write`](../../08_tools/tests/test_approval.py), [`test_resume_from_a_saved_checkpoint_after_a_restart`](../../08_tools/tests/test_approval.py), [`test_a_comment_over_mcp_waits_for_a_person_and_there_is_no_approve_tool`](../../08_tools/tests/test_mcp_server.py)
- 12: [`test_the_service_cannot_approve_itself_and_a_person_posts_it_once`](../../12_integration/tests/test_triage.py)

### AI-07 · Measured on questions nobody tuned for

A model-dependent component is measured on a held-out set written without seeing the prompt, over more than one run, against a non-model baseline.

*Why:* A score on the development set measures the prompt's fit to it.

*Applies to:* 07, 08 · *ADRs:* [0001](adr/0001-model-router-over-rules.md), [0013](adr/0013-offline-tests-measured-live-runs.md)

*Enforced by:*

- 08: [`test_template_placeholders_are_refused`](../../08_tools/tests/test_model_router.py), [`test_rules_are_free_but_each_extra_correct_route_has_a_price`](../../11_finops/tests/test_cost_per_correct.py)

*Exception EXC-004* for 07, until 2027-04-16: 07's answer evaluation ran once, on one model (gpt-4o-mini), with no non-model baseline for answers.

### AI-08 · Memory is selected, bounded, and never widens a check

What a model sees from past turns is chosen by relevance within a bound, with the user's standing instructions pinned. Nothing from memory counts as evidence for a check except the user's own pinned instructions.

*Why:* Appending everything grows cost without limit; a summary forgets; and a project named in passing earlier must not authorize a query now.

*Applies to:* 09 · *ADRs:* [0016](adr/0016-memory-retrieved-and-pinned.md)

*Enforced by:*

- 09: [`test_retrieval_with_pins_recalls_most_at_a_fraction_of_the_cost`](../../09_memory/tests/test_strategies.py), [`test_pinning_is_what_keeps_the_standing_instruction`](../../09_memory/tests/test_strategies.py), [`test_a_project_named_earlier_is_not_evidence_for_a_new_question`](../../09_memory/tests/test_assistant_memory.py)

### OPS-01 · Every run is traced, and content stays out by default

Every agent run is one trace with typed spans. Prompts, documents, model output and tool results are recorded only when explicitly enabled; metadata holds only identities, counts and values checked against a known format.

*Why:* Traces are read by more people than the data they describe.

*Applies to:* 06, 07, 08, 09, 12 · *ADRs:* [0007](adr/0007-trace-content-off-by-default.md)

*Enforced by:*

- 06: [`test_no_content_reaches_the_trace_file_and_the_control_sees_all_of_it`](../../10_observability/tests/test_leak_check.py)
- 07: [`test_no_content_reaches_the_trace_file_and_the_control_sees_all_of_it`](../../10_observability/tests/test_leak_check.py)
- 08: [`test_no_content_reaches_the_trace_file_and_the_control_sees_all_of_it`](../../10_observability/tests/test_leak_check.py), [`test_output_info_never_echoes_free_text_from_the_model`](../../08_tools/tests/test_guardrails.py), [`test_a_count_reconciles_span_for_span`](../../08_tools/tests/test_traces.py)
- 09: [`test_history_reaches_the_model_but_not_the_trace`](../../09_memory/tests/test_assistant_memory.py), [`test_the_trace_names_which_turns_the_model_saw_by_position_only`](../../09_memory/tests/test_assistant_memory.py)
- 12: [`test_no_content_reaches_the_trace_file_and_the_control_sees_all_of_it`](../../10_observability/tests/test_leak_check.py)

### OPS-02 · Costs come from a sourced price file

Every price has a source and a date. A cost that includes an unpriced model is reported as unpriced, never as zero or a partial sum.

*Why:* A wrong number that looks real is worse than a missing one.

*Applies to:* 07, 08, 11 · *ADRs:* [0008](adr/0008-prices-from-a-sourced-file.md)

*Enforced by:*

- 07: [`test_rag_cost_per_correct_answer`](../../11_finops/tests/test_cost_per_correct.py)
- 08: [`test_the_price_file_reproduces_every_cost_recorded_in_08`](../../11_finops/tests/test_cost_per_correct.py)
- 11: [`test_a_model_missing_from_the_file_is_refused`](../../11_finops/tests/test_prices_and_costing.py), [`test_one_unpriced_call_makes_the_run_and_every_total_unpriced`](../../11_finops/tests/test_prices_and_costing.py)

### OPS-03 · Model spend is held to a budget before the call

Every model call on behalf of an identity is checked against that identity's budget first, with a reserve for the call itself, so spend never passes the limit.

*Why:* A budget reported after the fact is a bill, not a control.

*Applies to:* 06, 07, 08, 12 · *ADRs:* [0009](adr/0009-budgets-before-the-call.md)

*Enforced by:*

- 08: [`test_a_daily_budget_stops_at_the_expected_question_and_calls_nothing_after`](../../11_finops/tests/test_budget.py), [`test_a_budget_stops_a_runaway_loop_mid_run`](../../11_finops/tests/test_budget.py)
- 12: [`test_the_model_route_is_held_to_the_callers_budget`](../../12_integration/tests/test_gateway.py)

*Exception EXC-003* for 06, 07, until 2027-03-31: 06's agents and 07's answers carry no caller identity, so their model calls are not held to a per-identity budget.

## Application

### APP-01 · Every call is made as a named identity

Every call to a system of record names the user it acts for, or a service identity for work no person started. Permissions are the system's own, never replicated into the agent.

*Why:* Delegated permissions cannot drift; a replicated copy can.

*Applies to:* 07, 08, 12 · *ADRs:* [0003](adr/0003-delegated-permissions.md), [0012](adr/0012-service-identity-and-idempotent-writes.md)

*Enforced by:*

- 07: [`test_zero_leakage_with_filter`](../../07_rag/tests/test_permissions.py)
- 08: [`test_jira_refuses_calls_without_a_named_user`](../../08_tools/tests/test_jql_tool.py), [`test_count_depends_on_who_asks`](../../08_tools/tests/test_jql_tool.py)
- 12: [`test_jira_permissions_still_apply_through_the_gateway`](../../12_integration/tests/test_gateway.py)

### APP-02 · Identity comes from the credential, never from the arguments

Who is asking is taken from a verified token. No tool or route accepts a user, caller or group argument.

*Why:* An agent that can name the user can name any user.

*Applies to:* 08, 12 · *ADRs:* [0006](adr/0006-identity-from-the-token.md)

*Enforced by:*

- 08: [`test_no_tool_takes_a_user_argument`](../../08_tools/tests/test_mcp_server.py), [`test_naming_another_user_in_the_arguments_changes_nothing`](../../08_tools/tests/test_mcp_server.py)
- 12: [`test_the_contract_refuses_a_request_before_any_agent_runs`](../../12_integration/tests/test_gateway.py)

### APP-03 · Fail closed

Unknown permissions, missing ancestors, unpriced models and unverifiable inputs are refused, not defaulted to open.

*Why:* Every default-open path is a leak waiting for its input.

*Applies to:* 07, 11, 12 · *ADRs:* [0008](adr/0008-prices-from-a-sourced-file.md)

*Enforced by:*

- 07: [`test_unknown_space_fails_closed`](../../07_rag/tests/test_permissions.py), [`test_chunk_without_gates_is_never_returned`](../../07_rag/tests/test_permissions.py)
- 11: [`test_an_unpriced_model_is_refused_before_it_is_called`](../../11_finops/tests/test_budget.py)
- 12: [`test_an_unsigned_or_forged_webhook_is_refused`](../../12_integration/tests/test_adapters.py)

### APP-04 · Tests run offline, and every control has a positive control

Every suite runs with no API key. A test that proves something never happens is paired with a run where it does (the control), so a zero means the check works, not that it is blind.

*Why:* A zero without a control is indistinguishable from a broken test.

*Applies to:* 06, 07, 08, 09, 10, 11, 12 · *ADRs:* [0013](adr/0013-offline-tests-measured-live-runs.md)

*Enforced by:*

- 06: [`test_a_call_with_no_step_left_raises`](../../tests/test_scripted_model.py)
- 07: [`test_unfiltered_baseline_leaks`](../../07_rag/tests/test_permissions.py)
- 08: [`test_without_the_guardrail_the_tool_would_have_run_it`](../../08_tools/tests/test_guardrails.py)
- 09: [`test_without_the_pin_the_same_words_are_not_evidence`](../../09_memory/tests/test_assistant_memory.py)
- 10: [`test_no_content_reaches_the_trace_file_and_the_control_sees_all_of_it`](../../10_observability/tests/test_leak_check.py)
- 11: [`test_a_daily_budget_stops_at_the_expected_question_and_calls_nothing_after`](../../11_finops/tests/test_budget.py)
- 12: [`test_without_the_marker_one_lost_response_posts_twice`](../../12_integration/tests/test_integration_check.py)

### APP-05 · One way to choose a model

Modules get their model through llm_provider.get_chat_model, so the provider and model are one run setting.

*Why:* Two selection paths drift: change the default in one and the other keeps the old one.

*Applies to:* 01, 02, 03, 04, 05, 06, 07, 08 · *ADRs:* none

*Checked by:* static check `model_construction` ([conformance.py](conformance.py)) over the code of every module it applies to.

*Exception EXC-001* for 07, until 2026-12-31: 07_rag/rag/graph.py builds its OpenAI and Anthropic models itself instead of calling llm_provider.get_chat_model.

### APP-06 · Memory belongs to one identity, can be deleted, and expires

A session is bound to the identity that created it; opening another identity's session is refused. A user's history can be deleted, and expires after its retention period, deleted rather than hidden.

*Why:* A session id is not a credential, and history kept forever is a liability, not a feature.

*Applies to:* 09 · *ADRs:* [0016](adr/0016-memory-retrieved-and-pinned.md)

*Enforced by:*

- 09: [`test_a_sqlite_session_survives_a_restart_and_belongs_to_its_owner`](../../09_memory/tests/test_session.py), [`test_someone_elses_session_is_refused_before_anything_runs`](../../09_memory/tests/test_assistant_memory.py), [`test_expired_items_are_deleted_on_the_next_write_not_just_hidden`](../../09_memory/tests/test_session.py), [`test_pop_undoes_the_last_item_and_clear_deletes_everything`](../../09_memory/tests/test_session.py)

## API

### API-01 · Every external interface is behind the gateway

Clients reach agents through one gateway that applies identity, scopes, rate limits, the contract and budgets in a fixed order.

*Why:* One policy point is one place to audit and change.

*Applies to:* 08, 12 · *ADRs:* none

*Enforced by:*

- 12: [`test_no_token_no_entry`](../../12_integration/tests/test_gateway.py), [`test_a_route_needs_its_scope`](../../12_integration/tests/test_gateway.py), [`test_a_burst_is_limited_per_subject_and_recovers`](../../12_integration/tests/test_gateway.py)

*Exception EXC-002* for 08, until 2027-03-31: 08's MCP server is reached directly, not through the gateway.

### API-02 · The contract is generated, committed and served

Each API version has an OpenAPI contract generated from the code, committed to the repo and served by the API. Drift between them fails the build.

*Why:* An API change should be visible in review as a contract diff.

*Applies to:* 12 · *ADRs:* [0014](adr/0014-api-versions-and-sunset.md)

*Enforced by:*

- 12: [`test_the_committed_contracts_match_the_code`](../../12_integration/tests/test_contracts.py), [`test_the_served_contract_is_the_committed_one`](../../12_integration/tests/test_gateway.py)

### API-03 · No breaking change within a major version

A change a client of the published contract could fail on requires a new major version. The CI gate compares a pull request's contract with the base branch.

*Why:* Clients upgrade on their schedule, not the provider's.

*Applies to:* 12 · *ADRs:* [0014](adr/0014-api-versions-and-sunset.md)

*Enforced by:*

- 12: [`test_the_gate_fails_a_pr_that_breaks_v2`](../../12_integration/tests/test_contracts.py), [`test_the_checker_classifies_every_labeled_edit`](../../12_integration/tests/test_contracts.py)

### API-04 · Versions are deprecated in the open and retired on a date

A deprecated version says so in its contract and in Deprecation, Sunset and Link headers on every response, and returns 410 naming its successor after the sunset.

*Why:* A client should learn of a retirement from the API, not from an outage.

*Applies to:* 12 · *ADRs:* [0014](adr/0014-api-versions-and-sunset.md)

*Enforced by:*

- 12: [`test_v1_is_served_with_its_deprecation_and_sunset`](../../12_integration/tests/test_gateway.py), [`test_after_its_sunset_v1_is_gone_and_says_where_to_go`](../../12_integration/tests/test_gateway.py), [`test_v1_is_marked_deprecated_in_its_contract`](../../12_integration/tests/test_contracts.py)

### API-05 · Errors are problem details, and hidden things look missing

Errors are application/problem+json. A resource the caller may not see gets the same answer as one that does not exist.

*Why:* An error that differs for hidden resources reveals that they exist.

*Applies to:* 08, 12 · *ADRs:* none

*Enforced by:*

- 08: [`test_a_comment_on_an_issue_the_token_holder_cannot_see_is_refused`](../../08_tools/tests/test_mcp_server.py)
- 12: [`test_triage_status_follows_the_event_driven_run`](../../12_integration/tests/test_gateway.py)

## Integration

### INT-01 · One envelope for every source

Events from every source are converted at the edge into one envelope with a source-unique id; consumers never parse a vendor's format.

*Why:* Consumers then depend on one contract, not on every source.

*Applies to:* 12 · *ADRs:* [0015](adr/0015-in-process-stand-ins.md)

*Enforced by:*

- 12: [`test_a_service_alert_is_already_event_shaped`](../../12_integration/tests/test_adapters.py), [`test_the_envelope_refuses_what_it_cannot_identify`](../../12_integration/tests/test_adapters.py)

### INT-02 · Handled exactly once, keyed on the business entity

Consumers assume at-least-once delivery. Duplicates are detected by event, and the work itself is keyed on the business entity, so two sources reporting one thing cause one action.

*Why:* Broker deduplication works within a time window and per source; a business key has neither limit.

*Applies to:* 12 · *ADRs:* [0010](adr/0010-idempotency-on-the-business-key.md)

*Enforced by:*

- 12: [`test_duplicates_are_handled_once`](../../12_integration/tests/test_delivery.py), [`test_one_outage_from_two_sources_is_triaged_once`](../../12_integration/tests/test_triage.py), [`test_keyed_on_events_two_sources_double_the_work_for_people_and_jira`](../../12_integration/tests/test_integration_check.py)

### INT-03 · Writes to external systems are idempotent

A write to a system of record carries a key that lets a retry find its own earlier write, so a lost response never becomes a second write.

*Why:* Retries are certain; duplicate writes are not acceptable.

*Applies to:* 08, 12 · *ADRs:* [0012](adr/0012-service-identity-and-idempotent-writes.md)

*Enforced by:*

- 08: [`test_a_lost_response_and_a_retry_still_post_once`](../../08_tools/tests/test_approval.py)
- 12: [`test_without_the_marker_one_lost_response_posts_twice`](../../12_integration/tests/test_integration_check.py)

### INT-04 · Retry what can recover; dead-letter what cannot

Invalid messages and policy halts go to the dead-letter queue at once with their reason; other failures retry with exponential backoff, then dead-letter. One bad message never blocks the rest, and a dead letter can be replayed once.

*Why:* Retrying a message that cannot succeed only delays the ones behind it.

*Applies to:* 12 · *ADRs:* [0015](adr/0015-in-process-stand-ins.md)

*Enforced by:*

- 12: [`test_a_message_that_keeps_failing_is_dead_lettered_and_the_rest_still_flow`](../../12_integration/tests/test_delivery.py), [`test_an_invalid_event_is_dead_lettered_at_once`](../../12_integration/tests/test_delivery.py), [`test_a_dead_letter_is_replayed_once_its_cause_is_fixed`](../../12_integration/tests/test_delivery.py)

### INT-05 · Batch files are checked against their control totals

A legacy batch file is accepted only when its header and trailer agree with its records; a bad record is rejected alone with its raw text and reason.

*Why:* A truncated night looks like a quiet night.

*Applies to:* 12 · *ADRs:* none

*Enforced by:*

- 12: [`test_a_truncated_file_is_rejected_whole`](../../12_integration/tests/test_adapters.py), [`test_a_bad_record_is_rejected_alone_and_the_rest_go_through`](../../12_integration/tests/test_adapters.py)

### INT-06 · Inbound webhooks are verified before they are read

A webhook's signature over the raw body is verified in constant time before the body is parsed.

*Why:* Parsing an unverified body is already trusting it.

*Applies to:* 12 · *ADRs:* none

*Enforced by:*

- 12: [`test_an_unsigned_or_forged_webhook_is_refused`](../../12_integration/tests/test_adapters.py), [`test_a_body_changed_after_signing_is_refused`](../../12_integration/tests/test_adapters.py)

## Exception register

| ID | Standard | Modules | Gap | Compensating control | Owner | Expires |
|---|---|---|---|---|---|---|
| EXC-001 | APP-05 | 07 | 07_rag/rag/graph.py builds its OpenAI and Anthropic models itself instead of calling llm_provider.get_chat_model. | Both paths read OPENAI_MODEL and default to gpt-6-luna with the same reasoning-effort rule; 07's runs record the model they called. | @LNLugo | 2026-12-31 |
| EXC-002 | API-01 | 08 | 08's MCP server is reached directly, not through the gateway. | The MCP server enforces identity from the bearer token and per-tool scopes itself, and makes no model calls, so no budget is at stake. | @LNLugo | 2027-03-31 |
| EXC-003 | OPS-03 | 06, 07 | 06's agents and 07's answers carry no caller identity, so their model calls are not held to a per-identity budget. | Both are costed by workflow in the cost report; neither is exposed to users except through the explorer app. | @LNLugo | 2027-03-31 |
| EXC-004 | AI-07 | 07 | 07's answer evaluation ran once, on one model (gpt-4o-mini), with no non-model baseline for answers. | Scored against lawyer annotations on CUAD's test contracts, never seen when the prompt was written; the README states the plus or minus 7 point uncertainty and attributes every failure to a cause. | @LNLugo | 2027-04-16 |
