"""Offline Qwen encoder. No automatic downloads or implicit model substitution."""
import os
from pathlib import Path
import re

from rag.common import canonical, read_json, sha256, file_sha256

QUERY_INSTRUCTION = ('Given a Vitis HLS question, compiler diagnostic, or C++ coding issue, '
                     'retrieve relevant official manual passages explaining the applicable semantics, '
                     'restrictions, or correct usage.')


def document_text(record):
    path = record['source'].get('section_path', [record['title']])
    # Normalize PDF whitespace for encoding, retaining the original text for display.
    body = re.sub(r'\s+', ' ', record['text']).strip()
    if record.get('kind') == 'fix_card':
        # Put the structured routing fields in the embedding input.  They are
        # deliberately also present in ``text`` for BM25 and human display,
        # but keeping them here makes dense retrieval robust to paraphrased
        # diagnostics (for example, "address base mode" vs. "offset=slave").
        card = ' '.join([
            record['error_family'],
            ' '.join(record['signature_terms']),
            ' '.join(record['required_constructs']),
            ' '.join(record['exclusions']),
            record['action'],
            record['applicability'],
        ])
        body = card + '\n' + body
    return ' > '.join(path[-3:]) + '\n' + body


class QwenEncoder:
    def __init__(self, model_path, threads=6, max_length=2048):
        os.environ['HF_HUB_OFFLINE'] = '1'
        os.environ['TRANSFORMERS_OFFLINE'] = '1'
        os.environ['TOKENIZERS_PARALLELISM'] = 'false'
        import torch
        import sentence_transformers
        import transformers
        from sentence_transformers import SentenceTransformer

        path = Path(model_path).resolve()
        if not path.is_dir():
            raise FileNotFoundError('Install local model first: ' + str(path))
        manifest = read_json(path / 'rag-model-manifest.json')
        if manifest['repository'] != 'Qwen/Qwen3-Embedding-0.6B':
            raise ValueError('Expected the original Qwen3-Embedding-0.6B model')
        for name, item in manifest['files'].items():
            target = (path / name).resolve()
            if not target.is_relative_to(path) or file_sha256(target) != item['sha256']:
                raise ValueError('Local model file checksum mismatch: ' + name)
        torch.set_num_threads(threads)
        self.model = SentenceTransformer(str(path), device='cpu', local_files_only=True,
                                         model_kwargs={'torch_dtype': torch.float32})
        self.model.max_seq_length = max_length
        self.max_length = max_length
        self.identity = dict(repository=manifest['repository'], revision=manifest['revision'],
                             model_manifest_sha256=sha256(canonical(manifest)), dimensions=1024,
                             pooling='last_token_from_model_config', normalized=True,
                             document_format='last_three_headings_plus_whitespace_normalized_body_v1',
                             query_instruction=QUERY_INSTRUCTION, max_length=max_length,
                             device='cpu', dtype='float32', quantization='none',
                             sentence_transformers=sentence_transformers.__version__,
                             transformers=transformers.__version__, torch=torch.__version__)

    def lengths(self, texts):
        return [len(ids) for ids in self.model.tokenizer(texts, truncation=False, padding=False)['input_ids']]

    def encode(self, texts, *, query=False, batch_size=8):
        import numpy as np
        values = [('Instruct: ' + QUERY_INSTRUCTION + '\nQuery: ' + text) if query else text for text in texts]
        lengths = self.lengths(values)
        if any(n > self.max_length for n in lengths):
            raise ValueError(f'Embedding input exceeds {self.max_length} tokens; re-chunk instead of silently truncating')
        vectors = self.model.encode(values, batch_size=batch_size, normalize_embeddings=True,
                                    convert_to_numpy=True, show_progress_bar=False)
        vectors = np.asarray(vectors, dtype=np.float32)
        if vectors.shape != (len(texts), 1024) or not np.isfinite(vectors).all():
            raise ValueError('Unexpected embedding dimensions or non-finite values')
        return vectors
