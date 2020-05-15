from argparse import ArgumentParser

import torch
import torch.nn as nn
from torch_geometric.nn import GATConv, GCNConv, SAGEConv
from torch_geometric.nn.inits import glorot, zeros


class GAT(nn.Module):
    def __init__(self, hparams) -> None:
        super(GAT, self).__init__()

        self.embedder = GATConv(
            in_channels=hparams.encoding_dim,
            out_channels=int(hparams.embedding_dim / hparams.nb_attn_heads),
            heads=hparams.nb_attn_heads,
            concat=True,
            dropout=hparams.nb_attn_dropout
        )

    @staticmethod
    def add_model_specific_args(parent_parser):
        parser = ArgumentParser(parents=[parent_parser])
        parser.add_argument('--embedding_dim', type=int, default=128)
        parser.add_argument('--nb_attn_heads', type=int, default=4)
        parser.add_argument('--nb_attn_dropout', type=float, default=0.5)
        return parser

    def forward(self, encodings, subnetwork):
        return self.embedder(encodings, subnetwork)


class GCN(nn.Module):
    def __init__(self, hparams) -> None:
        super(GCN, self).__init__()

        self.embedder = GCNConv(
            in_channels=hparams.encoding_dim,
            out_channels=hparams.embedding_dim,
        )

    @staticmethod
    def add_model_specific_args(parent_parser):
        parser = ArgumentParser(parents=[parent_parser])
        parser.add_argument('--embedding_dim', type=int, default=128)
        return parser

    def forward(self, encodings, subnetwork):
        return self.embedder(encodings, subnetwork)


class GraphSAGE(nn.Module):
    def __init__(self, hparams) -> None:
        super(GraphSAGE, self).__init__()

        self.embedder = SAGEConv(
            in_channels=hparams.encoding_dim,
            out_channels=hparams.embedding_dim,
            concat=True,
        )

    @staticmethod
    def add_model_specific_args(parent_parser):
        parser = ArgumentParser(parents=[parent_parser])
        parser.add_argument('--embedding_dim', type=int, default=128)
        return parser

    def forward(self, encodings, subnetwork):
        return self.embedder(encodings, subnetwork)


class MultiplexLayerAttention(nn.MultiLabelSoftMarginLoss):
    def __init__(self, in_channels, out_channels, layers, bias=True):
        super(MultiplexLayerAttention, self).__init__()

        self.in_channels = in_channels
        self.out_channels = out_channels
        self.layers = layers

        self.weight = nn.Parameter(torch.Tensor(in_channels, out_channels))
        self.att = nn.Parameter(torch.Tensor(1, out_channels))

        if bias:
            self.bias = nn.Parameter(torch.Tensor(out_channels))
        else:
            self.register_parameter('bias', None)

        self.reset_parameters()

    def reset_parameters(self):
        glorot(self.weight)
        glorot(self.att)
        zeros(self.bias)

    def forward(self, embeddings):
        w = torch.zeros((len(self.layers), 1)).type_as(self.att)

        for i, layer in enumerate(self.layers):
            x = torch.tanh(torch.matmul(embeddings[i], self.weight) + self.bias)
            w[i] = torch.mean(torch.matmul(x, self.att.t()), dim=0)

        w = torch.softmax(w, 0)
        z = torch.matmul(torch.stack(embeddings, 2), w)
        z = z.squeeze(2)
        return z

    @staticmethod
    def add_model_specific_args(parent_parser):
        parser = ArgumentParser(parents=[parent_parser])
        parser.add_argument('--embedding_dim', type=int, default=128)
        return parser


class MultiplexNodeAttention(nn.MultiLabelSoftMarginLoss):
    def __init__(self, in_channels, out_channels, layers, bias=True):
        super(MultiplexNodeAttention, self).__init__()

        self.in_channels = in_channels
        self.out_channels = out_channels
        self.layers = layers

        self.weight = nn.Parameter(torch.Tensor(in_channels, out_channels))
        self.att = nn.Parameter(torch.Tensor(len(self.layers), out_channels))

        if bias:
            self.bias = nn.Parameter(torch.Tensor(out_channels))
        else:
            self.register_parameter('bias', None)

        self.reset_parameters()

    def reset_parameters(self):
        glorot(self.weight)
        glorot(self.att)
        zeros(self.bias)

    def forward(self, embeddings):
        batch_size, in_channels = embeddings[0].size()
        w = torch.zeros((batch_size, len(self.layers), 1)).type_as(self.att)

        for i, layer in enumerate(self.layers):
            x = torch.tanh(torch.matmul(embeddings[i], self.weight) + self.bias)
            w[:, i] = torch.matmul(x, self.att.t())

        w = torch.softmax(w, 0)
        z = torch.matmul(torch.stack(embeddings, 2), w)
        z = z.squeeze(2)
        return z

    @staticmethod
    def add_model_specific_args(parent_parser):
        parser = ArgumentParser(parents=[parent_parser])
        parser.add_argument('--embedding_dim', type=int, default=128)
        return parser
