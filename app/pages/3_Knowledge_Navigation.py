"""Knowledge navigation over network logs: vector search against a graph walk, the network
findings, and how each retriever scored on questions written before and after it was frozen.

Offline by default: the hashing stand-in and in-memory stores, no key, no network. With
LLM_PROVIDER=openai and the Upstash variables set, "Live" uses OpenAI embeddings, Upstash
Vector and Upstash Redis, and the model writes a cited answer.
"""

import json
import os
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
KG = ROOT / "14_knowledge_graph"
for p in (KG, ROOT, ROOT / "10_observability"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from netgraph import graph as G  # noqa: E402
from netgraph.corpus import USERS, load_questions  # noqa: E402
from netgraph.navigator import Navigator  # noqa: E402
from netgraph.retrieve import unit  # noqa: E402

st.set_page_config(page_title="Knowledge Navigation", page_icon="🕸️", layout="wide")

LIVE_READY = all(os.environ.get(v) for v in ("OPENAI_API_KEY", "UPSTASH_VECTOR_REST_URL", "UPSTASH_VECTOR_REST_TOKEN",
                                              "UPSTASH_REDIS_REST_URL", "UPSTASH_REDIS_REST_TOKEN"))


@st.cache_resource
def navigator(live: bool) -> Navigator:
    return Navigator.live() if live else Navigator.offline()


st.title("Knowledge navigation over network logs")


mode = st.radio("Run", ["Offline (stand-in)", "Live (OpenAI + Upstash)"], horizontal=True,
                disabled=not LIVE_READY, help=None if LIVE_READY else "Set OPENAI_API_KEY and the four UPSTASH_* "
                "variables to run live.")
nav = navigator(mode.startswith("Live"))
_lines = sum(1 for k in nav.items if not k.startswith("doc:"))
_pages = len({k.split("#")[0] for k in nav.items if k.startswith("doc:")})
st.caption(f"{_lines:,} synthetic firewall, VPN, auth, syslog and inventory lines and {_pages} runbook, policy and "
           "change pages. Every person, address and host is invented.")

ask_tab, findings_tab, path_tab, results_tab = st.tabs(["Ask", "Network findings", "How are they connected?",
                                                        "Measured results"])

QUESTIONS = load_questions()

# A short path through the demo: the join, the permission boundary from both sides, a question
# written after the retriever was frozen, and one where the walk loses. Results are computed live.
DEMO = [
    ("q1", "netops_analyst", "1. The join: one address ties three people's failed logins to a geo-block"),
    ("q5", "netops_analyst", "2. Permissions: the network team asks about a server"),
    ("q4", "secops_analyst", "3. Permissions: security asks the same question and sees more"),
    ("m3", "netops_analyst", "4. Held-out: a question written after the design was frozen"),
    ("m7", "netops_analyst", "5. Where the walk loses: a plain lookup"),
]
SET_LABEL = {"development": "development", "held_out_1": "held-out 1", "held_out_2": "held-out 2",
             "held_out_3": "held-out 3"}
BY_ID = {q["id"]: q for q in QUESTIONS}


def side(column, title, hits, expected, forbidden):
    units = list(dict.fromkeys(unit(h.key) for h in hits))
    found = expected & set(units)
    column.markdown(f"**{title}**")
    if expected:
        column.metric("Expected evidence found", f"{len(found)} of {len(expected)}")
    leaked = forbidden & set(units)
    if forbidden:
        (column.error if leaked else column.success)(
            f"{len(leaked)} items this person may not read" if leaked else "Nothing this person may not read")
    column.dataframe([{"": "✓" if unit(h.key) in expected else "", "key": h.key,
                       "text": nav.items[h.key].text.replace("\n", " ")[:110]} for h in hits],
                     width="stretch", hide_index=True, height=390,
                     column_config={"": st.column_config.TextColumn(width=30),
                                    "key": st.column_config.TextColumn(width=120)})


with ask_tab:
    mode_pick = st.radio("Questions", ["Demo path", "All questions", "Type your own"], horizontal=True)
    if mode_pick == "Demo path":
        labels = {label: (qid, who) for qid, who, label in DEMO}
        qid, who = labels[st.radio("Step", list(labels), label_visibility="collapsed", key="demo_step")]
        q = BY_ID[qid]
        question = q["question"]
    elif mode_pick == "All questions":
        options = {f"{x['id']} ({SET_LABEL[x['set']]}): {x['question']}": x for x in QUESTIONS}
        q = options[st.selectbox("Question", list(options))]
        question = q["question"]
        who = st.selectbox("Ask as", list(USERS), index=list(USERS).index(q["as"]))
    else:
        q = None
        question = st.text_input("Question", value="Why can't alice.smith reach the database server?")
        who = st.selectbox("Ask as", list(USERS))
    if q is not None and mode_pick == "Demo path":
        st.markdown(f"> **{question}**  \n> asked as **{who}** ({', '.join(USERS[who]) or 'no groups'}) · "
                    f"{SET_LABEL[q['set']]} question · {q['kind'].replace('_', '-')}")
        st.caption(q["note"])
    same = q is not None and who == q["as"]
    expected = set(q["expected"]) if same else set()
    forbidden = set(q.get("forbidden", ())) if same else set()
    if q is not None and not same:
        st.caption("Asked as someone other than the question's author, so there is no expected evidence to score.")
    vec, gra = nav.ask(question, who, "vector"), nav.ask(question, who, "graph")
    left, right = st.columns(2)
    side(left, "Vector search (the article's pipeline)", vec.hits, expected, forbidden)
    side(right, "Graph walk", gra.hits, expected, forbidden)
    if gra.answer is not None:
        (st.success if gra.answer.grounded else st.warning)(gra.answer.text)
    else:
        st.caption("Offline: no model writes an answer. Each column is the evidence a model would be given.")
    sub = gra.subgraph
    if sub.number_of_edges():
        with st.expander("How the graph walk's results connect", expanded=mode_pick == "Demo path"):
            dot = ["graph G { rankdir=LR; node [fontsize=10];"]
            for n, d in sub.nodes(data=True):
                shape = "ellipse" if d["kind"] == "entity" else ("note" if d["kind"] == "doc" else "box")
                style = ', style=filled, fillcolor="#dff0d8"' if unit(n) in expected else ""
                dot.append(f'"{n}" [shape={shape}{style}];')
            dot += [f'"{a}" -- "{b}";' for a, b in sub.edges()]
            st.graphviz_chart("\n".join(dot + ["}"]))
            st.caption("Boxes are log lines, notes are pages, ellipses are the entities that join two or more of "
                       "them. Green: expected evidence.")

with findings_tab:
    groups = USERS[st.selectbox("As", list(USERS), key="findings_as")]
    vg = G.visible(nav.graph, groups)
    st.subheader("Addresses that failed VPN logins for three or more users")
    st.dataframe(G.fan_out(vg) or [{"address": "none"}], width="stretch", hide_index=True)
    st.subheader("Entities that join different logs")
    st.dataframe(G.bridges(vg), width="stretch", hide_index=True)
    st.subheader("Most central entities (PageRank over entities that appear together)")
    st.dataframe([{"entity": e, "pagerank": round(r, 4)} for e, r in G.central(vg)], width="stretch",
                 hide_index=True)
    st.subheader("Communities: entities that keep appearing together (Louvain)")
    for c in G.communities(vg)[:8]:
        st.write(", ".join(c))

with path_tab:
    entities = sorted(n for n, d in nav.graph.nodes(data=True) if d["kind"] == "entity")
    c1, c2, c3 = st.columns(3)
    a = c1.selectbox("From", entities, index=entities.index("user:john.doe"))
    b = c2.selectbox("To", entities, index=entities.index("policy:GEO_BLOCK_INBOUND"))
    who = c3.selectbox("As", list(USERS), key="path_as")
    chain = G.path(nav.graph, a, b, USERS[who])
    if chain is None:
        st.warning("No connection through anything this person may read.")
    else:
        for n in chain:
            st.write(f"- **{n}**" if nav.graph.nodes[n]["kind"] == "entity" else f"- {nav.items[n].text[:160]}")

with results_tab:
    st.write("Recall@10 of the expected evidence on multi-hop questions, offline (the lexical stand-in, not a real "
             "embedding model). Each held-out set was written after a freeze and scored once; the first two "
             "exposed faults that were then fixed, so later columns use a later version of the retriever.")
    res_dir = KG / "results"
    rows = []
    for name, f, split in (("development", "retrieval.json", "development"),
                           ("held-out 1", "held_out_v1.json", "held_out"),
                           ("held-out 2", "held_out_2.json", "held_out_2"),
                           ("held-out 3", "held_out_3.json", "held_out_3")):
        if (res_dir / f).exists():
            d = json.loads((res_dir / f).read_text())
            for r, v in d["retrievers"].items():
                s = v["summary"].get(split)
                if s:
                    rows.append({"questions": name, "retriever": r,
                                 "multi-hop": f"{s['multi_hop']['found']}/{s['multi_hop']['expected']}",
                                 "single-hop": f"{s['single_hop']['found']}/{s['single_hop']['expected']}",
                                 "forbidden returned": s["all"]["forbidden"]})
    st.dataframe(rows, width="stretch", hide_index=True)
