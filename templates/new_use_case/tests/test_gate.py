"""The use case's own tests, generated from the template. The 13_paved_road suite runs them."""

from pathlib import Path

from llm_provider import ScriptedChatModel
from paved_road import build
from paved_road.gate import run, summary

HERE = Path(__file__).resolve().parents[1]


def test_the_configuration_builds(tmp_path):
    uc = build(HERE, llm=ScriptedChatModel(), state_dir=tmp_path, trace_dir=tmp_path)
    assert uc.spec.owner["person"] and uc.spec.owner["team"]


def test_offline_gate():
    result = run(HERE, "offline")
    assert result.passed, summary([result])
