"""Conformance is computed, and CI holds it: no unregistered gap, no expired or stale
exception, and the published scorecard matches what the check finds today."""

import importlib.util
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCH = ROOT / "docs" / "architecture"
_spec = importlib.util.spec_from_file_location("conformance", ARCH / "conformance.py")
conf = importlib.util.module_from_spec(_spec)
sys.modules["conformance"] = conf  # dataclasses look the module up here
_spec.loader.exec_module(conf)
_spec2 = importlib.util.spec_from_file_location("render_standards", ARCH / "render_standards.py")
render = importlib.util.module_from_spec(_spec2)
_spec2.loader.exec_module(render)


def current():
    standards, exceptions = render.load()
    return standards, exceptions, conf.evaluate(standards, exceptions, date.today())


def test_the_repository_conforms_today():
    """The gate. It fails on an unregistered gap, a failing static check, or an exception
    that has expired or is no longer needed. EXC-001 expires on 2026-12-31: from then this
    fails until 07 is routed through llm_provider, which is the point."""
    _, _, report = current()
    problems = ([f"{r.standard} {r.module}: {r.status} {r.detail}" for r in report.gaps]
                + [f"{e['id']} expired {e['expires']}" for e in report.expired]
                + [f"{e['id']} stale" for e in report.stale] + [f"{e['id']} unknown" for e in report.unknown])
    assert problems == []


def test_the_published_scorecard_is_current():
    standards, exceptions, report = current()
    assert (ARCH / "scorecard.md").read_text() == conf.scorecard(report, standards, exceptions), \
        "run python docs/architecture/conformance.py --write"


def test_evidence_and_checks_are_for_modules_the_standard_governs():
    for s in render.load()[0]:
        assert set(s["evidence"]) <= set(s["applies_to"]), s["id"]
        assert set(s["checks"]) <= set(conf.CHECKS), s["id"]


def test_the_static_check_finds_exactly_the_registered_gap():
    assert conf.model_construction("07") == ["07_rag/rag/graph.py:74", "07_rag/rag/graph.py:88"]
    for m in ("01", "02", "03", "04", "05", "06", "08"):
        assert conf.model_construction(m) == [], m


def test_the_static_check_catches_a_new_model_built_in_place(tmp_path, monkeypatch):
    (tmp_path / "99_new").mkdir()
    (tmp_path / "99_new" / "agent.py").write_text("from langchain_openai import ChatOpenAI\nllm = ChatOpenAI(model='x')\n")
    (tmp_path / "99_new" / "test_agent.py").write_text("llm = ChatOpenAI()\n")   # tests are allowed to
    monkeypatch.setattr(conf, "ROOT", tmp_path)
    monkeypatch.setattr(conf, "MODULES", {"99": "99_new"})
    assert conf.model_construction("99") == ["99_new/agent.py:2"]


STD = [{"id": "X-01", "applies_to": ["07", "08"], "evidence": {"08": ["t"]}, "checks": []},
       {"id": "X-02", "applies_to": ["07"], "evidence": {}, "checks": []}]


def exc(**kw):
    return {"id": "EXC-9", "standard": "X-01", "modules": ["07"], "expires": date(2027, 1, 1), **kw}


def test_an_unregistered_gap_fails():
    r = conf.evaluate(STD, [], date(2026, 10, 16))
    assert [(g.standard, g.module, g.status) for g in r.gaps] == [("X-01", "07", "gap")] and not r.ok
    assert r.count("attested") == 1                       # review-only is counted apart, not as a gap


def test_a_registered_gap_passes_until_its_expiry():
    assert conf.evaluate(STD, [exc()], date(2026, 12, 31)).ok
    late = conf.evaluate(STD, [exc()], date(2027, 1, 2))
    assert not late.ok and [e["id"] for e in late.expired] == ["EXC-9"]


def test_an_exception_for_something_that_now_conforms_is_stale():
    r = conf.evaluate(STD, [exc(modules=["08"])], date(2026, 10, 16))
    assert [e["id"] for e in r.stale] == ["EXC-9"] and not r.ok


def test_an_exception_for_a_module_the_standard_does_not_govern_fails():
    r = conf.evaluate(STD, [exc(), exc(id="EXC-10", modules=["12"])], date(2026, 10, 16))
    assert [e["id"] for e in r.unknown] == ["EXC-10"]


def test_a_failing_static_check_needs_an_exception():
    std = [{"id": "X-03", "applies_to": ["07"], "evidence": {}, "checks": ["fake"]}]
    checks = {"fake": lambda m: ["07_rag/x.py:1"]}
    assert conf.evaluate(std, [], date(2026, 10, 16), checks).gaps[0].status == "failing"
    ok = conf.evaluate(std, [exc(standard="X-03")], date(2026, 10, 16), checks)
    assert ok.ok and ok.rows[0].detail == ["07_rag/x.py:1"]


def test_the_figures_quoted_in_the_readmes_are_the_computed_ones():
    import re

    standards, exceptions, report = current()
    pairs, conforming = len(report.rows), report.count("tested") + report.count("checked")
    enforced = sum(1 for s in standards if s["evidence"] or s["checks"])
    for doc in (ROOT / "README.md", ARCH / "reference-architecture.md"):
        for got, total in re.findall(r"(\d+) of (\d+) standard-module pairs", doc.read_text()):
            assert (int(got), int(total)) == (conforming, pairs), doc.name
    text = (ROOT / "README.md").read_text()
    assert f"{len(standards)} standards, {enforced} of them enforced" in text
    assert f"{len(exceptions)} registered exceptions" in text
