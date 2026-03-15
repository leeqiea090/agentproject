"""招投标系统 API 共享路由状态与辅助函数。"""
from fastapi import APIRouter
from pathlib import Path
import re
import logging

from app.schemas import (
    BidDocumentSection,
    CompanyProfile,
    ProductSpecification,
)
from app.config import get_settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tender", tags=["招投标系统"])

# 文件存储路径
_SETTINGS = get_settings()
UPLOAD_DIR = Path(_SETTINGS.tender_upload_dir)
BID_OUTPUT_DIR = Path(_SETTINGS.bid_output_dir)
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

_PLACEHOLDER_COMPANY = CompanyProfile(
    company_id="placeholder",
    name="【待填写：投标人名称】",
    legal_representative="【待填写：法定代表人】",
    address="【待填写：公司注册地址】",
    phone="【待填写：联系电话】",
)

_INVALID_DOWNLOAD_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\r\n]+')


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


def _safe_download_filename(stem: str, suffix: str) -> str:
    sanitized = _INVALID_DOWNLOAD_FILENAME_CHARS.sub("_", str(stem or "").strip())
    sanitized = sanitized.strip(" .")
    if not sanitized:
        sanitized = "投标文件"

    normalized_suffix = suffix if suffix.startswith(".") else f".{suffix}"
    if sanitized.lower().endswith(normalized_suffix.lower()):
        return sanitized
    return f"{sanitized}{normalized_suffix}"


def _sections_for_storage_or_response(
    internal_sections: list[BidDocumentSection],
    outbound_sections: list[BidDocumentSection],
    outbound_report: dict,
) -> list[BidDocumentSection]:
    if outbound_sections:
        return outbound_sections
    return internal_sections
