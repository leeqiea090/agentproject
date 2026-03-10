"""一键投标文件生成服务（按固定模板生成，强调格式稳定性）"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from langchain_openai import ChatOpenAI

from app.schemas import BidDocumentSection, ProcurementPackage, TenderDocument

logger = logging.getLogger(__name__)

_MAX_TECH_ROWS_PER_PACKAGE = 80
_PACKAGE_SCOPE_BEFORE_LINES = 8
_PACKAGE_SCOPE_AFTER_LINES = 80

# ── 详细度目标（Detail Targets）──
_DETAIL_TARGETS = {
    "technical_atomic_clauses_per_package": 15,
    "deviation_table_min_rows": 10,
    "narrative_sections_min_chars": 200,
    "evidence_per_item": 1,
    "config_items_min": 5,
    "config_description_min_sentences": 1,
}

# ── 富展开模式 ──
_RICH_EXPANSION_MODE = True

_COMPANY = "[投标方公司名称]"
_LEGAL_REP = "[法定代表人]"
_AUTHORIZED_REP = "[授权代表]"
_PHONE = "[联系电话]"
_ADDRESS = "[联系地址]"

_TEMPLATE_POLLUTION_PREFIXES = (
    "你是",
    "请生成",
    "输出json",
    "输出JSON",
    "markdown格式",
    "Markdown格式",
    "根据以上",
    "以下是",
    "as an ai",
)
_TEMPLATE_POLLUTION_TOKENS = ("{{", "}}", "<!--", "-->")
_TEMPLATE_POLLUTION_INFIX_KEYWORDS = (
    "system:",
    "assistant:",
    "user:",
    "只允许输出",
    "请严格按照",
    "请按以下",
    "根据上述",
    "输出格式",
    "返回json",
    "判定结果：",
    "原文长度",
    "用于内容校验",
    "debug:",
    "trace:",
)
_HARD_REQUIREMENT_MARKERS = ("≥", "≤", ">=", "<=", "不低于", "不少于", "不高于", "不大于", "至少")
_TECH_SECTION_HINTS = ("技术参数", "技术要求", "采购需求", "性能要求", "配置要求", "参数要求", "技术指标")
_GENERIC_TECH_KEYS = ("技术参数", "主要技术参数", "性能要求", "技术指标", "参数要求", "核心技术参数")
_CONFIG_REQUIREMENT_KEYS = (
    "设备配置",
    "配置与配件",
    "设备配置与配件",
    "配置清单",
    "主要配置",
    "标准配置",
    "装箱配置",
    "装箱配置单",
)
_CONFIG_SECTION_HINTS = _CONFIG_REQUIREMENT_KEYS
_TECH_KEYWORDS = (
    "激光", "荧光", "通道", "散射", "检测", "样本", "进样", "上样", "分析", "分辨率",
    "灵敏度", "软件", "模块", "配置", "波长", "流速", "补偿", "绝对计数", "体积",
    "温度", "接口", "兼容", "系统", "主机", "试剂", "耗材", "数据", "报表",
)
_NON_TECH_KEYS = (
    "项目名称", "项目编号", "采购人", "采购单位", "代理机构", "采购方式", "预算", "最高限价",
    "联系人", "联系电话", "地址", "日期", "时间", "供应商", "投标人", "评分", "资格",
    "商务", "售后", "付款", "质保", "交货地点", "交货时间", "开标", "响应文件",
)
_NON_TECH_CONTENT_HINTS = (
    "投标文件格式特殊要求", "投标文件格式", "响应文件格式", "正本与副本", "A4纸", "装订成册",
    "签字确认", "科室负责人", "目录编制", "页码要求",
)
_TECH_EXIT_HINTS = (
    "评分标准", "评分办法", "评分", "分值", "得分", "扣分", "商务部分", "商务条款",
    "合同条款", "付款方式", "包装运输方案", "配货单", "设备配置及参数清单", "耗材名称",
    "投标人须知", "乙方承诺", "甲方", "乙方", "争议的解决", "违约责任", "中国政府采购网",
    "技术部分 /", "技术部分", "投标文件格式特殊要求", "投标文件格式", "响应文件格式",
    "正本与副本", "装订成册", "装箱配置", "装箱配置单", "配置清单", "标准配置",
    "质保", "售后", "质疑", "投诉",
)
_TECH_KEY_EXCLUDES = {
    "乙方承诺",
    "甲方承诺",
    "序号",
    "耗材名称",
    "设备配置及参数清单",
    "包装运输方案",
}
_REGION_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("北京市", ("北京",)),
    ("天津市", ("天津",)),
    ("上海市", ("上海",)),
    ("重庆市", ("重庆",)),
    ("河北省", ("河北", "石家庄")),
    ("山西省", ("山西", "太原")),
    ("辽宁省", ("辽宁", "沈阳")),
    ("吉林省", ("吉林", "长春")),
    ("黑龙江省", ("黑龙江", "哈尔滨")),
    ("江苏省", ("江苏", "南京")),
    ("浙江省", ("浙江", "杭州")),
    ("安徽省", ("安徽", "合肥")),
    ("福建省", ("福建", "福州", "厦门")),
    ("江西省", ("江西", "南昌")),
    ("山东省", ("山东", "济南", "青岛")),
    ("河南省", ("河南", "郑州")),
    ("湖北省", ("湖北", "武汉")),
    ("湖南省", ("湖南", "长沙")),
    ("广东省", ("广东", "广州", "深圳")),
    ("海南省", ("海南", "海口")),
    ("四川省", ("四川", "成都")),
    ("贵州省", ("贵州", "贵阳")),
    ("云南省", ("云南", "昆明")),
    ("陕西省", ("陕西", "西安")),
    ("甘肃省", ("甘肃", "兰州")),
    ("青海省", ("青海", "西宁")),
    ("内蒙古自治区", ("内蒙古", "呼和浩特")),
    ("广西壮族自治区", ("广西", "南宁")),
    ("西藏自治区", ("西藏", "拉萨")),
    ("宁夏回族自治区", ("宁夏", "银川")),
    ("新疆维吾尔自治区", ("新疆", "乌鲁木齐")),
)
_CONFIG_EXIT_HINTS = (
    "质保",
    "售后",
    "付款方式",
    "商务条款",
    "合同条款",
    "评分标准",
    "评分办法",
    "技术参数",
    "技术要求",
    "质疑",
    "投诉",
    "投标人须知",
)
_CONFIG_ITEM_UNITS = ("台套", "台", "套", "个", "把", "本", "件", "组", "副", "支", "块", "张", "盒", "瓶", "根", "条", "份", "只")
_PENDING_BIDDER_RESPONSE = "待核实（需填入投标产品实参）"


def _today() -> str:
    return datetime.now().strftime("%Y年%m月%d日")


def _safe_text(text: str | None, default: str = "详见招标文件") -> str:
    if text is None:
        return default
    stripped = str(text).strip()
    return stripped or default


def _normalize_commitment_term(text: str | None, default: str = "按招标文件及合同约定执行") -> str:
    normalized = _safe_text(text, default)
    if not normalized:
        return default
    if any(token in normalized for token in ("[待填写]", "____", "＿", "乙方", "甲方在支付", "本合同项下")):
        return default
    if re.search(r"为\s{2,}[年月日天个]", normalized):
        return default
    if len(normalized) > 48 and any(token in normalized for token in ("合同", "发票", "违约")):
        return default
    return normalized


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        parts = [f"{k}：{_as_text(v)}" for k, v in value.items()]
        return "；".join(part for part in parts if part)
    if isinstance(value, list):
        return "；".join(_as_text(item) for item in value if _as_text(item))
    return str(value).strip()


def _fmt_money(amount: float) -> str:
    return f"{amount:,.2f}"


def _tender_context_text(tender: TenderDocument) -> str:
    eval_items = " ".join(f"{k}:{v}" for k, v in tender.evaluation_criteria.items())
    package_names = " ".join(pkg.item_name for pkg in tender.packages)
    package_requirements = " ".join(
        " ".join(str(v) for v in pkg.technical_requirements.values())
        for pkg in tender.packages
    )
    return " ".join(
        [
            tender.project_name,
            tender.project_number,
            tender.purchaser,
            tender.agency,
            tender.procurement_type,
            tender.special_requirements,
            eval_items,
            package_names,
            package_requirements,
        ]
    )


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _contains_non_technical_content(text: str) -> bool:
    return _contains_any(text, _NON_TECH_KEYS) or _contains_any(text, _NON_TECH_CONTENT_HINTS)


def _is_medical_project(tender: TenderDocument) -> bool:
    context = _tender_context_text(tender)
    return _contains_any(
        context,
        ("医疗", "器械", "检验", "试剂", "诊断", "流式", "医院"),
    )


def _requires_sme_declaration(tender: TenderDocument) -> bool:
    context = _tender_context_text(tender)
    return _contains_any(
        context,
        ("中小企业", "小微", "监狱企业", "残疾人福利性单位", "价格扣除", "声明函"),
    )


def _allow_consortium(tender: TenderDocument) -> bool:
    context = _tender_context_text(tender)
    if "不接受联合体" in context:
        return False
    return "联合体" in context and _contains_any(context, ("允许联合体", "接受联合体", "可联合体"))


def _has_imported_clues(tender: TenderDocument) -> bool:
    context = _tender_context_text(tender)
    return _contains_any(context, ("进口", "原装", "境外"))


def _detect_procurement_region(tender: TenderDocument) -> str:
    preferred_contexts = [
        _safe_text(tender.purchaser, ""),
        _safe_text(tender.project_name, ""),
    ]
    fallback_contexts = [
        _safe_text(tender.special_requirements, ""),
        _safe_text(tender.agency, ""),
    ]

    for context in [*preferred_contexts, *fallback_contexts]:
        for region, keywords in _REGION_KEYWORDS:
            if _contains_any(context, keywords):
                return region
    return ""


def _supplier_commitment_title(tender: TenderDocument) -> str:
    region = _detect_procurement_region(tender)
    return f"{region}政府采购供应商资格承诺函" if region else "政府采购供应商资格承诺函"


def _package_scope(tender: TenderDocument) -> str:
    if not tender.packages:
        return "全部包"
    return "、".join(f"包{pkg.package_id}" for pkg in tender.packages)


def _infer_package_quantity(pkg: ProcurementPackage, tender_raw: str) -> int:
    package_scope = _extract_package_scope_text(pkg, tender_raw)
    search_texts = [package_scope, tender_raw]
    patterns = (
        r"设备总台数\s*[:：;；]?\s*(\d+)\s*台",
        r"采购数量\s*[:：;；]?\s*(\d+)\s*(?:台|套|个|把|件|组|副|本)?",
        r"数量\s*[:：;；]?\s*(\d+)\s*(?:台|套|个|把|件|组|副|本)",
    )

    for text in search_texts:
        if not text.strip():
            continue
        for raw_line in text.splitlines():
            normalized = _normalize_requirement_line(raw_line)
            if not normalized:
                continue
            for pattern in patterns:
                match = re.search(pattern, normalized)
                if not match:
                    continue
                quantity = int(match.group(1))
                if quantity > 0:
                    return quantity

    return max(1, pkg.quantity)


def _package_detail_lines(tender: TenderDocument, tender_raw: str) -> str:
    if not tender.packages:
        return "- 包信息：详见招标文件。"

    lines: list[str] = []
    for pkg in tender.packages:
        delivery = _safe_text(pkg.delivery_time, "按招标文件约定")
        place = _safe_text(pkg.delivery_place, "采购人指定地点")
        quantity = _infer_package_quantity(pkg, tender_raw)
        lines.append(
            f"- 包{pkg.package_id}：{pkg.item_name}；数量：{quantity}；预算：{_fmt_money(pkg.budget)}元；"
            f"交货期：{delivery}；交货地点：{place}"
        )
    return "\n".join(lines)


def _quote_overview_table(tender: TenderDocument, tender_raw: str) -> str:
    headers = [
        "| 序号(包号) | 货物名称 | 数量 | 预算金额(元) | 投标报价(元) | 交货期 |",
        "|---|---|---:|---:|---:|---|",
    ]
    rows: list[str] = []

    if tender.packages:
        total_budget = 0.0
        for idx, pkg in enumerate(tender.packages, start=1):
            total_budget += pkg.budget
            quantity = _infer_package_quantity(pkg, tender_raw)
            rows.append(
                f"| {idx}（{pkg.package_id}） | {pkg.item_name} | {quantity} | "
                f"{_fmt_money(pkg.budget)} | [待填写] | {_safe_text(pkg.delivery_time, '按招标文件约定')} |"
            )
        rows.append(
            f"|  | **合计** |  | **{_fmt_money(total_budget)}** | **[待填写]** |  |"
        )
    else:
        rows.append("| 1 | [待填写] | [待填写] | [待填写] | [待填写] | [待填写] |")

    return "\n".join(headers + rows)


def _flatten_requirements(pkg: ProcurementPackage) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for key, value in pkg.technical_requirements.items():
        k = _safe_text(str(key), "技术参数")
        v = _safe_text(_as_text(value), "详见招标文件")
        items.extend(_expand_requirement_entry(k, v))
    return _dedupe_requirement_pairs(items)


def _is_sparse_technical_requirements(pkg: ProcurementPackage) -> bool:
    requirements = _flatten_requirements(pkg)
    meaningful = [
        key
        for key, _ in requirements
        if key and key not in {"核心技术参数", "其他参数", "技术参数"}
    ]
    return len(meaningful) < 2


def _dedupe_requirement_pairs(pairs: list[tuple[str, str]]) -> list[tuple[str, str]]:
    deduped: list[tuple[str, str]] = []
    seen: set[str] = set()
    for key, val in pairs:
        dedup_key = f"{key}::{val}"
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        deduped.append((key, val))
    return deduped


def _split_compound_requirement(key: str, value: str) -> list[tuple[str, str]]:
    """将复合条款拆分为原子级条目。

    处理如 "具备A功能、支持B和C" 这样的复合条款，
    按中文列举符号（、）以及连接词（同时、并且、以及）拆分，
    但保留数值范围和括号内内容不拆。
    """
    if not value or len(value) < 8:
        return [(key, value)]
    # 不拆分包含数值范围的纯参数值（如 "≥3个"、"10-20mL"）
    if re.fullmatch(r"[≥≤><\d\s.+\-~～至到%％μmLnNgGhHzZkKwWtT/*()（）]+", value.strip()):
        return [(key, value)]

    _VERB_MARKERS = ("具备", "支持", "提供", "满足", "采用", "配置", "配备", "可", "能够", "应", "须")
    _COMPOUND_DELIMITERS = r"(?<=[）\)\u4e00-\u9fff])、(?=[\u4e00-\u9fff])"

    fragments = re.split(_COMPOUND_DELIMITERS, value)
    if len(fragments) < 2:
        fragments = re.split(r"(?:同时|并且|以及|；|;)", value)
    if len(fragments) < 2:
        return [(key, value)]

    results: list[tuple[str, str]] = []
    for frag in fragments:
        frag = frag.strip(" ，,；;。、")
        if not frag or len(frag) < 3:
            continue
        pair = _extract_requirement_pair(frag)
        if pair:
            results.append(pair)
        else:
            results.append((key, frag))

    return results if len(results) > 1 else [(key, value)]


def _expand_requirement_entry(key: str, value: str) -> list[tuple[str, str]]:
    # 第一阶段：如果是笼统 key，按 ；; 拆分
    if any(marker in key for marker in _GENERIC_TECH_KEYS) and ("；" in value or ";" in value):
        expanded: list[tuple[str, str]] = []
        for fragment in re.split(r"[；;]", value):
            pair = _extract_requirement_pair(fragment)
            if pair:
                expanded.append(pair)
        if len(expanded) >= 2:
            return _dedupe_requirement_pairs(expanded)

    # 第二阶段：对每个条目做复合条款拆分（处理 、同时、并且 等中文列举）
    compound_results = _split_compound_requirement(key, value)
    if len(compound_results) > 1:
        return _dedupe_requirement_pairs(compound_results)

    return [(key, value)]


def _normalize_requirement_line(line: str) -> str:
    normalized = line.replace("\t", " ").replace("\r", " ").strip()
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = re.sub(r"^[★▲■●]\s*", "", normalized)
    normalized = re.sub(r"^\d+(?:\.\d+){0,5}\s*", "", normalized)
    normalized = re.sub(r"^[（(]?\d+[）)]\s*", "", normalized)
    normalized = re.sub(r"^\d+(?=[A-Za-z\u4e00-\u9fa5])", "", normalized)
    normalized = re.sub(r"^[（(]?[一二三四五六七八九十\d]+[）).、]\s*", "", normalized)
    normalized = re.sub(r"^(?:[-*•]|第\d+[项条]?)\s*", "", normalized)
    return normalized.strip(" ;；")


def _is_outline_token(text: str) -> bool:
    return bool(re.fullmatch(r"[（(]?\d+(?:\.\d+){0,5}[）)]?", text.strip()))


def _looks_like_technical_requirement(text: str) -> bool:
    if any(marker in text for marker in _HARD_REQUIREMENT_MARKERS):
        return True
    if _contains_any(text, _TECH_KEYWORDS):
        return True
    return bool(re.search(r"\d", text) and _contains_any(text, ("支持", "具备", "配置", "提供", "采用", "满足")))


def _extract_requirement_pair(fragment: str) -> tuple[str, str] | None:
    normalized = _normalize_requirement_line(fragment)
    normalized = re.sub(r"^(?:采购需求|技术参数|技术要求|性能要求|配置要求|参数要求|技术指标)[:：]?\s*", "", normalized)
    if len(normalized) < 4 or len(normalized) > 120:
        return None
    if _contains_non_technical_content(normalized):
        return None
    if not _looks_like_technical_requirement(normalized):
        return None

    table_cells = [cell.strip() for cell in normalized.split("|") if cell.strip()]
    if len(table_cells) >= 2:
        key_cell = table_cells[0]
        val_cell = table_cells[1]
        if _is_outline_token(key_cell) and len(table_cells) >= 3:
            key_cell = table_cells[1]
            val_cell = table_cells[2]
        normalized = f"{key_cell}：{val_cell}"

    match = re.match(r"^(?P<key>[A-Za-z0-9\u4e00-\u9fa5（）()/+.\-]{2,30})[：:]\s*(?P<val>.+)$", normalized)
    if match:
        key = match.group("key").strip()
        key = re.sub(r"^[★▲■●]\s*", "", key)
        key = re.sub(r"^\d+(?=[A-Za-z\u4e00-\u9fa5])", "", key)
        val = match.group("val").strip(" ；;。")
        if (
            key
            and val
            and not _contains_non_technical_content(key)
            and not _contains_non_technical_content(val)
            and key not in _TECH_KEY_EXCLUDES
            and not re.fullmatch(r"[（(]?\d+分[）)]?", key)
            and not _is_outline_token(key)
        ):
            return key, val

    comp_match = re.match(
        r"^(?P<key>[A-Za-z0-9\u4e00-\u9fa5（）()/+.\-]{2,30})\s*(?P<val>(?:≥|≤|>=|<=|不低于|不少于|不高于|不大于|至少|不超过).+)$",
        normalized,
    )
    if comp_match:
        key = comp_match.group("key").strip()
        key = re.sub(r"^[★▲■●]\s*", "", key)
        key = re.sub(r"^\d+(?=[A-Za-z\u4e00-\u9fa5])", "", key)
        val = comp_match.group("val").strip(" ；;。")
        if (
            key in _TECH_KEY_EXCLUDES
            or re.fullmatch(r"[（(]?\d+分[）)]?", key)
            or _is_outline_token(key)
            or _contains_non_technical_content(key)
            or _contains_non_technical_content(val)
        ):
            return None
        return key, val

    return None


def _extract_requirements_from_raw(pkg: ProcurementPackage, tender_raw: str) -> list[tuple[str, str]]:
    if not tender_raw.strip():
        return []

    item_tokens = [token for token in re.split(r"[，,、；;（）()\\s/]+", pkg.item_name) if len(token) >= 2]
    relevant_window = 0
    in_tech_scope = False
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()

    for raw_line in tender_raw.splitlines():
        normalized = _normalize_requirement_line(raw_line)
        if not normalized:
            relevant_window = max(0, relevant_window - 1)
            in_tech_scope = False if relevant_window == 0 else in_tech_scope
            continue

        if _contains_any(normalized, _TECH_EXIT_HINTS):
            in_tech_scope = False
            relevant_window = 0
            continue

        if _contains_any(normalized, _TECH_SECTION_HINTS):
            in_tech_scope = True
            relevant_window = 18

        if item_tokens and any(token in normalized for token in item_tokens):
            relevant_window = 12

        scoped = in_tech_scope or relevant_window > 0
        if relevant_window > 0:
            relevant_window -= 1

        if not scoped and not _looks_like_technical_requirement(normalized):
            continue

        fragments = [frag for frag in re.split(r"[；;。]", normalized) if frag.strip()]
        for fragment in fragments:
            if _contains_any(fragment, _TECH_EXIT_HINTS):
                continue
            pair = _extract_requirement_pair(fragment)
            if not pair:
                continue
            key, val = pair
            dedup_key = f"{key}::{val}"
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            pairs.append(pair)
            if len(pairs) >= _MAX_TECH_ROWS_PER_PACKAGE:
                return pairs

    return pairs


def _effective_requirements(pkg: ProcurementPackage, tender_raw: str) -> list[tuple[str, str]]:
    requirements = _flatten_requirements(pkg)
    if not _is_sparse_technical_requirements(pkg):
        # Still atomize even non-sparse requirements
        return _atomize_requirements(requirements)

    package_scoped_raw = _extract_package_technical_scope_text(pkg, tender_raw)
    extra_pairs = _extract_requirements_from_raw(pkg, package_scoped_raw)
    if not extra_pairs:
        return _atomize_requirements(requirements)

    existing_keys = {key for key, _ in requirements}
    merged = list(requirements)
    for key, val in extra_pairs:
        if key in existing_keys:
            continue
        merged.append((key, val))
        existing_keys.add(key)

    return _atomize_requirements(merged)


def _atomize_requirement(key: str, value: str) -> list[tuple[str, str]]:
    """将复合技术要求拆分成原子级条款。

    规则：
    - 一个句子里有多个参数（用、或；分隔），拆成多条
    - "技术参数总括"这种合并项不允许进入最终技术偏离表
    - 一个表格里一行一个要求，就一行一条
    """
    # 跳过总括性/通用项
    _GENERIC_SUMMARY_KEYS = ("技术参数总括", "技术参数汇总", "技术要求总述", "整体要求", "总体要求", "参数一览")
    if any(gk in key for gk in _GENERIC_SUMMARY_KEYS):
        return []

    normalized_val = _as_text(value)
    if not normalized_val or len(normalized_val) < 4:
        return [(key, normalized_val or "详见招标文件")]

    # 检测是否含多个参数（用、；分隔，且每段含数值或技术关键词）
    # 分隔符：；、，或分号后跟数字/技术关键词
    segments = re.split(r"[；;]", normalized_val)
    if len(segments) <= 1:
        # 尝试用、分隔（仅当每段都含技术内容）
        sub_segments = re.split(r"[、]", normalized_val)
        if len(sub_segments) >= 3 and all(
            any(m in seg for m in _HARD_REQUIREMENT_MARKERS) or re.search(r"\d", seg)
            for seg in sub_segments
        ):
            segments = sub_segments

    if len(segments) <= 1:
        return [(key, normalized_val)]

    # 拆分成原子条款
    results: list[tuple[str, str]] = []
    for idx, segment in enumerate(segments, start=1):
        seg = segment.strip()
        if not seg or len(seg) < 3:
            continue
        # 尝试从 segment 中提取 sub_key:sub_val
        pair = _extract_requirement_pair(seg)
        if pair:
            results.append(pair)
        else:
            sub_key = f"{key}（{idx}）" if len(segments) > 1 else key
            results.append((sub_key, seg))

    return results if results else [(key, normalized_val)]


def _atomize_requirements(requirements: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """对需求列表执行原子化拆分。"""
    atomized: list[tuple[str, str]] = []
    seen: set[str] = set()
    for key, val in requirements:
        for sub_key, sub_val in _atomize_requirement(key, val):
            dedup = f"{sub_key}::{sub_val}"
            if dedup in seen:
                continue
            seen.add(dedup)
            atomized.append((sub_key, sub_val))
    return atomized


def _markdown_cell(text: str) -> str:
    normalized = re.sub(r"\s+", " ", str(text).strip())
    return normalized.replace("|", "/")


def _extract_match_tokens(*texts: str) -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()
    for text in texts:
        raw_tokens = re.split(r"[，,、；;：:（）()【】\\[\\]\\s/\\\\]+", text)
        for token in raw_tokens:
            normalized = token.strip()
            if len(normalized) < 2:
                continue
            if normalized.isdigit():
                continue
            if normalized in seen:
                continue
            seen.add(normalized)
            tokens.append(normalized)
    tokens.sort(key=len, reverse=True)
    return tokens


def _extract_section_blocks(text: str, start_hints: tuple[str, ...], exit_hints: tuple[str, ...], max_lines: int = 80) -> list[str]:
    lines = text.splitlines()
    if not lines:
        return []

    blocks: list[str] = []
    current: list[str] = []
    started = False

    for raw_line in lines:
        normalized = _normalize_requirement_line(raw_line)
        if not normalized:
            continue

        if not started and _contains_any(normalized, start_hints):
            started = True
            current = [normalized]
            continue

        if not started:
            continue

        if _contains_any(normalized, exit_hints) and not _contains_any(normalized, start_hints):
            if current:
                blocks.append("\n".join(current).strip())
            current = []
            started = False
            continue

        current.append(normalized)
        if len(current) >= max_lines:
            blocks.append("\n".join(current).strip())
            current = []
            started = False

    if started and current:
        blocks.append("\n".join(current).strip())
    return [block for block in blocks if block]


def _extract_package_technical_scope_text(
    pkg: ProcurementPackage,
    tender_raw: str,
    other_package_names: tuple[str, ...] = (),
) -> str:
    package_scope = _extract_package_scope_text(pkg, tender_raw, other_package_names=other_package_names)
    blocks = _extract_section_blocks(
        package_scope,
        start_hints=_TECH_SECTION_HINTS,
        exit_hints=(*_TECH_EXIT_HINTS, *_CONFIG_SECTION_HINTS),
        max_lines=80,
    )
    return "\n".join(blocks).strip() if blocks else package_scope


def _extract_package_configuration_scope_text(
    pkg: ProcurementPackage,
    tender_raw: str,
    other_package_names: tuple[str, ...] = (),
) -> str:
    package_scope = _extract_package_scope_text(pkg, tender_raw, other_package_names=other_package_names)
    blocks = _extract_section_blocks(
        package_scope,
        start_hints=_CONFIG_SECTION_HINTS,
        exit_hints=_CONFIG_EXIT_HINTS,
        max_lines=60,
    )
    return "\n".join(blocks).strip()


def _trim_evidence_snippet(snippet: str, anchor: str) -> str:
    normalized = snippet
    if anchor:
        anchor_pos = normalized.find(anchor)
        if anchor_pos >= 0:
            normalized = normalized[anchor_pos:]

    cut_positions: list[int] = []
    for marker in _TECH_EXIT_HINTS:
        pos = normalized.find(marker)
        if pos > max(8, len(anchor)):
            cut_positions.append(pos)
    if cut_positions:
        normalized = normalized[:min(cut_positions)]

    normalized = normalized.strip(" ；;，,。/")
    return normalized


def _extract_package_scope_text(
    pkg: ProcurementPackage,
    tender_raw: str,
    other_package_names: tuple[str, ...] = (),
) -> str:
    text = tender_raw or ""
    if not text.strip():
        return text

    lines = text.splitlines()
    if not lines:
        return text

    item_tokens = [pkg.item_name, *_extract_match_tokens(pkg.item_name)]
    # Build tokens for other packages' item names to detect cross-package boundaries
    other_tokens: list[str] = []
    for name in other_package_names:
        other_tokens.extend(t for t in _extract_match_tokens(name) if len(t) >= 3 and t not in item_tokens)
    package_markers = {
        f"包{pkg.package_id}",
        f"第{pkg.package_id}包",
        f"{pkg.package_id}包",
    }
    package_candidates: list[tuple[int, int]] = []
    token_candidates: list[tuple[int, int]] = []

    for idx, raw_line in enumerate(lines):
        normalized = _normalize_requirement_line(raw_line)
        if not normalized:
            continue

        score = 0
        has_package_marker = any(marker in normalized for marker in package_markers)
        if any(token and token in normalized for token in item_tokens):
            score += 3
        if has_package_marker:
            score += 2
        lookahead = " ".join(lines[idx:min(len(lines), idx + 12)])
        if score and _contains_any(lookahead, _TECH_SECTION_HINTS):
            score += 2

        if score:
            if has_package_marker:
                package_candidates.append((score, idx))
            else:
                token_candidates.append((score, idx))

    candidate_indexes = package_candidates or token_candidates

    if not candidate_indexes:
        return text

    scopes: list[str] = []
    seen_scopes: set[str] = set()
    for _, idx in sorted(candidate_indexes, reverse=True)[:3]:
        current_line = _normalize_requirement_line(lines[idx])
        start = idx
        if not any(marker in current_line for marker in package_markers):
            while start > 0 and idx - start < _PACKAGE_SCOPE_BEFORE_LINES:
                previous = _normalize_requirement_line(lines[start - 1])
                if previous and re.search(r"(?:包\s*\d+|第\s*\d+\s*包|\d+\s*包)", previous) and not any(
                    marker in previous for marker in package_markers
                ):
                    break
                start -= 1

        end = idx + 1
        while end < len(lines) and end - idx < _PACKAGE_SCOPE_AFTER_LINES:
            following = _normalize_requirement_line(lines[end])
            if following and re.search(r"(?:包\s*\d+|第\s*\d+\s*包|\d+\s*包)", following) and not any(
                marker in following for marker in package_markers
            ):
                break
            # Cross-package boundary: break if another package's item_name appears
            if following and other_tokens and any(t in following for t in other_tokens):
                break
            end += 1

        scope = "\n".join(lines[start:end]).strip()
        if not scope or scope in seen_scopes:
            continue
        seen_scopes.add(scope)
        scopes.append(scope)

    return "\n".join(scopes) if scopes else text


def _build_loose_match_pattern(text: str) -> str:
    pieces = [re.escape(piece) for piece in re.split(r"\s+", text.strip()) if piece]
    return r"\s*".join(pieces)


def _find_requirement_pair_position(text: str, req_key: str, req_val: str) -> tuple[int, str]:
    key_pattern = _build_loose_match_pattern(req_key)
    val_pattern = _build_loose_match_pattern(req_val)
    if not key_pattern or not val_pattern:
        return -1, ""

    patterns = (
        rf"{key_pattern}\s*[:：]?\s*{val_pattern}",
        rf"{key_pattern}[^\n]{{0,24}}{val_pattern}",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.start(), match.group(0)
    return -1, ""


def _find_evidence_position(text: str, candidates: list[str]) -> tuple[int, str]:
    idx = -1
    matched = ""
    candidates = [
        candidate.strip()
        for candidate in candidates
        if candidate and candidate.strip()
    ]
    lowered = text.lower()
    for candidate in candidates:
        pos = lowered.find(candidate.lower())
        if pos >= 0:
            idx = pos
            matched = candidate
            break
    return idx, matched


def _extract_evidence_snippet(package_raw: str, req_key: str, req_val: str, fallback_raw: str = "") -> tuple[str, str, bool]:
    source = "招标原文片段"
    if not package_raw.strip() and not fallback_raw.strip():
        quote = f"{_markdown_cell(req_key)}：{_markdown_cell(req_val)}（依据结构化解析结果）"
        return source, quote, False

    idx = -1
    matched = ""
    text = ""
    if package_raw.strip():
        idx, matched = _find_requirement_pair_position(package_raw, req_key, req_val)
        if idx >= 0:
            text = package_raw
        else:
            relaxed_candidates = [req_key, *_extract_match_tokens(req_key, req_val)[:6]]
            idx, matched = _find_evidence_position(package_raw, relaxed_candidates)
            if idx >= 0:
                text = package_raw

    if idx < 0 and fallback_raw.strip() and fallback_raw != package_raw:
        idx, matched = _find_requirement_pair_position(fallback_raw, req_key, req_val)
        if idx >= 0:
            text = fallback_raw

    if idx < 0:
        quote = f"{_markdown_cell(req_key)}：{_markdown_cell(req_val)}（原文未定位到完全同名片段）"
        return source, quote, False

    start = max(0, idx - 24)
    end = min(len(text), idx + max(24, len(matched)) + 36)
    snippet = text[start:end].replace("\n", " ").replace("\r", " ")
    snippet = re.sub(r"\s+", " ", snippet).strip()
    snippet = _trim_evidence_snippet(snippet, matched)
    if len(snippet) > 120:
        snippet = snippet[:120] + "..."
    return source, _markdown_cell(snippet), True


def _build_response_commitment(req_key: str, req_val: str) -> str:
    key = _markdown_cell(req_key)
    value = _markdown_cell(req_val)
    if any(marker in value for marker in _HARD_REQUIREMENT_MARKERS):
        return f"承诺满足“{key}”且指标不低于“{value}”，按招标条款逐项验收。"
    return f"承诺满足“{key}：{value}”，交付时提供对应技术资料并配合验收。"


def _format_payment_execution_line(payment: str) -> str:
    if payment == "按招标文件及合同约定执行":
        return "6. 商务执行：付款方式按招标文件及合同约定执行。"
    return f"6. 商务执行：付款方式按“{payment}”执行。"


def _fuzzy_spec_lookup(product: Any, req_key: str) -> str:
    """在 product.specifications 中做模糊匹配，返回匹配到的值或空字符串。"""
    if product is None:
        return ""
    specs = getattr(product, "specifications", None) or {}
    if not specs:
        return ""
    normalized_key = _as_text(req_key)
    if not normalized_key:
        return ""
    # Exact match
    if normalized_key in specs:
        return _as_text(specs[normalized_key])
    # Short key match
    short_key = normalized_key.split("：", 1)[0].strip()
    if short_key in specs:
        return _as_text(specs[short_key])
    # Substring match
    for spec_key, spec_val in specs.items():
        k = _as_text(spec_key)
        if not k:
            continue
        if k in normalized_key or normalized_key in k:
            return _as_text(spec_val)
    # Token overlap match
    key_tokens = [t for t in re.split(r"[，,、；;：:（）()\[\]\s/]+", short_key) if len(t) >= 2]
    if key_tokens:
        for spec_key, spec_val in specs.items():
            k = _as_text(spec_key)
            if k and all(t in k for t in key_tokens[:3]):
                return _as_text(spec_val)
    return ""


_CAPABILITY_MARKERS = ("具备", "支持", "可", "能够", "提供", "配备", "配置", "采用", "满足", "兼容", "允许")


def _extract_numeric_with_unit(text: str) -> tuple[float | None, str]:
    """从文本中提取数值和单位。"""
    match = re.search(r"(-?\d+(?:\.\d+)?)\s*([^\d\s，,；;。、≥≤><]+)?", _as_text(text))
    if not match:
        return None, ""
    try:
        value = float(match.group(1))
    except ValueError:
        return None, ""
    unit = (match.group(2) or "").strip()
    return value, unit


def _fuzzy_token_spec_lookup(product: Any, req_key: str) -> str:
    """宽松 token 重叠匹配：只要 ≥1 个长度≥3 的 token 命中 spec key 就返回。"""
    if product is None:
        return ""
    specs = getattr(product, "specifications", None) or {}
    if not specs:
        return ""
    short_key = _as_text(req_key).split("：", 1)[0].strip()
    tokens = [t for t in re.split(r"[，,、；;：:（）()\[\]\s/]+", short_key) if len(t) >= 3]
    if not tokens:
        return ""
    for spec_key, spec_val in specs.items():
        k = _as_text(spec_key)
        if k and any(t in k for t in tokens):
            return _as_text(spec_val)
    return ""


def _try_numeric_threshold_match(req_val: str, product: Any) -> str:
    """如果招标值含比较符，在产品参数中找同单位且满足阈值的值。"""
    if product is None:
        return ""
    specs = getattr(product, "specifications", None) or {}
    if not specs:
        return ""
    for marker in _HARD_REQUIREMENT_MARKERS:
        if marker not in req_val:
            continue
        threshold, unit = _extract_numeric_with_unit(req_val)
        if threshold is None:
            continue
        for spec_key, spec_val in specs.items():
            sv = _as_text(spec_val)
            spec_num, spec_unit = _extract_numeric_with_unit(sv)
            if spec_num is None:
                continue
            if unit and spec_unit and unit != spec_unit:
                if not ({unit, spec_unit} <= {"个", "台", "套", "只", "支", "条", "根", "把"}):
                    continue
            if marker in ("≥", ">=", "不低于", "不少于", "至少"):
                if spec_num >= threshold:
                    return sv
            elif marker in ("≤", "<=", "不高于", "不大于"):
                if spec_num <= threshold:
                    return sv
        break
    return ""


def _build_response_value(req_val: str, *, req_key: str = "", product: Any = None) -> str:
    """Return product spec value if available, with multiple fallback strategies to avoid '待核实'.

    When _RICH_EXPANSION_MODE is enabled, exhausts all product context before falling back to
    pending placeholders.
    """
    if product is None:
        return _PENDING_BIDDER_RESPONSE

    # 策略1: 精确/模糊 spec 匹配
    if req_key:
        matched = _fuzzy_spec_lookup(product, req_key)
        if matched:
            return matched

    # 策略2: 宽松 token 匹配
    if req_key:
        token_matched = _fuzzy_token_spec_lookup(product, req_key)
        if token_matched:
            return token_matched

    # 策略3: 数值门槛匹配 — 如果招标要求含 ≥/≤ 等比较符
    if req_val and any(m in req_val for m in _HARD_REQUIREMENT_MARKERS):
        numeric_match = _try_numeric_threshold_match(req_val, product)
        if numeric_match:
            return numeric_match

    # 策略4: 布尔/能力类推断 — "具备"/"支持" 类条款
    combined = f"{req_key} {req_val}"
    if any(marker in combined for marker in _CAPABILITY_MARKERS):
        p_name = _as_text(getattr(product, "product_name", ""))
        p_mfr = _as_text(getattr(product, "manufacturer", ""))
        if p_name:
            return f"满足，投标产品（{p_mfr} {p_name}）具备该功能"

    # 策略5: 富展开模式 — 产品信息充分时给出描述而非空白占位符
    if _RICH_EXPANSION_MODE:
        specs = getattr(product, "specifications", None) or {}
        p_name = _as_text(getattr(product, "product_name", ""))
        p_mfr = _as_text(getattr(product, "manufacturer", ""))
        p_model = _as_text(getattr(product, "model", ""))

        # 策略5a: 找到任意相关 spec 值进行关联
        if req_key and specs:
            req_tokens = [t for t in re.split(r"[，,、；;：:（）()\[\]\s/]+", req_key) if len(t) >= 2]
            for spec_key, spec_val in specs.items():
                k = _as_text(spec_key)
                if k and req_tokens and any(t in k for t in req_tokens):
                    return _as_text(spec_val)

        # 策略5b: 产品信息充分时给出上下文描述
        if p_name and len(specs) >= 3:
            identity = f"{p_mfr} {p_model}" if p_model else p_mfr
            return f"响应，投标产品（{identity.strip()} {p_name}）满足该项要求，详见技术偏离表"

        # 策略5c: 即使信息不够充分，有产品名时也给出承诺式响应
        if p_name:
            return f"响应，投标产品（{p_name}）满足招标要求"

    # 策略6: 兜底（原始模式）
    specs = getattr(product, "specifications", None) or {}
    p_name = _as_text(getattr(product, "product_name", ""))
    p_mfr = _as_text(getattr(product, "manufacturer", ""))
    if p_name and len(specs) >= 3:
        return f"响应，详见投标产品（{p_mfr} {p_name}）技术偏离表"

    return _PENDING_BIDDER_RESPONSE


def _build_requirement_rows(pkg: ProcurementPackage, tender_raw: str, product: Any = None) -> tuple[list[dict[str, Any]], int]:
    requirements = _effective_requirements(pkg, tender_raw)
    package_scoped_raw = _extract_package_technical_scope_text(pkg, tender_raw)
    rows: list[dict[str, Any]] = []
    for req_key, req_val in requirements[:_MAX_TECH_ROWS_PER_PACKAGE]:
        source, quote, mapped = _extract_evidence_snippet(package_scoped_raw, req_key, req_val, tender_raw)
        response = _build_response_value(req_val, req_key=req_key, product=product)
        has_real_response = response != _PENDING_BIDDER_RESPONSE
        # Build bidder evidence from product if available
        bidder_evidence = ""
        if has_real_response and product is not None:
            bidder_evidence = f"产品参数库：{req_key}={response}"
        rows.append(
            {
                "key": req_key,
                "requirement": req_val,
                "response": response,
                "evidence_source": source,
                "evidence_quote": quote,
                "mapped": mapped,
                "has_real_response": has_real_response,
                "bidder_evidence": bidder_evidence,
            }
        )
    return rows, len(requirements)


def _build_deviation_table(
    tender: TenderDocument,
    pkg: ProcurementPackage,
    requirement_rows: list[dict[str, Any]],
    total_requirements: int,
    product: Any = None,
) -> str:
    """构建 8 列技术偏离表（升级版）。"""
    # 产品身份信息
    p_model = ""
    p_name = ""
    p_mfr = ""
    if product is not None:
        p_model = _as_text(getattr(product, "model", "")) or _as_text(getattr(product, "product_name", ""))
        p_name = _as_text(getattr(product, "product_name", ""))
        p_mfr = _as_text(getattr(product, "manufacturer", ""))

    lines = [
        f"### （一）技术偏离及详细配置明细表（第{pkg.package_id}包）",
        f"项目名称：{tender.project_name}",
        f"项目编号：{tender.project_number}",
        f"投标型号：{p_mfr} {p_model}" if p_model else "",
        "",
        "| 条款编号 | 招标要求 | 投标型号 | 实际响应值 | 偏离情况 | 证据材料 | 页码 | 说明/验收备注 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    lines = [line for line in lines if line or line == ""]

    if not requirement_rows:
        lines.append(
            f"| {pkg.package_id}.1 | 详见招标文件采购需求 | {p_model or '[待填写]'} | {_PENDING_BIDDER_RESPONSE} | 待核实 | 结构化解析结果 | — | 建议复核原文并补齐投标方证据 |"
        )
        return "\n".join(lines)

    for idx, row in enumerate(requirement_rows, start=1):
        clause_no = f"{pkg.package_id}.{idx}"
        req = f"{_markdown_cell(str(row['key']))}：{_markdown_cell(str(row['requirement']))}"
        has_real = row.get("has_real_response", False)
        bidder_ev = row.get("bidder_evidence", "")
        model_cell = _markdown_cell(p_model) if p_model else "[待填写]"
        response_cell = _markdown_cell(str(row['response']))

        if has_real and bidder_ev:
            evidence_text = (
                f"{_markdown_cell(str(row['evidence_source']))}；"
                f"{_markdown_cell(bidder_ev)}"
            )
            deviation = "无偏离"
            remark = "已匹配产品参数"
        else:
            evidence_text = (
                f"{_markdown_cell(str(row['evidence_source']))}；"
                "投标方证据待补充"
            )
            deviation = "待核实"
            remark = "需补充投标方证据"

        # 页码：使用证据引用的索引作为参考
        page_ref = f"第{idx}项" if has_real else "—"

        lines.append(
            f"| {clause_no} | {req} | {model_cell} | {response_cell} | {deviation} | {evidence_text} | {page_ref} | {remark} |"
        )

    if total_requirements > len(requirement_rows):
        lines.append(
            f"| — | 其余技术参数 | {p_model or '[待填写]'} | {_PENDING_BIDDER_RESPONSE} | 待核实 | 证据映射表继续列示 | — | 待补投标方证据 |"
        )

    return "\n".join(lines)


def _classify_config_item(name: str) -> str:
    """按关键词对配置项进行类别归类。"""
    n = name.strip()
    if any(k in n for k in ("主机", "整机", "仪器", "设备", "分析仪", "检测仪")):
        return "核心设备"
    if any(k in n for k in ("软件", "系统", "模块", "程序")):
        return "配套软件"
    if any(k in n for k in ("试剂", "耗材", "液", "管路", "滤芯", "滤器")):
        return "配套耗材"
    if any(k in n for k in ("说明书", "文件", "手册", "合格证", "报告", "彩页")):
        return "随机技术文件"
    if any(k in n for k in ("工具", "扳手", "螺丝", "钥匙")):
        return "随机工具"
    if any(k in n for k in ("附件", "配件", "接头", "适配", "支架", "台车", "推车")):
        return "标配附件"
    if any(k in n for k in ("电源线", "数据线", "连接线", "电缆", "网线")):
        return "连接线缆"
    if any(k in n for k in ("UPS", "稳压", "电源", "不间断")):
        return "电源保障"
    return "按招标文件配置要求"


def _config_dedup_tokens(name: str) -> set[str]:
    """提取配置项名称的 token 集合用于 Jaccard 去重。"""
    return {t for t in re.split(r"[，,、；;：:（）()\[\]\s/]+", name.strip()) if len(t) >= 2}


def _extract_configuration_items(pkg: ProcurementPackage, tender_raw: str) -> list[tuple[str, str, str, str]]:
    requirements = _effective_requirements(pkg, tender_raw)
    parsed_items: list[tuple[str, str, str, str]] = []

    for key, val in requirements:
        if not any(marker in key for marker in _CONFIG_REQUIREMENT_KEYS):
            continue
        fragments = [frag.strip() for frag in re.split(r"[，,；;、]", val) if frag.strip()]
        for fragment in fragments:
            normalized = _normalize_requirement_line(fragment)
            if not normalized:
                continue
            match = re.match(
                r"^(?P<name>.+?)(?P<qty>\d+(?:\.\d+)?)\s*(?P<unit>台|套|个|把|本|件|组|副|支|块|张|台套)?$",
                normalized,
            )
            if match:
                name = match.group("name").strip(" ：:")
                qty = match.group("qty")
                unit = match.group("unit") or "项"
            else:
                name = normalized.strip(" ：:")
                qty = "1"
                unit = "项"

            if not name:
                continue

            remark = _classify_config_item(name)
            parsed_items.append((name, unit, qty, remark))

    config_scope = _extract_package_configuration_scope_text(pkg, tender_raw)
    if config_scope:
        unit_pattern = "|".join(re.escape(unit) for unit in _CONFIG_ITEM_UNITS)
        for raw_line in config_scope.splitlines():
            normalized_line = _normalize_requirement_line(raw_line)
            normalized_line = re.sub(
                r"^(?:装箱配置单|装箱配置|配置清单|设备配置与配件|设备配置|配置与配件|主要配置|标准配置)[:：]?\s*",
                "",
                normalized_line,
            ).strip(" ：:;；,，。")
            if not normalized_line or _contains_any(normalized_line, _CONFIG_EXIT_HINTS):
                continue

            fragments = [frag.strip() for frag in re.split(r"[；;、,，]", normalized_line) if frag.strip()]
            for fragment in fragments:
                normalized = _normalize_requirement_line(fragment)
                if not normalized or _contains_any(normalized, _CONFIG_EXIT_HINTS):
                    continue
                if "|" in normalized:
                    cells = [cell.strip() for cell in normalized.split("|") if cell.strip()]
                    if not cells or any(cell in {"序号", "配置名称", "名称", "数量", "单位", "备注"} for cell in cells):
                        continue
                    if cells and re.fullmatch(r"\d+", cells[0]):
                        cells = cells[1:]
                    normalized = " ".join(cells[:4]).strip()
                if not normalized:
                    continue

                match = re.match(
                    rf"^(?P<name>.+?)(?P<qty>\d+(?:\.\d+)?)\s*(?P<unit>{unit_pattern})(?:\s|$)",
                    normalized,
                )
                if match:
                    name = match.group("name").strip(" ：:")
                    qty = match.group("qty")
                    unit = match.group("unit")
                else:
                    name = normalized.strip(" ：:")
                    qty = "1"
                    unit = "项"

                if not name or len(name) < 2 or _contains_any(name, _CONFIG_SECTION_HINTS):
                    continue

                remark = _classify_config_item(name)
                parsed_items.append((name, unit, qty, remark))

    # 智能去重：基于 token Jaccard 相似度而非简单子串匹配
    deduped: list[tuple[str, str, str, str]] = []
    deduped_token_sets: list[set[str]] = []
    for item in parsed_items:
        norm_name = re.sub(r"[\s，,。；;：:]+$", "", item[0]).strip()
        if not norm_name:
            continue
        item_tokens = _config_dedup_tokens(norm_name)
        is_dup = False
        for existing_item, existing_tokens in zip(deduped, deduped_token_sets):
            if not item_tokens or not existing_tokens:
                continue
            intersection = item_tokens & existing_tokens
            union = item_tokens | existing_tokens
            jaccard = len(intersection) / len(union) if union else 0
            if jaccard >= 0.8 and item[1] == existing_item[1]:
                is_dup = True
                break
        if is_dup:
            continue
        deduped.append(item)
        deduped_token_sets.append(item_tokens)

    # Config pollution cleaning — remove non-config items that leaked through boundary detection
    cleaned = _clean_config_items(deduped, pkg.package_id)
    return cleaned


def _clean_config_items(
    raw_config_items: list[tuple[str, str, str, str]],
    package_id: str = "",
) -> list[tuple[str, str, str, str]]:
    """Config Cleaner: 统一的配置项清理规则。

    清理规则:
    1. 过滤表头噪音 (序号、配置名称等)
    2. 过滤模板说明行 (如"按招标文件要求")
    3. 过滤重复项 (已在上游完成)
    4. 过滤跨包污染项 (包含其他包号)
    5. 过滤非配置内容 (评分标准、商务条款等)
    """
    _CONFIG_POLLUTION_TOKENS = (
        "评分标准", "评分办法", "商务条款", "合同条款", "投标人须知",
        "质保期", "售后服务", "付款方式", "验收标准", "违约责任",
        "评审因素", "评审办法", "评审标准", "投标有效期", "履约保证金",
        "包装要求", "运输要求", "保险要求", "技术参数", "技术要求",
        "采购需求", "性能要求", "参数要求",
    )

    _TABLE_HEADER_TOKENS = (
        "序号", "配置名称", "名称", "数量", "单位", "备注", "说明", "规格",
        "品牌", "型号", "产地", "价格", "小计", "合计",
    )

    _TEMPLATE_STATEMENT_PATTERNS = (
        "按招标文件", "按采购文件", "详见招标文件", "详见采购文件",
        "见附件", "见清单", "参见", "如下", "以下",
    )

    cleaned: list[tuple[str, str, str, str]] = []
    for item in raw_config_items:
        name, unit, qty, remark = item

        # 规则1: 过滤表头
        if name in _TABLE_HEADER_TOKENS:
            continue

        # 规则2: 过滤模板说明行
        if any(pattern in name for pattern in _TEMPLATE_STATEMENT_PATTERNS):
            continue

        # 规则3: 过滤污染内容
        if any(token in name for token in _CONFIG_POLLUTION_TOKENS):
            continue

        # 规则4: 过滤跨包污染 (包含"包X"但不是当前包)
        if package_id:
            other_package_pattern = r"包\s*(\d+)"
            matches = re.findall(other_package_pattern, name)
            if matches and all(match != package_id for match in matches):
                continue  # 包含其他包号,跨包污染

        # 规则5: 最小长度检查
        if len(name) < 2:
            continue

        # 规则6: 纯数字或纯符号
        if re.fullmatch(r"[\d\s\-_]+", name) or re.fullmatch(r"[^\u4e00-\u9fa5\w]+", name):
            continue

        cleaned.append(item)

    return cleaned


def _build_configuration_table(pkg: ProcurementPackage, tender_raw: str, product: Any = None) -> str:
    """构建双层配置表：第一层配置明细表 + 第二层配置功能描述表。"""
    # ── 第一层：详细配置明细表（含是否标配、用途说明）──
    lines = [
        f"### （二-A）详细配置明细表（第{pkg.package_id}包）",
        "| 序号 | 配置名称 | 单位 | 数量 | 是否标配 | 用途说明 | 备注 |",
        "|---:|---|---|---:|---|---|---|",
    ]

    # Build product identity header if product is available
    product_identity_lines: list[str] = []
    if product is not None:
        p_name = _as_text(getattr(product, "product_name", ""))
        p_model = _as_text(getattr(product, "model", ""))
        p_mfr = _as_text(getattr(product, "manufacturer", ""))
        p_origin = _as_text(getattr(product, "origin", ""))
        if p_name or p_model or p_mfr:
            identity_parts = []
            if p_mfr:
                identity_parts.append(f"品牌/厂家：{p_mfr}")
            if p_model:
                identity_parts.append(f"型号：{p_model}")
            if p_origin:
                identity_parts.append(f"产地：{p_origin}")
            product_identity_lines.append(
                f"| 1 | {p_name or pkg.item_name}主机 | 台 | {_infer_package_quantity(pkg, tender_raw)} | 是 | 核心设备主机 | {'；'.join(identity_parts) if identity_parts else '核心设备'} |"
            )

    config_items = _extract_configuration_items(pkg, tender_raw)
    if not config_items and not product_identity_lines:
        quantity = _infer_package_quantity(pkg, tender_raw)
        lines.extend(
            [
                f"| 1 | {pkg.item_name}主机 | 台 | {quantity} | 是 | 核心设备 | 核心设备 |",
                "| 2 | 随机附件及工具 | 套 | 1 | 是 | 设备运维保障 | 按招标文件配置要求 |",
                "| 3 | 技术文件（合格证/说明书等） | 套 | 1 | 是 | 操作指导与合规文件 | 交货时随货提供 |",
            ]
        )
        return "\n".join(lines)

    idx = 1
    if product_identity_lines:
        lines.extend(product_identity_lines)
        idx = 2

    # 收集配置项描述信息，用于第二层
    config_descriptions: list[tuple[str, str, str]] = []  # (name, usage, remark)

    for name, unit, qty, remark in config_items:
        # Enhance remark with product spec value if available
        matched_spec = _fuzzy_spec_lookup(product, name) if product else ""
        usage = _infer_config_usage(name)
        is_standard = "是" if _is_standard_config(name) else "选配"

        if matched_spec:
            remark_full = f"{remark}；投标产品：{matched_spec}"
        else:
            remark_full = remark

        lines.append(f"| {idx} | {_markdown_cell(name)} | {unit} | {qty} | {is_standard} | {usage} | {remark_full} |")
        config_descriptions.append((name, usage, remark_full))
        idx += 1

    # ── 第二层：配置功能描述章节 ──
    desc_lines = [
        "",
        f"### （二-B）配置功能描述（第{pkg.package_id}包）",
        "",
    ]
    p_name = _as_text(getattr(product, "product_name", "")) if product else pkg.item_name
    for name, usage, remark in config_descriptions:
        desc_lines.append(f"**{name}**")
        desc_lines.append(f"- 用途说明：{usage}")
        desc_lines.append(f"- 在{p_name}设备运行中的作用：{_infer_config_role(name, p_name)}")
        if any(kw in name for kw in ("软件", "系统", "模块", "程序")):
            desc_lines.append("- 涉及安装/培训：是，需安装调试后进行操作培训")
        elif any(kw in name for kw in ("试剂", "耗材")):
            desc_lines.append("- 涉及验收：是，需核对品名、规格、有效期")
        desc_lines.append("")

    lines.extend(desc_lines)
    return "\n".join(lines)


def _is_standard_config(name: str) -> bool:
    """判断配置项是否为标配。"""
    n = name.strip()
    if any(k in n for k in ("主机", "整机", "仪器", "设备", "分析仪", "检测仪", "说明书", "合格证", "电源线")):
        return True
    if any(k in n for k in ("选配", "可选", "升级", "扩展")):
        return False
    return True


def _infer_config_usage(name: str) -> str:
    """根据配置名称推断用途说明。"""
    n = name.strip()
    if any(k in n for k in ("主机", "整机", "仪器", "设备", "分析仪", "检测仪")):
        return "核心检测/分析设备"
    if any(k in n for k in ("软件", "系统", "模块", "程序")):
        return "数据处理/分析/管理功能"
    if any(k in n for k in ("试剂", "耗材", "液", "管路", "滤芯")):
        return "日常运行消耗品"
    if any(k in n for k in ("说明书", "文件", "手册", "合格证", "彩页")):
        return "操作指导与合规文件"
    if any(k in n for k in ("工具", "扳手", "螺丝", "钥匙")):
        return "设备维护保障工具"
    if any(k in n for k in ("附件", "配件", "接头", "适配", "支架", "台车")):
        return "设备功能扩展/辅助配件"
    if any(k in n for k in ("电源线", "数据线", "连接线", "电缆", "网线")):
        return "设备连接/供电保障"
    if any(k in n for k in ("UPS", "稳压", "电源", "不间断")):
        return "设备电源稳定保障"
    return "按招标文件配置要求提供"


def _infer_config_role(name: str, product_name: str) -> str:
    """推断配置项在设备中的功能角色。"""
    n = name.strip()
    if any(k in n for k in ("主机", "整机", "仪器", "设备")):
        return f"作为{product_name}的核心运行单元，承载主要检测/分析功能"
    if any(k in n for k in ("软件", "系统", "模块")):
        return f"为{product_name}提供数据采集、分析和管理支持，是设备智能化运行的关键组件"
    if any(k in n for k in ("试剂", "耗材")):
        return f"为{product_name}日常运行提供必需消耗品，直接影响检测结果准确性"
    if any(k in n for k in ("UPS", "稳压", "电源")):
        return f"为{product_name}提供稳定电源保障，防止意外断电导致数据丢失或设备损坏"
    if any(k in n for k in ("附件", "配件", "支架")):
        return f"辅助{product_name}完成特定功能或扩展应用场景"
    return f"配合{product_name}正常运行使用"


def _build_main_parameter_table(pkg: ProcurementPackage, tender_raw: str, product: Any = None) -> str:
    lines = [
        f"### 包{pkg.package_id}：{pkg.item_name}",
        "| 序号 | 技术参数项 | 招标要求 | 响应情况 | 备注 |",
        "|---:|---|---|---|---|",
    ]

    requirements = _effective_requirements(pkg, tender_raw)
    if not requirements:
        lines.append(f"| 1 | 核心技术参数 | 详见招标文件 | {_PENDING_BIDDER_RESPONSE} | 待核实 |")
        return "\n".join(lines)

    for idx, (key, val) in enumerate(requirements[:_MAX_TECH_ROWS_PER_PACKAGE], start=1):
        response = _build_response_value(val, req_key=key, product=product)
        note = "无偏离" if response != _PENDING_BIDDER_RESPONSE else "待核实"
        lines.append(
            f"| {idx} | {_markdown_cell(key)} | {_markdown_cell(val)} | {_markdown_cell(response)} | {note} |"
        )

    if len(requirements) > _MAX_TECH_ROWS_PER_PACKAGE:
        lines.append(f"|  | 其余参数 | 详见附录参数表 | {_PENDING_BIDDER_RESPONSE} | 待核实 |")

    return "\n".join(lines)


def _build_response_checklist_table(
    pkg: ProcurementPackage,
    mapped_count: int,
    total_requirements: int,
    requirement_rows: list[dict[str, Any]] | None = None,
) -> str:
    # Count how many rows have real product responses
    real_response_count = 0
    if requirement_rows:
        real_response_count = sum(1 for r in requirement_rows if r.get("has_real_response"))

    if total_requirements <= 0:
        evidence_result = "未提取到结构化参数，已保留待核实框架"
        evidence_status = "待补证"
        param_conclusion = "未提取到结构化参数，待人工补充"
        param_status = "待补实参"
    elif real_response_count == total_requirements:
        evidence_result = f"已完成 {mapped_count}/{total_requirements} 项招标原文映射，已绑定投标方证据"
        evidence_status = "已完成"
        param_conclusion = f"已证实 {real_response_count}/{total_requirements} 项，全部已填入投标产品实参"
        param_status = "已完成"
    elif real_response_count > 0:
        evidence_result = f"已完成 {mapped_count}/{total_requirements} 项招标原文映射，部分已绑定投标方证据"
        evidence_status = "部分完成"
        param_conclusion = f"已证实 {real_response_count}/{total_requirements} 项，其余 {total_requirements - real_response_count} 项待补实参"
        param_status = "部分完成"
    else:
        evidence_result = f"已完成 {mapped_count}/{total_requirements} 项招标原文映射，投标方证据待补"
        evidence_status = "待补证"
        param_conclusion = "已形成逐条响应框架，待填入投标产品实参"
        param_status = "待补实参"

    lines = [
        f"### （三）技术响应检查清单（第{pkg.package_id}包）",
        "| 序号 | 校验项 | 响应结论 | 证据载体 | 校验状态 |",
        "|---:|---|---|---|---|",
        f"| 1 | 关键技术参数逐条响应 | {param_conclusion} | 技术偏离表 | {param_status} |",
        "| 2 | 配置清单完整性 | 已按招标文件配置项展开列示，待匹配投标型号 | 配置明细表 | 待复核 |",
        "| 3 | 交付与培训要求 | 已保留响应框架，待结合投标方案复核 | 报价书与服务方案 | 待复核 |",
        "| 4 | 质保与售后要求 | 已保留服务承诺框架，待补投标方细节 | 售后服务方案 | 待复核 |",
        f"| 5 | 技术条款证据映射 | {evidence_result} | 技术条款证据映射表 | {evidence_status} |",
    ]
    return "\n".join(lines)


def _build_evidence_mapping_table(
    pkg: ProcurementPackage,
    requirement_rows: list[dict[str, Any]],
    total_requirements: int,
) -> str:
    lines = [
        f"### （四）技术条款证据映射表（第{pkg.package_id}包）",
        "| 序号 | 技术参数项 | 证据来源 | 原文片段 | 应用位置 |",
        "|---:|---|---|---|---|",
    ]

    if not requirement_rows:
        lines.append("| 1 | 核心技术参数 | 结构化解析结果 / 待补投标方证据 | 未提取到可映射原文片段，需人工复核原文并补齐投标方证据 | 技术偏离表第1行 |")
        return "\n".join(lines)

    for idx, row in enumerate(requirement_rows, start=1):
        has_real = row.get("has_real_response", False)
        bidder_ev = row.get("bidder_evidence", "")
        if has_real and bidder_ev:
            source_text = f"{_markdown_cell(str(row['evidence_source']))} / 产品参数库"
            quote_text = (
                f"{_markdown_cell(str(row['evidence_quote']))}；"
                f"{_markdown_cell(bidder_ev)}"
            )
        else:
            source_text = f"{_markdown_cell(str(row['evidence_source']))} / 待补投标方证据"
            quote_text = (
                f"{_markdown_cell(str(row['evidence_quote']))}；"
                "投标方证据待补充"
            )
        lines.append(
            f"| {idx} | {_markdown_cell(str(row['key']))} | {source_text} | "
            f"{quote_text} | 技术偏离表第{idx}行 |"
        )

    if total_requirements > len(requirement_rows):
        lines.append("|  | 其余参数项 | 招标原文 / 待补投标方证据 | 详见延伸条款，需人工补充映射与投标方证据 | 技术偏离表后续行 |")

    return "\n".join(lines)


def _sanitize_generated_content(section_title: str, content: str) -> tuple[str, list[str]]:
    normalized = content.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "").strip()
    removed_lines: list[str] = []
    kept: list[str] = []
    for line in normalized.split("\n"):
        stripped = line.strip()
        lower = stripped.lower()
        if stripped.startswith("#### "):
            line = "### " + stripped[5:]
            stripped = line.strip()
            lower = stripped.lower()
        if stripped.startswith(">"):
            line = re.sub(r"^>\s*", "", stripped)
            stripped = line.strip()
            lower = stripped.lower()
        if stripped in {section_title, f"# {section_title}", f"## {section_title}"}:
            removed_lines.append(stripped)
            continue
        if any(token in stripped for token in _TEMPLATE_POLLUTION_TOKENS):
            removed_lines.append(stripped)
            continue
        if any(lower.startswith(prefix.lower()) for prefix in _TEMPLATE_POLLUTION_PREFIXES):
            removed_lines.append(stripped)
            continue
        if any(keyword in lower for keyword in _TEMPLATE_POLLUTION_INFIX_KEYWORDS):
            removed_lines.append(stripped)
            continue
        if re.match(r"^(system|assistant|user)\s*[:：]", lower):
            removed_lines.append(stripped)
            continue
        if re.match(r"^(好的|当然|以下|下面|请注意|温馨提示)[，,:：]", stripped):
            removed_lines.append(stripped)
            continue
        if re.search(r"(根据你|根据您).{0,8}(提供|输入)", stripped):
            removed_lines.append(stripped)
            continue
        kept.append(line.rstrip())

    cleaned = "\n".join(kept).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned, removed_lines


def _detect_template_pollution(content: str) -> list[str]:
    findings: list[str] = []
    lowered = content.lower()
    if "todo" in lowered or "tbd" in lowered or "lorem ipsum" in lowered:
        findings.append("存在未清理的占位英文模板词")
    if "```" in content:
        findings.append("存在未清理的代码块围栏")
    if "{{" in content or "}}" in content:
        findings.append("存在未渲染模板变量")
    if "system:" in lowered or "assistant:" in lowered or "user:" in lowered:
        findings.append("存在角色提示词残留")
    if "判定结果：" in content or "原文长度" in content:
        findings.append("存在内部调试痕迹")
    return findings


def _apply_template_pollution_guard(sections: list[BidDocumentSection]) -> list[BidDocumentSection]:
    guarded: list[BidDocumentSection] = []
    for section in sections:
        cleaned, removed = _sanitize_generated_content(section.section_title, section.content)
        findings = _detect_template_pollution(cleaned)
        if removed:
            logger.debug("章节[%s] 模板污染清理：移除 %d 行提示性文本。", section.section_title, len(removed))
        if findings:
            logger.debug("章节[%s] 模板污染检查告警：%s", section.section_title, "；".join(findings))
        guarded.append(
            BidDocumentSection(
                section_title=section.section_title,
                content=cleaned,
                attachments=section.attachments,
            )
        )
    return guarded


def _build_qualification_license_block(tender: TenderDocument) -> str:
    lines = [
        "### （一）投标公司资质-营业执照",
        "（此处留空，待上传证件）",
    ]

    if _is_medical_project(tender):
        lines.extend(
            [
                "",
                "### （二）投标公司资质-医疗器械经营许可证/备案凭证（如适用）",
                "（此处留空，待上传证件）",
                "",
                "### （三）生产厂家资质-营业执照",
                "（此处留空，待上传证件）",
                "",
                "### （四）生产厂家资质-医疗器械生产/经营许可文件（如适用）",
                "（此处留空，待上传证件）",
                "",
                "### （五）投标产品注册证/备案证明（如适用）",
                "（此处留空，待上传证件）",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "### （二）与项目相关的行业资质证书（如适用）",
                "（此处留空，待上传证件）",
                "",
                "### （三）质量管理体系或服务能力证明（如适用）",
                "（此处留空，待上传证件）",
            ]
        )

    lines.extend(
        [
            "",
            "### （六）投标产品授权文件",
            "（此处留空，待上传证件）",
        ]
    )

    if _has_imported_clues(tender):
        lines.extend(
            [
                "",
                "### （七）进口产品合法来源与报关资料（如适用）",
                "（此处留空，待上传证明材料）",
            ]
        )

    return "\n".join(lines)


def _build_enterprise_declaration_block(tender: TenderDocument, today: str) -> str:
    _ = tender
    return f"""## 八、企业类型声明函（分支选择）
请按企业实际情况勾选并提交对应材料：

### 分支A：中小企业声明函（货物/服务）
□ 适用。  
本公司郑重声明：本次投标所提供货物/服务由符合《中小企业划型标准规定》的企业制造/承接。
（此处留空，待按采购文件附表填写企业名称、从业人数、营业收入、资产总额等信息）

### 分支B：监狱企业证明材料
□ 适用。  
如本单位属于监狱企业，提交由省级以上监狱管理局、戒毒管理局（含新疆生产建设兵团）出具的证明文件。

### 分支C：残疾人福利性单位声明函
□ 适用。  
如本单位属于残疾人福利性单位，提交残疾人福利性单位声明函及相关证明材料。

### 分支D：非中小企业声明
□ 适用。  
本公司郑重声明：本次投标所提供货物/服务不属于中小企业政策优惠适用范围，并对声明真实性负责。

企业名称（盖章）：{_COMPANY}  
法定代表人或授权代表：{_AUTHORIZED_REP}  
日期：{today}"""


def _build_consortium_declaration_block(tender: TenderDocument, today: str) -> str:
    allows = _allow_consortium(tender)
    branch_b_hint = "如选择分支B，须同步提交联合体协议书及职责分工。" if allows else "分支B本项目不适用。"
    return f"""## 四、联合体投标声明（分支选择）
请按投标组织形式勾选：
- □ 分支A：本次以独立投标方式参与，不组成联合体；
- □ 分支B：本次以联合体方式参与；{branch_b_hint}

投标人名称：{_COMPANY}  
日期：{today}"""


def _build_detail_quote_table(tender: TenderDocument, tender_raw: str) -> str:
    lines = [
        "| 序号 | 货物名称 | 规格型号 | 生产厂家 | 品牌 | 单价(元) | 数量 | 总价(元) |",
        "|---:|---|---|---|---|---:|---|---:|",
    ]

    if not tender.packages:
        lines.append("| 1 | [待填写] | [待填写] | [待填写] | [待填写] | [待填写] | [待填写] | [待填写] |")
        lines.append("|  | **合计报价** |  |  |  |  |  | **[待填写]** |")
        return "\n".join(lines)

    total_budget = 0.0
    for idx, pkg in enumerate(tender.packages, start=1):
        total_budget += pkg.budget
        quantity = _infer_package_quantity(pkg, tender_raw)
        lines.append(
            f"| {idx} | {pkg.item_name} | [品牌型号] | [生产厂家] | [品牌] | [待填写] | "
            f"{quantity} | [待填写] |"
        )

    lines.append(
        f"|  | **预算合计（参考）** |  |  |  |  |  | **{_fmt_money(total_budget)}** |"
    )
    lines.append("|  | **投标总报价** |  |  |  |  |  | **[待填写]** |")
    return "\n".join(lines)


def _gen_qualification(llm: ChatOpenAI, tender: TenderDocument) -> BidDocumentSection:
    """第一章：资格性证明文件"""
    _ = llm
    today = _today()
    purchaser = _safe_text(tender.purchaser, "[采购人名称]")
    license_block = _build_qualification_license_block(tender)
    supplier_commitment_title = _supplier_commitment_title(tender)
    content = f"""## 一、符合《中华人民共和国政府采购法》第二十二条规定声明
{purchaser}：

{_COMPANY}参与贵方组织的“{tender.project_name}”（项目编号：{tender.project_number}，投标范围：{_package_scope(tender)}）项目投标活动，现郑重声明如下：
1. 具备独立承担民事责任的能力；
2. 具有良好的商业信誉和健全的财务会计制度；
3. 具有履行合同所必需的设备和专业技术能力；
4. 具有依法缴纳税收和社会保障资金的良好记录；
5. 参加政府采购活动前三年内，在经营活动中没有重大违法记录；
6. 法律、行政法规规定的其他条件。

我方对上述声明内容的真实性负责，如有虚假，愿依法承担相应责任。

投标人名称：{_COMPANY}  
法定代表人或授权代表（签字）：{_AUTHORIZED_REP}  
日期：{today}  
（加盖公章）

## 二、{supplier_commitment_title}
我方作为政府采购供应商，现就供应商资格事项作出如下承诺：
1. 具有独立承担民事责任的能力，且经营状态合法有效；
2. 具有良好的商业信誉，未被列入失信被执行人名单；
3. 依法纳税、依法缴纳社会保障资金，相关记录可查询；
4. 具备履约所需的设备、人员与专业技术能力；
5. 参加本次政府采购活动前三年内无重大违法记录；
6. 不存在围标串标、弄虚作假等违法违规行为；
7. 承诺接受采购人及监管部门对承诺事项的核验；
8. 如承诺不实，愿承担相应法律责任及采购文件约定责任。

承诺人（供应商盖章）：{_COMPANY}  
日期：{today}

### （一）基本养老保险缴纳证明
（此处留空，待上传证明材料）

### （二）基本医疗保险及生育保险缴纳证明
（此处留空，待上传证明材料）

### （三）工伤保险缴纳证明
（此处留空，待上传证明材料）

### （四）失业保险缴纳证明
（此处留空，待上传证明材料）

## 三、承诺通过合法渠道可查证无行贿犯罪记录
{purchaser}：

我方承诺通过“中国执行信息公开网（http://zxgk.court.gov.cn）”等合法渠道，可查证法定代表人及单位负责人近三年内无行贿犯罪记录。
如有不实，我方愿承担由此产生的一切法律责任。

投标人名称：{_COMPANY}  
日期：{today}

### （一）全国企业信用信息公示系统截图
（此处留空，待上传截图）

### （二）中国执行信息公开网截图
（此处留空，待上传截图）

### （三）中国裁判文书网截图
（此处留空，待上传截图）

### （四）信用中国截图
（此处留空，待上传截图）

### （五）中国政府采购网截图
（此处留空，待上传截图）

## 四、其他承诺
{purchaser}：

我方承诺在本项目投标及合同履行过程中，严格遵循公平竞争、诚实信用、合法合规原则，不实施商业贿赂等违法违规行为；如有违反，愿承担全部法律后果。

投标人名称：{_COMPANY}  
日期：{today}

## 五、法定代表人授权书
{purchaser}：

兹授权{_AUTHORIZED_REP}为我单位本项目授权代表，参加“{tender.project_name}”（项目编号：{tender.project_number}）投标活动，并有权签署与本项目有关的各类文件。

法定代表人：{_LEGAL_REP}  
授权代表：{_AUTHORIZED_REP}  
联系电话：{_PHONE}  
联系地址：{_ADDRESS}  
投标人名称（盖章）：{_COMPANY}  
日期：{today}

## 六、法定代表人及授权代表身份证明
### （一）法定代表人身份证明
（此处留空，待上传法定代表人身份证正反面复印件）

### （二）授权代表身份证明
（此处留空，待上传授权代表身份证正反面复印件）

## 七、相关证件
{license_block}

## 八、围标串标承诺函
{purchaser}：

我方郑重承诺，参与本项目投标过程中不存在围标、串标、弄虚作假等行为；若有违反，愿接受采购人及监管部门依法依规处理。

投标人名称：{_COMPANY}  
法定代表人或授权代表（签字）：{_AUTHORIZED_REP}  
日期：{today}
"""
    return BidDocumentSection(section_title="第一章 资格性证明文件", content=content.strip())


def _gen_compliance(llm: ChatOpenAI, tender: TenderDocument) -> BidDocumentSection:
    """第二章：符合性承诺"""
    _ = llm
    today = _today()
    purchaser = _safe_text(tender.purchaser, "[采购人名称]")
    payment = _normalize_commitment_term(tender.commercial_terms.payment_method)
    validity = _safe_text(tender.commercial_terms.validity_period, "90日历天")
    warranty = _normalize_commitment_term(tender.commercial_terms.warranty_period)
    bond = _normalize_commitment_term(tender.commercial_terms.performance_bond, "按招标文件约定执行")
    consortium_block = _build_consortium_declaration_block(tender, today)
    enterprise_declaration_block = _build_enterprise_declaration_block(tender, today)
    medical_extra_block = ""
    if _is_medical_project(tender):
        medical_extra_block = f"""

## 九、医疗器械合规声明函（适用医疗项目）
我方声明：本次投标涉及的医疗器械产品在供货时将确保注册证/备案凭证、说明书、标签、合格证及追溯信息完整有效，且与投标型号一致。

投标人名称：{_COMPANY}  
日期：{today}"""

    content = f"""## 一、投标报价承诺
{purchaser}：

我方承诺本项目报价真实、完整、唯一且具有竞争性，不存在低于成本恶意报价、围标串标、虚假报价等行为。投标报价已充分考虑运输、安装、调试、培训、税费及售后服务等全部费用。

投标人名称：{_COMPANY}  
日期：{today}

## 二、投标文件规范性、符合性承诺
我方承诺：投标文件的签署、盖章、装订、密封、递交、响应格式及内容均符合采购文件要求，对采购文件提出的实质性条款已逐项响应，不存在重大偏离。

投标人名称：{_COMPANY}  
日期：{today}

## 三、满足主要商务条款的承诺书
我方承诺对以下商务条款作出实质性响应并严格履行：
1. 付款方式：{payment}
2. 投标有效期：{validity}
3. 质保期：{warranty}
4. 履约保证金：{bond}
5. 交货期限与地点：按招标文件及合同约定执行
6. 其他商务要求：如验收、违约责任、售后条款等均按招标文件及合同条款执行

投标人名称：{_COMPANY}  
授权代表：{_AUTHORIZED_REP}  
日期：{today}

{consortium_block}

## 五、技术部分实质性内容承诺
我方承诺：所投产品或服务对招标文件技术条款逐条响应，满足（或优于）采购文件要求；如出现偏离，将在“技术偏离表”中如实披露并说明原因。

投标人名称：{_COMPANY}  
日期：{today}

## 六、其他要求承诺
我方承诺遵守招标文件关于诚信投标、廉洁投标、知识产权、信息安全和保密义务等全部要求，不实施影响采购公平性的行为。

投标人名称：{_COMPANY}  
日期：{today}

## 七、投标人关联单位说明
我方承诺如实披露与本单位存在下列关系的单位：
1. 与投标人单位负责人为同一人的其他单位：[待填写]
2. 与投标人存在直接控股、管理关系的其他单位：[待填写]

投标人名称：{_COMPANY}  
日期：{today}

{enterprise_declaration_block}
{medical_extra_block}
"""
    return BidDocumentSection(section_title="第二章 符合性承诺", content=content.strip())


def _build_post_table_narratives(
    pkg: ProcurementPackage,
    tender: TenderDocument,
    tender_raw: str,
    product: Any = None,
    requirement_rows: list[dict[str, Any]] | None = None,
) -> str:
    """生成表格后的详细技术响应说明章节，大幅增加正文页数。

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
    specs = (getattr(product, "specifications", None) or {}) if product else {}
    warranty = _normalize_commitment_term(tender.commercial_terms.warranty_period)
    delivery_time = ""
    delivery_place = ""
    if pkg.delivery_time:
        delivery_time = _safe_text(pkg.delivery_time, "按招标文件约定")
    if pkg.delivery_place:
        delivery_place = _safe_text(pkg.delivery_place, "采购人指定地点")

    sections: list[str] = []

    # ── 1. 关键性能说明 ──
    perf_lines = [
        f"### （五）关键性能说明（第{pkg.package_id}包）",
        "",
        f"本包投标产品为{p_mfr} {p_name}（型号：{p_model or '详见技术偏离表'}），"
        f"针对采购文件技术要求逐项响应如下：",
        "",
    ]
    # 列出有实参的关键性能
    real_rows = [r for r in requirement_rows if r.get("has_real_response")]
    for idx, row in enumerate(real_rows[:10], start=1):
        key = _as_text(row.get("key", ""))
        response = _as_text(row.get("response", ""))
        perf_lines.append(f"{idx}. **{key}**：{response}。该参数满足招标文件要求，确保设备在实际应用中达到预期性能。")
    if not real_rows:
        for idx, (k, v) in enumerate(list(specs.items())[:8], start=1):
            perf_lines.append(f"{idx}. **{k}**：{_as_text(v)}。")
    if not real_rows and not specs:
        perf_lines.append("（本包关键性能参数待补充产品实参后展开说明。）")
    perf_lines.append("")
    perf_lines.append(
        f"综上，{p_name}的核心性能指标均满足或优于采购文件技术要求，"
        "能够有效支撑采购人的日常业务需求。"
    )
    sections.append("\n".join(perf_lines))

    # ── 2. 配置说明 ──
    config_lines = [
        f"### （六）配置说明（第{pkg.package_id}包）",
        "",
        f"本包投标设备{p_name}的配置方案严格按照采购文件要求编制，"
        "主要包括以下几个方面：",
        "",
        f"1. **核心设备**：{p_name}主机{_infer_package_quantity(pkg, tender_raw)}台（套），"
        f"品牌{p_mfr or '[待填写]'}，型号{p_model or '[待填写]'}。",
        "2. **配套软件**：随机提供设备运行所需的全套软件系统，包括数据采集、"
        "分析处理和报告管理模块。",
        "3. **标准附件与耗材**：按采购文件配置清单提供全部标准附件、"
        "随机工具和初始耗材。",
        "4. **技术文件**：提供设备使用说明书、合格证、装箱单及相关技术文件。",
        "",
        "上述配置方案确保设备到场即可开展安装调试工作，"
        "各配置项的详细清单见配置明细表。",
    ]
    sections.append("\n".join(config_lines))

    # ── 3. 交付说明 ──
    delivery_lines = [
        f"### （七）交付说明（第{pkg.package_id}包）",
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
        f"### （八）验收说明（第{pkg.package_id}包）",
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
        f"### （九）使用与培训说明（第{pkg.package_id}包）",
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


def _generate_rich_draft_sections(
    tender: TenderDocument,
    products: dict,
) -> list[BidDocumentSection]:
    """Rich Draft Mode: 生成详细说明章节。

    包含:
    - 关键性能说明
    - 配置说明
    - 交付说明
    - 验收说明
    - 使用与培训说明
    """
    sections = []

    for pkg in tender.packages:
        product = products.get(pkg.package_id)
        if not product:
            continue

        # 1. 关键性能说明
        performance_content = f"### 包{pkg.package_id} 关键性能说明\n\n"
        if product.specifications:
            for key, value in list(product.specifications.items())[:10]:
                performance_content += f"**{key}**：{value}\n\n"
                performance_content += f"- 该参数满足招标文件要求，具体响应见技术偏离表。\n"
                performance_content += f"- 产品在该指标上具备良好的性能表现，能够满足实际使用需求。\n\n"
        else:
            performance_content += f"{product.product_name}具备完整的技术功能，各项关键性能指标均满足招标要求。\n\n"

        # 2. 配置说明
        config_content = f"### 包{pkg.package_id} 配置说明\n\n"
        if product.config_items:
            for idx, item in enumerate(product.config_items[:15], 1):
                if isinstance(item, dict):
                    config_name = item.get("配置项", f"配置{idx}")
                    config_desc = item.get("说明", "标配")
                    config_content += f"{idx}. **{config_name}**：{config_desc}\n"
                    config_content += f"   - 用途：用于设备正常运行和日常维护\n\n"
        else:
            config_content += "配置清单包含主机及全套标准附件，详见配置表。\n\n"

        # 3. 交付说明
        delivery_content = f"### 包{pkg.package_id} 交付说明\n\n"
        delivery_content += f"- 交货期：{pkg.delivery_time or '按招标文件约定'}\n"
        delivery_content += f"- 交货地点：{pkg.delivery_place or '采购人指定地点'}\n"
        delivery_content += "- 交货方式：由我公司负责运输至指定地点，包装符合国家标准\n"
        delivery_content += "- 交货内容：全套设备、标准配件、技术资料、培训服务\n\n"

        # 4. 验收说明
        acceptance_content = f"### 包{pkg.package_id} 验收说明\n\n"
        acceptance_content += "- 验收标准：按照国家相关标准及招标文件要求\n"
        acceptance_content += "- 验收方式：开箱验收、外观检查、功能测试、性能验证\n"
        acceptance_content += "- 验收文件：提供产品合格证、检测报告、使用说明书等\n"
        acceptance_content += "- 验收配合：我公司派专业技术人员现场指导验收\n\n"

        # 5. 使用与培训说明
        training_content = f"### 包{pkg.package_id} 使用与培训说明\n\n"
        training_content += "**培训计划**：\n"
        training_content += "- 培训对象：设备操作人员、维护人员\n"
        training_content += "- 培训内容：设备原理、操作规程、日常维护、故障排除\n"
        training_content += "- 培训方式：现场培训+远程技术支持\n"
        training_content += "- 培训时长：不少于3天，确保人员熟练掌握\n\n"
        training_content += "**技术支持**：\n"
        training_content += "- 提供7×24小时技术热线\n"
        training_content += "- 定期巡检和技术咨询\n"
        training_content += "- 提供详细的中文操作手册和维护手册\n\n"

        combined_content = performance_content + config_content + delivery_content + acceptance_content + training_content

        sections.append(BidDocumentSection(
            section_title=f"第三章附：包{pkg.package_id}详细说明",
            content=combined_content
        ))

    return sections


def _gen_technical(llm: ChatOpenAI, tender: TenderDocument, tender_raw: str, products: dict | None = None, mode: str = "internal") -> BidDocumentSection:
    """第三章：商务及技术部分 - 支持双模式"""
    _ = llm
    products = products or {}
    today = _today()
    purchaser = _safe_text(tender.purchaser, "[采购人名称]")
    package_details = _package_detail_lines(tender, tender_raw)
    quote_table = _quote_overview_table(tender, tender_raw)

    technical_sections: list[str] = []
    if tender.packages:
        for pkg in tender.packages:
            product = products.get(pkg.package_id)
            requirement_rows, total_requirements = _build_requirement_rows(pkg, tender_raw, product=product)
            mapped_count = sum(1 for row in requirement_rows if bool(row.get("mapped")))

            technical_sections.append(
                _build_deviation_table(
                    tender=tender,
                    pkg=pkg,
                    requirement_rows=requirement_rows,
                    total_requirements=total_requirements,
                    product=product,
                )
            )
            technical_sections.append(_build_configuration_table(pkg, tender_raw, product=product))
            technical_sections.append(
                _build_response_checklist_table(
                    pkg=pkg,
                    mapped_count=mapped_count,
                    total_requirements=total_requirements,
                    requirement_rows=requirement_rows,
                )
            )
            technical_sections.append(
                _build_evidence_mapping_table(
                    pkg=pkg,
                    requirement_rows=requirement_rows,
                    total_requirements=total_requirements,
                )
            )
            # ── 详细技术响应说明章节（post-table narratives）──
            technical_sections.append(
                _build_post_table_narratives(
                    pkg=pkg,
                    tender=tender,
                    tender_raw=tender_raw,
                    product=product,
                    requirement_rows=requirement_rows,
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
                    "### （三）技术响应检查清单",
                    "| 序号 | 校验项 | 响应结论 | 证据载体 | 校验状态 |",
                    "|---:|---|---|---|---|",
                    "| 1 | 技术参数逐条响应 | 已覆盖 | 技术偏离表 | 已完成 |",
                    "| 2 | 技术条款证据映射 | 未提取参数，需人工补充映射 | 技术条款证据映射表 | 待复核 |",
                    "",
                    "### （四）技术条款证据映射表",
                    "| 序号 | 技术参数项 | 证据来源 | 原文片段 | 应用位置 |",
                    "|---:|---|---|---|---|",
                    "| 1 | 核心技术参数 | 结构化解析结果 | 未提取到可映射原文片段，需人工复核原文 | 技术偏离表第1行 |",
                ]
            )
        )

    content = f"""## 一、报价书
{purchaser}：

我方{_COMPANY}已详细研究“{tender.project_name}”（项目编号：{tender.project_number}）采购文件，愿按采购文件及合同条款要求提供合格货物及服务，并承担相应责任义务。现提交报价文件如下：
1. 投标范围：{_package_scope(tender)}
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

说明：本章已按“逐包、逐参数、逐校验项”强制结构化编制。若采购文件另有固定格式，以采购文件格式为准。
说明：本章已提供技术条款证据映射框架，正式递交前需补齐投标方实参与证明材料。
"""
    return BidDocumentSection(section_title="第三章 商务及技术部分", content=content.strip())


def _gen_appendix(llm: ChatOpenAI, tender: TenderDocument, tender_raw: str, products: dict | None = None) -> BidDocumentSection:
    """第四章：报价书附件（技术参数明细 + 售后服务方案）"""
    _ = llm
    products = products or {}
    today = _today()
    warranty = _normalize_commitment_term(tender.commercial_terms.warranty_period)
    payment = _normalize_commitment_term(tender.commercial_terms.payment_method)

    parameter_tables: list[str] = []
    if tender.packages:
        for pkg in tender.packages:
            parameter_tables.append(_build_main_parameter_table(pkg, tender_raw, product=products.get(pkg.package_id)))
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

{_build_detail_quote_table(tender, tender_raw)}

## 二、技术服务和售后服务的内容及措施
### （一）技术服务
1. 安装调试服务：设备到货后安排专业工程师现场安装、调试并协助完成验收；
2. 培训服务：提供操作培训、日常维护培训和故障初判培训，确保使用科室独立开展工作；
3. 技术咨询服务：提供7×24小时电话/线上技术支持，必要时提供现场技术支持；
4. 质量保障服务：供货产品均为全新合格产品，随机文件齐全，来源可追溯；
5. 交付配合服务：根据采购人计划安排发运、卸货、安装和交接，保障项目按期落地。

### （二）售后服务
1. 质保期承诺：{warranty}；
2. 响应时限：接到通知后4小时内响应，24小时内提供现场处置或明确解决方案；
3. 维护保养：每年至少2次预防性巡检维护，形成维护记录；
4. 配件保障：提供常用备件保障及更换服务，确保设备持续稳定运行；
5. 质保期外服务：继续提供长期技术支持，收费标准公开透明；
{_format_payment_execution_line(payment)}

投标人名称：{_COMPANY}  
授权代表：{_AUTHORIZED_REP}  
日期：{today}

## 三、产品彩页
（此处留空，待上传产品彩页）

## 四、节能/环保/能效认证证书（如适用）
（此处留空，待上传节能/环保/能效认证证书）

## 五、检测/质评数据节选
（此处留空，待上传检测报告或室间质评结果）
"""
    return BidDocumentSection(section_title="第四章 报价书附件", content=content.strip())


def generate_bid_sections(
    tender: TenderDocument,
    tender_raw: str,
    llm: ChatOpenAI,
    products: dict | None = None,
    mode: str = "rich_draft",  # 新增参数: "internal" | "rich_draft"
) -> list[BidDocumentSection]:
    """
    根据招标文件生成全部投标文件章节 - 支持双模式。

    Args:
        tender: 结构化招标文件数据
        tender_raw: 招标文件原始文本（供技术章节追溯）
        llm: 语言模型实例（为兼容接口保留）
        products: 包号→产品规格映射（可选，用于填入产品实参）
        mode: 生成模式
            - "internal": 内部模式,允许待核实、待补证、待补实参
            - "rich_draft": 富展开模式,必须输出完整详细说明

    Returns:
        各章节列表

    模式说明:
    - Internal mode: 用于内部审阅,可包含 [待核实]、[待补证] 等标记
    - Rich draft mode: 用于外发准备,必须包含:
        * 技术偏离表
        * 关键性能说明 (每条技术要求至少2-3句详细说明)
        * 配置说明 (逐项说明用途)
        * 交付说明
        * 验收说明
        * 使用与培训说明
    """
    logger.info("开始一键生成投标文件章节 - 模式: %s", mode)
    logger.debug("招标原文长度：%d 字符", len(tender_raw))

    sections = [
        _gen_qualification(llm, tender),
        _gen_compliance(llm, tender),
        _gen_technical(llm, tender, tender_raw, products=products, mode=mode),
        _gen_appendix(llm, tender, tender_raw, products=products),
    ]

    # Rich draft mode 需要额外的详细说明章节
    if mode == "rich_draft" and products:
        rich_sections = _generate_rich_draft_sections(tender, products)
        sections.extend(rich_sections)

    sections = _apply_template_pollution_guard(sections)

    logger.info("一键投标文件章节生成完成，共 %d 章", len(sections))
    return sections
