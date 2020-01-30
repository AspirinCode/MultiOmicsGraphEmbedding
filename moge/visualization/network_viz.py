import networkx as nx
import pandas as pd
import plotly.express as px
from fa2 import ForceAtlas2

forceatlas2 = ForceAtlas2(
    # Behavior alternatives
    outboundAttractionDistribution=True,  # Dissuade hubs
    linLogMode=False,  # NOT IMPLEMENTED
    adjustSizes=False,  # Prevent overlap (NOT IMPLEMENTED)
    edgeWeightInfluence=1.0,

    # Performance
    jitterTolerance=1.0,  # Tolerance
    barnesHutOptimize=True,
    barnesHutTheta=1.2,
    multiThreaded=False,  # NOT IMPLEMENTED

    # Tuning
    scalingRatio=2.0,
    strongGravityMode=False,
    gravity=1.0,

    # Log
    verbose=False)


def graph_viz(g: nx.Graph, nodelist: list, node_labels=None, edge_label=None, title="Graph", pos=None, iterations=100):
    if pos is None:
        pos = forceatlas2.forceatlas2_networkx_layout(g.subgraph(nodelist), pos=None, iterations=iterations)
    if node_labels is not None and node_labels.isna().any():
        node_labels.fillna("nan", inplace=True)

    node_x, node_y = zip(*[(pos[node][0], pos[node][1])
                           for node in nodelist])
    edge_x, edge_y, edge_data = zip(*[([pos[edge[0]][0], pos[edge[1]][0], None],
                                       [pos[edge[0]][1], pos[edge[1]][1], None],
                                       edge[2])
                                      for edge in g.subgraph(nodelist).edges(data=True)])
    edge_data = pd.DataFrame(edge_data)

    fig = px.scatter(x=node_x, y=node_y,
                     hover_name=nodelist,
                     symbol=node_labels if node_labels is not None else None,
                     title=title)
    fig.add_scatter(edge_data, x=edge_x, y=edge_y,
                    mode='lines', line=dict(width=1),
                    color=edge_data[edge_label] if edge_label else 'rgb(210,210,210)',
                    hoverinfo='none')

    return fig
