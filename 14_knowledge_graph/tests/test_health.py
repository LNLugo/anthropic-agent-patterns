"""The health checks behind RUNBOOK.md: silent on healthy data, and each planted fault is
caught by the check meant for it. A check that never fires proves nothing."""

import json
from pathlib import Path

import pytest

import run_health_check as rhc
from netgraph import health
from netgraph.corpus import load

HERE = Path(__file__).resolve().parents[1]

# The check each fault exists to trip. Others may fire too (a stopped feed also costs recall);
# these must.
CAUGHT_BY = {
    "vendor changes the VPN log format": {"parse:vpn"},
    "firewall feed stops": {"fresh:firewall"},
    "auth log tagged readable by netops": {"permissions"},
    "graph store save stops partway": {"graph store"},
    "walk retuned without re-measuring (4 hops to 1)": {"regression"},
}


@pytest.fixture(scope="module")
def report():
    from tracing import Tracer, set_tracer

    previous = set_tracer(Tracer(exporters=[]))
    try:
        yield {name: rhc.run(name, fault) for name, fault in rhc.SCENARIOS.items()}
    finally:
        set_tracer(previous)


def alarms(scenario):
    return {c["name"] for c in scenario["checks"] if not c["ok"]}


def test_healthy_data_raises_no_alarm(report):
    assert alarms(report["healthy"]) == set()
    assert len(report["healthy"]["checks"]) == 13      # 5 parse, 5 fresh, store, permissions, regression


def test_every_planted_fault_is_caught_by_the_check_meant_for_it(report):
    assert set(CAUGHT_BY) == set(rhc.SCENARIOS) - {"healthy"}
    for name, expected in CAUGHT_BY.items():
        assert expected <= alarms(report[name]), name


def test_a_changed_log_format_stops_the_rebuild_instead_of_indexing_half_a_feed(report):
    s = report["vendor changes the VPN log format"]
    assert s["not_run"].startswith("the rebuild stops: vpn:292")
    assert [c["name"] for c in s["checks"]] == [f"parse:{x}" for x in ("vpn", "firewall", "syslog", "auth",
                                                                        "inventory")]


def test_freshness_measures_hours_since_the_newest_line():
    checks = {c.name: c for c in health.freshness(load())}
    assert checks["fresh:vpn"].ok and 0 < checks["fresh:vpn"].value <= 6
    assert checks["fresh:inventory"].ok                 # a monthly snapshot, not a stream
    assert not health.freshness([])[0].ok               # a feed with no lines at all is not fresh


def test_the_recorded_health_run_is_what_the_code_produces_now(report):
    """RUNBOOK.md quotes results/health.json; this fails until it is regenerated."""
    recorded = json.loads((HERE / "results" / "health.json").read_text())
    assert recorded == {"scenarios": list(report.values())}
