from __future__ import annotations

from app.schemas import BidDocumentSection, BidEvidenceBinding, CommercialTerms, NormalizedRequirement, ProcurementPackage, TenderDocument
from app.services.one_click_generator import (
    _apply_template_pollution_guard,
    _build_configuration_table,
    _build_detail_quote_table,
    _build_enterprise_declaration_block,
    _build_requirement_rows,
    compute_validation_gate,
    _effective_requirements,
    _gen_appendix,
    _gen_qualification,
    _gen_technical,
    generate_bid_sections,
)
from app.services.tender_parser import TenderParser


def _sample_tender(
    project_name: str = "检验设备采购项目",
    special_requirements: str = "本项目按中小企业政策执行，允许联合体投标。",
) -> TenderDocument:
    return TenderDocument(
        project_name=project_name,
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
                technical_requirements={"激光器": "≥3", "荧光通道": "≥11"},
                delivery_time="合同签订后30日内",
                delivery_place="采购人指定地点",
            )
        ],
        commercial_terms=CommercialTerms(
            payment_method="验收后付款",
            validity_period="90日历天",
            warranty_period="1年",
            performance_bond="不收取",
        ),
        evaluation_criteria={"价格分": 30, "技术分": 60, "商务分": 10},
        special_requirements=special_requirements,
    )


def test_template_pollution_guard_removes_prompt_artifacts() -> None:
    from app.schemas import BidDocumentSection

    sections = [
        BidDocumentSection(
            section_title="第二章 符合性承诺",
            content=(
                "你是投标文件撰写专家。\n"
                "请生成以下内容。\n"
                "当然，以下是符合性承诺草稿：\n"
                "System: 你必须只输出JSON。\n"
                "## 二、正文\n"
                "本单位承诺合法合规。\n"
                "{{未渲染变量}}\n"
            ),
            attachments=[],
        )
    ]

    guarded = _apply_template_pollution_guard(sections)
    text = guarded[0].content
    assert "你是投标文件撰写专家" not in text
    assert "请生成以下内容" not in text
    assert "当然，以下是符合性承诺草稿" not in text
    assert "System: 你必须只输出JSON" not in text
    assert "{{未渲染变量}}" not in text
    assert "本单位承诺合法合规" in text


def test_enterprise_declaration_branching_for_sme_policy() -> None:
    tender = _sample_tender()
    block = _build_enterprise_declaration_block(tender, "2026年03月09日")
    assert "企业类型声明函（分支选择）" in block
    assert "分支A：中小企业声明函" in block
    assert "分支B：监狱企业证明材料" in block
    assert "分支C：残疾人福利性单位声明函" in block
    assert "判定结果" not in block


def test_enterprise_declaration_keeps_branches_without_sme_policy() -> None:
    tender = _sample_tender(
        project_name="通用设备采购项目",
        special_requirements="按采购文件执行，不涉及政策加分。",
    )
    block = _build_enterprise_declaration_block(tender, "2026年03月09日")
    assert "企业类型声明函（分支选择）" in block
    assert "分支D：非中小企业声明" in block
    assert "判定结果" not in block


def test_technical_section_is_forced_structured() -> None:
    tender = _sample_tender(special_requirements="本项目不接受联合体。")
    section = _gen_technical(llm=None, tender=tender, tender_raw="raw text")
    assert "### 包1：" in section.content
    assert "技术偏离及详细配置明细表" in section.content
    assert "详细配置明细表" in section.content
    assert "技术响应检查清单" in section.content
    assert "技术条款证据映射表" in section.content
    assert "（第1包）" not in section.content
    assert "具体参数待填写" not in section.content
    assert "招标原文长度" not in section.content


def test_qualification_section_uses_detected_region_title() -> None:
    tender = _sample_tender(project_name="吉林大学中日联谊医院流式细胞仪采购项目")
    section = _gen_qualification(llm=None, tender=tender)
    assert "吉林省政府采购供应商资格承诺函" in section.content
    assert "黑龙江省政府采购供应商资格承诺函" not in section.content


def test_qualification_section_prefers_purchaser_region_over_agency_region() -> None:
    tender = _sample_tender(project_name="流式细胞仪采购项目")
    tender.purchaser = "吉林大学中日联谊医院"
    tender.agency = "北京某招标有限公司"

    section = _gen_qualification(llm=None, tender=tender)

    assert "吉林省政府采购供应商资格承诺函" in section.content
    assert "北京市政府采购供应商资格承诺函" not in section.content


def test_generated_sections_are_word_friendly_without_raw_markdown_markers() -> None:
    tender = _sample_tender()
    technical = _gen_technical(llm=None, tender=tender, tender_raw="技术参数：激光器≥3；荧光通道≥11。")
    appendix = _gen_appendix(llm=None, tender=tender, tender_raw="技术参数：激光器≥3；荧光通道≥11。")

    assert "#### " not in technical.content
    assert "\n> " not in technical.content
    assert "#### " not in appendix.content
    assert "节能/环保/能效认证证书（如适用）" in appendix.content


def test_technical_section_can_fallback_to_raw_text_requirements() -> None:
    tender = _sample_tender()
    tender.packages[0].technical_requirements = {}

    section = _gen_technical(
        llm=None,
        tender=tender,
        tender_raw="采购需求 技术参数：激光器：≥3；荧光通道：≥11；分析速度：不少于10000个事件/秒。",
    )

    assert "激光器" in section.content
    assert "荧光通道" in section.content
    assert "分析速度" in section.content
    assert "未提取到结构化参数" not in section.content


def test_technical_fallback_filters_out_scoring_and_contract_noise() -> None:
    tender = _sample_tender()
    tender.packages[0].technical_requirements = {}

    section = _gen_technical(
        llm=None,
        tender=tender,
        tender_raw=(
            "2、主要技术参数：\n"
            "2.1.1激光器：488nm蓝色激光器，需要同时检测不少于4个荧光通道；\n"
            "2.3.4系统维护：系统无特定维护套装，无需定期更换管路；\n"
            "技术部分 / 对招标文件技术规格要求的响应程度（29分）\n"
            "乙方承诺：自签订合同之日起一年内，在吉林省范围内同品牌、同型号、同配置的产品，价格低于本次采购价格，由乙方将差价赔付给甲方。\n"
            "设备配置及参数清单：（见附页）共 页，需医院科室负责人等签字确认。\n"
            "序号 | 耗材名称\n"
        ),
    )

    assert "激光器" in section.content
    assert "系统维护" in section.content
    assert "（29分）" not in section.content
    assert "乙方承诺" not in section.content
    assert "序号 | 耗材名称" not in section.content


def test_compliance_terms_normalize_contract_placeholders() -> None:
    tender = _sample_tender()
    tender.commercial_terms.payment_method = (
        "甲方在支付本合同项下每一笔款项前，乙方应当开具并提供等额合格的发票，"
        "乙方未按期开具或拒绝开具或所开具的发票不符合约定的，甲方有权延期付款，不构成违约"
    )
    tender.commercial_terms.warranty_period = "自设备安装调试验收合格之日起算保修期为   年"

    section = _gen_appendix(llm=None, tender=tender, tender_raw="技术参数：激光器≥3。")

    assert "甲方在支付本合同项下每一笔款项前" not in section.content
    assert "保修期为   年" not in section.content
    assert "按招标文件及合同约定执行" in section.content


def test_appendix_filters_document_format_noise_from_parameter_table() -> None:
    tender = _sample_tender()
    tender.packages[0].technical_requirements = {}

    section = _gen_appendix(
        llm=None,
        tender=tender,
        tender_raw=(
            "主要技术参数：\n"
            "2.1 激光器：≥3；\n"
            "2.2 荧光通道：≥11；\n"
            "12 | 8.4 | 投标文件格式特殊要求：投标文件正本与副本采用A4纸印刷分别装订成册\n"
        ),
    )

    assert "激光器" in section.content
    assert "荧光通道" in section.content
    assert "投标文件格式特殊要求" not in section.content
    assert "A4纸" not in section.content
    assert "| 8.4 |" not in section.content


def test_appendix_payment_sentence_avoids_duplicate_contract_clause() -> None:
    tender = _sample_tender()
    tender.commercial_terms.payment_method = "按招标文件及合同约定执行"

    section = _gen_appendix(llm=None, tender=tender, tender_raw="技术参数：激光器：≥3。")

    assert "付款方式按招标文件及合同约定执行。" in section.content
    assert "付款方式按“按招标文件及合同约定执行”及合同约定执行。" not in section.content


def test_effective_requirements_split_composite_technical_parameters() -> None:
    tender = _sample_tender()
    pkg = tender.packages[0]
    pkg.technical_requirements = {
        "技术参数": "激光器：≥3；荧光通道：≥11；分析速度：不少于10000个事件/秒",
    }

    requirements = _effective_requirements(pkg, tender_raw="")

    assert ("激光器", "≥3") in requirements
    assert ("荧光通道", "≥11") in requirements
    assert ("分析速度", "不少于10000个事件/秒") in requirements
    assert ("技术参数", "激光器：≥3；荧光通道：≥11；分析速度：不少于10000个事件/秒") not in requirements


def test_requirement_rows_bind_evidence_within_same_package_scope() -> None:
    pkg1 = ProcurementPackage(
        package_id="1",
        item_name="X射线血液辐照设备",
        quantity=1,
        budget=2145000.0,
        technical_requirements={"适用范围": "国内", "工作用途": "输血科"},
        delivery_time="合同签订后30个日历日内交货",
        delivery_place="甲方指定地点",
    )
    pkg2 = ProcurementPackage(
        package_id="2",
        item_name="手术用头架",
        quantity=1,
        budget=328500.0,
        technical_requirements={"适用范围": "国际", "工作用途": "门诊中心、手术室"},
        delivery_time="合同签订后90个日历日内交货",
        delivery_place="甲方指定地点",
    )
    tender_raw = (
        "包1 X射线血液辐照设备\n"
        "技术参数与性能要求\n"
        "适用范围：国内\n"
        "工作用途：输血科\n"
        "包2 手术用头架\n"
        "技术参数与性能要求\n"
        "适用范围：国际\n"
        "工作用途：门诊中心、手术室\n"
    )

    rows, _ = _build_requirement_rows(pkg2, tender_raw)

    scope_quotes = {row["key"]: row["evidence_quote"] for row in rows}
    assert "国际" in scope_quotes["适用范围"]
    assert "国内" not in scope_quotes["适用范围"]
    assert "门诊中心、手术室" in scope_quotes["工作用途"]
    assert "输血科" not in scope_quotes["工作用途"]


def test_configuration_table_uses_real_configuration_items_instead_of_template() -> None:
    tender = _sample_tender(project_name="手术用头架采购项目")
    pkg = tender.packages[0]
    pkg.item_name = "手术用头架"
    pkg.technical_requirements = {
        "设备配置与配件": "头夹（颅骨固定架）1个, 万向球轴连接器1个, 底座1个, 成人头托1个, 成人可重复使用头钉9个, 儿童可重复使用头钉3个",
    }

    table = _build_configuration_table(pkg, tender_raw="")

    assert "头夹（颅骨固定架） | 个 | 1" in table
    assert "成人可重复使用头钉 | 个 | 9" in table
    assert "儿童可重复使用头钉 | 个 | 3" in table
    assert "配套软件系统" not in table
    assert "随机附件及工具" not in table


def test_requirement_rows_keep_response_values_pending_until_bidder_facts_are_bound() -> None:
    tender = _sample_tender()
    pkg = tender.packages[0]
    pkg.technical_requirements = {"激光器": "≥3", "荧光通道": "≥11"}

    rows, _ = _build_requirement_rows(pkg, tender_raw="")

    response_map = {row["key"]: row["response"] for row in rows}
    assert response_map["激光器"] == "待核实（需填入投标产品实参）"
    assert response_map["荧光通道"] == "待核实（需填入投标产品实参）"
    assert all("承诺满足" not in row["response"] for row in rows)


def test_requirement_rows_can_consume_structured_workflow_matches() -> None:
    tender = _sample_tender()
    pkg = tender.packages[0]

    rows, total = _build_requirement_rows(
        pkg,
        tender_raw="",
        normalized_result={
            "technical_requirements": [
                {
                    "requirement_id": "T-1-1",
                    "package_id": "1",
                    "param_name": "激光器",
                    "normalized_value": "≥3",
                    "source_text": "激光器≥3",
                    "source_page": 12,
                }
            ]
        },
        evidence_result={
            "technical_matches": [
                {
                    "requirement_id": "T-1-1",
                    "package_id": "1",
                    "parameter_name": "激光器",
                    "response_value": "3个独立激光器",
                    "deviation_status": "无偏离",
                    "bid_evidence_file": "产品彩页.pdf",
                    "bid_evidence_page": 8,
                    "bid_evidence_type": "产品规格",
                    "bid_evidence_snippet": "激光器：3个独立激光器",
                    "bidder_evidence_quote": "激光器：3个独立激光器",
                    "bidder_evidence_source": "包1 产品参数",
                    "tender_source_text": "激光器≥3",
                    "tender_source_page": 12,
                    "proven": True,
                }
            ]
        },
        product_profile={"technical_specs": {"激光器": "3个独立激光器"}},
    )

    assert total == 1
    assert rows[0]["response"] == "3个独立激光器"
    assert rows[0]["evidence_source"] == "产品彩页.pdf / 产品规格"
    assert rows[0]["bidder_evidence_page"] == 8
    assert rows[0]["tender_quote"] == "激光器≥3"


def test_generated_sections_do_not_use_commitment_sentences_as_response_values() -> None:
    tender = _sample_tender()
    pkg = tender.packages[0]
    pkg.technical_requirements = {"激光器": "≥3", "荧光通道": "≥11"}

    technical = _gen_technical(llm=None, tender=tender, tender_raw="包1\n技术参数\n激光器：≥3\n荧光通道：≥11\n")
    appendix = _gen_appendix(llm=None, tender=tender, tender_raw="包1\n技术参数\n激光器：≥3\n荧光通道：≥11\n")

    assert "承诺满足" not in technical.content
    assert "承诺满足" not in appendix.content
    assert "待核实（需填入投标产品实参）" in technical.content
    assert "待核实（需填入投标产品实参）" in appendix.content
    assert " | 无偏离 | " not in technical.content


def test_detail_quote_table_prefers_quantity_inferred_from_package_scope() -> None:
    tender = TenderDocument(
        project_name="手术用头架、X射线血液辐照设备(二次)",
        project_number="[230001]wdzb[CS]20250014-1",
        budget=2473500.0,
        purchaser="哈尔滨医科大学附属第一医院",
        agency="某代理机构",
        procurement_type="竞争性磋商",
        packages=[
            ProcurementPackage(
                package_id="1",
                item_name="X射线血液辐照设备",
                quantity=1,
                budget=2145000.0,
                technical_requirements={},
                delivery_time="合同签订后30个日历日内交货",
                delivery_place="甲方指定地点",
            ),
            ProcurementPackage(
                package_id="2",
                item_name="手术用头架",
                quantity=1,
                budget=328500.0,
                technical_requirements={},
                delivery_time="合同签订后90个日历日内交货",
                delivery_place="甲方指定地点",
            ),
        ],
        commercial_terms=CommercialTerms(
            payment_method="按招标文件及合同约定执行",
            validity_period="90日历天",
            warranty_period="5年",
            performance_bond="不收取",
        ),
        evaluation_criteria={},
        special_requirements="",
    )
    tender_raw = (
        "包1 X射线血液辐照设备\n"
        "设备总台数：1台\n"
        "包2 手术用头架\n"
        "设备总台数：3台\n"
    )

    table = _build_detail_quote_table(tender, tender_raw)

    assert "| 2 | 手术用头架 | [品牌型号] | [生产厂家] | [品牌] | [待填写] | 3 | [待填写] |" in table


def test_configuration_table_can_extract_items_from_raw_configuration_section() -> None:
    tender = _sample_tender(project_name="检验科设备采购项目")
    pkg = tender.packages[0]
    pkg.item_name = "进口全自动电泳仪"
    pkg.technical_requirements = {}

    table = _build_configuration_table(
        pkg,
        tender_raw=(
            "包1 进口全自动电泳仪\n"
            "技术参数与性能要求\n"
            "检测原理：琼脂凝胶电泳法\n"
            "装箱配置单：\n"
            "1、主机 1台\n"
            "2、上样组件 1套\n"
            "3、说明书 1份\n"
            "质保：进口一年，国产三年\n"
        ),
    )

    assert "主机 | 台 | 1 | 是 | 核心检测/分析设备 | 核心模块" in table
    assert "上样组件 | 套 | 1 |" in table
    assert "说明书 | 份 | 1 |" in table
    assert "随机附件及工具" not in table


def test_configuration_table_can_use_product_profile_config_items() -> None:
    tender = _sample_tender(project_name="检验科设备采购项目")
    pkg = tender.packages[0]
    pkg.item_name = "进口全自动电泳仪"

    table = _build_configuration_table(
        pkg,
        tender_raw="",
        product_profile={
            "config_items": [
                {"配置项": "主机", "单位": "台", "数量": "1", "说明": "核心检测设备"},
                {"配置项": "上样组件", "单位": "套", "数量": "1", "说明": "自动上样模块"},
                {"配置项": "说明书", "单位": "份", "数量": "1", "说明": "中文操作资料"},
            ]
        },
    )

    assert "主机 | 台 | 1 | 是 |" in table
    assert "核心模块；核心检测设备" in table or "核心模块" in table
    assert "上样组件 | 套 | 1 |" in table
    assert "说明书 | 份 | 1 |" in table


def test_requirement_rows_trim_evidence_before_configuration_and_complaint_sections() -> None:
    tender = _sample_tender(project_name="检验科设备采购项目")
    pkg = tender.packages[0]
    pkg.item_name = "进口流式细胞分析仪"
    pkg.technical_requirements = {
        "卫生部国家临检中心TBNK淋巴细胞亚群项目的室间质评具备独立分组": "是",
        "刀锋式点样": "是",
    }

    rows, _ = _build_requirement_rows(
        pkg,
        tender_raw=(
            "包1 进口流式细胞分析仪\n"
            "技术参数与性能要求\n"
            "卫生部国家临检中心TBNK淋巴细胞亚群项目的室间质评有独立分组且实验室数量充足。\n"
            "刀锋式点样。\n"
            "装箱配置单：主机1台、流式管架1套\n"
            "质保：进口一年，国产三年\n"
            "质疑：是参与所质疑项目采购活动的供应商。\n"
        ),
    )

    quotes = {row["key"]: row["evidence_quote"] for row in rows}
    assert "独立分组" in quotes["卫生部国家临检中心TBNK淋巴细胞亚群项目的室间质评具备独立分组"]
    assert "质疑" not in quotes["卫生部国家临检中心TBNK淋巴细胞亚群项目的室间质评具备独立分组"]
    assert "刀锋式点样" in quotes["刀锋式点样"]
    assert "装箱配置单" not in quotes["刀锋式点样"]
    assert "质保" not in quotes["刀锋式点样"]


def test_parser_can_correct_package_quantity_from_package_scope_text() -> None:
    quantity = TenderParser._infer_package_quantity_from_text(
        tender_text=(
            "包1 X射线血液辐照设备\n"
            "设备总台数：1台\n"
            "包2 手术用头架\n"
            "设备总台数：3台\n"
        ),
        package_id="2",
        item_name="手术用头架",
        current_quantity=1,
    )

    assert quantity == 3


def test_validation_gate_does_not_flag_meaningful_short_param_names_as_truncated() -> None:
    sections = [
        BidDocumentSection(
            section_title="第三章 商务及技术部分",
            content="技术偏离及详细配置明细表\n| 1 | 主机 | 满足 |",
            attachments=[],
        )
    ]
    normalized_reqs = {
        "1": [
            NormalizedRequirement(
                package_id="1",
                requirement_id="pkg1-req-001",
                param_name="主机",
                raw_text="主机：1台",
                source_text="主机：1台",
            ),
            NormalizedRequirement(
                package_id="1",
                requirement_id="pkg1-req-002",
                param_name="接口",
                raw_text="接口：不少于2个",
                source_text="接口：不少于2个",
            ),
        ]
    }
    evidence_bindings = {
        "1": [
            BidEvidenceBinding(
                package_id="1",
                requirement_id="pkg1-req-001",
                snippet="主机：1台",
                covers_requirement=True,
            ),
            BidEvidenceBinding(
                package_id="1",
                requirement_id="pkg1-req-002",
                snippet="接口：不少于2个",
                covers_requirement=True,
            ),
        ]
    }

    gate = compute_validation_gate(
        sections=sections,
        normalized_reqs=normalized_reqs,
        evidence_bindings=evidence_bindings,
        target_package_ids=["1"],
    )

    assert gate.snippet_truncation_count == 0


def test_generate_bid_sections_strict_mode_outputs_pending_draft_when_validation_still_fails() -> None:
    tender = _sample_tender(project_name="单包严格外发校验项目")
    raw_text = (
        "包1 流式细胞分析仪\n"
        "技术参数：激光器≥3；荧光通道≥11。\n"
        "交货期：合同签订后30日内。\n"
    )

    result = generate_bid_sections(
        tender,
        raw_text,
        llm=None,
        require_validation_pass=True,
    )

    combined = "\n".join(section.content for section in result.sections)
    assert result.draft_level.value == "internal_draft"
    assert "待补充" in combined
    assert "【内部草稿" in combined
