from pathlib import Path

import pytest

from rag.data import load_cuad

FIXTURE = Path(__file__).parent / "fixtures" / "mini_cuad.json"


@pytest.fixture
def mini():
    return load_cuad(FIXTURE)


@pytest.fixture
def contract(mini):
    return mini[0][0]


@pytest.fixture
def queries(mini):
    return {q.category: q for q in mini[1]}
