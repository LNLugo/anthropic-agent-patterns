"""The tools over MCP: the same call, two tokens, two answers. Runs a local server and a client.

    python demo_mcp.py          # offline: no model; the client calls the tools directly

1. count_jira_issues as billing_oncall and as new_hire: different counts, because
   Jira applies the token holder's permissions.
2. new_hire passes user="billing_oncall": dropped; still new_hire's view.
3. A bad token: HTTP 401, before any tool runs.
4. A guardrail over MCP: INFRA is outside the deployment's scope.
5. comment_on_issue: pending approval; a person approves it outside MCP.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import tempfile
import threading
import time
from datetime import date
from pathlib import Path

import httpx2
import uvicorn
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from agent_tools.comments import CommentStore
from agent_tools.guardrails import deployment_scope, keys_match_project, restricted_project
from agent_tools.jira_mock import MockJira
from agent_tools.mcp_server import READ, WRITE, ApprovalDesk, IssuedToken, StaticTokenVerifier, build_mcp_server

HERE = Path(__file__).resolve().parent
TODAY = date(2026, 10, 12)
TOKENS = {"token-oncall": IssuedToken("billing_oncall", (READ, WRITE)), "token-hire": IssuedToken("new_hire", (READ,))}
Q3 = {"project": "BILL", "created_from": "2026-07-01", "created_before": "2026-10-01",
      "issue_type": "Incident", "priority": "P1"}


async def call(url, token, name, args):
    async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}) as http:
        async with Client(streamable_http_client(url, http_client=http)) as client:
            r = await client.call_tool(name, args)
            return r.content[0].text if r.is_error else r.structured_content


def main() -> int:
    logging.disable(logging.INFO)  # the SDK logs every HTTP request and refusal; the demo prints its own
    jira, users = MockJira.from_fixture(HERE / "tests" / "fixtures" / "mini_jira.json")
    with tempfile.TemporaryDirectory() as tmp:
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        url = f"http://127.0.0.1:{port}/mcp"
        desk = ApprovalDesk(Path(tmp) / "approvals.db", jira, CommentStore())
        policy = [restricted_project(), deployment_scope(frozenset({"BILL", "PAY"})), keys_match_project()]
        server, audit = build_mcp_server(jira, users, StaticTokenVerifier(TOKENS, url), url, desk,
                                         guardrails=policy, today=lambda: TODAY)
        srv = uvicorn.Server(uvicorn.Config(server.streamable_http_app(), host="127.0.0.1", port=port, log_level="error"))
        threading.Thread(target=srv.run, daemon=True).start()
        while not srv.started:
            time.sleep(0.05)
        print(f"MCP server at {url}\n")

        for token in ("token-oncall", "token-hire"):
            r = asyncio.run(call(url, token, "count_jira_issues", Q3))
            print(f"1. {token}: {r['count']} BILL P1 incidents in Q3, visible to {r['visible_to']}")
        r = asyncio.run(call(url, "token-hire", "count_jira_issues", {**Q3, "user": "billing_oncall"}))
        print(f"2. token-hire with user='billing_oncall': {r['count']}, visible to {r['visible_to']}")
        status = httpx2.post(url, headers={"Authorization": "Bearer stolen", "Accept": "application/json, text/event-stream"},
                             json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                                 "protocolVersion": "2025-06-18", "capabilities": {},
                                 "clientInfo": {"name": "demo", "version": "1"}}}).status_code
        print(f"3. a bad token: HTTP {status}")
        print(f"4. token-hire counting INFRA: {asyncio.run(call(url, 'token-hire', 'count_jira_issues', {**Q3, 'project': 'INFRA'}))}")
        r = asyncio.run(call(url, "token-oncall", "comment_on_issue", {"issue_key": "BILL-101", "body": "Q3 P1 count: 4"}))
        print(f"5. comment_on_issue: {r['status']} ({r['request_id']}); posted so far: {len(desk.store.comments)}")
        print(f"   approval desk, outside MCP: {desk.decide(r['request_id'], approved=True, approver='incident_lead')}")

        print("\nAudit:")
        for e in audit:
            print(f"   {e.subject:15} {e.tool:18} {e.outcome}")
        srv.should_exit = True
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
