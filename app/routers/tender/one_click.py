from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shutil
import uuid

from fastapi import BackgroundTasks, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.schemas import DraftLevel, OneClickJobStartResponse, OneClickJobStatusResponse
from app.services.docx_builder import build_bid_docx
from app.services.llm import get_chat_model
from app.services.one_click_generator import generate_bid_sections
from app.services.one_click_generator.common import BidSectionsValidationError
from app.services.tender_parser import create_tender_parser
from app.services.tender_workflow import _materialize_sections
from app.routers.tender.common import (
    BID_OUTPUT_DIR,
    UPLOAD_DIR,
    _PLACEHOLDER_COMPANY,
    _safe_download_filename,
    logger,
    one_click_job_storage,
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


def _run_one_click_generation(job_id: str, save_path: Path) -> None:
    """执行后台一键成稿流程并写出结果文件。"""
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
        gen_result = generate_bid_sections(
            tender_doc,
            raw_text,
            llm,
            require_validation_pass=True,
        )
        sections, _ = _materialize_sections(
            sections=gen_result.sections,
            tender=tender_doc,
            company=_PLACEHOLDER_COMPANY,
            products={},
            evidence_result=None,
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
            tender_doc,
            _PLACEHOLDER_COMPANY,
            output_file,
            draft_level=gen_result.draft_level,
        )
        if not output_file.exists() or output_file.stat().st_size == 0:
            raise RuntimeError("Word 文件生成失败，输出文件为空")

        filename = _safe_download_filename(f"{doc_label}_{tender_doc.project_name}", ".docx")
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


@router.post("/one-click", summary="一键生成底稿骨架")
async def one_click_generate(file: UploadFile = File(...)):
    """
    上传招标文件（PDF 或 Word），自动解析并生成可下载的底稿骨架 Word 文档。

    - 输入：招标文件（.pdf / .docx）
    - 输出：可补录的底稿骨架（.docx）
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
        gen_result = generate_bid_sections(
            tender_doc,
            raw_text,
            llm,
            require_validation_pass=True,
        )
        sections, _ = _materialize_sections(
            sections=gen_result.sections,
            tender=tender_doc,
            company=_PLACEHOLDER_COMPANY,
            products={},
            evidence_result=None,
        )

        # 5. 构建 Word 文档
        doc_label = _one_click_doc_label(gen_result.draft_level)
        output_file = _one_click_output_path(job_id, gen_result.draft_level)
        build_bid_docx(
            sections,
            tender_doc,
            _PLACEHOLDER_COMPANY,
            output_file,
            draft_level=gen_result.draft_level,
        )

        logger.info("投标文件生成成功：%s", output_file)

        # 6. 返回文件下载
        return FileResponse(
            output_file,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename=_safe_download_filename(f"{doc_label}_{tender_doc.project_name}", ".docx"),
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


@router.post("/one-click/start", response_model=OneClickJobStartResponse, summary="启动一键生成底稿任务")
async def start_one_click_generate(background_tasks: BackgroundTasks, file: UploadFile = File(...)):
    """异步启动一键成稿任务。"""
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
        for candidate_level in (job_info.get("draft_level"), DraftLevel.internal_draft.value, DraftLevel.external_ready.value):
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
