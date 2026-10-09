# 07 · Retrieval (RAG)

**Control question:** *What evidence did the model see, and can every claim be traced back to it?*

Phase 1 treated the Augmented LLM as a given. This pattern builds its
retrieval capability, and measures it, on a task where being wrong is
expensive: finding clauses in commercial contracts.

## Why contracts, and why CUAD

[CUAD](https://github.com/TheAtticusProject/cuad) (Hendrycks et al., NeurIPS
2021, CC BY 4.0) contains 510 commercial contracts in which lawyers marked the
exact text for 41 clause types, such as Governing Law, Non-Compete and Cap on
Liability. Those character offsets are ground truth for two things that RAG
demos usually leave unmeasured:

* **Retrieval:** did the retrieved chunks contain the clause the lawyer marked?
* **Citations:** does the text the model cites match the clause the lawyer marked?

About two thirds of the clause questions have no answer in a given contract.
That makes CUAD a good test of **abstention**: a reviewer needs "this contract
has no non-compete" as much as they need the clause itself.

## Architecture

```text
 contract ──► chunking (child 1,000 chars / 200 overlap, parent 4,000)
                │
 query ───────► retriever (from YAML)                     scoped to one contract
                │   bm25 ─┐
                │   dense ─┴─► RRF fusion ─► cross encoder rerank ─► [parent windows]
                ▼
           generate (Claude)  ──►  JSON: answer + [{chunk_id, quote}]  or  abstain
                ▼
           validate citations: chunk was shown? quote found in it? map to contract offsets
                ▼
     ≥1 valid citation ──► answer          none, or model abstained ──► abstain
                │
 context ledger: chunk ids injected, characters, tokens, latency, model calls, cost
```

The answer flow is a LangGraph `StateGraph` (`rag/graph.py`), consistent with
patterns 01 to 06.

## Design decisions

| Decision | Choice | Reason |
|---|---|---|
| Retrieval scope | Filtered to the contract under review | Reviewers ask about one agreement. In production the same filter carries business unit, vendor and contract type. |
| Fusion | Reciprocal Rank Fusion, k=60 | Rank based, so BM25 scores and cosine similarity never need a common scale. |
| Reranking | Cross encoder over the top 20 | Predictable latency and throughput compared with an LLM reranking pass, and no extra tokens. |
| Citation rule | An answer ships only with a citation that resolves to shown text | Fabricated or misattributed quotes are caught mechanically, not by trusting the model. |
| Missing evidence | Abstain | On a clause the contract does not contain, a confident wrong answer is the expensive failure. |
| Strategies | One YAML file each | Each row in the ablation table can be reproduced with one command. |
| Cost | Prices passed as arguments | No price is hard coded, so cost figures stay current. |

## Results

### Lexical baseline (reproducible offline)

25 CUAD test contracts (seed 7), 285 answerable queries, `configs/offline/`:

| Strategy | k | Hit@k | Span recall@k | MRR | Est. context tokens / query |
|---|---|---|---|---|---|
| BM25 | 5 | 0.681 | 0.632 | 0.513 | 1,205 |
| BM25 + parent windows | 3 | 0.730 | 0.682 | 0.594 | 2,748 |

Parent windows raise hit rate by about 5 points and cost 2.3x the context
tokens. Whether that trade is worth it depends on the price of a miss, which is
why every row reports both.

### Full ablation

`configs/01` to `05` add dense embeddings (`BAAI/bge-small-en-v1.5`), hybrid
fusion, cross encoder reranking (`cross-encoder/ms-marco-MiniLM-L-6-v2`) and
parent windows. Run them with the commands below. `results/summary.md` is
generated, not hand written.

### Answer quality

`run_answer.py` reports, on a balanced sample of answerable and unanswerable
queries:

* answer rate when the clause exists
* abstain rate when it does not
* citation validity and citation precision against lawyer spans
* tokens, p50 and p95 latency, and cost per query

## Run it

```bash
cd 07_rag
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# data: CUAD test split (SQuAD format)
git clone --depth 1 https://github.com/TheAtticusProject/cuad.git /tmp/cuad
mkdir -p data/cuad && unzip -o /tmp/cuad/data.zip -d data/cuad

# tests (offline, no API key, no model downloads)
pytest -q

# retrieval ablation
python run_eval.py --cuad data/cuad/test.json --configs configs/0*.yaml --contracts 25

# end to end with Claude
export ANTHROPIC_API_KEY=...        # model defaults to $CLAUDE_MODEL
python run_answer.py --cuad data/cuad/test.json --config configs/04_hybrid_rerank.yaml \
  --contracts 10 --queries 60 --input-price <USD per Mtok> --output-price <USD per Mtok>
```

`configs/offline/` runs without downloading any model. Its TF-IDF/SVD
"dense" retriever is a lexical stand-in for smoke tests, not a semantic result.

## Layout

```text
07_rag/
├── configs/              one YAML per retrieval strategy (+ offline/ smoke configs)
├── rag/
│   ├── data.py           CUAD loader: contracts, clause queries, gold spans
│   ├── chunking.py       fixed-size chunks with exact offsets, parent windows
│   ├── retrievers.py     BM25, dense, RRF hybrid, cross encoder rerank, parent
│   ├── config.py         YAML to retriever
│   ├── metrics.py        hit@k, span recall@k, MRR, context size, latency
│   ├── citations.py      citation validation and precision against gold spans
│   ├── ledger.py         context ledger: what entered the context and what it cost
│   ├── graph.py          LangGraph answer flow with abstention
│   └── pipeline.py       index construction
├── run_eval.py           retrieval ablation
├── run_answer.py         end-to-end answer eval with Claude
└── tests/                31 offline tests
```

## Deliberately out of scope

Chunking bakeoffs, embedding model leaderboards and retrieval method matrices
are upstream choices here, not the subject. One sensible default is held
fixed while the variables that change risk are measured: what evidence the
model sees, whether it can cite it, and what it costs.

## Next

* Contextual retrieval (chunk descriptions prepended before indexing) as a
  sixth config
* pgvector backend behind the same retriever interface
* Multi-agent context: per-agent retrieval compared with retrieving once into
  shared state, using the ledger to measure context agreement and token cost

## Credits

CUAD: Hendrycks, Burns, Chen and Ball, *CUAD: An Expert-Annotated NLP Dataset
for Legal Contract Review*, NeurIPS 2021. The idea of defining retrieval
strategies as config files was influenced by Decoding AI's open-source
Second Brain course.
