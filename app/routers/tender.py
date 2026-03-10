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


@router.post("/upload", response_model=TenderUploadResponse)
async def upload_tender_file(file: UploadFile = File(...)):
    """
    上传招标文件PDF

    Args:
        file: 上传的PDF文件

    Returns:
        上传响应，包含tender_id
    """
    # 检查文件类型
    if not file.filename.endswith('.pdf'):
        raise HTTPException(status_code=400, detail="只支持PDF文件")

    # 生成唯一ID
    tender_id = str(uuid.uuid4())

    # 保存文件
    file_path = UPLOAD_DIR / f"{tender_id}.pdf"
    try:
        with file_path.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        file_size = file_path.stat().st_size

        # 存储元数据
        tender_storage[tender_id] = {
            "tender_id": tender_id,
            "original_filename": file.filename,
            "file_path": str(file_path),
            "file_size": file_size,
            "upload_time": datetime.now(),
            "status": "uploaded",
            "parsed_data": None
        }

        logger.info(f"招标文件上传成功: {tender_id}, 文件名: {file.filename}, 大小: {file_size} bytes")

        return TenderUploadResponse(
            tender_id=tender_id,
            upload_time=datetime.now(),
            status="uploaded"
        )
    except Exception as e:
        logger.error(f"文件上传失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"文件上传失败: {str(e)}")


@router.post("/parse/{tender_id}", response_model=TenderParseResponse)
async def parse_tender_file(tender_id: str):
    """
    解析招标文件，提取结构化信息

    Args:
        tender_id: 招标文件ID

    Returns:
        解析结果
    """
    # 检查tender是否存在
    if tender_id not in tender_storage:
        raise HTTPException(status_code=404, detail="招标文件不存在")

    tender_info = tender_storage[tender_id]

    # 检查状态
    if tender_info["status"] == "parsing":
        raise HTTPException(status_code=409, detail="该文件正在解析中")

    # 更新状态
    tender_info["status"] = "parsing"

    try:
        # 创建解析器
        llm = get_chat_model()
        parser = create_tender_parser(llm)

        # 解析PDF
        file_path = tender_info["file_path"]
        tender_doc = parser.parse_tender_document(file_path)

        # 获取原始文本长度
        raw_text = parser.extract_text_from_pdf(file_path)
        raw_text_length = len(raw_text)

        # 存储解析结果
        tender_info["parsed_data"] = tender_doc.model_dump()
        tender_info["raw_text"] = raw_text
        tender_info["status"] = "parsed"

        logger.info(f"招标文件解析成功: {tender_id}, 项目: {tender_doc.project_name}")

        return TenderParseResponse(
            tender_id=tender_id,
            parsed_data=tender_doc,
            raw_text_length=raw_text_length,
            parse_time=datetime.now()
        )

    except Exception as e:
        logger.error(f"招标文件解析失败: {str(e)}")
        tender_info["status"] = "error"
        tender_info["error_message"] = str(e)
        raise HTTPException(status_code=500, detail=f"解析失败: {str(e)}")


@router.get("/parsed/{tender_id}", response_model=TenderDocument)
async def get_parsed_tender(tender_id: str):
    """
    获取已解析的招标文件数据

    Args:
        tender_id: 招标文件ID

    Returns:
        解析后的招标文件数据
    """
    if tender_id not in tender_storage:
        raise HTTPException(status_code=404, detail="招标文件不存在")

    tender_info = tender_storage[tender_id]

    if tender_info["status"] != "parsed":
        raise HTTPException(status_code=400, detail="该文件尚未解析或解析失败")

    return TenderDocument(**tender_info["parsed_data"])


@router.post("/company/profile", response_model=CompanyProfile)
async def create_or_update_company_profile(profile: CompanyProfile):
    """
    创建或更新企业信息

    Args:
        profile: 企业信息

    Returns:
        已保存的企业信息
    """
    # 生成ID（如果没有）
    if not profile.company_id:
        profile.company_id = str(uuid.uuid4())

    company_storage[profile.company_id] = profile

    logger.info(f"企业信息已保存: {profile.company_id}, 企业名: {profile.name}")

    return profile


@router.get("/company/profile/{company_id}", response_model=CompanyProfile)
async def get_company_profile(company_id: str):
    """
    获取企业信息

    Args:
        company_id: 企业ID

    Returns:
        企业信息
    """
    if company_id not in company_storage:
        raise HTTPException(status_code=404, detail="企业信息不存在")

    return company_storage[company_id]


@router.post("/products", response_model=ProductSpecification)
async def add_product(product: ProductSpecification):
    """
    添加产品信息

    Args:
        product: 产品信息

    Returns:
        已保存的产品信息
    """
    if not product.product_id:
        product.product_id = str(uuid.uuid4())

    product_storage[product.product_id] = product

    logger.info(f"产品已添加: {product.product_id}, 产品名: {product.product_name}")

    return product


@router.get("/products/{product_id}", response_model=ProductSpecification)
async def get_product(product_id: str):
    """
    获取产品信息

    Args:
        product_id: 产品ID

    Returns:
        产品信息
    """
    if product_id not in product_storage:
        raise HTTPException(status_code=404, detail="产品信息不存在")

    return product_storage[product_id]


@router.get("/products", response_model=list[ProductSpecification])
async def list_products():
    """
    获取所有产品列表

    Returns:
        产品列表
    """
    return list(product_storage.values())


@router.post("/bid/generate", response_model=BidGenerateResponse)
async def generate_bid_document(request: BidGenerateRequest):
    """
    生成投标文件

    Args:
        request: 投标文件生成请求

    Returns:
        生成结果
    """
    # 验证输入数据
    if request.tender_id not in tender_storage:
        raise HTTPException(status_code=404, detail="招标文件不存在")

    tender_info = tender_storage[request.tender_id]
    if tender_info["status"] != "parsed":
        raise HTTPException(status_code=400, detail="招标文件未解析")

    if request.company_profile_id not in company_storage:
        raise HTTPException(status_code=404, detail="企业信息不存在")

    # 准备数据
    tender_doc = TenderDocument(**tender_info["parsed_data"])
    company_profile = company_storage[request.company_profile_id]

    # 获取产品信息
    products = {}
    for package_id, product_id in request.product_ids.items():
        if product_id not in product_storage:
            raise HTTPException(status_code=404, detail=f"产品 {product_id} 不存在")
        products[package_id] = product_storage[product_id]

    try:
        # 创建生成器
        llm = get_chat_model()
        generator = create_bid_generator(llm)
        workflow_agent = TenderWorkflowAgent(llm)
        raw_text = str(tender_info.get("raw_text", "") or "")

        # 初始化状态
        initial_state: BidGenerationState = {
            "tender_doc": tender_doc,
            "company_profile": company_profile,
            "products": products,
            "request": request,
            "sections": [],
            "current_section": "",
            "errors": [],
            "bid_id": "",
            "status": "generating"
        }

        # 执行生成
        final_state = generator.generate(initial_state)

        bid_id = final_state["bid_id"]
        generated_sections = final_state["sections"]
        analysis_result = _default_step1_result(tender_doc)
        clause_result = workflow_agent.step3_classify_clauses(
            tender=tender_doc,
            analysis_result=analysis_result,
            selected_packages=request.selected_packages,
            raw_text=raw_text,
        )
        normalization_result = workflow_agent.step4_normalize_requirements(
            tender=tender_doc,
            analysis_result=analysis_result,
            clause_result=clause_result,
            selected_packages=request.selected_packages,
            raw_text=raw_text,
        )
        product_fact_result = _extract_product_facts(
            tender=tender_doc,
            products=products,
            selected_packages=request.selected_packages,
        )
        evidence_result = workflow_agent.step4_bind_evidence(
            tender=tender_doc,
            raw_text=raw_text,
            analysis_result=analysis_result,
            clause_result=clause_result,
            company=company_profile,
            products=products,
            selected_packages=request.selected_packages,
            normalized_result=normalization_result,
            product_fact_result=product_fact_result,
        )
        materialized_sections, materialize_report = _materialize_sections(
            sections=generated_sections,
            tender=tender_doc,
            company=company_profile,
            products=products,
            evidence_result=evidence_result,
        )
        consistency_report = _second_validation(
            analysis_result=analysis_result,
            validation_result={"overall_status": "通过"},
            sections=materialized_sections,
            generation_result={
                "selected_packages": request.selected_packages,
                "citations": [],
            },
            tender=tender_doc,
            selected_packages=request.selected_packages,
            products=products,
            evidence_result=evidence_result,
        )
        sections, outbound_report = _sanitize_for_external_delivery(
            materialized_sections,
            hard_validation_result=consistency_report,
            evidence_result=evidence_result,
        )
        stored_sections = _sections_for_storage_or_response(materialized_sections, sections, outbound_report)
        outbound_view = _build_external_delivery_view(
            sections,
            outbound_report,
            download_url=f"/api/tender/bid/download/{bid_id}?format=docx",
        )

        # 保存结果
        bid_storage[bid_id] = {
            "bid_id": bid_id,
            "tender_id": request.tender_id,
            "company_id": request.company_profile_id,
            "sections": [s.model_dump() for s in stored_sections],
            "generated_time": datetime.now(),
            "status": "generated",
            "materialize_report": materialize_report,
            "consistency_report": consistency_report,
            "outbound_report": outbound_view,
        }

        logger.info(
            "投标文件生成成功: %s, 共 %d 个章节，深注入更新 %d 个章节，一致性状态=%s，外发状态=%s",
            bid_id,
            len(stored_sections),
            len(materialize_report.get("changed_sections", [])),
            consistency_report.get("overall_status", "unknown"),
            outbound_view.get("status", "unknown"),
        )

        return BidGenerateResponse(
            bid_id=bid_id,
            tender_id=request.tender_id,
            status="generated",
            sections=stored_sections,
            materialize_report=materialize_report,
            consistency_report=consistency_report,
            outbound_report=outbound_view,
            file_path="",  # 暂时为空，后续添加PDF生成功能
            download_url=outbound_view.get("download_url", ""),
            generated_time=datetime.now()
        )

    except Exception as e:
        logger.error(f"投标文件生成失败: {str(e)}")
        raise HTTPException(status_code=500, detail=f"生成失败: {str(e)}")


@router.get("/bid/{bid_id}", response_model=BidGenerateResponse)
async def get_bid_document(bid_id: str):
    """
    获取已生成的投标文件信息

    Args:
        bid_id: 投标文件ID

    Returns:
        投标文件信息
    """
    if bid_id not in bid_storage:
        raise HTTPException(status_code=404, detail="投标文件不存在")

    bid_info = bid_storage[bid_id]

    sections = [BidDocumentSection(**s) for s in bid_info["sections"]]
    outbound_report = bid_info.get("outbound_report", {})

    return BidGenerateResponse(
        bid_id=bid_info["bid_id"],
        tender_id=bid_info["tender_id"],
        status=bid_info["status"],
        sections=sections,
        materialize_report=bid_info.get("materialize_report", {}),
        consistency_report=bid_info.get("consistency_report", {}),
        outbound_report=outbound_report,
        file_path=bid_info.get("file_path", ""),
        download_url=str(outbound_report.get("download_url", "") or ""),
        generated_time=bid_info["generated_time"]
    )


@router.get("/bid/download/{bid_id}")
async def download_bid_document(bid_id: str, format: str = "docx"):
    """
    下载投标文件

    Args:
        bid_id: 投标文件ID
        format: 文件格式 (docx/markdown)

    Returns:
        文件下载
    """
    if bid_id not in bid_storage:
        raise HTTPException(status_code=404, detail="投标文件不存在")

    bid_info = bid_storage[bid_id]
    if _is_external_delivery_blocked(bid_info.get("outbound_report")):
        raise HTTPException(status_code=409, detail="当前标书未通过硬校验，已阻断外发下载")
    sections = [BidDocumentSection(**s) for s in bid_info["sections"]]

    if format == "docx":
        # 获取招标文件和企业信息，用于封面
        tender_info = tender_storage.get(bid_info["tender_id"])
        company = company_storage.get(bid_info["company_id"])

        if not tender_info or not company:
            raise HTTPException(status_code=404, detail="关联的招标/企业信息不存在")

        tender_doc = TenderDocument(**tender_info["parsed_data"])
        output_file = BID_OUTPUT_DIR / f"{bid_id}.docx"

        try:
            build_bid_docx(sections, tender_doc, company, output_file)
        except Exception as e:
            logger.error(f"Word文件生成失败: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Word文件生成失败: {str(e)}")

        return FileResponse(
            output_file,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename=f"投标文件_{tender_doc.project_name}.docx",
        )

    elif format == "markdown":
        output_file = BID_OUTPUT_DIR / f"{bid_id}.md"

        with output_file.open("w", encoding="utf-8") as f:
            for section in sections:
                f.write(f"\n\n{section.content}\n\n")
                if section.attachments:
                    f.write("\n**附件：**\n")
                    for att in section.attachments:
                        f.write(f"- {att}\n")
                f.write("\n---\n")

        return FileResponse(
            output_file,
            media_type="text/markdown",
            filename=f"投标文件_{bid_id}.md"
        )

    else:
        raise HTTPException(status_code=400, detail="不支持的格式，请使用 docx 或 markdown")


# ──────────────────────────────────────────────────────────────────────────────
# 正式流程：文档接入 → 包件切分 → 条款分类 → 需求归一化 → 规则决策 →
# 证据绑定 → 分章节生成 → 硬校验 → 双输出 → 评测回归
# ──────────────────────────────────────────────────────────────────────────────

def _resolve_selected_packages(tender_doc: TenderDocument, selected_packages: list[str]) -> list[str]:
    package_ids = {pkg.package_id for pkg in tender_doc.packages}
    if not selected_packages:
        return sorted(package_ids)
    return [pkg_id for pkg_id in selected_packages if pkg_id in package_ids] or sorted(package_ids)


def _resolve_raw_text_for_tender(tender_info: dict, parser) -> str:
    raw_text = str(tender_info.get("raw_text") or "").strip()
    if raw_text:
        return raw_text
    file_path = tender_info.get("file_path")
    if file_path:
        raw_text = parser.extract_text(file_path)
        tender_info["raw_text"] = raw_text
        return raw_text
    return ""


def _ensure_tender_parsed_for_workflow(tender_id: str, tender_info: dict, parser) -> TenderDocument:
    parsed_data = tender_info.get("parsed_data")
    if parsed_data:
        if tender_info.get("status") != "parsed":
            tender_info["status"] = "parsed"
        return TenderDocument(**parsed_data)

    file_path = tender_info.get("file_path")
    if not file_path:
        raise HTTPException(status_code=400, detail="招标文件缺少可解析文件路径")

    tender_info["status"] = "parsing"
    try:
        tender_doc = parser.parse_tender_document(file_path)
        raw_text = parser.extract_text(file_path)
        tender_info["parsed_data"] = tender_doc.model_dump()
        tender_info["raw_text"] = raw_text
        tender_info["status"] = "parsed"
        logger.info("工作流自动完成招标解析: %s", tender_id)
        return tender_doc
    except Exception as exc:
        tender_info["status"] = "error"
        tender_info["error_message"] = str(exc)
        logger.error("工作流自动解析失败: %s", exc)
        raise HTTPException(status_code=500, detail=f"工作流自动解析失败: {exc}")


def _ensure_tender_indexed_for_workflow(tender_id: str, raw_text: str) -> str | None:
    text = (raw_text or "").strip()
    if not text:
        return None

    source = f"tender::{tender_id}"
    if source in workflow_kb_indexed_sources:
        return source

    try:
        ingest_text_to_kb(
            text=text,
            source=source,
            metadata={"tender_id": tender_id, "doc_type": "tender_raw"},
        )
        workflow_kb_indexed_sources.add(source)
        logger.info("已入库招标原文，source=%s", source)
        return source
    except Exception as exc:  # noqa: BLE001
        logger.warning("招标原文入库失败，不影响主流程：%s", exc)
        return None


def _workflow_stage(
    stage_code: str,
    stage_name: str,
    status: str,
    summary: str,
    data: dict | None = None,
    issues: list[str] | None = None,
) -> dict:
    return {
        "stage_code": stage_code,
        "stage_name": stage_name,
        "status": status,
        "summary": summary,
        "data": data or {},
        "issues": issues or [],
    }


@router.post("/workflow/run", response_model=TenderWorkflowResponse, summary="运行十层AI正式流程")
async def run_tender_workflow(req: TenderWorkflowRequest):
    """
    十层正式流程：
    1) 文档接入；
    2) 包件切分；
    3) 条款分类；
    4) 需求归一化；
    5) 规则决策；
    6) 证据绑定；
    7) 分章节生成；
    8) 硬校验；
    9) 双输出；
    10) 评测回归。

    兼容保留 analysis/material_validation/generation/review 四类摘要字段。
    """
    if req.tender_id not in tender_storage:
        raise HTTPException(status_code=404, detail="招标文件不存在")

    tender_info = tender_storage[req.tender_id]
    llm = get_chat_model()
    parser = create_tender_parser(llm)
    was_preparsed = bool(tender_info.get("parsed_data")) and tender_info.get("status") == "parsed"
    tender_doc = _ensure_tender_parsed_for_workflow(req.tender_id, tender_info, parser)
    raw_text = _resolve_raw_text_for_tender(tender_info, parser)
    kb_source = _ensure_tender_indexed_for_workflow(req.tender_id, raw_text)

    selected_packages = _resolve_selected_packages(tender_doc, req.selected_packages)
    company = company_storage.get(req.company_profile_id) if req.company_profile_id else None

    products: dict[str, ProductSpecification] = {}
    for pkg_id in selected_packages:
        product_id = req.product_ids.get(pkg_id)
        if not product_id:
            continue
        product = product_storage.get(product_id)
        if product:
            products[pkg_id] = product

    workflow_agent = TenderWorkflowAgent(llm)
    stages: list[dict] = []

    ingestion_dict = _build_document_ingestion_view(
        raw_text=raw_text,
        file_path=tender_info.get("file_path"),
        tender_id=req.tender_id,
    )
    stages.append(
        _workflow_stage(
            stage_code="document_ingestion",
            stage_name="文档接入",
            status="completed",
            summary=ingestion_dict.get("summary", "文档接入完成。"),
            data={
                **ingestion_dict,
                "project_name": tender_doc.project_name,
                "project_number": tender_doc.project_number,
                "parse_mode": "cached" if was_preparsed else "auto",
            },
        )
    )

    package_scope_dict = _build_package_segmentation_view(
        tender=tender_doc,
        raw_text=raw_text,
        selected_packages=selected_packages,
    )
    package_stage_status = "completed" if not package_scope_dict.get("missing_scope_packages") else "warning"
    stages.append(
        _workflow_stage(
            stage_code="package_segmentation",
            stage_name="包件切分",
            status=package_stage_status,
            summary=package_scope_dict.get("summary", "包件切分完成。"),
            data=package_scope_dict,
            issues=package_scope_dict.get("missing_scope_packages", []),
        )
    )

    analysis_dict = workflow_agent.step1_analyze_tender(
        tender=tender_doc,
        raw_text=raw_text,
        kb_source=kb_source,
    )

    # 内部资料校验（兼容旧摘要字段）
    validation_dict = workflow_agent.step2_validate_materials(
        tender=tender_doc,
        required_materials=analysis_dict.get("required_materials", []),
        selected_packages=selected_packages,
        company=company,
        products=products,
    )

    clause_dict = workflow_agent.step3_classify_clauses(
        tender=tender_doc,
        analysis_result=analysis_dict,
        selected_packages=selected_packages,
        raw_text=raw_text,
    )
    stages.append(
        _workflow_stage(
            stage_code="clause_classification",
            stage_name="条款分类",
            status="completed",
            summary=clause_dict.get("summary", "已完成条款分类。"),
            data=clause_dict,
        )
    )

    normalization_dict = workflow_agent.step4_normalize_requirements(
        tender=tender_doc,
        analysis_result=analysis_dict,
        clause_result=clause_dict,
        selected_packages=selected_packages,
        raw_text=raw_text,
    )
    product_fact_dict = _extract_product_facts(
        tender=tender_doc,
        products=products,
        selected_packages=selected_packages,
    )
    requirement_match_dict = _match_requirements_to_product_facts(
        normalized_result=normalization_dict,
        product_fact_result=product_fact_dict,
        company=company,
        products=products,
    )
    stages.append(
        _workflow_stage(
            stage_code="requirement_normalization",
            stage_name="需求归一化",
            status="completed",
            summary=normalization_dict.get("summary", "需求归一化完成。"),
            data={
                **normalization_dict,
                "product_fact_extraction": product_fact_dict,
                "response_value_hints": {
                    "technical_matches": requirement_match_dict.get("technical_matches", []),
                    "summary": requirement_match_dict.get("summary", ""),
                },
            },
        )
    )

    # ── 详细展开 (Detail Expander) ──
    detail_expansion_dict = _expand_extracted_facts(
        normalized_result=normalization_dict,
        products=products,
        tender=tender_doc,
    )
    stages.append(
        _workflow_stage(
            stage_code="detail_expansion",
            stage_name="详细展开",
            status="completed",
            summary=detail_expansion_dict.get("summary", "详细展开完成。"),
            data=detail_expansion_dict,
        )
    )

    rule_dict = workflow_agent.step5_decide_rules(
        tender=tender_doc,
        raw_text=raw_text,
        selected_packages=selected_packages,
        company=company,
        products=products,
        clause_result=clause_dict,
    )
    rule_blocking_items = rule_dict.get("blocking_fill_items", [])
    rule_manual_items = rule_dict.get("manual_fill_items", [])
    rule_stage_status = "blocked" if rule_blocking_items else "completed" if not rule_manual_items else "warning"
    stages.append(
        _workflow_stage(
            stage_code="rule_decision",
            stage_name="规则决策",
            status=rule_stage_status,
            summary=rule_dict.get("summary", "规则决策完成。"),
            data={
                **rule_dict,
                "material_validation_status": validation_dict.get("overall_status", ""),
            },
            issues=rule_blocking_items + [item for item in rule_manual_items if item not in rule_blocking_items],
        )
    )

    evidence_dict = workflow_agent.step4_bind_evidence(
        tender=tender_doc,
        raw_text=raw_text,
        analysis_result=analysis_dict,
        clause_result=clause_dict,
        company=company,
        products=products,
        selected_packages=selected_packages,
        normalized_result=normalization_dict,
        product_fact_result=product_fact_dict,
    )
    evidence_stage_status = "completed" if not evidence_dict.get("issues") else "warning"
    stages.append(
        _workflow_stage(
            stage_code="evidence_binding",
            stage_name="证据绑定",
            status=evidence_stage_status,
            summary=evidence_dict.get("summary", "已完成证据绑定。"),
            data={
                **evidence_dict,
                "requirement_product_matching": requirement_match_dict,
            },
            issues=evidence_dict.get("issues", []),
        )
    )

    should_continue = req.continue_on_material_gaps or (
        validation_dict.get("overall_status") == "通过" and not rule_blocking_items
    )
    sections: list[BidDocumentSection] = []
    internal_sections: list[BidDocumentSection] = []
    sanitized_sections: list[BidDocumentSection] = []
    sanitize_stage_data: dict = {
        "status": "未执行",
        "changed_sections": [],
        "placeholder_sections": [],
        "summary": "第九层未执行，外发净化未执行。",
    }
    hard_validation_dict: dict | None = None
    review_dict: dict | None = None
    materialize_dict: dict = {
        "changed_sections": [],
        "unresolved_sections": [],
        "summary": "尚未执行正文实参注入。",
    }

    generation_dict: dict = {
        "generated": False,
        "bid_id": "",
        "section_titles": [],
        "citations": [],
        "download_url": "",
        "file_path": "",
        "integration_notes": "",
        "selected_packages": selected_packages,
        "product_summary": [],
        "summary": "资料校验未通过，已阻断分章节生成。",
    }

    generation_stage_status = "blocked"
    generation_stage_issues: list[str] = []
    if should_continue:
        company_for_docx = company or _PLACEHOLDER_COMPANY
        # 保证下载接口可读取公司信息
        company_storage[company_for_docx.company_id or "placeholder"] = company_for_docx

        step3_dict, sections = workflow_agent.step3_integrate_bid(
            tender=tender_doc,
            raw_text=raw_text,
            selected_packages=selected_packages,
            company=company_for_docx,
            products=products,
            kb_source=kb_source,
            analysis_result=analysis_dict,
            product_fact_result=product_fact_dict,
            normalized_result=normalization_dict,
            evidence_result=evidence_dict,
        )
        internal_sections, materialize_dict = _materialize_sections(
            sections=sections,
            tender=tender_doc,
            company=company,
            products=products,
            evidence_result=evidence_dict,
        )
        sections = internal_sections

        bid_id = f"WF_BID_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}"
        generation_dict = {
            "generated": True,
            "bid_id": bid_id,
            "section_titles": [s.section_title for s in sections],
            "citations": step3_dict.get("citations", []),
            "download_url": (
                f"/api/tender/bid/download/{bid_id}?format=docx"
                if req.generate_docx
                else f"/api/tender/bid/download/{bid_id}?format=markdown"
            ),
            "file_path": "",
            "integration_notes": step3_dict.get("integration_notes", ""),
            "selected_packages": step3_dict.get("selected_packages", selected_packages),
            "product_summary": step3_dict.get("product_summary", []),
            "materialize_report": materialize_dict,
            "summary": step3_dict.get("summary", "标书已生成。"),
        }
        generation_stage_status = "completed" if not materialize_dict.get("unresolved_sections") else "warning"
    else:
        generation_stage_issues = (
            validation_dict.get("missing_items", [])
            or rule_blocking_items
            or ["资料校验未通过，流程已阻断。"]
        )
        if rule_blocking_items and validation_dict.get("overall_status") == "通过":
            generation_dict["summary"] = "规则决策识别到关键阻断项，已阻断分章节生成。"

    stages.append(
        _workflow_stage(
            stage_code="chapter_generation",
            stage_name="分章节生成",
            status=generation_stage_status,
            summary=generation_dict.get("summary", ""),
            data={
                **generation_dict,
                "material_validation_status": validation_dict.get("overall_status", ""),
                "materialize_report": materialize_dict,
            },
            issues=generation_stage_issues or materialize_dict.get("unresolved_sections", []),
        )
    )

    if sections:
        hard_validation_dict = workflow_agent.step6_validate_consistency(
            analysis_result=analysis_dict,
            validation_result=validation_dict,
            sections=sections,
            generation_result=generation_dict,
            tender=tender_doc,
            selected_packages=selected_packages,
            products=products,
            evidence_result=evidence_dict,
        )
        review_dict = workflow_agent.step4_review_bid(
            tender=tender_doc,
            analysis_result=analysis_dict,
            validation_result=validation_dict,
            sections=sections,
            generation_result=generation_dict,
            selected_packages=selected_packages,
            products=products,
            evidence_result=evidence_dict,
        )
        review_dict["secondary_validation"] = hard_validation_dict
        hard_stage_status = (
            "completed"
            if hard_validation_dict.get("overall_status") == "通过" and review_dict.get("ready_for_submission")
            else "warning"
        )
        stages.append(
            _workflow_stage(
                stage_code="hard_validation",
                stage_name="硬校验",
                status=hard_stage_status,
                summary=review_dict.get("conclusion", "合规校验完成。"),
                data={
                    **hard_validation_dict,
                    "review": review_dict,
                },
                issues=_ensure_str_list(hard_validation_dict.get("issues", [])) + review_dict.get("major_issues", []),
            )
        )

        sanitized_sections, sanitize_stage_data = workflow_agent.step8_sanitize_outbound(
            sections,
            hard_validation_result=hard_validation_dict,
            evidence_result=evidence_dict,
        )

        bid_id = generation_dict["bid_id"]
        company_for_docx = company or _PLACEHOLDER_COMPANY
        output_file = BID_OUTPUT_DIR / f"{bid_id}.docx"
        if req.generate_docx and not _is_external_delivery_blocked(sanitize_stage_data):
            build_bid_docx(sanitized_sections, tender_doc, company_for_docx, output_file)
            file_path = str(output_file)
        else:
            file_path = ""
        if _is_external_delivery_blocked(sanitize_stage_data):
            generation_dict["download_url"] = ""

        stored_sections = _sections_for_storage_or_response(sections, sanitized_sections, sanitize_stage_data)
        outbound_view = _build_external_delivery_view(
            sanitized_sections,
            sanitize_stage_data,
            download_url=generation_dict.get("download_url", ""),
            file_path=file_path,
        )
        generation_dict["file_path"] = file_path
        generation_dict["download_url"] = outbound_view.get("download_url", "")
        bid_storage[bid_id] = {
            "bid_id": bid_id,
            "tender_id": req.tender_id,
            "company_id": company_for_docx.company_id,
            "sections": [s.model_dump() for s in stored_sections],
            "generated_time": datetime.now(),
            "status": "generated",
            "file_path": file_path,
            "materialize_report": materialize_dict,
            "consistency_report": hard_validation_dict,
            "outbound_report": outbound_view,
        }

        sanitize_stage_data = {
            **outbound_view,
            "bid_id": bid_id,
        }
        internal_audit = _build_internal_audit_snapshot(
            ingestion_result=ingestion_dict,
            package_result=package_scope_dict,
            clause_result=clause_dict,
            normalized_result=normalization_dict,
            product_fact_result=product_fact_dict,
            rule_result=rule_dict,
            evidence_result=evidence_dict,
            validation_result=validation_dict,
            hard_validation_result=hard_validation_dict,
            sections=sections,
        )
        dual_output_summary = (
            f"已输出内部审计版与外发净化版；内部章节 {len(sections)} 章，外发章节 {len(sanitized_sections)} 章。"
            if not _is_external_delivery_blocked(sanitize_stage_data)
            else f"已保留内部审计版；外发净化版已阻断，内部章节 {len(sections)} 章。"
        )
        dual_output_stage_status = (
            "blocked"
            if _is_external_delivery_blocked(sanitize_stage_data)
            else "completed" if sanitize_stage_data.get("status") == "通过" else "warning"
        )
        stages.append(
            _workflow_stage(
                stage_code="dual_output",
                stage_name="双输出",
                status=dual_output_stage_status,
                summary=dual_output_summary,
                data={
                    "internal_audit": internal_audit,
                    "external_delivery": sanitize_stage_data,
                },
                issues=sanitize_stage_data.get("placeholder_sections", []) + sanitize_stage_data.get("blocked_reasons", []),
            )
        )
    else:
        hard_validation_dict = {
            "executed": False,
            "overall_status": "需修订",
            "check_items": [],
            "issues": ["分章节生成未执行，无法完成发布前硬校验。"],
            "suggestions": ["先补齐资料缺口并完成分章节生成。"],
            "summary": "硬校验未执行：缺少生成章节。",
        }
        stages.append(
            _workflow_stage(
                stage_code="hard_validation",
                stage_name="硬校验",
                status="blocked",
                summary=hard_validation_dict["summary"],
                data=hard_validation_dict,
                issues=hard_validation_dict["issues"],
            )
        )

        review_dict = {
            "ready_for_submission": False,
            "risk_level": "high",
            "compliance_score": 0.0,
            "major_issues": ["第七层未执行，暂无可审核标书内容。"],
            "recommendations": ["先补齐资料缺失项，然后重新运行流程。"],
            "secondary_validation": hard_validation_dict,
            "conclusion": "当前流程已阻断，暂不具备提交条件。",
        }
        internal_audit = _build_internal_audit_snapshot(
            ingestion_result=ingestion_dict,
            package_result=package_scope_dict,
            clause_result=clause_dict,
            normalized_result=normalization_dict,
            product_fact_result=product_fact_dict,
            rule_result=rule_dict,
            evidence_result=evidence_dict,
            validation_result=validation_dict,
            hard_validation_result=hard_validation_dict,
            sections=[],
        )
        stages.append(
            _workflow_stage(
                stage_code="dual_output",
                stage_name="双输出",
                status="skipped",
                summary="已保留内部审计信息，外发净化版未生成。",
                data={
                    "internal_audit": internal_audit,
                    "external_delivery": sanitize_stage_data,
                },
                issues=[],
            )
        )

    regression_dict = workflow_agent.step9_regression(
        stages=stages,
        consistency_result=hard_validation_dict,
        review_result=review_dict,
        sanitize_result=sanitize_stage_data,
        evidence_result=evidence_dict,
        normalized_result=normalization_dict,
        product_fact_result=product_fact_dict,
        sections=sections or None,
        selected_packages=selected_packages,
    )
    regression_stage_status = "completed" if regression_dict.get("overall_status") == "通过" else "warning"
    stages.append(
        _workflow_stage(
            stage_code="evaluation_regression",
            stage_name="评测回归",
            status=regression_stage_status,
            summary=regression_dict.get("summary", "评测回归完成。"),
            data=regression_dict,
            issues=[
                item["name"]
                for item in regression_dict.get("checks", [])
                if item.get("status") != "通过"
            ],
        )
    )

    workflow_status = "completed" if should_continue else "blocked"
    workflow_id = str(uuid.uuid4())

    response = TenderWorkflowResponse(
        workflow_id=workflow_id,
        tender_id=req.tender_id,
        status=workflow_status,
        stages=stages,
        analysis=TenderWorkflowStep1Result(**analysis_dict),
        material_validation=TenderWorkflowStep2Result(**validation_dict),
        generation=TenderWorkflowStep3Result(**generation_dict),
        review=TenderWorkflowStep4Result(**review_dict),
        generated_time=datetime.now(),
    )

    workflow_storage[workflow_id] = response.model_dump()
    return response


@router.get("/workflow/{workflow_id}", response_model=TenderWorkflowResponse, summary="查询十层流程结果")
async def get_tender_workflow_result(workflow_id: str):
    if workflow_id not in workflow_storage:
        raise HTTPException(status_code=404, detail="流程结果不存在")
    return TenderWorkflowResponse(**workflow_storage[workflow_id])


# ──────────────────────────────────────────────────────────────────────────────
# 一键生成接口：上传招标文件 → 自动输出投标文件 Word
# ──────────────────────────────────────────────────────────────────────────────

_ALLOWED_SUFFIXES = {".pdf", ".docx", ".doc"}
_ONE_CLICK_STEP_LABELS = {
    "queued": "准备开始",
    "extracting": "文档解析",
    "parsing": "需求抽取",
    "structuring": "条款整理",
    "generating": "章节生成",
    "building": "输出文档",
    "completed": "处理完成",
    "error": "处理失败",
}

# 占位公司信息（用于生成封面）
_PLACEHOLDER_COMPANY = CompanyProfile(
    company_id="placeholder",
    name="[投标方公司名称]",
    legal_representative="[法定代表人]",
    address="[公司注册地址]",
    phone="[联系电话]",
)


def _set_one_click_job_status(
    job_id: str,
    *,
    status: str,
    step_code: str,
    message: str,
    progress: int,
    filename: str = "",
    download_url: str = "",
    error: str = "",
) -> None:
    one_click_job_storage[job_id] = {
        "job_id": job_id,
        "status": status,
        "step_code": step_code,
        "step_label": _ONE_CLICK_STEP_LABELS.get(step_code, step_code),
        "message": message,
        "progress": max(0, min(100, progress)),
        "filename": filename,
        "download_url": download_url,
        "error": error,
        "updated_time": datetime.now(),
    }


def _run_one_click_generation(job_id: str, save_path: Path) -> None:
    try:
        _set_one_click_job_status(
            job_id,
            status="running",
            step_code="extracting",
            message="正在解析文档内容…",
            progress=12,
        )
        llm = get_chat_model()
        parser = create_tender_parser(llm)
        raw_text = parser.extract_text(save_path)

        _set_one_click_job_status(
            job_id,
            status="running",
            step_code="parsing",
            message="正在抽取招标需求…",
            progress=32,
        )
        tender_doc = parser.parse_tender_document(save_path)

        _set_one_click_job_status(
            job_id,
            status="running",
            step_code="structuring",
            message="正在整理条款与技术参数…",
            progress=52,
        )

        _set_one_click_job_status(
            job_id,
            status="running",
            step_code="generating",
            message="正在生成投标文件章节…",
            progress=72,
        )
        sections = generate_bid_sections(tender_doc, raw_text, llm)

        _set_one_click_job_status(
            job_id,
            status="running",
            step_code="building",
            message="正在输出 Word 文档…",
            progress=90,
        )
        output_file = BID_OUTPUT_DIR / f"{job_id}_投标文件.docx"
        build_bid_docx(sections, tender_doc, _PLACEHOLDER_COMPANY, output_file)

        safe_name = tender_doc.project_name.replace("/", "_").replace("\\", "_")
        filename = f"投标文件_{safe_name}.docx"
        download_url = f"/api/tender/one-click/download/{job_id}"
        logger.info("一键生成任务完成: %s -> %s", job_id, output_file)
        _set_one_click_job_status(
            job_id,
            status="completed",
            step_code="completed",
            message="投标文件已生成完成，可直接下载。",
            progress=100,
            filename=filename,
            download_url=download_url,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error("一键生成后台任务失败：%s", exc, exc_info=True)
        _set_one_click_job_status(
            job_id,
            status="error",
            step_code="error",
            message=f"生成失败：{exc}",
            progress=0,
            error=str(exc),
        )
    finally:
        try:
            save_path.unlink(missing_ok=True)
        except Exception:
            logger.warning("一键生成临时文件清理失败: %s", save_path)


@router.post("/one-click", summary="一键生成投标文件")
async def one_click_generate(file: UploadFile = File(...)):
    """
    上传招标文件（PDF 或 Word），自动解析并生成可下载的投标文件 Word 文档。

    - 输入：招标文件（.pdf / .docx）
    - 输出：投标文件（.docx）下载链接
    """
    # 1. 校验文件格式
    filename = file.filename or ""
    suffix = Path(filename).suffix.lower()
    if suffix not in _ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式 '{suffix}'，请上传 PDF 或 Word（.docx）文件",
        )

    # 2. 保存上传文件
    job_id = str(uuid.uuid4())
    save_path = UPLOAD_DIR / f"{job_id}{suffix}"
    try:
        with save_path.open("wb") as buf:
            shutil.copyfileobj(file.file, buf)
    except Exception as exc:
        logger.error("文件保存失败: %s", exc)
        raise HTTPException(status_code=500, detail=f"文件保存失败: {exc}")

    try:
        # 3. 解析招标文件
        llm = get_chat_model()
        parser = create_tender_parser(llm)

        logger.info("开始提取文本：%s", save_path)
        raw_text = parser.extract_text(save_path)

        logger.info("开始解析招标结构")
        tender_doc = parser.parse_tender_document(save_path)

        # 4. 一键生成投标文件各章节（按固定模板生成，使用解析后的结构化数据）
        sections = generate_bid_sections(tender_doc, raw_text, llm)

        # 5. 构建 Word 文档
        output_file = BID_OUTPUT_DIR / f"{job_id}_投标文件.docx"
        build_bid_docx(sections, tender_doc, _PLACEHOLDER_COMPANY, output_file)

        logger.info("投标文件生成成功：%s", output_file)

        # 6. 返回文件下载
        safe_name = tender_doc.project_name.replace("/", "_").replace("\\", "_")
        return FileResponse(
            output_file,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename=f"投标文件_{safe_name}.docx",
        )

    except Exception as exc:
        logger.error("一键生成失败：%s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"生成失败：{exc}")
    finally:
        # 清理上传的临时文件
        try:
            save_path.unlink(missing_ok=True)
        except Exception:
            pass


@router.post("/one-click/start", response_model=OneClickJobStartResponse, summary="启动一键生成任务")
async def start_one_click_generate(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    filename = file.filename or ""
    suffix = Path(filename).suffix.lower()
    if suffix not in _ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式 '{suffix}'，请上传 PDF 或 Word（.docx）文件",
        )

    job_id = str(uuid.uuid4())
    save_path = UPLOAD_DIR / f"{job_id}{suffix}"
    try:
        with save_path.open("wb") as buf:
            shutil.copyfileobj(file.file, buf)
    except Exception as exc:
        logger.error("一键生成任务文件保存失败: %s", exc)
        raise HTTPException(status_code=500, detail=f"文件保存失败: {exc}")

    _set_one_click_job_status(
        job_id,
        status="queued",
        step_code="queued",
        message="文件上传成功，正在准备开始…",
        progress=3,
    )
    background_tasks.add_task(_run_one_click_generation, job_id, save_path)
    return OneClickJobStartResponse(**one_click_job_storage[job_id])


@router.get("/one-click/status/{job_id}", response_model=OneClickJobStatusResponse, summary="查询一键生成任务状态")
async def get_one_click_job_status(job_id: str):
    job_info = one_click_job_storage.get(job_id)
    if not job_info:
        raise HTTPException(status_code=404, detail="任务不存在")
    return OneClickJobStatusResponse(**job_info)


@router.get("/one-click/download/{job_id}", summary="下载一键生成结果")
async def download_one_click_result(job_id: str):
    job_info = one_click_job_storage.get(job_id)
    if not job_info:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job_info.get("status") != "completed":
        raise HTTPException(status_code=409, detail="任务尚未完成")

    output_file = BID_OUTPUT_DIR / f"{job_id}_投标文件.docx"
    if not output_file.exists():
        raise HTTPException(status_code=404, detail="生成文件不存在")

    return FileResponse(
        output_file,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=job_info.get("filename") or f"投标文件_{job_id}.docx",
    )
