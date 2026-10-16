"""What each strategy puts in front of the model, and what the benchmark measures."""

import pytest

import run_memory_benchmark as bench
from memory import InMemorySession
from memory.strategies import append_all, compact, context_tokens, is_standing, retrieve, retrieve_pinned, window


def history(*pairs, pins=()):
    s = InMemorySession("s", "ana")
    for n, (q, a) in enumerate(pairs):
        s.add_items([{"role": "user", "content": q, "pinned": n in pins}, {"role": "assistant", "content": a}])
    return s.get_items()


H = history(("From now on, only count Signals.", "Understood."),
            ("Which work orders are open on ESC-127-02?", "WO-1001."),
            ("How many P2s in PAY?", "3."), ("Show the rate limit.", "10 a minute."),
            ("Any outages at S203?", "None."), pins=(0,))


@pytest.mark.parametrize("text, standing", [
    ("From now on, only count Signals.", True), ("Always show me the JQL.", True),
    ("Going forward, use UTC.", True), ("I work nights, so today means since 7 pm.", False),
    ("How many incidents always happen on Mondays?", True),       # the rule is wording: a false positive
])
def test_standing_instructions_are_found_by_wording(text, standing):
    assert is_standing(text) is standing


def test_append_all_keeps_everything_and_window_keeps_the_end():
    assert append_all(H, "q") == H
    assert window(4)(H, "q") == H[-4:] and window(0)(H, "q") == []


def test_compact_keeps_the_recent_items_and_a_bounded_summary_of_the_rest():
    ctx = compact(keep=4, budget=20)(H, "q")
    assert ctx[1:] == H[-4:] and ctx[0].get("summary")
    assert context_tokens(ctx[:1]) <= 20 + 7    # the budget plus its label
    assert "WO-1001" in ctx[0]["content"]          # the newest older exchange fits
    assert "From now on" not in ctx[0]["content"]  # the oldest exchange did not fit


def test_retrieve_finds_the_exchange_the_question_points_at():
    ctx = retrieve(k=1, recent=2)(H, "Is WO-1001 on ESC-127-02 still open?")
    assert "WO-1001." in [i["content"] for i in ctx] and ctx[-2:] == H[-2:]


def test_retrieve_alone_misses_an_instruction_the_question_does_not_mention():
    question = "How many open incidents were created this week?"
    assert not any("Signals" in i["content"] for i in retrieve(k=1, recent=2)(H, question))
    assert any("Signals" in i["content"] for i in retrieve_pinned(k=1, recent=2)(H, question))


def test_nothing_is_retrieved_on_no_shared_words():
    assert retrieve(k=3, recent=0)(H, "zzz") == []


def test_items_come_back_in_conversation_order_without_duplicates():
    ctx = retrieve_pinned(k=3, recent=4)(H, "count Signals work orders")
    seqs = [i["seq"] for i in ctx]
    assert seqs == sorted(set(seqs))


# ---------------------------------------------------------------- the benchmark

@pytest.fixture(scope="module")
def results():
    return bench.run()


def test_append_all_never_forgets_and_grows_with_the_conversation(results):
    rows = results["append all"]
    assert all(r["recall"] == r["of"] for r in rows)
    assert rows[-1]["context_tokens"] > 15 * rows[0]["context_tokens"]


def test_the_bounded_strategies_stay_bounded(results):
    for name in ("last 10 items", "append and compact", "retrieve relevant", "retrieve relevant + pinned"):
        sizes = [r["context_tokens"] for r in results[name]]
        assert max(sizes) < 200, name


def test_pinning_is_what_keeps_the_standing_instruction(results):
    def total(name, fid):
        return sum(r["hits"][fid] for r in results[name])
    assert total("retrieve relevant", "pin-signals") <= 1 and total("retrieve relevant + pinned", "pin-signals") == 25


def test_retrieval_with_pins_recalls_most_at_a_fraction_of_the_cost(results):
    pinned, full = results["retrieve relevant + pinned"], results["append all"]
    assert sum(r["recall"] for r in pinned) >= 0.9 * sum(r["of"] for r in pinned)
    assert full[-1]["context_tokens"] > 30 * pinned[-1]["context_tokens"]


def test_a_bounded_summary_forgets_the_start_of_a_long_conversation(results):
    assert [r["recall"] for r in results["append and compact"]][1:] == [0, 0, 0, 0]
