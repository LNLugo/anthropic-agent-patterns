from orchestrator_workers_04_v3 import app


graph = app.get_graph()

png_bytes = graph.draw_mermaid_png()

with open(
    "orchestrator_workers_04_v3_graph.png",
    "wb"
) as f:
    f.write(png_bytes)

print(
    "Graph saved as orchestrator_workers_04_v3_graph.png"
)