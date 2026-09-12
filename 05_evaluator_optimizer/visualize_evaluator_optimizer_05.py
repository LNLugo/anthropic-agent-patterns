from evaluator_optimizer_05 import app


graph = app.get_graph()

png_bytes = graph.draw_mermaid_png()

with open(
    "evaluator_optimizer_05_graph.png",
    "wb"
) as f:
    f.write(png_bytes)

print(
    "Graph saved as evaluator_optimizer_05_graph.png"
)