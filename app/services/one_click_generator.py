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
_PACKAGE_SCOPE_AFTER_LINES = 120

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
_CONFIG_REQUIREMENT_KEYS = ("设备配置", "配置与配件", "设备配置与配件", "配置清单", "主要配置", "标准配置")
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
    "正本与副本", "装订成册",
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


def _expand_requirement_entry(key: str, value: str) -> list[tuple[str, str]]:
    if not any(marker in key for marker in _GENERIC_TECH_KEYS):
        return [(key, value)]
    if "；" not in value and ";" not in value:
        return [(key, value)]

    expanded: list[tuple[str, str]] = []
    for fragment in re.split(r"[；;]", value):
        pair = _extract_requirement_pair(fragment)
        if pair:
            expanded.append(pair)

    if len(expanded) < 2:
        return [(key, value)]
    return _dedupe_requirement_pairs(expanded)


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
        return requirements

    package_scoped_raw = _extract_package_scope_text(pkg, tender_raw)
    extra_pairs = _extract_requirements_from_raw(pkg, package_scoped_raw)
    if not extra_pairs:
        return requirements

    existing_keys = {key for key, _ in requirements}
    merged = list(requirements)
    for key, val in extra_pairs:
        if key in existing_keys:
            continue
        merged.append((key, val))
        existing_keys.add(key)

    return merged


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


def _extract_package_scope_text(pkg: ProcurementPackage, tender_raw: str) -> str:
    text = tender_raw or ""
    if not text.strip():
        return text

    lines = text.splitlines()
    if not lines:
        return text

    item_tokens = [pkg.item_name, *_extract_match_tokens(pkg.item_name)]
    package_markers = {
        f"包{pkg.package_id}",
        f"第{pkg.package_id}包",
        f"{pkg.package_id}包",
    }
    candidate_indexes: list[tuple[int, int]] = []

    for idx, raw_line in enumerate(lines):
        normalized = _normalize_requirement_line(raw_line)
        if not normalized:
            continue

        score = 0
        if any(token and token in normalized for token in item_tokens):
            score += 3
        if any(marker in normalized for marker in package_markers):
            score += 2
        lookahead = " ".join(lines[idx:min(len(lines), idx + 12)])
        if score and _contains_any(lookahead, _TECH_SECTION_HINTS):
            score += 2

        if score:
            candidate_indexes.append((score, idx))

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
            end += 1

        scope = "\n".join(lines[start:end]).strip()
        if not scope or scope in seen_scopes:
            continue
        seen_scopes.add(scope)
        scopes.append(scope)

    return "\n".join(scopes) if scopes else text


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

    candidates = [
        f"{req_key}：{req_val}",
        req_key,
        req_val,
        *_extract_match_tokens(req_key, req_val)[:8],
    ]

    search_texts: list[str] = []
    if package_raw.strip():
        search_texts.append(package_raw)
    if fallback_raw.strip() and fallback_raw != package_raw:
        search_texts.append(fallback_raw)

    idx = -1
    matched = ""
    text = ""
    for candidate_text in search_texts:
        idx, matched = _find_evidence_position(candidate_text, candidates)
        if idx >= 0:
            text = candidate_text
            break

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


def _build_response_value(req_val: str) -> str:
    return _markdown_cell(req_val)


def _build_requirement_rows(pkg: ProcurementPackage, tender_raw: str) -> tuple[list[dict[str, Any]], int]:
    requirements = _effective_requirements(pkg, tender_raw)
    package_scoped_raw = _extract_package_scope_text(pkg, tender_raw)
    rows: list[dict[str, Any]] = []
    for req_key, req_val in requirements[:_MAX_TECH_ROWS_PER_PACKAGE]:
        source, quote, mapped = _extract_evidence_snippet(package_scoped_raw, req_key, req_val, tender_raw)
        rows.append(
            {
                "key": req_key,
                "requirement": req_val,
                "response": _build_response_value(req_val),
                "evidence_source": source,
                "evidence_quote": quote,
                "mapped": mapped,
            }
        )
    return rows, len(requirements)


def _build_deviation_table(
    tender: TenderDocument,
    pkg: ProcurementPackage,
    requirement_rows: list[dict[str, Any]],
    total_requirements: int,
) -> str:
    lines = [
        f"### （一）技术偏离及详细配置明细表（第{pkg.package_id}包）",
        f"项目名称：{tender.project_name}",
        f"项目编号：{tender.project_number}",
        "",
        "| 序号 | 招标技术参数要求 | 投标产品响应参数 | 偏离情况 | 响应依据/证据映射 |",
        "|---:|---|---|---|---|",
    ]

    if not requirement_rows:
        lines.append(
            "| 1 | 详见招标文件采购需求 | 详见拟投产品参数资料 | 无偏离 | 结构化解析结果（建议复核原文） |"
        )
        return "\n".join(lines)

    for idx, row in enumerate(requirement_rows, start=1):
        req = f"{_markdown_cell(str(row['key']))}：{_markdown_cell(str(row['requirement']))}"
        evidence_text = f"{_markdown_cell(str(row['evidence_source']))}；{_markdown_cell(str(row['evidence_quote']))}"
        lines.append(
            f"| {idx} | {req} | {_markdown_cell(str(row['response']))} | 无偏离 | {evidence_text} |"
        )

    if total_requirements > len(requirement_rows):
        lines.append(
            "|  | 其余技术参数 | 详见后附完整技术响应表 | 无偏离 | 证据映射表继续列示 |"
        )

    return "\n".join(lines)


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

            remark = "按招标文件配置要求"
            if "主机" in name:
                remark = "核心设备"
            elif "软件" in name:
                remark = "配套软件"
            elif "文件" in name or "说明书" in name:
                remark = "随机技术文件"
            parsed_items.append((name, unit, qty, remark))

    seen: set[str] = set()
    deduped: list[tuple[str, str, str, str]] = []
    for item in parsed_items:
        dedup_key = "::".join(item[:3])
        if dedup_key in seen:
            continue
        seen.add(dedup_key)
        deduped.append(item)
    return deduped


def _build_configuration_table(pkg: ProcurementPackage, tender_raw: str) -> str:
    lines = [
        f"### （二）详细配置明细表（第{pkg.package_id}包）",
        "| 序号 | 配置名称 | 单位 | 数量 | 备注 |",
        "|---:|---|---|---:|---|",
    ]

    config_items = _extract_configuration_items(pkg, tender_raw)
    if not config_items:
        quantity = _infer_package_quantity(pkg, tender_raw)
        lines.extend(
            [
                f"| 1 | {pkg.item_name}主机 | 台 | {quantity} | 核心设备 |",
                "| 2 | 随机附件及工具 | 套 | 1 | 按招标文件配置要求 |",
                "| 3 | 技术文件（合格证/说明书等） | 套 | 1 | 交货时随货提供 |",
            ]
        )
        return "\n".join(lines)

    for idx, (name, unit, qty, remark) in enumerate(config_items, start=1):
        lines.append(f"| {idx} | {_markdown_cell(name)} | {unit} | {qty} | {remark} |")
    return "\n".join(lines)


def _build_main_parameter_table(pkg: ProcurementPackage, tender_raw: str) -> str:
    lines = [
        f"### 包{pkg.package_id}：{pkg.item_name}",
        "| 序号 | 技术参数项 | 招标要求 | 响应情况 | 备注 |",
        "|---:|---|---|---|---|",
    ]

    requirements = _effective_requirements(pkg, tender_raw)
    if not requirements:
        lines.append("| 1 | 核心技术参数 | 详见招标文件 | 详见拟投产品参数资料 | 无偏离 |")
        return "\n".join(lines)

    for idx, (key, val) in enumerate(requirements[:_MAX_TECH_ROWS_PER_PACKAGE], start=1):
        lines.append(
            f"| {idx} | {_markdown_cell(key)} | {_markdown_cell(val)} | {_build_response_value(val)} | 无偏离 |"
        )

    if len(requirements) > _MAX_TECH_ROWS_PER_PACKAGE:
        lines.append("|  | 其余参数 | 详见附录参数表 | 全部响应 | 无偏离 |")

    return "\n".join(lines)


def _build_response_checklist_table(
    pkg: ProcurementPackage,
    mapped_count: int,
    total_requirements: int,
) -> str:
    if total_requirements <= 0:
        evidence_result = "未提取到结构化参数，已保留人工复核项"
        evidence_status = "待复核"
    else:
        evidence_result = f"已完成 {mapped_count}/{total_requirements} 项条款证据映射"
        evidence_status = "已完成" if mapped_count > 0 else "待复核"

    lines = [
        f"### （三）技术响应检查清单（第{pkg.package_id}包）",
        "| 序号 | 校验项 | 响应结论 | 证据载体 | 校验状态 |",
        "|---:|---|---|---|---|",
        "| 1 | 关键技术参数逐条响应 | 已形成偏离表逐项响应 | 技术偏离表 | 已完成 |",
        "| 2 | 配置清单完整性 | 已按招标文件配置项展开列示 | 配置明细表 | 已完成 |",
        "| 3 | 交付与培训要求 | 已承诺按招标文件执行 | 报价书与服务方案 | 已完成 |",
        "| 4 | 质保与售后要求 | 已在售后章节明确响应时限与保障 | 售后服务方案 | 已完成 |",
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
        lines.append("| 1 | 核心技术参数 | 结构化解析结果 | 未提取到可映射原文片段，需人工复核原文 | 技术偏离表第1行 |")
        return "\n".join(lines)

    for idx, row in enumerate(requirement_rows, start=1):
        lines.append(
            f"| {idx} | {_markdown_cell(str(row['key']))} | {_markdown_cell(str(row['evidence_source']))} | "
            f"{_markdown_cell(str(row['evidence_quote']))} | 技术偏离表第{idx}行 |"
        )

    if total_requirements > len(requirement_rows):
        lines.append("|  | 其余参数项 | 招标原文 | 详见延伸条款，需人工补充映射 | 技术偏离表后续行 |")

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


def _gen_technical(llm: ChatOpenAI, tender: TenderDocument, tender_raw: str) -> BidDocumentSection:
    """第三章：商务及技术部分"""
    _ = llm
    today = _today()
    purchaser = _safe_text(tender.purchaser, "[采购人名称]")
    package_details = _package_detail_lines(tender, tender_raw)
    quote_table = _quote_overview_table(tender, tender_raw)

    technical_sections: list[str] = []
    if tender.packages:
        for pkg in tender.packages:
            requirement_rows, total_requirements = _build_requirement_rows(pkg, tender_raw)
            mapped_count = sum(1 for row in requirement_rows if bool(row.get("mapped")))

            technical_sections.append(
                _build_deviation_table(
                    tender=tender,
                    pkg=pkg,
                    requirement_rows=requirement_rows,
                    total_requirements=total_requirements,
                )
            )
            technical_sections.append(_build_configuration_table(pkg, tender_raw))
            technical_sections.append(
                _build_response_checklist_table(
                    pkg=pkg,
                    mapped_count=mapped_count,
                    total_requirements=total_requirements,
                )
            )
            technical_sections.append(
                _build_evidence_mapping_table(
                    pkg=pkg,
                    requirement_rows=requirement_rows,
                    total_requirements=total_requirements,
                )
            )
    else:
        technical_sections.append(
            "\n".join(
                [
                    "### （一）技术偏离及详细配置明细表",
                    "| 序号 | 招标技术参数要求 | 投标产品响应参数 | 偏离情况 | 响应依据/证据映射 |",
                    "|---:|---|---|---|---|",
                    "| 1 | 详见招标文件 | 详见拟投产品参数资料 | 无偏离 | 结构化解析结果（建议复核原文） |",
                    "",
                    "### （二）详细配置明细表",
                    "| 序号 | 配置名称 | 单位 | 数量 | 备注 |",
                    "|---:|---|---|---:|---|",
                    "| 1 | 核心配置 | 项 | 1 | 待按项目补充 |",
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
说明：本章已提供技术条款证据映射，供评审与复核使用。
"""
    return BidDocumentSection(section_title="第三章 商务及技术部分", content=content.strip())


def _gen_appendix(llm: ChatOpenAI, tender: TenderDocument, tender_raw: str) -> BidDocumentSection:
    """第四章：报价书附件（技术参数明细 + 售后服务方案）"""
    _ = llm
    today = _today()
    warranty = _normalize_commitment_term(tender.commercial_terms.warranty_period)
    payment = _normalize_commitment_term(tender.commercial_terms.payment_method)

    parameter_tables: list[str] = []
    if tender.packages:
        for pkg in tender.packages:
            parameter_tables.append(_build_main_parameter_table(pkg, tender_raw))
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
) -> list[BidDocumentSection]:
    """
    根据招标文件生成全部投标文件章节。

    Args:
        tender: 结构化招标文件数据
        tender_raw: 招标文件原始文本（供技术章节追溯）
        llm: 语言模型实例（为兼容接口保留）

    Returns:
        各章节列表
    """
    logger.info("开始一键生成投标文件章节")
    logger.debug("招标原文长度：%d 字符", len(tender_raw))

    sections = [
        _gen_qualification(llm, tender),
        _gen_compliance(llm, tender),
        _gen_technical(llm, tender, tender_raw),
        _gen_appendix(llm, tender, tender_raw),
    ]
    sections = _apply_template_pollution_guard(sections)

    logger.info("一键投标文件章节生成完成，共 %d 章", len(sections))
    return sections
