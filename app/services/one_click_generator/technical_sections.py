from __future__ import annotations

import logging
import re
from typing import Any

import app.services.one_click_generator.common as _common
import app.services.one_click_generator.table_builders as _table_builders
import app.services.one_click_generator.qualification_sections as _qualification_sections
import app.services.evidence_binder as _evidence_binder
import app.services.quality_gate as _quality_gate
import app.services.requirement_processor as _requirement_processor
from langchain_openai import ChatOpenAI

from app.schemas import (
    BidDocumentSection,
    ClauseCategory,
    NormalizedRequirement,
    ProcurementPackage,
    TenderDocument,
)
from app.services.one_click_generator.common import (
    _AUTHORIZED_REP,
    _COMPANY,
    _DETAIL_TARGETS,
    _PENDING_BIDDER_RESPONSE,
    _PHONE,
    _as_text,
    _infer_package_quantity,
    _normalize_commitment_term,
    _package_detail_lines,
    _package_scope,
    _quote_overview_table,
    _safe_text,
    _today,
)
from app.services.one_click_generator.config_tables import (
    _build_configuration_table,
    _build_evidence_mapping_table,
    _build_main_parameter_table,
    _build_response_checklist_table,
    _classify_config_item,
)
from app.services.one_click_generator.qualification_sections import _build_detail_quote_table
from app.services.one_click_generator.response_tables import (
    _build_deviation_table,
    _build_requirement_rows,
)
from app.services.requirement_processor import (
    _classify_clause_category,
    _is_bad_requirement_name,
    _is_bad_requirement_value,
    _markdown_cell,
    _package_forbidden_terms,
)

logger = logging.getLogger(__name__)



def _collapse_repeated_nontech_block(
    block_type: str,
    pkg_id: str,
    lines: list[str],
    memo: dict[str, dict[str, str]],
) -> str:
    """
    内部可编辑底稿模式：
    不折叠重复分表；每个包都输出完整表格。
    这样人工审核时可以直接在当前包内修改，不需要来回找上一包。
    """
    return "\n".join(lines) if lines else ""

def _canonicalize_clause_text(text: str) -> str:
    t = _safe_text(text, "")
    t = re.sub(r"^(实质性条款|重要条款|一般条款)[:：]\s*", "", t)
    t = t.replace("★", "").replace("▲", "").replace("■", "")
    t = re.sub(r"^\d+(?:\.\d+)*[:：]?\s*", "", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def _row_signature(key: str, req: str) -> str:
    """
    去重签名优先使用 requirement 文本。
    这样“检测性能：当SIT...<0.05%”和“重要条款：当SIT...<0.05%”会被视为同一条。
    """
    key_n = _canonicalize_clause_text(key)
    req_n = _canonicalize_clause_text(req)
    if req_n and len(req_n) >= 6:
        return req_n
    return f"{key_n}::{req_n}"

def _sanitize_rows_for_section(
    pkg: ProcurementPackage,
    rows: list[dict[str, Any]],
    *,
    expected_category: ClauseCategory | None = None,
) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    seen: set[str] = set()
    forbidden = _package_forbidden_terms(pkg.item_name)

    for row in rows:
        key = _safe_text(row.get("key"), "")
        req = _safe_text(row.get("requirement"), "")
        text = f"{key} {req}"

        if not key or not req:
            continue
        if _is_bad_requirement_name(key) or _is_bad_requirement_value(req):
            continue
        if forbidden and any(tok in text for tok in forbidden):
            continue

        inferred_cat = _classify_clause_category(key, req)
        if expected_category is not None and inferred_cat != expected_category:
            continue

        sig = _row_signature(key, req)
        if sig in seen:
            continue
        seen.add(sig)

        row = dict(row)
        row["key"] = _canonicalize_clause_text(key)
        row["requirement"] = _canonicalize_clause_text(req)
        row["category"] = inferred_cat.value
        cleaned.append(row)

    return cleaned



def __reexport_all(module) -> None:
    for name, value in vars(module).items():
        if name.startswith("__"):
            continue
        globals()[name] = value

for _module in (_common, _table_builders, _qualification_sections, _evidence_binder, _quality_gate, _requirement_processor,):
    __reexport_all(_module)

del _module
def _build_post_table_narratives(
    pkg: ProcurementPackage,
    tender: TenderDocument,
    tender_raw: str,
    product: Any = None,
    requirement_rows: list[dict[str, Any]] | None = None,
    *,
    draft_mode: str = "review",
) -> str:
    """生成表格后的详细技术响应说明章节。

    双模式 Section Writer:
    - draft_mode="internal": 允许待核实/待补证，用于内部审核
    - draft_mode="rich": 必须引用本包真实参数/配置/证据，围绕本包事实展开

    包含 5 个子章节：
    1. 关键性能说明
    2. 配置说明
    3. 交付说明
    4. 验收说明
    5. 使用与培训说明
    """
    requirement_rows = requirement_rows or []
    p_name = _as_text(getattr(product, "product_name", "")) if product else pkg.item_name
    p_model = _as_text(getattr(product, "model", "")) if product else ""
    p_mfr = _as_text(getattr(product, "manufacturer", "")) if product else ""
    p_brand = _as_text(getattr(product, "brand", "")) if product else ""
    specs = (getattr(product, "specifications", None) or {}) if product else {}
    config_items_raw = (getattr(product, "config_items", None) or []) if product else []
    evidence_refs = (getattr(product, "evidence_refs", None) or []) if product else []
    warranty = _normalize_commitment_term(tender.commercial_terms.warranty_period)
    delivery_time = ""
    delivery_place = ""
    if pkg.delivery_time:
        delivery_time = _safe_text(pkg.delivery_time, "按招标文件约定")
    if pkg.delivery_place:
        delivery_place = _safe_text(pkg.delivery_place, "采购人指定地点")

    is_rich = draft_mode == "rich"
    has_real_product = bool(p_model and p_mfr)

    sections: list[str] = []

    # ── 1. 关键性能说明 ──
    perf_lines = [
        "### （五）关键性能说明",
        "",
    ]
    if has_real_product:
        perf_lines.append(
            f"本包投标产品为{p_mfr} {p_name}（型号：{p_model}），"
            f"品牌：{p_brand or p_mfr}，"
            f"针对采购文件技术要求逐项响应如下："
        )
    else:
        perf_lines.append(
            f"本包投标产品为{p_name}（型号：{p_model or '待核实'}），"
            f"针对采购文件技术要求逐项响应如下："
        )
    perf_lines.append("")

    # 列出有实参的关键性能
    real_rows = [r for r in requirement_rows if r.get("has_real_response")]

    # 自动降级为“编辑提示模式”：
    # 只要没有真实产品身份，或真实响应行太少，就不要继续脑补正文
    effective_internal = (draft_mode == "internal") or (not has_real_product) or (len(real_rows) < 3)

    if effective_internal:
        if draft_mode == "review":
            return ""
        return "\n".join([
            "### （五）编辑提示",
            "",
            f"- 待补品牌/型号/生产厂家：{p_name}",
            "- 待补关键技术参数实参：按上表逐条填写“实际响应值”",
            "- 待补证据页码：优先填写说明书/彩页/注册证/厂家承诺中的对应页码",
            "- 待补配置清单：补齐随机文件、首批耗材、培训计划",
            "- 待补售后/验收/培训承诺：以采购文件原文和厂家承诺为准，不要自动脑补时限和天数",
            "- 待补后再展开正文说明，当前不输出交付/验收/培训的完成态描述",
        ])
    for idx, row in enumerate(real_rows[:10], start=1):
        key = _as_text(row.get("key", ""))
        response = _as_text(row.get("response", ""))
        evidence = _as_text(row.get("evidence_ref", ""))
        evidence_note = f"（证据来源：{evidence}）" if evidence and is_rich else ""
        perf_lines.append(
            f"{idx}. **{key}**：{response}。"
            f"该参数满足招标文件要求，确保设备在实际应用中达到预期性能。{evidence_note}"
        )
    if not real_rows and specs:
        for idx, (k, v) in enumerate(list(specs.items())[:8], start=1):
            perf_lines.append(f"{idx}. **{k}**：{_as_text(v)}。")
    if not real_rows and not specs:
        if is_rich:
            perf_lines.append("（本包关键性能参数待补充产品实参后展开说明。）")
        else:
            perf_lines.append("（待核实：关键性能参数尚未从投标材料中提取。）")
    perf_lines.append("")
    if has_real_product:
        perf_lines.append(
            f"综上，{p_brand or p_mfr} {p_name}（{p_model}）的核心性能指标均满足或优于"
            "采购文件技术要求，能够有效支撑采购人的日常业务需求。"
        )
    else:
        perf_lines.append(
            f"综上，{p_name}当前已形成逐条响应框架；待补充投标型号、产品实参及对应证据材料后，"
            "再形成最终技术结论。"
        )
    sections.append("\n".join(perf_lines))

    # ── 2. 配置说明 ──
    config_lines = [
        "### （六）配置说明",
        "",
    ]
    if has_real_product:
        config_lines.append(
            f"本包投标设备{p_brand or p_mfr} {p_name}（{p_model}）的配置方案"
            "严格按照采购文件要求编制，主要包括以下几个方面："
        )
    else:
        config_lines.append(
            f"本包投标设备{p_name}的配置方案严格按照采购文件要求编制，"
            "主要包括以下几个方面："
        )
    config_lines.append("")

    # 按类别组织配置项
    if config_items_raw:
        categorized: dict[str, list[dict]] = {}
        for item in config_items_raw:
            if not isinstance(item, dict):
                continue
            name = _as_text(item.get("配置项") or item.get("name", ""))
            if not name:
                continue
            category = _classify_config_item(name)
            categorized.setdefault(category, []).append(item)

        cat_order = ["核心模块", "标准附件", "配套软件", "初始耗材", "随机文件", "安装/培训资料"]
        cat_idx = 1
        for cat in cat_order:
            items = categorized.get(cat, [])
            if not items:
                continue
            item_names = "、".join(
                _as_text(it.get("配置项") or it.get("name", ""))
                for it in items[:5]
            )
            desc = _as_text(items[0].get("说明", "")) if items else ""
            config_lines.append(f"{cat_idx}. **{cat}**：{item_names}。{desc}")
            cat_idx += 1
        # 处理未分类项
        for cat, items in categorized.items():
            if cat not in cat_order and items:
                item_names = "、".join(
                    _as_text(it.get("配置项") or it.get("name", ""))
                    for it in items[:5]
                )
                config_lines.append(f"{cat_idx}. **{cat}**：{item_names}。")
                cat_idx += 1
    else:
        # 配置项不足，不自动脑补完成态，输出待补清单
        qty = _infer_package_quantity(pkg, tender_raw)
        config_lines.extend([
            f"1. **核心模块**：待补真实交付模块清单（{p_name}主机{qty}台/套）",
            "2. **配套软件**：待补软件名称/版本/授权方式",
            "3. **初始耗材**：待补首批耗材明细",
            "4. **随机文件**：待补说明书/合格证/装箱单",
            "5. **安装/培训资料**：待补安装指导手册和操作培训教材",
        ])

    config_lines.append("")
    config_lines.append(
        "上述配置方案确保设备到场即可开展安装调试工作，"
        "各配置项的详细清单见配置明细表。"
    )
    sections.append("\n".join(config_lines))

    # ── 3. 交付说明 ──
    delivery_lines = [
        "### （七）交付说明",
        "",
        f"1. **交货期限**：{delivery_time or '按招标文件约定执行'}。"
        "我方将在合同签订后安排生产/备货，确保按期交付。",
        f"2. **交货地点**：{delivery_place or '采购人指定地点'}。"
        "我方负责将设备安全运抵指定地点，运输过程中的一切风险由我方承担。",
        "3. **包装运输**：采用专业包装方式，确保设备在运输过程中不受损坏。"
        "外包装标注收货信息、产品名称和注意事项。",
        "4. **到货验收**：设备到场后由供需双方共同开箱检验，"
        "核对设备型号、配置清单和外观完好性，并签署到货验收单。",
        "5. **安装调试**：我方安排专业工程师到现场进行安装调试，"
        "确保设备达到正常使用状态，调试完成后出具调试报告。",
    ]
    sections.append("\n".join(delivery_lines))

    # ── 4. 验收说明 ──
    acceptance_lines = [
        "### （八）验收说明",
        "",
        "本包设备验收分为以下阶段：",
        "",
        "1. **到货验收**：设备到达指定地点后，由采购人与供应商共同开箱检验。"
        "核对设备品牌、型号、数量、配置清单和外观，确认无误后签署到货验收单。",
        "2. **安装调试验收**：安装调试完成后，按照采购文件和合同约定的技术指标"
        "逐项进行功能测试和性能验证。验收标准以采购文件技术要求为依据。",
        "3. **试运行验收**：设备安装调试后进入试运行期，"
        "试运行期间供应商提供全程技术保障。"
        "试运行合格后由采购人组织终验。",
        f"4. **质保期起算**：质保期自验收合格之日起计算，质保期为{warranty}。",
        "",
        "验收过程中如发现设备不符合采购文件要求或存在质量问题，"
        "供应商应在规定时间内免费更换或修复。",
    ]
    sections.append("\n".join(acceptance_lines))

    # ── 5. 使用与培训说明 ──
    training_lines = [
        "### （九）使用与培训说明",
        "",
        "为确保采购人能够独立、熟练使用本包投标设备，我方提供以下培训服务：",
        "",
        "1. **操作培训**：设备安装调试完成后，安排专业培训师对采购人操作人员"
        "进行系统培训，内容包括设备基本原理、操作流程、"
        "日常维护和常见故障排除。",
        "2. **培训方式**：采用现场操作演示+理论讲解相结合的方式，"
        "确保培训人员掌握全部操作技能。培训结束后提供培训教材和操作手册。",
        "3. **培训时间**：培训不少于2天，具体时间按采购人要求安排。",
        "4. **高级培训**：根据采购人需要，可安排更深层次的应用培训，"
        "包括数据分析方法、质控管理和高级功能应用。",
        "5. **远程支持**：培训结束后持续提供远程技术指导和答疑服务，"
        "确保使用人员在实际操作中遇到问题能得到及时解答。",
        "",
        "培训完成后由参训人员签署培训确认记录，"
        "确认培训内容和效果满足实际使用需求。",
    ]
    sections.append("\n".join(training_lines))

    return "\n\n".join(sections)



def _default_response_placeholder(section_type: str) -> str:
    mapping = {
        "service": "【待填写：服务承诺】",
        "config": "【待填写：配置响应】",
        "acceptance": "【待填写：验收承诺】",
        "documentation": "【待填写：资料提供承诺】",
    }
    return mapping.get(section_type, "【待填写】")


def _normalize_section_response(raw_value: Any, section_type: str) -> str:
    text = _safe_text(raw_value, "")
    if not text:
        return _default_response_placeholder(section_type)

    generic_pending_markers = (
        _PENDING_BIDDER_RESPONSE,
        "【待填写：投标产品实参】",
        "待补充（投标产品实参）",
        "待核实（需填入投标产品实参）",
    )
    if any(marker in text for marker in generic_pending_markers):
        return _default_response_placeholder(section_type)
    return text

def _default_evidence_placeholder(section_type: str) -> str:
    mapping = {
        "service": "【待补证：售后服务方案/厂家承诺】",
        "config": "【待补证：配置清单/彩页/说明书】",
        "acceptance": "【待补证：验收方案/验收记录模板/技术资料】",
        "documentation": "【待补证：说明书/合格证/注册证/授权文件】",
    }
    return mapping.get(section_type, "【待补证】")


def _beautify_nontech_key(section_type: str, key: str, req: str) -> str:
    key_n = _canonicalize_clause_text(key)
    req_n = _canonicalize_clause_text(req)

    if section_type == "service":
        if any(t in req_n for t in ("培训", "安装调试")):
            return "培训/安装服务"
        if "LIS" in req_n:
            return "配套接口服务"
        if any(t in req_n for t in ("维护", "保养")):
            return "维护保养服务"

    return key_n

def _normalize_section_evidence(row: dict[str, Any], section_type: str) -> str:
    """
    分表里的“证据材料”只允许使用投标方侧证据；
    不再回落到招标原文 evidence_quote / evidence。
    """
    bidder_ev = _safe_text(row.get("bidder_evidence"), "")
    if bidder_ev:
        return bidder_ev

    bidder_source = _safe_text(row.get("bidder_evidence_source"), "")
    if bidder_source:
        return bidder_source

    evidence_ref = _safe_text(row.get("evidence_ref"), "")
    if evidence_ref and "招标" not in evidence_ref and "原文" not in evidence_ref:
        return evidence_ref

    return _default_evidence_placeholder(section_type)





def _generate_rich_draft_sections(
    tender: TenderDocument,
    products: dict,
    *,
    draft_mode: str = "rich",
    normalized_reqs: dict[str, list[NormalizedRequirement]] | None = None,
    active_packages: list[ProcurementPackage] | None = None,
    writer_contexts: dict[str, list] | None = None,
) -> list[BidDocumentSection]:
    """双模式 Section Writer — 底稿优化版，固定分表输出。

    writer 输入必须是 requirement + package_context + table_type。
    固定分表输出：
    1. 技术参数响应表（仅 technical_requirement）
    2. 配置清单（仅 config_requirement）
    3. 售后服务响应表（仅 service_requirement）
    4. 验收/资料要求响应表（acceptance + documentation）

    每个包独立生成，只消费同包对象。
    当传入 writer_contexts 时，优先使用其中已校验的需求分组。
    """
    sections: list[BidDocumentSection] = []
    is_rich = draft_mode == "rich"
    normalized_reqs = normalized_reqs or {}
    writer_contexts = writer_contexts or {}
    packages = active_packages or tender.packages
    for pkg in packages:
        product = products.get(pkg.package_id)
        if not product:
            continue

        p_name = _as_text(getattr(product, "product_name", ""))
        p_model = _as_text(getattr(product, "model", ""))
        p_mfr = _as_text(getattr(product, "manufacturer", ""))
        p_brand = _as_text(getattr(product, "brand", ""))
        specs = getattr(product, "specifications", None) or {}
        config_items = getattr(product, "config_items", None) or []
        evidence_refs = getattr(product, "evidence_refs", None) or []
        has_real_product = bool(p_model and p_mfr)
        pkg_reqs = normalized_reqs.get(pkg.package_id, [])

        # 优先使用 WriterContext 中已校验的需求分组（保证包一致性）
        pkg_wctxs = writer_contexts.get(pkg.package_id, [])
        wctx_by_type = {wc.table_type: wc.requirements for wc in pkg_wctxs} if pkg_wctxs else {}

        # ── 分表1: 技术参数响应表 ──
        tech_reqs = wctx_by_type.get("technical_deviation") or [
            r for r in pkg_reqs if r.category == ClauseCategory.technical_requirement
        ]
        tech_content = f"### 包{pkg.package_id} 技术参数响应表\n\n"
        if has_real_product:
            tech_content += f"投标产品：**{p_brand or p_mfr} {p_name}**（型号：{p_model}）\n\n"
        if tech_reqs:
            tech_content += "| 序号 | 参数名称 | 招标要求 | 投标响应 | 偏离说明 |\n"
            tech_content += "|------|----------|----------|----------|----------|\n"
            for idx, req in enumerate(tech_reqs, 1):
                response = specs.get(req.param_name, "待核实（需填入投标产品实参）")
                deviation = "无偏离" if req.param_name in specs else "待核实"
                material_mark = "★" if req.is_material else ""
                tech_content += f"| {idx} | {material_mark}{_markdown_cell(req.param_name)} | {_markdown_cell(req.threshold or req.raw_text)} | {_markdown_cell(str(response))} | {deviation} |\n"
        else:
            tech_content += "[TODO:待补技术参数响应表]\n"
        tech_content += "\n"

        # ── 分表2: 配置清单 ──
        config_reqs = wctx_by_type.get("config_list") or [
            r for r in pkg_reqs if r.category == ClauseCategory.config_requirement
        ]
        config_content = f"### 包{pkg.package_id} 配置清单\n\n"
        if config_items:
            config_content += "| 序号 | 配置项 | 数量 | 说明 |\n"
            config_content += "|------|--------|------|------|\n"
            for idx, item in enumerate(config_items, 1):
                if isinstance(item, dict):
                    name = _as_text(item.get("配置项") or item.get("name", ""))
                    qty = _as_text(item.get("数量") or item.get("qty", "1"))
                    desc = _as_text(item.get("说明") or item.get("description", "标配"))
                    config_content += f"| {idx} | {_markdown_cell(name)} | {qty} | {_markdown_cell(desc)} |\n"
        elif config_reqs:
            config_content += "| 序号 | 配置项 | 招标要求 | 投标响应 |\n"
            config_content += "|------|--------|----------|----------|\n"
            for idx, req in enumerate(config_reqs, 1):
                config_content += f"| {idx} | {_markdown_cell(req.param_name)} | {_markdown_cell(req.raw_text)} | [TODO:待补] |\n"
        else:
            config_content += "[TODO:待补配置清单]\n"
        config_content += "\n"

        # ── 分表3: 售后服务响应表 ──
        service_reqs = wctx_by_type.get("service_response") or [
            r for r in pkg_reqs if r.category == ClauseCategory.service_requirement
        ]
        service_content = f"### 包{pkg.package_id} 售后服务响应表\n\n"
        warranty = tender.commercial_terms.warranty_period
        service_content += f"- 质保期：{warranty or '[TODO:待补质保期限]'}\n"
        service_content += "- 响应时间：接到通知后24小时内响应\n"
        service_content += "- 维修服务：提供7×24小时技术热线\n\n"
        if service_reqs:
            service_content += "| 序号 | 服务项 | 招标要求 | 投标承诺 |\n"
            service_content += "|------|--------|----------|----------|\n"
            for idx, req in enumerate(service_reqs, 1):
                service_content += f"| {idx} | {_markdown_cell(req.param_name)} | {_markdown_cell(req.raw_text)} | 满足 |\n"
        service_content += "\n"

        # ── 分表4: 验收/资料要求响应表 ──
        accept_reqs = wctx_by_type.get("acceptance_doc_response") or [
            r for r in pkg_reqs if r.category in (
                ClauseCategory.acceptance_requirement,
                ClauseCategory.documentation_requirement,
            )
        ]
        accept_content = f"### 包{pkg.package_id} 验收及资料要求响应表\n\n"
        if accept_reqs:
            accept_content += "| 序号 | 类型 | 要求项 | 招标要求 | 投标响应 |\n"
            accept_content += "|------|------|--------|----------|----------|\n"
            for idx, req in enumerate(accept_reqs, 1):
                cat_label = "验收" if req.category == ClauseCategory.acceptance_requirement else "资料"
                accept_content += f"| {idx} | {cat_label} | {_markdown_cell(req.param_name)} | {_markdown_cell(req.raw_text)} | 满足 |\n"
        else:
            accept_content += "- 验收标准：按照国家标准及采购文件要求\n"
            accept_content += "- 随机文件：[TODO:待补随机文件清单]\n"
        accept_content += "\n"

        # ── 合并为一个章节 ──
        combined = tech_content + config_content + service_content + accept_content
        sections.append(BidDocumentSection(
            section_title=f"第三章附：包{pkg.package_id}分表响应",
            content=combined,
        ))

        # ── 独立的详细说明章节（关键性能+交付+培训） ──
        detail_content = _build_package_detail_section(
            pkg, tender, product, has_real_product, specs, evidence_refs, is_rich,
        )
        sections.append(BidDocumentSection(
            section_title=f"第三章附：包{pkg.package_id}详细说明",
            content=detail_content,
        ))

    return sections


def _build_package_detail_section(
    pkg: ProcurementPackage,
    tender: TenderDocument,
    product: Any,
    has_real_product: bool,
    specs: dict,
    evidence_refs: list,
    is_rich: bool,
) -> str:
    """构建单包详细说明（关键性能+交付+培训），与分表解耦。"""
    p_name = _as_text(getattr(product, "product_name", "")) if product else pkg.item_name
    p_model = _as_text(getattr(product, "model", "")) if product else ""
    p_mfr = _as_text(getattr(product, "manufacturer", "")) if product else ""
    p_brand = _as_text(getattr(product, "brand", "")) if product else ""

    content = f"### 包{pkg.package_id} 关键性能说明\n\n"
    if has_real_product:
        content += f"本包投标产品为 **{p_brand or p_mfr} {p_name}**（型号：{p_model}），生产厂家：{p_mfr}。\n\n"
    else:
        content += f"本包投标产品为{p_name}。[TODO:待补品牌型号及厂家信息]\n\n"

    if specs:
        content += "**核心技术参数**：\n\n"
        for idx, (key, value) in enumerate(list(specs.items())[:10], start=1):
            content += f"{idx}. **{key}**：{_as_text(value)}。\n"
        content += "\n"
    else:
        content += "[TODO:待补关键性能参数]\n\n"

    # 交付说明
    content += f"### 包{pkg.package_id} 交付说明\n\n"
    content += f"- 交货期：{pkg.delivery_time or '[TODO:待补交货期限]'}\n"
    content += f"- 交货地点：{pkg.delivery_place or '[TODO:待补交货地点]'}\n"
    content += "- 交货方式：由我公司负责运输至指定地点\n\n"

    # 培训说明
    content += f"### 包{pkg.package_id} 使用与培训说明\n\n"
    content += f"- 培训对象：{pkg.item_name}操作人员、维护人员\n"
    content += "- 培训方式：现场培训+远程技术支持\n"
    content += "- 培训时长：不少于3天 [TODO:待核实具体培训时长要求]\n"
    warranty = tender.commercial_terms.warranty_period
    content += f"- 质保期：{warranty or '[TODO:待补质保期限]'}\n\n"

    return content


def _gen_technical(
    llm: ChatOpenAI,
    tender: TenderDocument,
    tender_raw: str,
    products: dict | None = None,
    *,
    normalized_result: dict[str, Any] | None = None,
    evidence_result: dict[str, Any] | None = None,
    product_profiles: dict[str, dict[str, Any]] | None = None,
    tender_bindings: dict[str, list] | None = None,
    bid_bindings: dict[str, list] | None = None,
    active_packages: list[ProcurementPackage] | None = None,
) -> BidDocumentSection:
    """第三章：商务及技术部分 — 增强版：分类分表"""
    packages = active_packages or tender.packages
    _ = llm
    products = products or {}
    today = _today()
    purchaser = _safe_text(tender.purchaser, "[采购人名称]")
    package_details = _package_detail_lines(tender, tender_raw, packages=packages)
    quote_table = _quote_overview_table(tender, tender_raw, packages=packages)

    technical_sections: list[str] = []
    service_sections: list[str] = []

    # 用于跨包折叠重复的非技术分表
    reused_nontech_blocks: dict[str, dict[str, str]] = {
        "service": {},
        "config": {},
        "acceptance": {},
        "documentation": {},
    }

    if packages:
        for pkg in packages:
            product = products.get(pkg.package_id)
            product_profile = (product_profiles or {}).get(pkg.package_id)
            # 构建 binding 索引（按 requirement_id 查找）
            _pkg_tender_binds = {}
            _pkg_bid_binds = {}
            if tender_bindings and pkg.package_id in tender_bindings:
                for tb in tender_bindings[pkg.package_id]:
                    rid = getattr(tb, "requirement_id", None) or (tb.get("requirement_id") if isinstance(tb, dict) else None)
                    if rid:
                        _pkg_tender_binds[rid] = tb if isinstance(tb, dict) else tb.model_dump() if hasattr(tb, "model_dump") else {}

            requirement_rows, total_requirements = _build_requirement_rows(
                pkg,
                tender_raw,
                product=product,
                normalized_result=normalized_result,
                evidence_result=evidence_result,
                product_profile=product_profile,
                category_filter=None,
                tender_bindings=_pkg_tender_binds,
                bid_bindings=_pkg_bid_binds,
            )

            # ── 条款分类过滤：优先使用行上已标注的 category 字段 ──
            tech_rows = []
            svc_rows = []
            config_rows_list = []
            acceptance_rows = []
            doc_rows = []
            for row in requirement_rows:
                key = _safe_text(row.get("key"), "")
                req = _safe_text(row.get("requirement"), "")

                # 永远先做一次基于文本的重分类
                inferred_cat = _classify_clause_category(key, req)

                raw_cat_text = _safe_text(row.get("category"), "")
                raw_cat: ClauseCategory | None = None
                if raw_cat_text:
                    try:
                        raw_cat = ClauseCategory(raw_cat_text)
                    except ValueError:
                        raw_cat = None

                # 规则：
                # 1) 如果 inferred 是明显非技术类，则以 inferred 为准
                # 2) 只有 inferred == technical_requirement 时，才允许相信 raw_cat
                if inferred_cat != ClauseCategory.technical_requirement:
                    cat = inferred_cat
                else:
                    cat = raw_cat or inferred_cat

                row = dict(row)
                row["category"] = cat.value

                if cat == ClauseCategory.service_requirement:
                    svc_rows.append(row)
                elif cat == ClauseCategory.config_requirement:
                    config_rows_list.append(row)
                elif cat == ClauseCategory.acceptance_requirement:
                    acceptance_rows.append(row)
                elif cat == ClauseCategory.documentation_requirement:
                    doc_rows.append(row)
                elif cat in (
                        ClauseCategory.commercial_requirement,
                        ClauseCategory.compliance_note,
                        ClauseCategory.attachment_requirement,
                        ClauseCategory.noise,
                ):
                    pass
                else:
                    tech_rows.append(row)

            # ── 技术偏离表最小行数检查 ──
            tech_rows = _sanitize_rows_for_section(
                pkg, tech_rows, expected_category=ClauseCategory.technical_requirement
            )
            svc_rows = _sanitize_rows_for_section(
                pkg, svc_rows, expected_category=ClauseCategory.service_requirement
            )
            config_rows_list = _sanitize_rows_for_section(
                pkg, config_rows_list, expected_category=ClauseCategory.config_requirement
            )
            acceptance_rows = _sanitize_rows_for_section(
                pkg, acceptance_rows, expected_category=ClauseCategory.acceptance_requirement
            )
            doc_rows = _sanitize_rows_for_section(
                pkg, doc_rows, expected_category=ClauseCategory.documentation_requirement
            )
            base_min_rows = _DETAIL_TARGETS.get("deviation_table_min_rows", 10)

            # 自适应阈值：
            # 至少要求 6 条；但如果本包清洗后就只有 8 条有效技术项，
            # 就不再强行按 10 条报警。
            adaptive_min_rows = min(base_min_rows, max(6, len(tech_rows)))

            if len(tech_rows) < adaptive_min_rows:
                logger.warning(
                    "包%s 技术偏离表行数不足: %d < %d (最小要求)，建议补充技术条款",
                    pkg.package_id, len(tech_rows), adaptive_min_rows,
                )

            mapped_count = sum(1 for row in tech_rows if bool(row.get("mapped")))

            technical_sections.append(f"### 包{pkg.package_id}：{pkg.item_name}")

            technical_sections.append(
                _build_deviation_table(
                    tender=tender,
                    pkg=pkg,
                    requirement_rows=tech_rows,
                    total_requirements=len(tech_rows),
                    product=product,
                )
            )

            technical_sections.append(
                _build_configuration_table(
                    pkg,
                    tender_raw,
                    product=product,
                    product_profile=product_profile,
                    normalized_result=normalized_result,
                )
            )

            include_internal_diagnostics = False

            if include_internal_diagnostics:
                technical_sections.append(
                    _build_response_checklist_table(
                        pkg=pkg,
                        mapped_count=mapped_count,
                        total_requirements=len(tech_rows),
                        requirement_rows=tech_rows,
                    )
                )
                technical_sections.append(
                    _build_evidence_mapping_table(
                        pkg=pkg,
                        requirement_rows=tech_rows,
                        total_requirements=len(tech_rows),
                    )
                )

            # ── 售后服务要求分表 ──
            # ── 售后服务要求分表 ──
            if svc_rows:
                svc_table_lines = [
                    f"### 包{pkg.package_id} 售后服务要求响应表",
                    "| 序号 | 售后服务要求 | 响应承诺 | 证据材料 |",
                    "|---:|---|---|---|",
                ]
                for idx, row in enumerate(svc_rows, start=1):
                    pretty_key = _beautify_nontech_key(
                        "service",
                        _safe_text(row.get("key"), ""),
                        _safe_text(row.get("requirement", row.get("value", "")), ""),
                    )
                    key = _markdown_cell(pretty_key)
                    req = _markdown_cell(row.get("requirement", row.get("value", "")))
                    resp = _markdown_cell(_normalize_section_response(row.get("response"), "service"))
                    evidence = _markdown_cell(_normalize_section_evidence(row, "service"))
                    svc_table_lines.append(f"| {idx} | {key}：{req} | {resp} | {evidence} |")
                service_sections.append(
                    _collapse_repeated_nontech_block(
                        "service",
                        str(pkg.package_id),
                        svc_table_lines,
                        reused_nontech_blocks,
                    )
                )

            # ── 配置类要求分表 ──
            if config_rows_list:
                cfg_table_lines = [
                    f"### 包{pkg.package_id} 配置要求响应表",
                    "| 序号 | 配置要求 | 响应情况 | 证据材料 |",
                    "|---:|---|---|---|",
                ]
                for idx, row in enumerate(config_rows_list, start=1):
                    key = _markdown_cell(row.get("key", ""))
                    req = _markdown_cell(row.get("requirement", row.get("value", "")))
                    resp = _markdown_cell(_normalize_section_response(row.get("response"), "config"))
                    evidence = _markdown_cell(_normalize_section_evidence(row, "config"))
                    cfg_table_lines.append(f"| {idx} | {key}：{req} | {resp} | {evidence} |")
                service_sections.append(
                    _collapse_repeated_nontech_block(
                        "config",
                        str(pkg.package_id),
                        cfg_table_lines,
                        reused_nontech_blocks,
                    )
                )

            # ── 验收要求分表 ──
            acc_table_lines = [
                f"### 包{pkg.package_id} 验收要求响应表",
                "| 序号 | 验收要求 | 响应承诺 | 验收方式 | 证据材料 |",
                "|---:|---|---|---|---|",
            ]

            if acceptance_rows:
                for idx, row in enumerate(acceptance_rows, start=1):
                    key = _markdown_cell(row.get("key", ""))
                    req = _markdown_cell(row.get("requirement", row.get("value", "")))
                    resp = _markdown_cell(
                        _normalize_section_response(row.get("response"), "acceptance")
                    )
                    evidence = _markdown_cell(
                        _normalize_section_evidence(row, "acceptance")
                    )
                    acc_table_lines.append(
                        f"| {idx} | {key}：{req} | {resp} | 【待填写：验收方式】 | {evidence} |"
                    )
            else:
                acc_table_lines.extend([
                    "| 1 | 到货验收：核对品牌、型号、数量、外观及装箱清单 | 【待填写：响应承诺】 | 【待填写：到货验收】 | 【待补证：装箱单/到货验收单】 |",
                    "| 2 | 安装调试验收：完成安装、通电开机、基础功能验证 | 【待填写：响应承诺】 | 【待填写：安装调试验收】 | 【待补证：安装调试记录/厂家服务单】 |",
                    "| 3 | 试运行或性能验收：按采购文件或院方要求完成性能验证 | 【待填写：响应承诺】 | 【待填写：试运行/性能验收】 | 【待补证：试运行记录/性能验证记录】 |",
                    "| 4 | 资料移交验收：说明书、合格证、注册证/备案凭证、培训记录等资料齐套 | 【待填写：响应承诺】 | 【待填写：资料验收】 | 【待补证：随机资料清单/培训签到表】 |",
                ])

            service_sections.append(
                _collapse_repeated_nontech_block(
                    "acceptance",
                    str(pkg.package_id),
                    acc_table_lines,
                    reused_nontech_blocks,
                )
            )

            # ── 资料/文档要求分表 ──
            doc_table_lines = [
                f"### 包{pkg.package_id} 资料/文档要求响应表",
                "| 序号 | 资料要求 | 响应承诺 | 提供方式 | 证据材料 |",
                "|---:|---|---|---|---|",
            ]

            # ── 资料/文档要求分表 ──
            doc_table_lines = [
                f"### 包{pkg.package_id} 资料/文档要求响应表",
                "| 序号 | 资料要求 | 响应承诺 | 提供方式 | 证据材料 |",
                "|---:|---|---|---|---|",
            ]

            if doc_rows:
                for idx, row in enumerate(doc_rows, start=1):
                    key = _markdown_cell(row.get("key", ""))
                    req = _markdown_cell(row.get("requirement", row.get("value", "")))
                    resp = _markdown_cell(
                        _normalize_section_response(row.get("response"), "documentation")
                    )
                    evidence = _markdown_cell(
                        _normalize_section_evidence(row, "documentation")
                    )
                    doc_table_lines.append(
                        f"| {idx} | {key}：{req} | {resp} | 【待填写：提供方式】 | {evidence} |"
                    )
            else:
                doc_table_lines.extend([
                    "| 1 | 随机文件：说明书、合格证、保修卡、装箱单等资料齐套 | 【待填写：响应承诺】 | 【待填写：随货/另附/电子版】 | 【待补证：随机文件清单】 |",
                    "| 2 | 合规文件：注册证/备案凭证、生产/经营许可文件、授权文件等与投标型号一致 | 【待填写：响应承诺】 | 【待填写：随附/投标文件附后】 | 【待补证：注册证/备案凭证/授权文件】 |",
                    "| 3 | 培训与验收资料：培训签到表、安装调试记录、验收单等资料可完整移交 | 【待填写：响应承诺】 | 【待填写：交付时提供】 | 【待补证：培训记录/安装调试记录/验收单模板】 |",
                ])

            service_sections.append(
                _collapse_repeated_nontech_block(
                    "documentation",
                    str(pkg.package_id),
                    doc_table_lines,
                    reused_nontech_blocks,
                )
            )
    else:
        technical_sections.append(
            "\n".join(
                [
                    "### （一）技术偏离及详细配置明细表",
                    "| 条款编号 | 招标要求 | 投标型号 | 实际响应值 | 偏离情况 | 证据材料 | 页码 | 说明/验收备注 |",
                    "|---|---|---|---|---|---|---|---|",
                    "| 1.1 | 详见招标文件 | [待填写] | 详见拟投产品参数资料 | 无偏离 | 结构化解析结果 | — | 建议复核原文 |",
                    "",
                    "### （二-A）详细配置明细表",
                    "| 序号 | 配置名称 | 单位 | 数量 | 是否标配 | 用途说明 | 备注 |",
                    "|---:|---|---|---:|---|---|---|",
                    "| 1 | 核心配置 | 项 | 1 | 是 | 核心设备 | 待按项目补充 |",
                    "",
                    "### （三）编辑提示",
                    "- 当前仅生成基础技术响应框架。",
                    "- 待补充拟投型号、关键参数实参、配置清单及投标方证据后，再输出完整技术说明。",
                ]
            )
        )

    # 合并服务/配置分表
    service_block = ""
    if service_sections:
        service_block = "\n\n## 四、售后服务/配置/验收/资料要求响应\n\n" + "\n\n".join(service_sections)

    content = f"""## 一、报价书
{purchaser}：

我方{_COMPANY}已详细研究“{tender.project_name}”（项目编号：{tender.project_number}）采购文件，愿按采购文件及合同条款要求提供合格货物及服务，并承担相应责任义务。现提交报价文件如下：
1. 投标范围：{_package_scope(tender, packages)}
2. 报价原则：满足招标文件实质性条款，报价包含货物、运输、安装、调试、培训、税费、售后服务等全部费用
3. 履约承诺：严格按合同约定进度组织供货、安装、验收及售后服务
4. 有效期承诺：投标有效期按招标文件约定执行

采购包信息摘要：
{package_details}

投标人名称：{_COMPANY}  
授权代表：{_AUTHORIZED_REP}  
联系电话：{_PHONE}  
日期：{today}

## 二、报价一览表
项目名称：{tender.project_name}  
项目编号：{tender.project_number}

{quote_table}

## 三、技术偏离及详细配置明细表
{"\n\n".join(technical_sections)}
{service_block}
"""
    return BidDocumentSection(section_title="第三章 商务及技术部分", content=content.strip())


def _build_appendix_service_placeholder(
    tender: TenderDocument,
    packages: list[ProcurementPackage] | None = None,
) -> str:
    warranty = _normalize_commitment_term(tender.commercial_terms.warranty_period)
    payment = _normalize_commitment_term(tender.commercial_terms.payment_method)
    pkgs = packages if packages is not None else tender.packages

    lines = [
        "## 二、技术服务和售后服务的内容及措施",
        "### （一）逐包补齐清单",
        "| 包号 | 待补主题 | 回填位置 | 建议附件 |",
        "|---|---|---|---|",
    ]

    for pkg in pkgs:
        lines.append(
            f"| 包{pkg.package_id} | 售后响应时限、保养频次、培训安排、验收方式 | "
            "第三章《四、售后服务/配置/验收/资料要求响应》中对应包分表 | "
            "售后服务方案 / 厂家服务承诺 / 培训计划 / 安装调试记录模板 |"
        )

    lines.extend([
        "",
        "### （二）商务总述",
        f"- 质保期：{warranty}",
        f"- 付款方式：{payment}",
    ])
    return "\n".join(lines)


def _build_product_brochure_checklist(packages: list[ProcurementPackage]) -> str:
    lines = [
        "## 三、产品彩页",
        "| 包号 | 建议文件名 | 核对要点 |",
        "|---|---|---|",
    ]
    for pkg in packages:
        lines.append(
            f"| 包{pkg.package_id} | 包{pkg.package_id}_产品彩页.pdf | 至少包含品牌、型号、主要参数、配置清单，并与第三章对应 |"
        )
    return "\n".join(lines)


def _build_energy_cert_checklist(packages: list[ProcurementPackage]) -> str:
    lines = [
        "## 四、节能/环保/能效认证证书（如适用）",
        "| 包号 | 是否适用 | 建议文件名 | 核对要点 |",
        "|---|---|---|---|",
    ]
    for pkg in packages:
        lines.append(
            f"| 包{pkg.package_id} | 【待填写：是/否】 | 包{pkg.package_id}_节能环保能效证书.pdf | 证书名称、适用型号、有效期应与投标型号一致 |"
        )
    return "\n".join(lines)


def _build_quality_report_checklist(packages: list[ProcurementPackage]) -> str:
    lines = [
        "## 五、检测/质评数据节选",
        "| 包号 | 建议文件名 | 优先内容 | 核对要点 |",
        "|---|---|---|---|",
    ]
    for pkg in packages:
        lines.append(
            f"| 包{pkg.package_id} | 包{pkg.package_id}_检测或质评资料.pdf | 检测报告 / 室间质评 / 能力验证 / 原厂质控资料 | 核对项目名称、型号、分组、结论与投标产品一致 |"
        )
    return "\n".join(lines)



def _gen_appendix(
    llm: ChatOpenAI,
    tender: TenderDocument,
    tender_raw: str,
    products: dict | None = None,
    *,
    normalized_result: dict[str, Any] | None = None,
    evidence_result: dict[str, Any] | None = None,
    product_profiles: dict[str, dict[str, Any]] | None = None,
    active_packages: list[ProcurementPackage] | None = None,
) -> BidDocumentSection:
    """第四章：报价书附件（技术参数明细 + 售后服务方案）"""
    _ = llm
    packages = active_packages or tender.packages
    products = products or {}
    today = _today()
    warranty = _normalize_commitment_term(tender.commercial_terms.warranty_period)
    payment = _normalize_commitment_term(tender.commercial_terms.payment_method)

    parameter_tables: list[str] = []
    if packages:
        for pkg in packages:
            parameter_tables.append(
                _build_main_parameter_table(
                    pkg,
                    tender_raw,
                    product=products.get(pkg.package_id),
                    normalized_result=normalized_result,
                    evidence_result=evidence_result,
                    product_profile=(product_profiles or {}).get(pkg.package_id),
                )
            )
    else:
        parameter_tables.append(
            "\n".join(
                [
                    "### 包信息",
                    "| 序号 | 技术参数项 | 招标要求 | 响应情况 | 备注 |",
                    "|---:|---|---|---|---|",
                    "| 1 | 核心参数 | 详见招标文件 | 详见拟投产品参数资料 | 无偏离 |",
                ]
            )
        )

    content = f"""## 一、产品主要技术参数明细表及报价表
### （一）产品主要技术参数
{"\n\n".join(parameter_tables)}

### （二）报价明细表
项目名称：{tender.project_name}  
项目编号：{tender.project_number}

{_build_detail_quote_table(tender, tender_raw, packages=packages)}

{_build_appendix_service_placeholder(tender, packages=packages)}

投标人名称：{_COMPANY}  
授权代表：{_AUTHORIZED_REP}  
日期：{today}

{_build_product_brochure_checklist(packages)}

{_build_energy_cert_checklist(packages)}

{_build_quality_report_checklist(packages)}
"""
    return BidDocumentSection(section_title="第四章 报价书附件", content=content.strip())
