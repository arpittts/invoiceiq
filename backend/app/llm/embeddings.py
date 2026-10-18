"""Text embeddings for HSN/SAC retrieval.

The default model is BAAI/bge-small-en-v1.5 through fastembed (ONNX, runs on CPU,
384 dimensions). It is free and local, but adds about 175 MB of RAM, so it is
loaded only when the first lookup needs it, and only once per process.
The Docker image ships the model files, so a cold start doesn't download them.
"""

import hashlib
import math
import re
import threading
from functools import lru_cache
from typing import Protocol

from app.config import get_settings


class Embedder(Protocol):
    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...
    def embed_query(self, text: str) -> list[float]: ...


class FastEmbedder:
    def __init__(self, model_name: str, cache_dir: str | None = None):
        self.model_name = model_name
        self.cache_dir = cache_dir
        self._model = None
        self._lock = threading.Lock()  # two requests must not load the model twice

    def _get_model(self):
        with self._lock:
            if self._model is None:
                from fastembed import TextEmbedding  # imported late: the import itself is heavy

                self._model = TextEmbedding(self.model_name, cache_dir=self.cache_dir)
        return self._model

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        # passage_embed: the document side of bge's query/passage pair.
        return [vector.tolist() for vector in self._get_model().passage_embed(texts)]

    def embed_query(self, text: str) -> list[float]:
        # query_embed adds bge's search instruction prefix for queries.
        return next(iter(self._get_model().query_embed([text]))).tolist()


class HashingEmbedder:
    """A tiny stand-in for tests: texts that share words get similar vectors.

    Each word is hashed into one of 384 slots (the "hashing trick"), then the
    vector is normalised. No model, no download, deterministic.
    """

    dimensions = 384

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            slot = int(hashlib.md5(word.encode()).hexdigest(), 16) % self.dimensions
            vector[slot] += 1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


@lru_cache
def get_embedder() -> Embedder:
    settings = get_settings()
    return FastEmbedder(settings.embedding_model, settings.embedding_cache_dir)
