from __future__ import annotations

from app.schemas import BidDocumentSection, CommercialTerms, CompanyProfile, ProcurementPackage, ProductSpecification, TenderDocument
from app.services.tender_workflow import _materialize_sections, _prepare_citations, _second_validation


def _section(title: str, content: str) -> BidDocumentSection:
    """返回章节。"""
    return BidDocumentSection(section_title=title, content=content, attachments=[])


def test_prepare_citations_normalizes_and_deduplicates() -> None:
    """测试prepare引用normalizesanddeduplicates。"""
    long_text = "A" * 400
    hits = [
        {
            "text": long_text,
            "score": 0.91234567,
            "metadata": {"source": "tender::demo", "chunk_index": "2"},
        },
        {
            "text": long_text,
            "score": 0.91234567,
            "metadata": {"source": "tender::demo", "chunk_index": "2"},
        },
    ]

    citations = _prepare_citations(hits, limit=5)
    assert len(citations) == 1
    assert citations[0]["source"] == "tender::demo"
    assert citations[0]["chunk_index"] == 2
    assert citations[0]["score"] == 0.912346
    assert citations[0]["quote"].endswith("...")


def test_materialize_sections_keeps_date_placeholder_without_explicit_document_date() -> None:
    """测试materialize章节keeps日期占位符withoutexplicit文档日期。"""
    tender = TenderDocument(
        project_name="示例项目",
        project_number="TP-2026-009",
        budget=100.0,
        purchaser="某医院",
        agency="某代理机构",
        procurement_type="竞争性谈判",
        packages=[
            ProcurementPackage(
                package_id="1",
                item_name="流式细胞仪",
                quantity=1,
                budget=100.0,
                technical_requirements={},
                delivery_time="30日内交货",
                delivery_place="采购人指定地点",
            )
        ],
        commercial_terms=CommercialTerms(),
        evaluation_criteria={},
        special_requirements="",
    )
    company = CompanyProfile(
        company_id="c1",
        name="某投标单位",
        legal_representative="张三",
        address="长春市某路1号",
        phone="13800000000",
    )
    sections = [
        BidDocumentSection(
            section_title="一、响应文件封面格式",
            content="谈判日期：【待填写：日期】\n日期：【待填写：年 月 日】",
            attachments=[],
        )
    ]

    materialized, _ = _materialize_sections(
        sections=sections,
        tender=tender,
        company=company,
        products={},
        evidence_result=None,
    )
    content = materialized[0].content

    assert "2026年" not in content
    assert "谈判日期：【待填写：日期】" in content
    assert "日期：【待填写：年 月 日】" in content


def test_second_validation_detects_missing_items_and_placeholders() -> None:
    """测试第二校验detectsmissing项andplaceholders。"""
    analysis_result = {
        "required_materials": [
            "营业执照",
            "授权书",
            "报价书",
        ],
        "citations": [],
    }
    validation_result = {"overall_status": "需补充"}
    sections = [
        _section("第一章 资格性证明文件", "营业执照 [待填写]"),
        _section("第二章 符合性承诺", "授权书说明"),
        _section("第三章 商务及技术部分", "技术参数"),
    ]

    result = _second_validation(
        analysis_result=analysis_result,
        validation_result=validation_result,
        sections=sections,
        generation_result={"citations": []},
    )

    assert result["overall_status"] == "需修订"
    assert any(item["name"] == "占位符与留空项检查" and item["status"] == "需修订" for item in result["check_items"])
    assert any(item["name"] == "技术条款证据映射" and item["status"] == "需修订" for item in result["check_items"])
    assert any("检索引用" in issue for issue in result["issues"])
    assert any("分章节生成不完整" in issue for issue in result["issues"])


def test_second_validation_passes_when_content_and_citations_are_complete() -> None:
    """测试第二校验passeswhen内容and引用arecomplete。"""
    analysis_result = {
        "required_materials": [
            "营业执照",
            "授权书",
            "报价书",
            "技术偏离表",
        ],
        "citations": [{"source": "tender::demo", "chunk_index": 1, "score": 0.8, "quote": "项目要求摘要"}],
    }
    validation_result = {"overall_status": "通过"}
    sections = [
        _section("第一章 资格性证明文件", "营业执照与授权书材料已附。"),
        _section("第二章 符合性承诺", "报价书承诺与商务条款响应。"),
        _section(
            "第三章 商务及技术部分",
            "技术偏离表逐条响应。技术条款证据映射表已附。\n"
            "| 条款编号 | 招标要求 | 投标型号 | 实际响应值 | 偏离情况 | 证据材料 | 页码 | 说明/验收备注 |\n"
            "### （二-B）配置功能描述\n"
            "### （五）关键性能说明\n"
            "### （六）配置说明\n"
            "### （七）交付说明\n"
            "### （八）验收说明\n"
            "### （九）使用与培训说明\n",
        ),
        _section("第四章 报价书附件", "报价书明细与附件目录。"),
    ]

    result = _second_validation(
        analysis_result=analysis_result,
        validation_result=validation_result,
        sections=sections,
        generation_result={"citations": [{"source": "tender::demo", "chunk_index": 2, "score": 0.7, "quote": "评分标准摘录"}]},
    )

    # 新增 5 项深度检查在无投标材料时预期为"需修订"，基础结构性检查应通过
    _DEPTH_CHECK_PREFIXES = (
        "offered_fact_coverage",
        "bid_evidence_coverage",
        "config_detail_score",
        "mapping_count_consistency",
        "section_template_similarity",
    )
    structural_checks = [
        item for item in result["check_items"]
        if not item["name"].startswith(_DEPTH_CHECK_PREFIXES)
    ]
    assert all(item["status"] == "通过" for item in structural_checks)
    # 深度检查存在即可
    depth_check_names = {item["name"] for item in result["check_items"]} - {item["name"] for item in structural_checks}
    assert len(depth_check_names) >= 5


def test_second_validation_detects_package_leakage_and_model_gaps() -> None:
    """测试第二校验detects包件leakageand模型gaps。"""
    tender = TenderDocument(
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
                technical_requirements={"激光器": "≥3"},
                delivery_time="合同签订后30日内",
                delivery_place="采购人指定地点",
            ),
            ProcurementPackage(
                package_id="2",
                item_name="手术头架",
                quantity=2,
                budget=300000.0,
                technical_requirements={"材质": "碳纤维"},
                delivery_time="合同签订后30日内",
                delivery_place="采购人指定地点",
            ),
        ],
        commercial_terms=CommercialTerms(payment_method="验收合格后付款"),
        evaluation_criteria={"技术分": 60, "价格分": 30, "商务分": 10},
    )
    analysis_result = {
        "required_materials": ["营业执照", "技术偏离表"],
        "citations": [{"source": "tender::demo", "chunk_index": 1, "score": 0.9, "quote": "摘要"}],
        "key_information": {
            "project_name": tender.project_name,
            "project_number": tender.project_number,
        },
    }
    validation_result = {"overall_status": "通过"}
    sections = [
        _section("第一章 资格性证明文件", "项目名称：医院流式细胞分析仪采购项目\n项目编号：HLJ-2026-018"),
        _section("第三章 商务及技术部分", "包2 技术参数说明\n技术偏离表\n[品牌型号]\n技术条款证据映射表"),
        _section("第四章 报价书附件", "| 1 | 进口流式细胞分析仪 | [品牌型号] | 某厂家 | 某品牌 | 1000000 | 1 | 1000000 |"),
    ]

    result = _second_validation(
        analysis_result=analysis_result,
        validation_result=validation_result,
        sections=sections,
        generation_result={"citations": [{"source": "tender::demo", "chunk_index": 2, "score": 0.8, "quote": "评分"}]},
        tender=tender,
        selected_packages=["1"],
        products={
            "1": ProductSpecification(
                product_id="p1",
                product_name="流式细胞分析仪",
                manufacturer="某厂家",
                model="FC5000",
                origin="中国",
                specifications={"激光器": "≥3"},
                price=1000000.0,
            )
        },
    )

    assert result["overall_status"] == "需修订"
    assert any(item["name"] == "包件分仓检查" and item["status"] == "需修订" for item in result["check_items"])
    assert any(item["name"] == "技术响应实参化" and item["status"] == "需修订" for item in result["check_items"])
    assert any(item["name"] == "数量/型号一致性" and item["status"] == "需修订" for item in result["check_items"])


def test_second_validation_blocks_unproven_no_deviation_rows() -> None:
    """测试第二校验文本块unprovennodeviation行。"""
    analysis_result = {
        "required_materials": ["营业执照", "技术偏离表"],
        "citations": [{"source": "tender::demo", "chunk_index": 1, "score": 0.9, "quote": "摘要"}],
        "key_information": {
            "project_name": "医院流式细胞分析仪采购项目",
            "project_number": "HLJ-2026-018",
        },
    }
    validation_result = {"overall_status": "通过"}
    sections = [
        _section("第一章 资格性证明文件", "项目名称：医院流式细胞分析仪采购项目\n项目编号：HLJ-2026-018"),
        _section(
            "第三章 商务及技术部分",
            "技术条款证据映射表\n| 1 | 激光器 | ≥3 | 待核实（未匹配到已证实产品事实） | 无偏离 | 未匹配到证据 |",
        ),
        _section("第四章 报价书附件", "报价书附件"),
    ]

    result = _second_validation(
        analysis_result=analysis_result,
        validation_result=validation_result,
        sections=sections,
        generation_result={"citations": [{"source": "tender::demo", "chunk_index": 2, "score": 0.8, "quote": "评分"}]},
        evidence_result={
            "technical_matches": [
                {
                    "parameter_name": "激光器",
                    "response_value": "",
                    "proven": False,
                }
            ],
            "proven_response_count": 0,
            "proven_completion_rate": 0.0,
            "unproven_items": ["包1 激光器"],
        },
    )

    assert result["overall_status"] == "需修订"
    assert any(item["name"] == "已证实响应完成率" and item["status"] == "需修订" for item in result["check_items"])
    assert any(item["name"] == "技术响应语义一致性" and item["status"] == "需修订" for item in result["check_items"])


def test_second_validation_rejects_mapping_table_with_only_tender_excerpt() -> None:
    """测试带only招标文件摘录的第二校验rejects映射表格。"""
    analysis_result = {
        "required_materials": ["营业执照", "技术偏离表"],
        "citations": [{"source": "tender::demo", "chunk_index": 1, "score": 0.9, "quote": "摘要"}],
        "key_information": {
            "project_name": "医院流式细胞分析仪采购项目",
            "project_number": "HLJ-2026-018",
        },
    }
    sections = [
        _section("第一章 资格性证明文件", "项目名称：医院流式细胞分析仪采购项目\n项目编号：HLJ-2026-018"),
        _section(
            "第三章 商务及技术部分",
            "技术条款证据映射表\n| 1 | 激光器 | 招标原文片段 | 激光器≥3 | 技术偏离表第1行 |",
        ),
        _section("第四章 报价书附件", "报价书附件"),
    ]

    result = _second_validation(
        analysis_result=analysis_result,
        validation_result={"overall_status": "通过"},
        sections=sections,
        generation_result={"citations": [{"source": "tender::demo", "chunk_index": 2, "score": 0.8, "quote": "评分"}]},
        evidence_result={
            "technical_matches": [
                {
                    "package_id": "1",
                    "parameter_name": "激光器",
                    "requirement_value": "≥3",
                    "requirement_source_excerpt": "激光器≥3",
                    "matched_fact_quote": "激光器：3个独立激光器",
                    "bidder_evidence_quote": "激光器：3个独立激光器",
                    "response_value": "3个独立激光器",
                    "proven": True,
                }
            ],
            "proven_response_count": 1,
            "proven_completion_rate": 1.0,
            "unproven_items": [],
        },
    )

    assert result["overall_status"] == "需修订"
    assert any(item["name"] == "技术条款证据映射" and item["status"] == "需修订" for item in result["check_items"])
    assert any("证据映射表存在但内容未完成" in issue for issue in result["issues"])
