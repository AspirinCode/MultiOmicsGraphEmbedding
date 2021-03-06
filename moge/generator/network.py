import networkx as nx
import numpy as np
import pandas as pd
import torch
import torch_sparse
from cogdl.datasets.gtn_data import GTNDataset
from cogdl.datasets.han_data import HANDataset
from ogb.linkproppred import PygLinkPropPredDataset
from ogb.nodeproppred import PygNodePropPredDataset, DglNodePropPredDataset
from scipy.io import loadmat
from torch.utils import data
from torch_geometric.data import InMemoryDataset

from moge.module.PyG.latte import is_negative


class Network:
    def get_networkx(self):
        if not hasattr(self, "G"):
            G = nx.Graph()
            for metapath in self.edge_index_dict:
                edgelist = self.edge_index_dict[metapath].t().numpy().astype(str)
                edgelist = np.core.defchararray.add([metapath[0][0], metapath[-1][0]], edgelist)
                edge_type = "".join([n for i, n in enumerate(metapath) if i % 2 == 1])
                G.add_edges_from(edgelist, edge_type=edge_type)

            self.G = G

        return self.G

    def get_projection_pos(self, embeddings_all, UMAP: classmethod, n_components=2):
        pos = UMAP(n_components=n_components).fit_transform(embeddings_all)
        pos = {embeddings_all.index[i]: pair for i, pair in enumerate(pos)}
        return pos

    def get_node_degrees(self, directed=True):
        index = pd.concat([pd.DataFrame(range(v), [k, ] * v) for k, v in self.num_nodes_dict.items()],
                          axis=0).reset_index()
        multi_index = pd.MultiIndex.from_frame(index, names=["node_type", "node"])

        metapaths = list(self.edge_index_dict.keys())
        metapath_names = [".".join(metapath) if isinstance(metapath, tuple) else metapath for metapath in
                          metapaths]
        self.node_degrees = pd.DataFrame(data=0, index=multi_index,
                                         columns=metapath_names)

        for metapath, name in zip(metapaths, metapath_names):
            edge_index = self.edge_index_dict[metapath]

            head, tail = metapath[0], metapath[-1]
            D = torch_sparse.SparseTensor(row=edge_index[0], col=edge_index[1],
                                          sparse_sizes=(self.num_nodes_dict[head],
                                                        self.num_nodes_dict[tail]))

            self.node_degrees.loc[(head, name)] = (
                    self.node_degrees.loc[(head, name)] + D.storage.rowcount().numpy()).values
            if not directed:
                self.node_degrees.loc[(tail, name)] = (
                        self.node_degrees.loc[(tail, name)] + D.storage.colcount().numpy()).values

        return self.node_degrees

    def get_embedding_dfs(self, embeddings_dict, global_node_index):
        embeddings = []
        for node_type in self.node_types:
            nodes = global_node_index[node_type].numpy().astype(str)
            nodes = np.core.defchararray.add(node_type[0], nodes)
            if isinstance(embeddings_dict[node_type], torch.Tensor):
                df = pd.DataFrame(embeddings_dict[node_type].detach().cpu().numpy(), index=nodes)
            else:
                df = pd.DataFrame(embeddings_dict[node_type], index=nodes)
            embeddings.append(df)

        return embeddings

    def get_embeddings_types_labels(self, embeddings, global_node_index):
        embeddings_all = pd.concat(embeddings, axis=0)

        types_all = embeddings_all.index.to_series().str.slice(0, 1)
        if hasattr(self, "y_dict") and len(self.y_dict) > 0:
            labels = pd.Series(
                self.y_dict[self.head_node_type][global_node_index[self.head_node_type]].squeeze(-1).numpy(),
                index=embeddings[0].index,
                dtype=str)
        else:
            labels = None

        return embeddings_all, types_all, labels


class HeteroNetDataset(torch.utils.data.Dataset, Network):
    def __init__(self, dataset, node_types=None, metapaths=None, head_node_type=None, directed=True,
                 resample_train: float = None, add_reverse_metapaths=True, inductive=True):
        """
        This class handles processing of the data & train/test spliting.
        :param dataset:
        :param node_types:
        :param metapaths:
        :param head_node_type:
        :param directed:
        :param resample_train:
        :param add_reverse_metapaths:
        """
        self.dataset = dataset
        self.directed = directed
        self.use_reverse = add_reverse_metapaths
        self.node_types = node_types
        self.head_node_type = head_node_type
        self.inductive = inductive

        # PyTorchGeometric Dataset

        if isinstance(dataset, PygNodePropPredDataset) and not hasattr(dataset[0], "edge_index_dict"):
            print("PygNodePropPredDataset Homogenous (use HeteroNeighborSampler class)")
            self.process_PygNodeDataset_homo(dataset)
        elif isinstance(dataset, PygNodePropPredDataset) and hasattr(dataset[0], "edge_index_dict"):
            print("PygNodePropPredDataset Hetero (use HeteroNeighborSampler class)")
            self.process_PygNodeDataset_hetero(dataset)
        elif isinstance(dataset, DglNodePropPredDataset):
            print("DGLNodePropPredDataset Hetero")
            self.process_DglNodeDataset_hetero(dataset)

        elif isinstance(dataset, PygLinkPropPredDataset) and hasattr(dataset[0], "edge_reltype") and \
                not hasattr(dataset[0], "edge_index_dict"):
            print("PygLink_edge_reltype_dataset Hetero (use TripletSampler class)")
            self.process_edge_reltype_dataset(dataset)
        elif isinstance(dataset, PygLinkPropPredDataset) and hasattr(dataset[0], "edge_index_dict"):
            print("PygLinkDataset Hetero (use TripletSampler class)")
            self.process_PygLinkDataset_hetero(dataset)
        elif isinstance(dataset, PygLinkPropPredDataset) and not hasattr(dataset[0], "edge_index_dict") \
                and not hasattr(dataset[0], "edge_reltype"):
            print("PygLinkDataset Homo (use EdgeSampler class)")
            self.process_PygLinkDataset_homo(dataset)

        elif isinstance(dataset, InMemoryDataset):
            print("InMemoryDataset")
            self.process_inmemorydataset(dataset, train_ratio=0.5)
        elif isinstance(dataset, HANDataset) or isinstance(dataset, GTNDataset):
            print(f"{dataset.__class__.__name__}")
            self.process_COGDLdataset(dataset, metapaths, node_types, resample_train)
        elif "blogcatalog6k" in dataset:
            self.process_BlogCatalog6k(dataset, train_ratio=0.5)
        else:
            raise Exception(f"Unsupported dataset {dataset}")

        if hasattr(self, "y_dict"):
            if self.y_dict[self.head_node_type].dim() > 1 and self.y_dict[self.head_node_type].size(-1) != 1:
                self.multilabel = True
                self.classes = torch.arange(self.y_dict[self.head_node_type].size(1))
                self.class_counts = self.y_dict[self.head_node_type].sum(0)
            else:
                self.multilabel = False

                mask = self.y_dict[self.head_node_type] != -1
                labels = self.y_dict[self.head_node_type][mask]
                self.classes = labels.unique()

                if self.y_dict[self.head_node_type].dim() > 1:
                    labels = labels.squeeze(-1).numpy()
                else:
                    labels = labels.numpy()
                self.class_counts = pd.Series(labels).value_counts(sort=False)

            self.n_classes = self.classes.size(0)
            self.class_weight = torch.true_divide(1, torch.tensor(self.class_counts, dtype=torch.float))

            assert -1 not in self.classes
            assert self.class_weight.numel() == self.n_classes, f"self.class_weight {self.class_weight.numel()}, n_classes {self.n_classes}"
        else:
            self.multilabel = False
            self.n_classes = None
            print("WARNING: Dataset doesn't have node label (y_dict attribute).")

        assert hasattr(self, "num_nodes_dict")

        if not hasattr(self, "x_dict") or len(self.x_dict) == 0:
            self.x_dict = {}

        if resample_train is not None and resample_train > 0:
            self.resample_training_idx(resample_train)
        else:
            print("train_ratio", self.get_train_ratio())
        self.train_ratio = self.get_train_ratio()

    def name(self):
        if not hasattr(self, "_name"):
            return self.dataset.__class__.__name__
        else:
            return self._name

    @property
    def node_attr_shape(self):
        if not hasattr(self, "x_dict") or len(self.x_dict) == 0:
            node_attr_shape = {}
        else:
            node_attr_shape = {k: v.size(1) for k, v in self.x_dict.items()}
        return node_attr_shape

    def split_train_val_test(self, train_ratio, sample_indices=None):
        if sample_indices is not None:
            indices = sample_indices[torch.randperm(sample_indices.size(0))]
        else:
            indices = torch.randperm(self.num_nodes_dict[self.head_node_type])

        num_indices = indices.size(0)
        training_idx = indices[:int(num_indices * train_ratio)]
        validation_idx = indices[int(num_indices * train_ratio):]
        testing_idx = indices[int(num_indices * train_ratio):]
        return training_idx, validation_idx, testing_idx

    def resample_training_idx(self, train_ratio):
        all_idx = torch.cat([self.training_idx, self.validation_idx, self.testing_idx])
        self.training_idx, self.validation_idx, self.testing_idx = \
            self.split_train_val_test(train_ratio=train_ratio, sample_indices=all_idx)
        print(f"Resampled training set at {self.get_train_ratio()}%")


    def get_metapaths(self):
        if self.use_reverse:
            return self.metapaths + self.get_reverse_metapath(self.metapaths, self.edge_index_dict)
        else:
            return self.metapaths

    def get_num_nodes_dict(self, edge_index_dict):
        num_nodes_dict = {}
        for keys, edge_index in edge_index_dict.items():
            key = keys[0]
            N = int(edge_index[0].max() + 1)
            num_nodes_dict[key] = max(N, num_nodes_dict.get(key, N))

            key = keys[-1]
            N = int(edge_index[1].max() + 1)
            num_nodes_dict[key] = max(N, num_nodes_dict.get(key, N))
        return num_nodes_dict

    @staticmethod
    def add_reverse_edge_index(edge_index_dict) -> None:
        reverse_edge_index_dict = {}
        for metapath in edge_index_dict:
            if is_negative(metapath) or edge_index_dict[metapath] == None: continue
            reverse_metapath = HeteroNetDataset.get_reverse_metapath_name(metapath, edge_index_dict)

            reverse_edge_index_dict[reverse_metapath] = edge_index_dict[metapath][[1, 0], :]
        edge_index_dict.update(reverse_edge_index_dict)

    @staticmethod
    def get_reverse_metapath_name(metapath, edge_index_dict=None):
        if isinstance(metapath, tuple):
            reverse_metapath = tuple(a + "_by" if i == 1 else a for i, a in enumerate(reversed(metapath)))
        elif isinstance(metapath, str):
            reverse_metapath = "".join(reversed(metapath))
            if reverse_metapath in edge_index_dict:
                reverse_metapath = reverse_metapath[:2] + "_" + reverse_metapath[2:]
        elif isinstance(metapath, (int, np.int)):
            reverse_metapath = str(metapath) + "_"
        else:
            raise NotImplementedError(f"{metapath} not supported")
        return reverse_metapath

    @staticmethod
    def get_reverse_metapath(metapaths, edge_index_dict) -> list:
        reverse_metapaths = []
        for metapath in metapaths:
            reverse = HeteroNetDataset.get_reverse_metapath_name(metapath, edge_index_dict)
            reverse_metapaths.append(reverse)
        return reverse_metapaths

    @staticmethod
    def sps_adj_to_edgeindex(adj):
        adj = adj.tocoo(copy=False)
        return torch.tensor(np.vstack((adj.row, adj.col)).astype("long"))

    def process_BlogCatalog6k(self, dataset, train_ratio):
        data = loadmat(dataset)  # From http://dmml.asu.edu/users/xufei/Data/blogcatalog6k.mat
        self._name = "BlogCatalog3"
        self.y_index_dict = {"user": torch.arange(data["friendship"].shape[0]),
                             "tag": torch.arange(data["tagnetwork"].shape[0])}
        self.node_types = ["user", "tag"]
        self.head_node_type = "user"
        self.y_dict = {self.head_node_type: torch.tensor(data["usercategory"].toarray().astype(int))}
        print("self.y_dict", {k: v.shape for k, v in self.y_dict.items()})

        self.metapaths = [("user", "usertag", "tag"),
                          ("tag", "tagnetwork", "tag"),
                          ("user", "friendship", "user"), ]
        self.edge_index_dict = {
            ("user", "friendship", "user"): self.sps_adj_to_edgeindex(data["friendship"]),
            ("user", "usertag", "tag"): self.sps_adj_to_edgeindex(data["usertag"]),
            ("tag", "tagnetwork", "tag"): self.sps_adj_to_edgeindex(data["tagnetwork"])}
        self.num_nodes_dict = self.get_num_nodes_dict(self.edge_index_dict)
        assert train_ratio is not None
        self.training_idx, self.validation_idx, self.testing_idx = self.split_train_val_test(train_ratio)

    def process_COGDLdataset(self, dataset: HANDataset, metapath, node_types, train_ratio):
        data = dataset.data
        assert self.head_node_type is not None
        assert node_types is not None
        print(f"Edge_types: {len(data['adj'])}")
        self.node_types = node_types
        if metapath is not None:
            self.edge_index_dict = {metapath: data["adj"][i][0] for i, metapath in enumerate(metapath)}
        else:
            self.edge_index_dict = {f"{self.head_node_type}{i}{self.head_node_type}": data["adj"][i][0] \
                                    for i in range(len(data["adj"]))}
        self.edge_types = list(range(dataset.num_edge))
        self.metapaths = list(self.edge_index_dict.keys())
        self.x_dict = {self.head_node_type: data["x"]}
        self.in_features = data["x"].size(1)

        self.training_idx, self.training_target = data["train_node"], data["train_target"]
        self.validation_idx, self.validation_target = data["valid_node"], data["valid_target"]
        self.testing_idx, self.testing_target = data["test_node"], data["test_target"]

        self.y_index_dict = {self.head_node_type: torch.cat([self.training_idx, self.validation_idx, self.testing_idx])}
        self.num_nodes_dict = self.get_num_nodes_dict(self.edge_index_dict)

        # Create new labels vector for all nodes, with -1 for nodes without label
        self.y_dict = {
            self.head_node_type: torch.cat([self.training_target, self.validation_target, self.testing_target])}

        new_y_dict = {nodetype: -torch.ones(self.num_nodes_dict[nodetype] + 1).type_as(self.y_dict[nodetype]) \
                      for nodetype in self.y_dict}
        for node_type in self.y_dict:
            new_y_dict[node_type][self.y_index_dict[node_type]] = self.y_dict[node_type]
        self.y_dict = new_y_dict

        if self.inductive:
            other_nodes = torch.arange(self.num_nodes_dict[self.head_node_type])
            idx = ~np.isin(other_nodes, self.training_idx) & \
                  ~np.isin(other_nodes, self.validation_idx) & \
                  ~np.isin(other_nodes, self.testing_idx)
            other_nodes = other_nodes[idx]
            self.training_subgraph_idx = torch.cat(
                [self.training_idx, torch.tensor(other_nodes, dtype=self.training_idx.dtype)],
                dim=0).unique()

        self.data = data

    def process_stellargraph(self, dataset, metapath, node_types, train_ratio):
        graph = dataset.load()
        self.node_types = graph.node_types if node_types is None else node_types
        self.metapaths = graph.metapaths
        self.y_index_dict = {k: torch.tensor(graph.nodes(k, use_ilocs=True)) for k in graph.node_types}

        edgelist = graph.edges(include_edge_type=True, use_ilocs=True)
        edge_index_dict = {path: [] for path in metapath}
        for u, v, t in edgelist:
            edge_index_dict[metapath[t]].append([u, v])
        self.edge_index_dict = {metapath: torch.tensor(edges, dtype=torch.long).T for metapath, edges in
                                edge_index_dict.items()}
        self.training_node, self.validation_node, self.testing_node = self.split_train_val_test(train_ratio)

    def process_inmemorydataset(self, dataset: InMemoryDataset, train_ratio):
        data = dataset[0]
        self.edge_index_dict = data.edge_index_dict
        self.num_nodes_dict = data.num_nodes_dict
        if self.node_types is None:
            self.node_types = list(data.num_nodes_dict.keys())
        self.y_dict = data.y_dict
        self.y_index_dict = data.y_index_dict

        new_y_dict = {nodetype: -torch.ones(self.num_nodes_dict[nodetype] + 1).type_as(self.y_dict[nodetype]) for
                      nodetype in self.y_dict}
        for node_type in self.y_dict:
            new_y_dict[node_type][self.y_index_dict[node_type]] = self.y_dict[node_type]
        self.y_dict = new_y_dict

        self.metapaths = list(self.edge_index_dict.keys())
        assert train_ratio is not None
        self.training_idx, self.validation_idx, self.testing_idx = \
            self.split_train_val_test(train_ratio,
                                      sample_indices=self.y_index_dict[self.head_node_type])

    def train_dataloader(self, collate_fn=None, batch_size=128, num_workers=12, **kwargs):
        loader = data.DataLoader(self.training_idx, batch_size=batch_size,
                                 shuffle=True, num_workers=num_workers,
                                 collate_fn=collate_fn if callable(collate_fn) else self.get_collate_fn(collate_fn,
                                                                                                        mode="train",
                                                                                                        **kwargs))
        return loader

    def valtrain_dataloader(self, collate_fn=None, batch_size=128, num_workers=12, **kwargs):
        loader = data.DataLoader(torch.cat([self.training_idx, self.validation_idx]), batch_size=batch_size,
                                 shuffle=True, num_workers=num_workers,
                                 collate_fn=collate_fn if callable(collate_fn) else self.get_collate_fn(collate_fn,
                                                                                                        mode="validation",
                                                                                                        **kwargs))
        return loader

    def valid_dataloader(self, collate_fn=None, batch_size=128, num_workers=4, **kwargs):
        loader = data.DataLoader(self.validation_idx, batch_size=batch_size,
                                 shuffle=True, num_workers=num_workers,
                                 collate_fn=collate_fn if callable(collate_fn) else self.get_collate_fn(collate_fn,
                                                                                                        mode="validation",
                                                                                                        **kwargs))
        return loader

    def test_dataloader(self, collate_fn=None, batch_size=128, num_workers=4, **kwargs):
        loader = data.DataLoader(self.testing_idx, batch_size=batch_size,
                                 shuffle=True, num_workers=num_workers,
                                 collate_fn=collate_fn if callable(collate_fn) else self.get_collate_fn(collate_fn,
                                                                                                        mode="testing",
                                                                                                        **kwargs))
        return loader

    def get_collate_fn(self, collate_fn: str, mode=None, **kwargs):

        def collate_wrapper(iloc):
            if "HAN_batch" in collate_fn:
                return self.collate_HAN_batch(iloc, mode=mode)
            elif "HAN" in collate_fn:
                return self.collate_HAN(iloc, mode=mode)
            else:
                raise Exception(f"Correct collate function {collate_fn} not found.")

        return collate_wrapper

    def filter_edge_index(self, input, allowed_nodes):
        if isinstance(input, tuple):
            edge_index = input[0]
            values = edge_index[1]
        else:
            edge_index = input
            values = None

        mask = np.isin(edge_index[0], allowed_nodes) & np.isin(edge_index[1], allowed_nodes)
        edge_index = edge_index[:, mask]

        if values == None:
            values = torch.ones(edge_index.size(1))
        else:
            values = values[mask]

        return (edge_index, values)

    def collate_HAN(self, iloc, mode=None):
        if not isinstance(iloc, torch.Tensor):
            iloc = torch.tensor(iloc)

        if "train" in mode:
            filter = True if self.inductive else False
            if self.inductive and hasattr(self, "training_subgraph_idx"):
                allowed_nodes = self.training_subgraph_idx
            else:
                allowed_nodes = self.training_idx
        elif "valid" in mode:
            filter = True if self.inductive else False
            if self.inductive and hasattr(self, "training_subgraph_idx"):
                allowed_nodes = torch.cat([self.validation_idx, self.training_subgraph_idx])
            else:
                allowed_nodes = self.validation_idx
        elif "test" in mode:
            filter = False
            allowed_nodes = self.testing_idx
        else:
            filter = False
            print("WARNING: should pass a value in `mode` in collate_HAN()")

        if isinstance(self.dataset, HANDataset):
            X = {"adj": [(edge_index, values) \
                             if not filter else self.filter_edge_index((edge_index, values), allowed_nodes) \
                         for edge_index, values in self.data["adj"][:len(self.metapaths)]],
                 "x": self.data["x"] if hasattr(self.data, "x") else None,
                 "idx": iloc}
        else:
            X = {
                "adj": [(self.edge_index_dict[i], torch.ones(self.edge_index_dict[i].size(1))) \
                            if not filter else self.filter_edge_index(self.edge_index_dict[i], allowed_nodes) \
                        for i in self.metapaths],
                "x": self.data["x"] if hasattr(self.data, "x") else None,
                "idx": iloc}

        X["adj"] = [edge for edge in X["adj"] if edge[0].size(1) > 0]

        y = self.y_dict[self.head_node_type][iloc]
        return X, y, None

    def collate_HAN_batch(self, iloc, mode=None):
        if not isinstance(iloc, torch.Tensor):
            iloc = torch.tensor(iloc)

        X_batch, y, weights = self.sample(iloc, mode=mode)  # uses HeteroNetSampler PyG sampler method

        X = {}
        X["adj"] = [(X_batch["edge_index_dict"][metapath], torch.ones(X_batch["edge_index_dict"][metapath].size(1))) \
                    for metapath in self.metapaths if metapath in X_batch["edge_index_dict"]]
        X["x"] = self.data["x"][X_batch["global_node_index"][self.head_node_type]]
        X["idx"] = X_batch["global_node_index"][self.head_node_type]

        return X, y, weights

    def get_train_ratio(self):
        if self.validation_idx.size() != self.testing_idx.size() or not (self.validation_idx == self.testing_idx).all():
            train_ratio = self.training_idx.numel() / \
                          sum([self.training_idx.numel(), self.validation_idx.numel(), self.testing_idx.numel()])
        else:
            train_ratio = self.training_idx.numel() / sum([self.training_idx.numel(), self.validation_idx.numel()])
        return train_ratio