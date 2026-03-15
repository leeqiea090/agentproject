from __future__ import annotations

from app.schemas import (
    BidDocumentSection,
    CommercialTerms,
    CompanyLicense,
    CompanyProfile,
    ProcurementPackage,
    ProductSpecification,
    TenderDocument,
)
from app.services.tender_workflow import (
    _build_evidence_bindings,
    _build_regression_report,
    _classify_clauses,
    _decide_rule_branches,
    _extract_product_facts,
    _materialize_sections,
    _match_requirements_to_product_facts,
    _normalize_requirements,
    _sanitize_for_external_delivery,
)


def _sample_tender() -> TenderDocument:
    """构造测试用的招标文档样例。"""
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
    """测试classify条款输出expectedbranchdecisions。"""
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
    assert "procurement_requirements" in result["structured_categories"]
    assert "evidence_materials" in result["structured_categories"]


def test_build_evidence_bindings_tracks_match_rate() -> None:
    """测试build证据绑定tracks匹配rate。"""
    tender = _sample_tender()
    analysis_result = {
        "required_materials": ["营业执照", "医疗器械注册证"],
        "scoring_rules": ["技术分 60 分"],
    }
    raw_text = (
        "投标人须提供营业执照和医疗器械注册证。技术要求：激光器≥3，荧光通道≥11。"
        "评审因素包括技术分60分。"
    )
    clause_result = _classify_clauses(
        tender=tender,
        analysis_result=analysis_result,
        selected_packages=["1"],
        raw_text=raw_text,
    )
    normalized_result = _normalize_requirements(
        tender=tender,
        analysis_result=analysis_result,
        clause_result=clause_result,
        selected_packages=["1"],
        raw_text=raw_text,
    )
    product = ProductSpecification(
        product_id="p1",
        product_name="流式细胞分析仪",
        manufacturer="某厂家",
        model="FC5000",
        origin="美国",
        specifications={"激光器": "3个", "荧光通道": "12个"},
        price=1000000.0,
        registration_number="国械注进20260001",
        authorization_letter="data/product/auth.pdf",
    )
    product_fact_result = _extract_product_facts(
        tender=tender,
        products={"1": product},
        selected_packages=["1"],
    )

    result = _build_evidence_bindings(
        tender=tender,
        raw_text=raw_text,
        company=None,
        products={"1": product},
        selected_packages=["1"],
        normalized_result=normalized_result,
        product_fact_result=product_fact_result,
    )

    assert result["total"] >= 3
    assert result["matched_count"] >= 3
    assert result["binding_rate"] >= 0.5
    assert result["proven_completion_rate"] >= 1.0
    assert result["technical_matches"]
    assert any(item["matched"] for item in result["bindings"])


def test_extract_product_facts_and_match_requirements() -> None:
    """测试extract产品事实and匹配需求。"""
    tender = _sample_tender()
    analysis_result = {
        "required_materials": ["营业执照", "医疗器械注册证"],
        "scoring_rules": ["技术分 60 分"],
    }
    clause_result = _classify_clauses(
        tender=tender,
        analysis_result=analysis_result,
        selected_packages=["1"],
        raw_text="激光器≥3，荧光通道≥11。",
    )
    normalized_result = _normalize_requirements(
        tender=tender,
        analysis_result=analysis_result,
        clause_result=clause_result,
        selected_packages=["1"],
        raw_text="激光器≥3，荧光通道≥11。",
    )
    company = CompanyProfile(
        company_id="c1",
        name="测试医疗科技有限公司",
        legal_representative="张三",
        address="长春市高新区示范路1号",
        phone="13800000000",
    )
    product = ProductSpecification(
        product_id="p1",
        product_name="流式细胞分析仪",
        manufacturer="某厂家",
        model="FC5000",
        origin="美国",
        specifications={"激光器": "3个独立激光器", "荧光通道": "12个"},
        price=1000000.0,
        registration_number="国械注进20260001",
        authorization_letter="data/product/auth.pdf",
    )

    product_fact_result = _extract_product_facts(
        tender=tender,
        products={"1": product},
        selected_packages=["1"],
    )
    match_result = _match_requirements_to_product_facts(
        normalized_result=normalized_result,
        product_fact_result=product_fact_result,
        company=company,
        products={"1": product},
    )

    assert product_fact_result["fact_count"] >= 6
    assert match_result["match_count"] == 2
    assert match_result["proven_count"] == 2
    assert match_result["compliant_count"] == 2
    assert match_result["proven_completion_rate"] == 1.0
    assert all(item["deviation_status"] == "无偏离" for item in match_result["technical_matches"])


def test_match_requirements_to_identity_offered_facts() -> None:
    """测试匹配需求toidentityoffered事实。"""
    tender = _sample_tender()
    tender.packages[0].technical_requirements = {"原产地": "美国", "生产厂家": "某厂家"}
    analysis_result = {
        "required_materials": ["营业执照"],
        "scoring_rules": ["技术分 60 分"],
    }
    raw_text = "技术要求：原产地为美国，生产厂家为某厂家。"
    clause_result = _classify_clauses(
        tender=tender,
        analysis_result=analysis_result,
        selected_packages=["1"],
        raw_text=raw_text,
    )
    normalized_result = _normalize_requirements(
        tender=tender,
        analysis_result=analysis_result,
        clause_result=clause_result,
        selected_packages=["1"],
        raw_text=raw_text,
    )
    product = ProductSpecification(
        product_id="p-origin",
        product_name="流式细胞分析仪",
        manufacturer="某厂家",
        model="FC5000",
        origin="美国",
        specifications={},
        price=1000000.0,
    )

    product_fact_result = _extract_product_facts(
        tender=tender,
        products={"1": product},
        selected_packages=["1"],
    )
    match_result = _match_requirements_to_product_facts(
        normalized_result=normalized_result,
        product_fact_result=product_fact_result,
        company=None,
        products={"1": product},
    )

    assert product_fact_result["offered_fact_count"] >= 5
    assert match_result["match_count"] == 2
    assert match_result["proven_count"] == 2
    assert {item["parameter_name"] for item in match_result["technical_matches"]} == {"原产地", "生产厂家"}
    assert all(item["bidder_evidence_bound"] for item in match_result["technical_matches"])


def test_sanitize_for_external_delivery_reports_changed_and_placeholder_sections() -> None:
    """测试外发reportschangedand占位符章节的sanitize。"""
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
    # [待填写] is a critical placeholder → hard block external delivery
    assert sanitize_result["status"] == "阻断外发"
    assert any("关键占位符" in reason for reason in sanitize_result["blocked_reasons"])


def test_sanitize_for_external_delivery_blocks_when_completion_is_insufficient() -> None:
    """测试外发文本块whencompletionisinsufficient的sanitize。"""
    sections = [
        BidDocumentSection(
            section_title="第三章 商务及技术部分",
            content="技术条款证据映射表\n| 1 | 激光器 | ≥3 | 待核实 | 待核实 | 未补证 |",
            attachments=[],
        )
    ]

    _, sanitize_result = _sanitize_for_external_delivery(
        sections,
        hard_validation_result={"overall_status": "需修订"},
        evidence_result={"proven_completion_rate": 0.2},
    )

    assert sanitize_result["status"] == "阻断外发"
    assert "硬校验未通过" in sanitize_result["blocked_reasons"]


def test_sanitize_for_external_delivery_blocks_when_config_table_is_too_thin() -> None:
    """测试外发文本块when配置表格istoothin的sanitize。"""
    sections = [
        BidDocumentSection(
            section_title="第三章 商务及技术部分",
            content=(
                "### 包1：进口流式细胞分析仪\n"
                "### （二-A）详细配置明细表\n"
                "| 序号 | 配置名称 | 单位 | 数量 | 是否标配 | 用途说明 | 备注 |\n"
                "|---:|---|---|---:|---|---|---|\n"
                "| 1 | 主机 | 台 | 1 | 是 | 核心设备主机 | 核心设备 |\n"
                "| 2 | 说明书 | 份 | 1 | 是 | 操作指导 | 随机文件 |\n"
                "### （五）关键性能说明\n"
                "已补充关键性能说明。\n"
            ),
            attachments=[],
        )
    ]

    _, sanitize_result = _sanitize_for_external_delivery(sections)

    assert sanitize_result["status"] == "阻断外发"
    assert any("配置项仅2项" in reason for reason in sanitize_result["blocked_reasons"])


def test_build_regression_report_can_mark_ready_for_delivery() -> None:
    """测试交付的build回归报告canmarkready。"""
    stages = [
        {"status": "completed"},
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
    evidence_result = {
        "binding_rate": 0.75,
        "bidder_binding_rate": 0.75,
        "match_rate": 0.75,
        "proven_completion_rate": 1.0,
    }

    report = _build_regression_report(
        stages=stages,
        consistency_result=consistency_result,
        review_result=review_result,
        sanitize_result=sanitize_result,
        evidence_result=evidence_result,
    )

    assert report["overall_status"] == "通过"
    assert report["ready_for_delivery"] is True
    assert report["score"] > 50
    # Original 8 checks should all pass
    original_checks = [item for item in report["checks"] if item["name"] not in {
        "package_isolation_score", "single_package_focus_score",
        "atomic_requirement_rate", "offered_fact_coverage",
        "bid_evidence_coverage", "config_pollution_rate", "package_contamination_rate",
        "external_block_rate",
        "config_detail_score", "mapping_count_consistency",
        "section_template_similarity", "placeholder_leakage",
        "detail_target_atomic_clauses", "detail_target_deviation_rows",
        "detail_target_narrative_chars", "detail_target_evidence_coverage",
        "actual_param_coverage", "bid_evidence_page_coverage",
        "config_avg_items_per_package", "template_paragraph_ratio",
        "external_hardgate_block_rate",
        "fact_density_per_page", "table_category_mixing_rate",
    }]
    assert all(item["status"] == "通过" for item in original_checks)
    # New metrics should exist
    new_metric_names = {item["name"] for item in report["checks"]} - {item["name"] for item in original_checks}
    assert "single_package_focus_score" in new_metric_names
    assert "atomic_requirement_rate" in new_metric_names
    assert "offered_fact_coverage" in new_metric_names
    assert "bid_evidence_coverage" in new_metric_names
    assert "package_contamination_rate" in new_metric_names
    assert "config_detail_score" in new_metric_names
    assert "mapping_count_consistency" in new_metric_names
    assert "placeholder_leakage" in new_metric_names
    assert "external_block_rate" in new_metric_names
    assert "fact_density_per_page" in new_metric_names
    assert "table_category_mixing_rate" in new_metric_names


def test_build_evidence_bindings_exposes_bid_side_pages() -> None:
    """测试build证据绑定exposes投标sidepages。"""
    tender = _sample_tender()
    product = ProductSpecification(
        product_id="p1",
        product_name="流式细胞分析仪",
        manufacturer="某厂家",
        model="FC5000",
        origin="美国",
        specifications={"激光器": "3个独立激光器"},
        price=1000000.0,
        evidence_refs=[{"description": "激光器", "page": 6, "file_name": "彩页-激光器.pdf"}],
    )
    analysis_result = {
        "required_materials": [],
        "scoring_rules": [],
    }
    clause_result = _classify_clauses(
        tender=tender,
        analysis_result=analysis_result,
        selected_packages=["1"],
        raw_text="激光器≥3",
    )
    normalized_result = _normalize_requirements(
        tender=tender,
        analysis_result=analysis_result,
        clause_result=clause_result,
        selected_packages=["1"],
        raw_text="激光器≥3",
    )

    result = _build_evidence_bindings(
        tender=tender,
        raw_text="激光器≥3",
        company=None,
        products={"1": product},
        selected_packages=["1"],
        normalized_result=normalized_result,
    )

    technical_match = result["technical_matches"][0]
    assert technical_match["bid_evidence_file"] == "产品彩页.pdf"
    assert technical_match["bid_evidence_page"] == 6
    assert technical_match["bid_evidence_type"] == "产品规格"


def test_normalize_requirements_outputs_machine_readable_fields() -> None:
    """测试normalize需求输出machinereadablefields。"""
    tender = _sample_tender()
    analysis_result = {
        "required_materials": ["营业执照", "医疗器械注册证"],
        "scoring_rules": ["技术分 60 分"],
    }
    clause_result = _classify_clauses(
        tender=tender,
        analysis_result=analysis_result,
        selected_packages=["1"],
        raw_text="激光器≥3，荧光通道≥11，合同签订后30日内交货。",
    )

    result = _normalize_requirements(
        tender=tender,
        analysis_result=analysis_result,
        clause_result=clause_result,
        selected_packages=["1"],
        raw_text="激光器≥3，荧光通道≥11，合同签订后30日内交货。",
    )

    assert result["technical_requirements"]
    first_item = result["technical_requirements"][0]
    assert first_item["package_id"] == "1"
    assert first_item["parameter_name"] == "激光器"
    assert first_item["comparator"] == "≥"
    assert first_item["threshold"] == "3"
    assert result["commercial_requirements"]


def test_decide_rule_branches_collects_manual_fill_items() -> None:
    """测试deciderulebranchescollectsmanualfill项。"""
    tender = _sample_tender()
    company = CompanyProfile(
        company_id="c1",
        name="测试医疗科技有限公司",
        legal_representative="张三",
        address="长春市高新区示范路1号",
        phone="13800000000",
        licenses=[CompanyLicense(license_type="营业执照", license_number="91110101TEST")],
    )
    products = {
        "1": ProductSpecification(
            product_id="p1",
            product_name="流式细胞分析仪",
            manufacturer="某厂家",
            model="",
            origin="",
            specifications={"激光器": "≥3"},
            price=0.0,
            certifications=[],
            registration_number="",
            authorization_letter="",
        )
    }

    result = _decide_rule_branches(
        tender=tender,
        raw_text="本项目允许采购进口设备，需提供注册证及合法来源证明。",
        selected_packages=["1"],
        company=company,
        products=products,
        clause_result={"branch_decisions": []},
    )

    assert "包1 品牌型号" in result["manual_fill_items"]
    assert "包1 单价" in result["manual_fill_items"]
    assert "包1 原产地/合法来源" in result["blocking_fill_items"]
    assert "包1 授权链/报关材料" in result["blocking_fill_items"]
    assert result["ready_for_generation"] is False
    assert any(item["decision_name"] == "合法来源/报关分支" for item in result["branch_decisions"])


def test_materialize_sections_injects_company_and_product_values() -> None:
    """测试materialize章节injects企业and产品值。"""
    tender = _sample_tender()
    company = CompanyProfile(
        company_id="c1",
        name="测试医疗科技有限公司",
        legal_representative="张三",
        address="长春市高新区示范路1号",
        phone="13800000000",
    )
    product = ProductSpecification(
        product_id="p1",
        product_name="流式细胞分析仪",
        manufacturer="某厂家",
        model="FC5000",
        origin="中国",
        specifications={"激光器": "≥3"},
        price=1000000.0,
        certifications=["CE"],
    )
    sections = [
        BidDocumentSection(
            section_title="第一章 资格性证明文件",
            content="投标人名称：[投标方公司名称]\n法定代表人：[法定代表人]",
            attachments=[],
        ),
        BidDocumentSection(
            section_title="第四章 报价书附件",
            content=(
                "| 序号 | 货物名称 | 规格型号 | 生产厂家 | 品牌 | 单价(元) | 数量 | 总价(元) |\n"
                "|---:|---|---|---|---|---:|---|---:|\n"
                "| 1 | 进口流式细胞分析仪 | [品牌型号] | [生产厂家] | [品牌] | [待填写] | 1 | [待填写] |\n"
                "|  | **投标总报价** |  |  |  |  |  | **[待填写]** |"
            ),
            attachments=[],
        ),
    ]

    materialized, report = _materialize_sections(
        sections=sections,
        tender=tender,
        company=company,
        products={"1": product},
    )

    assert "测试医疗科技有限公司" in materialized[0].content
    assert "FC5000" in materialized[1].content
    assert "某厂家" in materialized[1].content
    assert "1,000,000.00" in materialized[1].content
    assert report["changed_sections"] == ["第一章 资格性证明文件", "第四章 报价书附件"]


def test_materialize_sections_enriches_qualification_technical_and_appendix_content() -> None:
    """测试materialize章节enriches资格审查技术andappendix内容。"""
    tender = _sample_tender()
    company = CompanyProfile(
        company_id="c1",
        name="测试医疗科技有限公司",
        legal_representative="张三",
        address="长春市高新区示范路1号",
        phone="13800000000",
        licenses=[
            CompanyLicense(
                license_type="营业执照",
                license_number="91110101TEST",
                valid_until="长期",
            )
        ],
        social_insurance_proof="data/company/social-proof.pdf",
    )
    product = ProductSpecification(
        product_id="p1",
        product_name="流式细胞分析仪",
        manufacturer="某厂家",
        model="FC5000",
        origin="中国",
        specifications={"激光器": "3个独立激光器", "荧光通道": "12个"},
        price=1000000.0,
        certifications=["CE认证", "ISO13485"],
        registration_number="国械注准20260001",
        authorization_letter="data/product/auth.pdf",
    )
    sections = [
        BidDocumentSection(
            section_title="第一章 资格性证明文件",
            content=(
                "## 一、资格声明\n"
                "投标人名称：[投标方公司名称]\n"
                "### （一）基本养老保险缴纳证明\n"
                "（此处留空，待上传证明材料）\n"
                "### （二）中国政府采购网截图\n"
                "（此处留空，待上传截图）\n"
            ),
            attachments=[],
        ),
        BidDocumentSection(
            section_title="第三章 商务及技术部分",
            content=(
                "### （一）技术偏离及详细配置明细表（第1包）\n"
                "| 序号 | 招标技术参数要求 | 投标产品响应参数 | 偏离情况 | 响应依据/证据映射 |\n"
                "|---:|---|---|---|---|\n"
                "| 1 | 激光器：≥3 | ≥3 | 无偏离 | 招标原文片段 |\n"
                "### 包1：进口流式细胞分析仪\n"
                "| 序号 | 技术参数项 | 招标要求 | 响应情况 | 备注 |\n"
                "|---:|---|---|---|---|\n"
                "| 1 | 荧光通道 | ≥11 | ≥11 | 无偏离 |\n"
            ),
            attachments=[],
        ),
        BidDocumentSection(
            section_title="第四章 报价书附件",
            content=(
                "## 三、产品彩页\n"
                "（此处留空，待上传产品彩页）\n"
                "## 四、节能/环保/能效认证证书（如适用）\n"
                "（此处留空，待上传节能/环保/能效认证证书）\n"
                "## 五、检测/质评数据节选\n"
                "（此处留空，待上传检测报告或室间质评结果）\n"
            ),
            attachments=[],
        ),
    ]

    materialized, _ = _materialize_sections(
        sections=sections,
        tender=tender,
        company=company,
        products={"1": product},
    )

    qualification = materialized[0].content
    technical = materialized[1].content
    appendix = materialized[2].content

    assert "已关联社保缴纳证明：data/company/social-proof.pdf" in qualification
    assert "企业主体与资质实填摘要" in qualification
    assert "营业执照：91110101TEST" in qualification
    assert "3个独立激光器" in technical
    assert "12个" in technical
    assert "拟投产品实参摘要" in technical
    assert "已关联彩页资料：流式细胞分析仪 / FC5000 / 某厂家" in appendix
    assert "已关联认证材料：CE认证、ISO13485" in appendix
    assert "附件资料实填摘要" in appendix


def test_materialize_sections_writes_bidder_evidence_into_mapping_rows() -> None:
    """测试materialize章节writes投标侧证据into映射行。"""
    tender = _sample_tender()
    company = CompanyProfile(
        company_id="c1",
        name="测试医疗科技有限公司",
        legal_representative="张三",
        address="长春市高新区示范路1号",
        phone="13800000000",
    )
    product = ProductSpecification(
        product_id="p1",
        product_name="流式细胞分析仪",
        manufacturer="某厂家",
        model="FC5000",
        origin="美国",
        specifications={"激光器": "3个独立激光器"},
        price=1000000.0,
        registration_number="国械注进20260001",
        authorization_letter="data/product/auth.pdf",
    )
    sections = [
        BidDocumentSection(
            section_title="第三章 商务及技术部分",
            content=(
                "### （一）技术偏离及详细配置明细表（第1包）\n"
                "| 序号 | 招标技术参数要求 | 投标产品响应参数 | 偏离情况 | 响应依据/证据映射 |\n"
                "|---:|---|---|---|---|\n"
                "| 1 | 激光器：≥3 | 待核实（需填入投标产品实参） | 待核实 | 招标：激光器≥3 |\n\n"
                "### （四）技术条款证据映射表（第1包）\n"
                "| 序号 | 技术参数项 | 证据来源 | 原文片段 | 应用位置 |\n"
                "|---:|---|---|---|---|\n"
                "| 1 | 激光器 | 招标原文片段 | 激光器≥3 | 技术偏离表第1行 |"
            ),
            attachments=[],
        )
    ]
    evidence_result = {
        "technical_matches": [
            {
                "package_id": "1",
                "parameter_name": "激光器",
                "requirement_value": "≥3",
                "requirement_source_excerpt": "激光器≥3",
                "matched_fact_quote": "激光器：3个独立激光器",
                "matched_fact_source": "产品参数库",
                "bidder_evidence_bound": True,
                "bidder_evidence_source": "包1 产品参数",
                "bidder_evidence_quote": "激光器：3个独立激光器",
                "response_value": "3个独立激光器",
                "deviation_status": "无偏离",
                "comparison_reason": "已完成数值门槛校验",
                "proven": True,
            }
        ]
    }

    materialized, report = _materialize_sections(
        sections=sections,
        tender=tender,
        company=company,
        products={"1": product},
        evidence_result=evidence_result,
    )

    content = materialized[0].content
    assert "投标方证据：激光器：3个独立激光器" in content
    assert "招标原文 / 产品参数库 / 包1 产品参数" in content
    assert "| 1 | 激光器：≥3 | 3个独立激光器 | 【待填写：无偏离/正偏离/负偏离】 |" in content
    assert report["changed_sections"] == ["第三章 商务及技术部分"]


def test_materialize_sections_can_fill_truth_from_evidence_binding() -> None:
    """测试证据binding中的materialize章节canfilltruth。"""
    tender = _sample_tender()
    tender.packages[0].technical_requirements = {"原产地": "美国"}
    product = ProductSpecification(
        product_id="p-origin",
        product_name="流式细胞分析仪",
        manufacturer="某厂家",
        model="FC5000",
        origin="美国",
        specifications={},
        price=1000000.0,
    )
    sections = [
        BidDocumentSection(
            section_title="第三章 商务及技术部分",
            content=(
                "### （一）技术偏离及详细配置明细表（第1包）\n"
                "| 序号 | 招标技术参数要求 | 投标产品响应参数 | 偏离情况 | 响应依据/证据映射 |\n"
                "|---:|---|---|---|---|\n"
                "| 1 | 原产地：美国 | 待核实（需填入投标产品实参） | 待核实 | 招标：原产地：美国 |\n"
            ),
            attachments=[],
        )
    ]
    evidence_result = {
        "technical_matches": [
            {
                "package_id": "1",
                "parameter_name": "原产地",
                "requirement_value": "美国",
                "requirement_source_excerpt": "原产地：美国",
                "matched_fact_value": "美国",
                "matched_fact_quote": "原产地：美国",
                "matched_fact_source": "产品档案",
                "bidder_evidence_bound": True,
                "bidder_evidence_source": "包1 原产地",
                "bidder_evidence_quote": "原产地：美国",
                "response_value": "美国",
                "deviation_status": "无偏离",
                "comparison_reason": "已完成文本级事实比对",
                "proven": True,
            }
        ]
    }

    materialized, _ = _materialize_sections(
        sections=sections,
        tender=tender,
        company=None,
        products={"1": product},
        evidence_result=evidence_result,
    )

    content = materialized[0].content
    assert "| 1 | 原产地：美国 | 美国 | 【待填写：无偏离/正偏离/负偏离】 |" in content
    assert "投标方证据：原产地：美国" in content


def test_materialize_sections_fills_full_width_critical_placeholders() -> None:
    """测试materialize章节fillsfullwidthcriticalplaceholders。"""
    tender = _sample_tender()
    company = CompanyProfile(
        company_id="c1",
        name="测试医疗科技有限公司",
        legal_representative="张三",
        address="长春市高新区示范路1号",
        phone="13800000000",
        staff=[],
    )
    product = ProductSpecification(
        product_id="p1",
        product_name="流式细胞分析仪",
        manufacturer="某厂家",
        model="FC5000",
        origin="美国",
        specifications={"激光器": "3个独立激光器"},
        price=1000000.0,
    )
    sections = [
        BidDocumentSection(
            section_title="一、响应文件封面格式",
            content=(
                "供应商全称：（公章）【待填写：投标人名称】\n"
                "授权代表：【待填写：授权代表】\n"
                "电话：【待填写：联系电话】\n"
                "法定代表人：【待填写：法定代表人姓名】\n"
                "品牌型号：【待填写：品牌/型号，产地】\n"
            ),
            attachments=[],
        )
    ]

    materialized, report = _materialize_sections(
        sections=sections,
        tender=tender,
        company=company,
        products={"1": product},
    )

    content = materialized[0].content
    assert "测试医疗科技有限公司" in content
    assert "张三" in content
    assert "13800000000" in content
    assert "FC5000" in content
    assert "美国" in content
    assert "【待填写：投标人名称】" not in content
    assert report["changed_sections"] == ["一、响应文件封面格式"]


def test_materialize_sections_rebuilds_coarse_deviation_table_with_evidence_pages() -> None:
    """测试带证据pages的materialize章节rebuilds粗粒度偏离表。"""
    tender = _sample_tender()
    tender.packages[0].technical_requirements = {
        "激光器": "≥3",
        "荧光通道": "≥11",
        "分析速度": "不少于10000个事件/秒",
    }
    product = ProductSpecification(
        product_id="p1",
        product_name="流式细胞分析仪",
        manufacturer="某厂家",
        model="FC5000",
        origin="美国",
        specifications={
            "激光器": "3个独立激光器",
            "荧光通道": "12个检测通道",
            "分析速度": "12000个事件/秒",
        },
        price=1000000.0,
        config_items=[
            {"配置项": "主机", "数量": "1", "说明": "核心检测模块"},
            {"配置项": "工作站", "数量": "1", "说明": "数据采集与分析"},
        ],
        acceptance_notes="按激光器、通道数和分析速度逐项验收。",
    )
    sections = [
        BidDocumentSection(
            section_title="五、技术偏离及详细配置明细表",
            content=(
                "### 合同包1：进口流式细胞分析仪\n"
                "| 序号 | 货物名称 | 品牌型号、产地 | 数量/单位 | 报价(元) | 谈判文件的参数和要求 | 响应文件参数 | 偏离情况 |\n"
                "|---:|---|---|---|---:|---|---|---|\n"
                "| 1 | 进口流式细胞分析仪 | 【待填写：品牌/型号，产地】 | 1/台 | 【待填写】 | 详见采购文件技术要求 | 【待填写：逐条响应参数/配置/证据】 | 【待填写：无偏离/正偏离/负偏离】 |\n"
            ),
            attachments=[],
        )
    ]
    evidence_result = {
        "technical_matches": [
            {
                "package_id": "1",
                "parameter_name": "激光器",
                "requirement_value": "≥3",
                "response_value": "3个独立激光器",
                "matched_fact_quote": "激光器：3个独立激光器",
                "matched_fact_source": "产品参数库",
                "bidder_evidence_bound": True,
                "bidder_evidence_source": "产品彩页.pdf",
                "bidder_evidence_quote": "激光器：3个独立激光器",
                "bid_evidence_file": "产品彩页.pdf",
                "bid_evidence_page": 5,
                "deviation_status": "无偏离",
                "comparison_reason": "已完成数值门槛校验",
                "proven": True,
            },
            {
                "package_id": "1",
                "parameter_name": "荧光通道",
                "requirement_value": "≥11",
                "response_value": "12个检测通道",
                "matched_fact_quote": "荧光通道：12个检测通道",
                "matched_fact_source": "产品参数库",
                "bidder_evidence_bound": True,
                "bidder_evidence_source": "厂家参数页.pdf",
                "bidder_evidence_quote": "荧光通道：12个检测通道",
                "bid_evidence_file": "厂家参数页.pdf",
                "bid_evidence_page": 6,
                "deviation_status": "无偏离",
                "comparison_reason": "已完成数值门槛校验",
                "proven": True,
            },
            {
                "package_id": "1",
                "parameter_name": "分析速度",
                "requirement_value": "不少于10000个事件/秒",
                "response_value": "12000个事件/秒",
                "matched_fact_quote": "分析速度：12000个事件/秒",
                "matched_fact_source": "产品说明书.pdf",
                "bidder_evidence_bound": True,
                "bidder_evidence_source": "产品说明书.pdf",
                "bidder_evidence_quote": "分析速度：12000个事件/秒",
                "bid_evidence_file": "产品说明书.pdf",
                "bid_evidence_page": 8,
                "deviation_status": "无偏离",
                "comparison_reason": "已完成数值门槛校验",
                "proven": True,
            },
        ]
    }

    materialized, _ = _materialize_sections(
        sections=sections,
        tender=tender,
        company=None,
        products={"1": product},
        evidence_result=evidence_result,
    )

    content = materialized[0].content
    assert "| 条款编号 | 招标要求 | 投标型号 | 实际响应值 | 偏离情况 | 证据材料 | 页码 | 说明/验收备注 |" in content
    assert "| 1 | 激光器 | ≥3 | FC5000 | 3个独立激光器 | 【待填写：无偏离/正偏离/负偏离】 | 产品彩页.pdf | 5 |" in content
    assert "| 2 | 荧光通道 | ≥11 | 同上 | 12个检测通道 | 【待填写：无偏离/正偏离/负偏离】 | 厂家参数页.pdf | 6 |" in content
    assert "| 3 | 分析速度 | 不少于10000个事件/秒 | 同上 | 12000个事件/秒 | 【待填写：无偏离/正偏离/负偏离】 | 产品说明书.pdf | 8 |" in content
    assert "配置功能描述" in content
    assert "主机" in content
    assert "工作站" in content


def test_materialize_sections_rewrites_service_plan_with_package_specific_notes() -> None:
    """测试带包件specificnotes的materialize章节rewrites服务plan。"""
    tender = TenderDocument(
        project_name="设备采购项目",
        project_number="TP-2026-009",
        budget=3000000.0,
        purchaser="某医院",
        agency="某代理机构",
        procurement_type="竞争性谈判",
        packages=[
            ProcurementPackage(
                package_id="1",
                item_name="X射线血液辐照设备",
                quantity=1,
                budget=2000000.0,
                technical_requirements={"辐照剂量": "可设定", "联锁保护": "具备"},
                delivery_time="合同签订后45日内",
                delivery_place="输血科",
            ),
            ProcurementPackage(
                package_id="2",
                item_name="全自动电泳仪",
                quantity=1,
                budget=1000000.0,
                technical_requirements={"样本通道": "≥8", "分析软件": "具备"},
                delivery_time="合同签订后30日内",
                delivery_place="检验科",
            ),
        ],
        commercial_terms=CommercialTerms(payment_method="验收合格后付款"),
        evaluation_criteria={"技术分": 60, "价格分": 30, "商务分": 10},
    )
    product_1 = ProductSpecification(
        product_id="p1",
        product_name="X射线血液辐照设备",
        manufacturer="辐照厂家",
        model="XR-9000",
        origin="德国",
        specifications={"辐照剂量": "1~99Gy", "联锁保护": "具备"},
        functional_notes="重点校验联锁状态、辐照剂量和屏蔽安全。",
        acceptance_notes="按剂量均匀性、联锁保护和射线安全要求验收。",
        training_notes="培训内容覆盖剂量设置、辐照流程和异常停机处理。",
        price=2000000.0,
    )
    product_2 = ProductSpecification(
        product_id="p2",
        product_name="全自动电泳仪",
        manufacturer="电泳厂家",
        model="EP-500",
        origin="中国",
        specifications={"样本通道": "8通道", "分析软件": "具备"},
        functional_notes="重点校验样本通道、成像模块和结果分析软件。",
        acceptance_notes="按条带分辨率、重复性和结果分析功能要求验收。",
        training_notes="培训内容覆盖样本上样、电泳程序设置和结果分析。",
        price=900000.0,
    )
    sections = [
        BidDocumentSection(
            section_title="六、技术服务和售后服务的内容及措施",
            content=(
                "### 合同包1：X射线血液辐照设备\n"
                "#### 1. 供货组织与进度安排\n"
                "按采购文件要求执行。\n\n"
                "### 合同包2：全自动电泳仪\n"
                "#### 1. 供货组织与进度安排\n"
                "按采购文件要求执行。"
            ),
            attachments=[],
        )
    ]

    materialized, _ = _materialize_sections(
        sections=sections,
        tender=tender,
        company=None,
        products={"1": product_1, "2": product_2},
    )

    content = materialized[0].content
    assert "XR-9000" in content
    assert "重点校验联锁状态、辐照剂量和屏蔽安全" in content
    assert "培训内容覆盖剂量设置、辐照流程和异常停机处理" in content
    assert "EP-500" in content
    assert "重点校验样本通道、成像模块和结果分析软件" in content
    assert "培训内容覆盖样本上样、电泳程序设置和结果分析" in content


def test_materialize_sections_fills_review_table_locations_and_evidence_refs() -> None:
    """测试materialize章节fills评审表格locationsand证据refs。"""
    tender = _sample_tender()
    company = CompanyProfile(
        company_id="c1",
        name="测试医疗科技有限公司",
        legal_representative="张三",
        address="长春市高新区示范路1号",
        phone="13800000000",
        licenses=[
            CompanyLicense(
                license_type="营业执照",
                license_number="91110101TEST",
                valid_until="长期",
            )
        ],
    )
    product = ProductSpecification(
        product_id="p1",
        product_name="流式细胞分析仪",
        manufacturer="某厂家",
        model="FC5000",
        origin="美国",
        specifications={"激光器": "3个独立激光器"},
        price=1000000.0,
        registration_number="国械注进20260001",
    )
    sections = [
        BidDocumentSection(
            section_title="附三、详细评审响应对照表",
            content=(
                "| 序号 | 评审项 | 采购文件评分要求 | 响应文件对应内容 | 自评说明 | 证明材料/页码 |\n"
                "|---:|---|---|---|---|---|\n"
                "| 1 | 技术参数响应 | 激光器≥3 | 【待填写：对应章节/材料】 | 【待填写：如何满足该评分项】 | 【待填写：页码】 |\n"
                "| 2 | 售后服务方案 | 包含培训、验收、维保 | 【待填写：对应章节/材料】 | 【待填写：如何满足该评分项】 | 【待填写：页码】 |\n"
                "| 3 | 营业执照 | 与主体资格一致 |  |  |  |\n"
            ),
            attachments=[],
        )
    ]
    evidence_result = {
        "technical_matches": [
            {
                "package_id": "1",
                "parameter_name": "激光器",
                "requirement_value": "≥3",
                "response_value": "3个独立激光器",
                "bid_evidence_file": "产品彩页.pdf",
                "bid_evidence_page": 5,
                "proven": True,
            }
        ]
    }

    materialized, _ = _materialize_sections(
        sections=sections,
        tender=tender,
        company=company,
        products={"1": product},
        evidence_result=evidence_result,
    )

    content = materialized[0].content
    assert "技术偏离及详细配置明细表（第1包）" in content
    assert "产品彩页.pdf 第5页" in content
    assert "技术服务和售后服务的内容及措施（第1包）" in content
    assert "营业执照（91110101TEST）" in content


def test_materialize_sections_route_five_column_parameter_table_to_deviation_logic() -> None:
    """测试materialize章节routefivecolumn参数表格todeviationlogic。"""
    tender = _sample_tender()
    product = ProductSpecification(
        product_id="p1",
        product_name="流式细胞分析仪",
        manufacturer="某厂家",
        model="FC5000",
        origin="美国",
        specifications={"激光器": "3个独立激光器"},
        price=1000000.0,
    )
    sections = [
        BidDocumentSection(
            section_title="五、技术偏离及详细配置明细表",
            content=(
                "| 序号 | 技术参数项 | 采购文件技术要求 | 响应文件响应情况 | 偏离情况 |\n"
                "|---:|---|---|---|---|\n"
                "| 1 | 激光器 | ≥3 | 待核实（未匹配到已证实产品事实） | 待核实 |\n"
            ),
            attachments=[],
        )
    ]
    evidence_result = {
        "technical_matches": [
            {
                "package_id": "1",
                "parameter_name": "激光器",
                "requirement_value": "≥3",
                "response_value": "3个独立激光器",
                "proven": False,
            }
        ]
    }

    materialized, _ = _materialize_sections(
        sections=sections,
        tender=tender,
        company=None,
        products={"1": product},
        evidence_result=evidence_result,
    )

    content = materialized[0].content
    assert "| 1 | 激光器 | ≥3 | 3个独立激光器 | 【待填写：无偏离/正偏离/负偏离】 |" in content
    assert "待核实（未匹配到已证实产品事实）" not in content
    assert "| 1 | 激光器 | ≥3 | 3个独立激光器 | 待核实 |" not in content


def test_materialize_sections_preserves_rich_pending_guidance_in_deviation_rows() -> None:
    """已有逐条引导语时，不应在物化阶段回写成统一空壳占位。"""
    tender = _sample_tender()
    sections = [
        BidDocumentSection(
            section_title="五、技术偏离及详细配置明细表",
            content=(
                "| 条款编号 | 招标要求 | 投标型号 | 实际响应值 | 偏离情况 | 证据材料 | 页码 | 说明/验收备注 |\n"
                "|---:|---|---|---|---|---|---:|---|\n"
                "| 1 | X射线球管最大电压≥150kV | 待定型号 | "
                "【待填写：品牌/型号/规格/配置及逐条响应】；请填写与“≥150kV”逐项对应的实际响应值，并标注说明书/彩页页码。"
                " | 【待填写：无偏离/正偏离/负偏离】 | 说明书/彩页/厂家参数表（页码待补） | 待补页码 | 请补充实际响应值及对应页码后复核 |\n"
            ),
            attachments=[],
        )
    ]

    materialized, _ = _materialize_sections(
        sections=sections,
        tender=tender,
        company=None,
        products={},
        evidence_result=None,
    )

    content = materialized[0].content
    assert "【待填写：品牌/型号/规格/配置及逐条响应】；请填写与“≥150kV”逐项对应的实际响应值，并标注说明书/彩页页码。" in content
    assert "【待填写：无偏离/正偏离/负偏离】" in content
    assert "说明书/彩页/厂家参数表（页码待补）" in content
    assert "请补充实际响应值及对应页码后复核" in content
    assert "【待填写：实际响应值】" not in content
