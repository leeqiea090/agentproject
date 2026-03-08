from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np

from app.config import get_settings
from app.services.chunking import split_text
from app.services.embeddings import embed_query, embed_texts

_DB_CONN: sqlite3.Connection | None = None
_DB_PATH: Path | None = None
_LOCK = threading.Lock()

_ALLOWED_META_TYPES = (str, int, float, bool)


def _normalize_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    if not metadata:
        return {}

    normalized: dict[str, Any] = {}
    for key, value in metadata.items():
        if value is None:
            continue
        if isinstance(value, _ALLOWED_META_TYPES):
            normalized[str(key)] = value
        else:
            normalized[str(key)] = str(value)
    return normalized


def _resolve_db_path() -> Path:
    settings = get_settings()
    configured = Path(settings.vector_db_path)

    if configured.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
        return configured

    return configured / "vector_store.sqlite3"


def _initialize_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS kb_chunks (
            id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            text TEXT NOT NULL,
            embedding_json TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    connection.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_kb_chunks_source
        ON kb_chunks(source)
        """
    )
    connection.commit()


def _get_connection() -> sqlite3.Connection:
    global _DB_CONN, _DB_PATH

    if _DB_CONN is None:
        with _LOCK:
            if _DB_CONN is None:
                db_path = _resolve_db_path()
                os.makedirs(db_path.parent, exist_ok=True)

                conn = sqlite3.connect(str(db_path), check_same_thread=False)
                conn.row_factory = sqlite3.Row
                _initialize_schema(conn)

                _DB_CONN = conn
                _DB_PATH = db_path

    return _DB_CONN


def ingest_text_to_kb(
    text: str,
    source: str,
    metadata: dict[str, Any] | None = None,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    chunk_size = chunk_size or settings.default_chunk_size
    chunk_overlap = chunk_overlap if chunk_overlap is not None else settings.default_chunk_overlap

    chunks = split_text(text=text, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    if not chunks:
        return {
            "source": source,
            "chunks_indexed": 0,
            "total_characters": len(text),
        }

    vectors = embed_texts(chunks)
    if len(vectors) != len(chunks):
        raise RuntimeError("Embedding count mismatch with chunk count.")

    base_meta = _normalize_metadata(metadata)
    rows: list[tuple[str, str, int, str, str, str]] = []

    for idx, (chunk, vector) in enumerate(zip(chunks, vectors)):
        record_id = str(uuid4())
        merged_meta = {
            "source": source,
            "chunk_index": idx,
            **base_meta,
        }
        rows.append(
            (
                record_id,
                source,
                idx,
                chunk,
                json.dumps(vector, ensure_ascii=False),
                json.dumps(merged_meta, ensure_ascii=False),
            )
        )

    conn = _get_connection()
    with _LOCK:
        conn.executemany(
            """
            INSERT INTO kb_chunks (id, source, chunk_index, text, embedding_json, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()

    return {
        "source": source,
        "chunks_indexed": len(chunks),
        "total_characters": len(text),
    }


def _cosine_similarity(query_vec: np.ndarray, doc_vec: np.ndarray) -> float:
    query_norm = np.linalg.norm(query_vec)
    doc_norm = np.linalg.norm(doc_vec)
    if query_norm == 0 or doc_norm == 0:
        return 0.0
    return float(np.dot(query_vec, doc_vec) / (query_norm * doc_norm))


def search_knowledge(query: str, top_k: int = 5) -> list[dict[str, Any]]:
    settings = get_settings()
    k = max(1, min(top_k, 20)) if top_k else settings.default_top_k

    query_embedding = embed_query(query)
    if not query_embedding:
        return []

    query_vec = np.asarray(query_embedding, dtype=np.float32)

    conn = _get_connection()
    cursor = conn.execute("SELECT text, embedding_json, metadata_json FROM kb_chunks")
    rows = cursor.fetchall()

    scored: list[tuple[float, str, dict[str, Any]]] = []
    for row in rows:
        try:
            doc_embedding = json.loads(row["embedding_json"])
            doc_vec = np.asarray(doc_embedding, dtype=np.float32)
            if doc_vec.shape != query_vec.shape:
                continue

            score = _cosine_similarity(query_vec, doc_vec)
            metadata = json.loads(row["metadata_json"])
            if not isinstance(metadata, dict):
                metadata = {}

            scored.append((score, row["text"], metadata))
        except Exception:
            continue

    scored.sort(key=lambda item: item[0], reverse=True)

    results: list[dict[str, Any]] = []
    for score, text, metadata in scored[:k]:
        results.append(
            {
                "text": text,
                "score": round(score, 6),
                "metadata": metadata,
            }
        )

    return results


def knowledge_base_stats() -> dict[str, Any]:
    conn = _get_connection()
    row = conn.execute("SELECT COUNT(1) AS cnt FROM kb_chunks").fetchone()
    count = int(row["cnt"]) if row else 0

    settings = get_settings()
    db_path = str(_DB_PATH or _resolve_db_path())

    return {
        "collection": settings.vector_collection_name,
        "path": db_path,
        "count": count,
    }
