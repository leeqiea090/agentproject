from __future__ import annotations

import app.routers.tender.common as _common
import importlib


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
        llm = _router_api().get_chat_model()
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
        gen_result = generate_bid_sections(
            tender_doc,
            raw_text,
            llm,
            require_validation_pass=True,
        )
        sections = gen_result.sections
        _set_one_click_job_status(
            job_id,
            status="running",
            step_code="building",
            message="正在输出 Word 文档…",
            progress=90,
        )
        output_file = BID_OUTPUT_DIR / f"{job_id}_投标文件.docx"
        build_bid_docx(sections, tender_doc, _PLACEHOLDER_COMPANY, output_file)
        if not output_file.exists() or output_file.stat().st_size == 0:
            raise RuntimeError("Word 文件生成失败，输出文件为空")

        filename = _safe_download_filename(f"投标文件_{tender_doc.project_name}", ".docx")
        download_url = f"/api/tender/one-click/download/{job_id}"
        logger.info("一键生成任务完成: %s -> %s", job_id, output_file)
        is_external_ready = gen_result.validation_gate.passes_external_gate()
        _set_one_click_job_status(
            job_id,
            status="completed",
            step_code="completed",
            message=(
                "投标文件已生成完成，可直接下载。"
                if is_external_ready
                else "待补充底稿已生成，可下载后补充未完善信息。"
            ),
            progress=100,
            filename=filename,
            download_url=download_url,
        )
        one_click_job_storage[job_id]["validation_gate"] = gen_result.validation_gate.model_dump()
        one_click_job_storage[job_id]["regression_metrics"] = gen_result.regression_metrics.model_dump()
        one_click_job_storage[job_id]["draft_level"] = gen_result.draft_level.value
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
        llm = _router_api().get_chat_model()
        parser = create_tender_parser(llm)

        logger.info("开始提取文本：%s", save_path)
        raw_text = parser.extract_text(save_path)

        logger.info("开始解析招标结构")
        tender_doc = parser.parse_tender_document(save_path)

        # 4. 一键生成投标文件各章节（按固定模板生成，使用解析后的结构化数据）
        gen_result = generate_bid_sections(
            tender_doc,
            raw_text,
            llm,
            require_validation_pass=True,
        )
        sections = gen_result.sections

        # 5. 构建 Word 文档
        output_file = BID_OUTPUT_DIR / f"{job_id}_投标文件.docx"
        build_bid_docx(sections, tender_doc, _PLACEHOLDER_COMPANY, output_file)

        logger.info("投标文件生成成功：%s", output_file)

        # 6. 返回文件下载
        return FileResponse(
            output_file,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename=_safe_download_filename(f"投标文件_{tender_doc.project_name}", ".docx"),
        )

    except BidSectionsValidationError as exc:
        logger.warning("一键生成被硬校验阻断：%s", exc)
        raise HTTPException(status_code=409, detail=str(exc))
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
        filename=job_info.get("filename") or _safe_download_filename(f"投标文件_{job_id}", ".docx"),
    )
