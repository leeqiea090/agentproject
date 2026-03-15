from __future__ import annotations

from app.schemas import BidDocumentSection, BidEvidenceBinding, ClauseCategory, CommercialTerms, NormalizedRequirement, ProcurementPackage, ProductSpecification, TenderDocument
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
from app.services.one_click_generator.format_driven_sections.common import _build_pkg_deviation_table
from app.services.one_click_generator.pipeline import _pack_normalized_result
from app.services.one_click_generator.response_tables import _build_deviation_table
from app.services.evidence_binder import _extract_evidence_snippet
from app.services.one_click_generator.config_tables import _build_main_parameter_table
from app.services.requirement_processor import _classify_clause_category, _extract_package_scope_text
from app.services.tender_parser import TenderParser


def _sample_tender(
    project_name: str = "检验设备采购项目",
    special_requirements: str = "本项目按中小企业政策执行，允许联合体投标。",
) -> TenderDocument:
    """构造测试用的招标文档样例。"""
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
    """测试模板pollutionguardremovespromptartifacts。"""
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
    """测试中小企业policy的enterprisedeclarationbranching。"""
    tender = _sample_tender()
    block = _build_enterprise_declaration_block(tender, "2026年03月09日")
    assert "企业类型声明函（分支选择）" in block
    assert "分支A：中小企业声明函" in block
    assert "分支B：监狱企业证明材料" in block
    assert "分支C：残疾人福利性单位声明函" in block
    assert "判定结果" not in block


def test_enterprise_declaration_keeps_branches_without_sme_policy() -> None:
    """测试enterprisedeclarationkeepsbrancheswithout中小企业policy。"""
    tender = _sample_tender(
        project_name="通用设备采购项目",
        special_requirements="按采购文件执行，不涉及政策加分。",
    )
    block = _build_enterprise_declaration_block(tender, "2026年03月09日")
    assert "企业类型声明函（分支选择）" in block
    assert "分支D：非中小企业声明" in block
    assert "判定结果" not in block


def test_technical_section_is_forced_structured() -> None:
    """测试技术章节isforcedstructured。"""
    tender = _sample_tender(special_requirements="本项目不接受联合体。")
    section = _gen_technical(llm=None, tender=tender, tender_raw="raw text")
    assert "### 包1：" in section.content
    assert "技术偏离及详细配置明细表" in section.content
    assert "详细配置明细表" in section.content
    assert "技术响应检查清单" not in section.content
    assert "技术条款证据映射表" not in section.content
    assert "（第1包）" not in section.content
    assert "具体参数待填写" not in section.content
    assert "招标原文长度" not in section.content


def test_qualification_section_uses_detected_region_title() -> None:
    """测试资格审查章节usesdetected地区标题。"""
    tender = _sample_tender(project_name="吉林大学中日联谊医院流式细胞仪采购项目")
    section = _gen_qualification(llm=None, tender=tender)
    assert "吉林省政府采购供应商资格承诺函" in section.content
    assert "黑龙江省政府采购供应商资格承诺函" not in section.content


def test_qualification_section_prefers_purchaser_region_over_agency_region() -> None:
    """测试资格审查章节preferspurchaser地区overagency地区。"""
    tender = _sample_tender(project_name="流式细胞仪采购项目")
    tender.purchaser = "吉林大学中日联谊医院"
    tender.agency = "北京某招标有限公司"

    section = _gen_qualification(llm=None, tender=tender)

    assert "吉林省政府采购供应商资格承诺函" in section.content
    assert "北京市政府采购供应商资格承诺函" not in section.content


def test_generated_sections_are_word_friendly_without_raw_markdown_markers() -> None:
    """测试generated章节arewordfriendlywithoutrawmarkdownmarkers。"""
    tender = _sample_tender()
    technical = _gen_technical(llm=None, tender=tender, tender_raw="技术参数：激光器≥3；荧光通道≥11。")
    appendix = _gen_appendix(llm=None, tender=tender, tender_raw="技术参数：激光器≥3；荧光通道≥11。")

    assert "#### " not in technical.content
    assert "\n> " not in technical.content
    assert "#### " not in appendix.content
    assert "节能/环保/能效认证证书（如适用）" in appendix.content


def test_technical_section_can_fallback_to_raw_text_requirements() -> None:
    """测试技术章节canfallbackto原始文本需求。"""
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
    """测试技术fallbackfiltersoutscoringandcontract噪声。"""
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
    """测试符合性审查termsnormalizecontractplaceholders。"""
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
    """测试参数表格中的appendixfilters文档格式噪声。"""
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
    """测试appendix付款sentenceavoidsduplicatecontract条款。"""
    tender = _sample_tender()
    tender.commercial_terms.payment_method = "按招标文件及合同约定执行"

    section = _gen_appendix(llm=None, tender=tender, tender_raw="技术参数：激光器：≥3。")

    assert "付款方式按招标文件及合同约定执行。" in section.content
    assert "付款方式按“按招标文件及合同约定执行”及合同约定执行。" not in section.content


def test_effective_requirements_split_composite_technical_parameters() -> None:
    """测试有效需求切分composite技术parameters。"""
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


def test_effective_requirements_prefers_complete_raw_values_over_dirty_parser_values() -> None:
    """测试有效需求preferscompleteraw值over脏解析器值。"""
    pkg = ProcurementPackage(
        package_id="5",
        item_name="全自动化学发光免疫分析仪（2025357）",
        quantity=1,
        budget=48000.0,
        technical_requirements={
            "加样针": "、加样针",
            "加样系统": "或样本针加样需双重清洗",
        },
        delivery_time="签订合同后15个工作日送达指定地点",
        delivery_place="采购人指定地点",
    )

    requirements = dict(
        _effective_requirements(
            pkg,
            tender_raw=(
                "合同包 5（全自动化学发光免疫分析仪（2025357））\n"
                "三、技术参数：\n"
                "7、加样系统：一次性Tip加样；或样本针加样需双重清洗。\n"
                "8、加样针：≥3针。\n"
                "四、装箱配置单：\n"
                "五、质保：进口一年，国产三年。\n"
            ),
        )
    )

    assert requirements["加样针"] == "≥3针"
    assert requirements["加样系统"] == "一次性Tip加样；或样本针加样需双重清洗"


def test_requirement_rows_bind_evidence_within_same_package_scope() -> None:
    """测试需求行绑定证据withinsame包件范围。"""
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
    """测试模板的configuration表格uses有效configuration项instead。"""
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
    """测试需求行keep响应值待补until投标侧事实arebound。"""
    tender = _sample_tender()
    pkg = tender.packages[0]
    pkg.technical_requirements = {"激光器": "≥3", "荧光通道": "≥11"}

    rows, _ = _build_requirement_rows(pkg, tender_raw="")

    response_map = {row["key"]: row["response"] for row in rows}
    assert response_map["激光器"] == "待核实（需填入投标产品实参）"
    assert response_map["荧光通道"] == "待核实（需填入投标产品实参）"
    assert all("承诺满足" not in row["response"] for row in rows)


def test_requirement_rows_can_consume_structured_workflow_matches() -> None:
    """测试需求行canconsumestructured工作流matches。"""
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


def test_pkg_deviation_table_exposes_missing_tech_points_instead_of_fake_fallback() -> None:
    """测试fakefallback的包件偏离表exposesmissing技术要点instead。"""
    tender = _sample_tender()
    pkg = tender.packages[0]

    table = _build_pkg_deviation_table(tender, pkg, tender_raw="仅有项目概况，没有技术参数章节")

    assert "【待人工根据采购文件逐条补录技术参数，禁止仅写“响应/完全响应”】" in table
    assert "详见采购文件技术要求" not in table


def test_generate_bid_sections_forwards_structured_payload_to_format_generator() -> None:
    """测试生成投标章节forwardsstructuredpayloadto格式生成器。"""
    tender = _sample_tender().model_copy(
        update={
            "project_number": "[TP]XJ-2026-001",
            "procurement_type": "竞争性谈判",
        }
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

    result = generate_bid_sections(
        tender,
        "竞争性谈判文件\n合同包 1（流式细胞分析仪）\n三、技术参数：\n1、激光器：≥3\n",
        llm=None,
        products={"1": product},
        normalized_result={
            "technical_requirements": [
                {
                    "requirement_id": "wf-1",
                    "package_id": "1",
                    "param_name": "激光器",
                    "normalized_value": "≥3",
                    "source_text": "激光器：≥3",
                }
            ]
        },
        evidence_result={
            "technical_matches": [
                {
                    "requirement_id": "wf-1",
                    "package_id": "1",
                    "parameter_name": "激光器",
                    "response_value": "3个独立激光器",
                    "deviation_status": "无偏离",
                    "bid_evidence_file": "产品彩页.pdf",
                    "bid_evidence_type": "产品规格",
                }
            ]
        },
        product_profiles={
            "1": {
                "technical_specs": {"激光器": "3个独立激光器"},
            }
        },
        selected_packages=["1"],
    )

    deviation_section = next(section for section in result.sections if section.section_title == "五、技术偏离及详细配置明细表")
    assert "3个独立激光器" in deviation_section.content
    assert "详见采购文件技术要求" not in deviation_section.content


def test_pack_normalized_result_keeps_full_semantic_value_instead_of_bare_threshold() -> None:
    """测试bare阈值的打包归一化结果keepsfullsemantic值instead。"""
    packed = _pack_normalized_result(
        {
            "1": [
                NormalizedRequirement(
                    package_id="1",
                    requirement_id="pkg1-req-001",
                    param_name="稳定电压范围",
                    operator="=",
                    threshold="50",
                    unit="V",
                    raw_text="稳定电压范围：至少包含50-600V范围电压",
                    source_text="稳定电压范围：至少包含50-600V范围电压",
                )
            ]
        }
    )

    assert packed["technical_requirements"][0]["normalized_value"] == "至少包含50-600V范围电压"


def test_pack_normalized_result_falls_back_to_source_text_when_raw_value_is_thin() -> None:
    """测试打包归一化结果fallsbacktosource文本whenraw值isthin。"""
    packed = _pack_normalized_result(
        {
            "1": [
                NormalizedRequirement(
                    package_id="1",
                    requirement_id="pkg1-req-002",
                    param_name="维修响应速度",
                    operator="=",
                    threshold="0.5",
                    unit="小时",
                    raw_text="维修响应速度：0.5",
                    source_text="维修响应速度：要求本地设有厂家技术服务人员，接到通知后0.5小时内响应",
                )
            ]
        }
    )

    assert packed["technical_requirements"][0]["normalized_value"] == "要求本地设有厂家技术服务人员，接到通知后0.5小时内响应"


def test_pack_normalized_result_serializes_clause_category_enum_to_plain_value() -> None:
    """测试打包归一化结果会把 ClauseCategory 枚举序列化成裸字符串。"""
    packed = _pack_normalized_result(
        {
            "1": [
                NormalizedRequirement(
                    package_id="1",
                    requirement_id="pkg1-req-003",
                    param_name="售后服务",
                    raw_text="售后服务：质保期5年",
                    source_text="售后服务：质保期5年",
                    category=ClauseCategory.service_requirement,
                )
            ]
        }
    )

    assert packed["technical_requirements"][0]["category"] == "service_requirement"


def test_clause_classifier_keeps_after_sales_lis_requirement_out_of_technical_table() -> None:
    """测试含 LIS 关键词的售后要求不会被误判成技术条款。"""
    category = _classify_clause_category("售后服务要求", "承担双向LIS数据传输费用")
    assert category == ClauseCategory.service_requirement


def test_clause_classifier_treats_plain_upgrade_clause_as_service_requirement() -> None:
    """测试“升级/免费升级”短条款归为服务要求，不污染技术表。"""
    category = _classify_clause_category("4.9 升级", "免费升级")
    assert category == ClauseCategory.service_requirement


def test_generate_bid_sections_excludes_enum_categorized_service_rows_from_technical_table() -> None:
    """测试生成章节时，枚举分类的售后/合规条款不会污染技术表，售后条款进入服务章节。"""
    tender = TenderDocument(
        project_name="手术用头架、X射线血液辐照设备(二次)",
        project_number="[230001]wdzb[CS]20250014-1",
        budget=2145000.0,
        purchaser="某医院",
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
            )
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

    result = generate_bid_sections(
        tender,
        (
            "合同包1（X射线血液辐照设备）\n"
            "3 技术参数与性能要求\n"
            "3.1 辐照杯中心剂量率：≥5Gy/min\n"
            "4 售后服务\n"
            "4.1 质保期：5年\n"
        ),
        llm=None,
        normalized_result={
            "technical_requirements": [
                {
                    "requirement_id": "tech-1",
                    "package_id": "1",
                    "param_name": "辐照杯中心剂量率",
                    "normalized_value": "≥5Gy/min",
                    "raw_text": "辐照杯中心剂量率：≥5Gy/min",
                    "source_text": "辐照杯中心剂量率：≥5Gy/min",
                    "category": ClauseCategory.technical_requirement,
                },
                {
                    "requirement_id": "svc-1",
                    "package_id": "1",
                    "param_name": "售后服务",
                    "normalized_value": "质保期：5年",
                    "raw_text": "售后服务：质保期：5年",
                    "source_text": "售后服务：质保期：5年",
                    "category": ClauseCategory.service_requirement,
                },
                {
                    "requirement_id": "cmp-1",
                    "package_id": "1",
                    "param_name": "实质性条款说明",
                    "normalized_value": "若有任何一条负偏离或不满足则导致响应无效",
                    "raw_text": "实质性条款说明：若有任何一条负偏离或不满足则导致响应无效",
                    "source_text": "实质性条款说明：若有任何一条负偏离或不满足则导致响应无效",
                    "category": ClauseCategory.compliance_note,
                },
            ]
        },
    )

    deviation_section = next(section for section in result.sections if section.section_title == "四、技术偏离及详细配置明细表")
    service_section = next(section for section in result.sections if section.section_title == "五、技术服务和售后服务的内容及措施")

    assert "辐照杯中心剂量率" in deviation_section.content
    assert "售后服务" not in deviation_section.content
    assert "实质性条款说明" not in deviation_section.content
    assert "质保期：5年" in service_section.content
    assert "辐照杯中心剂量率" not in service_section.content


def test_requirement_rows_fall_back_to_raw_atomic_items_when_structured_rows_are_too_coarse() -> None:
    """测试结构化结果过粗时，技术表会回退原文补齐，避免只剩空壳骨架。"""
    pkg = ProcurementPackage(
        package_id="1",
        item_name="手术用头架",
        quantity=1,
        budget=100000.0,
        technical_requirements={
            "固定方式": "三钉式固定，三钉按等腰三角形分布，三钉同步对头部加压，固定稳固",
            "三钉压力分布": "三钉压力分布均匀，都具有压力指示线，可以单独调节全部头钉压力",
            "双钉侧调整": "双钉侧可灵活调整，锁紧后无晃动，保证受力均衡",
            "连接器旋转": "连接器可以360度灵活旋转，方便和头夹连接",
        },
        delivery_time="合同签订后90个日历日内交货",
        delivery_place="甲方指定地点",
    )
    tender_raw = (
        "包1 手术用头架\n"
        "3 技术参数与性能要求（一行只写一个方面）\n"
        "3.1 固定方式：三钉式固定，三钉按等腰三角形分布，三钉同步对头部加压，固定稳固。\n"
        "3.2 压力调节：三钉压力分布均匀，都具有压力指示线，可以单独调节全部头钉压力。\n"
        "3.3 双钉侧调整：双钉侧可灵活调整，锁紧后无晃动，保证受力均衡。\n"
        "3.4 连接器旋转：连接器可以360度灵活旋转，方便和头夹连接。\n"
    )

    rows, total = _build_requirement_rows(
        pkg,
        tender_raw,
        normalized_result={
            "technical_requirements": [
                {
                    "requirement_id": "pkg1-req-001",
                    "package_id": "1",
                    "param_name": "技术参数与性能要求",
                    "normalized_value": "3.1 固定方式：三钉式固定，三钉按等腰三角形分布，三钉同步对头部加压，固定稳固",
                    "category": "technical_requirement",
                },
                {
                    "requirement_id": "pkg1-req-002",
                    "package_id": "1",
                    "param_name": "是否进口",
                    "normalized_value": "是",
                    "category": "technical_requirement",
                },
            ]
        },
    )

    keys = {row["key"] for row in rows}
    assert "技术参数与性能要求" not in keys
    assert "固定方式" in keys
    assert "三钉压力分布" in keys or "压力调节" in keys
    assert "连接器旋转" in keys
    assert total >= len(rows) >= 4


def test_fallback_requirement_rows_filter_after_sales_and_compliance_from_technical_table() -> None:
    """测试原文兜底技术表不会混入售后条款和实质性条款说明。"""
    pkg = ProcurementPackage(
        package_id="1",
        item_name="全自动化学发光免疫分析仪",
        quantity=1,
        budget=100000.0,
        technical_requirements={},
        delivery_time="合同签订后30个日历日内交货",
        delivery_place="甲方指定地点",
    )
    tender_raw = (
        "包1 全自动化学发光免疫分析仪\n"
        "3 技术参数与性能要求\n"
        "3.1 检测速度：≥200测试/小时。\n"
        "3.2 样本位：≥100个。\n"
        "4 售后服务要求\n"
        "4.1 承担双向LIS数据传输费用。\n"
        "4.2 质保期：5年。\n"
        "4.9 升级：免费升级。\n"
        "5 实质性条款说明：若有任何一条负偏离或不满足则导致响应无效。\n"
    )

    rows, total = _build_requirement_rows(pkg, tender_raw)

    combined_rows = [f"{row['key']} {row['requirement']}" for row in rows]
    assert any("检测速度" in text for text in combined_rows)
    assert any("样本位" in text for text in combined_rows)
    assert all("售后" not in text for text in combined_rows)
    assert all("LIS数据传输费用" not in text for text in combined_rows)
    assert all("质保期" not in text for text in combined_rows)
    assert all("升级" not in text for text in combined_rows)
    assert all("实质性条款" not in text for text in combined_rows)
    assert total >= len(rows) >= 2


def test_deviation_table_treats_string_none_as_unfilled_response() -> None:
    """测试偏离表treatsstringnoneasunfilled响应。"""
    tender = _sample_tender(project_name="检验科购置全自动电泳仪等设备")
    pkg = tender.packages[0]

    table = _build_deviation_table(
        tender,
        pkg,
        requirement_rows=[
            {
                "key": "稳定电压范围",
                "requirement": "至少包含50-600V范围电压",
                "response": "None",
                "deviation_status": "无偏离",
            }
        ],
        total_requirements=1,
        product=None,
    )

    assert "| 序号 | 技术参数项 | 采购文件技术要求 | 响应文件响应情况 | 偏离情况 |" in table
    assert "当前仅依据采购文件展开技术条款" in table
    assert "None" not in table
    assert "【待填写：品牌/型号/规格/配置及逐条响应】" in table
    assert "【待填写：无偏离/正偏离/负偏离】" in table


def test_generated_sections_do_not_use_commitment_sentences_as_response_values() -> None:
    """测试generated章节donotuse承诺sentencesas响应值。"""
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
    """测试包件范围中的明细报价表格prefers数量inferred。"""
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
    """测试rawconfiguration章节中的configuration表格canextract项。"""
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
    """测试configuration表格canuse产品画像配置项。"""
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


def test_configuration_table_prefers_pkg_quantity_over_raw_guess() -> None:
    """测试configuration表格prefers包件数量overrawguess。"""
    pkg = ProcurementPackage(
        package_id="6",
        item_name="进口流式细胞分析仪（2025349）",
        quantity=1,
        budget=2000000.0,
        technical_requirements={},
        delivery_time="合同签订后30日内",
        delivery_place="采购人指定地点",
    )
    product = ProductSpecification(
        product_name="进口流式细胞分析仪（2025349）",
        manufacturer="某厂家",
        model="FCM-9000",
        origin="美国",
        specifications={},
        price=0.0,
    )

    table = _build_configuration_table(
        pkg,
        tender_raw="第6包 进口流式细胞分析仪（2025349） 数量：2",
        product=product,
    )

    assert "| 1 | 进口流式细胞分析仪（2025349）主机 | 台 | 1 | 是 | 核心设备主机 |" in table
    assert "| 1 | 进口流式细胞分析仪（2025349）主机 | 台 | 2 | 是 |" not in table


def test_extract_package_scope_text_skips_multi_package_summary_lines() -> None:
    """测试extract包件范围文本skipsmulti包件汇总行。"""
    pkg = ProcurementPackage(
        package_id="1",
        item_name="进口全自动电泳仪（2025342）",
        quantity=1,
        budget=100000.0,
        technical_requirements={},
        delivery_time="",
        delivery_place="",
    )
    tender_raw = (
        "投标范围：包1、包2、包3\n"
        "包1：进口全自动电泳仪（2025342）\n"
        "技术参数：\n"
        "检测原理：琼脂凝胶电泳法\n"
        "包2：进口荧光显微镜（2025344）\n"
        "技术参数：\n"
        "照明系统：柯勒照明\n"
    )

    scope = _extract_package_scope_text(
        pkg,
        tender_raw,
        other_package_names=("进口荧光显微镜（2025344）",),
    )

    assert "投标范围：包1、包2、包3" not in scope
    assert "检测原理：琼脂凝胶电泳法" in scope
    assert "照明系统：柯勒照明" not in scope


def test_evidence_snippet_does_not_fallback_to_other_package_text() -> None:
    """测试证据片段doesnotfallbacktoother包件文本。"""
    source, quote, mapped = _extract_evidence_snippet(
        "包1 技术参数\n检测原理：琼脂凝胶电泳法\n",
        "照明系统",
        "柯勒照明",
        fallback_raw="包2 技术参数\n照明系统：柯勒照明\n",
    )

    assert source == "招标原文片段"
    assert not mapped
    assert quote == ""


def test_main_parameter_table_filters_out_non_technical_rows() -> None:
    """测试主参数表filtersoutnon技术行。"""
    tender = _sample_tender(project_name="检验科设备采购项目")
    pkg = tender.packages[0]

    table = _build_main_parameter_table(
        pkg,
        tender_raw="",
        normalized_result={
            "technical_requirements": [
                {
                    "package_id": "1",
                    "requirement_id": "pkg1-req-001",
                    "param_name": "激光器",
                    "normalized_value": "≥3",
                    "category": "technical_requirement",
                },
                {
                    "package_id": "1",
                    "requirement_id": "pkg1-req-002",
                    "param_name": "质保",
                    "normalized_value": "进口一年，国产三年",
                    "category": "service_requirement",
                },
                {
                    "package_id": "1",
                    "requirement_id": "pkg1-req-003",
                    "param_name": "装箱配置单",
                    "normalized_value": "详见招标文件",
                    "category": "config_requirement",
                },
            ]
        },
    )

    assert "激光器" in table
    assert "质保" not in table
    assert "装箱配置单" not in table


def test_effective_requirements_clean_dirty_value_and_replace_generic_config_placeholder() -> None:
    """测试有效需求清理脏值andreplacegeneric配置占位符。"""
    tender = _sample_tender()
    pkg = tender.packages[0]
    pkg.technical_requirements = {
        "加样针": "、加样针",
        "装箱配置单": "详见招标文件",
    }

    requirements = dict(_effective_requirements(pkg, tender_raw=""))

    assert requirements["加样针"] == "加样针"
    assert requirements["装箱配置单"] == "【待补充：配置清单】"


def test_requirement_rows_trim_evidence_before_configuration_and_complaint_sections() -> None:
    """测试需求行裁剪证据beforeconfigurationandcomplaint章节。"""
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
    """测试包件范围文本中的解析器cancorrect包件数量。"""
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
    """测试校验门禁doesnotflagmeaningfulshort参数名称astruncated。"""
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


def test_validation_gate_does_not_use_fixed_package_ids_for_device_pollution() -> None:
    """测试设备pollution的校验门禁doesnotusefixed包件 ID。"""
    tender = _sample_tender(project_name="流式细胞分析仪采购项目")
    sections = [
        BidDocumentSection(
            section_title="第三章 商务及技术部分",
            content=(
                "### 包1：流式细胞分析仪\n"
                "### （一）技术偏离及详细配置明细表\n"
                "| 条款编号 | 招标要求 | 投标型号 | 实际响应值 | 偏离情况 | 证据材料 | 页码 | 说明/验收备注 |\n"
                "|---|---|---|---|---|---|---|---|\n"
                "| 1.1 | 激光器：≥3 | X100 | 3个独立激光器 | 无偏离 | 产品参数 | 8 | 已匹配产品参数 |\n"
            ),
            attachments=[],
        )
    ]
    normalized_reqs = {
        "1": [
            NormalizedRequirement(
                package_id="1",
                requirement_id="pkg1-req-001",
                param_name="激光器",
                raw_text="激光器：≥3",
                source_text="激光器：≥3",
            )
        ]
    }

    gate = compute_validation_gate(
        sections=sections,
        normalized_reqs=normalized_reqs,
        evidence_bindings={},
        target_package_ids=["1"],
        tender=tender,
    )

    assert not gate.package_contamination_detected


def test_generate_bid_sections_strict_mode_outputs_pending_draft_when_validation_still_fails() -> None:
    """测试生成投标章节严格模式输出待补草稿when校验stillfails。"""
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
    assert "【内部草稿" in combined
    assert combined.count("【内部草稿") == 1
    assert "【待填写" in combined
    assert "[TODO:" not in combined
    assert "（此处留空，待上传" not in combined
    assert not any(section.section_title == "售后服务要求响应表" for section in result.sections)
