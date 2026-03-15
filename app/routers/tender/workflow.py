from __future__ import annotations

from datetime import datetime
import uuid

from fastapi import HTTPException

from app.schemas import (
    BidDocumentSection,
    ProductSpecification,
    TenderDocument,
    TenderWorkflowRequest,
    TenderWorkflowResponse,
    TenderWorkflowStep1Result,
    TenderWorkflowStep2Result,
    TenderWorkflowStep3Result,
    TenderWorkflowStep4Result,
)
from app.services.docx_builder import build_bid_docx
from app.services.llm import get_chat_model
from app.services.retriever import ingest_text_to_kb
from app.services.tender_parser import create_tender_parser
from app.services.tender_workflow import (
    TenderWorkflowAgent,
    _build_document_ingestion_view,
    _build_internal_audit_snapshot,
    _build_package_segmentation_view,
    _ensure_str_list,
    _expand_extracted_facts,
    _extract_product_facts,
    _match_requirements_to_product_facts,
    _materialize_sections,
)
from app.routers.tender.common import (
    BID_OUTPUT_DIR,
    _PLACEHOLDER_COMPANY,
    _build_external_delivery_view,
    _is_external_delivery_blocked,
    _sections_for_storage_or_response,
    bid_storage,
    company_storage,
    logger,
    product_storage,
    router,
    tender_storage,
    workflow_kb_indexed_sources,
    workflow_storage,
)

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
        tender=tender_doc,
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
