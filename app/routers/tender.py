"""招投标系统API路由"""
from fastapi import APIRouter, UploadFile, File, HTTPException
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
    # ErrorResponse
)
from app.services.tender_parser import create_tender_parser
from app.services.bid_generator import create_bid_generator, BidGenerationState
from app.services.one_click_generator import generate_bid_sections
from app.services.docx_builder import build_bid_docx
from app.services.tender_workflow import TenderWorkflowAgent
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
        sections = final_state["sections"]

        # 保存结果
        bid_storage[bid_id] = {
            "bid_id": bid_id,
            "tender_id": request.tender_id,
            "company_id": request.company_profile_id,
            "sections": [s.model_dump() for s in sections],
            "generated_time": datetime.now(),
            "status": "generated"
        }

        logger.info(f"投标文件生成成功: {bid_id}, 共 {len(sections)} 个章节")

        return BidGenerateResponse(
            bid_id=bid_id,
            tender_id=request.tender_id,
            status="generated",
            sections=sections,
            file_path="",  # 暂时为空，后续添加PDF生成功能
            download_url=f"/api/tender/bid/download/{bid_id}?format=docx",
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

    return BidGenerateResponse(
        bid_id=bid_info["bid_id"],
        tender_id=bid_info["tender_id"],
        status=bid_info["status"],
        sections=sections,
        file_path=bid_info.get("file_path", ""),
        download_url=f"/api/tender/bid/download/{bid_id}",
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
# 四阶段正式流程：解析 → 资料校验 → 标书整合 → 审核
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


@router.post("/workflow/run", response_model=TenderWorkflowResponse, summary="运行四阶段AI正式流程")
async def run_tender_workflow(req: TenderWorkflowRequest):
    """
    四阶段正式流程：
    1) 解析招标并提炼关键结果；
    2) 校验已上传资料；
    3) 整合生成标书；
    4) 自动审核并给出结论。
    """
    if req.tender_id not in tender_storage:
        raise HTTPException(status_code=404, detail="招标文件不存在")

    tender_info = tender_storage[req.tender_id]
    if tender_info.get("status") != "parsed":
        raise HTTPException(status_code=400, detail="招标文件未解析，请先执行解析")

    tender_doc = TenderDocument(**tender_info["parsed_data"])
    llm = get_chat_model()
    parser = create_tender_parser(llm)
    raw_text = _resolve_raw_text_for_tender(tender_info, parser)

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

    # Step 1: 解析
    analysis_dict = workflow_agent.step1_analyze_tender(tender_doc, raw_text)

    # Step 2: 资料校验
    validation_dict = workflow_agent.step2_validate_materials(
        tender=tender_doc,
        required_materials=analysis_dict.get("required_materials", []),
        selected_packages=selected_packages,
        company=company,
        products=products,
    )

    should_continue = req.continue_on_material_gaps or validation_dict.get("overall_status") == "通过"
    sections: list[BidDocumentSection] = []

    generation_dict: dict = {
        "generated": False,
        "bid_id": "",
        "section_titles": [],
        "download_url": "",
        "file_path": "",
        "integration_notes": "",
        "summary": "资料校验未通过，已阻断第三步。",
    }

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
        )

        bid_id = f"WF_BID_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:6]}"
        output_file = BID_OUTPUT_DIR / f"{bid_id}.docx"
        if req.generate_docx:
            build_bid_docx(sections, tender_doc, company_for_docx, output_file)
            file_path = str(output_file)
        else:
            file_path = ""

        bid_storage[bid_id] = {
            "bid_id": bid_id,
            "tender_id": req.tender_id,
            "company_id": company_for_docx.company_id,
            "sections": [s.model_dump() for s in sections],
            "generated_time": datetime.now(),
            "status": "generated",
            "file_path": file_path,
        }

        generation_dict = {
            "generated": True,
            "bid_id": bid_id,
            "section_titles": [s.section_title for s in sections],
            "download_url": f"/api/tender/bid/download/{bid_id}?format=docx",
            "file_path": file_path,
            "integration_notes": step3_dict.get("integration_notes", ""),
            "summary": step3_dict.get("summary", "标书已生成。"),
        }

    # Step 4: 审核
    if sections:
        review_dict = workflow_agent.step4_review_bid(
            tender=tender_doc,
            analysis_result=analysis_dict,
            validation_result=validation_dict,
            sections=sections,
        )
    else:
        review_dict = {
            "ready_for_submission": False,
            "risk_level": "high",
            "compliance_score": 0.0,
            "major_issues": ["第三步未执行，暂无可审核标书内容。"],
            "recommendations": ["先补齐第二步缺失项，然后重新运行流程。"],
            "conclusion": "当前流程已阻断，暂不具备提交条件。",
        }

    workflow_status = "completed" if should_continue else "blocked"
    workflow_id = str(uuid.uuid4())

    response = TenderWorkflowResponse(
        workflow_id=workflow_id,
        tender_id=req.tender_id,
        status=workflow_status,
        analysis=TenderWorkflowStep1Result(**analysis_dict),
        material_validation=TenderWorkflowStep2Result(**validation_dict),
        generation=TenderWorkflowStep3Result(**generation_dict),
        review=TenderWorkflowStep4Result(**review_dict),
        generated_time=datetime.now(),
    )

    workflow_storage[workflow_id] = response.model_dump()
    return response


@router.get("/workflow/{workflow_id}", response_model=TenderWorkflowResponse, summary="查询四阶段流程结果")
async def get_tender_workflow_result(workflow_id: str):
    if workflow_id not in workflow_storage:
        raise HTTPException(status_code=404, detail="流程结果不存在")
    return TenderWorkflowResponse(**workflow_storage[workflow_id])


# ──────────────────────────────────────────────────────────────────────────────
# 一键生成接口：上传招标文件 → 自动输出投标文件 Word
# ──────────────────────────────────────────────────────────────────────────────

_ALLOWED_SUFFIXES = {".pdf", ".docx", ".doc"}

# 占位公司信息（用于生成封面）
_PLACEHOLDER_COMPANY = CompanyProfile(
    company_id="placeholder",
    name="[投标方公司名称]",
    legal_representative="[法定代表人]",
    address="[公司注册地址]",
    phone="[联系电话]",
)


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
