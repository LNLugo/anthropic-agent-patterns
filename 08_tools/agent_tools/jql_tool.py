"""A bounded, read only Jira query tool.

The model never writes JQL. It fills a typed query (QuerySpec) whose every
field is an enum or a date, the tool validates it against a fixed allowlist,
compiles it to JQL itself, and runs it as the user. That closes three doors at
once:

  injection     there is no free text field to smuggle "OR project = X" into
  scope creep   only allowlisted projects can be queried, whatever the user's
                own permissions are
  cost          the date range is capped and at most MAX_KEYS issue keys come
                back, while the count stays exact

Permissions are not this tool's job. It passes the caller through, and Jira
decides what that caller can see. Counts are therefore "issues visible to
you", and the answer says so.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date

from .catalog import FINANCE
from .jira_mock import Caller, MockJira

ALLOWED_PROJECTS = FINANCE.queryable  # the default catalog; BoundedJiraTool takes any allowlist
ISSUE_TYPES = ("Incident", "Bug", "Task", "Change")
PRIORITIES = ("P1", "P2", "P3", "P4")
STATUSES = ("Open", "In Progress", "Resolved", "Closed")
GROUP_BY = ("priority", "status", "issue_type")
MAX_RANGE_DAYS = 366
MAX_KEYS = 25


class ToolRejected(ValueError):
    """The request is outside the tool's bounded surface. Nothing was run."""


@dataclass(frozen=True)
class QuerySpec:
    project: str
    created_from: date
    created_before: date  # exclusive
    issue_type: str | None = None
    priority: str | None = None
    status: str | None = None
    group_by: str | None = None


@dataclass(frozen=True)
class ToolResult:
    count: int
    keys: tuple[str, ...]
    truncated: bool
    jql: str
    groups: dict[str, int] | None = None


def validate(spec: QuerySpec, allowed_projects: frozenset[str] = ALLOWED_PROJECTS) -> None:
    if spec.project not in allowed_projects:
        raise ToolRejected(f"project {spec.project!r} is not available to this tool")
    for value, allowed, name in (
        (spec.issue_type, ISSUE_TYPES, "issue_type"),
        (spec.priority, PRIORITIES, "priority"),
        (spec.status, STATUSES, "status"),
        (spec.group_by, GROUP_BY, "group_by"),
    ):
        if value is not None and value not in allowed:
            raise ToolRejected(f"{name} {value!r} is not one of {', '.join(allowed)}")
    if not (isinstance(spec.created_from, date) and isinstance(spec.created_before, date)):
        raise ToolRejected("created_from and created_before must be dates")
    if spec.created_from >= spec.created_before:
        raise ToolRejected("created_from must be before created_before")
    if (spec.created_before - spec.created_from).days > MAX_RANGE_DAYS:
        raise ToolRejected(f"date range is capped at {MAX_RANGE_DAYS} days")


def to_jql(spec: QuerySpec) -> str:
    """The exact JQL a real client would send. Built by the tool, never by the model.

    The end bound is exclusive ("created < next day"): in JQL, created <= a bare
    date means midnight at the start of that day, which silently drops the day.
    """
    parts = [f"project = {spec.project}"]
    if spec.issue_type:
        parts.append(f"issuetype = {spec.issue_type}")
    if spec.priority:
        parts.append(f"priority = {spec.priority}")
    if spec.status:
        parts.append(f'status = "{spec.status}"')
    parts.append(f'created >= "{spec.created_from.isoformat()}"')
    parts.append(f'created < "{spec.created_before.isoformat()}"')
    return " AND ".join(parts) + " ORDER BY created DESC"


class BoundedJiraTool:
    """count_jira_issues: the only operation exposed. There is no write path."""

    name = "count_jira_issues"

    def __init__(self, client: MockJira, allowed_projects: frozenset[str] = ALLOWED_PROJECTS, max_keys: int = MAX_KEYS):
        self.client, self.allowed_projects, self.max_keys = client, frozenset(allowed_projects), max_keys

    @property
    def schema(self) -> dict:
        """Tool definition for model tool calling or an MCP server. Enums mirror the validator."""
        return {
            "name": self.name,
            "description": (
                "Count Jira issues the current user can see, filtered by project, type, priority, "
                "status and creation date. Read only. Returns the count, up to "
                f"{self.max_keys} issue keys, and the JQL that was run."
            ),
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["project", "created_from", "created_before"],
                "properties": {
                    "project": {"type": "string", "enum": sorted(self.allowed_projects)},
                    "created_from": {"type": "string", "format": "date"},
                    "created_before": {"type": "string", "format": "date", "description": "exclusive"},
                    "issue_type": {"type": "string", "enum": list(ISSUE_TYPES)},
                    "priority": {"type": "string", "enum": list(PRIORITIES)},
                    "status": {"type": "string", "enum": list(STATUSES)},
                    "group_by": {"type": "string", "enum": list(GROUP_BY)},
                },
            },
        }

    def spec_from_arguments(self, args: dict) -> QuerySpec:
        """Parse tool call arguments as a model would send them. Unknown keys are rejected,
        so a raw "jql" argument can never pass through."""
        allowed = set(self.schema["input_schema"]["properties"])
        unknown = set(args) - allowed
        if unknown:
            raise ToolRejected(f"unknown arguments: {', '.join(sorted(unknown))}")
        missing = {"project", "created_from", "created_before"} - set(args)
        if missing:
            raise ToolRejected(f"missing arguments: {', '.join(sorted(missing))}")
        try:
            created_from = date.fromisoformat(args["created_from"])
            created_before = date.fromisoformat(args["created_before"])
        except (TypeError, ValueError) as e:
            raise ToolRejected(f"dates must be YYYY-MM-DD: {e}") from None
        return QuerySpec(
            project=args["project"], created_from=created_from, created_before=created_before,
            issue_type=args.get("issue_type"), priority=args.get("priority"),
            status=args.get("status"), group_by=args.get("group_by"),
        )

    def run(self, spec: QuerySpec, caller: Caller) -> ToolResult:
        validate(spec, self.allowed_projects)
        issues = self.client.search(
            caller, project=spec.project, created_from=spec.created_from,
            created_before=spec.created_before, issue_type=spec.issue_type,
            priority=spec.priority, status=spec.status,
        )
        groups = None
        if spec.group_by:
            attr = {"priority": "priority", "status": "status", "issue_type": "type"}[spec.group_by]
            groups = dict(sorted(Counter(getattr(i, attr) for i in issues).items()))
        keys = tuple(i.key for i in issues)
        return ToolResult(
            count=len(issues), keys=keys[: self.max_keys], truncated=len(keys) > self.max_keys,
            jql=to_jql(spec), groups=groups,
        )
