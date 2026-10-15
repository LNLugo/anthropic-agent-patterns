"""Serve the tools over MCP on localhost, for an MCP client (an agent, or the MCP Inspector) to connect to.

    python serve_mcp.py              # http://127.0.0.1:8765/mcp
    python serve_mcp.py --port 9000

Connect with a bearer token from the table printed at start-up; each one is a
user in the synthetic Jira, and the tools answer as that user. The tokens are
demo stand-ins for on-behalf-of tokens; do not expose this server beyond
localhost. Pending comments are listed and decided with --approvals.

    python serve_mcp.py --approvals list
    python serve_mcp.py --approvals approve REQUEST_ID --approver incident_lead

Pending approvals live in the checkpoint file, so --approvals finds them from a
separate process. The mock comment store does not: it lives in each process's
memory, so a comment approved from the command line is posted to that
process's copy. In a deployment both sides write to Jira.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

from agent_tools.comments import CommentStore
from agent_tools.guardrails import deployment_scope, keys_match_project, restricted_project
from agent_tools.jira_mock import MockJira
from agent_tools.mcp_server import READ, WRITE, ApprovalDesk, IssuedToken, StaticTokenVerifier, build_mcp_server

HERE = Path(__file__).resolve().parent
DB = HERE / "results" / "mcp_approvals.db"
TOKENS = {
    "demo-oncall": IssuedToken("billing_oncall", (READ, WRITE)),
    "demo-newhire": IssuedToken("new_hire", (READ,)),
    "demo-contractor": IssuedToken("contractor", (READ,)),
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--approvals", nargs="*", metavar="ARG", help="list | approve ID | decline ID")
    ap.add_argument("--approver", default="incident_lead")
    args = ap.parse_args()

    jira, users = MockJira.from_fixture(HERE / "tests" / "fixtures" / "mini_jira.json")
    DB.parent.mkdir(exist_ok=True)
    desk = ApprovalDesk(DB, jira, CommentStore())

    if args.approvals is not None:
        verb, *rest = args.approvals or ["list"]
        if verb == "list":
            import sqlite3
            ids = [r[0] for r in sqlite3.connect(DB).execute("select distinct thread_id from checkpoints")] if DB.exists() else []
            waiting = [(i, desk.pending(i)) for i in ids]
            for i, p in waiting:
                if p:
                    print(f"{i}: {p['requested_by']} asks to comment on {p['issue_key']}: {p['body']!r}")
            if not any(p for _, p in waiting):
                print("No pending approvals.")
            return 0
        print(desk.decide(rest[0], approved=(verb == "approve"), approver=args.approver))
        return 0

    url = f"http://127.0.0.1:{args.port}/mcp"
    server, _ = build_mcp_server(jira, users, StaticTokenVerifier(TOKENS, url), url, desk,
                                 guardrails=[restricted_project(), deployment_scope(frozenset({"BILL", "PAY"})),
                                             keys_match_project()])
    print(f"MCP server at {url}\nBearer tokens (demo only):")
    for token, t in TOKENS.items():
        print(f"  {token:16} {t.subject:15} {', '.join(t.scopes)}")
    uvicorn.run(server.streamable_http_app(), host="127.0.0.1", port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
