import torch

import pytorch_lightning as pl

import torch
import torch.nn as nn


class FocalLoss(nn.Module):
    """Softmax focal loss
    references: Focal Loss for Dense Object Detection
                https://github.com/Hsuxu/FocalLoss-PyTorch
    """

    def __init__(self, label_size, activation_type="SOFTMAX",
                 gamma=2.0, alpha=0.25, epsilon=1.e-9):
        super(FocalLoss, self).__init__()
        self.num_cls = label_size
        self.activation_type = activation_type
        self.gamma = gamma
        self.alpha = alpha
        self.epsilon = epsilon

    def forward(self, logits, target):
        """
        Args:
            logits: model's output, shape of [batch_size, num_cls]
            target: ground truth labels, shape of [batch_size]
        Returns:
            shape of [batch_size]
        """
        if self.activation_type == "SOFTMAX":
            idx = target.view(-1, 1).long()
            one_hot_key = torch.zeros(idx.size(0), self.num_cls,
                                      dtype=torch.float)
            one_hot_key = one_hot_key.scatter_(1, idx, 1)
            logits = torch.softmax(logits, dim=-1)
            loss = -self.alpha * one_hot_key * \
                   torch.pow((1 - logits), self.gamma) * \
                   (logits + self.epsilon).log()
            loss = loss.sum(1)
        elif self.activation_type == "SIGMOID":
            multi_hot_key = target
            logits = torch.sigmoid(logits)
            zero_hot_key = 1 - multi_hot_key
            loss = -self.alpha * multi_hot_key * \
                   torch.pow((1 - logits), self.gamma) * \
                   (logits + self.epsilon).log()
            loss += -(1 - self.alpha) * zero_hot_key * \
                    torch.pow(logits, self.gamma) * \
                    (1 - logits + self.epsilon).log()
        else:
            raise TypeError("Unknown activation type: " + self.activation_type
                            + "Supported activation types: ")
        return loss.mean()


class ClassificationLoss(torch.nn.Module):
    def __init__(self, n_classes, class_weight=None,
                 loss_type="SOFTMAX_CROSS_ENTROPY"):
        super(ClassificationLoss, self).__init__()
        self.label_size = n_classes
        self.loss_type = loss_type
        if loss_type == "SOFTMAX_CROSS_ENTROPY":
            self.criterion = torch.nn.CrossEntropyLoss(class_weight)
        elif loss_type == "SOFTMAX_FOCAL_CROSS_ENTROPY":
            self.criterion = FocalLoss(n_classes, "SOFTMAX")
        elif loss_type == "SIGMOID_FOCAL_CROSS_ENTROPY":
            self.criterion = FocalLoss(n_classes, "SIGMOID")
        elif loss_type == "BCE_WITH_LOGITS":
            self.criterion = torch.nn.BCEWithLogitsLoss()
        else:
            raise TypeError(
                "Unsupported loss type: %s." % (
                    loss_type))

    def forward(self, logits, target, use_hierar=False, multiclass=False,
                hierar_penalty=1.0, hierar_paras=None, hierar_relations=None):
        if use_hierar:
            assert self.loss_type in ["BCE_WITH_LOGITS",
                                      "SIGMOID_FOCAL_CROSS_ENTROPY"]
            if not multiclass:
                target = torch.eye(self.label_size)[target]
            return self.criterion(logits, target) + \
                   hierar_penalty * self.recursive_regularize(hierar_paras,
                                                              hierar_relations)
        else:
            if multiclass:
                assert self.loss_type in ["BCE_WITH_LOGITS",
                                          "SIGMOID_FOCAL_CROSS_ENTROPY"]
            else:
                if self.loss_type not in ["SOFTMAX_CROSS_ENTROPY",
                                          "SOFTMAX_FOCAL_CROSS_ENTROPY"]:
                    target = torch.eye(self.label_size)[target]
            return self.criterion(logits, target)

    def recursive_regularize(self, paras, hierar_relations):
        """ Only support hierarchical text classification with BCELoss
        references: http://www.cse.ust.hk/~yqsong/papers/2018-WWW-Text-GraphCNN.pdf
                    http://www.cs.cmu.edu/~sgopal1/papers/KDD13.pdf
        """
        recursive_loss = 0.0
        for i in range(len(paras)):
            if i not in hierar_relations:
                continue
            children_ids = hierar_relations[i]
            if not children_ids:
                continue
            children_ids_list = torch.tensor(children_ids, dtype=torch.long)
            children_paras = torch.index_select(paras, 0, children_ids_list)
            parent_para = torch.index_select(paras, 0, torch.tensor(i))
            parent_para = parent_para.repeat(children_ids_list.size()[0], 1)
            diff_paras = parent_para - children_paras
            diff_paras = diff_paras.view(diff_paras.size()[0], -1)
            recursive_loss += 1.0 / 2 * torch.norm(diff_paras, p=2) ** 2
        return recursive_loss