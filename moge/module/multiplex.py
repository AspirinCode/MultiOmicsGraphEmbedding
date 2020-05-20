import numpy as np
import pandas as pd
import torch
from transformers import AlbertConfig

from moge.module.classifier import Dense, HierarchicalAWX
from moge.module.embedder import GAT, GCN, GraphSAGE, MultiplexLayerAttention, MultiplexNodeAttention
from moge.module.encoder import ConvLSTM, AlbertEncoder
from moge.module.encoder_embedder_cls import EncoderEmbedderClassifier
from moge.module.losses import ClassificationLoss, get_hierar_relations


class MultiplexEmbedder(EncoderEmbedderClassifier, torch.nn.Module):
    def __init__(self, hparams):
        torch.nn.Module.__init__(self)

        assert isinstance(hparams.encoder, dict)
        assert isinstance(hparams.embedder, dict)
        # assert isinstance(hparams.vocab_size, dict)
        self.hparams = hparams

        ################### Encoding ####################
        self._encoder = {}
        for seq_type, encoder in hparams.encoder.items():
            if encoder == "ConvLSTM":
                self.__setattr__("_encoder_" + seq_type, ConvLSTM(hparams))
                self._encoder[seq_type] = self.__getattr__("_encoder_" + seq_type)
            elif encoder == "Albert":
                config = AlbertConfig(
                    vocab_size=hparams.vocab_size,
                    embedding_size=hparams.word_embedding_size,
                    hidden_size=hparams.encoding_dim,
                    num_hidden_layers=hparams.num_hidden_layers,
                    num_hidden_groups=hparams.num_hidden_groups,
                    hidden_dropout_prob=hparams.hidden_dropout_prob,
                    attention_probs_dropout_prob=hparams.attention_probs_dropout_prob,
                    num_attention_heads=hparams.num_attention_heads,
                    intermediate_size=hparams.intermediate_size,
                    type_vocab_size=1,
                    max_position_embeddings=hparams.max_length,
                )
                self._encoder[seq_type] = AlbertEncoder(config)
            else:
                raise Exception("hparams.encoder must be one of {'ConvLSTM', 'Albert'}")

        ################### Layer-specfic Embedding ####################
        self._embedder = {}
        for subnetwork_type, embedder in hparams.embedder.items():
            if embedder == "GAT":
                self.__setattr__("_embedder_" + subnetwork_type, GAT(hparams))
                self._embedder[subnetwork_type] = self.__getattr__("_embedder_" + subnetwork_type)
            elif embedder == "GCN":
                self.__setattr__("_embedder_" + subnetwork_type, GCN(hparams))
                self._embedder[subnetwork_type] = self.__getattr__("_embedder_" + subnetwork_type)
            elif embedder == "GraphSAGE":
                self.__setattr__("_embedder_" + subnetwork_type, GraphSAGE(hparams))
                self._embedder[subnetwork_type] = self.__getattr__("_embedder_" + subnetwork_type)
            else:
                raise Exception(f"hparams.embedder[{subnetwork_type}]] must be one of ['GAT', 'GCN', 'GraphSAGE']")

        ################### Multiplex Embedding ####################
        print("\nhparams", hparams)
        layers = list(hparams.embedder.keys())
        if hparams.multiplex_embedder == "MultiplexLayerAttention":
            self._multiplex_embedder = MultiplexLayerAttention(embedding_dim=hparams.embedding_dim,
                                                               hidden_dim=hparams.multiplex_hidden_dim,
                                                               attention_dropout=hparams.multiplex_attn_dropout,
                                                               layers=layers)
        elif hparams.multiplex_embedder == "MultiplexNodeAttention":
            self._multiplex_embedder = MultiplexNodeAttention(embedding_dim=hparams.embedding_dim,
                                                              hidden_dim=hparams.multiplex_hidden_dim,
                                                              attention_dropout=hparams.multiplex_attn_dropout,
                                                              layers=layers)
        else:
            print('"multiplex_embedder" not used. Concatenate multi-layer embeddings instead.')
            hparams.embedding_dim = hparams.embedding_dim * len(hparams.embedder)

        ################### Classifier ####################
        if hparams.classifier == "Dense":
            self._classifier = Dense(hparams)
        elif hparams.classifier == "HierarchicalAWX":
            self._classifier = HierarchicalAWX(hparams)
        else:
            raise Exception("hparams.classifier must be one of {'Dense'}")

        if hparams.use_hierar:
            label_map = pd.Series(range(len(hparams.classes)), index=hparams.classes).to_dict()
            hierar_relations = get_hierar_relations(hparams.hierar_taxonomy_file,
                                                    label_map=label_map)

        self.criterion = ClassificationLoss(
            n_classes=hparams.n_classes,
            loss_type=hparams.loss_type,
            hierar_penalty=hparams.hierar_penalty if hparams.use_hierar else None,
            hierar_relations=hierar_relations if hparams.use_hierar else None
        )

    def forward(self, X):
        encodings = self._encoder["Protein_seqs"](X["Protein_seqs"])

        embeddings = []
        for subnetwork_type, _ in self.hparams.embedder.items():
            if X[subnetwork_type].dim() > 2:
                X[subnetwork_type] = X[subnetwork_type][0].squeeze(0)
            embeddings.append(self._embedder[subnetwork_type](encodings, X[subnetwork_type]))

        if hasattr(self, "_multiplex_embedder"):
            embeddings = self._multiplex_embedder.forward(embeddings)
        else:
            embeddings = torch.cat(embeddings, 1)

        y_pred = self._classifier(embeddings)
        return y_pred

    def loss(self, Y_hat: torch.Tensor, Y, weights=None):
        Y = Y.type_as(Y_hat)
        if isinstance(weights, torch.Tensor):
            idx = torch.nonzero(weights).view(-1)
        else:
            idx = torch.tensor(np.nonzero(weights)[0])

        Y = Y[idx, :]
        Y_hat = Y_hat[idx, :]

        return self.criterion.forward(
            Y_hat, Y,
            use_hierar=self.hparams.use_hierar, multiclass=True,
            classifier_weight=self._classifier.fc_classifier.linear.weight if self.hparams.use_hierar else None,
        )

    def get_embeddings(self, X, batch_size=100, return_multi_emb=False):
        """
        Get embeddings for a set of nodes in `X`.
        :param X: a dict with keys {"input_seqs", "subnetwork"}
        :param cuda (bool): whether to run computations in GPUs
        :return (np.array): a numpy array of size (node size, embedding dim)
        """
        encodings = self.get_encodings(X, key="Protein_seqs", batch_size=batch_size)

        multi_embeddings = []
        for subnetwork_type, _ in self.hparams.embedder.items():
            multi_embeddings.append(self._embedder[subnetwork_type](encodings, X[subnetwork_type]))

        if return_multi_emb:
            return multi_embeddings

        if "Multiplex" in self.hparams.multiplex_embedder:
            embeddings = self._multiplex_embedder.forward(multi_embeddings)
        else:
            embeddings = torch.cat(multi_embeddings, 1)

        return embeddings.detach().cpu().numpy()

    def predict(self, embeddings, cuda=True):
        y_pred = self._classifier(embeddings)
        if "LOGITS" in self.hparams.loss_type:
            y_pred = torch.softmax(y_pred, 1) if "SOFTMAX" in self.hparams.loss_type else torch.sigmoid(y_pred)

        return y_pred.detach().cpu().numpy()
