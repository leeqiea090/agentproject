from __future__ import annotations

from app.schemas import BidDocumentSection, BidEvidenceBinding, CommercialTerms, ProcurementPackage, TenderDocument
from app.services.chunking import split_to_blocks
from app.services.quality_gate import compute_regression_metrics, compute_validation_gate


def _sample_tender() -> TenderDocument:
    return TenderDocument(
        project_name="检验设备采购项目",
        project_number="XJ-2026-001",
        budget=1000000.0,
        purchaser="某医院",
        agency="某代理机构",
        procurement_type="公开招标",
        packages=[
            ProcurementPackage(
                package_id="1",
                item_name="流式细胞分析仪",
                quantity=1,
                budget=800000.0,
                technical_requirements={"激光器": "≥3"},
                delivery_time="合同签订后30日内",
                delivery_place="采购人指定地点",
            )
        ],
        commercial_terms=CommercialTerms(),
        evaluation_criteria={},
        special_requirements="",
    )


def test_split_to_blocks_separates_table_cells_and_marks_noise_rows() -> None:
    text = (
        "包1 全自动电泳仪\n"
        "第三章 技术要求\n"
        "1.1 核心参数\n"
        "| 参数项 | 要求 |\n"
        "| --- | --- |\n"
        "| 主机 | 1台 |\n"
        "| 说明 | 含安装 |\n"
        "交货期：合同签订后30日内。\n"
    )

    blocks = split_to_blocks(text)
    table_cells = [block for block in blocks if block.block_type == "table_cell"]

    assert table_cells
    assert any(
        block.text == "参数项"
        and block.package_id == "1"
        and block.section_title == "第三章 技术要求"
        and block.clause_no == "1.1"
        and block.table_id
        and block.row == 0
        and block.col == 0
        and block.is_noise
        for block in table_cells
    )
    assert any(
        block.text == "主机"
        and block.row == 1
        and block.col == 0
        and not block.is_noise
        and block.char_end > block.char_start
        for block in table_cells
    )
    assert any(
        block.text == "含安装"
        and block.row == 2
        and block.col == 1
        and block.is_noise
        for block in table_cells
    )
    assert not any(block.text == "| 主机 | 1台 |" for block in blocks)
    assert any(
        block.text == "交货期：合同签订后30日内。"
        and block.block_type == "paragraph"
        and block.char_end > block.char_start
        for block in blocks
    )


def test_validation_gate_flags_project_meta_anomaly() -> None:
    tender = _sample_tender()
    sections = [
        BidDocumentSection(
            section_title="第三章 技术部分",
            content="项目名称：检验设备采购项目\n包1 流式细胞分析仪\n技术参数：激光器≥3。",
            attachments=[],
        )
    ]

    gate = compute_validation_gate(
        sections=sections,
        target_package_ids=["1"],
        tender=tender,
    )

    assert gate.project_meta_anomaly_detected is True
    assert gate.passes_external_gate() is False
    assert "项目元信息异常" in gate.failure_reasons()


def test_regression_metrics_include_project_meta_consistency_score() -> None:
    tender = _sample_tender()
    sections = [
        BidDocumentSection(
            section_title="封面",
            content=(
                "项目名称：检验设备采购项目\n"
                "项目编号：XJ-2026-001\n"
                "| 1 | 流式细胞分析仪 | 1 | 满足 |\n"
            ),
            attachments=[],
        )
    ]

    metrics = compute_regression_metrics(
        sections=sections,
        target_package_ids=["1"],
        tender=tender,
    )

    assert metrics.project_meta_consistency_score == 1.0
    assert not any("项目元信息一致性不足" in warning for warning in metrics.quality_warnings)


def test_regression_metrics_warn_when_project_meta_is_inconsistent() -> None:
    tender = _sample_tender()
    sections = [
        BidDocumentSection(
            section_title="封面",
            content="项目名称：检验设备采购项目\n流式细胞分析仪\n",
            attachments=[],
        )
    ]

    metrics = compute_regression_metrics(
        sections=sections,
        target_package_ids=["1"],
        tender=tender,
    )

    assert metrics.project_meta_consistency_score < 0.8
    assert any("项目元信息一致性不足" in warning for warning in metrics.quality_warnings)


def test_bid_evidence_binding_reserves_evidence_alias_fields() -> None:
    binding = BidEvidenceBinding(
        package_id="1",
        requirement_id="pkg1-req-001",
        evidence_type="manual",
        file_name="产品说明书.pdf",
        file_page=12,
        snippet="主机参数满足要求",
        covers_requirement=True,
    )

    assert binding.evidence_file == "产品说明书.pdf"
    assert binding.evidence_page == 12
    assert binding.evidence_snippet == "主机参数满足要求"


def test_validation_gate_ignores_project_meta_when_counting_multi_package_forbidden_terms() -> None:
    tender = TenderDocument(
        project_name="手术用头架、X射线血液辐照设备(二次)",
        project_number="CS-2026-002",
        budget=1000000.0,
        purchaser="某医院",
        agency="某代理机构",
        procurement_type="竞争性磋商",
        packages=[
            ProcurementPackage(
                package_id="1",
                item_name="X射线血液辐照设备",
                quantity=1,
                budget=700000.0,
                technical_requirements={"功能": "满足"},
                delivery_time="合同签订后30日内",
                delivery_place="采购人指定地点",
            ),
            ProcurementPackage(
                package_id="2",
                item_name="手术用头架",
                quantity=3,
                budget=300000.0,
                technical_requirements={"功能": "满足"},
                delivery_time="合同签订后30日内",
                delivery_place="采购人指定地点",
            ),
        ],
        commercial_terms=CommercialTerms(),
        evaluation_criteria={},
        special_requirements="",
    )
    sections = [
        BidDocumentSection(
            section_title="附一、资格性审查响应对照表",
            content=(
                "### 包1：X射线血液辐照设备\n"
                "项目名称：手术用头架、X射线血液辐照设备(二次)\n"
                "交货地点：甲方指定地点\n"
                "响应文件对应内容：资格承诺函及相关证明材料。\n\n"
                "### 包2：手术用头架\n"
                "项目名称：手术用头架、X射线血液辐照设备(二次)\n"
                "交货地点：甲方指定地点\n"
                "响应文件对应内容：资格承诺函及相关证明材料。\n"
            ),
            attachments=[],
        )
    ]

    gate = compute_validation_gate(
        sections=sections,
        target_package_ids=["1", "2"],
        tender=tender,
    )

    assert gate.package_contamination_detected is False
