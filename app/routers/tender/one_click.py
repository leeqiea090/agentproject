from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import shutil
from typing import Annotated
import uuid

from fastapi import BackgroundTasks, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.schemas import (
    BidDocumentSection,
    BidGenerationPreferences,
    DraftLevel,
    OneClickInteractiveGenerateRequest,
    OneClickJobStartResponse,
    OneClickJobStatusResponse,
    OneClickPackageOption,
    OneClickPrepareResponse,
    OneClickPromptRequest,
    OneClickPromptResponse,
    TenderDocument,
)
from app.services.bid_preferences import (
    apply_generation_preferences,
    normalize_generation_preferences,
    ordered_section_titles,
)
from app.services.docx_builder import build_bid_docx
from app.services.interactive_fill import (
    apply_interactive_answers,
    build_company_from_answers,
    plan_interactive_fill,
    serialize_interactive_prompts,
)
from app.services.llm import get_chat_model
from app.services.one_click_generator import generate_bid_sections
from app.services.one_click_generator.common import BidSectionsValidationError
from app.services.quality_gate import render_editable_draft_sections
from app.services.tender_parser import create_tender_parser
from app.services.tender_workflow import _materialize_sections
from app.routers.tender.common import (
    BID_OUTPUT_DIR,
    UPLOAD_DIR,
    _PLACEHOLDER_COMPANY,
    _safe_download_filename,
    logger,
    one_click_job_storage,
    one_click_session_storage,
    router,
)


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


def _one_click_doc_label(draft_level: DraftLevel | str | None) -> str:
    """生成一键成稿文档的展示标签。"""
    level = draft_level.value if isinstance(draft_level, DraftLevel) else str(draft_level or "").strip()
    return "投标底稿" if level == DraftLevel.internal_draft.value else "投标文件"


def _one_click_output_path(job_id: str, draft_level: DraftLevel | str | None):
    """计算一键成稿结果文件的输出路径。"""
    return BID_OUTPUT_DIR / f"{job_id}_{_one_click_doc_label(draft_level)}.docx"


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
    """更新一键成稿任务的状态记录。"""
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


def _load_tender(session_data: dict) -> TenderDocument:
    return TenderDocument(**session_data["tender_doc"])


def _resolve_package(tender_doc: TenderDocument, package_id: str | None):
    if not package_id:
        return None
    normalized = str(package_id).strip()
    for pkg in tender_doc.packages:
        if pkg.package_id == normalized:
            return pkg
    raise HTTPException(status_code=400, detail=f"包号 '{normalized}' 不存在于当前招标文件中")


def _single_package_tender(tender_doc: TenderDocument, package_id: str | None) -> TenderDocument:
    pkg = _resolve_package(tender_doc, package_id)
    if pkg is None:
        return tender_doc
    return tender_doc.model_copy(update={"packages": [pkg]})


def _selected_packages_arg(package_id: str | None) -> list[str] | None:
    normalized = str(package_id or "").strip()
    return [normalized] if normalized else None


def _package_options(tender_doc: TenderDocument) -> list[OneClickPackageOption]:
    return [
        OneClickPackageOption(
            package_id=pkg.package_id,
            item_name=pkg.item_name,
            quantity=pkg.quantity,
            budget=pkg.budget,
        )
        for pkg in tender_doc.packages
    ]


def _download_filename(
    doc_label: str,
    tender_doc: TenderDocument,
    package_id: str | None = None,
) -> str:
    pkg = _resolve_package(tender_doc, package_id) if package_id else None
    if pkg is None:
        stem = f"{doc_label}_{tender_doc.project_name}"
    else:
        stem = f"{doc_label}_包{pkg.package_id}_{pkg.item_name}_{tender_doc.project_name}"
    return _safe_download_filename(stem, ".docx")


def _build_interactive_preview(
    tender_doc: TenderDocument,
    raw_text: str,
    package_id: str,
    api_key: str | None = None,
) -> dict:
    package = _resolve_package(tender_doc, package_id)
    llm = get_chat_model(api_key=api_key)
    _, gen_result, sections = _generate_one_click_sections(
        tender_doc,
        raw_text,
        selected_package=package.package_id,
        llm=llm,
    )
    interactive_sections = render_editable_draft_sections(sections, add_draft_watermark=False)
    interactive_plan = plan_interactive_fill(interactive_sections, llm=llm)
    interactive_sections = interactive_plan["sections"]
    manual_placeholders = interactive_plan["manual_items"]
    prompts = interactive_plan["prompts"]

    return {
        "package_id": package.package_id,
        "item_name": package.item_name,
        "sections": [section.model_dump() for section in interactive_sections],
        "section_titles": [section.section_title for section in interactive_sections],
        "prompts": prompts,
        "manual_placeholder_count": sum(int(item.get("count", 0)) for item in manual_placeholders),
        "manual_placeholder_examples": [str(item.get("label") or "") for item in manual_placeholders[:6]],
        "validation_gate": gen_result.validation_gate.model_dump(),
        "regression_metrics": gen_result.regression_metrics.model_dump(),
        "draft_level": gen_result.draft_level.value,
    }


def _ensure_session_preview(session_data: dict, package_id: str, api_key: str | None = None) -> dict:
    package_cache = session_data.setdefault("package_cache", {})
    if package_id not in package_cache:
        tender_doc = _load_tender(session_data)
        package_cache[package_id] = _build_interactive_preview(
            tender_doc=tender_doc,
            raw_text=str(session_data.get("raw_text") or ""),
            package_id=package_id,
            api_key=api_key,
        )
    return package_cache[package_id]


def _normalize_request_api_key(api_key: str | None) -> str | None:
    value = str(api_key or "").strip()
    return value or None


def _parse_generation_preferences_json(raw: str | None) -> BidGenerationPreferences | None:
    """解析表单传入的生成偏好 JSON。"""
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
        return normalize_generation_preferences(payload)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"生成偏好参数不合法：{exc}")


def _generate_one_click_sections(
    tender_doc: TenderDocument,
    raw_text: str,
    *,
    selected_package: str | None,
    llm,
) -> tuple[TenderDocument, object, list[BidDocumentSection]]:
    """生成单包 one-click 章节并完成基础实装。"""
    scoped_tender = _single_package_tender(tender_doc, selected_package)
    gen_result = generate_bid_sections(
        tender_doc,
        raw_text,
        llm,
        products={},
        selected_packages=_selected_packages_arg(selected_package),
    )
    sections, _ = _materialize_sections(
        sections=gen_result.sections,
        tender=scoped_tender,
        company=_PLACEHOLDER_COMPANY,
        products={},
        product_profiles=gen_result.product_profiles,
    )
    return scoped_tender, gen_result, sections


def _run_one_click_generation(
    job_id: str,
    save_path: Path,
    selected_package: str | None = None,
    api_key: str | None = None,
    generation_preferences: BidGenerationPreferences | None = None,
) -> None:
    """执行后台一键成稿流程并写出结果文件。"""
    try:
        _set_one_click_job_status(
            job_id,
            status="running",
            step_code="extracting",
            message="正在解析文档内容…",
            progress=12,
        )
        llm = get_chat_model(api_key=api_key)
        parser = create_tender_parser(llm)
        # raw_text = parser.extract_text(save_path)

        _set_one_click_job_status(
            job_id,
            status="running",
            step_code="parsing",
            message="正在抽取招标需求…",
            progress=32,
        )
        raw_text = parser.extract_text(save_path)
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
        scoped_tender, gen_result, sections = _generate_one_click_sections(
            tender_doc,
            raw_text,
            selected_package=selected_package,
            llm=llm,
        )
        sections = apply_generation_preferences(
            sections,
            generation_preferences,
            llm=llm,
        )
        _set_one_click_job_status(
            job_id,
            status="running",
            step_code="building",
            message="正在输出 Word 文档…",
            progress=90,
        )
        doc_label = _one_click_doc_label(gen_result.draft_level)
        output_file = _one_click_output_path(job_id, gen_result.draft_level)
        build_bid_docx(
            sections,
            scoped_tender,
            _PLACEHOLDER_COMPANY,
            output_file,
            draft_level=gen_result.draft_level,
            generation_preferences=generation_preferences,
        )
        if not output_file.exists() or output_file.stat().st_size == 0:
            raise RuntimeError("Word 文件生成失败，输出文件为空")

        filename = _download_filename(doc_label, scoped_tender, selected_package)
        download_url = f"/api/tender/one-click/download/{job_id}"
        logger.info("一键生成任务完成: %s -> %s", job_id, output_file)
        is_external_ready = gen_result.validation_gate.passes_external_gate()
        _set_one_click_job_status(
            job_id,
            status="completed",
            step_code="completed",
            message=(
                f"{doc_label}已生成完成，可直接下载。"
                if is_external_ready
                else "投标底稿已生成，可下载补录；如需成熟响应稿，请改走 workflow 并上传产品/证据资料。"
            ),
            progress=100,
            filename=filename,
            download_url=download_url,
        )
        one_click_job_storage[job_id]["validation_gate"] = gen_result.validation_gate.model_dump()
        one_click_job_storage[job_id]["regression_metrics"] = gen_result.regression_metrics.model_dump()
        one_click_job_storage[job_id]["draft_level"] = gen_result.draft_level.value
        one_click_job_storage[job_id]["output_file"] = str(output_file)
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


@router.post("/one-click/prepare", response_model=OneClickPrepareResponse, summary="上传招标文件并准备交互式生成")
async def prepare_one_click_generation(
    file: UploadFile = File(...),
    x_llm_api_key: Annotated[str | None, Header()] = None,
):
    """解析招标文件，返回包件列表，供前端做单包交互生成。"""
    filename = file.filename or ""
    suffix = Path(filename).suffix.lower()
    if suffix not in _ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式 '{suffix}'，请上传 PDF 或 Word（.docx）文件",
        )

    temp_id = str(uuid.uuid4())
    save_path = UPLOAD_DIR / f"{temp_id}{suffix}"
    try:
        with save_path.open("wb") as buf:
            shutil.copyfileobj(file.file, buf)
    except Exception as exc:
        logger.error("交互式准备阶段文件保存失败: %s", exc)
        raise HTTPException(status_code=500, detail=f"文件保存失败: {exc}")

    try:
        llm = get_chat_model(api_key=_normalize_request_api_key(x_llm_api_key))
        parser = create_tender_parser(llm)
        raw_text = parser.extract_text(save_path)
        tender_doc = parser.parse_tender_document(save_path)
    except Exception as exc:
        logger.error("交互式准备阶段解析失败: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"解析失败：{exc}")
    finally:
        save_path.unlink(missing_ok=True)

    session_id = str(uuid.uuid4())
    one_click_session_storage[session_id] = {
        "session_id": session_id,
        "original_filename": filename,
        "created_time": datetime.now(),
        "tender_doc": tender_doc.model_dump(),
        "raw_text": raw_text,
        "package_cache": {},
    }

    packages = _package_options(tender_doc)
    default_package_id = packages[0].package_id if packages else ""
    return OneClickPrepareResponse(
        session_id=session_id,
        project_name=tender_doc.project_name,
        project_number=tender_doc.project_number,
        packages=packages,
        default_package_id=default_package_id,
    )


@router.post("/one-click/prompts", response_model=OneClickPromptResponse, summary="生成单包交互式待填写项")
async def get_one_click_prompts(
    req: OneClickPromptRequest,
    x_llm_api_key: Annotated[str | None, Header()] = None,
):
    """仅针对选中的一个包生成待填写项，重复字段只返回一次。"""
    session_data = one_click_session_storage.get(req.session_id)
    if not session_data:
        raise HTTPException(status_code=404, detail="交互式会话不存在或已失效")

    tender_doc = _load_tender(session_data)
    scoped_tender = _single_package_tender(tender_doc, req.package_id)
    try:
        preview_data = _ensure_session_preview(
            session_data,
            req.package_id,
            api_key=_normalize_request_api_key(x_llm_api_key),
        )
    except BidSectionsValidationError as exc:
        logger.warning("交互式待填写项生成被硬校验阻断：%s", exc)
        raise HTTPException(status_code=409, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("交互式待填写项生成失败：%s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"生成待填写项失败：{exc}")
    prompts = serialize_interactive_prompts(preview_data["prompts"])
    preferences = normalize_generation_preferences(req.generation_preferences)
    preview_sections = [BidDocumentSection(**item) for item in preview_data.get("sections", [])]
    package = scoped_tender.packages[0] if scoped_tender.packages else None

    return OneClickPromptResponse(
        session_id=req.session_id,
        package_id=req.package_id,
        project_name=scoped_tender.project_name,
        project_number=scoped_tender.project_number,
        item_name=package.item_name if package else "",
        prompt_count=len(prompts),
        manual_placeholder_count=int(preview_data.get("manual_placeholder_count") or 0),
        manual_placeholder_examples=list(preview_data.get("manual_placeholder_examples", [])),
        section_titles=ordered_section_titles(preview_sections, preferences),
        prompts=prompts,
        draft_level=str(preview_data.get("draft_level") or ""),
        validation_gate=preview_data.get("validation_gate"),
        regression_metrics=preview_data.get("regression_metrics"),
    )


@router.post("/one-click/generate-interactive", summary="按交互式答案生成单包文档")
async def generate_interactive_one_click(
    req: OneClickInteractiveGenerateRequest,
    x_llm_api_key: Annotated[str | None, Header()] = None,
):
    """将前端收集到的答案回填到单包底稿中并输出 Word。"""
    session_data = one_click_session_storage.get(req.session_id)
    if not session_data:
        raise HTTPException(status_code=404, detail="交互式会话不存在或已失效")

    tender_doc = _load_tender(session_data)
    scoped_tender = _single_package_tender(tender_doc, req.package_id)
    try:
        preview_data = _ensure_session_preview(
            session_data,
            req.package_id,
            api_key=_normalize_request_api_key(x_llm_api_key),
        )
    except BidSectionsValidationError as exc:
        logger.warning("交互式单包文档生成被硬校验阻断：%s", exc)
        raise HTTPException(status_code=409, detail=str(exc))
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("交互式单包预生成失败：%s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"生成单包预览失败：{exc}")
    sections = [BidDocumentSection(**item) for item in preview_data.get("sections", [])]
    if not sections:
        raise HTTPException(status_code=409, detail="当前包尚未生成待填写项，请先请求 prompts")

    answers = {str(key).strip(): str(value).strip() for key, value in req.answers.items() if str(value).strip()}
    final_sections = apply_interactive_answers(sections, preview_data.get("prompts", []), answers)
    preferences = normalize_generation_preferences(req.generation_preferences)
    llm = None
    if preferences is not None and (
        preferences.language_style.value != "standard"
        or preferences.custom_language_instruction
    ):
        llm = get_chat_model(api_key=_normalize_request_api_key(x_llm_api_key))
    final_sections = apply_generation_preferences(
        final_sections,
        preferences,
        llm=llm,
    )
    company = build_company_from_answers(answers)
    draft_level = preview_data.get("draft_level") or DraftLevel.internal_draft.value
    output_id = str(uuid.uuid4())
    output_file = _one_click_output_path(output_id, draft_level)

    try:
        build_bid_docx(
            final_sections,
            scoped_tender,
            company,
            output_file,
            draft_level=draft_level,
            generation_preferences=preferences,
        )
    except Exception as exc:
        logger.error("交互式单包文档生成失败：%s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"生成失败：{exc}")

    if not output_file.exists() or output_file.stat().st_size == 0:
        raise HTTPException(status_code=500, detail="Word 文件生成失败，输出文件为空")

    doc_label = _one_click_doc_label(draft_level)
    return FileResponse(
        output_file,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=_download_filename(doc_label, scoped_tender, req.package_id),
    )


@router.post("/one-click", summary="一键生成底稿骨架")
async def one_click_generate(
    file: UploadFile = File(...),
    selected_package: str | None = Form(default=None),
    generation_preferences_json: str | None = Form(default=None),
    x_llm_api_key: Annotated[str | None, Header()] = None,
):
    """
    上传招标文件（PDF 或 Word），自动解析并生成可下载的底稿骨架 Word 文档。

    - 输入：招标文件（.pdf / .docx）
    - 输出：可补录的底稿骨架（.docx）
    - 可选：`selected_package` 指定只生成单个包
    """
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
        logger.error("文件保存失败: %s", exc)
        raise HTTPException(status_code=500, detail=f"文件保存失败: {exc}")

    generation_preferences = _parse_generation_preferences_json(generation_preferences_json)
    try:
        llm = get_chat_model(api_key=_normalize_request_api_key(x_llm_api_key))
        parser = create_tender_parser(llm)

        logger.info("开始提取文本：%s", save_path)
        raw_text = parser.extract_text(save_path)

        logger.info("开始解析招标结构")
        tender_doc = parser.parse_tender_document(save_path)
        scoped_tender, gen_result, sections = _generate_one_click_sections(
            tender_doc,
            raw_text,
            selected_package=selected_package,
            llm=llm,
        )
        sections = apply_generation_preferences(
            sections,
            generation_preferences,
            llm=llm,
        )

        doc_label = _one_click_doc_label(gen_result.draft_level)
        output_file = _one_click_output_path(job_id, gen_result.draft_level)
        build_bid_docx(
            sections,
            scoped_tender,
            _PLACEHOLDER_COMPANY,
            output_file,
            draft_level=gen_result.draft_level,
            generation_preferences=generation_preferences,
        )

        logger.info("投标文件生成成功：%s", output_file)
        return FileResponse(
            output_file,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename=_download_filename(doc_label, scoped_tender, selected_package),
        )

    except BidSectionsValidationError as exc:
        logger.warning("一键生成被硬校验阻断：%s", exc)
        raise HTTPException(status_code=409, detail=str(exc))
    except Exception as exc:
        logger.error("一键生成失败：%s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail=f"生成失败：{exc}")
    finally:
        save_path.unlink(missing_ok=True)


@router.post("/one-click/start", response_model=OneClickJobStartResponse, summary="启动一键生成底稿任务")
async def start_one_click_generate(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    selected_package: str | None = Form(default=None),
    generation_preferences_json: str | None = Form(default=None),
    x_llm_api_key: Annotated[str | None, Header()] = None,
):
    """异步启动一键成稿任务，可选只生成指定包。"""
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
    generation_preferences = _parse_generation_preferences_json(generation_preferences_json)
    background_tasks.add_task(
        _run_one_click_generation,
        job_id,
        save_path,
        selected_package,
        _normalize_request_api_key(x_llm_api_key),
        generation_preferences,
    )
    return OneClickJobStartResponse(**one_click_job_storage[job_id])


@router.get("/one-click/status/{job_id}", response_model=OneClickJobStatusResponse, summary="查询一键生成任务状态")
async def get_one_click_job_status(job_id: str):
    """查询一键成稿任务状态。"""
    job_info = one_click_job_storage.get(job_id)
    if not job_info:
        raise HTTPException(status_code=404, detail="任务不存在")
    return OneClickJobStatusResponse(**job_info)


@router.get("/one-click/download/{job_id}", summary="下载一键生成结果")
async def download_one_click_result(job_id: str):
    """下载一键成稿生成的文件。"""
    job_info = one_click_job_storage.get(job_id)
    if not job_info:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job_info.get("status") != "completed":
        raise HTTPException(status_code=409, detail="任务尚未完成")

    output_file_str = str(job_info.get("output_file") or "").strip()
    output_file = Path(output_file_str) if output_file_str else None
    if output_file is None or not output_file.exists():
        for candidate_level in (
            job_info.get("draft_level"),
            DraftLevel.internal_draft.value,
            DraftLevel.external_ready.value,
        ):
            candidate = _one_click_output_path(job_id, candidate_level)
            if candidate.exists():
                output_file = candidate
                break
    if output_file is None or not output_file.exists():
        raise HTTPException(status_code=404, detail="生成文件不存在")

    return FileResponse(
        output_file,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=job_info.get("filename") or _safe_download_filename(f"投标文件_{job_id}", ".docx"),
    )
