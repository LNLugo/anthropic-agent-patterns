"""Human approval on the one write action: the run pauses, survives on disk, and resumes on a decision."""

import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.sqlite import SqliteSaver

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root: llm_provider
from llm_provider import scripted, scripted_model  # noqa: E402

from agent_tools.assistant import ask, build_assistant, checkpoint_serde, pending, resume  # noqa: E402
from agent_tools.comments import CommentStore, CommentTool  # noqa: E402
from agent_tools.guardrails import ToolInputGuardrailTripwireTriggered, finance_assistant_policy  # noqa: E402
from agent_tools.jira_mock import MockJira  # noqa: E402
from agent_tools.jql_tool import BoundedJiraTool  # noqa: E402
from agent_tools.model_router import ModelRouter  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "mini_jira.json"
TODAY = date(2026, 10, 12)
ASK = "Add 'Q3 P1 count: 4' to BILL-101 for the postmortem"


def comment_call(key="BILL-101", body="Q3 P1 count: 4"):
    return AIMessage(content="", tool_calls=[{"name": "comment_on_issue", "args": {"issue_key": key, "body": body}, "id": "c1"}])


@pytest.fixture
def env(tmp_path):
    jira, users = MockJira.from_fixture(FIXTURE)
    store = CommentStore()
    return {"jira": jira, "users": users, "store": store, "db": tmp_path / "checkpoints.db"}


def build(env, guardrails=()):
    """A fresh graph on a fresh connection to the same checkpoint file, as a restarted service would build."""
    conn = sqlite3.connect(env["db"], check_same_thread=False)
    tool, comments = BoundedJiraTool(env["jira"]), CommentTool(env["jira"], env["store"])
    router = ModelRouter(scripted_model(), tool, comment_tool=comments)
    app = build_assistant(tool, lambda q, c, k: [], router, guardrails=guardrails,
                          comment_tool=comments, checkpointer=SqliteSaver(conn, serde=checkpoint_serde()))
    return app, conn


def test_a_write_pauses_for_approval_and_writes_nothing(env):
    app, conn = build(env)
    with scripted([comment_call()]):
        out = ask(app, ASK, env["users"]["billing_oncall"], TODAY, thread_id="t1")
    assert "__interrupt__" in out and env["store"].comments == []
    assert pending(app, "t1") == {"action": "comment_on_issue", "issue_key": "BILL-101", "body": "Q3 P1 count: 4",
                                  "requested_by": "billing_oncall", "question": ASK}
    conn.close()


def test_resume_from_a_saved_checkpoint_after_a_restart(env):
    """The request is made, the service stops, and a new one with a new connection,
    graph and router answers the approval from the checkpoint file. The model is not
    called again: the empty script would raise on any call."""
    app, conn = build(env)
    with scripted([comment_call()]):
        ask(app, ASK, env["users"]["billing_oncall"], TODAY, thread_id="t1")
    conn.close()
    del app

    app2, conn2 = build(env)
    assert pending(app2, "t1")["issue_key"] == "BILL-101"
    with scripted([]):
        out = resume(app2, "t1", approved=True, approver="incident_lead")
    assert out["stop_reason"] == "posted" and pending(app2, "t1") is None
    [c] = env["store"].comments
    assert (c.issue_key, c.author, c.approved_by, c.body) == ("BILL-101", "billing_oncall", "incident_lead", "Q3 P1 count: 4")
    assert out["answer"] == "Posted to BILL-101 as billing_oncall, approved by incident_lead."
    conn2.close()


def test_a_finished_run_cannot_be_resumed_into_a_second_write(env):
    app, conn = build(env)
    with scripted([comment_call()]):
        ask(app, ASK, env["users"]["billing_oncall"], TODAY, thread_id="t1")
    resume(app, "t1", approved=True, approver="incident_lead")
    try:
        resume(app, "t1", approved=True, approver="incident_lead")
    except Exception:
        pass  # whether LangGraph refuses or ignores it, the store is what matters
    assert len(env["store"].comments) == 1
    conn.close()


def test_a_declined_write_is_not_posted(env):
    app, conn = build(env)
    with scripted([comment_call()]):
        ask(app, ASK, env["users"]["billing_oncall"], TODAY, thread_id="t1")
    out = resume(app, "t1", approved=False, approver="incident_lead", reason="wrong issue")
    assert out["stop_reason"] == "declined" and out["answer"] == "Not posted to BILL-101: wrong issue."
    assert env["store"].comments == []
    conn.close()


def test_the_requester_cannot_approve_their_own_write(env):
    app, conn = build(env)
    with scripted([comment_call()]):
        ask(app, ASK, env["users"]["billing_oncall"], TODAY, thread_id="t1")
    out = resume(app, "t1", approved=True, approver="billing_oncall")
    assert out["stop_reason"] == "declined" and "cannot approve their own write" in out["answer"]
    assert env["store"].comments == []
    conn.close()


def test_nobody_is_asked_to_approve_a_write_the_requester_could_not_make(env):
    """BILL-107 carries a security level new_hire does not hold: no approval request,
    and the answer does not say whether the issue exists."""
    app, conn = build(env)
    with scripted([comment_call(key="BILL-107")]):
        out = ask(app, "Add a note to BILL-107", env["users"]["new_hire"], TODAY, thread_id="t1")
    assert "__interrupt__" not in out and pending(app, "t1") is None
    assert out["answer"] == "Not posted: BILL-107 is not an issue you can see."
    conn.close()


def test_access_is_checked_again_when_the_approval_arrives(env):
    app, conn = build(env)
    with scripted([comment_call()]):
        ask(app, ASK, env["users"]["billing_oncall"], TODAY, thread_id="t1")
    env["jira"].get_issue = lambda caller, key: None  # access revoked while the approval waited
    out = resume(app, "t1", approved=True, approver="incident_lead")
    assert out["answer"] == "Not posted: BILL-101 is no longer an issue you can see." and env["store"].comments == []
    conn.close()


def test_guardrails_run_before_anyone_is_asked(env):
    app, conn = build(env, finance_assistant_policy())
    with scripted([comment_call(key="INFRA-301")]):
        out = ask(app, "Note on INFRA-301", env["users"]["new_hire"], TODAY, thread_id="t1")
    assert pending(app, "t1") is None and out["stop_reason"] == "guardrail"
    assert out["answer"].startswith("Not run: INFRA is outside this assistant's scope")
    with pytest.raises(ToolInputGuardrailTripwireTriggered), scripted([comment_call(key="SECOPS-401")]):
        ask(app, "Note on SECOPS-401", env["users"]["sec_engineer"], TODAY, thread_id="t2")
    assert env["store"].comments == []
    conn.close()


def test_reads_do_not_pause(env):
    app, conn = build(env)
    count = AIMessage(content="", tool_calls=[{"name": "count_jira_issues", "id": "c1", "args": {
        "project": "BILL", "project_evidence": "BILL", "created_from": "2026-07-01", "created_before": "2026-10-01"}}])
    with scripted([count]):
        out = ask(app, "How many BILL issues in Q3?", env["users"]["new_hire"], TODAY, thread_id="t1")
    assert "__interrupt__" not in out and out["result"].count > 0
    conn.close()


def test_the_write_path_requires_a_checkpointer(env):
    tool = BoundedJiraTool(env["jira"])
    with pytest.raises(ValueError, match="checkpointer"):
        build_assistant(tool, lambda q, c, k: [], ModelRouter(scripted_model(), tool),
                        comment_tool=CommentTool(env["jira"], env["store"]))


def test_the_count_tool_and_the_mock_stay_read_only():
    assert not hasattr(MockJira, "add_comment") and not hasattr(BoundedJiraTool, "post")


def test_a_saved_approval_loads_under_strict_deserialization(tmp_path):
    """LangGraph is moving to blocking unregistered types when a checkpoint is loaded.
    Run the full restart and resume with that already switched on (it is read once, at
    start-up, hence the subprocess): the comment must post, with nothing blocked. The
    demo traces its runs, to a temporary folder rather than the working tree."""
    import os
    import subprocess

    here = Path(__file__).resolve().parents[1]
    env = {k: v for k, v in os.environ.items() if not k.endswith("_API_KEY")}
    run = subprocess.run([sys.executable, str(here / "demo_approval.py")], cwd=here, capture_output=True,
                         text=True, timeout=120, env={**env, "LANGGRAPH_STRICT_MSGPACK": "true",
                                                         "TRACE_EXPORT_DIR": str(tmp_path / "traces")})
    out = run.stdout + run.stderr
    assert run.returncode == 0, out
    assert "Posted to BILL-101 as billing_oncall, approved by incident_lead." in out
    assert "Blocked" not in out and "unregistered" not in out
    [trace_file] = (tmp_path / "traces").glob("traces-*.jsonl")
    ended = [r for r in map(json.loads, trace_file.read_text().splitlines()) if r["type"] == "trace" and r["ended_at"]]
    asks = [t["group_id"] for t in ended if t["workflow"] == "assistant"]
    resumes = [t["group_id"] for t in ended if t["workflow"] == "assistant.resume"]
    assert asks and asks == resumes and all(t["status"] == "ok" for t in ended)  # each request, then its decision
