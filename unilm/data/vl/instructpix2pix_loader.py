try:
    from fairseq.data.encoders.gpt2_bpe import GPT2BPE
except:
    print("GPT2BPE not found, please install fairseq first if you want to use GPT2BPE")
import os

from PIL import Image

try:
    from torchvision.transforms import InterpolationMode

    BICUBIC = InterpolationMode.BICUBIC
except ImportError:
    BICUBIC = Image.BICUBIC

import numpy as np
import torch
from tiktoken.core import Encoding
from torchvision.transforms import CenterCrop, Compose, Resize
import base64
import io
import random
from infinibatch import iterators
from unilm.data.vl.vl_base_loader import VLBaseLoader

ALT_KEY = 'MMAltTextWords'
CAPTION_KEY = 'MMCaptionWords'
CONTENT_KEY = 'Content'
IMAGE_KEY = 'MMImage'
BOI_SYMBOL = "<image>"
EOI_SYMBOL = "</image>"


class NumpyNormalize(torch.nn.Module):
    def __init__(self, mean, std):
        super().__init__()
        self.mean = mean
        self.std = std

    def forward(self, img):
        """
        Args:
            img (PIL Image or Tensor).
        Returns:
        """
        image = np.array(img).transpose(2, 0, 1)  # B, H, W, C  -> B, C, H, W
        image = image / 255.0
        image -= np.array(self.mean).reshape(-1, 1, 1)
        image /= np.array(self.std).reshape(-1, 1, 1)
        return image

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(mean={self.mean}, std={self.std})"


class InstructPix2PixLoader(VLBaseLoader):
    def _setup(self):
        self.max_image_num = self.args.max_image_num
        self.image_token_length = self.args.image_token_length
        self.random_drop_caption_prob = self.args.random_drop_caption_prob
        self.dictionary.add_symbol(BOI_SYMBOL)
        self.dictionary.add_symbol(EOI_SYMBOL)

    def _build_filter(self):
        return None

    def _build_image_transform(self):
        preprocess_image = {
            'gpt': Compose([
                Resize(224, interpolation=BICUBIC),
                CenterCrop(224),
                NumpyNormalize((0.48145466, 0.4578275, 0.40821073), (0.26862954, 0.26130258, 0.27577711))
            ]),
            'diff': Compose([
                Resize(512),
                CenterCrop(512),
                NumpyNormalize([0.5], [0.5])
            ])
        }
        return preprocess_image

    def _build_text_transform(self):
        def text_transform(text):
            append_eos = False
            fs_dict = self.dictionary
            if isinstance(self.tokenizer, Encoding):
                words = list(map(str, self.tokenizer.encode(text, allowed_special="all")))
            else:
                words = self.tokenizer.encode(text, out_type=str)
            # ids = [fs_dict.bos_index]
            ids = []
            for i, word in enumerate(words):
                idx = fs_dict.index(word)
                ids.append(idx)
            if append_eos:
                ids.append(fs_dict.eos_index)
            return ids

        return text_transform

    def _batchify(self, lines):

        if self.max_sentences is not None:
            if self.batch_read_ahead > 0:
                lines = iterators.BlockwiseShuffleIterator(lines, self.batch_read_ahead, self.seed)
            batches = iterators.FixedBatchIterator(lines, self.max_sentences)
        else:
            # -
            def dynamic_batch_size(sample):
                lengths = [len(x) for x in sample]
                batch_size = self.max_tokens // max(
                    lengths) // self.required_batch_size_multiple * self.required_batch_size_multiple
                return max(1, batch_size)

            batches = iterators.BucketedReadaheadBatchIterator(
                lines,
                read_ahead=self.batch_read_ahead,
                key=(lambda x: max(len(x[0]), len(x[1]))) if self.shuffle else None,
                batch_size=dynamic_batch_size,
                shuffle=self.shuffle,
                seed=self.seed,
            )

        def collate(batch):
            batch_size = len(batch)

            gpt_max_length = max([len(x[0]) for x in batch])

            gpt_source_ids = np.full(shape=(batch_size, gpt_max_length - 1), dtype=np.int32,
                                     fill_value=self.dictionary.pad())
            gpt_target_ids = np.full(shape=(batch_size, gpt_max_length - 1), dtype=np.int32,
                                     fill_value=self.dictionary.pad())
            gpt_input_mask_all = np.full(shape=(batch_size, gpt_max_length - 1), dtype=np.int32, fill_value=0)
            gpt_loss_mask_all = np.full(shape=(batch_size, gpt_max_length - 1), dtype=np.int32, fill_value=1)
            chunk_tokens_all = np.full(shape=(batch_size, gpt_max_length - 1), dtype=np.int32, fill_value=0)
            segment_tokens_all = np.full(shape=(batch_size, gpt_max_length - 1), dtype=np.int32, fill_value=0)

            all_gpt_source_image_tokens = []
            all_target_image_tokens = []

            for i, (full_tokens, gpt_src_image_tokens, tgt_image_tokens, text_input_mask, text_loss_mask, chunk_tokens,
                    segment_tokens) in enumerate(batch):
                gpt_source_ids[i, :len(full_tokens) - 1] = full_tokens[:-1]
                gpt_target_ids[i, :len(full_tokens) - 1] = full_tokens[1:]
                gpt_input_mask_all[i, :len(full_tokens) - 1] = text_input_mask[:-1]
                gpt_loss_mask_all[i, :len(full_tokens) - 1] = text_loss_mask[:-1]
                chunk_tokens_all[i, :len(full_tokens) - 1] = chunk_tokens[:-1]
                segment_tokens_all[i, :len(full_tokens) - 1] = segment_tokens[:-1]
                all_gpt_source_image_tokens.extend(gpt_src_image_tokens)
                all_target_image_tokens.append(tgt_image_tokens)

            gpt_image_source_ids = np.stack(all_gpt_source_image_tokens).astype(np.float32) \
                if all_gpt_source_image_tokens else None
            image_target_ids = np.stack(all_target_image_tokens)

            ret_batch = {
                'vl_instructpix2pix': {
                    'net_input': {
                        'src_tokens': gpt_source_ids.astype(np.int64),
                        'gpt_img_src_tokens': gpt_image_source_ids,
                        'img_tgt_tokens': image_target_ids.astype(np.float32),
                        'img_gpt_input_mask': gpt_input_mask_all.astype(np.bool_),
                        'gpt_loss_mask': gpt_loss_mask_all.astype(np.bool_),
                        'chunk_tokens': chunk_tokens_all.astype(np.int64),
                        'segment_tokens': segment_tokens_all.astype(np.int64),
                    },
                    'target': gpt_target_ids.astype(np.int64),
                    'nsentences': batch_size,
                    'ntokens': sum([len(x[0]) for x in batch]),
                }
            }

            return ret_batch

        padded_batches = iterators.MapIterator(
            batches, collate
        )

        return padded_batches

    def _prepare(self, _random, doc):
        text_tokens = doc[CAPTION_KEY]
        src_image_tokens = doc[IMAGE_KEY][:-1]
        tgt_image_tokens = doc[IMAGE_KEY][-1]
        text_input_mask = doc['input_mask']
        text_loss_mask = doc['loss_mask']
        chunk_tokens = doc['chunk_tokens']
        segment_tokens = doc['segment_tokens']

        gpt_src_image_tokens = [self.image_transform['gpt'](im) for im in src_image_tokens]
        tgt_image_tokens = self.image_transform['diff'](tgt_image_tokens)

        return text_tokens, gpt_src_image_tokens, tgt_image_tokens, text_input_mask, text_loss_mask, chunk_tokens, segment_tokens

    def _read_from_files(self, source_file):
        file_path = os.path.join(self.data_dir, 'data', source_file)
        if not os.path.exists(file_path):
            print('| file {} not exists'.format(file_path), flush=True)
            return iter([])  # skip bad file
        try:
            with open(file_path, 'r', encoding='utf8') as f:
                lines = f.read().strip().split('\n')
        except:
            return iter([])  # skip bad file

        bos_id = self.dictionary.bos()
        eos_id = self.dictionary.eos()
        boi_id = self.dictionary.index(BOI_SYMBOL)
        eoi_id = self.dictionary.index(EOI_SYMBOL)
        for doc_str in lines:
            item = doc_str.strip().split('\t')
            # prepare text and image tokens
            input_tokens = self.text_transform(item[0])
            edit_tokens = self.text_transform(item[1])
            # random drop caption
            if random.random() < self.random_drop_caption_prob:
                input_tokens = []

            num_seeds = (len(item) - 2) // 2
            seed = random.randint(0, num_seeds - 1)
            try:
                image_tokens = [item[seed * 2 + 2], item[seed * 2 + 3]]
                image_tokens = [Image.open(io.BytesIO(base64.b64decode(im))).convert("RGB") for im in image_tokens]

                doc = [bos_id] + input_tokens + [boi_id] * (self.image_token_length + 1) + [
                    eoi_id] + edit_tokens + [eos_id]
                doc_input_mask = [0] + [0] * len(input_tokens) + [0] + [1] * self.image_token_length + [0] + [
                    0] * len(edit_tokens) + [0]
                doc_loss_mask = [1] + [1] * len(input_tokens) + [0] + [0] * self.image_token_length + [1] + [
                    1] * len(edit_tokens) + [1]
                chunk_tokens = [0] + [0] * len(input_tokens) + [1] + [1] * self.image_token_length + [1] + [
                    1] * len(edit_tokens) + [1]
                segment_tokens = [0] + [0] * len(input_tokens) + [1] + [1] * self.image_token_length + [1] + [
                    0] * len(edit_tokens) + [0]

                yield {
                    CAPTION_KEY: doc,
                    IMAGE_KEY: image_tokens,
                    'input_mask': doc_input_mask,
                    'loss_mask': doc_loss_mask,
                    'chunk_tokens': chunk_tokens,
                    'segment_tokens': segment_tokens,
                }

            except:
                continue
