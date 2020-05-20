import numpy as np
import pandas as pd
import torch
from transformers import AlbertConfig

from moge.module.classifier import Dense, HierarchicalAWX
from moge.module.embedder import GAT
from moge.module.encoder import ConvLSTM, AlbertEncoder
from moge.module.losses import ClassificationLoss, get_hierar_relations


class EncoderEmbedderClassifier(torch.nn.Module):
    def __init__(self) -> None:
        super(EncoderEmbedderClassifier).__init__()

    def get_encoder(self, node_type):
        return self.__getattr__("_encoder_" + node_type)

    def set_encoder(self, node_type, model):
        self.__setattr__("_encoder_" + node_type, model)

    def get_embedder(self, subnetwork_type):
        return self.__getattr__("_embedder_" + subnetwork_type)

    def set_embedder(self, subnetwork_type, model):
        self.__setattr__("_embedder_" + subnetwork_type, model)

    def get_embeddings(self, *args):
        raise NotImplementedError()

    def get_encodings(self, X, node_type, batch_size=32):
        if node_type is not None:
            input_seqs = X[node_type]

        if isinstance(self._encoder, dict):
            encoder_module = self.get_encoder(node_type)
        else:
            encoder_module = self._encoder

        if batch_size is not None:
            input_chunks = input_seqs.split(split_size=batch_size, dim=0)
            encodings = []
            for i in range(len(input_chunks)):
                encodings.append(encoder_module.forward(input_chunks[i]))
            encodings = torch.cat(encodings, 0)
        else:
            encodings = encoder_module.forward(X)

        return encodings

    def predict(self, embeddings, cuda=True):
        y_pred = self._classifier(embeddings)
        if "LOGITS" in self.hparams.loss_type:
            y_pred = torch.softmax(y_pred, 1) if "SOFTMAX" in self.loss_type else torch.sigmoid(y_pred)

        return y_pred.detach().cpu().numpy()


class MonoplexEmebdder(EncoderEmbedderClassifier):
    def __init__(self, hparams):
        torch.nn.Module.__init__(self)

        if hparams.encoder == "ConvLSTM":
            self._encoder = ConvLSTM(hparams)
        elif hparams.encoder == "Albert":
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
            self._encoder = AlbertEncoder(config)
        else:
            raise Exception("hparams.encoder must be one of {'ConvLSTM', 'Albert'}")

        if hparams.embedder == "GAT":
            self._embedder = GAT(hparams)
        else:
            raise Exception("hparams.embedder must be one of {'GAT'}")

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
        self.hparams = hparams

    def forward(self, X):
        input_seqs, subnetwork = X["input_seqs"], X["subnetwork"]
        # print("input_seqs", input_seqs.shape)
        # print("subnetwork", len(subnetwork), [batch.shape for batch in subnetwork])
        if subnetwork.dim() > 2:
            subnetwork = subnetwork.squeeze(0)

        encodings = self._encoder(input_seqs)
        embeddings = self._embedder(encodings, subnetwork)
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

        return self.criterion.forward(Y_hat, Y,
                                      use_hierar=self.hparams.use_hierar, multiclass=True,
                                      classifier_weight=self._classifier.fc_classifier.linear.weight if self.hparams.use_hierar else None, )

    def get_embeddings(self, X, batch_size=None):
        """
        Get embeddings for a set of nodes in `X`.
        :param X: a dict with keys {"input_seqs", "subnetwork"}
        :param cuda (bool): whether to run computations in GPUs
        :return (np.array): a numpy array of size (node size, embedding dim)
        """
        encodings = self.get_encodings(X, node_type="input_seqs", batch_size=batch_size)

        embeddings = self._embedder(encodings, X["subnetwork"])

        return embeddings.detach().cpu().numpy()


