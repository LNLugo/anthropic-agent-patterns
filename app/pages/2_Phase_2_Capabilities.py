"""Phase 2 capability demos: permission-aware retrieval, leakage check, assistant, router eval.

Runs fully offline on the synthetic wiki and Jira fixtures. No API key, no model download.
Streamlit picks this file up automatically because it lives in app/pages/.
"""

import sys
from datetime import date
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
RAG_DIR = ROOT / "07_rag"
TOOLS_DIR = ROOT / "08_tools"
for p in (RAG_DIR, TOOLS_DIR):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from rag.acl import (  # noqa: E402
    PermissionFilteredBM25,
    build_index,
    can_view,
    effective_gates,
    load_wiki,
    measure_leakage,
)
import demo_assistant  # noqa: E402  (08_tools; provides the permission-filtered wiki search)
from agent_tools.assistant import ask, build_assistant  # noqa: E402
from agent_tools.evaluate import evaluate  # noqa: E402
from agent_tools.jira_mock import MockJira  # noqa: E402
from agent_tools.jql_tool import BoundedJiraTool  # noqa: E402
from agent_tools.router import RulesRouter  # noqa: E402

WIKI_PATH = RAG_DIR / "tests" / "fixtures" / "mini_confluence.json"
JIRA_PATH = TOOLS_DIR / "tests" / "fixtures" / "mini_jira.json"
ROUTER_PATH = TOOLS_DIR / "tests" / "fixtures" / "router_questions.json"
PUBLISHED_PATH = TOOLS_DIR / "results_published.json"
LOCAL_RESULTS = TOOLS_DIR / "results"

st.set_page_config(page_title="Phase 2 Capabilities", page_icon="🔐", layout="wide")


# --------------------------------------------------
# Cached resources (built once per session)
# --------------------------------------------------

@st.cache_resource
def wiki_resources():
    import json

    wiki, users = load_wiki(WIKI_PATH)
    index = build_index(wiki)
    probes = json.loads(WIKI_PATH.read_text(encoding="utf-8"))["probe_queries"]
    return wiki, users, index, probes


@st.cache_resource
def assistant_resources():
    jira, callers = MockJira.from_fixture(JIRA_PATH)
    # RulesRouter keeps this page offline and free; demo_assistant.py runs the model router.
    app = build_assistant(BoundedJiraTool(jira), demo_assistant.wiki_search(), RulesRouter())
    return app, callers


def groups_label(groups) -> str:
    return ", ".join(sorted(groups)) or "no groups"


def show_jql(jql: str) -> None:
    """Show the JQL wrapped, so it is readable in narrow columns (older Streamlit falls back)."""
    try:
        st.code(jql, language="sql", wrap_lines=True)
    except TypeError:
        st.markdown(f"`{jql}`")


def preset_box(label: str, options: list[str], key: str) -> str:
    """A preset dropdown plus an editable text box; choosing a preset refills the box."""
    text_key = f"{key}_text"
    if text_key not in st.session_state:
        st.session_state[text_key] = options[0]

    def _sync():
        st.session_state[text_key] = st.session_state[key]

    st.selectbox(label, options, key=key, on_change=_sync)
    return st.text_input("Or type your own", key=text_key)


wiki, users, index, probes = wiki_resources()


# --------------------------------------------------
# Header
# --------------------------------------------------

st.title("🔐 Phase 2: The Capability Layer")
st.markdown(
    "Retrieval and tools that respect **who is asking**. Everything here runs offline on "
    "synthetic Confluence and Jira data, so every result is reproducible."
)

tab1, tab2, tab3, tab4, tab5 = st.tabs(
    ["1 · Permission-aware retrieval", "2 · Leakage check", "3 · Assistant", "4 · Router accuracy",
     "5 · Rules vs model"]
)


# --------------------------------------------------
# Tab 1: same query, different users, filter on or off
# --------------------------------------------------

with tab1:
    st.subheader("Same question, different people, different pages")
    st.caption("Naive RAG ignores who is asking. Turn the filter off to see what leaks.")

    c1, c2 = st.columns([3, 2])
    with c1:
        query = preset_box("Query", probes, key="t1_preset")
    with c2:
        chosen = st.multiselect(
            "Users",
            list(users),
            default=[u for u in ["new_hire", "billing_oncall", "contractor"] if u in users],
            key="t1_users",
        )
        enforce = st.toggle("Permission filter", value=True, key="t1_filter")
        k = st.slider("Results per user", 1, 5, 3, key="t1_k")

    retriever = PermissionFilteredBM25(index, enforce=enforce)
    if enforce:
        st.success("Filter ON: results are restricted to pages each user can open.")
    else:
        st.error("Filter OFF: this is naive RAG. Leaks are flagged in red.")

    cols = st.columns(max(1, len(chosen)))
    total_leaks = 0
    for col, uid in zip(cols, chosen):
        user = users[uid]
        with col:
            st.markdown(f"**{uid}**  \n`{groups_label(user.groups)}`")
            hits = retriever.retrieve(query, user, k)
            if not hits:
                st.info("Nothing this user is allowed to see matches.")
            for rank, s in enumerate(hits, start=1):
                page = wiki.pages[index.page_of[s.chunk.id]]
                allowed = can_view(user, effective_gates(page.id, wiki))
                text = f"{rank}. **[{page.id}] {page.title}**  \nowner {page.owner}, updated {page.last_modified}"
                if allowed:
                    st.markdown(text)
                else:
                    total_leaks += 1
                    st.error(text + "  \n⚠️ LEAK: this user cannot open this page")
    if not enforce:
        st.metric("Restricted pages shown", total_leaks)


# --------------------------------------------------
# Tab 2: leakage check across every persona and probe
# --------------------------------------------------

with tab2:
    st.subheader("Proof, not anecdote")
    st.caption(
        "Every persona asks every probe query. The 'off' row is the positive control: "
        "it proves the check can detect a leak, so the 'on' row's zero means something."
    )
    k2 = st.slider("Results per query", 1, 8, 5, key="t2_k")
    personas = list(users.values())
    rows = {
        label: measure_leakage(PermissionFilteredBM25(index, enforce=enf), index, personas, probes, k=k2)
        for label, enf in (("off", False), ("on", True))
    }
    restricted_pages = sum(1 for pid in wiki.pages if len(effective_gates(pid, wiki)) > 1)
    st.write(
        f"{len(wiki.pages)} pages in {len(wiki.spaces)} spaces ({restricted_pages} restricted), "
        f"{len(index.chunks)} chunks, {len(personas)} personas, {len(probes)} probe queries."
    )
    st.table(
        [
            {
                "Filter": label,
                "Queries": r.queries,
                "Chunks returned": r.chunks_returned,
                "Restricted returned": r.restricted_returned,
            }
            for label, r in rows.items()
        ]
    )
    if rows["on"].restricted_returned:
        st.error(f"LEAK with filter on: {rows['on'].restricted_returned} restricted chunks returned.")
    elif not rows["off"].restricted_returned:
        st.warning("Positive control found no leaks: the probe set cannot detect a broken filter.")
    else:
        st.success("PASS: zero restricted chunks returned with the filter on.")
    with st.expander(f"What leaks with the filter off ({rows['off'].restricted_returned} chunks)"):
        st.dataframe(
            [{"User": u, "Query": q, "Chunk": c} for u, q, c in rows["off"].leaked],
            hide_index=True,
        )


# --------------------------------------------------
# Tab 3: assistant routes counting to Jira, knowledge to the wiki
# --------------------------------------------------

QUESTIONS = [
    "How many P1 incidents did the billing platform have last quarter?",
    "How do I rotate the billing API credentials?",
    "How do I get VPN access?",
]

with tab3:
    st.subheader("Counting goes to Jira, knowledge goes to the wiki")
    st.caption(
        "RAG cannot count. The router sends counting questions to a bounded Jira query that runs "
        "with each person's permissions and shows its JQL; knowledge questions go to the "
        "permission-filtered wiki search. This page routes with the rules, so it runs offline at "
        "no cost; the model router, which routed 18 to 19 of 20 held-out questions where the "
        "rules routed 9, runs in `08_tools/demo_assistant.py`. Tab 5 has the comparison."
    )
    app, callers = assistant_resources()
    c1, c2 = st.columns([3, 2])
    with c1:
        question = preset_box("Question", QUESTIONS, key="t3_preset")
    with c2:
        who = st.multiselect(
            "Users",
            list(callers),
            default=[u for u in ["billing_oncall", "new_hire", "contractor"] if u in callers],
            key="t3_users",
        )
        today = st.date_input("Today", value=date(2026, 10, 12), key="t3_today")

    cols = st.columns(max(1, len(who)))
    for col, uid in zip(cols, who):
        caller = callers[uid]
        out = ask(app, question, caller, today)
        with col:
            st.markdown(f"**{uid}**  \n`{groups_label(caller.groups)}`")
            st.markdown(f"Route: **{out.get('route', 'n/a')}**")
            for line in str(out.get("answer", "")).splitlines():
                if line.startswith("JQL:"):
                    show_jql(line[4:].strip())
                elif line.strip():
                    st.write(line)


# --------------------------------------------------
# Tab 4: router accuracy, including where it fails
# --------------------------------------------------

with tab4:
    st.subheader("Measured, including the failures")
    r = evaluate(ROUTER_PATH)
    m1, m2 = st.columns(2)
    m1.metric("Routed correctly", f"{r.routed_correctly}/{r.total}", f"{r.router_accuracy:.0%}", delta_color="off")
    m2.metric(
        "Queries extracted exactly",
        f"{r.extracted_correctly}/{r.extraction_total}",
        f"{r.extraction_accuracy:.0%}",
        delta_color="off",
    )
    if r.misroutes:
        st.markdown("**Misroutes**")
        st.table([{"Question": q, "Expected": want, "Got": got} for q, want, got in r.misroutes])
    if r.extraction_misses:
        st.markdown("**Extraction misses**")
        st.table([{"Question": q, "What differed": why} for q, why in r.extraction_misses])
    st.caption("Vague questions are where a production assistant needs a clarifying step.")


# --------------------------------------------------
# Tab 5: rules vs a model choosing tools, on development and held-out questions
# --------------------------------------------------

def _count(n, word):
    return f"{n} {word}" + ("" if n == 1 else "es" if word.endswith("s") else "s")


def _span(values, fmt="{}"):
    """One value, or 'low to high' across runs."""
    lo, hi = min(values), max(values)
    return fmt.format(lo) if lo == hi else f"{fmt.format(lo)} to {fmt.format(hi)}"


with tab5:
    import json

    st.subheader("Rules vs a model choosing tools")
    st.markdown(
        "The same routing and extraction decisions, made two ways: hand-written rules, and a model "
        "that must call one of three tools (`count_jira_issues`, `search_wiki`, `ask_clarification`). "
        "Whatever the model sends goes through the same validator as the rules, so the bound does not "
        "depend on the model."
    )

    published = json.loads(PUBLISHED_PATH.read_text(encoding="utf-8"))
    choice = st.radio(
        "Question set",
        list(published["sets"]),
        format_func=lambda k: published["sets"][k]["label"],
        horizontal=False,
    )
    entry = published["sets"][choice]
    try:
        rules = evaluate(TOOLS_DIR / "tests" / "fixtures" / entry["file"])
    except ValueError as e:  # a question file still holding template placeholders
        rules = None
        st.info(f"Rules not shown: {e}")

    def _of(n, total):
        return f"{n} of {total}" if total else "n/a"

    rows = [] if rules is None else [{
        "Router": "Rules (computed live)", "Runs": 1,
        "Routed correctly": _of(rules.routed_correctly, rules.total),
        "Extracted exactly": _of(rules.extracted_correctly, rules.extraction_total),
        "Refused by the bound": "n/a", "Stopped by coverage check": "n/a",
        "Tokens in / out": "0", "Latency p50": "~0", "Cost per 1,000 questions": "$0",
    }]
    for name, model in entry["models"].items():
        runs = model["runs"]
        n, total, ext_total = len(runs), runs[0]["total"], runs[0]["extraction_total"]
        cost = [r["cost_per_question_usd"] for r in runs if r["cost_per_question_usd"] is not None]
        rows.append({
            "Router": name, "Runs": n,
            "Routed correctly": ", ".join(str(r["routed"]) for r in runs) + f" of {total}",
            "Extracted exactly": (", ".join(str(r["extracted"]) for r in runs) + f" of {ext_total}"
                                  if ext_total else "n/a"),
            "Refused by the bound": _span([r["rejected_by_bound"] for r in runs]),
            "Stopped by coverage check": (", ".join(str(r["stopped_by_coverage"]) for r in runs)
                                          if model.get("coverage_check") else "off"),
            "Tokens in / out": f"{runs[0]['tokens_in']} / {runs[0]['tokens_out']}",
            "Latency p50": _span([r["latency_p50_s"] for r in runs], "{:.2f} s"),
            "Cost per 1,000 questions": f"${sum(cost) / len(cost) * 1000:.2f}" if cost else "not measured",
        })
    st.table(rows)
    st.caption(
        f"Model results recorded {published['recorded']} with `08_tools/run_model_router_eval.py`; "
        "rules recomputed on every page load. gpt-6-luna takes no temperature setting, so it was run "
        "three times on the held-out set."
    )

    finding = entry.get("finding")
    if finding:
        st.markdown(f"#### {finding['title']}")
        left, right = st.columns([1, 1])
        with left:
            st.markdown(f"**Question:** {finding['q']}")
            st.markdown(f"**Should have:** {finding['expected']}")
            st.markdown(f"**Seen:** {finding['seen']}")
        with right:
            st.markdown(f"**{finding.get('panel_label', 'The call the model made')}**")
            st.json(finding["tool_call"])
        st.warning(finding["why_it_matters"])

    st.markdown("#### Where each router missed")
    if rules is not None:
        with st.expander(f"Rules: {_count(len(rules.misroutes), 'misroute')}, "
                         f"{_count(len(rules.extraction_misses), 'extraction miss')}"):
            if rules.misroutes:
                st.table([{"Question": q, "Expected": want, "Got": got} for q, want, got in rules.misroutes])
            if rules.extraction_misses:
                st.table([{"Question": q, "What differed": why} for q, why in rules.extraction_misses])
    for name, model in entry["models"].items():
        for i, run in enumerate(model["runs"], 1):
            label = name if len(model["runs"]) == 1 else f"{name}, run {i}"
            with st.expander(f"{label}: {_count(len(run['misroutes']), 'misroute')}, "
                             f"{_count(len(run['extraction_misses']), 'extraction miss')}"):
                if run["misroutes"]:
                    st.table([{"Question": m["q"], "Expected": m["expected"], "Got": m["got"]}
                              for m in run["misroutes"]])
                if run["extraction_misses"]:
                    st.table([{"Question": m["q"], "What differed": m["why"]} for m in run["extraction_misses"]])
                if not run["misroutes"] and not run["extraction_misses"]:
                    st.write("No misses.")

    local = sorted(LOCAL_RESULTS.glob("model_router_*.json"), reverse=True) if LOCAL_RESULTS.exists() else []
    if local:
        with st.expander(f"Your local runs ({len(local)} files in 08_tools/results, not committed)"):
            pick = st.selectbox("Run", local, format_func=lambda p: p.name)
            run = json.loads(pick.read_text(encoding="utf-8"))
            st.table([{
                "Question": r["q"], "Expected": r["expected_route"], "Got": r["route"],
                "Route ok": "yes" if r["route_ok"] else "no",
                "Extraction": {True: "yes", False: "no", None: ""}[r["extraction_ok"]],
                "Arguments": json.dumps(r["arguments"]) if r["arguments"] else "",
            } for r in run["rows"]])
