from routing_02 import app


# Get the compiled graph
graph = app.get_graph()

# Render graph as PNG
png = graph.draw_mermaid_png()

# Save PNG
with open("routing_02_graph.png", "wb") as f:
    f.write(png)

print("Graph saved to routing_02_graph.png")