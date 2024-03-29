import argparse
import logging
import os
import time
from datetime import datetime, timedelta
import traceback

import numpy as np
from transformers import AutoConfig, AutoTokenizer
from transformers import HfArgumentParser
from transformers.trainer import TrainingArguments

from splitner.additional_args import AdditionalArguments
from splitner.dataset import NerDataCollator
from splitner.dataset_qa import NerQADataset
from splitner.evaluator_qa import EvaluatorQA
from splitner.trainer import NerTrainer
from splitner.utils.general import set_all_seeds, set_wandb, parse_config, setup_logging

logger = logging.getLogger(__name__)


class NerQAExecutor:
    def __init__(self, train_args: TrainingArguments, additional_args: AdditionalArguments):
        os.environ["WANDB_MODE"] = additional_args.wandb_mode
        set_wandb(additional_args.wandb_dir)
        logger.info("training args: {0}".format(train_args.to_json_string()))
        logger.info("additional args: {0}".format(additional_args.to_json_string()))
        set_all_seeds(train_args.seed)

        self.train_args = train_args
        self.additional_args = additional_args

        self.train_dataset = NerQADataset(additional_args, "train")
        self.dev_dataset = NerQADataset(additional_args, "dev")
        self.test_dataset = NerQADataset(additional_args, "test")

        # num_labels = 3 (for BIO tagging scheme), num_labels = 4 (for BIOE tagging scheme) etc.
        self.num_labels = self.additional_args.num_labels

        model_path = additional_args.resume if additional_args.resume else additional_args.base_model
        bert_config = AutoConfig.from_pretrained(model_path, num_labels=self.num_labels)

        model_class = self.get_model_class()
        self.model = model_class.from_pretrained(model_path, config=bert_config, additional_args=additional_args)

        trainable_params = filter(lambda p: p.requires_grad, self.model.parameters())
        logger.info("# trainable params: {0}".format(sum([np.prod(p.size()) for p in trainable_params])))

        tokenizer = AutoTokenizer.from_pretrained(model_path, use_fast=True)
        data_collator = NerDataCollator(args=additional_args, pattern_vocab=self.train_dataset.pattern_vocab)
        self.trainer = NerTrainer(model=self.model,
                                  args=train_args,
                                  tokenizer=tokenizer,
                                  data_collator=data_collator,
                                  train_dataset=self.train_dataset,
                                  eval_dataset=self.dev_dataset,
                                  compute_metrics=self.compute_metrics)

        ''' 
        for i in range(0, 4):
            print("Sentence:"+str(self.trainer.train_dataset.contexts[i].sentence))
            print(str(self.trainer.train_dataset.contexts[i].entity))
            print("Entity_Text:"+str(self.trainer.train_dataset.contexts[i].entity_text))
            print(str(self.trainer.train_dataset.contexts[i].bert_tokens))
            print(str(self.trainer.train_dataset.contexts[i].mention_span))
       ''' 


    def compute_metrics(self, eval_prediction):
        evaluator = EvaluatorQA(gold=eval_prediction.label_ids, predicted=eval_prediction.predictions,
                                num_labels=self.num_labels, none_tag=self.additional_args.none_tag)
        logger.info("entity metrics:\n{0}".format(evaluator.entity_metric.report()))
        return {"micro_f1": evaluator.entity_metric.micro_avg_f1()}

    def dump_predictions(self, dataset):
        os.makedirs(self.additional_args.predictions_dir, exist_ok=True)
        timer_file_path = os.path.join(self.additional_args.predictions_dir, "{0}-timer.log".format(dataset.corpus_type))
        timer_file = open(timer_file_path, "a")
        
        total_elapsed = 0
        n = 1   # manually set to 10 for prod experiments
        for i in range(0, n):
            logger.info("{0}-th prediction".format(str(i)))
            logger.info("start time: {0}".format(str(datetime.now())))
            start = time.time()

            model_predictions: np.ndarray = self.trainer.predict(dataset).predictions
            data = self.bert_to_orig_token_mapping1(dataset, model_predictions)
            # data = self.bert_to_orig_token_mapping2(dataset, model_predictions)

            elapsed = time.time() - start
            logger.info("elapsed time: {0} seconds: {1}".format(str(elapsed), str(timedelta(seconds=elapsed))))
            total_elapsed += elapsed
            timer_file.write(f"Iteration {str(i)}: {str(elapsed)}\n")

        avg_elapsed = total_elapsed / n
        timer_file.write(f"Avg: {str(avg_elapsed)}\n")
        timer_file.close()

        predictions_file = os.path.join(self.additional_args.predictions_dir, "{0}.tsv".format(dataset.corpus_type))
        logger.info("Outputs published in file: {0}".format(predictions_file))
        with open(predictions_file, "w", encoding="utf-8") as f:
            # f.write("Token\tGold\tPredicted\n")
            for sent in data:
                for word in sent:
                    f.write("{0}\t{1}\t{2}\n".format(word[0], word[1], word[2]))
                f.write("\n")

    # take the tag output for the first bert token as the tag for the original token
    # slightly more: "true positives", slightly less: "false positives", "false negatives"
    def bert_to_orig_token_mapping1(self, dataset, model_predictions):
        data_dict = {}
        pad_tag = self.additional_args.pad_tag
        none_tag = self.additional_args.none_tag
        for i in range(len(dataset)):
            context = dataset.contexts[i]
            text_sentence = " ".join([tok.text for tok in context.sentence.tokens])
            prediction = model_predictions[i]
            if text_sentence not in data_dict:
                entry = []
                for tok in context.sentence.tokens:
                    # considering only the first gold tag associated with the token
                    # gold_tag = "ENTITY" if self.additional_args.detect_spans else tok.tags[0]
                    gold_tag = tok.tags[0]
                    entry.append([tok.text, gold_tag, pad_tag])
                data_dict[text_sentence] = entry
            ptr = 0
            r = min(prediction.shape[0], len(context.bert_tokens))
            for j in range(r):
                if context.bert_tokens[j].token_type == 0:
                    continue

                if context.bert_tokens[j].token.offset != ptr:
                    continue

                if data_dict[text_sentence][ptr][2] not in [pad_tag, none_tag]:
                    ptr += 1
                    continue

                if prediction[j] == NerQADataset.get_tag_index("B", none_tag) or \
                        prediction[j] == NerQADataset.get_tag_index("S", none_tag):
                    tag_assignment = "B-" + context.entity
                elif prediction[j] == NerQADataset.get_tag_index("I", none_tag) or \
                        prediction[j] == NerQADataset.get_tag_index("E", none_tag):
                    tag_assignment = "I-" + context.entity
                else:
                    tag_assignment = none_tag

                data_dict[text_sentence][ptr][2] = tag_assignment
                ptr += 1

        data = []
        for context in dataset.contexts:
            text_sentence = " ".join([tok.text for tok in context.sentence.tokens])
            if text_sentence in data_dict:
                data.append(data_dict[text_sentence])
                del data_dict[text_sentence]

        return data

    # for each original token, if the output for bert sub-tokens is inconsistent, then map to NONE_TAG else take the tag
    # slightly more: "true positives", slightly less: "false negatives", considerably less: "false positives"
    # TODO: needs proof-reading
    def bert_to_orig_token_mapping2(self, dataset, model_predictions):
        data_dict = {}
        pad_tag = self.additional_args.pad_tag
        none_tag = self.additional_args.none_tag
        for i in range(len(dataset)):
            context = dataset.contexts[i]
            text_sentence = " ".join([tok.text for tok in context.sentence.tokens])
            prediction = model_predictions[i]
            if text_sentence not in data_dict:
                # considering only the first gold tag associated with the token
                data_dict[text_sentence] = [[tok.text, tok.tags[0], pad_tag] for tok in context.sentence.tokens]
            ptr = -1
            r = min(prediction.shape[0], len(context.bert_tokens))
            for j in range(1, r - 1):
                if context.bert_tokens[j].token_type == 0:
                    continue

                curr_assigned_tag = data_dict[text_sentence][ptr][2]
                if curr_assigned_tag not in [pad_tag, none_tag] and curr_assigned_tag[2:] != context.entity:
                    ptr += 1
                    continue

                if context.bert_tokens[j].token.offset > ptr:
                    ptr += 1

                    if prediction[j] == NerQADataset.get_tag_index("B", none_tag) or \
                            prediction[j] == NerQADataset.get_tag_index("S", none_tag):
                        tag_assignment = "B-" + context.entity
                    elif prediction[j] == NerQADataset.get_tag_index("I", none_tag) or \
                            prediction[j] == NerQADataset.get_tag_index("E", none_tag):
                        tag_assignment = "I-" + context.entity
                    else:
                        tag_assignment = none_tag
                    if data_dict[text_sentence][ptr][2] in [none_tag, pad_tag]:
                        data_dict[text_sentence][ptr][2] = tag_assignment

                # TODO: need to make this condition stricter (last tag should be "E", all intermediate ones "I"
                elif (prediction[j] != NerQADataset.get_tag_index("I", none_tag) or
                      prediction[j] != NerQADataset.get_tag_index("E", none_tag)) \
                        and data_dict[text_sentence][ptr][2][2:] == context.entity:
                    data_dict[text_sentence][ptr][2] = none_tag

        data = []
        for context in dataset.contexts:
            text_sentence = " ".join([tok.text for tok in context.sentence.tokens])
            if text_sentence in data_dict:
                data.append(data_dict[text_sentence])
                del data_dict[text_sentence]

        return data

    def run(self):
        if self.train_args.do_train:
            logger.info("training mode: start time: {0}".format(str(datetime.now())))

            start = time.time()
            try:
                self.trainer.train(self.additional_args.resume)
            except:
                traceback.print_exc()

            end = time.time()
            logger.info("end time: {0}".format(str(datetime.now())))
            elapsed = end - start
            logger.info("Elapsed time: {0} | In seconds: {1}".format(str(elapsed), str(timedelta(seconds=elapsed))))

        else:
            logger.info("prediction mode")
            # self.dump_predictions(self.train_dataset)
            # self.dump_predictions(self.dev_dataset)
            self.dump_predictions(self.test_dataset)
            # throws some threading related tqdm/wandb exception in the end (but code fully works)

    def get_model_class(self):
        if self.additional_args.model_mode == "std":
            from splitner.model import NerModel
            return NerModel
        if self.additional_args.model_mode == "roberta_std":
            from splitner.model_roberta import NerRobertaModel
            return NerRobertaModel
        if self.additional_args.model_mode == "crf":
            from splitner.model_crf import NerModelWithCrf
            return NerModelWithCrf
        if self.additional_args.model_mode == "bidaf":
            from splitner.model_bidaf import NerModelBiDAF
            return NerModelBiDAF


def main():
    setup_logging()
    parser = HfArgumentParser([TrainingArguments, AdditionalArguments])
    
    import sys
    if len(sys.argv) == 2 and sys.argv[1].endswith(".json"):
        # when a config json file is provided, parse it to get our arguments.
        train_args, additional_args = parse_config(parser, sys.argv[1])
    else:
        train_args, additional_args = parser.parse_args_into_dataclasses()

    executor = NerQAExecutor(train_args, additional_args)
    executor.run()



if __name__ == "__main__":
    main()
