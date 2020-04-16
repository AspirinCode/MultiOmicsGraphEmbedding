import pytorch_lightning as pl
import torch
import torch.nn as nn
from ignite.metrics import Precision, Recall
from torch.autograd import Variable
from torch.nn import functional as F
from torch_geometric.nn import GATConv


class EncoderLSTM(pl.LightningModule):
    def __init__(self, encoding_dim: int, embedding_dim: int, n_classes: int, vocab: dict, word_embedding_size=None,
                 nb_lstm_layers=1, nb_lstm_units=100, nb_lstm_dropout=0.2, nb_lstm_hidden_dropout=0.2,
                 nb_lstm_batchnorm=True,
                 nb_conv1d_filters=192, nb_conv1d_kernel_size=26, nb_max_pool_size=2, nb_conv1d_dropout=0.2,
                 nb_conv1d_batchnorm=True,
                 nb_attn_heads=4, nb_attn_dropout=0.5,
                 nb_cls_dense_size=512, nb_cls_dropout=0.2,
                 verbose=False,
                 ):
        super(EncoderLSTM, self).__init__()
        self.vocab = vocab
        if word_embedding_size is None:
            self.word_embedding_size = len(self.vocab)
        else:
            self.word_embedding_size = word_embedding_size

        self.nb_conv1d_filters = nb_conv1d_filters
        self.nb_conv1d_kernel_size = nb_conv1d_kernel_size
        self.nb_max_pool_size = nb_max_pool_size
        self.nb_conv1d_dropout = nb_conv1d_dropout
        self.nb_conv1d_batchnorm = nb_conv1d_batchnorm

        self.nb_lstm_layers = nb_lstm_layers
        self.nb_lstm_units = nb_lstm_units
        self.nb_lstm_dropout = nb_lstm_dropout
        self.nb_lstm_batchnorm = nb_lstm_batchnorm

        self.nb_lstm_hidden_dropout = nb_lstm_hidden_dropout

        self.encoding_dim = encoding_dim

        # Encoder
        self.word_embedding = nn.Embedding(
            num_embeddings=len(self.vocab) + 1,
            embedding_dim=self.word_embedding_size,
            padding_idx=0)

        self.conv1 = nn.Conv1d(
            in_channels=self.word_embedding_size,
            out_channels=self.nb_conv1d_filters,
            kernel_size=self.nb_conv1d_kernel_size)

        self.conv1_dropout = nn.Dropout(p=self.nb_conv1d_dropout)
        # self.conv_batchnorm = nn.LayerNorm([self.nb_conv1d_filters, ])

        self.lstm = nn.LSTM(
            input_size=self.nb_conv1d_filters,
            hidden_size=self.nb_lstm_units,
            num_layers=self.nb_lstm_layers,
            dropout=self.nb_lstm_dropout,
            batch_first=True, )

        self.lstm_hidden_dropout = nn.Dropout(p=self.nb_lstm_hidden_dropout)
        self.lstm_batchnorm = nn.LayerNorm(self.nb_lstm_units * self.nb_lstm_layers)

        self.encoder = nn.Linear(self.nb_lstm_units * self.nb_lstm_layers, self.encoding_dim)

        # Embedder
        self.nb_attn_heads = nb_attn_heads
        self.nb_attn_dropout = nb_attn_dropout
        self.embedding_dim = embedding_dim

        self.embedder = GATConv(
            in_channels=self.encoding_dim,
            out_channels=int(self.embedding_dim / self.nb_attn_heads),
            heads=self.nb_attn_heads,
            concat=True,
            dropout=self.nb_attn_dropout
        )

        # Classifier
        self.n_classes = n_classes
        self.nb_cls_dense_size = nb_cls_dense_size
        self.nb_cls_dropout = nb_cls_dropout

        self.classifier = nn.Sequential(
            nn.Linear(self.embedding_dim, self.nb_cls_dense_size),
            nn.ReLU(),
            nn.Dropout(p=self.nb_cls_dropout),
            nn.Linear(self.nb_cls_dense_size, self.n_classes),
            nn.Sigmoid()
        )

        self.init_metrics()

    def init_hidden(self, batch_size):
        # the weights are of the form (nb_layers, batch_size, nb_lstm_units)
        hidden_a = torch.randn(self.nb_lstm_layers, batch_size, self.nb_lstm_units).cuda()
        hidden_b = torch.randn(self.nb_lstm_layers, batch_size, self.nb_lstm_units).cuda()

        hidden_a = Variable(hidden_a)
        hidden_b = Variable(hidden_b)

        return (hidden_a, hidden_b)

    def forward(self, input_seqs, subnetwork):
        X = F.sigmoid(self.get_encodings(input_seqs))

        # Embedder
        # X = self.embedder(X, subnetwork)
        # Classifier
        # X = self.classifier(X)
        return X

    def get_encodings(self, input_seqs):
        batch_size, seq_len = input_seqs.size()
        X_lengths = (input_seqs > 0).sum(1)
        self.hidden = self.init_hidden(batch_size)
        X = self.word_embedding(input_seqs)
        X = X.permute(0, 2, 1)
        X = F.relu(F.max_pool1d(self.conv1(X), self.nb_max_pool_size))
        X = self.conv1_dropout(X)
        if self.nb_conv1d_batchnorm:
            X = F.layer_norm(X, X.shape[1:])
            # X = self.conv_batchnorm(X)
        X = X.permute(0, 2, 1)
        X_lengths = (X_lengths - self.nb_conv1d_kernel_size) / self.nb_max_pool_size + 1
        X = torch.nn.utils.rnn.pack_padded_sequence(X, X_lengths, batch_first=True, enforce_sorted=False)
        _, self.hidden = self.lstm(X, self.hidden)
        X = self.hidden[0].view(self.nb_lstm_layers * batch_size, self.nb_lstm_units)
        X = self.lstm_hidden_dropout(X)
        if self.nb_lstm_batchnorm:
            X = self.lstm_batchnorm(X)
        X = self.encoder(X)
        return X

    def get_embeddings(self, X):
        encodings = self.get_encodings(X["input_seqs"])
        embeddings = self.embedder(encodings, X["subnetwork"])
        return embeddings

    def loss(self, Y_hat, Y, weights=None):
        Y = Y.type_as(Y_hat)
        return F.binary_cross_entropy(Y_hat, Y, weights, reduction="mean")

        # Y = Y.view(-1)
        #
        # # flatten all predictions
        # Y_hat = Y_hat.view(-1, self.n_classes)
        #
        # # create a mask by filtering out all tokens that ARE NOT the padding token
        # tag_pad_token = 0
        # mask = (Y > tag_pad_token).float()
        #
        # # count how many tokens we have
        #
        # nb_tokens = int(torch.sum(mask).data[0])
        # # pick the values for the label and zero out the rest with the mask
        # Y_hat = Y_hat[range(Y_hat.shape[0]), Y] * mask
        #
        # # compute cross entropy loss which ignores all <PAD> tokens
        # ce_loss = -torch.sum(Y_hat) / nb_tokens
        #
        # return ce_loss

    def training_step(self, batch, batch_nb):
        train_X, train_y, train_weights = batch
        input_seqs, subnetwork = train_X["input_seqs"], train_X["subnetwork"]

        Y_hat = self.forward(input_seqs, subnetwork)
        loss = self.loss(Y_hat, train_y, None)
        return {"loss": loss}

    def validation_step(self, batch, batch_nb):
        X, y, train_weights = batch
        input_seqs, subnetwork = X["input_seqs"], X["subnetwork"]
        Y_hat = self.forward(input_seqs, subnetwork)
        loss = self.loss(Y_hat, y, None)

        # self.update_metrics(Y_hat, y)
        return {"val_loss": loss,
                # "val_precision": self.precision.compute(),
                # "val_recall": self.recall.compute()
                }

    def validation_epoch_end(self, outputs):
        avg_loss = torch.stack([x["val_loss"] for x in outputs]).mean()
        tensorboard_logs = {"val_loss": avg_loss}

        results = {"avg_val_loss": avg_loss,
                   # "avg_val_precision": self.precision.compute(),
                   "log": tensorboard_logs}
        # self.reset_metrics()

        return results

    def init_metrics(self):
        self.precision = Precision(average=True, is_multilabel=True)
        self.recall = Recall(average=True, is_multilabel=True)

    def update_metrics(self, y_pred, y_true):
        self.precision.update(((y_pred > 0.5).type_as(y_true), y_true))
        self.recall.update(((y_pred > 0.5).type_as(y_true), y_true))

    def reset_metrics(self):
        self.precision.reset()
        self.recall.reset()

    def configure_optimizers(self):
        optimizer = torch.optim.Adam(self.parameters(), lr=1e-3)
        return optimizer
