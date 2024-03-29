import numpy as np

from splitner.dataset_qa import NerQADataset
from splitner.evaluator import Span, Metric


class EvaluatorQA:

    def __init__(self, gold, predicted, num_labels, none_tag, dataset: NerQADataset = None):
        self.num_labels = num_labels
        self.none_tag = none_tag
        self.gold = gold.tolist() if isinstance(gold, np.ndarray) else gold
        self.predicted = predicted.tolist() if isinstance(predicted, np.ndarray) else predicted
        self.dataset = dataset
        self.tags = list(self.dataset.tag_to_text_mapping.keys()) if self.dataset else ["TAG"]

        if self.num_labels == 2:
            # "BO" tagging scheme
            self.gold_entity_spans = self.get_spans_for_2_tag_scheme(self.gold)
            self.predicted_entity_spans = self.get_spans_for_2_tag_scheme(self.predicted)
        else:
            self.gold_entity_spans = self.get_spans(self.gold)
            self.predicted_entity_spans = self.get_spans(self.predicted)
        self.entity_metric = self.calc_entity_metrics()

    def calc_entity_metrics(self):
        entity_metric = Metric(self.tags)
        for gold_sent_spans, predicted_sent_spans in zip(self.gold_entity_spans, self.predicted_entity_spans):
            for span in predicted_sent_spans:
                if span in gold_sent_spans:
                    entity_metric.add_tp(span)
                else:
                    entity_metric.add_fp(span)
            for span in gold_sent_spans:
                if span not in predicted_sent_spans:
                    entity_metric.add_fn(span)
        return entity_metric

    def get_spans(self, batch):
        batch_spans = []
        b_tag_index = NerQADataset.get_tag_index("B", none_tag=self.none_tag)
        i_tag_index = NerQADataset.get_tag_index("I", none_tag=self.none_tag)
        e_tag_index = NerQADataset.get_tag_index("E", none_tag=self.none_tag)
        s_tag_index = NerQADataset.get_tag_index("S", none_tag=self.none_tag)
        for context_index in range(len(batch)):
            context_spans = []
            prev_span = None
            for tok_index in range(len(batch[context_index])):
                if self.gold[context_index][tok_index] == -100:
                    prev_span = None
                    continue
                if batch[context_index][tok_index] == b_tag_index:
                    tag = self.dataset.contexts[context_index].entity if self.dataset else "TAG"
                    curr_span = Span(context_index, tok_index, tok_index, tag)
                    context_spans.append(curr_span)
                    prev_span = curr_span
                elif batch[context_index][tok_index] == s_tag_index:
                    tag = self.dataset.contexts[context_index].entity if self.dataset else "TAG"
                    curr_span = Span(context_index, tok_index, tok_index, tag)
                    context_spans.append(curr_span)
                    prev_span = None
                elif prev_span and batch[context_index][tok_index] == i_tag_index:
                    tag = self.dataset.contexts[context_index].entity if self.dataset else "TAG"
                    if tag == prev_span.tag:
                        if self.num_labels == 3:
                            prev_span.end = tok_index
                    else:
                        prev_span = None
                elif prev_span and batch[context_index][tok_index] == e_tag_index:
                    tag = self.dataset.contexts[context_index].entity if self.dataset else "TAG"
                    if tag == prev_span.tag:
                        prev_span.end = tok_index
                    else:
                        prev_span = None
                else:
                    prev_span = None
            batch_spans.append(context_spans)
        return batch_spans

    def get_spans_for_2_tag_scheme(self, batch):
        batch_spans = []
        b_tag_index = NerQADataset.get_tag_index("B", none_tag=self.none_tag)
        for context_index in range(len(batch)):
            context_spans = []
            prev_span = None
            for tok_index in range(len(batch[context_index])):
                if self.gold[context_index][tok_index] == -100:
                    prev_span = None
                    continue
                if batch[context_index][tok_index] == b_tag_index:
                    tag = self.dataset.contexts[context_index].entity if self.dataset else "TAG"
                    if prev_span and tag == prev_span.tag:
                        prev_span.end = tok_index
                    else:
                        curr_span = Span(context_index, tok_index, tok_index, tag)
                        context_spans.append(curr_span)
                        prev_span = curr_span
                else:
                    prev_span = None
            batch_spans.append(context_spans)
        return batch_spans
