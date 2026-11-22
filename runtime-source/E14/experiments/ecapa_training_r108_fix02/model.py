"""Construct architecture classes directly. No fetching or checkpoint loading here."""
import hashlib

import torch
from torch import nn
from torch.nn import functional as F
from speechbrain.lobes.features import Fbank
from speechbrain.lobes.models.ECAPA_TDNN import ECAPA_TDNN, Classifier
from speechbrain.nnet.losses import AdditiveAngularMargin

from .augment import specaugment
from .deterministic_padding import adapt_reflection


def parameter_sha256(model):
    h = hashlib.sha256()
    for name, value in sorted(model.state_dict().items()):
        h.update(name.encode())
        h.update(value.detach().cpu().contiguous().numpy().tobytes())
    return h.hexdigest()


class ECAPATraining(nn.Module):
    def __init__(self, config):
        super().__init__()
        self.features = Fbank(n_mels=80, sample_rate=16000, **config['fbank'])
        self.encoder = ECAPA_TDNN(input_size=80, lin_neurons=192, device='cpu', **config['ecapa'])
        padding_adapters=adapt_reflection(self.encoder)
        self.classifier = Classifier(input_size=192, out_neurons=800, device='cpu')
        self.margin = AdditiveAngularMargin(**config['aam'])
        self.initialization = dict(origin='random_constructor', loaded_checkpoint=None,padding_adapters=padding_adapters,
                                   classifier_classes=800, embedding_size=192,
                                   parameter_sha256=parameter_sha256(self))

    def forward(self, waves, labels, seeds=None, augmentation=None):
        features = self.features(waves)
        features = features - features.mean(dim=1, keepdim=True)
        audits = []
        if augmentation is not None:
            transformed = []
            for x, label, seed in zip(features, labels, seeds):
                y, event = specaugment(x, int(label), seed, augmentation['probability'], augmentation['params'])
                transformed.append(y)
                audits.append(event)
            features = torch.stack(transformed)
        embeddings = self.encoder(features).squeeze(1)
        cosine = self.classifier(embeddings).squeeze(1)
        targets = F.one_hot(labels, 800).float()
        logits = self.margin(cosine, targets)
        # Accuracy must use cosine; target-dependent margin logits are for AAM loss only.
        return F.cross_entropy(logits, labels), cosine, logits, embeddings, audits
