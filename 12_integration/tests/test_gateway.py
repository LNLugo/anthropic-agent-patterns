"""The gateway: identity, version lifecycle, scopes, rate limits, the contract and the
budget, in that order; every response checked against the published contract."""

from datetime import date, datetime, timedelta, timezone

import jsonschema
import pytest
from langchain_core.messages import AIMessage
from starlette.testclient import TestClient

from agent_tools.comments import CommentStore
from agent_tools.jira_mock import MockJira
from agent_tools.mcp_server import READ, IssuedToken, StaticTokenVerifier
from gateway.app import Clock, Gateway, RateLimiter
from gateway.contracts import contract
from gateway.routes import PROBLEM, ROUTES
from integration.adapters import service_alert
from integration.delivery import Broker, Consumer, DeadLetters, ProcessedStore
from integration.triage import Triage
from llm_provider import scripted, scripted_model

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
TOKENS = {"t-lead": IssuedToken("maintenance_lead", (READ, "model:invoke")),
          "t-super": IssuedToken("station_supervisor", (READ,)),
          "t-contractor": IssuedToken("contractor", (READ,))}
Q = {"project": "ELEV", "created_from": "2026-09-16", "created_before": "2026-10-17"}


class Recorder:
    """Every response the tests see, so each can be checked against the contract."""

    def __init__(self):
        self.seen = []


@pytest.fixture
def gw(tmp_path):
    jira, users = MockJira.from_fixture("fixtures/transit_jira.json")
    clock = Clock(NOW)
    triage = Triage(jira, users, CommentStore(), tmp_path / "approvals.db", NOW.date())
    g = Gateway(jira, users, StaticTokenVerifier(TOKENS, "http://gateway"), triage, clock, RateLimiter(10, 10))
    client = TestClient(g.asgi())
    rec = Recorder()

    def call(method, url, token=None, **kw):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        r = client.request(method, url, headers=headers, **kw)
        rec.seen.append((method, url, r))
        return r

    yield {"g": g, "call": call, "clock": clock, "triage": triage, "rec": rec}
    check_against_contract(rec.seen)


def check_against_contract(seen):
    """A 200 must match its route's response schema; anything else must be problem+json."""
    for method, url, r in seen:
        if r.status_code == 200 and "/openapi.json" not in url:
            version, path = url.split("?")[0].split("/", 2)[1:]
            route = next(x for x in ROUTES if x["version"] == version and x["method"] == method.lower()
                         and (x["path"] == "/" + path or x["path"].startswith("/triage") and path.startswith("triage")))
            jsonschema.validate(r.json(), route["response"])
        elif r.status_code != 200:
            assert r.headers["content-type"].startswith("application/problem+json")
            jsonschema.validate(r.json(), PROBLEM)


def test_no_token_no_entry(gw, traces):
    r = gw["call"]("GET", "/v2/incidents/count", params=Q)
    assert r.status_code == 401 and r.headers["WWW-Authenticate"] == 'Bearer error="invalid_token"'
    assert gw["call"]("GET", "/v2/incidents/count", "forged", params=Q).status_code == 401
    [span] = [s for s in traces.of_kind("custom") if s["name"] == "gateway"][:1]
    assert span["data"]["status"] == 401 and traces.of_kind("tool") == []      # no agent ran


def test_a_count_runs_as_the_caller_with_its_trace_id(gw, traces):
    r = gw["call"]("GET", "/v2/incidents/count", "t-super", params={**Q, "status": "Open"})
    assert r.status_code == 200 and r.json()["count"] == 3 and r.json()["visible_to"] == "station_supervisor"
    trace_id = r.headers["x-trace-id"]
    assert {s["trace_id"] for s in traces.spans} == {trace_id}                 # the agent run joined the request's trace
    assert [t for t in traces.traces if t["ended_at"]][0]["metadata"] == {"caller": "station_supervisor"}


def test_jira_permissions_still_apply_through_the_gateway(gw):
    r = gw["call"]("GET", "/v2/incidents/count", "t-contractor", params=Q)
    assert r.status_code == 200 and r.json()["count"] == 0 and r.json()["visible_to"] == "contractor"


def test_v1_is_served_with_its_deprecation_and_sunset(gw):
    r = gw["call"]("GET", "/v1/incidents/count", "t-super", params={"project": "ELEV"})
    assert r.status_code == 200 and set(r.json()) == {"project", "count", "keys"}
    assert r.headers["Deprecation"] == f"@{int(datetime(2026, 10, 1, tzinfo=timezone.utc).timestamp())}"
    assert r.headers["Sunset"] == "Sun, 31 Jan 2027 00:00:00 GMT"
    assert '</v2/incidents/count>; rel="successor-version"' in r.headers["Link"] and 'rel="deprecation"' in r.headers["Link"]
    assert "Deprecation" not in gw["call"]("GET", "/v2/incidents/count", "t-super", params=Q).headers


def test_after_its_sunset_v1_is_gone_and_says_where_to_go(gw):
    gw["clock"].now = datetime(2027, 1, 31, tzinfo=timezone.utc)
    r = gw["call"]("GET", "/v1/incidents/count", "t-super", params={"project": "ELEV"})
    assert r.status_code == 410 and "Use /v2/incidents/count" in r.json()["detail"]
    assert "successor-version" in r.headers["Link"]


def test_a_route_needs_its_scope(gw):
    r = gw["call"]("POST", "/v2/ask", "t-super", json={"question": "How many ELEV incidents?"})
    assert r.status_code == 403 and "model:invoke" in r.json()["title"]


@pytest.mark.parametrize("params, why", [
    ({**Q, "user": "maintenance_lead"}, "unknown parameters: user"),
    ({"project": "ELEV", "created_from": "2026-09-16"}, "created_before is required"),
    ({**Q, "project": "SECOPS"}, "is not one of"),
    ({**Q, "created_from": "16/09/2026"}, "is not a 'date'"),
])
def test_the_contract_refuses_a_request_before_any_agent_runs(gw, traces, params, why):
    r = gw["call"]("GET", "/v2/incidents/count", "t-super", params=params)
    assert r.status_code == 400 and why in r.json()["detail"]
    assert traces.of_kind("tool") == [] and traces.of_kind("guardrail") == []


def test_a_burst_is_limited_per_subject_and_recovers(gw):
    codes = [gw["call"]("GET", "/v2/incidents/count", "t-super", params=Q).status_code for _ in range(25)]
    assert codes.count(200) == 10 and codes.count(429) == 15 and codes[:10] == [200] * 10
    limited = gw["rec"].seen[-1][2]
    assert limited.headers["Retry-After"] == "6"                   # 10 a minute: one every 6 s
    assert gw["call"]("GET", "/v2/incidents/count", "t-contractor", params=Q).status_code == 200  # someone else
    gw["clock"].now += timedelta(seconds=6)
    assert gw["call"]("GET", "/v2/incidents/count", "t-super", params=Q).status_code == 200


def test_triage_status_follows_the_event_driven_run(gw):
    call, triage = gw["call"], gw["triage"]
    assert call("GET", "/v2/triage/ELEV-41", "t-super").json() == {"issue_key": "ELEV-41", "state": "none"}
    broker = Broker()
    Consumer(broker, triage.handlers(), None, DeadLetters(":memory:")).broker.publish(service_alert(
        {"alert_id": "A-1", "asset": "ESC-127-02", "station": "S127", "kind": "out_of_service",
         "severity": "major", "raised_at": "2026-10-16T07:00:00Z", "issue_key": "ELEV-41"}))
    Consumer(broker, triage.handlers(), None, DeadLetters(":memory:")).run_until_idle()
    assert call("GET", "/v2/triage/ELEV-41", "t-super").json() == {
        "issue_key": "ELEV-41", "state": "pending_approval", "requested_by": "svc-alert-triage"}
    triage.decide("triage-ELEV-41", True, "maintenance_lead")
    assert call("GET", "/v2/triage/ELEV-41", "t-super").json()["state"] == "posted"
    assert call("GET", "/v2/triage/ELEV-41", "t-contractor").status_code == 404   # cannot see it, cannot learn it exists


def test_the_model_route_is_held_to_the_callers_budget(gw):
    """Each request is one model call priced as gpt-6-luna at the held-out mean; the budget
    fits three. The fourth is refused at the gateway with 429, and no model is called."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "11_finops"))
    from finops import BudgetPolicy, PriceBook, SpendMeter
    from tracing import InMemoryExporter, Tracer, set_tracer

    book = PriceBook.load()
    one = book.cost("gpt-6-luna", 728, 39)
    meter = SpendMeter(book, today=lambda: NOW.date())
    set_tracer(Tracer([InMemoryExporter(), meter]))
    g = gw["g"]
    g.llm, g.model, g.budget = scripted_model(), "gpt-6-luna", BudgetPolicy(meter, {"maintenance_lead": 3.5 * one}, reserve=one)
    reply = AIMessage(content="", usage_metadata={"input_tokens": 728, "output_tokens": 39, "total_tokens": 767},
                      tool_calls=[{"name": "count_jira_issues", "id": "c1", "args": {
                          "project": "ELEV", "created_from": "2026-09-16", "created_before": "2026-10-17"}}])
    codes = []
    for _ in range(4):
        with scripted([reply]) as model:
            r = gw["call"]("POST", "/v2/ask", "t-lead", json={"question": "How many ELEV incidents this month?"})
            codes.append((r.status_code, len(model.calls)))
            if r.status_code == 429:
                model.discard_steps()
    assert codes == [(200, 1), (200, 1), (200, 1), (429, 0)]
    assert gw["rec"].seen[-1][2].json()["type"] == "/problems/budget-exceeded"
    assert meter.spent("maintenance_lead") == pytest.approx(3 * one)


def test_the_served_contract_is_the_committed_one(gw):
    """Drift: if the code changes the API, the committed contract must change with it, in review."""
    from gateway.contracts import committed

    for v in ("v1", "v2"):
        assert gw["call"]("GET", f"/{v}/openapi.json").json() == committed(v) == contract(v)
