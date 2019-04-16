import matplotlib.pyplot as plt
import numpy as np
from scipy.sparse import coo_matrix


def bar_chart(results: dict, measures, title=None, bar_width=0.08, loc="best"):
    methods = list(results.keys())
    y_pos = np.arange(len(measures))

    if type(measures) == str:
        performances = [results[method] for method in methods]

        plt.bar(y_pos, performances, align='center', alpha=0.5)
        plt.xticks(y_pos, methods)
        plt.ylabel(measures)

    elif type(measures) == list:
        n_groups = len(methods)
        performances = {}
        fig, ax = plt.subplots(dpi=300)
        index = np.arange(n_groups)

        color_dict = {"LINE": "b", "HOPE": "c", "SDNE": "y", "node2vec": "g", "BioVec": "m", "rna2rna": "r",
                      "siamese": "r",
                      "Databases": "k"}
        opacity = 0.8

        for method in methods:
            performances[method] = []
            for measure in measures:
                performances[method].append(results[method][measure])

        for idx, method in enumerate(methods):
            plt.bar(y_pos + idx * bar_width, performances[method], bar_width,
                    alpha=opacity,
                    color=color_dict[method],
                    label=method.replace("test_", ""))
        # plt.xlabel('Methods')
        plt.ylabel('Scores')
        plt.xticks(y_pos + bar_width * (n_groups / 2), measures)
        plt.legend(loc=loc)

    plt.tight_layout()
    plt.title(title)
    plt.show()


def matrix_heatmap(matrix, figsize=(12,12), cmap='gray', **kwargs):
    # Scatter plot of the graph adjacency matrix

    fig = plt.figure(figsize=figsize)
    ax = fig.add_subplot(111)

    if np.isnan(matrix).any():
        matrix = np.nan_to_num(matrix)

    cax = ax.matshow(matrix, cmap=cmap, **kwargs)
    fig.colorbar(cax)

def plot_coo_matrix(m):
    if not isinstance(m, coo_matrix):
        m = coo_matrix(m)
    fig = plt.figure(figsize=(15,15))
    ax = fig.add_subplot(111)
    ax.plot(m.col, m.row, 's', ms=1)
    ax.set_aspect('equal')
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.invert_yaxis()
    ax.set_aspect('equal')
    ax.set_xticks([])
    ax.set_yticks([])
    return ax

