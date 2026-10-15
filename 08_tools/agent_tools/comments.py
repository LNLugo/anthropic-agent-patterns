"""comment_on_issue: the module's one write action, and the reason it needs human approval.

Writes are kept apart from reads. The count tool (jql_tool.BoundedJiraTool) and
the mock Jira stay read only; comments go through CommentTool into its own
store, which stands in for Jira's comment API.

    check()  validates the arguments and that the requester can see the issue;
             runs before anyone is asked to approve, so nobody approves an impossible write
    post()   writes the comment as the requester, recording who approved it;
             checks visibility again, because access can change while approval waits
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .jira_mock import Caller, MockJira
from .jql_tool import ALLOWED_PROJECTS, ToolRejected

KEY = re.compile(r"^([A-Z][A-Z0-9]+)-(\d+)$")
MAX_BODY = 2000


@dataclass(frozen=True)
class Comment:
    id: int
    issue_key: str
    author: str          # the requester: the comment is posted on their behalf
    approved_by: str     # the person who approved it
    body: str
    created: str


@dataclass
class CommentStore:
    """Stands in for Jira's comment API. The only place in the module that changes data."""

    comments: list[Comment] = field(default_factory=list)

    def add(self, issue_key: str, author: str, approved_by: str, body: str) -> Comment:
        c = Comment(len(self.comments) + 1, issue_key, author, approved_by, body,
                    datetime.now(timezone.utc).isoformat(timespec="seconds"))
        self.comments.append(c)
        return c


class CommentTool:
    name = "comment_on_issue"

    def __init__(self, client: MockJira, store: CommentStore, allowed_projects: frozenset[str] = ALLOWED_PROJECTS):
        self.client, self.store, self.allowed_projects = client, store, frozenset(allowed_projects)

    @property
    def schema(self) -> dict:
        return {
            "name": self.name,
            "description": (
                "Add a comment to one Jira issue, on the user's behalf. A person must approve "
                "every comment before it is posted. Use only when the user asks to add or post something to an issue."
            ),
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["issue_key", "body"],
                "properties": {
                    "issue_key": {"type": "string", "description": "for example BILL-107"},
                    "body": {"type": "string", "description": f"the comment text, at most {MAX_BODY} characters"},
                },
            },
        }

    def check(self, args: dict, caller: Caller) -> tuple[str, str]:
        """(issue_key, body) if this write could go ahead; raises ToolRejected otherwise."""
        unknown = set(args) - {"issue_key", "body"}
        if unknown:
            raise ToolRejected(f"unknown arguments: {', '.join(sorted(unknown))}")
        key, body = str(args.get("issue_key", "")).strip(), str(args.get("body", "")).strip()
        m = KEY.match(key)
        if not m:
            raise ToolRejected(f"{key!r} is not an issue key like BILL-107")
        if m.group(1) not in self.allowed_projects:
            raise ToolRejected(f"project {m.group(1)!r} is not available to this tool")
        if not body:
            raise ToolRejected("the comment is empty")
        if len(body) > MAX_BODY:
            raise ToolRejected(f"the comment is longer than {MAX_BODY} characters")
        if self.client.get_issue(caller, key) is None:
            raise ToolRejected(f"{key} is not an issue you can see")
        return key, body

    def post(self, issue_key: str, body: str, caller: Caller, approved_by: str) -> Comment:
        if self.client.get_issue(caller, issue_key) is None:
            raise ToolRejected(f"{issue_key} is no longer an issue you can see")
        return self.store.add(issue_key, caller.id, approved_by, body)
