import pytest

from rag.chunking import chunk_contract, fixed_size


def test_loader_parses_category_details_and_spans(contract, queries):
    gl = queries["Governing Law"]
    assert gl.answerable and gl.contract_id == contract.id
    s, e = gl.gold_spans[0]
    assert contract.text[s:e] == "governed by the laws of the State of New York"
    assert gl.text.startswith("Governing Law: Which state")
    assert not queries["Non-Compete"].answerable


def test_chunk_offsets_round_trip(contract):
    for c in fixed_size(contract.text, contract.id, size=300, overlap=60):
        assert contract.text[c.start : c.end] == c.text


def test_chunks_cover_the_whole_contract(contract):
    chunks = fixed_size(contract.text, contract.id, size=300, overlap=60)
    covered = set()
    for c in chunks:
        covered.update(range(c.start, c.end))
    assert covered >= {i for i, ch in enumerate(contract.text) if not ch.isspace()}


def test_overlap_is_respected(contract):
    chunks = fixed_size(contract.text, contract.id, size=300, overlap=60)
    for a, b in zip(chunks, chunks[1:]):
        assert b.start < a.end, "consecutive chunks should overlap"


def test_parents_contain_their_children(contract):
    kids, parents = chunk_contract(contract.text, contract.id, size=300, overlap=60, parent_size=1200)
    for k in kids:
        p = parents[k.parent_id]
        assert p.start <= k.start < p.end


@pytest.mark.parametrize("size,overlap", [(0, 0), (100, 100), (100, -1)])
def test_invalid_chunk_settings(size, overlap):
    with pytest.raises(ValueError):
        fixed_size("abc def", "c", size, overlap)
