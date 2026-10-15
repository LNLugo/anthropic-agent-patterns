"""The tools over MCP: identity from the bearer token, never from tool arguments.

These run a real HTTP server on localhost and a real MCP client, so the bearer
token, scopes and 401s are exercised as a deployed client would meet them."""

import asyncio
import socket
import threading
import time
from datetime import date
from pathlib import Path

import httpx2
import pytest
import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from agent_tools.comments import CommentStore
from agent_tools.guardrails import deployment_scope, keys_match_project, restricted_project
from agent_tools.jira_mock import MockJira
from agent_tools.mcp_server import READ, WRITE, ApprovalDesk, IssuedToken, StaticTokenVerifier, build_mcp_server

FIXTURE = Path(__file__).parent / "fixtures" / "mini_jira.json"
TODAY = date(2026, 10, 12)
Q3 = {"project": "BILL", "created_from": "2026-07-01", "created_before": "2026-10-01",
      "issue_type": "Incident", "priority": "P1"}
TOKENS = {
    "t-oncall": IssuedToken("billing_oncall", (READ, WRITE)),
    "t-hire": IssuedToken("new_hire", (READ,)),
    "t-sec": IssuedToken("sec_engineer", (READ, WRITE)),
    "t-expired": IssuedToken("billing_oncall", (READ,), expires_at=int(time.time()) - 60),
    "t-nobody": IssuedToken("someone_not_in_the_directory", (READ,)),
}
POLICY = [restricted_project(), deployment_scope(frozenset({"BILL", "PAY"})), keys_match_project()]


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class Wiki:
    def __call__(self, question, caller, k):
        pages = [("ENG-3", "Billing API Credential Rotation")]
        if "billing-oncall" in caller.groups:
            pages.append(("ENG-9", "Billing On-call Escalation (restricted)"))
        return pages[:k]


@pytest.fixture(scope="module")
def mcp(tmp_path_factory):
    jira, users = MockJira.from_fixture(FIXTURE)
    port = free_port()
    url = f"http://127.0.0.1:{port}/mcp"
    desk = ApprovalDesk(tmp_path_factory.mktemp("mcp") / "approvals.db", jira, CommentStore())
    server, audit = build_mcp_server(jira, users, StaticTokenVerifier(TOKENS, url), url, desk,
                                     knowledge=Wiki(), guardrails=POLICY, today=lambda: TODAY)
    srv = uvicorn.Server(uvicorn.Config(server.streamable_http_app(), host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not srv.started and time.time() < deadline:
        time.sleep(0.05)
    yield {"url": url, "audit": audit, "desk": desk}
    srv.should_exit = True
    thread.join(5)


def session(mcp, token, fn):
    async def run():
        async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}) as http:
            async with Client(streamable_http_client(mcp["url"], http_client=http)) as client:
                return await fn(client)
    return asyncio.run(run())


def call(mcp, token, name, args):
    async def fn(client):
        r = await client.call_tool(name, args)
        return r.is_error, (r.structured_content if not r.is_error else r.content[0].text)
    return session(mcp, token, fn)


# ---------------------------------------------------------------- identity

def test_the_same_call_returns_what_each_token_holder_can_see(mcp):
    ok1, oncall = call(mcp, "t-oncall", "count_jira_issues", Q3)
    ok2, hire = call(mcp, "t-hire", "count_jira_issues", Q3)
    assert not ok1 and not ok2
    assert (oncall["count"], oncall["visible_to"]) == (4, "billing_oncall")
    assert (hire["count"], hire["visible_to"]) == (3, "new_hire")  # BILL-107 is hidden from new_hire


def test_no_tool_takes_a_user_argument(mcp):
    tools = session(mcp, "t-hire", lambda c: c.list_tools())
    names = {t.name for t in tools.tools}
    assert names == {"count_jira_issues", "search_wiki", "comment_on_issue"}
    for t in tools.tools:
        props = set(t.input_schema.get("properties", {}))
        assert not props & {"user", "caller", "user_id", "groups", "as_user"}, (t.name, props)


def test_naming_another_user_in_the_arguments_changes_nothing(mcp):
    """An agent cannot borrow someone else's view by passing their name: the SDK drops
    the unknown argument, and Jira still applies the token holder's permissions."""
    is_error, out = call(mcp, "t-hire", "count_jira_issues", {**Q3, "user": "billing_oncall"})
    assert not is_error and (out["visible_to"], out["count"]) == ("new_hire", 3)


def test_a_bad_or_expired_token_is_refused_before_any_tool_runs(mcp):
    """Refused at the HTTP layer with 401: no MCP session is created, so no tool can run."""
    initialize = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}}
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    for token, status in (("not-a-token", 401), ("t-expired", 401), ("t-hire", 200)):
        r = httpx2.post(mcp["url"], json=initialize, headers={**headers, "Authorization": f"Bearer {token}"})
        assert r.status_code == status, token
        if status == 401:
            assert 'error="invalid_token"' in r.headers["www-authenticate"]


def test_a_valid_token_for_someone_outside_the_directory_gets_nothing(mcp):
    is_error, text = call(mcp, "t-nobody", "count_jira_issues", Q3)
    assert is_error and "no signed-in user" in text


def test_wiki_results_follow_the_token_too(mcp):
    _, oncall = call(mcp, "t-oncall", "search_wiki", {"query": "billing on-call"})
    _, hire = call(mcp, "t-hire", "search_wiki", {"query": "billing on-call"})
    assert len(oncall["pages"]) == 2 and [p["id"] for p in hire["pages"]] == ["ENG-3"]


# ---------------------------------------------------------------- the same controls as the assistant

def test_the_tool_bound_applies_over_mcp(mcp):
    is_error, text = call(mcp, "t-hire", "count_jira_issues", {**Q3, "created_from": "2024-01-01"})
    assert is_error and "capped at 366 days" in text


def test_guardrails_apply_over_mcp(mcp):
    is_error, text = call(mcp, "t-hire", "count_jira_issues", {**Q3, "project": "INFRA"})
    assert is_error and "outside this assistant's scope" in text
    is_error, text = call(mcp, "t-sec", "count_jira_issues", {**Q3, "project": "SECOPS"})
    assert is_error and "halted by guardrail restricted_project" in text
    halted = [e for e in mcp["audit"] if e.outcome == "halted"]
    assert halted and halted[-1].subject == "sec_engineer" and "SECOPS" in halted[-1].detail


# ---------------------------------------------------------------- writes

def test_a_comment_needs_the_write_scope(mcp):
    is_error, text = call(mcp, "t-hire", "comment_on_issue", {"issue_key": "BILL-101", "body": "note"})
    assert is_error and "jira:write" in text


def test_a_comment_over_mcp_waits_for_a_person_and_there_is_no_approve_tool(mcp):
    is_error, out = call(mcp, "t-oncall", "comment_on_issue", {"issue_key": "BILL-101", "body": "Q3 P1 count: 4"})
    assert not is_error and out["status"] == "pending_approval"
    rid, desk = out["request_id"], mcp["desk"]
    assert desk.store.comments == []
    assert desk.pending(rid)["requested_by"] == "billing_oncall"
    tools = session(mcp, "t-oncall", lambda c: c.list_tools())
    assert not any("approv" in t.name for t in tools.tools)
    answer = desk.decide(rid, approved=True, approver="incident_lead")
    assert answer == "Posted to BILL-101 as billing_oncall, approved by incident_lead."
    [c] = desk.store.comments
    assert (c.author, c.approved_by) == ("billing_oncall", "incident_lead")


def test_a_comment_on_an_issue_the_token_holder_cannot_see_is_refused(mcp):
    is_error, text = call(mcp, "t-sec", "comment_on_issue", {"issue_key": "BILL-107", "body": "note"})
    assert is_error and "BILL-107 is not an issue you can see" in text


def test_every_call_is_audited_with_the_token_subject(mcp):
    assert mcp["audit"] and all(e.subject in {"billing_oncall", "new_hire", "sec_engineer"} for e in mcp["audit"])
