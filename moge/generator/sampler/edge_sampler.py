import numpy as np
import torch
from ogb.linkproppred import PygLinkPropPredDataset

from moge.generator.sampler.datasets import HeteroNetDataset
from .triplets_sampler import TripletSampler


class EdgeSampler(HeteroNetDataset):
    def __init__(self, dataset, node_types=None, metapaths=None, head_node_type=None, directed=True, train_ratio=0.7,
                 add_reverse_metapaths=True, process_graphs=False):
        super(EdgeSampler, self).__init__(dataset, node_types, metapaths, head_node_type, directed, train_ratio,
                                          add_reverse_metapaths,
                                          process_graphs)

    def process_PygLinkDataset_homo(self, dataset: PygLinkPropPredDataset, train_ratio):
        data = dataset[0]
        self._name = dataset.name

        self.head_node_type = "entity"
        self.metapaths = [(self.head_node_type, "default", self.head_node_type)]

        self.edge_index_dict = {self.metapaths[0]: data.edge_index}
        self.num_nodes_dict = self.get_num_nodes_dict(self.edge_index_dict)
        self.node_types = list(self.num_nodes_dict.keys())

        self.x_dict = {self.head_node_type: data.x} if hasattr(data, "x") else {}
        self.node_attr_shape = {node_type: x.size(1) for node_type, x in self.x_dict.items()}

        split_idx = dataset.get_edge_split()
        train_edges, valid_edges, test_edges = split_idx["train"], split_idx["valid"], split_idx["test"]
        self.triples = {}
        for key in train_edges.keys():
            if isinstance(train_edges[key], torch.Tensor):
                self.triples[key] = torch.cat([train_edges[key], valid_edges[key], test_edges[key]], dim=0)
            else:
                self.triples[key] = np.array(train_edges[key] + valid_edges[key] + test_edges[key])

        self.training_idx = torch.arange(0, len(train_edges["relation"]))
        self.validation_idx = torch.arange(self.training_idx.size(0),
                                           self.training_idx.size(0) + len(valid_edges["relation"]))
        self.testing_idx = torch.arange(self.training_idx.size(0) + self.validation_idx.size(0),
                                        self.training_idx.size(0) + self.validation_idx.size(0) + len(
                                            test_edges["relation"]))

    def get_collate_fn(self, collate_fn: str, batch_size=None, mode=None):
        if "triples_batch" in collate_fn:
            return self.sample_triples
        else:
            raise Exception(f"Correct collate function {collate_fn} not found.")

    def sample_triples(self, iloc):
        if not isinstance(iloc, torch.Tensor):
            iloc = torch.tensor(iloc)

        X = {"edge_index_dict": {}, "global_node_index": {}, "x_dict": {}}

        triples = {k: v[iloc] for k, v in self.triples.items()}
        relation_ids = triples["relation"].unique()

        # Gather all nodes sampled
        for relation_id in relation_ids:
            metapath = self.metapaths[relation_id]
            head_type, tail_type = metapath[0], metapath[-1]

            mask = triples["relation"] == relation_id
            X["global_node_index"].setdefault(head_type, []).append(triples["head"][mask])
            X["global_node_index"].setdefault(tail_type, []).append(triples["tail"][mask])

        X["global_node_index"] = {node_type: torch.cat(node_sets, dim=0).unique() \
                                  for node_type, node_sets in X["global_node_index"].items()}

        local2batch = {
            node_type: dict(zip(X["global_node_index"][node_type].numpy(),
                                range(len(X["global_node_index"][node_type])))
                            ) for node_type in X["global_node_index"]}

        # Get edge_index with batch id
        for relation_id in relation_ids:
            metapath = self.metapaths[relation_id]
            head_type, tail_type = metapath[0], metapath[-1]

            mask = triples["relation"] == relation_id
            sources = triples["head"][mask].apply_(local2batch[head_type].get)
            targets = triples["tail"][mask].apply_(local2batch[tail_type].get)
            X["edge_index_dict"][metapath] = torch.stack([sources, targets], dim=1).t()

        if self.use_reverse:
            self.add_reverse_edge_index(X["edge_index_dict"])

        # Make x_dict
        if hasattr(self, "x_dict") and len(self.x_dict) > 0:
            X["x_dict"] = {node_type: self.x_dict[node_type][X["global_node_index"][node_type]] \
                           for node_type in self.x_dict}

        return X, None, None