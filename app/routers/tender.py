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
    ErrorResponse
)
from app.services.tender_parser import create_tender_parser
from app.services.bid_generator import create_bid_generator, BidGenerationState
from app.services.llm import get_llm

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
        llm = get_llm()
        parser = create_tender_parser(llm)

        # 解析PDF
        file_path = tender_info["file_path"]
        tender_doc = parser.parse_tender_document(file_path)

        # 获取原始文本长度
        raw_text = parser.extract_text_from_pdf(file_path)
        raw_text_length = len(raw_text)

        # 存储解析结果
        tender_info["parsed_data"] = tender_doc.model_dump()
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
        llm = get_llm()
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
            download_url=f"/api/tender/bid/download/{bid_id}",
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
async def download_bid_document(bid_id: str, format: str = "markdown"):
    """
    下载投标文件

    Args:
        bid_id: 投标文件ID
        format: 文件格式 (markdown/pdf)

    Returns:
        文件下载
    """
    if bid_id not in bid_storage:
        raise HTTPException(status_code=404, detail="投标文件不存在")

    bid_info = bid_storage[bid_id]
    sections = [BidDocumentSection(**s) for s in bid_info["sections"]]

    if format == "markdown":
        # 生成Markdown文件
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

    elif format == "pdf":
        # TODO: 实现PDF生成
        raise HTTPException(status_code=501, detail="PDF生成功能待开发")

    else:
        raise HTTPException(status_code=400, detail="不支持的格式")
