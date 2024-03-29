import torch
import torch.nn as nn
import torch.nn.functional as F


class DiceLoss(nn.Module):
    def __init__(self, eps=1e-8):
        super(DiceLoss, self).__init__()
        self.ignore_index = -100
        self.eps = eps

    def forward(self, logits, labels, mask):
        num_classes = logits.shape[1]
        labels_mod = labels.clone()
        labels_mod[labels_mod == self.ignore_index] = 0
        labels_1_hot = torch.eye(num_classes, device=logits.device)[labels_mod].to(logits.device)
        labels_1_hot *= mask.unsqueeze(-1).repeat(1, num_classes)

        dice_total = 0.
        for index in range(num_classes):
            dice_total += self.dice_coefficient_per_class(labels_1_hot[:, index], logits[:, index], mask)
        return 1. - dice_total / num_classes

    def dice_coefficient_per_class(self, labels, logits, mask):
        labels_mod = labels * mask
        logits_mod = torch.sigmoid(logits) * mask
        intersection = (labels_mod * logits_mod).sum()
        return (2. * intersection + self.eps) / (labels_mod.sum() + logits_mod.sum() + self.eps)


class CrossEntropyPunctuationLoss(nn.Module):
    def __init__(self):
        super(CrossEntropyPunctuationLoss, self).__init__()
        self.ignore_index = -100

    def forward(self, logits, labels, mask, word_type):
        labels_mod = labels.clone()
        labels_mod[labels_mod == self.ignore_index] = 0
        log_vec = -F.log_softmax(logits, dim=1)
        val_vec = torch.gather(log_vec, -1, labels_mod.unsqueeze(-1)).squeeze()
        weight = torch.ones(logits.shape[-1], device=labels.device)
        word_type_mod = word_type + 1.0
        x = weight[labels_mod] * word_type_mod * mask
        loss = (x * val_vec).sum() / x.sum()
        return loss
