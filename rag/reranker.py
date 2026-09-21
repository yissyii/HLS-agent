"""Optional local cross-encoder reranking for retrieved RAG candidates."""
from pathlib import Path

from rag.common import canonical, file_sha256, read_json, sha256


class QwenReranker:
    """CPU-safe wrapper around Qwen3-Reranker-0.6B via SentenceTransformers."""

    repository = 'Qwen/Qwen3-Reranker-0.6B'

    def __init__(self, model_path, threads=4, max_length=2048):
        import torch
        import sentence_transformers
        from sentence_transformers import CrossEncoder

        path = Path(model_path).resolve()
        if not path.is_dir():
            raise FileNotFoundError('Install local reranker first: ' + str(path))
        manifest = read_json(path / 'rag-reranker-manifest.json')
        if manifest.get('repository') != self.repository:
            raise ValueError('Expected the Qwen3-Reranker-0.6B model')
        for name, item in manifest.get('files', {}).items():
            target = (path / name).resolve()
            if not target.is_relative_to(path) or file_sha256(target) != item['sha256']:
                raise ValueError('Local reranker file checksum mismatch: ' + name)
        torch.set_num_threads(threads)
        self.model = CrossEncoder(str(path), device='cpu', max_length=max_length)
        self.path = path
        self.max_length = max_length
        self.identity = dict(repository=self.repository, revision=manifest.get('revision'),
                             model_manifest_sha256=sha256(canonical(manifest)), max_length=max_length,
                             device='cpu', dtype='float32', sentence_transformers=sentence_transformers.__version__,
                             torch=torch.__version__)

    def score(self, query, passages, batch_size=8):
        if not passages:
            return []
        values = self.model.predict([(query, passage) for passage in passages],
                                    batch_size=batch_size, show_progress_bar=False)
        return [float(value) for value in values]
