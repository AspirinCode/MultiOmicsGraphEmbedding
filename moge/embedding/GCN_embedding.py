from kegra.layers.graph import GraphConvolution
from keras.layers import LSTM
from keras.regularizers import l2

from .siamese_triplet_online_embedding import *
from .static_graph_embedding import NeuralGraphEmbedding


class GCNEmbedding(NeuralGraphEmbedding):
    def __init__(self, d: int, method_name: str, x_features: list, y_label: str, n_labels: int):
        self.x_features = x_features
        self.y_label = y_label
        self.n_labels = n_labels
        self.support = 1
        super(GCNEmbedding, self).__init__(d, method_name)

    def create_network(self):
        input = Input(batch_shape=(None, None))  # (batch_number, sequence_length)
        x = Embedding(5, 4, input_length=None, mask_zero=True, trainable=True)(
            input)  # (batch_number, sequence_length, 5)
        print("Embedding", x)

        x = Lambda(lambda y: K.expand_dims(y, axis=2), name="lstm_lambda_1")(x)  # (batch_number, sequence_length, 1, 5)
        x = Conv2D(filters=320, kernel_size=(26, 1), activation='relu',
                   data_format="channels_last", name="lstm_conv_1")(x)  # (batch_number, sequence_length-5, 1, 192)
        x = Lambda(lambda y: K.squeeze(y, axis=2), name="lstm_lambda_2")(x)  # (batch_number, sequence_length-5, 192)
        print("conv2D", x)
        #     x = BatchNormalization(center=True, scale=True, name="conv1_batch_norm")(x)
        x = MaxPooling1D(pool_size=13, padding="same")(x)
        x = Dropout(0.2)(x)

        #     x = Convolution1D(filters=192, kernel_size=6, activation='relu', name="lstm_conv_2")(x)
        #     print("conv1d_2", x)
        # #     x = BatchNormalization(center=True, scale=True, name="conv2_batch_norm")(x)
        #     x = MaxPooling1D(pool_size=3, padding="same")(x)
        #     print("max pooling_2", x)
        #     x = Dropout(0.5)(x)

        x = Bidirectional(LSTM(64, return_sequences=False, return_state=False))(x)  # (batch_number, 320+320)
        print("brnn", x)
        x = Dropout(0.5)(x)

        x = Dense(self._d, activation='linear')(x)
        #     x = BatchNormalization(center=True, scale=True, name="embedding_output_normalized")(x)

        print("embedding", x)
        return Model(input, x, name="lstm_network")

    def build_keras_model(self, multi_gpu=False):
        K.clear_session()
        if multi_gpu:
            device = "/cpu:0"
            allow_soft_placement = True
        else:
            device = "/gpu:0"
            allow_soft_placement = False

        with tf.device(device):
            input_seqs = Input(batch_shape=(None, None), dtype=tf.int8, name="input_seqs")
            labels_directed = Input(batch_shape=(None, None), sparse=False, dtype=tf.float32,
                                    name="labels_directed")
            chromosome_name = Input(batch_shape=(None, 323), sparse=False, dtype=tf.uint8,
                                    name="chromosome_name")
            transcript_start = Input(batch_shape=(None, 1), sparse=False, dtype=tf.float32,
                                     name="transcript_start")
            transcript_end = Input(batch_shape=(None, 1), sparse=False, dtype=tf.float32,
                                   name="transcript_end")
            print("labels_directed", labels_directed)

            # build create_network to use in each siamese 'leg'
            lstm_network = self.create_network()

            # encode each of the inputs into a list of embedding vectors with the conv_lstm_network
            x = lstm_network(input_seqs)
            print("embeddings", x)

            #     x = Dropout(0.5)(x)
            x = GraphConvolution(128, self.support,
                                 activation='relu',
                                 kernel_regularizer=l2(5e-4),
                                 use_bias=False)([x, labels_directed])
            x = Dropout(0.5)(x)

            x = GraphConvolution(64, self.support, name="embedding_output",
                                 activation='relu',
                                 use_bias=False)([x, labels_directed])
            x = Dropout(0.5)(x)

            y_pred = Dense(self.n_labels, activation='sigmoid')(x)

            self.model = Model(inputs=[input_seqs, labels_directed, chromosome_name, transcript_start, transcript_end],
                               outputs=y_pred)

        # Multi-gpu parallelization
        if multi_gpu:
            self.model = multi_gpu_model(self.model, gpus=4, cpu_merge=True, cpu_relocation=True)

        # Compile & train
        self.model.compile(
            loss="binary_crossentropy",
            optimizer="adam",
            metrics=["top_k_categorical_accuracy",
                     #              precision_m, recall_m
                     ],
        )
        print("Network total weights:", self.model.count_params())

    def learn_embedding(self, generator_train, generator_test, tensorboard=True, histogram_freq=0,
                        embeddings=False, early_stopping=False,
                        multi_gpu=False, subsample=True, n_steps=500, validation_steps=None,
                        edge_f=None, is_weighted=False, no_python=False, rebuild_model=False, seed=0,
                        **kwargs):
        self.generator_train = generator_train
        self.generator_test = generator_test
        try:
            self.hist = self.model.fit_generator(generator_train, epochs=self.epochs, shuffle=False,
                                                 validation_data=generator_test,
                                                 validation_steps=validation_steps,
                                                 callbacks=self.get_callbacks(early_stopping, tensorboard,
                                                                              histogram_freq, embeddings),
                                                 use_multiprocessing=True, workers=8, **kwargs)
        except KeyboardInterrupt:
            print("Stop training")

    def get_callbacks(self, early_stopping=0, tensorboard=True, histogram_freq=0, embeddings=False, write_grads=False):
        callbacks = []
        if tensorboard:
            if not hasattr(self, "tensorboard"):
                self.build_tensorboard(histogram_freq=histogram_freq, embeddings=embeddings, write_grads=write_grads)
            callbacks.append(self.tensorboard)

        if early_stopping > 0:
            if not hasattr(self, "early_stopping"):
                self.early_stopping = EarlyStopping(monitor='val_loss', min_delta=0, patience=early_stopping, verbose=0,
                                                    mode='auto',
                                                    baseline=None, restore_best_weights=False)
            callbacks.append(self.early_stopping)

        if len(callbacks) == 0: callbacks = None
        return callbacks

    def build_tensorboard(self, histogram_freq, embeddings: bool, write_grads):
        if not hasattr(self, "log_dir"):
            self.log_dir = "logs/{}_{}".format(type(self).__name__[0:20], time.strftime('%m-%d_%H-%M%p').strip(" "))
            print("log_dir:", self.log_dir)

        if embeddings:
            x_test, node_labels = self.generator_test.load_data(return_node_names=True, y_label=self.y_label)
            if not os.path.exists(self.log_dir): os.makedirs(self.log_dir)
            with open(os.path.join(self.log_dir, "metadata.tsv"), 'w') as f:
                np.savetxt(f, node_labels, fmt="%s")
                f.close()

        self.tensorboard = TensorBoard(
            log_dir=self.log_dir,
            histogram_freq=histogram_freq,
            write_grads=write_grads, write_graph=False, write_images=False,
            batch_size=self.batch_size,
            update_freq="epoch",
            embeddings_freq=1 if embeddings else 0,
            embeddings_metadata=os.path.join(self.log_dir, "metadata.tsv") if embeddings else None,
            embeddings_data=x_test if embeddings else None,
            embeddings_layer_names=["embedding_output"] if embeddings else None,
        )
        # Add params text to tensorboard
