import networkx as nx
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go


# from fa2 import ForceAtlas2
#
# forceatlas2 = ForceAtlas2(
#     # Behavior alternatives
#     outboundAttractionDistribution=True,  # Dissuade hubs
#     linLogMode=False,  # NOT IMPLEMENTED
#     adjustSizes=False,  # Prevent overlap (NOT IMPLEMENTED)
#     edgeWeightInfluence=1.0,
#     # Performance
#     jitterTolerance=1.0,  # Tolerance
#     barnesHutOptimize=True,
#     barnesHutTheta=1.2,
#     multiThreaded=False,
#     # Tuning
#     scalingRatio=2.0,
#     strongGravityMode=True,
#     gravity=1.0,
#     # Log
#     verbose=False)

def node_degree_viz(node_degrees, x_label, y_label, width=500, height=90):
    fig = go.Figure(data=go.Heatmap(z=node_degrees.applymap(lambda x: np.log10(x + 1)),
                                    x=node_degrees.columns, y=node_degrees.index, colorscale="Greys"),
                    layout=go.Layout(
                        xaxis=dict(title=x_label),
                        yaxis=dict(title=y_label),
                        width=width,
                        height=height,
                        margin=dict(l=5, r=5, b=5, t=5, pad=5),
                        font=dict(size=12, ),
                        # xaxis_nticks=36
                    ))
    return fig

def graph_viz(g: nx.Graph,
              nodelist: list, node_symbol: pd.Series = None, node_color: pd.Series = None,
              edge_label: str = None, max_edges=10000,
              title=None, width=1000, height=800,
              pos=None, iterations=100, showlegend=True, **kwargs):
    if pos is None:
        pos = forceatlas2.forceatlas2_networkx_layout(g.subgraph(nodelist), pos=None, iterations=iterations)

    # Nodes data
    node_symbol = process_labels(node_symbol)
    node_color = process_labels(node_color)

    node_x, node_y = zip(*[(pos[node][0], pos[node][1])
                           for node in nodelist])

    if node_color is not None and node_color.str.contains("#").any():
        express_mode = False
    else:
        express_mode = True

    if express_mode:
        fig = px.scatter(x=node_x, y=node_y,
                         hover_name=nodelist,
                         symbol=node_symbol if node_symbol is not None else None,
                         color=node_color if node_color is not None else None, **kwargs)
    else:
        fig = go.Figure()
        fig.add_scatter3d(x=node_x, y=node_y,
                          mode='markers',
                          text=nodelist,
                          marker=dict(color=node_color,
                                      size=5,
                                      ),
                          **kwargs)

    # Edges data
    edges = list(g.subgraph(nodelist).edges(data=True if edge_label else False))
    # Samples only certain edges
    if max_edges and len(edges) > max_edges:
        np.random.shuffle(edges)
        edges = edges[:max_edges]

    if edge_label:
        plot_edge_w_labels(fig, edges, edge_label, pos, plot3d=False)
    else:
        plot_edges(fig, edges, pos, plot3d=False)

    configure_layout(fig, height, showlegend, title, width)

    return fig


def graph_viz3d(g: nx.Graph,
                nodelist: list, node_symbol=None, node_color=None, nodeline_color=None, node_text=None,
                edge_label: str = None, max_edges=10000,
                title=None, width=1000, height=800,
                pos=None, showlegend=True, **kwargs):
    """

    :param g: 
    :param nodelist: 
    :param node_symbol: The symbol of a node.
    :param node_color (pd.Series): The color of each node markers. A pd.Series indexed by nodelist with values of either categorical string, hex color, or a tuple of RGB colors.
    :param nodeline_color (pd.Series, Optional): The outline color of each node.
    :param node_text: 
    :param edge_label: 
    :param max_edges: 
    :param title: 
    :param width: 
    :param height: 
    :param pos: 
    :param showlegend: 
    :param kwargs: 
    :return: 
    """
    if pos is None:
        raise Exception("Must provide pos as dict, i.e. {<node>:<3d coordinates>}")

    # Nodes data
    node_symbol = process_labels(node_symbol)
    node_color = process_labels(node_color)

    node_x, node_y, node_z = zip(*[(pos[node][0], pos[node][1], pos[node][2])
                                   for node in nodelist])

    # Select express mode only if node_color is categorical strings
    if isinstance(node_color, pd.Series) and node_color.str.contains("#").any():
        # if node_color contains hex colors (e.g. #aaaaa)
        express_mode = False
    elif isinstance(node_color, pd.Series) and node_color.map(lambda x: isinstance(x, tuple) and len(x) == 3).any():
        # if node_color are a tuple of RGB values
        express_mode = False
        node_color = node_color.map(lambda rgb: [val / (max(rgb)) for val in rgb])  # change values
        node_color = node_color.map(
            lambda rgb: f"rgba({int(255 * rgb[0])}, {int(255 * rgb[1])}, {int(255 * rgb[2])}, 1.0)")
    else:
        express_mode = True

    if express_mode:
        fig = px.scatter_3d(x=node_x, y=node_y, z=node_z, size_max=5,
                            hover_name=nodelist,
                            symbol=node_symbol if node_symbol is not None else None,
                            color=node_color if node_color is not None else None,
                            # text=node_text,
                            color_continuous_scale='HSV',
                            **kwargs)
    else:
        fig = go.Figure()
        fig.add_scatter3d(x=node_x, y=node_y, z=node_z,
                          mode='markers',
                          text=node_text,
                          marker=dict(color=node_color,
                                      size=5,
                                      line=dict(color=nodeline_color, width=50) if nodeline_color is not None else None,
                                      ),
                          **kwargs)

    # Edges data
    edges = list(g.subgraph(nodelist).edges(data=True if edge_label else False))

    # Samples only certain edges
    if max_edges and len(edges) > max_edges:
        np.random.shuffle(edges)
        edges = edges[:max_edges]
    print("nodes", len(node_x), "edges", len(edges))

    if edge_label:
        plot_edge_w_labels(fig, edges, edge_label, pos, plot3d=True)
    else:
        plot_edges(fig, edges, pos, plot3d=True)

    configure_layout(fig, height, showlegend, title, width)

    return fig


def configure_layout(fig, height, showlegend, title, width):
    # Figure
    axis = dict(showline=False,  # hide axis line, grid, ticklabels and  title
                zeroline=False,
                showgrid=False,
                showticklabels=False,
                title=''
                )
    fig.update_layout(
        title=title,
        showlegend=showlegend,
        # legend=dict(autosize=True, width=100),
        legend_orientation="v",
        autosize=True,
        width=width,
        height=height,
        margin=dict(
            l=5,
            r=5,
            b=5,
            t=5,
            pad=5
        ),
        xaxis=axis,
        yaxis=axis
    )


def plot_edges(fig, edges, pos, plot3d=True):
    Xed, Yed, Zed = [], [], []
    for edge in edges:
        Xed += [pos[edge[0]][0], pos[edge[1]][0], None]
        Yed += [pos[edge[0]][1], pos[edge[1]][1], None]
        if plot3d: Zed += [pos[edge[0]][2], pos[edge[1]][2], None]

    if plot3d:
        fig.add_scatter3d(x=Xed, y=Yed, z=Zed,
                          mode='lines',
                          name='edges, ' + str(len(Xed)),
                          line=dict(
                              # color=hash_color(edge_data[edge_label]) if edge_label else 'rgb(210,210,210)',
                              color='rgb(50,50,50)',
                              width=0.25, ),
                          # showlegend=True,
                          hoverinfo='none')
    else:
        fig.add_scatter(x=Xed, y=Yed,
                        mode='lines',
                        name='edges, ' + str(len(Xed)),
                        line=dict(
                            # color=hash_color(edge_data[edge_label]) if edge_label else 'rgb(210,210,210)',
                            color='rgb(50,50,50)',
                            width=0.25, ),
                        # showlegend=True,
                        hoverinfo='none')


def plot_edge_w_labels(fig, edges, edge_label, pos, plot3d=True):
    Xed_by_label, Yed_by_label, Zed_by_label = {}, {}, {}
    for edge in edges:
        label = edge[2][edge_label]
        Xed_by_label.setdefault(label, []).extend([pos[edge[0]][0], pos[edge[1]][0], None])
        Yed_by_label.setdefault(label, []).extend([pos[edge[0]][1], pos[edge[1]][1], None])
        if plot3d: Zed_by_label.setdefault(label, []).extend([pos[edge[0]][2], pos[edge[1]][2], None])

    if plot3d:
        for label in Xed_by_label:
            fig.add_scatter3d(x=Xed_by_label[label], y=Yed_by_label[label], z=Zed_by_label[label],
                              mode='lines',
                              name=label + ", size:" + str(len(edges)),
                              line=dict(
                                  color=label,
                                  colorscale="Viridis",
                                  width=0.5, ),
                              # showlegend=True,
                              hoverinfo='none')
    else:
        for label in Xed_by_label:
            fig.add_scatter(x=Xed_by_label[label], y=Yed_by_label[label],
                            mode='lines',
                            name=label + ", " + str(len(Xed_by_label[label])),
                            line=dict(
                                # color=hash_color([label])[0],
                                width=0.5, ),
                            # showlegend=True,
                            hoverinfo='none')


def process_labels(labels: pd.Series, delim="|"):
    if labels is not None and type(labels) is pd.Series:
        if labels.isna().any():
            labels.fillna("None", inplace=True)
        if labels.dtype == "object" and labels.str.contains(delim).any():
            labels = labels.str.split(delim, expand=True)[0].astype(str)
    return labels
