"""The module's tools over MCP, where the caller's identity comes from the transport.

    MCP client (any agent) --HTTP, Authorization: Bearer <token>--> token verifier --> subject
                                                                                        |
            count_jira_issues / search_wiki / comment_on_issue  <-- caller = directory[subject]
                                     |
             the assistant graph: guardrails --> bound --> Jira, as the caller --> guardrails

No tool takes a user, caller or group argument. Who is asking comes only from
the verified bearer token, so an agent cannot ask for someone else's view of
Jira by passing a different name. Each tool also needs a scope: jira:read for
counts and wiki search, jira:write as well for comments.

Every call runs through the same graph as the assistant (assistant.py), with a
PresetRouter that passes the client's chosen tool and arguments in. The client
has already done the routing; nothing is reimplemented for MCP, so guardrails,
the bound and Jira's permissions apply exactly as they do in the assistant.

Comments do not post. comment_on_issue returns a pending approval request; a
person decides it through ApprovalDesk, outside MCP. There is deliberately no
approve tool, so no agent can approve its own write.

Across MCP, a guardrail's raise_exception refuses the call and is recorded as
a halt; stopping the client's own agent run is the client's job, since the
server cannot reach into it.

Run with mcp 2.x (MCPServer, formerly FastMCP). In production the verifier
would validate an on-behalf-of token exchanged from the user's sign-in (OAuth
2.0 token exchange, RFC 8693) and Jira would be called with it; here a static
token table stands in.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

from langgraph.checkpoint.sqlite import SqliteSaver
from pydantic import BaseModel
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from .assistant import KnowledgeSearch, ask, build_assistant, checkpoint_serde, pending, resume
from .comments import CommentStore, CommentTool
from .guardrails import GuardrailRecord, ToolGuardrail, ToolGuardrailTripwireTriggered
from .jira_mock import Caller, MockJira
from .jql_tool import BoundedJiraTool, ToolRejected
from .model_router import Decision
from .observe import get_tracer

READ, WRITE = "jira:read", "jira:write"


class CountResult(BaseModel):
    count: int
    keys: list[str]
    truncated: bool
    groups: dict[str, int] | None
    jql: str
    visible_to: str          # whose permissions Jira applied: the token's subject


class WikiPage(BaseModel):
    id: str
    title: str


class WikiResult(BaseModel):
    pages: list[WikiPage]
    visible_to: str


class CommentRequest(BaseModel):
    status: str
    request_id: str
    issue_key: str
    message: str


# ---------------------------------------------------------------- identity

@dataclass(frozen=True)
class IssuedToken:
    subject: str
    scopes: tuple[str, ...]
    expires_at: int | None = None


class StaticTokenVerifier:
    """Maps bearer tokens to subjects and scopes. Stands in for validating a signed
    on-behalf-of token; an unknown or expired token is refused before any tool runs."""

    def __init__(self, tokens: dict[str, IssuedToken], resource: str):
        self.tokens, self.resource = tokens, resource

    async def verify_token(self, token: str) -> AccessToken | None:
        t = self.tokens.get(token)
        if t is None or (t.expires_at is not None and t.expires_at < time.time()):
            return None
        return AccessToken(token=token, client_id="mcp-client", scopes=list(t.scopes),
                           subject=t.subject, expires_at=t.expires_at, resource=self.resource)


class PresetRouter:
    """The MCP client already chose the tool and its arguments; pass that choice into the graph."""

    def __init__(self, decision: Decision):
        self.decision = decision

    def decide(self, question: str, today: date, context: list[dict] | None = None) -> Decision:
        return self.decision


@dataclass
class AuditEntry:
    tool: str
    subject: str
    arguments: dict
    outcome: str                                   # ok, refused, halted, pending_approval
    detail: str = ""
    guardrails: list[GuardrailRecord] = field(default_factory=list)
    trace_id: str | None = None                    # the full record of the call, in the trace file


class ToolRefused(ToolError):
    """A deliberate refusal: the call did not run, and the client is told why. (The SDK keeps the
    text of any other exception on the server, so an unexpected crash leaks nothing.)"""


# ---------------------------------------------------------------- approvals, outside MCP

class ApprovalDesk:
    """Where a person sees and decides pending writes. Not exposed over MCP."""

    def __init__(self, db: Path, jira: MockJira, store: CommentStore):
        self.db, self.jira, self.store = Path(db), jira, store

    def graph(self, decision: Decision | None = None, guardrails: list[ToolGuardrail] = ()):
        conn = sqlite3.connect(self.db, check_same_thread=False)
        tool, comments = BoundedJiraTool(self.jira), CommentTool(self.jira, self.store)
        app = build_assistant(tool, lambda q, c, k: [], PresetRouter(decision or Decision("none")),
                              guardrails=guardrails, comment_tool=comments,
                              checkpointer=SqliteSaver(conn, serde=checkpoint_serde()))
        return app, conn

    def pending(self, request_id: str) -> dict | None:
        app, conn = self.graph()
        try:
            return pending(app, request_id)
        finally:
            conn.close()

    def decide(self, request_id: str, approved: bool, approver: str, reason: str = "") -> str:
        app, conn = self.graph()
        try:
            if pending(app, request_id) is None:
                raise KeyError(f"no pending request {request_id!r}")
            return resume(app, request_id, approved=approved, approver=approver, reason=reason)["answer"]
        finally:
            conn.close()


# ---------------------------------------------------------------- the server

def build_mcp_server(jira: MockJira, users: dict[str, Caller], verifier: StaticTokenVerifier, url: str,
                     desk: ApprovalDesk, knowledge: KnowledgeSearch | None = None,
                     guardrails: list[ToolGuardrail] = (),
                     today: Callable[[], date] = date.today) -> tuple[MCPServer, list[AuditEntry]]:
    audit: list[AuditEntry] = []
    tool = BoundedJiraTool(jira)
    server = MCPServer(
        "jira-tools",
        instructions="Counts and comments on Jira issues, as the signed-in user. Comments need a person's approval.",
        token_verifier=verifier,
        auth=AuthSettings(issuer_url="http://127.0.0.1/issuer", resource_server_url=url,
                          validate_token_resource=True, required_scopes=[READ]),
    )

    def caller(scope: str) -> Caller:
        """Who is asking, from the verified token. The scope check is recorded as a guardrail
        verdict, so a refused call is in the trace file like any other."""
        token = get_access_token()
        subject = token.subject if token is not None else None
        held = list(token.scopes) if token is not None else []
        ok = subject in users and scope in held
        get_tracer().event("guardrail", "token_scope", stage="input", subject=subject, required=scope, held=held,
                     behavior="allow" if ok else "raise_exception", triggered=not ok)
        if token is None or token.subject not in users:
            raise ToolRefused("no signed-in user")
        if scope not in token.scopes:
            raise ToolRefused(f"this token does not carry the {scope} scope")
        return users[token.subject]

    def traced(name: str):
        """One trace per MCP call. The assistant run inside joins it, so the token check,
        the guardrails and the tool call are one record."""
        return get_tracer().trace(f"mcp.{name}", metadata={"transport": "mcp"})

    def through_graph(name: str, args: dict, who: Caller, decision: Decision,
                      thread_id: str | None = None, **graph_kw) -> dict:
        app = build_assistant(tool, knowledge or (lambda q, c, k: []), PresetRouter(decision),
                              guardrails=guardrails, **graph_kw)
        current = get_tracer().current_trace()
        entry = AuditEntry(name, who.id, args, "ok", trace_id=current.trace_id if current else None)
        audit.append(entry)
        try:
            out = ask(app, f"MCP {name} call", who, today(), thread_id=thread_id)
        except ToolGuardrailTripwireTriggered as e:
            entry.outcome, entry.detail, entry.guardrails = "halted", str(e.output.output_info), e.records
            raise ToolRefused(f"halted by guardrail {e.guardrail.name}")
        entry.guardrails = out.get("guardrail_records", [])
        if out.get("stop_reason") in ("guardrail", "rejected", "none", "clarify"):
            entry.outcome, entry.detail = "refused", out["answer"]
            raise ToolRefused(out["answer"])
        return out

    @server.tool(structured_output=True)
    def count_jira_issues(project: str, created_from: str, created_before: str, issue_type: str | None = None,
                          priority: str | None = None, status: str | None = None, group_by: str | None = None) -> CountResult:
        """Count Jira issues the signed-in user can see, by project, type, priority, status and creation
        date (created_before is exclusive). Read only. Returns the count, up to 25 keys and the JQL run."""
        with traced("count_jira_issues"):
            who = caller(READ)
            args = {k: v for k, v in {"project": project, "created_from": created_from, "created_before": created_before,
                                      "issue_type": issue_type, "priority": priority, "status": status,
                                      "group_by": group_by}.items() if v is not None}
            try:
                spec = tool.spec_from_arguments(args)
                decision = Decision("aggregation", spec, arguments=args)
            except ToolRejected as e:
                decision = Decision("aggregation", None, str(e), tool.name, args)
            out = through_graph("count_jira_issues", args, who, decision)
        r = out["result"]
        return CountResult(count=r.count, keys=list(r.keys), truncated=r.truncated, groups=r.groups,
                           jql=r.jql, visible_to=who.id)

    @server.tool(structured_output=True)
    def search_wiki(query: str) -> WikiResult:
        """Search the engineering wiki for pages the signed-in user can open."""
        with traced("search_wiki"):
            who = caller(READ)
            if knowledge is None:
                raise ToolRefused("no wiki is connected to this server")
            out = through_graph("search_wiki", {"query": query}, who, Decision("knowledge"))
        return WikiResult(pages=[WikiPage(id=pid, title=title) for pid, title in out.get("pages", [])], visible_to=who.id)

    @server.tool(structured_output=True)
    def comment_on_issue(issue_key: str, body: str) -> CommentRequest:
        """Ask to add a comment to one issue, on the signed-in user's behalf. Nothing is posted until a
        person approves it; returns a request id to report back to the user."""
        with traced("comment_on_issue"):
            who = caller(WRITE)
            request_id = f"req-{uuid.uuid4().hex[:12]}"
            args = {"issue_key": issue_key, "body": body}
            app_conn = sqlite3.connect(desk.db, check_same_thread=False)
            try:
                out = through_graph("comment_on_issue", args, who, Decision("comment", arguments=args),
                                    comment_tool=CommentTool(jira, desk.store),
                                    checkpointer=SqliteSaver(app_conn, serde=checkpoint_serde()), thread_id=request_id)
            finally:
                app_conn.close()
            if "__interrupt__" not in out:
                raise ToolRefused(out.get("answer", "not posted"))
        audit[-1].outcome, audit[-1].detail = "pending_approval", request_id
        return CommentRequest(status="pending_approval", request_id=request_id, issue_key=issue_key,
                              message="A person must approve this comment before it is posted.")

    return server, audit
