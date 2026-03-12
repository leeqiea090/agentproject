from __future__ import annotations

import app.routers.tender.common as _common
import importlib

from app.services.quality_gate import render_editable_draft_sections


def __reexport_all(module) -> None:
    for name, value in vars(module).items():
        if name.startswith("__"):
            continue
        globals()[name] = value


for _module in (_common,):
    __reexport_all(_module)


del _module


def _router_api():
    return importlib.import_module("app.routers.tender")


def _existing_output_file(file_path: str | Path | None) -> Path | None:
    if not file_path:
        return None

    candidate = Path(file_path)
    try:
        if candidate.exists() and candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    except OSError:
        return None
    return None

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
        llm = _router_api().get_chat_model()
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
        llm = _router_api().get_chat_model()
        generator = _router_api().create_bid_generator(llm)
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
            "status": "generating",
            "product_profiles": {},
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
        outbound_view = _build_external_delivery_view(
            sections,
            outbound_report,
            download_url=f"/api/tender/bid/download/{bid_id}?format=docx",
        )

        # 调用管道获取校验门和回归指标
        try:
            pipeline_result = generate_bid_sections(
                tender_doc, raw_text, llm,
                products=products,
                selected_packages=request.selected_packages,
            )
            validation_gate = pipeline_result.validation_gate
            regression_metrics = pipeline_result.regression_metrics
            draft_level_str = pipeline_result.draft_level.value
            doc_mode_str = pipeline_result.document_mode.value
        except Exception:
            validation_gate = None
            regression_metrics = None
            draft_level_str = ""
            doc_mode_str = ""

        # 6d: 外发门阻断
        if validation_gate is not None and not validation_gate.passes_external_gate():
            outbound_view = {
                **outbound_view,
                "status": "阻断外发",
                "generated": False,
                "section_titles": [],
            }

        stored_sections = _sections_for_storage_or_response(materialized_sections, sections, outbound_view)

        file_path = ""
        download_url = ""
        if stored_sections:
            output_file = BID_OUTPUT_DIR / f"{bid_id}.docx"
            build_bid_docx(stored_sections, tender_doc, company_profile, output_file)
            file_path = str(output_file)
            download_url = f"/api/tender/bid/download/{bid_id}?format=docx"
        outbound_view["file_path"] = file_path

        # 保存结果
        bid_storage[bid_id] = {
            "bid_id": bid_id,
            "tender_id": request.tender_id,
            "company_id": request.company_profile_id,
            "sections": [s.model_dump() for s in stored_sections],
            "generated_time": datetime.now(),
            "status": "generated",
            "file_path": file_path,
            "download_url": download_url,
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
            file_path=file_path,
            download_url=download_url,
            generated_time=datetime.now(),
            validation_gate=validation_gate,
            regression_metrics=regression_metrics,
            draft_level=draft_level_str,
            document_mode=doc_mode_str,
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
        download_url=str(bid_info.get("download_url", "") or outbound_report.get("download_url", "") or ""),
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
    if format == "docx":
        tender_info = tender_storage.get(bid_info["tender_id"])
        project_name = bid_id
        if tender_info and tender_info.get("parsed_data"):
            project_name = TenderDocument(**tender_info["parsed_data"]).project_name

        output_file = _existing_output_file(bid_info.get("file_path"))
        if output_file is None:
            output_file = _existing_output_file(BID_OUTPUT_DIR / f"{bid_id}.docx")

        if output_file is None:
            # 仅在成品文件缺失时兜底重建，避免每次下载都重新生成。
            company = company_storage.get(bid_info["company_id"])
            if not tender_info or not company:
                raise HTTPException(status_code=404, detail="关联的招标/企业信息不存在")

            sections = [BidDocumentSection(**s) for s in bid_info["sections"]]
            tender_doc = TenderDocument(**tender_info["parsed_data"])
            output_file = BID_OUTPUT_DIR / f"{bid_id}.docx"

            try:
                build_bid_docx(sections, tender_doc, company, output_file)
            except Exception as e:
                logger.error(f"Word文件生成失败: {str(e)}")
                raise HTTPException(status_code=500, detail=f"Word文件生成失败: {str(e)}")

            bid_info["file_path"] = str(output_file)
        else:
            bid_info["file_path"] = str(output_file)

        if isinstance(bid_info.get("outbound_report"), dict):
            bid_info["outbound_report"]["file_path"] = str(output_file)

        return FileResponse(
            output_file,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename=_safe_download_filename(f"投标文件_{project_name}", ".docx"),
        )

    elif format == "markdown":
        sections = render_editable_draft_sections(
            [BidDocumentSection(**s) for s in bid_info["sections"]]
        )
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
