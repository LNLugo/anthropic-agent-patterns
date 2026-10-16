"""The gateway: every request passes the same policies, in this order.

    1 identity      bearer token -> subject and scopes            401, before anything else
    2 version       v1 deprecated: Deprecation, Sunset, Link headers; after its sunset   410
    3 scope         the route's scope must be on the token        403
    4 rate limit    token bucket per subject                       429 with Retry-After
    5 contract      query and body checked against the route's schema; unknown parameters refused   400
    6 handler       the agent runs as the caller: 08's guardrails, bound and Jira permissions
    7 budget        on the one route that calls a model, 11's budget before each call   429

Errors are problem+json (RFC 9457). Every request is one trace (10_observability)
for the identity that made it, with a gateway span carrying the route, version and
status, so 11's meter charges a model call to the caller and an audit can see a
refused request like any other. In Azure this is API Management's inbound policy
pipeline; in AWS, API Gateway with an authorizer and usage plans.
"""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from email.utils import format_datetime

import jsonschema
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from agent_tools.assistant import ask as run_assistant
from agent_tools.assistant import build_assistant
from agent_tools.jira_mock import Caller, MockJira
from agent_tools.jql_tool import BoundedJiraTool, QuerySpec
from agent_tools.mcp_server import PresetRouter
from agent_tools.model_router import Decision, ModelRouter
from integration.triage import PROJECTS, Triage, transit_policy
from tracing import get_tracer

from .contracts import contract
from .routes import DEPRECATION_POLICY, ROUTES, VERSIONS


class Clock:
    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@dataclass
class TokenBucket:
    """capacity requests at once, refilled at rate per second."""

    capacity: float
    rate: float
    tokens: float = field(default=-1.0)
    at: float | None = None

    def take(self, now: float) -> float:
        """0 if a request may go now, else the seconds until one may."""
        if self.tokens < 0:
            self.tokens, self.at = self.capacity, now
        self.tokens = min(self.capacity, self.tokens + (now - self.at) * self.rate)
        self.at = now
        if self.tokens >= 1:
            self.tokens -= 1
            return 0.0
        return (1 - self.tokens) / self.rate


class RateLimiter:
    def __init__(self, capacity: int = 10, per_minute: int = 10):
        self.capacity, self.rate = capacity, per_minute / 60
        self.buckets: dict[str, TokenBucket] = {}

    def take(self, subject: str, now: float) -> float:
        return self.buckets.setdefault(subject, TokenBucket(self.capacity, self.rate)).take(now)


def problem(status: int, slug: str, title: str, detail: str = "", headers: dict | None = None) -> JSONResponse:
    body = {"type": f"/problems/{slug}", "title": title, "status": status}
    if detail:
        body["detail"] = detail
    return JSONResponse(body, status_code=status, media_type="application/problem+json", headers=headers)


def _midnight(d: str) -> datetime:
    return datetime.combine(date.fromisoformat(d), time(), timezone.utc)


class Gateway:
    def __init__(self, jira: MockJira, users: dict[str, Caller], verifier, triage: Triage, clock: Clock,
                 limiter: RateLimiter | None = None, llm=None, model: str | None = None, budget=None):
        self.jira, self.users, self.verifier, self.triage, self.clock = jira, users, verifier, triage, clock
        self.limiter = limiter or RateLimiter()
        self.llm, self.model, self.budget = llm, model, budget
        self.tool = BoundedJiraTool(jira, allowed_projects=PROJECTS)

    # ---------------------------------------------------------------- the pipeline

    async def dispatch(self, request: Request, route: dict) -> JSONResponse:
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex
        tracer = get_tracer()
        token = await self._token(request)
        who = token.subject if token is not None and token.subject in self.users else None
        with tracer.trace(f"gateway {route['version']} {route['operationId']}",
                          metadata={"caller": who} if who else {"transport": "http"}) as t:
            with tracer.span("custom", "gateway", method=route["method"].upper(), route=route["path"],
                             version=route["version"]) as span:
                response = await self._policies(request, route, token, who)
                span.set(status=response.status_code, caller=who)
        response.headers["x-request-id"] = rid
        if t is not None:
            response.headers["x-trace-id"] = t.trace_id
        return response

    async def _token(self, request: Request):
        auth = request.headers.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            return None
        return await self.verifier.verify_token(auth[7:].strip())

    async def _policies(self, request: Request, route: dict, token, who: str | None) -> JSONResponse:
        if who is None:
            return problem(401, "unauthenticated", "No valid token",
                           headers={"WWW-Authenticate": 'Bearer error="invalid_token"'})
        lifecycle = self._lifecycle(route["version"])
        if lifecycle.get("gone"):
            return problem(410, "version-sunset", f"{route['version']} was retired on {VERSIONS[route['version']]['sunset']}",
                           f"Use {VERSIONS[route['version']]['successor']}.", headers=lifecycle["headers"])
        if route["scope"] not in token.scopes:
            return problem(403, "missing-scope", f"This token does not carry the {route['scope']} scope")
        wait = self.limiter.take(who, self.clock().timestamp())
        if wait:
            return problem(429, "rate-limited", "Too many requests", f"Try again in {math.ceil(wait)} s.",
                           headers={"Retry-After": str(math.ceil(wait))})
        try:
            args = await self._arguments(request, route)
        except jsonschema.ValidationError as e:
            return problem(400, "invalid-request", "The request does not match the contract", e.message)
        response = self._handle(route, self.users[who], args)
        for k, v in lifecycle.get("headers", {}).items():
            response.headers[k] = v
        return response

    def _lifecycle(self, version: str) -> dict:
        v = VERSIONS[version]
        if not v.get("deprecated"):
            return {}
        sunset = _midnight(v["sunset"])
        headers = {"Deprecation": f"@{int(_midnight(v['deprecated']).timestamp())}",
                   "Sunset": format_datetime(sunset, usegmt=True),
                   "Link": f'<{v["successor"]}>; rel="successor-version", <{DEPRECATION_POLICY}>; rel="deprecation"'}
        return {"headers": headers, "gone": self.clock() >= sunset}

    async def _arguments(self, request: Request, route: dict) -> dict:
        params = {p["name"]: p for p in route["parameters"]}
        args = dict(request.path_params)
        unknown = set(request.query_params) - set(params)
        if unknown:
            raise jsonschema.ValidationError(f"unknown parameters: {', '.join(sorted(unknown))}")
        for name, p in params.items():
            if p["in"] != "query":
                continue
            if name not in request.query_params:
                if p.get("required"):
                    raise jsonschema.ValidationError(f"{name} is required")
                continue
            value = request.query_params[name]
            jsonschema.validate(value, p["schema"], format_checker=jsonschema.FormatChecker())
            args[name] = value
        if route.get("request"):
            try:
                body = json.loads(await request.body() or b"null")
            except json.JSONDecodeError:
                raise jsonschema.ValidationError("the body is not JSON") from None
            jsonschema.validate(body, route["request"])
            args.update(body)
        return args

    # ---------------------------------------------------------------- handlers: the agent, as the caller

    def _count(self, caller: Caller, spec: QuerySpec):
        app = build_assistant(self.tool, lambda q, c, k: [], PresetRouter(Decision("aggregation", spec)),
                              guardrails=transit_policy())
        return run_assistant(app, f"count {spec.project}", caller, self.clock().date())

    def _handle(self, route: dict, caller: Caller, args: dict) -> JSONResponse:
        op, version = route["operationId"], route["version"]
        if op == "countIncidents":
            today = self.clock().date()
            if version == "v1":
                spec = QuerySpec(args["project"], today - timedelta(days=30), today + timedelta(days=1), status="Open")
            else:
                spec = QuerySpec(args["project"], date.fromisoformat(args["created_from"]),
                                 date.fromisoformat(args["created_before"]), status=args.get("status"))
            out = self._count(caller, spec)
            if "result" not in out:
                return problem(422, "refused", "The query was not run", out.get("answer", ""))
            r = out["result"]
            body = {"project": spec.project, "count": r.count, "keys": list(r.keys)}
            if version == "v2":
                body |= {"truncated": r.truncated, "jql": r.jql, "visible_to": caller.id}
            return JSONResponse(body)
        if op == "getTriage":
            key = args["issue_key"]
            if self.jira.get_issue(caller, key) is None:   # missing and hidden look the same
                return problem(404, "not-found", f"No incident {key} that you can see")
            state = self.triage.graph(Decision("none"), comments=True).get_state(
                {"configurable": {"thread_id": f"triage-{key}"}})
            if not state.values:
                return JSONResponse({"issue_key": key, "state": "none"})
            ended = state.values.get("stop_reason")
            body = {"issue_key": key, "state": "pending_approval" if state.next else (ended or "pending_approval")}
            if state.values.get("proposal"):
                body["requested_by"] = state.values["proposal"]["requested_by"]
            return JSONResponse(body)
        if op == "ask":
            if self.llm is None:
                return problem(503, "no-model", "No model is configured for this gateway")
            router = ModelRouter(self.llm, self.tool, coverage=False)
            if self.model:
                router.model = self.model
            app = build_assistant(self.tool, lambda q, c, k: [], router, guardrails=transit_policy(), budget=self.budget)
            out = run_assistant(app, args["question"], caller, self.clock().date())
            if out.get("stop_reason") == "budget":
                return problem(429, "budget-exceeded", "Today's model budget is used", out["answer"])
            return JSONResponse({"answer": out.get("answer", ""), "ended": out.get("stop_reason") or "answered"})
        return problem(404, "not-found", "No such operation")

    # ---------------------------------------------------------------- the ASGI app

    def asgi(self) -> Starlette:
        routes = []
        for r in ROUTES:
            async def endpoint(request: Request, r=r):
                return await self.dispatch(request, r)
            routes.append(Route(f"/{r['version']}{r['path']}", endpoint, methods=[r["method"].upper()]))
        for v in VERSIONS:
            async def spec(request: Request, v=v):
                return JSONResponse(contract(v))
            routes.append(Route(f"/{v}/openapi.json", spec, methods=["GET"]))
        return Starlette(routes=routes)
