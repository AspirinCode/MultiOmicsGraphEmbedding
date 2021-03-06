import matplotlib.cm as cm
import matplotlib.colors as colors
import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
from scipy.sparse import csgraph
from sklearn.decomposition import TruncatedSVD


def visualize_embedding(embedding, network, nodelist=None, edgelist=[], node_pos=None, top_k=0, test_nodes=None,
                        node_label="locus_type", cmap="gist_ncar", figsize=(20, 15), dpi=150, label_size=9, **kwargs):
    if nodelist is None:
        nodelist = embedding.node_list
    if node_pos is None:
        node_pos = embedding.get_tsne_node_pos()

    if (edgelist is None or len(edgelist) == 0) and top_k > 0:
        edgelist = embedding.get_top_k_predicted_edges(edge_type="d", top_k=top_k,
                                                       node_list=nodelist, training_network=network)
        if len(edgelist[0]) > 2:  # Has weight component
            edge_weights = [w for u,v,w in edgelist]
            edgelist = [(u, v) for u,v,w in edgelist]
            kwargs["edge_color"] = edge_weights
            kwargs["edge_cmap"] = cm.get_cmap("binary")
            kwargs["edge_vmin"] = 0.0
            kwargs["edge_vmax"] = 1.0
            kwargs["style"] = "dashed"

    if test_nodes is not None:
        labels_dict = {node:node for node in test_nodes if node in nodelist}
        kwargs["labels"] = labels_dict
        kwargs["with_labels"] = True
        kwargs["font_size"] = 6

    if node_label is not None:
        cmap, node_colormap, node_colors, node_labels = get_node_colormap(cmap, network, node_label, nodelist)

        plot_embedding2D(node_pos, node_list=embedding.node_list, node_colors=node_colors,
                         legend=True, node_labels=node_labels, node_colormap=node_colormap, legend_size=20,
                         di_graph=network.G.subgraph(nodelist), cmap=cmap, nodelist=nodelist,
                         plot_nodes_only=False, edgelist=edgelist,
                         figsize=figsize, dpi=dpi, label_size=label_size, **kwargs)
    else:
        plot_embedding2D(node_pos, node_list=embedding.node_list,
                         di_graph=network.G.subgraph(nodelist), cmap=cmap, nodelist=nodelist,
                         plot_nodes_only=False, edgelist=edgelist,
                         figsize=figsize, dpi=dpi, label_size=label_size, **kwargs)


def plot_embedding2D(node_pos, node_list, di_graph=None,
                     legend=True, node_labels=None, node_colormap=None, legend_size=10,
                     node_colors=None, plot_nodes_only=True,
                     cmap="viridis", file_name=None, figsize=(17, 15), dpi=150, label_size=9, **kwargs):
    fig = plt.figure(figsize=figsize, dpi=dpi)
    ax = fig.add_subplot(1, 1, 1)

    if legend and node_labels is not None and type(
            node_labels) != list and node_colormap is not None and node_colors is not None:
        scalarMap = cm.ScalarMappable(norm=colors.Normalize(vmin=0.0, vmax=1.0, clip=False), cmap=cmap)
        top_node_labels = node_labels.value_counts()[:legend_size].index  # Get top k most popular legends labels
        for label in top_node_labels:
            ax.plot([0], [0],
                    color=scalarMap.to_rgba(node_colormap[label], norm=False),
                    label=label, linewidth=4) if label in node_colormap.keys() else None

    if "width" not in kwargs: kwargs["width"] = 0.1
    if "node_size" not in kwargs: kwargs["node_size"] = 25
    if "with_labels" not in kwargs or "labels" not in kwargs: kwargs["with_labels"] = False
    if "font_size" not in kwargs: kwargs["font_size"] = 5
    if "node_size" in kwargs and kwargs["node_size"] == "centrality":
        kwargs["node_size"] = node_centrality(network=di_graph)

    if di_graph is None:
        # Plot using plt scatter
        plt.scatter(node_pos[:, 0], node_pos[:, 1], c=node_colors, cmap=cmap)
    else:
        # Plot using networkx with edge structure
        if type(node_pos) is not dict:
            node_num, embedding_dimension = node_pos.shape
            assert node_num == len(node_list), "node_pos {}".format(node_pos.shape)
            if (embedding_dimension > 2):
                print("Embedding dimension greater than 2, use tSNE to reduce it to 2")
                laplacian = csgraph.laplacian(node_pos, normed=True)
                node_pos = TruncatedSVD(n_components=2).fit_transform(laplacian)

            pos = {}
            for i, node in enumerate(node_list):
                pos[node] = node_pos[i, :]
        else:
            pos = node_pos

        if kwargs["with_labels"]:
            for node in di_graph.nodes():
                x, y = pos[node]
                plt.text(x, y + 0.02, s=node, fontsize=label_size,
                         # bbox=dict(facecolor='gray', alpha=0.2),
                         horizontalalignment='center')

        if plot_nodes_only:
            nx.draw_networkx_nodes(di_graph, pos=pos,
                                   node_color=node_colors, cmap=cmap, ax=ax,
                                   alpha=0.8, **kwargs)
        else:
            nx.draw_networkx(di_graph, pos=pos,
                             node_color=node_colors, cmap=cmap, ax=ax,
                             arrows=True,
                             alpha=0.8, **kwargs)

        if legend:
            plt.legend(loc='best')
        plt.axis('off')

    if file_name:
        plt.savefig('%s_vis.pdf' % (file_name), dpi=150, format='pdf', bbox_inches='tight')
        plt.figure()


def get_node_colormap(cmap, network, node_label, nodelist):
    annotations = network.annotations
    if type(node_label) == list:
        node_labels = node_label
        assert len(node_label) == len(nodelist)
        sorted_node_labels = sorted(set(node_labels), reverse=True)
        colors = np.linspace(0, 1, len(sorted_node_labels))
        node_colormap = {f: colors[sorted_node_labels.index(f)] for f in set(node_labels)}
        node_colors = [node_colormap[n] if n in node_colormap.keys() else None for n in node_labels]

    elif annotations[node_label].dtype == "object":
        node_labels = annotations.loc[nodelist][node_label].str.split("|", expand=True)[0].astype(str)
        sorted_node_labels = sorted(node_labels.unique(), reverse=True)
        colors = np.linspace(0, 1, len(sorted_node_labels))
        node_colormap = {f: colors[sorted_node_labels.index(f)] for f in node_labels.unique()}
        node_colors = [node_colormap[n] if n in node_colormap.keys() else None for n in node_labels]

    elif annotations[node_label].dtype == "float":

        node_labels = annotations.loc[nodelist][node_label].values
        cmap = "gray"
        node_colormap = None
        node_colors = [n / node_labels.max() for n in node_labels]
    return cmap, node_colormap, node_colors, node_labels


def get_node_color(node_labels, n=256, index=False):
    if not index:
        return [float(hash(s) % n) / n for s in node_labels]
    else:
        return [hash(s) % n for s in node_labels]


def node_centrality(network):
    centrality = \
        nx.algorithms.centrality.degree_centrality(network)
    # first element are nodes again
    _, nodes_centrality = zip(*sorted(centrality.items()))
    max_centrality = max(nodes_centrality)
    nodes_centrality = [40 + 100 * t / max_centrality for t in nodes_centrality]
    return nodes_centrality

def get_edges_specs(_network, _node_pos):
    d = {'xs': [],
         'ys': [],
         'alphas': [],
         'name': []}
    calc_alpha = lambda h: 0.1 + 0.9 * h
    for u, v, data in _network.edges(data=True):
        d['xs'].append([_node_pos[u][0], _node_pos[v][0]])
        d['ys'].append([_node_pos[u][1], _node_pos[v][1]])
        d['alphas'].append(calc_alpha(data['weight']) if "weight" in data else 0.3)
        d['name'].append(str(u) + ' - ' + str(v))
    return d
