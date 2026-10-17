# 14 Knowledge navigation over network logs

Questions in plain language over firewall, VPN, auth and system logs, answered from the log lines and the runbooks they point to. Network analysis joins lines that share an address, a user, a host or a policy.

The module is modelled on [From Raw Firewall Logs to Answers: Building a RAG Pipeline for Network Security](https://medium.com/@everythingismindgame/from-raw-firewall-logs-to-answers-building-a-rag-pipeline-for-network-security-b31d625f081d) and its [example code](https://github.com/code-with-amitk/Code-examples/tree/master/Machine_Learning/RAG_Pipeline/Code/RAG_Pipeline_VPN_Firewall_logs). That code embeds each line, finds the nearest lines and answers from them.

The article's own example is a multi-hop question: "reason of failure for user john.doe?" The VPN lines name the user. The firewall lines that deny the same address do not, so nearest-line search cannot join the two. The question here is whether a graph can.

**The answer, measured:** yes, on questions that need a join, but only modestly. It took two held-out sets to find out, and the graph costs some single-hop accuracy.

```bash
cd 14_knowledge_graph
python -m netgraph.corpus            # rewrite the fixtures (a test checks they reproduce)
python run_evaluation.py             # offline: every retriever, every question set
python run_evaluation.py --live      # OpenAI embeddings, Upstash Vector and Upstash Redis
streamlit run ../app/Agent_Patterns.py   # then "Knowledge Navigation" in the sidebar
```

## The data

The data is synthetic and deterministic (seed 14). It contains 1,281 log lines:
- **firewall:** 643 lines;
- **VPN:** 352 lines;
- **syslog:** 161 lines, from routers and servers;
- **auth:** 118 lines, readable by the security team only;
- **asset inventory:** 7 lines.

There are also 17 pages: runbooks, policy pages and change records. Each page's front matter says who may read it and which entities it applies to. The line format follows the article: `2026-10-14 08:01:05 VPN_LOGIN_FAILED user=john.doe ip=203.0.113.45`.

Scenario lines are planted among background traffic. Each question in [fixtures/questions.yaml](fixtures/questions.yaml) lists the exact lines and pages that answer it, as the asker may see them. Some questions also list the lines the asker must never get.

The article's sample logs are not reused: the example repository carries no license.

## How it works

| Step | What happens | Where |
|---|---|---|
| Parse | Each line's fields are read by a pattern: users, addresses, hosts, policies, changes. A model is not used for this, because a regular expression is exact and free | `corpus.py` |
| Graph | Log lines and page sections are nodes, and so is every entity they name. Two lines that share an address are two hops apart | `graph.py` |
| Store | Vectors go to Upstash Vector, with the readers' groups as metadata. The graph goes to Upstash Redis: one hash per node, one set of neighbours per node | `stores.py` |
| Vector search | The question's nearest items. The asker's groups filter inside the query, before ranking | `retrieve.py` |
| Graph walk | Personalised PageRank from the best few items and from any entity the question names exactly, run over the visible graph only | `retrieve.py` |
| Priors | Raise items whose type is rare, or whose pair of actors is new (first login from this address), and lower items outside the question's time words ("this morning", "since lunchtime") | `retrieve.py`, `timewin.py` |
| Answer | The model answers from the evidence only and cites line keys. An answer that cites a key it was not shown is withheld | `answer.py` |

Network analysis also runs on its own, in the app's "Network findings" tab:
- **Fan-out.** The one address that failed VPN logins for three users, 203.0.113.45, is flagged as credential stuffing.
- **Bridges.** These are entities seen in several logs. An address appears in the firewall, auth and inventory logs.
- **Central entities.** These are ranked by PageRank.
- **Communities.** Groups of entities that appear together, found with Louvain.
- **Shortest path.** The path between two entities, through only what the asker may read. For the network team, a service account has no path to an external address, because the link runs through the auth log.

## Results

All results are recall@10 of the expected evidence. Log lines and pages each count as one unit.

These are offline results. The vector half uses a lexical stand-in (hashed words), not a real embedding model. So these numbers compare the retrievers over a word-matching baseline. OpenAI's numbers come from `--live`, which has not been run yet.

**How the questions were kept honest.** The retriever was frozen three times. After each freeze, a new set of questions was written and scored once. The pull request was squash-merged, so `main` holds one commit; the original commits are kept under the tag [`kg-freeze-history`](https://github.com/LNLugo/anthropic-agent-patterns/commits/kg-freeze-history):

| Commit | Step |
|---|---|
| `d57b0cd` | Retriever frozen before any held-out question is scored |
| `0b8cce4` | Held-out 1 scored; result kept before any change |
| `4770091` | Second freeze, with the fixes held-out 1 exposed |
| `d875102` | Held-out 2 questions written, not yet scored |
| `6a6127b` | Held-out 2 scored once; result kept |
| `2bbeeb7` | Third freeze, with the fixes held-out 2 exposed |
| `26d0844` | Held-out 3 questions written, not yet scored |
| `25fa665` | Held-out 3 scored once; result kept |

What each set exposed:
- **Held-out 1** exposed three faults: the stand-in matched common words, pages outranked everything, and named entities were ignored. They were fixed.
- **Held-out 2** exposed two more. "Rare" is not the same as "unusual for this person", and the time words did nothing. They were fixed too.
- **Held-out 3** was scored once, by the retriever as it is now. A test fails if the retriever changes and that result no longer reproduces.

| Questions | Retriever version | Vector only (the article) | Best control without the walk | **Graph walk** |
|---|---|---|---|---|
| Held-out 1, multi-hop | first freeze | 6/26 | 11/26 | **10/26** |
| Held-out 2, multi-hop | second freeze | 4/14 | 5/14 | **3/14** |
| **Held-out 3, multi-hop** | final | 9/26 | 13/26 | **16/26** |
| Held-out 3, single-hop | final | 4/4 | 4/4 | **2/4** |
| Development, multi-hop (seen while building) | final | 3/33 | 17/33 | 24/33 |

The control for the walk is everything the graph retriever uses except the walk itself:
- the same priors;
- the same vector pool;
- every line that names an entity in the question.

So the difference in the last two columns is the walk. On held-out 3, the walk beat the control on three of eight questions. It lost one question (m7, a single-hop list of router events, where it drifted to related lines) and tied the other four.

**What each part is worth on held-out 3**, taking one part away at a time from the final retriever (16/26 multi-hop):

| Taken away | Multi-hop | What it shows |
|---|---|---|
| All priors | 10/26 | The priors carry most of the gain |
| Time windows | 13/26 | Worth 3 |
| Novelty (new pairs of actors) | 15/26 | Worth 1 here, and it costs 1 on held-out 1 (12/26 with it, 13/26 without): no clear effect either way |
| Entity links | **20/26** | Linking hurt on these questions. It is kept as frozen and reported, not tuned away |
| Nothing, plus IDF edge weights | 17/26 | Within noise |

**Read with care:**
- Eight questions per set is small. A difference of three is suggestive, not proof.
- The development column is not evidence. It was used to build the retriever.
- No retriever, on any set, returned a line or page the asker may not read. A control asks the same questions with the security group and does get those items back, so the zero is the filter working.

## Controls

| Control | Test |
|---|---|
| Permissions filter before ranking, in the vector query and on the graph | `test_no_retriever_ever_returns_what_the_asker_may_not_read`, plus its widened-filter control |
| A group name cannot inject into Upstash's filter language | `test_the_vector_filter_is_built_from_checked_group_names` |
| An index made for another model's dimensions is refused before indexing | `test_an_index_made_for_another_model_is_refused_before_indexing` |
| Answers cite only evidence they were shown; otherwise they are withheld | `test_a_grounded_answer_cites_only_what_it_was_shown` |
| Traces hold counts, not questions or lines (with a content-on control) | `test_retrieval_traces_counts_not_content` |
| Quoted numbers are what the code produces now | `test_the_published_numbers_are_what_the_code_produces_now` |
| Fixtures are exactly what the generator writes | `test_the_fixtures_are_exactly_what_the_generator_writes` |

Nine mutations to these controls were tried. The suite caught the seven that still applied; two no longer matched the code.

## Running live

1. Create an Upstash Vector index with **1536 dimensions and cosine similarity** (text-embedding-3-small's size), and an Upstash Redis database.
2. Set these variables:
   - `OPENAI_API_KEY`;
   - `UPSTASH_VECTOR_REST_URL` and `UPSTASH_VECTOR_REST_TOKEN`;
   - `UPSTASH_REDIS_REST_URL` and `UPSTASH_REDIS_REST_TOKEN`.
3. Run `pip install -r requirements.txt`, then `python run_evaluation.py --live`. This writes `results/retrieval_live.json` with the same tables.

Indexing embeds about 1,300 short texts, roughly 40,000 tokens. At $0.02 per million tokens ([OpenAI](https://developers.openai.com/api/docs/models/text-embedding-3-small), checked 2026-10-17), that is under a tenth of a cent.

## Limits

- **The offline embedding is lexical.** A real model may find better starting points. If it does, the walk and the priors would have less to fix.
- **Synthetic data, one author.** The questions and the scenarios were written by the same hand that built the retriever. Freezing first limits that bias but does not remove it.
- **Upstash not yet exercised.** The adapters are tested against fakes of the two SDKs' interfaces, not against Upstash itself. The first `--live` run is also the first test of them.
- **No structured query.** The article itself says RAG does not replace SIEM correlation and detection rules. Time windows here are soft weights, not filters.
- **Single-hop cost.** A router that sends questions naming one entity and one event type to a direct lookup, and the rest to the walk, is the obvious next step. It is not built, and it would need held-out set 4.
