"""SmartDMS document control: what is built of the design, and what intake found.

Offline: reads results/intake.json and runs intake over the synthetic fixtures in process.
No key, no network, no model call.
"""

import json
import os
import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
MOD = ROOT / "15_document_control"
for p in (MOD, ROOT, ROOT / "12_integration", ROOT / "10_observability", ROOT / "07_rag"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from docctl import status  # noqa: E402

st.set_page_config(page_title="Document Control", page_icon="📄", layout="wide")
st.title("Document control (SmartDMS)")
st.caption("Quality and regulatory documents from three invented source systems: a quality DMS, team sites and "
           "ERP-linked supplier documents. Every site, person, supplier and document is synthetic. The design is "
           "[docs/designs/smartdms.md](https://github.com/LNLugo/anthropic-agent-patterns/blob/main/docs/designs/"
           "smartdms.md); this page shows what of it is built and what intake measured.")

R = json.loads((MOD / "results" / "intake.json").read_text())


RR = json.loads((MOD / "results" / "retrieval.json").read_text())
RC = json.loads((MOD / "results" / "comparison.json").read_text())


@st.cache_resource
def intake():
    from docctl.intake import run_intake

    return run_intake()


@st.cache_resource
def search_index():
    from docctl.index import build
    from llm_provider import HashingEmbeddings

    emb = HashingEmbeddings()
    return build(intake(), emb), emb


build_tab, intake_tab, ask_tab, compare_tab, review_tab, doc_tab = st.tabs(
    ["Build status", "Intake", "Ask", "Compare", "Review", "A document"])

with build_tab:
    data = status.load()
    counts = {s: sum(1 for x in data["sections"] if x["status"] == s) for s in data["statuses"]}
    cols = st.columns(len(counts))
    for c, (s, n) in zip(cols, counts.items()):
        c.metric(s, f"{n} of 23")
    st.dataframe([{"§": s["section"], "Section": s["title"], "Status": s["status"], "PR": s["pr"],
                   "Note": s.get("note", "")} for s in data["sections"]], width="stretch", hide_index=True,
                 height=600)
    st.subheader("Stand-ins for managed services")
    st.dataframe([{"Managed service": x["service"], "Stand-in here": x["stand_in"]} for x in data["stand_ins"]],
                 width="stretch", hide_index=True)

with intake_tab:
    c, life, ids, pii = R["contracts"], R["lifecycle"], R["identity"], R["personal_data"]
    cls, ext, val = R["classification"], R["extraction"], R["validation"]
    st.markdown("Deterministic first: rules make every decision below. No model is called.")
    a, b, c3, d = st.columns(4)
    a.metric("Quarantined at the contract", f"{c['quarantined']}", help="Each source's data contract, checked "
             "on arrival. Planted: " + str(c["planted"]))
    b.metric("Most effective revisions of one document", str(life["most_effective_revisions_of_one_document_at_any_step"]),
             help=f"At every one of {life['steps_checked']} steps of a shuffled, duplicated replay")
    c3.metric("Illegal status changes quarantined",
              f"{life['illegal_transitions']['planted_caught']} of {life['illegal_transitions']['planted']}")
    d.metric("Held in two systems, one record", f"{ids['held_in_two_systems']['found']}")
    a, b, c3, d = st.columns(4)
    a.metric("Classified correctly", f"{cls['correct']} of {cls['documents']}")
    b.metric("Filed as the wrong type", str(cls["wrong_type_filed"]))
    c3.metric("Values extracted correctly", f"{ext['correct']} of {ext['of']}")
    d.metric("Planted validation failures to review", f"{val['planted'] - val['missed']} of {val['planted']}")
    a, b, c3, _ = st.columns(4)
    a.metric("Personal data redacted", f"{pii['redacted']} of {pii['planted']}")
    b.metric("Approver names kept", f"{pii['approver_names_kept']} of {pii['approver_names']}")
    c3.metric("Chunks reused, not re-embedded", f"{R['parsing_and_chunking']['share_reused']:.0%}")
    st.caption("Read with care: one author wrote the documents and the rules, so the born-digital figures are an "
               "upper bound. The misses are the honest part: OCR damage on scans, and names in free text that "
               "redaction rules were never written for.")
    st.subheader("Classification by type")
    st.dataframe([{"type": t, **v} for t, v in cls["by_type"].items()], width="stretch", hide_index=True)
    st.subheader("Extraction by format")
    st.dataframe([{"format": f, **v} for f, v in ext["by_format"].items()], width="stretch", hide_index=True)
    st.subheader("Personal data by kind")
    st.dataframe([{"kind": k, **v} for k, v in pii["by_kind"].items()], width="stretch", hide_index=True)

with ask_tab:
    from docctl.assistant import ask
    from docctl.corpus import USERS
    from docctl.evaluate import questions

    st.markdown("Effective versions only, by default; only what the asker may open; every sentence cited, or "
                "no answer. Offline, no model writes an answer: the evidence it would be given is shown.")
    a, b, c3, d = st.columns(4)
    a.metric("Expected sections found", f"{RR['recall']['all']['found']} of {RR['recall']['all']['expected']}")
    b.metric("Superseded returned by default", str(RR["stale_versions"]["returned_by_default"]),
             help=f"Control without the status filter: {RR['stale_versions']['control_without_the_status_filter']}")
    c3.metric("Shown to someone who may not open it", str(RR["access"]["unreadable_returned"]),
              help=f"Control with every group: {RR['access']['control_with_every_group']} forbidden sections")
    d.metric("Answered or declined correctly", f"{RR['answerability']['correct']} of {RR['answerability']['of']}")
    qs = questions()
    pick = st.selectbox("Development question", [f"{q['id']} ({q['kind']}): {q['question']}" for q in qs],
                        key="ask_question")
    q = qs[[f"{x['id']} ({x['kind']}): {x['question']}" for x in qs].index(pick)]
    left, right = st.columns([3, 1])
    question = left.text_input("Question", value=q["question"], key=f"ask_text_{q['id']}")
    who = right.selectbox("Ask as", list(USERS), index=list(USERS).index(q["as"]), key="ask_as")
    st.caption(q["note"])
    llm = None
    if os.environ.get("OPENAI_API_KEY") and st.checkbox("Use the live model", value=False):
        from llm_provider import get_chat_model

        llm = get_chat_model()
    idx, emb = search_index()
    ans = ask(question, USERS[who], idx, llm, emb)
    st.markdown(f"Route: **{ans.route}**" + (f" · tool **{ans.tool}**" if ans.tool else ""))
    if ans.tool_result:
        st.json(ans.tool_result)
    if ans.sentences:
        st.success(ans.text())
        st.dataframe([{"cites": f"{c['doc_number']} rev {c['revision']}", "section": c["section"],
                       "status": c["status"]} for s_ in ans.sentences for c in s_["citations"]], hide_index=True)
    elif ans.reason:
        (st.info if ans.route in ("out_of_scope", "compare") else st.warning)(ans.reason)
    if ans.retrieval is not None:
        r = ans.retrieval
        st.caption(f"Retrieval: {r.route} · coverage {r.coverage} · answerable {r.answerable}")
        st.dataframe([{"section": f"{h.row.doc_number} rev {h.row.revision} · {h.row.section_path}",
                       "status": h.label, "flagged": "instruction-like text" if h.row.suspected_injection else "",
                       "text": h.row.text.replace("\n", " ")[:100]} for h in r.hits], width="stretch",
                     hide_index=True)

with compare_tab:
    from docctl import headline
    from docctl.compare import classify_rules, diff, match

    v = RC["version_vs_version"]
    st.markdown("A deterministic diff finds every change; something else only says what kind of change it is. "
                f"{v['pairs']} revision pairs with {v['planted_changes']} planted changes, plus formatting noise "
                "that must not count.")
    r = v["rules"]
    a, b, c3, d = st.columns(4)
    a.metric("Planted changes found (diff + rules)", f"{r['found']} of {r['planted']}")
    b.metric("Typed correctly", f"{r['type_correct']} of {r['found']}")
    c3.metric("Graded correctly", f"{r['severity_correct']} of {r['found']}")
    d.metric("False findings", str(r["false_findings"]))
    if isinstance(v["model_arms"], dict):
        st.dataframe([{"run": i + 1, "arm": arm, **{k: x[k] for k in ("found", "planted", "type_correct",
                                                                        "severity_correct", "false_findings")}}
                      for i, run in enumerate(v["model_arms"]["runs"]) for arm, x in run.items()], hide_index=True)
    else:
        st.info("The headline's two model arms (diff + model, and the model comparing directly) have not been run "
                "live yet: `python run_comparison.py --live` with a key. ADR 0019 is Proposed until then.")
    ps = headline.pairs()
    pid = st.selectbox("Revision pair", [p["id"] for p in ps], key="pair")
    p = next(x for x in ps if x["id"] == pid)
    da, db = headline.load(p)
    fs = [classify_rules(ch) for ch in diff(da, db, p["a"], p["b"])]
    matched = {id(f) for _, f in match(fs, p["changes"])[0]}
    st.dataframe([{"section": f.change.section, "change": f.change.kind, "before": f.change.before[:70],
                   "after": f.change.after[:70], "type": f.type, "severity": f.severity,
                   "planted": "✓" if id(f) in matched else "false finding"} for f in fs],
                 width="stretch", hide_index=True)
    cc, ds = RC["certificate_vs_specification"], RC["document_vs_standard"]
    left, right = st.columns(2)
    left.metric("Out-of-specification results caught", f"{cc['planted_caught']} of {cc['planted_arrived']}",
                help="Of those that reached intake; the third was quarantined at its contract")
    right.metric("Planted breaks of the standard found", f"{ds['planted_caught']} of {ds['planted']}",
                 help=f"Passed wrongly: {ds['planted_passed_wrongly']}")
    st.dataframe(ds["planted_checks"], hide_index=True)

with review_tab:
    from docctl.review import route

    rv = RC["review"]
    st.markdown("The AI recommends; a qualified person approves. High-risk types always go to a person, and a "
                "share of automatic approvals is reviewed blind.")
    a, b, c3, d = st.columns(4)
    a.metric("Decisions recorded", str(rv["audit_records"]))
    b.metric("Edited by a reviewer", str(rv["decisions"].get("edit", 0)))
    c3.metric("Sampled for blind review", str(rv["blind_sampled"]))
    d.metric("Audit chain", "verifies" if rv["audit_chain_verifies"] else "broken",
             help=f"A changed record is found at its position ({rv['tampered_record_found_at']})")
    st.caption(f"Written back to the DMS once each, including after a lost response; without a request id the "
               f"control wrote {rv['written_back_without_request_id_after_a_lost_response']} times. The service "
               "and the requester cannot approve their own output.")
    recs = intake().records()
    st.dataframe([{"document": k, "type": r.doc_type or "unclassified", "risk": (rt := route(r)).risk,
                   "goes to": "blind review" if rt.blind else rt.to, "why": "; ".join(rt.reasons)}
                  for k, r in recs.items()], width="stretch", hide_index=True, height=400)
    st.subheader("Override rates")
    st.dataframe([{"type": t, **x} for t, x in rv["override_rates"]["by_type"].items()], hide_index=True)

with doc_tab:
    i = intake()
    recs = i.records()
    key = st.selectbox("Document", list(recs), index=list(recs).index("COA-2005-L26-0742/01"))
    r = recs[key]
    left, right = st.columns(2)
    left.markdown(f"**{key}** · from **{r.system}** · type **{r.doc_type or 'unclassified'}** "
                  f"({r.classification.confidence:.2f}, by {r.classification.by}) · status **{i.status(r)}**")
    (left.warning if r.review else left.success)(
        "Goes to review: " + "; ".join([f.rule + " (" + f.field + ")" for f in r.failures]
                                       + ([r.classification.reason] if r.classification.review else []))
        if r.review else "Passes intake checks")
    left.dataframe([{"field": f.name, "value": str(f.value)[:60], "page": f.page,
                     "OCR confidence": f.confidence} for f in r.fields.values()], width="stretch", hide_index=True)
    right.markdown(f"**Chunks** ({len(r.chunks)})")
    right.dataframe([{"section": c.section_path, "tokens": c.token_count, "table": c.is_table,
                      "text": c.text[:90]} for c in r.chunks], width="stretch", hide_index=True)
    loc = i.registry.records()[key]
    st.caption("Held in: " + ", ".join(f"{x.system} ({x.uri})" for x in loc.locations)
               + (" · conflicting copy quarantined: " + ", ".join(x.uri for x in loc.conflicts) if loc.conflicts else ""))
