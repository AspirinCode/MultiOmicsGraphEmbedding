from keras.backend.tensorflow_backend import set_session
from keras.layers import Layer
from keras.optimizers import Adam, Adagrad

from moge.embedding.siamese_graph_embedding import *
from moge.embedding.siamese_graph_embedding import SiameseGraphEmbedding
from moge.network.heterogeneous_network import HeterogeneousNetwork
from moge.network.triplet_generator import SampledTripletDataGenerator, OnlineTripletGenerator


class SiameseTripletGraphEmbedding(SiameseGraphEmbedding):
    def __init__(self, d=128, margin=0.2, batch_size=2048, lr=0.001, epochs=10, directed_proba=0.5, weighted=True,
                 compression_func="sqrt", negative_sampling_ratio=2.0, max_length=1400, truncating="post", seed=0,
                 verbose=False, conv1_kernel_size=12, conv1_batch_norm=False, max1_pool_size=6, conv2_kernel_size=6,
                 conv2_batch_norm=True, max2_pool_size=3,
                 lstm_unit_size=320, dense1_unit_size=1024, dense2_unit_size=512,
                 directed_distance="euclidean", undirected_distance="euclidean", source_target_dense_layers=True,
                 embedding_normalization=False,
                 **kwargs):
        super().__init__(d, margin, batch_size, lr, epochs, directed_proba, weighted, compression_func,
                         negative_sampling_ratio,
                         max_length, truncating, seed, verbose, conv1_kernel_size, conv1_batch_norm, max1_pool_size,
                         conv2_kernel_size, conv2_batch_norm,
                         max2_pool_size, lstm_unit_size, dense1_unit_size, dense2_unit_size,
                         directed_distance, undirected_distance, source_target_dense_layers,
                         embedding_normalization,
                         **kwargs)

    def identity_loss(self, y_true, y_pred):
        return K.mean(y_pred - 0 * y_true)

    def triplet_loss(self, inputs):
        encoded_i, encoded_j, encoded_k, is_directed = inputs

        positive_distance = Lambda(self.st_euclidean_distance, name="lambda_positive_distances")([encoded_i, encoded_j, is_directed])
        negative_distance = Lambda(self.st_euclidean_distance, name="lambda_negative_distances")([encoded_i, encoded_k, is_directed])
        return K.mean(K.maximum(0.0, positive_distance - negative_distance + self.margin))

    def build_keras_model(self, multi_gpu=False):
        if multi_gpu:
            device = "/cpu:0"
            allow_soft_placement = True
        else:
            device = "/gpu:0"
            allow_soft_placement = False

        K.clear_session()
        tf.reset_default_graph()
        config = tf.ConfigProto(allow_soft_placement=allow_soft_placement, log_device_placement=True)
        self.sess = tf.Session(config=config)
        set_session(self.sess)

        with tf.device(device):
            input_seq_i = Input(batch_shape=(self.batch_size, None), name="input_seq_i")
            input_seq_j = Input(batch_shape=(self.batch_size, None), name="input_seq_j")
            input_seq_k = Input(batch_shape=(self.batch_size, None), name="input_seq_k")
            is_directed = Input(batch_shape=(self.batch_size, 1), dtype=tf.int8, name="is_directed")

            # build create_lstm_network to use in each siamese 'leg'
            self.lstm_network = self.create_lstm_network()

            # encode each of the two inputs into a vector with the conv_lstm_network
            encoded_i = self.lstm_network(input_seq_i)
            print(encoded_i) if self.verbose else None
            encoded_j = self.lstm_network(input_seq_j)
            print(encoded_j) if self.verbose else None
            encoded_k = self.lstm_network(input_seq_k)
            print(encoded_k) if self.verbose else None

            output = Lambda(self.triplet_loss, name="lambda_triplet_loss_output")([encoded_i, encoded_j, encoded_k, is_directed])

            self.siamese_net = Model(inputs=[input_seq_i, input_seq_j, input_seq_k, is_directed], outputs=output)

        # Multi-gpu parallelization
        if multi_gpu:
            self.siamese_net = multi_gpu_model(self.siamese_net, gpus=4, cpu_merge=True, cpu_relocation=False)

        self.build_tensorboard()
        # Compile & train
        self.siamese_net.compile(loss=self.identity_loss,  # binary_crossentropy, cross_entropy, contrastive_loss
                                 optimizer=Adam(lr=self.lr, beta_1=0.9, beta_2=0.999, epsilon=0.1),
                                 )
        print("Network total weights:", self.siamese_net.count_params()) if self.verbose else None

    def learn_embedding(self, network: HeterogeneousNetwork, network_val=None, multi_gpu=False,
                        subsample=True, n_steps=500, validation_steps=None, tensorboard=True, histogram_freq=0,
                        early_stopping=False,
                        edge_f=None, is_weighted=False, no_python=False, rebuild_model=False, seed=0, **kwargs):

        generator_train = self.get_training_data_generator(network, n_steps, seed)

        if network_val is not None:
            self.generator_val = SampledTripletDataGenerator(network=network_val, weighted=self.weighted,
                                                             maxlen=self.max_length, padding='post', truncating="post",
                                                             tokenizer=generator_train.tokenizer,
                                                             negative_sampling_ratio=1.0,
                                                             batch_size=self.batch_size, shuffle=True, seed=seed, verbose=self.verbose) \
                if not hasattr(self, "generator_val") else self.generator_val
        else:
            self.generator_val = None
        assert generator_train.tokenizer.word_index == self.generator_val.tokenizer.word_index

        if not hasattr(self, "siamese_net") or rebuild_model: self.build_keras_model(multi_gpu)
        if histogram_freq > 0:
            self.tensorboard.histogram_freq = histogram_freq
            self.generator_val = self.generator_val.__getitem__(0) if type(
                self.generator_val) == OnlineTripletGenerator else self.generator_val
        try:
            self.hist = self.siamese_net.fit_generator(generator_train, epochs=self.epochs,
                                                       validation_data=self.generator_val,
                                                       validation_steps=validation_steps,
                                                       callbacks=self.get_callbacks(early_stopping, tensorboard),
                                                       use_multiprocessing=True, workers=8, **kwargs)
        except KeyboardInterrupt:
            print("Stop training")
        finally:
            self.save_network_weights()

    def get_training_data_generator(self, network, n_steps=250, seed=0):
        if not hasattr(self, "generator_train"):
            self.generator_train = SampledTripletDataGenerator(network=network, weighted=self.weighted,
                                                               compression_func=self.compression_func, n_steps=n_steps,
                                                               maxlen=self.max_length, padding='post',
                                                               truncating=self.truncating,
                                                               negative_sampling_ratio=self.negative_sampling_ratio,
                                                               directed_proba=self.directed_proba,
                                                               batch_size=self.batch_size, shuffle=True, seed=seed,
                                                               verbose=self.verbose)
        else:
            return self.generator_train
        self.node_list = self.generator_train.node_list
        return self.generator_train

    def get_reconstructed_adj(self, beta=2.0, X=None, node_l=None, node_l_b=None, edge_type="d", interpolate=False):
        """
        :param X:
        :param node_l: list of node names
        :param edge_type:
        :return:
        """
        if hasattr(self, "reconstructed_adj") and edge_type=="d":
            adj = self.reconstructed_adj
        else:
            embs = self.get_embedding()
            assert len(self.node_list) == embs.shape[0]

            adj = self._pairwise_similarity(embs, edge_type)

        if interpolate:
            adj = np.interp(adj, (adj.min(), adj.max()), (0, 1))
        if (node_l is None or node_l == self.node_list):
            if edge_type == "d": self.reconstructed_adj = adj  # Cache reconstructed_adj to memory for faster recall
            return adj
        elif set(node_l) < set(self.node_list):
            return self._select_adj_indices(adj, node_l, node_l_b)
        else:
            raise Exception("A node in node_l is not in self.node_list.")

    def _pairwise_similarity(self, embeddings, edge_type="d"):
        if edge_type == 'd':
            embeddings_X = embeddings[:, 0:int(self._d / 2)]
            embeddings_Y = embeddings[:, int(self._d / 2):self._d]

            if self.directed_distance == "euclidean":
                adj = pairwise_distances(X=embeddings_X,
                                         Y=embeddings_Y,
                                         metric="euclidean", n_jobs=-2)

                # Get node-specific adaptive threshold
                # adj = self.transform_adj_adaptive_threshold(adj, margin=self.margin)
                print("Euclidean with adaptive threshold")
                np.exp(-2.0 * adj)
                # adj = adj.T  # Transpose
                # adj = -adj
                print("Euclidean dist")

            elif self.directed_distance == "cosine":
                adj = pairwise_distances(X=embeddings_X,
                                         Y=embeddings_Y,
                                         metric="cosine", n_jobs=-2)
                # adj = adj.T  # Transpose
                print("Cosine similarity")

            elif self.directed_distance == "dot_sigmoid":
                adj = np.matmul(embeddings_X, embeddings_Y.T)
                adj = sigmoid(adj)
                print("Dot product & sigmoid")

        elif edge_type == 'u':
            if self.undirected_distance == "euclidean":
                adj = pairwise_distances(X=embeddings,
                                         metric="euclidean", n_jobs=-2)
                adj = np.exp(-2.0 * adj)
                # adj = self.transform_adj_adaptive_threshold(adj, margin=self.margin/2)
                print("Euclidean dist")

            elif self.undirected_distance == "cosine":
                adj = pairwise_distances(X=embeddings,
                                         metric="cosine", n_jobs=-2)

            elif self.undirected_distance == "dot_sigmoid":
                adj = np.matmul(embeddings, embeddings.T)
                adj = sigmoid(adj)
        else:
            raise Exception("Unsupported edge_type", edge_type)
        return adj

    def transform_adj_adaptive_threshold(self, adj_pred, margin=0.2):
        network_adj = self.generator_train.network.get_adjacency_matrix(edge_types="d",
                                                                        node_list=self.node_list)
        self.distance_threshold = self.get_adaptive_threshold(adj_pred, network_adj, margin)

        predicted_adj = np.zeros(adj_pred.shape)
        for node_id in range(predicted_adj.shape[0]):
            predicted_adj[node_id, :] = (adj_pred[node_id, :] < self.distance_threshold).astype(float)
        adj_pred = predicted_adj
        return adj_pred

    def get_adaptive_threshold(self, adj_pred, adj_true, margin):
        distance_threshold = np.zeros((len(self.node_list),))
        for nonzero_node_id in np.unique(adj_true.nonzero()[0]):
            _, nonzero_node_cols = adj_true[nonzero_node_id].nonzero()
            positive_distances = adj_pred[nonzero_node_id, nonzero_node_cols]
            distance_threshold[nonzero_node_id] = np.min(positive_distances)
        median_threshold = np.min(distance_threshold[distance_threshold > 0]) + margin / 2
        distance_threshold[distance_threshold == 0] = median_threshold
        return distance_threshold


class SiameseOnlineTripletGraphEmbedding(SiameseTripletGraphEmbedding):
    def __init__(self, d=128, margin=0.2, batch_size=256, lr=0.001, epochs=10, directed_proba=0.5, weighted=True,
                 compression_func="sqrt", negative_sampling_ratio=2.0, max_length=1400, truncating="post", seed=0,
                 verbose=False, conv1_kernel_size=12, conv1_batch_norm=False, max1_pool_size=6, conv2_kernel_size=6,
                 conv2_batch_norm=True, max2_pool_size=3, lstm_unit_size=320, dense1_unit_size=1024,
                 dense2_unit_size=512,
                 directed_distance="euclidean", undirected_distance="euclidean", source_target_dense_layers=True,
                 embedding_normalization=False, **kwargs):
        self.directed_margin = margin
        self.undirected_margin = margin / 2
        print("directed_margin", self.directed_margin, ", undirected_margin", self.undirected_margin)
        assert directed_proba <= 1.0 and directed_proba >= 0, "directed_proba must be in [0, 1.0] range"
        super().__init__(d, margin, batch_size, lr, epochs, directed_proba, weighted, compression_func,
                         negative_sampling_ratio,
                         max_length, truncating, seed, verbose, conv1_kernel_size, conv1_batch_norm, max1_pool_size,
                         conv2_kernel_size, conv2_batch_norm, max2_pool_size, lstm_unit_size, dense1_unit_size,
                         dense2_unit_size,
                         directed_distance, undirected_distance, source_target_dense_layers, embedding_normalization,
                         **kwargs)

    def custom_recall(self, inputs):
        pairwise_distances, labels = inputs
        y_pred = tf.gather_nd(pairwise_distances, labels.indices)
        y_true = labels.values

        def _recall(_y_true, _y_pred):
            return recall_d(y_true, y_pred)

        return _recall

    def custom_precision(self, inputs):
        pairwise_distances, labels = inputs
        y_pred = tf.gather_nd(pairwise_distances, labels.indices)
        y_true = labels.values

        def _precision(_y_true, _y_pred):
            return precision_d(y_true, y_pred)

        return _precision

    def pairwise_distances(self, embeddings, directed=True, squared=True):
        embeddings_s = embeddings[:, 0:int(self._d / 2)]
        embeddings_t = embeddings[:, int(self._d / 2):self._d]
        if directed:
            return self._pairwise_euclidean(embeddings_s, embeddings_t, squared)
        else:
            return self._pairwise_euclidean(embeddings, embeddings, squared)
            # return K.minimum(self._pairwise_euclidean(embeddings_s, embeddings_s, squared),
            #                  self._pairwise_euclidean(embeddings_t, embeddings_t, squared))

    def _pairwise_euclidean(self, embeddings_s, embeddings_t, squared):
        dot_product = K.dot(embeddings_s, K.transpose(embeddings_t))
        square_norm = tf.diag_part(dot_product)
        distances = K.expand_dims(square_norm, 1) - 2.0 * dot_product + K.expand_dims(square_norm, 0)
        distances = K.maximum(distances, 0.0)
        if not squared:
            mask = tf.to_float(tf.equal(distances, 0.0))
            distances = distances + mask * 1e-16
            distances = tf.sqrt(distances)
            # Correct the epsilon added: set the distances on the mask to be exactly 0.0
            distances = distances * (1.0 - mask)
        return distances

    def batch_contrastive_loss(self, inputs):
        pairwise_distance_directed, pairwise_distance_undirected, labels_directed, labels_undirected = inputs
        y_pred_directed = tf.gather_nd(pairwise_distance_directed, labels_directed.indices)
        y_true_directed = K.round(labels_directed.values)

        y_pred_undirected = tf.gather_nd(pairwise_distance_undirected, labels_undirected.indices)
        y_true_undirected = K.round(labels_undirected.values)
        def _contrastive_loss(_y_true, _y_pred):
            return contrastive_loss(y_true_directed, y_pred_directed) + contrastive_loss(y_true_undirected,
                                                                                         y_pred_undirected)

        return _contrastive_loss

    def batch_kl_divergence_loss(self, inputs):
        pairwise_similarity_directed, pairwise_similarity_undirected, labels_directed, labels_undirected = inputs
        y_pred_directed = tf.gather_nd(pairwise_similarity_directed, labels_directed.indices + 5)
        y_true_directed = K.round(labels_directed.values)

        y_pred_undirected = tf.gather_nd(pairwise_similarity_undirected, labels_undirected.indices + 5)
        y_true_undirected = K.round(labels_undirected.values)
        def _kl_loss(_y_true, _y_pred):
            return cross_entropy(y_true_directed, y_pred_directed) + \
                   cross_entropy(y_true_undirected, y_pred_undirected)

        return _kl_loss

    def pairwise_similarity(self, embeddings, directed=True):
        embeddings_s = embeddings[:, 0:int(self._d / 2)]
        embeddings_t = embeddings[:, int(self._d / 2):self._d]
        if directed:

            return self._pairwise_sigmoid(embeddings_s, embeddings_t)
        else:
            return self._pairwise_sigmoid(embeddings, embeddings)

    def _pairwise_sigmoid(self, embeddings_s, embeddings_t):
        dot_product = K.dot(embeddings_s, K.transpose(embeddings_t))
        sigmoid = K.sigmoid(dot_product)
        return sigmoid

    def build_keras_model(self, multi_gpu=False):
        if multi_gpu:
            device = "/cpu:0"
            allow_soft_placement = True
        else:
            device = "/gpu:0"
            allow_soft_placement = False

        K.clear_session()
        tf.reset_default_graph()
        # config = tf.ConfigProto(allow_soft_placement=allow_soft_placement)
        # self.sess = tf.Session(config=config)
        # set_session(self.sess)

        with tf.device(device):
            input_seqs = Input(batch_shape=(None, None), dtype=tf.int8, name="input_seqs")
            labels_directed = Input(batch_shape=(None, None), sparse=True, dtype=tf.float32,
                                    name="labels_directed")
            labels_undirected = Input(batch_shape=(None, None), sparse=True, dtype=tf.float32,
                                      name="labels_undirected")
            print("labels_directed", labels_directed) if self.verbose else None
            print("labels_undirected", labels_undirected) if self.verbose else None

            # build create_lstm_network to use in each siamese 'leg'
            self.lstm_network = self.create_lstm_network()

            # encode each of the inputs into a list of embedding vectors with the conv_lstm_network
            embeddings = self.lstm_network(input_seqs)
            print("embeddings", embeddings) if self.verbose else None

            # directed_pairwise_distances = Lambda(lambda x: self.pairwise_distances(x, directed=True),
            #                                      name="directed_pairwise_distances")(embeddings)
            # undirected_pairwise_distances = Lambda(lambda x: self.pairwise_distances(x, directed=False),
            #                                        name="undirected_pairwise_distances")(embeddings)
            # print("directed_pairwise_distances", directed_pairwise_distances) if self.verbose else None

            self.triplet_loss = OnlineTripletLoss(directed_margin=self.margin, undirected_margin=self.margin,
                                                  undirected_weight=self.directed_proba,
                                                  directed_distance=self.directed_distance,
                                                  undirected_distance=self.undirected_distance)
            output = self.triplet_loss([embeddings, labels_directed, labels_undirected])
            print("output", output) if self.verbose else None

            self.siamese_net = Model(inputs=[input_seqs, labels_directed, labels_undirected], outputs=output)

            # Multi-gpu parallelization
            if multi_gpu:
                self.siamese_net = multi_gpu_model(self.siamese_net, gpus=4, cpu_merge=True, cpu_relocation=False)

            # Build tensorboard
            self.build_tensorboard()

            # Compile & train
            self.siamese_net.compile(loss=self.identity_loss,
                                     # loss=self.batch_contrastive_loss([directed_pairwise_distances,
                                     #                                   undirected_pairwise_distances,
                                     #                                   labels_directed, labels_undirected]),
                                     optimizer=Adagrad(),
                                     # metrics=[self.custom_recall([directed_pairwise_distances, labels_directed]),
                                     #          self.custom_precision([directed_pairwise_distances, labels_directed])] if \
                                     #     self.directed_distance == "euclidean" else None,

                                     )
            print("Network total weights:", self.siamese_net.count_params()) if self.verbose else None


    def learn_embedding(self, network: HeterogeneousNetwork, network_val=None, tensorboard=False, histogram_freq=0,
                        early_stopping=False, multi_gpu=False, subsample=True, n_steps=500, validation_steps=None,
                        edge_f=None, is_weighted=False, no_python=False, rebuild_model=False, seed=0, **kwargs):
        generator_train = self.get_training_data_generator(network, n_steps, seed)

        if network_val is not None:
            self.generator_val = OnlineTripletGenerator(network=network_val, weighted=self.weighted,
                                                        maxlen=self.max_length, padding='post', truncating="post",
                                                        tokenizer=generator_train.tokenizer,
                                                        negative_sampling_ratio=5.0, n_steps=int(n_steps / 5),
                                                        batch_size=self.batch_size, shuffle=True, seed=seed,
                                                        verbose=self.verbose) \
                if not hasattr(self, "generator_val") else self.generator_val
        else:
            self.generator_val = None
        assert generator_train.tokenizer.word_index == self.generator_val.tokenizer.word_index

        if not hasattr(self, "siamese_net") or rebuild_model: self.build_keras_model(multi_gpu)

        if histogram_freq > 0:
            self.tensorboard.histogram_freq = histogram_freq
            self.generator_val = self.generator_val.__getitem__(0) if type(
                self.generator_val) == OnlineTripletGenerator else self.generator_val
        try:
            self.hist = self.siamese_net.fit_generator(generator_train, epochs=self.epochs,
                                                       validation_data=self.generator_val,
                                                       validation_steps=validation_steps,
                                                       callbacks=self.get_callbacks(early_stopping, tensorboard),
                                                       use_multiprocessing=True, workers=8, **kwargs)
        except KeyboardInterrupt:
            print("Stop training")
        finally:
            self.save_network_weights()

    def get_training_data_generator(self, network, n_steps=250, seed=0):
        self.generator_train = OnlineTripletGenerator(network=network, weighted=self.weighted,
                                                      compression_func=self.compression_func,
                                                      n_steps=n_steps,
                                                      maxlen=self.max_length, padding='post',
                                                      truncating=self.truncating,
                                                      negative_sampling_ratio=self.negative_sampling_ratio,
                                                      directed_proba=self.directed_proba,
                                                      batch_size=self.batch_size, shuffle=True, seed=seed,
                                                      verbose=self.verbose) \
            if not hasattr(self, "generator_train") else self.generator_train
        self.node_list = self.generator_train.node_list
        return self.generator_train


class OnlineTripletLoss(Layer):
    def __init__(self, directed_margin=0.2, undirected_margin=0.1, undirected_weight=1.0, directed_distance="euclidean",
                 undirected_distance="euclidean",
                 **kwargs):
        super(OnlineTripletLoss, self).__init__(**kwargs)
        self.output_dim = ()
        self.directed_margin = directed_margin
        self.undirected_margin = undirected_margin
        self.undirected_weight = undirected_weight
        self.directed_distance = directed_distance
        self.undirected_distance = undirected_distance

    def build(self, input_shape):
        assert isinstance(input_shape, list)
        embeddings_shape, labels_directed_shape, labels_undirected_shape = input_shape
        self._d = int(embeddings_shape[-1])

        super(OnlineTripletLoss, self).build(input_shape)

    def compute_output_shape(self, input_shape):
        return ()

    def call(self, input, **kwargs):
        assert isinstance(input, list), "(embeddings, labels_directed, labels_undirected) expected"
        embeddings, labels_directed, labels_undirected = input

        embeddings_s = embeddings[:, : int(self._d / 2)]
        embeddings_t = embeddings[:, int(self._d / 2):]

        directed_loss = batch_hard_triplet_loss(embeddings_s, embeddings_t, labels=labels_directed,
                                                margin=self.directed_margin, squared=True,
                                                distance=self.directed_distance)
        print("labels_directed", labels_directed)
        if self.undirected_weight > 0.0:
            undirected_loss = batch_hard_triplet_loss(embeddings, embeddings, labels=labels_undirected,
                                                      margin=self.undirected_margin, squared=True,
                                                      distance=self.undirected_distance)
            return tf.add(directed_loss, self.undirected_weight * undirected_loss)
        else:
            return directed_loss


def _pairwise_distances(embeddings_A, embeddings_B, squared=False):
    """Compute the 2D matrix of distances between all the embeddings.
    Args:
        embeddings: tensor of shape (batch_size, embed_dim)
        squared: Boolean. If true, output is the pairwise squared euclidean distance matrix.
                 If false, output is the pairwise euclidean distance matrix.
    Returns:
        pairwise_distances: tensor of shape (batch_size, batch_size)
    """
    # Get the dot product between all embeddings
    # shape (batch_size, batch_size)
    dot_product = tf.matmul(embeddings_A, embeddings_B, transpose_b=True)

    # Get squared L2 norm for each embedding. We can just take the diagonal of `dot_product`.
    # This also provides more numerical stability (the diagonal of the result will be exactly 0).
    # shape (batch_size,)
    square_norm = tf.diag_part(dot_product)

    # Compute the pairwise distance matrix as we have:
    # ||a - b||^2 = ||a||^2  - 2 <a, b> + ||b||^2
    # shape (batch_size, batch_size)
    distances = tf.expand_dims(square_norm, 1) - 2.0 * dot_product + tf.expand_dims(square_norm, 0)

    # Because of computation errors, some distances might be negative so we put everything >= 0.0
    distances = tf.maximum(distances, 0.0)

    if not squared:
        # Because the gradient of sqrt is infinite when distances == 0.0 (ex: on the diagonal)
        # we need to add a small epsilon where distances == 0.0
        mask = tf.to_float(tf.equal(distances, 0.0))
        distances = distances + mask * 1e-16

        distances = tf.sqrt(distances)

        # Correct the epsilon added: set the distances on the mask to be exactly 0.0
        distances = distances * (1.0 - mask)

    return distances
def _pairwise_dot_sigmoid_similarity(embeddings_A, embeddings_B):
    dot_product = tf.matmul(embeddings_A, embeddings_B, transpose_b=True)
    sigmoids = tf.sigmoid(dot_product)
    return sigmoids
def _pairwise_cosine_similarity(embeddings_A, embeddings_B):
    normalize_a = tf.nn.l2_normalize(embeddings_A, axis=-1)
    normalize_b = tf.nn.l2_normalize(embeddings_B, axis=-1)
    cosine_similarities = tf.matmul(normalize_a, normalize_b, transpose_b=True)
    return cosine_similarities
# def _pairwise_alpha_similarity(embeddings_A, embeddings_B):

def _get_anchor_positive_triplet_mask(labels):
    """Return a 2D mask where mask[a, p] is True iff a and p have a positive edge weight > 0.5.
    Args:
        labels: tf.float32 `Sparse Tensor` with shape [batch_size, batch_size]
    Returns:
        mask: tf.bool `Tensor` with shape [batch_size, batch_size]
    """
    positive_edges = tf.sparse_retain(labels, tf.greater(labels.values, 0.5))
    positive_mask = tf.sparse.to_dense(tf.SparseTensor(positive_edges.indices,
                                                       tf.ones_like(positive_edges.values, dtype=tf.bool),
                                                       labels.dense_shape),
                                       default_value=False)
    return positive_mask
def _get_anchor_negative_triplet_mask(labels):
    """Return a 2D mask where mask[a, n] is True iff a and n have a negative edge weight (e.g. < 0.5).
    Args:
        labels: tf.float32 `Sparse Tensor` with shape [batch_size, batch_size]
    Returns:
        mask: tf.bool `Tensor` with shape [batch_size, batch_size]
    """
    negative_edges = tf.sparse_retain(labels, tf.less(labels.values, 0.5))
    negative_mask = tf.sparse.to_dense(tf.SparseTensor(negative_edges.indices,
                                                       tf.ones_like(negative_edges.values, dtype=tf.bool),
                                                       labels.dense_shape),
                                       default_value=False)
    return negative_mask

def batch_hard_triplet_loss(embeddings_U, embeddings_V, labels, margin, squared=True, distance="euclidean"):
    """Build the triplet loss over a batch of embeddings.
    For each anchor, we get the hardest positive and hardest negative to form a triplet.
    Args:
        labels: labels of the batch, of size (batch_size,)
        embeddings: tensor of shape (batch_size, embed_dim)
        margin: margin for triplet loss
        squared: Boolean. If true, output is the pairwise squared euclidean distance matrix.
                 If false, output is the pairwise euclidean distance matrix.
    Returns:
        triplet_loss: scalar tensor containing the triplet loss
        :param directed:
        :param distance:
    """
    # Get the pairwise distance matrix
    if distance == "euclidean":
        pairwise_dist = _pairwise_distances(embeddings_U, embeddings_V, squared=squared)
    elif distance == "dot_sigmoid":
        pairwise_dist = 1 - _pairwise_dot_sigmoid_similarity(embeddings_U, embeddings_V)
    elif distance == "cosine":
        pairwise_dist = 1 - _pairwise_cosine_similarity(embeddings_U, embeddings_V)

    # For each anchor, get the hardest positive
    # First, we need to get a mask for every valid positive (they should have same label)
    mask_anchor_positive = _get_anchor_positive_triplet_mask(labels)
    mask_anchor_positive = tf.to_float(mask_anchor_positive)

    # We put to 0 any element where (a, p) is not a connection
    anchor_positive_dist = tf.multiply(mask_anchor_positive, pairwise_dist)

    # shape (batch_size, 1)
    hardest_positive_dist = tf.reduce_max(anchor_positive_dist, axis=1, keepdims=True)
    tf.summary.scalar("hardest_positive_dist", tf.reduce_mean(hardest_positive_dist))

    # For each anchor, get the hardest negative
    # First, we need to get a mask for every valid negative (they should have different labels)
    mask_anchor_negative = _get_anchor_negative_triplet_mask(labels)
    mask_anchor_negative = tf.to_float(mask_anchor_negative)

    # We add the maximum value in each row to the invalid negatives (label(a) == label(n))
    max_anchor_negative_dist = tf.reduce_max(pairwise_dist, axis=1, keepdims=True)
    anchor_negative_dist = pairwise_dist + max_anchor_negative_dist * (1.0 - mask_anchor_negative)

    # shape (batch_size,)
    hardest_negative_dist = tf.reduce_min(anchor_negative_dist, axis=1, keepdims=True)
    tf.summary.scalar("hardest_negative_dist", tf.reduce_mean(hardest_negative_dist))

    # Combine biggest d(a, p) and smallest d(a, n) into final triplet loss
    triplet_loss = tf.maximum(hardest_positive_dist - hardest_negative_dist + margin, 0.0)

    # Get final mean triplet loss
    triplet_loss = tf.reduce_mean(triplet_loss)

    return triplet_loss


def frobenius_norm_loss(embeddings_B, embeddings_A, labels: tf.SparseTensor, squared=True, distance="euclidean"):
    """
    Args:
        labels: labels of the batch, of size (batch_size,)
        embeddings: tensor of shape (batch_size, embed_dim)
        margin: margin for triplet loss
        squared: Boolean. If true, output is the pairwise squared euclidean distance matrix.
                 If false, output is the pairwise euclidean distance matrix.
    Returns:
        triplet_loss: scalar tensor containing the triplet loss
    """
    # Get the pairwise distance matrix
    if distance == "euclidean":
        pairwise_similarity = tf.exp(-_pairwise_distances(embeddings_A, embeddings_B, squared=squared))
    elif distance == "dot_sigmoid":
        pairwise_similarity = _pairwise_dot_sigmoid_similarity(embeddings_A, embeddings_B)
    elif distance == "cosine":
        pairwise_similarity = _pairwise_cosine_similarity(embeddings_A, embeddings_B)

    y_pred = tf.gather_nd(pairwise_similarity, labels.indices)
    frobenius_norm_loss = tf.norm(tf.subtract(y_pred, labels.values), ord=2)

    return frobenius_norm_loss


def batch_constrastive_loss(embeddings_B, embeddings_A, labels: tf.SparseTensor, squared=True, distance="euclidean"):
    if distance == "euclidean":
        pairwise_distances = _pairwise_distances(embeddings_A, embeddings_B, squared=squared)
    elif distance == "dot_sigmoid":
        pairwise_distances = 1 - _pairwise_dot_sigmoid_similarity(embeddings_A, embeddings_B)
    elif distance == "cosine":
        pairwise_distances = 1 - _pairwise_cosine_similarity(embeddings_A, embeddings_B)

    y_pred = tf.gather_nd(pairwise_distances, labels.indices)
    y_true = K.round(labels.values)
    constrastive_loss = contrastive_loss(y_true, y_pred)

    return constrastive_loss
