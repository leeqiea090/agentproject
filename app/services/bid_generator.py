"""投标文件生成Agent - 使用LangGraph编排多个子Agent"""
from typing import Any, TypedDict, Annotated
import operator
from datetime import datetime
import logging
import re

from langgraph.graph import StateGraph, END
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from app.schemas import (
    TenderDocument,
    CompanyProfile,
    ProductSpecification,
    BidDocumentSection,
    BidGenerateRequest,
    ClauseCategory,
)

logger = logging.getLogger(__name__)

# ── 条款分类关键词规则（与 requirement_processor 保持一致）──
_SERVICE_KEYWORDS = ("售后", "质保", "维修", "保修", "维护", "响应时间", "培训", "安装调试", "技术支持", "巡检")
_DOC_KEYWORDS = ("说明书", "手册", "合格证", "资料", "文件", "技术文档", "使用手册", "操作手册")
_ACCEPTANCE_KEYWORDS = ("验收", "检测", "测试", "试运行", "调试")
_CONFIG_KEYWORDS = ("配置", "配件", "附件", "装箱", "随机", "标配", "选配")

_MODEL_POLLUTION_PREFIXES = (
    "你是",
    "请生成",
    "输出JSON",
    "Markdown格式",
    "根据以上",
    "以下是",
    "as an ai",
)
_MODEL_POLLUTION_TOKENS = ("{{", "}}", "<!--", "-->", "```")
_MODEL_POLLUTION_INFIX_KEYWORDS = (
    "system:",
    "assistant:",
    "user:",
    "只允许输出",
    "输出格式",
    "返回json",
    "判定结果：",
    "原文长度",
    "debug:",
    "trace:",
)
_HARD_REQUIREMENT_MARKERS = ("≥", "≤", ">=", "<=", "不低于", "不少于", "不高于", "不大于", "至少")

# ── 内联条款分类 ──
_INTERNAL_DRAFT_MARKERS = ("待核实", "待补证", "待补充", "[TODO", "招标原文片段")
_EXTERNAL_FORBIDDEN_PATTERNS = re.compile(
    r"待核实|待补证|待补充|\[TODO|招标原文片段|\[待\w+\]"
)


def _classify_req_category(key: str, val: str) -> str:
    """对单个条款做简单分类，返回 ClauseCategory 值。"""
    combined = f"{key} {val}"
    if any(k in combined for k in _SERVICE_KEYWORDS):
        return "service_requirement"
    if any(k in combined for k in _DOC_KEYWORDS):
        return "documentation_requirement"
    if any(k in combined for k in _ACCEPTANCE_KEYWORDS):
        return "acceptance_requirement"
    if any(k in combined for k in _CONFIG_KEYWORDS):
        return "config_requirement"
    return "technical_requirement"


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        return "；".join(f"{k}：{_as_text(v)}" for k, v in value.items() if _as_text(v))
    if isinstance(value, list):
        return "；".join(_as_text(item) for item in value if _as_text(item))
    return str(value).strip()


def _sanitize_model_output(section_title: str, content: str) -> str:
    normalized = content.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "").strip()
    lines: list[str] = []
    for line in normalized.split("\n"):
        stripped = line.strip()
        lowered = stripped.lower()
        if stripped in {section_title, f"# {section_title}", f"## {section_title}"}:
            continue
        if any(token in stripped for token in _MODEL_POLLUTION_TOKENS):
            continue
        if any(lowered.startswith(prefix.lower()) for prefix in _MODEL_POLLUTION_PREFIXES):
            continue
        if any(keyword in lowered for keyword in _MODEL_POLLUTION_INFIX_KEYWORDS):
            continue
        if re.match(r"^(system|assistant|user)\s*[:：]", lowered):
            continue
        if re.match(r"^(好的|当然|以下|下面|请注意|温馨提示)[，,:：]", stripped):
            continue
        if re.search(r"(根据你|根据您).{0,8}(提供|输入)", stripped):
            continue
        lines.append(line.rstrip())
    cleaned = "\n".join(lines).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned


def _markdown_cell(text: Any) -> str:
    normalized = re.sub(r"\s+", " ", _as_text(text))
    return normalized.replace("|", "/")


def _build_requirement_response_value(req_text: str, matched_spec_value: str, *, product: ProductSpecification | None = None) -> str:
    if matched_spec_value:
        return matched_spec_value

    # 能力推断：如果条款含 "具备/支持" 类动词且有产品信息
    _CAP_MARKERS = ("具备", "支持", "提供", "配备", "配置", "满足", "可", "能够", "兼容")
    if product is not None and any(m in req_text for m in _CAP_MARKERS):
        return f"满足，投标产品（{product.product_name}）具备该功能"

    # 上下文兜底：产品信息充分时给出描述
    if product is not None and product.product_name.strip():
        specs = product.specifications or {}
        if len(specs) >= 3:
            mfr = _as_text(product.manufacturer) if product.manufacturer else ""
            return f"响应，详见投标产品（{mfr} {product.product_name}）技术偏离表"

    return "待核实（未匹配到已证实产品事实）"


def _first_numeric_value(value: str) -> float | None:
    match = re.search(r"-?\d+(?:\.\d+)?", _as_text(value))
    if not match:
        return None
    try:
        return float(match.group(0))
    except ValueError:
        return None


def _evaluate_deviation_status(req_text: str, matched_spec_value: str) -> str:
    requirement = _as_text(req_text)
    response = _as_text(matched_spec_value)
    if not response:
        return "待核实"

    for marker in _HARD_REQUIREMENT_MARKERS:
        if marker in requirement:
            threshold = _first_numeric_value(requirement)
            response_numeric = _first_numeric_value(response)
            if threshold is None or response_numeric is None:
                return "待核实"
            if marker in {"≥", ">=", "不低于", "不少于", "至少"}:
                return "无偏离" if response_numeric >= threshold else "有偏离"
            if marker in {"≤", "<=", "不高于", "不大于"}:
                return "无偏离" if response_numeric <= threshold else "有偏离"

    compact_requirement = re.sub(r"\s+", "", requirement)
    compact_response = re.sub(r"\s+", "", response)
    if compact_requirement and compact_response:
        if compact_requirement == compact_response or compact_response in compact_requirement or compact_requirement in compact_response:
            return "无偏离"
    return "待核实"


def _ensure_compliance_branch_blocks(content: str, allow_consortium: bool, requires_sme: bool) -> str:
    result = content.strip()
    if "联合体投标声明（分支选择）" not in result:
        branch_b_hint = "分支B适用时须附联合体协议书。" if allow_consortium else "分支B本项目不适用。"
        result += (
            "\n\n## 联合体投标声明（分支选择）\n"
            "请按投标组织形式勾选：\n"
            "- □ 分支A：独立投标，不组成联合体；\n"
            f"- □ 分支B：联合体投标，{branch_b_hint}\n"
        )

    if "企业类型声明函（分支选择）" not in result:
        _ = requires_sme
        result += (
            "\n\n## 企业类型声明函（分支选择）\n"
            "请按企业实际情况勾选并提交对应材料：\n"
            "### 分支A：中小企业声明函（货物/服务）\n"
            "□ 适用。\n"
            "### 分支B：监狱企业证明材料\n"
            "□ 适用。\n"
            "### 分支C：残疾人福利性单位声明函\n"
            "□ 适用。\n"
            "### 分支D：非中小企业声明\n"
            "□ 适用。\n"
        )

    return result


def _build_structured_technical_block(package: Any, product: ProductSpecification) -> str:
    """构建结构化技术响应块——技术偏离表只含技术参数，服务/资料/验收独立分表。"""
    tech_rows: list[str] = []
    service_rows: list[str] = []
    doc_rows: list[str] = []
    evidence_rows: list[str] = []
    tech_req = package.technical_requirements or {}
    product_specs = product.specifications or {}
    proven_count = 0
    no_deviation_count = 0

    if tech_req:
        tech_idx = 1
        svc_idx = 1
        doc_idx = 1
        for req_key, req_val in tech_req.items():
            req_name = _as_text(req_key) or "技术参数"
            req_text = _as_text(req_val) or "详见招标文件"

            # 分类
            cat = _classify_req_category(req_name, req_text)

            matched = ""
            matched_spec_key = ""
            for spec_key, spec_val in product_specs.items():
                sk = _as_text(spec_key)
                if req_name in sk or sk in req_name:
                    matched = _as_text(spec_val)
                    matched_spec_key = sk
                    break
            if not matched:
                req_tokens = [t for t in re.split(r"[，,、；;：:（）()\[\]\s/]+", req_name) if len(t) >= 3]
                if req_tokens:
                    for spec_key, spec_val in product_specs.items():
                        sk = _as_text(spec_key)
                        if sk and any(t in sk for t in req_tokens):
                            matched = _as_text(spec_val)
                            matched_spec_key = sk
                            break

            response_text = _build_requirement_response_value(req_text, matched, product=product)
            deviation_status = _evaluate_deviation_status(req_text, matched)
            if matched:
                proven_count += 1
            if deviation_status == "无偏离":
                no_deviation_count += 1

            evidence_text = (
                f"招标条款：{_markdown_cell(req_name)}={_markdown_cell(req_text)}；"
                f"产品参数：{_markdown_cell(matched_spec_key)}={_markdown_cell(matched)}"
                if matched
                else f"招标条款：{_markdown_cell(req_name)}={_markdown_cell(req_text)}；产品参数：未匹配到同名参数，需补充"
            )

            if cat in ("service_requirement", "acceptance_requirement"):
                service_rows.append(
                    f"| {svc_idx} | {_markdown_cell(req_name)} | {_markdown_cell(req_text)} | {_markdown_cell(response_text)} | {deviation_status} |"
                )
                svc_idx += 1
            elif cat == "documentation_requirement":
                doc_rows.append(
                    f"| {doc_idx} | {_markdown_cell(req_name)} | {_markdown_cell(req_text)} | {_markdown_cell(response_text)} |"
                )
                doc_idx += 1
            else:
                # technical_requirement / config_requirement → 技术偏离表
                tech_rows.append(
                    f"| {tech_idx} | {_markdown_cell(req_name)} | {_markdown_cell(req_text)} | {_markdown_cell(response_text)} | {deviation_status} | {_markdown_cell(evidence_text)} |"
                )
                evidence_rows.append(
                    f"| {tech_idx} | {_markdown_cell(req_name)} | 招标技术条款 | {_markdown_cell(evidence_text)} | 技术偏离表第{tech_idx}行 |"
                )
                tech_idx += 1
    else:
        tech_rows.append(
            "| 1 | 核心技术参数 | 详见招标文件 | 待核实（未提取到结构化技术要求） | 待核实 | 招标条款+产品参数待人工补充 |"
        )
        evidence_rows.append("| 1 | 核心技术参数 | 招标技术条款 | 暂未匹配到产品参数，需补充证据 | 技术偏离表第1行 |")

    total_rows = len(tech_rows)
    unresolved_count = max(0, total_rows - proven_count)

    # ── 技术偏离表（只含技术参数）──
    deviation_table = "\n".join(
        [
            "#### 1) 技术偏离表",
            "| 序号 | 参数项 | 招标要求 | 投标产品响应参数 | 偏离说明 | 证据映射 |",
            "|---:|---|---|---|---|---|",
            *tech_rows,
        ]
    )

    config_table = "\n".join(
        [
            "#### 2) 配置清单",
            "| 序号 | 配置项 | 说明 |",
            "|---:|---|---|",
            f"| 1 | 产品名称 | {product.product_name} |",
            f"| 2 | 品牌/厂家 | {product.manufacturer} |",
            f"| 3 | 型号 | {product.model or '[待补充]'} |",
            f"| 4 | 产地 | {product.origin or '[待补充]'} |",
            f"| 5 | 参考价格(元) | {product.price:.2f} |",
        ]
    )

    checklist = "\n".join(
        [
            "#### 3) 响应校验清单",
            "| 序号 | 校验项 | 结论 |",
            "|---:|---|---|",
            f"| 1 | 技术参数逐条对照 | 已证实 {proven_count}/{total_rows} 项 |",
            f"| 2 | 产品信息完整性 | {'已完成' if product.model and product.manufacturer else '待补充'} |",
            f"| 3 | 偏离项披露 | 仅 {no_deviation_count} 项已证实可标注无偏离；其余 {max(0, total_rows - no_deviation_count)} 项保持待核实/有偏离 |",
            f"| 4 | 证据映射完整性 | 已形成 {len(evidence_rows)} 条映射记录；待补证 {unresolved_count} 项 |",
        ]
    )

    evidence_table = "\n".join(
        [
            "#### 4) 技术条款证据映射表",
            "| 序号 | 参数项 | 证据来源 | 证据摘要 | 应用位置 |",
            "|---:|---|---|---|---|",
            *evidence_rows,
        ]
    )

    blocks = [
        f"### 包{package.package_id}：{package.item_name}",
        deviation_table,
        config_table,
    ]

    # ── 服务/验收要求独立分表 ──
    if service_rows:
        blocks.append("\n".join([
            "#### 5) 售后服务/验收要求响应表",
            "| 序号 | 要求项 | 招标要求 | 响应承诺 | 偏离说明 |",
            "|---:|---|---|---|---|",
            *service_rows,
        ]))

    # ── 资料要求独立分表 ──
    if doc_rows:
        blocks.append("\n".join([
            "#### 6) 资料/文档要求响应表",
            "| 序号 | 要求项 | 招标要求 | 响应承诺 |",
            "|---:|---|---|---|",
            *doc_rows,
        ]))

    blocks.extend([checklist, evidence_table])

    return "\n\n".join(blocks)


class BidGenerationState(TypedDict):
    """投标文件生成的状态"""
    # 输入
    tender_doc: TenderDocument
    company_profile: CompanyProfile
    products: dict[str, ProductSpecification]  # package_id -> product
    request: BidGenerateRequest

    # 中间结果
    sections: Annotated[list[BidDocumentSection], operator.add]
    current_section: str
    errors: Annotated[list[str], operator.add]

    # 输出
    bid_id: str
    status: str
    product_profiles: dict  # package_id -> ProductProfile dict


class BidGeneratorAgent:
    """投标文件生成主Agent"""

    def __init__(self, llm: ChatOpenAI | None = None):
        """
        初始化投标文件生成Agent

        Args:
            llm: 语言模型实例
        """
        # 增加 max_tokens 预算,避免输出被截断
        self.llm = llm or ChatOpenAI(model="gpt-4o-mini", temperature=0.3, max_tokens=4096)
        self.graph = self._build_graph()

    def _build_graph(self) -> StateGraph:
        """构建LangGraph工作流"""
        workflow = StateGraph(BidGenerationState)

        # 添加节点（各个生成步骤）
        workflow.add_node("generate_qualification", self.generate_qualification_section)
        workflow.add_node("generate_compliance", self.generate_compliance_section)
        workflow.add_node("generate_technical", self.generate_technical_section)
        workflow.add_node("generate_commercial", self.generate_commercial_section)
        workflow.add_node("generate_service", self.generate_service_section)
        workflow.add_node("finalize", self.finalize_bid)

        # 定义流程
        workflow.set_entry_point("generate_qualification")
        workflow.add_edge("generate_qualification", "generate_compliance")
        workflow.add_edge("generate_compliance", "generate_technical")
        workflow.add_edge("generate_technical", "generate_commercial")
        workflow.add_edge("generate_commercial", "generate_service")
        workflow.add_edge("generate_service", "finalize")
        workflow.add_edge("finalize", END)

        return workflow.compile()

    # ============ 各章节生成函数 ============

    def generate_qualification_section(self, state: BidGenerationState) -> dict[str, Any]:
        """生成第一章：资格性证明文件"""
        logger.info("生成资格性证明文件章节")

        company = state["company_profile"]
        tender = state["tender_doc"]

        prompt = ChatPromptTemplate.from_messages([
            ("system", """你是一位专业的投标文件撰写专家。请根据企业信息生成"资格性证明文件"章节。

该章节通常包括：
1. 符合《中华人民共和国政府采购法》第二十二条规定的声明
2. 营业执照说明
3. 项目人员清单
4. 政府采购供应商资格承诺函
5. 社保缴纳证明说明
6. 信用查询说明（全国企业信用信息公示系统、中国执行信息公开网等）
7. 法定代表人授权书
8. 相关资质证件说明
9. 围标串标承诺函

要求：
- 语气正式、专业
- 格式规范，符合政府采购要求
- 突出企业资质齐全、信誉良好
- 每个部分用Markdown二级标题分隔"""),
            ("user", """企业信息：
- 企业名称：{company_name}
- 法定代表人：{legal_rep}
- 地址：{address}
- 电话：{phone}
- 资质证照数量：{license_count}

项目信息：
- 项目名称：{project_name}
- 项目编号：{project_number}

请生成"第一章 资格性证明文件"的内容（Markdown格式）：""")
        ])

        chain = prompt | self.llm
        response = chain.invoke({
            "company_name": company.name,
            "legal_rep": company.legal_representative,
            "address": company.address,
            "phone": company.phone,
            "license_count": len(company.licenses),
            "project_name": tender.project_name,
            "project_number": tender.project_number
        })

        section = BidDocumentSection(
            section_title="第一章 资格性证明文件",
            content=_sanitize_model_output("第一章 资格性证明文件", str(response.content)),
            attachments=[lic.file_path for lic in company.licenses if lic.file_path]
        )

        return {
            "sections": [section],
            "current_section": "qualification"
        }

    def generate_compliance_section(self, state: BidGenerationState) -> dict[str, Any]:
        """生成第二章：符合性承诺"""
        logger.info("生成符合性承诺章节")

        tender = state["tender_doc"]
        company = state["company_profile"]
        request = state["request"]
        context_text = " ".join(
            [
                tender.project_name,
                tender.special_requirements,
                " ".join(f"{k}{v}" for k, v in tender.evaluation_criteria.items()),
            ]
        )
        allow_consortium = ("联合体" in context_text and "不接受联合体" not in context_text)
        requires_sme = any(
            token in context_text
            for token in ("中小企业", "小微", "监狱企业", "残疾人福利性单位", "价格扣除")
        )

        prompt = ChatPromptTemplate.from_messages([
            ("system", """你是投标文件撰写专家。请生成"符合性承诺"章节。

该章节包括：
1. 投标报价承诺
2. 投标文件规范性、符合性承诺
3. 满足主要商务条款的承诺书
4. 联合体投标说明（通常为"非联合体"）
5. 技术部分实质性内容承诺
6. 其他要求承诺

每个承诺都要：
- 明确响应招标文件要求
- 格式规范
- 包含承诺人签字日期等格式要素"""),
            ("user", """项目信息：
- 项目名称：{project_name}
- 项目编号：{project_number}

商务条款：
- 付款方式：{payment_method}
- 投标有效期：{validity_period}
- 履约保证金：{performance_bond}

投标包号：{packages}

企业名称：{company_name}

声明函分支要求：
- 联合体分支：{consortium_branch}
- 企业类型声明分支：{enterprise_branch}

请生成"第二章 符合性承诺"的内容（Markdown格式），并明确输出分支化声明：""")
        ])

        chain = prompt | self.llm
        response = chain.invoke({
            "project_name": tender.project_name,
            "project_number": tender.project_number,
            "payment_method": tender.commercial_terms.payment_method,
            "validity_period": tender.commercial_terms.validity_period,
            "performance_bond": tender.commercial_terms.performance_bond,
            "packages": ", ".join(request.selected_packages),
            "company_name": company.name,
            "consortium_branch": "允许联合体（同时给出独立投标/联合体投标两种声明）" if allow_consortium else "不允许联合体（输出非联合体声明）",
            "enterprise_branch": "需提供中小企业/监狱企业/残疾人福利性单位分支声明" if requires_sme else "输出非中小企业声明",
        })

        cleaned_content = _sanitize_model_output("第二章 符合性承诺", str(response.content))
        ensured_content = _ensure_compliance_branch_blocks(
            content=cleaned_content,
            allow_consortium=allow_consortium,
            requires_sme=requires_sme,
        )
        section = BidDocumentSection(
            section_title="第二章 符合性承诺",
            content=ensured_content
        )

        return {
            "sections": [section],
            "current_section": "compliance"
        }

    def generate_technical_section(self, state: BidGenerationState) -> dict[str, Any]:
        """生成第三章：商务及技术部分（分包+分阶段生成，避免输出过载）"""
        logger.info("生成商务及技术部分章节")

        tender = state["tender_doc"]
        products = state["products"]
        request = state["request"]

        # 对每个投标包生成技术响应（分包生成，每包独立调用）
        technical_responses = []

        for package_id in request.selected_packages:
            # 找到对应的采购包
            package = next((p for p in tender.packages if p.package_id == package_id), None)
            if not package:
                logger.warning("包隔离检查：package_id=%s 不存在于 tender.packages", package_id)
                continue

            # 找到对应的产品
            product = products.get(package_id)
            if not product:
                logger.warning("包隔离检查：package_id=%s 无对应产品", package_id)
                continue

            # 分阶段生成：1) 表格 2) 说明文字
            # 阶段1: 生成技术偏离表和配置清单（结构化，不需要LLM）
            structured_block = _build_structured_technical_block(package, product)

            # 阶段2: 生成详细技术说明（使用独立的LLM调用，提高输出预算）
            detailed_explanation = self._generate_technical_explanation(package, product)

            # 合并两部分
            combined_response = f"{structured_block}\n\n{detailed_explanation}"
            technical_responses.append(combined_response)

        if technical_responses:
            full_content = "\n\n".join(technical_responses)
        else:
            full_content = (
                "### 技术响应总览\n\n"
                "| 序号 | 校验项 | 结论 |\n"
                "|---:|---|---|\n"
                "| 1 | 目标包号是否匹配 | 未找到可用的包号与产品映射，请检查 selected_packages 与 product_ids |"
            )

        section = BidDocumentSection(
            section_title="第三章 商务及技术部分",
            content=full_content
        )

        return {
            "sections": [section],
            "current_section": "technical"
        }

    def _generate_technical_explanation(self, package: Any, product: Any) -> str:
        """为单个包生成详细技术说明（独立LLM调用，更高token预算）"""
        prompt = ChatPromptTemplate.from_messages([
            ("system", """你是技术文档撰写专家。请为该产品生成详细的技术说明文字。

要求：
- 详细说明产品的关键性能特点
- 解释产品如何满足招标技术要求
- 突出技术优势和创新点
- 说明技术配置的合理性
- 每个关键技术点至少2-3句话说明
- 输出应为完整的段落文字，不是表格
- 字数不少于300字"""),
            ("user", """包信息：
- 包号：{package_id}
- 货物名称：{item_name}

产品信息：
- 产品名称：{product_name}
- 厂家：{manufacturer}
- 型号：{model}

技术要求：
{tech_requirements}

产品规格：
{product_specs}

请生成详细的技术说明文字（Markdown格式）：""")
        ])

        tech_req_text = "\n".join(
            f"- {k}: {v}"
            for k, v in (package.technical_requirements or {}).items()
        ) or "详见招标文件"

        product_specs_text = "\n".join(
            f"- {k}: {v}"
            for k, v in (product.specifications or {}).items()
        ) or "暂无详细规格"

        chain = prompt | self.llm
        response = chain.invoke({
            "package_id": package.package_id,
            "item_name": package.item_name,
            "product_name": product.product_name,
            "manufacturer": product.manufacturer or "待补充",
            "model": product.model or "待补充",
            "tech_requirements": tech_req_text,
            "product_specs": product_specs_text,
        })

        return f"#### 包{package.package_id} 详细技术说明\n\n{_sanitize_model_output('详细技术说明', str(response.content))}"

    def generate_commercial_section(self, state: BidGenerationState) -> dict[str, Any]:
        """生成报价部分（包含在第三章或第四章）"""
        logger.info("生成商务报价部分")

        tender = state["tender_doc"]
        products = state["products"]
        request = state["request"]

        # 计算报价
        quotations = []
        total_price = 0.0

        for package_id in request.selected_packages:
            package = next((p for p in tender.packages if p.package_id == package_id), None)
            product = products.get(package_id)

            if package and product:
                unit_price = product.price * request.discount_rate
                subtotal = unit_price * package.quantity
                total_price += subtotal

                quotations.append({
                    "package_id": package_id,
                    "item_name": package.item_name,
                    "quantity": package.quantity,
                    "unit_price": unit_price,
                    "subtotal": subtotal
                })

        # 生成报价表格
        prompt = ChatPromptTemplate.from_messages([
            ("system", """你是财务报价专家。请根据报价数据生成标准的投标报价书和报价一览表。

要求：
- 使用表格格式（Markdown）
- 价格精确到小数点后2位
- 包含：包号、货物名称、数量、单价、小计
- 最后一行是"合计"
- 大写金额：人民币XXX万XXX千XXX元整"""),
            ("user", """报价数据：
{quotations}

总价：{total_price}元

请生成报价书和报价一览表（Markdown格式）：""")
        ])

        chain = prompt | self.llm
        response = chain.invoke({
            "quotations": str(quotations),
            "total_price": total_price
        })

        section = BidDocumentSection(
            section_title="报价书及报价一览表",
            content=_sanitize_model_output("报价书及报价一览表", str(response.content))
        )

        return {
            "sections": [section],
            "current_section": "commercial"
        }

    def generate_service_section(self, state: BidGenerationState) -> dict[str, Any]:
        """生成售后服务方案"""
        logger.info("生成售后服务方案章节")

        tender = state["tender_doc"]
        request = state["request"]
        custom_plan = request.custom_service_plan

        prompt = ChatPromptTemplate.from_messages([
            ("system", """你是医疗设备售后服务方案专家。请生成详细的售后服务承诺。

内容包括：
1. 技术服务
   - 安装调试
   - 人员培训
   - 技术咨询
2. 售后服务
   - 质保期承诺
   - 响应时间
   - 维修保养计划
   - 备件供应
   - 应急预案

要求：
- 专业、详细
- 承诺具体、可执行
- 突出服务优势"""),
            ("user", """项目信息：
- 项目名称：{project_name}
- 设备类型：{equipment_types}

{custom_instruction}

请生成"技术服务和售后服务方案"（Markdown格式）：""")
        ])

        equipment_types = ", ".join([
            p.item_name for p in tender.packages
            if p.package_id in request.selected_packages
        ])

        custom_instruction = f"特别要求：{custom_plan}" if custom_plan else "请使用标准服务方案"

        chain = prompt | self.llm
        response = chain.invoke({
            "project_name": tender.project_name,
            "equipment_types": equipment_types,
            "custom_instruction": custom_instruction
        })

        section = BidDocumentSection(
            section_title="第四章 技术服务和售后服务",
            content=_sanitize_model_output("第四章 技术服务和售后服务", str(response.content))
        )

        return {
            "sections": [section],
            "current_section": "service"
        }

    def finalize_bid(self, state: BidGenerationState) -> dict[str, Any]:
        """完成投标文件生成，添加目录和封面。

        同时生成 internal（母版，允许待核实/待补证）和 external（可外发）两个版本。
        """
        logger.info("完成投标文件组装")

        tender = state["tender_doc"]
        company = state["company_profile"]

        # 生成封面
        cover_content = f"""# 政府采购响应文件

## 项目名称：{tender.project_name}

## 项目编号：{tender.project_number}

---

**供应商全称**：{company.name}（公章）

**授权代表**：{company.legal_representative}

**电话**：{company.phone}

**日期**：{datetime.now().strftime('%Y年%m月%d日')}

---
"""

        cover_section = BidDocumentSection(
            section_title="封面",
            content=cover_content
        )

        # 生成目录
        toc_lines = ["# 目录\n"]
        for i, section in enumerate(state["sections"], 1):
            toc_lines.append(f"{i}. {section.section_title}")

        toc_section = BidDocumentSection(
            section_title="目录",
            content="\n".join(toc_lines)
        )

        return {
            "sections": [cover_section, toc_section],
            "status": "completed",
            "bid_id": f"BID_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        }

    def generate(self, state: BidGenerationState) -> BidGenerationState:
        """
        执行投标文件生成

        Args:
            state: 初始状态

        Returns:
            最终状态
        """
        try:
            final_state = self.graph.invoke(state)
            logger.info(f"投标文件生成完成，共 {len(final_state['sections'])} 个章节")
            return final_state
        except Exception as e:
            logger.error(f"投标文件生成失败: {str(e)}")
            raise


def create_bid_generator(llm: ChatOpenAI | None = None) -> BidGeneratorAgent:
    """
    工厂函数：创建投标文件生成Agent

    Args:
        llm: 可选的LLM实例

    Returns:
        BidGeneratorAgent实例
    """
    return BidGeneratorAgent(llm)
