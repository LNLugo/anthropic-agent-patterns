"""The leak check as a test: with sensitive data off, no planted marker from any content
source reaches the trace file; with it on, every marker does (the control)."""

import run_leak_check as leak


def test_no_content_reaches_the_trace_file_and_the_control_sees_all_of_it(tmp_path):
    off = leak.summarize(leak.run_all(tmp_path / "off", include_sensitive=False))
    on = leak.summarize(leak.run_all(tmp_path / "on", include_sensitive=True))
    assert off["marker_hits"] == {source: 0 for source in leak.MARKERS}
    assert all(n > 0 for n in on["marker_hits"].values()), on["marker_hits"]
    assert off["spans_by_kind"] == on["spans_by_kind"]  # the setting changes content, never structure
    assert set(off["spans_by_kind"]) == {"agent", "custom", "generation", "guardrail", "handoff", "tool"}


def test_the_audit_questions_are_answered_from_the_file_alone(tmp_path):
    import audit_report

    leak.run_all(tmp_path, include_sensitive=False)
    r = audit_report.report(*audit_report.load(tmp_path))
    [halt] = r["halts"]
    assert (halt["identity"], halt["guardrail"]) == ("sec_engineer", "restricted_project")
    assert halt["found"]["project"] == "SECOPS"
    [declined] = r["declined_writes"]
    assert (declined["requested_by"], declined["approver"], declined["issue"]) == ("billing_oncall", "incident_lead", "BILL-101")
    oncall = r["identities"]["billing_oncall"]
    assert oncall["tool count_jira_issues: ok"] == 2 and oncall["tool count_jira_issues: rejected"] == 1
    assert oncall["guardrail reject_content"] == 3  # out of scope, evidence not in the question, the bound
    assert oncall["writes requested"] == 2 and oncall["tool comment_on_issue: posted"] == 1  # posted as the requester
    assert r["identities"]["incident_lead"] == {"runs": 2, "runs ok": 2, "writes approved (as approver)": 1,
                                                "writes declined (as approver)": 1}
