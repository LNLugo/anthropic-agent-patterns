"""Every route the gateway serves, described once. The OpenAPI contract is generated
from this, the handlers are registered from it, and the breaking-change check reads it.

API-led layers, as this repo has them:
    system API      08's bounded Jira tool (count_jira_issues): one system, its rules
    process API     12's triage: combines Jira, the work-order file and approvals
    experience API  this gateway's routes: what a client calls, versioned and governed
"""

from __future__ import annotations

COUNT_V1 = {
    "type": "object", "additionalProperties": False, "required": ["project", "count", "keys"],
    "properties": {"project": {"type": "string"}, "count": {"type": "integer", "minimum": 0},
                   "keys": {"type": "array", "items": {"type": "string"}}},
}
COUNT_V2 = {
    "type": "object", "additionalProperties": False,
    "required": ["project", "count", "keys", "truncated", "jql", "visible_to"],
    "properties": {"project": {"type": "string"}, "count": {"type": "integer", "minimum": 0},
                   "keys": {"type": "array", "items": {"type": "string"}}, "truncated": {"type": "boolean"},
                   "jql": {"type": "string"}, "visible_to": {"type": "string"}},
}
TRIAGE_V2 = {
    "type": "object", "additionalProperties": False, "required": ["issue_key", "state"],
    "properties": {"issue_key": {"type": "string"},
                   "state": {"type": "string", "enum": ["none", "pending_approval", "posted", "declined"]},
                   "requested_by": {"type": "string"}},
}
ASK_REQUEST = {
    "type": "object", "additionalProperties": False, "required": ["question"],
    "properties": {"question": {"type": "string", "minLength": 1, "maxLength": 500}},
}
ASK_V2 = {
    "type": "object", "additionalProperties": False, "required": ["answer", "ended"],
    "properties": {"answer": {"type": "string"}, "ended": {"type": "string"}},
}
PROBLEM = {
    "type": "object", "required": ["type", "title", "status"],
    "properties": {"type": {"type": "string"}, "title": {"type": "string"}, "status": {"type": "integer"},
                   "detail": {"type": "string"}, "instance": {"type": "string"}},
}

PROJECT = {"name": "project", "in": "query", "required": True,
           "schema": {"type": "string", "enum": ["ELEV", "SIG"]}}
DATE = {"type": "string", "format": "date"}

ROUTES = [
    {"version": "v1", "method": "get", "path": "/incidents/count", "operationId": "countIncidents",
     "scope": "jira:read", "summary": "Open incidents created in the last 30 days",
     "parameters": [PROJECT], "response": COUNT_V1},
    {"version": "v2", "method": "get", "path": "/incidents/count", "operationId": "countIncidents",
     "scope": "jira:read", "summary": "Incidents in a project and creation window, as the caller can see them",
     "parameters": [PROJECT,
                    {"name": "created_from", "in": "query", "required": True, "schema": DATE},
                    {"name": "created_before", "in": "query", "required": True, "schema": DATE},
                    {"name": "status", "in": "query", "required": False,
                     "schema": {"type": "string", "enum": ["Open", "Resolved"]}}],
     "response": COUNT_V2},
    {"version": "v2", "method": "get", "path": "/triage/{issue_key}", "operationId": "getTriage",
     "scope": "jira:read", "summary": "Where an incident's automated triage stands",
     "parameters": [{"name": "issue_key", "in": "path", "required": True, "schema": {"type": "string"}}],
     "response": TRIAGE_V2},
    {"version": "v2", "method": "post", "path": "/ask", "operationId": "ask",
     "scope": "model:invoke", "summary": "Ask the assistant; a model routes the question, within your budget",
     "parameters": [], "request": ASK_REQUEST, "response": ASK_V2},
]

VERSIONS = {
    # v1 is deprecated: it counts a fixed 30-day window the client cannot see or change.
    "v1": {"deprecated": "2026-10-01", "sunset": "2027-01-31", "successor": "/v2/incidents/count"},
    "v2": {},
}
DEPRECATION_POLICY = "https://github.com/LNLugo/anthropic-agent-patterns/blob/main/12_integration/README.md#api-lifecycle"
