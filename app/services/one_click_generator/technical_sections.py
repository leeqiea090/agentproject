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
    _clean_delivery_text,
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
    可编辑底稿模式下，不再折叠跨包重复的非技术分表。

    原因：
    1. 折叠虽然能缩短篇幅，但会迫使人工在不同包之间来回跳转；
    2. 包2/3/4/5/6 即使内容相同，正式稿阶段通常也希望每包有完整独立表格；
    3. 对“人工审核可快速修订”来说，完整展开优先于篇幅压缩。
    """
    return "\n".join(lines) if lines else ""

def _canonicalize_clause_text(text: str, *, for_signature: bool = False) -> str:
    """返回条款文本。"""
    t = _safe_text(text, "")
    t = re.sub(r"^(实质性条款|重要条款|一般条款)[:：]\s*", "", t)
    t = t.replace("★", "").replace("▲", "").replace("■", "")
    t = re.sub(r"\s+", " ", t).strip()

    if for_signature:
        # 这里只用于“去重签名”，只删除“明确像编号”的前缀
        # 不要误删 405nm / 32bit / 0.5~50μm 这类真实参数值
        t = re.sub(r"^(?:\d+(?:\.\d+)*[.)、:：]\s*|[（(]\d+[）)]\s*|第[一二三四五六七八九十\d]+[项条]\s*)", "", t)

    return t.strip()


def _row_signature(key: str, req: str) -> str:
    """
    去重签名优先使用 requirement 文本。
    这样“检测性能：当SIT...<0.05%”和“重要条款：当SIT...<0.05%”会被视为同一条。
    """
    key_n = _canonicalize_clause_text(key, for_signature=True)
    req_n = _canonicalize_clause_text(req, for_signature=True)
    if req_n and len(req_n) >= 6:
        return req_n
    return f"{key_n}::{req_n}"

def _sanitize_rows_for_section(
    pkg: ProcurementPackage,
    rows: list[dict[str, Any]],
    *,
    expected_category: ClauseCategory | None = None,
) -> list[dict[str, Any]]:
    """清洗章节的行。"""
    cleaned: list[dict[str, Any]] = []
    seen: set[str] = set()
    forbidden = _package_forbidden_terms(pkg.item_name)

    generic_keys = {
        "技术参数与性能要求",
        "技术参数",
        "技术要求",
        "性能要求",
        "参数要求",
    }
    seq_only_pattern = re.compile(
        r"^(?:序号|条款编号|编号)[:：]\s*\*?\d+(?:\.\d+)*$"
    )

    for row in rows:
        key = _safe_text(row.get("key"), "")
        req = _safe_text(row.get("requirement"), "")
        text = f"{key} {req}"

        if not key or not req:
            continue

        # 过滤“技术参数与性能要求：序号：3.1”这类纯编号行
        if key in generic_keys and seq_only_pattern.fullmatch(req):
            continue

        # 把“要求：xxx / 技术要求：xxx”收成真正的正文
        if key in generic_keys:
            req = re.sub(r"^(?:要求|技术要求|性能要求|参数要求)[:：]\s*", "", req).strip()
            if not req:
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
    """将指定模块的公开成员重新导出到当前命名空间。"""
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
        delivery_time = _clean_delivery_text(pkg.delivery_time, "按招标文件约定")
    if pkg.delivery_place:
        delivery_place = _clean_delivery_text(pkg.delivery_place, "采购人指定地点", is_place=True)

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
    """返回默认响应占位符。"""
    mapping = {
        "service": "承诺按采购文件及厂家服务方案执行，提供送货、安装调试、培训、维保及售后响应服务。",
        "config": "承诺所投配置满足招标要求，随机配置及随机文件与投标型号保持一致。",
        "acceptance": "承诺按采购文件及采购人要求配合完成到货、安装调试、试运行和资料移交验收。",
        "documentation": "承诺随货或随投标文件提供与投标型号一致的说明书、合格证、注册证/备案凭证、授权文件等资料。",
    }
    return mapping.get(section_type, "承诺按采购文件要求执行。")

def _default_acceptance_method(text: str) -> str:
    """返回默认验收method。"""
    t = _safe_text(text, "")
    if any(k in t for k in ("到货", "装箱", "外观", "数量")):
        return "到货验收"
    if any(k in t for k in ("安装", "调试", "通电", "开机")):
        return "安装调试验收"
    if any(k in t for k in ("试运行", "性能", "验证")):
        return "试运行/性能验收"
    if any(k in t for k in ("资料", "说明书", "合格证", "注册证", "培训记录")):
        return "资料验收"
    return "按采购文件及采购人要求验收"


def _default_document_supply_method(text: str) -> str:
    """返回默认文档supplymethod。"""
    t = _safe_text(text, "")
    if any(k in t for k in ("随机文件", "说明书", "保修卡", "装箱单")):
        return "随货/另附/电子版"
    if any(k in t for k in ("注册证", "备案凭证", "授权文件", "许可文件")):
        return "随附/投标文件附后"
    if any(k in t for k in ("培训", "验收", "安装调试记录")):
        return "交付时提供"
    return "按采购文件要求提供"

def _smart_default_response(section_type: str, key: str, req: str) -> str:
    """返回默认smart响应。"""
    text = f"{_safe_text(key, '')} {_safe_text(req, '')}"
    text = text.replace("：", " ").replace(":", " ")

    if section_type == "service":
        if "质保期" in text:
            return "承诺质保期满足采购文件要求，自验收合格之日起计算。"
        if any(k in text for k in ("响应", "到场", "电话", "小时", "分钟")):
            return "承诺设售后服务热线，按采购文件要求在约定时限内响应、到场并完成故障处理。"
        if any(k in text for k in ("维修密码", "密码支持")):
            return "承诺按采购文件要求提供维修密码或必要权限支持，不设置不合理限制。"
        if any(k in text for k in ("维修资料", "维护资料", "技术资料", "维修手册")):
            return "承诺提供维修所需技术资料、维护手册及故障排查资料。"
        if any(k in text for k in ("维修工具", "专用工具")):
            return "承诺提供维修所需专用工具或等效支持，保障维保实施。"
        if any(k in text for k in ("升级", "免费升级")):
            return "承诺按采购文件要求提供软件或系统免费升级服务。"
        if any(k in text for k in ("备用机", "替代设备")):
            return "承诺如设备无法及时修复或维修周期过长，按采购文件要求提供备用机或替代设备保障使用。"
        if any(k in text for k in ("配件库", "备件", "零配件", "关键零部件", "更换零件", "耗材")):
            return "承诺具备备件、耗材储备和更换保障能力，确保关键零部件供应及时。"
        if any(k in text for k in ("保养", "维护", "巡检")):
            return "承诺按采购文件要求提供定期维护保养、巡检及使用指导，并提供相应维护资料。"
        if any(k in text for k in ("安装", "调试", "培训", "卸货", "上线")):
            return "承诺负责送货、卸货、安装调试及人员培训，直至设备可正常投入使用。"
        if any(k in text for k in ("联系人", "联系方式", "维修站", "售后服务机构")):
            return "承诺提供售后服务联系人、联系方式及服务机构信息，保障服务可及时触达。"
        if any(k in text for k in ("项目清单", "注册证", "备案", "试剂列表")):
            return "承诺提供可开展项目清单及对应注册证、备案凭证等合规资料。"
        return "承诺按采购文件及厂家服务方案执行，提供送货、安装调试、培训、维保及售后响应服务。"

    if section_type == "config":
        if any(k in text for k in ("装箱", "配置单", "随机配置")):
            return "承诺按装箱配置单和投标型号交付完整标配、选配清单。"
        return "承诺所投配置满足招标要求，随机配置及随机文件与投标型号保持一致。"

    if section_type == "acceptance":
        if any(k in text for k in ("到货", "装箱", "外观", "数量")):
            return "承诺配合采购人完成到货验收，并按装箱单逐项核对品牌、型号、数量、外观及随机资料。"
        if any(k in text for k in ("安装", "调试", "通电", "开机")):
            return "承诺完成安装调试、通电开机及基础功能验证，并配合形成安装调试记录。"
        if any(k in text for k in ("试运行", "性能", "验证")):
            return "承诺按采购文件及采购人要求配合完成试运行或性能验证。"
        if any(k in text for k in ("资料", "说明书", "合格证", "注册证", "培训记录")):
            return "承诺按采购文件要求同步移交验收所需资料，并配合完成资料核验。"
        return "承诺按采购文件及采购人要求配合完成到货、安装调试、试运行和资料移交验收。"

    if section_type == "documentation":
        if any(k in text for k in ("随机文件", "说明书", "保修卡", "装箱单")):
            return "承诺随货或按采购文件要求提供与投标型号一致的说明书、合格证、保修卡、装箱单等随机文件。"
        if any(k in text for k in ("注册证", "备案凭证", "授权文件", "许可文件")):
            return "承诺提供与投标型号一致的注册证、备案凭证、许可文件及授权文件。"
        if any(k in text for k in ("培训", "验收", "安装调试记录")):
            return "承诺在交付及验收阶段同步移交培训、安装调试及验收资料。"
        return "承诺随货或随投标文件提供与投标型号一致的说明书、合格证、注册证/备案凭证、授权文件等资料。"

    return _default_response_placeholder(section_type)


def _normalize_section_response(
    raw_value: Any,
    section_type: str,
    key: str = "",
    req: str = "",
) -> str:
    """归一化章节响应。"""
    text = _safe_text(raw_value, "")
    if not text:
        return _smart_default_response(section_type, key, req)

    generic_pending_markers = (
        _PENDING_BIDDER_RESPONSE,
        "【待填写：投标产品实参】",
        "待补充（投标产品实参）",
        "待核实（需填入投标产品实参）",
        "【待填写：服务承诺】",
        "【待填写：配置响应】",
        "【待填写：响应承诺】",
    )
    if any(marker in text for marker in generic_pending_markers):
        return _smart_default_response(section_type, key, req)

    return text

def _default_evidence_placeholder(section_type: str) -> str:
    """返回默认证据占位符。"""
    mapping = {
        "service": "【待补证：售后服务方案/厂家承诺】",
        "config": "【待补证：配置清单/彩页/说明书】",
        "acceptance": "【待补证：验收方案/验收记录模板/技术资料】",
        "documentation": "【待补证：说明书/合格证/注册证/授权文件】",
    }
    return mapping.get(section_type, "【待补证】")


def _beautify_nontech_key(section_type: str, key: str, req: str) -> str:
    """美化非技术类条目名称的展示文本。"""
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


def _rich_pending(message: str) -> str:
    """生成富底稿场景下的待补占位文本。"""
    return f"【待补：{message}】"


def _resolve_rich_commitment_response(
    requirement: NormalizedRequirement,
    *,
    warranty: str = "",
    evidence_refs: list[Any] | None = None,
) -> str:
    """解析并返回富承诺响应。"""
    text = _safe_text(f"{requirement.param_name} {requirement.raw_text}", "")
    evidence_refs = evidence_refs or []

    if "质保" in text and warranty:
        return warranty

    if evidence_refs:
        return f"详见{_as_text(evidence_refs[0])}"

    return _rich_pending("按采购文件、产品资料或证据材料填写")





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
            tech_content += f"{_rich_pending('根据采购文件逐条补录技术参数响应表')}\n"
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
                config_content += (
                    f"| {idx} | {_markdown_cell(req.param_name)} | {_markdown_cell(req.raw_text)} | "
                    f"{_markdown_cell(_rich_pending('按投标配置清单填写'))} |\n"
                )
        else:
            config_content += f"{_rich_pending('按投标产品配置清单逐项填写')}\n"
        config_content += "\n"

        # ── 分表3: 售后服务响应表 ──
        service_reqs = wctx_by_type.get("service_response") or [
            r for r in pkg_reqs if r.category == ClauseCategory.service_requirement
        ]
        service_content = f"### 包{pkg.package_id} 售后服务响应表\n\n"
        warranty = tender.commercial_terms.warranty_period
        service_content += f"- 质保期：{warranty or _rich_pending('按采购文件或厂家承诺填写')}\n"
        service_content += (
            f"- 证据来源：{_as_text(evidence_refs[0])}\n\n"
            if evidence_refs else
            f"- 证据来源：{_rich_pending('售后承诺函/说明书/服务方案')}\n\n"
        )
        if service_reqs:
            service_content += "| 序号 | 服务项 | 招标要求 | 投标承诺 |\n"
            service_content += "|------|--------|----------|----------|\n"
            for idx, req in enumerate(service_reqs, 1):
                response = _resolve_rich_commitment_response(
                    req,
                    warranty=_as_text(warranty),
                    evidence_refs=evidence_refs,
                )
                service_content += (
                    f"| {idx} | {_markdown_cell(req.param_name)} | {_markdown_cell(req.raw_text)} | "
                    f"{_markdown_cell(response)} |\n"
                )
        else:
            service_content += f"{_rich_pending('按采购文件逐条补录售后服务承诺')}\n"
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
                accept_content += (
                    f"| {idx} | {cat_label} | {_markdown_cell(req.param_name)} | {_markdown_cell(req.raw_text)} | "
                    f"{_markdown_cell(_resolve_rich_commitment_response(req, evidence_refs=evidence_refs))} |\n"
                )
        else:
            accept_content += f"- 验收标准：{_rich_pending('按采购文件或验收方案填写')}\n"
            accept_content += f"- 随机文件：{_rich_pending('补充随机文件清单')}\n"
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
        content += f"本包投标产品为{p_name}。{_rich_pending('补充品牌、型号及生产厂家信息')}\n\n"

    if specs:
        content += "**核心技术参数**：\n\n"
        for idx, (key, value) in enumerate(list(specs.items())[:10], start=1):
            content += f"{idx}. **{key}**：{_as_text(value)}。\n"
        content += "\n"
    else:
        content += f"{_rich_pending('补充关键性能参数')}\n\n"

    # 交付说明
    content += f"### 包{pkg.package_id} 交付说明\n\n"
    content += f"- 交货期：{_clean_delivery_text(pkg.delivery_time, '') or _rich_pending('按采购文件填写交货期限')}\n"
    content += f"- 交货地点：{_clean_delivery_text(pkg.delivery_place, '', is_place=True) or _rich_pending('按采购文件填写交货地点')}\n"
    content += f"- 交货方式：{_rich_pending('按采购文件或投标承诺填写')}\n\n"

    # 培训说明
    content += f"### 包{pkg.package_id} 使用与培训说明\n\n"
    content += f"- 培训对象：{pkg.item_name}操作人员、维护人员\n"
    content += f"- 培训方式：{_rich_pending('按培训方案填写')}\n"
    content += f"- 培训时长：{_rich_pending('按采购文件或投标承诺填写')}\n"
    warranty = tender.commercial_terms.warranty_period
    content += f"- 质保期：{warranty or _rich_pending('按采购文件或厂家承诺填写')}\n\n"

    return content


def _gen_technical(*args, **kwargs):
    """显式禁用旧版技术章节生成入口。"""
    raise RuntimeError(
        "旧结构生成器 _gen_technical 已禁用。请改用 build_format_driven_sections()."
    )

def _build_appendix_service_placeholder(
    tender: TenderDocument,
    packages: list[ProcurementPackage] | None = None,
) -> str:
    """构建appendix服务占位符。"""
    pkgs = packages if packages is not None else tender.packages
    sections: list[str] = ["## 二、技术服务和售后服务的内容及措施"]

    for pkg in pkgs:
        delivery_time = _normalize_commitment_term(_clean_delivery_text(pkg.delivery_time, "") or "按采购文件约定")
        delivery_place = _normalize_commitment_term(_clean_delivery_text(pkg.delivery_place, "", is_place=True) or "采购人指定地点")
        warranty = _normalize_commitment_term(tender.commercial_terms.warranty_period or "按采购文件约定")

        sections.extend([
            f"### 包{pkg.package_id}：{pkg.item_name}",
            "",
            "#### 1. 供货组织措施",
            "我方设立本项目专项供货小组，由项目负责人统筹对接采购人、厂家、物流及安装工程师，"
            "对备货、发运、到货、安装、调试、培训、验收全过程实行节点管理，确保责任到人、进度可控。",
            "",
            "#### 2. 备货与到货前核查措施",
            "发货前逐项核对设备品牌、型号、数量、配置清单、随机资料、注册证/备案凭证、合格证明文件，"
            "确保实际供货内容与投标响应文件及合同约定一致；对外包装、序列号、附件完整性进行出库复核。",
            "",
            "#### 3. 运输与交付措施",
            f"我方承诺按照“{delivery_time}”要求完成供货，并将设备安全运送至“{delivery_place}”。"
            "运输过程采用原厂包装或符合医疗设备运输要求的专业包装方式，防止受潮、震动、碰撞和污染。"
            "到货后由双方共同进行外观检查、数量清点和装箱清单核对。",
            "",
            "#### 4. 安装调试措施",
            "设备到货验收合格后，我方安排专业技术人员到场完成安装、通电、基础功能测试和参数校准，"
            "并按采购文件技术要求逐项验证主要功能和配置。安装调试完成后，形成安装调试记录并交采购人确认。",
            "",
            "#### 5. 培训措施",
            "我方将在设备安装调试完成后，对采购人操作人员和日常管理人员开展现场培训，"
            "内容包括设备结构组成、操作流程、注意事项、日常维护、常见故障处理及安全管理要求。"
            "培训结束后可提供培训签到记录或培训证明材料。",
            "",
            "#### 6. 验收配合措施",
            "我方按采购文件约定配合开展到货验收、安装调试验收和功能性能验收，"
            "提交说明书、合格证、装箱单、保修文件及其他随机资料，确保验收资料齐套、过程留痕、结论明确。",
            "",
            "#### 7. 售后服务措施",
            f"质保期按采购文件要求执行，为“{warranty}”。质保期内提供技术支持、故障响应、维修维护、"
            "配件供应及升级服务；对采购文件明确要求的响应时限、维保频次、培训及技术支持内容，"
            "均严格按采购文件及厂家承诺执行。",
            "",
        ])

    return "\n".join(sections).strip()

def _build_product_brochure_checklist(packages: list[ProcurementPackage]) -> str:
    """构建产品brochurechecklist。"""
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
    """构建energycertchecklist。"""
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
    """构建quality报告checklist。"""
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



def _gen_appendix(*args, **kwargs):
    """显式禁用旧版附件章节生成入口。"""
    raise RuntimeError(
        "旧结构生成器 _gen_appendix 已禁用。请改用 build_format_driven_sections()."
    )
