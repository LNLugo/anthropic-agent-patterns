# ADR 0006: Over MCP, identity comes only from the verified token

**Status:** Accepted, 2026-10-16 · **Standards:** APP-02

## Context

MCP lets any agent call the tools. If a tool took a user argument, any agent could ask for anyone's view of Jira.

## Decision

No tool takes a user, caller or group argument. The subject comes from the verified bearer token, and each tool needs
a scope (`jira:read`, plus `jira:write` for comments). In production the token would be an on-behalf-of token exchanged
from the user's sign-in (RFC 8693).

## Alternatives rejected

- Pass the user as an argument and trust the client: trusts the least trusted party.
- One service token for all clients: loses who is asking.

## Consequences

Every call is audited with the token's subject and its trace id. Unknown or expired tokens get 401 before any tool
runs.

## Evidence

Over real HTTP: the same call returns 4 for one token holder and 3 for another, and a `user=` argument sent with the
second token is dropped (08).

## The strongest objection

*"What stops a client from using someone else's token?"* Nothing in the tool, and that is correct: token theft is
the identity provider's problem, handled with short lifetimes and audience checks. The tool's job is never to accept
identity from anywhere else.
