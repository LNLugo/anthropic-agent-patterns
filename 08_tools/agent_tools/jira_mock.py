"""A stand-in for Jira Data Center that enforces its own permissions.

The point of the tool path in the design is that authorization is delegated:
the tool calls Jira as the user, and Jira decides what that user can see. This
mock reproduces the two rules that matter for counts:

  project permission  the user must hold a group with Browse on the project
  issue security      an issue with a security level is visible only to the
                      groups that level names

There is no service account and no admin bypass. Every call names a user, and
results are filtered exactly as Jira would filter them for that user. The mock
is read only: it has no method that changes data.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path


@dataclass(frozen=True)
class Caller:
    """The person on whose behalf a call is made (an on-behalf-of token, in production)."""

    id: str
    groups: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class Issue:
    key: str
    project: str
    type: str
    priority: str
    status: str
    created: date
    summary: str
    security_level: str | None = None


class MockJira:
    def __init__(self, projects: dict, security_levels: dict, issues: list[Issue]):
        self._browse = {k: frozenset(v["browse_groups"]) for k, v in projects.items()}
        self.project_names = {k: v["name"] for k, v in projects.items()}
        self._levels = {k: frozenset(v["groups"]) for k, v in security_levels.items()}
        self._issues = list(issues)

    @classmethod
    def from_fixture(cls, path: str | Path) -> tuple["MockJira", dict[str, Caller]]:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        issues = [
            Issue(
                key=i["key"], project=i["project"], type=i["type"], priority=i["priority"],
                status=i["status"], created=date.fromisoformat(i["created"]), summary=i["summary"],
                security_level=i.get("security_level"),
            )
            for i in raw["issues"]
        ]
        users = {u["id"]: Caller(u["id"], frozenset(u.get("groups", ()))) for u in raw["users"]}
        return cls(raw["projects"], raw.get("security_levels", {}), issues), users

    def _visible(self, issue: Issue, caller: Caller) -> bool:
        if not (caller.groups & self._browse.get(issue.project, frozenset())):
            return False
        if issue.security_level is None:
            return True
        # Unknown security level: hidden from everyone (fail closed).
        return bool(caller.groups & self._levels.get(issue.security_level, frozenset()))

    def search(
        self,
        caller: Caller,
        *,
        project: str,
        created_from: date,
        created_before: date,
        issue_type: str | None = None,
        priority: str | None = None,
        status: str | None = None,
    ) -> list[Issue]:
        """Issues matching the filters that this caller is allowed to see, newest first."""
        if not isinstance(caller, Caller) or not caller.id:
            raise PermissionError("every Jira call must be made on behalf of a named user")
        hits = [
            i for i in self._issues
            if i.project == project
            and created_from <= i.created < created_before
            and (issue_type is None or i.type == issue_type)
            and (priority is None or i.priority == priority)
            and (status is None or i.status == status)
            and self._visible(i, caller)
        ]
        return sorted(hits, key=lambda i: (i.created, i.key), reverse=True)
