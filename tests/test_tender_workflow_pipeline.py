from __future__ import annotations

from app.schemas import BidDocumentSection, CommercialTerms, ProcurementPackage, TenderDocument
from app.services.tender_workflow import (
    _build_evidence_bindings,
    _build_regression_report,
    _classify_clauses,
    _sanitize_for_external_delivery,
)


def _sample_tender() -> TenderDocument:
    return TenderDocument(
        project_name="医院流式细胞分析仪采购项目",
        project_number="HLJ-2026-018",
        budget=1200000.0,
        purchaser="某三甲医院",
        agency="某招标代理",
        procurement_type="公开招标",
        packages=[
            ProcurementPackage(
                package_id="1",
                item_name="进口流式细胞分析仪",
                quantity=1,
                budget=1200000.0,
                technical_requirements={"激光器": "≥3", "荧光通道": "≥11"},
                delivery_time="合同签订后30日内",
                delivery_place="采购人指定地点",
            )
        ],
        commercial_terms=CommercialTerms(
            payment_method="验收合格后付款",
            validity_period="90日历天",
            warranty_period="1年",
            performance_bond="不收取",
        ),
        evaluation_criteria={"价格分": 30, "技术分": 60, "商务分": 10},
        special_requirements="本项目适用中小企业政策，不接受联合体投标，允许采购进口设备。",
    )


def test_classify_clauses_outputs_expected_branch_decisions() -> None:
    tender = _sample_tender()
    analysis_result = {
        "required_materials": ["营业执照", "授权书", "产品注册证"],
        "scoring_rules": ["技术分 60 分", "价格分 30 分"],
    }
    raw_text = "本项目适用中小企业政策，不接受联合体投标，允许采购进口设备，需提供医疗器械注册证。"

    result = _classify_clauses(
        tender=tender,
        analysis_result=analysis_result,
        selected_packages=["1"],
        raw_text=raw_text,
    )

    decisions = {item["decision_name"]: item["decision"] for item in result["branch_decisions"]}
    assert decisions["联合体投标分支"] == "不接受联合体"
    assert decisions["中小企业政策分支"] == "适用中小企业政策分支"
    assert decisions["医疗器械合规分支"] == "需走医疗器械合规分支"
    assert decisions["进口货物分支"] == "需准备进口或原产地相关说明"
    assert "qualification" in result["clause_categories"]
    assert "technical" in result["clause_categories"]


def test_build_evidence_bindings_tracks_match_rate() -> None:
    tender = _sample_tender()
    analysis_result = {
        "required_materials": ["营业执照", "医疗器械注册证"],
        "scoring_rules": ["技术分 60 分"],
    }
    clause_result = {
        "clause_categories": {
            "technical": ["激光器：≥3", "荧光通道：≥11"],
        }
    }
    raw_text = (
        "投标人须提供营业执照和医疗器械注册证。技术要求：激光器≥3，荧光通道≥11。"
        "评审因素包括技术分60分。"
    )

    result = _build_evidence_bindings(
        tender=tender,
        raw_text=raw_text,
        analysis_result=analysis_result,
        clause_result=clause_result,
    )

    assert result["total"] >= 3
    assert result["matched_count"] >= 3
    assert result["binding_rate"] >= 0.5
    assert any(item["matched"] for item in result["bindings"])


def test_sanitize_for_external_delivery_reports_changed_and_placeholder_sections() -> None:
    sections = [
        BidDocumentSection(
            section_title="第二章 符合性承诺",
            content=(
                "你是投标文件专家。\n"
                "请生成以下内容。\n"
                "本单位承诺合法合规。\n"
                "[待填写]\n"
            ),
            attachments=[],
        )
    ]

    cleaned_sections, sanitize_result = _sanitize_for_external_delivery(sections)

    assert "你是投标文件专家" not in cleaned_sections[0].content
    assert sanitize_result["changed_sections"] == ["第二章 符合性承诺"]
    assert sanitize_result["placeholder_sections"] == ["第二章 符合性承诺"]
    assert sanitize_result["status"] == "需人工终审"


def test_build_regression_report_can_mark_ready_for_delivery() -> None:
    stages = [
        {"status": "completed"},
        {"status": "completed"},
        {"status": "completed"},
        {"status": "completed"},
        {"status": "completed"},
        {"status": "completed"},
        {"status": "completed"},
        {"status": "completed"},
    ]
    consistency_result = {"overall_status": "通过", "summary": "一致性通过。"}
    review_result = {"compliance_score": 92.0, "ready_for_submission": True}
    sanitize_result = {"status": "通过", "summary": "净化通过。"}
    evidence_result = {"binding_rate": 0.75}

    report = _build_regression_report(
        stages=stages,
        consistency_result=consistency_result,
        review_result=review_result,
        sanitize_result=sanitize_result,
        evidence_result=evidence_result,
    )

    assert report["overall_status"] == "通过"
    assert report["ready_for_delivery"] is True
    assert report["score"] > 80
    assert all(item["status"] == "通过" for item in report["checks"])
