from abc import ABCMeta

import numpy as np

from MulticoreTSNE import MulticoreTSNE as TSNE
from sklearn.cluster import KMeans
from sklearn.decomposition import PCA
from sklearn.metrics import pairwise_distances

from moge.evaluation.link_prediction import largest_indices
from moge.evaluation.utils import get_scalefree_fit_score
from moge.visualization.plot_data import matrix_heatmap


class StaticGraphEmbedding:
    __metaclass__ = ABCMeta

    def __init__(self, d):
        '''Initialize the Embedding class

        Args:
            d: dimension of embedding
        '''
        pass

    def get_method_name(self):
        ''' Returns the name for the embedding method

        Return:
            The name of embedding
        '''
        return ''

    def get_method_summary(self):
        ''' Returns the summary for the embedding include method name and paramater setting

        Return:
            A summary string of the method
        '''
        return ''

    def learn_embedding(self, graph):
        '''Learning the graph embedding from the adjcency matrix.

        Args:
            graph: the graph to embed in networkx DiGraph format
        '''
        pass

    def get_embedding(self):
        ''' Returns the learnt embedding

        Return:
            A numpy array of size #nodes * d
        '''
        pass

    def get_node_list(self):
        return self.node_list

    def get_edge_weight(self, i, j):
        '''Compute the weight for edge between node i and node j

        Args:
            i, j: two node id in the graph for embedding
        Returns:
            A single number represent the weight of edge between node i and node j

        '''
        pass

    def is_trained(self):
        if hasattr(self, "_X"):
            return True
        else:
            return False

    def get_reconstructed_adj(self, edge_type=None):
        '''Compute the adjacency matrix from the learned embedding

        Returns:
            A numpy array of size #nodes * #nodes containing the reconstructed adjacency matrix.
        '''
        pass

class ImportedGraphEmbedding(StaticGraphEmbedding):
    __metaclass__ = ABCMeta

    def __init__(self, d, method_name="ImportedGraphEmbedding"):
        '''Initialize the Embedding class

        Args:
            d: dimension of embedding
        '''
        self._d = d
        self._method_name = method_name

    def get_method_name(self):
        ''' Returns the name for the embedding method

        Return:
            The name of embedding
        '''
        return self._method_name

    def get_method_summary(self):
        ''' Returns the summary for the embedding include method name and paramater setting

        Return:
            A summary string of the method
        '''
        return self._method_name + str(self._d)

    def learn_embedding(self, graph):
        '''Learning the graph embedding from the adjcency matrix.

        Args:
            graph: the graph to embed in networkx DiGraph format
        '''
        pass

    def get_embedding(self, node_list=None):
        ''' Returns the learnt embedding

        Return:
            A numpy array of size #nodes * d
        '''
        if node_list is None:
            return self._X
        elif set(node_list) <= set(self.node_list):
            idx = [self.node_list.index(node) for node in node_list]
            return self._X[idx, :]
        else:
            raise Exception("node_list contains a node not included in trained embeddings")

    def get_edge_weight(self, i, j):
        '''Compute the weight for edge between node i and node j

        Args:
            i, j: two node id in the graph for embedding
        Returns:
            A single number represent the weight of edge between node i and node j

        '''
        pass

    def import_embedding(self, file, node_list):
        self.imported = True
        with open(file, "r") as fin:
            node_num, size = [int(x) for x in fin.readline().strip().split()]
            vectors = {}
            self.node_list = []

            # Read embedding file
            while 1:
                l = fin.readline()
                if l == '':
                    break
                vec = l.strip().split(' ')
                assert len(vec) == size + 1
                vectors[vec[0]] = [float(x) for x in vec[1:]]
            fin.close()
            assert len(vectors) == node_num

            if self.get_method_name() == "rna2rna":
                self._d = size
                self.embedding_s = []
                self.embedding_t = []

                for node in node_list:
                    if node in vectors.keys():
                        self.embedding_s.append(vectors[node][0 : int(self._d/2)])
                        self.embedding_t.append(vectors[node][int(self._d/2) : int(self._d)])
                        self.node_list.append(node)

                self.embedding_s = np.array(self.embedding_s)
                self.embedding_t = np.array(self.embedding_t)
                self._X = np.concatenate([self.embedding_s, self.embedding_t], axis=1)

            else:
                self._d = size
                self._X = []
                for node in node_list:
                    if node in vectors.keys():
                        self._X.append(vectors[node])
                        self.node_list.append(node)
                self._X = np.array(self._X)

        print(self.get_method_name(), "imported", self._X.shape)


    def get_reconstructed_adj(self, edge_type=None, node_l=None, ):
        '''Compute the adjacency matrix from the learned embedding

        Returns:
            A numpy array of size #nodes * #nodes containing the reconstructed adjacency matrix.
        '''
        if hasattr(self, "reconstructed_adj"):
            reconstructed_adj = self.reconstructed_adj

        elif self._method_name == "LINE":
            reconstructed_adj = np.divide(1, 1 + np.exp(-np.matmul(self._X, self._X.T)))

        elif self._method_name == "node2vec":
            reconstructed_adj = self.softmax(np.dot(self._X, self._X.T))

        elif self._method_name == "BioVec":
            reconstructed_adj = self.softmax(np.dot(self._X, self._X.T)) # TODO Double check paper
            reconstructed_adj = reconstructed_adj.T  # Transpose matrix since there's a bug

        elif self._method_name == "rna2rna":
            reconstructed_adj = pairwise_distances(X=self._X[:, 0:int(self._d / 2)],
                                                   Y=self._X[:, int(self._d / 2):self._d],
                                                   metric="euclidean", n_jobs=-2)
            reconstructed_adj = np.exp(-2.0 * reconstructed_adj)
            reconstructed_adj = reconstructed_adj.T  # Transpose matrix since there's a bug

        elif self._method_name == "HOPE":
            reconstructed_adj = np.matmul(self._X[:, 0:int(self._d / 2)], self._X[:, int(self._d / 2):self._d].T)
            if not ((reconstructed_adj >= 0).all() and (reconstructed_adj <= 1).all()):
                reconstructed_adj = np.interp(reconstructed_adj, (reconstructed_adj.min(), reconstructed_adj.max()), (0, 1))

        elif self._method_name == "SDNE":
            reconstructed_adj = pairwise_distances(X=self._X, Y=self._X, metric="euclidean", n_jobs=-2)
            reconstructed_adj = np.exp(-2.0 * reconstructed_adj)

        else:
            raise Exception("Method" + self.get_method_name() + "not supported")



        if node_l is None or node_l == self.node_list:
            self.reconstructed_adj = reconstructed_adj
            return reconstructed_adj
        elif set(node_l) < set(self.node_list):
            idx = [self.node_list.index(node) for node in node_l]
            return reconstructed_adj[idx, :][:, idx]
        else:
            raise Exception("A node in node_l is not in self.node_list.")

    def softmax(self, X):
        exps = np.exp(X)
        return exps/np.sum(exps, axis=0)

    def get_top_k_predicted_edges(self, edge_type, top_k, node_list=None, training_network=None):
        nodes = self.node_list
        if node_list is not None:
            nodes = [n for n in nodes if n in node_list]

        estimated_adj = self.get_reconstructed_adj(edge_type=edge_type, node_l=nodes)
        np.fill_diagonal(estimated_adj, 0)

        if training_network is not None:
            training_adj = training_network.get_adjacency_matrix(edge_types=[edge_type], node_list=nodes)
            assert estimated_adj.shape == training_adj.shape
            rows, cols = training_adj.nonzero()
            estimated_adj[rows, cols] = 0

        top_k_indices = largest_indices(estimated_adj, top_k, smallest=False)
        top_k_pred_edges = [(nodes[x[0]], nodes[x[1]], estimated_adj[x[0], x[1]]) for x in zip(*top_k_indices)]

        return top_k_pred_edges

    def get_bipartite_adj(self, node_list_A, node_list_B, edge_type=None):
        nodes_A = [n for n in self.node_list if n in node_list_A]
        nodes_B = [n for n in self.node_list if n in node_list_B]
        nodes = list(set(nodes_A) | set(nodes_B))

        estimated_adj = self.get_reconstructed_adj(node_l=nodes)
        assert len(nodes) == estimated_adj.shape[0]
        nodes_A_idx = [nodes.index(node) for node in nodes_A if node in nodes]
        nodes_B_idx = [nodes.index(node) for node in nodes_B if node in nodes]
        bipartite_adj = estimated_adj[nodes_A_idx, :][:, nodes_B_idx]
        return bipartite_adj

    def get_scalefree_fit_score(self, node_list_A, node_list_B=None, k_power=1, plot=False):
        if node_list_B is not None:
            adj = self.get_bipartite_adj(node_list_A, node_list_B)
        else:
            adj = self.get_reconstructed_adj(node_l=node_list_A)

        degrees_A = np.sum(adj, axis=1)

        return get_scalefree_fit_score(degrees_A, k_power, plot)

    def predict(self, X):
        """
        Bulk predict whether an edge exists between a pair of nodes, provided as a collection in X.
        :param X: [n_pairs, 2], where each element is the string name of a node
        :return y_pred: [n_pairs]
        """
        estimated_adj = self.get_reconstructed_adj()
        node_set = set(self.node_list)
        node_set_in_X = set(node for pair in X for node in pair)

        if node_set_in_X <= node_set:
            X_u_inx = [self.node_list.index(u) for u, v in X if u in node_set and v in node_set]
            X_v_inx = [self.node_list.index(v) for u, v in X if u in node_set and v in node_set]
            y_pred = estimated_adj[X_u_inx, X_v_inx]
        else:
            y_pred = []
            for u, v in X:
                if u in node_set and v in node_set:
                    y_pred.append(estimated_adj[self.node_list.index(u), self.node_list.index(v)])
                else:
                    y_pred.append(0.0)

        y_pred = np.array(y_pred, dtype=np.float).reshape((-1, 1))
        assert y_pred.shape[0] == X.shape[0]
        return y_pred

    def process_tsne_node_pos(self, perplexity=80):
        embs = self.get_embedding()
        embs_pca = PCA(n_components=2).fit_transform(embs)
        self.node_pos = TSNE(init=embs_pca, perplexity=perplexity, n_jobs=8).fit_transform(embs)

    def get_tsne_node_pos(self):
        if hasattr(self, "node_pos"):
            return self.node_pos
        else:
            self.process_tsne_node_pos()
            return self.node_pos

    def predict_cluster(self, n_clusters=8, node_list=None, n_jobs=-2, return_clusters=False):
        embs = self.get_embedding()
        kmeans = KMeans(n_clusters, n_jobs=n_jobs)
        y_pred = kmeans.fit_predict(embs)

        if node_list is not None and set(node_list) <= set(self.node_list) and node_list != self.node_list:
            idx = [self.node_list.index(node) for node in node_list]
            y_pred = np.array(y_pred)[idx]

        return y_pred

