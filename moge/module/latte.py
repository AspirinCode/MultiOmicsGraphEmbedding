import copy, random
import numpy as np
import torch
from torch import nn as nn
from torch.nn import Parameter
import torch.nn.functional as F
from torch_geometric.nn import MessagePassing
from torch_geometric.nn.inits import glorot
from torch_geometric.utils import remove_self_loops, add_self_loops, softmax
import torch_sparse
from torch_sparse.tensor import SparseTensor
from torch_sparse.matmul import matmul

import pytorch_lightning as pl


class LATTE(nn.Module):
    def __init__(self, embedding_dim: int, t_order: int, num_nodes_dict: dict, node_attr_shape: dict, metapaths: list,
                 use_proximity_loss=True):
        super(LATTE, self).__init__()
        self.metapaths = metapaths
        self.node_types = list(num_nodes_dict.keys())
        self.embedding_dim = embedding_dim * t_order
        self.use_proximity_loss = use_proximity_loss
        self.t_order = t_order

        layers = []
        t_order_metapaths = copy.copy(metapaths)
        for t in range(t_order):
            if t == 0:
                layers.append(
                    LATTELayer(embedding_dim=embedding_dim, num_nodes_dict=num_nodes_dict,
                               node_attr_shape=node_attr_shape,
                               metapaths=t_order_metapaths, first=True))
            else:
                layers.append(
                    LATTELayer(embedding_dim=embedding_dim, num_nodes_dict=num_nodes_dict,
                               node_attr_shape=node_attr_shape,
                               metapaths=t_order_metapaths, first=False))
            t_order_metapaths = self.join_metapaths(t_order_metapaths, metapaths)

        self.layers = nn.ModuleList(layers)

    def join_metapaths(self, metapath_A, metapath_B):
        metapaths = []
        for relation_a in metapath_A:
            for relation_b in metapath_B:
                if relation_a[-1] == relation_b[0]:
                    new_relation = relation_a + relation_b[1:]
                    metapaths.append(new_relation)
        return metapaths

    def join_edge_indexes(self, edge_index_dict_A, edge_index_dict_B, x_index_dict):
        output_dict = {}
        for metapath_a, edge_index_a in edge_index_dict_A.items():
            if edge_index_a is None or (isinstance(edge_index_a, tuple) and edge_index_a[0] is None): continue

            # Preprocess edge_index_a
            if isinstance(edge_index_a, tuple):
                edge_index_a = edge_index_a[0]
                values_a = edge_index_a[1]
            else:
                values_a = torch.ones_like(edge_index_a[0].detach(), dtype=torch.float)
            if edge_index_a.size(1) <= 5: continue
            if values_a.dtype != torch.int:
                values_a = values_a.to(torch.float)

            for metapath_b, edge_index_b in edge_index_dict_B.items():
                if edge_index_b is None or (isinstance(edge_index_b, tuple) and edge_index_b[0] is None): continue

                if metapath_a[-1] == metapath_b[0]:
                    metapath_join = metapath_a + metapath_b[1:]

                    # Preprocess edge_index_a
                    if isinstance(edge_index_b, tuple):
                        edge_index_b = edge_index_b[0]
                        values_b = edge_index_b[1]
                    else:
                        values_b = torch.ones_like(edge_index_b[0].detach(), dtype=torch.float)
                    if edge_index_b.size(1) <= 5: continue
                    if values_b.dtype != torch.int:
                        values_b = values_b.to(torch.float)

                    try:
                        new_edge_index = torch_sparse.spspmm(indexA=edge_index_a,
                                                             valueA=values_a,
                                                             indexB=edge_index_b,
                                                             valueB=values_b,
                                                             m=x_index_dict[metapath_a[0]].size(0),
                                                             k=x_index_dict[metapath_a[-1]].size(0),
                                                             n=x_index_dict[metapath_b[-1]].size(0),
                                                             coalesced=True)
                    except Exception as e:
                        print(metapath_a, metapath_b)
                        print("sizes", x_index_dict[metapath_a[0]].size(0),
                              x_index_dict[metapath_a[-1]].size(0), x_index_dict[metapath_b[-1]].size(0), )
                        print("edge_index_a", edge_index_a.size(1), edge_index_a.dtype)
                        print("values_a", values_a.size(), values_a.dtype)
                        print("edge_index_b", edge_index_b.size(1), edge_index_b.dtype)
                        print("values_b", values_b.size(), values_b.dtype)
                        print(e)

                    if new_edge_index[0].size(1) <= 5: continue
                    # print("new_edge_index", new_edge_index[0].shape)
                    output_dict[metapath_join] = new_edge_index
        return output_dict

    def forward(self, x_dict, x_index_dict, edge_index_dict):
        proximity_loss = torch.tensor(0.0, device=x_index_dict[
            self.node_types[0]].device) if self.use_proximity_loss else None

        h_all_dict = {node_type: [] for node_type in self.node_types}

        for t in range(self.t_order):
            if t == 0:
                h_dict, t_proximity_loss = self.layers[t].forward(x_dict=x_dict, x_index_dict=x_index_dict,
                                                                  edge_index_dict=edge_index_dict)
                if self.t_order > 1:
                    t_order_edge_index_dict = self.join_edge_indexes(edge_index_dict, edge_index_dict, x_index_dict)
            else:
                h_dict, t_proximity_loss = self.layers[t].forward(x_dict=x_dict,
                                                                  x_index_dict=x_index_dict,
                                                                  edge_index_dict=t_order_edge_index_dict,
                                                                  h1_dict=h_dict)
                t_order_edge_index_dict = self.join_edge_indexes(t_order_edge_index_dict, edge_index_dict, x_index_dict)

            # print("t_order_edge_index_dict", {k:v for k,v in t_order_edge_index_dict.items()})

            if self.t_order > 1:
                for node_type in self.node_types:
                    h_all_dict[node_type].append(h_dict[node_type])
            if self.use_proximity_loss:
                proximity_loss += t_proximity_loss

        if self.t_order > 1:
            embedding_output = {node_type: torch.cat(h_t_list, dim=1) for node_type, h_t_list in h_all_dict.items()}
        else:
            embedding_output = h_dict
        return embedding_output, proximity_loss


class LATTELayer(MessagePassing, pl.LightningModule):
    def __init__(self, embedding_dim: int, num_nodes_dict: {str: int}, node_attr_shape: {str: int}, metapaths: list,
                 use_proximity_loss=True, neg_sampling_ratio=1.0, first=True) -> None:
        super(LATTELayer, self).__init__(aggr="add", flow="target_to_source", node_dim=0)
        self.first = first
        self.node_types = list(num_nodes_dict.keys())
        self.metapaths = list(metapaths)
        self.num_nodes_dict = num_nodes_dict
        self.embedding_dim = embedding_dim
        self.use_proximity_loss = use_proximity_loss
        self.neg_sampling_ratio = neg_sampling_ratio

        # Computes beta
        self.conv = torch.nn.ModuleDict(
            {node_type: torch.nn.Conv1d(
                in_channels=node_attr_shape[
                    node_type] if self.first and node_type in node_attr_shape else self.embedding_dim,
                out_channels=self.get_relation_size(node_type),
                kernel_size=1) \
                for node_type in self.node_types})  # W_phi.shape (H_-1 x F)

        self.linear = torch.nn.ModuleDict(
            {node_type: torch.nn.Linear(in_channels, embedding_dim, bias=False) \
             for node_type, in_channels in node_attr_shape.items()})  # W.shape (F x D_m)
        self.attn_l = torch.nn.ModuleList(
            [torch.nn.Linear(embedding_dim, 1, bias=True) for metapath in self.metapaths])
        self.attn_r = torch.nn.ModuleList(
            [torch.nn.Linear(embedding_dim, 1, bias=True) for metapath in self.metapaths])

        # If some node type are not attributed, assign embeddings for them
        non_attr_node_types = (num_nodes_dict.keys() - node_attr_shape.keys())
        if len(non_attr_node_types) > 0:
            self.embeddings = torch.nn.ModuleDict(
                {node_type: nn.Embedding(num_embeddings=self.num_nodes_dict[node_type], embedding_dim=embedding_dim) \
                 for node_type in non_attr_node_types}
            )
        self.reset_parameters()

    def get_head_relations(self, head_node_type) -> list:
        relations = [metapath for metapath in self.metapaths if metapath[0] == head_node_type]
        return relations

    def get_relation_size(self, node_type) -> int:
        relations = self.get_head_relations(node_type)
        return 1 + len(relations)

    def reset_parameters(self):
        for i, metapath in enumerate(self.metapaths):
            glorot(self.attn_l[i].weight)
            glorot(self.attn_r[i].weight)

        for node_type in self.linear:
            glorot(self.linear[node_type].weight)
        for node_type in self.conv:
            glorot(self.conv[node_type].weight)
        for node_type in self.embeddings:
            self.embeddings[node_type].reset_parameters()

    def __repr__(self):
        return '{}(linear={}, attn={}, embedding={})'.format(self.__class__.__name__,
                                                             {nodetype: linear.weight.shape for
                                                              nodetype, linear in self.linear.items()},
                                                             {metapath: attn.shape for metapath, attn in
                                                              self.attn_l.items()},
                                                             self.embedding_dim)

    def forward(self, x_dict, x_index_dict, edge_index_dict, h1_dict=None):
        """

        :param x_dict: a dict of node attributes indexed node_type
        :param x_index_dict: A dict of index values indexed by node_type in this mini-batch sampling
        :param edge_index_dict: Sparse adjacency matrices for each metapath relation. A dict of edge_index indexed by metapath
        :param h1_dict: Embeddings of the previous order. Default: None (if first order). A dict of edge_index indexed by metapath
        :return: output_emb, loss
        """
        # H_t = W_t * x
        h_dict = {}
        for node_type in self.node_types:
            if node_type in x_dict.keys():
                h_dict[node_type] = (self.linear[node_type](x_dict[node_type])).view(-1, self.embedding_dim)
            else:
                h_dict[node_type] = self.embeddings[node_type].weight[x_index_dict[node_type]]

        # Compute relations attention coefficients
        beta = {}
        for node_type in self.node_types:
            if node_type in x_dict and self.first:
                beta[node_type] = self.conv[node_type].forward(x_dict[node_type].unsqueeze(-1))
            elif not self.first:
                beta[node_type] = self.conv[node_type].forward(h1_dict[node_type].unsqueeze(-1))

            # Use self.embeddings when first layer and node_type is not attributed
            else:
                beta[node_type] = self.conv[node_type].forward(h_dict[node_type].unsqueeze(-1))
            beta[node_type] = torch.softmax(beta[node_type], dim=1)

        if not self.training:
            self._beta = {}
            for node_type in self.node_types:
                _beta = beta[node_type].mean(dim=0).squeeze(-1).cpu().numpy()
                self._beta[node_type] = {metapath: _beta[i] for i, metapath in
                                         enumerate(self.get_head_relations(node_type) + ["self"])}

        score_l, score_r = {}, {}

        # Compute node-level attention coefficients
        for i, metapath in enumerate(self.metapaths):
            head_type, tail_type = metapath[0], metapath[-1]
            if self.first:
                score_l[metapath] = self.attn_l[i].forward(h_dict[head_type]).sum(dim=-1)  # score_l = attn_l * W * x_1
            else:
                score_l[metapath] = self.attn_l[i].forward(h1_dict[head_type]).sum(dim=-1)  # score_l = attn_l * h_1

            score_r[metapath] = self.attn_r[i].forward(h_dict[tail_type]).sum(dim=-1)  # score_r = attn_r * W * x_1

        # For each metapath in a node_type, use GAT message passing to aggregate h_j neighbors
        emb_relation_agg = {}
        emb_output = {}
        for node_type in self.node_types:
            emb_relation_agg[node_type] = torch.zeros(
                size=(x_index_dict[node_type].size(0),  # X_m = (num_nodes, num_relations, embedding_dim)
                      self.get_relation_size(node_type),
                      self.embedding_dim),
                device=self.conv[node_type].weight.device)

            for i, metapath in enumerate(self.get_head_relations(node_type)):
                if metapath not in edge_index_dict or edge_index_dict[metapath] == None:
                    continue
                head_type, tail_type = metapath[0], metapath[-1]
                head_num_node, tail_num_node = len(x_index_dict[head_type]), len(x_index_dict[tail_type])

                if isinstance(edge_index_dict[metapath], tuple):
                    edge_index, _ = edge_index_dict[metapath]
                else:
                    edge_index = edge_index_dict[metapath]

                emb_relation_agg[head_type][:, i] = self.propagate(
                    edge_index,
                    size=(tail_num_node, head_num_node),
                    x=(h_dict[tail_type], h_dict[head_type]),
                    alpha=(score_r[metapath], score_l[metapath]))

            emb_relation_agg[head_type][:, -1] = h_dict[head_type]
            emb_output[node_type] = torch.matmul(emb_relation_agg[head_type].permute(0, 2, 1),
                                                 beta[head_type]).squeeze(-1)

        if self.use_proximity_loss:
            proximity_loss = self.proximity_loss(edge_index_dict, score_l, score_r, x_index_dict)
        else:
            proximity_loss = None

        return emb_output, proximity_loss

    def message(self, x_j, alpha_j, alpha_i, index, ptr, size_i):
        alpha = alpha_j if alpha_i is None else alpha_j + alpha_i
        # alpha = F.leaky_relu(alpha, self.negative_slope)
        alpha = softmax(alpha, index=index, num_nodes=size_i)
        # alpha = F.dropout(alpha, p=self.dropout, training=self.training)
        return x_j * alpha.unsqueeze(-1)

    @staticmethod
    def negative_sample(edge_index, M: int, N: int, num_neg_samples: int):
        num_neg_samples = min(num_neg_samples,
                              M * N - edge_index.size(1))

        rng = range(M * N)
        idx = (edge_index[0] * N + edge_index[1]).to('cpu')  # idx = N * i + j

        perm = torch.tensor(random.sample(rng, num_neg_samples))
        mask = torch.from_numpy(np.isin(perm, idx)).to(torch.bool)
        rest = mask.nonzero().view(-1)
        while rest.numel() > 0:  # pragma: no cover
            tmp = torch.tensor(random.sample(rng, rest.size(0)))
            mask = torch.from_numpy(np.isin(tmp, idx)).to(torch.bool)
            perm[rest] = tmp
            rest = rest[mask.nonzero().view(-1)]

        row = perm / N
        col = perm % N
        neg_edge_index = torch.stack([row, col], dim=0).long()

        return neg_edge_index.to(edge_index.device)

    def proximity_loss(self, edge_index_dict, score_l, score_r, x_index_dict):
        loss = torch.tensor(0, dtype=torch.float, device=self.conv[self.node_types[0]].weight.device)

        # KL Divergence over observed edges, -\sum_(a_ij) a_ij log(e_ij)
        for metapath, edge_index in edge_index_dict.items():
            if edge_index is None: continue

            if isinstance(edge_index, tuple):  # Weighted edges
                edge_index, values = edge_index
            else:
                values = 1

            e_ij = score_l[metapath][edge_index[0]] + score_r[metapath][edge_index[1]]
            loss += -torch.mean(values * torch.log(torch.sigmoid(e_ij)), dim=-1)

        # KL Divergence over negative sampling edges, -\sum_(a'_uv) a_uv log(-e'_uv)
        for metapath, edge_index in edge_index_dict.items():
            if edge_index is None: continue
            if isinstance(edge_index, tuple):  # Weighted edges
                edge_index, values = edge_index
            else:
                values = 1

            neg_edge_index = self.negative_sample(edge_index,
                                                  M=x_index_dict[metapath[0]].size(0),
                                                  N=x_index_dict[metapath[-1]].size(0),
                                                  num_neg_samples=edge_index.size(1))
            e_ij = score_l[metapath][neg_edge_index[0]] + score_r[metapath][neg_edge_index[1]]
            loss += -torch.mean(torch.log(torch.sigmoid(-e_ij)), dim=-1)

        return loss

    def get_relation_weights(self):
        return {"-".join(k) if isinstance(k, tuple) else node_type: v for node_type in self.node_types for k, v in
                self._beta[node_type].items()}


def adamic_adar(indexA, valueA, indexB, valueB, m, k, n, coalesced=False):
    A = SparseTensor(row=indexA[0], col=indexA[1], value=valueA,
                     sparse_sizes=(m, k), is_sorted=not coalesced)
    B = SparseTensor(row=indexB[0], col=indexB[1], value=valueB,
                     sparse_sizes=(k, n), is_sorted=not coalesced)

    deg_A = A.storage.colcount()
    deg_B = B.storage.rowcount()
    deg_normalized = 1 / (deg_A + deg_B).to(torch.float)
    D = SparseTensor(row=torch.arange(deg_normalized.size(0)),
                     col=torch.arange(deg_normalized.size(0)),
                     value=deg_normalized,
                     sparse_sizes=(deg_normalized.size(0), deg_normalized.size(0)))

    C = matmul(matmul(A, D), B)
    row, col, value = C.coo()

    return torch.stack([row, col], dim=0), value
