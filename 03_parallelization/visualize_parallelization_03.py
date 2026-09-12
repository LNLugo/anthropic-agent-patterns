from parallelization_03 import app


# Get the compiled graph
graph = app.get_graph()

# Render graph as PNG
png = graph.draw_mermaid_png()

# Save PNG
with open("parallelization_03_graph.png", "wb") as f:
    f.write(png)

print("Graph saved to parallelization_03_graph.png")