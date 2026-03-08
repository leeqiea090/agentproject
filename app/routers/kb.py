from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

from docx import Document
from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from pypdf import PdfReader

from app.schemas import (
    IngestResponse,
    IngestTextRequest,
    KnowledgeBaseStatsResponse,
    SearchHit,
    SearchRequest,
    SearchResponse,
)
from app.services.retriever import ingest_text_to_kb, knowledge_base_stats, search_knowledge

router = APIRouter(prefix="/kb", tags=["知识库"])


def _decode_text_bytes(payload: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "gbk"):
        try:
            return payload.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise HTTPException(status_code=400, detail="不支持的文本编码格式。")


def _extract_text_from_file(filename: str, payload: bytes) -> str:
    suffix = Path(filename).suffix.lower()

    if suffix == ".pdf":
        reader = PdfReader(BytesIO(payload))
        text_parts = [page.extract_text() or "" for page in reader.pages]
        return "\n".join(text_parts).strip()

    if suffix == ".docx":
        document = Document(BytesIO(payload))
        return "\n".join(paragraph.text for paragraph in document.paragraphs).strip()

    return _decode_text_bytes(payload).strip()


@router.post("/text", response_model=IngestResponse, summary="导入纯文本", description="将一段文本分块后写入知识库")
def ingest_text(req: IngestTextRequest):
    result = ingest_text_to_kb(
        text=req.text,
        source=req.source,
        metadata=req.metadata,
        chunk_size=req.chunk_size,
        chunk_overlap=req.chunk_overlap,
    )
    return IngestResponse(**result)


@router.post("/file", response_model=IngestResponse, summary="上传文件导入知识库", description="支持 PDF、DOCX、TXT 等文件，自动提取文本并分块写入知识库")
async def ingest_file(
    file: UploadFile = File(...),
    source: str | None = Query(default=None),
    chunk_size: int | None = Query(default=None, ge=200, le=3000),
    chunk_overlap: int | None = Query(default=None, ge=0, le=800),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="上传的文件必须包含文件名。")

    payload = await file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="上传的文件内容为空。")

    text = _extract_text_from_file(file.filename, payload)
    if not text:
        raise HTTPException(status_code=400, detail="未能从文件中提取到可读文本。")

    metadata: dict[str, Any] = {
        "filename": file.filename,
        "content_type": file.content_type or "",
    }

    result = ingest_text_to_kb(
        text=text,
        source=source or file.filename,
        metadata=metadata,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
    return IngestResponse(**result)


@router.post("/search", response_model=SearchResponse, summary="语义检索", description="在知识库中进行语义相似度检索，返回最相关的文本片段")
def search(req: SearchRequest):
    hits = search_knowledge(query=req.query, top_k=req.top_k)
    return SearchResponse(
        query=req.query,
        hits=[SearchHit(**hit) for hit in hits],
    )


@router.get("/stats", response_model=KnowledgeBaseStatsResponse, summary="知识库统计", description="查看知识库的集合名称、存储路径及文档块总数")
def stats():
    return KnowledgeBaseStatsResponse(**knowledge_base_stats())
