from __future__ import annotations

import threading
from collections.abc import Iterable

from sentence_transformers import SentenceTransformer

from app.config import get_settings

_MODEL: SentenceTransformer | None = None
_MODEL_LOCK = threading.Lock()


def _get_model() -> SentenceTransformer:
    global _MODEL
    if _MODEL is None:
        with _MODEL_LOCK:
            if _MODEL is None:
                settings = get_settings()
                _MODEL = SentenceTransformer(settings.embedding_model_name)
    return _MODEL


def embed_texts(texts: Iterable[str]) -> list[list[float]]:
    text_list = [text for text in texts if text and text.strip()]
    if not text_list:
        return []

    model = _get_model()
    vectors = model.encode(text_list, normalize_embeddings=True)
    return vectors.tolist()


def embed_query(query: str) -> list[float]:
    vectors = embed_texts([query])
    return vectors[0] if vectors else []
