from pathlib import Path

from langchain_core.runnables.graph_mermaid import draw_mermaid_png

from agents_06_v3_1 import app

# Two outcomes route to final_answer; the diagram draws one edge for both, so
# its label names both.
mermaid = app.get_graph().draw_mermaid().replace(
    "&nbsp;FinalOutput&nbsp;", "&nbsp;FinalOutput or Interruption: max_turns&nbsp;"
)

output_path = Path(__file__).resolve().parent / "agents_06_v3_1_graph.png"
output_path.write_bytes(draw_mermaid_png(mermaid))

print(f"Graph saved as {output_path}")
