"""招投标系统API路由"""
from fastapi import APIRouter, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse
from pathlib import Path
import shutil
import uuid
from datetime import datetime
import logging

from app.schemas import (
    TenderDocument,
    TenderUploadResponse,
    TenderParseResponse,
    CompanyProfile,
    ProductSpecification,
    BidGenerateRequest,
    BidGenerateResponse,
    BidDocumentSection,
    TenderWorkflowRequest,
    TenderWorkflowResponse,
    TenderWorkflowStep1Result,
    TenderWorkflowStep2Result,
    TenderWorkflowStep3Result,
    TenderWorkflowStep4Result,
    OneClickJobStartResponse,
    OneClickJobStatusResponse,
    # ErrorResponse
)
from app.services.tender_parser import create_tender_parser
from app.services.bid_generator import create_bid_generator, BidGenerationState
from app.services.one_click_generator import generate_bid_sections
from app.services.docx_builder import build_bid_docx
from app.services.retriever import ingest_text_to_kb
from app.services.tender_workflow import (
    TenderWorkflowAgent,
    _build_document_ingestion_view,
    _build_internal_audit_snapshot,
    _build_package_segmentation_view,
    _default_step1_result,
    _ensure_str_list,
    _expand_extracted_facts,
    _extract_product_facts,
    _match_requirements_to_product_facts,
    _materialize_sections,
    _sanitize_for_external_delivery,
    _second_validation,
)
from app.services.llm import get_chat_model

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tender", tags=["招投标系统"])

# 文件存储路径
UPLOAD_DIR = Path("data/uploads/tenders")
BID_OUTPUT_DIR = Path("data/outputs/bids")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
BID_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# 临时存储（实际项目中应使用数据库）
tender_storage: dict[str, dict] = {}
company_storage: dict[str, CompanyProfile] = {}
product_storage: dict[str, ProductSpecification] = {}
bid_storage: dict[str, dict] = {}
workflow_storage: dict[str, dict] = {}
workflow_kb_indexed_sources: set[str] = set()
one_click_job_storage: dict[str, dict] = {}


def _is_external_delivery_blocked(outbound_report: dict | None) -> bool:
    return str((outbound_report or {}).get("status", "") or "").strip() == "阻断外发"


def _build_external_delivery_view(
    sections: list[BidDocumentSection],
    outbound_report: dict,
    *,
    download_url: str = "",
    file_path: str = "",
) -> dict:
    if _is_external_delivery_blocked(outbound_report):
        return {
            **outbound_report,
            "generated": False,
            "download_url": "",
            "file_path": "",
            "section_titles": [],
        }

    return {
        **outbound_report,
        "generated": True,
        "download_url": download_url,
        "file_path": file_path,
        "section_titles": [section.section_title for section in sections],
    }


def _sections_for_storage_or_response(
    internal_sections: list[BidDocumentSection],
    outbound_sections: list[BidDocumentSection],
    outbound_report: dict,
) -> list[BidDocumentSection]:
    return internal_sections if _is_external_delivery_blocked(outbound_report) else outbound_sections
