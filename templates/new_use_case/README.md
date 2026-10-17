# New use case template

Copied by `13_paved_road/new_use_case.py`. Edit in this order:

1. `use_case.yaml`: the catalog (your projects and the words people use for them), `data`, `identity`, `tools`. Keep `policy` as it is unless a design review agrees otherwise.
2. `golden.yaml`: replace the example cases with yours. Keep one of each kind: an answer, a permission difference, a refusal, a halt, a clarifying question.
3. `prompts/router.md`: only the vocabulary the model needs.
4. `hooks.py`: usually nothing.

Then run the gate: `cd 13_paved_road && python run_gate.py ../use_cases/<name>`. It must pass before the pull request can merge.

Record decisions with the templates in `docs/templates/`.
